"""persona_drift.py — the offline drift scorer (nothing live is wired).

Fakes only: a two-axis fake embedder (``[1,0]`` = the negative-seed pole, ``[0,1]`` = persona
pole) makes the bands exact; the real hashed-BoW embedder is checked for determinism; bge is
never loaded.

Negative controls:
  * an all-bad sample under ``MIN_SAMPLE`` is ``insufficient_sample``, never ``ok`` and never
    ``exceeded`` (a small sample must not pass OR fail the bar);
  * thirty good replies are ``ok`` but the same thirty with a bad slice are ``exceeded``;
  * kid/minor rows with bad replies do NOT move the verdict (they are skipped), and the report
    holds no reply text and no user id;
  * the in-process hook is ``None`` with the flag off.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import persona_drift as pd
import persona_layer as pl

GOOD = "I'm glad you told me. That sounds like a lot, and I'm right here with you."
BAD_OPENER = "Great! Of course! I'd be happy to help you with that right away."


def fake_embed(text: str):
    """Pole [1,0] for the 'corporate / cold / model' register, [0,1] for everything else."""
    t = text.lower()
    cold = ("great!" in t or "as an ai" in t or "your request has been processed" in t
            or "language model" in t or "certainly!" in t)
    return [1.0, 0.0] if cold else [0.0, 1.0]


def _rows(good=0, bad=0, kid_bad=0):
    rows = [{"role": "assistant", "text": GOOD} for _ in range(good)]
    rows += [{"role": "assistant", "text": BAD_OPENER} for _ in range(bad)]
    rows += [{"role": "assistant", "text": BAD_OPENER, "member_mode": "kid", "user_id": "mia"} for _ in range(kid_bad)]
    return rows


def test_lexical_embedder_is_deterministic_and_unit_length():
    a, b = pd.lexical_embed("I'm glad you told me."), pd.lexical_embed("I'm glad you told me.")
    assert a == b and abs(pd.cosine(a, a) - 1.0) < 1e-9
    assert pd.cosine(a, pd.lexical_embed("completely unrelated words here")) < 0.5
    assert pd.lexical_embed("") == [0.0] * 256


def test_weighted_top_k_mean():
    assert pd.weighted_top_k_mean([0.1, 0.9, 0.5], k=2) == pytest.approx((2 * 0.9 + 1 * 0.5) / 3)
    assert pd.weighted_top_k_mean([]) == 0.0


def test_bands_from_the_fake_embedder():
    rec = pl.default_persona()
    pos = [fake_embed(a) for a in pd.positive_anchors(rec)]
    neg = [fake_embed(a) for a in pd.NEGATIVE_SEEDS]
    assert pos and all(v == [0.0, 1.0] for v in pos) and all(v == [1.0, 0.0] for v in neg)
    good = pd.score_reply(GOOD, pos, neg, fake_embed)
    bad = pd.score_reply(BAD_OPENER, pos, neg, fake_embed)
    assert pd.band_for(good) == "aligned" and pd.band_for(bad) == "deviation"
    assert pd.band_for(0.0) == "neutral"


def test_positive_anchors_come_from_the_rendered_persona():
    anchors = pd.positive_anchors(pl.default_persona())
    assert any("warm, curious and thoughtful" in a for a in anchors)
    assert all(len(a) > 12 for a in anchors)


# ── style checks ───────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,check,ok", [
    ("Great! I can do that.", "opener", False),
    ("Of course! Here you go.", "opener", False),
    ("Certainly, one moment.", "opener", False),
    ("Certainly! One moment.", "opener", False),
    ("Greatly appreciated, that.", "opener", True),      # a word that merely starts like a banned opener
    ("Sure, that's fine.", "opener", True),
    ("I am Gemma, a model trained by Google.", "no_selfid", False),
    ("As an AI language model, I can't.", "no_selfid", False),
    ("I'm Zoe.", "no_selfid", True),
    ("Here are the steps:\n- first\n- second", "no_markdown", False),
    ("This is **important**.", "no_markdown", False),
    ("Just a plain sentence.", "no_markdown", True),
    ("It is raining today and you should take an umbrella with you when you go out.", "contractions", False),
    ("It's raining today, and you'll want an umbrella when you head out the door.", "contractions", True),
])
def test_style_checks(text, check, ok):
    assert pd.style_checks(text, pl.default_persona())[check] is ok


def test_contraction_and_brevity_checks_apply_only_when_relevant():
    rec_short = pl.validate_persona({"traits": [{"name": "warm", "strength": "mid"}], "voice_style": {"brevity": "short"}})
    rec_default = pl.default_persona()
    long = "One. Two. Three. Four. Five."
    assert pd.style_checks(long, rec_short)["brevity"] is False
    assert pd.style_checks("One. Two.", rec_short)["brevity"] is True
    assert pd.style_checks(long, rec_default)["brevity"] is None        # not asked for ⇒ not applicable
    assert pd.style_checks("Okay.", rec_default)["contractions"] is None  # too short to judge


def test_trait_cues_are_reported_per_trait():
    rec = pl.validate_persona({"traits": [{"name": "warm", "strength": "mid"}, {"name": "curious", "strength": "mid"}]})
    rates = pd.trait_cue_rates(["I'm glad you're here.", "What happened next?", "Fine."], rec)
    assert rates == {"warm": pytest.approx(0.333, abs=0.001), "curious": pytest.approx(0.333, abs=0.001)}
    assert pd.trait_cue_rates([], rec) == {}


# ── the bar ────────────────────────────────────────────────────────────────────────────
def test_thirty_good_replies_are_ok():
    rep = pd.score_rows(_rows(good=30), embed=fake_embed)
    assert rep.status == "ok" and rep.n_scored == 30 and rep.bands["aligned"] == 30
    assert rep.deviation_share == 0 and rep.style_pass_rate == 1.0 and rep.reasons == []


def test_a_bad_slice_exceeds_the_bar():
    rep = pd.score_rows(_rows(good=24, bad=6), embed=fake_embed)  # 20 % bad
    assert rep.status == "exceeded"
    assert rep.deviation_share == pytest.approx(0.2) and rep.style_pass_rate == pytest.approx(0.8)
    assert any("deviation share" in r for r in rep.reasons) and any("style pass rate" in r for r in rep.reasons)


def test_a_small_sample_is_neither_a_pass_nor_a_fail():
    rep = pd.score_rows(_rows(bad=pd.MIN_SAMPLE - 1), embed=fake_embed)  # all bad, but too few
    assert rep.status == "insufficient_sample" and rep.reasons
    rep2 = pd.score_rows(_rows(good=pd.MIN_SAMPLE - 1), embed=fake_embed)
    assert rep2.status == "insufficient_sample"  # all good, too few: still not "ok"


def test_just_inside_the_bar_is_ok():
    rep = pd.score_rows(_rows(good=27, bad=3), embed=fake_embed)  # exactly 10 % deviation, 90 % style
    assert rep.status == "ok", rep.to_dict()


# ── kid / minor: skipped, nothing retained ─────────────────────────────────────────────
def test_kid_rows_are_skipped_and_do_not_move_the_verdict():
    rep = pd.score_rows(_rows(good=30, kid_bad=40), embed=fake_embed)
    assert rep.skipped_kid == 40 and rep.n_scored == 30 and rep.status == "ok"
    minors = [{"role": "assistant", "text": BAD_OPENER, "minor": True}] * 5
    assert pd.score_rows(minors, embed=fake_embed).skipped_kid == 5


def test_the_report_holds_no_text_and_no_user_id():
    rows = _rows(good=3, kid_bad=2) + [{"role": "assistant", "text": "secret reply text", "user_id": "jason"}]
    blob = json.dumps(pd.score_rows(rows, embed=fake_embed).to_dict())
    for leak in ("secret reply text", "jason", "mia", "glad you told me"):
        assert leak not in blob


def test_non_assistant_and_empty_rows_are_ignored():
    rows = [{"role": "user", "text": GOOD}, {"role": "assistant", "text": "  "}, {"role": "assistant", "content": GOOD}]
    rep = pd.score_rows(rows, embed=fake_embed)
    assert rep.n_scored == 1 and rep.skipped_other == 2


# ── hook: flag-dark ────────────────────────────────────────────────────────────────────
def test_the_in_process_hook_is_off_by_default(monkeypatch):
    monkeypatch.delenv(pd.FLAG, raising=False)
    assert pd.maybe_score_reply(GOOD, pl.default_persona(), fake_embed) is None
    monkeypatch.setenv(pd.FLAG, "1")
    assert pd.maybe_score_reply(GOOD, pl.default_persona(), fake_embed) == "aligned"
    assert pd.maybe_score_reply(BAD_OPENER, pl.default_persona(), fake_embed) == "deviation"


def test_nothing_in_the_service_calls_the_drift_scorer():
    """Phase 0 wires nothing live: only the module itself and its tests reference it."""
    from pathlib import Path

    svc = Path(__file__).resolve().parents[1]
    offenders = [p.relative_to(svc).as_posix() for p in svc.rglob("*.py")
                 if not p.relative_to(svc).as_posix().startswith(("tests/", "alembic/"))
                 and p.name != "persona_drift.py" and "persona_drift" in p.read_text(errors="ignore")]
    assert offenders == []


# ── CLI ────────────────────────────────────────────────────────────────────────────────
def _write(tmp_path, rows, name="t.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(p)


def test_cli_exit_codes(tmp_path, capsys):
    bad = _write(tmp_path, _rows(bad=40), "bad.jsonl")          # style fails ⇒ exceeded, whatever the embedder
    assert pd.main([bad]) == pd.EXIT_EXCEEDED
    assert "status=exceeded" in capsys.readouterr().out
    short = _write(tmp_path, _rows(good=3), "short.jsonl")
    assert pd.main([short]) == pd.EXIT_INSUFFICIENT
    assert pd.main([str(tmp_path / "missing.jsonl")]) == pd.EXIT_ERROR
    broken = tmp_path / "broken.jsonl"
    broken.write_text('{"role": "assistant", "text": "x"\n')
    assert pd.main([str(broken)]) == pd.EXIT_ERROR
    assert "not valid JSON" in capsys.readouterr().err


def test_cli_accepts_plain_text_json_output_and_a_persona_file(tmp_path, capsys):
    plain = tmp_path / "t.txt"
    plain.write_text("\n".join(BAD_OPENER for _ in range(35)) + "\n")
    persona = tmp_path / "persona.json"
    persona.write_text(json.dumps({"traits": [{"name": "warm", "strength": "high"}], "voice_style": {"brevity": "short"}}))
    code = pd.main([str(plain), "--persona", str(persona), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == pd.EXIT_EXCEEDED and out["n_scored"] == 35 and out["bar"]["provisional"] is True
    assert BAD_OPENER not in json.dumps(out)
    bad_persona = tmp_path / "bad.json"
    bad_persona.write_text(json.dumps({"traits": [{"name": "warm", "strength": "extreme"}]}))
    assert pd.main([str(plain), "--persona", str(bad_persona)]) == pd.EXIT_ERROR


def test_cli_writes_nothing_to_disk(tmp_path):
    p = _write(tmp_path, _rows(bad=31))
    before = sorted(x.name for x in tmp_path.iterdir())
    pd.main([p, "--json"])
    assert sorted(x.name for x in tmp_path.iterdir()) == before


def test_cli_refuses_to_score_as_kid_mode():
    with pytest.raises(SystemExit):
        pd.main(["x.jsonl", "--mode", "kid"])
