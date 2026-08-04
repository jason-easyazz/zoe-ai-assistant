"""Offline tests for the voice-corpus curator + the corpus-listing contract.

Two things are pinned here:

1. ``scripts/maintenance/curate_voice_corpus.py`` — the classifier (format probe
   + threshold decision), the move planner, and the "never deletes" property.
   All synthetic WAVs, no model, no live corpus: runs in the fast ``ci_safe``
   lane. The VAD-dependent half is a separate host-only test at the bottom
   (``importorskip`` + model-present skip, matching test_voice_barge_in.py).

2. **Quarantine subdirectories must stay invisible to the replay probe.** The
   curator moves failures into ``<corpus>/quarantine-*-<date>/`` rather than
   deleting them, which is only safe because every corpus consumer globs the top
   level. That is proved EXECUTABLY: the real source of
   ``services/zoe-data/tests/replay_samples.py::_select`` is extracted and run
   against a fixture tree. If it ever grows a recursive glob, this goes red —
   the same class of silent corpus drift that reddened #1642.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import struct
import sys
import types
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load(mod_name: str, rel: str):
    spec = importlib.util.spec_from_file_location(mod_name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod  # register before exec (annotation resolution)
    spec.loader.exec_module(mod)
    return mod


cvc = _load("curate_voice_corpus", "scripts/maintenance/curate_voice_corpus.py")

CURATOR_SRC = (REPO / "scripts/maintenance/curate_voice_corpus.py").read_text()
REPLAY_SRC = (REPO / "services/zoe-data/tests/replay_samples.py").read_text()


# ── fixtures ──────────────────────────────────────────────────────────────────

def _write_wav(path: Path, *, rate: int = 16000, channels: int = 1,
               sampwidth: int = 2, frames: int = 1600, value: int = 1000) -> Path:
    """A synthetic WAV with fully controllable header parameters."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        if sampwidth == 2:
            payload = struct.pack(f"<{frames * channels}h", *([value] * frames * channels))
        else:
            payload = bytes([128] * frames * channels * sampwidth)
        w.writeframes(payload)
    return path


# ── format probe ──────────────────────────────────────────────────────────────

def test_probe_format_accepts_the_corpus_contract(tmp_path):
    info = cvc.probe_format(_write_wav(tmp_path / "good.wav"))
    assert info["ok"] is True and info["reason"] is None
    assert (info["rate"], info["channels"], info["sampwidth"]) == (16000, 1, 2)


@pytest.mark.parametrize("kwargs,needle", [
    ({"rate": 24000}, "rate=24000"),           # the measured 100-file class
    ({"rate": 8000}, "rate=8000"),
    ({"channels": 2}, "channels=2"),
    ({"sampwidth": 1}, "sampwidth=1B"),
    ({"frames": 0}, "zero audio frames"),
])
def test_probe_format_rejects_off_contract_files(tmp_path, kwargs, needle):
    info = cvc.probe_format(_write_wav(tmp_path / "bad.wav", **kwargs))
    assert info["ok"] is False
    assert needle in info["reason"]


def test_probe_format_rejects_invalid_riff(tmp_path):
    p = tmp_path / "notriff.wav"
    p.write_bytes(b"this is not a RIFF header at all, not even close")
    info = cvc.probe_format(p)
    assert info["ok"] is False
    assert "unreadable WAV" in info["reason"]


# ── classifier ────────────────────────────────────────────────────────────────

def test_classify_keeps_valid_speech():
    klass, reason = cvc.classify({"ok": True}, 0.83, 0.20)
    assert klass == cvc.CLASS_KEEP
    assert "0.830" in reason and "BORDERLINE" not in reason


def test_classify_quarantines_clear_nonspeech():
    klass, reason = cvc.classify({"ok": True}, 0.05, 0.20)
    assert klass == cvc.CLASS_NONSPEECH
    assert "0.050" in reason and "0.20" in reason


def test_classify_keeps_borderline_between_quarantine_and_runtime_threshold():
    """0.20 <= peak < 0.50 is quiet/distant REAL speech — the hard samples the
    gate most needs. Kept, flagged, never moved."""
    klass, reason = cvc.classify({"ok": True}, 0.35, 0.20)
    assert klass == cvc.CLASS_KEEP
    assert "BORDERLINE" in reason
    assert cvc.is_borderline(klass, 0.35) is True
    assert cvc.is_borderline(klass, 0.83) is False


def test_threshold_is_load_bearing_negative_control():
    """Flip the threshold across the same score and the verdict MUST flip.

    If this stays green with both thresholds, the threshold is not being read
    and every quarantine decision is unproven.
    """
    assert cvc.classify({"ok": True}, 0.30, 0.20)[0] == cvc.CLASS_KEEP
    assert cvc.classify({"ok": True}, 0.30, 0.50)[0] == cvc.CLASS_NONSPEECH


def test_format_failure_beats_a_perfect_speech_score():
    """An off-format file is unusable to the replay path whatever it contains."""
    klass, reason = cvc.classify({"ok": False, "reason": "rate=24000 (want 16000)"}, 0.99, 0.20)
    assert klass == cvc.CLASS_FORMAT
    assert "24000" in reason


def test_unscored_files_are_never_quarantined():
    """peak=None means 'no evidence'. Absent evidence never moves a file."""
    assert cvc.classify({"ok": True}, None, 0.20)[0] == cvc.CLASS_KEEP


# ── scan / plan / apply ───────────────────────────────────────────────────────

def _fixture_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    _write_wav(corpus / "100000_001.wav")                 # good
    _write_wav(corpus / "100001_002.wav")                 # good
    _write_wav(corpus / "100002_003.wav", rate=24000)     # format
    (corpus / "100003_004.wav").write_bytes(b"nope")      # format (bad RIFF)
    _write_wav(corpus / "quarantine-old-20260719" / "100004_005.wav")  # already out
    return corpus


def test_list_corpus_is_top_level_only(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    names = [os.path.basename(p) for p in cvc.list_corpus(corpus)]
    assert names == ["100000_001.wav", "100001_002.wav", "100002_003.wav", "100003_004.wav"]
    assert "100004_005.wav" not in names, "quarantined audio must not be re-scanned"


def test_scan_and_plan_without_vad(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    rows = cvc.scan(str(corpus), 0.20, vad_factory=None)
    by_class = {r["file"]: r["class"] for r in rows}
    assert by_class["100000_001.wav"] == cvc.CLASS_KEEP
    assert by_class["100002_003.wav"] == cvc.CLASS_FORMAT
    assert by_class["100003_004.wav"] == cvc.CLASS_FORMAT

    plan = cvc.plan_moves(rows, str(corpus), "20260804")
    assert {p["file"] for p in plan} == {"100002_003.wav", "100003_004.wav"}
    for item in plan:
        assert item["dest_dir"].endswith("quarantine-format-20260804")
        assert item["conflict"] is False


def test_scan_scores_speech_through_the_injected_vad(tmp_path):
    """The scorer is injected, so the whole pipeline is testable without a model."""
    corpus = tmp_path / "corpus"
    _write_wav(corpus / "speech.wav")
    _write_wav(corpus / "noise.wav")

    def fake_score(path, _factory):
        peak = 0.9 if "speech" in os.path.basename(str(path)) else 0.01
        return {"peak": peak, "mean": peak, "hops": 3, "frac_speech_hops": peak,
                "rms": 100.0, "duration_s": 0.1}

    orig = cvc.score_speech
    cvc.score_speech = fake_score
    try:
        rows = cvc.scan(str(corpus), 0.20, vad_factory=lambda: object())
    finally:
        cvc.score_speech = orig
    by_class = {r["file"]: r["class"] for r in rows}
    assert by_class["speech.wav"] == cvc.CLASS_KEEP
    assert by_class["noise.wav"] == cvc.CLASS_NONSPEECH


def test_dry_run_moves_nothing(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    before = sorted(os.listdir(corpus))
    rows = cvc.scan(str(corpus), 0.20, vad_factory=None)
    plan = cvc.plan_moves(rows, str(corpus), "20260804")
    result = cvc.apply_moves(plan, str(corpus), 0.20, None, execute=False)
    assert result["moved"] == 0
    assert sorted(os.listdir(corpus)) == before


def test_execute_moves_and_writes_a_manifest(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    rows = cvc.scan(str(corpus), 0.20, vad_factory=None)
    plan = cvc.plan_moves(rows, str(corpus), "20260804")
    result = cvc.apply_moves(plan, str(corpus), 0.20, "deadbeef", execute=True)

    assert result["moved"] == 2 and not result["errors"]
    top = set(os.listdir(corpus))
    assert "100002_003.wav" not in top and "100003_004.wav" not in top
    qdir = corpus / "quarantine-format-20260804"
    # MOVED, never deleted — the bytes are still on disk.
    assert (qdir / "100002_003.wav").exists() and (qdir / "100003_004.wav").exists()

    manifest = json.loads((qdir / "manifest.json").read_text())
    assert manifest["speech_threshold"] == 0.20
    assert manifest["vad_model_sha256"] == "deadbeef"
    entries = {e["file"]: e for e in manifest["entries"]}
    assert set(entries) == {"100002_003.wav", "100003_004.wav"}
    for entry in entries.values():
        assert entry["reason"] and entry["mtime"] and entry["mtime_iso"]
        assert "class" in entry and "scores" in entry


def test_rerun_merges_into_the_existing_manifest(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    rows = cvc.scan(str(corpus), 0.20, vad_factory=None)
    cvc.apply_moves(cvc.plan_moves(rows, str(corpus), "20260804"),
                    str(corpus), 0.20, None, execute=True)
    _write_wav(corpus / "110000_009.wav", rate=24000)
    rows2 = cvc.scan(str(corpus), 0.20, vad_factory=None)
    cvc.apply_moves(cvc.plan_moves(rows2, str(corpus), "20260804"),
                    str(corpus), 0.20, None, execute=True)

    manifest = json.loads((corpus / "quarantine-format-20260804" / "manifest.json").read_text())
    assert {e["file"] for e in manifest["entries"]} == {
        "100002_003.wav", "100003_004.wav", "110000_009.wav"}


def test_a_name_collision_never_overwrites(tmp_path):
    """An overwrite is a delete in disguise — refuse and leave the file in place."""
    corpus = _fixture_corpus(tmp_path)
    qdir = corpus / "quarantine-format-20260804"
    qdir.mkdir()
    (qdir / "100002_003.wav").write_bytes(b"PRIOR QUARANTINE CONTENT")

    rows = cvc.scan(str(corpus), 0.20, vad_factory=None)
    plan = cvc.plan_moves(rows, str(corpus), "20260804")
    result = cvc.apply_moves(plan, str(corpus), 0.20, None, execute=True)

    assert result["conflicts"] == 1
    assert (qdir / "100002_003.wav").read_bytes() == b"PRIOR QUARANTINE CONTENT"
    assert (corpus / "100002_003.wav").exists(), "collided file stays in the corpus"
    assert result["moved"] == 1  # the other format failure still moved


def test_curator_has_no_delete_path():
    """The corpus is permanent evidence: quarantine is a MOVE, forever.

    An AST-level ban (not a grep) so a delete cannot sneak in as an alias.
    """
    banned = {"remove", "unlink", "rmtree", "removedirs", "rmdir"}
    tree = ast.parse(CURATOR_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            assert name not in banned, f"curator must never delete (found {name}())"


def test_default_threshold_is_conservative_relative_to_runtime():
    """The curation line must sit BELOW the live VAD decision threshold.

    Quarantining at the runtime threshold would evict the ~11% of real captures
    that score under it (#1642's census) — quiet, distant, clipped-but-real
    speech, i.e. the hardest and most valuable gate samples.
    """
    assert cvc.DEFAULT_SPEECH_THRESHOLD < cvc.RUNTIME_SPEECH_THRESHOLD
    assert cvc.DEFAULT_SPEECH_THRESHOLD == 0.20


# ── the probe's corpus selection must not recurse ─────────────────────────────

def _extract_select():
    """Execute the REAL ``_select`` from replay_samples.py in isolation.

    replay_samples imports the flue client at module scope (service deps), so it
    cannot be imported in the slim lane — but the function's own source can be
    compiled and run. That keeps this a behavioural proof rather than a grep.
    """
    tree = ast.parse(REPLAY_SRC)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_select")
    ns: dict = {"glob": __import__("glob"), "os": os}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "replay_samples._select", "exec"), ns)
    return ns["_select"]


def test_replay_probe_selection_excludes_quarantine_subdirs(tmp_path):
    """The load-bearing property that makes quarantine-by-move safe."""
    corpus = tmp_path / "corpus"
    _write_wav(corpus / "100000_001.wav")
    _write_wav(corpus / "100001_002.wav")
    _write_wav(corpus / "quarantine-nonspeech-20260804" / "090000_009.wav")
    _write_wav(corpus / "quarantine-tv-falsewakes-20260719" / "000438_185.wav")

    select = _extract_select()
    args = types.SimpleNamespace(since=None, last=None)
    picked = [os.path.basename(p) for p in select(str(corpus), args)]
    assert picked == ["100000_001.wav", "100001_002.wav"]

    # …and the "newest N" slice the probe actually uses stays clean too.
    args_last = types.SimpleNamespace(since=None, last=3)
    picked_last = [os.path.basename(p) for p in select(str(corpus), args_last)]
    assert "090000_009.wav" not in picked_last
    assert "000438_185.wav" not in picked_last
    assert len(picked_last) == 2


def test_barge_in_corpus_replay_also_globs_top_level_only():
    """The second corpus consumer (host-only real-voice barge replay)."""
    src = (REPO / "services/zoe-data/tests/test_voice_barge_in.py").read_text()
    assert 'glob.glob(os.path.join(_CORPUS_DIR, "*.wav"))' in src
    assert "rglob" not in src and "os.walk" not in src


# ── host-only: the REAL Silero path ───────────────────────────────────────────

_MODEL_PATH = "/home/zoe/models/silero_vad.onnx"


@pytest.mark.skipif(not os.path.isfile(_MODEL_PATH), reason="Silero model not on this host")
def test_score_speech_runs_the_real_vad(tmp_path):
    pytest.importorskip("onnxruntime")
    pytest.importorskip("numpy")
    factory = cvc.load_vad_factory(str(REPO / "services" / "zoe-data"))
    if factory() is None:
        pytest.skip("Silero VAD failed to load on this host")

    # Digital silence: unambiguous non-speech, so the scorer must report a peak
    # under the quarantine line (this also proves the scorer is really running —
    # a stubbed-out scorer would not distinguish anything).
    silent = _write_wav(tmp_path / "silence.wav", value=0)
    scores = cvc.score_speech(silent, factory)
    assert 0.0 <= scores["peak"] <= 1.0
    assert scores["hops"] > 0, "the real streaming path must complete hops"
    assert scores["peak"] < cvc.DEFAULT_SPEECH_THRESHOLD
    assert cvc.classify({"ok": True}, scores["peak"], cvc.DEFAULT_SPEECH_THRESHOLD)[0] == \
        cvc.CLASS_NONSPEECH
