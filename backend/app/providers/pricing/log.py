"""Pricing and model call logs. Never log secrets or raw API keys."""

from __future__ import annotations

import logging

LOGGER = logging.getLogger("library_survey.pricing")


def configure() -> None:
    if LOGGER.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = True


def info(message: str, **fields: object) -> None:
    extra = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    LOGGER.info(f"{message} {extra}" if extra else message)


def warning(message: str, **fields: object) -> None:
    extra = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    LOGGER.warning(f"{message} {extra}" if extra else message)
