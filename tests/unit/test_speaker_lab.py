"""Speaker-embedding optimisation round - the pure logic (no model, no audio, no household data).

``scripts/perf/speaker_lab.py`` (enrolment recipes, S/AS-norm, length-dependent thresholds,
paired bootstrap, the #2002 adaptation replay) and the variant / pre-processing helpers in
``scripts/perf/speaker_bakeoff.py`` run on planted synthetic embeddings and tones.  Record:
docs/research/speaker-resnet34-optimisation-2026-10.md.

Instruments are checked, not assumed: a planted-geometry set must recover EER 0, a label-free
set must sit near 50 %, a shifted variant must be flagged by the paired delta and an identical
one must not, and each adaptation guard is shown to refuse on its own.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
_had_cuda = "CUDA_VISIBLE_DEVICES" in os.environ  # the harness hides CUDA at import; do not leak that


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(REPO / "scripts" / "perf"))
bake = _load("speaker_bakeoff", "scripts/perf/speaker_bakeoff.py")
lab = _load("speaker_lab", "scripts/perf/speaker_lab.py")
if not _had_cuda:
    os.environ.pop("CUDA_VISIBLE_DEVICES", None)

SR = 16000
D = 32


def _units(rng, n, d=D):
    x = rng.standard_normal((n, d))
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def _speakers(rng, n_spk, per, noise=0.35, d=D):
    """n_spk planted speakers, ``per`` noisy clips each -> (E, labels)."""
    cents = _units(rng, n_spk, d)
    E = np.concatenate([cents[s] + noise * rng.standard_normal((per, d)) / np.sqrt(d) for s in range(n_spk)])
    lab_ = np.repeat(np.arange(n_spk), per)
    return lab.l2(E), lab_


# ----------------------------------------------------------------------------- variants
def test_parse_variant_roundtrip_and_types():
    assert bake.parse_variant("wespeaker_resnet34") == ("wespeaker_resnet34", {})
    name, o = bake.parse_variant("wespeaker_resnet34@opt=basic,intra=4,cap=3.5,capmode=dense,arena=0,file=x.onnx")
    assert name == "wespeaker_resnet34"
    assert o == {"opt": "basic", "intra": 4, "cap": 3.5, "capmode": "dense", "arena": 0, "file": "x.onnx"}


@pytest.mark.parametrize("spec", [
    "m@bogus=1", "m@opt=turbo", "m@intra=two", "m@mode=fast", "m@capmode=middle", "m@cap", "m@intra=",
])
def test_parse_variant_rejects_bad_options(spec):
    with pytest.raises(ValueError):
        bake.parse_variant(spec)


def test_variant_tag_is_filesystem_safe_and_bare_for_bare_specs():
    assert bake.variant_tag("wespeaker_resnet34") == "wespeaker_resnet34"
    t = bake.variant_tag("wespeaker_resnet34@file=a b/c.onnx,cap=3")
    assert "/" not in t and " " not in t and "@" not in t and "," not in t


# ----------------------------------------------------------------------------- pre-processing
def _tone_with_silence(sec_pre=0.5, sec_voice=2.0, sec_gap=0.6, sec_post=0.5):
    t = np.arange(int(sec_voice * SR)) / SR
    voice = (0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    z = lambda s: np.zeros(int(s * SR), np.float32) + 1e-5  # noqa: E731
    return np.concatenate([z(sec_pre), voice, z(sec_gap), voice, z(sec_post)])


def test_trim_edges_keeps_interior_strip_removes_it():
    x = _tone_with_silence()
    trimmed = bake.apply_prep(x, {"trim": 40})
    assert len(trimmed) < len(x) - int(0.8 * SR) and len(trimmed) > len(x) - int(1.3 * SR)  # edges only
    stripped = bake.apply_prep(x, {"strip": 40})
    assert len(stripped) < len(trimmed) - int(0.3 * SR)  # the 0.6 s gap is gone too


def test_strip_returns_input_when_nothing_would_remain():
    x = np.zeros(SR, np.float32) + 1e-6
    assert bake.strip_silence(x, 40) is x or len(bake.strip_silence(x, 40)) == len(x)


def test_cap_modes():
    x = np.arange(10 * SR, dtype=np.float32)
    assert len(bake.cap_window(x, 3.0)) == 3 * SR
    assert bake.cap_window(x, 3.0, "first")[0] == 0
    assert bake.cap_window(x, 3.0, "last")[-1] == x[-1]
    c = bake.cap_window(x, 4.0, "center")
    assert c[0] == (10 - 4) // 2 * SR
    short = x[: 2 * SR]
    assert bake.cap_window(short, 3.0) is short  # never pads or copies a clip under the cap


def test_cap_dense_picks_the_voiced_window():
    z = np.zeros(4 * SR, np.float32) + 1e-5
    v = (0.3 * np.sin(2 * np.pi * 200 * np.arange(3 * SR) / SR)).astype(np.float32)
    x = np.concatenate([z, v, z])  # voiced seconds 4-7 of 11
    w = bake.cap_window(x, 3.0, "dense")
    assert len(w) == 3 * SR and float(np.abs(w).mean()) > 0.15
    assert float(np.abs(bake.cap_window(x, 3.0, "first")).mean()) < 0.05


def test_apply_prep_is_identity_without_options():
    x = _tone_with_silence()
    assert bake.apply_prep(x, {}) is x


# ----------------------------------------------------------------------------- enrolment recipes
def test_mean_is_unit_norm_and_order_free():
    rng = np.random.default_rng(1)
    E = _units(rng, 5) * rng.uniform(0.5, 3, (5, 1))  # arbitrary clip norms must not matter
    a = lab.enrol_centroid(E, method="mean")
    b = lab.enrol_centroid(E[::-1], method="mean")
    assert abs(np.linalg.norm(a) - 1) < 1e-9 and np.allclose(a, b)


def test_wmean_leans_toward_the_long_clip():
    rng = np.random.default_rng(2)
    E = _units(rng, 3)
    plain = lab.enrol_centroid(E, method="mean")
    w = lab.enrol_centroid(E, durs=[8.0, 1.0, 1.0], method="wmean")
    assert float(w @ E[0]) > float(plain @ E[0])
    same = lab.enrol_centroid(E, durs=[3.0, 3.0, 3.0], method="wmean")
    assert np.allclose(same, plain)


def test_reject_drops_the_stranger_and_never_below_min_keep():
    rng = np.random.default_rng(3)
    spk = _units(rng, 1)[0]
    good = lab.l2(spk + 0.15 * rng.standard_normal((4, D)) / np.sqrt(D))
    stranger = _units(rng, 1)
    E = np.concatenate([good[:2], stranger, good[2:]])
    clean = lab.enrol_centroid(good, method="mean")
    dirty = lab.enrol_centroid(E, method="mean")
    fixed = lab.enrol_centroid(E, method="reject", tau=0.5)
    assert float(fixed @ clean) > float(dirty @ clean) + 0.02  # rejection restores the profile
    assert float(fixed @ clean) > 0.98
    # three mutually unrelated clips: nothing can be kept past min_keep
    X = _units(rng, 3)
    out = lab.enrol_centroid(X, method="reject", tau=0.99, min_keep=2)
    assert abs(np.linalg.norm(out) - 1) < 1e-9


def test_trim1_only_acts_with_five_or_more_clips():
    rng = np.random.default_rng(4)
    E = _units(rng, 4)
    assert np.allclose(lab.enrol_centroid(E, method="trim1"), lab.enrol_centroid(E, method="mean"))
    E5 = _units(rng, 5)
    assert not np.allclose(lab.enrol_centroid(E5, method="trim1"), lab.enrol_centroid(E5, method="mean"))


def test_unknown_method_raises():
    with pytest.raises(ValueError):
        lab.enrol_centroid(_units(np.random.default_rng(0), 3), method="median")


def test_parse_kv():
    assert lab.parse_kv("a=1,b=2.5,c=x") == {"a": 1, "b": 2.5, "c": "x"}
    assert lab.parse_kv("") == {}


# ----------------------------------------------------------------------------- normalisation
def test_raw_scorer_is_plain_dot():
    rng = np.random.default_rng(5)
    A, X = _units(rng, 3), _units(rng, 7)
    sc = lab.Scorer("raw")
    assert np.allclose(sc.score(A, sc.stats(A), X, sc.stats(X)), A @ X.T)


def test_snorm_matches_the_textbook_formula():
    rng = np.random.default_rng(6)
    C, A, X = _units(rng, 50), _units(rng, 3), _units(rng, 7)
    sc = lab.Scorer("snorm", C)
    got = sc.score(A, sc.stats(A), X, sc.stats(X))
    S = A @ X.T
    ea, ex = A @ C.T, X @ C.T
    want = 0.5 * ((S - ea.mean(1)[:, None]) / (ea.std(1)[:, None] + 1e-6) + (S - ex.mean(1)[None]) / (ex.std(1)[None] + 1e-6))
    assert np.allclose(got, want)


def test_asnorm_uses_only_the_top_k_and_k_all_equals_snorm():
    rng = np.random.default_rng(7)
    C, A, X = _units(rng, 40), _units(rng, 2), _units(rng, 5)
    full = lab.Scorer("snorm", C)
    top = lab.Scorer("asnorm", C, k=40)
    assert np.allclose(full.score(A, full.stats(A), X, full.stats(X)), top.score(A, top.stats(A), X, top.stats(X)))
    k5 = lab.Scorer("asnorm", C, k=5)
    mu, sd = k5.stats(X)
    top5 = -np.sort(-(X @ C.T), axis=1)[:, :5]
    assert np.allclose(mu, top5.mean(1)) and np.allclose(sd, top5.std(1) + 1e-6)
    assert not np.allclose(mu, full.stats(X)[0])


def test_z_and_t_norm_are_the_one_sided_halves():
    rng = np.random.default_rng(8)
    C, A, X = _units(rng, 30), _units(rng, 2), _units(rng, 4)
    s = lab.Scorer("snorm", C)
    z, t = lab.Scorer("znorm", C), lab.Scorer("tnorm", C)
    zs = z.score(A, z.stats(A), X, z.stats(X))
    ts = t.score(A, t.stats(A), X, t.stats(X))
    assert np.allclose(0.5 * (zs + ts), s.score(A, s.stats(A), X, s.stats(X)))


def test_normalised_scorer_requires_a_cohort():
    with pytest.raises(ValueError):
        lab.Scorer("asnorm")
    with pytest.raises(ValueError):
        lab.Scorer("quantum", np.eye(3))


def test_centering_removes_a_shared_channel_offset():
    rng = np.random.default_rng(9)
    E, y = _speakers(rng, 6, 10, noise=0.25)
    chan = _units(rng, 1)[0] * 2.0  # a common (channel) component added to everything
    Eb = lab.l2(E + chan)
    mu = Eb[:30].mean(axis=0)  # a cohort that shares the channel
    Ec = lab.centered(Eb, mu)

    def gap(M):
        S = M @ M.T
        same = y[:, None] == y[None, :]
        np.fill_diagonal(same, False)
        return S[same].mean() - S[y[:, None] != y[None, :]].mean()

    assert gap(Ec) > gap(Eb)


# ----------------------------------------------------------------------------- metrics + bootstrap
def test_planted_speakers_are_perfectly_separated_and_shuffled_labels_are_not():
    rng = np.random.default_rng(10)
    E, y = _speakers(rng, 12, 8, noise=0.05)
    cents = np.stack([lab.enrol_centroid(E[y == s][:3]) for s in range(12)])
    probes = np.stack([E[(y == s)][3:] for s in range(12)])  # (12, 5, D)
    P = probes.reshape(-1, D)
    py = np.repeat(np.arange(12), 5)
    S = cents @ P.T
    same = np.arange(12)[:, None] == py[None, :]
    m = lab.metrics(S[same], S[~same])
    assert m["eer"] == 0.0 and m["frr_far1"] == 0.0
    flat = np.concatenate([S[same], S[~same]])
    np.random.default_rng(0).shuffle(flat)
    shuffled = lab.metrics(flat[: same.sum()], flat[same.sum():])
    assert 0.35 < shuffled["eer"] < 0.65  # no labels, no signal: the instrument can fail


def test_paired_delta_flags_a_real_shift_and_ignores_an_identical_copy():
    rng = np.random.default_rng(11)
    n = 300
    G = np.vstack([rng.normal(0.6, 0.12, n)] * 4)
    I = np.vstack([rng.normal(0.1, 0.12, n)] * 4)
    idx = lab.boot_indices(n, n, 60, seed=3)
    base = lab.boot_owner(G, I, idx)
    same = lab.paired_delta(lab.boot_owner(G.copy(), I.copy(), idx), base)
    assert all(not v["excludes_0"] and v["mean"] == 0.0 for v in same.values())
    worse = lab.paired_delta(lab.boot_owner(G - 0.20, I, idx), base)  # genuine scores sag 0.2
    assert worse["eer"]["excludes_0"] and worse["eer"]["mean"] > 0.03
    c = lab.ci(base)
    assert all(lo <= hi for lo, hi in c.values())
    assert lab.inside({"eer": 0.5, "frr_far1": 0.5, "frr_far01": 0.5}, c) == {k: False for k in lab.KEYS}


# ----------------------------------------------------------------------------- owner draws
def test_owner_draws_reproduce_the_bakeoff_generator_exactly():
    rng = np.random.default_rng(12)
    n = 400
    sess = rng.integers(0, 40, n)
    dur = rng.uniform(1.0, 6.0, n)
    ok = (dur >= 2.0) & (rng.random(n) < 0.9)
    seed = 7
    got = lab.owner_draws(ok, sess, seed, 30, 3, 3)
    # the bake-off's own loop, verbatim (cmd_eval.pooled)
    sess_ids = [s for s in np.unique(sess[ok]) if (ok & (sess == s)).sum() >= 3]
    r2 = np.random.default_rng(seed + 1)
    want = []
    for _ in range(30):
        s = sess_ids[r2.integers(len(sess_ids))]
        want.append(r2.choice(np.where(ok & (sess == s))[0], 3, replace=False))
    assert all(np.array_equal(a, b) for a, b in zip(got, want))


def test_owner_draws_nested_prefixes_and_pool_floor():
    rng = np.random.default_rng(13)
    sess = np.repeat(np.arange(10), 10)
    ok = np.ones(100, bool)
    ok[:25] = False  # sessions 0,1 and half of 2 lose clips
    d8 = lab.owner_draws(ok, sess, 7, 12, 8, 8)
    assert all(len(set(sess[d])) == 1 and len(d) == 8 and len(set(d)) == 8 for d in d8)
    assert all((ok & (sess == sess[d[0]])).sum() >= 8 for d in d8)
    with pytest.raises(ValueError):
        lab.owner_draws(ok, sess, 7, 3, 11, 11)


def test_owner_scores_leave_the_enrolment_session_out_and_score_cosines():
    rng = np.random.default_rng(14)
    E, y = _speakers(rng, 4, 30, noise=0.2)  # 4 "sessions" of the same speaker are overkill: one speaker, 4 sessions
    Ec = E[:120]
    csess = np.repeat(np.arange(8), 15)
    cdur = np.full(120, 3.0)
    Ei = lab.l2(rng.standard_normal((40, D)))
    core = np.ones(120, bool)
    ok = core & (cdur >= 2)
    draws = lab.owner_draws(ok, csess, 7, 5, 3, 3)
    tr = lab.owner_scores(Ec, Ei, cdur, csess, core, np.arange(30), np.arange(30, 40), draws, lab.Scorer("raw"))
    G = tr["G"]
    assert G.shape == (5, 120) and tr["If"].shape == (5, 30) and tr["Ic"].shape == (5, 10)
    for d, en in enumerate(draws):
        assert np.isnan(G[d, csess == csess[en[0]]]).all()
        assert np.isfinite(G[d, csess != csess[en[0]]]).all()
    assert np.all(np.abs(tr["If"]) <= 1.0 + 1e-9)


# ----------------------------------------------------------------------------- public axis
def test_public_axis_recovers_planted_speakers_and_reports_buckets():
    rng = np.random.default_rng(15)
    n_spk, per_enrol, per_probe = 10, 4, 12
    E, y = _speakers(rng, n_spk, per_enrol + per_probe, noise=0.12)
    role_probe = np.tile(np.r_[np.zeros(per_enrol, bool), np.ones(per_probe, bool)], n_spk)
    spk = np.array([f"s{v:02d}" for v in y])
    e_idx, p_idx = np.where(~role_probe)[0], np.where(role_probe)[0]
    bucket = np.array(list("sml"))[np.arange(len(p_idx)) % 3]
    S, same = lab.public_scores(E, p_idx, e_idx, spk, lab.Scorer("raw"), n_enrol=3)
    assert S.shape == (4, len(p_idx), n_spk) and same.sum() == len(p_idx)  # C(4,3)=4 hold-out combos
    rep = lab.public_report(S, same, bucket, n_boot=20)
    assert rep["all"]["eer"] < 0.05 and set(lab.BUCKETS) <= {k[-1] for k in rep if k.startswith("bucket_")}
    assert rep["ci95"]["eer"][0] <= rep["all"]["eer"] <= rep["ci95"]["eer"][1] + 1e-9
    S2, _ = lab.public_scores(E, p_idx, e_idx, spk, lab.Scorer("raw"), n_enrol=2)
    assert S2.shape[0] == 6  # C(4,2)


# ----------------------------------------------------------------------------- length thresholds
def test_length_dependent_thresholds_equalise_far_across_duration_groups():
    rng = np.random.default_rng(16)
    n_draw, n_imp, n_gen = 3, 4000, 600
    idur = np.where(rng.random(n_imp) < 0.4, 1.5, 3.5)
    ispk = rng.integers(0, 60, n_imp)
    # short impostors score HIGHER (shorter clips are noisier / less separable)
    I = np.where(idur[None, :] <= 2.0, rng.normal(0.18, 0.14, (n_draw, n_imp)), rng.normal(0.05, 0.08, (n_draw, n_imp)))
    gdur = np.where(rng.random(n_gen) < 0.4, 1.5, 3.5)
    G = np.where(gdur[None, :] <= 2.0, rng.normal(0.55, 0.15, (n_draw, n_gen)), rng.normal(0.62, 0.12, (n_draw, n_gen)))
    out = lab.length_threshold_cv(G, gdur, I, idur, ispk, [2.0], n_rep=6)
    g, l = out["far0.01"]["global"], out["far0.01"]["length"]
    # a single threshold lets the short group run far above the 1 % target; per-group thresholds do not
    assert g["far_by_group"][0] > 0.02 and g["far_by_group"][1] < 0.006
    assert abs(l["far_by_group"][0] - 0.01) < 0.006 and abs(l["far_by_group"][1] - 0.01) < 0.006
    assert 0.0 <= l["frr"] <= 1.0


@pytest.mark.filterwarnings("ignore::RuntimeWarning")  # the single-group control leaves a group empty on purpose
def test_length_threshold_ci_is_paired_and_shows_the_far_gap_closing():
    rng = np.random.default_rng(26)
    n_draw, n_imp, n_gen = 3, 3000, 500
    idur = np.where(rng.random(n_imp) < 0.4, 1.5, 3.5)
    ispk = rng.integers(0, 60, n_imp)
    I = np.where(idur[None, :] <= 2.0, rng.normal(0.18, 0.14, (n_draw, n_imp)), rng.normal(0.05, 0.08, (n_draw, n_imp)))
    gdur = np.where(rng.random(n_gen) < 0.4, 1.5, 3.5)
    G = np.where(gdur[None, :] <= 2.0, rng.normal(0.55, 0.15, (n_draw, n_gen)), rng.normal(0.62, 0.12, (n_draw, n_gen)))
    out = lab.length_threshold_cv(G, gdur, I, idur, ispk, [2.0], n_rep=4, n_boot=40)
    ci_ = out["ci"]["far0.01"]
    # the global threshold leaves a FAR gap between the groups whose interval excludes 0; per-group thresholds close it
    assert ci_["far_gap_global"]["ci95"][0] > 0
    assert abs(ci_["far_gap_length"]["mean"]) < 0.5 * abs(ci_["far_gap_global"]["mean"])
    for k in ("frr_delta_overall", "frr_delta_g0", "frr_delta_g1"):
        lo, hi = ci_[k]["ci95"]
        assert lo <= ci_[k]["mean"] <= hi
    # the CI means track the point estimates of the same run (paired, same tallies)
    pt = out["far0.01"]
    assert abs(ci_["far_global_g0"]["mean"] - pt["global"]["far_by_group"][0]) < 0.01
    # identical schemes (one duration group, so length == global) => the paired FRR difference is exactly 0
    same = lab.length_threshold_cv(G, np.full(n_gen, 3.5), I, np.full(n_imp, 3.5), ispk, [2.0], n_rep=2, n_boot=20)
    assert same["ci"]["far0.01"]["frr_delta_overall"]["ci95"] == [0.0, 0.0]


def test_adapt_boot_is_paired_and_zero_when_nothing_changed():
    rng = np.random.default_rng(27)
    per = []
    for _ in range(12):
        gb = rng.normal(0.5, 0.1, 80)
        ib = rng.normal(0.0, 0.1, 200)
        per.append({"gb": gb, "ga": gb + 0.1, "ib": ib, "ia": ib.copy()})
    d = lab.adapt_boot(per, 0.4, 0.5, n_boot=60)
    assert d["genuine_mean"]["ci95"][0] > 0.09 and d["frr_far1"]["ci95"][1] < 0  # genuine scores rose, FRR fell
    assert d["far_at_thr1"]["ci95"] == [0.0, 0.0]  # impostors untouched => paired difference is exactly 0
    for p in per:
        p["ga"] = p["gb"].copy()
    z = lab.adapt_boot(per, 0.4, 0.5, n_boot=30)
    assert all(v["ci95"] == [0.0, 0.0] for v in z.values())
    assert lab.draw_boot_diff([1, 2, 3], [1, 2, 3], 20)["ci95"] == [0.0, 0.0]
    b = lab.draw_boot([0.0, 1.0, 1.0, 1.0], 100)
    assert b["ci95"][0] <= b["mean"] <= b["ci95"][1]


def test_public_report_explicit_boot_indices_match_the_seeded_default():
    rng = np.random.default_rng(28)
    E, y = _speakers(rng, 6, 16, noise=0.2)
    role_probe = np.tile(np.r_[np.zeros(4, bool), np.ones(12, bool)], 6)
    spk = np.array([f"s{v}" for v in y])
    e_idx, p_idx = np.where(~role_probe)[0], np.where(role_probe)[0]
    S, same = lab.public_scores(E, p_idx, e_idx, spk, lab.Scorer("raw"))
    bucket = np.array(list("sml"))[np.arange(len(p_idx)) % 3]
    a = lab.public_report(S, same, bucket, n_boot=15)
    idx = lab.public_boot_indices(len(p_idx), 15, 11)
    b = lab.public_report(S, same, bucket, n_boot=99, idx=idx)  # idx wins over n_boot
    assert np.array_equal(a["_boots"], b["_boots"])


def test_group_of_edges_are_inclusive_on_the_low_side():
    assert list(lab.group_of([1.0, 2.0, 2.01, 5.0], [2.0])) == [0, 0, 1, 1]
    assert list(lab.group_of([1.0, 2.0, 3.0, 4.0, 5.0], [2.0, 4.0])) == [0, 0, 1, 1, 2]


# ----------------------------------------------------------------------------- adaptation replay
def _stream(rng, anchor, n, noise):
    return lab.l2(anchor + noise * rng.standard_normal((n, D)) / np.sqrt(D))


def _t(n, gap=3600.0 * 5):
    return np.arange(n) * gap + 1.7e9


def test_adapt_applies_high_confidence_owner_turns_and_pulls_toward_the_voice():
    rng = np.random.default_rng(17)
    anchor = _units(rng, 1)[0]
    drift = lab.l2(anchor + 0.6 * _units(rng, 1)[0])  # the voice has drifted (cold, new mic ...)
    clips = _stream(rng, drift, 60, 0.15)
    prof, applied, why = lab.adapt_replay(anchor, clips, _t(60), np.full(60, 4.0), min_score=0.5, min_anchor_cos=0.5)
    assert applied.sum() > 5 and why["applied"] == int(applied.sum())
    assert float(prof @ drift) > float(anchor @ drift)
    assert float(prof @ anchor) >= 0.92 - 1e-9


@pytest.mark.parametrize("kw,reason", [
    ({"min_score": 0.99}, "low_score"),
    ({"min_anchor_cos": 0.999, "min_score": -1.0}, "off_anchor"),
    ({"min_speech_s": 9.0, "min_score": -1.0, "min_anchor_cos": -1.0}, "short_clip"),
])
def test_each_guard_refuses_on_its_own(kw, reason):
    rng = np.random.default_rng(18)
    anchor = _units(rng, 1)[0]
    clips = _stream(rng, anchor, 10, 0.3)
    base = dict(min_score=-1.0, min_anchor_cos=-1.0)
    base.update(kw)
    _, applied, why = lab.adapt_replay(anchor, clips, _t(10), np.full(10, 3.0), **base)
    assert applied.sum() == 0 and why.get(reason) == 10


def test_rate_limits_gap_and_per_day():
    rng = np.random.default_rng(19)
    anchor = _units(rng, 1)[0]
    clips = np.repeat(anchor[None], 30, axis=0)
    day0 = 1.7e9 // 86400 * 86400  # a UTC midnight, so the whole test sits inside one UTC day
    # one offer per minute for 30 min: the 600 s gap admits offers 0, 10 and 20 only
    _, a1, w1 = lab.adapt_replay(anchor, clips, day0 + np.arange(30) * 60.0, np.full(30, 3.0), min_score=0.0, min_anchor_cos=0.0)
    assert a1.sum() == 3 and w1["rate_interval"] == 27
    # every 11 minutes for 5.5 h: the gap always passes, the per-day cap stops it at 5
    _, a2, w2 = lab.adapt_replay(anchor, clips, day0 + np.arange(30) * 660.0, np.full(30, 3.0), min_score=0.0, min_anchor_cos=0.0,
                                 drift_bound=-1.0)
    assert a2.sum() == 5 and w2["rate_day"] == 25


def test_drift_bound_holds_against_a_persistent_adversary():
    rng = np.random.default_rng(20)
    anchor = _units(rng, 1)[0]
    evil = lab.l2(_units(rng, 1)[0] - (_units(rng, 1)[0] @ anchor) * anchor + 0.3 * anchor)
    n = 5000
    prof, applied, why = lab.adapt_replay(anchor, np.repeat(evil[None], n, axis=0), np.arange(n) * 90000.0,
                                          np.full(n, 3.0), min_score=-1.0, min_anchor_cos=-1.0)
    assert float(prof @ anchor) >= 0.92 - 1e-9
    assert why.get("drift_bound", 0) > 0 and applied.sum() < n  # refused, not clamped


# ----------------------------------------------------------------------------- end to end on a synthetic scratch dir
def _synthetic_scratch(tmp_path, rng):
    """A miniature scratch dir with the bake-off's file layout: one 'owner' voice in 12 sessions, 8 public
    speakers (4 far-field enrolment + 12 probes each), 10 TTS voices, a 40-clip cohort, planted geometry."""
    import json

    d = 48
    owner = _units(rng, 1, d)[0]
    chan = _units(rng, 1, d)[0] * 0.8  # shared far-field channel component
    n_sess, per = 12, 14
    names, dur, sess, mtime, emb_c = [], [], [], [], []
    t0 = 1.7e9 // 86400 * 86400
    for s_ in range(n_sess):
        for k in range(per):
            names.append(f"c{s_}_{k}.wav")
            dur.append(float(rng.choice([1.5, 2.5, 3.5, 5.0])))
            sess.append(s_)
            mtime.append(t0 + s_ * 86400 + k * 700.0)
            emb_c.append(owner + 0.30 * rng.standard_normal(d) / np.sqrt(d) + 0.2 * chan)
    idx = {"root": str(tmp_path), "names": names, "dur": dur, "session": sess, "mtime": mtime}
    man, emb_i = [], []

    def add(src, spk, role, bucket, var, vec):
        man.append({"source": src, "speaker": spk, "role": role, "bucket": bucket, "variant": var,
                    "path": f"{src}/{var}/{len(man)}.wav", "dur": {"s": 1.5, "m": 3.0, "l": 6.0}[bucket]})
        emb_i.append(vec + 0.2 * chan)

    for sp in range(8):
        c = _units(rng, 1, d)[0]
        for e in range(4):
            add("libri", f"L{sp}", "enrol", "l", "farfield", c + 0.25 * rng.standard_normal(d) / np.sqrt(d))
        for j in range(12):
            b = "sml"[j % 3]
            v = c + 0.25 * rng.standard_normal(d) / np.sqrt(d)
            add("libri", f"L{sp}", "probe", b, "farfield", v)
            add("libri", f"L{sp}", "probe", b, "clean", v)
    for sp in range(10):
        c = _units(rng, 1, d)[0]
        for j in range(6):
            add("kokoro", f"K{sp}", "probe", "sml"[j % 3], "farfield", c + 0.25 * rng.standard_normal(d) / np.sqrt(d))
    coh, emb_h = [], []
    for sp in range(20):
        c = _units(rng, 1, d)[0]
        for v in ("clean", "farfield"):
            coh.append({"source": "cohort", "speaker": f"H{sp}", "role": "cohort", "bucket": "m", "variant": v,
                        "path": f"{v}/{sp}.wav", "dur": 3.0})
            emb_h.append(c + 0.25 * rng.standard_normal(d) / np.sqrt(d) + (0.2 * chan if v == "farfield" else 0))
    (tmp_path / "impostors").mkdir()
    (tmp_path / "impostors" / "manifest.json").write_text(json.dumps(man))
    (tmp_path / "cohort").mkdir()
    (tmp_path / "cohort" / "manifest.json").write_text(json.dumps(coh))
    (tmp_path / "corpus_index.json").write_text(json.dumps(idx))
    E = np.vstack([emb_c, emb_i, emb_h]).astype(np.float32)
    meta = json.dumps({"model": "synthetic"})
    np.savez(tmp_path / "emb_syn.npz", emb=E, secs=np.zeros(len(E)), dur=np.zeros(len(E)), group=np.array([""] * len(E)), meta=meta)
    np.savez(tmp_path / "emb_syn2.npz", emb=(E + 0.01).astype(np.float32), secs=np.zeros(len(E)), dur=np.zeros(len(E)),
             group=np.array([""] * len(E)), meta=meta)
    n_c = len(names)
    np.savez(tmp_path / "core.npz", strict=np.ones(n_c, bool), majority=np.ones(n_c, bool), finite_all=np.ones(n_c, bool))
    return lab.Ctx(tmp_path, tmp_path / "corpus_index.json", tmp_path / "core.npz", "syn")


def _args(**kw):
    import argparse

    return argparse.Namespace(**{"seed": 7, "boot": 8, "args": "", **kw})


def test_every_experiment_runs_end_to_end_on_a_synthetic_scratch_dir(tmp_path):
    ctx = _synthetic_scratch(tmp_path, np.random.default_rng(21))
    a = _args(boot=2)
    base = lab.baseline_table(ctx, a, {})
    assert base["owner_strict"]["all"]["eer"] < 0.2 and base["public_farfield"]["all"]["eer"] < 0.2
    var = lab.exp_variant(ctx, a, {"tag": "syn2"})  # a +0.01 shift moves the headline metric by (almost) nothing
    assert abs(var["owner_strict"]["delta"]["eer"]["mean"]) < 0.05
    assert set(var["public"]["inside_base_ci"]) == set(lab.KEYS)
    enrol = lab.exp_enrol(ctx, a, {})
    assert {"n3/mean", "n5/reject0.30", "n8/mean"} <= set(enrol["owner"])
    assert enrol["owner_contaminated"]["n5_1bad/wmean"]["delta"]  # contamination arms carry paired deltas
    norm = lab.exp_norm(ctx, a, {"quick": 1})
    assert "asnorm k=50[farfield]" in norm["owner"] and "raw" in norm["public"]
    sens = lab.exp_centersens(ctx, a, {})
    assert "all_240" in sens["owner"] and "none" in sens["public"]
    len_ = lab.exp_lenthr(ctx, a, {})
    assert {"far0.01", "far0.001", "ci"} <= set(len_["edges_2s"]) and len_["thresholds_insample"]["far0.01"]["global"] > 0
    ad = lab.exp_adapt(ctx, a, {})
    assert set(ad["arms"]) >= {"resemblyzer_scale_defaults", "far01_gate+hi_anchor"}
    far = ad["arms"]["far01_gate"]
    assert far["n_draws_used"] > 0 and far["applied"] > 0 and far["anchor_cos"] >= 0.92 - 1e-9  # the drift bound holds
    assert ad["arms"]["far01_gate+hi_anchor"]["kw"]["min_anchor_cos"] >= far["kw"]["min_anchor_cos"]
    assert {"delta_ci", "lookalike_delta", "poison_admitted_ci"} <= set(far) and "far01_gate+hi_anchor minus far01_gate" in ad["arm_vs_arm"]
    fin = lab.exp_final(ctx, a, {"tag": "syn", "center": 1})
    assert "final_n3" in fin["owner_strict"] and "final_n4" in fin["public"]
    text = lab.show(fin) + lab.show_other(len_) + lab.show_other(ad)
    assert "EER" in text and "FAR" in text


def test_show_reprints_a_saved_result_without_the_scratch_dir(tmp_path):
    import json

    rep = {"all": {"eer": 0.01, "frr_far1": 0.02, "frr_far01": 0.03}, "ci95": {k: [0.0, 0.05] for k in lab.KEYS}}
    f = tmp_path / "r.json"
    f.write_text(json.dumps({"owner": rep}))
    a = _args(experiment="show", args=f"file={f}", scratch=str(tmp_path), corpus_index="x", core="x", base="x", json=None)
    lab.run(a)


def test_centred_analysis_refuses_missing_cohort_rows_and_keeps_missing_trials_absent(tmp_path):
    ctx = _synthetic_scratch(tmp_path, np.random.default_rng(31))
    Ec, Ei, Eh, mu = ctx.split("syn", center=True)  # complete cohort: fine
    assert mu is not None and np.isfinite(Ec).all()
    z = np.load(tmp_path / "emb_syn.npz")
    E = z["emb"].copy()
    E[ctx.n_c + 3] = np.nan  # one impostor trial row never embedded

    def save(tag, arr):
        np.savez(tmp_path / f"emb_{tag}.npz", emb=arr, secs=z["secs"], dur=z["dur"], group=z["group"], meta=str(z["meta"]))

    save("part", E)
    _, Ei2, _, _ = ctx.split("part", center=True)
    assert np.allclose(Ei2[3], 0.0) and not np.allclose(Ei2[4], 0.0)  # absent stays absent, not normalize(-mu)
    E[ctx.n_c + ctx.n_i + 1] = np.nan  # now a cohort row is missing too
    save("part2", E)
    with pytest.raises(ValueError, match="complete, finite"):
        ctx.split("part2", center=True)
    E2 = z["emb"].copy()
    E2[ctx.n_c + ctx.n_i:] = np.nan  # a `--groups imp` screening artefact: no cohort at all
    save("imp", E2)
    with pytest.raises(ValueError):
        ctx.split("imp", center=True)
    ctx.split("imp", center=False)  # uncentred use of a screening artefact still works
