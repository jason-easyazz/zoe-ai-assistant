"""Which corpus samples the replay gate actually replays.

``services/zoe-data/tests/replay_samples.py::_select`` is the SSOT behind
``voice_regression_probe.py --samples`` and ``scripts/perf/measure_voice.py
--last``, so it decides what every voice-gate verdict MEANS. It used to sort by
FILENAME — and corpus names are ``HHMMSS_millis.wav``, a time of day with no
date — so ``--last N``, documented everywhere as "newest N", returned the N
highest times-of-day across the whole corpus. Measured 2026-08-04 on the live
1003-file corpus: the name-sorted last 20 and the capture-time-sorted last 20
shared **zero** files, and half the name-sorted slice was 2026-06-20, the oldest
capture day there is.

The fixtures below build a corpus where NAME order and CAPTURE order are exactly
reversed, so a revert to ``sorted(glob.glob(...))`` cannot leave these green —
that inversion is the negative control, asserted explicitly in
``test_name_order_and_capture_order_disagree_in_the_fixture``.

``_select`` is extracted with ``ast`` and exec'd in an isolated namespace rather
than imported: importing ``replay_samples`` executes module-level ``os.chdir``,
a ``sys.path`` mutation and ``from zoe_flue_client import …``, none of which
belong in a ``ci_safe`` unit lane. The namespace is deliberately just
``{glob, os}`` — the same shape ``tests/unit/test_curate_voice_corpus.py`` uses
— so this doubles as a pin that ``_select`` never grows a heavier dependency.
"""
from __future__ import annotations

import ast
import builtins
import glob as _glob
import os
import types

import pytest

pytestmark = pytest.mark.ci_safe

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPLAY_PATH = os.path.join(REPO_ROOT, "services", "zoe-data", "tests", "replay_samples.py")


def _extract_select():
    """Exec the REAL ``_select`` from replay_samples.py in isolation."""
    with open(REPLAY_PATH, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_select")
    ns: dict = {"glob": _glob, "os": os}
    exec(compile(ast.Module(body=[fn], type_ignores=[]),
                 "replay_samples._select", "exec"), ns)
    return ns["_select"]


SELECT = _extract_select()


def _args(**kw):
    base = {"since": None, "last": None}
    base.update(kw)
    return types.SimpleNamespace(**base)


def _wav(directory, name: str, mtime: float) -> str:
    """A corpus member: real (empty) file at a controlled capture time."""
    path = os.path.join(directory, name)
    with open(path, "wb") as fh:
        fh.write(b"RIFF")
    os.utime(path, (mtime, mtime))
    return path


# Name order and capture order are exact opposites here: the file that sorts
# FIRST by name was captured LAST, and vice versa.
#   name    : 080000, 090000, 235000, 235959
#   capture : 235959, 235000, 090000, 080000
DAY = 86400.0
BASE = 1780000000.0
FIXTURE = [
    ("235959_000.wav", BASE + 0 * DAY),   # oldest capture, sorts last by name
    ("235000_000.wav", BASE + 1 * DAY),
    ("090000_000.wav", BASE + 2 * DAY),
    ("080000_000.wav", BASE + 3 * DAY),   # newest capture, sorts first by name
]


@pytest.fixture()
def corpus(tmp_path):
    d = tmp_path / "samples"
    d.mkdir()
    for name, mtime in FIXTURE:
        _wav(str(d), name, mtime)
    return str(d)


def _names(paths) -> list[str]:
    return [os.path.basename(p) for p in paths]


# ── the negative control ──────────────────────────────────────────────────────

def test_name_order_and_capture_order_disagree_in_the_fixture(corpus):
    """Guard the guard: if the fixture ever stops inverting the two orders, every
    assertion below would pass under the old name-sorting code too."""
    by_name = sorted(_names(_glob.glob(os.path.join(corpus, "*.wav"))))
    by_capture = [n for n, _ in sorted(FIXTURE, key=lambda r: r[1])]
    assert by_name == list(reversed(by_capture)), (
        "fixture no longer distinguishes name order from capture order")
    assert set(by_name[-2:]).isdisjoint(by_capture[-2:]), (
        "the two 'last 2' slices must share nothing, mirroring the live 0/20 overlap")


# ── the fix ───────────────────────────────────────────────────────────────────

def test_selection_is_ordered_newest_last_by_capture_time(corpus):
    assert _names(SELECT(corpus, _args())) == [
        "235959_000.wav", "235000_000.wav", "090000_000.wav", "080000_000.wav"]


def test_last_n_returns_the_newest_by_capture_time_not_by_name(corpus):
    # Under the old name sort this was ["235000_000.wav", "235959_000.wav"].
    assert _names(SELECT(corpus, _args(last=2))) == [
        "090000_000.wav", "080000_000.wav"]


def test_last_one_is_the_most_recent_capture(corpus):
    assert _names(SELECT(corpus, _args(last=1))) == ["080000_000.wav"]


def test_last_larger_than_the_corpus_returns_everything(corpus):
    assert len(SELECT(corpus, _args(last=99))) == len(FIXTURE)


def test_ties_break_on_name_for_a_total_reproducible_order(tmp_path):
    """Two captures in the same mtime tick must still order deterministically."""
    d = tmp_path / "samples"
    d.mkdir()
    for name in ("120002_000.wav", "120000_000.wav", "120001_000.wav"):
        _wav(str(d), name, BASE)
    assert _names(SELECT(str(d), _args())) == [
        "120000_000.wav", "120001_000.wav", "120002_000.wav"]
    assert _names(SELECT(str(d), _args(last=1))) == ["120002_000.wav"]


# ── quarantine exclusion (the property curation depends on) ───────────────────

def test_quarantine_subdirs_are_never_selected(corpus):
    """Curation quarantines by MOVING into a dated subdir. That is only safe
    while selection globs the top level non-recursively."""
    q = os.path.join(corpus, "quarantine-nonspeech-20260804")
    os.mkdir(q)
    # Newest capture in the tree AND first by name — it would win under either
    # ordering if the glob ever went recursive.
    _wav(q, "000001_000.wav", BASE + 99 * DAY)

    picked = _names(SELECT(corpus, _args()))
    assert "000001_000.wav" not in picked
    assert len(picked) == len(FIXTURE)
    assert "000001_000.wav" not in _names(SELECT(corpus, _args(last=2)))


def test_non_wav_files_are_ignored(corpus):
    with open(os.path.join(corpus, "manifest.json"), "w") as fh:
        fh.write("{}")
    assert len(SELECT(corpus, _args())) == len(FIXTURE)


def test_empty_corpus_selects_nothing(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert SELECT(str(d), _args()) == []
    assert SELECT(str(d), _args(last=5)) == []


# ── --since keeps its honest NAME semantics ───────────────────────────────────

def test_since_filters_by_filename_not_by_capture_time(corpus):
    """--since was always name-based and its help said so. It stays name-based:
    silently redefining it would be the same bug in the other direction."""
    picked = _names(SELECT(corpus, _args(since="0900")))
    # Name-wise 090000/235000/235959 sort >= "0900"; 080000 does not — even
    # though 080000 is the NEWEST capture in the tree.
    assert picked == ["235959_000.wav", "235000_000.wav", "090000_000.wav"]


def test_since_result_is_still_ordered_by_capture_time(corpus):
    picked = _names(SELECT(corpus, _args(since="0900", last=1)))
    assert picked == ["090000_000.wav"]


# ── --since-date is the capture-time counterpart ──────────────────────────────

def test_since_date_filters_by_capture_time(corpus):
    picked = _names(SELECT(corpus, _args(since_mtime=BASE + 2 * DAY)))
    assert picked == ["090000_000.wav", "080000_000.wav"]


def test_since_date_is_inclusive_of_the_boundary(corpus):
    picked = _names(SELECT(corpus, _args(since_mtime=BASE + 3 * DAY)))
    assert picked == ["080000_000.wav"]


def test_absent_since_mtime_attribute_is_tolerated(corpus):
    """Callers built before --since-date pass a namespace without the attribute;
    _select must read it defensively (the corpus-curation suite does exactly
    this when it exec's _select)."""
    legacy = types.SimpleNamespace(since=None, last=2)
    assert not hasattr(legacy, "since_mtime")
    assert _names(SELECT(corpus, legacy)) == ["090000_000.wav", "080000_000.wav"]


def test_since_and_since_date_compose(corpus):
    picked = _names(SELECT(corpus, _args(since="0900", since_mtime=BASE + 1 * DAY)))
    assert picked == ["235000_000.wav", "090000_000.wav"]


# ── the dependency pin ────────────────────────────────────────────────────────

def test_select_needs_only_glob_and_os(corpus):
    """_select is exec'd here (and in the corpus-curation suite) in a namespace
    of exactly {glob, os}. Every test above runs through that namespace, so a
    new import inside _select would NameError. This asserts it head-on."""
    with open(REPLAY_PATH, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_select")
    bound = {a.arg for a in fn.args.args}
    loaded = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            pytest.fail("_select must not import: it is exec'd in an isolated namespace")
        if isinstance(node, ast.Name):
            (loaded if isinstance(node.ctx, ast.Load) else bound).add(node.id)
        elif isinstance(node, ast.comprehension):
            for tgt in ast.walk(node.target):
                if isinstance(tgt, ast.Name):
                    bound.add(tgt.id)

    free = loaded - bound - set(dir(builtins))
    assert free == {"glob", "os"}, f"_select gained a new global dependency: {free}"
