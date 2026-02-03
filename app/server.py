import os
import logging
import tempfile
import shutil
import time
import json
import yaml
import mimetypes
import subprocess

from pathlib import Path
from fastapi import FastAPI, File, UploadFile, Form
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

                log_fname = f"{int(now)}_{Path(temp_path).name}"
                log_path = Path("logs") / "audio" / log_fname
                log_path.mkdir(parents=True, exist_ok=True)
                shutil.copy2(temp_path, log_path)
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            text = transcript
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
        os.makedirs("logs", exist_ok=True)
        with open("logs/requests.log", "a") as f:
            f.write(json.dumps(log_entry) + "\n")

        yield json.dumps({"result": result}) + "\n"

    return StreamingResponse(
        _generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
