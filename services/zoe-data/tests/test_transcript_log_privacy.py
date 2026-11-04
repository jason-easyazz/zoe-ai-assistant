"""What someone said never reaches an INFO/WARNING log line - only its length and a hash (privacy fix 2026-11-03).

The service log (``~/.zoe-logs``) and the panel's ``voice.log`` sit outside the forget cascade and rotate by size, yet they
carried raw utterances: ``ROUTER_SHADOW text=``, ``voice/turn_stream ... transcript=``, ``intent_miss: <text>``, the panel's
``turn_stream TTFA=... transcript=``. Each now logs ``log_redaction.utterance_tag`` (``len=N utt=<12 hex>``), the same digest
the router shadow log keys its records by.

Two layers:
- behaviour: ``utterance_tag`` carries no word of the input, is stable, and equals the shadow log's ``utt`` key; the panel
  daemon's copy is byte-identical; the log readers (``panel_voice_log``, ``struggle_replay_eval``) parse the new lines.
- a static pin over the three files: no logger call at INFO or above passes a raw-utterance variable (``text``,
  ``transcript``, ...) as an argument. Negative control: the same scanner flags the pre-fix form.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import logging
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
for p in (ROOT, REPO / "scripts" / "lib"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import log_redaction  # noqa: E402

pytestmark = pytest.mark.ci_safe

UTTERANCE = "remind me to call grandma about the hospital on friday"
TAG = re.compile(r"^len=\d+ utt=[0-9a-f]{12}$")

FILES = (
    ROOT / "routers" / "voice_tts.py",
    ROOT / "intent_router.py",
    REPO / "scripts" / "setup" / "zoe_voice_daemon.py",
)
# Variables that hold what a person said in those files. Wrapping one in a call that reduces it (the tag, len) is fine.
RAW = {"text", "transcript", "normalized_text", "primary", "final_transcript", "utterance"}
SAFE_CALLS = {"_utt_tag", "utterance_tag", "len", "bool", "type"}
LOUD = {"info", "warning", "error", "critical", "exception"}


def _raw_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Call):
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
        if name in SAFE_CALLS:
            return []
    if isinstance(node, ast.Name) and node.id in RAW:
        return [node.id]
    if isinstance(node, ast.IfExp):   # ``"hit" if text else "fallback"``: the condition is not logged, only a branch is
        return _raw_names(node.body) + _raw_names(node.orelse)
    if isinstance(node, ast.Compare):  # a comparison logs a bool, not the words
        return []
    out: list[str] = []
    for child in ast.iter_child_nodes(node):
        out += _raw_names(child)
    return out


def raw_log_calls(source: str) -> list[tuple[int, str]]:
    """(line, variable) for every logger.<loud>(...) call that passes a raw utterance variable as a format argument."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LOUD):
            continue
        recv = node.func.value
        if not (isinstance(recv, ast.Name) and recv.id in {"logger", "log", "_log", "LOG"}):
            continue
        for arg in node.args:   # the message too: an f-string or "..." % text embeds the words before logging sees them
            hits += [(node.lineno, n) for n in _raw_names(arg)]
    return hits


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_raw_utterance_in_loud_log_lines(path):
    assert raw_log_calls(path.read_text(encoding="utf-8")) == []


def test_scanner_catches_the_pre_fix_forms():
    # negative control: the exact shapes that shipped before this fix
    pre = '''
logger.warning("ROUTER_SHADOW text=%r -> routed=%s", text[:70], rr)
logger.info("voice/turn_stream panel=%s STT=%.2fs transcript=%r", panel_id, t_stt, transcript[:80])
logger.info("intent_miss: %s", text)
logger.info("ack: %r", (text or "")[:40])
log.info("turn_stream TTFA=%.2fs transcript=%r", ttfa, transcript[:80])
logger.info("ok %s", _utt_tag(text))
logger.debug("debug may carry %r", text)
logger.info(f"Transcript: {transcript}")
logger.warning("heard %s" % text)
'''
    assert [v for _, v in raw_log_calls(pre)] == ["text", "transcript", "text", "text", "transcript", "transcript", "text"]


def test_tag_has_no_words_and_joins_the_shadow_log():
    tag = log_redaction.utterance_tag(UTTERANCE)
    assert TAG.match(tag)
    for word in UTTERANCE.split():
        assert word not in tag
    assert tag == log_redaction.utterance_tag(UTTERANCE)            # stable: equal turns compare equal
    assert tag != log_redaction.utterance_tag(UTTERANCE + "?")
    # semantic_router's shadow record key: sha256(text)[:12]
    assert tag.endswith("utt=" + hashlib.sha256(UTTERANCE.encode()).hexdigest()[:12])
    assert f"len={len(UTTERANCE)} " in tag
    assert TAG.match(log_redaction.utterance_tag(None)) and TAG.match(log_redaction.utterance_tag(""))


def test_rendered_record_carries_no_utterance(caplog):
    lg = logging.getLogger("zoe.test.transcript_privacy")
    with caplog.at_level(logging.INFO, logger=lg.name):
        lg.info("voice/turn_stream panel=%s STT=%.2fs %s", "p1", 0.4, log_redaction.utterance_tag(UTTERANCE))
    assert "grandma" not in caplog.text and "hospital" not in caplog.text
    assert "utt=" in caplog.text


def test_panel_daemon_tag_matches_the_box():
    # the daemon ships to the Pi alone, so it carries its own copy; it must stay byte-for-byte the same digest
    src = (REPO / "scripts" / "setup" / "zoe_voice_daemon.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "utterance_tag")
    ns: dict = {"hashlib": hashlib}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "daemon", "exec"), ns)  # noqa: S102 - our own source
    for s in (UTTERANCE, "", None, "lights off"):
        assert ns["utterance_tag"](s) == log_redaction.utterance_tag(s)


def test_panel_voice_log_reads_both_line_forms():
    import panel_voice_log as pvl

    tag = log_redaction.utterance_tag("lights off")
    new = f"2026-11-03 20:00:01,123 [voice-daemon] INFO turn_stream TTFA=0.61s {tag}"
    old = "2026-10-16 21:00:12,000 [voice-daemon] INFO turn_stream TTFA=0.59s transcript='what time is it'"
    got = [e.data["transcript"] for e in pvl.events([new, old]) if e.kind == "turn"]
    assert got == [tag, "what time is it"]


def test_long_speech_tool_tells_a_hash_only_capture_from_a_missing_one(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    spec = importlib.util.spec_from_file_location("mlsc", REPO / "scripts" / "perf" / "measure_long_speech_capture.py")
    ml = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(ml)
    except ImportError as exc:   # the perf tool's heavier imports are optional on a slim lane
        pytest.skip(f"perf tool not importable here: {exc}")
    (tmp_path / "zoe-data.app.log").write_text(
        "x STT_CAPTURE file=/s/120000_001.wav moonshine " + log_redaction.utterance_tag("lights off") + "\n"
        "x STT_CAPTURE file=/s/110000_002.wav moonshine='turn it up'\n")
    monkeypatch.setattr(ml, "LOG_DIR", tmp_path)
    assert ml.logged_transcript("120000_001") == ml.HASH_ONLY
    assert ml.logged_transcript("110000_002") == "turn it up"
    assert ml.logged_transcript("999999_999") is None
    assert "hash-only" in ml.classify(ml.HASH_ONLY)["transcript"]
    assert ml.classify(None)["transcript"] == "not found in ~/.zoe-logs"


def test_struggle_replay_reads_both_line_forms():
    spec = importlib.util.spec_from_file_location("struggle_replay_eval",
                                                  REPO / "scripts" / "maintenance" / "struggle_replay_eval.py")
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    tag = log_redaction.utterance_tag("lights off")
    m = ev._TURN.search(f"voice/turn_stream panel=zoe-touch-pi STT=0.42s {tag}")
    assert m and m.group(3) is None and m.group(4) == tag
    m = ev._TURN.search("voice/turn_stream panel=zoe-touch-pi STT=0.42s transcript='lights off'")
    assert m and m.group(3) == "'lights off'"
    r = ev._ROUTE.search(f"ROUTER_SHADOW {tag} -> routed=home (best=home 0.81) 3.2ms scores={{'home': 0.81}}")
    assert r and r.group(1) == "home"
    assert ev._ROUTE.search("ROUTER_SHADOW text='x' -> routed=home (best=home 0.81) 3.2ms scores={'home': 0.81}")
