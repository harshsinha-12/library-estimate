from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class CatalogRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    authors: list[str]
    identifiers: dict[str, str]


class CatalogFetcher(Protocol):
    async def fetch_by_identifier(self, identifier: str) -> list[CatalogRecord]: ...

