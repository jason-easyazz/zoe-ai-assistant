#!/usr/bin/env python3
"""affected_tests — run only the tests a diff can affect (the inner-loop test runner).

THE way an agent (or Zoe's own self-evolution harness) runs tests locally while
iterating. It is NOT the merge gate: a full ``ci_safe`` lane run (or CI's
``validate``) is still required before a PR is marked ready.

What it does
------------
1. Takes the diff of the working tree against the merge-base with ``<base>``
   (committed + staged + unstaged + untracked). Pure local git, no network,
   no ``gh``: the base may be any local ref or commit. Default base:
   ``$AFFECTED_TESTS_BASE`` or the first of ``origin/main``, ``main``, ``master``
   that resolves locally (no fetch). ``--files`` bypasses git entirely.
2. Builds a STATIC dependency graph of the repo (rebuilt every run from the
   files on disk, so it cannot go stale; a CONTENT-keyed parse cache in the
   clone's common git dir only makes it faster, ~2 s warm vs ~30 s cold):
     * python import edges (AST ``import``/``from``; a function-scope import in
       non-test code is followed only to importers that use that function, for
       ``LAZY_HOPS`` levels), plus string constants naming a module
       (``mock.patch("pkg.mod.attr")``, ``monkeypatch.setattr("mod.attr", ...)``);
     * text-reference edges: a file whose string literals (python) or tokens
       (sh/yml/js/...) name another file by repo path, or by basename (with its
       parent directory also named when the basename is not unique). This is
       how a test that reads ``.github/workflows/validate.yml`` or runs
       ``scripts/maintenance/x.sh`` is selected when that file changes.
3. Walks the graph backwards from every changed file to the test files that
   can reach it, per CI lane (the lanes mirror ``.github/workflows/validate.yml``;
   ``tests/unit/test_affected_tests.py`` pins the mirror).
4. Runs ONLY the selected tests, one pytest per lane (lanes cannot share a
   process — see tests/AGENTS.md), each under ``/tmp/zoe-test-lane.lock``
   (``flock(2)``, the same lock ``flock(1)`` takes), niced, never ``-n``,
   after waiting out a running voice lander and low MemAvailable. Exits with
   the real pytest rc (first failing lane's rc).

Safe by default — it falls back to the FULL lane(s) and says why when:
``conftest.py`` / ``pytest.ini`` / ``pyproject.toml`` / ``setup.cfg`` /
requirements or constraints / an Alembic migration changed; a changed module
is used directly by a lane's conftest; the selection would cover more than
``HUB_FRACTION`` of a lane anyway; a changed file is of an unknown type and no
test references it; the base ref cannot be resolved; or git fails. It never
silently selects zero tests for a code change: a code change no test can reach
is reported by name. A docs-only diff may select none and says so.

Machine-readable: ``--json -`` prints one JSON summary on stdout (human output
and pytest output go to stderr); ``--json FILE`` writes it to FILE.
``--dry-run`` selects without running. Schema: see ``SUMMARY_SCHEMA``.
"""
from __future__ import annotations

import argparse
import ast
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

SUMMARY_SCHEMA = 1
CACHE_VERSION = 11
REPO = Path(__file__).resolve().parents[2]

# --- lanes: MIRROR of .github/workflows/validate.yml (pinned by tests/unit/test_affected_tests.py) ---


@dataclass(frozen=True)
class Lane:
    name: str
    test_root: str                  # repo-relative directory owning the lane's tests
    marker: str | None              # -m expression (None = no marker filter)
    pythonpath: tuple[str, ...]     # repo-relative, "." = repo root
    service_dir: str | None         # requirements under here force this lane full
    ignore: tuple[str, ...] = ()
    only_files: tuple[str, ...] = ()  # lane runs exactly these files (HA bridge)
    extra_args: tuple[str, ...] = ()


LANES: tuple[Lane, ...] = (
    Lane("zoe-data", "services/zoe-data/tests", "ci_safe", ("services/zoe-data",), "services/zoe-data",
         ignore=("services/zoe-data/tests/samantha_live",),
         extra_args=("--override-ini=asyncio_mode=auto",)),
    Lane("tests-unit", "tests/unit", "ci_safe", (".", "services/zoe-data"), None,
         extra_args=("--override-ini=asyncio_mode=auto",)),
    Lane("zoe-auth", "services/zoe-auth/tests", None, ("services/zoe-auth", "services/zoe-auth/tests"),
         "services/zoe-auth"),
    Lane("ha-bridge", "services/homeassistant-mcp-bridge/tests", None, ("services/zoe-data",),
         "services/homeassistant-mcp-bridge",
         only_files=("services/homeassistant-mcp-bridge/tests/test_ha_bridge.py",
                     "services/homeassistant-mcp-bridge/tests/test_ha_tool_names.py"),
         extra_args=("--override-ini=asyncio_mode=auto",)),
)
LANES += (
    # validate.yml's "People MemPalace integration cleanup" step: one file, run by path
    Lane("people-mempalace", "tests/integration", None, ("services/zoe-data",), None,
         only_files=("tests/integration/test_people_mempalace_sync.py",),
         extra_args=("--override-ini=asyncio_mode=auto",)),
    # validate.yml's "People graph invariants on CI PostgreSQL" step (skips locally without ZOE_TEST_PG_URL)
    Lane("people-graph-pg", "services/zoe-data/tests", None, ("services/zoe-data",), None,
         only_files=("services/zoe-data/tests/test_people_graph_postgres.py",),
         extra_args=("--override-ini=asyncio_mode=auto",)),
)
LANE_ENV = {"ZOE_DATA_DB": ":memory:", "ZOE_MEMORY_STARTUP_STRICT": "false"}

HUB_FRACTION = 0.5
HUB_MIN_LANE = 20   # a lane this small just runs its selection (the hub fallback would save nothing)
CODE_EXT = (".py", ".sh", ".bash", ".js", ".mjs", ".cjs")
AGGREGATOR_FANOUT = 20
READER_HOPS = 1   # importer levels above a python module that READS a changed non-python file
LAZY_HOPS = int(os.environ.get("AFFECTED_TESTS_LAZY_HOPS", "2"))
LOCK_PATH = os.environ.get("AFFECTED_TESTS_LOCK", "/tmp/zoe-test-lane.lock")
LANDER_RE = r"^(\S*/)?bash [^ ]*/land_voice_pr\.sh"   # argv-anchored (never self-matches a waiter's own cmdline)

ASSET_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf", ".ico", ".woff", ".woff2", ".ttf",
             ".mp3", ".wav", ".ogg"}
DOC_EXT = {".md", ".rst", ".txt", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf"}
TEXT_EXT = {".py", ".sh", ".bash", ".yml", ".yaml", ".js", ".mjs", ".cjs", ".ts", ".json", ".toml", ".ini",
            ".cfg", ".conf", ".service", ".timer", ".socket", ".path", ".html", ".css", ".sql", ".env",
            ".example", ".j2", ".tmpl", ".template", ".rules", ".nft", ".lua", ".txt", ".csv", ".jsonl",
            ".xml", ".plist", ".desktop", ".mako", ".lock", ".gitignore", ".dockerignore"}
TEXT_NAMES = {"Dockerfile", "Makefile", "CODEOWNERS", ".gitignore", ".dockerignore", ".gitattributes",
              ".pre-commit-config.yaml", ".mcp.json"}
# files whose change can affect how EVERY test in a lane is collected/run
GLOBAL_CONFIG = {"pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "setup.py"}
DEP_RE = re.compile(r"(^|/)(requirements[^/]*\.txt|constraints[^/]*\.txt|Pipfile(\.lock)?|poetry\.lock|uv\.lock)$")
# files scanned as reference SOURCES (their text may name another file). Bounded: big data blobs skipped.
REF_SCAN_EXT = {".py", ".sh", ".bash", ".yml", ".yaml", ".mjs", ".cjs", ".toml", ".ini", ".cfg", ".service",
                ".timer"}
REF_SCAN_MAX_BYTES = 400_000          # data blobs (yml/json/...) past this are not scanned
REF_SCAN_MAX_PY_BYTES = 8_000_000     # python is always parsed (routers/voice_tts.py is ~410 KB)
# a test that walks the tree (rglob/os.walk/git ls-files) can be affected by ANY added/removed/non-doc file
SCANNER_RE = re.compile(r"\.rglob\(|os\.walk\(|\bls-files\b|glob\.glob\(|\.glob\(\s*[\"']\*\*")
# anchored on the checkout (a walk of tmp_path is not a repo walk). __file__ covers `Path(__file__).parent.parent`
REPO_ANCHOR_RE = re.compile(r"\bls-files\b|parents\[\d\]|\bREPO(_ROOT)?\b|ROOT\b|(?i:\broot\b)|--show-toplevel|__file__")
FILE_EXT_TAIL = {"py", "sh", "yml", "yaml", "json", "md", "html", "js", "mjs", "txt", "toml", "ini", "cfg", "env",
                 "service", "timer", "css", "sql", "log", "jsonl", "csv", "lock", "conf", "png", "onnx", "gguf"}
TOKEN_RE = re.compile(r"[A-Za-z0-9_.@/+-]+")
DOTTED_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")
SKIP_DIRS = ("node_modules/", "/node_modules/", ".git/", "dist/lib/", "/vendor/")


def log(msg: str) -> None:
    print(msg, file=sys.stderr if _OUT[0] else sys.stdout, flush=True)


_OUT = [False]   # True = human output to stderr (``--json -`` owns stdout)


# ------------------------------------------------------------------ git (local only)

def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}).stdout


def resolve_base(root: Path, base: str | None) -> str | None:
    cands = [base] if base else [os.environ.get("AFFECTED_TESTS_BASE") or "", "origin/main", "main", "master"]
    for c in cands:
        if not c:
            continue
        try:
            _git(root, "rev-parse", "--verify", "-q", f"{c}^{{commit}}")
            return c
        except subprocess.CalledProcessError:
            if base:
                return None
    return None


def changed_files(root: Path, base: str) -> tuple[str, list[tuple[str, str]]]:
    """(merge_base_sha, [(status, path)]) — working tree vs merge-base, plus untracked."""
    mb = _git(root, "merge-base", base, "HEAD").strip()
    out: dict[str, str] = {}
    parts = _git(root, "diff", "-z", "--no-renames", "--name-status", mb, "--").split("\0")
    for st, path in zip(parts[0::2], parts[1::2]):
        if path:
            out[path] = st[0]
    for path in _git(root, "ls-files", "-z", "--others", "--exclude-standard").split("\0"):
        if path:
            out[path] = "A"
    return mb, sorted((s, p) for p, s in out.items())


def repo_files(root: Path) -> list[str]:
    try:
        files = set(_git(root, "ls-files", "-z").split("\0"))
        files |= set(_git(root, "ls-files", "-z", "--others", "--exclude-standard").split("\0"))
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}
    return sorted(f for f in files if f and not any(s in f for s in SKIP_DIRS) and (root / f).is_file())


# ------------------------------------------------------------------ parsing (+ cache)

_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def _parse_py(text: str) -> dict:
    """imports [[dotted, level, from_names | None, scope]], strings, idents, defs, ok.

    ``scope`` = the OUTERMOST function an import sits in (None at module level): a lazy import runs only
    when that function is called. ``defs`` maps each top-level function / method name to the identifiers
    its body uses, so a lazy import's callers inside the module can be found; ``idents`` is every name and
    attribute the file uses."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return {"imports": [], "strings": TOKEN_RE.findall(text), "idents": [], "defs": {}, "ok": False}
    imports: list[list] = []
    strings: list[str] = []
    idents: set[str] = set()
    defs: dict[str, set[str]] = {}
    stack: list[tuple[ast.AST, str | None]] = [(tree, None)]
    while stack:
        node, scope = stack.pop()
        if isinstance(node, ast.Import):
            imports += [[a.name, 0, None, scope] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports.append([node.module or "", node.level, [a.name for a in node.names if a.name != "*"], scope])
            idents.update(a.name for a in node.names)   # `from registry import load as run` still USES load
            if scope is not None:
                defs.setdefault(scope, set()).update(a.name for a in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 400:
            strings.append(node.value)
        elif isinstance(node, ast.Name):
            idents.add(node.id)
        elif isinstance(node, ast.Attribute):
            idents.add(node.attr)
        if scope is None and isinstance(node, _FUNC):
            inner = node.name
        else:
            inner = scope
        if inner is not None and isinstance(node, (ast.Name, ast.Attribute)):
            defs.setdefault(inner, set()).add(node.id if isinstance(node, ast.Name) else node.attr)
        stack += [(c, inner) for c in ast.iter_child_nodes(node)]
    return {"imports": imports, "strings": strings, "idents": sorted(idents),
            "defs": {k: sorted(v) for k, v in defs.items()}, "ok": True}


def _js_harness(rel: str) -> bool:
    """a node test harness (``dist/test_touch_conversation_mode.js``): it RUNS what it names, like a script."""
    n = rel.rsplit("/", 1)[-1]
    return n.endswith((".js", ".mjs", ".cjs")) and (n.startswith("test_") or ".test." in n or _in_test_tree(rel))


def _runner(rel: str) -> bool:
    return rel.endswith((".sh", ".bash")) or _js_harness(rel)


def _scanned(rel: str) -> bool:
    n = rel.rsplit("/", 1)[-1]
    return ("." + n.rsplit(".", 1)[-1] if "." in n else "") in REF_SCAN_EXT or n in TEXT_NAMES or _js_harness(rel)


def _file_record(rel: str, text: str) -> dict:
    ext = "." + rel.rsplit(".", 1)[-1] if "." in rel.rsplit("/", 1)[-1] else ""
    rec: dict = {"imports": [], "strings": [], "idents": [], "defs": {}, "ok": True, "scanner": False,
                 "syspath": False}
    if ext == ".py":
        parsed = _parse_py(text)
        strings = parsed.pop("strings")
        rec.update(parsed)
    else:
        strings = TOKEN_RE.findall(text)
    rec["strings"] = sorted(set(strings))
    # a scanner walks the REPO (not a tmp_path): it walks, and it anchors on the checkout
    rec["scanner"] = bool(SCANNER_RE.search(text)) and bool(REPO_ANCHOR_RE.search(text))
    rec["syspath"] = ext == ".py" and ("sys.path" in text or "spec_from_file_location" in text)
    return rec


_EMPTY_REC = {"imports": [], "strings": [], "idents": [], "defs": {}, "ok": True, "scanner": False, "syspath": False}


def _cache_path(root: Path) -> Path | None:
    if os.environ.get("AFFECTED_TESTS_NO_CACHE"):
        return None
    if os.environ.get("AFFECTED_TESTS_CACHE"):
        return Path(os.environ["AFFECTED_TESTS_CACHE"])
    try:
        # the COMMON git dir: entries are content-keyed, so every worktree of the clone shares one cache
        gd = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return gd / "zoe-affected-tests-cache.json"


def load_records(root: Path, files: list[str]) -> dict[str, dict]:
    cp = _cache_path(root)
    cache: dict = {}
    if cp and cp.is_file():
        try:
            cache = json.loads(cp.read_text())
            if cache.get("v") != CACHE_VERSION:
                cache = {}
        except (OSError, ValueError):
            cache = {}  # corrupt cache = no cache; the graph is rebuilt from the files, never trusted stale
    entries = cache.get("files", {})
    recs: dict[str, dict] = {}
    dirty = False
    for rel in files:
        if not _scanned(rel):
            recs[rel] = _EMPTY_REC
            continue
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        if len(data) > (REF_SCAN_MAX_PY_BYTES if rel.endswith(".py") else REF_SCAN_MAX_BYTES):
            recs[rel] = _EMPTY_REC
            continue
        # keyed by CONTENT, not mtime: a checkout/rebase that keeps size and mtime cannot serve a stale parse
        key = hashlib.blake2b(data, digest_size=16).hexdigest()
        ent = entries.get(rel)
        if ent and ent.get("k") == key:
            recs[rel] = ent["r"]
        else:
            recs[rel] = _file_record(rel, data.decode("utf-8", errors="replace"))
            entries[rel] = {"k": key, "r": recs[rel]}
            dirty = True
    if cp and dirty:
        try:
            tmp = cp.with_name(f"{cp.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"v": CACHE_VERSION, "files": {k: v for k, v in entries.items()
                                                                      if k in recs and recs[k] is not _EMPTY_REC}}))
            tmp.replace(cp)
        except OSError:
            pass
    return recs


# ------------------------------------------------------------------ graph

def _is_test_file(rel: str) -> bool:
    n = rel.rsplit("/", 1)[-1]
    return n.startswith("test_") and n.endswith(".py")


def _in_test_tree(rel: str) -> bool:
    """test files AND their helpers/fixtures (everything under a tests/ directory)."""
    return _is_test_file(rel) or rel.startswith("tests/") or "/tests/" in rel


def lane_of(rel: str) -> Lane | None:
    for lane in LANES:
        if rel.startswith(lane.test_root + "/"):
            return lane
    return None


def _search_roots(rel: str) -> list[str]:
    """sys.path roots a file's absolute imports resolve against (most specific first).

    Mirrors how each lane runs: zoe-data tests get services/zoe-data (pytest.ini pythonpath) plus their own
    rootdir; tests/unit gets the repo root + services/zoe-data; zoe-auth is its own flat tree (never
    zoe-data's - the two trees share top-level names like ``models``)."""
    d = rel.rsplit("/", 1)[0] if "/" in rel else ""
    roots = [d]
    if rel.startswith("services/zoe-auth/"):
        roots += ["services/zoe-auth", "services/zoe-auth/tests"]
    elif rel.startswith("services/zoe-data/"):
        roots += ["services/zoe-data", "services/zoe-data/tests"]
    elif rel.startswith("services/"):
        svc = "/".join(rel.split("/")[:2])
        roots += [svc, svc + "/tests", "services/zoe-data"]
    else:
        roots += ["", "services/zoe-data"]
    seen: list[str] = []
    for r in roots:
        if r not in seen:
            seen.append(r)
    return seen


@dataclass
class Graph:
    files: set[str]
    recs: dict[str, dict]
    rdeps: dict[str, dict[str, str]] = field(default_factory=dict)  # target -> {source: why}
    # implicit parent-package __init__ execution (``from routers.lists import x`` runs routers/__init__.py):
    # followed ONLY when that __init__.py is itself the changed file. Otherwise a package __init__ that
    # re-exports every submodule (routers/) would make every router test depend on every router.
    weak: dict[str, dict[str, str]] = field(default_factory=dict)
    # function-scope imports in non-test code (memory_service.forget() lazily importing omi_recordings): the
    # importer's behaviour changes only on that code path, so only LAZY_HOPS levels of importers above it
    # are selected. Without this, zoe-data's lazy imports make nearly every module reach nearly every test.
    lazy: dict[str, dict[str, str]] = field(default_factory=dict)
    # AGGREGATORS (non-test modules with >= AGGREGATOR_FANOUT strong local imports: main.py importing every
    # router, routers/__init__.py): a change below one reaches only the aggregator's DIRECT importers.
    # A test that drives the app through ``from main import app`` is still selected; a module that imports
    # main for one helper does not drag every test above it in.
    aggregators: set[str] = field(default_factory=set)
    idents_cache: dict[str, frozenset] = field(default_factory=dict)
    by_stem: dict[str, list[str]] = field(default_factory=dict)
    by_base: dict[str, list[str]] = field(default_factory=dict)

    def add(self, src: str, dst: str, why: str) -> None:
        if src != dst:
            self.rdeps.setdefault(dst, {}).setdefault(src, why)


def _module_candidates(dotted: str, base_dir: str) -> list[str]:
    parts = dotted.split(".")
    pre = (base_dir + "/") if base_dir else ""
    out = []
    for i in range(len(parts), 0, -1):
        p = pre + "/".join(parts[:i])
        out += [p + ".py", p + "/__init__.py"]
    return out


STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}


def _resolve(g: Graph, rel: str, dotted: str, level: int, strict: bool = False) -> tuple[list[str], list[str]]:
    """(modules the import names, parent-package __init__ files it executes implicitly).

    ``strict`` is for dotted STRING constants (``mock.patch("a.b.attr")``): they must name a module, or a
    module plus one attribute, under the file's own roots - never the basename fallback."""
    hits: list[str] = []
    parents: list[str] = []
    if not level and dotted.split(".")[0] in STDLIB:
        return [], []
    if level:
        base = rel.rsplit("/", 1)[0] if "/" in rel else ""
        for _ in range(level - 1):
            base = base.rsplit("/", 1)[0] if "/" in base else ""
        roots = [base]
    else:
        roots = _search_roots(rel)
    for r in roots:
        cands = _module_candidates(dotted, r) if dotted else [((r + "/") if r else "") + "__init__.py"]
        for i, c in enumerate(cands):
            if c in g.files:
                if strict and i // 2 > 1:
                    break
                hits.append(c)
                # importing a.b.c also executes the package __init__ files above it (implicitly: weak)
                pkg = c.rsplit("/", 1)[0] if "/" in c else ""
                if c.endswith("/__init__.py"):
                    pkg = pkg.rsplit("/", 1)[0] if "/" in pkg else ""
                while pkg and pkg != r and (pkg + "/__init__.py") in g.files:
                    parents.append(pkg + "/__init__.py")
                    pkg = pkg.rsplit("/", 1)[0] if "/" in pkg else ""
                break
        if hits:
            return hits, parents
    if not level and dotted and not strict and g.recs.get(rel, {}).get("syspath"):
        # the file edits sys.path (or loads by file location) then imports by bare name:
        # fall back to every module with that basename
        hits = list(g.by_stem.get(dotted.split(".")[0], []))
    return hits, parents


def build_graph(root: Path, files: list[str], extra: list[str] = ()) -> Graph:
    recs = load_records(root, files)
    g = Graph(set(files) | set(extra), recs)
    for f in g.files:
        n = f.rsplit("/", 1)[-1]
        g.by_base.setdefault(n, []).append(f)
        if n.endswith(".py"):
            stem = n[:-3] if n != "__init__.py" else (f.rsplit("/", 2)[-2] if f.count("/") else "")
            if stem:
                g.by_stem.setdefault(stem, []).append(f)
    for src, rec in recs.items():
        in_tests = _in_test_tree(src)
        for dotted, level, names, scope in rec["imports"]:
            is_lazy = scope is not None
            strong: list[str] = []
            weak: list[str] = []
            base_hits, parents = _resolve(g, src, dotted, level)
            weak += parents
            if names is None:
                strong += base_hits
            else:
                # `from pkg import a, b`: a name that is a SUBMODULE is a strong edge to it, and pkg/__init__.py
                # is only executed implicitly (weak); a name that is an attribute of pkg/__init__ is strong to it.
                for n in names:
                    sub, sub_parents = _resolve(g, src, (dotted + "." if dotted else "") + n, level)
                    sub = [h for h in sub if h not in base_hits]
                    if sub:
                        strong += sub
                        weak += base_hits + sub_parents
                    else:
                        strong += base_hits
            for dst in set(strong):
                if is_lazy and not in_tests:
                    if dst != src:
                        g.lazy.setdefault(dst, {}).setdefault(src, set()).add(scope)
                else:
                    g.add(src, dst, "imports")
            for dst in set(weak) - set(strong):
                if dst != src:
                    g.weak.setdefault(dst, {}).setdefault(src, "imports a submodule of")
        for s in rec["strings"]:
            if (src.endswith(".py") and DOTTED_RE.match(s) and s.split(".")[0] in g.by_stem
                    and s.rsplit(".", 1)[1] not in FILE_EXT_TAIL):
                for dst in _resolve(g, src, s, 0, strict=True)[0]:
                    g.add(src, dst, f"names module '{s}'")
    fan: dict[str, int] = {}
    for srcs in g.rdeps.values():
        for src in srcs:
            fan[src] = fan.get(src, 0) + 1
    g.aggregators = {f for f, n in fan.items() if n >= AGGREGATOR_FANOUT and not _in_test_tree(f)}
    return g


def _segments(s: str) -> list[str]:
    return [x for x in re.split(r"[/\\]", s) if x and x not in (".", "..")]


def text_referencers(g: Graph, target: str, seg_index: dict[str, set[str]]) -> dict[str, str]:
    """files whose literals/tokens name ``target`` (full path, or basename + parent dir when not unique)."""
    base = target.rsplit("/", 1)[-1]
    parent = target.rsplit("/", 2)[-2] if target.count("/") >= 1 else ""
    unique = len(g.by_base.get(base, [])) <= 1
    out: dict[str, str] = {}
    for src in seg_index.get(base, ()):
        if src == target:
            continue
        strings = g.recs[src]["strings"]
        if any(target in s for s in strings):
            out[src] = f"names {target}"
        elif unique or not parent:
            out[src] = f"names {base}"
        elif any(parent in _segments(s) for s in strings):
            out[src] = f"names {base} (+ dir {parent})"
    return out


def segment_index(g: Graph) -> dict[str, set[str]]:
    idx: dict[str, set[str]] = {}
    for src, rec in g.recs.items():
        for s in rec["strings"]:
            for seg in _segments(s):
                if "." in seg or seg in g.by_base:
                    idx.setdefault(seg, set()).add(src)
    return idx


# ------------------------------------------------------------------ selection

@dataclass
class Selection:
    base: str | None = None
    merge_base: str | None = None
    changed: list[tuple[str, str]] = field(default_factory=list)
    full_all_reason: str | None = None
    lane_full: dict[str, str] = field(default_factory=dict)          # lane -> reason
    tests: dict[str, dict[str, str]] = field(default_factory=dict)   # lane -> {test file: why}
    unmarked: dict[str, list[str]] = field(default_factory=dict)     # selected but outside the lane marker
    unreached: list[str] = field(default_factory=list)                # code changes no test reaches
    notes: list[str] = field(default_factory=list)


def _kind(rel: str) -> str:
    n = rel.rsplit("/", 1)[-1]
    ext = ("." + n.rsplit(".", 1)[-1]) if "." in n else ""
    if n == "conftest.py":
        return "conftest"
    if "/alembic/" in rel or rel.endswith("/alembic.ini"):
        return "migration"
    if n in GLOBAL_CONFIG:
        return "global-config"
    if DEP_RE.search(rel):
        return "deps"
    if ext == ".py":
        return "py"
    # DOCUMENTATION by location, not extension: a .txt / .md inside a service or script tree may be runtime
    # data (a prompt, a lexicon) and is treated as text that a test must reach
    is_doc_name = n in ("AGENTS.md", "CLAUDE.md", "README.md", "CHANGELOG.md") or n.startswith("README")
    if rel.startswith("docs/") or is_doc_name or ("/" not in rel and ext in DOC_EXT):
        return "doc"
    if ext in ASSET_EXT:
        return "text"   # a UI image / font / sound: no python lane loads it; named when no test reaches it
    if ext in TEXT_EXT or n in TEXT_NAMES:
        return "text"
    return "unknown"


def _lane_has_marker(root: Path, lane: Lane, rel: str) -> bool:
    if lane.marker is None:
        return True
    try:
        # the marker APPLIED (``pytestmark = pytest.mark.ci_safe`` / ``@pytest.mark.ci_safe``), not a mention of it
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        return re.search(rf"\bmark\.{re.escape(lane.marker)}\b", text) is not None
    except OSError:
        return False


def _lane_tests(g: Graph, lane: Lane) -> list[str]:
    if lane.only_files:
        return [f for f in lane.only_files if f in g.files]
    return sorted(f for f in g.files if f.startswith(lane.test_root + "/") and _is_test_file(f)
                  and not any(f.startswith(i + "/") for i in lane.ignore))


def _conftests_for(g: Graph, lane: Lane) -> list[str]:
    out = []
    parts = lane.test_root.split("/")
    for i in range(len(parts) + 1):
        c = "/".join(parts[:i] + ["conftest.py"])
        if c in g.files:
            out.append(c)
    out += [f for f in g.files if f.startswith(lane.test_root + "/") and f.endswith("/conftest.py")]
    return sorted(set(out))


def _consuming_lanes(rel: str) -> list[Lane]:
    """lanes that run a service's code: it owns them, or they put the service on PYTHONPATH."""
    svc = "/".join(rel.split("/")[:2]) if rel.startswith("services/") else None
    return [ln for ln in LANES if svc and (ln.service_dir == svc or svc in ln.pythonpath)]


def conftest_direct_deps(g: Graph, conftest: str) -> list[str]:
    """modules a conftest uses DIRECTLY (imports, or names as ``sys.modules.get("mod")``).

    A change to one of these can alter a fixture every test in the lane runs, so it forces the full lane.
    Their TRANSITIVE dependencies do not: a conftest that resets ``exact_words``' backend is not affected by
    a change deep inside ``memory_service``, and an import-time break there still fails every selected test,
    because every selected test loads the conftest."""
    out = [dst for dst, srcs in g.rdeps.items() if conftest in srcs]
    for s in g.recs.get(conftest, {}).get("strings", ()):
        if s.isidentifier():
            out += [f for f in g.by_stem.get(s, []) if any(f == ((r + "/") if r else "") + s + ".py"
                                                         for r in _search_roots(conftest))]
    return sorted(set(out))


def _closure(g: Graph, start: str, seg_index: dict[str, set[str]],
             memo: dict[str, dict[str, str]]) -> dict[str, tuple[str, str]]:
    """reverse reachability from ONE changed file: node -> (via, why) back-pointer toward it.

    Import edges are followed transitively. A text-reference edge (R names N) means R reads or RUNS N:
    it is followed from the changed file itself (its content changed), and from any node when R is a test
    or a non-python file (a test / shell script / workflow that executes N is affected by what N imports
    or sources). A python MODULE that merely names another file (a registry listing paths) is affected
    only by that file's own content, so it is not followed past the first hop.

    A non-test python module that names the changed DATA file (skybridge.py serving touch/home.html) uses
    it on some code path only; its DIRECT importers are selected, not everything that imports it
    transitively (otherwise every test importing main.py would depend on every file main's routers read)."""
    unlimited = 1 << 30
    seen: dict[str, tuple[str, str]] = {start: ("", "changed")}
    # per node: (hop budget, filter). A filter is the set of function names through which the change
    # reaches that module (a LAZY import inside ``def forget_user`` -> {"forget_user", and every function
    # in the module that calls it}); only importers that USE one of those names are followed from it.
    state: dict[str, tuple[int, frozenset | None]] = {start: (unlimited, None)}
    q = deque([start])
    for src, why in g.weak.get(start, {}).items():   # a changed package __init__ runs for every submodule import
        seen[src], state[src] = (start, why), (unlimited, None)
        q.append(src)

    def dec(b: int) -> int:
        return b - 1 if b != unlimited else unlimited

    def better(new: tuple[int, frozenset | None], old: tuple[int, frozenset | None]) -> bool:
        if new[0] != old[0]:
            return new[0] > old[0]
        return old[1] is not None and (new[1] is None or not new[1] <= old[1])

    while q:
        cur = q.popleft()
        b, filt = state[cur]
        if b <= 0:
            continue
        if cur not in memo:
            memo[cur] = text_referencers(g, cur, seg_index)
        nxt: dict[str, tuple[str, int, frozenset | None]] = {}
        for src, why in g.rdeps.get(cur, {}).items():
            if filt is None or filt & _idents(g, src):
                nxt[src] = (why, dec(b), None)
        for src, scopes in g.lazy.get(cur, {}).items():
            if filt is not None and not filt & _idents(g, src):
                continue
            callers = _callers(g, src, scopes)
            nxt.setdefault(src, (f"lazily imports (in {', '.join(sorted(scopes))[:60]})",
                                 min(dec(b), LAZY_HOPS), callers))
        for src, why in memo[cur].items():
            if _is_test_file(src) or _runner(src):
                nxt.setdefault(src, (why, dec(b), None))        # a test / script that reads or runs it
            elif cur == start and src.endswith(".py"):
                nxt.setdefault(src, (why, min(dec(b), READER_HOPS), None))  # a module that reads it
            # a workflow / unit / compose file naming it is not followed: tests that read THOSE as text
            # are not affected by the named file's content
        for src, (why, nb, nf) in nxt.items():
            if src in g.aggregators and src != start:
                # main.py / routers/__init__.py: only their DIRECT importers (a chain of aggregators,
                # routers/__init__ -> main, counts as one)
                nb = b if cur in g.aggregators else min(nb, 1)
            if src not in state or better((nb, nf), state[src]):
                seen[src], state[src] = (cur, why), (nb, nf)
                q.append(src)
    return seen


def _idents(g: Graph, rel: str) -> frozenset:
    if rel not in g.idents_cache:
        rec = g.recs.get(rel, {})
        g.idents_cache[rel] = frozenset(rec.get("idents", ())) | frozenset(rec.get("strings", ()))
    return g.idents_cache[rel]


def _callers(g: Graph, rel: str, scopes: set[str]) -> frozenset:
    """``scopes`` plus every function in ``rel`` that (transitively) calls one of them by name."""
    defs = g.recs.get(rel, {}).get("defs", {})
    out = set(scopes)
    changed = True
    while changed:
        changed = False
        for name, used in defs.items():
            if name not in out and out.intersection(used):
                out.add(name)
                changed = True
    return frozenset(out)


def _explain(seen: dict[str, tuple[str, str]], node: str) -> str:
    """'imports a.py <- imports b.py (changed)' — the shortest path from the test to the change."""
    hops = []
    cur = node
    while True:
        via, why = seen[cur]
        if not via:
            break
        hops.append(f"{why} {via}")
        cur = via
    if len(hops) > 4:
        hops = hops[:2] + ["..."] + hops[-1:]
    return " -> ".join(hops) + " (changed)"


def select(root: Path, changed: list[tuple[str, str]], files: list[str] | None = None) -> Selection:
    sel = Selection(changed=changed)
    files = files if files is not None else repo_files(root)
    deleted = [p for s, p in changed if s == "D"]
    g = build_graph(root, files, extra=deleted)
    seg_index = segment_index(g)
    paths = [p for _, p in changed]
    kinds = {p: _kind(p) for p in paths}

    for p, k in kinds.items():
        if k == "unknown" and p.startswith("labs/"):
            continue   # labs/ is isolated from the runtime and no CI lane runs it; it is reported as unreached
        if k == "unknown":
            # a binary / model / unrecognised artefact: text references cannot establish its runtime consumers
            sel.full_all_reason = f"{p}: unknown file type (static selection cannot see who loads it)"
        elif k == "global-config":
            # root config reaches every lane; a nested one (a service's pyproject / pytest.ini) the lanes
            # that load that service - or every lane when it sits outside any service
            owner = [] if "/" not in p else _consuming_lanes(p)
            if owner:
                for ln in owner:
                    sel.lane_full.setdefault(ln.name, f"{p} changed (pytest/packaging config)")
            elif not p.startswith("labs/"):
                sel.full_all_reason = f"{p} changed (pytest/packaging config affects every lane)"
        elif k == "deps":
            owner = _consuming_lanes(p)
            if owner:
                for ln in owner:
                    sel.lane_full.setdefault(ln.name, f"{p} changed (lane dependencies)")
            else:
                sel.full_all_reason = sel.full_all_reason or f"{p} changed (dependency manifest outside a lane)"
        elif k == "migration":
            # a migration changes the schema every DB-backed test builds (#2323: a new 0070 revision broke
            # eleven migration / data-model tests that never name it): every lane that runs that service's code
            for ln in _consuming_lanes(p) or LANES:
                sel.lane_full.setdefault(ln.name, f"{p} changed (a schema migration)")
        elif k == "conftest":
            hit = [ln for ln in LANES if p in _conftests_for(g, ln)]
            for ln in hit:
                sel.lane_full.setdefault(ln.name, f"{p} changed (conftest = shared fixtures)")
    if sel.full_all_reason:
        return sel

    memo: dict[str, dict[str, str]] = {}
    per_file = {p: _closure(g, p, seg_index, memo) for p in paths}
    origin: dict[str, str] = {}
    for p in paths:
        for node in per_file[p]:
            origin.setdefault(node, p)   # the first changed file that reaches it explains it
    seen = origin
    any_structural = any(s in ("A", "D") for s, _ in changed)
    non_doc = [p for p in paths if kinds[p] != "doc"]

    for lane in LANES:
        if lane.name in sel.lane_full:
            continue
        lane_tests = _lane_tests(g, lane)
        if not lane_tests:
            continue
        for c in _conftests_for(g, lane):
            hit = sorted(set(conftest_direct_deps(g, c)) & set(paths))
            if hit:
                sel.lane_full[lane.name] = f"{hit[0]} changed and {c} uses it directly (shared fixture)"
                break
        if lane.name in sel.lane_full:
            continue
        chosen: dict[str, str] = {}
        for t in lane_tests:
            if t in seen:
                chosen[t] = "changed" if t in paths else _explain(per_file[origin[t]], t)
            elif (non_doc or any_structural) and g.recs.get(t, {}).get("scanner"):
                chosen[t] = "walks the repo tree (scanner test; runs on every non-doc/add/delete diff)"
            elif not g.recs.get(t, {}).get("ok", True):
                chosen[t] = "could not be parsed (its imports are unknown)"
        if not chosen:
            continue
        if len(chosen) > HUB_FRACTION * len(lane_tests) and len(lane_tests) > HUB_MIN_LANE:
            sel.lane_full[lane.name] = (f"selection covers {len(chosen)}/{len(lane_tests)} tests "
                                        f"(> {int(HUB_FRACTION * 100)}%): a hub module changed")
            continue
        keep = {}
        for t, why in sorted(chosen.items()):
            if _lane_has_marker(root, lane, t):
                keep[t] = why
            else:
                sel.unmarked.setdefault(lane.name, []).append(t)
        if keep:
            sel.tests[lane.name] = keep

    # "reached" = a test that will actually RUN reaches it: a kept (marker-passing) selection, or any test of a
    # lane that already runs full. A change whose only reachers are unmarked tests is not covered by a run.
    ran = {t for ts in sel.tests.values() for t in ts}
    full_tests = {t for ln in LANES if ln.name in sel.lane_full for t in _lane_tests(g, ln)}
    for p in paths:
        k = kinds[p]
        if k in ("doc", "conftest", "deps", "global-config", "migration"):
            continue
        if (ran | full_tests) & set(per_file[p]):
            continue
        sel.unreached.append(p)
        if _is_test_file(p):
            continue   # a test no validate lane runs (unmarked, outside only_files, deleted): named, not a lane
        if p.endswith(CODE_EXT) and not p.startswith("labs/"):
            # CODE no test reaches statically may still be reached at runtime (a registry, a built path):
            # never a green zero-test run for it - run the lane(s) that load its tree
            # (a service no lane owns - omi-sync, zoe-vaultd, panel-bluetooth, zoe-ui - is tested from tests/unit)
            for ln in (_consuming_lanes(p) or [x for x in LANES if x.name == "tests-unit"]):
                sel.lane_full.setdefault(ln.name, f"{p} changed and no test reaches it statically")
    if not non_doc and not sel.tests and not sel.lane_full:
        sel.notes.append("docs-only diff: no test reads the changed docs, so no tests are affected")
    return sel


# ------------------------------------------------------------------ running

def lane_command(lane: Lane, tests: list[str] | None, passthrough: list[str]) -> list[str]:
    targets = tests if tests is not None else (list(lane.only_files) or [lane.test_root])
    cmd = [sys.executable, "-m", "pytest", *targets, "-q", "-o", "faulthandler_timeout=300"]
    if lane.marker:
        cmd += ["-m", lane.marker]
    for i in lane.ignore:
        cmd += [f"--ignore={i}"]
    cmd += list(lane.extra_args) + list(passthrough)
    return cmd


def lane_env(root: Path, lane: Lane) -> dict[str, str]:
    env = dict(os.environ)
    env.update(LANE_ENV)
    env["PYTHONPATH"] = os.pathsep.join(str(root / p) if p != "." else str(root) for p in lane.pythonpath)
    return env


def _mem_available_mb() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        return None
    return None


def wait_for_box(lander_wait_s: int, mem_min_mb: int, mem_wait_s: int) -> None:
    """landings outrank test runs; low memory waits (bounded, then proceeds)."""
    deadline = time.monotonic() + lander_wait_s
    announced = False
    while lander_wait_s > 0 and time.monotonic() < deadline:
        r = subprocess.run(["pgrep", "-f", LANDER_RE], capture_output=True)
        if r.returncode != 0:
            break
        if not announced:
            log("[affected-tests] a voice landing is running; waiting for it (landings outrank test runs)")
            announced = True
        time.sleep(30)
    deadline = time.monotonic() + mem_wait_s
    while mem_min_mb > 0 and time.monotonic() < deadline:
        mem = _mem_available_mb()
        if mem is None or mem >= mem_min_mb:
            return
        log(f"[affected-tests] MemAvailable {mem} MB < {mem_min_mb} MB; waiting")
        time.sleep(30)


class LaneLock:
    def __init__(self, path: str | None, timeout_s: int):
        self.path, self.timeout_s, self.fd = path, timeout_s, None

    def __enter__(self):
        if not self.path:
            return self
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o666)
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(self.fd)
                    raise TimeoutError(f"could not take {self.path} within {self.timeout_s}s")
                time.sleep(2)
            except BaseException:
                os.close(self.fd)
                raise

    def __exit__(self, *exc):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)


def _box_busy(mem_min_mb: int) -> str | None:
    if subprocess.run(["pgrep", "-f", LANDER_RE], capture_output=True).returncode == 0:
        return "a voice landing is running"
    mem = _mem_available_mb()
    if mem_min_mb and mem is not None and mem < mem_min_mb:
        return f"MemAvailable {mem} MB < {mem_min_mb} MB"
    return None


class _lane_slot:
    """The test-lane lock, taken only while the box is free: wait out a lander / low memory, take the lock,
    then RE-CHECK under it (a lander may have started during a long lock wait); if the box turned busy,
    release and wait again. Bounded by --lander-wait; after that the run proceeds (it is already niced)."""

    def __init__(self, a):
        self.a, self.lock = a, None

    def __enter__(self):
        a = self.a
        if a.no_lock:
            return self
        deadline = time.monotonic() + a.lander_wait
        while True:
            wait_for_box(max(0, int(deadline - time.monotonic())), a.mem_min_mb, a.mem_wait)
            self.lock = LaneLock(LOCK_PATH, a.lock_timeout)
            self.lock.__enter__()
            busy = _box_busy(a.mem_min_mb)
            if not busy or time.monotonic() >= deadline:
                return self
            log(f"[affected-tests] {busy} after taking the lane lock; releasing it and waiting")
            self.lock.__exit__(None, None, None)
            self.lock = None
            time.sleep(30)

    def __exit__(self, *exc):
        if self.lock is not None:
            self.lock.__exit__(*exc)


def plan(sel: Selection, root: Path, passthrough: list[str]) -> list[dict]:
    out = []
    for lane in LANES:
        if sel.full_all_reason or lane.name in sel.lane_full:
            mode, tests = "full", None
            reason = sel.full_all_reason or sel.lane_full[lane.name]
        elif lane.name in sel.tests:
            mode, tests, reason = "selected", sorted(sel.tests[lane.name]), None
        else:
            continue
        out.append({"lane": lane.name, "mode": mode, "reason": reason, "tests": tests,
                    "why": sel.tests.get(lane.name, {}) if mode == "selected" else {},
                    "command": lane_command(lane, tests, passthrough), "rc": None, "duration_s": None})
    return out


def report(sel: Selection, lanes: list[dict]) -> None:
    log(f"[affected-tests] base={sel.base} merge-base={(sel.merge_base or '')[:12]} "
        f"changed={len(sel.changed)} file(s)")
    for st, p in sel.changed[:40]:
        log(f"    {st} {p}")
    if len(sel.changed) > 40:
        log(f"    ... {len(sel.changed) - 40} more")
    if sel.full_all_reason:
        log(f"[affected-tests] FULL FALLBACK (all lanes): {sel.full_all_reason}")
    for ln in lanes:
        if ln["mode"] == "full":
            log(f"[affected-tests] lane {ln['lane']}: FULL ({ln['reason']})")
        else:
            log(f"[affected-tests] lane {ln['lane']}: {len(ln['tests'])} test file(s) selected")
            for t in ln["tests"]:
                log(f"    {t}  <- {ln['why'][t]}")
    for lane, ts in sel.unmarked.items():
        log(f"[affected-tests] lane {lane}: {len(ts)} affected file(s) are not ci_safe-marked "
            f"(validate never runs them; the Jetson catch-all does): {', '.join(ts[:8])}")
    for p in sel.unreached:
        log(f"[affected-tests] NOTE: no test reaches {p} (no test imports or names it)")
    for n in sel.notes:
        log(f"[affected-tests] {n}")
    if not lanes:
        log("[affected-tests] nothing to run")


def summary(sel: Selection, lanes: list[dict], rc: int, dry_run: bool, error: str | None = None) -> dict:
    mode = "full" if sel.full_all_reason else ("none" if not lanes else
                                               ("full" if all(x["mode"] == "full" for x in lanes) else "selected"))
    return {"schema": SUMMARY_SCHEMA, "base": sel.base, "merge_base": sel.merge_base,
            "changed": [{"status": s, "path": p} for s, p in sel.changed], "mode": mode,
            "fallback_reason": sel.full_all_reason,
            "lanes": [{k: v for k, v in ln.items()} for ln in lanes],
            "unmarked": sel.unmarked, "unreached": sel.unreached, "notes": sel.notes,
            "dry_run": dry_run, "error": error, "rc": rc}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    passthrough: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, passthrough = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", nargs="?", help="local base ref/commit (default: origin/main, main, master)")
    ap.add_argument("--files", nargs="+", help="treat these repo-relative paths as the change (no git)")
    ap.add_argument("--full", action="store_true", help="run the full lanes")
    ap.add_argument("--dry-run", action="store_true", help="select and report, do not run pytest")
    ap.add_argument("--json", metavar="FILE", help="write a JSON summary to FILE ('-' = stdout)")
    ap.add_argument("--repo", default=str(REPO), help=argparse.SUPPRESS)
    ap.add_argument("--no-lock", action="store_true", help="do not take the test-lane lock (tests only)")
    ap.add_argument("--lock-timeout", type=int, default=3600)
    ap.add_argument("--lander-wait", type=int, default=3600, help="max seconds to wait out a voice landing")
    ap.add_argument("--mem-min-mb", type=int, default=1200)
    ap.add_argument("--mem-wait", type=int, default=1800)
    a = ap.parse_args(argv)
    _OUT[0] = a.json == "-"
    xdist = [x for x in passthrough if x == "-n" or re.match(r"^(-n\S+|--numprocesses|--dist|-d$)", x)]
    if xdist:
        # parallel pytest on this box once hard-hung it; the lane lock assumes ONE process
        ap.error(f"refusing pytest-xdist passthrough {xdist}: test lanes run single-process (tests/AGENTS.md)")
    root = Path(a.repo).resolve()

    sel = Selection()
    error = None
    if a.files:
        sel.base = "--files"
        changed = [("D" if not (root / f).exists() else "M", f) for f in a.files]
    else:
        sel.base = resolve_base(root, a.base)
        changed = []
        if sel.base is None:
            error = f"base ref {a.base or '(origin/main|main|master)'} does not resolve locally"
        else:
            try:
                sel.merge_base, changed = changed_files(root, sel.base)
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                error = f"git diff failed: {getattr(exc, 'stderr', '') or exc}".strip()
    if a.full:
        sel.full_all_reason = "--full requested"
        sel.changed = changed
    elif error:
        sel.full_all_reason = f"{error}; cannot know the change"
    else:
        try:
            files = repo_files(root)
            s2 = select(root, changed, files)
            s2.base, s2.merge_base = sel.base, sel.merge_base
            sel = s2
        except Exception as exc:  # noqa: BLE001 - any selector bug must fail SAFE (full lanes), never select zero
            sel.changed = changed
            sel.full_all_reason = f"selector error ({type(exc).__name__}: {exc}); running full lanes"

    lanes = plan(sel, root, passthrough)
    report(sel, lanes)
    rc = 0
    if not a.dry_run and lanes:
        for ln in lanes:
            lane = next(x for x in LANES if x.name == ln["lane"])
            log(f"[affected-tests] running lane {lane.name}: {' '.join(ln['command'][1:])}")
            t0 = time.monotonic()
            try:
                with _lane_slot(a):
                    cmd = ln["command"]
                    if not a.no_lock:
                        cmd = ["nice", "-n", "10", *cmd]
                    r = subprocess.run(cmd, cwd=root, env=lane_env(root, lane),
                                       stdout=sys.stderr if a.json == "-" else None)
                    ln["rc"] = r.returncode if r.returncode >= 0 else 128 - r.returncode   # signal -> 128+N
            except TimeoutError as exc:
                ln["rc"] = 75
                error = str(exc)
                log(f"[affected-tests] {exc}")
            ln["duration_s"] = round(time.monotonic() - t0, 1)
            log(f"[affected-tests] lane {lane.name}: rc={ln['rc']} in {ln['duration_s']}s")
            if ln["rc"] and not rc:
                rc = ln["rc"]
    summ = summary(sel, lanes, rc, a.dry_run, error)
    if a.json == "-":
        print(json.dumps(summ, indent=1))
    elif a.json:
        try:
            Path(a.json).write_text(json.dumps(summ, indent=1) + "\n")
        except OSError as exc:   # never let the summary mask pytest's rc
            log(f"[affected-tests] could not write {a.json}: {exc}")
            rc = rc or 74
    log(f"[affected-tests] rc={rc}" + (" (dry run)" if a.dry_run else ""))
    return rc


if __name__ == "__main__":
    sys.exit(main())
