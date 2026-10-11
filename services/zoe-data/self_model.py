"""self_model - Zoe knows what she is, what she can do today, what she cannot, and says so honestly.

Why. Asked "what can you do?", "can you order groceries?" or "are you always listening?", a 4B brain answers from its training
data: it invents capabilities (it will "order" the groceries), forgets the ones it has, and describes an assistant that listens to
nothing and remembers nothing. The truth lives in code: the tool registry the sidecar really registers, the router's intents, the
flags that are off today, the services that are up this minute. This module GENERATES a self-model from those registries (never
hand-written prose) and answers the self-questions from it, in the asker's language.

What it builds (``build_block`` / ``answer``): surfaces; tool groups with one canonical ask each; what is NOT switched on today
(flags read from the flag inventory + the process environment AT STARTUP); what she cannot do (a closed list); memory rules, one
line each, included only when the code that backs them is on; the hardware and models; and, at request time, counts of enrolled
accounts / voices / faces (counts only, never names), the clock, whether the night window is open, and whether the music player and
the lights bridge answer (health probes, 0.4 s, cached 20 s). Static sections come first and in a fixed order, volatile ones last,
so the block is byte-stable and prefix-cache friendly; a size cap drops the lowest-priority sections.

Where the words live (docs/architecture/samantha-brain-blueprint-2026-10-09.md section 2.9, language independence): NO English in
this file. Ids, flag names and proper nouns are in ``lexicons_data/self_model.json``; every sentence, cue and label is in
``lexicons_data/self_model_<lang>.json``. A language with no enabled file contributes nothing (the turn goes on to the brain). A
cue may only ROUTE a turn to an answer built from the generated model; it never grants a capability, and "can you X?" is answered
"no" only for an X on the closed unsupported list - anything else goes on to the router and the brain untouched.

Where it runs: ``fast_tiers.resolve`` (the unchanged name, wrapped at the bottom of that file) calls ``tier`` after the provenance
tier and before the router and the brain. Flag ``ZOE_SELF_MODEL``: ``off`` | ``shadow`` (DEFAULT: the turn is untouched, one
``SELF_MODEL`` log line says what would have answered) | ``enforce`` (the deterministic answer). VOICE-PATH: a turn that is not a
self-question costs one length check and a handful of anchored regexes; the answer is built from cached facts plus at most two
cached probes and four small COUNT reads (never in shadow).

Not here, deliberately: injecting the block into the brain's packet. ``zoe_flue_client`` is owned by the latency work; ``build_block``
is the function that seam would call (docs/knowledge/self-model.md). Until then every self-question this module recognises is
answered here, deterministically.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import re
import string
import time
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

ENV = "ZOE_SELF_MODEL"
DIR = Path(__file__).with_name("lexicons_data")
REPO = Path(__file__).resolve().parents[2]
TOOL_GROUPS_TS = REPO / "labs" / "flue-zoe-brain-2x" / "src" / "tools" / "tool-groups.ts"
ZOE_TOOLS_TS = REPO / "labs" / "flue-zoe-brain-2x" / "src" / "tools" / "zoe-tools.ts"
INVENTORY = REPO / "docs" / "knowledge" / "flag-inventory.json"
SNAPSHOT = DIR / "self_model_registry.json"
LANGS = ("en", "es")
#: the order cues are tried in: the narrow shapes before the broad ones, "can you X" last
KINDS = ("used_to_answer", "listening", "how_remember", "know_me", "see_me", "running_on", "cannot", "capabilities", "tools",
         "made_by", "who_are_you", "can_you")
VOICE_CHANNELS = frozenset({"voice", "livekit"})
TIER = "self_model"
#: how many not-switched-on capabilities a spoken/typed answer names (the block names them all)
DARK_IN_ANSWER = 4
_GUEST_IDS = frozenset({"", "guest", "anonymous", "voice-guest", "voice-daemon"})


def mode() -> str:
    """``off`` | ``shadow`` (default) | ``enforce``. Per-call env read; anything unrecognised is the safe default, shadow."""
    from typed_env import env_str

    v = env_str("ZOE_SELF_MODEL", "shadow").lower()
    if v in ("0", "false", "no", "off"):
        return "off"
    if v in ("1", "true", "yes", "on", "enforce"):
        return "enforce"
    return "shadow"


# ── the registries (read, never copied) ───────────────────────────────────────────────────────────────────────────────

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def parse_registry(groups_ts: str, tools_ts: str) -> dict:
    """The sidecar's tool registry, parsed from the two TypeScript files that DEFINE it: groups in declaration order, the
    always-on core, every registered tool name, the ungrouped ones (always disclosed) and each group's one-line purpose."""
    block = re.search(r"export const TOOL_GROUPS = \{(.*?)\}\s*as const", groups_ts, re.S)
    groups: list = []
    for name, body in re.findall(r"(\w+):\s*\[([^\]]*)\]", block.group(1) if block else ""):
        groups.append([name, re.findall(r"'([A-Za-z0-9_]+)'", body)])
    activator = (re.search(r"ACTIVATOR_TOOL_NAME\s*=\s*'([A-Za-z0-9_]+)'", groups_ts) or [None, ""])[1]
    core_m = re.search(r"CORE_TOOL_NAMES[^=]*=\s*\[(.*?)\]", groups_ts, re.S)
    core = [tok[0] or activator for tok in re.findall(r"'([A-Za-z0-9_]+)'|(ACTIVATOR_TOOL_NAME)", core_m.group(1) if core_m else "")]
    purposes_m = re.search(r"GROUP_PURPOSES[^=]*=\s*\{(.*?)\n\};", groups_ts, re.S)
    purposes = dict(re.findall(r"(\w+):\s*'([^']*)'", purposes_m.group(1) if purposes_m else ""))
    tools = [t.strip("'") for t in re.findall(r"defineTool\(\{\s*name:\s*('[A-Za-z0-9_]+'|ACTIVATOR_TOOL_NAME)", tools_ts)]
    tools = [activator if t == "ACTIVATOR_TOOL_NAME" else t for t in tools]
    grouped = {t for _, ts in groups for t in ts}
    return {"groups": groups, "core": core, "activator": activator, "tools": tools, "purposes": purposes,
            "ungrouped": [t for t in tools if t not in grouped and t not in core]}


def _registry_from_sources() -> dict:
    g, t = _read(TOOL_GROUPS_TS), _read(ZOE_TOOLS_TS)
    return parse_registry(g, t) if g and t else {}


@lru_cache(maxsize=1)
def registry() -> dict:
    """The live source when the sidecar sources are on disk, else the committed snapshot (``python3 self_model.py --snapshot``
    regenerates it; ``tests/test_self_model.py`` fails when it drifts). ``{}`` when neither is readable."""
    reg = _registry_from_sources()
    if reg.get("groups"):
        return reg
    try:
        return json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def router_domains() -> dict:
    """The two-stage router's domain -> tools map (the intents a quick request is routed to), or {}."""
    try:
        import router_two_stage

        return {d: list(ts) for d, ts in router_two_stage.DOMAIN_TOOLS.items() if ts}
    except Exception:  # noqa: BLE001 - the router is optional here
        return {}


# ── per-language data ─────────────────────────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def neutral() -> dict:
    return json.loads((DIR / "self_model.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def lex(lang: str) -> dict:
    """``self_model_<lang>.json`` when it exists AND says ``enabled`` ({} otherwise: that language contributes nothing)."""
    code = (lang or "").strip().lower().split("-")[0]
    path = DIR / f"self_model_{code}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if code and path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return data if data.get("enabled") else {}


def languages() -> tuple:
    return tuple(code for code in LANGS if lex(code))


def norm(text: str) -> str:
    """NFKC, casefold, accents stripped, question/exclamation marks and commas dropped, whitespace folded."""
    s = unicodedata.normalize("NFKC", text or "").casefold().replace("’", "'").replace("‘", "'")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[¿¡?!.,;:]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def strip_frame(s: str, lx: dict) -> str:
    """Drop leading fillers ("hey zoe") and trailing ones ("please") - repeatedly, bounded."""
    for _ in range(4):
        before = s
        for p in lx.get("lead") or ():
            s = re.sub(rf"^(?:{p})", "", s).strip()
        for p in lx.get("trail") or ():
            s = re.sub(rf"(?:{p})$", "", s).strip()
        if s == before:
            break
    return s


@lru_cache(maxsize=None)
def _cues(lang: str, kind: str) -> tuple:
    return tuple(re.compile(c) for c in (lex(lang).get("cues") or {}).get(kind) or ())


@dataclass(frozen=True)
class Question:
    kind: str
    lang: str
    x: str = ""             # can_you: the thing asked about (normalised)


def classify(text: str) -> Optional[Question]:
    """The self-question ``text`` is, or None. Pure; no IO. Whole-utterance match over the normalised, frame-stripped text."""
    t = (text or "").strip()
    if not t or t.count("\n") > 1:
        return None
    for lang in languages():
        lx = lex(lang)
        if len(t) > int(lx.get("max_chars") or 140):
            continue
        s = strip_frame(norm(t), lx)
        if not s:
            continue
        for kind in KINDS:
            for rx in _cues(lang, kind):
                m = rx.fullmatch(s)
                if m:
                    return Question(kind, lang, (m.groupdict().get("x") or "").strip())
    return None


def _word_re(fragments, *, start: bool = False, fillers=()) -> "re.Pattern[str]":
    body = "|".join(fragments)
    lead = rf"^(?:(?:{'|'.join(fillers)})\s+)?" if (start and fillers) else (r"^" if start else r"(?<!\w)")
    return re.compile(rf"{lead}(?:{body})(?!\w)")


@lru_cache(maxsize=None)
def _unsupported_res(lang: str) -> tuple:
    out = []
    declared = {u["id"] for u in neutral().get("unsupported", ())}
    for uid, spec in (lex(lang).get("unsupported") or {}).items():
        if uid in declared:
            out.append((uid, _word_re(spec["cues"], start=spec.get("where") == "start", fillers=lex(lang).get("fillers") or ())))
    return tuple(out)


@lru_cache(maxsize=None)
def _action_res(lang: str) -> tuple:
    """((group, regex), ...): the leading ACTIONS a tool group does ("add", "remind me", "set a timer"), from the language file."""
    out = []
    for group, spec in (lex(lang).get("groups") or {}).items():
        for frag in spec.get("actions") or ():
            out.append((group, re.compile(rf"^(?:(?:{'|'.join(lex(lang).get('fillers') or ('',))})\s+)?(?:{frag})(?!\w)")))
    return tuple(out)


@lru_cache(maxsize=None)
def _verb_res(lang: str, uid: str) -> tuple:
    verbs = (lex(lang).get("unsupported", {}).get(uid) or {}).get("verbs") or ()
    fill = "|".join(lex(lang).get("fillers") or ("",))
    return tuple(re.compile(rf"^(?:(?:{fill})\s+)?(?:{v})(?!\w)\s*") for v in verbs)


@lru_cache(maxsize=None)
def _object_end_re(lang: str):
    ends = lex(lang).get("object_end") or ()
    return re.compile(rf"(?<!\w)(?:{'|'.join(ends)})(?!\w)") if ends else None


def _object_span(x: str, lang: str, uid: str) -> Optional[str]:
    """For a closed-list item that only counts as the OBJECT of a particular action (a fan you are asked to switch off, a flight
    you are asked to find): the span after that action's verb, up to the first preposition (verb included). None when the request's action is
    not one of the item's verbs - so a fan or a flight that is only part of an ADD / REMIND / SET-A-TIMER request is not it."""
    for rx in _verb_res(lang, uid):
        m = rx.match(x)
        if m:
            rest = x[m.end():]
            cut = _object_end_re(lang)
            end = cut.search(rest) if cut else None
            return x[:m.end()] + (rest[:end.start()] if end else rest)     # the verb counts too: "lock" is both
    return None


def classify_can_you(x: str, lang: str) -> tuple:
    """``("unsupported", id)`` for a thing on the closed list, ``("supported", group)`` for one a tool group does,
    ``("unknown", "")`` otherwise. Only the first is ever answered here. The REQUESTED ACTION decides, never a word inside its
    object: a request that opens with an action a tool group does ("add", "remind me", "set a timer") is supported whatever it
    mentions ("add a fan to my shopping list", "remind me to book a flight"); a closed-list item with ``verbs`` is refused only
    as the object of one of those verbs ("turn off the fan", "find me a flight"); the rest are the verb-initial cues."""
    for group, rx in _action_res(lang):
        if rx.match(x):
            return "supported", group
    for uid, rx in _unsupported_res(lang):
        spec = lex(lang)["unsupported"][uid]
        if spec.get("verbs"):
            span = _object_span(x, lang, uid)
            if span is not None and rx.search(span):
                return "unsupported", uid
        elif rx.search(x):
            return "unsupported", uid
    words = set(re.findall(r"\w+", x))
    for group, spec in (lex(lang).get("groups") or {}).items():
        if words & set(spec.get("words") or ()):
            return "supported", group
    return "unknown", ""


# ── flags (inventory + environment, at startup) ───────────────────────────────────────────────────────────────────────

def _state_of(raw: str) -> str:
    v = (raw or "").strip().lower()
    if v in ("", "0", "false", "no", "off", "-"):
        return "off"
    return "shadow" if v == "shadow" else "on"


@lru_cache(maxsize=1)
def _inventory() -> dict:
    try:
        return json.loads(INVENTORY.read_text(encoding="utf-8")).get("flags", {}).get("prod", {})
    except (OSError, ValueError):
        return {}


def inventory_default(flag: str) -> Optional[str]:
    """The flag's code default from the generated inventory as on|off|shadow, or None (absent / several defaults / dynamic)."""
    defaults = (_inventory().get(flag) or {}).get("defaults") or []
    if len(defaults) != 1:
        return None
    lit = defaults[0].strip().strip("'\"")
    if lit in ("True", "true", "1", "on", "yes"):
        return "on"
    if lit in ("False", "false", "0", "off", "no", "", "-"):
        return "off"
    return "shadow" if lit == "shadow" else None


def flag_state(flag: str, fallback: str = "off") -> str:
    """on | off | shadow: the process environment when the flag is set, else the inventory's default, else ``fallback``."""
    raw = os.environ.get(flag)
    if raw is not None:
        return _state_of(raw)
    return inventory_default(flag) or fallback


@dataclass(frozen=True)
class Facts:
    registry: dict
    dark: tuple             # ((key, state), ...) for every capability whose flag is not fully on, file order
    memory_rules: tuple     # keys of the rules whose backing code is on, file order
    egress: tuple           # keys of the things that leave the box
    forget_days: int        # 0 = forever
    web_on: bool
    face_on: bool
    audio_saved: bool
    ambient_on: bool        # the panel's background capture, when zoe-data's own environment says so (the Pi daemon's env is invisible)
    ambient_on: bool        # the panel's background capture, when zoe-data's own environment says so (the Pi daemon's env is invisible)


@lru_cache(maxsize=1)
def facts() -> Facts:
    """Everything that is fixed for the life of the process: the registry and the flag-derived lists."""
    nf = neutral()
    dark = tuple((d["key"], st) for d in nf["dark_flags"] if (st := flag_state(d["flag"], d.get("default", "off"))) != "on")
    rules = tuple(r["key"] for r in nf["memory_rules"] if not r.get("flag") or flag_state(r["flag"], r.get("default", "on")) == "on")
    egress = tuple(e["key"] for e in nf["egress"] if not e.get("flag") or flag_state(e["flag"], e.get("default", "off")) == "on")
    try:
        import memory_forgotten

        days = int(memory_forgotten.shield_days())
    except Exception:  # noqa: BLE001
        days = 0
    return Facts(registry=registry(), dark=dark, memory_rules=rules, egress=egress, forget_days=days,
                 web_on=flag_state("ZOE_WEB_SEARCH_TOOL") == "on", face_on=flag_state("ZOE_FACE_ID_ENABLED") == "on",
                 audio_saved=flag_state("ZOE_VOICE_SAVE_AUDIO") == "on",
                 ambient_on=flag_state(nf["ambient"]["flag"], nf["ambient"].get("default", "off")) == "on")


def reset() -> None:
    """Drop every cache (tests; a config reload)."""
    for fn in (registry, lex, neutral, facts, _cues, _unsupported_res, _inventory, _action_res, _verb_res, _object_end_re):
        getattr(fn, "cache_clear", lambda: None)()          # (a test may have swapped one for a plain function)
    _probe_cache.clear()
    _count_cache.clear()


def groups() -> list:
    """Group names in registry order that the current registry really has."""
    return [g for g, _ in (facts().registry.get("groups") or [])]


def tool_count() -> int:
    reg = facts().registry
    names = set(reg.get("tools") or ()) - {reg.get("activator", "")}
    if not facts().web_on:
        names -= {"web_search"}
    return len(names)


def canonical_asks(lang: str = "en") -> dict:
    """group -> its one canonical ask, from the language file (the tool bench and the block read this)."""
    return {g: (lex(lang).get("groups") or {}).get(g, {}).get("ask", "") for g in groups()}


# ── live state (request time) ─────────────────────────────────────────────────────────────────────────────────────────

_probe_cache: dict = {}
_count_cache: dict = {}


def _clock() -> float:
    return time.time()


def night_window_open() -> bool:
    d = Path(os.environ.get("NIGHT_DIR") or Path.home() / ".zoe" / "night-window")
    try:
        return (d / "WINDOW_OPEN").exists()
    except OSError:
        return False


async def _tcp_up(url: str, timeout: float) -> bool:
    u = urlparse(url if "://" in url else f"http://{url}")
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(u.hostname or "127.0.0.1", u.port or 80), timeout)
        w.close()
        return True
    except Exception:  # noqa: BLE001 - any failure is "not answering"
        return False


async def probe(name: str) -> str:
    """ok | asleep | down for ``music`` / ``home``, cached for the neutral file's ttl. Never raises, never longer than the timeout."""
    cfg = neutral()["probe"]
    now = time.monotonic()
    hit = _probe_cache.get(name)
    if hit and now - hit[0] < float(cfg["ttl_s"]):
        return hit[1]
    spec = cfg[name]
    up = await _tcp_up(os.environ.get(spec["url_env"]) or spec["default"], float(cfg["timeout_s"]))
    state = "ok" if up else "down"
    if not up and name == "music":
        try:
            import ma_ondemand

            if ma_ondemand.enabled():
                state = "asleep"            # Music Assistant is reaped when idle and starts on the first request
        except Exception:  # noqa: BLE001
            pass
    _probe_cache[name] = (now, state)
    return state


async def _count(sql: str, params: tuple = (), ttl: float = 60.0) -> Optional[int]:
    key = (sql, params)
    hit = _count_cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value: Optional[int] = None
    try:
        from db_pool import get_db_ctx  # type: ignore[import]

        async def _run() -> int:
            async with get_db_ctx() as db:
                cur = await db.execute(sql, params) if params else await db.execute(sql)
                row = await cur.fetchone()
                return int(row[0])

        value = await asyncio.wait_for(_run(), 1.0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("self_model: count skipped (%s)", type(exc).__name__)
    _count_cache[key] = (time.monotonic(), value)
    return value


@dataclass(frozen=True)
class Live:
    now: float = 0.0
    night_open: bool = False
    music: str = "ok"
    home: str = "ok"
    accounts: Optional[int] = None
    voices: Optional[int] = None
    faces: Optional[int] = None
    ambient_recent: Optional[int] = None


def ambient_capture_on(live: "Live") -> bool:
    """Is the panel's background capture running? zoe-data cannot see the Pi daemon's environment, so it is on when zoe-data's own
    ``AMBIENT_CAPTURE_ENABLED`` says so OR ambient rows landed this week. The wake-word-only listening answer is true only when
    neither holds; otherwise the answer that says the room is captured without the wake word is served (both languages)."""
    return facts().ambient_on or (live.ambient_recent or 0) > 0


async def live_state(*, counts: bool = True) -> Live:
    music, home = await asyncio.gather(probe("music"), probe("home"))
    acc = vo = fa = amb = None
    if counts:
        acc, vo, fa, amb = await asyncio.gather(
            _count("SELECT COUNT(*) FROM auth_users"),
            _count("SELECT COUNT(DISTINCT user_id) FROM speaker_profiles"),
            _count("SELECT COUNT(DISTINCT user_id) FROM face_profiles WHERE active = 1"),
            _count("SELECT COUNT(*) FROM ambient_memory WHERE timestamp > NOW() - INTERVAL '7 days'"))
    return Live(now=_clock(), night_open=night_window_open(), music=music, home=home, accounts=acc, voices=vo, faces=fa,
                ambient_recent=amb)


# ── sentences ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def join_list(items: list, lx: dict) -> str:
    items = [i for i in items if i]
    j = lx.get("join") or {}
    if len(items) <= 1:
        return "".join(items)
    return f"{j.get('sep', ', ').join(items[:-1])} {j.get('and', '&')} {items[-1]}"


_FMT = string.Formatter()


def fill(template: list, slots: dict, *, limit: Optional[int] = None) -> str:
    """The sentences of ``template`` whose slots are all non-empty, formatted and joined. ``limit`` caps the sentence count."""
    out = []
    for sent in template:
        fields = [f for _, f, _, _ in _FMT.parse(sent) if f]
        if any(not slots.get(f) for f in fields):
            continue
        out.append(sent.format(**slots).strip())
    out = [s for s in out if s]
    return " ".join(out[:limit] if limit else out)


def _available(group: str, live: Live) -> bool:
    svc = neutral().get("service_of_group", {}).get(group)
    return not (svc and getattr(live, svc, "ok") == "down")


def _examples(lx: dict, live: Live, n: int = 3) -> tuple:
    """(the first ``n`` available abilities as "do" phrases, the rest of the available groups as labels)."""
    have = set(groups())
    pref = neutral()["feature_order"]
    order = [g for g in pref if g in have] + [g for g in groups() if g not in pref]
    gs = lx.get("groups") or {}
    avail = [g for g in order if _available(g, live) and g in gs]
    return [gs[g]["do"] for g in avail[:n]], [gs[g]["label"] for g in avail[n:]]


def _hedge(lx: dict, live: Live) -> str:
    st = lx.get("state") or {}
    parts = []
    for svc in ("music", "home"):
        s = getattr(live, svc)
        if s != "ok":
            parts.append(lx["hedge_fmt"].format(service=st.get(svc, svc), state=st.get(s, s)))
    return " ".join(parts)


def _dark_phrases(lx: dict) -> list:
    return [lx["dark"][k] for k, _ in facts().dark if k in (lx.get("dark") or {})]


def _surfaces(lx: dict) -> str:
    wake = neutral()["wake_phrase"]
    return join_list([lx["surfaces"][s].format(wake=wake) for s in neutral()["surfaces"] if s in lx.get("surfaces", {})], lx)


def _unsupported_names(lx: dict) -> list:
    spec = lx.get("unsupported") or {}
    return [spec[u["id"]]["name"] for u in neutral()["unsupported"] if u["id"] in spec]


def _alt_ok(uid: str, live: Live) -> bool:
    alt = next((u["alt"] for u in neutral()["unsupported"] if u["id"] == uid), "")
    return bool(alt) and alt in groups() and _available(alt, live)


def _memory_lines(lx: dict, limit: Optional[int] = None) -> str:
    f = facts()
    tail = lx["forget_tail"]["permanent"] if f.forget_days <= 0 else lx["forget_tail"]["bounded"].format(days=f.forget_days)
    lines = [lx["memory"][k].format(forget_tail=tail) for k in f.memory_rules if k in lx.get("memory", {})]
    return " ".join(lines[:limit] if limit else lines)


def _models_line(lx: dict) -> str:
    m = {x["role"]: x["name"] for x in neutral()["models"]}
    return lx["answers"]["models_line"].format(**m)


def _egress(lx: dict) -> str:
    return join_list([lx["egress"][k] for k in facts().egress if k in lx.get("egress", {})], lx)


# ── the block (what a brain seam would inject) ────────────────────────────────────────────────────────────────────────

def build_block(lang: str = "en", live: Optional[Live] = None, *, cap: Optional[int] = None) -> str:
    """The self-model as a compact block. Static sections first, volatile (who / now) last; sections are dropped in the neutral
    file's ``drop_order`` until the block fits ``cap``. Empty when the language or the registry is unavailable."""
    lx = lex(lang)
    if not lx or not groups():
        return ""
    live = live or Live()
    b = lx["block"]
    gs = lx.get("groups") or {}
    sec: dict = {"header": b["header"], "surfaces": f"{b['surfaces']}: {_surfaces(lx)}."}
    sec["abilities"] = f"{b['abilities']}: " + "; ".join(
        f"{gs[g]['label']} ({b['ask']}: \"{gs[g]['ask']}\")" for g in groups() if g in gs) + "."
    sec["cannot"] = f"{b['cannot']}: {join_list(_unsupported_names(lx), lx)}."
    dark = _dark_phrases(lx)
    if dark:
        sec["dark"] = f"{b['dark']}: {join_list(dark, lx)}."
    sec["memory"] = f"{b['memory']}: {_memory_lines(lx)}"
    sec["runs_on"] = f"{b['runs_on']}: {neutral()['hardware']}. {_models_line(lx)}."
    if live.accounts is not None:
        sec["who"] = f"{b['who']}: " + b["who_counts"].format(accounts=live.accounts, voice=live.voices or 0, face=live.faces or 0)
    if live.now:
        st = lx["state"]
        stamp = datetime.datetime.fromtimestamp(live.now).strftime("%Y-%m-%d %H:%M")
        sec["now"] = (f"{b['now']}: {stamp}; {st['night_open'] if live.night_open else st['night_closed']}; "
                      f"{st['music']} {st.get(live.music, live.music)}; {st['home']} {st.get(live.home, live.home)}.")
    limit = cap or int(neutral()["max_block_chars"])
    text = "\n".join(sec.values())
    for key in neutral()["drop_order"]:
        if len(text) <= limit:
            break
        sec.pop(key, None)
        text = "\n".join(sec.values())
    return text[:limit]


# ── answers ───────────────────────────────────────────────────────────────────────────────────────────────────────────

async def _enrolment(user_id: str) -> tuple:
    """(has a voice profile, has a face profile) for THIS user, or None where the read failed."""
    v = await _count("SELECT COUNT(*) FROM speaker_profiles WHERE user_id = ?", (user_id,), ttl=30.0)
    fc = await _count("SELECT COUNT(*) FROM face_profiles WHERE user_id = ? AND active = 1", (user_id,), ttl=30.0)
    return (None if v is None else v > 0), (None if fc is None else fc > 0)


async def _own_name(user_id: str) -> str:
    try:
        import identity_facts

        ident = await identity_facts.resolve_identity(user_id, budget_s=1.0)
        return str(getattr(ident, "name", "") or "") if ident else ""
    except Exception:  # noqa: BLE001
        return ""


def _is_guest(user_id: Optional[str]) -> bool:
    u = (user_id or "").strip().lower()
    return u in _GUEST_IDS or u.startswith("guest-")


async def answer(q: Question, user_id: str = "", *, channel: Optional[str] = None, speaker_verified: Optional[bool] = None,
                 live: Optional[Live] = None, session_id: str = "") -> Optional[str]:
    """The reply for self-question ``q``, built from the generated model; None when this module must not answer (the turn goes
    on). NEVER raises (CancelledError still propagates)."""
    try:
        lx = lex(q.lang)
        if not lx or not groups():
            return None
        voice = (channel or "") in VOICE_CHANNELS
        a = lx["answers"]
        cap = 3 if voice else None
        live = live or await live_state(counts=q.kind == "listening")
        examples, more = _examples(lx, live)
        slots: dict = {"wake": neutral()["wake_phrase"], "surfaces": _surfaces(lx), "examples": join_list(examples, lx),
                       "more": "" if voice else join_list(more[:6], lx), "hedge": _hedge(lx, live),
                       "dark": join_list(_dark_phrases(lx)[:DARK_IN_ANSWER], lx), "no_list": join_list(_unsupported_names(lx), lx),
                       "tool_count": tool_count(), "group_count": len(groups()),
                       "groups": join_list([lx["groups"][g]["label"] for g in groups() if g in lx["groups"]], lx),
                       "hardware": neutral()["hardware"], "models": _models_line(lx), "egress": _egress(lx)}
        if q.kind in ("capabilities", "tools", "cannot", "made_by", "who_are_you", "running_on"):
            return fill(a[q.kind], slots, limit=cap) or None
        if q.kind == "can_you":
            verdict, uid = classify_can_you(q.x, q.lang)
            if verdict != "unsupported":
                return None
            spec = lx["unsupported"][uid]
            return fill(a["can_you_no"], {"no": spec["no"], "instead": spec["instead"] if _alt_ok(uid, live) else ""}) or None
        if q.kind == "how_remember":
            return fill(a["how_remember"], {"memory_rules": _memory_lines(lx, 4 if voice else None)}) or None
        if q.kind == "listening":
            f = facts()
            slots["audio_clause"] = a["audio_clause"] if f.audio_saved else ""
            return fill(a["listening_ambient" if ambient_capture_on(live) else "listening"], slots) or None
        if q.kind == "see_me":
            return fill(a["see_me"], {"face_state": lx["state"]["on" if facts().face_on else "off"]}) or None
        if q.kind == "know_me":
            if speaker_verified is False:
                return fill(a["know_me_unverified"], {})
            name = "" if _is_guest(user_id) else await _own_name(user_id)
            if not name:
                return fill(a["know_me_guest"], {})
            has_voice, has_face = await _enrolment(user_id)
            return fill(a["know_me_yes"], {"name": name, "voice_clause": a["voice_clause"] if has_voice else "",
                                           "face_clause": a["face_clause"] if has_face else ""}) or None
        if q.kind == "used_to_answer":
            import memory_provenance as mp

            rec = mp.previous_reply(user_id, session_id=session_id)
            if rec is not None and rec.kind == "direct" and rec.tier == TIER:
                return fill(a["used_self_model"], {})
            import provenance_answers

            return await provenance_answers.explain(user_id, channel=channel, session_id=session_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - a turn is never broken by this tier
        logger.warning("self_model: answer failed (non-fatal): %s", type(exc).__name__)
    return None


# ── the seam (fast_tiers.resolve calls this once) ─────────────────────────────────────────────────────────────────────

async def tier(text: str, user_id: str, session_id: str = "", *, channel: Optional[str] = None,
               speaker_verified: Optional[bool] = None):
    """The deterministic self-question tier: a ``DispatchResult`` in ``enforce`` mode for a recognised self-question, else None.
    ``shadow`` (the default) and ``off`` return None and change nothing; shadow writes one ``SELF_MODEL`` line. NEVER raises."""
    try:
        m = mode()
        if m == "off":
            return None
        q = classify(text)
        if q is None:
            return None
        verdict = "-"
        if q.kind == "can_you":
            verdict = classify_can_you(q.x, q.lang)[0]
            if verdict != "unsupported":
                logger.info("SELF_MODEL mode=%s kind=can_you lang=%s verdict=%s answered=0", m, q.lang, verdict)
                return None
        if m == "shadow":
            logger.info("SELF_MODEL mode=shadow kind=%s lang=%s verdict=%s would_answer=1 block_chars=%d",
                        q.kind, q.lang, verdict, len(build_block(q.lang)))
            return None
        reply = await answer(q, user_id, channel=channel, speaker_verified=speaker_verified, session_id=session_id)
        if not reply:
            logger.info("SELF_MODEL mode=enforce kind=%s lang=%s verdict=%s answered=0", q.kind, q.lang, verdict)
            return None
        try:
            import memory_provenance as mp

            mp.note_direct_reply(user_id, "provenance" if q.kind == "used_to_answer" else TIER, session_id)
        except Exception:  # noqa: BLE001
            pass
        logger.info("SELF_MODEL mode=enforce kind=%s lang=%s verdict=%s answered=1 chars=%d", q.kind, q.lang, verdict, len(reply))
        import expert_dispatch as _xd

        return _xd.DispatchResult(domain="self_model", reply=reply, intent=f"self_model_{q.kind}", tier=TIER)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("self_model: tier failed (non-fatal): %s", type(exc).__name__)
        return None


def snapshot() -> dict:
    """The registry as the committed snapshot stores it (deterministic)."""
    return _registry_from_sources()


if __name__ == "__main__":  # python3 services/zoe-data/self_model.py --snapshot | --block [lang]
    import sys

    if "--snapshot" in sys.argv:
        SNAPSHOT.write_text(json.dumps(snapshot(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {SNAPSHOT}")
    else:
        print(build_block(sys.argv[2] if len(sys.argv) > 2 else "en", asyncio.run(live_state(counts=False))))
