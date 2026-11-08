"""The member register - how Zoe speaks with THIS person (flag-dark ``ZOE_MEMBER_REGISTER``; Samantha initiative lane F E4).

IDENTITY AS STORED LAYERS (``docs/research/samantha-initiative-2026-11-03/lane-F-personality-proactive.md`` section 2.2). A reply is a
function of layers, none a free-text adjective list:

  L0 invariants   the soul + doctrines + guards          sidecar prompt / code          NOT touched here (the stable cached prefix)
  L1 manner       counted behaviours, one voice          ``manner_block.py``            NOT touched here
  L2 register     length / formality / humour / pace /   ``member_register`` table       THIS module: per member, stored, editable
                  liked topics                           (alembic 0068)
  L3 situational  late-night quiet                       the clock, computed per turn   THIS module: ``situational_line``

Only L2 + L3 are rendered here, as ONE short line (``MAX_REGISTER_TOKENS``) that rides a ``zoe-register:`` envelope line and is appended
by the sidecar AFTER the doctrines, the persona and the manner block and just BEFORE the user-model card
(``labs/flue-zoe-brain-2x/src/register.ts``). The bytes up to it are the stable cached prefix and are identical for every member, so
a member's register costs a re-prefill of its own few tokens at most and never invalidates the shared prefix.

WHAT IS LEARNED, AND FROM WHAT. ``provenance``: ``stated`` (the member's own words, or an edit they or an owner make) >
``confirmed`` (an observation the member said yes to) > ``inferred`` (a proposal). ONLY stated and confirmed rows are rendered;
an inferred row is stored as a proposal and never reaches a prompt until the member confirms it. A lower-ranked write never
replaces a higher-ranked row, and inference never overrides what the member stated. ``forget`` writes a TOMBSTONE (value blanked,
``status='forgotten'``): inference cannot re-add it; only the member stating it again can. ``forget_user`` erases every row.

WHO GETS WHAT (``audience``). A guest, an unconfirmed voice, an unknown account and every failed lookup get the DEFAULT: no line, the
wire bytes are today's. An adult (``manner_block.is_adult``, fail closed) gets their register. A child (a ``member_modes`` minor / kid
row or a child role) gets a FIXED kid-safe register and none of the stored entries, and only while ``ZOE_MEMBER_REGISTER_KIDS`` is
on - a second, separate flag, because the persona layer holds minors on the fixed persona until the distress hand-off ships
(``persona_layer.MINORS_GET_PERSONA``). The kid text only restricts (plain words, short, no teasing, no guessing); it adds no persona.

Stdlib only apart from lazy DB / sibling imports. The rendered words are enum-mapped or sanitised: a stored value can never carry
prompt text (``validate`` allows enum words and ``[a-z0-9 -]`` topics only).
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

FLAG = "ZOE_MEMBER_REGISTER"
FLAG_KIDS = "ZOE_MEMBER_REGISTER_KIDS"
_TRUTHY = frozenset({"on", "1", "true", "yes"})

#: the budget for the whole rendered line, in tokens (chars/4, the repo convention); a longer render is dropped, never truncated
MAX_REGISTER_TOKENS = 100
MAX_REGISTER_CHARS = MAX_REGISTER_TOKENS * 4

#: single-valued register keys and their allowed values -> the words the model reads ("" = the default, says nothing)
ENUM_KEYS: dict[str, dict[str, str]] = {
    "length": {"terse": "keep replies very short", "normal": "", "fuller": "fuller replies are welcome"},
    "formality": {"casual": "an easy, casual tone", "neutral": "", "formal": "a more formal tone"},
    "humour": {"off": "no jokes", "light": "light humour is welcome when they start it"},
    "pace": {"slow": "an unhurried pace", "normal": "", "brisk": "a brisk pace"},
}
KEY_ORDER = ("length", "formality", "humour", "pace")
MAX_TOPICS = 5
TOPIC_MAX_CHARS = 24
_TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9 \-]{0,%d}$" % (TOPIC_MAX_CHARS - 1))
TOPIC_PREFIX = "topic:"

PROVENANCE_RANK = {"inferred": 1, "confirmed": 2, "stated": 3}
RENDERED = frozenset({"stated", "confirmed"})
ACTIVE, FORGOTTEN = "active", "forgotten"

LEAD = "With this person (their own preferences): "
TOPICS_LEAD = "they enjoy talking about "
NIGHT_LINE = "It is late: keep it quiet and brief, and raise nothing new."
#: late-night window in the household timezone, [start, end) as local hours (wraps midnight)
NIGHT_START_HOUR, NIGHT_END_HOUR = 22, 6

#: the fixed register a child gets (only while ZOE_MEMBER_REGISTER_KIDS is on). Restrictive by design; stored entries are ignored.
KID_REGISTER = ("With this person: use plain, short words and one idea at a time; be patient and kind; no sarcasm, teasing or "
                "jokes unless they make one first; never guess how they feel or how old they are; if they seem upset or unsafe, "
                "say a grown-up can help and stop.")

AUDIENCE_ADULT, AUDIENCE_CHILD, AUDIENCE_DEFAULT = "adult", "child", "default"


class RegisterValidationError(ValueError):
    pass


def enabled() -> bool:
    from typed_env import env_str

    return env_str("ZOE_MEMBER_REGISTER", "off").lower() in _TRUTHY  # literal on purpose: tools/audit/flag_inventory.py greps it


def kids_enabled() -> bool:
    from typed_env import env_str

    return env_str("ZOE_MEMBER_REGISTER_KIDS", "off").lower() in _TRUTHY


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text or "") / 4)


@dataclass(frozen=True)
class Entry:
    slot: str
    key: str
    value: str
    provenance: str
    status: str = ACTIVE

    def to_dict(self) -> dict:
        return {"slot": self.slot, "key": self.key, "value": self.value, "provenance": self.provenance, "status": self.status}


# ── validation ────────────────────────────────────────────────────────────────────────────────
def normalise_topic(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip().lower())


def validate(key: str, value: str) -> tuple[str, str, str]:
    """(slot, key, value) for an acceptable setting; ``RegisterValidationError`` otherwise. Enum words or a sanitised topic only."""
    k = (key or "").strip().lower()
    if k == "topic":
        v = normalise_topic(value)
        if not _TOPIC_RE.match(v):
            raise RegisterValidationError(f"a topic is 1-{TOPIC_MAX_CHARS} chars of letters, digits, spaces or hyphens")
        return TOPIC_PREFIX + v, "topic", v
    if k not in ENUM_KEYS:
        raise RegisterValidationError(f"unknown register key; known: {', '.join(KEY_ORDER)}, topic")
    v = (value or "").strip().lower()
    if v not in ENUM_KEYS[k]:
        raise RegisterValidationError(f"{k} must be one of: {', '.join(ENUM_KEYS[k])}")
    return k, k, v


# ── rendering (pure) ──────────────────────────────────────────────────────────────────────────
def situational_line(now: Optional[datetime] = None) -> str:
    """L3: the late-night line, or "" by day. ``now`` is injectable; naive means household-local."""
    from time_utils import zoe_timezone

    t = now or datetime.now(timezone.utc)
    hour = (t.astimezone(zoe_timezone()) if t.tzinfo else t).hour
    late = hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR
    return NIGHT_LINE if late else ""


def render(entries: list[Entry], now: Optional[datetime] = None) -> str:
    """The register line for these entries, or "". Deterministic (fixed key order, topics sorted): same state, same bytes.
    Only active stated / confirmed rows count; a value whose words are empty (the default) adds nothing."""
    live = [e for e in entries if e.status == ACTIVE and e.provenance in RENDERED]
    by_key = {e.key: e for e in live if e.key in ENUM_KEYS}
    parts = []
    for k in KEY_ORDER:
        e = by_key.get(k)
        words = ENUM_KEYS[k].get(e.value, "") if e else ""
        if words:
            parts.append(words)
    # Re-validate on the way OUT: a row written by anything but ``set_entry`` (a manual insert, a future writer) must not carry text.
    topics = sorted({e.value for e in live if e.key == "topic" and _TOPIC_RE.match(e.value)})[:MAX_TOPICS]
    if topics:
        parts.append(TOPICS_LEAD + ", ".join(topics))
    text = (LEAD + "; ".join(parts) + ".") if parts else ""
    out = " ".join(p for p in (text, situational_line(now)) if p)
    if len(out) > MAX_REGISTER_CHARS:
        logger.warning("MEMBER_REGISTER render over the %d-token budget; no line", MAX_REGISTER_TOKENS)
        return ""
    return out


# ── storage (alembic 0068) ────────────────────────────────────────────────────────────────────
def _db(db=None):
    import persona_layer

    return persona_layer._db(db)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def list_entries(user_id: str, db=None, include_forgotten: bool = False) -> list[Entry]:
    uid = (user_id or "").strip()
    if not uid:
        return []
    async with _db(db) as conn:
        rows = await (await conn.execute(
            "SELECT slot, key, value, provenance, status FROM member_register WHERE user_id = ? ORDER BY slot", (uid,))).fetchall()
    out = [Entry(r[0], r[1], r[2], r[3], r[4]) for r in rows]
    return out if include_forgotten else [e for e in out if e.status == ACTIVE]


async def set_entry(user_id: str, key: str, value: str, provenance: str, updated_by: str = "", db=None) -> str:
    """Store one setting. Returns ``written`` | ``kept`` (a higher-ranked row, or a tombstone against inference, stands).
    ``stated`` always wins (and lifts a tombstone); ``confirmed`` replaces confirmed / inferred; ``inferred`` only fills an empty slot
    or refreshes another inference. A rank conflict is ``kept``, never an error."""
    uid = (user_id or "").strip()
    if not uid:
        raise RegisterValidationError("no member")
    if provenance not in PROVENANCE_RANK:
        raise RegisterValidationError("provenance must be stated, confirmed or inferred")
    slot, k, v = validate(key, value)
    async with _db(db) as conn:
        row = await (await conn.execute(
            "SELECT provenance, status FROM member_register WHERE user_id = ? AND slot = ?", (uid, slot))).fetchone()
        if row is not None:
            if row[1] == FORGOTTEN and provenance != "stated":
                return "kept"
            if row[1] == ACTIVE and PROVENANCE_RANK[provenance] < PROVENANCE_RANK.get(row[0], 0):
                return "kept"
        if k == "topic":
            n = await (await conn.execute(
                "SELECT COUNT(*) FROM member_register WHERE user_id = ? AND key = 'topic' AND status = 'active' AND slot != ?",
                (uid, slot))).fetchone()
            if int(n[0]) >= MAX_TOPICS:
                raise RegisterValidationError(f"at most {MAX_TOPICS} topics")
        await conn.execute(
            """INSERT INTO member_register (user_id, slot, key, value, provenance, status, updated_by, updated_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
               ON CONFLICT(user_id, slot) DO UPDATE SET key = excluded.key, value = excluded.value,
                   provenance = excluded.provenance, status = 'active', updated_by = excluded.updated_by,
                   updated_at = excluded.updated_at""",
            (uid, slot, k, v, provenance, updated_by or "", _now()))
        await conn.commit()
    return "written"


async def confirm(user_id: str, key: str, value: str, updated_by: str = "", db=None) -> str:
    """The member said yes to an inferred proposal: raise it to ``confirmed``. Only an existing ACTIVE inferred row holding the SAME
    value can be confirmed (a confirm never invents a setting). ``written`` | ``kept``."""
    uid = (user_id or "").strip()
    slot, _k, v = validate(key, value)
    async with _db(db) as conn:
        row = await (await conn.execute(
            "SELECT value, provenance, status FROM member_register WHERE user_id = ? AND slot = ?", (uid, slot))).fetchone()
        if row is None or row[2] != ACTIVE or row[1] != "inferred" or row[0] != v:
            return "kept"
        await conn.execute(
            "UPDATE member_register SET provenance = 'confirmed', updated_by = ?, updated_at = ? WHERE user_id = ? AND slot = ?",
            (updated_by or "", _now(), uid, slot))
        await conn.commit()
    return "written"


async def forget(user_id: str, key: str, value: str = "", db=None) -> bool:
    """Take a setting away (member or owner). Leaves a tombstone so inference cannot re-add it; for a topic ``value`` names the slot."""
    uid = (user_id or "").strip()
    k = (key or "").strip().lower()
    if k != "topic" and k not in ENUM_KEYS:
        raise RegisterValidationError("unknown register key")
    slot = TOPIC_PREFIX + normalise_topic(value) if k == "topic" else k
    async with _db(db) as conn:
        cur = await conn.execute(
            "UPDATE member_register SET value = '', status = 'forgotten', updated_at = ? "
            "WHERE user_id = ? AND slot = ? AND status = 'active'", (_now(), uid, slot))
        await conn.commit()
        return bool(getattr(cur, "rowcount", 0))


async def forget_user(user_id: str, db=None) -> int:
    """Erase every register row of a member (``MemoryService.delete_user``). Fail closed: a store error raises; a missing table
    (migration not yet run) is nothing to erase."""
    uid = (user_id or "").strip()
    if not uid:
        return 0
    try:
        async with _db(db) as conn:
            cur = await conn.execute("DELETE FROM member_register WHERE user_id = ?", (uid,))
            await conn.commit()
            return int(getattr(cur, "rowcount", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        if "no such table" in text or "does not exist" in text:
            return 0
        # A process that never HAD a pool (a standalone memory test / script) has nothing to erase; a pool that was built and is
        # now gone means the erasure did not happen, so it raises for the caller to retry (alarms.forget_user's rule).
        if "not initialised" in text:
            from db_pool import pool_ever_initialised

            if not pool_ever_initialised():
                return 0
        raise


async def erase_entity(user_id: str, name: str, db=None) -> int:
    """Scrub a forgotten entity out of a member's register (``memory_forget_entity``): every ``topic`` row (active OR tombstone - the
    tombstone's slot still spells the label) whose label or slot names ``name`` as a whole word is DELETED, not blanked. The enum
    settings carry no names. Fail closed: a store error raises so the forget is not confirmed; a missing table is nothing to erase."""
    uid = (user_id or "").strip()
    if not uid or not (name or "").strip():
        return 0
    from memory_forgotten import name_pattern

    pat = name_pattern(name)
    try:
        async with _db(db) as conn:
            rows = await (await conn.execute(
                "SELECT slot, value FROM member_register WHERE user_id = ? AND key = 'topic'", (uid,))).fetchall()
            hit = [r[0] for r in rows if pat.search(str(r[0] or "")) or pat.search(str(r[1] or ""))]
            for slot in hit:
                await conn.execute("DELETE FROM member_register WHERE user_id = ? AND slot = ?", (uid, slot))
            if hit:
                await conn.commit()
            return len(hit)
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        if "no such table" in text or "does not exist" in text:
            return 0
        if "not initialised" in text:
            from db_pool import pool_ever_initialised

            if not pool_ever_initialised():
                return 0
        raise


async def live_entries(user_id: str, db=None) -> list[Entry]:
    """The member's active entries that may be SERVED: a topic naming a forgotten entity is withheld (the serve-time wall behind
    ``erase_entity``). Strict: a ledger outage drops every topic this turn - never "nothing is forgotten"."""
    entries = await list_entries(user_id, db=db)
    topics = [e for e in entries if e.key == "topic"]
    if not topics:
        return entries
    try:
        import memory_forgotten
        from memory_tombstones import matching_tombstone

        uid = (user_id or "").strip()
        bad = set()
        for e in topics:
            if matching_tombstone(uid, e.value) or await memory_forgotten.matches(uid, e.value, strict=True):
                bad.add(e.slot)
    except Exception:  # noqa: BLE001 - fail closed
        logger.info("MEMBER_REGISTER forgotten-ledger unreadable; no topics served")
        bad = {e.slot for e in topics}
    return [e for e in entries if e.slot not in bad]


# ── who gets what ─────────────────────────────────────────────────────────────────────────────
async def audience(user_id: str, db=None) -> str:
    """adult | child | default. Guests, unknown accounts, anything unproven and every failed lookup are DEFAULT. The id string is never read."""
    import asyncio

    import manner_block

    uid = (user_id or "").strip()
    if not uid or manner_block.is_guest(uid):
        return AUDIENCE_DEFAULT
    try:
        if await manner_block.is_adult(uid, db=db):
            return AUDIENCE_ADULT

        async def _child() -> bool:
            import persona_layer

            member = await persona_layer.load_member_mode(uid, db=db)
            if member.minor or member.mode in manner_block.MINOR_MODES:
                return True
            async with persona_layer._db(db) as conn:
                row = await (await conn.execute("SELECT role FROM auth_users WHERE user_id = ?", (uid,))).fetchone()
            return bool(row) and str(row[0] or "").strip().lower() in manner_block.CHILD_ROLES

        if await asyncio.wait_for(_child(), timeout=manner_block.LOOKUP_BUDGET_S):
            return AUDIENCE_CHILD
    except Exception:  # noqa: BLE001 - not knowing who this is means the default
        logger.warning("member register audience lookup failed for %s; default", uid, exc_info=True)
    return AUDIENCE_DEFAULT


async def line_for(user_id: str, now: Optional[datetime] = None, db=None) -> str:
    """The register line for THIS member's turn, or "" (= today's bytes). Never raises; flag off returns before any read."""
    try:
        if not enabled():
            return ""
        who = await audience(user_id, db=db)
        if who == AUDIENCE_CHILD:
            return KID_REGISTER if kids_enabled() else ""
        if who != AUDIENCE_ADULT:
            return ""
        import asyncio

        import manner_block

        # The register read has the same short budget as the audience lookups: a slow store means "no line" this turn, never
        # a pool-acquisition / 30 s command wait that silences a voice turn.
        entries = await asyncio.wait_for(live_entries((user_id or "").strip(), db=db), timeout=manner_block.LOOKUP_BUDGET_S)
        return render(entries, now=now)
    except Exception:  # noqa: BLE001 - a register is optional; the turn is not
        logger.warning("MEMBER_REGISTER failed; no line", exc_info=True)
        return ""
