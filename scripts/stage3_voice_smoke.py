"""Manual live voice check. Prints metadata only, never transcripts or credentials."""

from __future__ import annotations

import json
from uuid import UUID

from backend.app.config.settings import Settings
from backend.app.domain.repository import SurveyRepository
from backend.app.providers.voice import synthesize_prompt, transcribe_segments
from backend.app.storage.objects import S3ObjectStore
from backend.app.storage.redis_client import connect_redis


def main() -> None:
    settings = Settings.from_environment()
    repository = SurveyRepository(
        connect_redis(settings), S3ObjectStore(settings), key_prefix=settings.redis_key_prefix
    )
    repository.initialize()
    match = next((value for value in repository.survey_ids() if value.startswith("eb3f30fa")), None)
    if match is None:
        raise RuntimeError("canonical survey is unavailable")
    survey_id = UUID(match)
    timing = json.loads(repository.get_bytes(survey_id, "audio/timing.json"))
    segments = transcribe_segments(
        repository.get_bytes(survey_id, "audio/survey.m4a"),
        float(timing["started_monotonic_seconds"]),
    )
    aligned = all(
        item["monotonic_seconds"] >= timing["started_monotonic_seconds"]
        for item in segments
    )
    print(f"STT segments={len(segments)} clock_aligned={aligned}")
    speech = synthesize_prompt(
        "Please capture a damage close-up with a ruler.",
        model=settings.openai_tts_model,
        voice=settings.openai_tts_voice,
    )
    mp3_header = speech.startswith(b"ID3") or (
        len(speech) > 2 and speech[0] == 0xFF and speech[1] & 0xE0 == 0xE0
    )
    print(f"TTS mp3_bytes={len(speech)} valid_header={mp3_header}")


if __name__ == "__main__":
    main()
