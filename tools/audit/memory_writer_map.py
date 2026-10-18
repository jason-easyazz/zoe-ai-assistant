#!/usr/bin/env python3
"""Memory writer map, GENERATED from code (deep dive B8; replaces the hand-kept count in the 10-05 fidelity audit).

The audit (docs/research/memory-fidelity-audit-2026-10-05.md) counted 31 write paths into Zoe's memory by hand;
the architecture deep dive found at least 39 by 2026-10-18. A count kept in a document is stale the day it is
written, so this tool derives it: every call site in ``services/zoe-data`` (non-test) that WRITES the memory
drawers, as (module, enclosing function, kind):

- ``MemoryService`` write methods - ``ingest``, ``record_candidate``, ``supersede_by``, ``restore_superseded``,
  ``archive_duplicate``, ``archive_by_entity``, ``sweep_soft_archive``, ``forget_last`` (the method names are
  unique to MemoryService, so ANY receiver counts - an alias cannot hide a writer) and ``review`` /
  ``delete_user`` (generic names: only on a recognised MemoryService receiver such as ``svc`` or
  ``get_memory_service()``).
- raw drawer-collection writes - ``<col>.upsert/update/delete/add(...)`` on a Chroma collection handle. Inside ``memory_service.py``
  itself these are the storage layer's own writes (the funnel); anywhere else they BYPASS the funnel's guards
  and are the riskiest kind.

Each writer MODULE needs a role line in ``docs/knowledge/memory-writer-notes.json`` (so a new writer module cannot
land without someone saying what it is); ``tests/unit/test_memory_writer_map.py`` fails on a missing note and on a
stale map. Scope limit, stated in the generated doc: the people graph, exact-words, night-mind and other side stores
have their own tables; they are not memory-drawer writers and are not in this map.

Usage:
    python3 tools/audit/memory_writer_map.py            # rewrite docs/knowledge/memory-writer-map.md
    python3 tools/audit/memory_writer_map.py --check    # exit 1 if the doc is stale or a module has no note
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

SERVICE_DIR = "services/zoe-data"
NOTES_REL = "docs/knowledge/memory-writer-notes.json"
MAP_REL = "docs/knowledge/memory-writer-map.md"
FUNNEL = f"{SERVICE_DIR}/memory_service.py"

# Names no other class in zoe-data defines: any receiver is a MemoryService write.
UNIQUE_METHODS = frozenset({
    "ingest", "record_candidate", "supersede_by", "restore_superseded", "archive_duplicate",
    "archive_by_entity", "sweep_soft_archive", "forget_last",
    "retire_with_quote", "erase_rows", "relink_entity", "scrub_residue", "compact_index", "edit_outranked",
    "tick_access", "tick_consolidation",
})
# Generic names: only counted on a recognised MemoryService receiver.
GENERIC_METHODS = frozenset({"review", "delete_user"})
SVC_RECEIVER = re.compile(r"^(svc|service|self\.service|self\._svc|get_memory_service\(\)|_svc\(\)|memory_service|ms)$")
# A Chroma collection handle: the names the codebase uses for a drawers collection.
RAW_RECEIVER = re.compile(
    r"^(col|coll|ncol|rcol|ca|cb|collection|drawers|self\.(_?col|_?coll|_?collection|_raw)"
    r"|(get_drawers_collection|leased_drawers|get_collection)\(.*\))$"
)
RAW_METHODS = frozenset({"upsert", "update", "delete", "add"})


class _Walker(ast.NodeVisitor):
    def __init__(self, is_funnel: bool) -> None:
        self.stack: list[str] = []
        self.sites: dict[str, set[str]] = {}
        self.is_funnel = is_funnel

    def _scope(self, node) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _scope

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            attr, recv = node.func.attr, ast.unparse(node.func.value)
            kind = None
            if attr in RAW_METHODS and RAW_RECEIVER.match(recv):
                kind = "raw_" + attr
            elif not self.is_funnel:  # inside memory_service.py the service API is the implementation, not a caller
                if attr in UNIQUE_METHODS:
                    kind = attr
                elif attr in GENERIC_METHODS and SVC_RECEIVER.match(recv):
                    kind = attr
            if kind:
                self.sites.setdefault(".".join(self.stack) or "<module>", set()).add(kind)
        self.generic_visit(node)


def _is_test(rel: str) -> bool:
    p = Path(rel)
    return "tests" in p.parts or p.name.startswith("test_") or "samantha_live" in p.parts


def collect(repo: Path, files: list[str] | None = None) -> dict[str, dict[str, list[str]]]:
    """``{module path: {function qualname: sorted kinds}}`` for every memory-drawer writer."""
    if files is None:
        out = subprocess.run(["git", "ls-files", f"{SERVICE_DIR}/*.py"], cwd=repo, capture_output=True, text=True, check=True)
        files = [f for f in out.stdout.splitlines() if f]
    writers: dict[str, dict[str, list[str]]] = {}
    for rel in sorted(files):
        if _is_test(rel):
            continue
        try:
            tree = ast.parse((repo / rel).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        w = _Walker(is_funnel=rel == FUNNEL)
        w.visit(tree)
        if w.sites:
            writers[rel] = {fn: sorted(kinds) for fn, kinds in sorted(w.sites.items())}
    return writers


def load_notes(repo: Path) -> dict[str, str]:
    p = repo / NOTES_REL
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def missing_notes(writers: dict, notes: dict[str, str]) -> list[str]:
    return sorted(m for m in writers if len((notes.get(m) or "").strip()) < 20)


def stale_notes(writers: dict, notes: dict[str, str]) -> list[str]:
    return sorted(m for m in notes if m not in writers)


def count_sites(writers: dict) -> int:
    return sum(len(fns) for fns in writers.values())


def render(writers: dict, notes: dict[str, str]) -> str:
    n_sites, n_mods = count_sites(writers), len(writers)
    by_kind: dict[str, int] = {}
    for fns in writers.values():
        for kinds in fns.values():
            for k in kinds:
                by_kind[k] = by_kind.get(k, 0) + 1
    raw_outside = sorted(m for m, fns in writers.items() if m != FUNNEL and any(x.startswith("raw_") for k in fns.values() for x in k))
    lines = [
        "---",
        "type: Reference",
        "title: Memory writer map (GENERATED)",
        "description: Every code path in services/zoe-data that writes the memory drawers, derived from the AST - module, function, kind - with a role line per module. Replaces the hand-kept count in the 10-05 fidelity audit.",
        "tags: [memory, writers, generated, audit]",
        "---",
        "",
        "# Memory writer map",
        "",
        "**STATUS: GENERATED - do not hand-edit.** Regenerate with `python3 tools/audit/memory_writer_map.py`;",
        "role lines live in [`memory-writer-notes.json`](memory-writer-notes.json) (one per writer module - a new writer",
        "module fails `tests/unit/test_memory_writer_map.py` until it has one).",
        "",
        f"**{n_sites} write sites in {n_mods} modules** (the 2026-10-05 fidelity audit counted 31 by hand; the 2026-10-18",
        "deep dive found at least 39). A *write site* is one (module, enclosing function) that calls a `MemoryService`",
        "write method or upserts a drawers collection directly.",
        "",
        "Kinds: " + ", ".join(f"`{k}` {n}" for k, n in sorted(by_kind.items())) + ".",
        "",
        "## What counts",
        "",
        "- `MemoryService` write methods: `ingest`, `record_candidate`, `supersede_by`, `restore_superseded`, `archive_duplicate`,",
        "  `archive_by_entity`, `sweep_soft_archive`, `forget_last`, `retire_with_quote`, `erase_rows`, `relink_entity`, `scrub_residue`,",
        "  `compact_index`, `edit_outranked`, `tick_access`, `tick_consolidation` (any receiver) and `review`, `delete_user` (on a recognised",
        "  `MemoryService` receiver).",
        "- `raw_upsert` / `raw_update` / `raw_delete` / `raw_add`: a direct write on a drawers collection handle. In `memory_service.py` these are the storage",
        "  layer's own writes (the funnel). Anywhere else they **bypass the funnel's guards** (authority, tombstones, audit):",
        "  " + (", ".join(f"`{m.removeprefix(SERVICE_DIR + '/')}`" for m in raw_outside) or "none") + ".",
        "",
        "**Not covered** (own tables, not memory-drawer writers): the people graph (`people`, `person_relationships`,",
        "`person_activities`), exact-words, the night-mind and struggle stores, the forgotten-ledger, the Omi tables, and",
        "the Flue/TS callers (`services/zoe-core/extensions/memory.ts`), which reach the drawers only through",
        "`routers/memories.py` (listed below).",
        "",
        "## Writers",
        "",
        "| Module | Role | Write sites (function: kinds) |",
        "|---|---|---|",
    ]
    for mod, fns in writers.items():
        role = (notes.get(mod) or "**NO NOTE - add one to memory-writer-notes.json**").replace("|", "/")
        sites = "<br>".join(f"`{fn}`: {', '.join(kinds)}" for fn, kinds in fns.items())
        lines.append(f"| `{mod.removeprefix(SERVICE_DIR + '/')}` | {role} | {sites} |")
    stale = stale_notes(writers, notes)
    if stale:
        lines += ["", "## Stale notes", "", "Modules in `memory-writer-notes.json` that no longer write the drawers (delete the notes): "
                  + ", ".join(f"`{m}`" for m in stale) + "."]
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--check", action="store_true", help="exit 1 when the doc is stale or a writer module has no note")
    args = ap.parse_args(argv)
    writers = collect(args.repo)
    notes = load_notes(args.repo)
    text = render(writers, notes)
    target = args.repo / MAP_REL
    print(f"memory writers: {count_sites(writers)} write sites in {len(writers)} modules")
    gaps = missing_notes(writers, notes)
    if args.check:
        ok = True
        if gaps:
            print("modules with no role note: " + ", ".join(gaps))
            ok = False
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            print(f"{MAP_REL} is stale - rerun: python3 tools/audit/memory_writer_map.py")
            ok = False
        return 0 if ok else 1
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target}")
    if gaps:
        print("add role notes for: " + ", ".join(gaps), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
