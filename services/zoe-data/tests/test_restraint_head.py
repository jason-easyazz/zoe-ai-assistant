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


def test_the_head_was_trained_on_exactly_the_current_dev_split_and_never_on_held_out():
    """Stale-artefact guard: change the dev data and the head must be retrained (train_restraint_head.py)."""
    import hashlib
    spec = importlib.util.spec_from_file_location("restraint_eval", REPO / "scripts" / "perf" / "restraint_eval.py")
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    sets = ev.load_sets()
    dev_texts = sorted(t for _w, t in sets["dev"])
    sha = hashlib.sha256("\n".join(dev_texts).encode("utf-8")).hexdigest()
    meta = restraint_head._load()[4]
    assert meta["train_sha"] == sha and meta["n_train"] == len(dev_texts)
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


def test_stamp_embeds_and_records_the_head_class_in_the_row_metadata(monkeypatch):
    monkeypatch.setenv(restraint.ENV, "shadow")
    text = "Quinn keeps waking at three with a racing heart"
    restraint_head.set_embedder(_embedder_for({text: _direction("health")}))
    md: dict = {}
    restraint.stamp(md, text)
    assert "health" in md["sensitivity"].split(",") and md["sensitivity_v"] == restraint.VERSION
    # break-the-fix twin: no embedder -> the stamp is the word list's (here: nothing for this sentence)
    restraint_head.set_embedder(None)
    md2: dict = {}
    restraint.stamp(md2, text)
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
def test_held_out_class_exact_with_the_real_embedding_is_measured_not_assumed():
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
    # break-the-fix twin: the same held-out set with the head neutered is the word list
    restraint_head.set_embedder(None)
    off = ev.score(held, lambda t: restraint.classify(t, head=True))
    assert off["hit"] == without["hit"]


HELD_OUT_FLOOR = 0.90      # measured 263..268 / 294 = 91.2 % on the held-out split: docs/knowledge/restraint.md "Measurement (semantic head)"
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
    # the model loads; a later restamp of the same row applies the head and is head-complete
    _router(monkeypatch)
    restraint.stamp(md, ROW)
    assert md["sensitivity_v"] == restraint.VERSION and "health" in md["sensitivity"].split(",")
    assert "health" in restraint.row_classes(md, ROW)                         # ... and now it IS trusted


def test_a_stamp_where_the_head_ran_and_found_nothing_is_head_complete(monkeypatch):
    monkeypatch.setenv(restraint.ENV, "shadow")
    _router(monkeypatch, vec=np.zeros(384, dtype=np.float32))
    md: dict = {}
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
