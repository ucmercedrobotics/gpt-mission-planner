import os
import logging
import tempfile
import shutil
import time
import json
import uuid
import yaml
import mimetypes
import subprocess
import struct
import re
import xml.etree.ElementTree as ET
from typing import Any

from pathlib import Path
from fastapi import FastAPI, File, UploadFile, Form, Body, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from openai import OpenAI

from mission_planner import MissionPlanner
from network_interface import NetworkInterface
from orchards.tree_placement_generator import TreePlacementGenerator

load_dotenv()

LTL_KEY: str = "ltl"
PROMELA_TEMPLATE_KEY: str = "promela_template"
SPIN_PATH_KEY: str = "spin_path"

host = os.getenv("HOST", "127.0.0.1")
port = int(os.getenv("PORT", 8002))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
SCHEMAS_DIR = BASE_DIR.parent / "schemas"
CONTEXT_DIR = BASE_DIR / "resources" / "context"
PROMPTS_DIR = CONTEXT_DIR / "prompts"

config = "./app/config/localhost.yaml"
with open(config, "r") as f:
    config_yaml = yaml.safe_load(f)

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


class GenerateRequest(BaseModel):
    text: str | None


@app.get("/debug_polygon")
def debug_polygon():
    bin_path = BASE_DIR / "gpt_outputs" / "tfr" / "shiraz_8.bin"
    if not bin_path.exists():
        return {"error": "Debug bin file not found"}

    points, visit_points = _extract_tree_points_from_bin(bin_path)
    if not points:
        return {"error": "No tree points found in debug bin"}

    return {"treePoints": points, "visitPoints": visit_points}


_openai_client = None
_whisper_model = None

CONTENT_TYPE_EXTENSION_MAP: dict[str, str] = {
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/mp4": ".m4a",
    "audio/m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "video/quicktime": ".mov",
    "audio/3gpp": ".3gp",
    "audio/x-caf": ".caf",
}


def _infer_audio_suffix(upload_file: UploadFile) -> str:
    """Best-effort detection of a useful file suffix for temporary audio files."""
    if upload_file.filename:
        suffix = Path(upload_file.filename).suffix.lower()
        if suffix:
            return suffix

    if upload_file.content_type:
        content_type = upload_file.content_type.lower()
        if content_type in CONTENT_TYPE_EXTENSION_MAP:
            return CONTENT_TYPE_EXTENSION_MAP[content_type]
        guessed = mimetypes.guess_all_extensions(content_type)
        if guessed:
            return guessed[0]

    return ".tmp"


def _convert_to_mp3(source_path: str) -> str:
    """Convert arbitrary audio file at source_path to mp3 if needed."""
    if Path(source_path).suffix.lower() == ".mp3":
        return source_path

    if shutil.which("ffmpeg") is None:
        logger.warning(
            "ffmpeg not found; skipping conversion and using original audio file."
        )
        return source_path

    fd, mp3_path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        source_path,
        "-vn",
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "2",
        mp3_path,
    ]
    try:
        subprocess.run(
            command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except FileNotFoundError as exc:
        logger.error("ffmpeg not found while converting audio: %s", exc)
        os.remove(mp3_path)
        return source_path
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else ""
        logger.error("ffmpeg conversion failed: %s", stderr.strip())
        os.remove(mp3_path)
        return source_path

    return mp3_path


def _read_length_prefixed_chunks(file_path: Path) -> list[bytes]:
    chunks: list[bytes] = []
    with open(file_path, "rb") as f:
        while True:
            header = f.read(4)
            if len(header) < 4:
                break
            length = struct.unpack("!I", header)[0]
            if length <= 0:
                break
            payload = f.read(length)
            if len(payload) < length:
                break
            chunks.append(payload)
    return chunks


def _extract_move_to_tree_ids(xml_text: str) -> list[int]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    ids: list[int] = []
    for elem in root.findall(".//MoveToTreeID"):
        raw_id = elem.get("id")
        if raw_id is None:
            name = elem.get("name") or ""
            match = re.search(r"(\d+)", name)
            raw_id = match.group(1) if match else None
        if raw_id is None:
            continue
        try:
            ids.append(int(raw_id))
        except ValueError:
            continue
    return ids


def _build_visit_points(
    tree_points: list[dict[str, Any]], move_ids: list[int]
) -> list[dict[str, Any]]:
    index_map = {}
    for item in tree_points:
        try:
            tree_index = int(item.get("tree_index"))
        except (TypeError, ValueError):
            continue
        if "lat" in item and "lon" in item:
            index_map[tree_index] = {
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
            }

    visit_points = []
    for order, tree_id in enumerate(move_ids, start=1):
        coords = index_map.get(tree_id)
        if not coords:
            continue
        visit_points.append(
            {
                "lat": coords["lat"],
                "lon": coords["lon"],
                "order": order,
                "treeIndex": tree_id,
            }
        )
    return visit_points


def _extract_tree_points_from_bin(
    file_path: Path,
) -> tuple[list[dict[str, float]], list[dict[str, Any]]]:
    xml_text = None
    tree_points = []
    full_tree_points: list[dict[str, Any]] = []
    for chunk in _read_length_prefixed_chunks(file_path):
        try:
            decoded = chunk.decode("utf-8")
        except UnicodeDecodeError:
            continue

        try:
            data = json.loads(decoded)
        except json.JSONDecodeError:
            if decoded.strip().startswith("<"):
                xml_text = decoded
            continue

        if isinstance(data, list) and data and isinstance(data[0], dict):
            points: list[dict[str, float]] = []
            full_points: list[dict[str, Any]] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                full_points.append(item)
                if "lat" in item and "lon" in item:
                    try:
                        points.append(
                            {"lat": float(item["lat"]), "lon": float(item["lon"])}
                        )
                    except (TypeError, ValueError):
                        continue
            if points:
                tree_points = points
                full_tree_points = full_points

    visit_points: list[dict[str, Any]] = []
    if xml_text and full_tree_points:
        move_ids = _extract_move_to_tree_ids(xml_text)
        visit_points = _build_visit_points(full_tree_points, move_ids)

    return tree_points, visit_points


def transcribe(path: str) -> str:
    provider = os.getenv("STT_PROVIDER", "openai").lower()
    if provider == "local":
        return transcribe_local(path)
    return transcribe_openai(path)


def transcribe_openai(path: str) -> str:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    with open(path, "rb") as audio_file:
        transcript = _openai_client.audio.transcriptions.create(
            model=os.getenv("STT_MODEL", "gpt-4o-mini-transcribe"),
            file=audio_file,
            prompt="The user is a farmer speaking instructions for an ag-tech robot.",
        )
        return transcript.text


def transcribe_local(path: str) -> str:
    global _whisper_model
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        logger.error("faster-whisper not available: %s", exc)
        raise

    model_name = os.getenv("WHISPER_MODEL", "small")
    device = os.getenv("WHISPER_DEVICE", "cpu")
    compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

    if _whisper_model is None:
        _whisper_model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
        )

    segments, _info = _whisper_model.transcribe(path)
    text = "".join(segment.text for segment in segments).strip()
    return text


@app.get("/")
async def index():
    index_path = WEB_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse(
            "<h1>Missing web UI</h1><p>Expected app/web/index.html</p>",
            status_code=404,
        )
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/context_files")
async def get_context_files():
    try:
        if not PROMPTS_DIR.exists():
            return {"files": []}

        files = sorted({p.name for p in PROMPTS_DIR.rglob("*") if p.is_file()})
        return {"files": files}
    except Exception as e:
        logger.error(f"Error getting context files: {e}")
        return {"files": []}


@app.get("/schemas")
async def get_schemas():
    try:
        if not SCHEMAS_DIR.exists():
            return {"schemas": []}
        schemas = [p.stem for p in SCHEMAS_DIR.glob("*.xsd") if p.is_file()]
        schemas.sort()
        return {"schemas": schemas}
    except Exception as e:
        logger.error(f"Error getting schemas: {e}")
        return {"schemas": []}


@app.get("/tcp_defaults")
def get_tcp_defaults(request: Request):
    config_host = str(config_yaml.get("host", "127.0.0.1"))
    default_host = config_host
    if config_host in {"127.0.0.1", "localhost"} and request.client:
        default_host = request.client.host
    return {
        "host": default_host,
        "port": int(config_yaml.get("port", 12345)),
    }


@app.get("/missions")
def list_missions():
    mission_dir = Path("logs") / "missions"
    if not mission_dir.exists():
        return {"missions": []}

    missions = []
    for request_file in mission_dir.glob("*_request.json"):
        mission_id = request_file.name.replace("_request.json", "")
        xml_file = mission_dir / f"{mission_id}_result.xml"
        tree_file = mission_dir / f"{mission_id}_tree_points.json"
        try:
            with open(request_file, "r", encoding="utf-8") as f:
                request_data = json.load(f)
            missions.append(
                {
                    "id": mission_id,
                    "prompt": request_data.get("text"),
                    "createdAt": request_file.stat().st_mtime,
                    "xmlFile": xml_file.name if xml_file.exists() else None,
                    "treePointsFile": tree_file.name if tree_file.exists() else None,
                }
            )
        except Exception as exc:
            logger.warning("Failed reading mission %s: %s", request_file, exc)

    missions.sort(key=lambda m: m.get("createdAt", 0), reverse=True)
    return {"missions": missions}


@app.get("/missions/{mission_id}")
def get_mission(mission_id: str):
    mission_dir = Path("logs") / "missions"
    xml_path = mission_dir / f"{mission_id}_result.xml"
    if not xml_path.exists():
        return {"error": "Mission not found"}

    with open(xml_path, "r", encoding="utf-8") as f:
        result = f.read()

    tree_points_payload = None
    visit_points_payload = None
    tree_path = mission_dir / f"{mission_id}_tree_points.json"
    if tree_path.exists():
        try:
            with open(tree_path, "r", encoding="utf-8") as f:
                tree_points = json.load(f)
            if isinstance(tree_points, list):
                tree_points_payload = [
                    {"lat": float(p["lat"]), "lon": float(p["lon"])}
                    for p in tree_points
                    if isinstance(p, dict) and "lat" in p and "lon" in p
                ]
                move_ids = _extract_move_to_tree_ids(result)
                visit_points_payload = _build_visit_points(tree_points, move_ids)
        except Exception as exc:
            logger.warning("Failed reading tree points for %s: %s", mission_id, exc)

    payload = {"result": result, "mission": {"id": mission_id}}
    if tree_points_payload:
        payload["treePoints"] = tree_points_payload
    if visit_points_payload:
        payload["visitPoints"] = visit_points_payload
    return payload


@app.post("/missions/{mission_id}/send")
def send_mission(
    mission_id: str, request: Request, payload: dict = Body(default={})  # type: ignore
):
    mission_dir = Path("logs") / "missions"
    xml_path = mission_dir / f"{mission_id}_result.xml"
    if not xml_path.exists():
        return {"error": "Mission not found"}

    tree_points = None
    tree_path = mission_dir / f"{mission_id}_tree_points.json"
    if tree_path.exists():
        try:
            with open(tree_path, "r", encoding="utf-8") as f:
                tree_points = json.load(f)
        except Exception as exc:
            logger.warning("Failed reading tree points for %s: %s", mission_id, exc)

    try:
        config_host = config_yaml.get("host", "127.0.0.1")
        tcp_host = payload.get("tcpHost") or config_host
        if tcp_host in {"127.0.0.1", "localhost"} and request.client:
            tcp_host = request.client.host
        tcp_port_raw = payload.get("tcpPort") or config_yaml.get("port", 12345)
        tcp_port = int(tcp_port_raw)
        nic = NetworkInterface(logger, tcp_host, tcp_port)
        nic.init_socket()
        nic.send_file(str(xml_path), tree_points)
        nic.close_socket()
    except Exception as exc:
        logger.warning("TCP resend failed: %s", exc)
        return {"error": "Failed to send mission"}

    with open(xml_path, "r", encoding="utf-8") as f:
        result = f.read()

    return {"result": result, "sent": True}


@app.post("/generate")
async def generate(request: str = Form(...), file: UploadFile = File(None)):
    async def _generate():
        now = time.time()
        data = json.loads(request)
        print("data:", data)
        text = data.get("text")
        log_entry = {
            "timestamp": now,
            "request": request,
        }

        save_mission = data.get("saveMission", data.get("saveAudio", True))
        if isinstance(save_mission, str):
            save_mission = save_mission.lower() == "true"

        mission_id = f"{int(now)}_{uuid.uuid4().hex[:8]}"

        if file:
            audio_data = await file.read()
            suffix = _infer_audio_suffix(file)
            temp_fd, temp_path = tempfile.mkstemp(suffix=suffix)
            try:
                with os.fdopen(temp_fd, "wb") as temp_file:
                    temp_file.write(audio_data)
                    temp_file.flush()

                mp3_path = _convert_to_mp3(temp_path)
                try:
                    transcript = transcribe(mp3_path)
                finally:
                    if mp3_path != temp_path and os.path.exists(mp3_path):
                        os.remove(mp3_path)
                    yield json.dumps({"stt": transcript}) + "\n"

                if save_mission:
                    log_fname = f"{mission_id}_{Path(temp_path).name}"
                    log_path = Path("logs") / "audio" / log_fname
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(temp_path, log_path)
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            if not transcript or not transcript.strip():
                yield json.dumps({"error": "No speech detected"}) + "\n"
                return

            text = transcript
            if save_mission:
                log_entry["audioFile"] = log_fname

        if not text:
            yield json.dumps({"error": "Missing text input"}) + "\n"
            return

        context_files: list[str] = []

        # don't generate/check LTL by default
        ltl: bool = False
        pml_template_path: str = ""
        spin_path: str = ""

        try:
            if "contextFiles" in data:
                # Handle contextFiles as a list of filenames
                requested_files = data["contextFiles"]
                if isinstance(requested_files, str):
                    requested_files = [requested_files]

                for filename in requested_files:
                    if not filename:
                        continue

                    if "/" in filename or "\\" in filename:
                        candidate_path = (CONTEXT_DIR / filename).resolve()
                        if (
                            candidate_path.exists()
                            and candidate_path.is_file()
                            and CONTEXT_DIR in candidate_path.parents
                        ):
                            context_files.append(str(candidate_path))
                        else:
                            logger.warning(
                                f"Requested context file not found: {filename}"
                            )
                        continue

                    matches = [p for p in PROMPTS_DIR.rglob(filename) if p.is_file()]
                    if not matches:
                        logger.warning(f"Requested context file not found: {filename}")
                        continue
                    if len(matches) > 1:
                        logger.warning(
                            "Multiple context files matched %s. Using %s",
                            filename,
                            matches[0],
                        )
                    context_files.append(str(matches[0].resolve()))

            elif "context_files" in config_yaml:
                context_files.extend(config_yaml["context_files"])

            if "farm_polygon" in config_yaml:
                tpg = TreePlacementGenerator(
                    config_yaml["farm_polygon"]["points"],
                    config_yaml["farm_polygon"]["dimensions"],
                )
                logger.debug("Farm polygon points defined are: %s", tpg.polygon_coords)
                logger.debug("Farm dimensions defined are: %s", tpg.dimensions)
            else:
                tpg = None
                logger.warning(
                    "No farm polygon found. Assuming we're not dealing with an orchard grid..."
                )

            lint_xml = data.get("lintXml", config_yaml.get("lint_xml", True))

            # if user specifies config key -> optional keys
            ltl = config_yaml.get(LTL_KEY) or False
            pml_template_path = config_yaml.get(PROMELA_TEMPLATE_KEY) or ""
            spin_path = config_yaml.get(SPIN_PATH_KEY) or ""
            if ltl and not (pml_template_path and spin_path):
                ltl = False
                logger.warning(
                    "No spin configuration found. Proceeding without formal verification..."
                )

            mp = MissionPlanner(
                config_yaml["token"],
                [f"schemas/{data['schema']}.xsd"],
                lint_xml,
                context_files,
                tpg,
                config_yaml["max_retries"],
                config_yaml["max_tokens"],
                config_yaml["temperature"],
                ltl,
                pml_template_path,
                spin_path,
                config_yaml["log_directory"],
                logger,
            )
        except yaml.YAMLError as exc:
            logger.error(f"Improper YAML config: {exc}")

        file_xml_out = mp.run(text)
        with open(file_xml_out, "r") as f:
            result = f.read()

        tree_points_payload = None
        visit_points_payload = None
        if hasattr(mp, "tree_points") and mp.tree_points:
            tree_points_payload = [
                {"lat": float(p["lat"]), "lon": float(p["lon"])}
                for p in mp.tree_points
                if "lat" in p and "lon" in p
            ]
            move_ids = _extract_move_to_tree_ids(result)
            visit_points_payload = _build_visit_points(mp.tree_points, move_ids)

        try:
            tcp_host = data.get("tcpHost") or config_yaml.get("host", "127.0.0.1")
            tcp_port_raw = data.get("tcpPort") or config_yaml.get("port", 12345)
            tcp_port = int(tcp_port_raw)
            nic = NetworkInterface(logger, tcp_host, tcp_port)
            nic.init_socket()
            tree_points = (
                mp.tree_points
                if hasattr(mp, "tree_points") and mp.tree_points
                else None
            )
            nic.send_file(file_xml_out, tree_points)
            nic.close_socket()
        except Exception as exc:
            logger.warning("TCP send failed: %s", exc)

        log_entry["response"] = result

        mission_meta = None
        if save_mission:
            mission_dir = Path("logs") / "missions"
            mission_dir.mkdir(parents=True, exist_ok=True)

            request_path = mission_dir / f"{mission_id}_request.json"
            with open(request_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            xml_path = mission_dir / f"{mission_id}_result.xml"
            with open(xml_path, "w", encoding="utf-8") as f:
                f.write(result)

            tree_points = (
                mp.tree_points
                if hasattr(mp, "tree_points") and mp.tree_points
                else None
            )
            tree_path = None
            if tree_points is not None:
                tree_path = mission_dir / f"{mission_id}_tree_points.json"
                with open(tree_path, "w", encoding="utf-8") as f:
                    json.dump(tree_points, f, indent=2)

            log_entry["mission"] = {
                "id": mission_id,
                "requestFile": request_path.name,
                "xmlFile": xml_path.name,
                "treePointsFile": tree_path.name if tree_path else None,
            }
            mission_meta = {
                "id": mission_id,
                "prompt": text,
                "createdAt": now,
                "xmlFile": xml_path.name,
                "treePointsFile": tree_path.name if tree_path else None,
            }

        os.makedirs("logs", exist_ok=True)
        with open("logs/requests.log", "a") as f:
            f.write(json.dumps(log_entry) + "\n")

        payload = {"result": result}
        if tree_points_payload:
            payload["treePoints"] = tree_points_payload
        if visit_points_payload:
            payload["visitPoints"] = visit_points_payload
        if mission_meta:
            payload["mission"] = mission_meta
        yield json.dumps(payload) + "\n"

    return StreamingResponse(
        _generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
