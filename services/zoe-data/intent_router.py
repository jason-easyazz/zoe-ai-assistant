"""
Intent-first router: pattern-matches common family data requests and calls
mcporter-safe directly, bypassing the LLM for <1 second responses.
Falls through to the agent path (Hermes by default; OpenClaw only when explicitly enabled).

Inspired by the original Zoe HassIL intent system (Tier 0/1 classification).

Pattern priority: domain-specific (calendar, reminder, contact, note) checked
BEFORE generic list patterns to avoid collisions like "what's on my calendar"
matching as list_show.
"""
import asyncio
import json
import logging
import math
import os
import re
import shlex
import subprocess
import time
import shutil
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from fastapi import HTTPException

from time_utils import today_for_zoe_tz
from zoe_pi_promotion import LOW_RISK_PI_INTENT_GROUPS

if TYPE_CHECKING:
    from conversation_context import ConversationContext

logger = logging.getLogger(__name__)

_DAILY_BRIEFING_RESPONSE_CACHE: dict[str, tuple[float, str]] = {}
_DAILY_BRIEFING_CACHE_TTL_SECONDS = float(os.environ.get("ZOE_DAILY_BRIEFING_CACHE_TTL_SECONDS", "120"))
_DAILY_BRIEFING_CACHE_MAX_USERS = max(1, int(os.environ.get("ZOE_DAILY_BRIEFING_CACHE_MAX_USERS", "64")))


def _daily_briefing_cache_sweep(now: float | None = None) -> None:
    if not _DAILY_BRIEFING_RESPONSE_CACHE:
        return
    current = time.time() if now is None else now
    expired = [
        user_id
        for user_id, (stored_at, _response) in _DAILY_BRIEFING_RESPONSE_CACHE.items()
        if (current - stored_at) > _DAILY_BRIEFING_CACHE_TTL_SECONDS
    ]
    for user_id in expired:
        _DAILY_BRIEFING_RESPONSE_CACHE.pop(user_id, None)
    while len(_DAILY_BRIEFING_RESPONSE_CACHE) > _DAILY_BRIEFING_CACHE_MAX_USERS:
        oldest_user = min(_DAILY_BRIEFING_RESPONSE_CACHE, key=lambda key: _DAILY_BRIEFING_RESPONSE_CACHE[key][0])
        _DAILY_BRIEFING_RESPONSE_CACHE.pop(oldest_user, None)


def _daily_briefing_cache_get(user_id: str) -> Optional[str]:
    if _DAILY_BRIEFING_CACHE_TTL_SECONDS <= 0:
        return None
    _daily_briefing_cache_sweep()
    cached = _DAILY_BRIEFING_RESPONSE_CACHE.get(user_id)
    if not cached:
        return None
    stored_at, response = cached
    if (time.time() - stored_at) > _DAILY_BRIEFING_CACHE_TTL_SECONDS:
        _DAILY_BRIEFING_RESPONSE_CACHE.pop(user_id, None)
        return None
    return response


def _daily_briefing_cache_set(user_id: str, response: str) -> None:
    if _DAILY_BRIEFING_CACHE_TTL_SECONDS <= 0 or not response:
        return
    _daily_briefing_cache_sweep()
    _DAILY_BRIEFING_RESPONSE_CACHE[user_id] = (time.time(), response)
    _daily_briefing_cache_sweep()


def _spoken_day_ordinal(day: int) -> str:
    ordinals = {
        1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
        6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
        11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth", 15: "fifteenth",
        16: "sixteenth", 17: "seventeenth", 18: "eighteenth", 19: "nineteenth", 20: "twentieth",
        21: "twenty-first", 22: "twenty-second", 23: "twenty-third", 24: "twenty-fourth", 25: "twenty-fifth",
        26: "twenty-sixth", 27: "twenty-seventh", 28: "twenty-eighth", 29: "twenty-ninth", 30: "thirtieth",
        31: "thirty-first",
    }
    return ordinals.get(day, str(day))

# Short user phrases → expanded OpenClaw task (see openclaw_user_message in chat router).
# Imperative: models often answer "setup home automation" with generic menus unless told not to.
HA_FULL_SETUP_OPENCLAW_MESSAGE = (
    "[HA_PLATFORM_BOOTSTRAP — execute now, minimal chat]\n"
    "Do NOT answer with generic smart-home ideas (lighting lists, security options, routines menus, or "
    "\"what would you like to automate\"). The user asked to set up the Home Assistant platform on this server.\n"
    "Your FIRST tool action: open the browser to http://localhost:8123 and inspect the page (snapshot). "
    "If you see onboarding, complete it. Only if the form requires values you do not have, ask ONCE for "
    "admin username/password; otherwise keep going without extra questions.\n"
    "Then per the home-assistant skill: long-lived token named zoe-data, "
    "sed HA_ACCESS_TOKEN into /home/zoe/assistant/.env, docker restart homeassistant-mcp-bridge, "
    "add http trusted_proxies for the Zoe nginx reverse proxy in homeassistant configuration.yaml, "
    "docker restart homeassistant, confirm control via browser. "
    "Reply briefly only with progress/status — not a catalog of automation ideas."
)

# Zoe self-extension: build a new widget, page, or capability. Admin-gated.
# openclaw_user_message routes each to the right builder skill. execute_intent
# returns None so the request falls through to OpenClaw.
_BUILD_WIDGET_OPENCLAW_MSG = (
    "[ZOE_SELF_BUILD: widget]\n"
    "The user asked to build a new dashboard widget. Follow the `zoe-widget-builder` "
    "skill: check admin role, call zoe_self_capabilities to confirm the widget does "
    "not already exist, draft a spec, plan-then-confirm with the user, stage via "
    "scripts/preview/stage_widget.py, and hand off to the zoe-verify skill. Do NOT "
    "write any file outside services/zoe-ui/dist/_preview/ until verify approves.\n"
    "\n"
    "OUTPUT CONTRACT — this is mandatory:\n"
    "• Your final reply must be ≤2 short sentences (e.g. \"Here's a moon-phase widget — does it look right?\").\n"
    "• NEVER include code fences (```), JS, HTML, CSS, or any file contents in the chat reply. The user wants to SEE the widget working, not read its source.\n"
    "• After stage_widget.py prints its preview_url, emit exactly these two blocks at the end of your reply (literal text, no substitutions other than the URL/task_id you got back):\n"
    "  :::zoe-ui\n  {\"action\":\"navigate\",\"url\":\"<preview_url>\",\"target\":\"iframe\"}\n  :::\n"
    "  :::zoe-ui\n  {\"action\":\"orb_prompt\",\"prompt\":\"Here's your widget. Does it look right?\",\"auto_mic\":true,\"task_id\":\"<task_id>\"}\n  :::"
)
_BUILD_PAGE_OPENCLAW_MSG = (
    "[ZOE_SELF_BUILD: page]\n"
    "The user asked to build a new Zoe page. Follow the `zoe-page-builder` skill: "
    "admin role check, call zoe_self_capabilities to confirm the slug is free, "
    "plan-then-confirm, stage via scripts/preview/stage_page.py (generates desktop "
    "+ touch variants), and hand off to zoe-verify. Only promote after approval.\n"
    "\n"
    "OUTPUT CONTRACT — this is mandatory:\n"
    "• Your final reply must be ≤2 short sentences (e.g. \"Here's the page — take a look.\").\n"
    "• NEVER include code fences (```), HTML, JS, CSS, or any file contents. The preview iframe is the evidence.\n"
    "• After stage_page.py prints its preview_url, emit exactly these two blocks at the end of your reply:\n"
    "  :::zoe-ui\n  {\"action\":\"navigate\",\"url\":\"<preview_url>\",\"target\":\"iframe\"}\n  :::\n"
    "  :::zoe-ui\n  {\"action\":\"orb_prompt\",\"prompt\":\"Here's your page. Does it look right?\",\"auto_mic\":true,\"task_id\":\"<task_id>\"}\n  :::"
)
_EXTEND_CAPABILITY_OPENCLAW_MSG = (
    "[ZOE_SELF_BUILD: capability]\n"
    "The user asked for a capability Zoe doesn't currently have. Follow the "
    "`zoe-capability-extender` skill: admin role check, search installed skills + "
    "openclaw plugins + ClawHub before composing anything new. If composing, stage "
    "under ~/.openclaw/workspace/_skill_preview/ and run a smoke test before install.\n"
    "\n"
    "OUTPUT CONTRACT — this is mandatory:\n"
    "• Final reply is ≤2 short sentences describing WHAT new capability Zoe now has — not HOW you wired it.\n"
    "• NEVER include code fences (```) or skill source in the chat reply."
)

MCPORTER = shutil.which("mcporter-safe") or os.path.expanduser("~/bin/mcporter-safe")
# Resolve the Node bin dir dynamically — prefer the directory containing the located binary,
# fall back to the nvm path so existing installs don't break.
_node_which = shutil.which("node")
NODE_BIN = os.path.dirname(_node_which) if _node_which else os.path.expanduser("~/.nvm/versions/node/v22.22.0/bin")

SHOPPING_KEYWORDS = {
    "milk", "eggs", "bread", "butter", "cheese", "chicken", "beef", "pork",
    "vegetables", "fruit", "apples", "bananas", "oranges", "groceries",
    "cereal", "coffee", "tea", "sugar", "flour", "rice", "pasta",
    "toilet paper", "soap", "shampoo", "detergent", "paper towels",
    "snacks", "juice", "yogurt", "cream", "oil", "water", "soda",
}

EVENT_CATEGORY_HINTS = {
    "health": {
        "doctor", "dr", "dentist", "dental", "hospital", "clinic",
        "gp", "specialist", "physio", "physiotherapy", "chiro",
        "chiropractor", "therapy", "psychologist", "psychiatrist", "medical",
    },
    "work": {"interview", "1:1", "standup", "sprint", "retro", "office"},
    "family": {"school", "kids", "daycare", "parent-teacher", "family"},
}


def _infer_event_category(text: str) -> str:
    t = text.lower()
    for category, hints in EVENT_CATEGORY_HINTS.items():
        for h in hints:
            if re.search(rf"\b{re.escape(h)}\b", t):
                return category
    return "general"


def _normalize_chat_intent_text(raw: str) -> str:
    """Lowercase, Unicode-normalize, collapse whitespace, strip STT filler artifacts."""
    s = unicodedata.normalize("NFKC", (raw or "").strip()).lower()
    s = re.sub(r"\s+", " ", s).strip()
    # Strip a leading wake word the panel sometimes leaves in the transcript
    # ("hey zoe, what's the weather" → "what's the weather"). Without this, every
    # $-anchored fast-path regex misses and the whole turn falls through to the
    # slow brain path — the dominant cause of "voice feels slow" on common commands.
    # [\s,.!?:;-]* also eats trailing punctuation, since some STT models render
    # the wake word as its own sentence ("Zoe. What's the weather").
    s = re.sub(r"^(?:hey|hi|hello|ok|okay|yo|hiya)\s+zoe\b[\s,.!?:;-]*", "", s)
    s = re.sub(r"^zoe\b[\s,.!?:;-]*", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Replace "^and" → "add" ONLY when followed by a list-add phrasing ("X to [list-type] list").
    # STT frequently mishears "add" as "and" for Australian accents (e.g. "add bread" → "and bread").
    # The lookahead prevents regressions: "and turn on the lights" is NOT affected.
    s = re.sub(
        r"^and\s+(?=\S.+?\s+to\s+(?:the\s+|my\s+)?(?:shopping|grocery|groceries|personal|work|bucket|todo|tasks)\b)",
        "add ",
        s,
    )
    # Strip other common STT preamble artifacts at the start of transcripts.
    s = re.sub(r"^(?:uh+|um+|oh|so|yeah|okay|ok|right|well)\s+", "", s)
    # Strip trailing punctuation so $-anchored patterns match STT transcripts ending with ".".
    s = re.sub(r"[.!?…]+$", "", s).strip()
    return s


# Matches short setup commands; whole message must be this request (no extra clauses).
_HA_FULL_SETUP_RE = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+|will you\s+|help me\s+|i want to\s+|i need(?: you)? to\s+)?"
    r"(?:(?:setup|set up|configure|install)\s+home\s+(?:assistant|automation)|"
    r"home\s+(?:assistant|automation)\s+(?:setup|installation)|"
    r"(?:setup|set up)\s+hass|"
    r"(?:set up|create|add|make|build)\s+(?:a\s+)?(?:new\s+)?automation\s+in\s+home\s+assistant|"
    r"(?:create|add|make|build)\s+(?:a\s+)?home\s+assistant\s+automation)\s*"
    r"(?:for\s+me\s*)?(?:please\s*)?[!?.…]*\s*$",
    re.IGNORECASE,
)

_ENGINEERING_TASK_RE = re.compile(
    r"^(?:offload|delegate|assign|ask)\s+(?:this\s+)?(?:to\s+)?hermes\s+(?:to\s+)?(?P<task>.+)$"
    r"|^(?:create|start|queue)\s+(?:an?\s+)?engineering\s+task\s+(?:for\s+)?(?P<task2>.+)$",
    re.I,
)

_ENGINEERING_STATUS_RE = re.compile(
    r"\b(?:engineering|hermes)\s+(?:task|workflow|pr)\s+(?:status|progress)\b"
    r"|\bwhat'?s?\s+(?:the\s+)?(?:hermes|engineering)\s+(?:status|progress)\b",
    re.I,
)


def _is_ha_full_setup_message(t: str) -> bool:
    """True when the user wants the full HA bootstrap (onboarding, token, proxy trust, bridge)."""
    s = _normalize_chat_intent_text(t)
    if not s:
        return False
    if _HA_FULL_SETUP_RE.match(s):
        return True
    # Exact phrases after normalization (backstop if regex ever misses)
    cores = (
        "setup home assistant",
        "set up home assistant",
        "configure home assistant",
        "home assistant setup",
        "setup home automation",
        "set up home automation",
        "configure home automation",
        "home automation setup",
        "install home assistant",
        "set up hass",
        "setup hass",
        # Automation creation within an existing HA instance → OpenClaw browser
        "set up a new automation in home assistant",
        "create an automation in home assistant",
        "create a new automation in home assistant",
        "add an automation in home assistant",
        "make an automation in home assistant",
        "set up automation in home assistant",
        "create home assistant automation",
        "new automation in home assistant",
    )
    s2 = s.rstrip(".!?…").strip()
    if s2 in cores:
        return True
    for prefix in (
        "please ",
        "can you ",
        "could you ",
        "will you ",
        "i need you to ",
        "i need to ",
        "help me ",
        "i want to ",
    ):
        if s2.startswith(prefix):
            inner = re.sub(r"\s+", " ", s2[len(prefix) :].strip().rstrip(".!?…").strip())
            if inner in cores:
                return True
    return False


@dataclass
class Intent:
    name: str
    slots: dict = field(default_factory=dict)
    confidence: float = 0.9


def openclaw_user_message(intent: Optional[Intent], user_text: str) -> str:
    """Map intent + user text to the string sent to OpenClaw (expand shorthand tasks)."""
    if intent is not None:
        if intent.name == "ha_full_setup":
            return HA_FULL_SETUP_OPENCLAW_MESSAGE
        if intent.name == "connect_chatgpt":
            return _CONNECT_CHATGPT_OPENCLAW_MSG
        if intent.name == "build_widget":
            return f"{_BUILD_WIDGET_OPENCLAW_MSG}\n\nOriginal request: {user_text}"
        if intent.name == "build_page":
            return f"{_BUILD_PAGE_OPENCLAW_MSG}\n\nOriginal request: {user_text}"
        if intent.name == "extend_capability":
            return f"{_EXTEND_CAPABILITY_OPENCLAW_MSG}\n\nOriginal request: {user_text}"
        if intent.name == "self_improve":
            return _SELF_IMPROVE_OPENCLAW_MSG
    # Same phrases when intent fast path is off (e.g. force_openclaw) — still expand.
    tnorm = _normalize_chat_intent_text(user_text)
    if _is_ha_full_setup_message(tnorm):
        return HA_FULL_SETUP_OPENCLAW_MESSAGE
    return user_text


_TIME_QUERY_RE = re.compile(
    r"^(what'?s?\s+the\s+time|what\s+time\s+is\s+it(\s+(right\s+now|now))?"
    r"|tell\s+me\s+the\s+time|current\s+time|time\s+now|time\s+please|what\s+time\s+is\s+it"
    r"|do\s+you\s+have\s+the\s+time|(?:can\s+you\s+)?give\s+me\s+the\s+time"
    r"|what\s+hour\s+is\s+it)\??$",
    re.IGNORECASE,
)

_DATE_QUERY_RE = re.compile(
    r"^(what'?s?\s+today'?s?\s+date|what\s+(is\s+)?the\s+date(\s+today)?"
    r"|what\s+day\s+(is\s+it(\s+today)?|of\s+the\s+week(\s+is\s+it)?)|today'?s?\s+date"
    r"|what\s+year\s+is\s+it(\s+now)?|what\s+month\s+is\s+it(\s+now)?"
    r"|day\s+of\s+the\s+week|what'?s?\s+the\s+date(\s+today)?"
    r"|(?:show\s+me|tell\s+me)\s+(?:the\s+|today'?s?\s+)?date|what'?s?\s+today)\??$",
    re.IGNORECASE,
)

_TIME_PLANNING_CLARIFICATION_RE = re.compile(
    r"^(?:what(?:'s|\s+is)\s+)?(?:the\s+)?best\s+time\s+to\s+(?:leave|go|head\s+out)\b",
    re.IGNORECASE,
)

_TIME_MATH_CLARIFICATION_RE = re.compile(
    r"^(?:what(?:'s|\s+is)\s+)?(?:the\s+)?(?:meeting|travel|arrival|departure)\s+time\s+"
    r"(?:plus|minus|\+|-)\s+(?:the\s+)?(?:meeting|travel|arrival|departure)\s+time\b",
    re.IGNORECASE,
)


_CONNECT_CHATGPT_RE = re.compile(
    r"^(?:can you |please |could you )?(?:connect|link|auth(?:orize|orise|)?|set\s*up|add|enable)\b"
    r".*\b(?:chatgpt|openai|codex|gpt)\b",
    re.IGNORECASE,
)
_CONNECT_CHATGPT_OPENCLAW_MSG = (
    "[ZOE_CONNECT: chatgpt_oauth]\n"
    "The user wants to connect ChatGPT / OpenAI Codex to Zoe via OAuth.\n"
    "Run: openclaw onboard --non-interactive 2>/dev/null || openclaw gateway call system.providers_status\n"
    "1. Get the OAuth authorization URL from openclaw (use: openclaw onboard --print-url 2>/dev/null or parse gateway output).\n"
    "2. Emit exactly ONE :::zoe-ui block with a qr_code component and a link_preview for the URL, plus a status poll:\n"
    "   :::zoe-ui\n"
    '   {"type":"qr_code","title":"Connect ChatGPT to Zoe","message":"Scan to authorise — or tap the link below.","url":"<oauth_url>","id":"chatgpt-auth"}\n'
    "   :::\n"
    "   :::zoe-ui\n"
    '   {"type":"status","title":"Waiting for authorisation…","poll_endpoint":"/api/voice/chatgpt-auth-status","poll_interval_ms":3000,"id":"chatgpt-auth-status"}\n'
    "   :::\n"
    "3. Your verbal reply must be ≤2 short sentences, e.g.: \"Scan the QR code or tap the link to connect your ChatGPT account. I'll update you when it's done.\"\n"
    "4. When openclaw confirms auth success (poll or event), emit:\n"
    "   :::zoe-ui\n"
    '   {"type":"status","title":"ChatGPT Pro connected ✓","message":"Builder skills now use ChatGPT for code generation.","id":"chatgpt-auth-status"}\n'
    "   :::\n"
    "5. NEVER include tokens, keys, or credentials in the chat reply."
)

_BUILD_VERB = r"(?:add|build|create|make|scaffold|generate|put|design|code)"
# Broad match: build-verb at start + the word 'widget' appearing later in the sentence.
_BUILD_WIDGET_RE = re.compile(
    rf"^(?:can you |please |could you )?{_BUILD_VERB}\b.*\bwidget\b",
    re.IGNORECASE,
)
_BUILD_PAGE_RE = re.compile(
    rf"^(?:can you |please |could you )?{_BUILD_VERB}\b.*\bpage\b",
    re.IGNORECASE,
)
# Also catch "I want a <thing> widget/page"
_WANT_WIDGET_RE = re.compile(
    r"^(?:i\s+want|i'?d\s+like|i\s+need)\s+(?:a\s+|an\s+)?(?:new\s+|custom\s+)?.*\bwidget\b",
    re.IGNORECASE,
)
_WANT_PAGE_RE = re.compile(
    r"^(?:i\s+want|i'?d\s+like|i\s+need)\s+(?:a\s+|an\s+)?"
    r"(?:new\s+|custom\s+|dedicated\s+)?.*\bpage\b",
    re.IGNORECASE,
)
_EXTEND_CAPABILITY_RE = re.compile(
    r"^(?:can you |please |could you )?"
    r"(?:teach yourself|learn|add support|extend yourself|gain the ability|install a skill|"
    r"build me the ability|add the ability|learn how)\b",
    re.IGNORECASE,
)

# Self-improvement feedback loop: review intent misses and propose new patterns.
_SELF_IMPROVE_RE = re.compile(
    r"\b(?:"
    r"what\s+(?:are\s+you\s+struggling\s+with|can't\s+you\s+understand|do\s+you\s+(?:get\s+wrong|miss|struggle\s+with))"
    r"|review\s+(?:my|your)\s+intent\s+miss(?:es)?"
    r"|what\s+(?:patterns?|intents?)\s+(?:are\s+you\s+missing|do\s+you\s+miss)"
    r"|improve\s+yourself"
    r"|self[- ]improv(?:e|ement)"
    r"|(?:analyze|analyse)\s+(?:your\s+)?(?:miss(?:es)?|mistakes?|gaps?)"
    r"|what\s+(?:questions?|requests?)\s+(?:do\s+you|have\s+you)\s+(?:mis(?:s(?:ed)?)?|failed)"
    r")\b",
    re.IGNORECASE,
)

# Intent-miss self-improvement task for OpenClaw
_SELF_IMPROVE_OPENCLAW_MSG = (
    "[ZOE_SELF_IMPROVE: intent-miss review]\n"
    "Read ~/training/data/intent-misses.jsonl (last 7 days of misses).\n"
    "Use the self-improvement skill. Analyze the most common miss patterns.\n"
    "Propose 3-5 new regex patterns for intent_router.py based on what you find.\n"
    "Format each proposal as:\n"
    "  Pattern: <regex>\n"
    "  Intent: <intent_name>\n"
    "  Example phrases: <2-3 examples it would match>\n"
    "  Rationale: <why this is worth adding>\n"
    "Be concise and practical. Only propose patterns with 3+ occurrences in the miss log."
)

# "forget that" / "never mind what I said" — retract the last memory written.
_FORGET_LAST_RE = re.compile(
    r"^(?:please\s+)?"
    r"(?:forget\s+(?:that|what\s+i\s+(?:just\s+)?said|what\s+i\s+told\s+you|the\s+last\s+thing|that\s+memory)"
    r"|don'?t\s+remember\s+that"
    r"|scrap\s+(?:that|what\s+i\s+said)"
    r"|delete\s+that(?:\s+memory)?"
    r"|never\s+mind\s+(?:what\s+i\s+(?:just\s+)?said|that))"
    r"\.?\s*$",
    re.IGNORECASE,
)

# "forget what I told you about X" / "forget everything about X" -- entity-scoped
# forget (QA review F14). Deterministic, no LLM. The captured entity must pass
# strict name validation (person_extractor._looks_like_person_name) before the
# intent fires, so "forget about it/that/her" never nukes anything fuzzy.
_FORGET_ENTITY_RE = re.compile(
    r"^(?:please\s+)?"
    r"(?:forget|delete|remove|erase|scrap)\s+"
    r"(?:everything|all(?:\s+of\s+it)?|anything"
    r"|what(?:ever)?\s+i(?:'?ve)?\s+(?:just\s+)?(?:told\s+you|said|mentioned)"
    r"|what\s+you\s+know|your\s+memories|the\s+memories)?\s*"
    r"(?:you\s+know\s+)?about\s+"
    r"(?P<entity>[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,2})"
    r"\s*[.!?]*\s*$",
    re.IGNORECASE,
)

# ── Portrait intents ───────────────────────────────────────────────────────────
# "how well do you know me" / "what do you understand about me" → reveal portrait
_PORTRAIT_REVEAL_RE = re.compile(
    r"(?:how\s+well\s+do\s+you\s+know\s+me"
    r"|what\s+do\s+you\s+(?:really\s+)?(?:know|understand|think)\s+about\s+me"
    r"|what'?s?\s+your\s+understanding\s+of\s+me"
    r"|describe\s+(?:me|who\s+i\s+am)\s+(?:to\s+me|back\s+to\s+me)?"
    r"|how\s+well\s+(?:do\s+you\s+)?understand\s+me"
    r"|what\s+do\s+you\s+see\s+when\s+you\s+think\s+of\s+me)",
    re.IGNORECASE,
)

# "update your understanding" / "rebuild your portrait of me" → regenerate portrait now
_PORTRAIT_REFRESH_RE = re.compile(
    r"(?:update\s+your\s+(?:understanding|knowledge|portrait|model)\s+of\s+me"
    r"|rebuild\s+(?:your\s+)?(?:portrait|model|understanding)\s+of\s+me"
    r"|re(?:generate|build|create)\s+my\s+portrait"
    r"|you\s+(?:have\s+me\s+wrong|don'?t\s+(?:know|understand)\s+me\s+(?:well|properly|correctly)))",
    re.IGNORECASE,
)


# "Let's talk / let's chat / open voice mode" → navigate browser to voice conversation page.
# Uses search() not match() so it works on short STT utterances like "hey zoe let's talk".
_LETS_TALK_RE = re.compile(
    r"\b(let'?s\s+(?:talk|chat|have\s+a\s+(?:conversation|chat))"
    r"|let\s+us\s+(?:talk|chat)"
    r"|(?:open|start|switch\s+to)\s+(?:voice|conversation)\s+mode"
    r"|i\s+want\s+to\s+(?:talk|chat))\b",
    re.IGNORECASE,
)

# === GREETING (ZOE-42, ZOE-15) ===
# Placed after lets_talk so "let's chat" doesn't become a greeting.
# good_morning/good_evening are kept as separate intents for the daily-briefing flow.
_GREETING_RE = re.compile(
    r"^(?:hello|hi|hey|howdy|heya|hiya|greetings|yo|g'?day)(?:\s+(?:there|zoe|there\s+zoe))?\s*[!.,]?\s*$"
    r"|^(?:hello|hi|hey|howdy|heya|hiya|yo)(?:\s+(?:there|zoe|there\s+zoe))?\s+how\s+are\s+you(?:\s+today)?\s*[!.,?]?\s*$"
    r"|^sup(?:\s+zoe)?\s*\??$"
    r"|^what'?s\s+up(?:\s+zoe)?\s*\??\s*$"
    r"|^how\s+are\s+you(?:\s+today)?(?:\s+zoe)?\s*\??\s*$"
    r"|^how'?s\s+it\s+going(?:\s+zoe)?\s*\??\s*$"
    r"|^good\s+(?:afternoon|night)(?:\s+zoe)?\s*[!.,]?\s*$",
    re.IGNORECASE,
)

# Social acknowledgements ("thanks", "got it") and presence checks ("are you
# there?") — instant canned replies so they never wake the ~2-4s brain. All
# anchored ^...$ so they can't swallow a real request ("okay add milk" won't match).
_THANKS_RE = re.compile(
    r"^(?:thanks?|thank\s+you|thank\s+u|thx|ty|cheers|much\s+appreciated)"
    r"(?:\s+(?:so\s+much|a\s+lot|very\s+much|heaps))?(?:\s+zoe)?\s*[!.,]*$",
    re.IGNORECASE,
)
_ACK_RE = re.compile(
    r"^(?:got\s+it|gotcha|understood|makes\s+sense|good\s+to\s+know|noted|fair\s+enough)\s*[!.,]*$"
    r"|^(?:ok|okay|okey|k|alright|cool|nice|great|perfect|awesome|lovely|sweet|sounds\s+good)\s*[!.,]*$"
    r"|^(?:no\s+(?:problem|worries)|all\s+good)\s*[!.,]*$",
    re.IGNORECASE,
)
_STATUS_CHECK_RE = re.compile(
    r"^(?:are\s+you\s+|you\s+)?(?:there|listening|awake|around|still\s+(?:there|listening|awake|with\s+me))\s*\??$"
    r"|^(?:can\s+you\s+)?hear\s+me(?:\s+(?:ok|okay|now))?\s*\??$"
    r"|^(?:are\s+you\s+)?(?:still\s+)?(?:working|online|up|ready)\s*\??$"
    r"|^zoe\s*\??$",
    re.IGNORECASE,
)

# Bare wake-word used as a question ("zoe?", "hey zoe?") = a presence check.
# This must be tested against the RAW text: _normalize_chat_intent_text strips the
# wake word, so by the time the body runs "zoe?" has become "" and _STATUS_CHECK_RE
# never sees it. The trailing '?' is required so a plain vocative "zoe" (or "zoe,
# add milk", which normalizes to a real command) is NOT swallowed as a status check.
_BARE_NAME_QUERY_RE = re.compile(r"^(?:hey\s+|ok(?:ay)?\s+)?zoe\s*\?+$", re.IGNORECASE)

# Open-domain Q&A / creative — route to agent path (not brittle per-phrase intents).
_AGENT_CHAT_RE = re.compile(
    r"^(?:tell me about|what(?:'s| is) the (?:capital|weather)|"
    r"search the web|write me (?:an? )?(?:email|haiku|poem)|"
    r"can you explain|set up (?:a )?new automation|what is happening in|"
    r"tell me (?:a|another) joke|make me laugh|(?:do you |have you )?(?:got|have) any jokes|know any (?:good )?jokes|"
    r"say exactly[: ]+.+)",
    re.IGNORECASE,
)

# === SMART HOME LIGHTS (ZOE-9) ===
# Uses search() not match() so it works mid-sentence ("can you turn off the lights").
_SMART_HOME_RE = re.compile(
    r"\bturn\s+(?:on|off)\s+(?:the\s+)?(?:\w+\s+){0,3}lights?\b"
    r"|\bswitch\s+(?:on|off)\s+(?:the\s+)?(?:\w+\s+){0,3}lights?\b"
    r"|\bdim(?:mer)?\s+(?:the\s+)?(?:\w+\s+){0,3}lights?\b"
    r"|\bbrighten\s+(?:the\s+)?(?:\w+\s+){0,3}lights?\b"
    r"|\blights?\s+(?:on|off)\b"
    r"|\ball\s+lights?\s+off\b"
    r"|\bturn\s+off\s+everything\b",
    re.IGNORECASE,
)

# === MATH / CALCULATION (ZOE-10) ===
# Pure arithmetic fast-path — group 1 captures the numeric expression.
_CALCULATE_RE = re.compile(
    r"^(?:what(?:'?s|\s+is)\s+|calculate\s+|compute\s+|how\s+much\s+is\s+)?"
    r"(-?\d+(?:\.\d+)?\s*(?:[+\-\*\/]\s*-?\d+(?:\.\d+)?)+)\s*[=]?\s*\??$",
    re.IGNORECASE,
)
# "what is 25% of 80" → groups (1=pct, 2=base)
_CALCULATE_PCT_RE = re.compile(
    r"^(?:what(?:'?s|\s+is)\s+|calculate\s+)?(\d+(?:\.\d+)?)\s*(?:percent|%)\s+of\s+(\d+(?:\.\d+)?)\s*\??$",
    re.IGNORECASE,
)
# "what is 10 times 3" / "10 divided by 2" → groups (1=a, 2=op_word, 3=b)
_CALCULATE_WORDS_RE = re.compile(
    r"^(?:what(?:'?s|\s+is)\s+)?(-?\d+(?:\.\d+)?)\s+"
    r"(times|multiplied\s+by|divided\s+by|plus|minus)\s+"
    r"(-?\d+(?:\.\d+)?)\s*\??$",
    re.IGNORECASE,
)

# === TTS / SYSTEM VOLUME — Zoe's own voice (ZOE-13) ===
# Only matches phrases clearly about Zoe's speaking volume, not the music player.
# Placed before _AUTOGEN_UNKNOWN_GAP so these don't fall through to music_control.
_ZOE_VOICE_VOLUME_RE = re.compile(
    r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?speak\s+(?:up|louder|more\s+loudly|quieter|softer|down|more\s+softly)\b"
    r"|^(?:please\s+)?be\s+(?:quieter|louder|softer)\b"
    r"|^(?:please\s+)?(?:lower|raise|increase|decrease)\s+your\s+(?:voice|volume)\b"
    r"|(?:turn|bring|put)\s+your\s+volume\s+(?:up|down)\b"
    r"|your\s+voice\s+is\s+too\s+(?:loud|quiet|high|low)\b"
    # Generic bare "volume up/down" — no music context → system TTS volume
    r"|^(?:please\s+|can\s+you\s+|could\s+you\s+)?volume\s+(?:up|down|louder|quieter)\b"
    r"|^(?:turn\s+(?:the\s+)?volume\s+(?:up|down))\b"
    r"|^(?:volume\s+(?:up|down))\b"
    # Follow-up percentage commands — "make it 80%", "set it to 75%", "you make it 80"
    # ^you? handles STT artifact where recogniser prepends "you"
    r"|^(?:you\s+)?(?:make|set|put)\s+it\s+(?:to\s+|at\s+)?(\d{1,3})\s*%?"
    r"|^(?:set|put)\s+(?:the\s+)?volume\s+(?:to\s+|at\s+)?(\d{1,3})\s*%?"
    r"|^(\d{1,3})\s*(?:percent\b|%)"
    # "turn it up/down to X%" — catches "turn it up to 80%" before music_control does
    r"|(?:turn|bring)\s+it\s+(?:up|down)\s+to\s+(\d{1,3})\s*%?",
    re.IGNORECASE,
)


# === TOUCH PANEL / KIOSK PROVISIONING ===
_PANEL_SETUP_RE = re.compile(
    r"\b(?:set\s?up|connect|pair|add|register|provision|onboard)\b.{0,30}\b(?:touch\s+panel|kiosk|screen|panel|display)\b"
    r"|\b(?:touch\s+panel|kiosk|screen|panel|display)\b.{0,30}\b(?:set\s?up|connect|pair|add|register)\b",
    re.IGNORECASE,
)
_PANEL_STATUS_RE = re.compile(
    r"\b(?:status|check|ping|is it\s+(?:on|online|working)|health)\b.{0,30}\b(?:panel|kiosk|touch\s+screen|display)\b"
    r"|\b(?:panel|kiosk|touch\s+screen|display)\b.{0,30}\b(?:status|check|online|working|ok)\b",
    re.IGNORECASE,
)
_PANEL_LIST_RE = re.compile(
    r"\b(?:list|show|what)\b.{0,20}\b(?:panels?|kiosks?|touch\s+screens?|screens?)\b"
    r"|\bhow many\b.{0,15}\b(?:panels?|kiosks?|screens?)\b",
    re.IGNORECASE,
)
# "connect panel A3F7K2" / "enter code A3F7K2 for the screen"
_PANEL_ENTER_CODE_RE = re.compile(
    r"\b(?:connect|pair|confirm|enter|use|activate)\b.{0,30}\b(?:code|panel)\b.{0,20}\b([A-Z0-9]{6})\b"
    r"|\b([A-Z0-9]{6})\b.{0,30}\b(?:panel|kiosk|screen|display)\b",
    re.IGNORECASE,
)


def detect_intent(
    text: str,
    log_miss: bool = True,
    context: "Optional[ConversationContext]" = None,
    user_id: str = "unknown",
) -> Optional[Intent]:
    t = _normalize_chat_intent_text(text)

    # Bare name-as-question ("zoe?") — normalization strips the wake word to '', so
    # catch the presence check from the raw text before the body runs.
    if _BARE_NAME_QUERY_RE.match((text or "").strip()):
        return Intent("status_check", {})

    # Full Home Assistant / automation setup → OpenClaw (execute_intent returns None; chat expands message)
    if _is_ha_full_setup_message(t):
        return Intent("ha_full_setup", {})

    # Touch panel provisioning / status — chat.py renders AG-UI cards
    if _PANEL_ENTER_CODE_RE.search(t):
        m = _PANEL_ENTER_CODE_RE.search(t)
        code = (m.group(1) or m.group(2) or "").upper()
        return Intent("panel_confirm_code", {"code": code})
    if _PANEL_SETUP_RE.search(t):
        return Intent("panel_setup", {})
    if _PANEL_STATUS_RE.search(t):
        return Intent("panel_status", {})
    if _PANEL_LIST_RE.search(t):
        return Intent("panel_list", {})

    # "forget that" — retract the most recent memory write for the caller.
    # Matched very early so it never collides with other verbs.
    if _FORGET_LAST_RE.match(t):
        return Intent("memory_forget_last", {})

    # "forget everything about X" -- entity-scoped forget (QA review F14).
    # Only fires when the captured entity is name-shaped: "forget about it",
    # "forget about my day" etc. fall through to normal routing.
    m = _FORGET_ENTITY_RE.match(t)
    if m:
        # Prefer the raw text's capture so the name keeps the user's casing
        # ("Mary Jane", not "mary jane"); t is lowercased by normalization.
        _m_raw = _FORGET_ENTITY_RE.match((text or "").strip())
        _entity = ((_m_raw or m).group("entity") or "").strip().rstrip(".!?")
        # Quantifiers / deictics / time words the name validator lets through:
        # "forget about everything|today|it all" must never become a sweep.
        _FORGET_ENTITY_STOP = {
            "everything", "anything", "something", "nothing", "all", "stuff",
            "things", "life", "today", "tomorrow", "yesterday", "now",
            "earlier", "before", "again", "ourselves", "yourself", "myself",
        }
        try:
            from person_extractor import _looks_like_person_name
            _namey = (_entity.lower() not in _FORGET_ENTITY_STOP
                      and _looks_like_person_name(_entity))
        except Exception:
            _namey = False  # validator unavailable -> fail closed, never nuke
        if _namey:
            return Intent("memory_forget_entity", {"name": _entity})

    # Portrait intents — how well Zoe knows the user, and rebuilding understanding.
    if _PORTRAIT_REVEAL_RE.search(t):
        return Intent("portrait_reveal", {})
    if _PORTRAIT_REFRESH_RE.search(t):
        return Intent("portrait_refresh", {})

    # Connect ChatGPT / OpenAI to OpenClaw — admin-gated, handled via AG-UI OAuth flow.
    if _CONNECT_CHATGPT_RE.match(t):
        # Delegation intent — no structured slots to extract; empty dict bypasses
        # the nlu_extractor path in detect_and_extract_intent so it returns the
        # intent directly instead of trying (and failing) to extract slots.
        return Intent("connect_chatgpt", {})

    # Zoe self-extension — always routes to OpenClaw (admin-gated in the skill).
    # Checked BEFORE list/reminder/etc so "add X widget" doesn't become list_add.
    # Empty slots: these are delegation intents with no structured fields to
    # extract, so they must NOT carry {"raw": text} which would send them through
    # nlu_extractor and cause detect_and_extract_intent to return None on failure.
    if _BUILD_WIDGET_RE.match(t) or _WANT_WIDGET_RE.match(t):
        return Intent("build_widget", {})
    if _BUILD_PAGE_RE.match(t) or _WANT_PAGE_RE.match(t):
        return Intent("build_page", {})
    if _EXTEND_CAPABILITY_RE.match(t):
        return Intent("extend_capability", {})
    if _SELF_IMPROVE_RE.search(t):
        return Intent("self_improve", {})

    # "Let's talk" → navigate the browser panel to voice.html?conv=1 (conversation mode).
    # Placed before greetings so "let's chat" doesn't become a greeting.
    if _LETS_TALK_RE.search(t):
        return Intent("lets_talk", {})

    # === GREETINGS — morning/evening check-ins ===
    if re.match(r"^(?:good\s+)?morning(?:\s+zoe)?\.?$", t) or t in {
        "morning", "morning zoe", "hey morning",
    }:
        return Intent("good_morning", {})
    if re.match(r"^good\s+evening(?:\s+zoe)?\.?$|^good\s+night(?:\s+zoe)?\.?$", t) or t in {
        "evening", "evening zoe",
    }:
        return Intent("good_evening", {})

    # === GENERAL GREETING (ZOE-42, ZOE-15) — hello/hi/hey/good afternoon/etc. ===
    # good_morning/good_evening already handled above (they trigger the daily briefing).
    if _GREETING_RE.match(t):
        tod = None
        if "afternoon" in t:
            tod = "afternoon"
        elif "night" in t:
            tod = "night"
        return Intent("greeting", {"time_of_day": tod})

    if re.match(r"^ping\.?$", t):
        return Intent("greeting", {})

    # Social acknowledgements & presence checks — instant, no brain.
    if _THANKS_RE.match(t):
        return Intent("acknowledgement", {"kind": "thanks"})
    if _ACK_RE.match(t):
        return Intent("acknowledgement", {"kind": "ack"})
    if _STATUS_CHECK_RE.match(t):
        return Intent("status_check", {})

    if re.match(r"^what lists do i have\??$", t):
        return Intent("list_show", {})

    if re.match(
        r"^(?:open|show|go to|bring up|take me to) (?:the |my )?contacts(?: page)?\??$",
        t,
    ):
        return Intent("people_search", {"query": ""})

    if re.match(r"^remember that\b.+", t, re.IGNORECASE):
        return Intent("memory_remember", {"raw": text})

    # === CLOCK / CALENDAR QUERIES — checked before domain patterns (no slots needed) ===

    if _TIME_PLANNING_CLARIFICATION_RE.match(t):
        return Intent("time_planning_clarification", {"kind": "best_time_to_leave"})

    if _TIME_MATH_CLARIFICATION_RE.match(t):
        return Intent("time_planning_clarification", {"kind": "time_math"})

    # Modelled on HA's HassGetCurrentTime and HassGetCurrentDate — two separate intents
    if _TIME_QUERY_RE.match(t):
        return Intent("time_query", {})

    if _DATE_QUERY_RE.match(t):
        return Intent("date_query", {})

    # --- CALCULATE (ZOE-10) — arithmetic fast-path, no LLM needed ---
    # Percentage form: "what is 25% of 80"
    _pct_m = _CALCULATE_PCT_RE.match(t)
    if _pct_m:
        pct_val, base_val = _pct_m.group(1), _pct_m.group(2)
        return Intent("calculate", {"expression": f"{pct_val}/100*{base_val}",
                                    "display": f"{pct_val}% of {base_val}"})
    # Word-operator form: "what is 10 times 3" / "10 divided by 2"
    _words_m = _CALCULATE_WORDS_RE.match(t)
    if _words_m:
        a, op_word, b = _words_m.group(1), _words_m.group(2).lower(), _words_m.group(3)
        op = {"times": "*", "multiplied by": "*",
              "divided by": "/", "plus": "+", "minus": "-"}.get(op_word, "+")
        return Intent("calculate", {"expression": f"{a}{op}{b}",
                                    "display": f"{a} {op_word} {b}"})
    # Symbolic form: "2+2", "what is 100/4", "15 * 3 = ?"
    _calc_m = _CALCULATE_RE.match(t)
    if _calc_m and _calc_m.group(1):
        return Intent("calculate", {"expression": _calc_m.group(1).strip()})

    # === DOMAIN-SPECIFIC PATTERNS FIRST (to avoid list collisions) ===

    # --- CALENDAR CREATE (keyword classifier → LLM fills slots via detect_and_extract_intent) ---
    # Hard nouns: always calendar. Soft nouns: calendar unless note/journal/list/reminder present.
    _CAL_CREATE_VERB = re.compile(r"\b(?:add|put|create|schedule|set\s*up|make|book)\b")
    _CAL_HARD_NOUNS = {"calendar", "appointment"}
    _CAL_SOFT_NOUNS = {"event", "meeting"}
    _CAL_BLOCKERS = {"note", "notes", "journal", "diary", "list", "reminder", "shopping"}
    if _CAL_CREATE_VERB.search(t):
        _has_hard = any(kw in t for kw in _CAL_HARD_NOUNS)
        _has_soft = any(kw in t for kw in _CAL_SOFT_NOUNS)
        _blocked = any(kw in t for kw in _CAL_BLOCKERS)
        if _has_hard or (_has_soft and not _blocked):
            return Intent("calendar_create", {"raw": text})

    # --- CALENDAR SHOW ---
    for pattern in [
        r"^what(?:'s| is) on my (?:calendar|schedule)(.*)$",
        r"^whats on my (?:calendar|schedule)(.*)$",
        r"^(?:show|check) (?:me )?my (?:calendar|schedule|events)(.*)$",
        r"^(?:show|open|bring up|go to|take me to) (?:the |my )?(?:calendar|schedule)(?: (?:page|screen|view))?(.*)$",
        r"^my (?:calendar|schedule|events)$",
        r"^(?:upcoming|today'?s) (?:events|calendar|schedule)$",
        r"^show me (?:my )?week(?: at a glance)?$",
        r"^whats on today$",
        r"^what'?s on today$",
        r"^whats happening (?:today|this afternoon|this evening|tomorrow)(.*)$",
        r"^what'?s happening (?:today|this afternoon|this evening|tomorrow)(.*)$",
        r"^whats my first event(?: tomorrow| today)?$",
        r"^what'?s my first event(?: tomorrow| today)?$",
        r"^do i have free time(?: today| tomorrow| tomorrow evening| this evening)?$",
        r"^what (?:events )?do i have(?: today| this week| tomorrow)?$",
    ]:
        m = re.match(pattern, t)
        if m:
            qualifier = (m.group(1) if m.lastindex else "").strip()
            return Intent("calendar_show", {"qualifier": qualifier})

    # --- REMINDERS CREATE (keyword classifier → LLM fills slots via detect_and_extract_intent) ---
    # Pattern 1: "remind me to/at/in/on/about X", "set a reminder to/for/at/in/on X",
    #            "reminder to/for/at X", "remember to X"
    if re.match(
        r"^(?:remind me (?:to|at|in|on|about)|set a reminder (?:to|for|at|in|on)|reminder (?:to|for|at)|remember to) .+",
        t,
    ):
        return Intent("reminder_create", {"raw": text})
    # Pattern 2: "add/create/make a reminder for X"
    if re.match(r"^(?:add|create|make|schedule)\s+(?:a |an )?reminder\b.*", t):
        return Intent("reminder_create", {"raw": text})

    # --- REMINDERS LIST ---
    for pattern in [
        r"^(?:show|list|check|what are) (?:my )?reminders$",
        r"^my reminders$",
        r"^(?:open|show|bring up|go to|take me to) (?:the |my )?reminders(?: (?:page|screen|view))?$",
        r"^show todays reminders$",
        r"^show today'?s reminders$",
    ]:
        if re.match(pattern, t):
            return Intent("reminder_list", {})

    # --- CONTACTS INTRODUCE ---
    _PEOPLE_INTRODUCE_RE = re.compile(
        r'\b(?:zoe[,\s]+)?(?:meet|this is|introduce you to|say hi to|meet my|i\'d like you to meet)\s+'
        r'([A-Z][a-z]{1,30}(?:\s[A-Z][a-z]{1,20})?)',
        re.IGNORECASE,
    )
    m_intro = _PEOPLE_INTRODUCE_RE.search(t)
    if m_intro:
        return Intent("people_introduce", {"name": m_intro.group(1).strip()})

    # Note: third-party person-to-person relationships (e.g. "Sarah is Tom's
    # sister") are captured from natural language by person_extractor on every
    # turn (→ _write_relationship); there is no dedicated relate intent.

    # --- CONTACTS CREATE ---
    m = re.match(
        r"^(?:add|create|save) (?:a )?(?:contact|person|entry) (?:for |named )?(.+)$", t
    )
    if m:
        raw = m.group(1).strip()

        # Extract just the name: stop at the first comma or pronoun ("she's / he's / who is")
        name_part = re.split(
            r",|\s+(?:she\'?s?|he\'?s?|they\'?re?|who\s+is|as\s+(?:a|my)?)\b",
            raw, maxsplit=1, flags=re.I
        )[0].strip()
        # Capitalise each word (input text is normalised to lower)
        name = " ".join(w.capitalize() for w in name_part.split()) if name_part else raw

        rel = "friend"
        context = "personal"
        circle = "circle"

        tl = t.lower()
        # Context: work signals
        for tag in ["colleague", "coworker", "co-worker", "boss", "client", "contractor", "vendor"]:
            if tag in tl:
                context = "work"
                circle = "circle"
                rel = tag if tag != "coworker" else "colleague"
                break
        # Context: personal signals (only override if not already set to work)
        if context != "work":
            for tag in ["friend", "family", "neighbor", "neighbour", "partner", "spouse"]:
                if tag in tl:
                    context = "personal"
                    break

        # Relationship label
        rel_map = [
            ("best friend", "best friend"),
            ("spouse",      "spouse"),
            ("partner",     "partner"),
            ("colleague",   "colleague"),
            ("friend",      "friend"),
            ("family",      "family"),
            ("neighbor",    "neighbor"),
            ("neighbour",   "neighbor"),
        ]
        for keyword, label in rel_map:
            if keyword in tl:
                rel = label
                break

        # Tier
        for tag in ["inner circle", "best friend", "closest", "partner", "spouse"]:
            if tag in tl:
                circle = "inner"
                break

        return Intent("people_create", {"name": name, "relationship": rel, "context": context, "circle": circle})

    # --- CONTACTS SEARCH ---
    m = re.match(r"^(?:find|search|look up) (?:a )?(?:contact|person) (?:for |named )?(.+)$", t)
    if m:
        return Intent("people_search", {"query": m.group(1).strip()})

    m = re.match(r"^who is (.+)$", t)
    if m:
        return Intent("people_search", {"query": m.group(1).strip()})


    # --- NOTES CREATE ---
    # Unambiguous note-creation verbs (make/create/write/save/take) accept a
    # bare space, colon, or connector before the body ("make a note X",
    # "make a note: X", "write a note about X").
    m = re.match(
        r"^(?:make|create|write|save|take) (?:a )?note(?:s)?"
        r"(?:\s*[:\-]\s*|\s+(?:titled|called|about|on)\s+|\s+)(.+)$", t
    )
    if m:
        body = m.group(1).strip()
        return Intent("note_create", {"title": body[:60], "content": body})
    # "add a note" is ambiguous with a shopping add ("add a note pad", "add
    # sticky notes"), so it means note_create ONLY with an explicit colon or
    # connector ("add a note: X", "add a note about X") — never a bare space.
    m = re.match(
        r"^add (?:a )?note(?:s)?(?:\s*[:\-]\s*|\s+(?:titled|called|about|on)\s+)(.+)$", t
    )
    if m:
        body = m.group(1).strip()
        return Intent("note_create", {"title": body[:60], "content": body})

    # "jot down X" / "jot this down: X" / "note that X" / "note down X" —
    # imperative note phrasings that aren't caught above. Route straight to
    # note_create rather than defer, so the note is captured on the fast path.
    m = re.match(
        r"^(?:jot(?:\s+(?:this|that|it))?\s+down|note\s+down|note\s+that|note\s+this)"
        r"(?:\s*[:\-]\s*|\s+)(.+)$", t
    )
    if m:
        body = m.group(1).strip()
        return Intent("note_create", {"title": body[:60], "content": body})

    # --- NOTES SEARCH ---
    m = re.match(r"^(?:search|find|look up) (?:my )?notes (?:for |about )?(.+)$", t)
    if m:
        return Intent("note_search", {"query": m.group(1).strip()})

    # --- BROAD PEOPLE SEARCH (after notes, to avoid collision) ---
    m = re.match(r"^(?:find|look up) (.+)$", t)
    if m:
        query = m.group(1).strip()
        blocked = {"notes", "note", "list", "calendar", "schedule", "events",
                   "reminders", "recipe", "recipes", "weather", "timer"}
        if not any(kw in query for kw in blocked):
            return Intent("people_search", {"query": query})

    # --- WEATHER ---
    for pattern in [
        r"^what(?:'s| is) the weather(?: like)?(.*)$",
        r"^whats the weather(?: like)?(.*)$",
        r"^how(?:'s| is) the weather(.*)$",
        r"^(?:(?:can|could) you |please )?(?:show|open|bring up|pull up|go to|take me to)(?: me)? (?:the )?weather(?: (?:screen|page|panel))?(.*)$",
        r"^(?:will it|is it going to) rain(.*)$",
        r"^do i need (?:a |an )?(?:jacket|umbrella|coat)(.*)$",
        r"^temperature (?:today|tomorrow|outside)(.*)$",
        r"^weather(\s+(?:today|tomorrow|forecast|this week))?(.*)$",
    ]:
        m = re.match(pattern, t)
        if m:
            # Collect every captured group — the "weather" pattern uses two
            # capture groups (qualifier + trailing) so we combine them to
            # detect "weather forecast" / "weather this week".
            groups = m.groups() or ()
            qualifier = " ".join((g or "").strip() for g in groups).strip()
            is_forecast = (
                any(kw in qualifier for kw in ("tomorrow", "week", "forecast"))
                or any(kw in t for kw in ("tomorrow", "this week", "forecast"))
            )
            return Intent("weather", {"qualifier": qualifier, "forecast": is_forecast})

    # --- JOURNAL ---
    # Explicit "<verb> a journal entry[: <content>]" — the deterministic, DB-verified
    # path (_execute_journal_create_direct). "add" is included because the 4B brain
    # under-fires its journal tool on this exact phrasing (a "grateful for the rain"
    # entry misroutes to a weather answer); #1150 already stopped list_add from
    # swallowing "add a journal entry", so routing it positively here is the
    # completion of that fix, not a regression. A leading ":"/"-"/"—" separator
    # after "entry" is stripped so the colon doesn't leak into the stored content.
    m = re.match(
        r"^(?:write|create|make|start|new|add) (?:a |an )?(?:journal|diary) (?:entry)?(.*)$", t
    )
    if m:
        content = (m.group(1) or "").strip().lstrip(":-—").strip()
        return Intent("journal_create", {"content": content})

    if re.match(r"^(?:write|log|add) (?:in |to )?(?:my )?(?:journal|diary)$", t):
        return Intent("journal_create", {"content": ""})

    for pattern in [
        r"^(?:how'?s|what'?s|show) (?:my )?(?:journal|journaling) (?:streak|stats)$",
        r"^journal streak$",
    ]:
        if re.match(pattern, t):
            return Intent("journal_streak", {})

    if re.match(r"^(?:give me a |)journal(?:ing)? prompt", t):
        return Intent("journal_prompt", {})

    # --- TRANSACTIONS ---
    # Parse money exactly: integer cents (no float drift) plus the canonical
    # two-decimal dollars the command boundary still expects. A malformed match
    # (e.g. "1.2.3") returns None so we DON'T record a bogus $0 transaction —
    # the turn falls through to later intents / open-domain handling instead.
    from money import to_cents, to_dollars

    def _money_slots(raw: str):
        try:
            cents = to_cents(raw)
        except ValueError:
            return None
        return {"amount": to_dollars(cents), "amount_cents": cents}

    m = re.match(
        r"^i (?:spent|paid) \$?([\d.]+)(?: ?(?:dollars?|bucks?))?(?: (?:at|on|for) (.+))?$", t
    )
    if m:
        slots = _money_slots(m.group(1))
        if slots is not None:
            desc = (m.group(2) or "").strip() or "purchase"
            return Intent("transaction_create", {**slots, "description": desc})

    m = re.match(
        r"^(?:bought|purchased) (.+?) (?:for )\$?([\d.]+)$", t
    )
    if m:
        slots = _money_slots(m.group(2))
        if slots is not None:
            desc = m.group(1).strip()
            return Intent("transaction_create", {**slots, "description": desc})

    for pattern in [
        r"^(?:how much (?:did i|have i) (?:spent?|spend)|weekly spending|budget check|spending (?:summary|this week))(.*)$",
        r"^what(?:'s| did) (?:i|we) spend(.*)$",
    ]:
        m = re.match(pattern, t)
        if m:
            qualifier = (m.group(1) if m.lastindex else "").strip()
            period = "month" if "month" in qualifier else "week"
            return Intent("transaction_summary", {"period": period})

    # --- DAILY BRIEFING (composite) ---
    for pattern in [
        r"^what(?:'?s| is) (?:on )?(?:today|my day)(?: like)?$",
        r"^whats (?:on )?(?:today|my day)(?: like)?$",
        r"^(?:daily|morning) (?:briefing|update|rundown)$",
        r"^give me (?:a |my )?(?:daily |morning )?(?:briefing|update|rundown)$",
        r"^what(?:'s| is) (?:coming up|on|left)(?: for me)? today$",
        r"^what (?:do i|have i got) (?:have )?(?:on )?today$",
    ]:
        if re.match(pattern, t):
            return Intent("daily_briefing", {})

    # --- SMART HOME LIGHTS (ZOE-9) ---
    if _SMART_HOME_RE.search(t):
        # Determine action
        if re.search(r'\bdim\b', t):
            action = "dim"
        elif re.search(r'\bbrighten\b', t):
            action = "brighten"
        elif re.search(r'\b(?:turn|switch|flip)\s+on\b|\blights?\s+on\b', t):
            action = "turn_on"
        else:
            action = "turn_off"
        # Attempt to extract a room name
        _room_m = re.search(
            r'\b(bedroom|kitchen|living\s+room|bathroom|lounge|office|'
            r'dining\s+room|hallway|garage|backyard|garden|study)\b',
            t, re.IGNORECASE,
        )
        room = _room_m.group(1).replace(" ", "_") if _room_m else None
        return Intent("smart_home", {"action": action, "entity": "light", "room": room})

    # --- TIMER CREATE ---
    for pattern in [
        r"^(?:set|start|create|add) (?:a |an )?(?:(\d+)[\s\-]minute[s]?|(\d+)[\s\-]min[s]?) timer(?: (?:called|for|named) (.+))?$",
        r"^(?:set|start|create) (?:a |an )?timer (?:for|of) (\d+) min(?:utes?)?(?:\s+(?:called|for|named) (.+))?$",
        r"^(\d+) min(?:utes?)? timer(?: (?:for|called|named) (.+))?$",
        r"^(?:set|start) (?:a |an )?timer$",
    ]:
        m = re.match(pattern, t)
        if m:
            groups = [g for g in (m.groups() if m.lastindex else []) if g]
            mins = next((g for g in groups if g and g.isdigit()), "5")
            label = next((g for g in groups if g and not g.isdigit()), "Timer")
            return Intent("timer_create", {"minutes": int(mins), "label": label.title()})

    # --- RECIPE SEARCH ---
    m = re.match(
        r"^(?:show|find|get|search|look up)(?: me)? (?:a )?recipe (?:for |to make )?(.+)$", t
    )
    if m:
        return Intent("recipe_search", {"query": m.group(1).strip()})

    m = re.match(
        r"^how (?:do i |can i )?(?:make|cook|bake) (.+)$", t
    )
    if m:
        return Intent("recipe_search", {"query": m.group(1).strip()})

    # === MUSIC SETUP (checked before list_add so "add music service" doesn't misroute) ===
    _MUSIC_SETUP_EARLY_RE = re.compile(
        r"^(?:set\s?up|configure|connect|add|setup)\s+"
        r"(?:music(?:\s+assistant)?|spotify|youtube\s*music|apple\s*music"
        r"|deezer|tidal|plex|streaming|music\s+services?|music\s+settings?)$"
        r"|^music\s+settings?$"
        r"|^set\s+up\s+music\s+assistant$"
        r"|^connect\s+(?:my\s+)?(?:music|streaming)$",
        re.IGNORECASE,
    )
    if _MUSIC_SETUP_EARLY_RE.match(t):
        return Intent("music_setup", {})

    # === LIST PATTERNS (checked after domain-specific) ===

    # --- LIST ADD (with explicit list name) ---
    for pattern in [
        r"^add (.+?) to (?:the |my )?(.+?) ?list$",
        r"^put (.+?) on (?:the |my )?(.+?) ?list$",
        r"^add (.+?) to (?:the |my )?(shopping|grocery|groceries|todo|to do|to-do|personal|work|bucket)$",
        r"^(?:(?:can|could) you |please )?(?:add|put) (.+?) (?:to|on) (?:the |my )?(.+?) ?list$",
    ]:
        m = re.match(pattern, t)
        if m:
            item, lst = _sanitize_list_item(m.group(1)), m.group(2).strip()
            list_type = _normalize_list(lst)
            return Intent("list_add", {"item": item, "list_type": list_type})

    # --- LIST ADD (STT-garbled leading "add") ---
    # Moonshine renders a leading "Add" as near-homophones: "Add council to my
    # work list" arrives as "I'd go to council to my work list" (said-vs-did
    # bug: the add regexes miss and the semantic router drifts to calendar).
    # If the utterance ENDS with "to my <known> list", treat it as an add and
    # strip the garbled lead-in. Bare navigation ("go to my work list") leaves
    # no item text, so it still falls through to the LIST SHOW/OPEN patterns.
    m = re.match(
        r"^(?:i'?d|id|and|at|it|hey|a)?\s*(?:go to |goto )?(.+?) "
        r"to (?:the |my )?(shopping|grocery|groceries|todo|to do|to-do|personal|work|bucket|tasks?)"
        r" list[.!?]?$",
        t,
    )
    if m:
        item = _sanitize_list_item(m.group(1))
        if item and item.lower() not in {"go", "me", "take me", "us", "take us"}:
            lst = m.group(2).strip()
            return Intent(
                "list_add",
                {"item": item, "list_type": _normalize_list("tasks" if lst == "task" else lst)},
            )

    # --- LIST ADD (implicit, no list name) ---
    # Defer to the brain when a competing-capability cue is present without an
    # explicit shopping/grocery-list target: "add a journal entry: …", "add
    # Marcus to my contacts", "add a note …" must route to their own domains
    # via the brain, not get swallowed as shopping-list items. Explicit list
    # phrasings ("… to my shopping list") are handled above and unaffected.
    if not _has_competing_list_cue(t):
        for pattern in [
            r"^add (.+)$",
            r"^put (.+)$",
            r"^(?:(?:can|could) you |please )?add (.+)$",
            r"^(?:(?:can|could) you |please )?put (.+)$",
        ]:
            m = re.match(pattern, t)
            if m:
                item = _sanitize_list_item(m.group(1))
                list_type = _infer_list(item)
                return Intent("list_add", {"item": item, "list_type": list_type})

    # --- LIST ADD (natural language shopping) ---
    m = re.match(
        r"^(?:i need to buy|we need|we'?re out of|don'?t forget|buy|get) (.+)$", t
    )
    if m:
        item = _sanitize_list_item(m.group(1))
        return Intent("list_add", {"item": item, "list_type": "shopping"})

    # --- LIST SHOW ---
    for pattern in [
        r"^(?:show|read|check) (?:me )?(?:the |my )?(.+?) ?list$",
        r"^what(?:'?s| is) on (?:the |my )?(.+?) ?list$",
        r"^what do i need to (?:buy|get)$",
        r"^what'?s on my list$",
        r"^show my list$",
        r"^(?:open|show|bring up|go to|take me to) (?:the |my )?(?:shopping |grocery |groceries )?list(?: page| screen| view)?$",
        r"^(?:open|show|bring up|go to|take me to) lists?$",
        r"^(?:open|show|bring up|go to|take me to) (?:the |my )?(?:shopping|grocery|groceries)$",
        r"^show me list$",
    ]:
        m = re.match(pattern, t)
        if m:
            lst = m.group(1).strip() if m.lastindex else "shopping"
            if lst in ("my list", "the list", "list", "my"):
                lst = "shopping"
            list_type = _normalize_list(lst)
            return Intent("list_show", {"list_type": list_type})

    # --- LIST REMOVE ---
    for pattern in [
        r"^(?:remove|delete|take off|cross off) (.+?) from (?:the |my )?(.+?) ?list$",
        r"^(?:remove|delete|take off|cross off) (.+?) from (?:the |my )?(.+)$",
        r"^(?:we got|got|we have) (.+)$",
    ]:
        m = re.match(pattern, t)
        if m:
            item = m.group(1).strip()
            lst = m.group(2).strip() if m.lastindex >= 2 else "shopping"
            return Intent("list_remove", {"item": item, "list_type": _normalize_list(lst)})

    # === MUSIC / MEDIA CONTROLS ===
    _MUSIC_PLAY_RE = re.compile(
        r"^(?:play|put on|play me|play some|start playing)\s+(.+)$", re.IGNORECASE
    )
    _MUSIC_CMD_RE = re.compile(
        r"^(?P<cmd>pause|stop|resume|unpause|skip(?:\s+(?:this|the)\s+(?:song|track))?|next(?: song| track)?|previous(?: song| track)?"
        r"|next track|prev track|previous track|what(?:'s| is)(?: currently)? playing"
        r"|what song is this|volume up|volume down|louder|quieter|mute|unmute|shuffle|repeat)(?: the music| music)?\.?$",
        re.IGNORECASE,
    )
    _VOLUME_SET_RE = re.compile(r"^(?:set volume|volume) (?:to |at )?(\d{1,3})(?:\s*%)?\.?$", re.IGNORECASE)

    m = _MUSIC_PLAY_RE.match(t)
    if m:
        return Intent("music_play", {"query": m.group(1).strip()})

    m = _VOLUME_SET_RE.match(t)
    if m:
        return Intent("music_volume", {"level": int(m.group(1))})

    m = _MUSIC_CMD_RE.match(t)
    if m:
        cmd = m.group("cmd").lower().strip()
        cmd = cmd.replace(" ", "_")
        if cmd in {"next_song", "next_track"}: cmd = "next"
        if cmd in {"previous_song", "previous_track", "prev_track"}: cmd = "previous"
        if cmd in {"unpause"}: cmd = "resume"
        if cmd in {"louder"}: cmd = "volume_up"
        if cmd in {"quieter"}: cmd = "volume_down"
        if "playing" in cmd or "song_is_this" in cmd: cmd = "now_playing"
        return Intent("music_control", {"command": cmd})


    # --- SET VOLUME / TTS voice volume (ZOE-13) ---
    # Checked before _AUTOGEN_UNKNOWN_GAP so "speak louder / be quieter / your volume up"
    # routes to the system-audio path instead of the music media-player path.
    _voice_vol_m = _ZOE_VOICE_VOLUME_RE.search(t)
    if _voice_vol_m:
        # Use capture groups from the matching alternative — patterns that contain \d{1,3}
        # set group 1-4; patterns without a number leave all groups None.
        _captured = next((g for g in _voice_vol_m.groups() if g is not None), None)
        _level = int(_captured) if _captured else None
        _is_up = bool(re.search(r'\b(up|louder|raise|increase|higher|more\s+loudly)\b', t, re.IGNORECASE))
        direction = "set" if _level is not None else ("up" if _is_up else "down")
        return Intent("set_volume", {"direction": direction, "level": _level})

    # Coreference before generic volume gap-fill — "turn it down a bit" / "a bit quieter"
    # after a set_volume command should stay set_volume, not fall through to music_control.
    if context is not None and context.is_fresh():
        _ctx_name, _ctx_slots = context.resolve_coreference(t)
        if _ctx_name:
            return Intent(_ctx_name, _ctx_slots or {}, confidence=0.85)

    # Conversational volume phrases — covers polite/natural speech (zoe-self-improve 2026-05-11 refined)
    _AUTOGEN_UNKNOWN_GAP = re.compile(
        r'(?:can|could|would|will)\s+you\s+.*?(?:volume|louder|quieter)'
        r'|(?:turn|put|make|bring|bump|crank)\s+(?:your|the|it|that)?\s*(?:volume\s*)?(?:up|down|louder|quieter)'
        r'|(?:raise|increase|boost|lower|decrease|reduce)\s+(?:the|your)?\s*(?:volume|sound)'
        r'|(?:a\s+bit|just\s+a?\s*(?:little|tad)|slightly)\s+(?:louder|quieter|softer)',
        re.IGNORECASE,
    )
    if _AUTOGEN_UNKNOWN_GAP.search(t):
        _wrd = dict(zero=0,one=1,two=2,three=3,four=4,five=5,six=6,seven=7,eight=8,nine=9,ten=10)
        _num = re.search(r'\b(10|[0-9])\b', t)
        _wm  = re.search(r'\b(' + '|'.join(_wrd) + r')\b', t, re.IGNORECASE)
        _lvl = int(_num.group(1)) * 10 if _num else (_wrd.get(_wm.group(1).lower(), 5) * 10 if _wm else None)
        if _lvl is not None:
            return Intent("music_volume", {"level": _lvl})
        _up  = re.search(r'\b(up|raise|louder|increase|boost|higher)\b', t, re.IGNORECASE)
        return Intent("music_control", {"command": "volume_up" if _up else "volume_down"})

    # ── A2A Federation ────────────────────────────────────────────────────────
    _A2A_RE = re.compile(
        r'\b(?:call|ask|delegate\s+to|send\s+to|route\s+to|use)\b.{0,20}\b(?:agent|hermes|openclaw)\b'
        r'|which\s+agents?\s+(?:do\s+you|are|can)\b'
        r'|(?:show|list|what\s+are)\s+(?:my|your|the)?\s*(?:agents?|federation|peers?)\b'
        r'|(?:agent|federation)\s+(?:status|registry|health|card)\b'
        r'|what\s+agents?\s+(?:do\s+you\s+know|have\s+you|are\s+available)\b'
        r'|a2a\s+(?:status|health|check|federation)\b'
        r'|(?:hermes|openclaw)\s+(?:status|online|running|connected|health)\b'
        r'|are\s+(?:hermes|openclaw|peer\s+agents?)\s+(?:online|running|connected|available)\b'
        r'|list\s+(?:connected|registered|known|peer)\s+agents?\b',
        re.I,
    )
    if _A2A_RE.search(t):
        return Intent("a2a_federation_status", {})

    # ── Multica board visibility ───────────────────────────────────────────────
    if re.search(r"\b(?:pause|stop)\s+(?:multica\s+|engineering\s+)?dispatch\b", t, re.I):
        return Intent("engineering_dispatch_pause", {})
    if re.search(r"\bresume\s+(?:multica\s+|engineering\s+)?dispatch\b", t, re.I):
        return Intent("engineering_dispatch_resume", {})
    _MOVE_TODO_RE = re.search(
        r"\bmove\s+(?P<reference>ZOE-\d+|[0-9a-f-]{32,36})\s+to\s+todo\b",
        text,
        re.I,
    )
    if _MOVE_TODO_RE:
        return Intent(
            "engineering_ticket_move_todo",
            {"reference": _MOVE_TODO_RE.group("reference")},
        )
    _SPLIT_TICKET_RE = re.search(
        r"\bsplit\s+(?P<reference>ZOE-\d+|[0-9a-f-]{32,36})\s+into\s+(?P<title>.+)$",
        text.strip(),
        re.I,
    )
    if _SPLIT_TICKET_RE:
        return Intent(
            "engineering_ticket_split",
            {
                "reference": _SPLIT_TICKET_RE.group("reference"),
                "title": _SPLIT_TICKET_RE.group("title").strip(),
            },
        )
    if re.search(r"\b(?:show|list)\s+(?:the\s+)?multica\s+backlog\b", t, re.I):
        return Intent("engineering_ticket_list", {"status": "backlog"})
    _BARE_BLOCKED_RE = re.fullmatch(
        r"\s*(?:what(?:'s| is)|show|list)\s+(?:currently\s+)?blocked\s*[?.!]?\s*",
        text,
        re.I,
    )
    _QUALIFIED_BLOCKED_RE = re.search(
        r"\b(?:show|list|what(?:'s| is))\b.*\b(?:multica|engineering|tickets?)\b.*\bblocked\b"
        r"|\b(?:show|list|what(?:'s| is))\b.*\bblocked\b.*\b(?:multica|engineering|tickets?)\b",
        text,
        re.I,
    )
    if _BARE_BLOCKED_RE or _QUALIFIED_BLOCKED_RE:
        return Intent("engineering_ticket_list", {"status": "blocked"})

    _BOARD_RE = re.compile(
        r"what'?s?\s+on\s+(?:the\s+)?(?:multica\s+)?board\b"
        r"|show\s+(?:the\s+)?(?:multica\s+)?(?:active\s+)?(?:board|tasks?)\b"
        r"|open\s+(?:the\s+)?(?:multica\s+)?(?:task\s+)?board\b"
        r"|show\s+(?:me\s+)?multica\b"
        r"|what\s+is\s+(?:openclaw|hermes|the\s+agent)\s+(?:doing|working\s+on|running)\b"
        r"|any\s+(?:active\s+)?(?:agent\s+)?tasks?\s+(?:running|pending|in\s+progress)\b"
        r"|board\s+(?:status|view|items?)\b",
        re.I,
    )
    if _BOARD_RE.search(t):
        return Intent("board_status", {})

    _AGENT_ACTIVITY_RE = re.compile(
        r"show\s+(?:me\s+)?(?:my\s+)?(?:agent\s+)?activity\b"
        r"|what(?:'s| is)\s+running\s+in\s+the\s+background\b"
        r"|(?:any\s+)?background\s+tasks?\s+(?:running|active|pending)\b"
        r"|list\s+(?:my\s+)?(?:agent\s+)?(?:background\s+)?tasks?\b",
        re.I,
    )
    if _AGENT_ACTIVITY_RE.search(t):
        return Intent("agent_tasks_status", {})

    _eng_match = _ENGINEERING_TASK_RE.search(text.strip())
    if _eng_match:
        task_text = (_eng_match.group("task") or _eng_match.group("task2") or "").strip()
        if task_text:
            return Intent("engineering_task_create", {"task": task_text})

    if _ENGINEERING_STATUS_RE.search(t):
        return Intent("engineering_task_status", {})

    # ── Evolution proposals ────────────────────────────────────────────────────
    _EVOLVE_REVIEW_RE = re.compile(
        r'what\s+(?:needs?|could)\s+(?:be\s+)?improv(?:ing|ed|ement)\b'
        r'|show\s+(?:improvement\s+)?proposals?\b'
        r'|review\s+proposals?\b'
        r'|what\s+(?:are|is)\s+(?:you\s+)?struggling\s+with\b'
        r'|what\s+(?:have\s+you\s+)?noticed\s+(?:that\s+needs|needs?)\s+fix\b'
        r'|evolution\s+(?:proposals?|review|status)\b'
        r'|(?:what|show)\s+(?:improvements?|ideas?)\s+(?:has\s+)?(?:zoe|you)\s+(?:proposed|noticed|suggested)\b'
        r'|pending\s+proposals?\b'
        r"|zoe'?s?\s+(?:self.?improv\w+|improvement\s+ideas?)\b"
        r"|what\s+(?:does\s+)?(?:zoe|you)\s+want\s+to\s+change\b",
        re.I,
    )
    if _EVOLVE_REVIEW_RE.search(t):
        return Intent("evolution_proposals_review", {})

    _BOARD_HEAL_RE = re.compile(
        r"\b(?:fix|heal|triage|clean\s*up|review|sort\s*out)\b"
        r".*\b(?:board|issues?|multica|problems?|proposals?)\b"
        r"|\b(?:board|multica)\b.*\b(?:fix|heal|triage|clean\s*up|review|sort\s*out)\b"
        r"|self.?heal\b|board\s+review\b|check\s+the\s+board\b",
        re.I,
    )
    if _BOARD_HEAL_RE.search(t):
        return Intent("board_heal", {})

    # ── User issue / complaint reports ────────────────────────────────────────
    _USER_ISSUE_RE = re.compile(
        r'\byou\s+got\s+that\s+wrong\b'
        r'|\byou\s+keep\s+(?:getting|messing)\b'
        r'|\bthat\s+(?:didn\'?t|did\s+not)\s+work\b'
        r'|\bthat\'?s?\s+not\s+working\b'
        r'|\bthere\'?s?\s+(?:an?\s+)?(?:problem|issue|bug)\s+with\b'
        r'|\bfix\s+(?:your|the)\b'
        r'|\b\w+\s+(?:is\s+)?broken\b'
        r'|\b\w+\s+doesn\'?t\s+work\b'
        r'|\byou\s+should\s+know\s+that\b'
        r'|\bi\s+keep\s+having\s+(?:issues?|problems?)\b'
        r'|\byou\s+need\s+to\s+fix\b'
        r'|\bthat\s+was\s+(?:wrong|incorrect)\b'
        r'|\byou\s+(?:messed|failed)\b',
        re.I,
    )
    if _USER_ISSUE_RE.search(t):
        return Intent("user_issue_report", {"message": text})

    # Open-domain Q&A / creative — route to agent (closes intent-gap backlog without brittle regex)
    if _AGENT_CHAT_RE.search(t):
        return Intent("extend_capability", {"raw": text})

    # Context-based coreference resolution (OVOS Adapt pattern)
    if context is not None and context.is_fresh():
        _ctx_name, _ctx_slots = context.resolve_coreference(t)
        if _ctx_name:
            from conversation_context import ConversationContext as _CC  # noqa: F401 (type-check only)
            return Intent(_ctx_name, _ctx_slots or {}, confidence=0.85)

    if log_miss:
        logger.info("intent_miss: %s", text)
        try:
            # Write to intent-misses file for weekly self-review (PII stripped)
            import re as _re, json as _json, pathlib as _pathlib
            _MISS_PATH = _pathlib.Path.home() / "training" / "data" / "intent-misses.jsonl"
            _MISS_PATH.parent.mkdir(parents=True, exist_ok=True)
            # Strip names, numbers, emails, URLs before writing
            _clean = _re.sub(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b', '[NAME]', text)
            _clean = _re.sub(r'\b\d[\d\s\-]{6,}\b', '[NUMBER]', _clean)
            _clean = _re.sub(r'[\w.+-]+@[\w-]+\.\w+', '[EMAIL]', _clean)
            _clean = _re.sub(r'https?://\S+', '[URL]', _clean)
            with open(_MISS_PATH, "a") as _f:
                _f.write(_json.dumps({"text": _clean, "ts": __import__("time").time()}) + "\n")
        except Exception:
            pass  # Never let logging break intent routing
        try:
            from pi_intent_evidence import record_intent_miss_evidence

            record_intent_miss_evidence(text, route_class="fallback", user_id=user_id if isinstance(user_id, str) else "unknown")
        except Exception:
            pass  # Never let evidence collection break intent routing
    return None


def _relative_reminder_now():
    from datetime import datetime

    return datetime.now().replace(second=0, microsecond=0)


def _parse_relative_reminder_duration(raw: str):
    from datetime import timedelta

    value = re.sub(r"\s+", " ", str(raw or "").strip().lower())
    if not value:
        return None

    if value == "half an hour":
        return timedelta(minutes=30)

    quantity_words = {
        "a": 1,
        "an": 1,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "a few": 3,
        "a couple": 2,
        "a couple of": 2,
    }
    m = re.match(
        r"^(?P<qty>\d+|a\s+few|a\s+couple(?:\s+of)?|an?|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
        r"(?P<unit>min(?:ute)?s?|hours?|days?|weeks?)$",
        value,
        flags=re.IGNORECASE,
    )
    if not m:
        return None

    qty_text = m.group("qty").lower()
    quantity = int(qty_text) if qty_text.isdigit() else quantity_words.get(qty_text)
    if not quantity or quantity < 1 or quantity > 999:
        return None

    unit = m.group("unit").lower()
    if unit.startswith("min"):
        return timedelta(minutes=quantity)
    if unit.startswith("hour"):
        return timedelta(hours=quantity)
    if unit.startswith("day"):
        return timedelta(days=quantity)
    if unit.startswith("week"):
        return timedelta(weeks=quantity)
    return None


def _extract_relative_reminder_slots(text: str) -> Optional[dict]:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return None

    duration_pattern = (
        r"(?P<duration>"
        r"\d+\s+(?:min(?:ute)?s?|hours?|days?|weeks?)|"
        r"half\s+an\s+hour|"
        r"(?:a\s+few|a\s+couple(?:\s+of)?|an?|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
        r"(?:min(?:ute)?s?|hours?|days?|weeks?)"
        r")"
    )
    patterns = [
        rf"^remind me in {duration_pattern}\s+(?:to|about)\s+(?P<title>.+)$",
        rf"^remind me (?:to|about)\s+(?P<title>.+?)\s+in {duration_pattern}$",
        rf"^remember to\s+(?P<title>.+?)\s+in {duration_pattern}$",
        rf"^set a reminder in {duration_pattern}\s+(?:to|for|about)\s+(?P<title>.+)$",
        rf"^set a reminder (?:to|for|about)\s+(?P<title>.+?)\s+in {duration_pattern}$",
    ]
    title = ""
    duration_text = ""
    for pattern in patterns:
        match = re.match(pattern, raw, flags=re.IGNORECASE)
        if match:
            title = match.group("title").strip(" ,.-")
            duration_text = match.group("duration")
            break
    if not title or not duration_text:
        return None

    if re.search(r"\bevery\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|day|week|month|year)\b", title, flags=re.IGNORECASE):
        return None
    if re.search(r"\b(?:next|this|coming|last)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", title, flags=re.IGNORECASE):
        return None

    delta = _parse_relative_reminder_duration(duration_text)
    if not delta:
        return None
    due = _relative_reminder_now() + delta
    return {"title": title, "date": due.date().isoformat(), "time": due.strftime("%H:%M")}


def _extract_simple_reminder_slots(text: str) -> Optional[dict]:
    """Fast slots for common reminder phrases, including bounded relative durations.

    Recurring, modified-weekday, named-time, and other complex phrases fall back to NLU.
    """

    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return None
    relative_slots = _extract_relative_reminder_slots(raw)
    if relative_slots:
        return relative_slots

    patterns = [
        r"^remind me (?:to|about)\s+(.+)$",
        r"^remember to\s+(.+)$",
        r"^set a reminder (?:to|for|about)\s+(.+)$",
        r"^reminder (?:to|for|about)\s+(.+)$",
        r"^(?:add|create|make|schedule)\s+(?:a |an )?reminder\s+(?:to|for|about)?\s*(.+)$",
    ]
    body = ""
    for pattern in patterns:
        m = re.match(pattern, raw, flags=re.IGNORECASE)
        if m:
            body = m.group(1).strip()
            break
    if not body:
        return None
    if re.search(r"\bin\s+\d+\s+(?:min(?:ute)?s?|hours?|days?|weeks?)\b", body, flags=re.IGNORECASE):
        return None
    if re.search(
        r"\bin\s+(?:(?:a\s+few|a\s+couple(?:\s+of)?|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:min(?:ute)?s?|hours?|days?|weeks?)|an?\s+(?:min(?:ute)?|hour|day|week)|half\s+an\s+hour)\b",
        body,
        flags=re.IGNORECASE,
    ):
        return None
    if re.search(
        r"\b(?:next|this|coming|last)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        body,
        flags=re.IGNORECASE,
    ):
        return None
    if re.search(
        r"\bevery\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|day|week|month|year)\b",
        body,
        flags=re.IGNORECASE,
    ):
        return None
    if re.search(
        r"\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)$",
        body,
        flags=re.IGNORECASE,
    ) and not re.search(
        r"\son\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)$",
        body,
        flags=re.IGNORECASE,
    ):
        return None
    if re.search(
        r"\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
        body,
        flags=re.IGNORECASE,
    ):
        return None
    if re.search(
        r"\b(?:noon|midnight|morning|afternoon|evening|(?:to)?night|bedtime|lunchtime|dinnertime)\b",
        body,
        flags=re.IGNORECASE,
    ):
        return None
    if re.search(r"\bat\s+\d{1,4}(?::\d{2})?\b(?!\s*(?:am|pm))", body, flags=re.IGNORECASE):
        return None
    if re.match(
        r"^(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        body,
        flags=re.IGNORECASE,
    ):
        return None
    if re.search(
        r"\bon\s+(?:the\s+)?(?:\d{1,2}(?:st|nd|rd|th)?|(?:my|your|his|her|their)\s+birthday|new\s+year'?s?|christmas|easter|thanksgiving)$",
        body,
        flags=re.IGNORECASE,
    ):
        return None

    def _pop_time(value: str) -> tuple[str, str]:
        value = value.strip()
        time_patterns = [
            r"\s+at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))$",
            r"\s+(\d{1,2}(?::\d{2})\s*(?:am|pm)|\d{1,2}\s*(?:am|pm))$",
        ]
        for pattern in time_patterns:
            m = re.search(pattern, value, flags=re.IGNORECASE)
            if not m:
                continue
            parsed = _parse_time(m.group(1))
            if parsed:
                return value[:m.start()].strip(), parsed
        return value, ""

    def _pop_date(value: str) -> tuple[str, str]:
        value = value.strip()
        day_names = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
        month_names = (
            "january|february|march|april|may|june|july|august|september|october|november|december|"
            "jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec"
        )
        date_patterns = [
            rf"\s+on\s+(today|tomorrow|{day_names})$",
            r"\s+(today|tomorrow)$",
            rf"\s+on\s+((?:{month_names})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:\s+\d{{4}})?)$",
            rf"\s+on\s+(\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{month_names})(?:\s+\d{{4}})?)$",
            r"\s+on\s+(\d{4}-\d{2}-\d{2})$",
        ]
        for pattern in date_patterns:
            m = re.search(pattern, value, flags=re.IGNORECASE)
            if not m:
                continue
            parsed = _parse_date(m.group(1))
            if parsed:
                return value[:m.start()].strip(), parsed
        return value, ""

    body, date = _pop_date(body)
    body, time_value = _pop_time(body)
    if not date:
        body, date = _pop_date(body)

    title = re.sub(r"\s+", " ", body).strip(" ,.-")
    title = re.sub(r"^(?:to|for|about)\s+", "", title, flags=re.IGNORECASE).strip()
    if not title:
        return None

    slots = {"title": title, "date": date or _parse_date("today") or ""}
    if time_value:
        slots["time"] = time_value
    return slots


# ── Off-panel pending-contact offer replies (QA review F5) ────────────────────
#
# After Zoe voices "Would you like me to add Caitlin as a contact?", a Telegram
# (or any off-panel) user answers in plain text — there is no confirm card. A
# short affirmative next turn must resolve the surfaced offer via the sanctioned
# pending_suggestions.execute_suggestion path; a short refusal must dismiss it.
# Guarded three ways: the feature flag, the message SHAPE (short reply built
# only from yes/no words + fillers + the offered name), and the existence of an
# offer that has actually been surfaced in a prompt (turns_elapsed >= 1) — a
# bare "yes" with no live offer falls through to normal routing untouched.

_OFFER_AFFIRM_FIRST = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "alright", "absolutely",
    "definitely", "please", "go", "do", "add", "save",
})
_OFFER_DECLINE_FIRST = frozenset({"no", "nah", "nope", "dont", "don't", "not"})
_OFFER_REPLY_FILLER = frozenset({
    "yes", "yeah", "yep", "sure", "ok", "okay", "please", "add", "save", "her",
    "him", "them", "it", "that", "do", "go", "ahead", "as", "a", "my", "the",
    "contact", "contacts", "to", "thanks", "thank", "you", "zoe", "course",
    "of", "definitely", "absolutely", "no", "nah", "nope", "dont", "don't",
    "not", "need", "worry", "now", "right", "maybe", "later", "for",
})
_OFFER_REPLY_MAX_TOKENS = 8
_OFFER_REPLY_PUNCT_RE = re.compile(r"^[.,!?]+|[.,!?'’]+$")


def _offer_reply_tokens(text: str) -> list[str]:
    """Lowercase, punctuation-stripped tokens of a candidate offer reply, or []
    when the message is too long / empty to be one."""
    raw = (text or "").strip().lower().split()
    if not raw or len(raw) > _OFFER_REPLY_MAX_TOKENS:
        return []
    tokens = [_OFFER_REPLY_PUNCT_RE.sub("", t) for t in raw]
    return [t for t in tokens if t]


def _offer_reply_kind(text: str, offer_name: str) -> Optional[str]:
    """'accept' / 'dismiss' / None purely from the message shape.

    A reply qualifies only when it is short, opens with a yes/no word, and every
    remaining token is a known filler or a token of the offered person's name —
    so "yes let's book the flight" never hijacks an offer.
    """
    tokens = _offer_reply_tokens(text)
    if not tokens:
        return None
    name_tokens = {t for t in (offer_name or "").lower().split() if t}
    first = tokens[0]
    if first in _OFFER_DECLINE_FIRST:
        kind = "dismiss"
    elif first in _OFFER_AFFIRM_FIRST:
        kind = "accept"
    else:
        return None
    for t in tokens[1:]:
        if t not in _OFFER_REPLY_FILLER and t not in name_tokens:
            return None
    # An affirm opener followed by a negation token ("ok don't", "yes not now")
    # is a refusal, not an accept.
    if kind == "accept" and any(t in ("dont", "don't", "not", "no") for t in tokens[1:]):
        return "dismiss"
    return kind


async def _match_pending_offer_reply(text: str, user_id: str) -> Optional["Intent"]:
    """Map a short yes/no turn onto the SURFACED contact offer it answers.

    Binding rule: when the reply names a person ("yes add Caitlin"), it binds to
    the unique surfaced offer whose name matches; a bare reply ("yes") binds to
    the OLDEST surfaced offer — the one the for-prompt fold told the brain to
    ask FIRST — never the newest. A name matching zero or multiple offers falls
    through to normal routing instead of guessing.
    """
    # Cheap shape pre-check (opener word only) before any flag/DB work.
    tokens = _offer_reply_tokens(text)
    if not tokens or tokens[0] not in (_OFFER_AFFIRM_FIRST | _OFFER_DECLINE_FIRST):
        return None
    try:
        from pending_suggestions import (
            person_suggestions_enabled,
            surfaced_person_offers,
        )
        if not person_suggestions_enabled():
            return None
        offers = await surfaced_person_offers(user_id)
    except Exception as exc:
        logger.debug("_match_pending_offer_reply: lookup failed: %s", exc)
        return None
    offers = [o for o in offers if o.get("id")]
    if not offers:
        return None
    # A reply that carries a name token binds to that offer, if unambiguous.
    token_set = set(tokens[1:])
    named = [
        o for o in offers
        if token_set & {t for t in str(o.get("name") or "").lower().split() if t}
    ]
    if len(named) > 1:
        return None  # ambiguous — let the brain sort it out
    offer = named[0] if named else offers[0]
    kind = _offer_reply_kind(text, offer.get("name") or "")
    if kind is None:
        return None
    return Intent(
        "pending_offer_accept" if kind == "accept" else "pending_offer_dismiss",
        {
            "suggestion_id": offer["id"],
            "name": offer.get("name") or "",
            "relationship": offer.get("relationship") or "",
        },
    )


async def detect_and_extract_intent(
    text: str,
    user_id: str = "guest",  # fail-open to least-privilege, not admin (#1021/#1032 posture)
    context: "Optional[ConversationContext]" = None,
) -> Optional["Intent"]:
    """
    Async wrapper around detect_intent that populates structured slots for create
    intents via the local LLM (nlu_extractor).

    For query/compute intents (time_query, weather, calendar_show, etc.) that
    already carry structured slots, the intent is returned immediately with no
    LLM call.

    For create intents that the simplified keyword classifier tagged with
    {"raw": text}, the LLM extracts proper slots (title, date, time, …) and
    the returned Intent carries those structured values — all downstream
    consumers (_build_command, form builders, intent_card_data, broadcast
    functions) receive the same slot shape as before.

    Returns None when no intent matched OR when LLM extraction failed (caller
    should fall through to Zoe Agent).
    """
    def _context_turns() -> str:
        if context and context.is_fresh() and context.last_text:
            return f"previous={context.last_text!r}; previous_intent={context.last_intent}"
        return ""

    shadow_unset = object()

    def _env_enabled(name: str) -> bool:
        return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}

    def _schedule_pi_shadow(
        zoe_intent: Optional["Intent"],
        *,
        route_class: str,
        zoe_latency_ms: float | None,
        pi_result=shadow_unset,
        baseline_kind: str | None = None,
        baseline_comparable: bool | None = None,
        router_latency_ms: float | None = None,
    ) -> None:
        if not _env_enabled("ZOE_PI_INTENT_SHADOW_ENABLED"):
            return

        async def _runner() -> None:
            try:
                from pi_intent_shadow import maybe_record_pi_intent_shadow

                kwargs = {
                    "zoe_intent": zoe_intent.name if zoe_intent else None,
                    "zoe_confidence": zoe_intent.confidence if zoe_intent else None,
                    "zoe_latency_ms": zoe_latency_ms,
                    "route_class": route_class,
                    "baseline_kind": baseline_kind,
                    "baseline_comparable": baseline_comparable,
                    "router_latency_ms": router_latency_ms,
                    "user_id": user_id,
                    "context_turns": _context_turns(),
                }
                if pi_result is not shadow_unset:
                    kwargs["pi_result"] = pi_result
                await maybe_record_pi_intent_shadow(text, **kwargs)
            except Exception as exc:
                logger.debug("detect_and_extract_intent: Pi shadow evidence failed: %s", exc)

        task = asyncio.create_task(_runner())
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)

    def _pi_execution_has_promoted_groups() -> bool:
        if not _env_enabled("ZOE_PI_INTENT_ENABLED"):
            return False
        requested = {
            group.strip()
            for group in str(os.environ.get("ZOE_PI_INTENT_PROMOTED_GROUPS") or "").split(",")
            if group.strip()
        }
        return bool(requested.intersection(LOW_RISK_PI_INTENT_GROUPS))

    async def _try_pi_governor() -> tuple[Optional["Intent"], object | None]:
        if not _pi_execution_has_promoted_groups():
            return None, shadow_unset
        pi_classified = None
        try:
            from pi_intent_classifier import PI_INTENT_EXECUTE_THRESHOLD, classify_with_pi_intent_governor, pi_intent_is_promoted

            pi_classified = await classify_with_pi_intent_governor(text, context_turns=_context_turns())
            if pi_classified and pi_classified.intent and pi_classified.confidence >= PI_INTENT_EXECUTE_THRESHOLD:
                if pi_intent_is_promoted(pi_classified.intent):
                    return Intent(pi_classified.intent, dict(pi_classified.slots), confidence=pi_classified.confidence), pi_classified
                logger.debug(
                    "detect_and_extract_intent: Pi intent %s classified but not promoted for execution",
                    pi_classified.intent,
                )
        except Exception as exc:
            logger.debug("detect_and_extract_intent: Pi/Gemma governor failed: %s", exc)
        return None, pi_classified

    # Off-panel contact-offer replies FIRST: a bare "yes"/"sure"/"no thanks"
    # after a surfaced "add X as a contact?" offer must resolve that offer, and
    # would otherwise be swallowed by the acknowledgement/greeting intents below.
    # Triple-guarded (flag + shape + a surfaced offer existing); returns None for
    # everything else so normal routing is untouched.
    try:
        _offer_reply = await _match_pending_offer_reply(text, user_id)
    except Exception as _offer_exc:  # never let the offer path break routing
        logger.debug("detect_and_extract_intent: offer-reply match failed: %s", _offer_exc)
        _offer_reply = None
    if _offer_reply is not None:
        return _offer_reply

    started = time.perf_counter()
    intent = detect_intent(text, context=context, user_id=user_id)
    detect_latency_ms = (time.perf_counter() - started) * 1000
    if intent is None:
        routed_intent, pi_classified = await _try_pi_governor()
        _schedule_pi_shadow(
            None,
            route_class="fallback",
            zoe_latency_ms=detect_latency_ms,
            pi_result=pi_classified if _env_enabled("ZOE_PI_INTENT_ENABLED") else shadow_unset,
            baseline_kind="router_only_not_comparable",
            baseline_comparable=False,
            router_latency_ms=detect_latency_ms,
        )
        return routed_intent
    if intent.slots and "raw" in intent.slots:
        if intent.name == "reminder_create":
            structured = _extract_simple_reminder_slots(intent.slots["raw"])
            if structured:
                intent.slots = structured
                _schedule_pi_shadow(
                    intent,
                    route_class="deterministic",
                    zoe_latency_ms=(time.perf_counter() - started) * 1000,
                    baseline_kind="router",
                    baseline_comparable=True,
                    router_latency_ms=detect_latency_ms,
                )
                return intent
        try:
            from nlu_extractor import extract_slots_for_intent  # lazy — avoids circular at load
            structured = await extract_slots_for_intent(intent.name, intent.slots["raw"])
            if structured:
                intent.slots = structured
                _schedule_pi_shadow(
                    intent,
                    route_class="deterministic",
                    zoe_latency_ms=(time.perf_counter() - started) * 1000,
                    baseline_kind="router",
                    baseline_comparable=True,
                    router_latency_ms=detect_latency_ms,
                )
                return intent
        except Exception as _exc:
            logger.warning(
                "detect_and_extract_intent: nlu_extractor failed intent=%s err=%s",
                intent.name,
                _exc,
            )
        # Extraction failed — let Pi/Gemma classify the ambiguous utterance once before Zoe Agent fallback.
        extraction_failed_latency_ms = (time.perf_counter() - started) * 1000
        routed_intent, pi_classified = await _try_pi_governor()
        _schedule_pi_shadow(
            None,
            route_class="extraction_failed",
            zoe_latency_ms=extraction_failed_latency_ms,
            pi_result=pi_classified if _env_enabled("ZOE_PI_INTENT_ENABLED") else shadow_unset,
            baseline_kind="router_extraction_failed_not_comparable",
            baseline_comparable=False,
            router_latency_ms=detect_latency_ms,
        )
        return routed_intent
    _schedule_pi_shadow(
        intent,
        route_class="deterministic",
        zoe_latency_ms=detect_latency_ms,
        baseline_kind="router",
        baseline_comparable=True,
        router_latency_ms=detect_latency_ms,
    )
    return intent


def _normalize_list(raw: str) -> str:
    mapping = {
        "shopping": "shopping", "grocery": "shopping", "groceries": "shopping",
        "todo": "personal", "to do": "personal", "to-do": "personal",
        "personal": "personal", "work": "work", "tasks": "tasks",
        "bucket": "bucket",
    }
    return mapping.get(raw, "shopping")


_TASK_KEYWORDS = {
    "call", "email", "message", "buy ticket", "book", "pay", "bill",
    "task", "todo", "work", "project", "report", "review", "fix",
    "appointment", "meeting", "dentist", "doctor",
}


def _infer_list(item: str) -> str:
    lower = item.lower()
    if any(kw in lower for kw in _TASK_KEYWORDS):
        return "personal"
    return "shopping"


# Cues that belong to a NON-list capability (journal, people/contacts,
# calendar, reminders). When one of these appears in an "add …" turn WITHOUT an
# explicit shopping/grocery-list target, the deterministic list_add fast path
# must NOT claim the turn — defer to the brain, which routes journal/contacts
# correctly. An explicit list target overrides (see _EXPLICIT_LIST_TARGET_RE).
#
# NOTE (deliberate): bare "note"/"notes" is NOT here. Note-CREATION phrasings
# ("make a note: …", "note that …", "jot down …") are routed to note_create by
# the NOTES CREATE matchers *above* this fast path, so they never reach here.
# A shopping item that merely contains the word ("add sticky notes", "add a
# note pad") must stay a list_add — including it would over-defer those.
# "note" as a note-creation cue is a "make/take/write a note" verb frame, which
# the upstream note_create regex already owns.
_COMPETING_LIST_CUE_RE = re.compile(
    r"\b(journal|diary|contact|contacts|calendar|reminder|reminders)\b",
    re.IGNORECASE,
)
# Explicit shopping/grocery/list target — if present, keep list_add even when a
# competing word co-occurs ("add notebook to my shopping list" stays a list).
_EXPLICIT_LIST_TARGET_RE = re.compile(
    r"\bto\s+(?:the\s+|my\s+)?(?:shopping|grocery|groceries)(?:\s+list)?\b"
    r"|\b(?:shopping|grocery|groceries)\s+list\b"
    r"|\bto\s+(?:the\s+|my\s+)?(?:todo|to-do|to do|personal|work|bucket|tasks)\s+list\b",
    re.IGNORECASE,
)


def _has_competing_list_cue(text: str) -> bool:
    """True when `text` names a non-list capability and lacks an explicit list
    target, so the implicit list_add matcher should defer to the brain."""
    if _EXPLICIT_LIST_TARGET_RE.search(text):
        return False
    return bool(_COMPETING_LIST_CUE_RE.search(text))


def _sanitize_list_item(raw: str) -> str:
    item = str(raw or "").strip()
    item = re.sub(r"\s+", " ", item)
    item = re.sub(r"^(please|pls)\s+", "", item, flags=re.IGNORECASE)
    item = re.sub(r"^(add|put|get|buy)\s+", "", item, flags=re.IGNORECASE)
    item = re.sub(r"\s+(to|on)\s+(?:the\s+|my\s+)?(?:shopping|grocery|groceries)\s+list$", "", item, flags=re.IGNORECASE)
    item = re.sub(r"[.,;:!?]+$", "", item)
    return item.strip()


async def _run_mcporter(cmd: str) -> Optional[str]:
    """Run a single mcporter-safe command, return raw stdout or None on failure.

    Spawns via async_subprocess.run_to_completion so the fork happens OFF the
    event-loop thread — asyncio.create_subprocess_exec forks on the loop and
    can wedge the whole API (the 2026-06-29 outage class, #947).
    """
    env = os.environ.copy()
    env["PATH"] = f"{NODE_BIN}:{env.get('PATH', '')}"
    try:
        from async_subprocess import run_to_completion

        proc = await run_to_completion(shlex.split(cmd), env=env, timeout=10)
        if proc.returncode != 0:
            logger.warning(f"mcporter-safe failed: {proc.stderr.decode()}")
            return None
        return proc.stdout.decode().strip()
    except subprocess.TimeoutExpired:
        logger.warning("mcporter-safe timed out")
        return None
    except (OSError, UnicodeError, ValueError) as e:
        logger.error(f"mcporter error: {e}")
        return None


async def _load_direct_execution_user(db, user_id: str) -> Optional[dict]:
    cursor = await db.execute("SELECT id, role, name FROM users WHERE id = ?", (user_id,))
    row = await cursor.fetchone()
    if not row:
        return None
    raw_role = row.get("role") if hasattr(row, "get") else row["role"]
    return {
        "user_id": row["id"],
        "role": raw_role or "user",
        "username": row.get("name", "") if hasattr(row, "get") else "",
    }


async def _execute_reminder_create_direct(intent: Intent, user_id: str) -> Optional[str]:
    slots = intent.slots or {}
    title = str(slots.get("title") or "").strip()
    if not title:
        return None
    try:
        from database import get_db_ctx
        from models import ReminderCreate
        from reminder_service import create_reminder_record

        async with get_db_ctx() as db:
            user = await _load_direct_execution_user(db, user_id)
            if not user:
                return None
            payload = ReminderCreate(
                title=title,
                due_date=slots.get("date") or None,
                due_time=slots.get("time") or None,
                category=slots.get("category") or "general",
            )
            reminder = await create_reminder_record(payload, user=user, db=db)
        return _format_response(intent, json.dumps(reminder, default=str))
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("reminder_create direct execution unavailable; falling back to mcporter: %s", exc)
        return None


async def _execute_reminder_list_direct(intent: Intent, user_id: str) -> Optional[str]:
    """Read reminders straight from the reminders table (mirrors mcp_server's
    reminder_list query) so 'what are my reminders' works even when the mcporter
    MCP subprocess is down, and an empty list formats as 'No reminders set.'
    instead of surfacing as a failure (ok:false) to brain/tool consumers."""
    try:
        from database import get_db_ctx

        async with get_db_ctx() as db:
            cursor = await db.execute(
                "SELECT id, title, due_date, due_time, priority, category FROM reminders"
                " WHERE (visibility = 'family' OR user_id = ?)"
                " AND is_active = 1 AND deleted = 0 ORDER BY due_date, due_time LIMIT 20",
                (user_id,),
            )
            rows = [dict(r) for r in await cursor.fetchall()]
        return _format_response(intent, json.dumps({"reminders": rows}, default=str))
    except Exception as exc:
        logger.warning("reminder_list direct execution unavailable; falling back to mcporter: %s", exc)
        return None


def _escape_like_pattern(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _execute_list_show_direct(intent: Intent, user_id: str) -> Optional[str]:
    slots = intent.slots or {}
    list_type = str(slots.get("list_type") or "shopping").strip() or "shopping"
    list_name = str(slots.get("list_name") or "").strip()
    try:
        from database import get_db_ctx

        async with get_db_ctx() as db:
            if list_name:
                cursor = await db.execute(
                    "SELECT l.id, l.name, li.id as item_id, li.text, li.completed, li.quantity, li.category"
                    " FROM lists l LEFT JOIN list_items li ON l.id = li.list_id AND li.deleted=0"
                    " WHERE (l.user_id=? OR l.visibility='family') AND l.list_type=?"
                    " AND l.name LIKE ? ESCAPE '\\' AND l.deleted=0"
                    " ORDER BY li.sort_order",
                    (user_id, list_type, f"%{_escape_like_pattern(list_name)}%"),
                )
            else:
                cursor = await db.execute(
                    "SELECT l.id, l.name, li.id as item_id, li.text, li.completed, li.quantity, li.category"
                    " FROM lists l LEFT JOIN list_items li ON l.id = li.list_id AND li.deleted=0"
                    " WHERE (l.user_id=? OR l.visibility='family') AND l.list_type=? AND l.deleted=0"
                    " ORDER BY l.name, li.sort_order",
                    (user_id, list_type),
                )
            rows = await cursor.fetchall()

        lists_map: dict[str, dict] = {}
        for row in rows:
            data = dict(row)
            list_id = data.get("id")
            if not list_id:
                continue
            if list_id not in lists_map:
                lists_map[list_id] = {"id": list_id, "name": data.get("name"), "items": []}
            if data.get("item_id") and data.get("text"):
                lists_map[list_id]["items"].append(
                    {
                        "id": data.get("item_id"),
                        "text": data.get("text"),
                        "completed": bool(data.get("completed")),
                        "quantity": data.get("quantity"),
                        "category": data.get("category"),
                    }
                )

        return _format_response(intent, json.dumps({"lists": list(lists_map.values())}))
    except Exception as exc:
        logger.warning("list_show direct execution unavailable; falling back to mcporter: %s", exc)
        return None


async def _notify_lists_ui(event_type: str, data: dict) -> None:
    """Best-effort UI push mirroring mcp_server's list_* notifications.
    Never raises: a broadcast failure must not turn a successful DB write into
    ok:false. Imported lazily so intent_router doesn't hard-depend on mcp_server."""
    try:
        from mcp_server import _notify_ui

        await _notify_ui("lists", event_type, data)
    except Exception as exc:  # noqa: BLE001 — UI push is advisory only
        logger.debug("lists UI notify skipped: %s", exc)


async def _execute_list_add_direct(intent: Intent, user_id: str) -> Optional[str]:
    """Add an item to the user's list straight through the DB, mirroring
    mcp_server's list_add_item tool (same list resolution, family/personal
    visibility default, and list creation) so 'add milk to my shopping list'
    works when the mcporter subprocess is down. Returns a confirming string on
    success; None only on genuine failure (so ok:false still means real failure)."""
    slots = intent.slots or {}
    item = str(slots.get("item") or "").strip()
    if not item:
        return None
    lt = str(slots.get("list_type") or "shopping").strip() or "shopping"
    ln = str(slots.get("list_name") or "").strip() or lt.capitalize()
    try:
        from database import get_db_ctx

        async with get_db_ctx() as db:
            cursor = await db.execute(
                "SELECT id FROM lists WHERE list_type=? AND name=? AND deleted=0"
                " AND (user_id=? OR visibility='family')"
                " ORDER BY CASE WHEN visibility='family' THEN 0 ELSE 1 END LIMIT 1",
                (lt, ln, user_id),
            )
            row = await cursor.fetchone()
            item_id = str(uuid.uuid4())
            if row:
                # Existing list: a single INSERT is already atomic.
                list_id = row["id"]
                # Retry idempotency (same guard as the skybridge add): a voice
                # re-POST replays the identical add seconds later — treat it as
                # already done instead of inserting a duplicate.
                # created_at is a TEXT column, so it must be cast before the
                # timestamp comparison — a bare `created_at > now() - interval`
                # throws `operator does not exist: text > timestamp` on Postgres,
                # which silently drops the whole direct add to the mcporter
                # fallback (live 2026-07-08: 69 such errors under eval traffic).
                dup_cursor = await db.execute(
                    "SELECT id FROM list_items WHERE list_id=? AND lower(text)=lower(?)"
                    " AND deleted=0 AND created_at::timestamptz > now() - interval '10 seconds' LIMIT 1",
                    (list_id, item),
                )
                dup_row = await dup_cursor.fetchone()
                if dup_row:
                    logger.info("list add: duplicate %r within 10s on list %s — replay skipped", item, list_id)
                else:
                    await db.execute(
                        "INSERT INTO list_items (id, list_id, text, quantity, category) VALUES (?,?,?,?,?)",
                        (item_id, list_id, item, slots.get("quantity"), slots.get("category")),
                    )
            else:
                # Fresh list: the list row and its first item must land together,
                # or a failed item insert leaves an orphaned empty list. asyncpg
                # auto-commits each statement, so wrap both in one transaction.
                list_id = str(uuid.uuid4())

                async def _write_new_list_and_item() -> None:
                    await db.execute(
                        "INSERT INTO lists (id, user_id, name, list_type, visibility) VALUES (?,?,?,?,?)",
                        (list_id, user_id, ln, lt,
                         "personal" if lt in {"personal", "tasks", "shopping"} else "family"),
                    )
                    await db.execute(
                        "INSERT INTO list_items (id, list_id, text, quantity, category) VALUES (?,?,?,?,?)",
                        (item_id, list_id, item, slots.get("quantity"), slots.get("category")),
                    )

                txn = getattr(db, "transaction", None)
                if callable(txn):
                    async with txn():
                        await _write_new_list_and_item()
                else:  # fallback (e.g. a DB shim without transaction support)
                    await _write_new_list_and_item()
        await _notify_lists_ui(
            "list_updated",
            {"action": "item_added", "list_id": list_id, "item": {"id": item_id, "text": item}},
        )
        return _format_response(
            intent,
            json.dumps({"item_id": item_id, "list": ln, "list_id": list_id, "text": item, "status": "added"}),
        )
    except Exception as exc:
        logger.warning("list_add direct execution unavailable; falling back to mcporter: %s", exc)
        return None


async def _execute_list_remove_direct(intent: Intent, user_id: str) -> Optional[str]:
    """Remove (complete) an item from the user's list, mirroring mcp_server's
    list_remove_item tool (exact case-insensitive match first, LIKE-escaped
    substring fallback). If the item isn't on the list that's a clean success
    message (ok:true, 'X wasn't on your list'), not a failure — so brain/tool
    consumers don't surface a false error. Returns None only on genuine failure."""
    slots = intent.slots or {}
    item = str(slots.get("item") or "").strip()
    if not item:
        return None
    lt = str(slots.get("list_type") or "shopping").strip() or "shopping"
    try:
        from database import get_db_ctx

        async with get_db_ctx() as db:
            cursor = await db.execute(
                "SELECT li.id, li.list_id FROM list_items li JOIN lists l ON li.list_id = l.id"
                " WHERE (l.user_id=? OR l.visibility='family') AND l.list_type=? AND LOWER(li.text)=LOWER(?)"
                " AND li.deleted=0 AND l.deleted=0 LIMIT 1",
                (user_id, lt, item),
            )
            row = await cursor.fetchone()
            if not row:
                cursor = await db.execute(
                    "SELECT li.id, li.list_id FROM list_items li JOIN lists l ON li.list_id = l.id"
                    " WHERE (l.user_id=? OR l.visibility='family') AND l.list_type=? AND li.text LIKE ? ESCAPE '\\'"
                    " AND li.deleted=0 AND l.deleted=0 LIMIT 1",
                    (user_id, lt, f"%{_escape_like_pattern(item)}%"),
                )
                row = await cursor.fetchone()
            if not row:
                # Not on the list is a clean success, not a failure.
                friendly = "shopping list" if lt == "shopping" else f"{lt.replace('_', ' ')} list"
                return f"{item} wasn't on your {friendly}."
            item_id = row["id"]
            list_id = row["list_id"]
            await db.execute(
                "UPDATE list_items SET completed=1, updated_at=NOW() WHERE id=?", (item_id,)
            )
        await _notify_lists_ui(
            "list_updated",
            {"action": "item_completed", "list_id": list_id, "item_id": item_id},
        )
        return _format_response(
            intent, json.dumps({"item_id": item_id, "text": item, "status": "completed"})
        )
    except Exception as exc:
        logger.warning("list_remove direct execution unavailable; falling back to mcporter: %s", exc)
        return None


def _say_clock(hhmm: str) -> str:
    """'15:00' → '3 PM', '09:30' → '9:30 AM'. Empty/garbage → ''."""
    try:
        h, m = (hhmm or "").split(":")[:2]
        h = int(h); m = int(m)
        ap = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return f"{h12}:{m:02d} {ap}" if m else f"{h12} {ap}"
    except Exception:
        return ""


async def _execute_calendar_show_direct(intent: Intent, user_id: str) -> Optional[str]:
    """Read the calendar straight from the events table so 'what's on my calendar'
    works even when the mcporter MCP subprocess is down (the source of the
    calendar 'hit and miss'). Mirrors the qualifier→date-range logic in
    _build_command's calendar_show branch."""
    from datetime import timedelta
    slots = intent.slots or {}
    qualifier = str(slots.get("qualifier", "")).strip().lower()
    today_d = today_for_zoe_tz()
    if qualifier in ("today", "today's", ""):
        start = end = today_d
        scope = "today"
    elif qualifier == "tomorrow":
        start = end = today_d + timedelta(days=1)
        scope = "tomorrow"
    elif qualifier in ("this week", "this week's"):
        start = today_d - timedelta(days=today_d.weekday())
        end = today_d + timedelta(days=6 - today_d.weekday())
        scope = "this week"
    elif qualifier in ("this month", "this month's"):
        import calendar as cal_mod
        _, last = cal_mod.monthrange(today_d.year, today_d.month)
        start = today_d.replace(day=1); end = today_d.replace(day=last)
        scope = "this month"
    else:
        start = today_d; end = today_d + timedelta(days=7)
        scope = "in the next week"
    try:
        from database import get_db_ctx
        async with get_db_ctx() as db:
            cursor = await db.execute(
                "SELECT title, start_date, start_time, all_day FROM events"
                " WHERE (user_id=? OR visibility='family') AND deleted=0"
                " AND start_date BETWEEN ? AND ?"
                " ORDER BY start_date, COALESCE(NULLIF(start_time,''),'99:99')",
                (user_id, start.isoformat(), end.isoformat()),
            )
            rows = [dict(r) for r in await cursor.fetchall()]
    except Exception as exc:
        logger.warning("calendar_show direct execution unavailable; falling back to mcporter: %s", exc)
        return None

    if not rows:
        return f"You've got nothing on {scope}."
    single_day = start == end
    parts: list[str] = []
    seen: set = set()
    for r in rows:
        title = (r.get("title") or "something").strip()
        st = str(r.get("start_time") or "")
        sd = str(r.get("start_date") or "")
        dedup = (title.lower(), sd, st)
        if dedup in seen:  # identical duplicate event rows → say once
            continue
        seen.add(dedup)
        when = "" if r.get("all_day") else _say_clock(st)
        day = "" if single_day else _spoken_day(sd)
        # "today"/"tomorrow" read better without "on" ("at 9 AM today" not "...on today").
        day_phrase = day if day in ("today", "tomorrow") else (f"on {day}" if day else "")
        bits = [title]
        if when:
            bits.append(f"at {when}")
        if day_phrase:
            bits.append(day_phrase)
        parts.append(" ".join(bits))
    n = len(parts)
    lead = f"You've got {n} thing{'s' if n != 1 else ''} on {scope}: "
    return lead + ("; ".join(parts) if n > 1 else parts[0]) + "."


async def _notify_ui_channel(channel: str, event_type: str, data: dict) -> None:
    """Best-effort UI push mirroring mcp_server's per-tool notifications
    (calendar/notes/journal/all). Never raises: a broadcast failure must not
    turn a successful DB write into ok:false. Imported lazily so intent_router
    doesn't hard-depend on mcp_server."""
    try:
        from mcp_server import _notify_ui

        await _notify_ui(channel, event_type, data)
    except Exception as exc:  # noqa: BLE001 — UI push is advisory only
        logger.debug("%s UI notify skipped: %s", channel, exc)


async def _execute_calendar_create_direct(intent: Intent, user_id: str) -> Optional[str]:
    """Create a calendar event straight through the DB, mirroring mcp_server's
    calendar_create_event tool (same events columns, category default 'general',
    visibility 'family') so 'add X to my calendar' works when the mcporter
    subprocess is down. Slots are normalised with the same _parse_date/_parse_time
    that _build_command's calendar_create branch used. Unlike the note/journal/
    people executors there is deliberately NO MemPalace mirror here — mcp_server's
    calendar_create_event tool doesn't write one either, so matching it means
    omitting it. Returns a confirming string on success; None only on genuine
    failure (so ok:false still means real failure)."""
    slots = intent.slots or {}
    title = str(slots.get("title") or "").strip()
    if not title:
        return None
    raw_date = slots.get("date")
    if raw_date:
        start_date = _parse_date(str(raw_date)) or None
        if not start_date:
            # A date WAS given but couldn't be parsed — fail rather than silently
            # dropping it onto today, which would land the event on a day the user
            # didn't intend. (Distinct from the no-date case below.)
            return None
    else:
        # No day given → default to TODAY. A natural quick-add like "add lunch with
        # Jess at 12pm" means today; asking "which day?" every time is worse UX than
        # defaulting. The confirmation names the day ("...today at 12 PM"), so the
        # user can move it if they meant otherwise.
        start_date = today_for_zoe_tz().isoformat()
    start_time = _parse_time(str(slots.get("time") or "")) if slots.get("time") else None
    category = str(slots.get("category") or "general").strip() or "general"
    try:
        from calendar_service import create_event_record
        from database import get_db_ctx

        async with get_db_ctx() as db:
            record = await create_event_record(
                db,
                user_id=user_id,
                title=title,
                start_date=start_date,
                start_time=start_time,
                category=category,
                all_day=not start_time,
            )
        event_id = record["id"]
        await _notify_ui_channel(
            "calendar", "event_created",
            {"id": event_id, "title": title, "start_date": start_date,
             "start_time": start_time, "category": category},
        )
        day = _spoken_day(start_date)
        when = day if day in ("today", "tomorrow") else f"on {day}"
        clock = _say_clock(start_time) if start_time else ""
        when_phrase = f"{when} at {clock}" if clock else when
        return f"Added {title} to your calendar {when_phrase}."
    except Exception as exc:
        logger.warning("calendar_create direct execution unavailable; falling back to mcporter: %s", exc)
        return None


async def _execute_note_create_direct(intent: Intent, user_id: str) -> Optional[str]:
    """Save a note straight through the DB, mirroring mcp_server's note_create
    tool (same notes columns, category default 'general', visibility 'personal',
    and best-effort MemPalace mirror via routers.notes._store_note_memory) so
    'make a note' works when the mcporter subprocess is down. Returns a confirming
    string on success; None only on genuine failure."""
    slots = intent.slots or {}
    content = str(slots.get("content") or "").strip()
    if not content:
        return None
    title = slots.get("title")
    title = str(title).strip() if title else None
    category = str(slots.get("category") or "general").strip() or "general"
    try:
        from database import get_db_ctx

        note_id = str(uuid.uuid4())
        async with get_db_ctx() as db:
            await db.execute(
                "INSERT INTO notes (id, user_id, title, content, category, visibility)"
                " VALUES (?,?,?,?,?,?)",
                (note_id, user_id, title, content, category, "personal"),
            )
            # Mirror mcp_server: best-effort MemPalace write inside the same
            # session, never fatal to the note write itself.
            try:
                from routers.notes import _store_note_memory  # type: ignore

                await _store_note_memory(
                    db, user_id,
                    {"id": note_id, "title": title, "category": category, "content": content},
                    "created",
                )
            except Exception as mem_exc:  # noqa: BLE001 — memory mirror is advisory
                logger.debug("note_create memory mirror skipped: %s", mem_exc)
        await _notify_ui_channel(
            "notes", "note_created", {"id": note_id, "title": title, "category": category},
        )
        return "Saved your note."
    except Exception as exc:
        logger.warning("note_create direct execution unavailable; falling back to mcporter: %s", exc)
        return None


async def _execute_journal_create_direct(intent: Intent, user_id: str) -> Optional[str]:
    """Write a journal entry straight through the DB, mirroring mcp_server's
    journal_create_entry tool (same journal_entries columns, comma-split tags
    JSON, visibility 'personal', deleted 0, best-effort MemPalace mirror) so
    journalling works when the mcporter subprocess is down. Returns a confirming
    string on success; None only on genuine failure."""
    slots = intent.slots or {}
    content = str(slots.get("content") or "").strip()
    if not content:
        return None
    title = slots.get("title")
    title = str(title).strip() if title else None
    mood = slots.get("mood") or None
    mood_score = slots.get("mood_score")
    tags_str = slots.get("tags")
    tags_json = (
        json.dumps([t.strip() for t in str(tags_str).split(",")]) if tags_str else None
    )
    try:
        from database import get_db_ctx

        entry_id = str(uuid.uuid4())
        async with get_db_ctx() as db:
            await db.execute(
                "INSERT INTO journal_entries (id, user_id, content, title, mood, mood_score,"
                " tags, visibility, deleted) VALUES (?,?,?,?,?,?,?,'personal',0)",
                (entry_id, user_id, content, title, mood, mood_score, tags_json),
            )
            try:
                from routers.journal import _store_journal_memory  # type: ignore

                await _store_journal_memory(
                    db, user_id,
                    {"id": entry_id, "title": title, "mood": mood,
                     "content": content, "mood_score": mood_score},
                    "created",
                )
            except Exception as mem_exc:  # noqa: BLE001 — memory mirror is advisory
                logger.debug("journal_create memory mirror skipped: %s", mem_exc)
        await _notify_ui_channel(
            "journal", "entry_created", {"id": entry_id, "title": title, "mood": mood},
        )
        return "Saved your journal entry."
    except Exception as exc:
        logger.warning("journal_create direct execution unavailable; falling back to mcporter: %s", exc)
        return None


async def _execute_people_create_direct(intent: Intent, user_id: str) -> Optional[str]:
    """Create a person straight through the DB, mirroring mcp_server's
    people_create tool (same people columns, circle default 'circle', context
    default 'personal', visibility 'family', best-effort MemPalace mirror) so
    'add X to my contacts' works when the mcporter subprocess is down. Returns a
    confirming string on success; None only on genuine failure."""
    slots = intent.slots or {}
    name = str(slots.get("name") or "").strip()
    if not name:
        return None
    relationship = slots.get("relationship") or None
    # 'circle' = valid middle tier (inner|circle|public); people.circle is NOT
    # NULL so it needs a value (a NULL default made this direct INSERT fail →
    # silent mcporter fallback that persists nothing).
    circle = str(slots.get("circle") or "").strip() or "circle"
    context = str(slots.get("context") or "personal").strip() or "personal"
    # Private by default — a contact created by voice/chat should not be shared
    # with the whole family unless asked. Owner still sees it (people reads are
    # `visibility='family' OR user_id=caller`). Mirrors PR #1177.
    visibility = str(slots.get("visibility") or "").strip() or "personal"
    try:
        from database import get_db_ctx

        person_id = str(uuid.uuid4())
        async with get_db_ctx() as db:
            # Ensure the acting user exists first. The intent-dispatch path the
            # flue brain's tools use does NOT run _ensure_user_and_chat_session
            # (the chat path's guard), so an authed identity that only has
            # MemPalace memories but no `users` row would violate
            # people_user_id_fkey and silently fall back to the mcporter path
            # (which persists nothing). Mirror the chat path's upsert so contact
            # creation works for any acting identity.
            await db.execute(
                "INSERT INTO users (id, name, role) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (user_id, user_id, "member"),
            )
            # Dedupe by name (case-insensitive) — a double-tap Add / retry (or a
            # re-issued voice command) must not create a second row. Mirrors the
            # pending-suggestion accept flow's dedup. Idempotent: returns a truthy
            # confirmation so the caller treats it as success (no mcporter fallback).
            dup_cursor = await db.execute(
                "SELECT id FROM people WHERE user_id = ? AND lower(name) = lower(?)"
                " AND (deleted = 0 OR deleted IS NULL) LIMIT 1",
                (user_id, name),
            )
            if await dup_cursor.fetchone():
                rel_phrase = f" as your {relationship}" if relationship else ""
                return f"You already have {name}{rel_phrase} in your contacts."
            await db.execute(
                "INSERT INTO people (id, user_id, name, relationship, birthday, phone, email,"
                " notes, visibility, circle, context) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (person_id, user_id, name, relationship, None, None, None, None,
                 visibility, circle, context),
            )
            try:
                from routers.people import _store_person_memory  # type: ignore

                await _store_person_memory(
                    db, user_id,
                    {"id": person_id, "name": name, "relationship": relationship,
                     "birthday": None, "phone": None, "email": None, "notes": None},
                    "created",
                )
            except Exception as mem_exc:  # noqa: BLE001 — memory mirror is advisory
                logger.debug("people_create memory mirror skipped: %s", mem_exc)
        await _notify_ui_channel(
            "all", "people:created",
            {"id": person_id, "name": name, "relationship": relationship},
        )
        rel_phrase = f" as your {relationship}" if relationship else ""
        return f"Added {name}{rel_phrase} to your contacts."
    except Exception as exc:
        logger.warning("people_create direct execution unavailable; falling back to mcporter: %s", exc)
        return None


async def _execute_note_search_direct(intent: Intent, user_id: str) -> Optional[str]:
    """Search the user's notes straight from the DB (mirrors mcp_server's
    note_search query) so 'search my notes for X' surfaces the user's OWN notes.

    The mcporter path built the command WITHOUT user_id, so the MCP tool fell back
    to the family-admin identity and searched the wrong user's rows — an authed
    user's notes never surfaced. This direct executor binds the acting user_id in
    trusted code, exactly like the note_create direct executor. Returns the
    formatted response, or None to fall back to mcporter on genuine failure."""
    slots = intent.slots or {}
    query = str(slots.get("query") or "").strip()
    if not query:
        return None
    try:
        from database import get_db_ctx

        like = f"%{_escape_like_pattern(query)}%"
        async with get_db_ctx() as db:
            cursor = await db.execute(
                "SELECT id, title, content, category, created_at FROM notes"
                " WHERE (title ILIKE ? ESCAPE '\\' OR content ILIKE ? ESCAPE '\\')"
                " AND user_id = ? AND deleted = 0 LIMIT 10",
                (like, like, user_id),
            )
            rows = [dict(r) for r in await cursor.fetchall()]
        return _format_response(intent, json.dumps({"notes": rows}, default=str))
    except Exception as exc:
        logger.warning("note_search direct execution unavailable; falling back to mcporter: %s", exc)
        return None


async def _execute_people_search_direct(intent: Intent, user_id: str) -> Optional[str]:
    """Look up the user's contacts straight from the DB (mirrors mcp_server's
    people_search query) so 'who is X' surfaces the user's OWN contacts.

    Same root cause as note_search: the mcporter command omitted user_id, so the
    lookup ran as family-admin and never found an authed user's contacts. Binds
    the acting user_id in trusted code, mirroring the people_create direct
    executor. Returns the formatted response, or None to fall back to mcporter."""
    slots = intent.slots or {}
    query = str(slots.get("query") or "").strip()
    try:
        from database import get_db_ctx

        async with get_db_ctx() as db:
            if query:
                like = f"%{_escape_like_pattern(query)}%"
                cursor = await db.execute(
                    "SELECT id, name, relationship, birthday, phone, email FROM people"
                    " WHERE name ILIKE ? ESCAPE '\\' AND user_id = ? AND deleted = 0 LIMIT 10",
                    (like, user_id),
                )
            else:
                # Empty query = "show my contacts" / "open contacts page"
                # navigation. List the acting user's OWN contacts directly —
                # never return None here, or the intent falls through to the
                # mcporter command (which omits user_id and would surface
                # family-admin's contacts to any authed user).
                cursor = await db.execute(
                    "SELECT id, name, relationship, birthday, phone, email FROM people"
                    " WHERE user_id = ? AND deleted = 0 ORDER BY name LIMIT 20",
                    (user_id,),
                )
            rows = [dict(r) for r in await cursor.fetchall()]
        return _format_response(intent, json.dumps({"people": rows}, default=str))
    except Exception as exc:
        logger.warning("people_search direct execution unavailable; falling back to mcporter: %s", exc)
        return None


async def execute_intent(intent: Intent, user_id: str = "guest") -> Optional[str]:
    # ^ shared write funnel: fail-open to least-privilege guest, not admin, when a
    #   caller omits identity (#1021/#1032 posture). All live callers pass an explicit
    #   user_id; this default is defense-in-depth for future forgetful callers.
    if intent.name == "lets_talk":
        # Navigation is handled by _broadcast_intent_nav via _INTENT_PANEL_NAV in chat.py.
        # Return a short TTS reply confirming we're opening voice mode.
        return "Sure, let's talk."

    if intent.name == "music_setup":
        return await _execute_music_setup(user_id)

    if intent.name in {"music_play", "music_control", "music_volume"}:
        return await _execute_music_intent(intent, user_id)

    if intent.name == "good_morning":
        return await _execute_daily_briefing(user_id)

    if intent.name == "good_evening":
        fallback = "Good evening! Hope your day went well. Let me know if there's anything you need."
        try:
            from proactive.composer import compose_message
            return await compose_message(
                "good_evening",
                {
                    "user_id": user_id,
                    "request": (
                        "Provide a brief, warm end-of-day check-in. Mention outstanding "
                        "reminders or tomorrow's early events if known; otherwise give a "
                        "warm sign-off. Keep it under 3 sentences."
                    ),
                },
                fallback,
            )
        except (ImportError, RuntimeError, TypeError, ValueError) as _e:
            logger.warning("good_evening composer failed: %s", _e)
            return fallback

    if intent.name == "daily_briefing":
        return await _execute_daily_briefing(user_id)

    # Clock queries — instant Python datetime, no LLM, no network (mirrors HA's HassGetCurrentTime/Date)
    if intent.name == "time_query":
        from datetime import datetime
        import platform
        now = datetime.now()
        fmt = "%-I:%M %p" if platform.system() != "Windows" else "%I:%M %p"
        # Keep this short and natural for spoken output.
        return f"It's {now.strftime(fmt)}."

    if intent.name == "date_query":
        from datetime import datetime
        now = datetime.now()
        spoken_day = _spoken_day_ordinal(now.day)
        return f"Today is {now.strftime('%A')}, {now.strftime('%B')} {spoken_day}."

    # Social acknowledgements & presence checks — instant canned replies (no LLM).
    if intent.name == "acknowledgement":
        import random
        if intent.slots.get("kind") == "thanks":
            return random.choice(["You're welcome!", "Anytime.", "Happy to help.", "No worries."])
        return random.choice(["Got it.", "Sure thing.", "Okay.", "No problem."])

    if intent.name == "status_check":
        import random
        return random.choice(["I'm here.", "Still here.", "Listening.", "Ready when you are."])

    if intent.name == "time_planning_clarification":
        if intent.slots.get("kind") == "time_math":
            return "I need the actual times before I can work that out."
        return "What time do you need to arrive?"

    if intent.name == "connect_chatgpt":
        # Handled directly by chat.py via _chatgpt_connect_flow() — which runs
        # the full device-code OAuth flow inline in the SSE stream.  Return None
        # here so execute_intent does not produce a static text reply.
        return None

    # Contact-offer replies (QA review F5): a surfaced "add X as a contact?"
    # offer answered off-panel with a plain yes/no. Accept goes through the
    # sanctioned pending_suggestions.execute_suggestion path (same as the panel
    # card's accept route); refusal dismisses so the offer stops re-surfacing.
    if intent.name == "pending_offer_accept":
        from pending_suggestions import execute_suggestion
        _name = intent.slots.get("name") or "them"
        res = await execute_suggestion(intent.slots.get("suggestion_id", ""), user_id)
        if res.get("ok"):
            logger.info("pending_offer_accept: contact saved user=%s", user_id)
            return f"Done — I've added {_name} to your contacts."
        logger.info("pending_offer_accept failed user=%s err=%s", user_id, res.get("error"))
        return (
            f"I couldn't save {_name} as a contact just now — "
            "you can add them from the People panel."
        )

    if intent.name == "pending_offer_dismiss":
        from pending_suggestions import mark_resolved
        _name = intent.slots.get("name") or "them"
        if await mark_resolved(intent.slots.get("suggestion_id", ""), user_id):
            return f"No problem — I won't save {_name} as a contact."
        # The update didn't land (DB failure / offer already gone) — be honest
        # so the user isn't told it's dismissed while it can keep resurfacing.
        logger.info("pending_offer_dismiss failed user=%s", user_id)
        return (
            f"I tried to drop that offer for {_name} but couldn't update it just now — "
            "if I ask again, another no will clear it."
        )

    # "forget that" / "never mind what I said" — retract the last MemPalace write.
    # Scoped to the caller's user_id and to writes within the last 10 minutes so
    # a stale retraction can't wipe anything older. Short window keeps the
    # semantics predictable across voice + SSE clients.
    if intent.name == "memory_forget_last":
        try:
            from memory_service import get_memory_service
            svc = get_memory_service()
            ref = await svc.forget_last(user_id=user_id)
        except Exception as exc:
            logger.info("memory_forget_last: service unavailable: %s", exc)
            return "I couldn't reach the memory store right now, so nothing was changed."
        if ref is None:
            return "There's nothing recent I can forget — no new memories in the last few minutes."
        preview = (ref.text or "").strip()
        if len(preview) > 80:
            preview = preview[:77] + "…"
        return f"Done — I forgot: \"{preview}\"."

    # "forget everything about X" -- archive (soft-delete, NEVER hard-delete)
    # every memory of the caller's that is name-anchored on X (QA review F14).
    # Deterministic: MemoryService search + list, then a strict whole-word
    # match on the entity name -- no fuzzy nuking, no LLM. Guests fail closed.
    if intent.name == "memory_forget_entity":
        name = str(intent.slots.get("name", "")).strip()
        if not name:
            return "Who should I forget about? Give me the name and I'll do it."
        try:
            from memory_service import get_memory_service, is_guest_memory_user
        except Exception as exc:
            logger.info("memory_forget_entity: service unavailable: %s", exc)
            return "I couldn't reach the memory store right now, so nothing was changed."
        if is_guest_memory_user(user_id):
            # Fail closed: guests have no memory store and must not be able to
            # trigger archive sweeps.
            return "I don't keep memories in guest sessions, so there's nothing for me to forget."
        try:
            svc = get_memory_service()
            # Semantic search surfaces the ranked rows; the approved list makes
            # the sweep complete even when the entity falls outside search's
            # top-k. Both sides still pass the strict name filter below.
            rows = list(await svc.search(name, user_id=user_id, limit=25))
            # Paginate the approved sweep: a single limit=1000 page would let a
            # privacy request silently miss rows for users with bigger stores.
            offset = 0
            while True:
                page = await svc.list_by_status(
                    user_id=user_id, status="approved", limit=1000, offset=offset)
                if not page:
                    break
                rows.extend(page)
                if len(page) < 1000:
                    break
                offset += len(page)
        except Exception as exc:
            logger.info("memory_forget_entity: lookup failed: %s", exc)
            return "I couldn't reach the memory store right now, so nothing was changed."
        # Strict name anchoring: the row's text must contain the entity name as
        # a whole word/phrase (case-insensitive). No stemming, no similarity.
        name_re = re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
        seen_ids: set[str] = set()
        matches = []
        for r in rows:
            rid = getattr(r, "id", "") or ""
            if not rid or rid in seen_ids:
                continue
            seen_ids.add(rid)
            # Ownership guard: search can surface family-visible rows owned by
            # another user; a forget sweep must only ever archive the CALLER's
            # own rows.
            _md = getattr(r, "metadata", {}) or {}
            if _md.get("user_id") != user_id and _md.get("wing") != user_id:
                continue
            if name_re.search(getattr(r, "text", "") or ""):
                matches.append(r)
        if not matches:
            return f"I don't have anything saved about {name}."
        forgotten = 0
        for r in matches:
            try:
                await svc.review(r.id, decision="archive", actor=user_id,
                                 note=f"forget_entity:{name}")
                forgotten += 1
            except Exception as exc:
                logger.warning("memory_forget_entity: archive failed id=%s: %s",
                               r.id, exc)
        if forgotten == 0:
            return (f"I found {len(matches)} memories about {name} but couldn't "
                    "archive them just now -- nothing was changed.")
        # Forgetting a person also withdraws any pending "add X as a contact?"
        # offer — otherwise the just-forgotten name keeps resurfacing in every
        # prompt via the offer seam (live repro 2026-07-13). Best-effort.
        try:
            from pending_suggestions import resolve_person_offers_by_name
            await resolve_person_offers_by_name(user_id, name)
        except Exception as exc:
            # Memories ARE forgotten at this point — a failed offer withdrawal
            # must be visible (the stale offer would keep resurfacing the name)
            # but must not fail the forget. Self-healing backstop: offers expire
            # after 6 user turns regardless.
            logger.warning(
                "memory_forget_entity: offer withdrawal FAILED for user=%s (%s) — "
                "a pending contact offer may resurface until it expires",
                user_id, type(exc).__name__,
            )
        things = "thing" if forgotten == 1 else "things"
        suffix = "" if forgotten == len(matches) else (
            f" ({len(matches) - forgotten} I couldn't reach just now.)")
        return f"Okay — I've forgotten {forgotten} {things} about {name}.{suffix}"

    # "remember that <fact>" — an EXPLICIT, model-callable memory write. This is
    # the fulfillment for the Flue sidecar's remember_fact + remember_emotional_moment
    # tools (Wave 3 of the cutover cut list, docs/knowledge/flue-cutover-tool-cut-list.md §3,
    # plus the emotional-thread capture signal,
    # docs/architecture/zoe-memory-emotional-thread-handoff.md). Goes through
    # MemoryService.ingest, the same durable write path expert_dispatch uses for
    # voice facts — so PII scrubbing, dedup, and scope validation all apply. A stable
    # idempotency key (user + normalized text) collapses repeats, matching
    # expert_dispatch.store_fact.
    #
    # memory_type / valence / intensity come from the caller's slots. memory_type
    # defaults to "fact" (unchanged remember_fact behaviour). valence (pos|neg|mixed)
    # and intensity (0.0–1.0) are the emotional-moment signal; the store has no
    # columns for them, so they ride the metadata dict (surfacing as
    # candidate_valence / candidate_intensity) for the memory-side importance boost
    # to read. Junk valence/intensity is dropped, never stored.
    if intent.name == "memory_store":
        slots = intent.slots or {}
        text = str(slots.get("text", "")).strip()
        if not text:
            return "There's nothing to remember — what would you like me to store?"
        # memory_type is model/caller-controlled, so validate it against an
        # allowlist rather than forwarding an arbitrary string to the store: a
        # direct/replayed intent-dispatch (internal-token only) must not be able to
        # inject unknown types that downstream recall isn't built for. Unknown or
        # blank → fall back to the safe default "fact".
        _ALLOWED_MEMORY_TYPES = {"fact", "emotional_moment"}
        memory_type = str(slots.get("memory_type", "fact")).strip().lower()
        if memory_type not in _ALLOWED_MEMORY_TYPES:
            memory_type = "fact"

        # Build optional emotional metadata, validating hard so junk never lands.
        emo_metadata: dict[str, Any] = {}
        valence = str(slots.get("valence", "")).strip().lower()
        if valence in {"pos", "neg", "mixed"}:
            emo_metadata["valence"] = valence
        raw_intensity = slots.get("intensity")
        if raw_intensity is not None:
            try:
                intensity = float(raw_intensity)
            except (TypeError, ValueError):
                intensity = None
            # Ignore non-finite / out-of-range junk; clamp is intentional not here —
            # a value outside 0..1 is malformed input, not a saturated signal.
            if intensity is not None and math.isfinite(intensity) and 0.0 <= intensity <= 1.0:
                emo_metadata["intensity"] = intensity

        # emotional_moment gets a slightly LOWER default confidence than a plain
        # fact (0.8 vs 0.85): it's a model-emitted judgement of significance on a
        # 4B brain, so we stay a touch more conservative than an explicit "remember
        # X" fact the user directly asked to keep. (Value per the emotional-thread
        # handoff spec.)
        confidence = 0.8 if memory_type == "emotional_moment" else 0.85
        tags = ["brain", "explicit"]
        if memory_type == "emotional_moment":
            tags.append("emotional")

        try:
            import hashlib
            from memory_service import get_memory_service
            norm = re.sub(r"\s+", " ", text.lower()).strip()
            # Namespace the idempotency key by memory_type so an emotional_moment
            # and a plain fact with identical text don't collapse into one row.
            key_prefix = "emo-" if memory_type == "emotional_moment" else "fact-"
            user_turn_id = key_prefix + hashlib.sha1(f"{user_id}|{memory_type}|{norm}".encode()).hexdigest()[:16]
            svc = get_memory_service()
            ref = await svc.ingest(
                text,
                user_id=user_id,
                source="brain_tool",
                user_turn_id=user_turn_id,
                memory_type=memory_type,
                confidence=confidence,
                tags=tags,
                metadata=(emo_metadata or None),
            )
        except Exception as exc:
            logger.warning("memory_store ingest failed: %s", exc)
            return "I couldn't reach the memory store right now, so I haven't saved that."
        if ref is None:
            # ingest silently drops on PII reject / dedup / opt-out. Don't claim
            # a durable write the store didn't actually make.
            return "I couldn't save that just now — it may already be stored or contain something I can't keep."
        return "Got it — I'll remember that."

    # ── A2A Federation Status ──────────────────────────────────────────────────
    if intent.name == "a2a_federation_status":
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=5) as _cli:
                reg_resp, squad_resp, runtime_resp = await asyncio.gather(
                    _cli.get("http://localhost:8000/api/agent/registry"),
                    _cli.get("http://localhost:8000/api/agent/squad"),
                    _cli.get("http://localhost:8000/api/agent/runtimes"),
                    return_exceptions=True,
                )
            reg = reg_resp.json() if not isinstance(reg_resp, Exception) else {}
            squad = squad_resp.json() if not isinstance(squad_resp, Exception) else {}
            runtimes = runtime_resp.json() if not isinstance(runtime_resp, Exception) else {}

            agents = reg.get("agents", {})
            rt = runtimes.get("runtimes", {})
            last_probed = runtimes.get("last_probed", "")

            lines = ["**Agent Federation**\n"]

            # Per-agent status line using runtimes endpoint for accuracy
            for name, info in agents.items():
                rt_entry = rt.get(name, {})
                online = rt_entry.get("online", info.get("status") == "online")
                status_label = "online" if online else "offline"
                desc = info.get("description", "")[:70]
                lines.append(f"- **{name.title()}** [{status_label}] — {desc}")

            # Squad topology
            squads = squad.get("squads", {})
            if squads:
                lines.append("\n**Squad:**")
                for sq_name, sq_info in squads.items():
                    members_detail = sq_info.get("members_detail", [])
                    member_str = ", ".join(
                        f"{m['name']} ({m.get('status','?')})" for m in members_detail
                    ) if members_detail else ", ".join(sq_info.get("members", []))
                    lines.append(f"- `{sq_name}`: {member_str}")

            if last_probed:
                lines.append(f"\n_Last health probe: {last_probed[:19].replace('T',' ')} UTC_")

            lines.append(
                '\nTo delegate: "delegate to hermes: [task]" or "delegate to openclaw: [task]"'
            )
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("a2a_federation_status: %s", exc)
            return ("I couldn't reach the agent registry right now. "
                    "Hermes runs on port 8642, OpenClaw on port 18789.")

    # ── Multica Board Status ───────────────────────────────────────────────────
    if intent.name == "agent_tasks_status":
        try:
            from db_pool import get_db_ctx as _get_pg_db

            async with _get_pg_db() as db:
                rows = await db.fetch(
                    "SELECT id, task, status, created_at "
                    "FROM background_tasks WHERE user_id=$1 "
                    "ORDER BY created_at DESC LIMIT 10",
                    user_id,
                )

            if not rows:
                return "No background agent tasks right now."

            lines = ["**Agent activity** — recent tasks:\n"]
            for row in rows[:10]:
                title = (row["task"] or "task").split("\n", 1)[0][:72]
                when = row["created_at"]
                if hasattr(when, "isoformat"):
                    when = when.isoformat()
                lines.append(f"- **{title}** — `{row['status']}` ({when})")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("agent_tasks_status: %s", exc)
            return "I couldn't load agent activity right now. Check the sidebar or try again."

    if intent.name == "board_status":
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=5) as _cli:
                resp = await _cli.get("http://localhost:8000/api/agent/board")
                board = resp.json()

            available = board.get("available", False)
            groups = board.get("groups") or {}

            if not available:
                reason = board.get("reason", "Multica not configured")
                return (
                    f"The Multica task board is not active ({reason}). "
                    "To enable it, set `ZOE_MULTICA=true` in `.env` and configure "
                    "`MULTICA_BASE_URL`, `MULTICA_API_TOKEN`, `MULTICA_WORKSPACE_ID`.\n\n"
                    "When enabled, long-running tasks like `build a widget` or `improve yourself` "
                    "will appear here for your approval."
                )

            ordered_statuses = ("blocked", "in_progress", "in_review", "todo", "backlog")
            open_items = [
                (status, item)
                for status in ordered_statuses
                for item in groups.get(status, [])
            ]
            if not open_items:
                return (
                    "**Multica Tickets** — nothing open right now.\n\n"
                    "New requests are captured in Multica and wait for approval before entering "
                    "the one-ticket engineering lane."
                )

            lines = [f"**Multica Tickets** — {len(open_items)} open item(s):\n"]
            for status, item in open_items[:10]:
                metadata = [
                    value
                    for value in (
                        f"phase: {item.get('phase')}" if item.get("phase") else "",
                        f"blocked: {item.get('blocker')}" if item.get("blocker") else "",
                        f"children: {item.get('child_count')}" if item.get("child_count") else "",
                        f"PR: {item.get('pr_url')}" if item.get("pr_url") else "",
                    )
                    if value
                ]
                suffix = f" | {' | '.join(metadata)}" if metadata else ""
                lines.append(
                    f"- `{item.get('identifier') or item.get('id')}` "
                    f"**{item.get('title', '?')}** — `{status}`{suffix}"
                )
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("board_status: %s", exc)
            return "I couldn't check the task board right now. Try again in a moment."

    # ── Engineering Workflow ──────────────────────────────────────────────────
    if intent.name == "engineering_task_create":
        task_text = intent.slots.get("task", "").strip()
        if not task_text:
            return "What should Hermes work on?"
        try:
            from multica_client import (  # type: ignore[import]
                get_engineering_multica_agent_id,
                get_multica_client,
            )
            from multica_ticket_contract import describe_ticket  # type: ignore[import]

            client = get_multica_client()
            if not client.is_configured():
                return "Multica isn't connected, so I can't track that engineering task on the board."
            description = describe_ticket(
                task_text,
                zoe_kind="operator_task",
                evidence_profile="code",
                engineering_mode="interactive",
                acceptance_criteria=["Deliver the requested behavior in a small, reviewable change."],
                evidence_expectations=["Focused tests or validators", "PR URL when code changes are made"],
                source="chat_engineering_task",
            )
            issue = await client.create_issue(
                title=task_text[:120],
                description=description,
                priority="medium",
                status="todo",
                assignee_id=get_engineering_multica_agent_id(),
                assignee_type="agent",
            )
            if issue.get("id"):
                await client.attach_label(str(issue["id"]), "operator-task")
            else:
                return (
                    "I tried to add that engineering task to Multica, but the API didn't return an issue. "
                    "Please try again."
                )
            ident = issue.get("identifier") or issue.get("id") or "(new)"
            return (
                "I've added that to Multica for Zoe's engineering driver.\n\n"
                f"- Issue: `{ident}`\n"
                "Zoe will dispatch one bounded Hermes phase at a time and keep the ticket status in sync."
            )
        except Exception as exc:
            logger.warning("engineering_task_create: %s", exc)
            return "I couldn't add that engineering task to the board right now."

    if intent.name == "engineering_task_status":
        try:
            from executor_registry import poll_ref  # type: ignore[import]
            from multica_client import get_engineering_multica_agent_id, get_multica_client  # type: ignore[import]

            client = get_multica_client()
            if not client.is_configured():
                return "Multica isn't connected, so I can't check engineering status."
            hermes_id = str(get_engineering_multica_agent_id())
            active = []
            for status in ("in_progress", "todo"):
                for issue in await client.list_issues(status=status) or []:
                    if str(issue.get("assignee_id") or "") != hermes_id:
                        continue
                    if (issue.get("title") or "").lower().startswith("autopilot:"):
                        continue
                    active.append((status, issue))
            if not active:
                return "No active Hermes engineering issues on the board right now."
            lines = ["**Active Hermes engineering issues:**"]
            for status, issue in active[:5]:
                ident = issue.get("identifier") or issue.get("id")
                chain = await poll_ref(f"multica:{issue.get('id')}", issue=issue)
                phase = chain.get("status") if chain.get("found") else status
                pr = f" | PR: {chain.get('pr_url')}" if chain.get("pr_url") else ""
                lines.append(f"- `{ident}` — {phase} — {(issue.get('title') or '')[:80]}{pr}")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("engineering_task_status: %s", exc)
            return "I couldn't check Hermes engineering status right now."

    if intent.name == "engineering_dispatch_pause":
        try:
            from multica_dispatch_control import pause_dispatch

            pause_dispatch(f"paused from chat by {user_id}")
            return "Engineering dispatch is paused. Active work can finish, but no new ticket will start."
        except Exception as exc:
            logger.warning("engineering_dispatch_pause: %s", exc)
            return "I couldn't pause engineering dispatch right now."

    if intent.name == "engineering_dispatch_resume":
        try:
            from multica_dispatch_control import resume_dispatch

            changed = resume_dispatch()
            return (
                "Engineering dispatch is running again; the next approved ticket can enter the single lane."
                if changed
                else "Engineering dispatch was already running."
            )
        except Exception as exc:
            logger.warning("engineering_dispatch_resume: %s", exc)
            return "I couldn't resume engineering dispatch right now."

    if intent.name == "engineering_ticket_move_todo":
        try:
            from multica_operator import move_to_todo

            issue = await move_to_todo(intent.slots.get("reference", ""), approve=True)
            if not issue.get("id"):
                return "I couldn't find or update that Multica ticket."
            return (
                f"Moved `{issue.get('identifier') or issue.get('id')}` to todo and approved it for "
                "the one-ticket engineering lane."
            )
        except Exception as exc:
            logger.warning("engineering_ticket_move_todo: %s", exc)
            return "I couldn't update that Multica ticket right now."

    if intent.name == "engineering_ticket_split":
        try:
            from multica_operator import split_ticket

            child = await split_ticket(
                intent.slots.get("reference", ""),
                child_title=intent.slots.get("title", ""),
            )
            if not child.get("id"):
                return "I couldn't split that Multica ticket."
            return (
                f"Created child ticket `{child.get('identifier') or child.get('id')}` and blocked the parent "
                "until its children are complete."
            )
        except Exception as exc:
            logger.warning("engineering_ticket_split: %s", exc)
            return "I couldn't split that Multica ticket right now."

    if intent.name == "engineering_ticket_list":
        status = intent.slots.get("status", "backlog")
        try:
            from multica_client import get_multica_client

            issues = await get_multica_client().list_issues(status=status)
            if not issues:
                return f"No Multica tickets are currently `{status}`."
            lines = [f"**Multica {status}:**"]
            for issue in issues[:10]:
                lines.append(
                    f"- `{issue.get('identifier') or issue.get('id')}` — "
                    f"{(issue.get('title') or 'Untitled')[:90]}"
                )
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("engineering_ticket_list: %s", exc)
            return f"I couldn't list Multica tickets in `{status}` right now."

    # ── Evolution Proposals Review ─────────────────────────────────────────────
    if intent.name == "evolution_proposals_review":
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=5) as _cli:
                resp = await _cli.get(
                    "http://localhost:8000/api/agent/evolution/proposals",
                    params={"status": "pending", "limit": "5"},
                )
                data = resp.json()

            proposals = data.get("proposals", [])
            count = data.get("count", 0)

            if not proposals:
                return (
                    "No pending evolution proposals right now. I'm running the NOTICE phase nightly — "
                    "if I spot patterns that could improve things, I'll propose them here for your review."
                )

            lines = [f"**{count} pending evolution proposal{'s' if count != 1 else ''}:**\n"]
            for p in proposals[:5]:
                pid = p.get("id", "")
                lines.append(
                    f"- **{p.get('title','?')[:60]}**  \n"
                    f"  _{p.get('description','')[:100]}_  \n"
                    f"  [[approve]](/api/agent/evolution/proposals/{pid}/action) "
                    f"[[defer]](/api/agent/evolution/proposals/{pid}/action) "
                    f"[[reject]](/api/agent/evolution/proposals/{pid}/action)"
                )
            if count > 5:
                lines.append(f"\n_…and {count - 5} more. Say \"show more proposals\" to see them._")

            return "\n".join(lines)
        except Exception as exc:
            logger.warning("evolution_proposals_review: %s", exc)
            return ("I couldn't load the evolution proposals right now. "
                    "They're stored at `/api/agent/evolution/proposals` if you want to check directly.")

    # ── Board Heal ─────────────────────────────────────────────────────────────
    if intent.name == "board_heal":
        try:
            from multica_client import get_multica_client as _get_mc  # type: ignore[import]
            mc = _get_mc()
            if not mc.is_configured():
                return (
                    "Multica isn't connected, so I can't check the board. "
                    "Configure Multica before using the Hermes board-review workflow."
                )
            todo_issues = await mc.list_issues(status="todo")
            in_progress = await mc.list_issues(status="in_progress")
            total_open = len(todo_issues) + len(in_progress)
            if total_open == 0:
                return "Board is clear — no open issues. The agents will keep it that way."
            try:
                from multica_autopilot_sync import _run_board_review  # type: ignore[import]
                await _run_board_review()
                triggered = True
            except Exception:
                triggered = False
            lines = [
                f"**Board has {total_open} open issue(s).**\n",
                f"- **{len(todo_issues)} todo** / **{len(in_progress)} in-progress**\n",
            ]
            if triggered:
                lines.append(
                    "I triggered the Hermes board-review dispatcher for Hermes-assigned issues. "
                    "Issues needing credentials or human judgement will be marked for review.\n"
                )
            else:
                lines.append(
                    "I could read the board, but couldn't trigger the Hermes dispatcher just now.\n"
                )
            if todo_issues:
                lines.append("\n**Oldest open items:**")
                for item in todo_issues[:4]:
                    lines.append(f"- {item.get('title','?')[:70]}")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("board_heal: %s", exc)
            return "I couldn't read the board right now. Try again in a moment."

    # ── User issue / complaint report ─────────────────────────────────────────
    if intent.name == "user_issue_report":
        import random as _random
        _acks = [
            "Got it, I've logged that for review.",
            "Noted. I've added that to the ticket backlog.",
            "Thanks for letting me know. I've captured it so it can be triaged properly.",
            "I've logged that as feedback rather than letting it disappear into chat.",
        ]
        message = intent.slots.get("message", "")
        try:
            from multica_client import get_multica_client  # type: ignore[import]
            from multica_ticket_contract import describe_ticket  # type: ignore[import]

            client = get_multica_client()
            if client.is_configured():
                description = describe_ticket(
                    f"User reported:\n\n{message}",
                    zoe_kind="bug",
                    evidence_profile="code",
                    engineering_mode="interactive",
                    acceptance_criteria=["Reproduce or explain the reported behavior.", "Propose a safe fix or mark blocked with reason."],
                    evidence_expectations=["Issue triage note", "Test or validator if code changes are made"],
                    source=f"chat_user_issue:{user_id}",
                )
                issue = await client.create_issue(
                    title=f"User feedback: {message[:95]}",
                    description=description,
                    priority="medium",
                    status="backlog",
                )
                if issue.get("id"):
                    await client.attach_label(str(issue["id"]), "user-feedback")
                    return _random.choice(_acks)
        except Exception as _exc:
            logger.warning("user_issue_report: Multica capture failed: %s", _exc)
        try:
            from evolution_notice import record_user_issue  # type: ignore[import]
            await record_user_issue(
                message=message,
                user_id=user_id,
            )
        except Exception as _exc:
            logger.warning("user_issue_report: record failed: %s", _exc)
        return _random.choice(_acks)

    if intent.name == "portrait_reveal":
        try:
            from user_portrait import load_portrait  # type: ignore[import]
            portrait = await load_portrait(user_id)
        except Exception as exc:
            logger.debug("portrait_reveal: load failed: %s", exc)
            portrait = ""
        if not portrait:
            return (
                "I don't have a deep portrait of you yet — that takes a few weeks of "
                "conversations for me to put together. The more we talk, the better I'll understand you. "
                "I do have some facts stored — try asking what I know about you."
            )
        return f"Here's how I understand you:\n\n{portrait}"

    if intent.name == "portrait_refresh":
        try:
            from user_portrait import run_portrait_synthesis  # type: ignore[import]
            result = await run_portrait_synthesis(user_id)
            status = result.get("status", "unknown")
            if status == "ok":
                chars = result.get("chars", 0)
                count = result.get("memory_count", 0)
                return (
                    f"I've updated my understanding of you — synthesised from {count} memories "
                    f"({chars} characters). It'll shape our conversations from now on."
                )
            elif status == "too_few_memories":
                return (
                    "I don't have enough memories stored yet to build a proper portrait. "
                    "Keep talking to me and I'll build a richer picture over time."
                )
            else:
                return "I tried to update my understanding but hit an issue. I'll try again tonight."
        except Exception as exc:
            logger.warning("portrait_refresh: failed: %s", exc)
            return "Something went wrong updating my understanding — I'll try again tonight."

    # === GREETING (ZOE-42, ZOE-15) ===
    if intent.name == "greeting":
        return await _execute_greeting(intent, user_id)

    # === SMART HOME LIGHTS (ZOE-9) ===
    if intent.name == "smart_home":
        return await _execute_smart_home_intent(intent, user_id)

    # === CALCULATE (ZOE-10) ===
    if intent.name == "calculate":
        return _execute_calculate(intent)

    # === TTS / SYSTEM VOLUME (ZOE-13) ===
    if intent.name == "set_volume":
        return await _execute_set_volume_intent(intent)

    # Timer and recipe intents need panel navigation — emit a nav action so the cooking
    # page opens and the timer/recipe widget is pre-filled.  The text response is spoken
    # by the voice daemon; the panel action is dispatched by _broadcast_intent_nav in chat.py.
    if intent.name == "timer_create":
        # On the touch panel, Skybridge owns timers end-to-end (real countdown
        # card + alarm) and is consulted before this fast-path. But other
        # callers -- notably POST /api/system/intent-dispatch (the zoe-core Pi
        # brain's `timers` ability) -- call execute_intent directly and never
        # go through Skybridge, so this branch must also register a real
        # timer, not just speak a confirmation (see skybridge_service's
        # in-memory _TimerStore, shared via create_timer_direct).
        mins = intent.slots.get("minutes", 5)
        label = intent.slots.get("label", "Timer")
        named = "" if str(label).strip().lower() in ("", "timer") else f" for {label}"
        try:
            from skybridge_service import create_timer_direct

            resolved = create_timer_direct(user_id, minutes=mins, label=str(label))
            spoken = resolved.get("spoken_summary")
            if spoken:
                return spoken
        except Exception as exc:
            logger.warning("timer_create: failed to register real timer (falling back to spoken-only): %s", exc)
        return f"Starting a {mins} minute timer{named}."

    if intent.name == "recipe_search":
        query = intent.slots.get("query", "")
        # Panel nav to cooking page handled by _broadcast_intent_nav in chat.py.
        return f"Looking up a recipe for {query}."

    # Weather should not depend on mcporter command availability.
    # Route directly to the weather backend helpers for deterministic voice UX.
    if intent.name == "weather":
        return await _execute_weather_direct(
            user_id=user_id,
            forecast=bool(intent.slots.get("forecast")),
            advice=str(intent.slots.get("advice") or ""),
            location=str(intent.slots.get("location") or ""),
        )

    if intent.name == "list_show":
        direct_result = await _execute_list_show_direct(intent, user_id)
        if direct_result:
            return direct_result

    if intent.name == "list_add":
        direct_result = await _execute_list_add_direct(intent, user_id)
        if direct_result:
            return direct_result

    if intent.name == "list_remove":
        direct_result = await _execute_list_remove_direct(intent, user_id)
        if direct_result:
            return direct_result

    if intent.name == "calendar_show":
        direct_result = await _execute_calendar_show_direct(intent, user_id)
        if direct_result:
            return direct_result

    if intent.name == "reminder_create":
        direct_result = await _execute_reminder_create_direct(intent, user_id)
        if direct_result:
            return direct_result

    if intent.name == "reminder_list":
        direct_result = await _execute_reminder_list_direct(intent, user_id)
        if direct_result:
            return direct_result

    if intent.name == "calendar_create":
        direct_result = await _execute_calendar_create_direct(intent, user_id)
        if direct_result:
            return direct_result

    if intent.name == "note_create":
        direct_result = await _execute_note_create_direct(intent, user_id)
        if direct_result:
            return direct_result

    if intent.name == "journal_create":
        direct_result = await _execute_journal_create_direct(intent, user_id)
        if direct_result:
            return direct_result

    if intent.name == "people_create":
        direct_result = await _execute_people_create_direct(intent, user_id)
        if direct_result:
            return direct_result

    # Read/search intents: "no matches" is a VALID answer, so return it even when
    # empty (a truthiness gate would wrongly fall through to the mcporter path,
    # which resolves to family-admin and misses the acting user's rows entirely).
    if intent.name == "note_search":
        direct_result = await _execute_note_search_direct(intent, user_id)
        if direct_result is not None:
            return direct_result

    if intent.name == "people_search":
        direct_result = await _execute_people_search_direct(intent, user_id)
        if direct_result is not None:
            return direct_result

    try:
        cmd = _build_command(intent, user_id)
        if not cmd:
            return None

        logger.info(f"Intent {intent.name}: {cmd}")
        raw = await _run_mcporter(cmd)
        if raw is None:
            return None

        return _format_response(intent, raw)

    except Exception as e:
        logger.error(f"Intent execution error: {e}")
        return None


async def _execute_daily_briefing(user_id: str) -> Optional[str]:
    """Composite intent: weather + calendar + reminders."""
    cached_response = _daily_briefing_cache_get(user_id)
    if cached_response:
        return cached_response

    task_keys = ["weather", "calendar", "reminders"]
    task_results = await asyncio.gather(
        _daily_briefing_weather(user_id),
        _daily_briefing_calendar(user_id),
        _daily_briefing_reminders(user_id),
        return_exceptions=True,
    )

    results = {
        key: raw
        for key, raw in zip(task_keys, task_results)
        if raw and not isinstance(raw, BaseException)
    }

    lines = ["Here's your day:"]

    weather = results.get("weather", {})
    if weather.get("temp") is not None:
        lines.append(f"\nWeather: {weather['temp']}°C in {weather.get('city', 'your area')}, {weather.get('description', '')}.")

    events = results.get("calendar", {}).get("events", [])
    if events:
        lines.append(f"\nCalendar ({len(events)} event{'s' if len(events) != 1 else ''}):")
        for e in events:
            t_str = e.get("start_time", "all day")
            lines.append(f"  - {e.get('title', '?')} at {t_str}")
    else:
        lines.append("\nNo events on the calendar today.")

    reminders = results.get("reminders", {}).get("reminders", [])
    if reminders:
        lines.append(f"\nReminders ({len(reminders)}):")
        for r in reminders:
            t_str = r.get("due_time", "")
            suffix = f" at {t_str}" if t_str else ""
            lines.append(f"  - {r.get('title', '?')}{suffix}")

    response = "\n".join(lines)
    if all(key in results for key in task_keys):
        _daily_briefing_cache_set(user_id, response)
    return response


async def _daily_briefing_weather(user_id: str) -> Optional[dict]:
    # _get_current short-circuits on a fresh keyed-cache hit for the user's
    # resolved coords, so the warm path (panel-polled home area) is instant —
    # no separate flat-slot peek needed.
    try:
        # get_db_ctx, not `async for db in get_db()`: returning from inside the
        # generator leaks the pooled connection (#953 / the 2026-07-03 pool drain).
        from database import get_db_ctx
        from routers.weather import _get_current, _resolve_location, _row_to_prefs

        async with get_db_ctx() as db:
            cursor = await db.execute(
                "SELECT * FROM weather_preferences WHERE user_id = ?",
                [user_id],
            )
            prefs = _row_to_prefs(await cursor.fetchone())
            lat, lon, city, country = _resolve_location(prefs)
            current = await _get_current(lat, lon, city, country)
            return {
                "temp": current.get("temp"),
                "city": current.get("city") or city or "your area",
                "description": current.get("description", ""),
            }
    except Exception as exc:
        logger.warning("daily briefing weather direct execution unavailable: %s", exc)
    return None


async def _daily_briefing_calendar(user_id: str) -> Optional[dict]:
    try:
        from database import get_db_ctx

        today = today_for_zoe_tz().isoformat()
        async with get_db_ctx() as db:
            cursor = await db.execute(
                "SELECT id, title, start_time, end_time, category, location FROM events"
                " WHERE start_date = ? AND (visibility = 'family' OR user_id = ?) AND deleted = 0"
                " ORDER BY start_time",
                (today, user_id),
            )
            rows = await cursor.fetchall()
        return {"date": today, "events": [dict(r) for r in rows]}
    except Exception as exc:
        logger.warning("daily briefing calendar direct execution unavailable: %s", exc)
    return None


async def _daily_briefing_reminders(user_id: str) -> Optional[dict]:
    try:
        from database import get_db_ctx

        today = today_for_zoe_tz().isoformat()
        async with get_db_ctx() as db:
            cursor = await db.execute(
                "SELECT id, title, due_date, due_time, priority, category FROM reminders"
                " WHERE due_date = ? AND (visibility = 'family' OR user_id = ?)"
                " AND is_active = 1 AND deleted = 0 ORDER BY due_time",
                (today, user_id),
            )
            rows = await cursor.fetchall()
        return {"reminders": [dict(r) for r in rows]}
    except Exception as exc:
        logger.warning("daily briefing reminder direct execution unavailable: %s", exc)
    return None


def _spoken_day(raw: str) -> str:
    """'2026-06-22' → 'Monday' (today→'today', tomorrow→'tomorrow'); raw on miss."""
    try:
        import datetime as _dt
        d = _dt.date.fromisoformat(str(raw)[:10])
        today = today_for_zoe_tz()
        if d == today:
            return "today"
        if d == today + _dt.timedelta(days=1):
            return "tomorrow"
        return d.strftime("%A")
    except Exception:
        return str(raw)


async def _execute_weather_direct(user_id: str, forecast: bool = False,
                                  advice: str = "", location: str = "") -> Optional[str]:
    """Direct weather path used by voice fast-intent execution.

    This bypasses mcporter so household voice weather works even if external
    command tooling is unavailable.

    `advice` ("rain"|"warmth") returns a DIRECT yes/no answer to questions like
    "do I need an umbrella" / "should I take a jacket" instead of a forecast dump.

    `location` is a free-text place the user named ("weather in Perth"). When set
    it is geocoded and used INSTEAD of the user's saved home area; if it can't be
    resolved we say so rather than silently answering for the wrong place.
    """
    try:
        # get_db_ctx, not `async for db in get_db()`: the many early `return`s
        # in this body each leaked the pooled connection (#953 / the 2026-07-03
        # pool drain).
        from database import get_db_ctx
        from routers.weather import (
            _row_to_prefs, _resolve_location, _geocode,
            _get_current, _get_forecast,
        )
        adhoc = bool(location.strip())
        async with get_db_ctx() as db:
            if adhoc:
                geo = await _geocode(location)
                if not geo:
                    return f"I couldn't find a place called {location.strip()} to check the weather for."
                lat, lon, city, country = geo
            else:
                cursor = await db.execute(
                    "SELECT * FROM weather_preferences WHERE user_id = ?",
                    [user_id],
                )
                prefs = _row_to_prefs(await cursor.fetchone())
                lat, lon, city, country = _resolve_location(prefs)
            # One call for both home and named ("weather in Perth") locations:
            # the cache is keyed by coords, so a fresh hit for THESE coords is
            # instant (panel-warmed home area) and an ad-hoc city can never
            # pollute — or be fed by — another location's reading. This replaces
            # the old adhoc cache=False bypass AND the cached-city mismatch guard
            # the flat single-slot cache used to require.
            current = await _get_current(lat, lon, city, country)
            city_name = current.get("city") or city or "your area"
            # Speak numbers naturally: "18.3" → "18 point 3" (bare decimals get
            # mangled to "18 3" by TTS), and avoid markdown/°C which also mangle.
            def _say_num(n) -> str:
                try:
                    f = float(n)
                    if f == int(f):
                        return str(int(f))  # 21.0 → "21", not "21 point 0"
                except (TypeError, ValueError):
                    pass
                s = str(n)
                return s.replace(".", " point ") if "." in s else s
            # Direct advice answer ("do I need a jacket / umbrella") — reason over
            # today's forecast + current conditions and lead with yes/no.
            if advice:
                cur_desc = str(current.get("description", "")).lower()
                cur_temp = current.get("temp")
                today_hi = None
                today_desc = cur_desc
                try:
                    f = await _get_forecast(lat, lon)
                    d0 = (f.get("daily") or [{}])[0]
                    today_hi = d0.get("high")
                    today_desc = (str(d0.get("description", "")) or cur_desc).lower()
                except Exception:
                    pass
                wet_words = ("rain", "shower", "drizzle", "thunder", "storm", "sleet", "snow")
                is_wet = any(w in cur_desc or w in today_desc for w in wet_words)
                cond_desc = (current.get("description") or "").strip()
                cond_part = f" — it's {cond_desc}" if cond_desc else ""
                if advice == "rain":
                    if is_wet:
                        return f"Yes, take an umbrella{cond_part} in {city_name}."
                    return f"No, you shouldn't need an umbrella{cond_part} in {city_name}."
                # warmth / jacket
                ref = None
                for v in (today_hi, cur_temp):
                    try:
                        ref = float(v); break
                    except Exception:
                        continue
                cold = (ref is not None and ref < 16)
                if cold or is_wet:
                    why = []
                    if ref is not None:
                        why.append(f"it's around {_say_num(round(ref))} degrees")
                    if is_wet:
                        why.append("and wet" if why else "it's wet")
                    reason = (", " + " ".join(why)) if why else ""
                    return f"Yes, I'd take a jacket{reason} in {city_name}."
                temp_part = f" — it's around {_say_num(round(ref))} degrees" if ref is not None else ""
                return f"No, you should be fine without a jacket{temp_part} in {city_name}."
            if forecast:
                f = await _get_forecast(lat, lon)
                daily = f.get("daily", [])[:5]
                if not daily:
                    return f"I couldn't get the forecast for {city_name} right now."
                lines = [f"Here's the forecast for {city_name}."]
                for item in daily:
                    day = _spoken_day(item.get("day", "?"))
                    hi = item.get("high", "?")
                    lo = item.get("low", "?")
                    desc = item.get("description", "unknown")
                    lines.append(f"{day}, a high of {_say_num(hi)} and a low of {_say_num(lo)} degrees, {desc}.")
                return " ".join(lines)
            temp = current.get("temp")
            desc = current.get("description", "")
            feels = current.get("feels_like")
            if temp is None:
                return f"I couldn't get the weather for {city_name} right now."
            # Speak naturally: "18.3°C (overcast)" → "18 point 3 degrees and overcast".
            desc_part = f" and {desc}" if desc else ""
            # Whole degrees for speech: "17 degrees", never "17 point 1 degrees"
            # — more natural spoken, and it makes the reply stitchable by the
            # voice segment cache (voice_stitch works on the integer vocab).
            raw_temp = temp
            try:
                temp = round(float(temp))
            except (TypeError, ValueError):
                pass
            msg = f"It's {_say_num(temp)} degrees{desc_part} in {city_name}"
            if feels is not None:
                try:
                    # Gate on the REAL delta, not the rounded speech value —
                    # rounding must not change whether feels-like is mentioned.
                    if abs(float(feels) - float(raw_temp)) > 2:
                        msg += f", and it feels like {_say_num(round(float(feels)))} degrees"
                except Exception:
                    pass
            return msg + "."
    except Exception as exc:
        logger.warning("direct weather intent failed: %s", exc)
        return None


async def _execute_music_setup(user_id: str) -> str:
    """
    Return a rich markdown response for the music_setup intent.
    Fetches live status from MA so the reply reflects what is actually configured.
    """
    ma_url = os.environ.get("MUSIC_ASSISTANT_URL", "http://localhost:8095")
    ma_token = os.environ.get("MUSIC_ASSISTANT_TOKEN", "")
    hdrs: dict[str, str] = {"Content-Type": "application/json"}
    if ma_token:
        hdrs["Authorization"] = f"Bearer {ma_token}"

    version_str = ""
    providers_str = "No streaming services connected yet."

    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(f"{ma_url}/info", headers=hdrs)
            if r.status_code == 200:
                info = r.json()
                version_str = f" v{info.get('version', '')}"
    except Exception:
        pass

    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=5.0) as c:
            r = await c.post(f"{ma_url}/api", json={"command": "music/providers"}, headers=hdrs)
            if r.status_code == 200:
                data = r.json()
                providers = data if isinstance(data, list) else (data.get("items") or [])
                if providers:
                    names = [p.get("name") or p.get("domain") or p.get("id", "?") for p in providers]
                    providers_str = "Connected services: **" + "**, **".join(names) + "**."
    except Exception:
        pass

    return (
        f"🎵 **Music Assistant{version_str} Setup**\n\n"
        f"{providers_str}\n\n"
        f"To connect music services (Spotify, YouTube Music, Apple Music, Deezer and more), "
        f"open the **[Music page](/music.html)** — it shows a setup wizard with Connect buttons "
        f"for each provider.\n\n"
        f"Or open Music Assistant directly: [{ma_url}]({ma_url})"
    )


async def _music_top_recent_genre(user_id: str) -> Optional[str]:
    try:
        from database import get_db_ctx

        async with get_db_ctx() as db:
            row = await db.fetchrow(
                """
                SELECT genre,
                       SUM(CASE event_type
                           WHEN 'complete' THEN 2 WHEN 'repeat' THEN 3
                           WHEN 'partial' THEN 1 WHEN 'skip' THEN -2
                           ELSE 0 END) as score
                FROM music_listening_events
                WHERE user_id=$1 AND genre != '' AND ts > $2
                GROUP BY genre ORDER BY score DESC LIMIT 1
                """,
                user_id,
                time.time() - 86400 * 30,
            )
        if row and row["score"] > 0:
            return row["genre"]
    except Exception:
        pass
    return None


async def _music_recent_repeat_count(user_id: str, track_title: str) -> int:
    if not track_title:
        return 0
    try:
        from database import get_db_ctx

        async with get_db_ctx() as db:
            count = await db.fetchval(
                "SELECT count(*) FROM music_listening_events "
                "WHERE user_id=$1 AND track_title=$2 AND event_type IN ('complete','partial') "
                "AND ts > $3",
                user_id,
                track_title,
                time.time() - 1800,
            )
        return int(count or 0)
    except Exception:
        return 0


async def _music_recent_skip_count(user_id: str) -> int:
    try:
        from database import get_db_ctx

        async with get_db_ctx() as db:
            count = await db.fetchval(
                "SELECT count(*) FROM music_listening_events "
                "WHERE user_id=$1 AND event_type='skip' AND ts > $2",
                user_id,
                time.time() - 900,
            )
        return int(count or 0)
    except Exception:
        return 0


async def _post_music_ha_control(client, ha_url: str, payload: dict) -> Optional[str]:
    try:
        response = await client.post(f"{ha_url}/devices/control", json=payload)
    except Exception as exc:
        logger.warning("music HA control failed: %s", exc)
        return "I couldn't reach the Home Assistant bridge to control the music."
    if response.status_code >= 400:
        logger.warning("music HA control returned HTTP %s", response.status_code)
        return f"I couldn't control the music because the Home Assistant bridge returned HTTP {response.status_code}."
    return None


async def _execute_music_intent(intent: Intent, user_id: str) -> Optional[str]:
    """Route music intents to HA media_player via zoe-data HA bridge."""
    try:
        import os as _os, httpx as _httpx
        ha_url = _os.environ.get("ZOE_HA_BRIDGE_URL", "http://127.0.0.1:8007")
        slots = intent.slots or {}

        if intent.name == "music_play":
            query = slots.get("query", "music")

            # Genre-weighted discovery for vague queries
            _query = query.strip()
            if not _query or _query.lower() in {"something", "music", "anything", "a song", "some music"}:
                recent_genre = await _music_top_recent_genre(user_id)
                if recent_genre:
                    _query = recent_genre  # use top genre as search query
                if not _query:
                    _query = "music"  # final fallback

            payload = {
                "entity_id": _os.environ.get("ZOE_DEFAULT_MEDIA_PLAYER", "media_player.all"),
                "action": "play_media",
                "data": {
                    "media_content_id": _query,
                    "media_content_type": "music",
                },
            }
            async with _httpx.AsyncClient(timeout=8.0) as c:
                failure = await _post_music_ha_control(c, ha_url, payload)
                if failure:
                    return failure

            # Fire-and-forget: 5-signal play event logger
            async def _log_play_event() -> None:
                import asyncio as _asyncio
                await _asyncio.sleep(2)
                # Snapshot what started playing
                start_meta: dict = {}
                _ma_url = _os.environ.get("MUSIC_ASSISTANT_URL", "http://localhost:8095")
                _ma_tok = _os.environ.get("MUSIC_ASSISTANT_TOKEN", "")
                _ma_hdrs = {"Authorization": f"Bearer {_ma_tok}"} if _ma_tok else {}

                def _ma_extract_media(p: dict) -> dict:
                    media = p.get("current_media") or {}
                    artists = media.get("artists") or []
                    return {
                        "track_title": media.get("title") or media.get("name", ""),
                        "artist": artists[0].get("name", "") if artists else (media.get("artist", "") or ""),
                        "album": (media.get("album") or {}).get("name", "") if isinstance(media.get("album"), dict) else "",
                        "source": p.get("provider", ""),
                        "duration_seconds": float(media.get("duration", 0) or 0),
                    }

                try:
                    async with _httpx.AsyncClient(timeout=3) as cl:
                        r = await cl.post(f"{_ma_url}/api",
                            json={"command": "players/all"}, headers=_ma_hdrs)
                        if r.status_code == 200 and isinstance(r.json(), list):
                            for p in r.json():
                                if p.get("state") == "playing":
                                    start_meta = _ma_extract_media(p)
                                    break
                except Exception:
                    pass

                # Wait to observe how much was actually played
                await _asyncio.sleep(28)

                try:
                    async with _httpx.AsyncClient(timeout=3) as cl:
                        r = await cl.post(f"{_ma_url}/api",
                            json={"command": "players/all"}, headers=_ma_hdrs)
                        if r.status_code != 200 or not isinstance(r.json(), list):
                            raise Exception("MA unavailable")
                        players = r.json()

                    elapsed = 0.0
                    current_title = ""
                    for p in players:
                        if p.get("state") in ("playing", "paused"):
                            elapsed = float(p.get("elapsed_time", 0) or 0)
                            item = p.get("current_item", {}) or {}
                            track = item.get("track", {}) or {}
                            current_title = track.get("name") or track.get("title", "")
                            break

                    duration = start_meta.get("duration_seconds", 0)

                    # Ghost flush: paused track — elapsed keeps growing past end
                    if duration > 0 and elapsed > duration * 1.1:
                        return

                    # Determine signal
                    if duration > 0:
                        pct = min(elapsed / duration, 1.0)
                    else:
                        pct = 0.5  # unknown duration → assume partial

                    if pct < 0.10:
                        return  # noise — don't log

                    if current_title and start_meta.get("track_title") and current_title != start_meta["track_title"]:
                        event_type = "skip" if pct < 0.30 else ("partial" if pct < 0.80 else "complete")
                    elif pct < 0.30:
                        event_type = "skip"
                    elif pct < 0.80:
                        event_type = "partial"
                    else:
                        event_type = "complete"

                    # Check for repeat: same track played again in last 30 min
                    _recent = await _music_recent_repeat_count(user_id, start_meta.get("track_title", ""))
                    if _recent > 0 and event_type == "complete":
                        event_type = "repeat"

                    from database import log_music_event as _log
                    await _log(
                        user_id=user_id,
                        event_type=event_type,
                        query=slots.get("query", ""),
                        percent_played=round(pct, 3),
                        duration_seconds=duration,
                        **{k: v for k, v in start_meta.items() if k not in ("duration_seconds",)},
                        session_id=slots.get("session_id", ""),
                    )
                except Exception:
                    # Fallback: log as plain play if observation failed
                    try:
                        from database import log_music_event as _log
                        await _log(user_id=user_id, event_type="complete",
                                   query=slots.get("query", ""),
                                   **{k: v for k, v in start_meta.items() if k != "duration_seconds"})
                    except Exception:
                        pass
            try:
                import asyncio as _asyncio
                _asyncio.ensure_future(_log_play_event())
            except Exception:
                pass

            return f"Playing {_query}."

        elif intent.name == "music_control":
            cmd = slots.get("command", "")
            service_map = {
                "pause": "media_pause",
                "stop": "media_stop",
                "resume": "media_play",
                "next": "media_next_track",
                "previous": "media_previous_track",
                "volume_up": "volume_up",
                "volume_down": "volume_down",
                "shuffle": "shuffle_set",
                "mute": "volume_mute",
                "unmute": "volume_mute",
            }
            if cmd == "now_playing":
                # Fetch state from HA bridge
                async with _httpx.AsyncClient(timeout=8.0) as c:
                    r = await c.get(f"{ha_url}/states")
                    data = r.json() if r.status_code == 200 else {}
                mp = data.get("media_player", {})
                for entity, state in mp.items():
                    title = state.get("attributes", {}).get("media_title")
                    artist = state.get("attributes", {}).get("media_artist")
                    if title:
                        try:
                            import asyncio as _asyncio
                            from database import log_music_event as _log
                            _asyncio.ensure_future(_log(user_id=user_id, event_type="now_playing",
                                                        track_title=title, artist=artist or ""))
                        except Exception:
                            pass
                        return f"Playing {title}{' by ' + artist if artist else ''}."
                return "Nothing is playing right now."

            svc = service_map.get(cmd)
            if svc:
                extra = {}
                if cmd == "shuffle": extra = {"shuffle": True}
                if cmd == "mute":    extra = {"is_volume_muted": True}
                if cmd == "unmute":  extra = {"is_volume_muted": False}
                payload = {
                    "entity_id": _os.environ.get("ZOE_DEFAULT_MEDIA_PLAYER", "media_player.all"),
                    "action": svc,
                    "data": extra,
                }
                async with _httpx.AsyncClient(timeout=8.0) as c:
                    failure = await _post_music_ha_control(c, ha_url, payload)
                    if failure:
                        return failure

                # Log skip/pause events for taste learning
                _evt_type = None
                if cmd in ("next", "skip"):
                    _evt_type = "skip"
                elif cmd in ("pause", "stop"):
                    _evt_type = "pause"
                if _evt_type:
                    try:
                        import asyncio as _asyncio
                        from database import log_music_event as _log
                        _asyncio.ensure_future(_log(user_id=user_id, event_type=_evt_type))
                    except Exception:
                        pass

                result = None
                # Skip-streak mood detection
                if cmd in ("next", "skip"):
                    _recent_skips = await _music_recent_skip_count(user_id)
                    if _recent_skips >= 4:
                        label_str = {"next": "Skipped to next", "skip": "Skipped to next"}.get(cmd, cmd.title())
                        return (
                            label_str + ". You've skipped quite a few — want me to try a different genre or mood?"
                        )

                label = {"pause": "Paused", "stop": "Stopped", "resume": "Resumed",
                         "next": "Skipped to next", "previous": "Back to previous",
                         "volume_up": "Volume up", "volume_down": "Volume down",
                         "shuffle": "Shuffle on", "mute": "Muted", "unmute": "Unmuted"}.get(cmd, cmd.title())
                return f"{label}."
            return None

        elif intent.name == "music_volume":
            level = int(slots.get("level", 50))
            vol = max(0, min(100, level)) / 100.0
            payload = {
                "entity_id": _os.environ.get("ZOE_DEFAULT_MEDIA_PLAYER", "media_player.all"),
                "action": "volume_set",
                "data": {"volume_level": vol},
            }
            async with _httpx.AsyncClient(timeout=8.0) as c:
                failure = await _post_music_ha_control(c, ha_url, payload)
                if failure:
                    return failure
            try:
                import asyncio as _asyncio
                from database import log_music_event as _log
                _asyncio.ensure_future(_log(user_id=user_id, event_type="volume_change",
                                            volume_level=level))
            except Exception:
                pass
            return f"Volume set to {level}%."

    except Exception as exc:
        logger.warning("music intent failed: %s", exc)
    return None


def _build_command(intent: Intent, user_id: str) -> Optional[str]:
    s = intent.slots
    base = f"{MCPORTER} call"

    if intent.name == "list_add":
        item = s.get("item", "")
        lt = s.get("list_type", "shopping")
        return f'{base} zoe-data.list_add_item list_type={lt} text="{item}" user_id={user_id}'

    if intent.name == "list_show":
        lt = s.get("list_type", "shopping")
        return f"{base} zoe-data.list_get_items list_type={lt}"

    if intent.name == "list_remove":
        item = s.get("item", "")
        lt = s.get("list_type", "shopping")
        return f'{base} zoe-data.list_remove_item list_type={lt} item_text="{item}"'

    if intent.name == "calendar_create":
        title = s.get("title", "Event")
        date = s.get("date", "")
        time_ = s.get("time", "")
        cmd = f'{base} zoe-data.calendar_create_event title="{title}"'
        if date:
            parsed = _parse_date(date)
            if parsed:
                cmd += f" start_date={parsed}"
        if time_:
            parsed = _parse_time(time_)
            if parsed:
                cmd += f" start_time={parsed}"
        category = s.get("category", "general")
        if category:
            cmd += f" category={category}"
        return cmd

    if intent.name == "calendar_show":
        from datetime import timedelta
        qualifier = s.get("qualifier", "").strip().lower()
        today_d = today_for_zoe_tz()

        if qualifier in ("today", "today's"):
            return f"{base} zoe-data.calendar_today"
        elif qualifier == "tomorrow":
            tmrw = (today_d + timedelta(days=1)).isoformat()
            return f"{base} zoe-data.calendar_list_events start_date={tmrw} end_date={tmrw}"
        elif qualifier in ("this week", "this week's"):
            start = (today_d - timedelta(days=today_d.weekday())).isoformat()
            end = (today_d + timedelta(days=6 - today_d.weekday())).isoformat()
            return f"{base} zoe-data.calendar_list_events start_date={start} end_date={end}"
        elif qualifier in ("this month", "this month's"):
            import calendar as cal_mod
            _, last = cal_mod.monthrange(today_d.year, today_d.month)
            start = today_d.replace(day=1).isoformat()
            end = today_d.replace(day=last).isoformat()
            return f"{base} zoe-data.calendar_list_events start_date={start} end_date={end}"
        else:
            start = today_d.isoformat()
            end = (today_d + timedelta(days=7)).isoformat()
            return f"{base} zoe-data.calendar_list_events start_date={start} end_date={end}"

    if intent.name == "reminder_create":
        title = s.get("title", "")
        date_str = s.get("date", "")
        time_str = s.get("time", "")
        cmd = f'{base} zoe-data.reminder_create title="{title}"'
        if date_str:
            parsed = _parse_date(date_str)
            if parsed:
                cmd += f" due_date={parsed}"
        if time_str:
            cmd += f" due_time={time_str}"
        return cmd

    if intent.name == "reminder_list":
        return f"{base} zoe-data.reminder_list"

    if intent.name == "people_create":
        name = s.get("name", "")
        rel = s.get("relationship", "friend")
        context = s.get("context", "personal")
        circle = s.get("circle", "circle")
        return (
            f'{base} zoe-data.people_create name="{name}" relationship={rel} '
            f'context={context} circle={circle} user_id={user_id}'
        )

    if intent.name == "people_search":
        query = s.get("query", "")
        return f'{base} zoe-data.people_search query="{query}"'

    if intent.name == "note_create":
        title = s.get("title", "Note")
        content = s.get("content", "")
        return f'{base} zoe-data.note_create title="{title}" content="{content}" user_id={user_id}'

    if intent.name == "note_search":
        query = s.get("query", "")
        return f'{base} zoe-data.note_search query="{query}"'

    if intent.name == "weather":
        if s.get("forecast"):
            return f"{base} zoe-data.weather_forecast"
        return f"{base} zoe-data.weather_current"

    if intent.name == "journal_create":
        content = s.get("content", "")
        if content:
            return f'{base} zoe-data.journal_create_entry content="{content}"'
        return f"{base} zoe-data.journal_get_prompts"

    if intent.name == "journal_streak":
        return f"{base} zoe-data.journal_get_streak"

    if intent.name == "journal_prompt":
        return f"{base} zoe-data.journal_get_prompts"

    if intent.name == "transaction_create":
        desc = s.get("description", "purchase")
        amount = s.get("amount", 0)
        return f'{base} zoe-data.transaction_create description="{desc}" amount={amount} type=expense'

    if intent.name == "transaction_summary":
        period = s.get("period", "week")
        return f"{base} zoe-data.transaction_summary period={period}"

    return None


def _format_response(intent: Intent, raw_output: str) -> str:
    s = intent.slots
    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError:
        return raw_output

    if isinstance(data, dict) and "error" in data:
        return f"Sorry, that didn't work: {data['error']}"

    if intent.name == "list_add":
        item = s.get("item", "something")
        lt = s.get("list_type", "shopping")
        friendly = "shopping list" if lt == "shopping" else f"{lt.replace('_', ' ')} list"
        return f"Added {item} to your {friendly}."

    if intent.name == "list_show":
        lt = s.get("list_type", "shopping")
        friendly = "shopping list" if lt == "shopping" else f"{lt.replace('_', ' ')} list"
        lists = data.get("lists", [])
        active_lists = []
        for list_data in lists:
            items = list_data.get("items") or []
            active = [i["text"] for i in items if i.get("text") and not i.get("completed")]
            if active:
                active_lists.append((list_data.get("name") or friendly, active))
        if not active_lists:
            return f"Your {friendly} is empty."
        if len(active_lists) == 1:
            lines = [f"Your {friendly}:"]
            for item in active_lists[0][1]:
                lines.append(f"  - {item}")
            return "\n".join(lines)
        lines = [f"Your {friendly}s:"]
        for list_name, active in active_lists:
            lines.append(f"{list_name}:")
            for item in active:
                lines.append(f"  - {item}")
        return "\n".join(lines)


    if intent.name == "list_remove":
        item = s.get("item", "item")
        return f"Removed {item} from your list."

    if intent.name == "calendar_create":
        title = s.get("title", "event")
        category = s.get("category", "general")
        if category and category != "general":
            return f"Created calendar event: {title} ({category})."
        return f"Created calendar event: {title}."

    if intent.name == "calendar_show":
        events = data.get("events", [])
        if not events:
            return "No upcoming events this week."
        lines = ["Upcoming events:"]
        for e in events:
            t_str = e.get("start_time", "TBD")
            if isinstance(t_str, str) and len(t_str) > 5:
                t_str = t_str[:5]
            lines.append(f"  - {e.get('title', '?')} on {e.get('start_date', '?')} at {t_str}")
        return "\n".join(lines)

    if intent.name == "reminder_create":
        title = s.get("title", "reminder")
        date_str = s.get("date", "")
        time_str = s.get("time", "")
        suffix = ""
        if date_str:
            suffix += f" for {date_str}"
        if time_str:
            suffix += f" at {time_str}"
        return f"Reminder set: {title}{suffix}."

    if intent.name == "reminder_list":
        reminders = data.get("reminders", [])
        if not reminders:
            return "No reminders set."
        lines = ["Your reminders:"]
        for r in reminders:
            lines.append(f"  - {r.get('title', r.get('text', '?'))} (due: {r.get('due_date') or 'TBD'})")
        return "\n".join(lines)

    if intent.name == "people_create":
        name = s.get("name", "contact")
        context = s.get("context", "personal")
        circle = s.get("circle", "circle")
        tier_symbol = {"inner": "●", "circle": "○", "public": "□"}.get(circle, "○")
        return f"Added {name} to your {context} contacts {tier_symbol}."

    if intent.name == "people_search":
        people = data.get("people", [])
        if not people:
            return f"No contacts found for \"{s.get('query', '')}\"."
        lines = ["Found:"]
        for p in people:
            lines.append(f"  - {p.get('name', '?')} ({p.get('relationship', '?')})")
        return "\n".join(lines)

    if intent.name == "note_create":
        return f"Note saved: {s.get('title', 'note')}."

    if intent.name == "note_search":
        notes = data.get("notes", [])
        if not notes:
            return "No matching notes found."
        lines = ["Notes found:"]
        for n in notes:
            lines.append(f"  - {n.get('title', '?')}")
        return "\n".join(lines)

    if intent.name == "weather":
        if "forecast" in data:
            items = data.get("forecast", [])
            if not items:
                return "Couldn't get the forecast."
            city = data.get("city", "your area")
            lines = [f"Forecast for {city}:"]
            for item in items:
                lines.append(f"  - {item.get('datetime', '?')}: {item.get('temp', '?')}°C, {item.get('description', '?')}")
            return "\n".join(lines)
        temp = data.get("temp")
        desc = data.get("description", "")
        city = data.get("city", "your area")
        feels = data.get("feels_like")
        if temp is not None:
            msg = f"It's {temp}°C in {city} ({desc})"
            if feels and abs(feels - temp) > 2:
                msg += f", feels like {feels}°C"
            return msg + "."
        return f"Weather: {data}"

    if intent.name == "journal_create":
        if "prompts" in data:
            prompts = data["prompts"]
            lines = ["Here are some journal prompts to get you started:"]
            for p in prompts:
                lines.append(f"  - {p}")
            lines.append("\nTell me what you'd like to write about and I'll create the entry.")
            return "\n".join(lines)
        return f"Journal entry created: {data.get('title', 'entry')}."

    if intent.name == "journal_streak":
        current = data.get("current_streak", 0)
        longest = data.get("longest_streak", 0)
        total = data.get("total_entries", 0)
        msg = f"Journal stats: {total} total entries."
        if current > 0:
            msg += f" You're on a {current}-day streak!"
        if longest > current:
            msg += f" Your longest streak was {longest} days."
        return msg

    if intent.name == "journal_prompt":
        prompts = data.get("prompts", [])
        if not prompts:
            return "What's on your mind? Write anything you'd like."
        lines = ["Here are some journaling prompts:"]
        for p in prompts:
            lines.append(f"  - {p}")
        return "\n".join(lines)

    if intent.name == "transaction_create":
        desc = data.get("description", "purchase")
        amount = data.get("amount", 0)
        return f"Recorded: ${amount} for {desc}."

    if intent.name == "transaction_summary":
        period = data.get("period", "week")
        expense = data.get("total_expense", 0)
        income = data.get("total_income", 0)
        net = data.get("net", 0)
        by_cat = data.get("by_category", {})
        lines = [f"Spending summary ({period}):"]
        lines.append(f"  Total spent: ${expense:.2f}")
        if income > 0:
            lines.append(f"  Total income: ${income:.2f}")
            lines.append(f"  Net: ${net:.2f}")
        if by_cat:
            lines.append("  By category:")
            for cat, total in sorted(by_cat.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"    - {cat}: ${total:.2f}")
        return "\n".join(lines)

    return raw_output


async def _execute_greeting(intent: Intent, user_id: str) -> str:
    """Warm, time-aware greeting — instant fast-path, no LLM needed."""
    from datetime import datetime
    tod = intent.slots.get("time_of_day")
    if tod is None:
        hour = datetime.now().hour
        if hour < 12:
            tod = "morning"
        elif hour < 17:
            tod = "afternoon"
        elif hour < 21:
            tod = "evening"
        else:
            tod = "night"
    # Try to personalise with the user's preferred name from portrait
    name_suffix = ""
    try:
        from user_portrait import load_portrait_field  # type: ignore[import]
        name = await load_portrait_field(user_id, "preferred_name")
        if name:
            name_suffix = f", {name}"
    except Exception:
        pass
    greetings = {
        "morning":   f"Good morning{name_suffix}! What can I help you with today?",
        "afternoon": f"Good afternoon{name_suffix}! How can I help?",
        "evening":   f"Good evening{name_suffix}! What can I do for you?",
        "night":     f"Good evening{name_suffix}! Still up — what do you need?",
    }
    return greetings.get(tod, f"Hi{name_suffix}! How can I help?")


async def _execute_smart_home_intent(intent: Intent, user_id: str) -> Optional[str]:
    """Route light-control intents to the HA bridge (ZOE-9)."""
    try:
        import httpx as _httpx
        ha_url = os.environ.get("ZOE_HA_BRIDGE_URL", "http://127.0.0.1:8007")
        slots = intent.slots or {}
        action = slots.get("action", "turn_off")
        room = slots.get("room")

        # Build the HA entity_id from room name, falling back to the group alias
        if room:
            entity_id = f"light.{room.lower()}"
        else:
            entity_id = os.environ.get("ZOE_DEFAULT_LIGHT_ENTITY", "light.all")

        service_map = {
            "turn_on":  "turn_on",
            "turn_off": "turn_off",
            "dim":      "turn_on",
            "brighten": "turn_on",
        }
        service = service_map.get(action, "turn_on" if action == "turn_on" else "turn_off")
        data: dict = {"entity_id": entity_id}
        if action == "dim":
            data["brightness_pct"] = 25
        elif action == "brighten":
            data["brightness_pct"] = 100

        data.pop("entity_id", None)
        payload = {"entity_id": entity_id, "action": service, "data": data}
        async with _httpx.AsyncClient(timeout=8.0) as c:
            resp = await c.post(f"{ha_url}/devices/control", json=payload)
            resp.raise_for_status()

        action_labels = {
            "turn_on":  "on",
            "turn_off": "off",
            "dim":      "dimmed",
            "brighten": "brightened",
        }
        label = action_labels.get(action, action)
        if room:
            room_friendly = room.replace("_", " ").title()
            return f"{room_friendly} lights {label}."
        return f"Lights {label}."
    except Exception as exc:
        logger.warning("smart_home intent failed: %s", exc)
        # An HTTPStatusError means the bridge WAS reachable but rejected the
        # request (bad entity/room, revoked HA token, …) — the "make sure it's
        # connected" setup line would be misleading there. Duck-type via
        # exc.response (httpx is imported inside the try, so its exception
        # types can't be referenced safely here).
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status is not None:
            return (
                f"Home Assistant rejected that request (HTTP {status}). "
                "Check the device or room name, and that your Home Assistant token is still valid."
            )
        return (
            "I couldn't reach the smart home bridge. "
            "Make sure Home Assistant is connected — say \"set up home assistant\" to get started."
        )


def _execute_calculate(intent: Intent) -> str:
    """Safe arithmetic evaluator for bounded numeric expressions (ZOE-10)."""
    expr_raw = intent.slots.get("expression", "").strip()
    display = intent.slots.get("display", expr_raw)
    if not expr_raw:
        return "What would you like me to calculate?"

    import ast as _ast
    import operator as _operator

    safe = expr_raw.strip()
    if (
        not safe
        or len(safe) > 80
        or not re.fullmatch(r"[\d\s\+\-\*\/\.\(\)\%]+", safe)
        or "**" in safe
    ):
        return f"I can only handle numeric expressions. Try something like \"what is 2 + 2\"."

    binary_ops = {
        _ast.Add: _operator.add,
        _ast.Sub: _operator.sub,
        _ast.Mult: _operator.mul,
        _ast.Div: _operator.truediv,
        _ast.Mod: _operator.mod,
    }
    unary_ops = {
        _ast.UAdd: _operator.pos,
        _ast.USub: _operator.neg,
    }
    max_nodes = 64
    max_abs_value = 1_000_000_000_000
    seen_nodes = 0

    def _check_bound(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("non-numeric result")
        if abs(value) > max_abs_value:
            raise ValueError("result too large")
        return value

    def _eval_node(node):
        nonlocal seen_nodes
        seen_nodes += 1
        if seen_nodes > max_nodes:
            raise ValueError("expression too complex")
        if isinstance(node, _ast.Expression):
            return _eval_node(node.body)
        if isinstance(node, _ast.Constant):
            return _check_bound(node.value)
        if isinstance(node, _ast.BinOp) and type(node.op) in binary_ops:
            left = _eval_node(node.left)
            right = _eval_node(node.right)
            return _check_bound(binary_ops[type(node.op)](left, right))
        if isinstance(node, _ast.UnaryOp) and type(node.op) in unary_ops:
            return _check_bound(unary_ops[type(node.op)](_eval_node(node.operand)))
        raise ValueError("unsupported expression")

    try:
        parsed = _ast.parse(safe, mode="eval")
        result = _eval_node(parsed)
        if isinstance(result, float):
            # Show integer when result is whole
            result = int(result) if result == int(result) else round(result, 6)
        return f"{display} = {result}"
    except ZeroDivisionError:
        return "That expression would divide by zero."
    except Exception:
        return f"I couldn't calculate \"{display}\". Try a simpler expression like \"what is 15 * 4\"."


async def _execute_set_volume_intent(intent: Intent) -> str:
    """Adjust system (ALSA) audio volume for Zoe's TTS output (ZOE-13).

    Calls amixer directly to avoid the auth-gated HTTP endpoint.
    """
    slots = intent.slots or {}
    direction = slots.get("direction", "up")
    level: Optional[int] = slots.get("level")

    try:
        if direction == "set" and level is not None:
            # Set to an absolute percentage
            pct = max(0, min(100, level))
            proc = await asyncio.create_subprocess_exec(
                "amixer", "sset", "Master", f"{pct}%",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5.0)
            return f"Volume set to {pct}%."
        else:
            # Relative adjustment: read current level, step by 15
            proc = await asyncio.create_subprocess_exec(
                "amixer", "sget", "Master",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            m = re.search(r"\[(\d+)%\]", stdout.decode(errors="replace"))
            current = int(m.group(1)) if m else 50
            step = 15
            new_vol = min(100, current + step) if direction == "up" else max(0, current - step)
            proc2 = await asyncio.create_subprocess_exec(
                "amixer", "sset", "Master", f"{new_vol}%",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc2.communicate(), timeout=5.0)
            label = "louder" if direction == "up" else "quieter"
            return f"Got it, speaking a bit {label} now (volume at {new_vol}%)."
    except Exception as exc:
        logger.warning("set_volume intent failed: %s", exc)
        direction_word = "up" if direction != "down" else "down"
        if direction_word == "up":
            return "I'll try to speak louder. You can also adjust volume in Settings."
        return "I'll try to speak more softly. You can also adjust volume in Settings."


def _parse_date(raw: str) -> Optional[str]:
    from datetime import date, timedelta
    raw = raw.strip().lower()

    if raw == "today":
        return date.today().isoformat()
    if raw == "tomorrow":
        return (date.today() + timedelta(days=1)).isoformat()

    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, name in enumerate(day_names):
        if raw.startswith(name):
            today = date.today()
            days_ahead = (i - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return (today + timedelta(days=days_ahead)).isoformat()

    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
        "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
        "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    m = re.match(r"(\w+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?", raw)
    if m:
        month_name, day, year = m.group(1), int(m.group(2)), m.group(3)
        month = months.get(month_name)
        if month:
            yr = int(year) if year else date.today().year
            return f"{yr:04d}-{month:02d}-{day:02d}"

    m = re.match(r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(\w+)(?:\s+(\d{4}))?", raw)
    if m:
        day, month_name, year = int(m.group(1)), m.group(2), m.group(3)
        month = months.get(month_name)
        if month:
            yr = int(year) if year else date.today().year
            return f"{yr:04d}-{month:02d}-{day:02d}"

    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return raw

    return None


def _parse_time(raw: str) -> Optional[str]:
    raw = raw.strip().lower()
    raw = raw.replace(".", ":")
    m = re.match(r"^(\d{1,4})(?::(\d{2}))?\s*(am|pm)?(?:\b|$)", raw)
    if m:
        hour_str = m.group(1)
        minute_group = m.group(2)
        ampm = m.group(3)
        if minute_group is not None:
            hour = int(hour_str)
            minute = int(minute_group)
        elif len(hour_str) in (3, 4):
            hour = int(hour_str[:-2])
            minute = int(hour_str[-2:])
        else:
            hour = int(hour_str)
            minute = 0
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        return f"{hour:02d}:{minute:02d}"
    return None
