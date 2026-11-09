"""The owner's backfill of a named guest from saved panel clips (``named_guest_backfill.py``). Synthetic voices and invented names only
(the repo is public): ``Quinn`` is the guest, the clip files are fakes the stub embedder maps to vectors."""
from __future__ import annotations

import numpy as np
import pytest

import named_guest_backfill as ngb
import named_guest_enrol as nge
import named_guests as ng
from named_guest_support import (  # noqa: F401 - env is a fixture
    DEVICE, MID, OTHER, OWNER, env, run, unit,
)

pytestmark = pytest.mark.ci_safe


def near(base, cos, seed):
    o = np.random.default_rng(seed).standard_normal(256).astype(np.float32)
    o -= (o @ base) * base
    o /= np.linalg.norm(o)
    return (cos * base + np.sqrt(1 - cos * cos) * o).astype(np.float32)


class StubEmbedder:
    """``embed_wav`` by file content: each fake clip's bytes name its vector and length (the real model is the panel's pinned file)."""

    def __init__(self, table):
        self.table = table

    def embed_wav(self, wav_bytes):
        key = wav_bytes.decode()
        if key not in self.table:
            raise ValueError("unsupported WAV (need 16-bit PCM)")
        return self.table[key]


QUINN, MEMBER = unit(81), unit(82)


@pytest.fixture
def clips(env, tmp_path):
    env.conn.execute("INSERT INTO speaker_profiles (id, user_id, embedding_blob, consent_at, model_id) VALUES ('p', ?, ?, 'x', ?)",
                     (OWNER, MEMBER.tobytes(), MID))
    env.conn.commit()
    table, paths = {}, {}
    spec = [("q1", near(QUINN, 0.85, 1), 3.2), ("q2", near(QUINN, 0.85, 2), 2.6), ("q3", near(QUINN, 0.85, 3), 4.1),
            ("q4", near(QUINN, 0.85, 4), 1.9), ("own", near(MEMBER, 0.60, 5), 3.0), ("short", near(QUINN, 0.85, 6), 0.7),
            ("tv", unit(83), 3.5), ("junk", None, None)]
    for key, vec, secs in spec:
        p = tmp_path / f"{key}.wav"
        p.write_bytes(key.encode())
        paths[key] = str(p)
        if vec is not None:
            table[key] = (vec, secs)
    return paths, StubEmbedder(table)


def verdicts(report):
    return {c["path"].rsplit("/", 1)[-1][:-4]: c["verdict"] for c in report["clips"]}


def test_a_dry_run_scores_every_clip_and_writes_nothing(env, clips):
    paths, emb = clips
    rep = run(ngb.run(OWNER, "quinn", list(paths.values()), embedder=emb, dry_run=True))
    assert verdicts(rep) == {"q1": "kept", "q2": "kept", "q3": "kept", "q4": "kept", "own": "member", "short": "too_short",
                             "tv": "outlier", "junk": "unreadable"}
    assert rep["enough"] and not rep["stored"] and rep["name"] == "Quinn"
    kept = [c for c in rep["clips"] if c["verdict"] == "kept"]
    assert all(c["loo_score"] > c["live_bar"] for c in kept)                   # a live turn like these would clear the gate
    own = next(c for c in rep["clips"] if c["verdict"] == "member")
    assert own["member_score"] >= own["member_bar"]
    assert env.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)] and env.q("SELECT COUNT(*) FROM people") == [(0,)]


def test_the_real_run_stores_exactly_what_enrolment_stores_and_a_live_turn_matches_it(env, clips):
    paths, emb = clips
    rep = run(ngb.run(OWNER, "Quinn", list(paths.values()), embedder=emb, dry_run=False))
    assert rep["stored"]
    rows = env.q("SELECT owner_user_id, people_id, model_id, channel, n_segments FROM learned_voiceprints")
    assert rows == [(OWNER, rep["guest_id"], MID, "panel", 4)]
    assert env.q("SELECT user_id, name, visibility FROM people") == [(OWNER, "Quinn", "personal")]
    live = {"speaker": {"model_id": MID, "speech_s": 3.0, "guest": rep["guest_id"],
                        "guest_score": float(np.frombuffer(env.q("SELECT embedding FROM learned_voiceprints")[0][0], dtype="<f4")
                                             @ near(QUINN, 0.85, 9))}}
    g = run(ng.accept(live, DEVICE, member_identified=False))
    assert g is not None and g.name == "Quinn"


def test_the_owners_own_voice_never_becomes_the_guest_and_its_control(env, clips, monkeypatch):
    """Only member-voice clips: every one is refused. Break the fix (no member profiles read) and the same clips WOULD be stored."""
    paths, emb = clips
    own = [paths["own"]] * 3
    emb.table["own"] = (near(MEMBER, 0.60, 5), 3.0)
    rep = run(ngb.run(OWNER, "Quinn", own, embedder=emb, dry_run=False))
    assert not rep["stored"] and not rep["enough"] and env.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)]

    async def nobody(model_id):
        return []
    monkeypatch.setattr(ngb, "member_profiles", nobody)
    with pytest.raises(nge.GuestError):        # the store's own guard still refuses a member-like print: the second wall
        run(ngb.run(OWNER, "Quinn", own, embedder=emb, dry_run=False))
    monkeypatch.setattr(nge, "_sounds_like_a_member", lambda *a, **k: _false())
    assert run(ngb.run(OWNER, "Quinn", own, embedder=emb, dry_run=False))["stored"]       # both walls off: it is stored


async def _false():
    return False


def test_too_few_kept_clips_store_nothing(env, clips):
    paths, emb = clips
    rep = run(ngb.run(OWNER, "Quinn", [paths["q1"], paths["q2"], paths["short"]], embedder=emb, dry_run=False))
    assert not rep["enough"] and not rep["stored"] and rep["counts"] == {"kept": 2, "too_short": 1}
    assert env.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)]


def test_the_same_refusals_as_enrolment(env, clips, monkeypatch):
    paths, emb = clips
    good = [paths[k] for k in ("q1", "q2", "q3", "q4")]
    for owner, name in (("member-kid", "Quinn"), ("guest", "Quinn"), (OWNER, "Blake"), (OWNER, "123")):
        with pytest.raises(nge.GuestError):
            run(ngb.run(owner, name, good, embedder=emb, dry_run=False))
    monkeypatch.setenv("ZOE_NAMED_GUESTS", "off")
    with pytest.raises(nge.GuestError) as e:
        run(ngb.run(OWNER, "Quinn", good, embedder=emb, dry_run=False))
    assert e.value.status == 404
    assert run(ngb.run(OWNER, "Quinn", good, embedder=emb, dry_run=True))["enough"]     # a dry run works with the flag off
    assert env.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)]


# ── review #2357: the forget shield ───────────────────────────────────────────────────────────────────────────────────

_REAL_CONFIGURED = __import__("memory_forgotten").configured


@pytest.fixture(autouse=True)
def ledger(monkeypatch):
    """A configured forget ledger whose answer the test controls (``state['forgotten']``), and the release calls it saw."""
    import memory_forgotten as mf

    state = {"forgotten": False, "released": []}

    async def matches(uid, text, **_k):
        return state["forgotten"]

    async def release(uid, text):
        state["released"].append(text)
        return 1
    monkeypatch.setattr(mf, "configured", lambda: True)
    monkeypatch.setattr(mf, "matches", matches)
    monkeypatch.setattr(mf, "release", release)
    return state


def test_a_forget_made_mid_run_is_never_undone(env, clips, ledger):
    """The forget state is read BEFORE the clips are embedded: a forget landing while they embed is "forgotten since", refused 409
    - not read late and passed as an explicit re-teach that stores the print and releases the shield."""
    paths, emb = clips

    class ForgetsWhileEmbedding(StubEmbedder):
        def embed_wav(self, wav_bytes):
            ledger["forgotten"] = True
            return super().embed_wav(wav_bytes)
    with pytest.raises(nge.GuestError) as e:
        run(ngb.run(OWNER, "Quinn", list(paths.values()), embedder=ForgetsWhileEmbedding(emb.table), dry_run=False))
    assert e.value.status == 409 and ledger["released"] == []
    assert env.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)]


def test_a_name_forgotten_before_the_run_is_an_explicit_reteach(env, clips, ledger):
    paths, emb = clips
    ledger["forgotten"] = True
    assert run(ngb.run(OWNER, "Quinn", list(paths.values()), embedder=emb, dry_run=False))["stored"]
    assert ledger["released"] == ["Quinn"]


def test_an_unconfigured_ledger_stores_nothing(env, clips, monkeypatch):
    """No salt = ``matches`` would answer "not forgotten" without looking: refuse rather than store over a shield."""
    import memory_forgotten as mf

    monkeypatch.setattr(mf, "configured", lambda: False)
    paths, emb = clips
    with pytest.raises(nge.GuestError) as e:
        run(ngb.run(OWNER, "Quinn", list(paths.values()), embedder=emb, dry_run=False))
    assert e.value.status == 503 and env.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)]
    assert run(ngb.run(OWNER, "Quinn", list(paths.values()), embedder=emb, dry_run=True))["enough"]      # a dry run writes nothing


def test_runtime_env_loads_only_the_named_keys_and_the_real_env_wins(monkeypatch, tmp_path):
    import os

    import runtime_env

    f = tmp_path / ".env"
    f.write_text("ZOE_FORGET_LEDGER_SALT=synthetic-salt-value-0123456789\nOTHER_KEY=x\n", encoding="utf-8")
    monkeypatch.setattr(runtime_env, "_ENV_FILES", (str(f),))
    monkeypatch.setenv("ZOE_FORGET_LEDGER_SALT", "x")
    monkeypatch.delenv("ZOE_FORGET_LEDGER_SALT")      # (setenv first: undo then restores "unset", whatever load_env_keys put there)
    monkeypatch.delenv("OTHER_KEY", raising=False)
    runtime_env.load_env_keys(("ZOE_FORGET_LEDGER_SALT",))
    assert os.environ["ZOE_FORGET_LEDGER_SALT"] == "synthetic-salt-value-0123456789" and "OTHER_KEY" not in os.environ
    monkeypatch.setenv("ZOE_FORGET_LEDGER_SALT", "real-env-wins-0123456789abcdef")
    runtime_env.load_env_keys(("ZOE_FORGET_LEDGER_SALT",))
    assert os.environ["ZOE_FORGET_LEDGER_SALT"] == "real-env-wins-0123456789abcdef"
    monkeypatch.delenv("ZOE_FORGET_LEDGER_SALT")


def test_the_cli_loads_the_salt_and_refuses_a_real_run_without_it(monkeypatch, tmp_path, capsys):
    """Review #2357: the CLI must pick the salt up from the service .env; with none, a real run exits 3 (nothing stored). The output
    names the variable, never a value."""
    import argparse
    import asyncio
    import importlib.util
    import pathlib

    import memory_forgotten as mf
    import runtime_env

    monkeypatch.setattr(mf, "configured", _REAL_CONFIGURED)
    spec = importlib.util.spec_from_file_location(
        "ngb_cli", pathlib.Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "named_guest_backfill.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(runtime_env, "_ENV_FILES", (str(tmp_path / "absent.env"),))
    monkeypatch.setattr(runtime_env, "_ENV_BOOTSTRAPPED", False)
    monkeypatch.setenv("ZOE_FORGET_LEDGER_SALT", "x")
    monkeypatch.delenv("ZOE_FORGET_LEDGER_SALT")      # (setenv first: undo then restores "unset", whatever load_env_keys put there)
    args = argparse.Namespace(owner=OWNER, name="Quinn", clips=["x.wav"], min_kept=3, dry_run=False)
    assert asyncio.run(cli._main(args)) == 3
    out = capsys.readouterr().out
    assert "ZOE_FORGET_LEDGER_SALT" in out and "nothing was stored" in out
    # with the salt in the service .env the CLI gets past the ledger check (it then stops at the model, not at the ledger)
    f = tmp_path / "zoe-data.env"
    f.write_text("ZOE_FORGET_LEDGER_SALT=synthetic-salt-value-0123456789\n", encoding="utf-8")
    monkeypatch.setattr(runtime_env, "_ENV_FILES", (str(f),))
    monkeypatch.setattr(runtime_env, "_ENV_BOOTSTRAPPED", False)
    import omi_diarize
    monkeypatch.setattr(omi_diarize, "load_embedder", lambda: None)
    assert asyncio.run(cli._main(args)) == 3
    assert "speaker model" in capsys.readouterr().out
