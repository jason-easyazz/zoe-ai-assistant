"""The BOOK format for a pendant day (owner, 2026-11-09): the clean read laid out to be READ, in the transcript files and the morning review.

  * the speaker's name in bold, then a paragraph; consecutive turns by the same speaker are merged into one paragraph;
  * no inline timestamps: a scene break ``— 2:41 am —`` at each recording and at a gap of more than 60 s;
  * an uncertain turn is in italics instead of a bracket tag: ``Someone (Name?)`` for a leaning speaker, ``Someone`` for an unknown one;
  * a turn the clean read could not be trusted for shows the safest text with ``(lightly cleaned)`` beside it;
  * the VERBATIM, with times, as an appendix (the clean twin is a display layer; the verbatim is what the system keeps and reads).

``render`` is pure (turns in, markdown out). ``load_day`` reads one household day from the store. A transcript holds what people said: callers write it OUTSIDE the
repository (``zoe-omi-transcript.py`` refuses a path inside it) and never log it.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

import omi_repass_clean as oc

GAP_S = 60.0


def display_name(t: dict) -> str:
    if t.get("is_zoe"):
        return "Zoe"
    if t.get("name"):
        return str(t["name"])
    if t.get("lean"):
        return f"Someone ({t['lean']}?)"
    return "Someone"


def _uncertain(t: dict) -> bool:
    return not t.get("is_zoe") and not t.get("name")


def _clock(ts: float, tz: Any) -> str:
    d = dt.datetime.fromtimestamp(ts, tz)
    return f"{d.hour % 12 or 12}:{d.minute:02d} {'am' if d.hour < 12 else 'pm'}"


def shown_text(t: dict) -> str:
    """The clean twin when there is one, else the verbatim; fallback turns carry the mark."""
    clean = (t.get("clean_text") or "").strip()
    how = t.get("clean_how") or ""
    body = clean if clean else (t.get("text") or "").strip()
    if oc.marked(how) and body:
        body += " (lightly cleaned)"
    return body


def render(turns: list, *, title: str = "", tz: Any = None, note: str = "") -> str:
    """``turns``: dicts with ``rec`` (recording id), ``start_s`` / ``end_s`` (epoch), ``text`` (verbatim), ``clean_text`` / ``clean_how``, ``name`` (a confirmed
    speaker), ``lean`` (a leaning name), ``is_zoe``. In time order."""
    tz = tz or dt.timezone.utc
    turns = [t for t in turns if (t.get("text") or "").strip()]
    out = []
    if title:
        out += [f"# {title}", ""]
    out += [note or "*Private. A machine transcript of a pendant recording, lightly cleaned for reading. Uncertain turns are in italics; \"Someone\" could not be "
            "identified, and \"Someone (Name?)\" is a best guess. The verbatim, with times, is at the end.*", ""]
    prev_end: Optional[float] = None
    prev_rec = None
    para: Optional[dict] = None

    def flush() -> None:
        nonlocal para
        if para:
            body = " ".join(para["parts"])
            out.extend([f"**{para['name']}:** " + (f"*{body}*" if para["uncertain"] else body), ""])
        para = None

    for t in turns:
        name, unc = display_name(t), _uncertain(t)
        scene = prev_rec is None or t.get("rec") != prev_rec or (prev_end is not None and float(t["start_s"]) - prev_end > GAP_S)
        if scene:
            flush()
            out += [f"— {_clock(float(t['start_s']), tz)} —", ""]
        if para and para["name"] == name and para["uncertain"] == unc:
            para["parts"].append(shown_text(t))
        else:
            flush()
            para = {"name": name, "uncertain": unc, "parts": [shown_text(t)]}
        prev_end, prev_rec = float(t.get("end_s") or t["start_s"]), t.get("rec")
    flush()
    out += ["---", "", "## Verbatim, with times", ""]
    for t in turns:
        d = dt.datetime.fromtimestamp(float(t["start_s"]), tz)
        out.append(f"{d:%H:%M:%S} {display_name(t)}: {(t.get('text') or '').strip()}  ")
    return "\n".join(out).rstrip() + "\n"


async def load_day(db, user_id: str, day: dt.date, tz: Any) -> list:
    """The household day's turns from the store (verbatim + clean twin + resolved names), oldest first. Zoe's lines are kept (she is part of the conversation);
    other non-person lines (media) are left out."""
    a = dt.datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(dt.timezone.utc)
    b = a + dt.timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    try:
        rows = await _all(db, "SELECT s.recording_id, s.start_ms, s.end_ms, s.text, s.clean_text, s.clean_how, s.speaker, s.owner_label, s.not_person, s.tagged_person_id, "
                              "s.suggested_person_id, r.started_at FROM omi_segments s JOIN omi_recordings r ON r.id = s.recording_id "
                              "WHERE s.user_id = ? AND r.user_id = ? AND r.status = 'done' AND r.started_at >= ? AND r.started_at < ? ORDER BY r.started_at, s.seq",
                          (user_id, user_id, a.strftime(fmt), b.strftime(fmt)))
    except Exception as exc:  # noqa: BLE001
        if "no such column" in str(exc).lower() or type(exc).__name__ == "UndefinedColumnError":
            return []
        raise
    people = {pid: name for pid, name in await _all(db, "SELECT id, name FROM people WHERE user_id = ?", (user_id,))}
    me = ""
    for (n,) in await _all(db, "SELECT name FROM users WHERE id = ?", (user_id,)):
        me = str(n or "")
    out = []
    for rec, s_ms, e_ms, text, clean, how, spk, owner_label, not_person, tagged, sugg, started in rows:
        if not_person and not_person != "zoe":
            continue
        t0 = dt.datetime.strptime(started, fmt).replace(tzinfo=dt.timezone.utc).timestamp()
        name = None
        if not not_person:
            if tagged and people.get(tagged):
                name = people[tagged]
            elif (owner_label or spk) == "you" and me:
                name = me
        out.append({"rec": rec, "start_s": t0 + int(s_ms) / 1000.0, "end_s": t0 + int(e_ms) / 1000.0, "text": text or "", "clean_text": clean or "", "clean_how": how or "",
                    "is_zoe": not_person == "zoe", "name": name, "lean": people.get(sugg) if (sugg and not name and not not_person) else None})
    return out


async def _all(db, sql: str, params: tuple = ()) -> list:
    cur = await db.execute(sql, params)
    return [tuple(r) for r in await cur.fetchall()]
