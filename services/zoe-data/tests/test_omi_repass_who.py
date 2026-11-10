"""WHO SAID IT (``omi_repass_who.py``): the panel ResNet34 units, per-channel mean removal, the shared person registry, Zoe first, the wearer prior, and the abstain rules
(margin >= 0.10, >= 0.20 under 1.2 s, "X?" at >= 0.05, else "?"); the ``who_said`` items in the check queue and the owner's tap. Synthetic 8-d vectors; no model, no audio.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import json
from pathlib import Path

import numpy as np

import omi_repass as rp
import omi_repass_eval as eu
import omi_repass_who as who
from test_omi_recordings import OWNER, env  # noqa: F401
from test_omi_repass import first_pass, renv, result_for, rows, run, transcribe  # noqa: F401
from test_omi_repass_multi import multi_runner  # noqa: F401


def v(*xs):
    return list(np.array(list(xs) + [0.0] * (8 - len(xs)), dtype=float))


def near(base, other, w):
    """base pulled toward other by weight w (cos(base, result) falls as w grows)."""
    return list(np.array(base) + w * np.array(other))


A_, B_, C_ = v(1), v(0, 1), v(0, 0, 1)


def refs(**kw):
    return who.Refs(people=[{"person": "A", "v": A_}, {"person": "B", "v": B_}], **kw)


def test_a_clear_margin_labels_a_close_one_leans_and_a_tie_abstains():
    r = refs()
    # near(A, B, w): cos(A) = 1/sqrt(1+w^2), cos(B) = w/sqrt(1+w^2); the margin is (1-w)/sqrt(1+w^2)
    assert who.label_window(near(A_, B_, 0.2), r, dur_s=3.0)["key"] == "p:A"                       # margin 0.78: far over 0.10
    lean = who.label_window(near(A_, B_, 0.9), r, dur_s=3.0)                                        # margin 0.074: >= 0.05 but under 0.10
    assert lean["key"] == "p:A?" and lean["conf"] == "lean" and 0.05 <= lean["margin"] < 0.10
    tie = who.label_window(near(A_, B_, 1.0), r, dur_s=3.0)
    assert tie["key"] == "?" and tie["conf"] == "none" and abs(tie["margin"]) < 0.05


def test_a_short_turn_needs_twice_the_margin():
    r = refs()
    x = near(A_, B_, 0.8)                                                                           # margin 0.156: over 0.10, under 0.20
    assert who.label_window(x, r, dur_s=3.0)["key"] == "p:A"
    short = who.label_window(x, r, dur_s=1.0)
    assert short["key"] == "p:A?" and short["conf"] == "lean"                                      # only leaning under 1.2 s
    assert who.label_window(near(A_, B_, 0.3), r, dur_s=1.0)["key"] == "p:A"                       # a very clear short turn is still labelled


def test_a_winner_must_clear_the_absolute_score_and_a_wearer_turn_is_not_the_closest_person():
    r = refs()
    far = v(0, 0, 0, 1)                                                                             # nothing like A or B (cos 0)
    out = who.label_window(far, r, dur_s=3.0)
    assert out["key"] == "?" and out["conf"] == "none"


def test_zoe_is_decided_first_from_the_panel_log_exactly_then_by_her_print_and_never_competes_with_people():
    r = refs(zoe=v(0, 0, 0, 1))
    assert who.label_window(A_, r, zoe_hit=True)["key"] == "zoe"                                    # the panel log says Zoe was speaking: exact, whatever the voice scores
    assert who.label_window(v(0, 0, 0, 1), r)["key"] == "zoe"                                       # the print fallback
    assert who.label_window(near(A_, B_, 0.2), r)["key"] == "p:A"
    lines = rp.build_lines([[" hello", 1.0, 1.4, 0.9]], [[0.5, 2.5, A_, 0.2]], r, zoe_spans=[(500, 2500)])
    assert [ln["key"] for ln in lines] == ["zoe"]


def test_per_channel_mean_removal_separates_voices_a_shared_offset_hides():
    off = np.array(v(3, 3, 3, 3))                                                                    # the channel's common component dominates the raw cosines
    turns = {"A": list(off + 0.5 * np.array(A_)), "B": list(off + 0.5 * np.array(B_))}
    people_raw = [{"person": "A", "v": turns["A"]}, {"person": "B", "v": turns["B"]}]
    raw = who.Refs(people=people_raw)
    assert who.label_window(turns["A"], raw, dur_s=3.0)["key"] != "p:A"                              # raw: A and B look alike (margin below 0.05)
    mu = who.session_mean([turns["A"], turns["B"]])
    mr = who.Refs(people=[{"person": p, "v": who.remove_mean(t, mu)} for p, t in turns.items()], mu=mu)
    assert who.label_window(turns["A"], mr, dur_s=3.0)["key"] == "p:A" and who.label_window(turns["B"], mr, dur_s=3.0)["key"] == "p:B"


def test_a_print_is_the_panel_print_until_six_seconds_of_confirmed_pendant_speech_then_both():
    pan, pend = np.array(A_), np.array(near(A_, B_, 1.0))
    assert np.allclose(who.fused_print(who.unit(pan), who.unit(pend), 2.0), who.unit(pan))
    fused = who.fused_print(who.unit(pan), who.unit(pend), 6.0)
    assert not np.allclose(fused, who.unit(pan)) and np.allclose(fused, who.unit(who.unit(pan) + who.unit(pend)))
    assert np.allclose(who.fused_print(None, who.unit(pend), 0.0), who.unit(pend))                  # a pendant-only person uses the pendant print


def test_loudness_is_only_a_tie_breaker_for_the_wearer():
    r = who.Refs(you=A_, people=[{"person": "B", "v": B_}], loud_cut=0.1)
    turn = v(1, 1)                                                                                   # exactly between the wearer and B
    quiet = who.label_window(turn, r, dur_s=3.0, rms=0.01)
    loud = who.label_window(turn, r, dur_s=3.0, rms=0.5)
    assert quiet["ranked"][0][0] == "B" and loud["ranked"][0][0] == "you"                           # the tie goes to the wearer only when the turn is loud...
    assert dict(loud["ranked"])["you"] - dict(quiet["ranked"])["you"] == pytest.approx(who.LOUD_TIEBREAK, abs=1e-9)
    assert who.label_window(v(0, 1), r, dur_s=3.0, rms=0.9)["key"] == "p:B"                         # ...and loudness never turns a clearly different voice into "me"


# ── the registry and the channel means (database) ───────────────────────────────────────────────────────────────────────────────────────

def _registry(renv, model=None):
    model = model or rp.MODEL_ID
    c = renv.db.conn
    c.executescript("""CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, relationship TEXT, birthday TEXT, deleted INTEGER DEFAULT 0);
        CREATE TABLE learned_voiceprints (id TEXT PRIMARY KEY, owner_user_id TEXT, people_id TEXT, model_id TEXT, channel TEXT, embedding BLOB, rejections INTEGER DEFAULT 0,
                                          learning_enabled INTEGER DEFAULT 1);""")
    for pid, name in (("pa", "Alex Example"), ("pb", "Blake Example"), ("pc", "Casey Example")):
        c.execute("INSERT INTO people (id, user_id, name, relationship) VALUES (?, ?, ?, 'friend')", (pid, OWNER, name))
    c.commit()
    return c


def test_the_registry_reads_panel_and_pendant_prints_of_one_model_fuses_by_confirmed_seconds_and_skips_children(renv):
    c = _registry(renv)
    blob = lambda x: np.asarray(x, dtype=np.float32).tobytes()                                       # noqa: E731
    for i, (pid, panel, pendant) in enumerate((("pa", A_, near(A_, B_, 0.3)), ("pb", B_, None), ("pc", C_, None))):
        c.execute("INSERT INTO learned_voiceprints VALUES (?, ?, ?, ?, 'panel', ?, 0, 1)", (f"w{i}", OWNER, pid, rp.MODEL_ID, blob(panel)))
        if pendant is not None:
            c.execute("INSERT INTO learned_voiceprints VALUES (?, ?, ?, ?, 'pendant', ?, 0, 1)", (f"x{i}", OWNER, pid, rp.MODEL_ID, blob(pendant)))
    c.execute("INSERT INTO learned_voiceprints VALUES ('other', ?, 'pa', 'some-other-model', 'pendant', ?, 0, 1)", (OWNER, blob(C_)))     # another model never mixes
    c.commit()
    people, panel_mu = run(who.load_people(renv.db, OWNER, rp.MODEL_ID, None))
    got = {p["person"]: p["v"] for p in people}
    assert set(got) == {"pa", "pb", "pc"} and panel_mu is not None                                   # 3 panel prints => a panel mean exists
    base_a = who.remove_mean(A_, panel_mu)
    assert np.allclose(got["pa"], base_a)                                                            # no confirmed pendant seconds yet: panel print only (mean-removed)
    c.execute("INSERT INTO omi_recordings (id, user_id, device_id, recording_key, created_at) VALUES ('r1', ?, 'd', 'k', '2026-11-09T00:00:00Z')", (OWNER,))
    c.execute("INSERT INTO omi_segments (id, recording_id, user_id, seq, start_ms, end_ms, text, speaker, created_at, tagged_person_id, tag_source) "
              "VALUES ('s1', 'r1', ?, 0, 0, 7000, '', 'other', '2026-11-09T00:00:00Z', 'pa', 'owner')", (OWNER,))
    c.commit()
    people2, _ = run(who.load_people(renv.db, OWNER, rp.MODEL_ID, None))
    fused = {p["person"]: p["v"] for p in people2}["pa"]
    assert not np.allclose(fused, base_a)                                                            # 7 s confirmed => the pendant print joins
    c.execute("UPDATE people SET relationship = 'daughter', birthday = date('now', '-6 years') WHERE id = 'pb'")
    c.commit()
    assert "pb" not in {p["person"] for p in run(who.load_people(renv.db, OWNER, rp.MODEL_ID, None))[0]}      # a child is a label, never a match


def test_the_channel_mean_is_stored_with_model_and_channel_and_replaced_not_duplicated(renv):
    run(who.store_mean(renv.db, OWNER, rp.MODEL_ID, who.CHANNEL_PENDANT, "dev1", v(1, 1), 40, 900.0))
    run(who.store_mean(renv.db, OWNER, rp.MODEL_ID, who.CHANNEL_PENDANT, "dev1", v(2, 1), 50, 950.0))
    got = run(who.load_mean(renv.db, OWNER, rp.MODEL_ID, who.CHANNEL_PENDANT, "dev1"))
    assert np.allclose(got, np.asarray(v(2, 1), dtype=float), atol=1e-6)      # stored as given (its magnitude is part of the centering)
    assert renv.db.rows("SELECT COUNT(*) AS n FROM omi_channel_means")[0]["n"] == 1
    assert run(who.load_mean(renv.db, OWNER, rp.MODEL_ID, who.CHANNEL_PANEL, "dev1")) is None and run(who.load_mean(renv.db, OWNER, "other-model", who.CHANNEL_PENDANT, "dev1")) is None


def test_a_long_session_stores_its_mean_per_device_and_a_short_one_uses_the_stored_mean_of_ITS_device(renv, monkeypatch):
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")
    rid = rows(renv)[0]["id"]
    renv.db.conn.execute("UPDATE omi_recordings SET device_id = 'devA'")
    renv.db.conn.commit()
    long_res = {**result_for(rid), "speech_s": 700.0, "windows": [[0.0, 1.5, v(1, 1), 0.2], [0.75, 2.25, v(1, 2), 0.3], [1.5, 3.0, v(2, 1), 0.2]]}
    run(rp.run_night(budget_s=600, runner=_one_shot(long_res, rid, [])))
    stored = renv.db.rows("SELECT model_id, channel, scope, n_units FROM omi_channel_means")
    assert len(stored) == 1 and stored[0]["channel"] == "pendant" and stored[0]["scope"] == "devA" and stored[0]["model_id"] == rp.MODEL_ID and stored[0]["n_units"] == 3
    # tomorrow: a SHORT session on the same device uses that device's stored mean; a short session on ANOTHER device has none
    seen_mu: list = []
    real = rp.load_refs

    async def spy(db, uid, zoe, mu=None, loud_cut=None, persist=False):
        seen_mu.append(mu)
        return await real(db, uid, zoe, mu, loud_cut, persist)

    monkeypatch.setattr(rp, "load_refs", spy)
    short = {**result_for(rid), "speech_s": 5.0}
    for dev in ("devA", "devB"):
        path = renv.tmp / f"{dev}.wav"
        path.write_bytes(b"x")
        renv.db.conn.execute("UPDATE omi_recordings SET repass_state = 'pending', status = 'done', device_id = ?, audio_path = ?", (dev, str(path)))
        renv.db.conn.commit()
        run(rp.run_night(budget_s=600, runner=_one_shot(short, rid, [])))
    assert seen_mu[0] is not None and np.allclose(seen_mu[0], who.session_mean([v(1, 1), v(1, 2), v(2, 1)]), atol=1e-6) and seen_mu[1] is None


def test_two_devices_of_one_member_never_share_a_channel_mean(renv, monkeypatch):
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")
    rid = rows(renv)[0]["id"]
    renv.db.conn.execute("UPDATE omi_recordings SET device_id = 'devA'")
    renv.db.conn.execute("INSERT INTO omi_recordings (id, user_id, device_id, recording_key, created_at, status, audio_path, duration_s, started_at, repass_state, transcript) "
                         "SELECT 'rB', user_id, 'devB', 'kB', created_at, status, audio_path, duration_s, started_at, 'pending', transcript FROM omi_recordings WHERE id = ?", (rid,))
    renv.db.conn.commit()
    res = {"ok": True, "dur_s": 3.0, "speech_s": 700.0, "asr_s": 1, "emb_s": 0, "words": [[" hi", 0.0, 0.4, 0.9]], "chunks": [[0.0, 2.0, "hi"]]}

    def runner(manifest, out, timeout_s, mem_max="", threads=4):
        with open(out, "a") as fh:
            for it in json.loads(Path(manifest).read_text())["items"]:
                emb = v(1, 0) if it["id"] == rid else v(0, 1)
                fh.write(json.dumps({**res, "id": it["id"], "windows": [[0.0, 1.5, emb, 0.2], [0.75, 2.25, emb, 0.2]]}) + "\n")
            fh.write(json.dumps({"done": True, "files": 2, "carried": 0, "failed": 0, "rtf": 0.4}) + "\n")
        return 0

    run(rp.run_night(budget_s=600, runner=runner))
    got = {r["scope"]: r["n_units"] for r in renv.db.rows("SELECT scope, n_units FROM omi_channel_means")}
    assert got == {"devA": 2, "devB": 2}                                                              # one mean per device, each from its OWN turns only
    ma = run(who.load_mean(renv.db, OWNER, rp.MODEL_ID, who.CHANNEL_PENDANT, "devA"))
    mb = run(who.load_mean(renv.db, OWNER, rp.MODEL_ID, who.CHANNEL_PENDANT, "devB"))
    assert not np.allclose(ma, mb)


def test_with_fewer_than_three_panel_prints_the_stored_registry_panel_mean_is_used(renv):
    c = _registry(renv)
    blob = lambda x: np.asarray(x, dtype=np.float32).tobytes()                                       # noqa: E731
    for i, (pid, panel) in enumerate((("pa", A_), ("pb", B_), ("pc", C_))):
        c.execute("INSERT INTO learned_voiceprints VALUES (?, ?, ?, ?, 'panel', ?, 0, 1)", (f"w{i}", OWNER, pid, rp.MODEL_ID, blob(panel)))
    c.commit()
    _p, mu3 = run(who.load_people(renv.db, OWNER, rp.MODEL_ID, None, persist=True))                  # three prints: the mean is computed and stored (model_id + channel panel)
    assert mu3 is not None and renv.db.rows("SELECT channel, scope FROM omi_channel_means")[0]["channel"] == "panel"
    c.execute("DELETE FROM learned_voiceprints WHERE people_id IN ('pb', 'pc')")
    c.commit()
    people, mu1 = run(who.load_people(renv.db, OWNER, rp.MODEL_ID, None))                            # one print left: its own "mean" would zero it, so the STORED mean stands
    assert np.allclose(mu1, mu3, atol=1e-5) and len(people) == 1


def _one_shot(res, rid, seen):
    def runner(manifest, out, timeout_s, mem_max="", threads=4):
        man = json.loads(Path(manifest).read_text())
        seen.append(man)
        with open(out, "a") as fh:
            fh.write(json.dumps({**res, "id": rid}) + "\n")
            fh.write(json.dumps({"done": True, "files": 1, "carried": 0, "failed": 0, "rtf": 0.4, "device": "cuda", "peak_rss_mb": 1, "gpu_peak_mb": 1}) + "\n")
        return 0
    return runner


# ── who_said items + the owner's tap ────────────────────────────────────────────────────────────────────────────────────────────────────

def _ambiguous_night(renv, monkeypatch, tmp_path):
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")
    rid = rows(renv)[0]["id"]
    # a confirmed wearer print (>= 10 windows) so there are prints to decide with; the turn itself is nobody registered
    for i in range(12):
        renv.db.conn.execute("INSERT INTO omi_wearer_samples (id, user_id, recording_id, model_id, start_ms, end_ms, embedding, source, status, created_at) "
                             "VALUES (?, ?, 'x', ?, ?, ?, ?, 'owner_label', 'confirmed', '2026-11-09T00:00:00Z')",
                             (f"ws{i}", OWNER, rp.MODEL_ID, i * 100, i * 100 + 50, np.asarray(A_, dtype=np.float32).tobytes()))
    renv.db.conn.commit()
    mid = v(0.1, 0, 1)                                                                               # cos to the wearer ~0.1: under the absolute floor, so the turn is "?"
    res = {**result_for(rid), "windows": [[0.0, 1.0, mid, 0.2], [1.0, 2.0, mid, 0.2], [2.0, 3.0, mid, 0.2]]}
    return rid, res


def test_an_undecided_turn_becomes_a_who_said_item_with_its_embedding_stored_for_the_owners_tap(renv, monkeypatch, tmp_path):
    rid, res = _ambiguous_night(renv, monkeypatch, tmp_path)
    out = run(rp.run_night(budget_s=600, runner=_one_shot(res, rid, [])))
    assert out["status"] == "ok" and out["replaced"] == 1
    q = eu.check_queue(tmp_path / "repass", OWNER, kind="who_said")
    assert q and q[0]["kind"] == "who_said" and q[0]["mark"] == "unknown" and q[0]["cands"] and q[0]["cands"][0][0] == "you"    # the candidates, best first
    assert [c[1] for c in q[0]["cands"]] == sorted((c[1] for c in q[0]["cands"]), reverse=True)
    seg = q[0]["seg_id"]
    assert renv.db.rows("SELECT model_id FROM omi_segment_embeddings WHERE segment_id = ?", (seg,))[0]["model_id"] == rp.MODEL_ID
    assert all("hyps" not in x or x["hyps"] is None for x in q) and eu.check_queue(tmp_path / "repass", "someone-else") == []
    both = eu.check_queue(tmp_path / "repass", OWNER)
    assert {x["kind"] for x in both} >= {"who_said"}


def test_the_tap_for_you_confirms_a_wearer_sample_and_the_tap_for_a_person_adds_the_turn_to_that_pendant_print(renv, monkeypatch, tmp_path):
    _registry(renv)                                                    # people + learned_voiceprints exist (the tap's machinery reads them)
    rid, res = _ambiguous_night(renv, monkeypatch, tmp_path)
    run(rp.run_night(budget_s=600, runner=_one_shot(res, rid, [])))
    q = eu.check_queue(tmp_path / "repass", OWNER, kind="who_said")
    seg = q[0]["seg_id"]
    before = renv.db.rows("SELECT COUNT(*) AS n FROM omi_wearer_samples WHERE status = 'confirmed'")[0]["n"]
    out = run(rp.confirm_who(renv.db, OWNER, seg, "you"))
    assert out["person"] == "you" and renv.db.rows("SELECT COUNT(*) AS n FROM omi_wearer_samples WHERE status = 'confirmed'")[0]["n"] == before + 1
    assert renv.db.rows("SELECT owner_label FROM omi_segments WHERE id = ?", (seg,))[0]["owner_label"] == "you"
    import omi_voices
    called: dict = {}

    async def fake_tag(db, uid, **kw):
        called.update(kw)
        return {"learning": "learning"}

    monkeypatch.setattr(omi_voices, "tag", fake_tag)
    other = q[-1]["seg_id"]
    out2 = run(rp.confirm_who(renv.db, OWNER, other, "pa"))
    assert called == {"person_id": "pa", "segment_id": other, "source": "owner"} and out2["person"] == "pa"          # the existing machinery learns the embedding into that person's pendant print


def test_stale_proposals_expire_from_the_retention_loop_with_the_repass_flag_off(renv, monkeypatch):
    """The re-pass flag off must not stop an unconfirmed wearer proposal expiring (run_night returns before its own drop when off)."""
    import asyncio
    import omi_recordings as omi
    blob = np.asarray(A_, dtype=np.float32).tobytes()
    for n, (sid, created) in enumerate((("old", "2000-01-01T00:00:00Z"), ("fresh", "2999-01-01T00:00:00Z"))):
        renv.db.conn.execute("INSERT INTO omi_wearer_samples (id, user_id, recording_id, model_id, start_ms, end_ms, embedding, source, status, created_at) "
                             "VALUES (?, ?, 'x', ?, ?, ?, ?, 'heuristic', 'proposed', ?)", (sid, OWNER, rp.MODEL_ID, n * 100, n * 100 + 50, blob, created))
    renv.db.conn.commit()
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "off")
    monkeypatch.setattr(omi, "RETENTION_S", 0.01)

    async def go():
        try:
            await asyncio.wait_for(omi.run_retention_loop(), 1.0)
        except asyncio.TimeoutError:
            pass
    run(go())
    left = {r["id"] for r in renv.db.rows("SELECT id FROM omi_wearer_samples WHERE status = 'proposed'")}
    assert left == {"fresh"}
