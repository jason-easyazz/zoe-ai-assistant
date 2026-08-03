"""Token-budgeted result packet — the piece oh-my-pi does NOT have.

oh-my-pi renders search results for a frontier coding model with a large
context window (`search/render.ts` emits full markdown with untruncated
snippets). Zoe's brain is Gemma 4 E4B with an 8k window that is already
carrying a system prompt, memory packet, tool schemas and conversation
history. A 10-result markdown dump would evict the conversation.

So the packet is budgeted in the formatter, not hoped for in the prompt:
a hard character ceiling, per-result truncation on a sentence boundary, and
enough sources for the brain to say "according to X" without inventing one.

The budget is expressed in CHARACTERS with a documented divisor rather than a
real tokenizer: the divisor is a deliberate underestimate so the ceiling is
conservative, and importing a tokenizer for a ~4-line estimate would add a
dependency to the voice path for no accuracy that matters.
"""

from __future__ import annotations

import re

from .engines import Result
from .scrapers import Extract

# Conservative chars-per-token for English prose. Real ratio is ~4.0; 3.2
# under-estimates so the packet lands UNDER budget rather than over.
CHARS_PER_TOKEN = 3.2

DEFAULT_TOKEN_BUDGET = 350
MAX_SOURCES = 4
MAX_SNIPPET_CHARS = 180


def _truncate(text: str, limit: int) -> str:
    """Cut to `limit`, preferring the last sentence or word boundary."""
    text = text.strip()
    if len(text) <= limit:
        return text
    window = text[: limit + 1]
    for boundary in (". ", "! ", "? "):
        idx = window.rfind(boundary)
        if idx > limit * 0.5:
            return window[: idx + 1].strip()
    idx = window.rfind(" ")
    return (window[:idx] if idx > 0 else window[:limit]).rstrip(" ,;:") + "…"


def _host(url: str) -> str:
    match = re.match(r"https?://(?:www\.)?([^/]+)", url)
    return match.group(1) if match else url


def format_packet(
    results: list[Result],
    *,
    extract: Extract | None = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    max_sources: int = MAX_SOURCES,
) -> str:
    """Render results into a compact, budget-capped packet for the brain.

    Shape (one source per line, host not full URL — the brain speaks the host
    aloud and never reads a URL out):

        [web] 3 sources for "capital of australia"
        1. Canberra - Wikipedia (en.wikipedia.org) — Canberra is the capital…
        2. Canberra | Britannica (britannica.com) — federal capital of…
    """
    if not results and extract is None:
        return ""

    budget_chars = int(token_budget * CHARS_PER_TOKEN)
    lines: list[str] = []

    # A structured extract is strictly better evidence than a search snippet,
    # so it leads and gets the larger share of the budget.
    if extract is not None:
        head = _truncate(extract.text, min(budget_chars // 2, 400))
        lines.append(f"[{extract.source}] {extract.title}: {head}")

    used = sum(len(line) + 1 for line in lines)
    shown = 0
    body: list[str] = []
    for result in results[:max_sources]:
        snippet = _truncate(result.snippet, MAX_SNIPPET_CHARS)
        line = f"{shown + 1}. {result.title} ({_host(result.url)})"
        if snippet:
            line += f" — {snippet}"
        # Reserve room for the header line we prepend below.
        if used + len(line) + 1 > budget_chars - 60:
            break
        body.append(line)
        used += len(line) + 1
        shown += 1

    if shown:
        lines.append(f"[web] {shown} source{'s' if shown != 1 else ''}:")
        lines.extend(body)

    return "\n".join(lines)


def estimate_tokens(packet: str) -> int:
    """Conservative token estimate for a rendered packet."""
    return int(len(packet) / CHARS_PER_TOKEN) + 1
