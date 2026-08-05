"""Offline tests for the voice-corpus curator + the corpus-listing contract.

Two things are pinned here:

1. ``scripts/maintenance/curate_voice_corpus.py`` — the classifier (format probe
   + threshold decision), the move planner, and the "never deletes" property.
   All synthetic WAVs, no model, no live corpus. The module-level ``ci_safe``
   marker covers EVERY test here including the real-VAD one at the bottom — it
   is inert rather than excluded, kept synthetic by a model-file ``skipif`` plus
   ``importorskip``, and a test below pins both guards so the slim lane can
   never actually load a model (cross-review, #1643: the earlier wording here
   called it "a separate host-only test", which was false in marker terms).

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


def _run_main(argv: list[str]) -> int:
    """Drive the real CLI entrypoint and return its exit code."""
    saved = sys.argv
    sys.argv = ["curate_voice_corpus.py", *argv]
    try:
        return cvc.main()
    finally:
        sys.argv = saved


# ── format probe ──────────────────────────────────────────────────────────────

def test_probe_format_accepts_the_corpus_contract(tmp_path):
    info = cvc.probe_format(_write_wav(tmp_path / "good.wav"))
    assert info["ok"] is True and info["reason"] is None
    assert info["conforms"] is True and info["drift"] is None
    assert (info["rate"], info["channels"], info["sampwidth"]) == (16000, 1, 2)


@pytest.mark.parametrize("kwargs,needle", [
    ({"rate": 24000}, "rate=24000"),           # the measured 95-file class
    ({"rate": 8000}, "rate=8000"),
    ({"channels": 2}, "channels=2"),
    ({"sampwidth": 1}, "sampwidth=1B"),
])
def test_off_contract_but_readable_files_stay_usable(tmp_path, kwargs, needle):
    """DRIFT, not failure — the replay path resamples/downmixes these.

    The Codex P2 on #1643: ``_prepare_audio_for_moonshine`` (voice_tts.py:2071)
    resamples off-rate audio to 16 kHz before transcription, so a 24 kHz mono
    s16 capture is a perfectly good regression sample. Quarantining it would
    shrink the gate's evidence base for a capture-time defect. The mismatch is
    reported as ``drift`` and the file is KEPT.
    """
    info = cvc.probe_format(_write_wav(tmp_path / "drifted.wav", **kwargs))
    assert info["ok"] is True, "readable audio must remain usable"
    assert info["reason"] is None
    assert info["conforms"] is False
    assert needle in info["drift"]


@pytest.mark.parametrize("write,needle", [
    (lambda p: _write_wav(p, frames=0), "zero audio frames"),
    (lambda p: p.write_bytes(b"this is not a RIFF header at all"), "unreadable WAV"),
    (lambda p: p.write_bytes(b""), "unreadable WAV"),
])
def test_probe_format_rejects_only_genuinely_unusable_audio(tmp_path, write, needle):
    """The quarantine class is exactly what the STT path itself refuses."""
    p = tmp_path / "bad.wav"
    write(p)
    info = cvc.probe_format(p)
    assert info["ok"] is False
    assert needle in info["reason"]


def test_probe_format_rejects_an_unresampleable_rate(tmp_path):
    """rate <= 0 is the one rate ``_prepare_audio_for_moonshine`` will not fake.

    It returns the samples with the bad rate rather than pretending 16 kHz, so
    the audio cannot honestly be replayed — this IS a quarantine reason, and it
    is the boundary that separates the two classes above.
    """
    p = _write_wav(tmp_path / "zerorate.wav")
    raw = bytearray(p.read_bytes())
    # Zero the sample-rate field in the canonical 44-byte RIFF/WAVE header.
    raw[24:28] = (0).to_bytes(4, "little")
    p.write_bytes(bytes(raw))

    info = cvc.probe_format(p)

    assert info["ok"] is False
    assert "invalid sample rate" in info["reason"]


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


def test_unusable_audio_beats_a_perfect_speech_score():
    """A file the STT path cannot READ is unusable whatever it contains."""
    klass, reason = cvc.classify({"ok": False, "reason": "unreadable WAV (Error: …)"}, 0.99, 0.20)
    assert klass == cvc.CLASS_FORMAT
    assert "unreadable" in reason


def test_contract_drift_does_not_quarantine_a_speech_file():
    """The #1643 fix, at the classifier: drift annotates, it does not evict."""
    fmt = {"ok": True, "conforms": False, "drift": "rate=24000 (contract 16000)"}

    klass, reason = cvc.classify(fmt, 0.99, 0.20)

    assert klass == cvc.CLASS_KEEP
    assert "DRIFT" in reason and "24000" in reason


def test_drift_does_not_rescue_a_nonspeech_file():
    """Negative control on the annotation: it must not become an escape hatch.

    VAD scoring still applies to drifted files — a drifted capture below the
    threshold is still quarantined as non-speech, on the non-speech class.
    """
    fmt = {"ok": True, "conforms": False, "drift": "rate=24000 (contract 16000)"}

    klass, reason = cvc.classify(fmt, 0.05, 0.20)

    assert klass == cvc.CLASS_NONSPEECH
    assert "DRIFT" in reason


def test_unscored_files_are_never_quarantined():
    """peak=None means 'no evidence'. Absent evidence never moves a file."""
    assert cvc.classify({"ok": True}, None, 0.20)[0] == cvc.CLASS_KEEP


# ── scan / plan / apply ───────────────────────────────────────────────────────

def _fixture_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    _write_wav(corpus / "100000_001.wav")                 # good
    _write_wav(corpus / "100001_002.wav")                 # good
    _write_wav(corpus / "100002_003.wav", rate=24000)     # DRIFT — kept, not moved
    (corpus / "100003_004.wav").write_bytes(b"nope")      # format (bad RIFF)
    _write_wav(corpus / "100005_006.wav", frames=0)       # format (empty)
    _write_wav(corpus / "quarantine-old-20260719" / "100004_005.wav")  # already out
    return corpus


def test_list_corpus_is_top_level_only(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    names = [os.path.basename(p) for p in cvc.list_corpus(corpus)]
    assert names == ["100000_001.wav", "100001_002.wav", "100002_003.wav",
                     "100003_004.wav", "100005_006.wav"]
    assert "100004_005.wav" not in names, "quarantined audio must not be re-scanned"


def test_scan_and_plan_without_vad(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    rows = cvc.scan(str(corpus), 0.20, vad_factory=None)
    by_class = {r["file"]: r["class"] for r in rows}
    by_drift = {r["file"]: r["drift"] for r in rows}
    assert by_class["100000_001.wav"] == cvc.CLASS_KEEP
    # The #1643 fix: a 24 kHz capture is REPORTED, not quarantined.
    assert by_class["100002_003.wav"] == cvc.CLASS_KEEP
    assert by_drift["100002_003.wav"] is True
    assert by_drift["100000_001.wav"] is False
    assert by_class["100003_004.wav"] == cvc.CLASS_FORMAT
    assert by_class["100005_006.wav"] == cvc.CLASS_FORMAT

    assert cvc.summarise(rows, 0.20)["drift"] == 1

    plan = cvc.plan_moves(rows, str(corpus), "20260804")
    assert {p["file"] for p in plan} == {"100003_004.wav", "100005_006.wav"}
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


# ── VAD input normalisation (the other half of the #1643 fix) ─────────────────

def test_conforming_audio_reaches_the_vad_byte_identical():
    """Identity by construction for 16 kHz mono s16 — no census can shift.

    Keeping drifted files only helps if scoring them cannot perturb the files
    that were already being scored. A conforming buffer must come back as the
    SAME object/bytes, not a round-tripped copy.
    """
    raw = struct.pack("<1600h", *([1000] * 1600))

    assert cvc._to_vad_pcm(raw, 16000, 1, 2) == raw


def test_a_drifted_capture_is_resampled_before_the_vad_hears_it():
    """The VAD's contract is 16 kHz. Feeding it 24 kHz bytes as if they were
    16 kHz would mis-score exactly the files this PR now keeps — mirror what
    ``_prepare_audio_for_moonshine`` does instead."""
    raw = struct.pack("<2400h", *([1000] * 2400))  # 0.1 s @ 24 kHz

    out = cvc._to_vad_pcm(raw, 24000, 1, 2)

    assert len(out) // 2 == 1600, "0.1 s @ 24 kHz must become 0.1 s @ 16 kHz"
    assert out != raw
    assert set(struct.unpack(f"<{len(out) // 2}h", out)) == {1000}, "constant in, constant out"


def test_a_stereo_capture_is_downmixed_before_the_vad_hears_it():
    raw = struct.pack("<1600h", *([1000, 2000] * 800))  # 800 stereo frames

    out = cvc._to_vad_pcm(raw, 16000, 2, 2)

    assert len(out) // 2 == 800
    assert set(struct.unpack("<800h", out)) == {1500}, "L/R mean, not interleaved bytes"


def test_a_width_this_tool_cannot_decode_is_unscorable_not_quarantined(tmp_path):
    """Absent evidence never moves a file — the rule the whole tool rests on.

    An 8-bit capture is readable (so not a format quarantine) but this tool
    declines to invent an s16 decode for it. That must surface as "not scored"
    and KEEP, never as a quarantine.
    """
    with pytest.raises(cvc.UnscorableAudio):
        cvc._to_vad_pcm(b"\x80" * 1600, 16000, 1, 1)

    corpus = tmp_path / "corpus"
    _write_wav(corpus / "eightbit.wav", sampwidth=1)

    rows = cvc.scan(str(corpus), 0.20, vad_factory=lambda: object())

    assert rows[0]["class"] == cvc.CLASS_KEEP
    assert rows[0]["scores"] is None
    assert "UnscorableAudio" in rows[0]["score_error"]


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
    assert "100003_004.wav" not in top and "100005_006.wav" not in top
    assert "100002_003.wav" in top, "a resampleable 24 kHz capture STAYS in the corpus"
    qdir = corpus / "quarantine-format-20260804"
    # MOVED, never deleted — the bytes are still on disk.
    assert (qdir / "100003_004.wav").exists() and (qdir / "100005_006.wav").exists()

    manifest = json.loads((qdir / "manifest.json").read_text())
    assert manifest["speech_threshold"] == 0.20
    assert manifest["vad_model_sha256"] == "deadbeef"
    entries = {e["file"]: e for e in manifest["entries"]}
    assert set(entries) == {"100003_004.wav", "100005_006.wav"}
    for entry in entries.values():
        assert entry["reason"] and entry["mtime"] and entry["mtime_iso"]
        assert "class" in entry and "scores" in entry


def test_rerun_merges_into_the_existing_manifest(tmp_path):
    corpus = _fixture_corpus(tmp_path)
    rows = cvc.scan(str(corpus), 0.20, vad_factory=None)
    cvc.apply_moves(cvc.plan_moves(rows, str(corpus), "20260804"),
                    str(corpus), 0.20, None, execute=True)
    _write_wav(corpus / "110000_009.wav", frames=0)
    rows2 = cvc.scan(str(corpus), 0.20, vad_factory=None)
    cvc.apply_moves(cvc.plan_moves(rows2, str(corpus), "20260804"),
                    str(corpus), 0.20, None, execute=True)

    manifest = json.loads((corpus / "quarantine-format-20260804" / "manifest.json").read_text())
    assert {e["file"] for e in manifest["entries"]} == {
        "100003_004.wav", "100005_006.wav", "110000_009.wav"}


def test_a_name_collision_never_overwrites(tmp_path):
    """An overwrite is a delete in disguise — refuse and leave the file in place."""
    corpus = _fixture_corpus(tmp_path)
    qdir = corpus / "quarantine-format-20260804"
    qdir.mkdir()
    (qdir / "100003_004.wav").write_bytes(b"PRIOR QUARANTINE CONTENT")

    rows = cvc.scan(str(corpus), 0.20, vad_factory=None)
    plan = cvc.plan_moves(rows, str(corpus), "20260804")
    result = cvc.apply_moves(plan, str(corpus), 0.20, None, execute=True)

    assert result["conflicts"] == 1
    assert (qdir / "100003_004.wav").read_bytes() == b"PRIOR QUARANTINE CONTENT"
    assert (corpus / "100003_004.wav").exists(), "collided file stays in the corpus"
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


def test_the_real_vad_test_stays_inert_in_the_ci_safe_lane():
    """The module marker covers it, so its guards are what keep CI synthetic."""
    name = "def test_score_speech_runs_the_real_vad"
    src = Path(__file__).read_text()
    decorators = src.split(name, 1)[0].rsplit("@pytest.mark", 1)[1]
    assert "skipif(not os.path.isfile(_MODEL_PATH)" in decorators, "model guard lost"
    # ONLY that test's own body — this test quotes the same literal, and a
    # whole-file search would happily match itself.
    body = src.split(name, 1)[1].split("\ndef ", 1)[0]
    assert "importorskip(" + chr(34) + "onnxruntime" + chr(34) + ")" in body, "guard lost"


def test_a_capture_that_changed_after_classification_is_not_moved(tmp_path):
    """A live capture finishing mid-scan must not be quarantined on stale evidence.

    The corpus is auto-captured and ``_maybe_capture_stt`` writes with a plain
    ``shutil.copyfile`` to the final filename WITHOUT the harness lock, so the
    documented ``flock`` does not serialise capture writes against curation. A
    half-written WAV can therefore be classified (truncated header →
    "unreadable", partial audio → "non-speech") and then moved on a verdict that
    no longer describes the file (Codex + Greptile P1, #1643).
    """
    corpus = _fixture_corpus(tmp_path)
    rows = cvc.scan(str(corpus), 0.20, vad_factory=None)
    plan = cvc.plan_moves(rows, str(corpus), "20260805")
    assert plan, "fixture must plan at least one move"

    # The writer completes: the file is now a VALID capture, not the fragment
    # that was classified.
    victim = Path(plan[0]["source"])
    _write_wav(victim)

    res = cvc.apply_moves(plan, str(corpus), 0.20, None, execute=True)

    assert res["stale"] == 1
    assert res["moved"] == len(plan) - 1
    assert victim.exists(), "a file that changed after classification must stay put"
    assert not Path(plan[0]["dest"]).exists()
    assert any("changed on disk" in e for e in res["errors"])


def test_an_unchanged_capture_still_moves(tmp_path):
    """NEGATIVE CONTROL for the freshness check: it must not block normal moves.

    If the stat comparison were wrong in the other direction (say it compared
    something that always differs) the whole tool would silently stop working
    while every other test stayed green.
    """
    corpus = _fixture_corpus(tmp_path)
    rows = cvc.scan(str(corpus), 0.20, vad_factory=None)
    plan = cvc.plan_moves(rows, str(corpus), "20260805")

    res = cvc.apply_moves(plan, str(corpus), 0.20, None, execute=True)

    assert res["stale"] == 0
    assert res["moved"] == len(plan)


@pytest.mark.parametrize("bad", ["20", "-0.1", "1.5", "nan"])
def test_an_out_of_range_speech_threshold_is_refused_before_anything_moves(tmp_path, bad):
    """`--speech-threshold 20` (a mistyped 0.20) would quarantine the WHOLE corpus.

    Every scored WAV satisfies `peak < 20`. `nan` fails the other way — every
    comparison against it is False, so nothing is quarantined and the run
    reports a clean corpus. Both are validated away before any scan or move
    (Codex P2, #1643).
    """
    corpus = _fixture_corpus(tmp_path)
    before = sorted(os.listdir(corpus))

    rc = _run_main(["--corpus", str(corpus), "--speech-threshold", bad,
                    "--skip-vad", "--execute"])

    assert rc == 2
    assert sorted(os.listdir(corpus)) == before, "the corpus was touched despite the refusal"


def test_a_valid_threshold_is_still_accepted(tmp_path):
    """NEGATIVE CONTROL for the range check: the in-range path must survive."""
    corpus = _fixture_corpus(tmp_path)

    rc = _run_main(["--corpus", str(corpus), "--speech-threshold", "0.20", "--skip-vad"])

    assert rc == 0


def test_a_destination_appearing_after_planning_is_still_refused(tmp_path):
    """TOCTOU: shutil.move clobbers, so the check must be at MOVE time."""
    corpus = _fixture_corpus(tmp_path)
    rows = cvc.scan(str(corpus), 0.20, vad_factory=None)
    plan = cvc.plan_moves(rows, str(corpus), "20260805")
    assert plan and not any(p["conflict"] for p in plan), "fixture must plan clean moves"

    os.makedirs(plan[0]["dest_dir"], exist_ok=True)
    Path(plan[0]["dest"]).write_bytes(b"pre-existing, must survive")

    res = cvc.apply_moves(plan, str(corpus), 0.20, None, execute=True)

    assert res["conflicts"] == 1 and res["moved"] == len(plan) - 1
    assert Path(plan[0]["dest"]).read_bytes() == b"pre-existing, must survive"
    assert (corpus / plan[0]["file"]).exists(), "the source moved despite the conflict"
