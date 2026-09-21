from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    openai_small_model: str
    openai_tts_model: str
    openai_tts_voice: str

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            data_dir=Path(os.getenv("LIBRARY_DATA_DIR", "data/runtime")),
            openai_small_model=os.getenv("OPENAI_SMALL_MODEL", "gpt-4o-mini"),
            openai_tts_model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
            openai_tts_voice=os.getenv("OPENAI_TTS_VOICE", "marin"),
        )

