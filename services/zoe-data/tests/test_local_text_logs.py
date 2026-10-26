"""local_text_logs: retention, the forget cascade step and the member erase for the local utterance-text logs (deep dive A6).

The router shadow log kept ~10.8k raw utterances beside the hash-keyed record; nothing aged them out and nothing reached them on a
forget. These tests use throwaway logs (the conftest pins the live ones off) and each carries its break-the-fix control in its docstring.
"""
from __future__ import annotations

import json
import re
import stat

import pytest

import local_text_logs as ltl
import memory_forget_cascade as cascade
import memory_service
from memory_service import MemoryService, MemoryServiceError

pytestmark = pytest.mark.ci_safe

DAY = 86400.0
NOW = 2_000_000_000.0          # an injected clock; nothing here reads the real one


def rec(utt: str, text: str | None, age_days: float, **extra) -> str:
    r = {"ts": NOW - age_days * DAY, "utt": utt, "head_conf": 0.9, "mode": "shadow2", "actual_routed": "chat", **extra}
    if text is not None:
        r["utt_text"] = text
    return json.dumps(r, sort_keys=True)


def write(path, *lines: str, mode: int = 0o640):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    path.chmod(mode)
    return path


def load(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


@pytest.fixture
def shadow(tmp_path, monkeypatch):
    p = tmp_path / "data" / "router_head_shadow.jsonl"
    monkeypatch.setenv("ZOE_ROUTER_HEAD_LOG", str(p))
    monkeypatch.setenv("ZOE_INTENT_MISS_LOG", str(tmp_path / "misses.jsonl"))
    monkeypatch.setenv("ZOE_ADHOC_BACKUP_DIR", str(tmp_path / "adhoc"))
    monkeypatch.delenv("ZOE_TEXT_LOG_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("ZOE_ROUTER_SHADOW_KEEP", raising=False)
    return p


def test_retention_strips_old_text_keeps_hash_and_features(shadow):
    write(shadow, rec("a" * 12, "turn the lamp on", 45), rec("b" * 12, "what is the weather", 2), rec("c" * 12, None, 90))
    assert ltl.sweep(NOW) == {"adhoc_backups": 0, "router_shadow": 1, "intent_misses": 0}
    old, fresh, hashonly = load(shadow)
    assert "utt_text" not in old and old["utt"] == "a" * 12 and old["head_conf"] == 0.9 and old["actual_routed"] == "chat"
    assert fresh["utt_text"] == "what is the weather"                 # inside the window: untouched
    assert hashonly["utt"] == "c" * 12                                 # a hash-only row is never dropped by age
    assert "turn the lamp on" not in shadow.read_text()


def test_the_miner_still_gets_the_record_after_a_strip(shadow):
    """The self-train loop joins on the hash when ``utt_text`` is gone (labs/router-selftrain), so a stripped record must stay
    mineable: mode, hashes and routing fields intact."""
    write(shadow, rec("d" * 12, "play some jazz", 60, two_stage_domain="music", gated=False, failed=False, shortlist=["music"]))
    ltl.sweep(NOW)
    (r,) = load(shadow)
    assert r["utt"] == "d" * 12 and r["mode"] == "shadow2" and r["two_stage_domain"] == "music" and r["shortlist"] == ["music"]


def test_rotated_segments_are_swept_too(shadow):
    write(shadow, rec("a" * 12, "fresh", 1))
    seg = write(shadow.with_name(shadow.name + ".2"), rec("b" * 12, "old words in a rotated segment", 100))
    ltl.sweep(NOW)
    assert "old words" not in seg.read_text() and "fresh" in shadow.read_text()


def test_file_mode_is_kept_and_no_temp_litter(shadow):
    write(shadow, rec("a" * 12, "old", 99), mode=0o600)
    ltl.sweep(NOW)
    assert stat.S_IMODE(shadow.stat().st_mode) == 0o600
    assert sorted(p.name for p in shadow.parent.iterdir()) == [shadow.name, shadow.name + ".lock"]     # the lock file, and no .tmp litter


def test_a_noop_sweep_does_not_touch_the_file(shadow):
    write(shadow, rec("a" * 12, "fresh", 1))
    before = shadow.stat().st_mtime_ns
    assert ltl.sweep(NOW)["router_shadow"] == 0
    assert shadow.stat().st_mtime_ns == before


def test_retention_env_parsing(monkeypatch):
    monkeypatch.delenv("ZOE_TEXT_LOG_RETENTION_DAYS", raising=False)
    assert ltl.retention_days() == 30.0
    for raw, want in (("7", 7.0), ("0", 0.0), ("keep", None), ("FOREVER", None), ("-3", 0.0), ("garbage", 30.0)):
        monkeypatch.setenv("ZOE_TEXT_LOG_RETENTION_DAYS", raw)
        assert ltl.retention_days() == want, raw


def test_keep_disables_the_sweep_and_zero_keeps_nothing(shadow, monkeypatch):
    write(shadow, rec("a" * 12, "old", 400), rec("b" * 12, "fresh", 0))
    monkeypatch.setenv("ZOE_TEXT_LOG_RETENTION_DAYS", "keep")
    assert ltl.sweep(NOW) == {"adhoc_backups": 0} and "old" in shadow.read_text()
    monkeypatch.setenv("ZOE_TEXT_LOG_RETENTION_DAYS", "0")
    ltl.sweep(NOW + 1)
    assert "utt_text" not in shadow.read_text()


def test_intent_misses_are_dropped_by_age_not_stripped(shadow, tmp_path):
    misses = write(tmp_path / "misses.jsonl", json.dumps({"text": "old miss", "ts": NOW - 40 * DAY}),
                   json.dumps({"text": "new miss", "ts": NOW - DAY}))
    assert ltl.sweep(NOW)["intent_misses"] == 1
    assert [r["text"] for r in load(misses)] == ["new miss"]


def test_entity_erase_drops_the_record_whole_hash_included(shadow):
    import re
    write(shadow, rec("a" * 12, "remind me to call Marisol tomorrow", 1), rec("b" * 12, "what time is it", 1), rec("c" * 12, None, 1))
    assert ltl.erase_entity(re.compile(r"\bMarisol\b")) == 1
    utts = [r["utt"] for r in load(shadow)]
    assert utts == ["b" * 12, "c" * 12] and "Marisol" not in shadow.read_text()


def test_erase_all_text_member_erase_keeps_hash_only_rows(shadow, tmp_path):
    misses = write(tmp_path / "misses.jsonl", json.dumps({"text": "x", "ts": NOW}))
    write(shadow, rec("a" * 12, "words", 1), rec("c" * 12, None, 1))
    ltl.erase_all_text()
    assert [r["utt"] for r in load(shadow)] == ["a" * 12, "c" * 12] and "words" not in shadow.read_text()
    assert misses.read_text() == ""


def test_lines_appended_during_the_rewrite_survive(shadow):
    write(shadow, rec("a" * 12, "old", 99))
    state = {"done": False}

    def strip_and_append(line):
        if not state["done"]:                                    # the writer lands a line while we are mid-rewrite
            state["done"] = True
            with open(shadow, "a") as fh:
                fh.write(rec("z" * 12, "arrived mid-rewrite", 0) + "\n")
        return ltl._strip_or_drop(ltl.ROUTER_SHADOW, line, older_than=NOW - 30 * DAY)

    assert ltl.rewrite(shadow, strip_and_append) == 1
    assert [r["utt"] for r in load(shadow)] == ["a" * 12, "z" * 12]


def test_malformed_rows_are_dropped_by_every_pass_and_missing_files_are_fine(shadow):
    """Forget fails closed (codex round 3 on #2110). Break: return the line unchanged for an unparseable row again -> the word in a
    torn / hand-edited row survives the retention sweep, an entity erasure and a member erase."""
    shadow.parent.mkdir(parents=True)
    torn = '{"utt": "zzzzzzzzzzzz", "utt_text": "ring Marisol about'          # a torn append: not JSON, still holds a name
    shadow.write_text(torn + "\nnot json at all\n" + rec("a" * 12, "old", 99) + "\n" + rec("b" * 12, "fresh", 1) + "\n")
    assert ltl.sweep(NOW)["router_shadow"] == 3                                # 2 malformed dropped + 1 aged-out text stripped
    assert [r["utt"] for r in load(shadow)] == ["a" * 12, "b" * 12] and "Marisol" not in shadow.read_text()
    assert ltl.count_malformed(ltl.ROUTER_SHADOW) == 0

    # entity erasure: a malformed row holding the forgotten word is gone even though no record can be matched to it
    write(shadow, rec("a" * 12, "fine", 1), 'garbage ring Marisol {"utt"')
    assert ltl.erase_entity(re.compile(r"\bMarisol\b")) == 1
    assert "Marisol" not in shadow.read_text() and [r["utt"] for r in load(shadow)] == ["a" * 12]

    # member erase
    write(shadow, rec("a" * 12, "words", 1), "oops: words")
    assert ltl.count_malformed(ltl.ROUTER_SHADOW) == 1
    assert ltl.erase_all_text() == 2
    assert "words" not in shadow.read_text() and [r["utt"] for r in load(shadow)] == ["a" * 12]

    shadow.unlink()
    assert ltl.sweep(NOW) == {"adhoc_backups": 0, "router_shadow": 0, "intent_misses": 0} and ltl.erase_all_text() == 0


def test_a_json_row_that_is_not_an_object_is_malformed_too(shadow):
    write(shadow, '"just a string with Marisol"', "[1, 2]", rec("a" * 12, "keep", 1))
    assert ltl.erase_entity(re.compile(r"Marisol")) == 2
    assert [r["utt"] for r in load(shadow)] == ["a" * 12]


# ── the cascade step ────────────────────────────────────────────────────────────────

async def test_the_forget_cascade_reaches_the_text_logs(shadow):
    """Break: remove the ``text_logs`` step in ``memory_forget_cascade._clear`` -> the record naming Marisol stays."""
    write(shadow, rec("a" * 12, "ring Marisol about the weekend", 1), rec("b" * 12, "play jazz", 1))
    res = await cascade.cascade_forget("demo_bar_00000001", "Marisol")
    assert "text_logs" not in res.failed
    assert [r["utt"] for r in load(shadow)] == ["b" * 12]


async def test_a_text_log_failure_is_a_failed_cascade_not_a_silent_one(shadow, monkeypatch):
    """The cascade must not call a forget complete when a log still holds the words."""
    write(shadow, rec("a" * 12, "ring Marisol", 1))

    def boom(_rx):
        raise OSError("disk")
    monkeypatch.setattr(ltl, "erase_entity", boom)
    monkeypatch.setattr(cascade, "RETRY_DELAY_S", 0)
    res = await cascade.cascade_forget("demo_bar_00000001", "Marisol")
    assert "text_logs" in res.failed and not res.complete


def test_text_logs_is_a_retryable_label():
    assert "text_logs" in cascade.ALL_LABELS and "text_logs" not in cascade.DB_STEPS


# ── the member erase ────────────────────────────────────────────────────────────────

class _Col:
    def __init__(self, ids):
        self.ids, self.deleted = ids, []

    def get(self, **_kw):
        return {"ids": list(self.ids)}

    def delete(self, ids=None, **_kw):
        self.deleted = ids

    def upsert(self, **_kw):
        return None

    def count(self):
        return 0


async def test_delete_user_clears_the_text_layer_and_fails_closed(shadow, monkeypatch):
    """Break: drop the ``local_text_logs.erase_all_text`` call in ``MemoryService.delete_user`` -> the words stay readable."""
    write(shadow, rec("a" * 12, "private words", 1))
    svc = MemoryService(data_dir="/tmp/zoe-test-text-log-delete")
    svc._collection = lambda: _Col(["m1"])
    svc._audit_collection = lambda: _Col([])
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda uid: None)
    assert await svc.delete_user("jason", actor="admin") == 1
    assert "private words" not in shadow.read_text() and load(shadow)[0]["utt"] == "a" * 12

    def boom():
        raise OSError("disk")
    monkeypatch.setattr(ltl, "erase_all_text", boom)
    with pytest.raises(MemoryServiceError, match="local text-log erasure failed"):
        await svc.delete_user("jason", actor="admin")


# --- shadow2 worker racing an erase (the pre-forget utterance must not land after the erase) --------------------------------

def _shadow2_world(shadow, monkeypatch, decide):
    import sys
    import types

    import semantic_router as sr

    monkeypatch.setattr(sr, "_HEAD_LOG_PATH", str(shadow))
    monkeypatch.setattr(sr, "_with_text", lambda r, t: {**r, "utt_text": t})
    monkeypatch.setitem(sys.modules, "router_two_stage", types.SimpleNamespace(decide=decide))
    return sr


def _run_shadow2(sr, text, monkeypatch):
    """Start the shadow2 worker and return the Thread object ``_two_stage_shadow2`` created. The handle is captured AT creation
    (a ``threading`` shim recording every Thread), never found afterwards by scanning ``threading.enumerate()`` - a fast worker
    would already be gone by then."""
    import threading as th
    import types

    made: list = []

    class _Rec(th.Thread):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            made.append(self)

    shim = types.SimpleNamespace(**{k: v for k, v in vars(th).items() if k != "Thread"})
    shim.Thread = _Rec
    monkeypatch.setattr(sr, "threading", shim)
    sr._two_stage_shadow2(text, None, "chat")
    (worker,) = [t for t in made if t.name == "router-shadow2"]
    return worker


def test_shadow2_observation_computed_across_an_erase_is_dropped(shadow, monkeypatch):
    import re
    import threading as th

    started, release = th.Event(), th.Event()

    def decide(text, v):
        started.set()
        assert release.wait(10)
        return {"domain": "chat"}

    sr = _shadow2_world(shadow, monkeypatch, decide)
    worker = _run_shadow2(sr, "remind me to call Marisol tomorrow", monkeypatch)
    assert started.wait(10)
    ltl.erase_entity(re.compile(r"\bMarisol\b"))              # the erase finishes while the prediction is still pending
    release.set()
    worker.join(10)
    assert not shadow.exists() or "Marisol" not in shadow.read_text()


def test_shadow2_normal_observation_still_appends(shadow, monkeypatch):
    sr = _shadow2_world(shadow, monkeypatch, lambda t, v: {"domain": "chat"})
    worker = _run_shadow2(sr, "what time is it", monkeypatch)
    worker.join(10)
    assert [r.get("utt_text") for r in load(shadow)] == ["what time is it"]


def test_active_mode_observation_computed_across_an_erase_is_dropped(shadow, monkeypatch):
    """PR #2110 review: ``route()`` in ZOE_ROUTER_HEAD=active logged with no erase epoch. The prediction is held (events, no
    sleeps) while the erase completes. Break: drop the ``epoch`` argument from the active ``_log_two_stage`` call -> Marisol returns."""
    import re
    import threading as th

    import numpy as np

    started, release = th.Event(), th.Event()

    def decide(text, v):
        started.set()
        assert release.wait(10)
        return {"domain": "chat"}

    sr = _shadow2_world(shadow, monkeypatch, decide)

    class _M:
        def __matmul__(self, v):
            return np.zeros(64)

    monkeypatch.setattr(sr, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(sr, "embed", lambda t: np.zeros(4))
    monkeypatch.setattr(sr, "head_mode", lambda: "active")
    monkeypatch.setattr(sr, "_head_shadow", lambda *a, **k: None)
    monkeypatch.setattr(sr, "_apply_question_precedence", lambda *a, **k: None)
    monkeypatch.setattr(sr, "threshold", lambda: 0.5)
    monkeypatch.setattr(sr, "_MATRIX", _M())
    monkeypatch.setattr(sr, "_DOM_IDX", {d: 0 for d in sr.ROUTES})
    out: dict = {}
    t = th.Thread(target=lambda: out.update(sr.route("remind me to call Marisol tomorrow")))
    t.start()
    assert started.wait(10)
    ltl.erase_entity(re.compile(r"\bMarisol\b"))
    release.set()
    t.join(10)
    assert out.get("two_stage") is not None                    # the turn itself still routed
    assert not shadow.exists() or "Marisol" not in shadow.read_text()


def test_head_shadow_observation_across_an_erase_is_dropped(shadow, monkeypatch):
    """The sync head-shadow append carries the same epoch. Break: drop ``epoch`` from its ``_append_shadow_line`` call."""
    import re

    sr = _shadow2_world(shadow, monkeypatch, lambda t, v: None)
    epoch = sr._erase_epoch_now()
    ltl.erase_entity(re.compile(r"\bMarisol\b"))
    assert sr._append_shadow_line(str(shadow), json.dumps({"utt": "x", "utt_text": "call Marisol"}), epoch) is False
    assert not shadow.exists() or "Marisol" not in shadow.read_text()


def test_relative_log_settings_resolve_against_the_service_dir_not_the_cwd(tmp_path, monkeypatch):
    """PR #2110 review: the hourly sweep runs from another cwd; a relative ZOE_ROUTER_HEAD_LOG / ZOE_INTENT_MISS_LOG must name the
    file zoe-data (cwd = services/zoe-data) writes. Break: return the raw relative Path -> it follows the process cwd."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ZOE_ROUTER_HEAD_LOG", "data/custom.jsonl")
    monkeypatch.setenv("ZOE_INTENT_MISS_LOG", "data/miss.jsonl")
    assert ltl.log_path(ltl.ROUTER_SHADOW) == ltl._HERE / "data" / "custom.jsonl"
    assert ltl.log_path(ltl.INTENT_MISSES) == ltl._HERE / "data" / "miss.jsonl"
    monkeypatch.setenv("ZOE_ROUTER_HEAD_LOG", str(tmp_path / "abs.jsonl"))
    assert ltl.log_path(ltl.ROUTER_SHADOW) == tmp_path / "abs.jsonl"


def test_append_between_erase_passes_is_refused(shadow, monkeypatch):
    """PR #2110 review: an observation that captured the epoch AFTER the erase's initial bump must not land between the two log
    passes. Break: drop the erase_in_progress() check in _append_shadow_line."""
    import re

    sr = _shadow2_world(shadow, monkeypatch, lambda t, v: None)
    results = []
    real_apply = ltl._apply
    calls = {"n": 0}

    def apply_then_append(log, **kw):
        out = real_apply(log, **kw)
        calls["n"] += 1
        if calls["n"] == 1:      # between pass 1 and pass 2: epoch already bumped, so a fresh capture matches
            line = json.dumps({"utt": "x", "utt_text": "call Marisol"})
            results.append(sr._append_shadow_line(str(shadow), line, sr._erase_epoch_now()))
        return out

    monkeypatch.setattr(ltl, "_apply", apply_then_append)
    ltl.erase_entity(re.compile(r"\bMarisol\b"))
    assert results == [False]
    assert not ltl.erase_in_progress()
    assert not shadow.exists() or "Marisol" not in shadow.read_text()


# --- one path resolver for writer and erasers (PR #2110 review: a ``~`` log setting) -------------------------------------------

def test_tilde_log_setting_is_written_and_erased_at_the_same_file(tmp_path, monkeypatch):
    """ZOE_ROUTER_HEAD_LOG=~/x: the router writer and forget/retention must name ONE file (HOME-expanded), not the writer opening a
    literal ``~`` dir under the service. Break the fix (writer reads the raw env) -> the line lands in ./~/ and the erase misses it."""
    import semantic_router as sr

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_ROUTER_HEAD_LOG", "~/tilde-router.jsonl")
    monkeypatch.setenv("ZOE_INTENT_MISS_LOG", str(tmp_path / "misses.jsonl"))
    monkeypatch.chdir(tmp_path)
    resolved = sr._resolve_head_log_path()
    assert resolved == str(tmp_path / "tilde-router.jsonl") == str(ltl.log_path(ltl.ROUTER_SHADOW))
    monkeypatch.setattr(sr, "_HEAD_LOG_PATH", resolved)
    assert sr._append_shadow_line(sr._HEAD_LOG_PATH, rec("aaa", "tell Zed the secret", 0)) is True
    assert (tmp_path / "tilde-router.jsonl").exists() and not (tmp_path / "~").exists()
    assert ltl.erase_entity(re.compile("Zed")) == 1
    assert "Zed" not in (tmp_path / "tilde-router.jsonl").read_text()


def test_writer_and_reader_modules_resolve_through_local_text_logs():
    """No module may open the raw ZOE_ROUTER_HEAD_LOG value: the writer, the miner and the retrain gate all use the shared resolver."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    for rel in ("services/zoe-data/semantic_router.py", "labs/router-selftrain/mine_candidates.py",
                "scripts/maintenance/router_struggle_retrain.py"):
        src = (root / rel).read_text()
        assert "local_text_logs" in src, rel
