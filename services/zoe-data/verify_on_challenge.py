"""Back a claim up when the user challenges it — flag-dark ``ZOE_VERIFY_ON_CHALLENGE``.

Live 2026-10-04: a sports-history question got a wrong winner; the user said
"are you sure" and Zoe doubled down ("I'm pretty sure"). The Flue brain has a
``web_search`` tool but a 4B model rarely elects to call it to check ITSELF.
This puts the check in the seam, in code:

  1. the message is a short standalone challenge ("are you sure", "that's
     wrong", "really?", "I don't think so");
  2. the PREVIOUS exchange in this session was a checkable world-fact claim —
     the previous user question is a world-knowledge question (no personal
     anchor, not a tool/keyword intent, not own-fact/evidence) and the previous
     assistant turn actually asserted something (not a tool confirmation, not a
     decline);
  3. ONE web search, bounded at ``_SEARCH_TIMEOUT_S`` (8 s), built from the
     previous user question, through the broker's search entry point
     (``browser_broker.search_web``);
  4. results -> a delimited block appended after the user's words telling the
     brain to say whether it was right, give the right fact and NAME THE SOURCE
     DOMAIN; no results / timeout / error -> a short honest reply, no brain call
     ("I can't check that right now").

Flag off (the default) = ``prepare`` returns ``None`` before any read: no DB
read, no search, no change to the outbound bytes. ``prepare`` NEVER raises. The
question and the search text are never logged — only lengths and statuses.

The block reuses the registered ``[MEMORY CONTEXT`` open/close pair so the
sidecar elides it from every message but the newest (no new entry in
``FLUE_CONTEXT_BLOCKS``, no sidecar change).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

ENV_FLAG = "ZOE_VERIFY_ON_CHALLENGE"
_SEARCH_TIMEOUT_S = 8.0  # the whole check, hard-walled (≤ 8 s by contract)
_PROVIDER_BUDGET_S = 7.4  # the lookup's own bound; search_web adds its 0.5 s wall
_MAX_CHALLENGE_CHARS = 60
_MAX_QUERY_CHARS = 200
_MAX_ROWS = 3
_SNIPPET_CHARS = 240

BLOCK_OPEN = (
    "[MEMORY CONTEXT — live web check of the claim the user just challenged; "
    "use it to answer; do not mention this block]"
)
BLOCK_CLOSE = "[END MEMORY CONTEXT]"

CANT_CHECK_REPLY = (
    "I can't check that right now, so please treat my last answer as unconfirmed "
    "rather than certain."
)

# ── challenge shapes ─────────────────────────────────────────────────────────
# Short, standalone push-back at the previous answer. Anchored and length-capped
# so "sure, do that" / "make sure the light is off" / "I really like it" never
# match (cf. zoe_agent._VERIFY_CHALLENGE_RE, the legacy chat lane's narrower set;
# this adds the disagreement forms — "that's wrong", "I don't think so").
_CHALLENGE_RE = re.compile(
    r"^\W*(?:(?:um+|uh+|hmm+|but|no|nah|wait|hang\s+on|oh|so|ok(?:ay)?)[\s,.!…-]+)*(?:"
    r"(?:are|r)\s+(?:you|u)\s+(?:really\s+|absolutely\s+|completely\s+)?(?:sure|certain|positive)"
    r"(?:\s+about\s+that)?"
    r"|you\s+sure(?:\s+about\s+that)?"
    r"|sure\s*\?"
    r"|really\s*\?+"
    r"|seriously\s*\?+"
    r"|is\s+that\s+(?:right|true|correct|actually\s+(?:right|true|correct))"
    r"|(?:that|this|it)(?:['’]s|\s+is)\s+(?:not\s+(?:right|true|correct)|wrong|incorrect|not\s+what\s+i\s+heard)"
    r"|(?:that|this|it)\s+(?:doesn['’]?t|does\s+not)\s+(?:sound|seem)\s+(?:right|correct|true)"
    r"|i\s+(?:don['’]?t|do\s+not)\s+think\s+(?:so|that['’]?s\s+(?:right|correct|true))"
    r"|i\s+think\s+(?:you['’]?re|you\s+are)\s+(?:wrong|mistaken)"
    r"|(?:you['’]?re|you\s+are)\s+(?:wrong|mistaken)"
    r"|(?:can|could)\s+you\s+(?:double[-\s]?check|verify|confirm)(?:\s+that)?"
    r"|double[-\s]?check\s+(?:that|it)"
    # a short counter-claim may follow: "I don't think so, it was the other team"
    r")(?:[\s,.;:!?-]+(?:i|it|that|they|he|she|pretty|but|no)\b[^?!.]{0,36})?\W*$",
    re.IGNORECASE,
)


def _strip_intent_hint(message: str) -> str:
    """Drop a leading balanced ``[Intent hint: …]`` prefix (chat.py adds it on
    the streaming path; its repr can hold nested brackets, so scan by depth)."""
    if not message.startswith("[Intent hint:"):
        return message
    depth = 0
    for i, ch in enumerate(message):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return message[i + 1:].lstrip()
    return message


def enabled() -> bool:
    """``ZOE_VERIFY_ON_CHALLENGE`` — default OFF. Per-call env read."""
    return (os.environ.get("ZOE_VERIFY_ON_CHALLENGE") or "").strip().lower() in {"1", "true", "yes", "on"}


def is_challenge(message: str) -> bool:
    """True when ``message`` is a short standalone push-back at the last answer."""
    msg = _strip_intent_hint((message or "").strip()).strip()
    if not msg or len(msg) > _MAX_CHALLENGE_CHARS:
        return False
    return bool(_CHALLENGE_RE.match(msg))


# ── is the previous exchange a checkable claim? ──────────────────────────────
# A previous assistant turn that is a tool confirmation or a non-answer is not a
# claim to verify. Prefix/phrase table, deliberately small.
_NON_CLAIM_RE = re.compile(
    r"^\W*(?:reminder\s+set|added\b|done\b|found:|no\s+contacts|listening|timer\b|"
    r"i['’]ve\s+(?:added|set|noted|saved|put)|okay\b|ok\b|sure\b|got\s+it|"
    r"sorry|i\s+(?:don['’]?t|do\s+not|can['’]?t|cannot|couldn['’]?t)\b|i['’]m\s+not\s+sure|"
    r"i\s+have\s+no|no\s+idea|(?:i|we)\s+can\s+check)",
    re.IGNORECASE,
)
_MIN_CLAIM_CHARS = 12


def _keyword_intent(text: str) -> bool:
    """True when the deterministic keyword detector owns ``text`` (a tool turn).
    Fails open to False (cannot tell -> let the other rules decide)."""
    try:
        from intent_router import detect_intent

        return detect_intent(text, log_miss=False) is not None
    except Exception:  # noqa: BLE001
        return False


def previous_turn_is_claim(prev_user: str, prev_assistant: str) -> bool:
    """True when the previous exchange was a world-fact question answered with
    a factual assertion — not a tool result, not the user's own memory."""
    pu = (prev_user or "").strip()
    pa = (prev_assistant or "").strip()
    if not pu or len(pa) < _MIN_CLAIM_CHARS or _NON_CLAIM_RE.match(pa):
        return False
    from memory_gate import is_evidence_question, is_own_fact_question
    from trivia_gate import is_world_trivia

    if is_own_fact_question(pu) or is_evidence_question(pu):
        return False
    return is_world_trivia(pu) and not _keyword_intent(pu)


def build_query(prev_user: str) -> str:
    """ONE search query from the previous user question: pleasantries and the
    question mark stripped, capped. Pure."""
    q = _strip_intent_hint((prev_user or "").strip())
    q = re.sub(r"^\W*(?:(?:hey|hi|ok|okay|so|um+|zoe)[\s,]+)+", "", q, flags=re.IGNORECASE)
    q = re.sub(r"^(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?(?:tell\s+me|remind\s+me)\s+", "", q,
               flags=re.IGNORECASE)
    q = re.sub(r"[?!.\s]+$", "", q).strip()
    return q[:_MAX_QUERY_CHARS]


# ── previous turn from the session history ───────────────────────────────────
async def _load_previous_exchange(session_id: str) -> tuple[str, str]:
    """(previous user question, previous assistant answer) from ``chat_messages``
    for this session, or ("", ""). The current user message may or may not be
    stored yet: walk back to the newest assistant row, then to the user row
    before it. Never raises."""
    sid = (session_id or "").strip()
    if not sid:
        return "", ""
    try:
        from database import get_db_ctx

        async with get_db_ctx() as db:
            cur = await db.execute(
                "SELECT role, content FROM chat_messages WHERE session_id = ? "
                "ORDER BY created_at DESC LIMIT 6",
                (sid,),
            )
            rows = await cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("verify_on_challenge: history read failed (%s)", type(exc).__name__)
        return "", ""
    seq = [(str(r[0]), str(r[1] or "")) for r in rows]
    for i, (role, content) in enumerate(seq):
        if role == "assistant":
            for role2, content2 in seq[i + 1:]:
                if role2 == "user":
                    return content2, content
            break
    return "", ""


# ── search ───────────────────────────────────────────────────────────────────
async def _search(query: str) -> dict[str, Any]:
    """The broker's single bounded search (patched in tests)."""
    from browser_broker import search_web

    return await search_web(query, max_results=5, timeout_s=_PROVIDER_BUDGET_S)


def _clip(text: str, n: int) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _safe_line(text: str) -> str:
    """A single-line, bracket-free rendering so web text can never forge a
    block delimiter or a new instruction line."""
    return re.sub(r"[\[\]\r\n]+", " ", text or "")


def build_block(prev_user: str, prev_assistant: str, rows: list[dict[str, str]]) -> str:
    """The delimited block for a successful lookup. Pure."""
    lines = [
        BLOCK_OPEN,
        f"The user asked: {_clip(_safe_line(prev_user), 200)}",
        f"Your earlier answer was: {_clip(_safe_line(prev_assistant), 240)}",
        "Live search results:",
    ]
    for r in rows[:_MAX_ROWS]:
        lines.append(f"- {r.get('domain', '')}: {_clip(_safe_line(r.get('snippet') or r.get('title') or ''), _SNIPPET_CHARS)}")
    lines.append(
        "Say plainly whether your earlier answer was right, or give the corrected fact. "
        "Name the source by its site (for example \"according to <domain>\"). "
        "If the results do not settle it, say you could not confirm it. "
        "One or two short sentences; do not say you are \"pretty sure\"; "
        "do not repeat your earlier answer as if the check never happened."
    )
    lines.append(BLOCK_CLOSE)
    return "\n".join(lines)


@dataclass
class VerifyPlan:
    """What the seam does with this turn: ``reply`` (answer now, no brain call)
    xor ``block`` (append to the brain message)."""

    reply: str = ""
    block: str = ""
    status: str = ""
    domains: list[str] = field(default_factory=list)


async def prepare(message: str, user_id: str, session_id: str) -> Optional[VerifyPlan]:
    """The verification plan for this turn, or ``None`` (leave the turn alone).
    Flag off / not a challenge / previous turn not a checkable claim -> ``None``
    with no search. NEVER raises."""
    try:
        if not enabled() or not is_challenge(message):
            return None
        prev_user, prev_assistant = await _load_previous_exchange(session_id)
        if not previous_turn_is_claim(prev_user, prev_assistant):
            logger.info("VERIFY_CHALLENGE session=%s decision=skip reason=not_a_claim", session_id)
            return None
        query = build_query(prev_user)
        if not query:
            return None
        try:
            res = await asyncio.wait_for(_search(query), timeout=_SEARCH_TIMEOUT_S)
        except asyncio.TimeoutError:
            res = {"status": "timeout", "results": [], "domains": []}
        rows = [r for r in (res.get("results") or []) if isinstance(r, dict) and r.get("domain")]
        status = str(res.get("status") or "error")
        if status == "results" and rows:
            logger.info("VERIFY_CHALLENGE session=%s decision=search status=results rows=%d query_len=%d",
                        session_id, len(rows), len(query))
            return VerifyPlan(block=build_block(prev_user, prev_assistant, rows), status="results",
                              domains=list(dict.fromkeys(r["domain"] for r in rows[:_MAX_ROWS])))
        logger.info("VERIFY_CHALLENGE session=%s decision=search status=%s query_len=%d",
                    session_id, status, len(query))
        return VerifyPlan(reply=CANT_CHECK_REPLY, status=status)
    except Exception as exc:  # noqa: BLE001 - the check must never break a turn
        logger.warning("verify_on_challenge.prepare failed (non-fatal): %s", type(exc).__name__)
        return None
