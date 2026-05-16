"""
Intent-first router: pattern-matches common family data requests and calls
mcporter-safe directly, bypassing the LLM for <1 second responses.
Falls through to OpenClaw for everything else.

Inspired by the original Zoe HassIL intent system (Tier 0/1 classification).

Pattern priority: domain-specific (calendar, reminder, contact, note) checked
BEFORE generic list patterns to avoid collisions like "what's on my calendar"
matching as list_show.
"""
import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


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
    r"(?:setup|set up)\s+hass)\s*"
    r"(?:please\s*)?[!?.…]*\s*$",
    re.IGNORECASE,
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
    r"|tell\s+me\s+the\s+time|current\s+time|time\s+now|time\s+please|what\s+time\s+is\s+it)\??$",
    re.IGNORECASE,
)

_DATE_QUERY_RE = re.compile(
    r"^(what'?s?\s+today'?s?\s+date|what\s+(is\s+)?the\s+date(\s+today)?"
    r"|what\s+day\s+(is\s+it|of\s+the\s+week(\s+is\s+it)?)|today'?s?\s+date"
    r"|what\s+year\s+is\s+it(\s+now)?|what\s+month\s+is\s+it(\s+now)?"
    r"|day\s+of\s+the\s+week|what'?s?\s+the\s+date(\s+today)?)\??$",
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


def detect_intent(text: str, log_miss: bool = True) -> Optional[Intent]:
    t = _normalize_chat_intent_text(text)

    # Full Home Assistant / automation setup → OpenClaw (execute_intent returns None; chat expands message)
    if _is_ha_full_setup_message(t):
        return Intent("ha_full_setup", {})

    # "forget that" — retract the most recent memory write for the caller.
    # Matched very early so it never collides with other verbs.
    if _FORGET_LAST_RE.match(t):
        return Intent("memory_forget_last", {})

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

    # === CLOCK / CALENDAR QUERIES — checked before domain patterns (no slots needed) ===

    # Modelled on HA's HassGetCurrentTime and HassGetCurrentDate — two separate intents
    if _TIME_QUERY_RE.match(t):
        return Intent("time_query", {})

    if _DATE_QUERY_RE.match(t):
        return Intent("date_query", {})

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
    # Pattern 1: "remind me to X", "set a reminder for X", "reminder to X", "remember to X"
    if re.match(r"^(?:remind me to|set a reminder (?:to|for)|reminder to|remember to) .+", t):
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

    # --- CONTACTS CREATE ---
    m = re.match(
        r"^(?:add|create|save) (?:a )?(?:contact|person|entry) (?:for |named )?(.+)$", t
    )
    if m:
        name = m.group(1).strip()
        rel = "friend"
        for tag in ["friend", "colleague", "family", "neighbor", "neighbour"]:
            if tag in t:
                rel = tag.replace("neighbour", "neighbor")
                name = re.sub(
                    rf",?\s*(?:she'?s|he'?s|they'?re|as)?\s*(?:a |my )?{tag}\b",
                    "", name, flags=re.I,
                ).strip()
                break
        return Intent("people_create", {"name": name, "relationship": rel})

    # --- CONTACTS SEARCH ---
    m = re.match(r"^(?:find|search|look up) (?:a )?(?:contact|person) (?:for |named )?(.+)$", t)
    if m:
        return Intent("people_search", {"query": m.group(1).strip()})

    m = re.match(r"^who is (.+)$", t)
    if m:
        return Intent("people_search", {"query": m.group(1).strip()})


    # --- NOTES CREATE ---
    m = re.match(
        r"^(?:make|create|write|save|add|take) (?:a )?note(?:s)?(?: (?:titled|called|about|on))? (.+)$", t
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
    m = re.match(
        r"^(?:write|create|make|start|new) (?:a |an )?(?:journal|diary) (?:entry)?(.*)$", t
    )
    if m:
        content = m.group(1).strip() if m.group(1) else ""
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
    m = re.match(
        r"^i (?:spent|paid) \$?([\d.]+)(?: ?(?:dollars?|bucks?))?(?: (?:at|on|for) (.+))?$", t
    )
    if m:
        amount = float(m.group(1))
        desc = (m.group(2) or "").strip() or "purchase"
        return Intent("transaction_create", {"amount": amount, "description": desc})

    m = re.match(
        r"^(?:bought|purchased) (.+?) (?:for )\$?([\d.]+)$", t
    )
    if m:
        desc = m.group(1).strip()
        amount = float(m.group(2))
        return Intent("transaction_create", {"amount": amount, "description": desc})

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
        r"^give me (?:a )?rundown$",
        r"^what (?:do i|have i got) (?:have )?(?:on )?today$",
    ]:
        if re.match(pattern, t):
            return Intent("daily_briefing", {})

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

    # --- LIST ADD (implicit, no list name) ---
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
        r"^what'?s on (?:the |my )?(.+?) ?list$",
        r"^whats on (?:the |my )?(.+?) ?list$",
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

    if log_miss:
        logger.info("intent_miss: %s", text)
        # Write to intent-misses file for weekly self-review (PII stripped)
        import re as _re, json as _json, pathlib as _pathlib
        _MISS_PATH = _pathlib.Path.home() / "training" / "data" / "intent-misses.jsonl"
        _MISS_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Strip names, numbers, emails, URLs before writing
            _clean = _re.sub(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b', '[NAME]', text)
            _clean = _re.sub(r'\b\d[\d\s\-]{6,}\b', '[NUMBER]', _clean)
            _clean = _re.sub(r'[\w.+-]+@[\w-]+\.\w+', '[EMAIL]', _clean)
            _clean = _re.sub(r'https?://\S+', '[URL]', _clean)
            with open(_MISS_PATH, "a") as _f:
                _f.write(_json.dumps({"text": _clean, "ts": __import__("time").time()}) + "\n")
        except Exception:
            pass  # Never let logging break intent routing
    return None


async def detect_and_extract_intent(
    text: str, user_id: str = "family-admin"
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
    should fall through to Pi Agent).
    """
    intent = detect_intent(text)
    if intent is None:
        return None
    if intent.slots and "raw" in intent.slots:
        try:
            from nlu_extractor import extract_slots_for_intent  # lazy — avoids circular at load
            structured = await extract_slots_for_intent(intent.name, intent.slots["raw"])
            if structured:
                intent.slots = structured
                return intent
        except Exception as _exc:
            logger.warning(
                "detect_and_extract_intent: nlu_extractor failed intent=%s err=%s",
                intent.name,
                _exc,
            )
        # Extraction failed — let caller fall through to Pi Agent
        return None
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


def _sanitize_list_item(raw: str) -> str:
    item = str(raw or "").strip()
    item = re.sub(r"\s+", " ", item)
    item = re.sub(r"^(please|pls)\s+", "", item, flags=re.IGNORECASE)
    item = re.sub(r"^(add|put|get|buy)\s+", "", item, flags=re.IGNORECASE)
    item = re.sub(r"\s+(to|on)\s+(?:the\s+|my\s+)?(?:shopping|grocery|groceries)\s+list$", "", item, flags=re.IGNORECASE)
    item = re.sub(r"[.,;:!?]+$", "", item)
    return item.strip()


async def _run_mcporter(cmd: str) -> Optional[str]:
    """Run a single mcporter-safe command, return raw stdout or None on failure."""
    env = os.environ.copy()
    env["PATH"] = f"{NODE_BIN}:{env.get('PATH', '')}"
    try:
        proc = await asyncio.create_subprocess_exec(
            *shlex.split(cmd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            logger.warning(f"mcporter-safe failed: {stderr.decode()}")
            return None
        return stdout.decode().strip()
    except asyncio.TimeoutError:
        logger.warning("mcporter-safe timed out")
        return None
    except Exception as e:
        logger.error(f"mcporter error: {e}")
        return None


async def execute_intent(intent: Intent, user_id: str = "family-admin") -> Optional[str]:
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
        try:
            from proactive.composer import compose_message
            result = await compose_message(
                "Good evening — provide a brief, warm check-in for the end of day. "
                "Mention any outstanding reminders or tomorrow's early events if known, "
                "otherwise just a warm sign-off. Keep it under 3 sentences.",
                user_id,
            )
            return result
        except Exception as _e:
            logger.warning("good_evening composer failed: %s", _e)
            return "Good evening! Hope your day went well. Let me know if there's anything you need."

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

    if intent.name == "connect_chatgpt":
        # Handled directly by chat.py via _chatgpt_connect_flow() — which runs
        # the full device-code OAuth flow inline in the SSE stream.  Return None
        # here so execute_intent does not produce a static text reply.
        return None

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

    # Timer and recipe intents need panel navigation — emit a nav action so the cooking
    # page opens and the timer/recipe widget is pre-filled.  The text response is spoken
    # by the voice daemon; the panel action is dispatched by _broadcast_intent_nav in chat.py.
    if intent.name == "timer_create":
        mins = intent.slots.get("minutes", 5)
        label = intent.slots.get("label", "Timer")
        mins_str = f"{mins} minute" if int(mins) == 1 else f"{mins} minute"
        return f"Starting a {mins_str} timer for {label}."

    if intent.name == "recipe_search":
        query = intent.slots.get("query", "")
        # Panel nav to cooking page handled by _broadcast_intent_nav in chat.py.
        return f"Looking up a recipe for {query}."

    # Weather should not depend on mcporter command availability.
    # Route directly to the weather backend helpers for deterministic voice UX.
    if intent.name == "weather":
        return await _execute_weather_direct(user_id=user_id, forecast=bool(intent.slots.get("forecast")))

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
    base = f"{MCPORTER} call"
    cmds = {
        "weather": f"{base} zoe-data.weather_current",
        "calendar": f"{base} zoe-data.calendar_today",
        "reminders": f"{base} zoe-data.reminder_list today_only=true",
    }

    results = {}
    tasks = []
    for key, cmd in cmds.items():
        tasks.append((key, _run_mcporter(cmd)))

    for key, coro in tasks:
        raw = await coro
        if raw:
            try:
                results[key] = json.loads(raw)
            except json.JSONDecodeError:
                pass

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

    return "\n".join(lines)


async def _execute_weather_direct(user_id: str, forecast: bool = False) -> Optional[str]:
    """Direct weather path used by voice fast-intent execution.

    This bypasses mcporter so household voice weather works even if external
    command tooling is unavailable.
    """
    try:
        from database import get_db
        from routers.weather import _row_to_prefs, _resolve_location, _get_current, _get_forecast
        async for db in get_db():
            cursor = await db.execute(
                "SELECT * FROM weather_preferences WHERE user_id = ?",
                [user_id],
            )
            prefs = _row_to_prefs(await cursor.fetchone())
            lat, lon, city, country = _resolve_location(prefs)
            current = await _get_current(lat, lon, city, country)
            city_name = current.get("city") or city or "your area"
            if forecast:
                f = await _get_forecast(lat, lon)
                daily = f.get("daily", [])[:5]
                if not daily:
                    return f"I couldn't get the forecast for {city_name} right now."
                lines = [f"Forecast for {city_name}:"]
                for item in daily:
                    day = item.get("day", "?")
                    hi = item.get("high", "?")
                    lo = item.get("low", "?")
                    desc = item.get("description", "unknown")
                    lines.append(f"  - {day}: {hi}°C/{lo}°C, {desc}")
                return "\n".join(lines)
            temp = current.get("temp")
            desc = current.get("description", "")
            feels = current.get("feels_like")
            if temp is None:
                return f"I couldn't get the weather for {city_name} right now."
            msg = f"It's {temp}°C in {city_name} ({desc})"
            if feels is not None:
                try:
                    if abs(float(feels) - float(temp)) > 2:
                        msg += f", feels like {feels}°C"
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
                try:
                    import psycopg2 as _pg3, time as _t3
                    _conn3 = _pg3.connect(os.environ.get("POSTGRES_URL", "postgresql://zoe:zoe-db-2026-prod@localhost:5432/zoe"))
                    _cur3 = _conn3.cursor()
                    # Get top-scored genre from last 30 days
                    _cur3.execute("""
                        SELECT genre,
                               SUM(CASE event_type
                                   WHEN 'complete' THEN 2 WHEN 'repeat' THEN 3
                                   WHEN 'partial' THEN 1 WHEN 'skip' THEN -2
                                   ELSE 0 END) as score
                        FROM music_listening_events
                        WHERE user_id=%s AND genre != '' AND ts > %s
                        GROUP BY genre ORDER BY score DESC LIMIT 1
                    """, (user_id, _t3.time() - 86400 * 30))
                    _rows = _cur3.fetchone()
                    _conn3.close()
                    if _rows and _rows[1] > 0:
                        _query = _rows[0]  # use top genre as search query
                except Exception:
                    pass
                if not _query:
                    _query = "music"  # final fallback

            payload = {
                "type": "service",
                "domain": "media_player",
                "service": "play_media",
                "data": {
                    "entity_id": _os.environ.get("ZOE_DEFAULT_MEDIA_PLAYER", "media_player.all"),
                    "media_content_id": _query,
                    "media_content_type": "music",
                },
            }
            async with _httpx.AsyncClient(timeout=8.0) as c:
                await c.post(f"{ha_url}/execute", json=payload)

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
                    try:
                        import psycopg2 as _pg2, time as _time
                        _conn2 = _pg2.connect(os.environ.get("POSTGRES_URL", "postgresql://zoe:zoe-db-2026-prod@localhost:5432/zoe"))
                        _cur2 = _conn2.cursor()
                        _cur2.execute(
                            "SELECT count(*) FROM music_listening_events "
                            "WHERE user_id=%s AND track_title=%s AND event_type IN ('complete','partial') "
                            "AND ts > %s",
                            (user_id, start_meta.get("track_title", ""), _time.time() - 1800)
                        )
                        _recent = _cur2.fetchone()[0]
                        _conn2.close()
                        if _recent > 0 and event_type == "complete":
                            event_type = "repeat"
                    except Exception:
                        pass

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
                    "type": "service",
                    "domain": "media_player",
                    "service": svc,
                    "data": {
                        "entity_id": _os.environ.get("ZOE_DEFAULT_MEDIA_PLAYER", "media_player.all"),
                        **extra,
                    },
                }
                async with _httpx.AsyncClient(timeout=8.0) as c:
                    await c.post(f"{ha_url}/execute", json=payload)

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
                    try:
                        import psycopg2 as _pg, time as _t
                        _conn = _pg.connect(os.environ.get("POSTGRES_URL", "postgresql://zoe:zoe-db-2026-prod@localhost:5432/zoe"))
                        _cur = _conn.cursor()
                        _cur.execute(
                            "SELECT count(*) FROM music_listening_events "
                            "WHERE user_id=%s AND event_type='skip' AND ts > %s",
                            (user_id, _t.time() - 900)  # last 15 min
                        )
                        _recent_skips = _cur.fetchone()[0]
                        _conn.close()
                        if _recent_skips >= 4:
                            label_str = {"next": "Skipped to next", "skip": "Skipped to next"}.get(cmd, cmd.title())
                            return (
                                label_str + ". You've skipped quite a few — want me to try a different genre or mood?"
                            )
                    except Exception:
                        pass

                label = {"pause": "Paused", "stop": "Stopped", "resume": "Resumed",
                         "next": "Skipped to next", "previous": "Back to previous",
                         "volume_up": "Volume up", "volume_down": "Volume down",
                         "shuffle": "Shuffle on", "mute": "Muted", "unmute": "Unmuted"}.get(cmd, cmd.title())
                return f"{label}."

        elif intent.name == "music_volume":
            level = int(slots.get("level", 50))
            vol = max(0, min(100, level)) / 100.0
            payload = {
                "type": "service",
                "domain": "media_player",
                "service": "volume_set",
                "data": {
                    "entity_id": _os.environ.get("ZOE_DEFAULT_MEDIA_PLAYER", "media_player.all"),
                    "volume_level": vol,
                },
            }
            async with _httpx.AsyncClient(timeout=8.0) as c:
                await c.post(f"{ha_url}/execute", json=payload)
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
        from datetime import date, timedelta
        qualifier = s.get("qualifier", "").strip().lower()
        today_d = date.today()

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
        return f'{base} zoe-data.people_create name="{name}" relationship={rel} user_id={user_id}'

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
        if not lists or not lists[0].get("items"):
            return f"Your {friendly} is empty."
        items = lists[0]["items"]
        active = [i["text"] for i in items if not i.get("completed")]
        if not active:
            return f"Your {friendly} is empty."
        lines = [f"Your {friendly}:"]
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
            lines.append(f"  - {r.get('title', r.get('text', '?'))} (due: {r.get('due_date', 'TBD')})")
        return "\n".join(lines)

    if intent.name == "people_create":
        name = s.get("name", "contact")
        return f"Added {name} to your contacts."

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
