from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib import request

import dashscope
import oss2
import requests
from alibabacloud_alimt20181012.client import Client as AlimtClient
from alibabacloud_alimt20181012 import models as alimt_models
from alibabacloud_tea_openapi import models as open_api_models
from dashscope.audio.asr import Transcription
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


BASE_DIR = Path(os.getenv("APP_BASE_DIR", Path(__file__).resolve().parent))
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
STATIC_DIR = Path(os.getenv("APP_STATIC_DIR", str(BASE_DIR / "static")))
CONFIG_PATH = Path(os.getenv("APP_CONFIG_FILE", str(BASE_DIR / "config.json")))
CONFIG: dict[str, Any] = {}

ASR_MODEL = "fun-asr"
ASR_LANGUAGE_HINTS = ["zh", "en"]
ASR_CONCURRENCY = 2
OSS_EXPIRES_SECONDS = 86400


app = FastAPI(title="Aliyun ASR Translator")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def disable_static_cache(request: Any, call_next: Any) -> Any:
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response

jobs_lock = threading.RLock()
jobs: dict[str, "Job"] = {}


@dataclass
class Row:
    id: str
    file_name: str
    local_path: str
    status: str = "queued"
    text: str = ""
    translated_text: str = ""
    error: str = ""


@dataclass
class Job:
    id: str
    status: str = "queued"
    rows: list[Row] = field(default_factory=list)
    language_hints: list[str] = field(default_factory=list)
    asr_provider: str = "aliyun"
    error: str = ""

    @property
    def completed(self) -> int:
        return sum(1 for row in self.rows if row.status in {"completed", "failed"})

    @property
    def total(self) -> int:
        return len(self.rows)


class TranslateRequest(BaseModel):
    row_ids: list[str] | None = Field(default=None)
    target_language: str = Field(pattern="^(zh|en)$")


@app.on_event("startup")
def startup() -> None:
    load_runtime_config()
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    configure_dashscope()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    language_hints: str = Form("zh,en"),
    asr_provider: str = Form("aliyun"),
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个音频文件")

    job_id = uuid.uuid4().hex
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    rows: list[Row] = []
    for index, upload in enumerate(files, start=1):
        original_name = upload.filename or f"audio-{index}"
        file_name = safe_display_name(original_name)
        stored_name = f"{index:04d}-{uuid.uuid4().hex}-{file_name}"
        local_path = job_dir / stored_name
        with local_path.open("wb") as target:
            shutil.copyfileobj(upload.file, target)
        rows.append(Row(id=uuid.uuid4().hex, file_name=file_name, local_path=str(local_path)))

    job = Job(
        id=job_id,
        rows=rows,
        language_hints=parse_language_hints(language_hints),
        asr_provider=normalize_asr_provider(asr_provider),
    )
    with jobs_lock:
        jobs[job_id] = job

    background_tasks.add_task(process_job, job_id)
    return serialize_job(job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = get_existing_job(job_id)
    return serialize_job(job)


@app.post("/api/jobs/{job_id}/translate")
def translate_job(job_id: str, body: TranslateRequest) -> dict[str, Any]:
    job = get_existing_job(job_id)
    if job.status == "running":
        raise HTTPException(status_code=409, detail="识别任务还在运行，请等待识别完成后再翻译")

    row_ids = set(body.row_ids or [])
    rows = [
        row
        for row in job.rows
        if row.text and (not row_ids or row.id in row_ids)
    ]
    if not rows:
        raise HTTPException(status_code=400, detail="没有可翻译的识别文本")

    client = create_translate_client()
    for row in rows:
        try:
            row.translated_text = translate_text(client, row.text, body.target_language)
        except Exception as exc:  # noqa: BLE001
            row.error = f"翻译失败：{exc}"

    return serialize_job(job)


def process_job(job_id: str) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job.status = "running"

    rows = list(job.rows)
    with ThreadPoolExecutor(max_workers=ASR_CONCURRENCY) as executor:
        futures = {executor.submit(process_row, job_id, row.id): row.id for row in rows}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001
                row = find_row(job_id, futures[future])
                row.status = "failed"
                row.error = str(exc)

    with jobs_lock:
        if any(row.status == "completed" for row in job.rows):
            job.status = "completed"
        else:
            job.status = "failed"
            job.error = "所有文件识别失败"


def process_row(job_id: str, row_id: str) -> None:
    job = get_existing_job(job_id)
    row = find_row(job_id, row_id)
    update_row(row, status="recognizing")
    if job.asr_provider == "cloudflare":
        result = transcribe_cloudflare_file(row.local_path, job.language_hints or ASR_LANGUAGE_HINTS)
        text = extract_cloudflare_text(result)
    else:
        update_row(row, status="uploading", error="")
        signed_url = upload_to_oss(row.local_path, row.file_name)
        update_row(row, status="recognizing")
        result = transcribe_url(signed_url, job.language_hints or ASR_LANGUAGE_HINTS)
        text = extract_text(result)
    if not text:
        raise RuntimeError("识别结果为空")
    update_row(row, status="completed", text=text)


def configure_dashscope() -> None:
    api_key = config_get("dashscope", "api_key", env="DASHSCOPE_API_KEY")
    if api_key:
        dashscope.api_key = api_key

    workspace_id = config_get("dashscope", "workspace_id", env="DASHSCOPE_WORKSPACE_ID")
    region = os.getenv("DASHSCOPE_REGION", "cn-beijing")
    if workspace_id:
        dashscope.base_http_api_url = build_dashscope_base_url(str(workspace_id), region)


def build_dashscope_base_url(workspace_or_host: str, region: str) -> str:
    value = workspace_or_host.strip().rstrip("/")
    if value.endswith("/api/v1"):
        return value
    if value.startswith("http://") or value.startswith("https://"):
        return f"{value}/api/v1"
    if ".maas.aliyuncs.com" in value:
        return f"https://{value}/api/v1"
    return f"https://{value}.{region}.maas.aliyuncs.com/api/v1"


def upload_to_oss(local_path: str, file_name: str) -> str:
    bucket_name = require_config("OSS_BUCKET", "oss", "bucket")
    endpoint = require_config("OSS_ENDPOINT", "oss", "endpoint")
    access_key_id = config_get("oss", "access_key_id", env="OSS_ACCESS_KEY_ID") or require_config(
        "ALIBABA_CLOUD_ACCESS_KEY_ID", "translation", "access_key_id"
    )
    access_key_secret = config_get("oss", "access_key_secret", env="OSS_ACCESS_KEY_SECRET") or require_config(
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET", "translation", "access_key_secret"
    )
    prefix = os.getenv("OSS_PREFIX", "asr-uploads").strip("/")

    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(auth, endpoint, bucket_name)
    suffix = Path(file_name).suffix
    object_key = f"{prefix}/{uuid.uuid4().hex}{suffix}"
    bucket.put_object_from_file(object_key, local_path)
    return bucket.sign_url("GET", object_key, OSS_EXPIRES_SECONDS, slash_safe=True)


def transcribe_url(file_url: str, language_hints: list[str] | None = None) -> dict[str, Any]:
    if not dashscope.api_key:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY")

    task_response = Transcription.async_call(
        model=ASR_MODEL,
        file_urls=[file_url],
        language_hints=language_hints or None,
    )
    if task_response.status_code != HTTPStatus.OK:
        message = getattr(task_response.output, "message", None) or str(task_response)
        raise RuntimeError(f"提交识别任务失败：{message}")

    transcription_response = Transcription.wait(task=task_response.output.task_id)
    if transcription_response.status_code != HTTPStatus.OK:
        message = getattr(transcription_response.output, "message", None) or str(transcription_response)
        raise RuntimeError(f"识别任务失败：{message}")

    results = transcription_response.output.get("results", [])
    if not results:
        raise RuntimeError("识别任务没有返回结果")

    transcription = results[0]
    if transcription.get("subtask_status") != "SUCCEEDED":
        raise RuntimeError(json.dumps(transcription, ensure_ascii=False))

    url = transcription["transcription_url"]
    return json.loads(request.urlopen(url, timeout=60).read().decode("utf8"))


def transcribe_cloudflare_file(local_path: str, language_hints: list[str] | None = None) -> dict[str, Any]:
    account_id = require_config("CLOUDFLARE_ACCOUNT_ID", "cloudflare", "account_id")
    api_token = require_config("CLOUDFLARE_API_TOKEN", "cloudflare", "api_token")
    model = os.getenv("CLOUDFLARE_ASR_MODEL", "@cf/openai/whisper-large-v3-turbo")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

    with open(local_path, "rb") as audio_file:
        audio_base64 = base64.b64encode(audio_file.read()).decode("utf-8")

    payload: dict[str, Any] = {
        "audio": audio_base64,
        "task": "transcribe",
    }
    language = cloudflare_language(language_hints)
    if language:
        payload["language"] = language

    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_token}"},
        json=payload,
        timeout=300,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Cloudflare Whisper 请求失败：{response.status_code} {response.text}")

    data = response.json()
    if data.get("success") is False:
        raise RuntimeError(f"Cloudflare Whisper 识别失败：{json.dumps(data, ensure_ascii=False)}")
    return data


def cloudflare_language(language_hints: list[str] | None) -> str:
    if not language_hints:
        return ""
    if len(language_hints) == 1:
        return language_hints[0]
    return ""


def extract_cloudflare_text(result: dict[str, Any]) -> str:
    data = result.get("result", result)
    if isinstance(data, dict):
        text = data.get("text")
        if text:
            return str(text).strip()
        transcription_info = data.get("transcription_info")
        if isinstance(transcription_info, dict) and transcription_info.get("text"):
            return str(transcription_info["text"]).strip()
    return ""


def extract_text(result: dict[str, Any]) -> str:
    texts: list[str] = []
    for transcript in result.get("transcripts", []):
        text = transcript.get("text")
        if text:
            texts.append(text.strip())
    return "\n".join(text for text in texts if text)


def create_translate_client() -> AlimtClient:
    access_key_id = require_config("ALIBABA_CLOUD_ACCESS_KEY_ID", "translation", "access_key_id")
    access_key_secret = require_config("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "translation", "access_key_secret")
    endpoint = os.getenv("ALIMT_ENDPOINT", "mt.cn-hangzhou.aliyuncs.com")
    config = open_api_models.Config(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        endpoint=endpoint,
    )
    return AlimtClient(config)


def translate_text(client: AlimtClient, text: str, target_language: str) -> str:
    translated_parts: list[str] = []
    for chunk in split_text(text, 4500):
        request_model = alimt_models.TranslateGeneralRequest(
            format_type="text",
            source_language="auto",
            target_language=target_language,
            source_text=chunk,
            scene="general",
        )
        response = client.translate_general(request_model)
        translated = extract_translated_text(response)
        if not translated:
            raise RuntimeError("阿里翻译没有返回 translated 字段")
        translated_parts.append(translated)
    return "\n".join(translated_parts)


def extract_translated_text(response: Any) -> str:
    body = getattr(response, "body", None)
    data = getattr(body, "data", None)
    translated = getattr(data, "translated", None)
    if translated:
        return translated

    if hasattr(response, "to_map"):
        mapped = response.to_map()
        return (
            mapped.get("body", {})
            .get("Data", {})
            .get("Translated", "")
        )
    return ""


def split_text(text: str, limit: int) -> list[str]:
    normalized = text.strip()
    if len(normalized) <= limit:
        return [normalized]

    chunks: list[str] = []
    current = ""
    parts = re.split(r"([。！？.!?\n])", normalized)
    for index in range(0, len(parts), 2):
        sentence = parts[index]
        punctuation = parts[index + 1] if index + 1 < len(parts) else ""
        candidate = sentence + punctuation
        if len(candidate) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(candidate[start : start + limit] for start in range(0, len(candidate), limit))
            continue
        if len(current) + len(candidate) > limit:
            chunks.append(current)
            current = candidate
        else:
            current += candidate
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]


def get_existing_job(job_id: str) -> Job:
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


def find_row(job_id: str, row_id: str) -> Row:
    job = get_existing_job(job_id)
    for row in job.rows:
        if row.id == row_id:
            return row
    raise RuntimeError("任务行不存在")


def update_row(row: Row, **changes: Any) -> None:
    with jobs_lock:
        for key, value in changes.items():
            setattr(row, key, value)


def serialize_job(job: Job) -> dict[str, Any]:
    with jobs_lock:
        return {
            "id": job.id,
            "status": job.status,
            "asr_provider": job.asr_provider,
            "language_hints": job.language_hints,
            "completed": job.completed,
            "total": job.total,
            "error": job.error,
            "rows": [
                {
                    "id": row.id,
                    "file_name": row.file_name,
                    "status": row.status,
                    "text": row.text,
                    "translated_text": row.translated_text,
                    "error": row.error,
                }
                for row in job.rows
            ],
        }


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


def load_runtime_config() -> None:
    global CONFIG, ASR_MODEL, ASR_LANGUAGE_HINTS, ASR_CONCURRENCY, OSS_EXPIRES_SECONDS

    CONFIG = load_config()
    ASR_MODEL = os.getenv("DASHSCOPE_ASR_MODEL", "fun-asr")
    ASR_LANGUAGE_HINTS = parse_language_hints(os.getenv("DASHSCOPE_LANGUAGE_HINTS", "zh,en"))
    ASR_CONCURRENCY = max(1, int(os.getenv("ASR_CONCURRENCY", "2")))
    OSS_EXPIRES_SECONDS = int(os.getenv("OSS_SIGNED_URL_EXPIRES", "86400"))


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as source:
        return json.load(source)


def config_get(*keys: str, env: str | None = None, default: Any = None) -> Any:
    if env:
        env_value = os.getenv(env)
        if env_value not in (None, ""):
            return env_value

    value: Any = CONFIG
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value if value not in (None, "") else default


def require_config(env_name: str, *keys: str) -> str:
    value = config_get(*keys, env=env_name)
    if not value:
        json_path = ".".join(keys)
        raise RuntimeError(f"缺少配置：环境变量 {env_name} 或 config.json 的 {json_path}")
    return str(value)


def normalize_asr_provider(value: str) -> str:
    provider = value.strip().lower()
    if provider in {"cloudflare", "cf", "whisper", "whisper-large-v3-turbo"}:
        return "cloudflare"
    return "aliyun"


def parse_language_hints(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def safe_display_name(name: str) -> str:
    clean = Path(name.replace("\\", "/")).name.strip()
    return clean or "audio"
