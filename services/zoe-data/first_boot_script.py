"""first_boot_script - the first-boot flow as DATA: load it, check it has no dead ends, and look up a line (unwired).

Structure (``lexicons_data/first_boot.json``, language-neutral): one step per ``first_boot.ITEMS`` id plus ``hello`` and ``done``;
each step's mode, declared time budget, QR hand-off, what the mechanism behind it is TODAY (``backed_by``), and a transition for
every person behaviour. Words (``lexicons_data/first_boot_<lang>.json``): what Zoe says and what the panel shows, per step. A
language with no enabled file contributes nothing, and a new language is one file (the ``voice_enrollment_<lang>.json`` pattern).

``problems`` is the contract the tests and the lab hold the data to. A DEAD END is a (step, behaviour) that does not move the
person forward: unmapped, mapped to an unknown transition, a "defer" on a step that cannot be put off, or a transition whose words
are missing. ``repeat`` and ``rephrase`` are bounded by ``retries`` and then ``park``; ``help`` is not a dead end, but it is a
point where the person needs outside help, so ``help_points`` lists every one.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

import first_boot as fb

DIR = Path(__file__).resolve().parent / "lexicons_data"
STRUCTURE = DIR / "first_boot.json"
EXTRA_STEPS = ("hello", "done")
#: the words every step must have, and the words a transition needs
REQUIRED_WORDS = ("say", "screen", "done")
WORDS_FOR = {"defer": "later", "explain": "explain"}
COMMON_WORDS = ("rephrase", "repeat", "park", "park_screen", "resume", "ask_adult", "help", "help_screen", "deferred")
MODES = ("voice", "physical", "pinpad", "qr", "system")
STATUSES = ("exists", "partial", "missing")


@lru_cache(maxsize=1)
def structure() -> dict:
    return json.loads(STRUCTURE.read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def words(lang: str = "en") -> dict:
    path = DIR / f"first_boot_{(lang or '').strip().lower()}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("enabled") else {}


def steps() -> list:
    return list(structure()["steps"])


def step(step_id: str) -> Optional[dict]:
    return next((s for s in steps() if s["id"] == step_id), None)


def transition(step_id: str, behaviour: str) -> Optional[str]:
    s = step(step_id)
    return (s or {}).get("on", {}).get(behaviour)


def line(step_id: str, key: str, lang: str = "en", **slots) -> str:
    """The step's line ``key`` (else the shared ``common`` line), slots filled. "" when the language has no such line."""
    w = words(lang)
    raw = (w.get("steps", {}).get(step_id, {}) or {}).get(key)
    if raw is None:
        raw = w.get("common", {}).get(key, "")
    if isinstance(raw, dict):
        return {k: _fill(str(v), slots) for k, v in raw.items()}  # type: ignore[return-value]
    return _fill(str(raw), slots)


def _fill(text: str, slots: dict) -> str:
    """Fill only the slots given; an unfilled ``{slot}`` stays as written (never a KeyError into a turn)."""
    for key, value in slots.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def core_budget_s() -> int:
    """The declared budget of the IMPORTANT part (what 'about ten minutes' in the hello line covers): hello, every blocking
    item, the first useful thing, done."""
    core = {i.id for i in fb.ITEMS if i.tier == fb.BLOCKING} | {"first_win", *EXTRA_STEPS}
    return sum(int(s.get("budget_s", 0)) for s in steps() if s["id"] in core)


def dead_ends(lang: str = "en") -> list:
    """Every (step, behaviour) that does not move the person forward. Must be empty."""
    st, w = structure(), words(lang)
    allowed = set(st["transitions"])
    out = []
    for s in steps():
        sw = (w.get("steps", {}).get(s["id"]) or {})
        for b in st["behaviours"]:
            t = s.get("on", {}).get(b)
            if t not in allowed:
                out.append((s["id"], b))
            elif t == "defer" and not s.get("later_allowed"):
                out.append((s["id"], b))
            elif t in WORDS_FOR and not sw.get(WORDS_FOR[t]) and not (t == "defer" and w.get("common", {}).get("deferred")):
                out.append((s["id"], b))
    return out


def help_points() -> list:
    """Every (step, behaviour) that sends the person to the help card: stuck without outside help."""
    return [(s["id"], b) for s in steps() for b, t in s.get("on", {}).items() if t == "help"]


def problems(lang: str = "en") -> list:
    """Everything wrong with the data, as plain strings. Empty = the flow is complete for ``lang``."""
    st, w = structure(), words(lang)
    out: list = []
    ids = [s["id"] for s in steps()]
    want = ["hello"] + [i.id for i in fb.ITEMS] + ["done"]
    if ids != want:
        out.append(f"steps must be hello, every first_boot.ITEMS id in order, done: got {ids}")
    if not w:
        return out + [f"no enabled words file for {lang!r}"]
    max_chars = int(w.get("max_chars", 160))
    for key in COMMON_WORDS:
        if not w.get("common", {}).get(key):
            out.append(f"common.{key} missing")
    for s in steps():
        sid = s["id"]
        if s.get("mode") not in MODES:
            out.append(f"{sid}: bad mode {s.get('mode')!r}")
        if s.get("mode") == "qr" and not s.get("qr"):
            out.append(f"{sid}: a qr step names no qr hand-off")
        if (s.get("backed_by") or {}).get("status") not in STATUSES or not (s.get("backed_by") or {}).get("ref"):
            out.append(f"{sid}: backed_by needs a status and a ref")
        if int(s.get("budget_s", 0)) <= 0:
            out.append(f"{sid}: no declared budget")
        item = fb.BY_ID.get(sid)
        if item is not None and item.tier == fb.BLOCKING and sid in ("owner_account", "panel_paired") and s.get("later_allowed"):
            out.append(f"{sid}: everything after it waits on it, so 'later' must explain, not defer")
        sw = w.get("steps", {}).get(sid) or {}
        for key in REQUIRED_WORDS:
            if key not in sw:
                out.append(f"{sid}: words.{key} missing")
        for key, text in sw.items():
            if isinstance(text, str) and len(text) > max_chars:
                out.append(f"{sid}.{key}: {len(text)} chars > {max_chars}")
    out += [f"dead end: {sid} on {b}" for sid, b in dead_ends(lang)]
    if core_budget_s() > 600:
        out.append(f"the core flow is declared at {core_budget_s()} s; the hello line promises about ten minutes")
    return out
