#!/usr/bin/env python3
"""Accuracy levers for the WeSpeaker ResNet34 optimisation round (numpy only, no model code).

Record: docs/research/speaker-resnet34-optimisation-2026-10.md.  Driven by
``scripts/perf/speaker_bakeoff.py lab`` - which hands this module the scratch directory that
holds the embeddings (``emb_<tag>.npz``), the frozen owner-corpus index, the owner-core masks and
the public impostor / cohort manifests.  Nothing here reads audio, writes into the repository or
prints clip names: every output is an aggregate.

The TRIAL DESIGN is the bake-off's, reproduced from ``cmd_eval`` so every number is comparable:

* owner corpus: 30 random enrolment draws (3 clips of >= 2 s from ONE capture session); genuine
  probes = the strict/majority core clips of OTHER sessions; impostors = 476 LibriSpeech +
  280 Kokoro far-field clips (all labelled, all public).
* public axis: the 40 LibriSpeech test-clean speakers enrol 3 of 4 far-field clips (4 hold-outs),
  probes are their own 12 crops (genuine) vs the other 39 speakers' (impostor).

Everything new is a pure function (enrolment recipes, S/AS-norm, length-dependent thresholds with
speaker-disjoint folds, paired bootstrap deltas, the adaptation replay) so it can be unit-tested
on synthetic embeddings (tests/unit/test_speaker_lab.py).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from speaker_bakeoff import det_point, dist_summary, dprime  # noqa: E402  (pure numpy helpers)

BUCKETS = ("s", "m", "l")
BUCKET_NAMES = {"s": "<=2s", "m": "2-4s", "l": ">4s"}


# ----------------------------------------------------------------------------- vectors
def l2(x):
    x = np.asarray(x, dtype=np.float64)
    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.maximum(n, 1e-12)


def centered(E, mu):
    """Subtract a (public) mean vector and re-normalise - 'cohort-mean centring'."""
    return l2(np.asarray(E, dtype=np.float64) - mu)


# ----------------------------------------------------------------------------- enrolment recipes
def parse_kv(s: str) -> dict:
    """``a=1,b=x`` -> {'a': 1, 'b': 'x'} (ints/floats converted)."""
    out: dict = {}
    for kv in filter(None, (s or "").split(",")):
        k, _, v = kv.partition("=")
        for cast in (int, float):
            try:
                v = cast(v)  # type: ignore[assignment]
                break
            except ValueError:
                continue
        out[k] = v
    return out


def enrol_centroid(E, durs=None, method: str = "mean", tau: float = 0.30, wpow: float = 1.0,
                   min_keep: int = 2):
    """One profile vector from enrolment clip embeddings ``E`` (n, d).

    mean    : mean of the L2-normalised clips (what the panel does today), re-normalised.
    wmean   : the same with clip weights ``durs ** wpow`` (length-weighted).
    reject  : drop, one at a time, the clip whose leave-one-out cosine to the mean of the others
              is lowest while it is below ``tau`` (a clip that looks like a stranger to the rest
              of the enrolment), keeping at least ``min_keep``; mean of the survivors.
    trim1   : always drop the single lowest-leave-one-out clip when there are >= 5 (trimmed mean).
    """
    E = l2(E)
    n = len(E)
    w = np.ones(n) if durs is None or method == "mean" else np.maximum(np.asarray(durs, float), 1e-3) ** wpow
    if method in ("mean", "wmean"):
        return l2((E * w[:, None]).sum(axis=0))
    keep = list(range(n))

    def loo(idx):
        out = []
        for i in idx:
            rest = [j for j in idx if j != i]
            out.append(float(E[i] @ l2(E[rest].mean(axis=0))))
        return np.array(out)

    if method == "reject":
        while len(keep) > min_keep:
            c = loo(keep)
            k = int(np.argmin(c))
            if c[k] >= tau:
                break
            keep.pop(k)
    elif method == "trim1":
        if n >= 5:
            keep.pop(int(np.argmin(loo(keep))))
    else:
        raise ValueError(f"unknown enrolment method {method!r}")
    return l2(E[keep].mean(axis=0))


# ----------------------------------------------------------------------------- score normalisation
class Scorer:
    """Cosine of profile rows ``A`` against probe rows ``X`` with optional cohort normalisation.

    mode: raw | snorm (mean/sd over the whole cohort) | asnorm (over the top-k cohort scores) |
    znorm (enrolment side only) | tnorm (probe side only).  The cohort is a set of PUBLIC-speaker
    embeddings (never household), and S/AS-norm averages the enrolment-side and probe-side z-scores
    (Karam et al., Matejka et al.).
    """

    def __init__(self, mode: str = "raw", cohort=None, k: int | None = None):
        if mode not in ("raw", "snorm", "asnorm", "znorm", "tnorm"):
            raise ValueError(mode)
        if mode != "raw" and cohort is None:
            raise ValueError(f"{mode} needs a cohort")
        self.mode = mode
        self.C = None if cohort is None else l2(cohort)
        self.k = None if mode == "snorm" else k

    def stats(self, X):
        """(mean, sd) of each row's scores against the cohort (top-k for asnorm); None for raw."""
        if self.mode == "raw":
            return None
        S = np.asarray(X, dtype=np.float64) @ self.C.T
        if self.k and self.k < S.shape[1]:
            S = -np.partition(-S, self.k - 1, axis=1)[:, : self.k]
        return S.mean(axis=1), S.std(axis=1) + 1e-6

    def score(self, A, sa, X, sx):
        S = np.asarray(A, dtype=np.float64) @ np.asarray(X, dtype=np.float64).T
        if self.mode == "raw":
            return S
        za = (S - sa[0][:, None]) / sa[1][:, None]
        zx = (S - sx[0][None, :]) / sx[1][None, :]
        if self.mode == "znorm":
            return za
        if self.mode == "tnorm":
            return zx
        return 0.5 * (za + zx)


# ----------------------------------------------------------------------------- bootstrap
def _flat(M):
    M = np.asarray(M)
    return M[np.isfinite(M)]


def metrics(G, I) -> dict:
    """EER / FRR@FAR1% / FRR@FAR0.1% (+ thresholds) from genuine and impostor score arrays."""
    return det_point(_flat(G), _flat(I))


def boot_indices(n_g: int, n_i: int, n_boot: int, seed: int):
    """Shared resample indices over probe CLIPS so configs can be compared PAIRED."""
    r = np.random.default_rng(seed)
    return r.integers(0, n_g, (n_boot, n_g)), r.integers(0, n_i, (n_boot, n_i))


KEYS = ("eer", "frr_far1", "frr_far01")


def boot_owner(G, I, idx) -> np.ndarray:
    """(n_boot, 3) metrics over columns resampled by ``idx`` = boot_indices(...)."""
    gi, ii = idx
    out = np.zeros((len(gi), 3))
    for b in range(len(gi)):
        r = det_point(_flat(G[:, gi[b]]), _flat(I[:, ii[b]]))
        out[b] = [r[k] for k in KEYS]
    return out


def ci(boots, q=(2.5, 97.5)):
    return {k: [float(np.percentile(boots[:, j], q[0])), float(np.percentile(boots[:, j], q[1]))]
            for j, k in enumerate(KEYS)}


def paired_delta(b_var, b_base) -> dict:
    """Variant minus baseline on the same resamples: delta CI and whether it excludes 0."""
    d = np.asarray(b_var) - np.asarray(b_base)
    out = {}
    for j, k in enumerate(KEYS):
        lo, hi = np.percentile(d[:, j], [2.5, 97.5])
        out[k] = {"mean": float(d[:, j].mean()), "ci95": [float(lo), float(hi)], "excludes_0": bool(lo > 0 or hi < 0)}
    return out


def inside(point: dict, base_ci: dict) -> dict:
    """Is each variant point estimate inside the baseline's own 95 % interval? (the 'within the
    CI of fp32' acceptance test)."""
    return {k: bool(base_ci[k][0] <= point[k] <= base_ci[k][1]) for k in KEYS}


# ----------------------------------------------------------------------------- owner trials
def owner_draws(ok, sess, seed: int, n_draws: int, n_enrol: int, pool_min: int | None = None):
    """Enrolment draws: a random capture session, then ``n_enrol`` of its eligible clips (random
    order, so prefixes are nested: the first 3 of a draw of 8 are a valid 3-clip enrolment).

    With ``n_enrol=3, pool_min=3`` this reproduces the bake-off's draws exactly (same generator,
    same call sequence as ``cmd_eval.pooled``).
    """
    pool_min = n_enrol if pool_min is None else pool_min
    sess_ids = [s for s in np.unique(sess[ok]) if (ok & (sess == s)).sum() >= pool_min]
    if not sess_ids:
        raise ValueError(f"no session has {pool_min} eligible clips")
    r2 = np.random.default_rng(seed + 1)
    draws = []
    for _ in range(n_draws):
        s = sess_ids[r2.integers(len(sess_ids))]
        draws.append(r2.choice(np.where(ok & (sess == s))[0], n_enrol, replace=False))
    return draws


def owner_scores(Ec, Ei, cdur, csess, core_mask, imp_ff, imp_cl, draws, scorer: Scorer, *,
                 n_use: int | None = None, enrol: dict | None = None, enrol_embs=None):
    """Score all trials for one configuration.

    Returns ``G`` (draws, genuine core clips; NaN for the enrolment session itself), ``If``
    (draws, far-field impostors), ``Ic`` (draws, clean LibriSpeech impostors) and ``gcols``.
    ``enrol_embs`` overrides the enrolment-clip embeddings (e.g. contaminated enrolments).
    """
    enrol = enrol or {"method": "mean"}
    gcols = np.where(core_mask)[0]
    Xg, Xf, Xc = Ec[gcols], Ei[imp_ff], Ei[imp_cl]
    sg, sf, sc = scorer.stats(Xg), scorer.stats(Xf), scorer.stats(Xc)
    cents = []
    for d, en in enumerate(draws):
        en = en[:n_use] if n_use else en
        E = Ec[en] if enrol_embs is None else enrol_embs[d]
        cents.append(enrol_centroid(E, cdur[en], **enrol))
    A = np.stack(cents)
    sa = scorer.stats(A)
    G = scorer.score(A, sa, Xg, sg)
    for d, en in enumerate(draws):
        G[d, csess[gcols] == csess[en[0]]] = np.nan  # leave the enrolment session out
    return {"G": G, "If": scorer.score(A, sa, Xf, sf), "Ic": scorer.score(A, sa, Xc, sc), "gcols": gcols}


def owner_report(tr, cdur, imp_bucket, boots_idx=None, n_boot: int = 0, seed: int = 9) -> dict:
    """Headline + per-bucket + clean-impostor metrics (and bootstrap CI when ``n_boot``)."""
    G, If, Ic, gcols = tr["G"], tr["If"], tr["Ic"], tr["gcols"]
    out = {"all": metrics(G, If), "clean_impostors": metrics(G, Ic), "n_draws": int(G.shape[0]),
           "n_genuine_clips": int(len(gcols))}
    out["all"]["dprime"] = dprime(_flat(G), _flat(If))
    gb = np.where(cdur[gcols] <= 2.0, "s", np.where(cdur[gcols] <= 4.0, "m", "l"))
    for b in BUCKETS:
        g, i = G[:, gb == b], If[:, imp_bucket == b]
        if np.isfinite(g).sum() >= 20 and i.size >= 20:
            out[f"bucket_{b}"] = metrics(g, i)
    if n_boot:
        idx = boots_idx or boot_indices(G.shape[1], If.shape[1], n_boot, seed)
        out["_boots"] = boot_owner(G, If, idx)
        out["ci95"] = ci(out["_boots"])
    return out


# ----------------------------------------------------------------------------- public axis
def public_scores(Ep, p_idx, e_idx, spk, scorer: Scorer, *, n_enrol: int = 3, enrol: dict | None = None,
                  durs=None):
    """Labelled public axis: every combo of ``n_enrol`` of the speaker's enrolment clips.

    Returns S (combos, probes, speakers) and ``same`` (probes, speakers).
    ``spk`` = speaker id per row of Ep.  Combos for 4 clips / n=3: the 4 hold-outs (bake-off design).
    """
    from itertools import combinations

    enrol = enrol or {"method": "mean"}
    speakers = sorted(set(spk[e_idx]))
    by = {s: [i for i in e_idx if spk[i] == s] for s in speakers}
    n_clips = min(len(v) for v in by.values())
    combos = list(combinations(range(n_clips), n_enrol))
    X = Ep[p_idx]
    sx = scorer.stats(X)
    S = np.zeros((len(combos), len(p_idx), len(speakers)))
    for ci_, comb in enumerate(combos):
        A = np.stack([enrol_centroid(Ep[[by[s][j] for j in comb]],
                                     None if durs is None else durs[[by[s][j] for j in comb]], **enrol)
                      for s in speakers])
        S[ci_] = scorer.score(A, scorer.stats(A), X, sx).T  # (probes, speakers)
    same = spk[p_idx][:, None] == np.array(speakers)[None, :]
    return S, same


def public_report(S, same, bucket, n_boot: int = 0, seed: int = 11) -> dict:
    """Overall + per-length-bucket metrics; bootstrap over probe rows when ``n_boot``."""
    P = S.shape[1]
    nsp = S.shape[2]
    # genuine (combos, P); impostors (combos, P, nsp-1)
    Gm = np.stack([S[c][same] for c in range(S.shape[0])])
    Im = np.stack([S[c][~same].reshape(P, nsp - 1) for c in range(S.shape[0])])
    out = {"all": metrics(Gm, Im), "n_gen": int(Gm.size), "n_imp": int(Im.size)}
    for b in BUCKETS:
        m = bucket == b
        if m.sum() >= 10:
            out[f"bucket_{b}"] = metrics(Gm[:, m], Im[:, m, :])
    if n_boot:
        r = np.random.default_rng(seed)
        boots = np.zeros((n_boot, 3))
        for k in range(n_boot):
            ix = r.integers(0, P, P)
            q = det_point(Gm[:, ix].ravel(), Im[:, ix, :].ravel())
            boots[k] = [q[x] for x in KEYS]
        out["_boots"] = boots
        out["ci95"] = ci(boots)
    return out


# ----------------------------------------------------------------------------- length-dependent thresholds
def group_of(dur, edges) -> np.ndarray:
    """Index of the duration group: edges=[2.0] -> 0 for <=2 s, 1 for longer."""
    return np.searchsorted(np.asarray(edges, float), np.asarray(dur, float), side="left")


def length_threshold_cv(G, gdur, I, idur, ispk, edges, fars=(0.01, 0.001), seed: int = 5, n_rep: int = 20):
    """Cross-validated length-dependent thresholds.

    Impostor SPEAKERS are split into two folds at random (``n_rep`` times).  Thresholds are
    derived from one fold's impostors only - one global threshold, and one per duration group
    (``edges``) - and applied to the OTHER fold (and vice versa), so the realised FAR is not the
    in-sample FAR.  Genuine scores ``G`` (draws, clips; NaN = absent) have durations ``gdur``.
    Returns, per FAR target and scheme (global / length), the realised FAR and FRR overall and per
    duration group, averaged over folds and repeats.
    """
    G, I = np.asarray(G), np.asarray(I)
    gg, ig = group_of(gdur, edges), group_of(idur, edges)
    ng = len(edges) + 1
    fin = np.isfinite(G)
    spk_ids = np.unique(ispk)
    rr = np.random.default_rng(seed)
    acc = {(far, name): {"far": [], "frr": [], "far_g": [[] for _ in range(ng)], "frr_g": [[] for _ in range(ng)]}
           for far in fars for name in ("global", "length")}
    for _ in range(n_rep):
        perm = rr.permutation(spk_ids)
        folds = [np.isin(ispk, perm[: len(perm) // 2]), np.isin(ispk, perm[len(perm) // 2:])]
        for a, b in ((0, 1), (1, 0)):
            ia, ib = folds[a], folds[b]
            for far in fars:
                thr_global = np.quantile(_flat(I[:, ia]), 1.0 - far, method="higher")
                thr_len = np.array([
                    np.quantile(_flat(I[:, ia & (ig == g)]), 1.0 - far, method="higher")
                    if (ia & (ig == g)).sum() >= 20 else thr_global for g in range(ng)])
                for name, thr in (("global", np.full(ng, thr_global)), ("length", thr_len)):
                    d = acc[(far, name)]
                    miss = (G <= thr[gg][None, :]) & fin
                    d["frr"].append(float(miss.sum() / fin.sum()))
                    d["far"].append(float((I[:, ib] > thr[ig][ib][None, :]).mean()))
                    for g in range(ng):
                        mi = ib & (ig == g)
                        fg = fin[:, gg == g]
                        if fg.sum() and mi.sum():
                            d["frr_g"][g].append(float(((G[:, gg == g] <= thr[g]) & fg).sum() / fg.sum()))
                            d["far_g"][g].append(float((I[:, mi] > thr[g]).mean()))
    out = {}
    for far in fars:
        out[f"far{far}"] = {}
        for name in ("global", "length"):
            d = acc[(far, name)]
            out[f"far{far}"][name] = {
                "far": float(np.mean(d["far"])), "frr": float(np.mean(d["frr"])),
                "far_by_group": [float(np.mean(x)) if x else None for x in d["far_g"]],
                "frr_by_group": [float(np.mean(x)) if x else None for x in d["frr_g"]]}
    return out


# ----------------------------------------------------------------------------- adaptation replay (#2002)
def adapt_replay(anchor, clips, t, dur, *, alpha=0.05, min_score=0.85, min_anchor_cos=0.80,
                 drift_bound=0.92, min_gap_s=600.0, max_per_day=5, min_speech_s=2.0):
    """Replay the PR #2002 policy over a chronological stream of offered turns.

    A turn is applied when every guard passes: score to the CURRENT profile >= min_score, cosine
    to the ANCHOR >= min_anchor_cos, >= min_speech_s of speech, >= min_gap_s since the last update
    and fewer than max_per_day updates that UTC day, and the update would not leave the profile
    below ``drift_bound`` cosine of the anchor (refused, never clamped).
    Returns (final_profile, applied_mask, reasons dict).
    """
    anchor = l2(anchor)
    prof = anchor.copy()
    applied = np.zeros(len(clips), bool)
    reasons: dict = {}
    last_t = -1e18
    per_day: dict = {}

    def refuse(why):
        reasons[why] = reasons.get(why, 0) + 1

    for i, c in enumerate(l2(clips)):
        if dur[i] < min_speech_s:
            refuse("short_clip")
        elif float(c @ prof) < min_score:
            refuse("low_score")
        elif float(c @ anchor) < min_anchor_cos:
            refuse("off_anchor")
        elif t[i] - last_t < min_gap_s:
            refuse("rate_interval")
        elif per_day.get(int(t[i] // 86400), 0) >= max_per_day:
            refuse("rate_day")
        else:
            new = l2((1 - alpha) * prof + alpha * c)
            if float(new @ anchor) < drift_bound:
                refuse("drift_bound")
                continue
            prof = new
            applied[i] = True
            last_t = t[i]
            per_day[int(t[i] // 86400)] = per_day.get(int(t[i] // 86400), 0) + 1
    reasons["applied"] = int(applied.sum())
    return prof, applied, reasons


# ----------------------------------------------------------------------------- context (scratch dir)
class Ctx:
    """Embeddings + manifests from the scratch directory, split into [corpus | impostors | cohort]."""

    def __init__(self, scratch, corpus_index, core_path, base_tag):
        self.scratch = Path(scratch)
        self.idx = json.loads(Path(corpus_index).read_text())
        self.man = json.loads((self.scratch / "impostors" / "manifest.json").read_text())
        cm = self.scratch / "cohort" / "manifest.json"
        self.coh = json.loads(cm.read_text()) if cm.exists() else []
        self.n_c, self.n_i, self.n_h = len(self.idx["names"]), len(self.man), len(self.coh)
        self.cdur = np.array(self.idx["dur"])
        self.csess = np.array(self.idx["session"])
        self.cmtime = np.array(self.idx.get("mtime", np.zeros(self.n_c)))
        z = np.load(core_path, allow_pickle=False)
        self.cores = {"strict": z["strict"], "majority": z["majority"]}
        self.finite_all = z["finite_all"]
        g = lambda k: np.array([m[k] for m in self.man])  # noqa: E731
        self.src, self.var, self.role, self.bk, self.spk = g("source"), g("variant"), g("role"), g("bucket"), g("speaker")
        self.idur = g("dur")
        self.base_tag = base_tag
        self._cache: dict = {}
        self.imp_ff = np.where((self.role == "probe") & (self.var == "farfield"))[0]
        self.imp_cl = np.where((self.role == "probe") & (self.var == "clean") & (self.src == "libri"))[0]

    def raw(self, tag):
        if tag not in self._cache:
            z = np.load(self.scratch / f"emb_{tag}.npz", allow_pickle=False)
            self._cache[tag] = (np.asarray(z["emb"], dtype=np.float64), json.loads(str(z["meta"])), z)
        return self._cache[tag]

    def split(self, tag, center: bool = False, coh_variant: str = "farfield"):
        """(Ec, Ei, Eh, mu): L2-normalised blocks; ``center`` subtracts the cohort mean first."""
        E, _, _ = self.raw(tag)
        E = l2(np.nan_to_num(E, nan=0.0)) * np.isfinite(E).all(axis=1, keepdims=True)
        Ec, Ei, Eh = E[: self.n_c], E[self.n_c: self.n_c + self.n_i], E[self.n_c + self.n_i:]
        mu = None
        if center:
            hv = np.array([m["variant"] for m in self.coh])
            mu = Eh[hv == coh_variant].mean(axis=0)
            Ec, Ei, Eh = centered(Ec, mu), centered(Ei, mu), centered(Eh, mu)
        return Ec, Ei, Eh, mu

    def cohort(self, Eh, variant="farfield"):
        hv = np.array([m["variant"] for m in self.coh])
        sel = hv == variant if variant != "both" else np.ones(len(hv), bool)
        return Eh[sel]

    def public_idx(self, variant):
        e_idx = np.where((self.role == "enrol") & (self.src == "libri"))[0]
        p_idx = np.where((self.role == "probe") & (self.src == "libri") & (self.var == variant))[0]
        return e_idx, p_idx


def _row(name, rep, base_rep=None):
    a = rep["all"]
    ci_ = rep.get("ci95", {})
    f = lambda k: f"{100 * a[k]:.1f}% [{100 * ci_[k][0]:.1f}-{100 * ci_[k][1]:.1f}]" if k in ci_ else f"{100 * a[k]:.1f}%"  # noqa: E731
    return f"| {name} | {f('eer')} | {f('frr_far1')} | {f('frr_far01')} |"


# ----------------------------------------------------------------------------- experiments
def _design(ctx, a, core="strict", n_enrol=3, pool_min=None, n_draws=30):
    ok = ctx.cores[core] & (ctx.cdur >= 2.0)
    return owner_draws(ok, ctx.csess, a.seed if hasattr(a, "seed") else 7, n_draws, n_enrol, pool_min)


def exp_variant(ctx, a, kv):
    """Accuracy of an embedding variant (quantised model, cap, trim, ...) vs the baseline tag:
    owner strict + majority and public far-field, point estimates, CIs, paired deltas, 'inside
    fp32 CI' test.  --args tag=<variant tag>"""
    tag = kv["tag"]
    res = {"tag": tag, "base": ctx.base_tag}
    nb = a.boot
    for core in ("strict", "majority"):
        draws = _design(ctx, a, core)
        idx = boot_indices(int(ctx.cores[core].sum()), len(ctx.imp_ff), nb, 21)
        reps = {}
        for t in (ctx.base_tag, tag):
            Ec, Ei, _, _ = ctx.split(t)
            tr = owner_scores(Ec, Ei, ctx.cdur, ctx.csess, ctx.cores[core], ctx.imp_ff, ctx.imp_cl, draws, Scorer("raw"))
            reps[t] = owner_report(tr, ctx.cdur, ctx.bk[ctx.imp_ff], idx, nb)
        b, v = reps[ctx.base_tag], reps[tag]
        res[f"owner_{core}"] = {"base": _strip(b), "variant": _strip(v),
                                "delta": paired_delta(v["_boots"], b["_boots"]),
                                "inside_base_ci": inside(v["all"], b["ci95"])}
    e_idx, p_idx = ctx.public_idx("farfield")
    pr = {}
    for t in (ctx.base_tag, tag):
        _, Ei, _, _ = ctx.split(t)
        S, same = public_scores(Ei, p_idx, e_idx, ctx.spk, Scorer("raw"))
        pr[t] = public_report(S, same, ctx.bk[p_idx], nb)
    res["public"] = {"base": _strip(pr[ctx.base_tag]), "variant": _strip(pr[tag]),
                     "delta": paired_delta(pr[tag]["_boots"], pr[ctx.base_tag]["_boots"]),
                     "inside_base_ci": inside(pr[tag]["all"], pr[ctx.base_tag]["ci95"])}
    return res


def _strip(rep):
    return {k: v for k, v in rep.items() if k != "_boots"}


def _attach_deltas(full: dict, base_key: str) -> dict:
    """{key: stripped report + paired 'delta' vs full[base_key]} (same resamples => paired)."""
    out = {}
    for k, rep in full.items():
        out[k] = _strip(rep)
        if k != base_key:
            out[k]["delta"] = paired_delta(rep["_boots"], full[base_key]["_boots"])
    return out


def exp_enrol(ctx, a, kv):
    """Enrolment recipe: number of clips x averaging, plus a contaminated-enrolment arm.
    Nested draws: sessions with >= 8 eligible clips, 8 random clips each, prefixes of length n."""
    nb = a.boot
    core = kv.get("core", "strict")
    draws = _design(ctx, a, core, n_enrol=8, pool_min=8)
    Ec, Ei, _, _ = ctx.split(ctx.base_tag)
    idx = boot_indices(int(ctx.cores[core].sum()), len(ctx.imp_ff), nb, 31)
    full = {}
    recipes = {
        "mean": {"method": "mean"}, "wmean": {"method": "wmean", "wpow": 1.0},
        "wmean_sqrt": {"method": "wmean", "wpow": 0.5},
        "reject0.30": {"method": "reject", "tau": 0.30}, "reject0.40": {"method": "reject", "tau": 0.40},
        "trim1": {"method": "trim1"},
    }
    for n in (3, 5, 8):
        for name, rc in recipes.items():
            tr = owner_scores(Ec, Ei, ctx.cdur, ctx.csess, ctx.cores[core], ctx.imp_ff, ctx.imp_cl, draws,
                              Scorer("raw"), n_use=n, enrol=rc)
            full[f"n{n}/{name}"] = owner_report(tr, ctx.cdur, ctx.bk[ctx.imp_ff], idx, nb)
    rows = _attach_deltas(full, "n3/mean")
    # legacy design (any session with >= 3 eligible clips) for n=3: ties this experiment to the record
    leg = _design(ctx, a, core, n_enrol=3)
    tr = owner_scores(Ec, Ei, ctx.cdur, ctx.csess, ctx.cores[core], ctx.imp_ff, ctx.imp_cl, leg, Scorer("raw"))
    rows["legacy_n3/mean"] = _strip(owner_report(tr, ctx.cdur, ctx.bk[ctx.imp_ff], idx, nb))
    # contamination: enrol 5 clips where ONE (random position) is a public impostor clip = a stranger.
    # Same contaminated enrolments for every recipe, so the recipes are compared on identical trials.
    rr = np.random.default_rng(77)
    embs = []
    for en in draws:
        e5 = Ec[en[:5]].copy()
        e5[rr.integers(5)] = Ei[ctx.imp_ff[rr.integers(len(ctx.imp_ff))]]
        embs.append(e5)
    contf = {}
    for name, rc in recipes.items():
        tr = owner_scores(Ec, Ei, ctx.cdur, ctx.csess, ctx.cores[core], ctx.imp_ff, ctx.imp_cl, draws,
                          Scorer("raw"), n_use=5, enrol=rc, enrol_embs=embs)
        contf[f"n5_1bad/{name}"] = owner_report(tr, ctx.cdur, ctx.bk[ctx.imp_ff], idx, nb)
    # clean n=5 mean as the reference for "what contamination costs"
    tr = owner_scores(Ec, Ei, ctx.cdur, ctx.csess, ctx.cores[core], ctx.imp_ff, ctx.imp_cl, draws, Scorer("raw"), n_use=5)
    contf["n5_clean/mean"] = owner_report(tr, ctx.cdur, ctx.bk[ctx.imp_ff], idx, nb)
    cont = _attach_deltas(contf, "n5_clean/mean")
    # public axis (4 labelled far-field enrolment clips per speaker): n = 2, 3, 4
    e_idx, p_idx = ctx.public_idx("farfield")
    pub = {}
    for n in (2, 3, 4):
        for name in ("mean", "wmean"):
            durs = ctx.idur
            S, same = public_scores(Ei, p_idx, e_idx, ctx.spk, Scorer("raw"), n_enrol=n, enrol=recipes[name], durs=durs[: len(Ei)])
            pub[f"n{n}/{name}"] = _strip(public_report(S, same, ctx.bk[p_idx], nb))
    return {"owner": rows, "owner_contaminated": cont, "public": pub, "n_sessions_pool8": int(len(
        [s for s in np.unique(ctx.csess[ctx.cores[core] & (ctx.cdur >= 2)]) if (ctx.cores[core] & (ctx.cdur >= 2) & (ctx.csess == s)).sum() >= 8]))}


def exp_norm(ctx, a, kv):
    """Score normalisation against the PUBLIC dev-clean cohort: raw, cohort-mean centring, S-norm,
    AS-norm (top-k), Z/T-norm, and centring + AS-norm."""
    nb = a.boot
    core = kv.get("core", "strict")
    draws = _design(ctx, a, core)
    idx = boot_indices(int(ctx.cores[core].sum()), len(ctx.imp_ff), nb, 41)
    e_idx, p_idx = ctx.public_idx("farfield")
    fo, fp = {}, {}
    cfgs = [("raw", dict(mode="raw", center=False, cv="farfield"))]
    for cv in ("farfield", "clean"):
        cfgs += [(f"center[{cv}]", dict(mode="raw", center=True, cv=cv)), (f"snorm[{cv}]", dict(mode="snorm", center=False, cv=cv))]
        for k in (20, 50, 100, 200):
            cfgs.append((f"asnorm k={k}[{cv}]", dict(mode="asnorm", k=k, center=False, cv=cv)))
        cfgs += [(f"znorm[{cv}]", dict(mode="znorm", center=False, cv=cv)), (f"tnorm[{cv}]", dict(mode="tnorm", center=False, cv=cv)),
                 (f"center+asnorm k=50[{cv}]", dict(mode="asnorm", k=50, center=True, cv=cv))]
    if kv.get("quick"):  # unit tests: a representative subset
        cfgs = [x for x in cfgs if x[0] in ("raw", "center[farfield]", "asnorm k=50[farfield]")]
    for name, c in cfgs:
        Ec, Ei, Eh, _ = ctx.split(ctx.base_tag, center=c["center"], coh_variant=c["cv"])
        sc = Scorer(c["mode"], ctx.cohort(Eh, c["cv"]) if c["mode"] != "raw" else None, c.get("k"))
        tr = owner_scores(Ec, Ei, ctx.cdur, ctx.csess, ctx.cores[core], ctx.imp_ff, ctx.imp_cl, draws, sc)
        fo[name] = owner_report(tr, ctx.cdur, ctx.bk[ctx.imp_ff], idx, nb)
        S, same = public_scores(Ei, p_idx, e_idx, ctx.spk, sc)
        fp[name] = public_report(S, same, ctx.bk[p_idx], nb)
    return {"owner": _attach_deltas(fo, "raw"), "public": _attach_deltas(fp, "raw")}


def exp_lenthr(ctx, a, kv):
    """Length-dependent thresholds (<=2 s vs longer, and <=2/2-4/>4 s) at FAR 1 % / 0.1 %, derived
    on one half of the impostor speakers and applied to the other half (speaker-disjoint folds).
    Genuine clips: the owner strict core (30 draws).  Impostor 'speaker' = LibriSpeech speaker or
    Kokoro voice."""
    core = kv.get("core", "strict")
    draws = _design(ctx, a, core)
    Ec, Ei, _, _ = ctx.split(kv.get("tag", ctx.base_tag), center=bool(kv.get("center", 0)))
    tr = owner_scores(Ec, Ei, ctx.cdur, ctx.csess, ctx.cores[core], ctx.imp_ff, ctx.imp_cl, draws, Scorer("raw"))
    gdur = ctx.cdur[tr["gcols"]]
    # impostor DURATION = actual clip duration from the manifest
    idur = ctx.idur[ctx.imp_ff]
    ispk = ctx.spk[ctx.imp_ff]
    thr = {}
    gi = group_of(idur, [2.0])
    for far in (0.01, 0.001):  # in-sample thresholds on ALL impostor trials, for the record (CV numbers above are the honest ones)
        thr[f"far{far}"] = {"global": float(np.quantile(_flat(tr["If"]), 1 - far, method="higher")),
                           "le2s": float(np.quantile(_flat(tr["If"][:, gi == 0]), 1 - far, method="higher")),
                           "gt2s": float(np.quantile(_flat(tr["If"][:, gi == 1]), 1 - far, method="higher"))}
    dg, di = dist_summary(_flat(tr["G"])), dist_summary(_flat(tr["If"]))
    return {"thresholds_insample": thr, "dist_genuine": {k: round(float(v), 3) for k, v in dg.items()},
            "dist_impostor": {k: round(float(v), 3) for k, v in di.items()}, "edges_2s": length_threshold_cv(tr["G"], gdur, tr["If"], idur, ispk, [2.0]),
            "edges_2_4s": length_threshold_cv(tr["G"], gdur, tr["If"], idur, ispk, [2.0, 4.0]),
            "genuine_share_le2s": float((gdur <= 2.0).mean()),
            "impostor_share_le2s": float((idur <= 2.0).mean())}


def exp_adapt(ctx, a, kv):
    """Replay PR #2002's adaptation policy on ResNet34 profiles (see adapt_replay).  Guard scores
    are model-specific, so min_score / min_anchor_cos are set from the PUBLIC impostor distribution
    (the FAR-0.1 % score) instead of the Resemblyzer-scale 0.85 / 0.80 defaults."""
    core = kv.get("core", "strict")
    Ec, Ei, _, _ = ctx.split(kv.get("tag", ctx.base_tag), center=bool(kv.get("center", 0)))
    draws = _design(ctx, a, core)
    out = {"policy": {}, "arms": {}}
    # reference thresholds from the ANCHOR-only trials (fixed for every arm)
    tr0 = owner_scores(Ec, Ei, ctx.cdur, ctx.csess, ctx.cores[core], ctx.imp_ff, ctx.imp_cl, draws, Scorer("raw"))
    thr1, thr01 = metrics(tr0["G"], tr0["If"])["thr_far1"], metrics(tr0["G"], tr0["If"])["thr_far01"]
    out["policy"] = {"thr_far1": thr1, "thr_far01": thr01}
    gcols = np.where(ctx.cores[core])[0]
    order = gcols[np.argsort(ctx.cmtime[gcols])]
    arms = {"resemblyzer_scale_defaults": dict(min_score=0.85, min_anchor_cos=0.80),
            "far1_gate": dict(min_score=thr1, min_anchor_cos=thr1),
            "far01_gate": dict(min_score=thr01, min_anchor_cos=thr01),
            "far01_gate+hi_anchor": dict(min_score=thr01, min_anchor_cos=max(thr01, kv.get("anchor_cos", 0.45)))}
    imp_cols = ctx.imp_ff
    rr = np.random.default_rng(5)
    for arm, kw in arms.items():
        res = {"applied": [], "genuine_before": [], "genuine_after": [], "frr1_before": [], "frr1_after": [],
               "frr01_before": [], "frr01_after": [], "far1_after": [], "far01_after": [], "imp_mean_before": [],
               "imp_mean_after": [], "anchor_cos": [], "poison_admitted": [], "lookalike_before": [], "lookalike_after": [],
               "lookalike_cos": []}
        for en in draws:
            anchor = enrol_centroid(Ec[en[:3]], None, "mean")
            t_enrol = ctx.cmtime[en].max()
            stream = [i for i in order if ctx.cmtime[i] > t_enrol and ctx.csess[i] != ctx.csess[en[0]]]
            if len(stream) < 40:
                continue
            cut = int(0.7 * len(stream))
            train, test = stream[:cut], stream[cut:]
            prof, applied, reasons = adapt_replay(anchor, Ec[train], ctx.cmtime[train], ctx.cdur[train], **kw)
            gb = Ec[test] @ anchor
            ga = Ec[test] @ prof
            ib = Ei[imp_cols] @ anchor
            ia = Ei[imp_cols] @ prof
            res["applied"].append(int(applied.sum()))
            res["genuine_before"].append(float(gb.mean()))
            res["genuine_after"].append(float(ga.mean()))
            res["frr1_before"].append(float((gb <= thr1).mean()))
            res["frr1_after"].append(float((ga <= thr1).mean()))
            res["frr01_before"].append(float((gb <= thr01).mean()))
            res["frr01_after"].append(float((ga <= thr01).mean()))
            res["far1_after"].append(float((ia > thr1).mean()))
            res["far01_after"].append(float((ia > thr01).mean()))
            res["imp_mean_before"].append(float(ib.mean()))
            res["imp_mean_after"].append(float(ia.mean()))
            res["anchor_cos"].append(float(prof @ anchor))
            # poisoning: the single most owner-like public impostor offered every day for 73 days
            k = int(np.argmax(ib))
            look = Ei[imp_cols[k]]
            n_off = 73 * 5
            tt = np.arange(n_off) * 17280.0  # five offers a day, evenly spaced
            clips = np.repeat(look[None], n_off, axis=0)
            pprof, papplied, _ = adapt_replay(anchor, clips, tt, np.full(n_off, 4.0), **kw)
            res["poison_admitted"].append(int(papplied.sum()))
            res["lookalike_before"].append(float(look @ anchor))
            res["lookalike_after"].append(float(look @ pprof))
            res["lookalike_cos"].append(float(pprof @ anchor))
        m = lambda k: float(np.mean(res[k])) if res[k] else None  # noqa: E731
        out["arms"][arm] = {"n_draws_used": len(res["applied"]), **{k: m(k) for k in res}, "kw": kw}
    return out


def exp_final(ctx, a, kv):
    """The recommended pipeline vs the bake-off baseline, on the bake-off's own design.

    final = ``tag`` embeddings (+ cohort-mean centring when ``center=1``) + mean enrolment with outlier
    rejection (tau 0.30).  Legacy design (3-clip enrolment, 30 draws) is compared PAIRED with the fp32
    raw baseline - the numbers the record's section 3 quotes; the nested design adds 5 and 8 clips."""
    tag, center = kv.get("tag", ctx.base_tag), bool(kv.get("center", 1))
    nb = a.boot
    out = {}
    rej = {"method": "reject", "tau": 0.30}
    for core in ("strict", "majority"):
        draws = _design(ctx, a, core)
        idx = boot_indices(int(ctx.cores[core].sum()), len(ctx.imp_ff), nb, 51)
        full = {}
        Ec, Ei, _, _ = ctx.split(ctx.base_tag)
        full["fp32_raw_n3"] = owner_report(owner_scores(Ec, Ei, ctx.cdur, ctx.csess, ctx.cores[core], ctx.imp_ff, ctx.imp_cl, draws, Scorer("raw")),
                                           ctx.cdur, ctx.bk[ctx.imp_ff], idx, nb)
        Ec, Ei, _, _ = ctx.split(tag, center=center)
        full["final_n3"] = owner_report(owner_scores(Ec, Ei, ctx.cdur, ctx.csess, ctx.cores[core], ctx.imp_ff, ctx.imp_cl, draws, Scorer("raw"), enrol=rej),
                                        ctx.cdur, ctx.bk[ctx.imp_ff], idx, nb)
        out[f"owner_{core}"] = _attach_deltas(full, "fp32_raw_n3")
        out[f"owner_{core}"]["inside_base_ci"] = inside(full["final_n3"]["all"], full["fp32_raw_n3"]["ci95"])
    ok = ctx.cores["strict"] & (ctx.cdur >= 2.0)
    draws8 = owner_draws(ok, ctx.csess, a.seed, 30, 8, 8)
    idx = boot_indices(int(ctx.cores["strict"].sum()), len(ctx.imp_ff), nb, 52)
    nest = {}
    for n in (3, 5, 8):
        nest[f"nested_n{n}"] = owner_report(owner_scores(Ec, Ei, ctx.cdur, ctx.csess, ctx.cores["strict"], ctx.imp_ff, ctx.imp_cl, draws8,
                                                         Scorer("raw"), n_use=n, enrol=rej), ctx.cdur, ctx.bk[ctx.imp_ff], idx, nb)
    out["owner_strict_nested"] = _attach_deltas(nest, "nested_n3")
    e_idx, p_idx = ctx.public_idx("farfield")
    _, Ei0, _, _ = ctx.split(ctx.base_tag)
    pub = {}
    S, same = public_scores(Ei0, p_idx, e_idx, ctx.spk, Scorer("raw"))
    pub["fp32_raw_n3"] = public_report(S, same, ctx.bk[p_idx], nb)
    for n in (3, 4):
        S, same = public_scores(Ei, p_idx, e_idx, ctx.spk, Scorer("raw"), n_enrol=n, enrol=rej)
        pub[f"final_n{n}"] = public_report(S, same, ctx.bk[p_idx], nb)
    out["public"] = _attach_deltas(pub, "fp32_raw_n3")
    out["public"]["inside_base_ci"] = inside(pub["final_n3"]["all"], pub["fp32_raw_n3"]["ci95"])
    return out


def exp_centersens(ctx, a, kv):
    """How sensitive is cohort-mean centring to WHICH public clips make the mean?  Far-field dev-clean
    cohort: all 240 clips, each half of the speakers, and 24 random clips (3 draws)."""
    tag = kv.get("tag", ctx.base_tag)
    nb = a.boot
    core = "strict"
    draws = _design(ctx, a, core)
    idx = boot_indices(int(ctx.cores[core].sum()), len(ctx.imp_ff), nb, 61)
    e_idx, p_idx = ctx.public_idx("farfield")
    Ec0, Ei0, Eh0, _ = ctx.split(tag)
    hv = np.array([m["variant"] for m in ctx.coh])
    hs = np.array([m["speaker"] for m in ctx.coh])
    ff = hv == "farfield"
    spk_ids = sorted(set(hs[ff]))
    rr = np.random.default_rng(3)
    subsets = {"none": None, "all_240": ff,
               "speakers_A": ff & np.isin(hs, spk_ids[::2]), "speakers_B": ff & np.isin(hs, spk_ids[1::2])}
    for k in range(3):
        m = np.zeros(len(ff), bool)
        m[rr.choice(np.where(ff)[0], min(24, int(ff.sum())), replace=False)] = True
        subsets[f"random_24_{k}"] = m
    full_o, full_p = {}, {}
    for name, m in subsets.items():
        if m is None:
            Ec, Ei = Ec0, Ei0
        else:
            mu = Eh0[m].mean(axis=0)
            Ec, Ei = centered(Ec0, mu), centered(Ei0, mu)
        full_o[name] = owner_report(owner_scores(Ec, Ei, ctx.cdur, ctx.csess, ctx.cores[core], ctx.imp_ff, ctx.imp_cl, draws, Scorer("raw")),
                                    ctx.cdur, ctx.bk[ctx.imp_ff], idx, nb)
        S, same = public_scores(Ei, p_idx, e_idx, ctx.spk, Scorer("raw"))
        full_p[name] = public_report(S, same, ctx.bk[p_idx], nb)
    return {"owner": _attach_deltas(full_o, "none"), "public": _attach_deltas(full_p, "none")}


EXPERIMENTS = {"centersens": exp_centersens, "final": exp_final, "variant": exp_variant, "enrol": exp_enrol, "norm": exp_norm, "lenthr": exp_lenthr, "adapt": exp_adapt}


def baseline_table(ctx, a, kv):
    """Reproduce the bake-off rows for the baseline tag (cross-check against the record)."""
    nb = a.boot
    out = {}
    for core in ("strict", "majority"):
        draws = _design(ctx, a, core)
        Ec, Ei, _, _ = ctx.split(ctx.base_tag)
        tr = owner_scores(Ec, Ei, ctx.cdur, ctx.csess, ctx.cores[core], ctx.imp_ff, ctx.imp_cl, draws, Scorer("raw"))
        rep = _strip(owner_report(tr, ctx.cdur, ctx.bk[ctx.imp_ff], None, nb))
        rep["dist_gen"], rep["dist_imp"] = dist_summary(_flat(tr["G"])), dist_summary(_flat(tr["If"]))
        out[f"owner_{core}"] = rep
    e_idx, p_idx = ctx.public_idx("farfield")
    _, Ei, _, _ = ctx.split(ctx.base_tag)
    S, same = public_scores(Ei, p_idx, e_idx, ctx.spk, Scorer("raw"))
    out["public_farfield"] = _strip(public_report(S, same, ctx.bk[p_idx], nb))
    e_idx, p_idx = ctx.public_idx("clean")
    if len(p_idx):  # clean probes against far-field enrolment, as in the bake-off's 'clean probes' column
        e_ff, _ = ctx.public_idx("farfield")
        S, same = public_scores(Ei, p_idx, e_ff, ctx.spk, Scorer("raw"))
        out["public_cleanprobes"] = _strip(public_report(S, same, ctx.bk[p_idx], nb))
    return out


EXPERIMENTS["baseline"] = baseline_table


def flatten_metrics(res, prefix: str = ""):
    """Yield (path, metrics-dict) for every nested dict that carries an ``eer``, for compact printing."""
    if isinstance(res, dict):
        if isinstance(res.get("all"), dict) and "eer" in res["all"]:
            yield f"{prefix}/all", res  # the parent carries the CI
            for k, v in res.items():
                if k.startswith("bucket_") and isinstance(v, dict):
                    yield f"{prefix}/{k}", v
            return
        if "eer" in res and "frr_far1" in res:
            yield prefix, res
            return
        for k, v in res.items():
            if k in ("ci95", "delta", "policy", "inside_base_ci"):  # not metric blocks themselves
                continue
            yield from flatten_metrics(v, f"{prefix}/{k}" if prefix else str(k))


def show(res, brief: bool = False) -> str:
    """One line per metric block: EER / FRR@1% / FRR@0.1% (+ 95 % CIs when present)."""
    lines = []
    for path, m in flatten_metrics(res):
        if brief and "/bucket_" in path:
            continue
        a = m["all"] if "all" in m else m
        c = m.get("ci95") or {}
        f = lambda k: (f"{100 * a[k]:5.2f}" + (f" [{100 * c[k][0]:.2f}-{100 * c[k][1]:.2f}]" if k in c else ""))  # noqa: E731
        lines.append(f"{path:58s} EER {f('eer')}  FRR1 {f('frr_far1')}  FRR.1 {f('frr_far01')}")
    for path, d in flatten_deltas(res):
        lines.append(f"{path:58s} " + "  ".join(
            f"d{k.split('_')[-1] if k != 'eer' else 'EER'} {100 * v['mean']:+.2f} [{100 * v['ci95'][0]:+.2f},{100 * v['ci95'][1]:+.2f}]"
            f"{'*' if v['excludes_0'] else ''}" for k, v in d.items()))
    for path, d in flatten_inside(res):
        lines.append(f"{path:58s} point inside fp32 95% CI: " + "  ".join(f"{k}={'yes' if v else 'NO'}" for k, v in d.items()))
    return "\n".join(lines)


def flatten_deltas(res, prefix: str = ""):
    if isinstance(res, dict):
        for k, v in res.items():
            if k == "delta" and isinstance(v, dict) and "eer" in v:
                yield f"{prefix}/delta" if prefix else "delta", v
            else:
                yield from flatten_deltas(v, f"{prefix}/{k}" if prefix else str(k))


def flatten_inside(res, prefix: str = ""):
    if isinstance(res, dict):
        for k, v in res.items():
            if k == "inside_base_ci":
                yield f"{prefix}/inside_base_ci" if prefix else k, v
            else:
                yield from flatten_inside(v, f"{prefix}/{k}" if prefix else str(k))


def show_other(res) -> str:
    """Compact text for the experiments whose result is not a grid of EER blocks."""
    lines = []
    if "thresholds_insample" in res:
        lines.append(f"in-sample thresholds: {res['thresholds_insample']}")
        lines.append(f"genuine: {res['dist_genuine']}")
        lines.append(f"impostor: {res['dist_impostor']}")
    for e in ("edges_2s", "edges_2_4s"):
        for far, d in (res.get(e) or {}).items():
            for n, v in d.items():
                lines.append(f"{e:11s} {far:9s} {n:6s} FAR {100 * v['far']:.3f}%  FRR {100 * v['frr']:.2f}%   "
                             f"FAR/group {[round(100 * x, 3) for x in v['far_by_group']]}  FRR/group {[round(100 * x, 2) for x in v['frr_by_group']]}")
    if "arms" in res:
        lines.append(f"anchor-only thresholds: {res['policy']}")
        for arm, v in res["arms"].items():
            lines.append(f"{arm:24s} applied {v['applied']:.1f}  genuine {v['genuine_before']:.3f}->{v['genuine_after']:.3f}  "
                         f"FRR@FAR1% {100 * v['frr1_before']:.2f}->{100 * v['frr1_after']:.2f}%  FRR@FAR0.1% {100 * v['frr01_before']:.2f}->{100 * v['frr01_after']:.2f}%  "
                         f"FAR1%-thr {100 * v['far1_after']:.2f}%  FAR0.1%-thr {100 * v['far01_after']:.3f}%  imp mean {v['imp_mean_before']:.3f}->{v['imp_mean_after']:.3f}  "
                         f"cos(anchor) {v['anchor_cos']:.3f}  | poison admitted {v['poison_admitted']:.1f}, lookalike {v['lookalike_before']:.3f}->{v['lookalike_after']:.3f} (cos {v['lookalike_cos']:.3f})  gates {({k: round(x, 3) for k, x in v['kw'].items()})}")
    return "\n".join(lines)


def run(a) -> None:
    kv = parse_kv(a.args)
    if a.experiment == "show":  # re-print a saved result:  --experiment show --args file=<json>
        print(show(json.loads(Path(kv["file"]).read_text()), bool(kv.get("brief", 1))))
        return
    ctx = Ctx(a.scratch, a.corpus_index, a.core, a.base)
    if a.experiment not in EXPERIMENTS:
        sys.exit(f"unknown experiment {a.experiment!r}; one of {sorted(EXPERIMENTS)}")
    res = EXPERIMENTS[a.experiment](ctx, a, kv)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, default=float))
    print(show(res, bool(kv.get("brief", 1))) or show_other(res) or json.dumps(res, indent=1, default=float))
