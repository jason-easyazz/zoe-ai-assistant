"""Distress hand-off: a deterministic floor for self-harm, abuse and danger language (governance note section 7).

On such a turn the BRAIN IS NOT ASKED: Zoe speaks one fixed, warm, two-sentence pointer to a human (the household contact and the local crisis line), the turn is kept
out of every memory writer, and the household contact is told once with no words from the turn. No humour, no diagnosis, no persona.

LANGUAGE INDEPENDENCE BY CONSTRUCTION (blueprint 2.9): this file reads no word of any language. Cues, guards and reply texts live in ``lexicons_data/<lang>.json``
(``distress``; en + es). A cue is a closed-vocabulary sequence (named ``groups`` joined by a closed ``fillers`` list: "I want my mum to die" is not "I want to die") or a
regex; text and patterns are folded alike (NFKC, casefold, accents off Latin letters, apostrophes dropped). Every language with a section is scanned on every turn; the reply
is in the language of the cue that fired. TIERS: ``handoff`` (a first-person cue in its own clause), ``gentle`` (the same cue behind a negation or a story/quote marker, a bare
topical word, a third party, low mood), ``none`` (nothing, or an idiom). A guard only ever LOWERS handoff to gentle; only an idiom voids a cue: over-trigger, never miss.

FLAGS (per call): ``ZOE_DISTRESS_HANDOFF`` off | shadow | enforce (DEFAULT enforce; a typo enforces), ``ZOE_DISTRESS_GENTLE`` default shadow. CONFIG, never hardcoded:
``ZOE_HOUSEHOLD_COUNTRY`` (falls back to ``ZOE_LOCATION_COUNTRY``) picks the numbers from ``distress_data/crisis_lines.json`` (or ``ZOE_DISTRESS_LINES_FILE``);
``ZOE_DISTRESS_CONTACT_NAME`` / ``_USER`` / ``ZOE_DISTRESS_NOTIFY`` (all | minors | off). An enforced turn is marked off the record (``memory_provenance.mark_turn``); the
digest and night-mind readers re-check the TEXT (``guarded_text``). Logs carry ids and enums only. Doc: docs/knowledge/distress-handoff.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import lexicons
from typed_env import env_str

logger = logging.getLogger(__name__)

ENV, ENV_GENTLE = "ZOE_DISTRESS_HANDOFF", "ZOE_DISTRESS_GENTLE"
LINES_FILE = Path(__file__).with_name("distress_data") / "crisis_lines.json"
MAX_CHARS = 2000
NOTIFY_COOLDOWN_S = 6 * 3600.0
_RANK = {"none": 0, "gentle": 1, "handoff": 2}
_GUESTS = frozenset({"", "guest", "anonymous", "voice-guest", "voice-daemon"})
_NOTIFIED: dict = {}


@dataclass(frozen=True)
class Verdict:
    tier: str = "none"      # handoff | gentle | none
    cls: str = ""           # the cue's class (suicide, abuse, danger, ...); never the words
    lang: str = ""
    why: str = ""           # cue | negated | quoted | topic


NONE = Verdict()


def _mode(raw: str, default: str) -> str:
    raw = raw.lower()
    if raw in {"0", "false", "no", "none", "off"}:
        return "off"
    if raw == "shadow":
        return "shadow"
    return "enforce" if raw in {"1", "true", "yes", "on", "enforce", "active"} else default   # a typo keeps the default


def handoff_mode() -> str:
    return _mode(env_str("ZOE_DISTRESS_HANDOFF", "enforce"), "enforce")


def gentle_mode() -> str:
    return "off" if handoff_mode() == "off" else _mode(env_str("ZOE_DISTRESS_GENTLE", "shadow"), "shadow")


def mode_for(tier: str) -> str:
    return handoff_mode() if tier == "handoff" else (gentle_mode() if tier == "gentle" else "off")


def _strip(s: str) -> str:
    """Accents off Latin letters (kana voicing marks stay), apostrophes dropped, whitespace collapsed."""
    out: list = []
    for ch in unicodedata.normalize("NFD", s):
        if unicodedata.category(ch) == "Mn" and out and out[-1].isascii():
            continue
        if ch not in "'’‘`ʼ":
            out.append(ch)
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", "".join(out))).strip()


@dataclass(frozen=True)
class _Cue:
    id: str
    cls: str
    tier: str
    negatable: bool
    rx: "re.Pattern"


@dataclass(frozen=True)
class _Pack:
    lang: str
    cues: tuple
    topic: tuple
    idioms: tuple
    neg: Optional["re.Pattern"]
    quote: Optional["re.Pattern"]
    breaks: "re.Pattern"


def _lit(phrase: str) -> str:
    return r"\s+".join(re.escape(w) for w in _strip(phrase).split(" "))


@lru_cache(maxsize=None)
def _pack(lang: str) -> Optional[_Pack]:
    d = lexicons.load(lang).get("distress") or {}
    if not d.get("enabled"):
        return None
    cjk = lexicons.is_cjk(lang)
    wrap = (lambda b: f"(?:{b})") if cjk else (lambda b: rf"(?<!\w)(?:{b})(?!\w)")
    fills = "|".join(_lit(f) for f in d.get("fillers", []) + d.get("negation", []))   # a negation may sit inside a cue: it demotes, below
    sep = r"\s*" if cjk else (rf"(?:\s+(?:{fills}))*\s+" if fills else r"\s+")
    groups = {g: "|".join(_lit(p) for p in items) for g, items in d.get("groups", {}).items()}
    cues = []
    for c in d.get("cues", []):
        parts = [f"(?:{groups[g]})" for g in c.get("seq", [])]
        body = sep.join(parts[:-1] + [f"(?P<end>{parts[-1]})"]) if parts else _strip(c["re"])
        cues.append(_Cue(c["id"], c["cls"], c["tier"], bool(c.get("negatable")), re.compile(wrap(body), re.I)))
    one = lambda items: re.compile(wrap("|".join(_strip(i) for i in items)), re.I) if items else None  # noqa: E731
    brk = "|".join(_lit(w) for w in d.get("clause_breaks", []))
    breaks = re.compile(r"[.!?;:,\n]" + (rf"|(?<!\w)(?:{brk})(?!\w)" if brk else ""))
    return _Pack(lang, tuple(cues), tuple(re.compile(wrap(_strip(t)), re.I) for t in d.get("topic", [])),
                 tuple(re.compile(_strip(i), re.I) for i in d.get("idioms", [])), one(d.get("negation")), one(d.get("quote_markers")), breaks)


def _scan(s: str, p: _Pack) -> Verdict:
    idioms = [m.span() for rx in p.idioms for m in rx.finditer(s)]
    ends = [0] + [m.end() for m in p.breaks.finditer(s)]
    near = lambda m: any(a < m.end() + 15 and b > m.start() - 15 for a, b in idioms)  # noqa: E731
    best = NONE
    for cue in p.cues:
        for m in cue.rx.finditer(s):
            if near(m):
                continue
            tier, why = cue.tier, "cue"
            if tier == "handoff":
                head = m.start()
                pre = s[max(e for e in ends if e <= head):head]
                inner = s[head:m.start("end")] if "end" in cue.rx.groupindex else ""
                if cue.negatable and p.neg is not None and (p.neg.search(inner) or re.search(rf"(?:{p.neg.pattern})(?:\s+\w+){{0,2}}\s*$", pre, re.I)):
                    tier, why = "gentle", "negated"
                elif p.quote is not None and p.quote.search(pre):
                    tier, why = "gentle", "quoted"
            if _RANK[tier] > _RANK[best.tier]:
                best = Verdict(tier, cue.cls, p.lang, why)
    if best.tier == "none":
        for rx in p.topic:
            m = rx.search(s)
            if m and not near(m):
                return Verdict("gentle", "topic", p.lang, "topic")
    return best


def detect(text: str) -> Verdict:
    """The tier of one turn. Pure and fast (a few compiled regexes per language); never raises."""
    try:
        s = _strip(unicodedata.normalize("NFKC", text or "").casefold())[:MAX_CHARS]
        best = NONE
        for pack in filter(None, (_pack(c) for c in lexicons.LANGS)) if s else ():
            v = _scan(s, pack)
            if _RANK[v.tier] > _RANK[best.tier]:
                best = v
        return best
    except Exception as exc:  # noqa: BLE001 - a floor must not break a turn
        logger.warning("distress detect failed (%s)", type(exc).__name__)
        return NONE


def guarded_text(text: str) -> bool:
    """Must this turn never reach a memory writer? (an ENFORCED tier only; shadow changes nothing). Stateless."""
    v = detect(text)
    return v.tier != "none" and mode_for(v.tier) == "enforce"


@dataclass(frozen=True)
class Config:
    country: str = ""
    contact_name: str = ""
    contact_user: str = ""
    notify: str = "all"


def config() -> Config:
    raw = env_str("ZOE_HOUSEHOLD_COUNTRY", "") or env_str("ZOE_LOCATION_COUNTRY", "")
    if len(raw) > 2:
        try:
            from identity_facts import _COUNTRY_NAMES
            raw = next((k for k, v in _COUNTRY_NAMES.items() if v.lower() == raw.lower()), raw)
        except Exception:  # noqa: BLE001
            pass
    notify = env_str("ZOE_DISTRESS_NOTIFY", "all").lower()
    return Config({"UK": "GB"}.get(raw.upper(), raw.upper()), env_str("ZOE_DISTRESS_CONTACT_NAME", ""),
                  env_str("ZOE_DISTRESS_CONTACT_USER", ""), notify if notify in {"all", "minors", "off"} else "all")


@lru_cache(maxsize=8)
def _table(path: str) -> dict:
    try:
        return (json.loads(Path(path).read_text(encoding="utf-8")).get("countries") or {}) if path else {}
    except (OSError, ValueError):
        logger.warning("distress: crisis lines file unreadable (%s)", Path(path).name)
        return {}


def _entry(country: str) -> dict:
    return _table(env_str("ZOE_DISTRESS_LINES_FILE", "")).get(country) or _table(str(LINES_FILE)).get(country) or {}


def _line(r: dict, ent: dict, minor: bool) -> str:
    pick = (ent.get("kids") or ent.get("crisis")) if minor else ent.get("crisis")
    return r["line_fmt"].format(**pick) if pick else r["line_fallback"]


def build_reply(v: Verdict, *, minor: bool = False, user_id: str = "") -> str:
    """The fixed text for a verdict, in the language of the cue that fired. Never model text."""
    r, cfg, who = lexicons.load(v.lang or "en")["distress"]["replies"], config(), "child" if minor else "adult"
    if v.tier == "gentle":
        return r["gentle"][who]
    ent = _entry(cfg.country)
    contact = cfg.contact_name if cfg.contact_name and user_id != cfg.contact_user else r["contact_fallback_" + who]
    return r["handoff"][r.get("variant", {}).get(v.cls, who)].format(
        contact=contact, line=_line(r, ent, minor), emergency=ent.get("emergency") or r["emergency_fallback"])


async def _is_minor(user_id: str) -> bool:
    try:
        import persona_layer
        return bool(user_id not in _GUESTS and (await persona_layer.load_member_mode(user_id)).minor)
    except Exception:  # noqa: BLE001 - no record = the adult wording (the crisis line serves anyone)
        return False


async def _notify(user_id: str, v: Verdict, minor: bool) -> None:
    """Tell the household contact ONCE (6 h per member), plainly, with no words from the turn: a notification row + the bell broadcast."""
    cfg = config()
    try:
        if (not cfg.contact_user or cfg.notify == "off" or (cfg.notify == "minors" and not minor)
                or user_id in _GUESTS or user_id == cfg.contact_user or v.tier != "handoff"):
            return
        if time.monotonic() - _NOTIFIED.get(user_id, -1e9) < NOTIFY_COOLDOWN_S:
            return
        _NOTIFIED[user_id] = time.monotonic()
        r = lexicons.load(v.lang or "en")["distress"]["replies"]
        who = r["notify_unknown_who"]
        try:
            import identity_facts
            ident = await identity_facts.resolve_identity(user_id, budget_s=1.0)
            who = ident.name if ident and ident.name else who
        except Exception:  # noqa: BLE001
            pass
        message = r["notify"].format(who=who, line=_line(r, _entry(cfg.country), minor), contact=cfg.contact_name or r["notify_contact_fallback"])
        from db_pool import get_db_ctx
        from push import broadcaster
        nid = str(uuid.uuid4())
        async with get_db_ctx() as db:
            await db.execute("INSERT INTO notifications (id, user_id, type, title, message, data, delivered, created_at) "
                             "VALUES (?, ?, 'distress_handoff', ?, ?, ?, 0, NOW())",
                             (nid, cfg.contact_user, r["notify_title"], message, json.dumps({"member": user_id, "cls": v.cls})))
            await db.commit()
        await broadcaster.broadcast("all", "notification_created", {"id": nid, "type": "distress_handoff", "title": r["notify_title"],
                                                                      "message": message, "delivered": False}, user_id=cfg.contact_user)
        logger.info("DISTRESS_NOTIFIED member=%s", user_id)
    except Exception as exc:  # noqa: BLE001
        _NOTIFIED.pop(user_id, None)
        logger.warning("distress notify failed (%s)", type(exc).__name__)


async def handle(text: str, user_id: str, *, dry: bool = False) -> Optional[str]:
    """The seam. The fixed reply when this turn is an ENFORCED tier (the caller must NOT ask the brain), else None. Logs ids and enums only.
    ``dry`` (the replay harness) still answers but marks and notifies nothing. NEVER raises."""
    try:
        v = detect(text)
        mode = mode_for(v.tier)
        if v.tier == "none" or mode == "off":
            return None
        uid = (user_id or "").strip()
        logger.info("DISTRESS_HANDOFF user=%s tier=%s lang=%s mode=%s", uid or "guest", v.tier, v.lang, mode)
        if mode != "enforce":
            return None
        if not config().country:
            logger.warning("DISTRESS_HANDOFF cannot name a crisis line: ZOE_HOUSEHOLD_COUNTRY is not set")
        minor = await _is_minor(uid)
        if not dry:
            import memory_provenance
            memory_provenance.mark_turn(uid, text)
            asyncio.ensure_future(_notify(uid, v, minor))
        return build_reply(v, minor=minor, user_id=uid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("distress handle failed (%s)", type(exc).__name__)
        return None
