"""
zoe_ui_components.py — Automatic AG-UI component extraction from agent response text.

After OpenClaw (or any agent) returns plain text, this module pattern-matches
the response and produces structured AG-UI component payloads that the chat
router emits as `zoe.ui_component` CustomEvents.

Patterns detected:
  - Markdown tables containing price/currency values → price_table
  - Numbered lists with addresses / location-like text → map_embed (geocoded via Nominatim)
  - Short bulleted/numbered option lists at the end of a response → action_menu
  - Tabular data with no prices → data_grid
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

_PRICE_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?")
_MD_TABLE_RE = re.compile(
    r"^\|.+\|\s*\n\|[-| :]+\|\s*\n((?:\|.+\|\s*\n?)+)",
    re.MULTILINE,
)
_ADDRESS_WORDS = re.compile(
    r"\b(?:street|st\.?|road|rd\.?|avenue|ave\.?|drive|dr\.?|lane|ln\.?|"
    r"blvd\.?|boulevard|way|court|ct\.?|place|pl\.?|highway|hwy|"
    r"geraldton|perth|fremantle|joondalup|suburb|supermarket|store|shop)\b",
    re.IGNORECASE,
)
_NUMBERED_LIST_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$", re.MULTILINE)
_BULLET_LIST_RE = re.compile(r"^\s*[-*•]\s+(.+)$", re.MULTILINE)


def _parse_md_table(raw: str) -> list[dict[str, str]]:
    """Parse a markdown table into a list of dicts keyed by header."""
    lines = [l.strip() for l in raw.strip().splitlines()]
    if len(lines) < 3:
        return []
    headers = [h.strip() for h in lines[0].strip("|").split("|")]
    rows = []
    for line in lines[2:]:
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(dict(zip(headers, cells)))
    return rows


def _table_to_price_rows(rows: list[dict]) -> list[dict]:
    """Convert parsed table rows into price_table component row format."""
    out = []
    for r in rows:
        vals = list(r.values())
        # Find which column looks like a price
        price = ""
        store = ""
        product = ""
        for k, v in r.items():
            kl = k.lower()
            if "price" in kl or "cost" in kl or "$" in v:
                price = v
            elif "store" in kl or "shop" in kl or "retailer" in kl or "name" in kl:
                store = v
            elif "product" in kl or "item" in kl or "brand" in kl or "desc" in kl:
                product = v

        # Fallback: first col = store, second = product, third = price
        if not price and len(vals) >= 3:
            store = store or vals[0]
            product = product or vals[1]
            price = price or vals[2]
        elif not price and len(vals) >= 2:
            store = store or vals[0]
            price = price or vals[1]

        if not price:
            continue

        row: dict[str, Any] = {"store": store, "product": product, "price": price}
        # Mark the first/cheapest row as best — done by caller after sorting
        out.append(row)
    return out


async def _geocode(address: str) -> tuple[float, float] | None:
    """Geocode an address using Nominatim (free, no API key)."""
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            r = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": address, "format": "json", "limit": 1},
                headers={"User-Agent": "ZoeAI/1.0"},
            )
            data = r.json()
            if data:
                return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as exc:
        logger.debug("geocode failed for %r: %s", address, exc)
    return None


def auto_extract_components(text: str) -> list[dict]:
    """
    Analyse `text` and return a list of AG-UI component payloads.
    Each item is a dict like ``{"component": "price_table", "props": {...}}``.
    """
    components: list[dict] = []

    # ── 1. Price tables from markdown ─────────────────────────────────────────
    for match in _MD_TABLE_RE.finditer(text):
        table_text = match.group(0)
        if not _PRICE_RE.search(table_text):
            continue
        raw_rows = _parse_md_table(table_text)
        if not raw_rows:
            continue
        price_rows = _table_to_price_rows(raw_rows)
        if not price_rows:
            continue
        # Mark cheapest
        def _price_val(row: dict) -> float:
            s = _PRICE_RE.search(row.get("price", ""))
            return float(s.group(0).replace("$", "").replace(",", "")) if s else 9999.0

        price_rows.sort(key=_price_val)
        if price_rows:
            price_rows[0]["best"] = True
        components.append({"component": "price_table", "props": {"rows": price_rows}})

    # ── 2. Location lists → map_embed ─────────────────────────────────────────
    # Only emit a map if the markers already have lat/lng (e.g. from Pi Agent's
    # show_map tool). We skip Nominatim geocoding here to avoid adding network
    # latency to the SSE stream. Auto-geocoding can be added as a background task
    # in a future iteration.
    # (Placeholder — no-op for auto-extraction; Pi Agent / show_map tool handles maps)

    # ── 3. Option lists → action_menu ─────────────────────────────────────────
    # Look for a short bulleted section at the end of the response
    # (max 5 options, each under 80 chars)
    tail = text[-800:]  # check only the last portion
    bullet_items = _BULLET_LIST_RE.findall(tail)
    if 2 <= len(bullet_items) <= 5 and all(len(b) < 80 for b in bullet_items):
        # Only emit if the text ends with (or near) these bullets — i.e. they're
        # a genuine "what would you like to do next?" section
        last_bullet_end = max(
            (m.end() for m in _BULLET_LIST_RE.finditer(tail)), default=0
        )
        if last_bullet_end >= len(tail) - 80:
            icons = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
            options = [
                {"icon": icons[i], "label": item.strip(), "message": item.strip()}
                for i, item in enumerate(bullet_items)
            ]
            components.append({
                "component": "action_menu",
                "props": {"prompt": "What would you like to do?", "options": options},
            })

    return components
