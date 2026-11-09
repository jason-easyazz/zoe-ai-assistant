"""The 12B's own audio as a re-pass ASR backend (``gemma4-12b-audio``): declared budget, the 12B-only url rule, the request shape, chunking, and junk-reply guards.
Synthetic audio only; the "12B" is a local HTTP double.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import base64
import http.server
import importlib.util
import io
import json
import threading
import wave
from pathlib import Path

import numpy as np

import omi_repass as rp
from test_omi_repass import fake_runner, first_pass, renv, rows, run, transcribe  # noqa: F401 - the re-pass's own fixture and doubles
from test_omi_recordings import env  # noqa: F401

WORKER = Path(__file__).resolve().parents[3] / "scripts/night/jobs/omi_repass_worker.py"


def _worker():
    spec = importlib.util.spec_from_file_location("omi_repass_worker_audio", WORKER)
    w = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(w)
    return w


def test_the_audio_backend_is_declared_with_its_budget_and_shares_the_12b():
    b = rp.BACKENDS[rp.AUDIO_ASR]
    assert b["runs_on"] == "12b" and b["extra_12b_mib"] >= 500 and b["rtf"] > 0 and b["fallback"] is None
    assert f'"{rp.AUDIO_ASR}"' in WORKER.read_text()


def test_the_audio_backend_refuses_the_live_4b_and_remote_urls_and_passes_the_12b_url(renv, monkeypatch):
    transcribe(first_pass(renv) and renv, monkeypatch)
    monkeypatch.setenv("ZOE_OMI_NIGHT_ASR", rp.AUDIO_ASR)
    monkeypatch.delenv("GEMMA_SERVER_URL", raising=False)
    assert run(rp.run_night(budget_s=600, runner=fake_runner()))["status"] == "wrong_stage"      # the 12B-audio backend run BEFORE the 12B is loaded is refused
    assert run(rp.run_night(budget_s=600, runner=fake_runner(), stage_12b=True))["status"] == "needs_12b"
    monkeypatch.setenv("GEMMA_SERVER_URL", "http://127.0.0.1:11434/v1")                          # the daytime brain is never used
    assert run(rp.run_night(budget_s=600, runner=fake_runner(), stage_12b=True))["status"] == "needs_12b"
    monkeypatch.setenv("GEMMA_SERVER_URL", "http://example.invalid:11500/v1")                    # never remote
    assert run(rp.run_night(budget_s=600, runner=fake_runner(), stage_12b=True))["status"] == "needs_12b"
    assert rows(renv)[0]["repass_state"] == "pending"                                          # nothing touched
    monkeypatch.setenv("GEMMA_SERVER_URL", "http://127.0.0.1:11500/v1")
    seen: list = []
    out = run(rp.run_night(budget_s=600, runner=fake_runner(seen=seen), stage_12b=True))
    assert out["status"] == "ok" and out["model"] == rp.AUDIO_ASR and seen[0]["asr"] == rp.AUDIO_ASR and seen[0]["llm_url"] == "http://127.0.0.1:11500/v1"
    assert rows(renv)[0]["repass_state"] == "done" and rows(renv)[0]["repass_model"] == f"{rp.AUDIO_ASR}+ecapa"


def test_a_cpu_backend_is_refused_inside_the_12b_stage(renv, monkeypatch):
    """The mirror guard: a mismatch (--audio-mmproj set, ZOE_OMI_NIGHT_ASR left on whisper) must not load 2.8 GB beside the loaded 12B."""
    transcribe(first_pass(renv) and renv, monkeypatch)
    monkeypatch.setenv("GEMMA_SERVER_URL", "http://127.0.0.1:11500/v1")
    assert run(rp.run_night(budget_s=600, runner=fake_runner(), stage_12b=True))["status"] == "wrong_stage"
    assert rows(renv)[0]["repass_state"] == "pending"


def test_the_request_is_a_verbatim_input_audio_chunk_with_the_name_hint():
    w = _worker()
    got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got["path"] = self.path
            got["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            out = json.dumps({"choices": [{"message": {"content": "Hello there Billie"}}], "usage": {"completion_tokens": 4, "prompt_tokens": 90}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = w.gemma_transcribe(f"http://127.0.0.1:{srv.server_port}/v1", w.wav_bytes(np.zeros(16000 * 3, dtype="float32")), 3.0, "People who may be mentioned: Billie.")
    finally:
        srv.shutdown()
    parts = got["body"]["messages"][0]["content"]
    assert got["path"] == "/v1/chat/completions" and got["body"]["temperature"] == 0
    assert parts[0]["type"] == "text" and "verbatim" in parts[0]["text"] and "no summary" in parts[0]["text"] and "Billie" in parts[0]["text"]
    assert parts[1]["type"] == "input_audio" and parts[1]["input_audio"]["format"] == "wav"
    with wave.open(io.BytesIO(base64.b64decode(parts[1]["input_audio"]["data"]))) as wf:
        assert (wf.getframerate(), wf.getnchannels(), wf.getsampwidth()) == (16000, 1, 2)
    assert r["text"] == "Hello there Billie" and r["completion_tokens"] == 4


def test_chunks_stay_under_30_s_words_get_even_times_and_nonsense_replies_are_dropped(monkeypatch):
    w = _worker()
    assert w.CHUNK_MAX_S <= 30.0
    assert w.clean_reply("There is no speech in this audio.", 5) == "" and w.clean_reply("", 5) == "" and w.clean_reply("so " * 200, 5) == ""
    assert w.clean_reply('  "Okay then, see you."  ', 5) == "Okay then, see you."
    monkeypatch.setattr(w, "vad_chunks", lambda x: [(1.0, 3.0), (5.0, 9.0)])
    monkeypatch.setattr(w, "gemma_transcribe", lambda url, wav, dur, hint="", model="gemma", timeout_s=300.0: {"text": "one two" if dur == 2.0 else "a b c d"})
    be = w.Gemma12bAudio(4, False, url="http://127.0.0.1:11500/v1")
    words, late = be.transcribe(np.zeros(16000 * 10, dtype="float32"), "", None)
    assert not late and [x[0].strip() for x in words] == ["one", "two", "a", "b", "c", "d"]
    assert words[0][1] == 1.0 and words[1][1] == 2.0 and all(1.0 <= x[1] < x[2] <= 9.0 for x in words)
    late_words, late = be.transcribe(np.zeros(16000 * 10, dtype="float32"), "", 1.0)            # an expired deadline carries the file
    assert late and late_words == []


def test_the_audio_backend_will_not_load_against_the_live_4b():
    with pytest.raises(RuntimeError):
        _worker().Gemma12bAudio(4, False, url="http://127.0.0.1:11434/v1").load()
