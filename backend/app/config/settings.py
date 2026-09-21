from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(REPO_ROOT / ".env.local", override=False)

os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for the survey backend")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    openai_small_model: str
    openai_tts_model: str
    openai_stt_model: str
    openai_tts_voice: str
    redis_host: str
    redis_port: int
    redis_username: str
    redis_password: str = field(repr=False)
    redis_key_prefix: str
    s3_endpoint_url: str
    s3_access_key_id: str = field(repr=False)
    s3_secret_access_key: str = field(repr=False)
    s3_bucket: str
    s3_region: str

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            data_dir=Path(os.getenv("LIBRARY_DATA_DIR", "data/runtime")),
            openai_small_model=os.getenv("OPENAI_SMALL_MODEL", "gpt-5.6-luna"),
            openai_tts_model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
            openai_stt_model=os.getenv("OPENAI_STT_MODEL", "gpt-4o-transcribe-diarize"),
            openai_tts_voice=os.getenv("OPENAI_TTS_VOICE", "marin"),
            redis_host=_require("REDIS_HOST"),
            redis_port=int(_require("REDIS_PORT")),
            redis_username=_require("REDIS_USERNAME"),
            redis_password=_require("REDIS_PASSWORD"),
            redis_key_prefix=os.getenv("REDIS_KEY_PREFIX", "ls:v1"),
            s3_endpoint_url=_require("S3_ENDPOINT_URL"),
            s3_access_key_id=_require("S3_ACCESS_KEY_ID"),
            s3_secret_access_key=_require("S3_SECRET_ACCESS_KEY"),
            s3_bucket=_require("S3_BUCKET"),
            s3_region=os.getenv("S3_REGION", "auto"),
        )
