"""The semantic half of the restraint classifier (``restraint_head.py``): six logistic units on the router's bge-small embedding.

Fixtures only: invented names, no household text. Every behaviour has a break-the-fix twin (the head neutered, a threshold
raised, the embedder removed) so a green run cannot mean "the head was never wired". The measurement test needs the bge model
on disk and skips (never passes) without it; everything else is stdlib + numpy and runs in the slim CI lane.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

import restraint
import restraint_head

SVC = Path(__file__).resolve().parents[1]
REPO = SVC.parents[1]
FIXTURE = SVC / "tests" / "fixtures" / "restraint_labelled.json"
CLASSES = restraint.CLASSES
PLAIN = "Remind me to buy oat milk"


@pytest.fixture(autouse=True)
def _clean():
    restraint_head.reset()
    yield
    restraint_head.reset()


def _direction(cls: str) -> np.ndarray:
    """A unit vector the head scores as strongly ``cls`` (its own weight row): the head's math without any model."""
    classes, W, _b, _thr, _meta = restraint_head._load()
    v = W[classes.index(cls)]
    return v / np.linalg.norm(v)


def _embedder_for(mapping: dict):
    """text -> unit vector, counting calls."""
    calls = []

    def fn(text):
        calls.append(text)
        return mapping.get(text, np.zeros(384, dtype=np.float32))
    fn.calls = calls
    return fn


# ── the shipped artefact ────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_shipped_head_is_well_formed_and_small():
    head = restraint_head._load()
    assert head is not None, "models/restraint_head.npz/.json missing - the .gitignore whitelist must list both"
    classes, W, b, thr, meta = head
    assert tuple(classes) == tuple(CLASSES) or set(classes) == set(CLASSES)
    assert W.shape == (len(classes), 384) and b.shape == thr.shape == (len(classes),)
    assert np.all((thr > 0) & (thr <= 1))
    assert meta["format"] == restraint_head.FORMAT and meta["embedder"] == "BAAI/bge-small-en-v1.5"
    assert restraint_head.HEAD_PATH.stat().st_size < 64 * 1024        # a matrix product, not a model: ~10 KB of RAM


def _load_eval():
    spec = importlib.util.spec_from_file_location("restraint_eval", REPO / "scripts" / "perf" / "restraint_eval.py")
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    return ev


def test_the_training_fingerprint_changes_when_only_a_label_changes():
    """Codex P2 (#1996): a texts-only hash stays put when a row's ``classes`` are corrected, so the stale-artefact test would
    keep accepting weights trained against the old labels."""
    ev = _load_eval()
    dev = ev.load_sets()["dev"]
    base = ev.fingerprint(dev)
    i = next(k for k, (w, _t) in enumerate(dev) if w)
    relabelled = list(dev)
    relabelled[i] = (("affect",) if dev[i][0] != ("affect",) else ("money",), dev[i][1])      # same text, different label
    assert [t for _w, t in relabelled] == [t for _w, t in dev]
    assert ev.fingerprint(relabelled) != base
    plain_to_sens = list(dev)
    j = next(k for k, (w, _t) in enumerate(dev) if not w)
    plain_to_sens[j] = (("health",), dev[j][1])
    assert ev.fingerprint(plain_to_sens) != base
    # what must NOT matter: row order and the order of classes inside a row
    assert ev.fingerprint(list(reversed(dev))) == base
    assert ev.fingerprint([(("money", "health"), "x")]) == ev.fingerprint([(("health", "money"), "x")])


def test_the_head_was_trained_on_exactly_the_current_dev_split_and_never_on_held_out():
    """Stale-artefact guard: change the dev data - text OR a row's labels - and the head must be retrained
    (train_restraint_head.py). The fingerprint is over canonical (classes, text) rows, the function the trainer records."""
    ev = _load_eval()
    sets = ev.load_sets()
    dev_texts = sorted(t for _w, t in sets["dev"])
    meta = restraint_head._load()[4]
    assert meta["train_sha_scheme"] == ev.FINGERPRINT_SCHEME
    assert meta["train_sha"] == ev.fingerprint(sets["dev"]) and meta["n_train"] == len(dev_texts)
    norm = lambda s: re.sub(r"\W+", " ", s.lower()).strip()           # noqa: E731
    held = {norm(t) for _w, t in sets["held_out"]}
    assert not held & {norm(t) for t in dev_texts}, "a held-out sentence is in the dev split"


def test_the_labelled_fixture_is_clean_and_its_splits_are_disjoint():
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for split in ("dev", "held_out"):
        rows = fx[split]
        assert len(rows) >= 300
        assert all(set(r["classes"]) <= set(CLASSES) for r in rows)
        for c in CLASSES:
            assert sum(c in r["classes"] for r in rows) >= 35, (split, c)
        assert sum(not r["classes"] for r in rows) >= 100
        norms = [re.sub(r"\W+", " ", r["text"].lower()).strip() for r in rows]
        assert len(norms) == len(set(norms)), f"duplicate sentence inside {split}"
    d = {re.sub(r"\W+", " ", r["text"].lower()).strip() for r in fx["dev"]}
    h = {re.sub(r"\W+", " ", r["text"].lower()).strip() for r in fx["held_out"]}
    # two trivial plain sentences ("set a timer for ten minutes") were written independently into both splits; the
    # loader drops them from held-out, so the fixture may share at most those (negative control: it is a handful, not a leak)
    assert len(d & h) <= 5


# ── a damaged model file is "no head", never a crash ────────────────────────────────────────────────────────────────────
def _write_head(tmp_path, monkeypatch, *, W=None, b=None, thr=None):
    n = len(CLASSES)
    W = np.zeros((n, 384), dtype=np.float32) if W is None else W
    b = np.zeros(n, dtype=np.float32) if b is None else b
    thr = np.full(n, 0.5, dtype=np.float32) if thr is None else thr
    npz, meta = tmp_path / "restraint_head.npz", tmp_path / "restraint_head.json"
    np.savez(npz, W=W, b=b, thr=thr)
    meta.write_text(json.dumps({"format": restraint_head.FORMAT, "classes": list(CLASSES), "dim": 384,
                                "embedder": "BAAI/bge-small-en-v1.5"}), encoding="utf-8")
    monkeypatch.setattr(restraint_head, "HEAD_PATH", npz)
    monkeypatch.setattr(restraint_head, "META_PATH", meta)
    restraint_head.reset()


def test_a_well_formed_temp_head_loads(tmp_path, monkeypatch):
    """Control for the malformed cases below: the same writer, correct shapes, loads."""
    _write_head(tmp_path, monkeypatch)
    assert restraint_head.available()


@pytest.mark.parametrize("which,shape", [
    ("b", (1,)), ("thr", (1,)), ("b", (len(CLASSES) + 1,)), ("thr", (len(CLASSES) + 1,)),
    ("b", (len(CLASSES), 1)), ("thr", (1, len(CLASSES))), ("b", ()), ("thr", ()),
])
def test_a_wrongly_shaped_bias_or_threshold_is_refused_independently(tmp_path, monkeypatch, which, shape):
    """Codex P2 (#1996): a wrong shape on EITHER array must refuse the head, whatever the other one is."""
    _write_head(tmp_path, monkeypatch, **{which: np.zeros(shape, dtype=np.float32)})
    assert restraint_head._load() is None and not restraint_head.available()
    assert restraint_head.decide_from_vector(np.ones(384)) == ()
    restraint_head.set_embedder(_embedder_for({"I am so sad": np.ones(384, dtype=np.float32)}))
    assert restraint_head.classes("I am so sad", embed=True) == ()      # falls back to the word list: never raises


def test_b_and_thr_both_wrong_in_the_same_way_is_the_case_the_chain_let_through(tmp_path, monkeypatch):
    """``b.shape != thr.shape != (n,)`` is a chained comparison: it rejects only when BOTH adjacent tests are true, so a head
    whose ``b`` and ``thr`` were both (1,) loaded and silently scored only the first class (broadcast + zip)."""
    _write_head(tmp_path, monkeypatch, b=np.zeros(1, dtype=np.float32), thr=np.zeros(1, dtype=np.float32))
    assert restraint_head._load() is None
    text = "the dentist is on Friday"
    assert restraint.classify_full(text, head=True) == restraint.classify_full(text, head=False)


def _arr(shape, index, value):
    a = np.zeros(shape, dtype=np.float32)
    a.flat[index] = value
    return a


@pytest.mark.parametrize("label,kwargs", [
    ("nan W", lambda n: {"W": _arr((n, 384), 5, np.nan)}),
    ("inf W", lambda n: {"W": _arr((n, 384), 5, np.inf)}),
    ("inf b", lambda n: {"b": _arr((n,), 2, np.inf)}),
    ("-inf b", lambda n: {"b": _arr((n,), 0, -np.inf)}),
    ("nan b", lambda n: {"b": _arr((n,), 1, np.nan)}),
    ("nan thr", lambda n: {"thr": np.where(np.arange(n) == 3, np.nan, 0.5).astype(np.float32)}),
    ("thr > 1", lambda n: {"thr": np.where(np.arange(n) == 0, 1.5, 0.5).astype(np.float32)}),
    ("thr < 0", lambda n: {"thr": np.where(np.arange(n) == 4, -0.1, 0.5).astype(np.float32)}),
    ("inf thr", lambda n: {"thr": np.where(np.arange(n) == 2, np.inf, 0.5).astype(np.float32)}),
])
def test_non_finite_or_out_of_range_parameters_refuse_the_head(tmp_path, monkeypatch, label, kwargs):
    """Codex P2 (#1996): a same-shaped head with NaN/Inf weights or biases, or thresholds outside [0, 1], used to load; every
    NaN probability then compares false, ``lookup`` cached an empty result as a real classification and the row was stamped
    head-complete, so under enforce a sensitive paraphrase could pass as non-sensitive instead of falling back safely."""
    _write_head(tmp_path, monkeypatch, **kwargs(len(CLASSES)))
    assert restraint_head._load() is None, label
    assert not restraint_head.available()
    restraint_head.set_embedder(_embedder_for({"I am so sad": np.ones(384, dtype=np.float32)}))
    assert restraint_head.lookup("I am so sad", embed=True) is None      # "did not run", not "found nothing"


def test_a_threshold_exactly_zero_or_one_is_still_allowed(tmp_path, monkeypatch):
    """The range is closed [0, 1]: the boundary values are not refused (control for the rejections above)."""
    n = len(CLASSES)
    _write_head(tmp_path, monkeypatch, thr=np.array([0.0, 1.0] + [0.5] * (n - 2), dtype=np.float32))
    assert restraint_head.available()


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_a_non_finite_score_at_runtime_is_never_cached_as_an_empty_classification(bad):
    """Even past the loader, a non-finite score must read as 'head unavailable for this call' (None, nothing cached),
    never as a cached ``()`` that stamps the row head-complete."""
    text = "something the embedder returned garbage for"
    v = np.zeros(384, dtype=np.float32)
    v[0] = bad
    restraint_head.set_embedder(_embedder_for({text: v}))
    assert restraint_head.lookup(text, embed=True) is None
    assert restraint_head.lookup(text, embed=False) is None             # nothing was cached
    assert restraint_head.decide_from_vector(v) == ()                   # the public helper stays fail-safe
    # a healthy vector for the same text afterwards is classified (the failure was not remembered)
    restraint_head.set_embedder(_embedder_for({text: _direction("grief")}))
    assert "grief" in (restraint_head.lookup(text, embed=True) or ())


def test_a_non_finite_score_from_weights_poisoned_after_load_is_not_cached(monkeypatch):
    """Belt and braces: weights corrupted AFTER load (the loader's own check bypassed) still cannot cache an empty result."""
    classes, W, b, thr, meta = restraint_head._load()
    W2 = W.copy()
    W2[0, 0] = np.nan
    monkeypatch.setitem(restraint_head._STATE, "head", (classes, W2, b, thr, meta))
    text = "a sensitive paraphrase"
    restraint_head.set_embedder(_embedder_for({text: np.ones(384, dtype=np.float32)}))
    assert restraint_head.lookup(text, embed=True) is None
    assert restraint_head.lookup(text, embed=False) is None


# ── the head's own math ─────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("cls", CLASSES)
def test_a_vector_along_a_class_weight_fires_that_class(cls):
    assert cls in restraint_head.decide_from_vector(_direction(cls))


def test_the_zero_vector_and_a_wrong_sized_vector_fire_nothing():
    assert restraint_head.decide_from_vector(np.zeros(384)) == ()
    assert restraint_head.decide_from_vector(np.ones(7)) == ()


def test_raising_a_threshold_above_the_score_silences_the_class_break_the_fix(monkeypatch):
    classes, W, b, thr, meta = restraint_head._load()
    v = _direction("health")
    assert "health" in restraint_head.decide_from_vector(v)
    muted = (classes, W, b, np.ones_like(thr) * 1.5, meta)
    monkeypatch.setitem(restraint_head._STATE, "head", muted)
    assert restraint_head.decide_from_vector(v) == ()


# ── the union with the word list ────────────────────────────────────────────────────────────────────────────────────────
def test_the_head_only_ever_adds_a_class_to_what_the_lexicon_found():
    text = "Anika's scan came back and she is waiting on the results"
    base = restraint.classify(text, head=False)
    restraint_head.set_embedder(_embedder_for({text: _direction("grief")}))
    got, signals = restraint.classify_full(text, head=True)
    assert set(base) <= set(got)                       # nothing the lexicon found is taken away
    assert "grief" in got and "grief:head" in signals  # ... and the head's class is named as the head's


def test_no_embedder_means_the_word_list_alone_byte_for_byte():
    for text in ("the dentist is on Friday", PLAIN, "I'm dreading Sunday dinner", "Mum and I fell out over Christmas"):
        assert restraint.classify_full(text, head=True) == restraint.classify_full(text, head=False)


def test_a_plain_sentence_the_head_scores_low_is_not_classed():
    restraint_head.set_embedder(_embedder_for({PLAIN: np.zeros(384, dtype=np.float32)}))
    assert restraint.classify(PLAIN, head=True) == ()


def test_the_default_hot_path_never_embeds_and_reads_only_what_was_embedded_before():
    text = "Quinn keeps waking at three with a racing heart"
    fn = _embedder_for({text: _direction("health")})
    restraint_head.set_embedder(fn)
    assert restraint.classify(text) == restraint.classify(text, head=False)     # default = cache only
    assert fn.calls == []                                                         # ... and it did not embed
    assert "health" in restraint.classify(text, head=True)                       # the write path embeds once
    assert fn.calls == [text]
    assert "health" in restraint.classify(text)                                  # now the hot path sees it, from the cache
    assert fn.calls == [text]                                                    # still one embedding


def test_row_classes_of_an_unstamped_row_never_embeds():
    text = "Quinn keeps waking at three with a racing heart"
    fn = _embedder_for({text: _direction("health")})
    restraint_head.set_embedder(fn)
    restraint.row_classes({}, text)
    assert fn.calls == []


def test_stamp_records_the_head_class_once_the_worker_has_filled_the_cache(monkeypatch):
    monkeypatch.setenv(restraint.ENV, "shadow")
    text = "Quinn keeps waking at three with a racing heart"
    restraint_head.set_embedder(_embedder_for({text: _direction("health")}))
    md: dict = {}
    restraint.stamp(md, text)
    assert "health" not in md["sensitivity"].split(",") and md["sensitivity_v"] == restraint.LEXICON_VERSION   # not yet: queued
    assert restraint_head.drain()
    restraint.stamp(md, text)                                                  # the cache is filled: the re-stamp carries the head
    assert "health" in md["sensitivity"].split(",") and md["sensitivity_v"] == restraint.VERSION
    # break-the-fix twin: no embedder -> the stamp is the word list's (here: nothing for this sentence)
    restraint_head.set_embedder(None)
    md2: dict = {}
    restraint.stamp(md2, text)
    restraint_head.drain()
    assert "health" not in md2["sensitivity"].split(",")


def test_the_head_never_loads_a_model_it_only_borrows_the_routers(monkeypatch):
    import sys
    monkeypatch.delitem(sys.modules, "semantic_router", raising=False)
    assert restraint_head._live_embedder() is None
    assert restraint_head.classes("Quinn keeps waking at three with a racing heart", embed=True) == ()

    class _Loaded:                                      # the router with its model in memory
        _MODEL = object()
        _MODEL_NAME = "BAAI/bge-small-en-v1.5"
        embed = staticmethod(lambda text: _direction("health"))
    monkeypatch.setitem(sys.modules, "semantic_router", _Loaded)
    assert restraint_head.classes("Quinn keeps waking at three with a racing heart", embed=True).count("health") == 1

    class _NotLoaded:                                   # imported but the model has not loaded yet: still inert, never triggers a load
        _MODEL = None
        embed = staticmethod(lambda text: (_ for _ in ()).throw(AssertionError("must not embed")))
    restraint_head.reset()
    monkeypatch.setitem(sys.modules, "semantic_router", _NotLoaded)
    assert restraint_head.classes("another sentence entirely", embed=True) == ()


def test_an_embedder_that_raises_is_no_head_not_a_broken_classifier():
    def boom(_t):
        raise RuntimeError("onnx exploded")
    restraint_head.set_embedder(boom)
    assert restraint.classify("the dentist is on Friday", head=True) == restraint.classify("the dentist is on Friday", head=False)


def test_a_missing_head_file_leaves_the_classifier_as_it_was(monkeypatch, tmp_path):
    monkeypatch.setattr(restraint_head, "HEAD_PATH", tmp_path / "none.npz")
    monkeypatch.setattr(restraint_head, "META_PATH", tmp_path / "none.json")
    restraint_head.reset()
    restraint_head.set_embedder(_embedder_for({}))
    assert not restraint_head.available()
    assert restraint.classify("the dentist is on Friday", head=True) == restraint.classify("the dentist is on Friday", head=False)


def test_the_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(restraint_head, "CACHE_MAX", 3)
    restraint_head.set_embedder(_embedder_for({}))
    for i in range(10):
        restraint_head.classes(f"sentence number {i}", embed=True)
    assert len(restraint_head._CACHE) == 3


# ── the measurement (needs the bge model on disk) ───────────────────────────────────────────────────────────────────────
def test_held_out_class_exact_with_the_real_embedding_is_measured_not_assumed(monkeypatch):
    """The number the ledger asks for. Skips (never passes) without the model. Floors are the first run on the held-out split, rounded down."""
    try:
        from fastembed import TextEmbedding
        model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        list(model.embed(["warm up"]))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"bge-small not available here: {exc!r}")
    spec = importlib.util.spec_from_file_location("restraint_eval", REPO / "scripts" / "perf" / "restraint_eval.py")
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)

    def embed(text):
        v = np.asarray(next(iter(model.embed([text]))), dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)
    held = ev.load_sets()["held_out"]
    restraint_head.set_embedder(embed)
    with_head = ev.score(held, lambda t: restraint.classify(t, head=True))
    without = ev.score(held, lambda t: restraint.classify(t, head=False))
    assert without["hit"] / without["n"] < 0.70                         # the pre-head word list (61.6 %)
    assert with_head["hit"] / with_head["n"] >= HELD_OUT_FLOOR, (with_head["hit"], with_head["n"])
    for c, (n, hit, _s) in with_head["per"].items():
        assert hit / n >= CLASS_FLOOR, (c, hit, n)
    # the head may add a false positive on a hard negative; it must not add many (the word list alone has WORD_LIST_FP)
    assert with_head["plain_fp"] <= without["plain_fp"] + 3, (with_head["plain_fp"], without["plain_fp"])
    # break-the-fix twin: the same held-out set with the head neutered is the word list.
    # ``set_embedder(None)`` means "back to the live semantic_router lookup", and in the full lane an earlier test has
    # already warmed that module's model, so the head stayed ACTIVE (268 hits, not the word list's 181): order-dependent.
    # Take the live lookup out of the picture so the twin means what it says in any order.
    restraint_head.set_embedder(None)
    monkeypatch.setattr(restraint_head, "_live_embedder", lambda: None)
    off = ev.score(held, lambda t: restraint.classify(t, head=True))
    assert off["hit"] == without["hit"]


HELD_OUT_FLOOR = 0.90      # measured 267..268 / 294 = 90.8..91.2 % on the held-out split: docs/knowledge/restraint.md "Measurement (semantic head)"
CLASS_FLOOR = 0.78         # the weakest held-out classes: other_member 80.7 %, health 84.6 %


# ── review round 1 (#1996): stamp honesty, English only, model identity ─────────────────────────────────────────────────
ROW = "Quinn keeps waking at three with a racing heart"
SPANISH = "Mi madre está muy enferma y el médico dice que necesita una operación"


def _router(monkeypatch, name="BAAI/bge-small-en-v1.5", vec=None):
    """A ``semantic_router`` with its model in memory (the live service after the first routed turn)."""
    import sys

    class _R:
        _MODEL = object()
        _MODEL_NAME = name
        embed = staticmethod(lambda text: _direction("health") if vec is None else vec)
    monkeypatch.setitem(sys.modules, "semantic_router", _R)
    return _R


def test_a_row_created_before_the_model_loads_is_not_stamped_head_complete(monkeypatch):
    """P1: a v3 stamp says "the head has seen this row"; row_classes trusts it forever, so it may only be written when the head ran."""
    import sys
    monkeypatch.setenv(restraint.ENV, "shadow")
    monkeypatch.delitem(sys.modules, "semantic_router", raising=False)        # the router has not loaded its model yet
    md: dict = {}
    restraint.stamp(md, ROW)
    assert md["sensitivity_v"] == restraint.LEXICON_VERSION != restraint.VERSION
    # a reader does not trust it: row_classes recomputes (cache-only head, nothing cached -> word list)
    assert restraint.row_classes(md, ROW) == restraint.classify(ROW, head=False)
    # the model loads; a later restamp of the same row queues the head, and the one after it is head-complete
    _router(monkeypatch)
    restraint.stamp(md, ROW)
    assert restraint_head.drain()
    restraint.stamp(md, ROW)
    assert md["sensitivity_v"] == restraint.VERSION and "health" in md["sensitivity"].split(",")
    assert "health" in restraint.row_classes(md, ROW)                         # ... and now it IS trusted


def test_a_stamp_where_the_head_ran_and_found_nothing_is_head_complete(monkeypatch):
    monkeypatch.setenv(restraint.ENV, "shadow")
    _router(monkeypatch, vec=np.zeros(384, dtype=np.float32))
    md: dict = {}
    restraint.stamp(md, PLAIN)
    assert restraint_head.drain()
    restraint.stamp(md, PLAIN)
    assert md["sensitivity"] == "" and md["sensitivity_v"] == restraint.VERSION


def test_head_settled_is_false_until_the_head_ran_and_true_for_a_language_it_never_serves(monkeypatch):
    import sys
    monkeypatch.delitem(sys.modules, "semantic_router", raising=False)
    restraint.classify(ROW, head=True)
    assert not restraint.head_settled(ROW)
    _router(monkeypatch)
    restraint.classify(ROW, head=True)
    assert restraint.head_settled(ROW)
    assert restraint.head_settled(SPANISH)                                    # nothing left for the head to do on Spanish


def test_the_english_head_is_not_applied_to_spanish_text(monkeypatch):
    import lexicons
    assert lexicons.detect(SPANISH) == "es"
    fn = _embedder_for({SPANISH: _direction("grief")})
    restraint_head.set_embedder(fn)
    assert restraint.classify_full(SPANISH, head=True) == restraint.classify_full(SPANISH, head=False)
    assert fn.calls == []                                                     # not even embedded
    # the stamp for a Spanish row is head-complete: there is nothing for the head to add, so it is not re-stamped forever
    monkeypatch.setenv(restraint.ENV, "shadow")
    md: dict = {}
    restraint.stamp(md, SPANISH)
    assert md["sensitivity_v"] == restraint.VERSION


# Codex P2 (#1996): ``detect_scored`` falls back to ("en", 0) for ANY language it has no word list for (Polish, Russian,
# Portuguese, ...) and for English with no function word, so "the pack says en" is not evidence that the text IS English.
@pytest.mark.parametrize("text", [
    "Moja mama jest chora i nie mogę spać",        # Polish: no word list, falls back to ("en", 0)
    "Мама больна, и я очень волнуюсь",              # Russian
    "A minha mãe está doente e não consigo dormir",  # Portuguese
    "Mi madre está muy enferma",                   # Spanish (positive evidence for es)
    "Ma mère est très malade",                     # French (positive evidence for fr)
])
def test_the_english_head_needs_positive_english_evidence(text, monkeypatch):
    import lexicons
    assert lexicons.detect(text) in ("en", "es", "fr")                        # the premise: several of these read as "en"
    fn = _embedder_for({text: _direction("grief")})
    restraint_head.set_embedder(fn)
    assert restraint.classify_full(text, head=True) == restraint.classify_full(text, head=False)
    assert fn.calls == []                                                     # not even embedded
    assert restraint.head_settled(text)                                       # nothing the head will ever add
    monkeypatch.setenv(restraint.ENV, "shadow")
    md: dict = {}
    restraint.stamp(md, text)
    assert md["sensitivity_v"] == restraint.VERSION                           # head-complete, never re-stamped forever


def test_the_head_still_serves_english_with_positive_evidence():
    """Negative control for the gate above: an English line carrying English-only markers (``she``, ``are``) is still head-classified."""
    text = "she is so ill and we are worried"
    import lexicons
    assert lexicons.detect_scored(text) == ("en", 3) or lexicons.detect_scored(text)[1] > 0
    restraint_head.set_embedder(_embedder_for({text: _direction("grief")}))
    classes, signals = restraint.classify_full(text, head=True)
    assert "grief" in classes and "grief:head" in signals


# #1996 enforce precondition: ``detect_scored`` only knows en/es/fr/de and its English list is is/to/in/my/no/me, so Dutch and
# Afrikaans read as ("en", 1+) on exactly the words they share with English. A hit count is not evidence of English.
NOT_ENGLISH = [
    "Mijn moeder is ziek en ik maak me zorgen",             # Dutch: detector says ("en", 1) on "is"
    "Ek is baie bekommerd oor my ma",                       # Afrikaans: ("en", 2) on "is", "my"
    "Het gaat niet goed met mijn vader in het ziekenhuis",  # Dutch ("in")
    "Mama is in het ziekenhuis, ik ben bang",               # Dutch ("is", "in")
    "Ek is bang my pa gaan sterf",                          # Afrikaans
    "Ze maakt zich zorgen over mij",                        # Dutch: "over" means "about", not an English marker
    "Hij heeft pijn in zijn been",                          # Dutch: "been" is a leg
    "Mijn oma's verjaardag is morgen",                      # Dutch plural 's, not an English contraction
    "Mijn nichtje heeft twee auto's gekocht",               # Dutch plural 's
    "Mir geht's schlecht seit dem Tod meiner Oma",          # German geht's is not an English contraction
    "Wir haben also kein Geld mehr",                        # German "also" means "so"
    "Meine Mutter ist krank und ich mache mir Sorgen",      # German
]


@pytest.mark.parametrize("text", NOT_ENGLISH)
def test_dutch_afrikaans_and_german_never_reach_the_english_head(text, monkeypatch):
    fn = _embedder_for({text: _direction("grief")})
    restraint_head.set_embedder(fn)
    assert not restraint._head_applies(text)
    assert restraint.classify_full(text, head=True) == restraint.classify_full(text, head=False)
    assert fn.calls == []                                                     # not even embedded
    monkeypatch.setenv(restraint.ENV, "shadow")
    md: dict = {}
    restraint.stamp(md, text)
    assert md["sensitivity_v"] == restraint.VERSION                           # head-complete: nothing the head will ever add


@pytest.mark.parametrize("text", [
    "My dad passed away in June",
    "User's wife died of cancer last year",
    "She's so ill and we are worried",
    "It's been a hard week for Quinn",
    "My husband wants a divorce",
    "I can't sleep and I'm scared",
    "They'll be at the funeral on Friday",
])
def test_english_with_an_english_only_word_or_contraction_still_reaches_the_head(text):
    """Negative control for the gate above: dropping the weak fallback must not shut the head off for English."""
    assert restraint._head_applies(text)
    restraint_head.set_embedder(_embedder_for({text: _direction("grief")}))
    classes, _signals = restraint.classify_full(text, head=True)
    assert "grief" in classes


def test_the_old_weak_fallback_is_what_let_dutch_through_break_the_fix(monkeypatch):
    """With ``hits > 0`` restored as evidence the Dutch line reaches the head again, so the test above is not vacuous."""
    import lexicons
    text = "Mijn moeder is ziek en ik maak me zorgen"
    assert lexicons.detect_scored(text) == ("en", 1)
    assert not restraint._head_applies(text)
    real = restraint._head_applies
    monkeypatch.setattr(restraint, "_head_applies",
                        lambda t: real(t) or (lexicons.detect_scored(str(t))[0] == "en" and lexicons.detect_scored(str(t))[1] > 0))
    restraint_head.set_embedder(_embedder_for({text: _direction("grief")}))
    assert "grief" in restraint.classify(text, head=True)                     # the leak, reproduced


# ── the deferred queue survives a router whose model is not loaded yet ────────────────────────────────────────────────
def _no_router(monkeypatch):
    import sys
    monkeypatch.delitem(sys.modules, "semantic_router", raising=False)


def _wait_for(cond, timeout=5.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_a_text_deferred_before_the_model_loads_is_parked_and_classified_once_it_does(monkeypatch):
    """RED before: the worker's embed found no model, returned None and the text was gone, so the row kept lexicon classes forever."""
    _no_router(monkeypatch)
    assert restraint_head.defer(ROW)
    assert restraint_head.drain()
    assert restraint_head.pending() == 1
    assert restraint_head.lookup(ROW) is None and restraint_head.retry_pending() == 0   # model still absent: nothing to retry yet
    _router(monkeypatch)
    assert restraint_head.retry_pending() == 1
    assert restraint_head.drain()
    assert "health" in (restraint_head.lookup(ROW) or ())
    assert restraint_head.pending() == 0


def test_the_worker_retries_by_itself_without_anyone_calling_retry(monkeypatch):
    monkeypatch.setattr(restraint_head, "RETRY_S", 0.05)
    restraint_head.reset()                                                     # wakes a worker still waiting on the old interval
    _no_router(monkeypatch)
    assert restraint_head.defer(ROW)
    assert _wait_for(lambda: restraint_head.pending() == 1)
    _router(monkeypatch)
    assert _wait_for(lambda: restraint_head.lookup(ROW) is not None), "the worker never retried the parked text"
    assert "health" in restraint_head.lookup(ROW)
    assert restraint_head.pending() == 0


def test_a_later_defer_is_also_a_retry_trigger(monkeypatch):
    _no_router(monkeypatch)
    restraint_head.defer(ROW)
    assert restraint_head.drain() and restraint_head.pending() == 1
    _router(monkeypatch)
    restraint_head.defer("Wren has a new standing desk")
    assert _wait_for(lambda: restraint_head.lookup(ROW) is not None)
    assert restraint_head.drain()


def test_a_row_stamped_before_the_model_loads_is_head_complete_after_it_without_a_second_stamp(monkeypatch):
    monkeypatch.setenv(restraint.ENV, "shadow")
    _no_router(monkeypatch)
    md: dict = {}
    restraint.stamp(md, ROW)
    assert md["sensitivity_v"] == restraint.LEXICON_VERSION
    assert restraint_head.drain()
    _router(monkeypatch)
    restraint_head.retry_pending()
    assert restraint_head.drain()
    assert "health" in restraint.row_classes(md, ROW)                          # the reader sees the head, no restamp needed
    restraint.stamp(md, ROW)
    assert md["sensitivity_v"] == restraint.VERSION and "health" in md["sensitivity"].split(",")


def test_the_parked_set_is_bounded_and_keeps_the_newest(monkeypatch):
    monkeypatch.setattr(restraint_head, "PENDING_MAX", 3)
    _no_router(monkeypatch)
    for i in range(5):
        restraint_head.defer(f"parked text {i}")
    assert restraint_head.drain()
    assert restraint_head.pending() == 3
    assert restraint_head._PENDING_DROPPED == 2
    assert list(restraint_head._PENDING) == ["parked text 2", "parked text 3", "parked text 4"]


def test_a_model_name_mismatch_is_not_parked_because_retrying_cannot_fix_it(monkeypatch):
    _router(monkeypatch, name="some/other-384-model")
    restraint_head.defer(ROW)
    assert restraint_head.drain()
    assert restraint_head.pending() == 0 and restraint_head.lookup(ROW) is None


def test_without_the_parking_the_text_is_lost_break_the_fix(monkeypatch):
    """Negative control: neuter ``_park`` (the old behaviour) and the same sequence leaves the row without the head."""
    monkeypatch.setattr(restraint_head, "_park", lambda raw: None)
    _no_router(monkeypatch)
    restraint_head.defer(ROW)
    assert restraint_head.drain()
    _router(monkeypatch)
    assert restraint_head.retry_pending() == 0
    assert restraint_head.drain()
    assert restraint_head.lookup(ROW) is None


def test_a_router_running_a_different_embedding_model_disables_the_head_with_one_log_line(monkeypatch, caplog):
    import logging
    _router(monkeypatch, name="some/other-384-model")
    with caplog.at_level(logging.WARNING, logger="restraint_head"):
        assert restraint_head._live_embedder() is None
        assert restraint_head.lookup(ROW, embed=True) is None
        assert restraint.classify(ROW, head=True) == restraint.classify(ROW, head=False)
        assert restraint_head._live_embedder() is None
    lines = [r for r in caplog.records if "restraint head disabled" in r.getMessage()]
    assert len(lines) == 1                                                    # one line, not one per row
    # the matching name works (twin)
    restraint_head.reset()
    _router(monkeypatch)
    assert restraint_head.lookup(ROW, embed=True) is not None


def test_a_router_that_does_not_name_its_model_is_not_trusted(monkeypatch):
    import sys

    class _Anon:
        _MODEL = object()
        embed = staticmethod(lambda text: _direction("health"))
    monkeypatch.setitem(sys.modules, "semantic_router", _Anon)
    assert restraint_head._live_embedder() is None


# ── the head never embeds on a turn or on the event loop (2026-10-12: stamp stalled the loop 11 ms median, 32 ms worst) ──────
def _thread_recording_embedder(vec):
    import threading
    seen: list = []

    def fn(_text):
        seen.append(threading.get_ident())
        return vec
    fn.seen = seen
    return fn


def test_stamp_never_embeds_on_the_calling_thread_but_the_worker_does(monkeypatch):
    """RED if ``stamp`` embeds inline again: the embedder would then run on the test's own thread."""
    import threading
    monkeypatch.setenv(restraint.ENV, "shadow")
    fn = _thread_recording_embedder(_direction("health"))
    restraint_head.set_embedder(fn)
    md: dict = {}
    restraint.stamp(md, ROW)
    assert threading.get_ident() not in fn.seen                                # nothing embedded inline
    assert restraint_head.drain()
    assert len(fn.seen) == 1 and fn.seen[0] != threading.get_ident()           # the worker did it, once, off this thread
    assert "health" in restraint.classify(ROW)                                 # and a reader now sees the head from the cache


def test_stamp_costs_a_queue_put_not_an_embedding(monkeypatch):
    import time
    monkeypatch.setenv(restraint.ENV, "shadow")

    def slow(_text):
        time.sleep(0.2)
        return np.zeros(384, dtype=np.float32)
    restraint_head.set_embedder(slow)
    t = time.perf_counter()
    restraint.stamp({}, ROW)
    assert time.perf_counter() - t < 0.1                                       # a 200 ms embedder is not waited for
    restraint_head.reset()


def test_the_defer_queue_is_bounded_and_never_blocks(monkeypatch):
    import threading
    gate = threading.Event()
    restraint_head.set_embedder(lambda _t: gate.wait(5) and np.zeros(384, dtype=np.float32))
    queued = [restraint_head.defer(f"text number {i}") for i in range(restraint_head.DEFER_MAX + 50)]
    assert not all(queued) and any(queued)                                     # past the bound a text is dropped, not waited on
    gate.set()
    assert restraint_head.drain(10)


@pytest.mark.asyncio
async def test_the_nightly_thread_pass_embeds_off_the_event_loop(monkeypatch):
    """RED if ``store_thread_classes`` embeds on the loop: the embedder would run on the loop's own thread."""
    import threading
    loop_thread = threading.get_ident()
    fn = _thread_recording_embedder(_direction("health"))
    restraint_head.set_embedder(fn)
    monkeypatch.setenv(restraint.ENV, "shadow")

    class _Cur:
        def __init__(self, rows=()):
            self._rows = rows

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def fetchall(self):
            return list(self._rows)

        async def _self(self):
            return self

        def __await__(self):
            return self._self().__await__()

    class _Db:
        def execute(self, *_a, **_k):
            return _Cur()

    n = await restraint.store_thread_classes(_Db(), "member-a", [("open_loops:1", ROW, "open_loop")])
    assert n == 1
    assert fn.seen and loop_thread not in fn.seen                              # embedded, and never on the event loop's thread


def test_eval_json_mode_writes_only_json_to_stdout(monkeypatch, capsys):
    """Codex (#1996): ``--json`` must leave stdout one parseable document; the human status line goes to stderr."""
    spec = importlib.util.spec_from_file_location("restraint_eval_json", REPO / "scripts" / "perf" / "restraint_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr("sys.argv", ["restraint_eval.py", "--no-head", "--json"])
    assert mod.main() == 0
    out = capsys.readouterr()
    assert "lexicon only / dev" in json.loads(out.out)
    assert out.err.startswith("head:")


# ── isolation: a router warmed by an earlier test is process state, not a property of this test ────────────────────────
def test_a_warm_router_makes_the_head_live_unless_the_test_hides_it(monkeypatch):
    """The negative control for the held-out twin above: with a router already warm (what a full-lane run has by now), a
    test that merely drops its injected embedder still has a live head. The isolation must hide the live lookup."""
    import sys
    import types

    head = restraint_head._load()
    router = types.SimpleNamespace(_MODEL=object(), _MODEL_NAME=head[4]["embedder"],
                                   embed=lambda text: np.zeros(384, dtype=np.float32))
    monkeypatch.setitem(sys.modules, "semantic_router", router)
    restraint_head.set_embedder(None)
    assert restraint_head._live_embedder() is router.embed                  # the leak: no injected embedder, head still on
    monkeypatch.setattr(restraint_head, "_live_embedder", lambda: restraint_head._EMBEDDER)
    assert restraint_head._live_embedder() is None                          # the isolation: now it is the word list
