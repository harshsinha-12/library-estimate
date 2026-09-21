"""Structured citation schema for Responses API web search.

Drafts still require a human confirm before they price a copy.
"""

from __future__ import annotations

PRICE_SCHEMA_NAME = "price_citations"
BATCH_SCHEMA_NAME = "price_citation_batch"
MAX_LISTING_URLS = 5
BATCH_SIZE = 5
DEFAULT_SMALL_MODEL = "gpt-5.6-luna"

_CITATION_ITEM: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title",
        "url",
        "snippet",
        "offer_type",
        "amount",
        "currency",
        "format",
        "condition",
    ],
    "properties": {
        "title": {"type": "string"},
        "url": {"type": "string"},
        "snippet": {"type": "string"},
        "offer_type": {
            "type": "string",
            "enum": ["physical", "ebook", "rental", "bundle", "unknown"],
        },
        "amount": {"type": ["number", "null"]},
        "currency": {
            "type": ["string", "null"],
            "pattern": "^[A-Z]{3}$",
        },
        "format": {"type": ["string", "null"]},
        "condition": {"type": ["string", "null"]},
    },
}

PRICE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["citations"],
    "properties": {
        "citations": {
            "type": "array",
            "maxItems": MAX_LISTING_URLS,
            "items": _CITATION_ITEM,
        }
    },
}

BATCH_PRICE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["results"],
    "properties": {
        "results": {
            "type": "array",
            "maxItems": BATCH_SIZE,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "citations"],
                "properties": {
                    "name": {"type": "string"},
                    "citations": {
                        "type": "array",
                        "maxItems": MAX_LISTING_URLS,
                        "items": _CITATION_ITEM,
                    },
                },
            },
        }
    },
}

OFFER_TYPES = frozenset(_CITATION_ITEM["properties"]["offer_type"]["enum"])
SHOP_HOST_PARTS = (
    "amazon.",
    "flipkart.",
    "bookswagon.",
    "crossword.",
    "sapnaonline.",
    "abebooks.",
    "barnesandnoble.",
    "ikea.",
    "croma.",
    "pepperfry.",
    "rakuten.",
    "ibs.it",
    "mondadori.",
    "kinokuniya.",
)
