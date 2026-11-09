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
