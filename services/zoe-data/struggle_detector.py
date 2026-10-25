"""The STRUGGLE DETECTOR (Phase 1 of the self-correction loop): notice the turns where Zoe visibly struggled, record them
(the read side - clusters, endpoint, CLI, nightly line - is ``struggle_digest.py``). Signals are ``struggle_signals.py``
(pure); this module is the runtime around them.

``ZOE_STRUGGLE_DETECT`` = off | **shadow (default)** | on, read per call.
  * off    - NO detector machinery runs: no queue, no worker, no route/brain notes, no rows, no report reads. The hooks
             still return after one env read, bumping only the in-process ``observed`` / ``dropped_off`` counters.
  * shadow - detect + store + report (admin endpoint, CLI, nightly ``STRUGGLE_SUMMARY`` line). NOTHING user-facing changes.
  * on     - shadow, plus the two user-facing pieces: a one-line acknowledgement of a bare "that was wrong" that no other tier
             answered (``ack_line``) and the owner's struggles line in the admin morning brief.

OFF THE TURN PATH, by construction:
  * The hooks (``observe``) are called from the turn's EXISTING post-turn background tasks
    (``routers/voice_tts._run_voice_memory_passes``, ``routers/chat._persist_memory_candidates``), never from the reply path. A
    hook is one ``put_nowait`` on a bounded queue; a full queue drops the turn and counts it. Every exception is swallowed and
    counted (``stats()``); a detector bug can never break or slow a turn.
  * In-process notes feed the turn record: ``note_route`` (``semantic_router.route``: the router's domain / score /
    two-stage failure), ``note_skybridge`` (Skybridge's claim and whether the router vetoed it), ``note_brain``
    (``brain_dispatch._log_lane``: a brain lane served this session) and ``note_snap`` (``stt_snap`` via the voice lane: a
    misheard command word was snapped, or would have been). Each is a deque
    append - measured in ``tests/test_struggle_detector.py`` (``test_hook_overhead``).
  * One worker task drains the queue: builds the ``Turn``, runs ``struggle_signals.detect`` against the previous turn of the
    same conversation (voice: the panel; chat: the session) by the same user, and writes the events. A store failure is
    counted, never retried on the turn's time.

PRIVACY (same rules as the transcript the turn already lives in, ``chat_messages``):
  * a member's row may carry ``summary`` = the display-form (lower case, unfolded), raw-scrubbed user words (<= 160 chars) - never Zoe's reply;
  * a GUEST row (and an off-the-record / distress turn, and a turn that names a forgotten entity) carries no words at all,
    only categories (signal, domain, the text-free ``pattern`` shape, numbers);
  * the digest shows example words only from the VIEWER's own rows - a member's words never reach another member's report;
  * ``erase_entity`` (the forget path) deletes the user's rows whose summary names the entity, ``delete_user`` (the audited
    right-to-be-forgotten) every row of the user - both RAISE on a store failure so the forget is not confirmed - and both first
    take those rows out of the Phase 2 proposals (``struggle_proposals.forget_events`` / ``forget_user``);
  * rows older than ``ZOE_STRUGGLE_RETENTION_DAYS`` (default 90) are swept by the worker at most once an hour.

Synthetic traffic is never recorded: the replay harness panel (``replay-*``) and synthetic users (``user_filters``).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import OrderedDict, deque
from typing import Any, Optional

import struggle_signals as sig
import traffic_class

logger = logging.getLogger(__name__)

ENV = "ZOE_STRUGGLE_DETECT"
MODES = ("off", "shadow", "on")
_OFF = frozenset({"off", "0", "false", "no", "disabled"})
_ON = frozenset({"on", "1", "true", "yes", "enforce"})

GUEST_IDS = frozenset({"", "guest", "anonymous", "voice-guest", "voice-daemon"})
SUMMARY_MAX = 160
NOTE_TTL_S = 300.0
HISTORY_TTL_S = 600.0
HISTORY_MAX = 512
#: the user's words / the reply as the worker sees them (a pasted blob must not make string matching expensive on the loop;
#: VERBOSE needs only the reply's length up to a few budgets)
TEXT_CAP = 600
REPLY_CAP = 3000
SWEEP_EVERY_S = 3600.0
STORE_TIMEOUT_S = 5.0
#: panels whose traffic is a harness or a test, never a household conversation
SYNTHETIC_PANELS = ("replay", "test", "probe", "ci-", "e2e", "bench")
ACK_LINE = "Sorry about that. I've made a note of it."

#: counters (process lifetime): observed / enqueued / dropped_off / dropped_full / skipped_synthetic / skipped_traffic /
#: processed / events /
#: hook_errors / process_errors / store_errors / sweeps / swept
_COUNTERS: dict[str, int] = {}


def _count(name: str, n: int = 1) -> None:
    _COUNTERS[name] = _COUNTERS.get(name, 0) + n


def stats() -> dict[str, Any]:
    """The detector's counters, queue depth and mode (for the admin endpoint and tests)."""
    q = _STATE.get("queue")
    return {"mode": mode(), "queue_depth": q.qsize() if q is not None else 0, "queue_max": queue_max(),
            **{k: _COUNTERS.get(k, 0) for k in ("observed", "enqueued", "dropped_off", "dropped_full",
                                                 "skipped_synthetic", "skipped_traffic", "processed", "events",
                                                 "hook_errors",
                                                 "process_errors", "store_errors", "sweeps", "swept")}}


# ── configuration (per-call env reads) ───────────────────────────────────────

def mode() -> str:
    """``off`` | ``shadow`` (default: unset or unrecognised) | ``on``."""
    import os

    raw = (os.environ.get("ZOE_STRUGGLE_DETECT") or "").strip().lower()
    if raw in _OFF:
        return "off"
    if raw in _ON:
        return "on"
    return "shadow"


def window_s() -> float:
    from typed_env import env_float

    return max(5.0, min(300.0, env_float("ZOE_STRUGGLE_WINDOW_S", sig.DEFAULT_WINDOW_S)))


def retention_days() -> int:
    from typed_env import env_int

    return max(1, env_int("ZOE_STRUGGLE_RETENTION_DAYS", 90))


def queue_max() -> int:
    from typed_env import env_int

    return max(8, env_int("ZOE_STRUGGLE_QUEUE_MAX", 256))


def is_guest(user_id: Optional[str]) -> bool:
    u = (user_id or "").strip().lower()
    return u in GUEST_IDS or u.startswith("guest-")


# ── in-process notes (the router's and the brain's view of a turn) ───────────

_ROUTES: "deque[tuple[float, str, dict]]" = deque(maxlen=64)
_BRAINS: "deque[tuple[float, str, str, str]]" = deque(maxlen=64)
_SKY: "deque[tuple[float, str, str, bool]]" = deque(maxlen=64)
_SNAPS: "deque[tuple[float, str, dict]]" = deque(maxlen=32)


def note_route(text: str, decision: Optional[dict], *, two_stage_active: bool = False,
               two_stage_failed: bool = False) -> None:
    """``semantic_router.route`` decided ``text``: keep the domain, score and two-stage outcome for the turn record. O(1),
    never raises."""
    try:
        if not decision or mode() == "off":
            return
        ts = decision.get("two_stage") if isinstance(decision.get("two_stage"), dict) else None
        _ROUTES.append((time.time(), str(text or "")[:500], {
            "domain": str(decision.get("domain") or ""),
            "score": decision.get("score"),
            "two_stage_domain": str((ts or {}).get("domain") or ""),
            "two_stage_conf": (ts or {}).get("head_conf"),
            "two_stage_active": bool(two_stage_active),
            "two_stage_failed": bool(two_stage_failed),
            "head_top": str((ts or {}).get("head_top") or ""),
            # the similarity view (the head's own domain replaces ``domain`` when it decided)
            "sim_scores": {str(k): float(v) for k, v in list((decision.get("scores") or {}).items())[:24]
                           if isinstance(v, (int, float))},
        }))
    except Exception:  # noqa: BLE001
        _count("hook_errors")


def note_skybridge(text: str, domain: str, action: str, decision: str) -> None:
    """Skybridge classified ``text`` as ``domain``/``action`` and the router gate said ``decision`` (allow / veto). O(1),
    never raises (``skybridge_service.resolve_skybridge_request``)."""
    try:
        if mode() == "off" or not domain:
            return
        _SKY.append((time.time(), str(text or "")[:500], str(domain), str(decision or "") == "veto"))
    except Exception:  # noqa: BLE001
        _count("hook_errors")


def note_brain(session_id: str, served: str, outcome: str) -> None:
    """A brain lane answered (``brain_dispatch``'s one ``BRAIN_LANE`` line per turn). O(1), never raises."""
    try:
        if not session_id or mode() == "off":
            return
        _BRAINS.append((time.time(), str(session_id), str(served or ""), str(outcome or "")))
    except Exception:  # noqa: BLE001
        _count("hook_errors")


def note_snap(text: str, info: dict) -> None:
    """``stt_snap`` recovered (or, in shadow, would have recovered) a misheard command word on the turn whose words are now
    ``text``: the turn gets a MISHEAR event with fix ``stt_snapped``. ``info`` is text-free (outcome / intent tag / safety /
    cost / position). O(1), never raises."""
    try:
        if mode() == "off":
            return
        keep = ("outcome", "intent", "safety", "cost", "position")
        _SNAPS.append((time.time(), str(text or "")[:500], {k: info.get(k) for k in keep if k in (info or {})}))
    except Exception:  # noqa: BLE001
        _count("hook_errors")


#: (ts, session_id, user_id, kind, name): what EXECUTED a turn (``ZOE_STRUGGLE_OUTCOMES`` only) - a fast tier (``fast_tiers.resolve``),
#: an intent executor (``intent_router.execute_intent``, which knows the user but not the session) or a brain tool call (``zoe_flue_client``)
_HANDLERS: "deque[tuple[float, str, str, str, str]]" = deque(maxlen=128)


def outcomes_on() -> bool:
    try:
        import turn_outcomes

        return turn_outcomes.enabled()
    except Exception:  # noqa: BLE001
        return False


def note_handler(session_id: str, user_id: str, kind: str, name: str) -> None:
    """``kind`` (tier / intent / skybridge / tool) ``name`` ran for this session (or, with no session, this user). O(1), never raises;
    nothing at all unless ``ZOE_STRUGGLE_OUTCOMES`` is on."""
    try:
        if not name or mode() == "off" or not outcomes_on():
            return
        _HANDLERS.append((time.time(), str(session_id or ""), str(user_id or ""), str(kind or "")[:12], str(name)[:60]))
    except Exception:  # noqa: BLE001
        _count("hook_errors")


def note_tool(session_id: str, name: str) -> None:
    """The brain called the tool ``name`` in this session (a ``__TOOL__`` phase=start sentinel). O(1), never raises."""
    note_handler(session_id, "", "tool", name)


def _handlers_for(session_id: str, user_id: str, since: float, now: float) -> tuple[str, list[str]]:
    """(the last non-tool handler as ``kind:name``, the brain's tool names in call order) of the turn that ran from ``since`` to ``now``
    in ``session_id`` (or, for a session-less note, by ``user_id``). The notes are consumed: one note belongs to one turn; stale ones go."""
    handler, tools, keep = "", [], []
    for item in list(_HANDLERS):
        ts, sid, uid, kind, name = item
        mine = (sid and sid == session_id) or (not sid and uid and uid == user_id)
        if mine and since - 0.5 <= ts <= now + 0.5:
            if kind == "tool":
                tools.append(name)
            else:
                handler = f"{kind}:{name}"
        elif now - ts <= NOTE_TTL_S:
            keep.append(item)
    _HANDLERS.clear()
    _HANDLERS.extend(keep)
    return handler, tools


def _snap_for(text: str, now: float) -> dict:
    raw = (text or "").strip()
    for i in range(len(_SNAPS) - 1, -1, -1):
        ts, t, info = _SNAPS[i]
        if now - ts > NOTE_TTL_S:
            break
        if t.strip() == raw:
            try:
                del _SNAPS[i]    # one snap belongs to one turn
            except IndexError:
                pass
            return info
    return {}


def _route_for(text: str, now: float) -> tuple[Optional[float], dict]:
    norm = sig.normalise(text)
    raw = (text or "").strip()
    for ts, t, rec in reversed(_ROUTES):
        if now - ts > NOTE_TTL_S:
            break
        if t.strip() == raw or (norm and sig.normalise(t) == norm):
            return ts, rec
    return None, {}


def _sky_for(text: str, now: float) -> tuple[str, bool]:
    norm = sig.normalise(text)
    raw = (text or "").strip()
    for ts, t, dom, vetoed in reversed(_SKY):
        if now - ts > NOTE_TTL_S:
            break
        if t.strip() == raw or (norm and sig.normalise(t) == norm):
            return dom, vetoed
    return "", False


def _brain_for(session_id: str, since: float, now: float) -> Optional[tuple[str, str]]:
    for i in range(len(_BRAINS) - 1, -1, -1):
        ts, sid, served, outcome = _BRAINS[i]
        if now - ts > NOTE_TTL_S:
            break
        if sid == session_id and since - 0.5 <= ts <= now + 0.5:
            try:
                del _BRAINS[i]    # one brain answer belongs to one turn
            except IndexError:
                pass
            return served, outcome
    return None


# ── the hook ─────────────────────────────────────────────────────────────────

_STATE: dict[str, Any] = {"queue": None, "loop": None, "worker": None, "last_sweep": 0.0}


def _queue() -> "asyncio.Queue":
    loop = asyncio.get_running_loop()
    q = _STATE.get("queue")
    if q is None or _STATE.get("loop") is not loop:
        q = asyncio.Queue(maxsize=queue_max())
        _STATE.update(queue=q, loop=loop, worker=None)
    w = _STATE.get("worker")
    if w is None or w.done():
        # A process-lifetime worker, born inside whichever request queued first: start it from an EMPTY context so
        # it never inherits that request's traffic class (traffic_class.detached_task).
        _STATE["worker"] = traffic_class.detached_task(loop, _worker(q), name="struggle-detector")
    return q


def observe(channel: str, user_text: str, reply: str, user_id: str, session_id: str = "", *, panel_id: str = "",
            interrupted: bool = False, speaker_verified: Optional[bool] = None) -> bool:
    """THE post-turn hook: queue one finished exchange for detection. True when queued. Synchronous, O(1), NEVER raises.

    Call it from a turn's post-turn background work, never before the reply is out. A voice turn whose speaker the gate
    REJECTED (``speaker_verified=False``) is recorded as a guest's (categories, no words): it is not provably the member's.
    The texts are capped (``TEXT_CAP`` / ``REPLY_CAP``) so the worker's string work stays bounded on the event loop."""
    try:
        _count("observed")
        if mode() == "off":
            _count("dropped_off")
            return False
        pid = panel_id or _panel_from_session(session_id)
        traffic = _traffic(user_id, pid)
        uid = "guest" if speaker_verified is False else str(user_id or "")
        if traffic != "household":
            _count("skipped_synthetic" if (pid.startswith(SYNTHETIC_PANELS) or _synthetic(user_id)) else "skipped_traffic")
            if outcomes_on():   # a text-free outcome row (the traffic tag is the point): never detected, never in a pair, no words
                # The words ride the in-memory queue item ONLY so the route / latency / can't-do facts can be derived from them (the
                # route note is found by its text; the outcome class reads the reply). They are never stored: ``turn_outcomes.new_row``
                # keeps a non-household row text-free whatever it is given, and a non-household item never reaches history or events.
                try:
                    _queue().put_nowait((time.time(), str(channel or "chat"), str(user_text or "")[:TEXT_CAP],
                                         str(reply or "")[:REPLY_CAP], uid, str(session_id or ""), pid, bool(interrupted), traffic))
                except asyncio.QueueFull:
                    _count("dropped_full")
            return False
        item = (time.time(), str(channel or "chat"), str(user_text or "")[:TEXT_CAP], str(reply or "")[:REPLY_CAP], uid,
                str(session_id or ""), pid, bool(interrupted), traffic)
        q = _queue()
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            _count("dropped_full")
            return False
        _count("enqueued")
        return True
    except Exception:  # noqa: BLE001 - a detector bug never reaches the turn
        _count("hook_errors")
        return False


FEEDBACK_ITEM = "__feedback__"


def observe_feedback(user_id: str, interaction_id: str, kind: str) -> bool:
    """The thumbs-down / correction BUTTON (``routers/chat.submit_feedback``): queue it for the worker, which marks the rated turn's outcome
    row (``turn_outcomes.note_feedback``) - the worker stays the one writer of those rows. ``ZOE_STRUGGLE_OUTCOMES`` on only. O(1), never
    raises."""
    try:
        if mode() == "off" or not outcomes_on() or kind not in ("thumbs_down", "correction"):
            return False
        _queue().put_nowait((FEEDBACK_ITEM, str(user_id or ""), str(interaction_id or ""), str(kind)))
        return True
    except Exception:  # noqa: BLE001 - QueueFull included
        _count("hook_errors")
        return False


def _panel_from_session(session_id: str) -> str:
    """``voice-panel-<panel>-<hex8>`` -> ``<panel>``."""
    sid = session_id or ""
    if sid.startswith("voice-panel-"):
        rest = sid[len("voice-panel-"):]
        return rest.rsplit("-", 1)[0] if "-" in rest else rest
    return ""


def _traffic(user_id: str, panel_id: str) -> str:
    """``household`` | ``harness`` | ``probe`` | ``ci`` (``traffic_class``, P-2): a synthetic user or harness panel forces its class (the
    detector's old rule), else the SERVER-derived class of the turn being handled (bound by the request middleware). Never a client claim."""
    uid = str(user_id or "")
    if panel_id.startswith(SYNTHETIC_PANELS) or _synthetic(uid):
        return (traffic_class.class_for_user(uid) or traffic_class.class_for_panel(panel_id)
                or traffic_class.HARNESS)
    try:
        return traffic_class.current()
    except Exception:  # noqa: BLE001 - when in doubt it is NOT household: harness rows never train
        return traffic_class.HARNESS


def _synthetic(user_id: str) -> bool:
    if is_guest(user_id):
        return False
    try:
        from user_filters import is_synthetic_user

        return bool(is_synthetic_user(user_id))
    except Exception:  # noqa: BLE001
        return False


async def drain(timeout: float = 5.0) -> None:
    """Wait until every queued turn is processed (tests, the CLI)."""
    q = _STATE.get("queue")
    if q is not None and _STATE.get("loop") is asyncio.get_running_loop():
        await asyncio.wait_for(q.join(), timeout)


def reset() -> None:
    """Drop all in-process state (tests)."""
    w = _STATE.get("worker")
    if w is not None and not w.done():
        try:
            w.cancel()
        except RuntimeError:  # its loop is already closed: nothing left to run it
            pass
    _STATE.update(queue=None, loop=None, worker=None, last_sweep=0.0)
    _ROUTES.clear()
    _BRAINS.clear()
    _SKY.clear()
    _SNAPS.clear()
    _HANDLERS.clear()
    _HISTORY.clear()
    _OFF_RECORD_AT_TURN.clear()
    _COUNTERS.clear()
    _ERASED_AT.clear()
    _DELETED_AT.clear()
    _USER_LOCKS.clear()


# ── the worker ───────────────────────────────────────────────────────────────

#: conv key -> the last turn seen there (bounded, TTL'd): what a pair signal compares against
_HISTORY: "OrderedDict[str, list[sig.Turn]]" = OrderedDict()
#: turns kept per conversation: the previous one (pair rules) + the look-back (``struggle_signals.LOOKBACK_TURNS``)
HISTORY_TURNS = sig.LOOKBACK_TURNS + 1
#: turn_id -> True for a turn that was off the record (or undecidable) AT TURN TIME. memory_provenance's mark expires (~900 s), so a
#: LATE rating must never re-derive this from the expiring mark: the status is captured once, here, and carried with the history.
_OFF_RECORD_AT_TURN: "dict[str, bool]" = {}
#: a rating arriving later than this after the turn gets no words (matches memory_provenance's off-the-record mark lifetime)
WORDS_TTL_S = 900.0


def _off_record_now(turn: sig.Turn) -> bool:
    """Is this turn off the record / distress right now? Any doubt (import or lookup failing) = True (fail closed)."""
    try:
        import memory_provenance

        return bool(memory_provenance.is_off_record(turn.user_id, turn.text))
    except Exception:  # noqa: BLE001
        return True


async def _worker(q: "asyncio.Queue") -> None:
    while True:
        item = await q.get()
        try:
            await process(item)
            _count("processed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _count("process_errors")
            logger.debug("struggle_detector: process failed (%s)", type(exc).__name__)
        finally:
            q.task_done()


def build_turn(item: tuple) -> sig.Turn:
    ts, channel, text, reply, user_id, session_id, panel_id, interrupted = item[:8]
    panel_id = panel_id or _panel_from_session(session_id)
    route_ts, route = _route_for(text, ts)
    brain = _brain_for(session_id, route_ts if route_ts is not None else ts - 120.0, ts)
    guest = is_guest(user_id)
    conv = f"{channel}:{panel_id}" if channel == "voice" and panel_id else f"{channel}:{session_id or user_id}"
    domain = route.get("two_stage_domain") or route.get("domain") or ""
    score = route.get("score")
    sky_domain, sky_vetoed = _sky_for(text, ts)
    return sig.Turn(
        ts=ts, channel=channel, user_id=("guest" if guest else user_id), is_guest=guest, conv_key=conv,
        turn_id="t-" + hashlib.sha1(f"{user_id}|{session_id}|{ts:.3f}|{text}".encode("utf-8")).hexdigest()[:16],
        session_id=session_id, panel_id=panel_id, text=text, reply=reply,
        path=sig.PATH_BRAIN if brain else sig.PATH_FAST, lane=(brain or ("", ""))[0], lane_outcome=(brain or ("", ""))[1],
        predicted_domain=domain, predicted_score=float(score) if isinstance(score, (int, float)) else None,
        two_stage_domain=route.get("two_stage_domain") or "",
        two_stage_conf=(float(route["two_stage_conf"]) if isinstance(route.get("two_stage_conf"), (int, float)) else None),
        two_stage_active=bool(route.get("two_stage_active")),
        two_stage_failed=bool(route.get("two_stage_failed")),
        latency_ms=int((ts - route_ts) * 1000) if route_ts is not None else None, interrupted=bool(interrupted),
        started=route_ts, sim_scores=dict(route.get("sim_scores") or {}), head_top=str(route.get("head_top") or ""),
        sky_domain=sky_domain, sky_vetoed=sky_vetoed, stt_snap=_snap_for(text, ts) or None)


async def process(item: tuple) -> list[sig.Event]:
    """Detect and store the struggles of one queued turn. Returns the events (tests)."""
    if item and item[0] == FEEDBACK_ITEM:
        import turn_outcomes

        async with _user_lock(item[1]):
            if not _erased_since(item[1], time.time(), _DELETED_AT):
                await turn_outcomes.note_feedback(item[1], item[2], item[3])
        return []
    cur = build_turn(item)
    if _erased_since(cur.user_id, cur.ts, _DELETED_AT):
        return []          # the user was deleted after this turn was queued: it is not recorded at all
    traffic = item[8] if len(item) > 8 else "household"
    if traffic != "household":
        if outcomes_on():
            await _record_outcome(cur, [], str(item[4] or ""), traffic)
        return []
    for k in [k for k, h in _HISTORY.items() if not h or cur.ts - h[-1].ts > HISTORY_TTL_S]:
        _HISTORY.pop(k, None)
    history = list(_HISTORY.get(cur.conv_key) or [])
    _HISTORY[cur.conv_key] = (history + [cur])[-HISTORY_TURNS:]
    _HISTORY.move_to_end(cur.conv_key)
    while len(_HISTORY) > HISTORY_MAX:
        _HISTORY.popitem(last=False)
    if _off_record_now(cur):
        _OFF_RECORD_AT_TURN[cur.turn_id] = True
    if _OFF_RECORD_AT_TURN:
        live = {t.turn_id for h in _HISTORY.values() for t in h}
        for tid in [k for k in _OFF_RECORD_AT_TURN if k not in live]:
            _OFF_RECORD_AT_TURN.pop(tid, None)
    events = sig.detect_window(history, cur, window_s=window_s())
    if events:
        _count("events", len(events))
        async with _user_lock(cur.user_id):
            if not _erased_since(cur.user_id, cur.ts, _DELETED_AT):
                try:
                    rows = [await _row(e) for e in events]
                    await asyncio.wait_for(get_backend().add(rows), STORE_TIMEOUT_S)
                except Exception as exc:  # noqa: BLE001 - DB down / slow: counted, the turn already happened
                    _count("store_errors")
                    logger.warning("struggle_detector: event store failed (%s) events=%d channel=%s", type(exc).__name__,
                                   len(events), cur.channel)
        for e in events:
            logger.info("STRUGGLE signal=%s conf=%.2f fix=%s domain=%s path=%s channel=%s user=%s mode=%s",
                        e.signal, e.confidence, e.fix_type, e.about.domain() or "-", e.about.path, cur.channel,
                        "guest" if cur.is_guest else cur.user_id, mode())
    if outcomes_on():
        await _record_outcome(cur, events, str(item[4] or ""), traffic)
    await _maybe_sweep(cur.ts)
    return events


async def _words_of(turn: sig.Turn, at: float) -> str:
    """The failed turn's OWN words for its outcome row - the rules of an event's ``summary`` (a member, words that may be kept, scrubbed
    RAW, display form) - or ''."""
    if turn.is_guest or _erased_since(turn.user_id, at, _ERASED_AT) or not await _keep_words_bounded(turn, turn.text):
        return ""
    scrubbed = _scrub(turn.text)
    return sig.display(scrubbed)[:SUMMARY_MAX] if scrubbed is not None else ""


async def words_for_turn(user_id: str, turn_id: str) -> str:
    """A recent turn's own words for its outcome row, when a rating arrives AFTER the turn was recorded (the thumbs-down button): only
    while the turn is still in the in-process history (``HISTORY_TTL_S``), under the same keep-words rules. '' otherwise. The CALLER holds
    the user's lock across this read and the write of the words (``process``'s feedback branch)."""
    for turns in list(_HISTORY.values()):
        for t in turns:
            if t.turn_id == turn_id and t.user_id == user_id:
                now = time.time()
                if _OFF_RECORD_AT_TURN.get(t.turn_id) or now - t.ts > WORDS_TTL_S:
                    return ""      # off the record AT TURN TIME (never re-derived from the expiring mark), or too old
                return await _words_of(t, now)
    return ""


async def _message_id_for(cur: sig.Turn) -> str:
    """The id of the assistant message this turn's reply was saved as (``chat_messages``), '' when it is not found (not saved yet, a
    guest, a failed save). Matched on the reply's own text among the session's newest assistant rows; the rating button later finds the
    outcome row by this id alone."""
    reply = (cur.reply or "").strip()
    if not reply or cur.is_guest or not cur.session_id:
        return ""

    async def _look() -> str:
        from db_pool import get_db_ctx

        async with get_db_ctx() as db:
            c = await db.execute("SELECT id, content FROM chat_messages WHERE session_id = ? AND role = 'assistant' "
                                 "ORDER BY created_at DESC LIMIT 5", (cur.session_id,))
            for mid, content in await c.fetchall():
                if (content or "").strip().startswith(reply):
                    return str(mid)
        return ""

    try:
        return await asyncio.wait_for(_look(), STORE_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - no id = a later rating of this reply is simply not attached (never a neighbouring turn)
        return ""


async def _record_outcome(cur: sig.Turn, events: list, raw_user: str, traffic: str) -> None:
    """``ZOE_STRUGGLE_OUTCOMES`` on: write ``cur``'s outcome row, and mark the earlier turn a pair event is ABOUT as failed (with its own
    words and the route the follow-up proved). Every failure is counted; nothing raises. The words check through the writes run under the
    user's lock, so a forget / user delete cannot slip between them."""
    try:
        import turn_outcomes as to

        handler, tools = _handlers_for(cur.session_id, raw_user, cur.started if cur.started is not None else cur.ts - 120.0, cur.ts)
        own = [e for e in events if e.about is cur]
        outcome = to.outcome_of(cur, own)
        signals = [e.signal for e in own]
        label = next((lab for lab in (to.proposed_label(e, {}) for e in own) if lab[0]), ("", ""))
        household = traffic == "household"
        message_id = await _message_id_for(cur) if household else ""
        async with _user_lock(cur.user_id):
            if _erased_since(cur.user_id, cur.ts, _DELETED_AT):
                return
            words = await _words_of(cur, cur.ts) if household and to.failing(signals, outcome) else ""
            await to.record(to.new_row(cur, traffic=traffic, outcome=outcome, handler=handler, tools=tools, signals=signals,
                                       label=label, summary=words, mode=mode(), message_id=message_id))
            if not household:
                return
            for e in events:
                about = e.about
                if about is cur:
                    continue
                words = await _words_of(about, cur.ts) if about.user_id == cur.user_id and not about.is_guest else ""
                await to.mark_failed(about.user_id, about.turn_id, [e.signal], to.proposed_label(e, _follow_up(e)), words)
    except Exception as exc:  # noqa: BLE001
        _count("process_errors")
        logger.warning("struggle_detector: outcome record failed (%s) channel=%s traffic=%s", type(exc).__name__, cur.channel, traffic)


async def _may_keep_words(turn: sig.Turn, *texts: str) -> bool:
    """Words are kept only where the transcript keeps them: a member, not off the record / distress, not naming a forgotten
    entity. Any doubt (an import or a lookup failing) = no words."""
    if turn.is_guest:
        return False
    try:
        import memory_provenance

        if any(memory_provenance.is_off_record(turn.user_id, t) for t in texts if t):
            return False
    except Exception:  # noqa: BLE001
        return False
    try:
        from memory_tombstones import matching_tombstone

        if any(matching_tombstone(turn.user_id, t) for t in texts if t):
            return False
    except Exception:  # noqa: BLE001
        return False
    try:
        import memory_forgotten

        for t in texts:
            if t and await memory_forgotten.matches(turn.user_id, t):
                return False
    except Exception:  # noqa: BLE001
        return False
    return True


async def _keep_words_bounded(turn: sig.Turn, *texts: str) -> bool:
    """``_may_keep_words`` with a time bound: a slow forget-ledger lookup must not stall the worker (and so fill the queue);
    a lookup that runs out of time keeps NO words."""
    try:
        return await asyncio.wait_for(_may_keep_words(turn, *texts), STORE_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - TimeoutError included
        return False


def _scrub(text: str) -> Optional[str]:
    try:
        from memory_service import scrub_pii

        out, reject = scrub_pii(text)
        return None if reject else out
    except Exception:  # noqa: BLE001
        return None


async def _row(e: sig.Event) -> dict[str, Any]:
    cur, about = e.turn, e.about
    summary = ""
    texts = (about.text, cur.text) if about is not cur else (cur.text,)
    # words only when every turn quoted is this member's own (a panel's identity can flip between guest and member)
    if (about.user_id == cur.user_id and not _erased_since(cur.user_id, cur.ts, _ERASED_AT)
            and await _keep_words_bounded(cur, *texts)):
        # scrub the RAW words (normalising first would hide a card number / key / "password: x" from the scrubber); any
        # rejected text = no words at all
        scrubbed = [_scrub(t) for t in texts]
        if all(x is not None for x in scrubbed):
            summary = " -> ".join(p for p in (sig.display(x) for x in scrubbed) if p)[:SUMMARY_MAX]
    return {
        "id": uuid.uuid4().hex,
        "ts": float(cur.ts),
        "user_id": "guest" if cur.is_guest else cur.user_id,
        "is_guest": 1 if cur.is_guest else 0,
        "channel": cur.channel,
        "panel_id": cur.panel_id[:80],
        "session_id": "" if cur.is_guest else cur.session_id[:120],
        "signal": e.signal,
        "confidence": float(e.confidence),
        "fix_type": e.fix_type,
        "prev_turn_id": about.turn_id if about is not cur else "",
        "turn_id": cur.turn_id,
        "domain_predicted": about.domain(),
        # the handler that actually answered: a fast reply's own phrasing first (the router's prediction can differ), else the old rule
        "domain_served": ((sig.reply_domain(about.reply) if about.path == sig.PATH_FAST else "")
                          or about.two_stage_domain or (about.domain() if about.path == sig.PATH_FAST else "chat")),
        "path_served": about.path + (f":{about.lane}" if about.lane else ""),
        "latency_ms": about.latency_ms,
        "stt_ms": about.stt_ms,
        "pattern": e.pattern[:120],
        "summary": summary,
        "detail": json.dumps({"reasons": e.reasons, **{k: v for k, v in e.numbers.items() if v is not None}, **_follow_up(e)},
                             sort_keys=True)[:1000],
        "mode": mode(),
    }


def _follow_up(e: sig.Event) -> dict[str, Any]:
    """Text-free facts about the turn that REVEALED a pair struggle, for the Phase 2 labelling: the fast domain that answered it
    (``next_domain``, '' when the brain did or nothing is evidenced), how it was served, and when the failed turn finished
    (``about_ts``, to find its saved audio). Only for a pair by ONE identity: across a guest/member flip the follow-up is not
    provably the same person's."""
    cur, about = e.turn, e.about
    if about is cur or cur.is_guest or about.user_id != cur.user_id:      # a guest row keeps categories only: nothing to train on, nothing extra stored
        return {}
    return {"next_domain": sig.resolved_route(cur), "next_path": cur.path, "about_ts": round(float(about.ts), 3)}


async def _maybe_sweep(now: float) -> None:
    if now - float(_STATE.get("last_sweep") or 0.0) < SWEEP_EVERY_S:
        return
    _STATE["last_sweep"] = now
    try:
        n = await asyncio.wait_for(get_backend().purge_before(now - retention_days() * 86400.0), STORE_TIMEOUT_S)
        _count("sweeps")
        _count("swept", int(n or 0))
    except Exception as exc:  # noqa: BLE001
        _count("store_errors")
        logger.warning("struggle_detector: retention sweep failed (%s)", type(exc).__name__)
    try:   # the outcome rows age out the same way, whatever the flag says now (rows written while it was on still go)
        import turn_outcomes

        n = await asyncio.wait_for(turn_outcomes.get_backend().purge_before(now - retention_days() * 86400.0), STORE_TIMEOUT_S)
        _count("swept", int(n or 0))
    except Exception as exc:  # noqa: BLE001 - a missing table (0058 not run) included
        logger.warning("struggle_detector: outcome retention sweep failed (%s)", type(exc).__name__)


# ── the store ────────────────────────────────────────────────────────────────

_COLUMNS = ("id", "ts", "user_id", "is_guest", "channel", "panel_id", "session_id", "signal", "confidence", "fix_type",
            "prev_turn_id", "turn_id", "domain_predicted", "domain_served", "path_served", "latency_ms", "stt_ms", "pattern",
            "summary", "detail", "mode")


class SqlBackend:
    """``struggle_events`` (alembic 0050) through ``db_pool.get_db_ctx`` (``?`` placeholders)."""

    @staticmethod
    def _ctx():
        from db_pool import get_db_ctx  # type: ignore[import]

        return get_db_ctx()

    async def add(self, rows: list[dict]) -> int:
        marks = ", ".join("?" for _ in _COLUMNS)
        async with self._ctx() as db:
            for r in rows:
                await db.execute(f"INSERT INTO struggle_events ({', '.join(_COLUMNS)}) VALUES ({marks})",
                                 tuple(r.get(c) for c in _COLUMNS))
            await db.commit()
        return len(rows)

    async def rows(self, since: float, user_ids: Optional[list[str]] = None, limit: int = 5000) -> list[dict]:
        sql = f"SELECT {', '.join(_COLUMNS)} FROM struggle_events WHERE ts >= ?"
        params: list[Any] = [float(since)]
        if user_ids is not None:
            if not user_ids:
                return []
            sql += f" AND user_id IN ({', '.join('?' for _ in user_ids)})"
            params.extend(user_ids)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(int(limit))
        async with self._ctx() as db:
            cur = await db.execute(sql, tuple(params))
            return [dict(zip(_COLUMNS, tuple(r))) for r in await cur.fetchall()]

    async def matching(self, user_id: str, needle: str) -> list[tuple[str, str]]:
        async with self._ctx() as db:
            cur = await db.execute("SELECT id, summary FROM struggle_events WHERE user_id = ? AND LOWER(summary) LIKE ?",
                                   (user_id, f"%{(needle or '').lower()}%"))
            return [(str(r[0]), str(r[1])) for r in await cur.fetchall()]

    async def delete_ids(self, user_id: str, ids: list[str]) -> int:
        if not ids:
            return 0
        async with self._ctx() as db:
            cur = await db.execute(
                f"DELETE FROM struggle_events WHERE user_id = ? AND id IN ({', '.join('?' for _ in ids)})", (user_id, *ids))
            await db.commit()
            return int(getattr(cur, "rowcount", 0) or 0)

    async def delete_user(self, user_id: str) -> int:
        async with self._ctx() as db:
            cur = await db.execute("DELETE FROM struggle_events WHERE user_id = ?", (user_id,))
            await db.commit()
            return int(getattr(cur, "rowcount", 0) or 0)

    async def purge_before(self, cutoff: float) -> int:
        async with self._ctx() as db:
            cur = await db.execute("DELETE FROM struggle_events WHERE ts < ?", (float(cutoff),))
            await db.commit()
            return int(getattr(cur, "rowcount", 0) or 0)


class MemoryBackend:
    """In-process store (tests, the offline replay). Same contract; ``data`` is the stored rows."""

    def __init__(self) -> None:
        self.data: list[dict] = []

    async def add(self, rows: list[dict]) -> int:
        self.data.extend(dict(r) for r in rows)
        return len(rows)

    async def rows(self, since: float, user_ids: Optional[list[str]] = None, limit: int = 5000) -> list[dict]:
        out = [dict(r) for r in self.data if r["ts"] >= since and (user_ids is None or r["user_id"] in user_ids)]
        out.sort(key=lambda r: -r["ts"])
        return out[:limit]

    async def matching(self, user_id: str, needle: str) -> list[tuple[str, str]]:
        n = (needle or "").lower()
        return [(r["id"], r["summary"]) for r in self.data if r["user_id"] == user_id and n in (r["summary"] or "").lower()]

    async def delete_ids(self, user_id: str, ids: list[str]) -> int:
        before = len(self.data)
        drop = set(ids)
        self.data = [r for r in self.data if not (r["user_id"] == user_id and r["id"] in drop)]
        return before - len(self.data)

    async def delete_user(self, user_id: str) -> int:
        before = len(self.data)
        self.data = [r for r in self.data if r["user_id"] != user_id]
        return before - len(self.data)

    async def purge_before(self, cutoff: float) -> int:
        before = len(self.data)
        self.data = [r for r in self.data if r["ts"] >= cutoff]
        return before - len(self.data)


_backend: Optional[Any] = None


def get_backend():
    global _backend
    if _backend is None:
        _backend = SqlBackend()
    return _backend


def set_backend(backend: Optional[Any]) -> None:
    """Swap the store (tests / the offline replay); ``None`` restores the SQL default."""
    global _backend
    _backend = backend


# ── forgetting and deleting (fail closed: a store failure RAISES) ────────────

async def erase_entity(user_id: str, name: str) -> int:
    """Delete this user's rows whose summary names ``name`` (the forget sweep's whole-word pattern). Returns the rows removed.
    A store failure is RAISED: a forget that could not erase must not be confirmed. Only a missing table is "nothing stored"."""
    if not (user_id or "").strip() or not (name or "").strip():
        return 0
    async with _user_lock(user_id):   # no outcome / event write of this user is mid-flight while the erase runs (see ``_USER_LOCKS``)
        return await _erase_entity_locked(user_id, name)


async def _erase_entity_locked(user_id: str, name: str) -> int:
    _ERASED_AT[user_id] = time.time()     # turns still queued for this user keep no words
    _forget_history(user_id)
    from memory_forgotten import name_pattern, normalise_key

    pat = name_pattern(name)
    key = normalise_key(name)
    needle = (key.split(" ")[0] if key else name.strip().split(" ")[0]).lower()
    backend = get_backend()
    try:
        hits = await backend.matching(user_id, needle)
    except Exception as exc:
        if not _nothing_stored(exc):
            raise
        hits = []
    ids = [rid for rid, summary in hits if pat.search(summary or "")]
    if ids:
        # the proposals built on these rows go FIRST (Phase 2): a failure raises with the rows still there, so a retry finds them again
        import struggle_proposals

        await struggle_proposals.forget_events(ids)
    n = int(await backend.delete_ids(user_id, ids) or 0) if ids else 0
    return n + await _erase_outcomes(user_id, pat, needle)


def _outcomes_store_live() -> bool:
    """The outcome rows live where the events live: SQL beside a SQL event store, or an explicitly swapped-in store (tests). An in-memory
    event store (tests, the offline replay) beside the default SQL outcome store has no SQL rows to reach - the same rule as
    ``struggle_proposals._sql_events``."""
    import turn_outcomes

    return type(turn_outcomes.get_backend()) is not turn_outcomes.SqlBackend or isinstance(get_backend(), SqlBackend)


async def _erase_outcomes(user_id: str, pat, needle: str) -> int:
    """The failure-capture rows (``turn_outcomes``) naming the entity go too, whatever the flag says now; their seam ids leave the
    proposals / the retrain ledger first. RAISES on a failure; a missing table is "nothing stored"."""
    import struggle_proposals
    import turn_outcomes

    if not _outcomes_store_live():
        return 0
    try:
        ids = await turn_outcomes.matching_ids(user_id, pat, needle)
    except Exception as exc:
        if _nothing_stored(exc):
            return 0
        raise
    if not ids:
        return 0
    await struggle_proposals.forget_events([f"turn:{i}" for i in ids])
    return int(await turn_outcomes.get_backend().delete_ids(user_id, ids) or 0)


async def delete_user(user_id: str) -> int:
    """Remove every row of a user (``MemoryService.delete_user``). RAISES on a store failure (fail closed)."""
    if not (user_id or "").strip():
        return 0
    async with _user_lock(user_id):
        return await _delete_user_locked(user_id)


async def _delete_user_locked(user_id: str) -> int:
    _DELETED_AT[user_id] = time.time()    # turns still queued for this user are not recorded
    _forget_history(user_id)
    try:
        import struggle_proposals

        await struggle_proposals.forget_user(user_id)          # Phase 2 evidence first; the rows below are still there for a retry
        n = int(await get_backend().delete_user(user_id) or 0)
    except Exception as exc:
        if not _nothing_stored(exc):
            raise
        n = 0
    try:   # the failure-capture rows (turn_outcomes), whatever the flag says now
        import turn_outcomes

        if not _outcomes_store_live():
            return n
        return n + int(await turn_outcomes.get_backend().delete_user(user_id) or 0)
    except Exception as exc:
        if _nothing_stored(exc):
            return n
        raise


#: one lock per user: an outcome / event WRITE (its keep-words check through its insert) and a forget / user delete of that user never
#: interleave, so a write that was already past its check when the erase ran cannot land after the erase finished and find nothing to
#: delete ("forgotten means forever"). Writers re-check the erase marks INSIDE the lock; the erase sets its mark inside it too.
_USER_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}


def _user_lock(user_id: str) -> asyncio.Lock:
    key = (id(asyncio.get_running_loop()), user_id)
    lock = _USER_LOCKS.get(key)
    if lock is None:
        lock = _USER_LOCKS[key] = asyncio.Lock()
    return lock


#: user -> when a forget / a delete ran: a turn queued BEFORE it must not write the words (forget) or anything (delete) after it
_ERASED_AT: dict[str, float] = {}
_DELETED_AT: dict[str, float] = {}


def _erased_since(user_id: str, ts: float, marks: dict) -> bool:
    at = marks.get(user_id)
    return at is not None and ts <= at


def _forget_history(user_id: str) -> None:
    """Drop the in-memory previous turns of ``user_id`` (they hold raw words a later pair signal would quote)."""
    for k in list(_HISTORY):
        kept = [t for t in _HISTORY[k] if t.user_id != user_id]
        if kept:
            _HISTORY[k] = kept
        else:
            _HISTORY.pop(k, None)


def _nothing_stored(exc: BaseException) -> bool:
    """A table that does not exist yet (migration 0050 not run) holds no rows. Anything else - a lost pool, a pool never
    built in this process (a script would then wrongly report rows gone that live in Postgres) - is a FAILURE."""
    msg = f"{type(exc).__name__} {exc}".lower()
    return ("undefinedtable" in msg or "no such table" in msg
            or (("struggle_events" in msg or "turn_outcomes" in msg) and "does not exist" in msg))


# ── the one user-facing reply (mode ``on``) ──────────────────────────────────

def ack_line(text: str) -> Optional[str]:
    """``on`` only: a short, deterministic acknowledgement of a BARE "that was wrong" (no corrected value) that nothing else
    answered. None otherwise. Pure apart from the flag read."""
    try:
        if mode() != "on":
            return None
        import conversation_feedback as _cf

        hit = _cf.classify(sig.strip_wake(text))
        return ACK_LINE if hit and hit[0] == _cf.THUMBS_DOWN else None
    except Exception:  # noqa: BLE001
        return None
