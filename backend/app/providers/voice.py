"""Server-side OpenAI audio; no key is sent to the capture app."""

from __future__ import annotations

import json
import os
import time
from urllib.request import Request, urlopen
from uuid import uuid4

from backend.app.providers.llm_trace import record_llm_call


def _key() -> str:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OpenAI voice is not configured")
    return key


def transcribe_segments(
    audio: bytes, started_monotonic_seconds: float, *, model: str = "gpt-4o-transcribe-diarize"
) -> list[dict]:
    boundary = uuid4().hex
    parts = []
    for name, value in (
        ("model", model),
        ("response_format", "diarized_json"),
        ("chunking_strategy", "auto"),
    ):
        header = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
        parts.append(f"{header}\r\n\r\n{value}\r\n".encode())
    parts.append(
        (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
         'filename="survey.m4a"\r\nContent-Type: audio/mp4\r\n\r\n').encode()
        + audio
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    request = Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=b"".join(parts),
        headers={
            "Authorization": f"Bearer {_key()}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    reason = (
        "STT: transcribe operator speech from survey audio so notes can be aligned "
        "to the capture clock"
    )
    started = time.monotonic()
    try:
        with urlopen(request, timeout=45) as response:
            payload = json.load(response)
    except Exception as error:
        record_llm_call(
            reason=reason, operation="stt", provider="openai", model=model,
            query={"audio_bytes": len(audio)}, status="error", error=str(error),
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        raise
    latency_ms = round((time.monotonic() - started) * 1000)
    record_llm_call(
        reason=reason, operation="stt", provider="openai", model=model,
        query={"audio_bytes": len(audio)}, response=payload, latency_ms=latency_ms,
    )
    return [
        {
            "id": segment.get("id", f"speech_{index}"),
            "text": segment["text"],
            "monotonic_seconds": started_monotonic_seconds + float(segment["start"]),
            "ended_monotonic_seconds": started_monotonic_seconds + float(segment["end"]),
            "source": "openai_stt",
            "speaker": segment.get("speaker"),
        }
        for index, segment in enumerate(payload.get("segments") or [])
    ]


def synthesize_prompt(text: str, *, model: str = "gpt-4o-mini-tts", voice: str = "marin") -> bytes:
    if not text or len(text) > 4096:
        raise ValueError("prompt text must be 1–4096 characters")
    reason = "TTS: speak an operator prompt so the capture app does not hold an API key"
    started = time.monotonic()
    request = Request(
        "https://api.openai.com/v1/audio/speech",
        data=json.dumps(
            {"model": model, "voice": voice, "input": text, "response_format": "mp3"}
        ).encode(),
        headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=45) as response:
            audio = response.read(2_000_000)
    except Exception as error:
        record_llm_call(
            reason=reason, operation="tts", provider="openai", model=model,
            query={"text": text, "voice": voice}, status="error", error=str(error),
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        raise
    record_llm_call(
        reason=reason, operation="tts", provider="openai", model=model,
        query={"text": text, "voice": voice},
        response={"audio_bytes": len(audio), "format": "mp3"},
        latency_ms=round((time.monotonic() - started) * 1000),
    )
    return audio
