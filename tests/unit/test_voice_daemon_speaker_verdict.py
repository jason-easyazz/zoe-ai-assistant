"""The panel daemon reports the speaker gate's verdict for each voice turn (`speaker` block).

zoe-data turns it into ``speaker_verified`` for memory provenance (a self-fact spoken by a voice the
gate did not confirm is `user_unverified`, never the owner's own statement:
services/zoe-data/memory_authority.py, docs/knowledge/memory-authority.md). The request builder is
exercised for real, the gate (resemblyzer, the profile cache, the server's /identify) stubbed:

  * a scored candidate -> ``{"verified": null, "member": id, "score": s}`` - the SERVER judges it, the
    daemon never sends ``verified: true`` (a panel cannot make itself more trusted than the server allows);
  * the gate ran and nobody matched -> ``{"verified": false, "member": null, "score": null}``;
  * the gate off / in W5 shadow mode / errored / encoder missing -> the key is ABSENT (= no verdict =
    today's behaviour: nothing changes for a panel that has not turned the gate on);
  * the legacy flat ``voice_user_id`` / ``voice_score`` pair still rides beside it.

No audio device, no model, no network.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"


def _load(name: str):
    fake_pyaudio = types.ModuleType("pyaudio")
    fake_pyaudio.paInt16 = 8
    fake_pyaudio.PyAudio = object
    fake_requests = types.ModuleType("requests")
    fake_requests.exceptions = types.SimpleNamespace(
        SSLError=type("SSLError", (Exception,), {}),
        HTTPError=type("HTTPError", (Exception,), {}),
        RequestException=type("RequestException", (Exception,), {}),
    )
    stubs = {"pyaudio": fake_pyaudio, "requests": fake_requests}
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(name, DAEMON)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for n, prev in saved.items():
            if prev is not None:
                sys.modules[n] = prev
            else:
                sys.modules.pop(n, None)


@pytest.fixture(scope="module")
def daemon():
    return _load("zoe_voice_daemon_speaker_verdict_test")


class _FakePA:
    def get_sample_size(self, fmt):  # noqa: ARG002
        return 2


class _FakeResp:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=False):  # noqa: ARG002
        return iter(self._lines)


def _gate(daemon, monkeypatch, *, enabled=True, shadow=False, source="server", result=None):
    """Stub the gate: `result` is what the scorer returns, `source` what it records for the turn."""
    monkeypatch.setattr(daemon, "SPEAKER_ID_ENABLED", enabled)
    monkeypatch.setattr(daemon, "SPEAKER_ID_SHADOW", shadow)
    monkeypatch.setattr(daemon, "_start_shadow_scoring", lambda _w: None)   # shadow scoring is not under test

    def identify(_wav):
        daemon._claim_ctx.source = source
        return result

    monkeypatch.setattr(daemon, "_identify_speaker_from_wav", identify)


def _stream_payload(daemon, monkeypatch, **kw):
    posted: list[dict] = []
    done = [json.dumps({"transcript": "", "done": True, "reply": ""}).encode()]
    monkeypatch.setattr(daemon.requests, "post",
                        lambda url, json=None, **k: posted.append(json) or _FakeResp(done), raising=False)
    monkeypatch.setattr(daemon, "_do_single_turn", lambda *a, **k: True)
    daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=False, **kw)
    assert len(posted) == 1
    return posted[0]


def _blocking_payload(daemon, monkeypatch, **kw):
    posted: list[dict] = []

    def api_post(path, body, **_k):
        posted.append(body)
        return {"ok": False, "error": "stubbed"}   # the turn ends in the (stubbed) local fallback

    monkeypatch.setattr(daemon, "_api_post", api_post)
    monkeypatch.setattr(daemon, "VOICE_ROUTE_MODE", "direct", raising=False)
    monkeypatch.setattr(daemon, "_espeak_local", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_play_buffer_phrase", lambda: None)
    daemon._do_single_turn(None, b"RIFFwav", **kw)
    assert len(posted) == 1
    return posted[0]


BUILDERS = [pytest.param(_stream_payload, id="turn_stream"), pytest.param(_blocking_payload, id="turn")]


@pytest.mark.parametrize("build", BUILDERS)
def test_a_scored_candidate_is_reported_for_the_server_to_judge(daemon, monkeypatch, build):
    _gate(daemon, monkeypatch, source="local", result=("casey", 0.912345))
    p = build(daemon, monkeypatch)
    assert p["speaker"] == {"verified": None, "member": "casey", "score": 0.9123}
    assert (p["voice_user_id"], p["voice_score"]) == ("casey", 0.912345)      # the legacy pair, unchanged


@pytest.mark.parametrize("build", BUILDERS)
def test_a_scored_turn_nobody_matched_is_reported_unverified(daemon, monkeypatch, build):
    _gate(daemon, monkeypatch, source="server", result=None)
    p = build(daemon, monkeypatch)
    assert p["speaker"] == {"verified": False, "member": None, "score": None}
    assert "voice_user_id" not in p and "voice_score" not in p


@pytest.mark.parametrize("build", BUILDERS)
@pytest.mark.parametrize("kw", [
    dict(enabled=False),                                           # the gate is off (the default)
    dict(shadow=True, result=("casey", 0.99)),                     # W5 shadow week: scored + logged, never attached
    dict(source="error"),                                          # the identify call failed: not a verdict
    dict(source="encoder_unavailable"),                            # nothing was embedded
    dict(source=None),
])
def test_no_verdict_means_no_speaker_key(daemon, monkeypatch, build, kw):
    _gate(daemon, monkeypatch, **kw)
    p = build(daemon, monkeypatch)
    assert "speaker" not in p
    if kw.get("shadow") or kw.get("enabled") is False:
        assert "voice_user_id" not in p and "voice_score" not in p


@pytest.mark.parametrize("build", BUILDERS)
def test_the_daemon_never_asserts_verified_true(daemon, monkeypatch, build):
    for result, source in ((("casey", 0.999), "local"), (("casey", 1.0), "server"), (None, "server")):
        _gate(daemon, monkeypatch, source=source, result=result)
        assert build(daemon, monkeypatch)["speaker"]["verified"] is not True


@pytest.mark.parametrize("build", BUILDERS)
def test_a_handed_over_claim_is_reported_without_rescoring(daemon, monkeypatch, build):
    """A cancelled speculative turn re-runs as a normal one with the claim it already scored."""
    monkeypatch.setattr(daemon, "_speaker_claim_for_turn",
                        lambda _w: pytest.fail("must not re-score a handed-over claim"))
    p = build(daemon, monkeypatch, voice_claim=("casey", 0.81))
    assert p["speaker"] == {"verified": None, "member": "casey", "score": 0.81}
    p = build(daemon, monkeypatch, voice_claim=daemon.SCORED_NO_MATCH)
    assert p["speaker"]["verified"] is False


@pytest.mark.parametrize("claim", [None, (), ("casey", float("nan")), ("casey", float("inf")), ("", 0.9), ("a", "x")])
def test_speaker_field_edge_shapes(daemon, claim):
    field = daemon._speaker_field(claim)
    if claim == ():
        assert field == {"verified": False, "member": None, "score": None}
    else:
        assert field is None
    assert daemon._speaker_field(daemon._CLAIM_UNSET) is None


def test_scored_no_match_is_falsy_so_every_truthiness_check_still_reads_no_claim(daemon):
    assert not daemon.SCORED_NO_MATCH and daemon.SCORED_NO_MATCH is not None
    assert daemon._speaker_field(daemon.SCORED_NO_MATCH)["verified"] is False


def test_the_scored_no_match_is_only_produced_by_a_non_shadow_scored_turn(daemon, monkeypatch):
    _gate(daemon, monkeypatch, source="server", result=None)
    assert daemon._speaker_claim_for_turn(b"w") is daemon.SCORED_NO_MATCH
    _gate(daemon, monkeypatch, source="error", result=None)
    assert daemon._speaker_claim_for_turn(b"w") is None
    _gate(daemon, monkeypatch, enabled=False)
    assert daemon._speaker_claim_for_turn(b"w") is None
