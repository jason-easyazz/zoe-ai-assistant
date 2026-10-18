#!/usr/bin/env python3
"""Flag inventory, conflicting-default check, legacy-read ratchet, registry sync (audit P1-9, deep dive B6).

Scans sources for ``ZOE_*`` environment-variable reads —
``os.environ.get(...)``, ``os.getenv(...)``, ``os.environ["..."]``, ``environ.get(...)``,
the ``typed_env`` accessors (``env_str/env_bool/env_int/env_float/env_list`` and the
registry-driven ``flag``), module-local ``(name, default)`` wrappers, and TypeScript
``process.env.X`` — and joins them with the flag REGISTRY
(``services/zoe-data/flag_registry.py``: each flag declares name, type, default, allowed
values and owner doc once). It emits a machine JSON plus a human markdown table
(``docs/knowledge/flag-inventory.md``). The declared columns come from the registry; the
scanner supplies the facts (readers, the defaults each reader hard-codes, typed_env use).

Per flag: registry spec, statically-extracted read defaults (honest ``dynamic`` when the
default expression is not a plain literal, ``(required)`` for bare ``os.environ[...]``
subscripts), reader modules, whether any reader goes through ``typed_env``, and whether
the flag is documented in ``.env.example`` / ``services/zoe-data/.env.example``.

Also computed here (and enforced by ``tests/unit/test_flag_registry.py``):

- **conflicts** — a flag whose readers hard-code different EFFECTIVE defaults (``'1'`` and
  ``True`` are the same default; ``''`` and no default are both "unset"; ``dynamic`` is not
  comparable and is skipped). Each must match the registry default or be explicitly
  acknowledged there (``divergent_ok``).
- **legacy reads** — per-file count of ``ZOE_*`` read sites in prod code that do NOT go
  through ``typed_env``. ``docs/knowledge/flag-ratchet.json`` is the ceiling; it may only go
  down (``--tighten-ratchet`` lowers it after a migration).
- **other env** — non-``ZOE_`` names read by prod code (they gate live paths too).

Best-effort matching: calls are matched by NAME (``getenv``, the typed_env accessor names),
not by originating module — a local helper or mock that happens to share a name is counted
too, so the scanner can over-report. Shell scripts, systemd ``Environment=`` lines and
``in os.environ`` membership tests are out of scope (they set or probe, they do not read a
typed value).

Scope: files under ``labs/`` are inventoried in a separate LAB section; test files (any
path containing a ``tests`` directory or ``test_*.py``) are excluded — they set flags
rather than define runtime behaviour. Everything else is "prod".

Deterministic by construction: output is sorted, and no timestamps appear in the table
body (the generated-on date lives only in the frontmatter/status note, refreshed on
regeneration).

Usage:
    python3 tools/audit/flag_inventory.py [--repo PATH]
        [--json OUT.json] [--markdown OUT.md] [--check-only]
        [--sync-registry]      # add newly-read flags to the registry (never edits existing entries)
        [--tighten-ratchet]    # lower flag-ratchet.json to the current legacy-read counts

Do NOT hand-edit the generated markdown — rerun this tool.
"""
from __future__ import annotations

import argparse
import ast
import datetime as _dt
import importlib.util
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

TYPED_ENV_FUNCS = {"env_str", "env_bool", "env_int", "env_float", "env_list", "env_float_failsafe", "flag"}
FLAG_RE = re.compile(r"^ZOE_[A-Z0-9_]+$")
# Any upper-case env name; ZOE_* ones are flags, the rest are "other env".
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
ENV_EXAMPLE_RE = re.compile(r"\s*#?\s*([A-Z][A-Z0-9_]+)\s*=")
DYNAMIC = "dynamic"
REQUIRED = "(required)"
NO_DEFAULT = "-"

REGISTRY_REL = "services/zoe-data/flag_registry.py"
RATCHET_REL = "docs/knowledge/flag-ratchet.json"
REGISTRY_MARKER = "# --- BEGIN REGISTERED FLAGS (tools/audit/flag_inventory.py --sync-registry appends; edit entries in place) ---"

TS_EXTS = (".ts", ".tsx", ".mjs", ".js", ".cjs")
TS_SKIP_PARTS = {"node_modules", "dist", "vendor", "static", "build", "coverage"}


def _literal_repr(node: ast.expr | None) -> str:
    """Render a default expression: literal → repr, anything else → dynamic."""
    if node is None:
        return NO_DEFAULT
    try:
        return repr(ast.literal_eval(node))
    except (ValueError, SyntaxError):
        return DYNAMIC


def _typed_delegators(tree: ast.AST) -> frozenset[str]:
    """Names of module-local wrappers that are bare delegations to typed_env.

    Matches only the trivial shape ``def w(...): return env_x(...)`` (docstring
    allowed) — anything with extra logic stays untyped, which errs toward
    under-counting typed adoption rather than over-counting it.
    """
    out = set()
    # MODULE-LEVEL functions only, not ast.walk: a nested bare delegator (e.g. a
    # local `_env_int` helper inside some function) registers its NAME, and a
    # raw module-level wrapper sharing that name would then wrongly type every
    # read through it (Codex, #1577 — the name-collision class).
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = [st for st in node.body
                if not (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant))]
        # The delegation must FORWARD the wrapper's first parameter as the typed
        # call's name argument: `def enabled(label): return env_str("FIXED")` is a
        # bare delegation by shape but reads a name its callers never passed —
        # classifying it would record their first args as typed flag reads of
        # names the wrapper ignores (Codex, #1577).
        params = [a.arg for a in node.args.args]
        if (params and len(body) == 1 and isinstance(body[0], ast.Return)
                and isinstance(body[0].value, ast.Call)
                and _FlagVisitor._call_name(body[0].value.func) in TYPED_ENV_FUNCS
                and body[0].value.args
                and isinstance(body[0].value.args[0], ast.Name)
                and body[0].value.args[0].id == params[0]):
            out.add(node.name)
    return frozenset(out)


def _flag_constants(tree: ast.AST) -> dict[str, str]:
    """Module-level ``NAME = "ZOE_X"`` constants (also annotated), so a read through the constant is attributed to the flag.

    Only names assigned EXACTLY ONCE at module level: a constant that is rebound anywhere at module level could mean something else at the read, and
    an unresolved read is only ever under-counted (the honest direction). Found 2026-10-18: ``ZOE_SEAM_RECALL_INJECT``, live in the brain lane, was absent
    from the inventory because ``zoe_flue_client.py`` reads it as ``os.environ.get(_RECALL_INJECT_ENV)``. The constant may hold ANY upper-case env name
    (non-``ZOE_`` reads through a constant are inventoried too).
    """
    seen: dict[str, list[str | None]] = {}
    for node in ast.iter_child_nodes(tree):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                flag = value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) and ENV_NAME_RE.match(value.value) else None
                seen.setdefault(t.id, []).append(flag)
    return {name: vals[0] for name, vals in seen.items() if len(vals) == 1 and vals[0]}


def _module_literals(tree: ast.AST) -> dict[str, ast.expr]:
    """Module-level ``NAME = <literal>`` bindings assigned exactly once (resolves ``... or DEFAULT_TZ`` defaults)."""
    seen: dict[str, list[ast.expr]] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                seen.setdefault(t.id, []).append(value)
    out: dict[str, ast.expr] = {}
    for name, vals in seen.items():
        if len(vals) == 1:
            try:
                ast.literal_eval(vals[0])
            except (ValueError, SyntaxError):
                continue
            out[name] = vals[0]
    return out


def _environ_aliases(tree: ast.AST) -> tuple[frozenset[str], frozenset[str]]:
    """Names this module binds to ``os.environ`` / ``os.getenv`` (``from os import environ as E``, ``getenv as ge``,
    ``env = os.environ``), so an aliased read is still a read. Computed names and loop variables stay invisible."""
    environ_names, getenv_names = {"environ"}, {"getenv"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            for a in node.names:
                if a.name == "environ":
                    environ_names.add(a.asname or a.name)
                elif a.name == "getenv":
                    getenv_names.add(a.asname or a.name)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute) and node.value.attr == "environ":
            environ_names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return frozenset(environ_names), frozenset(getenv_names)


_NAME_KWARGS = ("key", "name", "var", "env", "env_var")
_PEEL_ATTRS = {"strip", "lower", "upper", "casefold", "rstrip", "lstrip"}


def _peel(v: ast.expr) -> ast.expr:
    """``os.environ.get("X", "").strip()`` → the inner ``get`` call (so ``... or "d"`` is attributed to it)."""
    while isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr in _PEEL_ATTRS:
        v = v.func.value
    return v


class _FlagVisitor(ast.NodeVisitor):
    def __init__(self, typed_delegators: frozenset[str] = frozenset(), flag_constants: dict[str, str] | None = None,
                 literals: dict[str, ast.expr] | None = None,
                 aliases: tuple[frozenset[str], frozenset[str]] | None = None) -> None:
        self._environ_names, self._getenv_names = aliases or (frozenset({"environ"}), frozenset({"getenv"}))
        self._consts = flag_constants or {}
        self._lits = literals or {}
        # id(env-read Call) -> the literal on the right of ``read or <literal>`` (the effective default)
        self._or_default: dict[int, ast.expr] = {}
        # (name, default_str, via_typed_env) for ZOE_* names; ``other`` is the
        # same shape for every other upper-case env name (strict read shapes only).
        self.reads: list[tuple[str, str, bool]] = []
        self.other: list[tuple[str, str, bool]] = []
        # Local wrappers in THIS module whose body is a bare delegation to a
        # typed_env helper (``def _env_int(n, d): return env_int(n, d)``) —
        # reads through them ARE typed (Greptile P1 on #1575: voice_tts/main.py
        # delegators were inflating the untyped count).
        self._typed_delegators = typed_delegators

    def _env_name(self, expr: ast.expr) -> str | None:
        """The env name a read's key expression denotes: a string literal, or a module-level constant that holds one."""
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str) and ENV_NAME_RE.match(expr.value):
            return expr.value
        if isinstance(expr, ast.Name):
            return self._consts.get(expr.id)
        return None

    @staticmethod
    def _call_name(func: ast.expr) -> str:
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
        return ""

    def _is_environ_get(self, func: ast.expr) -> bool:
        # os.environ.get(...) / .setdefault(...) or an alias (``from os import environ as E``) of it
        if not (isinstance(func, ast.Attribute) and func.attr in ("get", "setdefault")):
            return False
        v = func.value
        return (isinstance(v, ast.Attribute) and v.attr == "environ") or (isinstance(v, ast.Name) and v.id in self._environ_names)

    def _record(self, name: str, default: str, typed: bool) -> None:
        (self.reads if FLAG_RE.match(name) else self.other).append((name, default, typed))

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if isinstance(node.op, ast.Or):
            for left, right in zip(node.values, node.values[1:]):
                inner = _peel(left)
                lit = self._lits.get(right.id, right) if isinstance(right, ast.Name) else right
                if isinstance(lit, ast.Constant) and lit.value in ("", None):
                    continue  # ``x or ''`` adds no default: unset stays unset
                if isinstance(inner, ast.Call):
                    self._or_default[id(inner)] = lit
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = self._call_name(node.func)
        typed = name in TYPED_ENV_FUNCS or name in self._typed_delegators
        # Local safe-parse wrappers (the Pi daemon's _int_env-style helpers) take
        # (name, default) like getenv — without this, a flag read through one is
        # INVISIBLE to the inventory. Found the hard way: ZOE_VAD_TAIL_MS, the
        # main rollback knob of the fast-tail change, was absent from the
        # committed inventory because it used _int_env (Bugbot, #1573) — and
        # ZOE_TTS_KEEP_TAIL_MS / ZOE_TTS_LEAD_GUARD_MS had been missing all along.
        # Full sweep of the repo's (name, default)-shaped wrapper spellings
        # (2026-07-27, follow-up to the daemon trio): kokoro_sidecar and
        # greploop_guard's _env_int/_env_flag, main.py/voice_tts/pi_intent_lab's
        # _env_float/_env_int/_env_bool, agent_sync's _env_enabled,
        # logging_setup's _int_from_env, memory_idle_consolidation's _int_env.
        # SAFE against the (value, default)-shaped coercers that share these
        # names in the pi_* modules: those call sites pass variables, and the
        # ZOE_* string-constant filter below skips any non-constant first arg —
        # so a name can be on this list even when another module reuses it for
        # a different shape.
        strict_get = name in self._getenv_names or self._is_environ_get(node.func)
        wrapper_get = name in ("_int_env", "_float_env", "_env",
                               "_env_int", "_env_float", "_env_bool", "_env_flag",
                               "_env_enabled", "_int_from_env")
        first = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg in _NAME_KWARGS), None)
        if (typed or strict_get or wrapper_get) and first is not None:
            env = self._env_name(first)
            # Loose wrappers only count for ZOE_* names: a generic `_env("FOO")`
            # helper for something else must not flood the non-ZOE list.
            if env and (FLAG_RE.match(env) or typed or strict_get):
                default: ast.expr | None = None
                if len(node.args) > 1:
                    default = node.args[1]
                else:
                    for kw in node.keywords:
                        if kw.arg == "default":
                            default = kw.value
                # ``environ.get("X") or "d"`` / ``environ.get("X", "") or "d"``: the right-hand literal is the effective default.
                ord_ = self._or_default.get(id(node))
                if ord_ is not None and (default is None or (isinstance(default, ast.Constant) and default.value in ("", None))):
                    default = ord_
                if isinstance(default, ast.Name) and default.id in self._lits:
                    default = self._lits[default.id]
                # ``environ.setdefault("X", v)`` SETS v for child processes: it is not a reader's default, so not comparable.
                is_set = isinstance(node.func, ast.Attribute) and node.func.attr == "setdefault"
                self._record(env, DYNAMIC if is_set else _literal_repr(default), typed)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # os.environ["ZOE_X"] — reads only. Store/Del contexts are WRITES
        # (`os.environ["ZOE_X"] = v`, `del os.environ["ZOE_X"]`); recording
        # them as (required) reads told operators the process crashes
        # without a flag it actually sets itself.
        if not isinstance(node.ctx, ast.Load):
            self.generic_visit(node)
            return
        v = node.value
        if (isinstance(v, ast.Attribute) and v.attr == "environ") or (isinstance(v, ast.Name) and v.id in self._environ_names):
            env = self._env_name(node.slice)
            if env:
                self._record(env, REQUIRED, False)
        self.generic_visit(node)


# ---- TypeScript / JavaScript reads (process.env.X) ------------------------------------------

_TS_READ_RE = re.compile(
    r"""process\.env(?:\.([A-Za-z_][A-Za-z0-9_]*)|\[\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]\s*\])"""
    r"""(?P<tail>\s*(?:\?\?|\|\|)\s*(?P<lit>"[^"\n]*"|'[^'\n]*'|`[^`$\n]*`|-?\d[\d_]*(?:\.\d+)?|true\b|false\b|null\b|[^\s,;)\]]+))?"""
)
_TS_WRITE_RE = re.compile(r"\s*=(?!=)")


def _ts_default(lit: str | None) -> str:
    if lit is None:
        return NO_DEFAULT
    if lit in ("true", "false"):
        return repr(lit == "true")
    if lit == "null":
        return NO_DEFAULT
    if lit[0] in "\"'`" and lit[-1] == lit[0] and len(lit) >= 2:
        return repr(lit[1:-1])
    try:
        return repr(ast.literal_eval(lit.replace("_", "")))
    except (ValueError, SyntaxError):
        return DYNAMIC


def scan_ts_text(text: str) -> list[tuple[str, str, bool]]:
    """``process.env.X`` reads in TS/JS source → ``(name, default, False)``; assignments (writes) are skipped."""
    out: list[tuple[str, str, bool]] = []
    for m in _TS_READ_RE.finditer(text):
        name = m.group(1) or m.group(2)
        if not ENV_NAME_RE.match(name):
            continue
        if m.group("tail") is None and _TS_WRITE_RE.match(text, m.end()):
            continue  # process.env.X = ... is a write
        out.append((name, _ts_default(m.group("lit")), False))
    return out


def _is_test_path(rel: str) -> bool:
    parts = Path(rel).parts
    name = Path(rel).name
    return "tests" in parts or "test" in parts or name.startswith("test_") or ".test." in name or ".spec." in name


def _section(rel: str) -> str | None:
    if _is_test_path(rel):
        return None
    return "lab" if Path(rel).parts[:1] == ("labs",) else "prod"


# ---- registry + default normalisation --------------------------------------------------------

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
_SKIP = object()


def normalize_default(v: object) -> object:
    """Normalise a default VALUE to a comparable one.

    ``'1'`` ≡ ``True`` ≡ ``1``; ``''`` ≡ no default ≡ ``None``; numeric strings → numbers; empty
    sequences → ``None``. ``None`` and any falsy scalar compare equal (see :func:`distinct_defaults`)
    — an unset read and an explicit off/zero default behave the same for a flag.
    """
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if s.lower() in _TRUTHY:
            return True
        if s.lower() in _FALSY:
            return False
        try:
            return int(s)
        except ValueError:
            try:
                f = float(s)
            except ValueError:
                return s
            return f if f == f and f not in (float("inf"), float("-inf")) else s  # 'nan'/'inf' are words, not numbers
    if isinstance(v, (list, tuple)):
        return tuple(v) or None
    if isinstance(v, dict):
        return repr(v)
    return v


def effective_default(d: str) -> object:
    """A scanner default string (``repr`` of a literal, ``-``, ``dynamic``, ``(required)``) → comparable
    value, or ``_SKIP`` when it is not comparable (``dynamic``/``(required)``/unparseable)."""
    if d in (DYNAMIC, REQUIRED):
        return _SKIP
    if d == NO_DEFAULT:
        return None
    try:
        return normalize_default(ast.literal_eval(d))
    except (ValueError, SyntaxError):
        return _SKIP


def distinct_defaults(values) -> list[object]:
    """Distinct values from already-normalised defaults (``_SKIP`` ignored; ``None`` folded into a falsy sibling)."""
    vals: list[object] = []
    for v in values:
        if v is _SKIP or any(v == x for x in vals):
            continue
        vals.append(v)
    falsy_scalar = [x for x in vals if x is not None and not isinstance(x, (str, tuple)) and not x]
    if None in vals and falsy_scalar:
        vals = [x for x in vals if x is not None]
    return vals


def _display(v: object) -> str:
    return "unset" if v is None else repr(v)


def load_registry(repo: Path) -> dict[str, dict] | None:
    """The declared flag registry as ``{name: spec dict}``, or None when the repo has none (fixture repos)."""
    path = repo / REGISTRY_REL
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("_flag_registry_for_inventory", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations through sys.modules
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(spec.name, None)
    return {n: s.as_dict() for n, s in mod.FLAGS.items()}


# ---- scan ---------------------------------------------------------------------------------

def _tracked_files(repo: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.py", *[f"*{e}" for e in TS_EXTS]],
            cwd=repo, capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"git ls-files failed in {repo} (not a git worktree?): "
            f"{exc.stderr.strip() or exc} — run from the repo root or pass files explicitly"
        ) from exc
    return [f for f in out.stdout.splitlines() if f]


def _ts_scannable(rel: str) -> bool:
    p = Path(rel)
    if p.suffix not in TS_EXTS or TS_SKIP_PARTS & set(p.parts) or ".min." in p.name:
        return False
    return p.parts[:2] != ("services", "zoe-ui")  # browser JS has no process.env


def _documented(repo: Path) -> set[str]:
    documented: set[str] = set()
    for env_example in (repo / ".env.example", repo / "services" / "zoe-data" / ".env.example"):
        if env_example.exists():
            for line in env_example.read_text(encoding="utf-8").splitlines():
                m = ENV_EXAMPLE_RE.match(line)
                if m:
                    documented.add(m.group(1))
    return documented


def scan_repo(repo: Path, files: list[str] | None = None, registry: dict[str, dict] | None = None) -> dict:
    """Scan sources; return the inventory (flags, other env, conflicts, legacy-read counts).

    ``registry`` defaults to the repo's own ``flag_registry.py`` when it has one.
    """
    if files is None:
        files = _tracked_files(repo)
    if registry is None:
        registry = load_registry(repo)

    documented = _documented(repo)

    inventory: dict[str, dict[str, dict]] = {"prod": {}, "lab": {}}
    other: dict[str, dict] = {}
    by_file: dict[str, dict[str, set[str]]] = {}   # prod flag -> {file: defaults}
    legacy: Counter = Counter()                   # prod file -> non-typed ZOE_* read sites

    for rel in sorted(files):
        section = _section(rel)
        if section is None:
            continue
        path = repo / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if rel.endswith(".py"):
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            visitor = _FlagVisitor(_typed_delegators(tree), _flag_constants(tree), _module_literals(tree), _environ_aliases(tree))
            visitor.visit(tree)
            reads, others = visitor.reads, visitor.other
        elif _ts_scannable(rel):
            both = scan_ts_text(text)
            reads = [r for r in both if FLAG_RE.match(r[0])]
            others = [r for r in both if not FLAG_RE.match(r[0])]
        else:
            continue
        for flag, default, typed in reads:
            info = inventory[section].setdefault(
                flag, {"defaults": [], "readers": [], "typed_env": False, "in_env_example": flag in documented}
            )
            if default not in info["defaults"]:
                info["defaults"].append(default)
            if rel not in info["readers"]:
                info["readers"].append(rel)
            info["typed_env"] = info["typed_env"] or typed
            if section == "prod":
                by_file.setdefault(flag, {}).setdefault(rel, set()).add(default)
                if not typed:
                    legacy[rel] += 1
        if section == "prod":
            for name, default, typed in others:
                info = other.setdefault(name, {"defaults": [], "readers": [], "typed_env": False, "in_env_example": name in documented})
                if default not in info["defaults"]:
                    info["defaults"].append(default)
                if rel not in info["readers"]:
                    info["readers"].append(rel)
                info["typed_env"] = info["typed_env"] or typed

    for section_flags in (*inventory.values(), other):
        for info in section_flags.values():
            info["defaults"].sort()
            info["readers"].sort()

    # Registry join: the declared spec travels with each flag; flags the registry has never
    # heard of are marked (the CI test fails on them).
    if registry is not None:
        for section_flags in inventory.values():
            for flag, info in section_flags.items():
                info["registered"] = flag in registry
                info["spec"] = registry.get(flag)

    # Conflicts: prod flags whose readers hard-code different EFFECTIVE defaults.
    conflicts: dict[str, dict] = {}
    for flag, files_map in by_file.items():
        all_defaults = {d for ds in files_map.values() for d in ds}
        distinct = distinct_defaults(effective_default(d) for d in all_defaults)
        if len(distinct) > 1:
            entry: dict = {
                "effective": sorted(_display(v) for v in distinct),
                "sites": {f: sorted(ds) for f, ds in sorted(files_map.items())},
            }
            if registry is not None:
                spec = registry.get(flag)
                # Acknowledged = EVERY site that hard-codes a comparable default different from the declared one
                # (an unset read included: unset vs a non-zero literal IS a contradiction) is named in the
                # entry's divergent_ok. A non-empty divergent_ok that names some other file acknowledges nothing.
                unack: list[str] = []
                if spec:
                    want = normalize_default(spec["default"])
                    ack = spec.get("divergent_ok") or {}
                    unack = [f for f, ds in sorted(files_map.items()) if f not in ack and any(
                        effective_default(d) is not _SKIP and len(distinct_defaults([effective_default(d), want])) > 1
                        for d in ds)]
                entry["acknowledged"] = bool(spec) and not unack
                if unack:
                    entry["unacknowledged_sites"] = unack
            conflicts[flag] = entry

    # Divergences: a reader hard-codes a literal default that differs from the registry's declared
    # default (the registry is the single declaration; a flag marked divergent_ok is acknowledged).
    divergences: dict[str, list[list[str]]] = {}
    if registry is not None:
        for flag, files_map in by_file.items():
            spec = registry.get(flag)
            if not spec:
                continue
            want = normalize_default(spec["default"])
            ack = spec.get("divergent_ok") or {}
            # A site that hard-codes no value (unset / '' / dynamic) asserts nothing, so it cannot contradict
            # the declaration (it is still visible in ``conflicts``); only a differing literal does.
            bad = [[f, d] for f, ds in sorted(files_map.items()) if f not in ack for d in sorted(ds)
                   if effective_default(d) not in (None, _SKIP) and len(distinct_defaults([effective_default(d), want])) > 1]
            if bad:
                divergences[flag] = bad

    data: dict = {
        "flags": {k: dict(sorted(v.items())) for k, v in inventory.items()},
        "other_env": dict(sorted(other.items())),
        "conflicts": dict(sorted(conflicts.items())),
        "legacy_reads": {f: n for f, n in sorted(legacy.items())},
    }
    if registry is not None:
        data["divergences"] = dict(sorted(divergences.items()))
    if registry is not None:
        read = set(inventory["prod"]) | set(inventory["lab"])
        data["registry_unread"] = sorted(set(registry) - read)
    return data


# ---- registry sync --------------------------------------------------------------------------

_DOC_TIERS = ("docs/knowledge/", "docs/architecture/", "docs/adr/", "docs/guides/", "docs/research/", "docs/")
_DOC_SKIP = {"docs/knowledge/flag-inventory.md", "docs/knowledge/index.md", "docs/knowledge/open-problems.md"}


def _doc_index(repo: Path) -> dict[str, Counter]:
    """flag name → Counter(doc path → mentions), over tracked markdown (flag-inventory/index/ledger excluded)."""
    try:
        out = subprocess.run(["git", "ls-files", "docs/*.md", "AGENTS.md", "services/*/AGENTS.md", "*/AGENTS.md"],
                             cwd=repo, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}
    idx: dict[str, Counter] = {}
    for rel in out.stdout.splitlines():
        if rel in _DOC_SKIP or rel.startswith("docs/research/architecture-deep-dive"):
            continue
        try:
            text = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for name in set(re.findall(r"\bZOE_[A-Z0-9_]+\b", text)):
            idx.setdefault(name, Counter())[rel] += text.count(name)
    return idx


def _owner_doc(name: str, idx: dict[str, Counter]) -> str:
    docs = idx.get(name)
    if not docs:
        return ""

    def tier(rel: str) -> int:
        for i, pfx in enumerate(_DOC_TIERS):
            if rel.startswith(pfx):
                return i
        return len(_DOC_TIERS)  # AGENTS.md files last

    return sorted(docs, key=lambda r: (tier(r), -docs[r], r))[0]


def _infer_spec(flag: str, info: dict, typed_funcs: dict[str, set[str]], doc_idx: dict[str, Counter]) -> dict:
    """Best-effort starting spec for a flag the registry has not seen (hand-correct after the sync)."""
    fn = typed_funcs.get(flag, set())
    literal = [d for d in info["defaults"] if d not in (DYNAMIC, REQUIRED, NO_DEFAULT)]
    eff = [effective_default(d) for d in literal]
    ftype = None
    for f, t in (("env_bool", "bool"), ("env_int", "int"), ("env_float", "float"), ("env_float_failsafe", "float"),
                 ("env_list", "list"), ("env_str", "str")):
        if f in fn:
            ftype = t
            break
    if ftype is None:
        vals = [v for v in eff if v is not None]
        if vals and all(isinstance(v, bool) for v in vals):
            ftype = "bool"
        elif vals and all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
            ftype = "int"
        elif vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            ftype = "float"
        else:
            ftype = "str"
    # default: the most common effective literal; ties keep the first-seen (sorted) one
    default: object
    if eff:
        counts = Counter(repr(v) for v in eff)
        best = max(counts.values())
        pick = next(v for v in eff if counts[repr(v)] == best)
        default = pick
    else:
        default = None
    if ftype == "bool":
        default = bool(default)
    elif ftype == "int":
        # '1'/'0' read as bool-like strings normalise to True/False: for a numeric flag they are the numbers 1/0
        default = int(default) if isinstance(default, (int, float)) else 0
    elif ftype == "float":
        default = float(default) if isinstance(default, (int, float)) else 0.0
    elif ftype == "list":
        default = tuple(default) if isinstance(default, (list, tuple)) else ()
    else:
        default = "" if default is None else (default if isinstance(default, str) else str(default))
    return {"type": ftype, "default": default, "allowed": None, "owner_doc": _owner_doc(flag, doc_idx),
            "note": "", "divergent_ok": {}}


def numeric_wraps(tree: ast.AST) -> dict[str, set[str]]:
    """flag → {"int" | "float"} for a ``ZOE_*`` name read inside ``int(...)`` / ``float(...)`` in this module.

    ``int(os.environ.get("ZOE_X", "1"))`` reads a NUMBER whose '1' default must not be inferred as a bool
    (``normalize_default`` turns '1' into True, and True == 1 makes a wrong ``bool`` declaration invisible to the
    default comparison). ``bool(int(os.environ.get(...)))`` is a boolean read and is skipped.
    """
    parents: dict[ast.AST, ast.AST] = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parents[c] = n
    consts = _flag_constants(tree)
    out: dict[str, set[str]] = {}
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("int", "float") and n.args):
            continue
        par = parents.get(n)
        if isinstance(par, ast.Call) and isinstance(par.func, ast.Name) and par.func.id == "bool":
            continue
        for c in ast.walk(n.args[0]):
            nm = c.value if isinstance(c, ast.Constant) and isinstance(c.value, str) else consts.get(getattr(c, "id", ""))
            if nm and FLAG_RE.match(nm):
                out.setdefault(nm, set()).add(n.func.id)
    return out


def numeric_wrapped_flags(repo: Path, files: list[str]) -> dict[str, set[str]]:
    """``numeric_wraps`` over every prod/lab python file."""
    out: dict[str, set[str]] = {}
    for rel in sorted(files):
        if _section(rel) is None or not rel.endswith(".py"):
            continue
        try:
            tree = ast.parse((repo / rel).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for nm, kinds in numeric_wraps(tree).items():
            out.setdefault(nm, set()).update(kinds)
    return out


def _typed_func_index(repo: Path, files: list[str]) -> dict[str, set[str]]:
    """flag → set of typed accessor names used to read it (to infer a type).

    An ``int(...)`` / ``float(...)`` wrapper counts as an ``env_int`` / ``env_float`` read."""
    out: dict[str, set[str]] = {}
    for rel in sorted(files):
        if _section(rel) is None or not rel.endswith(".py"):
            continue
        try:
            tree = ast.parse((repo / rel).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        consts = _flag_constants(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and node.args:
                fn = _FlagVisitor._call_name(node.func)
                if fn in TYPED_ENV_FUNCS:
                    a = node.args[0]
                    nm = a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else consts.get(getattr(a, "id", ""))
                    if nm and FLAG_RE.match(nm):
                        out.setdefault(nm, set()).add(fn)
        for nm, kinds in numeric_wraps(tree).items():
            out.setdefault(nm, set()).update("env_" + k for k in kinds)
    return out


def _entry_line(name: str, s: dict) -> str:
    parts = [repr(name), repr(s["type"]), repr(s["default"])]
    if s.get("allowed"):
        parts.append(f"allowed={tuple(s['allowed'])!r}")
    if s.get("owner_doc"):
        parts.append(f"doc={s['owner_doc']!r}")
    if s.get("note"):
        parts.append(f"note={s['note']!r}")
    if s.get("divergent_ok"):
        parts.append(f"divergent_ok={dict(sorted(s['divergent_ok'].items()))!r}")
    return f"    _f({', '.join(parts)}),"


def write_registry(repo: Path, merged: dict[str, dict]) -> None:
    """Re-serialise the registry file: hand-written head kept verbatim, entries sorted by flag name."""
    path = repo / REGISTRY_REL
    text = path.read_text(encoding="utf-8")
    if REGISTRY_MARKER not in text:
        raise SystemExit(f"{REGISTRY_REL} is missing its BEGIN marker; refusing to rewrite")
    head = text.split(REGISTRY_MARKER, 1)[0]
    body = "\n".join(_entry_line(n, merged[n]) for n in sorted(merged))
    path.write_text(f"{head}{REGISTRY_MARKER}\n_ENTRIES = (\n{body}\n)\n", encoding="utf-8")


def sync_registry(repo: Path, data: dict, files: list[str]) -> list[str]:
    """Append every read-but-unregistered flag to the registry file. Existing entries are re-serialised verbatim
    (hand edits survive); returns the names added."""
    merged: dict[str, dict] = dict(load_registry(repo) or {})
    typed_funcs = _typed_func_index(repo, files)
    doc_idx = _doc_index(repo)
    added: list[str] = []
    for section in ("prod", "lab"):
        for flag, info in data["flags"][section].items():
            if flag in merged:
                continue
            merged[flag] = _infer_spec(flag, info, typed_funcs, doc_idx)
            added.append(flag)
    write_registry(repo, merged)
    return sorted(added)


# ---- ratchet -----------------------------------------------------------------------------

def load_ratchet(repo: Path) -> dict:
    p = repo / RATCHET_REL
    if not p.exists():
        return {"legacy_reads": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def tighten_ratchet(repo: Path, data: dict) -> dict:
    """Write the ratchet = min(current, baseline) per file; creates it from the current counts when absent."""
    p = repo / RATCHET_REL
    current = data["legacy_reads"]
    if p.exists():
        base = json.loads(p.read_text(encoding="utf-8"))["legacy_reads"]
        new = {f: min(n, base[f]) for f, n in current.items() if f in base}
    else:
        new = dict(current)
    out = {"legacy_reads": dict(sorted(new.items()))}
    p.write_text(json.dumps({
        "_note": "Ceiling for ZOE_* env reads in prod code that bypass typed_env (per file). May only go DOWN: "
                 "tests/unit/test_flag_registry.py fails when a file exceeds its number or a new file appears. "
                 "Lower it with: python3 tools/audit/flag_inventory.py --tighten-ratchet. Never raise it by hand "
                 "to land a new direct read - use typed_env.flag(...) / env_* instead.",
        **out,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def unregistered_reads(data: dict) -> list[str]:
    """ZOE_* names read by prod or lab code that the registry has never heard of (CI fails on any)."""
    return sorted({f for sec in data["flags"].values() for f, i in sec.items() if not i.get("registered")})


def ratchet_violations(current: dict[str, int], baseline: dict[str, int]) -> list[str]:
    """Files whose legacy direct-read count is above the ceiling (a file absent from the baseline has ceiling 0)."""
    out = []
    for f, n in sorted(current.items()):
        cap = baseline.get(f, 0)
        if n > cap:
            out.append(f"{f}: {n} direct ZOE_* read(s), ceiling {cap}")
    return out


# ---- rendering ---------------------------------------------------------------------------

def _cell(text: object) -> str:
    """A markdown table cell: pipes and newlines in a default (``"a|b"`` phrases) must not break the row."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(data: dict, generated_on: str) -> str:
    has_registry = any("spec" in i for i in data["flags"]["prod"].values()) or "registry_unread" in data
    lines = [
        "---",
        "type: Reference",
        "title: ZOE_* flag inventory (GENERATED)",
        "description: Generated inventory of every ZOE_* environment flag — the declared spec from the flag registry (type, default, allowed values, owner doc) joined with what the scanner finds (readers, the defaults they hard-code, typed_env adoption, .env.example coverage), plus the conflicting-default check and the legacy direct-read ratchet.",
        "tags: [flags, env, configuration, generated]",
        f"timestamp: {generated_on}T00:00:00Z",
        "---",
        "",
        "# ZOE_* flag inventory",
        "",
        "**STATUS: GENERATED — do not hand-edit.** Regenerate with:",
        "",
        "```",
        "python3 tools/audit/flag_inventory.py",
        "```",
        "",
        f"Last generated: {generated_on}. The table body is deterministic (sorted, no",
        "timestamps) so regeneration diffs show real flag changes only.",
        "",
        "Declared columns (type, default, allowed, owner doc) come from the flag registry,",
        "`services/zoe-data/flag_registry.py` — each flag declares itself there once; add a new",
        "flag with `python3 tools/audit/flag_inventory.py --sync-registry`, then correct its spec",
        "by hand. Read facts (read defaults, readers) come from the scanner.",
        "Read default `dynamic` = not statically extractable; `(required)` = bare",
        "`os.environ[...]` subscript (raises if unset); `-` = no default argument.",
        "",
    ]
    prod = data["flags"]["prod"]
    n_direct = sum(data["legacy_reads"].values())
    lines += [
        "## Summary",
        "",
        f"- {len(prod)} prod flags; {sum(1 for i in prod.values() if i['typed_env'])} read through `typed_env`.",
        f"- {n_direct} legacy direct read sites in prod code across {len(data['legacy_reads'])} files "
        "(ceiling in `docs/knowledge/flag-ratchet.json`; may only go down).",
        f"- {len(data['conflicts'])} flags whose readers hard-code different effective defaults (table below).",
        f"- {len(data['other_env'])} non-`ZOE_` env names read by prod code (table below).",
    ]
    if has_registry:
        unreg = [f for s in data["flags"].values() for f, i in s.items() if not i.get("registered")]
        unowned = sum(1 for i in prod.values() if i.get("spec") and not i["spec"]["owner_doc"])
        lines += [
            f"- {len(unreg)} flags read but not registered (CI fails on any).",
            f"- {unowned} prod flags with no owner doc yet.",
            f"- {len(data.get('registry_unread', []))} registered flags with no reader in the scanned sources.",
        ]
    lines.append("")

    lines += ["## Conflicting read defaults", "",
              "Same flag, readers hard-coding different EFFECTIVE defaults (`'1'` ≡ `True`; `''` ≡ unset). "
              "Each must equal the registry default, or the flag carries `divergent_ok` with a reason.", ""]
    if data["conflicts"]:
        lines += ["| Flag | Registry default | Effective read defaults | Divergence acknowledged |", "|---|---|---|---|"]
        for flag, c in data["conflicts"].items():
            spec = prod.get(flag, {}).get("spec") or {}
            rd = _cell("`" + repr(spec["default"]) + "`") if spec else "-"
            ack = ", ".join(f"`{f}`" for f in sorted(spec.get("divergent_ok") or {})) or ("no" if spec else "-")
            lines.append(f"| `{flag}` | {rd} | {', '.join(f'`{e}`' for e in c['effective'])} | {ack} |")
    else:
        lines.append("None.")
    lines.append("")

    for section, heading in (("prod", "Production flags"), ("lab", "Lab flags (`labs/` — not prod)")):
        flags = data["flags"][section]
        n_undoc = sum(1 for i in flags.values() if not i["in_env_example"])
        lines += [
            f"## {heading}",
            "",
            f"{len(flags)} flags; {n_undoc} not documented in `.env.example`.",
            "",
        ]
        if has_registry:
            lines += ["| Flag | Type | Default | Allowed | Owner doc | Read default(s) | typed_env | .env.example | Readers |",
                      "|---|---|---|---|---|---|---|---|---|"]
        else:
            lines += ["| Flag | Default(s) | typed_env | .env.example | Readers |", "|---|---|---|---|---|"]
        for flag, info in flags.items():
            defaults = _cell(", ".join(f"`{d}`" for d in info["defaults"]) or NO_DEFAULT)
            readers = "<br>".join(f"`{r}`" for r in info["readers"])
            tail = f"{'yes' if info['typed_env'] else 'no'} | {'yes' if info['in_env_example'] else 'NO'} | {readers} |"
            if has_registry:
                spec = info.get("spec")
                if spec:
                    allowed = ", ".join(f"`{a}`" for a in spec["allowed"]) if spec["allowed"] else "-"
                    doc = f"`{spec['owner_doc']}`" if spec["owner_doc"] else "-"
                    dflt = _cell("`" + repr(spec["default"]) + "`")
                    lines.append(f"| `{flag}` | {spec['type']} | {dflt} | {allowed} | {doc} | {defaults} | {tail}")
                else:
                    lines.append(f"| `{flag}` | **UNREGISTERED** | - | - | - | {defaults} | {tail}")
            else:
                lines.append(f"| `{flag}` | {defaults} | {tail}")
        lines.append("")

    other = data["other_env"]
    lines += [
        "## Non-`ZOE_` env names read by prod code",
        "",
        f"{len(other)} names. These gate live paths too but are not `ZOE_*` flags, so they are not in the registry; "
        "listed so they are visible. (`typed_env` = read through an accessor.)",
        "",
        "| Name | Default(s) | typed_env | .env.example | Readers |",
        "|---|---|---|---|---|",
    ]
    for name, info in other.items():
        defaults = _cell(", ".join(f"`{d}`" for d in info["defaults"]) or NO_DEFAULT)
        readers = "<br>".join(f"`{r}`" for r in info["readers"])
        lines.append(f"| `{name}` | {defaults} | {'yes' if info['typed_env'] else 'no'} | {'yes' if info['in_env_example'] else 'NO'} | {readers} |")
    lines.append("")

    if data.get("registry_unread"):
        lines += ["## Registered but not read by any scanned source", "",
                  "Flags set in `.env`/units/shell or read through a computed name, or dead entries to delete from the registry.", "",
                  ", ".join(f"`{n}`" for n in data["registry_unread"]), ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--json", type=Path, default=None, help="JSON output (default: docs/knowledge/flag-inventory.json)")
    ap.add_argument("--markdown", type=Path, default=None, help="Markdown output (default: docs/knowledge/flag-inventory.md)")
    ap.add_argument("--check-only", action="store_true", help="Scan and print summary; write nothing")
    ap.add_argument("--sync-registry", action="store_true", help="Add newly-read flags to the registry (existing entries untouched)")
    ap.add_argument("--tighten-ratchet", action="store_true", help="Lower flag-ratchet.json to the current legacy-read counts")
    args = ap.parse_args(argv)
    if args.check_only and (args.sync_registry or args.tighten_ratchet):
        ap.error("--check-only writes nothing; drop --sync-registry / --tighten-ratchet or drop --check-only")

    files = _tracked_files(args.repo)
    if args.sync_registry:
        added = sync_registry(args.repo, scan_repo(args.repo, files=files, registry={}), files)
        print(f"registry: added {len(added)} flag(s)" + (": " + ", ".join(added[:25]) + (" ..." if len(added) > 25 else "") if added else ""))
    data = scan_repo(args.repo, files=files)
    if args.tighten_ratchet:
        new = tighten_ratchet(args.repo, data)
        print(f"ratchet: {sum(new['legacy_reads'].values())} legacy read sites in {len(new['legacy_reads'])} files")
    prod, lab = data["flags"]["prod"], data["flags"]["lab"]
    undoc = sum(1 for i in prod.values() if not i["in_env_example"])
    typed = sum(1 for i in prod.values() if i["typed_env"])
    print(
        f"prod flags: {len(prod)} ({typed} via typed_env, {undoc} missing from .env.example); "
        f"lab flags: {len(lab)}; other env names: {len(data['other_env'])}; "
        f"conflicting defaults: {len(data['conflicts'])}; legacy direct reads: {sum(data['legacy_reads'].values())}"
    )
    if args.check_only:
        return 0

    generated_on = _dt.date.today().isoformat()
    md_path = args.markdown or args.repo / "docs" / "knowledge" / "flag-inventory.md"
    md_path.write_text(render_markdown(data, generated_on), encoding="utf-8")
    print(f"wrote {md_path}")
    json_path = args.json or args.repo / "docs" / "knowledge" / "flag-inventory.json"
    json_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
