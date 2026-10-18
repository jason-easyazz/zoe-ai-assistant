"""The memory writer map is GENERATED from code and a new writer cannot appear without a map entry (deep dive B8).

REPO rules: the committed map equals a fresh generation, every writer module has a role note, and the count never
falls below the deep dive's floor (39; the 10-05 audit's hand count of 31 was stale). NEGATIVE CONTROLS run the same
detector on synthetic modules so each rule is proven able to fail.

When this goes red: `python3 tools/audit/memory_writer_map.py` regenerates the doc; add a role line for a new module to
`docs/knowledge/memory-writer-notes.json` (say what triggers the write and whose words it stores).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("memory_writer_map", _REPO / "tools" / "audit" / "memory_writer_map.py")
mwm = importlib.util.module_from_spec(_spec)
sys.modules["memory_writer_map"] = mwm
_spec.loader.exec_module(mwm)


@pytest.fixture(scope="module")
def writers() -> dict:
    return mwm.collect(_REPO)


# ---- the real tree ---------------------------------------------------------------------------------

def test_every_writer_module_has_a_role_note(writers):
    gaps = mwm.missing_notes(writers, mwm.load_notes(_REPO))
    assert not gaps, (
        f"new memory writer module(s) with no role line: {gaps}. Add one each to {mwm.NOTES_REL} "
        "(what triggers the write, whose words it stores), then run python3 tools/audit/memory_writer_map.py."
    )


def test_committed_map_matches_the_code(writers):
    fresh = mwm.render(writers, mwm.load_notes(_REPO))
    committed = (_REPO / mwm.MAP_REL).read_text(encoding="utf-8")
    assert fresh == committed, f"{mwm.MAP_REL} is stale - rerun: python3 tools/audit/memory_writer_map.py"


def test_the_map_finds_at_least_the_39_writers_the_deep_dive_counted(writers):
    assert mwm.count_sites(writers) >= 39, mwm.count_sites(writers)
    # the known anchors: the per-turn extractor, the nightly digest, the brain tool, the storage funnel itself
    for anchor in ("memory_extractor.py", "memory_digest.py", "zoe_agent.py", "memory_service.py", "routers/memories.py"):
        assert f"services/zoe-data/{anchor}" in writers, anchor


def test_no_stale_notes(writers):
    assert mwm.stale_notes(writers, mwm.load_notes(_REPO)) == [], "notes for modules that no longer write: delete them"


# ---- negative controls on synthetic modules -----------------------------------------------------------

def _repo(tmp_path: Path, **modules: str) -> tuple[Path, list[str]]:
    d = tmp_path / "services" / "zoe-data"
    d.mkdir(parents=True)
    rels = []
    for name, src in modules.items():
        (d / name).write_text(src)
        rels.append(f"services/zoe-data/{name}")
    return tmp_path, rels


def test_control_a_new_ingest_caller_is_a_writer_and_needs_a_note(tmp_path):
    repo, files = _repo(
        tmp_path,
        **{"brand_new.py": "async def remember(svc):\n    await svc.ingest('x', user_id='u')\n"},
    )
    w = mwm.collect(repo, files)
    assert w == {"services/zoe-data/brand_new.py": {"remember": ["ingest"]}}
    assert mwm.missing_notes(w, {}) == ["services/zoe-data/brand_new.py"]  # fails the CI rule
    assert mwm.missing_notes(w, {"services/zoe-data/brand_new.py": "Stores a thing the member said, on request."}) == []


def test_control_an_aliased_receiver_cannot_hide_a_unique_method(tmp_path):
    repo, files = _repo(
        tmp_path,
        **{"sneaky.py": "def f(x):\n    return x.helper.record_candidate('t')\nasync def g(m):\n    await m.supersede_by('a', 'b')\n"},
    )
    assert mwm.collect(repo, files)["services/zoe-data/sneaky.py"] == {"f": ["record_candidate"], "g": ["supersede_by"]}


def test_control_generic_names_only_count_on_a_memory_receiver(tmp_path):
    repo, files = _repo(
        tmp_path,
        **{"other.py": "def a(pr):\n    pr.review('x')\n    queue.delete_user('u')\n",
           "mem.py": "async def b(svc):\n    await svc.review('i', decision='reject')\n    await get_memory_service().delete_user('u')\n"},
    )
    w = mwm.collect(repo, files)
    assert "services/zoe-data/other.py" not in w
    assert w["services/zoe-data/mem.py"] == {"b": ["delete_user", "review"]}


def test_control_a_raw_collection_upsert_is_a_writer_but_other_stores_are_not(tmp_path):
    repo, files = _repo(
        tmp_path,
        **{"raw.py": "def f(col):\n    col.upsert(ids=['a'])\ndef g(store):\n    store.upsert('u', 'v')\n"},
    )
    assert mwm.collect(repo, files)["services/zoe-data/raw.py"] == {"f": ["raw_upsert"]}


def test_control_the_funnel_counts_only_its_own_raw_writes(tmp_path):
    repo, files = _repo(
        tmp_path,
        **{"memory_service.py": "class MemoryService:\n    def _write_row(self, col):\n        col.upsert(ids=[1])\n    async def forget(self):\n        await self.review('x')\n        await self.ingest('y')\n"},
    )
    assert mwm.collect(repo, files)["services/zoe-data/memory_service.py"] == {"MemoryService._write_row": ["raw_upsert"]}


def test_control_test_files_are_not_writers(tmp_path):
    repo, files = _repo(tmp_path, **{"test_thing.py": "async def t(svc):\n    await svc.ingest('x')\n"})
    assert mwm.collect(repo, files) == {}


def test_control_a_stale_note_is_reported(tmp_path):
    assert mwm.stale_notes({}, {"services/zoe-data/gone.py": "Used to write memory, no longer does."}) == ["services/zoe-data/gone.py"]
