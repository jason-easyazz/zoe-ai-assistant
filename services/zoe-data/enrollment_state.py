"""Per-member biometric SETUP state, and the policy that decides when Zoe may OFFER to learn a voice (``ZOE_ENROLL_OFFER``).

The owner's requirement: if a member has not enrolled, Zoe must KNOW that and prompt them, by voice, at a natural moment; a "no" is
fine; and nobody ever runs a command. This module is the knowledge and the rules; ``voice_enrollment.py`` is the conversation, and
the daemon (``scripts/setup/zoe_voice_enroll_mode.py``) is the hands.

State (``get_state``). Whether a voice / face IS enrolled is READ from ``speaker_profiles`` / ``face_profiles`` - the tables that hold
the enrolment - never copied, so the answer cannot disagree with what the panel will actually match. What those tables cannot say -
what Zoe offered and what the member answered - is ``enrollment_offers`` (migration 0046). A read that fails reports ``None`` ("I do
not know"), never ``False``: Zoe must not offer to learn a voice she already has because the database blinked.

The offer policy (``decide_offer``, pure). An offer is made only when EVERY rule holds, and the first rule that fails is the reason
(returned, logged, tested one by one):

  flag       ``ZOE_ENROLL_OFFER`` is ``on`` (``shadow`` decides and logs, says nothing)
  person     a real member: not a guest, not a synthetic/test id, positively known as an adult - or, ONLY with
             ``ZOE_ENROLL_OFFER_KIDS`` on, as a child (biometrics of minors is an owner decision, default OFF); an unknown role is refused
  state      the voice is not already enrolled, and the read that said so succeeded
  declined   the member has not said no (or ignored three offers) inside the quiet period (``ZOE_ENROLL_DECLINE_DAYS``, default 60)
  frequency  nothing was offered in the last 24 hours, on either path
  moment     a PIN step-up on the panel, or the close of a conversation - never mid-task (the callers pick the moment)
  quiet      not inside the household quiet hours

Asking for it yourself ("learn my voice") skips every rule except the person rules and the flag, clears the quiet period, and is never
refused for frequency.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import speaker_models
from typed_env import env_bool, env_int, env_str

logger = logging.getLogger(__name__)

ENV = "ZOE_ENROLL_OFFER"
MODALITY_VOICE = "voice"
MODALITY_FACE = "face"
#: at most one offer per member per this many seconds, whichever moment made it
OFFER_GAP_S = 24 * 3600
#: offers made and never answered before the member is given the same quiet period as after a "no"
MAX_IGNORED = 3
DEFAULT_DECLINE_DAYS = 60
_TS = "%Y-%m-%dT%H:%M:%SZ"
_GUEST_PREFIX = "guest-"


# ── flags (per-call reads: a flip needs no restart) ───────────────────────────────────────────────────────────────────────

def mode() -> str:
    """``off`` (DEFAULT) | ``shadow`` (decide and log, say and do nothing) | ``on``. Anything unrecognised is ``off``."""
    raw = env_str("ZOE_ENROLL_OFFER", "off").lower()
    if raw in ("1", "true", "yes", "on", "enforce"):
        return "on"
    if raw == "shadow":
        return "shadow"
    return "off"


def kids_allowed() -> bool:
    """``ZOE_ENROLL_OFFER_KIDS`` - may Zoe OFFER (and accept a request for) voice enrolment to a child member? DEFAULT OFF: the
    biometrics of a minor are an owner decision. Forgetting a voice is never gated by it."""
    return env_bool("ZOE_ENROLL_OFFER_KIDS", False)


def decline_days() -> int:
    """``ZOE_ENROLL_DECLINE_DAYS`` - the quiet period after a "no" (default 60, at least 1)."""
    return max(1, env_int("ZOE_ENROLL_DECLINE_DAYS", DEFAULT_DECLINE_DAYS))


# ── time ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS)


def parse(value) -> Optional[datetime]:
    """An ISO-Z string (or a database timestamp) as an aware UTC datetime; None when empty or unreadable."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip().replace("T", " ").rstrip("Z")
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S%z"):
        try:
            dt = datetime.strptime(s, f)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ── the state ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SetupState:
    """What Zoe knows about one member's biometric setup. ``None`` in a ``*_enrolled`` field = the read failed (unknown)."""

    user_id: str
    voice_enrolled: Optional[bool] = False
    voice_samples: int = 0
    voice_enrolled_at: Optional[str] = None
    voice_consented: bool = False
    voice_upgrade: bool = False       # enrolled in ANOTHER speaker model, but not yet in every model ZOE_SPEAKER_MODEL needs
    face_enrolled: Optional[bool] = False
    face_samples: int = 0
    last_offered_at: Optional[str] = None
    offer_count: int = 0
    pending_since: Optional[str] = None
    ignored_count: int = 0
    accepted_at: Optional[str] = None
    declined_at: Optional[str] = None
    declined_until: Optional[str] = None

    def declined_now(self, now: datetime) -> bool:
        until = parse(self.declined_until)
        return until is not None and until > now

    def to_public(self, now: Optional[datetime] = None) -> dict:
        """The read API's shape: counts and timestamps, never an embedding, never a name."""
        now = now or _now()
        return {
            "user_id": self.user_id,
            "voice": {"enrolled": self.voice_enrolled, "samples": self.voice_samples, "enrolled_at": self.voice_enrolled_at,
                      "consented": self.voice_consented},
            "face": {"enrolled": self.face_enrolled, "samples": self.face_samples},
            "offer": {"last_offered_at": self.last_offered_at, "offer_count": self.offer_count,
                      "accepted_at": self.accepted_at, "declined_at": self.declined_at,
                      "declined_until": self.declined_until, "declined": self.declined_now(now)},
        }


@asynccontextmanager
async def _conn(db=None):
    """The caller's connection when given, else a pooled one."""
    if db is not None:
        yield db
        return
    from db_compat import get_compat_db

    async with get_compat_db() as conn:
        yield conn


async def _scalar_row(db, sql: str, params: tuple):
    async with db.execute(sql, params) as cur:
        return await cur.fetchone()


def acting_voice_count_query(user_id: Optional[str] = None) -> tuple:
    """``(sql, params)`` counting members (or, with ``user_id``, that member's rows) whose voice the CURRENT acting model can
    recognise: a consented ``speaker_profiles`` row of ``speaker_models.acting_model()``. The same rule ``_voice_state`` applies,
    shared so every "is this voice known?" answer agrees (``self_model``)."""
    acting = speaker_models.acting_model()
    where = "consent_at IS NOT NULL AND " + speaker_models.MODEL_SQL + " = ?"
    if user_id is None:
        return "SELECT COUNT(DISTINCT user_id) FROM speaker_profiles WHERE " + where, (acting,)
    return "SELECT COUNT(*) FROM speaker_profiles WHERE user_id = ? AND " + where, (user_id, acting)


def _enrolled_key(row) -> str:
    return str(row[1] or "")


async def _voice_state(db, uid: str) -> dict:
    """The voice fields of ``SetupState`` (a failed read raises: the caller reports 'unknown', never 'no').

    Model-aware (``speaker_models``): enrolled means a CONSENTED profile in the ACTING model (ResNet34). A member who holds only a
    leftover Resemblyzer row is NOT enrolled and is offered the re-enrolment the acting model needs. ``voice_upgrade`` stays in the
    shape for callers but is False whenever the acting model recognises the member."""
    async with db.execute(
        "SELECT sample_count, enrolled_at, consent_at, " + speaker_models.MODEL_SQL + " FROM speaker_profiles WHERE user_id = ?",
        (uid,),
    ) as cur:
        rows = await cur.fetchall()
    consented = dict()
    for r in sorted(rows, key=_enrolled_key):                 # newest last, so it wins per model
        if r[2] is not None:
            consented[speaker_models.normalize_model_id(r[3])] = r
    if not consented:
        return dict(voice_enrolled=False)
    complete = all(m in consented for m in speaker_models.enrolled_models())
    acting = consented.get(speaker_models.acting_model())
    if acting is None:
        return dict(voice_enrolled=False)                     # nothing that can recognise them in this mode
    return dict(voice_enrolled=complete, voice_consented=True, voice_upgrade=not complete,
                voice_samples=int(acting[0] or 0), voice_enrolled_at=str(acting[1]) if acting[1] is not None else None)


async def get_state(user_id: str, db=None) -> SetupState:
    """The member's setup state. Each source is read on its own: a failed read leaves THAT field unknown (None), the rest intact."""
    uid = (user_id or "").strip()
    voice: dict = {"voice_enrolled": None}
    face: dict = {"face_enrolled": None}
    offer: dict = {}
    try:
        async with _conn(db) as conn:
            try:
                voice = await _voice_state(conn, uid)
            except Exception as exc:  # noqa: BLE001 - unknown, not "no"
                logger.warning("enrollment_state: speaker read failed: %s", type(exc).__name__)
            try:
                row = await _scalar_row(
                    conn, "SELECT COUNT(*) FROM face_profiles WHERE user_id = ? AND active = 1", (uid,))
                n = int(row[0] or 0) if row is not None else 0
                face = {"face_enrolled": n > 0, "face_samples": n}
            except Exception as exc:  # noqa: BLE001
                logger.warning("enrollment_state: face read failed: %s", type(exc).__name__)
            try:
                row = await _scalar_row(
                    conn, "SELECT last_offered_at, offer_count, pending_since, ignored_count, accepted_at, declined_at, declined_until "
                          "FROM enrollment_offers WHERE user_id = ? AND modality = ?", (uid, MODALITY_VOICE))
                if row is not None:
                    offer = {"last_offered_at": row[0], "offer_count": int(row[1] or 0), "pending_since": row[2],
                             "ignored_count": int(row[3] or 0), "accepted_at": row[4], "declined_at": row[5],
                             "declined_until": row[6]}
            except Exception as exc:  # noqa: BLE001 - pre-0046 database: no offers recorded yet
                logger.warning("enrollment_state: offers read failed: %s", type(exc).__name__)
    except Exception as exc:  # noqa: BLE001 - no connection at all: everything unknown
        logger.warning("enrollment_state: no connection: %s", type(exc).__name__)
    return SetupState(user_id=uid, **voice, **face, **offer)


# ── who ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────

async def _account_facts(user_id: str, db=None) -> tuple:
    """(minor flag from ``member_modes``, lower-cased ``auth_users.role`` or "") - raises on a failed lookup."""
    import persona_layer

    member = await persona_layer.load_member_mode(user_id, db=db)
    minor = bool(member.minor or member.mode in _minor_modes())
    async with persona_layer._db(db) as conn:
        row = await (await conn.execute("SELECT role FROM auth_users WHERE user_id = ?", (user_id,))).fetchone()
    return minor, (str(row[0] or "").strip().lower() if row else "")


def _minor_modes() -> frozenset:
    import manner_block

    return manner_block.MINOR_MODES


async def audience_of(user_id: str, db=None) -> str:
    """``guest`` | ``synthetic`` | ``adult`` | ``minor`` | ``unknown`` (a missing account, an unrecognised role, a failed lookup).
    The id string is read only for the guest/synthetic sentinels; an adult is POSITIVELY an adult."""
    from user_filters import GUEST_USERS, is_synthetic_user

    uid = (user_id or "").strip()
    if uid in GUEST_USERS or uid.lower().startswith(_GUEST_PREFIX):
        return "guest"
    if is_synthetic_user(uid):
        return "synthetic"
    try:
        import manner_block

        minor, role = await _account_facts(uid, db=db)
        if minor or role in manner_block.CHILD_ROLES:
            return "minor"
        return "adult" if role in manner_block.ADULT_ROLES else "unknown"
    except Exception as exc:  # noqa: BLE001 - not knowing who someone is means no offer
        logger.warning("enrollment_state: audience lookup failed: %s", type(exc).__name__)
        return "unknown"


# ── the offer policy (pure) ───────────────────────────────────────────────────────────────────────────────────────────────

MOMENTS = ("pin_stepup", "conversation_end")


@dataclass(frozen=True)
class OfferDecision:
    offer: bool
    reason: str


def decide_offer(state: SetupState, *, audience: str, moment: str, now: datetime, flag: str = "on", kids: bool = False,
                 quiet_hours: bool = False, gap_s: int = OFFER_GAP_S) -> OfferDecision:
    """May Zoe OFFER to learn this member's voice at this moment? The first rule that fails is the reason. Pure."""
    if flag not in ("on", "shadow"):
        return OfferDecision(False, "flag_off")
    if moment not in MOMENTS:
        return OfferDecision(False, "bad_moment")
    if audience in ("guest", "synthetic"):
        return OfferDecision(False, audience)
    if audience == "minor":
        if not kids:
            return OfferDecision(False, "minor_not_enabled")
    elif audience != "adult":
        return OfferDecision(False, "audience_unknown")
    if state.voice_enrolled is None:
        return OfferDecision(False, "state_unknown")
    if state.voice_enrolled:
        return OfferDecision(False, "already_enrolled")
    if state.declined_now(now):
        return OfferDecision(False, "declined")
    last = parse(state.last_offered_at)
    if last is not None and (now - last).total_seconds() < gap_s:
        return OfferDecision(False, "offered_recently")
    if quiet_hours:
        return OfferDecision(False, "quiet_hours")
    return OfferDecision(True, "ok")


def can_self_request(audience: str, *, kids: bool) -> OfferDecision:
    """May this member ASK for voice enrolment? Everything an offer needs about the PERSON, none of the timing."""
    if audience in ("guest", "synthetic"):
        return OfferDecision(False, audience)
    if audience == "minor":
        return OfferDecision(kids, "ok" if kids else "minor_not_enabled")
    return OfferDecision(audience == "adult", "ok" if audience == "adult" else "audience_unknown")


# ── writes ────────────────────────────────────────────────────────────────────────────────────────────────────────────────

async def claim_offer(user_id: str, state: SetupState, *, now: Optional[datetime] = None, db=None,
                      days: Optional[int] = None) -> bool:
    """Take the member's offer slot, atomically: True for exactly ONE caller per ``state`` read, so the PIN path and the
    end-of-conversation path can never both offer in the same hour. A previous offer still unanswered counts as ignored, and the
    third ignored offer is NOT made: it starts the same quiet period as a "no" instead. Never raises (False = not claimed = no offer)."""
    now = now or _now()
    uid = (user_id or "").strip()
    stamp = fmt(now)
    ignored = state.ignored_count + (1 if state.pending_since else 0)
    try:
        async with _conn(db) as conn:
            if ignored >= MAX_IGNORED:
                await conn.execute(
                    "UPDATE enrollment_offers SET declined_until = ?, ignored_count = 0, pending_since = NULL, updated_at = ? "
                    "WHERE user_id = ? AND modality = ?",
                    (fmt(now + timedelta(days=days or decline_days())), stamp, uid, MODALITY_VOICE))
                await conn.commit()
                return False
            # Optimistic: the UPDATE only matches while last_offered_at is still what THIS caller read, so of two racing callers one
            # matches and the other does not. A member with no row yet is INSERTed (ON CONFLICT DO NOTHING: the race is decided there).
            async with conn.execute(
                "UPDATE enrollment_offers SET last_offered_at = ?, offer_count = offer_count + 1, pending_since = ?, ignored_count = ?, "
                "updated_at = ? WHERE user_id = ? AND modality = ? AND COALESCE(last_offered_at, '') = ?",
                (stamp, stamp, ignored, stamp, uid, MODALITY_VOICE, state.last_offered_at or ""),
            ) as cur:
                won = getattr(cur, "rowcount", 0) == 1
            if not won and state.last_offered_at is None:
                async with conn.execute(
                    "INSERT INTO enrollment_offers (user_id, modality, last_offered_at, offer_count, pending_since, ignored_count, "
                    "updated_at) VALUES (?, ?, ?, 1, ?, 0, ?) ON CONFLICT (user_id, modality) DO NOTHING",
                    (uid, MODALITY_VOICE, stamp, stamp, stamp),
                ) as cur:
                    won = getattr(cur, "rowcount", 0) == 1
            await conn.commit()
        return won
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrollment_state: claim_offer failed: %s", type(exc).__name__)
        return False


async def release_offer(user_id: str, before: SetupState, *, now: Optional[datetime] = None, db=None) -> bool:
    """Undo ``claim_offer`` for an offer that was never made (the announcement could not be queued): put the offer columns back to what
    ``before`` read, so the failure is not counted as an offer, an ignored offer or a pending question. Optimistic like the claim - it only
    applies while ``last_offered_at`` is still the stamp this claim wrote - so it can never undo someone else's later claim. Never raises."""
    now = now or _now()
    uid = (user_id or "").strip()
    try:
        async with _conn(db) as conn:
            async with conn.execute(
                "UPDATE enrollment_offers SET last_offered_at = ?, offer_count = ?, pending_since = ?, ignored_count = ?, updated_at = ? "
                "WHERE user_id = ? AND modality = ? AND last_offered_at = ?",
                (before.last_offered_at, before.offer_count, before.pending_since, before.ignored_count, fmt(now), uid, MODALITY_VOICE,
                 fmt(now)),
            ) as cur:
                undone = getattr(cur, "rowcount", 0) == 1
            await conn.commit()
        return undone
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrollment_state: release_offer failed: %s", type(exc).__name__)
        return False


async def record_answer(user_id: str, accepted: bool, *, now: Optional[datetime] = None, db=None,
                        days: Optional[int] = None) -> bool:
    """The member answered (or asked): ``accepted`` clears the quiet period; a "no" starts it. Upsert; never raises."""
    now = now or _now()
    uid = (user_id or "").strip()
    stamp = fmt(now)
    until = None if accepted else fmt(now + timedelta(days=days or decline_days()))
    try:
        async with _conn(db) as conn:
            if accepted:
                sql = ("INSERT INTO enrollment_offers (user_id, modality, accepted_at, declined_at, declined_until, pending_since, "
                       "ignored_count, updated_at) VALUES (?, ?, ?, NULL, NULL, NULL, 0, ?) "
                       "ON CONFLICT (user_id, modality) DO UPDATE SET accepted_at = excluded.accepted_at, declined_at = NULL, "
                       "declined_until = NULL, pending_since = NULL, ignored_count = 0, updated_at = excluded.updated_at")
                params = (uid, MODALITY_VOICE, stamp, stamp)
            else:
                sql = ("INSERT INTO enrollment_offers (user_id, modality, declined_at, declined_until, pending_since, "
                       "ignored_count, updated_at) VALUES (?, ?, ?, ?, NULL, 0, ?) "
                       "ON CONFLICT (user_id, modality) DO UPDATE SET declined_at = excluded.declined_at, "
                       "declined_until = excluded.declined_until, pending_since = NULL, ignored_count = 0, "
                       "updated_at = excluded.updated_at")
                params = (uid, MODALITY_VOICE, stamp, until, stamp)
            await conn.execute(sql, params)
            await conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrollment_state: record_answer failed: %s", type(exc).__name__)
        return False
