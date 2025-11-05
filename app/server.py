import os
import logging
import tempfile
import shutil
import time
import json
import yaml

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
            with tempfile.NamedTemporaryFile(suffix='.webm') as temp_file:
                temp_file.write(audio_data)
                transcript = transcribe(temp_file.name)
                yield json.dumps({"stt": transcript.text}) + '\n'

                log_fname = f"{int(now)}_{os.path.basename(temp_file.name)}"
                log_path = Path("logs") / "audio" / log_fname
                log_path.mkdir(parents=True, exist_ok=True)
                shutil.copy2(temp_file.name, log_path)

            text = transcript.text
            log_entry["audioFile"] = log_fname

        context_files: list[str] = []

        # don't generate/check LTL by default
        ltl: bool = False
        pml_template_path: str = ""
        spin_path: str = ""

        try:
            # FIXME this is bad logic, but the problem is the config file spec
            if "geojsonName" in data:
                match data["geojsonName"]:
                    case "reza": context_files = ["./app/resources/context/wheeled_bots/reza_medium_polygon.txt"]
                    case "greece": context_files = ["./app/resources/context/wheeled_bots/greece.txt"]
                    case other:
                        print(f"Warning: unhandled value for geojsonName:", data["geojsonName"])
            elif "context_files" in config_yaml:
                context_files = config_yaml["context_files"]
            else:
                logger.warning("No additional context files found. Proceeding...")
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
