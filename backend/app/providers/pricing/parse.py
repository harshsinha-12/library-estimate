"""Snippet price extraction and physical-book offer filtering."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from html import unescape
from urllib.parse import urlparse

from backend.app.providers.pricing.schema import SHOP_HOST_PARTS

RENTAL_TERMS = ("rental", "rent textbook", "rent this")
BUNDLE_TERMS = ("bundle", "3-pack", "box set digital")
AUDIO_TERMS = ("audiobook", "audio book", "audible")

CURRENCY_SYMBOLS = {
    "₹": "INR",
    "€": "EUR",
    "¥": "JPY",
    "£": "GBP",
    "$": "USD",
}
CURRENCY_WORDS = {
    "inr": "INR",
    "rs": "INR",
    "rs.": "INR",
    "rupee": "INR",
    "rupees": "INR",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "jpy": "JPY",
    "yen": "JPY",
    "usd": "USD",
    "dollar": "USD",
    "dollars": "USD",
}

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_AMOUNT = r"(?:[\d]{1,3}(?:,[\d]{3})+|[\d]+)(?:\.\d{1,2})?"
_SYMBOL_PRICE = re.compile(rf"([₹€¥£$])\s*({_AMOUNT})")
_CODE_PRICE = re.compile(
    rf"\b(INR|EUR|JPY|USD|GBP|Rs\.?|rupees?|euros?|yen|dollars?)\s*({_AMOUNT})",
    re.I,
)
_PRICE_CODE = re.compile(
    rf"({_AMOUNT})\s*(INR|EUR|JPY|USD|GBP|Rs\.?|rupees?|euros?|yen|dollars?)\b",
    re.I,
)
_SPOKEN_COST = re.compile(
    rf"(?:cost(?:\s+us)?|paid|worth|priced(?:\s+at)?)\s+"
    rf"(?:(?:[₹€¥£$]|INR|EUR|JPY|USD|GBP|Rs\.?|rupees?|euros?|yen|dollars?)\s*)?"
    rf"({_AMOUNT})"
    rf"(?:\s*(?:[₹€¥£$]|INR|EUR|JPY|USD|GBP|Rs\.?|rupees?|euros?|yen|dollars?))?",
    re.I,
)


def strip_html(value: str) -> str:
    return _SPACE.sub(" ", unescape(_TAG.sub(" ", value))).strip()


def classify_offer(title: str, snippet: str) -> str:
    title_l = title.lower()
    text = f"{title} {snippet}".lower()
    physical_title = (
        any(word in title_l for word in ("paperback", "hardcover", "hardback", "physical"))
        and "kindle" not in title_l
    )
    if physical_title:
        return "physical"
    if re.search(r"\be-?books?\b", text) or any(
        term in text for term in ("kindle", "kobo", "nook", "google play books")
    ):
        return "ebook"
    if any(term in text for term in RENTAL_TERMS):
        return "rental"
    if any(term in text for term in BUNDLE_TERMS):
        return "bundle"
    if any(term in text for term in AUDIO_TERMS):
        return "unknown"
    if "paperback" in text or "hardcover" in text or "hardback" in text or "physical" in text:
        return "physical"
    return "unknown"


def parse_prices(text: str, *, default_currency: str | None = None) -> list[tuple[Decimal, str]]:
    found: list[tuple[Decimal, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in _SYMBOL_PRICE.finditer(text):
        _add_price(found, seen, match.group(2), CURRENCY_SYMBOLS[match.group(1)])
    for match in _CODE_PRICE.finditer(text):
        _add_price(found, seen, match.group(2), _currency_word(match.group(1), default_currency))
    for match in _PRICE_CODE.finditer(text):
        _add_price(found, seen, match.group(1), _currency_word(match.group(2), default_currency))
    return found


def parse_spoken_cost(
    text: str, *, default_currency: str
) -> tuple[Decimal, str] | None:
    prices = parse_prices(text, default_currency=default_currency)
    if prices:
        return prices[0]
    match = _SPOKEN_COST.search(text)
    if not match:
        return None
    amount = _decimal(match.group(1))
    if amount is None:
        return None
    return amount, default_currency


def _currency_word(raw: str, default_currency: str | None) -> str:
    token = raw.lower().rstrip(".")
    if token in CURRENCY_WORDS:
        return CURRENCY_WORDS[token]
    if raw.upper() in {"INR", "EUR", "JPY", "USD", "GBP"}:
        return raw.upper()
    return default_currency or "USD"


def _decimal(raw: str) -> Decimal | None:
    try:
        value = Decimal(raw.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None
    if value <= 0:
        return None
    return value


def _add_price(
    found: list[tuple[Decimal, str]],
    seen: set[tuple[str, str]],
    raw_amount: str,
    currency: str,
) -> None:
    amount = _decimal(raw_amount)
    if amount is None:
        return
    key = (str(amount), currency)
    if key in seen:
        return
    seen.add(key)
    found.append((amount, currency))


def is_shop_url(url: str) -> bool:
    host = urlparse(url or "").netloc.lower()
    return any(part in host for part in SHOP_HOST_PARTS)
