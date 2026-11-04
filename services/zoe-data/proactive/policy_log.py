"""Proactive-speech decision table, LOG-ONLY (``ZOE_PROACTIVE_POLICY_LOG`` = off | log, default off, per-call read).

Samantha Initiative lane F E3 (docs/research/samantha-initiative-2026-11-03/lane-F-personality-proactive.md
section 3.3, record docs/knowledge/proactive-policy-log.md). For each candidate moment the household already has
(selector candidates, the night mind's one ``raise`` thread) it records WHICH RUNG the table would choose. It
never shows, says, sends or stores anything: no card, no dot, no speech, no DB write, no LLM call. The only
output is one log line per new decision, carrying a hashed member id, an item id, a coarse kind, a row number,
a rung and a fixed reason code - never an item's words, a name, or a sensitivity class's content.

``decide`` is pure. ``observe`` is the one seam (called from ``proactive.pull.pending_state``, the orb poll); it
reads signals that already exist (panel presence, quiet hours, recent voice activity, the candidate list) and
swallows every error. Off = returns before any I/O.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Rungs (lane F 3.2). DOT is "surfaces at the member's next pull / first turn" - the gentle line at the next turn.
SILENT, DOT, CARD, SPEAK_ON_PULL, SPEAK = "silent", "dot", "card", "speak_on_pull", "speak"
RUNGS = (SILENT, DOT, CARD, SPEAK_ON_PULL, SPEAK)

WEEK_CARD_CAP = 3
WEEK_DOT_CAP = 7
BUSY_TURN_AGE_S = 90.0     # a voice turn this recent = the member is mid-conversation
IGNORED_CLASS_LIMIT = 2    # row 12: two earlier items of the class raised and not taken up

# Candidate-kind vocabulary the table understands. Real signals today: open_loop, emotional, event, commitment,
# night_raise. The rest are for sources not yet built (the table is ready; nothing emits them).
KINDS = ("open_loop", "emotional", "event", "commitment", "night_raise", "change_notice", "inferred_fact",
         "date", "keep_in_touch", "routine", "reminder")


def mode() -> str:
    """``off`` (default; any value but ``log`` is off) | ``log``."""
    return "log" if (os.environ.get("ZOE_PROACTIVE_POLICY_LOG", "") or "").strip().lower() == "log" else "off"


@dataclass(frozen=True)
class Signals:
    kind: str
    verified: bool = True              # speaker/member identity confirmed
    guest_present: bool = False        # kiosk guest holds the panel
    deny_class: bool = False           # a restraint class (health, money, grief, ...) or a "don't bring up" mute
    requested_time: bool = False       # the member asked for this at a time (reminder / timer / alarm)
    busy: bool = False                 # mid-turn / in a call
    rest_window: bool = False          # quiet hours
    panel_near: bool = False           # the member's own fresh panel session
    ignored_in_class: int = 0
    week_cards: int = 0
    week_dots: int = 0
    date_is_today: bool = False        # row 8: card on the day
    cadence_opt_in: bool = False       # row 9
    routine_hints_enabled: bool = False  # row 11


@dataclass(frozen=True)
class Decision:
    row: int
    rung: str
    action: str   # "none" | "hold" | "suppress" | "surface" | "speak"
    reason: str   # fixed code, never free text


def decide(s: Signals) -> Decision:
    """The lane F 3.3 table. Rows 1-4 gate first (first match wins); rows 5-11 and 14 pick a rung; rows 12 and 13
    (back-off, weekly cap) then DOWNGRADE a surfacing pick, since evaluated strictly in order they could never
    fire after an earlier row had matched."""
    if s.guest_present or not s.verified:
        return Decision(1, SILENT, "none", "guest_or_unverified")
    if s.deny_class or s.kind == "emotional":
        return Decision(2, SILENT, "none", "deny_list_class")
    if s.requested_time or s.kind == "reminder":
        return Decision(3, SPEAK, "speak", "member_requested_time")
    if s.busy:
        return Decision(4, SILENT, "hold", "hold_mid_task")
    if s.rest_window:
        return Decision(4, SILENT, "hold", "hold_rest_window")

    if s.kind in ("change_notice", "event"):
        row, rung, why = 5, (CARD if s.panel_near else DOT), "owned_change_notice"
    elif s.kind == "night_raise":
        row, rung, why = 6, DOT, "night_mind_raise_pull_only"
    elif s.kind == "inferred_fact":
        row, rung, why = 7, CARD, "approve_before_write_weekly"
    elif s.kind == "date":
        row, rung, why = 8, (CARD if (s.date_is_today and s.panel_near) else DOT), "family_date_lead_time"
    elif s.kind == "keep_in_touch":
        row, rung, why = (9, DOT, "cadence_opt_in") if s.cadence_opt_in else (9, SILENT, "no_cadence_opt_in")
    elif s.kind == "open_loop":
        row, rung, why = 10, (CARD if s.panel_near else DOT), "member_open_loop_due"
    elif s.kind == "routine":
        row, rung, why = (11, DOT, "routine_hint_enabled") if s.routine_hints_enabled else (11, SILENT, "routine_hints_off")
    else:
        return Decision(14, SILENT, "none", "silence_wins")

    if rung == SILENT:
        return Decision(row, SILENT, "none", why)
    if s.ignored_in_class >= IGNORED_CLASS_LIMIT:
        return Decision(12, SILENT, "suppress", "class_backoff")
    if (rung == CARD and s.week_cards >= WEEK_CARD_CAP) or (rung == DOT and s.week_dots >= WEEK_DOT_CAP):
        return Decision(13, SILENT, "hold", "weekly_cap")
    return Decision(row, rung, "surface", why)


# ── the seam ─────────────────────────────────────────────────────────────────
_seen: set[tuple[str, str, str, str]] = set()       # (member hash, item id, row:rung:action, local day)
_week: dict[tuple[str, str], dict[str, int]] = {}   # (member hash, iso week) -> {"card": n, "dot": n}; in-memory, resets on restart
_SEEN_MAX = 4096


def _h(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:8]


def _reset_state() -> None:
    _seen.clear()
    _week.clear()


async def _tier(uid: str) -> str:
    from proactive.presence import TIER_ABSENT, panel_presence_tier

    try:
        return (await panel_presence_tier(uid))[0]
    except Exception:  # noqa: BLE001
        return TIER_ABSENT


def _busy() -> bool:
    try:
        from voice_activity import last_activity_age_s

        age = last_activity_age_s()
        return age is not None and age < BUSY_TURN_AGE_S
    except Exception:  # noqa: BLE001
        return False


async def _night_raise_refs(uid: str, now: datetime) -> list[str]:
    """The night mind's ONE ``raise`` thread as a source_ref (never its text); [] when the night mind is off."""
    try:
        import night_mind

        return [str(i.get("source_ref") or "") for i in await night_mind.morning_items(uid, now=now) if i.get("source_ref")]
    except Exception:  # noqa: BLE001
        return []


async def observe(uid: str, items: list, now: datetime | None = None) -> dict | None:
    """Record the rung the table would choose for each of the member's pending candidates (+ the night raise).
    Log-only: returns a counts dict for tests; ``None`` when off. Never raises, never delivers anything."""
    if mode() != "log":
        return None
    try:
        return await _observe(uid, items, now or datetime.now(timezone.utc))
    except Exception as exc:  # noqa: BLE001
        logger.debug("proactive-policy: observe skipped (%s)", type(exc).__name__)
        return None


async def _observe(uid: str, items: list, now: datetime) -> dict:
    from proactive.presence import TIER_BOUND_GUEST, TIER_OWNER
    from proactive.pull import _member, _quiet
    from time_utils import zoe_timezone

    verified = _member(uid)          # a guest sentinel / unknown id is row 1, whatever the panel says
    tier = await _tier(uid)
    busy, rest = _busy(), _quiet(now)
    refs = await _night_raise_refs(uid, now)
    me = _h(uid)
    local = now.astimezone(zoe_timezone())
    day = local.date().isoformat()
    iso = local.isocalendar()
    wk = (me, f"{iso[0]}-{iso[1]}")
    counts = {r: 0 for r in RUNGS}
    ignored: dict[str, int] = {}
    for i in items:
        if getattr(i, "surfaced", 0) >= 1:
            ignored[i.kind] = ignored.get(i.kind, 0) + 1
    rows = [(i.id, i.kind, bool(i.sensitivity)) for i in items] + [(f"night:{_h(r)}", "night_raise", False) for r in refs]
    for item_id, kind, sensitive in rows:
        kind = kind if kind in KINDS else "other"
        tally = _week.setdefault(wk, {"card": 0, "dot": 0})
        d = decide(Signals(
            kind=kind, verified=verified, guest_present=tier == TIER_BOUND_GUEST, deny_class=sensitive, busy=busy, rest_window=rest,
            panel_near=tier == TIER_OWNER, ignored_in_class=ignored.get(kind, 0),
            week_cards=tally["card"], week_dots=tally["dot"],
        ))
        counts[d.rung] += 1
        key = (me, item_id, f"{d.row}:{d.rung}:{d.action}", day)
        if key in _seen:
            continue
        if len(_seen) >= _SEEN_MAX:
            _seen.clear()
        _seen.add(key)
        if d.action == "surface" and d.rung in ("card", "dot"):
            tally[d.rung] += 1
        logger.info("PROACTIVE_POLICY user=%s item=%s kind=%s row=%d rung=%s reason=%s",
                    me, item_id, kind, d.row, d.rung, d.reason)
    logger.info("PROACTIVE_POLICY_PASS user=%s n=%d silent=%d dot=%d card=%d speak=%d",
                me, len(rows), counts[SILENT], counts[DOT], counts[CARD], counts[SPEAK] + counts[SPEAK_ON_PULL])
    return counts
