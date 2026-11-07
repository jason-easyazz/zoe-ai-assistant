"""The now card (``ZOE_MEMORY_NOW_CARD`` = off | shadow | on, default off). Record + rules: docs/knowledge/now-card.md.

A household member's turn carries one small card of what is live for them today (speaker, <= 2 open threads in their own words,
the next date within 7 days, one fact they stated), BUILT AT NIGHT from existing stores with no model (``rebuild``) and read BY KEY
per turn (``block_for``). Never inferred / derived / pending rows, ``open_loops`` (a model's paraphrase), private or off-the-wall
classes, muted topics or forgotten names (strict lookups: an outage drops the line); a minor gets no night-mind thread (D7). A
spoken turn needs a CONFIRMED voice. The seam (``zoe_flue_client``) sends it on one envelope line the sidecar appends to the
SYSTEM prompt after the user-model card. ``shadow`` logs ids and sizes per turn and sends nothing. Never raises on the turn path.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_TOKENS = 180                 # the whole card, header included (lane C: 120-180 tokens per member)
THREADS_MAX = 2
QUOTE_CHARS = 110
FACT_CHARS = 120
DATE_WINDOW_DAYS = 7
THREAD_RECENT_DAYS = 14          # a thread last spoken of longer ago than this is not "now"
GRACE_HOUR = 5                   # yesterday's card still serves before 05:00 local (the night pass rebuilds by then)
SERVE_BUDGET_S = 0.4             # the turn never waits longer than this for the card
ROW_TTL_S = 300.0
_SKIP_IDS = frozenset({"family-admin", "voice-daemon"})
_THREAD_KINDS_OFF = frozenset({"health", "feeling"})
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


def mode() -> str:
    import typed_env

    return str(typed_env.flag("ZOE_MEMORY_NOW_CARD"))


def harness_admitted() -> bool:
    """Also serve (and lazily build) the bar's ``demo_*`` ids, for a measured off-vs-on run only."""
    import typed_env

    return bool(typed_env.flag("ZOE_MEMORY_NOW_CARD_HARNESS"))


def est_tokens(text: str) -> int:
    t = text or ""
    cjk = len(_CJK.findall(t)) / max(1, len(t)) >= 0.3
    return int(math.ceil(len(t) * (0.8 if cjk else 0.25)))


def _cut(text: str, limit: int) -> str:
    t = " ".join(str(text or "").split())
    if len(t) <= limit:
        return t
    return t[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def _fmt(d: _dt.date) -> str:
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def _day(raw: Any) -> Optional[_dt.date]:
    try:
        return _dt.date.fromisoformat(str(raw or "")[:10])
    except ValueError:
        return None


def _local_today(now: Optional[_dt.datetime] = None) -> _dt.datetime:
    try:
        from time_utils import zoe_timezone

        tz = zoe_timezone()
    except Exception:  # noqa: BLE001 - slim lanes without the zone data
        tz = _dt.timezone.utc
    return (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(tz)


@dataclass(frozen=True)
class Inputs:
    name: str = ""
    threads: tuple = ()       # (observation id, day iso, quote)
    dates: tuple = ()         # (person id, label, month, day)
    facts: tuple = ()         # (memory id, text)


def next_date(dates: tuple, today: _dt.date) -> Optional[tuple]:
    """``(when, label, person_id)`` of the soonest date 0..``DATE_WINDOW_DAYS`` days ahead, or None. Pure."""
    best = None
    for pid, label, month, day in dates:
        for year in (today.year, today.year + 1):
            try:
                when = _dt.date(year, int(month), int(day))
            except (TypeError, ValueError):     # 29 Feb in a common year, a bad row
                continue
            delta = (when - today).days
            if 0 <= delta <= DATE_WINDOW_DAYS:
                key = (delta, str(label), str(pid))
                if best is None or key < best[0]:
                    best = (key, (when, str(label), str(pid)))
                break
    return best[1] if best else None


def compose(inp: Inputs, today: _dt.date) -> list:
    """``[kind, text, ref]`` lines; ``[]`` when only the speaker is left. Over budget: drop fact, then threads, then the date. Pure."""
    head = [["speaker", f"Speaking with: {_cut(inp.name, 40)}", "user"]] if inp.name.strip() else []
    threads = [["thread", f"{_fmt(_dt.date.fromisoformat(day[:10]))} they said “{_cut(q, QUOTE_CHARS)}”", f"night_observations:{oid}"]
               for oid, day, q in inp.threads[:THREADS_MAX]]
    nd = next_date(inp.dates, today)
    date = [["date", f"Coming up: {nd[1]}, {_fmt(nd[0])}", f"person:{nd[2]}"]] if nd else []
    fact = [["fact", f"They told you: {_cut(inp.facts[0][1], FACT_CHARS)}", f"memory:{inp.facts[0][0]}"]] if inp.facts else []
    while True:
        lines = head + threads + date + fact
        if not any(k != "speaker" for k, _t, _r in lines):
            return []
        if est_tokens(render(today.isoformat(), lines)) <= MAX_TOKENS:
            return lines
        for group in (fact, threads, date):
            if group:
                group.pop()
                break


def render(built_on: str, lines: list) -> str:
    if not lines:
        return ""
    day = _fmt(_dt.date.fromisoformat(built_on[:10]))
    body = "\n".join(f"- {t}" for _k, t, _r in lines)
    return (f"Now card ({day}; from what they told you, checked overnight). Use a line only where it fits what they say; "
            f"don't recite it or mention this card.\n{body}")


def version(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16] if text else ""


def _speakable(text: str, meta: Optional[dict], ref: str, mutes: list) -> bool:
    try:
        import panel_review_cards
        import provenance_answers
        import restraint

        if provenance_answers.sensitive_classes(text, meta or {}):
            return False
        if set(restraint.row_classes(meta or {}, text)) & panel_review_cards.OFF_THE_WALL:
            return False
        return not restraint.muted(text, ref, mutes)
    except Exception:  # noqa: BLE001
        return False


async def _forgotten(user_id: str, text: str) -> bool:
    """Names something forgotten? Strict: an outage counts as yes."""
    try:
        import memory_forgotten
        from memory_tombstones import matching_tombstone

        return bool(matching_tombstone(user_id, text) or await memory_forgotten.matches(user_id, text, strict=True))
    except Exception:  # noqa: BLE001
        return True


async def _db_rows(sql: str, params: tuple) -> list:
    from db_pool import get_db_ctx

    async with get_db_ctx() as db:
        return list(await (await db.execute(sql, params)).fetchall())


async def _name(uid: str) -> str:
    rows = await _db_rows("SELECT name FROM users WHERE id = ?", (uid,))
    name = str((rows[0][0] if rows else "") or "").strip()
    return name.title() if name.islower() else name


async def _threads(uid: str, today: _dt.date, mutes: list) -> tuple:
    import night_mind

    if not night_mind.enabled():
        return ()
    threads, obs = await night_mind.snapshot(uid)            # significant threads, CURRENT observations, forget re-checked
    live = []
    for t in threads:
        last = _day(t.get("last_day"))
        if (t.get("status") not in ("open", "changed") or t.get("leave_reason") or t.get("raise_policy") == "leave"
                or t.get("last_feeling") in night_mind.NEGATIVE or last is None or (today - last).days > THREAD_RECENT_DAYS):
            continue
        live.append((t, last))
    live.sort(key=lambda tl: (tl[0].get("raise_policy") != "raise", -night_mind.salience(tl[0], today), -tl[1].toordinal(), str(tl[0]["id"])))
    out = []
    for t, _last in live:
        mine = sorted((o for o in obs if o["thread_id"] == t["id"]), key=lambda o: (float(o.get("said_at") or 0), str(o["id"])), reverse=True)
        for o in mine:
            q = str(o.get("quote") or "")
            if (o.get("kind") in _THREAD_KINDS_OFF or o.get("feeling") in night_mind.NEGATIVE or _day(o.get("day")) is None
                    or not _speakable(q, {}, str(t.get("source_ref") or ""), mutes) or await _forgotten(uid, q)):
                continue
            out.append((str(o["id"]), str(o["day"]), q))
            break
        if len(out) >= THREADS_MAX:
            break
    return tuple(out)


async def _dates(uid: str, mutes: list) -> tuple:
    from person_profile import parse_birthday

    out: dict = {}
    for pid, kind, label, month, day, name in await _db_rows(
            "SELECT d.person_id, d.date_type, d.label, d.month, d.day, p.name FROM person_important_dates d "
            "JOIN people p ON p.id = d.person_id WHERE p.user_id = ? AND COALESCE(p.deleted, 0) = 0 "
            "AND d.month IS NOT NULL AND d.day IS NOT NULL", (uid,)):
        what = {"birthday": "birthday", "anniversary": "anniversary"}.get(str(kind or ""), str(label or "date"))
        out.setdefault((str(pid), what), (str(pid), f"{name}'s {what}", int(month), int(day)))
    for pid, name, raw in await _db_rows(
            "SELECT id, name, birthday FROM people WHERE user_id = ? AND COALESCE(deleted, 0) = 0 "
            "AND birthday IS NOT NULL AND birthday <> ''", (uid,)):
        md = parse_birthday(raw)
        if md:
            out.setdefault((str(pid), "birthday"), (str(pid), f"{name}'s birthday", md[0], md[1]))
    return tuple([out[k] for k in sorted(out) if _speakable(out[k][1], {}, f"person:{out[k][0]}", mutes)
                  and not await _forgotten(uid, out[k][1])])


async def _facts(uid: str, mutes: list, svc: Any) -> tuple:
    import memory_authority as auth
    from user_model_card import is_tentative

    rows = await svc.load_durable_for_hop(uid)
    rows = sorted(rows, key=lambda r: (-float((r.metadata or {}).get("added_ts") or 0.0), str(r.id)))
    out = []
    for r in rows:
        meta, text = dict(r.metadata or {}), str(r.text or "")
        if auth.row_class(meta, text) not in (auth.USER_STATED, auth.USER_CONFIRMED) or is_tentative(meta, text):
            continue
        if not _speakable(text, meta, f"memory:{r.id}", mutes) or await _forgotten(uid, text):
            continue
        out.append((str(r.id), text))
        if len(out) >= 3:
            break
    return tuple(out)


async def gather(uid: str, today: _dt.date, svc: Any = None) -> "tuple[Inputs, list]":
    """Each source fails closed on its own: one that cannot be read contributes nothing (and is named in ``errors``)."""
    import manner_block
    import restraint

    errors: list = []

    async def guard(label: str, coro: Any, empty: Any) -> Any:
        try:
            return await coro
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}:{type(exc).__name__}")
            return empty

    if svc is None:
        from memory_service import get_memory_service

        svc = get_memory_service()
    mutes = await restraint.list_mutes(uid)
    adult = await guard("adult", manner_block.is_adult(uid), False)    # not positively an adult (or unknown) = no night thread (D7)
    return Inputs(
        name=await guard("name", _name(uid), ""),
        threads=await guard("threads", _threads(uid, today, mutes), ()) if adult else (),
        dates=await guard("dates", _dates(uid, mutes), ()),
        facts=await guard("facts", _facts(uid, mutes, svc), ()),
    ), errors


class SqlStore:
    async def read(self, uid: str) -> Optional[tuple]:
        rows = await _db_rows("SELECT card_json, card_text, version, built_on FROM now_cards WHERE user_id = ?", (uid,))
        return tuple(rows[0]) if rows else None

    async def write(self, uid: str, card_json: str, text: str, ver: str, built_on: str, built_at: str) -> None:
        from db_pool import get_db_ctx

        async with get_db_ctx() as db:
            await db.execute(
                "INSERT INTO now_cards (user_id, card_json, card_text, version, built_on, built_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET card_json = excluded.card_json, card_text = excluded.card_text, "
                "version = excluded.version, built_on = excluded.built_on, built_at = excluded.built_at",
                (uid, card_json, text, ver, built_on, built_at))
            await db.commit()

    async def remove(self, uid: str) -> int:
        from db_pool import get_db_ctx

        async with get_db_ctx() as db:
            await db.execute("DELETE FROM now_cards WHERE user_id = ?", (uid,))
            await db.commit()
        return 1


_store: Any = SqlStore()
_ROWS: "dict[str, tuple[float, Optional[tuple]]]" = {}
_PENDING: set = set()


def set_store(store: Any) -> Any:
    global _store
    prev, _store = _store, store
    _ROWS.clear()
    return prev


def invalidate(uid: Optional[str] = None) -> None:
    if uid is None:
        _ROWS.clear()
    else:
        _ROWS.pop(uid, None)


def _no_table(exc: BaseException) -> bool:
    """No 0067 table, or a process that NEVER had a database (a lab, a bare test). One that had a pool and lost it is a fault."""
    text = f"{type(exc).__name__} {exc}".lower()
    if "undefinedtable" in text or "does not exist" in text or "no such table" in text:
        return True
    try:
        from db_pool import pool_ever_initialised

        return isinstance(exc, RuntimeError) and "not initialised" in text and not pool_ever_initialised()
    except Exception:  # noqa: BLE001
        return False


def build_eligible(uid: str, allow_synthetic: bool = False) -> str:
    from memory_service import is_guest_memory_user
    from user_filters import is_synthetic_user

    uid = (uid or "").strip()
    if not uid or is_guest_memory_user(uid) or uid in _SKIP_IDS:
        return "guest"
    if is_synthetic_user(uid) and not allow_synthetic:
        return "synthetic"
    return ""


async def rebuild(uid: str, *, today: Optional[_dt.date] = None, svc: Any = None, allow_synthetic: bool = False) -> dict:
    """Build and store one member's card (the night pass); a no-op while off. Never raises."""
    uid = (uid or "").strip()
    if mode() == "off":
        return {"status": "off"}
    why = build_eligible(uid, allow_synthetic)
    if why:
        return {"status": "skipped", "reason": why}
    try:
        now = _local_today()
        today = today or now.date()
        inp, errors = await gather(uid, today, svc)
        lines = compose(inp, today)
        text = render(today.isoformat(), lines)
        card = json.dumps({"built_on": today.isoformat(), "lines": lines}, ensure_ascii=False, sort_keys=True)
        await _store.write(uid, card, text, version(text), today.isoformat(), now.isoformat(timespec="seconds"))
        invalidate(uid)
        kinds = [k for k, _t, _r in lines]
        out = {"status": "ok" if lines else "empty", "tokens": est_tokens(text), "lines": len(lines), "threads": kinds.count("thread"),
               "date": kinds.count("date"), "fact": kinds.count("fact"), "version": version(text) or "-", "errors": errors}
        logger.info("NOW_CARD_BUILD user=%s status=%s tokens=%d lines=%d threads=%d date=%d fact=%d version=%s errors=%s", uid,
                    out["status"], out["tokens"], out["lines"], out["threads"], out["date"], out["fact"], out["version"],
                    ",".join(errors) or "-")
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("NOW_CARD_BUILD user=%s status=error (%s)", uid, type(exc).__name__)
        return {"status": "error", "error": type(exc).__name__}


def eligible(uid: str, voice: bool = False) -> str:
    """'' when THIS turn may carry the card, else why not (a spoken turn needs a CONFIRMED voice)."""
    import restraint
    import traffic_class
    from memory_service import is_guest_memory_user

    uid = (uid or "").strip()
    if not uid:
        return "empty"
    if is_guest_memory_user(uid) or uid in _SKIP_IDS:
        return "guest"
    forced = traffic_class.class_for_user(uid)
    if not (traffic_class.current() == traffic_class.HOUSEHOLD and not forced) and not (
            harness_admitted() and forced == traffic_class.HARNESS):
        return "not_household"
    verdict = restraint.current_verdict()
    if verdict is False or (voice and verdict is not True):
        return "unverified"
    return ""


def _fresh(built_on: str, now: _dt.datetime) -> bool:
    try:
        built = _dt.date.fromisoformat(str(built_on)[:10])
    except ValueError:
        return False
    today = now.date()
    return built == today or (built == today - _dt.timedelta(days=1) and now.hour < GRACE_HOUR)


async def _row(uid: str) -> Optional[tuple]:
    hit = _ROWS.get(uid)
    if hit and time.monotonic() - hit[0] < ROW_TTL_S:
        return hit[1]
    try:
        row = await _store.read(uid)
    except Exception as exc:  # noqa: BLE001
        if not _no_table(exc):
            raise
        row = None
    _ROWS[uid] = (time.monotonic(), row)
    return row


async def _live(uid: str, lines: list, svc: Any = None) -> list:
    """The stored lines whose sources still stand now; each check fails closed (the line is dropped)."""
    import restraint

    try:
        mutes = await restraint.list_mutes_strict(uid)
    except Exception:  # noqa: BLE001 - an unknown mute list is not "nothing muted": serve no line this turn
        logger.info("NOW_CARD user=%s mutes unreadable - no lines served", uid)
        return []
    # the WHOLE source text of each line, freshly read: a stored line is cut for display (FACT_CHARS / QUOTE_CHARS), and a mute or
    # a forget named after the cut must still drop it
    whole: dict = {}
    # a thread mute is stored against the THREAD (``night_threads:<id>``), the card line points at an observation: map it
    thread_of: dict = {}
    obs_ids: Optional[set] = None
    threads_ok = True
    if any(k == "thread" for k, _t, _r in lines):
        # Adult eligibility is rechecked at SERVE time: a member an admin marked a minor after the nightly build keeps their stored
        # card until it expires, so the stored thread lines must not outlive the mode change. A failed lookup drops them (D7).
        try:
            import manner_block

            threads_ok = bool(await manner_block.is_adult(uid))
        except Exception:  # noqa: BLE001
            threads_ok = False
    if threads_ok and any(k == "thread" for k, _t, _r in lines):
        try:
            import night_mind

            if night_mind.enabled():
                _thr, obs = await night_mind.snapshot(uid, use_cache=True, all_threads=True)
                obs_ids = {f"night_observations:{o['id']}" for o in obs}
                whole.update((f"night_observations:{o['id']}", str(o.get("quote") or "")) for o in obs)
                thread_of.update((f"night_observations:{o['id']}", f"night_threads:{o['thread_id']}")
                                 for o in obs if o.get("thread_id"))
        except Exception:  # noqa: BLE001
            obs_ids = None
    facts: Optional[set] = None
    mem = [r.split(":", 1)[1] for k, _t, r in lines if k == "fact"]
    if mem:
        try:
            if svc is None:
                from memory_service import get_memory_service

                svc = get_memory_service()
            fresh = await svc.recheck_durable(uid, mem)
            facts = {f"memory:{r.id}" for r in fresh}
            whole.update((f"memory:{r.id}", str(getattr(r, "text", "") or "")) for r in fresh)
        except Exception:  # noqa: BLE001
            facts = None
    out = []
    for kind, text, ref in lines:
        if kind == "thread" and (not threads_ok or obs_ids is None or ref not in obs_ids):
            continue
        if kind == "fact" and (facts is None or ref not in facts):
            continue
        if kind != "speaker":
            texts = [t for t in dict.fromkeys((text, whole.get(ref, ""))) if t]
            refs = [r for r in (ref, thread_of.get(ref, "")) if r]
            if any(restraint.muted(t, r, mutes) for t in texts for r in refs):
                continue
            if any([await _forgotten(uid, t) for t in texts]):
                continue
        out.append([kind, text, ref])
    return out if any(k != "speaker" for k, _t, _r in out) else []


async def card_for(uid: str, *, now: Optional[_dt.datetime] = None, svc: Any = None) -> "tuple[str, list, str]":
    """``(text, refs, skipped)``: the card this member may be served now, or '' and why not."""
    row = await _row(uid)
    if row is None:
        from traffic_class import HARNESS, class_for_user

        if harness_admitted() and class_for_user(uid) == HARNESS:      # the bar's ids are seeded by day: build once, serve next turn
            _spawn(rebuild(uid, allow_synthetic=True, svc=svc))
        return "", [], "no_card"
    card_json, _text, _ver, built_on = row
    if not _fresh(built_on, _local_today(now)):
        return "", [], "stale"
    lines = await _live(uid, list(json.loads(card_json or "{}").get("lines") or []), svc)
    if not lines:
        return "", [], "empty"
    text = render(built_on, lines)
    if est_tokens(text) > MAX_TOKENS:
        return "", [], "over_budget"
    return text, [r for _k, _t, r in lines], ""


def _short(refs: list) -> str:
    return ",".join(f"{r.split(':', 1)[0][:1]}:{r.split(':', 1)[-1][:8]}" for r in refs) or "-"


def _log(uid: str, m: str, text: str, refs: list, skipped: str, ms: float) -> None:
    logger.info("NOW_CARD user=%s mode=%s served=%d tokens=%d lines=%d ids=%s version=%s ms=%.1f skipped=%s", uid, m,
                int(m == "on" and bool(text)), est_tokens(text), len(refs), _short(refs), version(text) or "-", ms, skipped or "-")


def _spawn(coro: Any) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


async def drain() -> None:
    while _PENDING:
        await asyncio.gather(*list(_PENDING), return_exceptions=True)


async def _measure(uid: str, m: str, svc: Any = None) -> str:
    t0 = time.monotonic()
    try:
        text, refs, skipped = await asyncio.wait_for(card_for(uid, svc=svc), timeout=SERVE_BUDGET_S)
    except asyncio.TimeoutError:
        text, refs, skipped = "", [], "timeout"
    except Exception:  # noqa: BLE001
        text, refs, skipped = "", [], "error"
    _log(uid, m, text, refs, skipped, (time.monotonic() - t0) * 1000)
    return text


async def block_for(uid: str, *, voice: bool = False, svc: Any = None) -> str:
    """The card THIS turn carries ('' = no line = today's bytes). Off: '' before any work."""
    try:
        m = mode()
        if m == "off":
            return ""
        uid = (uid or "").strip()
        why = eligible(uid, voice)
        if why:
            _log(uid, m, "", [], why, 0.0)
            return ""
        if m == "shadow":
            _spawn(_measure(uid, m, svc))
            return ""
        return await _measure(uid, m, svc)
    except Exception:  # noqa: BLE001
        return ""


async def warm_block(uid: str) -> str:
    """The card the wake warm sends for the likely (server-verified) speaker, so it warms what the next turn sends. ``on`` only."""
    try:
        uid = (uid or "").strip()
        if mode() != "on" or build_eligible(uid, allow_synthetic=harness_admitted()):
            return ""
        text, _refs, _skipped = await asyncio.wait_for(card_for(uid), timeout=SERVE_BUDGET_S)
        return text
    except Exception:  # noqa: BLE001
        return ""


async def forget_user(uid: str) -> int:
    """``MemoryService.delete_user``. Raises on a store failure."""
    return await erase_entity(uid, "") if (uid or "").strip() else 0


async def erase_entity(uid: str, name: str) -> int:
    """Delete the card when it names ``name`` (whole word; "" = always, the member erase); the next night rebuilds it. Raises on a
    store failure: a forget that could not erase must not be confirmed."""
    if not (uid or "").strip():
        return 0
    from memory_forgotten import name_pattern

    try:
        row = await _store.read(uid)
        if row is None or (name and not name_pattern(name).search(str(row[1] or "") + "\n" + str(row[0] or ""))):
            return 0
        n = int(await _store.remove(uid) or 0)
    except Exception as exc:  # noqa: BLE001
        if _no_table(exc):
            return 0
        raise
    invalidate(uid)
    return n


async def rebuild_all(user_ids: list) -> dict:
    """The night-mind CLI's hook: tonight's threads into tomorrow's cards. Never raises."""
    return {str(u): (await rebuild(str(u))).get("status") for u in user_ids}
