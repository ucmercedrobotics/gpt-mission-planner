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
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from openai import OpenAI

from mission_planner import MissionPlanner
from utils.gps_utils import TreePlacementGenerator

load_dotenv()

LTL_KEY: str = "ltl"
PROMELA_TEMPLATE_KEY: str = "promela_template"
SPIN_PATH_KEY: str = "spin_path"

host = os.getenv("HOST", "127.0.0.1")
port = int(os.getenv("PORT", 8002))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

config = "./app/config/localhost.yaml"
with open(config, 'r') as f:
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

class GenerateRequest(BaseModel):
    text: str | None

_openai_client = None

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
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        logger.error("ffmpeg not found while converting audio: %s", exc)
        os.remove(mp3_path)
        raise
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else ""
        logger.error("ffmpeg conversion failed: %s", stderr.strip())
        os.remove(mp3_path)
        raise

    return mp3_path


def transcribe(path: str) -> str:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    with open(path, 'rb') as audio_file:
        transcript = _openai_client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=audio_file,
            prompt="The user is a farmer speaking instructions for an ag-tech robot.",
        )
        return transcript

@app.get("/context_files")
async def get_context_files():
    try:
        path = Path("./app/resources/context/wheeled_bots")
        if not path.exists():
            return {"files": []}

        files = [f.name for f in path.iterdir() if f.is_file()]
        return {"files": files}
    except Exception as e:
        logger.error(f"Error getting context files: {e}")
        return {"files": []}

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
                yield json.dumps({"stt": transcript.text}) + '\n'

                log_fname = f"{int(now)}_{Path(temp_path).name}"
                log_path = Path("logs") / "audio" / log_fname
                log_path.mkdir(parents=True, exist_ok=True)
                shutil.copy2(temp_path, log_path)
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            text = transcript.text
            log_entry["audioFile"] = log_fname

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

                wheeled_bots_path = Path("./app/resources/context/wheeled_bots")
                for filename in requested_files:
                    file_path = wheeled_bots_path / filename
                    if file_path.exists() and file_path.is_file():
                        context_files.append(str(file_path))
                    else:
                        logger.warning(f"Requested context file not found: {filename}")

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
                [f"schemas/schemas/{data["schema"]}.xsd"],
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
                logger
            )
        except yaml.YAMLError as exc:
            logger.error(f"Improper YAML config: {exc}")

        file_xml_out = mp.run(text)
        with open(file_xml_out, 'r') as f:
            result = f.read()

        log_entry["response"] = result
        os.makedirs("logs", exist_ok=True)
        with open("logs/requests.log", "a") as f:
            f.write(json.dumps(log_entry) + '\n')

        yield json.dumps({"result": result}) + '\n'

    return StreamingResponse(
        _generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )
