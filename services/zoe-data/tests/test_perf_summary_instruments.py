"""perf_summary and its three instruments (gap audit 2026-11-14.6): SQL_ADAPT_MEMO_SUMMARY (db_pool A5), MEMORY_SEARCH_EMBED_SUMMARY
(memory_service A4) and PALACE_EMBED_SUMMARY (palace_embed, #2460).

Each is pinned for: a strict line format (codes and integers only, no text can appear), at most one line per interval (never a line
per call), behaviour-neutral (the measured call returns the same thing with the instrument on or off, and an instrument that
breaks never breaks the call), and agreement with ground truth (the counted embeds equal the embedder's own call count).
``ci_safe``.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid

import pytest

import perf_summary as ps

pytestmark = pytest.mark.ci_safe

INT = r"\d{1,12}"


def _line_re(name, counters, hists=()):
    parts = [rf"s={INT}"] + [rf"{c}={INT}" for c in counters]
    for prefix, edges in hists:
        parts += [rf"{b}={INT}" for b in ps.bucket_names(prefix, edges)]
    return re.compile(rf"^{name}_SUMMARY " + " ".join(parts) + "$")


SQL_RE = _line_re("SQL_ADAPT_MEMO", ("hits", "misses", "size"))
SEARCH_RE = _line_re("MEMORY_SEARCH_EMBED", ("searches", "embeds", "reused"))
EMBED_RE = _line_re("PALACE_EMBED", ("calls", "texts", "ms_sum", "fallback"), (("ms", ps.MS_BUCKETS), ("len", ps.LEN_BUCKETS)))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _lines(caplog, name):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(f"{name}_SUMMARY")]


# ── the helper ────────────────────────────────────────────────────────────────────────────

def test_one_line_per_interval_never_per_call(caplog):
    caplog.set_level(logging.INFO, logger="perf_summary")
    clk = Clock()
    s = ps.Summary("DEMO", ("a", "b"), {"ms": ps.MS_BUCKETS}, every_s=600, clock=clk)
    for _ in range(500):
        s.observe({"ms": 7.0}, a=1)
    assert _lines(caplog, "DEMO") == []                     # 500 calls, no line inside the interval
    clk.t += 600
    s.add(b=2)
    clk.t += 1
    s.add(b=1)                                              # the next interval has only just begun
    (line,) = _lines(caplog, "DEMO")
    assert line == "DEMO_SUMMARY s=600 a=500 b=2 ms_le5=0 ms_le10=500 ms_le20=0 ms_le50=0 ms_le100=0 ms_le200=0 ms_le500=0 ms_gt500=0"


def test_bucket_edges_are_inclusive_upper_bounds():
    s = ps.Summary("DEMO", (), {"len": ps.LEN_BUCKETS}, clock=Clock())
    for v in (1, 2, 3, 32, 33):
        s.observe({"len": v})
    assert s.h["len"] == [1, 1, 1, 0, 0, 1, 1]              # 1 | 2 | 3->le4 | - | - | 32 | 33->gt32


def test_never_raises_and_unknown_names_are_ignored(caplog):
    clk = Clock()
    s = ps.Summary("DEMO", ("a",), {"ms": ps.MS_BUCKETS}, clock=clk)
    s.observe({"ms": "not a number"}, a=1)                  # swallowed
    s.add(zzz=5, a="x")                                     # swallowed
    s.observe({"nope": 1.0}, a=1)
    assert s.c == {"a": 1}

    def bad_fill(_c):
        raise RuntimeError("boom")
    t = ps.Summary("DEMO", ("a",), clock=clk, fill=bad_fill, every_s=1)
    clk.t += 5
    assert t.tick() is False


def test_the_strict_formats_reject_text():
    """Negative control: a line with any word in it does not pass the format a test pins."""
    ok = "SQL_ADAPT_MEMO_SUMMARY s=600 hits=10 misses=2 size=12"
    assert SQL_RE.match(ok)
    assert not SQL_RE.match(ok + " sql=SELECT")
    assert not SQL_RE.match(ok.replace("hits=10", "hits=ten"))
    assert not SEARCH_RE.match("MEMORY_SEARCH_EMBED_SUMMARY s=600 searches=1 embeds=1 reused=1 user=member_a")


# ── A5: the SQL rewrite memo ──────────────────────────────────────────────────────────────

def test_sql_memo_summary_counts_hits_and_misses_and_changes_no_rewrite(monkeypatch, caplog):
    import db_pool

    caplog.set_level(logging.INFO, logger="db_pool")
    sqls = ["SELECT * FROM t WHERE a = ? AND b = ?", "UPDATE t SET a = ? WHERE id = ?", "SELECT datetime('now')"]
    monkeypatch.setattr(db_pool, "_SQL_MEMO_SUMMARY", None)
    db_pool._adapt_sql_memo.cache_clear()
    off = [db_pool._adapt_params(q, [1, 2]) for q in sqls * 3]
    clk = Clock()
    db_pool._adapt_sql_memo.cache_clear()
    monkeypatch.setattr(db_pool, "_SQL_MEMO_BASE", [0, 0])
    monkeypatch.setattr(db_pool, "_SQL_MEMO_SUMMARY",
                        ps.Summary("SQL_ADAPT_MEMO", ("hits", "misses", "size"), fill=db_pool._sql_memo_fill, clock=clk,
                                   log=db_pool.logger))
    on = [db_pool._adapt_params(q, [1, 2]) for q in sqls * 3]
    assert on == off                                         # behaviour-neutral
    assert _lines(caplog, "SQL_ADAPT_MEMO") == []
    clk.t += ps.SUMMARY_EVERY_S
    db_pool._adapt_params(sqls[0], [1, 2])                   # a 10th call: a hit, then the line
    (line,) = _lines(caplog, "SQL_ADAPT_MEMO")
    assert SQL_RE.match(line) and line.endswith("hits=7 misses=3 size=3")
    assert "SELECT" not in line


def test_a_broken_sql_instrument_never_breaks_the_rewrite(monkeypatch):
    import db_pool

    good = db_pool._adapt_params("SELECT ? , ?", [1, 2])
    monkeypatch.setattr(db_pool, "_SQL_MEMO_SUMMARY", ps.Summary("X", (), fill=lambda c: 1 / 0, every_s=0))
    assert db_pool._adapt_params("SELECT ? , ?", [1, 2]) == good


# ── #2460: the palace MiniLM batches ──────────────────────────────────────────────────────

def _ef(monkeypatch, dyn):
    import palace_embed

    class Base:
        def _forward(self, documents, batch_size=32):
            return ("fixed-pad", tuple(documents))

    class EF(palace_embed.DynamicPadMixin, Base):
        pass
    monkeypatch.setattr(palace_embed, "embed_dynamic", dyn)
    monkeypatch.setattr(palace_embed, "_FALLBACK_LOGGED", True)   # no once-per-process warning state leaks out of a test
    return EF()


def test_palace_embed_summary_counts_calls_texts_buckets_and_fallback(monkeypatch, caplog):
    import palace_embed

    caplog.set_level(logging.INFO, logger="palace_embed")
    clk = Clock()
    monkeypatch.setattr(palace_embed, "_EMBED_SUMMARY",
                        ps.Summary("PALACE_EMBED", ("calls", "texts", "ms_sum", "fallback"),
                                   {"ms": ps.MS_BUCKETS, "len": ps.LEN_BUCKETS}, clock=clk, log=palace_embed.logger))
    ef = _ef(monkeypatch, lambda self, docs, bs: ("dynamic", tuple(docs)))
    assert ef._forward(["a private memory", "b"]) == ("dynamic", ("a private memory", "b"))
    assert ef._forward(["c"]) == ("dynamic", ("c",))

    def boom(self, docs, bs):
        raise ValueError("no tokenizer")
    ef2 = _ef(monkeypatch, boom)
    assert ef2._forward(["d"]) == ("fixed-pad", ("d",))       # the fallback answer, unchanged
    clk.t += ps.SUMMARY_EVERY_S
    assert ef2._forward([]) == ("fixed-pad", ())              # an empty call is counted too, then the line
    (line,) = _lines(caplog, "PALACE_EMBED")
    assert EMBED_RE.match(line) and "private" not in line
    f = dict(kv.split("=") for kv in line.split()[1:])
    assert (f["calls"], f["texts"], f["fallback"]) == ("4", "4", "1")
    assert f["len_le1"] == "3" and f["len_le2"] == "1" and f["ms_le5"] == "4"


def test_palace_embed_is_unchanged_with_the_instrument_off_or_broken(monkeypatch):
    import palace_embed

    ef = _ef(monkeypatch, lambda self, docs, bs: ("dynamic", tuple(docs)))
    monkeypatch.setattr(palace_embed, "_EMBED_SUMMARY", None)
    off = ef._forward(["x", "y"])

    class Broken:
        def observe(self, *_a, **_k):
            raise RuntimeError("boom")
    monkeypatch.setattr(palace_embed, "_EMBED_SUMMARY", Broken())
    assert ef._forward(["x", "y"]) == off == ("dynamic", ("x", "y"))

    def raises(self, docs, bs):
        raise KeyError("dyn")

    class Base2:
        def _forward(self, documents, batch_size=32):
            raise OSError("model gone")

    class EF2(palace_embed.DynamicPadMixin, Base2):
        pass
    monkeypatch.setattr(palace_embed, "embed_dynamic", raises)
    with pytest.raises(OSError):                              # the call's own exception still propagates, untouched
        EF2()._forward(["z"])


# ── A4: the search query embed (real in-memory chroma, a counting embedder) ─────────────────────

def _search_setup():
    """A real in-memory chroma collection with a counting embedder (skipped where chromadb is not installed)."""
    chromadb = pytest.importorskip("chromadb")
    np = pytest.importorskip("numpy")
    from chromadb.api.types import EmbeddingFunction

    import memory_service

    class _CountingEF(EmbeddingFunction):
        def __init__(self):
            self.calls = 0

        def __call__(self, input):
            self.calls += 1
            out = []
            for t in input:
                raw = np.frombuffer(hashlib.sha256(str(t).encode()).digest()[:16], dtype=np.uint8).astype(np.float32) - 127.5
                out.append((raw / np.linalg.norm(raw)).astype(np.float32))
            return out

        @staticmethod
        def name() -> str:
            return "zoe-test-counting-ps"

        def get_config(self):
            return {}

        @staticmethod
        def build_from_config(config):
            return _CountingEF()

    ef = _CountingEF()
    col = chromadb.EphemeralClient().create_collection(f"t{uuid.uuid4().hex}", embedding_function=ef)
    rows = [(f"o{i}", f"other household note {i}", {"user_id": f"other_{i}", "status": "approved", "confidence": 0.7})
            for i in range(5)] + [("m0", "the member's own note about the garden", {"user_id": "member_a", "status": "approved",
                                                                                     "confidence": 0.8})]
    col.add(ids=[r[0] for r in rows], documents=[r[1] for r in rows], metadatas=[r[2] for r in rows])
    ef.calls = 0
    svc = memory_service.MemoryService(data_dir="/nonexistent/zoe-test-perf-summary")
    svc._collection = lambda: col
    return memory_service, svc, ef


def _flat(hits):
    return [(h.id, h.text, sorted(h.metadata.items()), h.score) for h in hits]


@pytest.mark.parametrize("old_path", [False, True])
def test_search_embed_summary_agrees_with_the_embedder_and_changes_no_result(monkeypatch, caplog, old_path):
    ms, svc, ef = _search_setup()
    caplog.set_level(logging.INFO, logger="memory_service")
    if old_path:   # the pre-A4 path: two query_texts calls, two embeds
        monkeypatch.setattr(ms, "_query_embeddings_once", lambda c, q: None)
    monkeypatch.setattr(ms, "_SEARCH_EMBED_SUMMARY", None)
    off = _flat(svc._semantic_search("garden", "member_a", 5))
    clk = Clock()
    monkeypatch.setattr(ms, "_SEARCH_EMBED_SUMMARY",
                        ps.Summary("MEMORY_SEARCH_EMBED", ("searches", "embeds", "reused"), clock=clk, log=ms.logger))
    ef.calls = 0
    on = _flat(svc._semantic_search("garden", "member_a", 5))
    truth = ef.calls
    clk.t += ps.SUMMARY_EVERY_S
    ms._SEARCH_EMBED_SUMMARY.tick()
    assert on == off                                          # behaviour-neutral
    (line,) = _lines(caplog, "MEMORY_SEARCH_EMBED")
    assert SEARCH_RE.match(line)
    f = dict(kv.split("=") for kv in line.split()[1:])
    assert f["searches"] == "1" and int(f["embeds"]) == truth == (2 if old_path else 1)   # the instrument equals the ground truth
    assert f["reused"] == ("0" if old_path else "1")
