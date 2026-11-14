"""The household-text inventory's extra forget reach (``ZOE_FORGET_REACH_EXTRA``, default off): the voice STT audit log and the
opt-in Telegram STT capture corpus join retention, the entity forget (cascade step ``text_logs``) and the member erase.

Planted synthetic sentinel only. The flag-off arm is the control: with the flag off nothing in either store is touched (the
pre-change behaviour), so each erase assertion below fails on the old code.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import pytest

import local_text_logs as ltl
import recall_gate_trace

pytestmark = pytest.mark.ci_safe

SENTINEL = "Quillon Marsh"                                  # synthetic


@pytest.fixture
def stores(tmp_path, monkeypatch):
    stt = tmp_path / "voice" / "voice_stt.jsonl"
    stt.parent.mkdir()
    cap = tmp_path / "capture"
    cap.mkdir()
    monkeypatch.setenv("ZOE_VOICE_STT_LOG", str(stt))
    monkeypatch.setenv("ZOE_TELEGRAM_STT_CAPTURE_DIR", str(cap))
    monkeypatch.setattr(ltl, "LOGS", ())                    # only the extra stores are in play
    monkeypatch.setattr(recall_gate_trace, "erase_entity", lambda rx: 0)
    monkeypatch.setattr(recall_gate_trace, "erase_all", lambda: 0)
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    rows = [{"ts": new, "route": "panel", "transcript": f"remind me to call {SENTINEL}"},
            {"ts": new, "route": "panel", "transcript": "turn the kitchen lights off"},
            {"ts": old, "route": "panel", "transcript": "what is the weather"}]
    stt.write_text("".join(json.dumps(r) + "\n" for r in rows))
    (stt.parent / "voice_stt.jsonl.1").write_text(json.dumps({"ts": new, "transcript": f"{SENTINEL} is coming"}) + "\n")
    for stem, words, age in (("tg-a", f"tell {SENTINEL} hello", 0), ("tg-b", "play some jazz", 0), ("tg-c", "old note", 90)):
        (cap / f"{stem}.txt").write_text(words + "\n")
        (cap / f"{stem}.wav").write_bytes(b"RIFF")
        if age:
            t = time.time() - age * 86400
            for suf in (".txt", ".wav"):
                os.utime(cap / f"{stem}{suf}", (t, t))
    (cap / "tg-orphan.wav").write_bytes(b"RIFF")              # audio whose transcript is gone: cannot be judged
    return stt, cap


def _all_text(stt) -> str:
    return "".join(p.read_text() for p in stt.parent.glob("voice_stt.jsonl*"))


def test_flag_off_leaves_both_stores_untouched(stores, monkeypatch):
    stt, cap = stores
    monkeypatch.delenv("ZOE_FORGET_REACH_EXTRA", raising=False)
    before = (_all_text(stt), sorted(p.name for p in cap.iterdir()))
    ltl.erase_entity(re.compile(SENTINEL))
    ltl.erase_all_text()
    ltl.sweep()
    assert (_all_text(stt), sorted(p.name for p in cap.iterdir())) == before
    assert ltl.active_logs() == ()


def test_entity_forget_drops_every_line_and_capture_naming_the_sentinel(stores, monkeypatch):
    stt, cap = stores
    monkeypatch.setenv("ZOE_FORGET_REACH_EXTRA", "on")
    n = ltl.erase_entity(re.compile(SENTINEL))
    text = _all_text(stt)
    assert SENTINEL not in text and "kitchen lights" in text and "weather" in text
    names = sorted(p.name for p in cap.iterdir())
    assert names == ["tg-b.txt", "tg-b.wav", "tg-c.txt", "tg-c.wav"]          # the sentinel's pair and the orphan wav are gone
    assert n == 2 + 3


def test_member_erase_clears_both_stores(stores, monkeypatch):
    stt, cap = stores
    monkeypatch.setenv("ZOE_FORGET_REACH_EXTRA", "on")
    ltl.erase_all_text()
    assert "transcript" not in _all_text(stt)
    assert list(cap.iterdir()) == []


def test_retention_drops_old_iso_stamped_lines_and_old_captures(stores, monkeypatch):
    stt, cap = stores
    monkeypatch.setenv("ZOE_FORGET_REACH_EXTRA", "on")
    monkeypatch.setenv("ZOE_TEXT_LOG_RETENTION_DAYS", "30")
    out = ltl.sweep()
    text = _all_text(stt)
    assert "weather" not in text and "kitchen lights" in text and SENTINEL in text   # age only: the new lines stay
    assert not (cap / "tg-c.txt").exists() and (cap / "tg-a.txt").exists()
    assert out["voice_stt"] == 1 and out["telegram_capture"] == 3              # tg-c pair + the orphan wav



def test_evolution_notice_never_logs_the_missed_words():
    """The intent-gap notice logged the cluster's representative utterance (``rep[:60]``) into the service log, which no forget
    reaches; it now logs ``log_redaction.utterance_tag``. Static pin: no logger call passes ``rep`` (bare or sliced)."""
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / "evolution_notice.py").read_text(encoding="utf-8"))
    bad = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("info", "warning", "debug", "error")
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "logger"):
            for a in n.args[1:]:
                base = a.value if isinstance(a, ast.Subscript) else a
                if isinstance(base, ast.Name) and base.id in ("rep", "members", "text", "texts"):
                    bad.append(n.lineno)
    assert not bad, f"evolution_notice.py logs missed-utterance text at line(s) {bad}"
