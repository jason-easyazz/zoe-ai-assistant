"""A TEST DOUBLE of the scratch Postgres (``arms.pg_store.ScratchPostgres``): the same calls, a directory of files instead of a server.

It exists so the H arms' ``disk`` wiring is proven red-before-green in the slim CI lane (no docker, no Postgres). It makes NO claim about what
Postgres leaves on disk: that is MEASURED against the real scratch container by ``pilot/pg_erase_probe.py`` (docs/knowledge/zoe-memory-bench.md,
"Physical erase on the H arms"). What the double encodes is exactly the measured behaviour the scrub exists for:

* a DELETE does not remove the row's bytes: a dead row stays in its table file until ``compact`` (VACUUM FULL);
* the write-ahead log keeps every row ever written until ``compact`` switches it;
* the engine's document / bank delete leaves the log tables (``audit_log`` / ``llm_requests``) and an orphan ``entities`` row;
* ``erase_text`` / ``erase_bank`` remove those, and ``scan`` byte-scans a COPY of the files (+ asks for live rows), counts only.

``FakeHindsight(pg=FakePostgres(root))`` writes what the engine writes (retain -> documents / memory_units / entities / audit_log / llm_requests)
into it, so an arm run over the pair behaves like an arm over the real stack.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional, Sequence

from .pg_store import ENGINE_TABLES, LOG_SINKS, DERIVED_SINKS, token_variants

_SINKS = tuple(LOG_SINKS) + tuple(DERIVED_SINKS)


class FakePostgres:
    def __init__(self, root: "Optional[str]" = None):
        self.root = Path(root or tempfile.mkdtemp(prefix="fake-pg-"))
        self.rows: "dict[str, list[dict[str, Any]]]" = {}
        self.wal: "list[str]" = []
        self.calls: "list[str]" = []

    # ── what the engine writes (called by FakeHindsight) ──
    def write(self, table: str, bank: str, text: str, ref: str = "") -> None:
        self.rows.setdefault(table, []).append({"bank": bank, "text": text, "live": True, "ref": ref})
        self.wal.append(text)

    def kill(self, table: str, bank: str, ref: str = "") -> None:
        """The engine's DELETE: the row is dead (not visible), its bytes stay until ``compact``."""
        for r in self.rows.get(table, []):
            if r["bank"] == bank and r["live"] and (not ref or r["ref"] == ref):
                r["live"] = False

    def drop_bank(self, bank: str) -> None:
        """``delete_bank``: the engine tables of the bank die, ``audit_log`` / ``llm_requests`` are NOT touched (measured)."""
        for t in ENGINE_TABLES:
            self.kill(t, bank)

    # ── the same calls as ScratchPostgres ──
    def prepare(self) -> None:
        self.calls.append("prepare")

    def erase_text(self, bank: str, name: str) -> "dict[str, int]":
        self.calls.append("erase_text")
        out: "dict[str, int]" = {}
        pat = re.compile(re.escape(name), re.IGNORECASE)
        for t in _SINKS:
            keep = [r for r in self.rows.get(t, []) if not (r["bank"] == bank and pat.search(r["text"]))]
            out[t] = len(self.rows.get(t, [])) - len(keep)
            self.rows[t] = keep
        mentioned = " ".join(r["text"] for r in self.rows.get("memory_units", []) if r["bank"] == bank and r["live"])
        keep = [r for r in self.rows.get("entities", []) if not (r["bank"] == bank and r["live"] and r["text"] not in mentioned)]   # the lazy prune, now
        out["entities"] = len(self.rows.get("entities", [])) - len(keep)
        self.rows["entities"] = keep
        return out

    def erase_bank(self, bank: str) -> "dict[str, int]":
        self.calls.append("erase_bank")
        out: "dict[str, int]" = {}
        for t in _SINKS:
            keep = [r for r in self.rows.get(t, []) if r["bank"] != bank]
            out[t] = len(self.rows.get(t, [])) - len(keep)
            self.rows[t] = keep
        return out

    def erase_orphans(self) -> "dict[str, int]":
        self.calls.append("erase_orphans")
        live_banks = {r["bank"] for r in self.rows.get("banks", []) if r["live"]}
        out: "dict[str, int]" = {}
        for t in _SINKS:
            keep = [r for r in self.rows.get(t, []) if r["bank"] in live_banks]
            out[t] = len(self.rows.get(t, [])) - len(keep)
            self.rows[t] = keep
        return out

    def compact(self, *, vacuum: bool = True, stats: bool = True, wal: bool = True) -> None:
        self.calls.append("compact")
        if vacuum:
            for t in self.rows:
                self.rows[t] = [r for r in self.rows[t] if r["live"]]
        if wal:
            self.wal = [r["text"] for rows in self.rows.values() for r in rows]      # a new segment: only what is still there

    def other_banks(self, tokens: "Sequence[str]", bank: str) -> "dict[str, int]":
        alive = {r["bank"] for r in self.rows.get("banks", []) if r["live"]}
        out = {t: sum(1 for rows in self.rows.values() for r in rows if r["live"] and r["bank"] != bank and r["bank"] in alive and t.lower() in r["text"].lower())
               for t in tokens}
        return {t: n for t, n in out.items() if n}

    def scan(self, tokens: "Sequence[str]") -> "dict[str, Any]":
        from .. import lab_driver
        lab_driver._service_path()
        import memory_residue
        work = Path(tempfile.mkdtemp(prefix="fake-pg-scan-", dir=str(self.root)))
        try:
            (work / "base").mkdir()
            (work / "pg_wal").mkdir()
            for t, rows in self.rows.items():
                (work / "base" / t).write_text("\n".join(r["text"] for r in rows), encoding="utf-8")
            (work / "pg_wal" / "000000010000000000000001").write_text("\n".join(self.wal), encoding="utf-8")
            toks = list(tokens)
            spell = {t: token_variants(t) for t in toks}
            raw = memory_residue.scan_palace(work, sorted({v for vs in spell.values() for v in vs}), copy=False)["tokens"]
        finally:
            shutil.rmtree(work, ignore_errors=True)
        out: "dict[str, Any]" = {}
        for t in toks:
            files: "dict[str, int]" = {}
            for v in spell[t]:
                for f, n in raw[v]["files"].items():
                    files[f] = files.get(f, 0) + n
            live = {tb: sum(1 for r in rows if r["live"] and t.lower() in r["text"].lower()) for tb, rows in self.rows.items()}
            live = {k: n for k, n in live.items() if n}
            rel = {f.split("/", 1)[1] if f.startswith("base/") else "pg_wal": n for f, n in files.items()}
            out[t] = {"total": sum(files.values()) + sum(live.values()), "files": files, "pg_relations": rel, "live_rows": live}
        return {"tokens": out, "clean": all(v["total"] == 0 for v in out.values()), "seconds": 0.0}
