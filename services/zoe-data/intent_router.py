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
    """Lowercase, Unicode-normalize, collapse whitespace (UI often adds hidden chars)."""
    s = unicodedata.normalize("NFKC", (raw or "").strip()).lower()
    return re.sub(r"\s+", " ", s).strip()


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
        if intent.name == "build_widget":
            return f"{_BUILD_WIDGET_OPENCLAW_MSG}\n\nOriginal request: {user_text}"
        if intent.name == "build_page":
            return f"{_BUILD_PAGE_OPENCLAW_MSG}\n\nOriginal request: {user_text}"
        if intent.name == "extend_capability":
            return f"{_EXTEND_CAPABILITY_OPENCLAW_MSG}\n\nOriginal request: {user_text}"
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


def detect_intent(text: str) -> Optional[Intent]:
    t = _normalize_chat_intent_text(text)

    # Full Home Assistant / automation setup → OpenClaw (execute_intent returns None; chat expands message)
    if _is_ha_full_setup_message(t):
        return Intent("ha_full_setup", {})

    # "forget that" — retract the most recent memory write for the caller.
    # Matched very early so it never collides with other verbs.
    if _FORGET_LAST_RE.match(t):
        return Intent("memory_forget_last", {})

    # Zoe self-extension — always routes to OpenClaw (admin-gated in the skill).
    # Checked BEFORE list/reminder/etc so "add X widget" doesn't become list_add.
    if _BUILD_WIDGET_RE.match(t) or _WANT_WIDGET_RE.match(t):
        return Intent("build_widget", {"raw": text})
    if _BUILD_PAGE_RE.match(t) or _WANT_PAGE_RE.match(t):
        return Intent("build_page", {"raw": text})
    if _EXTEND_CAPABILITY_RE.match(t):
        return Intent("extend_capability", {"raw": text})

    # === CLOCK / CALENDAR QUERIES — checked before domain patterns (no slots needed) ===

    # Modelled on HA's HassGetCurrentTime and HassGetCurrentDate — two separate intents
    if _TIME_QUERY_RE.match(t):
        return Intent("time_query", {})

    if _DATE_QUERY_RE.match(t):
        return Intent("date_query", {})

    # === DOMAIN-SPECIFIC PATTERNS FIRST (to avoid list collisions) ===

    # --- CALENDAR CREATE ---
    # "add X to my calendar on DATE at TIME"
    m = re.match(
        r"^(?:add|put) (.+?) (?:to|on|in) (?:the |my )?(?:calendar|schedule)"
        r"(?:(?: on| for) (.+?))?(?:(?: at) (.+))?$", t
    )
    if m:
        title = m.group(1).strip()
        date_str = (m.group(2) or "").strip()
        time_str = (m.group(3) or "").strip()
        category = _infer_event_category(title)
        return Intent("calendar_create", {"title": title, "date": date_str, "time": time_str, "category": category})

    # "please add an event in my calendar beach at 230 today"
    m = re.match(
        r"^(?:please\s+)?(?:add|create|schedule|put|make)\s+(?:an?\s+)?(?:event|appointment|meeting)"
        r"\s+(?:to|in|on)\s+(?:the\s+|my\s+)?(?:calendar|schedule)\s+(.+?)(?:\s+at\s+(.+))?$",
        t,
    )
    if m:
        title = m.group(1).strip()
        tail = (m.group(2) or "").strip()
        date_str = ""
        time_str = tail
        tm = re.match(r"^([0-9]{1,4}(?::[0-9]{2})?\s*(?:am|pm)?)(?:\s+(.+))?$", tail)
        if tm:
            time_str = (tm.group(1) or "").strip()
            date_str = (tm.group(2) or "").strip()
        category = _infer_event_category(title)
        return Intent("calendar_create", {"title": title, "date": date_str, "time": time_str, "category": category})

    # "create/schedule a [keyword] called/titled/named X on DATE at TIME" (most specific first)
    m = re.match(
        r"^(?:create|add|schedule|set up|make) (?:a |an )?(?:event|appointment|meeting)"
        r" (?:called|titled|named) (.+?)(?:\s+(?:on|for)\s+(.+?))?(?:\s+at\s+(.+))?$", t
    )
    if m:
        title = m.group(1).strip()
        date_str = (m.group(2) or "").strip()
        time_str = (m.group(3) or "").strip()
        category = _infer_event_category(title)
        return Intent("calendar_create", {"title": title, "date": date_str, "time": time_str, "category": category})

    # "add an appointment at 2.30 today for the dentist"
    m = re.match(
        r"^(?:add|create|schedule|set up|make)\s+(?:an?\s+)?(?:appointment|event|meeting)"
        r"(?:\s+at\s+(.+?))?(?:\s+for\s+(.+))?$",
        t,
    )
    if m:
        at_clause = (m.group(1) or "").strip()
        title = (m.group(2) or "appointment").strip()
        date_str = ""
        time_str = ""
        if at_clause:
            tm = re.match(r"^([0-9]{1,4}(?::[0-9]{2})?(?:\.[0-9]{2})?\s*(?:am|pm)?)(?:\s+(.+))?$", at_clause)
            if tm:
                time_str = (tm.group(1) or "").strip()
                date_str = (tm.group(2) or "").strip()
            else:
                date_str = at_clause
        category = _infer_event_category(title)
        return Intent("calendar_create", {"title": title, "date": date_str, "time": time_str, "category": category})

    # Contains appointment/event/meeting keyword -- extract title, date, time
    if any(kw in t for kw in ("appointment", "event", "meeting")):
        m = re.match(
            r"^(?:create|add|schedule|set up|make|book) (?:a |an |me (?:a |an )?)?(.+?)(?:\s+(?:on|for)\s+(.+?))?(?:\s+at\s+(.+))?$", t
        )
        if m:
            title = m.group(1).strip()
            date_str = (m.group(2) or "").strip()
            time_str = (m.group(3) or "").strip()
            if title:
                category = _infer_event_category(title)
                return Intent("calendar_create", {"title": title, "date": date_str, "time": time_str, "category": category})

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

    # --- REMINDERS CREATE ---
    m = re.match(
        r"^(?:remind me to|set a reminder (?:to|for)|reminder to|remember to) (.+?)(?:(?: on| by| for| at) (.+))?$", t
    )
    if m:
        task = m.group(1).strip()
        date_or_time = (m.group(2) or "").strip()
        slots = {"title": task}
        if date_or_time:
            parsed_time = _parse_time(date_or_time)
            if parsed_time:
                slots["time"] = parsed_time
            else:
                slots["date"] = date_or_time
        return Intent("reminder_create", slots)

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
        r"^(?:make|create|write|save) (?:a )?note(?: (?:titled|called|about))? (.+)$", t
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

    return None


def _normalize_list(raw: str) -> str:
    mapping = {
        "shopping": "shopping", "grocery": "shopping", "groceries": "shopping",
        "todo": "personal", "to do": "personal", "to-do": "personal",
        "personal": "personal", "work": "work", "tasks": "tasks",
        "bucket": "bucket",
    }
    return mapping.get(raw, "shopping")


def _infer_list(item: str) -> str:
    lower = item.lower()
    if any(kw in lower for kw in SHOPPING_KEYWORDS):
        return "shopping"
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
