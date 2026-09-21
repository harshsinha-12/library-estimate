from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PriceQuery(StrictModel):
    query: str = Field(min_length=1)
    market: str = Field(pattern=r"^[a-z]{2}-[A-Z]{2}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class PriceEvidence(StrictModel):
    source: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    observed_at: datetime
    amount: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    format: str | None = None
    condition: str | None = None


class PriceFetcher(Protocol):
    async def fetch(self, query: PriceQuery) -> list[PriceEvidence]: ...

