#!/usr/bin/env python3
"""Speaker-gate rebuild, step 1 — score the embedded corpus against the four targets.

Runs ON THE PI over the output of ``speaker_shadow_embed.py`` (``embeddings.npz`` +
``manifest.json`` in the shadow dir). Pure numpy; the embeddings never leave the Pi.
It writes ``report.json`` and ``report.md`` containing AGGREGATES ONLY (counts, rates,
quantiles, thresholds, timings) — never a clip id, a name, a transcript or a vector —
and refuses to write them if any manifest clip id appears in the serialised report.

There are no speaker labels in the corpus (record §5.1). The evaluation is therefore
built from what is known and its limits are stated in the report:

* **Impostors, labelled**: the ``tv`` group (the quarantined TV false-wakes) — the only
  independently identified non-owner audio. Too few clips to resolve a 1 % FAR on its own.
* **Impostors, unsupervised**: cluster B of a 2-means over the ``top`` clips (record §2.3:
  far-field / ambient / TV; mostly other people, but possibly the owner far-field). It is
  derived from the same embeddings it is then scored in, so it can overstate separation;
  it is reported as ``pseudo-labelled`` and the labelled TV numbers are shown beside it.
* **Owner**: cluster A, scored with leave-sessions-out centroids (never the enrolment
  session — Omi's "same-session trap"): clips are grouped into capture sessions by
  mtime gaps, sessions are dealt into K contiguous folds, and each fold is scored against
  a centroid built from the OTHER folds.

Controls (``verify your instruments``): a label shuffle must give an EER near 50 %, and the
same A/B + leave-sessions-out pipeline run on random unit vectors gives the harness's
selection-bias floor (a pseudo-labelled result must sit under it). ``--compare-with`` adds the
check that matters most: a second, independent embedder's labels (cross-label EER) and whether
one space's cluster A is coherent in the other (homogeneity) — if the cut does not replicate,
it is a property of the model that made it, not of the audio.

Environment (scripts only — no live flag reads these)
------------------------------------------------------
  SPEAKER_SHADOW_DIR       shadow dir (default ~/.zoe-voice/speaker-shadow)
  SPEAKER_SHADOW_FOLDS     leave-sessions-out folds (default 5)
  SPEAKER_SHADOW_SESSION_GAP_S  mtime gap that starts a new session (default 1800)
  SPEAKER_SHADOW_SEED      RNG seed for k-means / controls / bootstrap (default 20261005)
  SPEAKER_SHADOW_BOOT      bootstrap resamples for the 95 % intervals (default 300)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

TARGETS = {
    "separation": 0.20,        # owner median minus runner-up median (cosine)
    "frr_at_far1": 0.10,       # false rejects must be below this at the FAR=1 % threshold
    "far_at_frr10": 0.01,      # false accepts must be below this at the FRR=10 % threshold
    "tv_accepts": 0,           # zero accepts of the TV clips
    "latency_p50_ms": 200.0,   # per turn, on the Pi
}
BUCKETS: List[Tuple[str, float, float]] = [("<2s", 0.0, 2.0), ("2-5s", 2.0, 5.0), (">5s", 5.0, 1e9)]


# --------------------------------------------------------------------------- basics

def l2norm(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.maximum(n, 1e-12)


def quantiles(x: Sequence[float], qs: Sequence[float] = (5, 25, 50, 75, 95)) -> Dict[str, float]:
    a = np.asarray(x, dtype=np.float64)
    if a.size == 0:
        return {f"p{int(q)}": float("nan") for q in qs}
    return {f"p{int(q)}": round(float(np.percentile(a, q)), 4) for q in qs}


def far_frr(target: np.ndarray, nontarget: np.ndarray, t: float) -> Tuple[float, float]:
    """(FAR, FRR) with accept := score >= t."""
    far = float(np.mean(nontarget >= t)) if nontarget.size else float("nan")
    frr = float(np.mean(target < t)) if target.size else float("nan")
    return far, frr


def _candidates(target: np.ndarray, nontarget: np.ndarray) -> np.ndarray:
    allv = np.unique(np.concatenate([target, nontarget]))
    # thresholds midway between sorted scores, plus the extremes (accept-all / reject-all)
    mids = (allv[:-1] + allv[1:]) / 2.0 if allv.size > 1 else allv
    return np.concatenate([[allv[0] - 1e-6], mids, [allv[-1] + 1e-6]])


def eer(target: np.ndarray, nontarget: np.ndarray) -> Tuple[float, float]:
    """(EER, threshold at EER). NaN when either side is empty."""
    if target.size == 0 or nontarget.size == 0:
        return float("nan"), float("nan")
    ts = _candidates(target, nontarget)
    ts_sorted_t, ts_sorted_n = np.sort(target), np.sort(nontarget)
    frr = np.searchsorted(ts_sorted_t, ts, side="left") / target.size            # target < t
    far = 1.0 - np.searchsorted(ts_sorted_n, ts, side="left") / nontarget.size   # nontarget >= t
    i = int(np.argmin(np.abs(far - frr)))
    return float((far[i] + frr[i]) / 2.0), float(ts[i])


def threshold_for_far(target: np.ndarray, nontarget: np.ndarray, far_max: float) -> float:
    """Smallest candidate threshold whose FAR <= far_max (lowest FRR within the FAR budget)."""
    ts = _candidates(target, nontarget)
    far = 1.0 - np.searchsorted(np.sort(nontarget), ts, side="left") / nontarget.size
    ok = np.nonzero(far <= far_max)[0]
    return float(ts[ok[0]]) if ok.size else float(ts[-1])


def threshold_for_frr(target: np.ndarray, frr_max: float) -> float:
    """Largest threshold whose FRR <= frr_max: accepts the top (1 - frr_max) of owner scores."""
    s = np.sort(target)
    k = int(np.floor(frr_max * s.size))        # at most k owner clips may be rejected
    return float(s[k]) if k < s.size else float(s[-1])


def bootstrap_ci(fn, target: np.ndarray, nontarget: np.ndarray, n: int, rng: np.random.Generator):
    if n <= 0 or target.size == 0 or nontarget.size == 0:
        return [float("nan"), float("nan")]
    vals = []
    for _ in range(n):
        t = target[rng.integers(0, target.size, target.size)]
        u = nontarget[rng.integers(0, nontarget.size, nontarget.size)]
        vals.append(fn(t, u))
    return [round(float(np.percentile(vals, 2.5)), 4), round(float(np.percentile(vals, 97.5)), 4)]


# --------------------------------------------------------------------------- clustering

def spherical_kmeans(x: np.ndarray, k: int, rng: np.random.Generator, restarts: int = 12, iters: int = 60):
    """k-means on unit vectors by cosine; k-means++ seeding; best of *restarts*. -> (labels, centroids)."""
    n = x.shape[0]
    best = (-np.inf, None, None)
    for _ in range(restarts):
        c = [x[rng.integers(0, n)]]
        for _j in range(1, k):
            d = 1.0 - np.max(x @ np.stack(c).T, axis=1)
            p = np.maximum(d, 0) ** 2
            p = p / p.sum() if p.sum() > 0 else np.full(n, 1.0 / n)
            c.append(x[rng.choice(n, p=p)])
        cent = l2norm(np.stack(c))
        labels = np.argmax(x @ cent.T, axis=1)
        for _it in range(iters):
            new = np.stack([cent[j] if not np.any(labels == j) else x[labels == j].mean(axis=0) for j in range(k)])
            new = l2norm(new)
            nl = np.argmax(x @ new.T, axis=1)
            cent = new
            if np.array_equal(nl, labels):
                break
            labels = nl
        score = float(np.sum(np.max(x @ cent.T, axis=1)))
        if score > best[0]:
            best = (score, labels.copy(), cent.copy())
    return best[1], best[2]


def silhouette_cosine(x: np.ndarray, labels: np.ndarray) -> float:
    """Mean silhouette with cosine distance (1 - cos); 0.0 for a single cluster."""
    ks = np.unique(labels)
    if ks.size < 2 or x.shape[0] < 3:
        return 0.0
    d = 1.0 - (x @ x.T)
    np.fill_diagonal(d, 0.0)
    s = np.zeros(x.shape[0])
    for i in range(x.shape[0]):
        own = labels == labels[i]
        n_own = own.sum()
        a = d[i, own].sum() / (n_own - 1) if n_own > 1 else 0.0
        b = min(d[i, labels == j].mean() for j in ks if j != labels[i])
        s[i] = 0.0 if n_own <= 1 else (b - a) / max(a, b, 1e-12)
    return float(s.mean())


# --------------------------------------------------------------------------- sessions / folds

def session_folds(mtimes: np.ndarray, k: int, gap_s: float) -> Tuple[np.ndarray, int]:
    """Assign each clip to one of *k* contiguous folds built from whole capture sessions.

    A new session starts where consecutive (sorted) mtimes are > gap_s apart. Sessions stay
    intact, so a held-out fold never shares a session with its enrolment centroid. Falls back
    to contiguous time blocks of clips if there are fewer sessions than folds.
    """
    n = mtimes.size
    order = np.argsort(mtimes, kind="stable")
    gaps = np.diff(mtimes[order])
    sess_sorted = np.concatenate([[0], np.cumsum(gaps > gap_s)])
    n_sessions = int(sess_sorted[-1]) + 1
    fold = np.zeros(n, dtype=int)
    if n_sessions >= k:
        # deal contiguous sessions into k folds of roughly equal clip counts
        target = n / k
        cur, filled = 0, 0
        for s in range(n_sessions):
            idx = order[sess_sorted == s]
            if filled >= target * (cur + 1) and cur < k - 1:
                cur += 1
            fold[idx] = cur
            filled += idx.size
    else:
        fold[order] = np.minimum((np.arange(n) * k) // max(n, 1), k - 1)
    return fold, n_sessions


def centroid(x: np.ndarray) -> np.ndarray:
    return l2norm(x.mean(axis=0))


def owner_scores_loso(emb: np.ndarray, fold: np.ndarray, k: int, pick=None) -> Tuple[np.ndarray, np.ndarray]:
    """Cosine of each owner clip to the centroid of the OTHER folds. -> (scores, fold-of-score)."""
    out = np.full(emb.shape[0], np.nan)
    for f in range(k):
        test = fold == f
        train = ~test
        if pick is not None:
            train = train & pick
        if test.sum() == 0 or train.sum() == 0:
            continue
        out[test] = emb[test] @ centroid(emb[train])
    ok = ~np.isnan(out)
    return out[ok], ok


def impostor_scores(imp_emb: np.ndarray, emb: np.ndarray, fold: np.ndarray, k: int) -> np.ndarray:
    """Each impostor's score = mean over fold-centroids (one value per impostor, honest n)."""
    if imp_emb.shape[0] == 0:
        return np.zeros(0)
    cents = [centroid(emb[fold != f]) for f in range(k) if np.any(fold != f)]
    return (imp_emb @ np.stack(cents).T).mean(axis=1)


# --------------------------------------------------------------------------- the evaluation

def _noise_pipeline_eer(shape, groups, mtimes, folds: int, gap_s: float, seed: int) -> float:
    """EER of the full A/B + leave-sessions-out pipeline run on random unit vectors."""
    rng = np.random.default_rng(seed + 99)
    x = l2norm(rng.standard_normal(shape))
    groups = np.asarray(groups)
    top, tv = np.nonzero(groups == "top")[0], np.nonzero(groups == "tv")[0]
    if top.size < folds * 4:
        return float("nan")
    lab, _ = spherical_kmeans(x[top], 2, np.random.default_rng(seed + 3), restarts=4)
    a = top[lab == np.argmax(np.bincount(lab))]
    b = top[lab != np.argmax(np.bincount(lab))]
    fold, _n = session_folds(np.asarray(mtimes, dtype=np.float64)[a], folds, gap_s)
    own, _ok = owner_scores_loso(x[a], fold, folds)
    pool = np.concatenate([impostor_scores(x[tv], x[a], fold, folds), impostor_scores(x[b], x[a], fold, folds)])
    return eer(own, pool)[0]


def _bucket_of(duration: float) -> str:
    for name, lo, hi in BUCKETS:
        if lo <= duration < hi:
            return name
    return BUCKETS[-1][0]


def evaluate(
    emb: np.ndarray,
    groups: Sequence[str],
    durations: Sequence[float],
    mtimes: Sequence[float],
    rms_dbfs: Sequence[float],
    latency: Dict[str, Any],
    *,
    folds: int = 5,
    session_gap_s: float = 1800.0,
    seed: int = 20261005,
    boot: int = 300,
    fixed_thresholds: Sequence[float] = (),
) -> Dict[str, Any]:
    """Compute the full aggregate report from embeddings + per-clip metadata (arrays aligned)."""
    rng = np.random.default_rng(seed)
    emb = l2norm(np.asarray(emb, dtype=np.float64))
    groups = np.asarray(groups)
    dur = np.asarray(durations, dtype=np.float64)
    mt = np.asarray(mtimes, dtype=np.float64)
    rms = np.asarray(rms_dbfs, dtype=np.float64)
    top = groups == "top"
    tv = groups == "tv"
    rep: Dict[str, Any] = {"schema": 1, "seed": seed, "targets": TARGETS}
    rep["counts"] = {"embedded": int(emb.shape[0]), "top": int(top.sum()), "tv": int(tv.sum()),
                     "nonspeech": int((groups == "nonspeech").sum())}

    # ---- cluster structure (the two-acoustic-cluster trap) --------------------------
    x_top = emb[top]
    cl: Dict[str, Any] = {}
    sil = {}
    labels_by_k = {}
    for kk in (2, 3, 4):
        if x_top.shape[0] > kk + 1:
            lab, _ = spherical_kmeans(x_top, kk, np.random.default_rng(seed + kk))
            labels_by_k[kk] = lab
            sil[str(kk)] = round(silhouette_cosine(x_top, lab), 4)
    cl["silhouette_by_k"] = sil
    cl["best_k_by_silhouette"] = int(max(sil, key=lambda s: sil[s])) if sil else None
    owner_mask_top = np.zeros(x_top.shape[0], dtype=bool)
    if 2 in labels_by_k:
        lab2 = labels_by_k[2]
        sizes = [int((lab2 == j).sum()) for j in (0, 1)]
        a_id = int(np.argmax(sizes))
        owner_mask_top = lab2 == a_id
        cl["k2_sizes"] = {"A": sizes[a_id], "B": sizes[1 - a_id]}
        cl["k2_A_fraction"] = round(sizes[a_id] / max(sum(sizes), 1), 4)
        cents = np.stack([centroid(x_top[lab2 == j]) for j in (0, 1)])
        cl["k2_centroid_cosine"] = round(float(cents[0] @ cents[1]), 4)
        # which side do the labelled TV clips fall on? (nearest centroid of the k=2 fit)
        if tv.any():
            a_cent = centroid(x_top[owner_mask_top])
            b_cent = centroid(x_top[~owner_mask_top]) if (~owner_mask_top).any() else a_cent
            tv_a = int(np.sum(emb[tv] @ a_cent >= emb[tv] @ b_cent))
            cl["tv_in_A"] = tv_a
            cl["tv_in_B"] = int(tv.sum()) - tv_a
        # coarse acoustic descriptors per cluster: is it a channel/level split or a speaker split?
        r_top, d_top = rms[top], dur[top]
        cl["rms_dbfs_median"] = {"A": round(float(np.median(r_top[owner_mask_top])), 2),
                                 "B": round(float(np.median(r_top[~owner_mask_top])), 2) if (~owner_mask_top).any() else None}
        cl["duration_s_median"] = {"A": round(float(np.median(d_top[owner_mask_top])), 2),
                                   "B": round(float(np.median(d_top[~owner_mask_top])), 2) if (~owner_mask_top).any() else None}
        # within-A and within-B spread: a tight A and a diffuse B looks like "owner vs everyone else"
        cl["mean_cos_to_own_centroid"] = {
            "A": round(float(np.mean(x_top[owner_mask_top] @ centroid(x_top[owner_mask_top]))), 4),
            "B": round(float(np.mean(x_top[~owner_mask_top] @ centroid(x_top[~owner_mask_top]))), 4) if (~owner_mask_top).any() else None,
        }
    rep["cluster"] = cl

    # ---- owner (A) vs impostors (TV, B) with leave-sessions-out centroids -----------
    top_idx = np.nonzero(top)[0]
    a_idx = top_idx[owner_mask_top]
    b_idx = top_idx[~owner_mask_top]
    tv_idx = np.nonzero(tv)[0]
    ev: Dict[str, Any] = {}
    if a_idx.size >= folds * 2:
        fold, n_sessions = session_folds(mt[a_idx], folds, session_gap_s)
        ev["sessions_in_A"] = n_sessions
        ev["folds"] = folds
        ev["fold_sizes"] = [int((fold == f).sum()) for f in range(folds)]
        emb_a = emb[a_idx]
        s_own, ok = owner_scores_loso(emb_a, fold, folds)
        d_own = dur[a_idx][ok]
        s_tv = impostor_scores(emb[tv_idx], emb_a, fold, folds)
        s_b = impostor_scores(emb[b_idx], emb_a, fold, folds)
        s_pool = np.concatenate([s_tv, s_b])
        ev["n"] = {"owner": int(s_own.size), "tv": int(s_tv.size), "B": int(s_b.size), "pool": int(s_pool.size)}
        ev["score_quantiles"] = {"owner": quantiles(s_own), "tv": quantiles(s_tv), "B": quantiles(s_b)}
        ev["score_max"] = {"tv": round(float(s_tv.max()), 4) if s_tv.size else None,
                           "B": round(float(s_b.max()), 4) if s_b.size else None}
        med_o = float(np.median(s_own))
        runner = max([float(np.median(v)) for v in (s_tv, s_b) if v.size] or [float("nan")])
        ev["separation"] = {
            "owner_median": round(med_o, 4),
            "runner_up_median": round(runner, 4),
            "owner_median_minus_runner_up_median": round(med_o - runner, 4),
            "owner_median_minus_pool_p95": round(med_o - float(np.percentile(s_pool, 95)), 4) if s_pool.size else None,
            "owner_median_minus_tv_max": round(med_o - float(s_tv.max()), 4) if s_tv.size else None,
        }
        e_pool, t_eer = eer(s_own, s_pool)
        ev["eer_pool"] = round(e_pool, 4)
        ev["eer_pool_ci95"] = bootstrap_ci(lambda a, b: eer(a, b)[0], s_own, s_pool, boot, rng)
        ev["eer_threshold"] = round(t_eer, 4)
        e_tv, _ = eer(s_own, s_tv)
        ev["eer_tv_only"] = round(e_tv, 4)
        # operating points
        t_far1 = threshold_for_far(s_own, s_pool, 0.01)
        far1, frr1 = far_frr(s_own, s_pool, t_far1)
        ev["at_far1pct_pool"] = {"threshold": round(t_far1, 4), "far": round(far1, 4), "frr": round(frr1, 4),
                                 "tv_accepted": int(np.sum(s_tv >= t_far1))}
        ev["frr_at_far1_ci95"] = bootstrap_ci(
            lambda a, b: far_frr(a, b, threshold_for_far(a, b, 0.01))[1], s_own, s_pool, boot, rng)
        t_frr10 = threshold_for_frr(s_own, 0.10)
        far10, frr10 = far_frr(s_own, s_pool, t_frr10)
        ev["at_frr10pct"] = {"threshold": round(t_frr10, 4), "frr": round(frr10, 4), "far_pool": round(far10, 4),
                             "far_tv": round(float(np.mean(s_tv >= t_frr10)), 4) if s_tv.size else None,
                             "tv_accepted": int(np.sum(s_tv >= t_frr10)), "tv_total": int(s_tv.size),
                             "B_accepted": int(np.sum(s_b >= t_frr10)), "B_total": int(s_b.size)}
        # Operating point for the secondary analyses = the pool-FAR<=1 % threshold. The zero-TV-accept
        # point is reported beside it: when the two differ a lot, the labelled TV clips are not
        # separable from the owner at a usable FRR (the gate cannot meet its zero-TV bar by cosine alone).
        t_op = t_far1
        t_zero_tv = (float(s_tv.max()) + 1e-6) if s_tv.size else t_far1
        far_z, frr_z = far_frr(s_own, s_pool, t_zero_tv)
        ev["zero_tv_accept_point"] = {"threshold": round(t_zero_tv, 4), "frr": round(frr_z, 4), "far_pool": round(far_z, 4)}
        ev["operating_threshold"] = round(t_op, 4)
        if s_tv.size:
            acc = s_tv >= t_op
            tvi = tv_idx
            ev["tv_diagnostics"] = {
                "scores_sorted": [round(float(v), 4) for v in np.sort(s_tv)],
                "percentile_within_owner_scores": [round(float(np.mean(s_own < v)), 4) for v in np.sort(s_tv)],
                "accepted_at_operating_threshold": int(acc.sum()),
                "mean_pairwise_cosine_among_tv": round(float((emb[tvi] @ emb[tvi].T)[np.triu_indices(s_tv.size, 1)].mean()), 4) if s_tv.size > 1 else None,
                "duration_s_mean": {"accepted": round(float(dur[tvi][acc].mean()), 2) if acc.any() else None,
                                    "rejected": round(float(dur[tvi][~acc].mean()), 2) if (~acc).any() else None},
                "rms_dbfs_mean": {"accepted": round(float(rms[tvi][acc].mean()), 2) if acc.any() else None,
                                  "rejected": round(float(rms[tvi][~acc].mean()), 2) if (~acc).any() else None},
                "max_cosine_to_any_owner_clip": quantiles((emb[tvi] @ emb[a_idx].T).max(axis=1), (50, 95)),
            }
        # length buckets (clip duration; net-speech length is not in the manifest)
        bk: Dict[str, Any] = {}
        pool_all_b = np.array([_bucket_of(v) for v in np.concatenate([dur[tv_idx], dur[b_idx]])])
        own_b = np.array([_bucket_of(v) for v in d_own])
        for name, _, _ in BUCKETS:
            o, p = s_own[own_b == name], s_pool[pool_all_b == name]
            e, _ = eer(o, p) if (o.size >= 10 and p.size >= 5) else (float("nan"), 0)
            bk[name] = {"owner_n": int(o.size), "pool_n": int(p.size), "eer": None if np.isnan(e) else round(e, 4),
                        "frr_at_op": round(float(np.mean(o < t_op)), 4) if o.size else None}
        ev["by_duration"] = bk
        # controls
        # Controls. (1) label shuffle: permute owner/impostor labels over the pooled scores -> ~50 %.
        # (2) noise embeddings: the SAME pipeline (cluster -> A/B -> leave-sessions-out) on random
        # unit vectors. Cluster A/B is derived from the embeddings it is scored in, so even noise
        # scores below 50 % EER; that number is the selection-bias floor of this harness (it shrinks
        # as the embedding dimension grows), and a pseudo-labelled result is only believable to the
        # extent it sits well under it. (A random *profile* is not
        # used: on clustered data a random direction separates the clusters by luck, anywhere 0-100 %.)
        shuf = rng.permutation(np.concatenate([np.ones(s_own.size, bool), np.zeros(s_pool.size, bool)]))
        allv = np.concatenate([s_own, s_pool])
        ev["control_label_shuffle_eer"] = round(eer(allv[shuf], allv[~shuf])[0], 4)
        ev["control_noise_embeddings_eer"] = round(
            _noise_pipeline_eer(emb.shape, groups, mt, folds, session_gap_s, seed), 4)
        ev["eer_pool_below_noise_floor"] = bool(ev["eer_pool"] < ev["control_noise_embeddings_eer"])
        ev["control_ok"] = bool(0.35 <= ev["control_label_shuffle_eer"] <= 0.65
                                and ev["control_noise_embeddings_eer"] == ev["control_noise_embeddings_eer"])
        # what enrolment must cover: train on a subset of conditions, test on the rest
        cond: Dict[str, Any] = {}
        r_a = rms[a_idx]
        lo_q, hi_q = np.percentile(r_a, [33.3, 66.7])
        quiet, loud = r_a <= lo_q, r_a >= hi_q
        for tag, train_mask, test_mask in (("enrol_loud_test_quiet", loud, quiet), ("enrol_quiet_test_loud", quiet, loud),
                                           ("enrol_all_other_folds_test_quiet", np.ones(a_idx.size, bool), quiet)):
            o = []
            for f in range(folds):
                tr = (fold != f) & train_mask
                te = (fold == f) & test_mask
                if tr.sum() and te.sum():
                    o.append(emb_a[te] @ centroid(emb_a[tr]))
            o = np.concatenate(o) if o else np.zeros(0)
            cond[tag] = {"n": int(o.size), "median": round(float(np.median(o)), 4) if o.size else None,
                         "frr_at_op": round(float(np.mean(o < t_op)), 4) if o.size else None}
        ev["condition_coverage"] = cond
        # how many enrolment clips: centroid of n random OTHER-fold clips
        curve: Dict[str, Any] = {}
        for n_enrol in (1, 3, 5, 8, 12, 24):
            fr, medians = [], []
            for rep_i in range(20):
                f = rep_i % folds
                te, tr = np.nonzero(fold == f)[0], np.nonzero(fold != f)[0]
                if tr.size < n_enrol or te.size == 0:
                    continue
                pick = rng.choice(tr, n_enrol, replace=False)
                c = centroid(emb_a[pick])
                sc = emb_a[te] @ c
                fr.append(float(np.mean(sc < t_op)))
                medians.append(float(np.median(sc)))
            curve[str(n_enrol)] = {"frr_at_op_mean": round(float(np.mean(fr)), 4) if fr else None,
                                   "owner_median_mean": round(float(np.mean(medians)), 4) if medians else None}
        ev["enrol_size_curve"] = curve
        # the labelled non-speech quarantine, scored like impostors: how many does the cosine gate accept?
        ns = np.nonzero(groups == "nonspeech")[0]
        s_ns = np.zeros(0)
        if ns.size:
            s_ns = impostor_scores(emb[ns], emb_a, fold, folds)
            ev["nonspeech_vs_owner"] = {
                "n": int(ns.size), "score_quantiles": quantiles(s_ns),
                "accepted_at_operating_threshold": int(np.sum(s_ns >= t_op)),
                "accepted_at_frr10_threshold": int(np.sum(s_ns >= t_frr10)),
                "duration_s_median": round(float(np.median(dur[ns])), 2),
                "rms_dbfs_median": round(float(np.median(rms[ns])), 2),
                # near-silence all embedding to ONE point (a channel/noise-floor attractor) that sits
                # near the owner centroid is the signature of a gate that needs a speech-evidence rule
                "mean_pairwise_cosine": round(float((emb[ns] @ emb[ns].T)[np.triu_indices(ns.size, 1)].mean()), 4) if ns.size > 1 else None,
                "mean_vector_cosine_to_A_centroid": round(float(centroid(emb[ns]) @ centroid(emb[a_idx])), 4),
                "mean_vector_cosine_to_B_centroid": round(float(centroid(emb[ns]) @ centroid(emb[b_idx])), 4) if b_idx.size else None,
            }
        # fixed thresholds (e.g. the live 0.70 / 0.75 for the incumbent): what do they do on this corpus?
        if len(fixed_thresholds):
            ev["at_fixed_thresholds"] = {
                f"{float(t):.2f}": {
                    "frr": round(float(np.mean(s_own < t)), 4), "far_B": round(float(np.mean(s_b >= t)), 4) if s_b.size else None,
                    "tv_accepted": int(np.sum(s_tv >= t)),
                    "nonspeech_accepted": int(np.sum(s_ns >= t)) if s_ns.size else None}
                for t in fixed_thresholds}
    else:
        ev["error"] = "too few cluster-A clips for leave-sessions-out evaluation"
    rep["eval"] = ev

    # ---- targets ---------------------------------------------------------------------
    t: Dict[str, Any] = {}
    if "separation" in ev:
        sep = ev["separation"]["owner_median_minus_runner_up_median"]
        t["separation"] = {"value": sep, "target": f">= {TARGETS['separation']}", "pass": bool(sep >= TARGETS["separation"]),
                           "strict_p95_value": ev["separation"]["owner_median_minus_pool_p95"]}
        t["frr_at_far1"] = {"value": ev["at_far1pct_pool"]["frr"], "target": f"< {TARGETS['frr_at_far1']}",
                            "pass": bool(ev["at_far1pct_pool"]["frr"] < TARGETS["frr_at_far1"])}
        t["far_at_frr10"] = {"value": ev["at_frr10pct"]["far_pool"], "target": f"< {TARGETS['far_at_frr10']}",
                             "pass": bool(ev["at_frr10pct"]["far_pool"] < TARGETS["far_at_frr10"])}
        t["tv_accepts"] = {"value_at_frr10_threshold": ev["at_frr10pct"]["tv_accepted"],
                           "value_at_far1_threshold": ev["at_far1pct_pool"]["tv_accepted"],
                           "frr_if_zero_tv_enforced": ev["zero_tv_accept_point"]["frr"], "target": "0",
                           "pass": bool(ev["at_frr10pct"]["tv_accepted"] == 0 and ev["at_far1pct_pool"]["tv_accepted"] == 0)}
    rep["latency"] = latency
    if latency.get("p50_ms") is not None:
        t["latency_p50"] = {"value": latency["p50_ms"], "target": f"< {TARGETS['latency_p50_ms']} ms",
                            "pass": bool(latency["p50_ms"] < TARGETS["latency_p50_ms"])}
    rep["targets_result"] = t
    return rep


def _eval_labelled(emb, own_idx, imp_idx, tv_idx, mtimes, folds: int, gap_s: float) -> Dict[str, Any]:
    """Owner/impostor scores for GIVEN label sets (indices into emb), leave-sessions-out. Aggregates."""
    mt = np.asarray(mtimes, dtype=np.float64)
    fold, _n = session_folds(mt[own_idx], folds, gap_s)
    own, _ok = owner_scores_loso(emb[own_idx], fold, folds)
    s_tv = impostor_scores(emb[tv_idx], emb[own_idx], fold, folds)
    s_imp = impostor_scores(emb[imp_idx], emb[own_idx], fold, folds)
    pool = np.concatenate([s_tv, s_imp])
    t_far1 = threshold_for_far(own, pool, 0.01)
    t_frr10 = threshold_for_frr(own, 0.10)
    return {
        "n_owner": int(own.size), "n_impostor": int(s_imp.size),
        "owner_median": round(float(np.median(own)), 4),
        "impostor_median": round(float(np.median(s_imp)), 4),
        "eer_pool": round(float(eer(own, pool)[0]), 4),
        "threshold_far1": round(float(t_far1), 4),
        "frr_at_far1": round(float(far_frr(own, pool, t_far1)[1]), 4),
        "far_pool_at_frr10": round(float(far_frr(own, pool, t_frr10)[0]), 4),
        "tv_accepted_at_far1": int(np.sum(s_tv >= t_far1)),
        "tv_accepted_at_frr10": int(np.sum(s_tv >= t_frr10)),
        "tv_percentile_within_owner": [round(float(np.mean(own < v)), 4) for v in np.sort(s_tv)],
        "tv_accepted_mask": (s_tv >= t_far1),
    }


def _a_set(emb, groups, seed: int):
    """Larger cluster of a 2-means over the 'top' clips, as a boolean mask over all clips."""
    top = np.nonzero(np.asarray(groups) == "top")[0]
    lab, _ = spherical_kmeans(emb[top], 2, np.random.default_rng(seed + 2))
    big = int(np.argmax(np.bincount(lab)))
    in_a = np.zeros(emb.shape[0], dtype=bool)
    in_a[top[lab == big]] = True
    return in_a


def _mean_pairwise_cos(x: np.ndarray) -> float:
    n = x.shape[0]
    if n < 2:
        return float("nan")
    tot = x.sum(axis=0)
    return float((tot @ tot - n) / (n * (n - 1)))


def _homogeneity(emb: np.ndarray, mask: np.ndarray, pool: np.ndarray, rng: np.random.Generator, reps: int = 40) -> Dict[str, float]:
    """Mean pairwise cosine inside *mask* vs same-size random draws from *pool* (z = lift in SDs)."""
    obs = _mean_pairwise_cos(emb[mask])
    k, base = int(mask.sum()), np.nonzero(pool)[0]
    draws = [_mean_pairwise_cos(emb[rng.choice(base, k, replace=False)]) for _ in range(reps)] if base.size >= k else []
    mu, sd = (float(np.mean(draws)), float(np.std(draws) + 1e-9)) if draws else (float("nan"), float("nan"))
    return {"observed": round(obs, 4), "random_mean": round(mu, 4), "lift": round(obs - mu, 4), "z": round((obs - mu) / sd, 1)}


def compare_spaces(emb1, emb2, groups, mtimes, *, folds: int = 5, gap_s: float = 1800.0, seed: int = 20261005) -> Dict[str, Any]:
    """Do two independent embedders agree about the corpus? (aligned arrays, same clips, same order)

    Each space cuts the 'top' clips into A/B with its own 2-means. If the cut were a property of the
    AUDIO (owner vs not-owner) the two spaces would agree; where they do not, a pseudo-label is an
    artefact of the model that made it. So each space is also scored with the OTHER space's labels
    ('cross-label': the label did not come from the embeddings being scored) and on the CONSENSUS
    sets (A in both / B in both). Aggregates only."""
    e1, e2 = l2norm(np.asarray(emb1, dtype=np.float64)), l2norm(np.asarray(emb2, dtype=np.float64))
    groups = np.asarray(groups)
    top, tv = groups == "top", np.nonzero(groups == "tv")[0]
    a1, a2 = _a_set(e1, groups, seed), _a_set(e2, groups, seed)
    b1, b2 = top & ~a1, top & ~a2
    idx = np.nonzero

    def run(emb, own_mask, imp_mask):
        if own_mask.sum() < folds * 2 or imp_mask.sum() < 5:
            return {"error": "too few clips"}
        r = _eval_labelled(emb, idx(own_mask)[0], idx(imp_mask)[0], tv, mtimes, folds, gap_s)
        return {k: v for k, v in r.items() if k != "tv_accepted_mask"}

    own1 = _eval_labelled(e1, idx(a1)[0], idx(b1)[0], tv, mtimes, folds, gap_s)
    own2 = _eval_labelled(e2, idx(a2)[0], idx(b2)[0], tv, mtimes, folds, gap_s)
    inter, union = int(np.sum(a1 & a2 & top)), int(np.sum((a1 | a2) & top))
    both_b = int(np.sum(b1 & b2))
    out: Dict[str, Any] = {
        "top_clips": int(top.sum()),
        "A_size": {"space1": int(a1.sum()), "space2": int(a2.sum()), "both": inter},
        "B_size": {"space1": int(b1.sum()), "space2": int(b2.sum()), "both": both_b},
        "A_membership_agreement": round(float(np.mean(a1[top] == a2[top])), 4),
        "A_jaccard": round(inter / union, 4) if union else None,
        "own_labels": {"space1": {k: v for k, v in own1.items() if k != "tv_accepted_mask"},
                       "space2": {k: v for k, v in own2.items() if k != "tv_accepted_mask"}},
        "cross_labels": {
            "space1_scored_with_space2_labels": run(e1, a2, b2),
            "space2_scored_with_space1_labels": run(e2, a1, b1),
        },
        "consensus": {
            "space1": run(e1, a1 & a2, b1 & b2),
            "space2": run(e2, a1 & a2, b1 & b2),
        },
        # Is one space's A a *coherent* set in the other space? (lift > 0 and large z = yes; ~0 = the cut is
        # specific to the model that made it.) Also the plain conditional: P(A2 | A1) vs P(A2 | B1).
        "homogeneity_of_A": {
            "A1_seen_by_space1": _homogeneity(e1, a1, top, np.random.default_rng(seed + 7)),
            "A1_seen_by_space2": _homogeneity(e2, a1, top, np.random.default_rng(seed + 8)),
            "A2_seen_by_space2": _homogeneity(e2, a2, top, np.random.default_rng(seed + 9)),
            "A2_seen_by_space1": _homogeneity(e1, a2, top, np.random.default_rng(seed + 10)),
        },
        "p_A2_given": {"A1": round(float(np.mean(a2[a1 & top])), 4), "B1": round(float(np.mean(a2[b1])), 4),
                       "all": round(float(np.mean(a2[top])), 4)},
        "tv_accepted_at_own_far1_threshold": {
            "space1": int(own1["tv_accepted_at_far1"]), "space2": int(own2["tv_accepted_at_far1"]),
            "both": int(np.sum(own1["tv_accepted_mask"] & own2["tv_accepted_mask"])), "tv_total": int(tv.size)},
    }
    return out


def latency_summary(clips: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-clip compute latency (fbank + inference; file read / resample excluded) after warm-up."""
    ok = [c for c in clips if c.get("status") == "ok" and not c.get("warmup")]
    if not ok:
        return {"n": 0, "p50_ms": None, "p95_ms": None}
    a = np.array([c["compute_ms"] for c in ok], dtype=np.float64)
    d = np.array([c["duration_s"] for c in ok], dtype=np.float64)
    out: Dict[str, Any] = {
        "n": int(a.size), "p50_ms": round(float(np.percentile(a, 50)), 1), "p95_ms": round(float(np.percentile(a, 95)), 1),
        "max_ms": round(float(a.max()), 1),
        "fraction_under_200ms": round(float(np.mean(a < 200.0)), 4),
        "fbank_p50_ms": round(float(np.percentile([c["fbank_ms"] for c in ok], 50)), 2),
        "infer_p50_ms": round(float(np.percentile([c["infer_ms"] for c in ok], 50)), 1),
        "clip_duration_s": quantiles(d, (5, 50, 95)),
        "corr_latency_duration": round(float(np.corrcoef(a, d)[0, 1]), 3) if a.size > 2 and d.std() > 0 else None,
    }
    by = {}
    for name, lo, hi in BUCKETS:
        m = (d >= lo) & (d < hi)
        if m.sum():
            by[name] = {"n": int(m.sum()), "p50_ms": round(float(np.percentile(a[m], 50)), 1),
                        "p95_ms": round(float(np.percentile(a[m], 95)), 1)}
    out["by_duration"] = by
    if a.size > 5 and d.std() > 0:  # linear model: ms ≈ a + b·seconds → cost of a typical turn length
        b, a0 = np.polyfit(d, a, 1)
        out["ms_per_second_of_audio"] = round(float(b), 1)
        out["fixed_ms"] = round(float(a0), 1)
    return out


# --------------------------------------------------------------------------- leak guard + output

def assert_aggregate_only(report: Dict[str, Any], manifest: Dict[str, Any]) -> None:
    """Refuse a report that carries any clip id, clip-array or per-clip table."""
    blob = json.dumps(report)
    for c in manifest.get("clips", []):
        if c["id"] in blob:
            raise AssertionError("report contains a clip id; refusing to write")
    for k in ("clips", "emb", "embeddings"):
        if k in report:
            raise AssertionError(f"report has a {k!r} key; refusing to write")


def render_markdown(rep: Dict[str, Any], run: Dict[str, Any]) -> str:
    L: List[str] = ["# Speaker-gate step 1 — Pi evaluation (aggregates only)", ""]
    m = run.get("model", {})
    L += [f"Model `{m.get('model_key')}` ({m.get('licence')}, sha256 `{str(m.get('sha256'))[:12]}…`, dim {m.get('dim')}); "
          f"threads {run.get('threads')}; wall {run.get('wall_s')} s; peak RSS {run.get('peak_rss_mb')} MB; "
          f"daemon health checks {run.get('daemon_health_checks')}, restarts seen {run.get('daemon_restarts_seen')}.", ""]
    L += ["## Counts", "", "```json", json.dumps({"manifest": run.get("counts"), "evaluated": rep["counts"]}, indent=1), "```", ""]
    L += ["## Cluster structure", "", "```json", json.dumps(rep["cluster"], indent=1), "```", ""]
    L += ["## Targets", "", "| target | value | bar | pass |", "|---|---|---|---|"]
    for k, v in rep["targets_result"].items():
        val = v.get("value", v.get("value_at_frr10_threshold"))
        L.append(f"| {k} | {val} | {v['target']} | {'PASS' if v['pass'] else 'FAIL'} |")
    L += ["", "## Evaluation", "", "```json", json.dumps(rep["eval"], indent=1), "```", ""]
    L += ["## Latency (Pi, compute only)", "", "```json", json.dumps(rep["latency"], indent=1), "```", ""]
    return "\n".join(L)


def load_inputs(shadow: Path):
    manifest = json.loads((shadow / "manifest.json").read_text())
    emb_all = np.load(shadow / "embeddings.npz")["emb"]
    rows = manifest["clips"]
    row_of = manifest["emb_row_of_clip"]
    kept = [c for c in rows if c["id"] in row_of]
    order = np.array([row_of[c["id"]] for c in kept])
    emb = emb_all[order]
    return manifest, kept, emb


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shadow-dir", default=os.environ.get("SPEAKER_SHADOW_DIR", str(Path.home() / ".zoe-voice" / "speaker-shadow")))
    ap.add_argument("--out-prefix", default="report", help="writes <prefix>.json and <prefix>.md into the shadow dir")
    ap.add_argument("--fixed-thresholds", default="", help="comma list, e.g. 0.70,0.75: report FRR/FAR at these")
    ap.add_argument("--compare-with", default=None, metavar="DIR",
                    help="another embed output dir (same corpus, different model): also write compare.json")
    args = ap.parse_args(list(argv) if argv is not None else None)
    shadow = Path(args.shadow_dir).expanduser()
    manifest, kept, emb = load_inputs(shadow)
    rep = evaluate(
        emb,
        [c["group"] for c in kept], [c["duration_s"] for c in kept], [c["mtime"] for c in kept], [c["rms_dbfs"] for c in kept],
        latency_summary(manifest["clips"]),
        folds=int(os.environ.get("SPEAKER_SHADOW_FOLDS", "5")),
        session_gap_s=float(os.environ.get("SPEAKER_SHADOW_SESSION_GAP_S", "1800")),
        seed=int(os.environ.get("SPEAKER_SHADOW_SEED", "20261005")),
        boot=int(os.environ.get("SPEAKER_SHADOW_BOOT", "300")),
        fixed_thresholds=[float(x) for x in args.fixed_thresholds.split(",") if x.strip()],
    )
    run = {k: manifest.get(k) for k in ("model", "threads", "wall_s", "peak_rss_mb", "counts", "daemon_health_checks",
                                        "daemon_restarts_seen", "complete", "abort_reason", "onnxruntime")}
    rep["run"] = run
    assert_aggregate_only(rep, manifest)
    old = os.umask(0o077)
    try:
        (shadow / f"{args.out_prefix}.json").write_text(json.dumps(rep, indent=1) + "\n")
        (shadow / f"{args.out_prefix}.md").write_text(render_markdown(rep, run))
    finally:
        os.umask(old)
    if args.compare_with:
        other_man, other_kept, other_emb = load_inputs(Path(args.compare_with).expanduser())
        mine = {c["id"]: i for i, c in enumerate(kept)}
        pairs = [(mine[c["id"]], j) for j, c in enumerate(other_kept) if c["id"] in mine]
        pairs.sort()
        i1 = [p[0] for p in pairs]
        i2 = [p[1] for p in pairs]
        cmp_rep = compare_spaces(
            emb[i1], other_emb[i2], [kept[i]["group"] for i in i1], [kept[i]["mtime"] for i in i1],
            folds=int(os.environ.get("SPEAKER_SHADOW_FOLDS", "5")),
            gap_s=float(os.environ.get("SPEAKER_SHADOW_SESSION_GAP_S", "1800")),
            seed=int(os.environ.get("SPEAKER_SHADOW_SEED", "20261005")))
        cmp_rep["models"] = {"space1": manifest.get("model", {}).get("model_key"),
                             "space2": other_man.get("model", {}).get("model_key"),
                             "clips_in_both": len(pairs)}
        assert_aggregate_only(cmp_rep, manifest)
        old = os.umask(0o077)
        try:
            (shadow / "compare.json").write_text(json.dumps(cmp_rep, indent=1) + "\n")
        finally:
            os.umask(old)
        print(json.dumps(cmp_rep, indent=1))
    print(json.dumps({"targets": rep["targets_result"], "control_ok": rep["eval"].get("control_ok")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
