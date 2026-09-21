"""ISBN-first, then name query construction for price search."""

from __future__ import annotations

import re

COUNTRY_NAMES = {"IN": "India", "IT": "Italy", "JP": "Japan"}
_OCR_NOISE = re.compile(r"^(isbn|issn|price|₹|\$|€|¥|www\.|http|o'?reilly|packt|wiley)", re.I)
BOOK_SHOPS = {
    "IN": "amazon.in OR flipkart.com OR bookswagon OR crossword.in",
    "IT": "amazon.it OR ibs.it OR mondadori",
    "JP": "amazon.co.jp OR rakuten.co.jp OR kinokuniya",
}
OBJECT_SHOPS = {
    "IN": "amazon.in OR flipkart.com OR ikea.com OR croma OR pepperfry",
    "IT": "amazon.it OR ikea.it OR mediaworld",
    "JP": "amazon.co.jp OR rakuten.co.jp OR ikea.jp",
}


def shop_query_terms(country_code: str, *, kind: str = "book") -> str:
    table = OBJECT_SHOPS if kind == "object" else BOOK_SHOPS
    return table.get(country_code) or "amazon.com OR barnesandnoble OR abebooks"


def shop_site_terms(country_code: str, *, kind: str = "book") -> str:
    if kind == "object":
        if country_code == "IN":
            return "site:amazon.in OR site:flipkart.com OR site:ikea.com OR site:pepperfry.com"
        if country_code == "IT":
            return "site:amazon.it OR site:ikea.it"
        if country_code == "JP":
            return "site:amazon.co.jp OR site:rakuten.co.jp"
        return "site:amazon.com OR site:ikea.com"
    if country_code == "IN":
        return "site:amazon.in OR site:flipkart.com OR site:bookswagon.com"
    if country_code == "IT":
        return "site:amazon.it OR site:ibs.it"
    if country_code == "JP":
        return "site:amazon.co.jp OR site:rakuten.co.jp"
    return "site:amazon.com OR site:abebooks.com OR site:barnesandnoble.com"


def titles_from_ocr(text: str | None) -> list[str]:
    """Pull one or more book names from cover/spine OCR. Does not invent an ISBN."""
    if not text or not str(text).strip():
        return []
    lines: list[str] = []
    for raw in str(text).replace("\r", "\n").split("\n"):
        line = re.sub(r"\s+", " ", raw).strip(" -•·|")
        if len(line) < 3 or _OCR_NOISE.search(line):
            continue
        if re.fullmatch(r"[\d\s\-xX.]+", line):
            continue
        if sum(ch.isalpha() for ch in line) < 3:
            continue
        lines.append(line)
    found: list[str] = []
    stacked: list[str] = []

    def flush() -> None:
        title = re.sub(r"\s+", " ", " ".join(stacked)).strip()
        stacked.clear()
        if len(title) >= 8 and title.lower() not in {item.lower() for item in found}:
            found.append(title[:160])

    for line in lines:
        words = line.split()
        if len(line) > 42 or len(words) >= 4:
            if stacked:
                flush()
            if line.lower() not in {item.lower() for item in found}:
                found.append(line[:160])
            continue
        stacked.append(line)
        if len(stacked) >= 4:
            flush()
    if stacked:
        flush()
    if not found:
        compact = re.sub(r"\s+", " ", str(text)).strip()
        if len(compact) >= 8 and not _OCR_NOISE.search(compact):
            return [compact[:160]]
    return found[:8]


def title_from_ocr(text: str | None) -> str | None:
    titles = titles_from_ocr(text)
    return titles[0] if titles else None


def template_query(
    *,
    isbn: str | None,
    title: str | None,
    author: str | None,
    publisher: str | None,
    edition: str | None,
    country_code: str,
    city: str | None = None,
    currency: str | None = None,
) -> tuple[str, str] | None:
    country = COUNTRY_NAMES.get(country_code, country_code)
    place = " ".join(part for part in (city, country) if part)
    shops = shop_query_terms(country_code, kind="book")
    if isbn:
        query = " ".join(
            part
            for part in (isbn, "paperback hardcover buy price", shops, place, currency)
            if part
        )
        return re.sub(r"\s+", " ", query).strip(), "isbn"
    if not title:
        return None
    extras = [part for part in (author, publisher, edition) if part]
    query = " ".join(
        [
            f'"{title.strip()}"',
            *extras,
            "paperback hardcover buy price",
            f"({shops})",
            place,
            currency or "",
        ]
    )
    return re.sub(r"\s+", " ", query).strip(), "name"


def template_object_query(
    *,
    label: str | None,
    category: str | None,
    spoken: str | None,
    country_code: str,
    city: str | None = None,
    currency: str | None = None,
) -> tuple[str, str] | None:
    country = COUNTRY_NAMES.get(country_code, country_code)
    place = " ".join(part for part in (city, country) if part)
    name = (label or spoken or category or "").strip()
    if len(name) < 3:
        return None
    query = " ".join(
        part
        for part in (
            f'"{name[:120]}"',
            category if category and category not in name.lower() else None,
            "used" if category in {"furniture", "appliance"} else None,
            "buy replacement price",
            f"({shop_query_terms(country_code, kind='object')})",
            place,
            currency or "",
        )
        if part
    )
    return re.sub(r"\s+", " ", query).strip(), "object"

