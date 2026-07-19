#!/usr/bin/env python3
"""Auto-generated ZOE_* flag inventory (audit P1-9).

Scans Python sources for ``ZOE_*`` environment-variable reads —
``os.environ.get(...)``, ``os.getenv(...)``, ``os.environ["..."]``, and the
``typed_env`` accessors (``env_str/env_bool/env_int/env_float/env_list``) —
and emits a machine JSON plus a human markdown table
(``docs/knowledge/flag-inventory.md``).

Per flag: name, statically-extracted default (honest ``dynamic`` when the
default expression is not a plain literal, ``(required)`` for bare
``os.environ[...]`` subscripts), reader modules, whether any reader goes
through ``typed_env``, and whether the flag is documented in ``.env.example``.

Best-effort matching: calls are matched by NAME (``getenv``, the typed_env
accessor names), not by originating module — a local helper or mock that
happens to share a name is counted too, so the scanner can over-report.

Scope: files under ``labs/`` are inventoried in a separate LAB section; test
files (any path containing a ``tests`` directory or ``test_*.py``) are
excluded — they set flags rather than define runtime behaviour. Everything
else is "prod".

Deterministic by construction: output is sorted, and no timestamps appear in
the table body (the generated-on date lives only in the frontmatter/status
note, refreshed on regeneration).

Usage:
    python3 tools/audit/flag_inventory.py [--repo PATH]
        [--json OUT.json] [--markdown OUT.md] [--check-only]

Do NOT hand-edit the generated markdown — rerun this tool.
"""
from __future__ import annotations

import argparse
import ast
import datetime as _dt
import json
import re
import subprocess
import sys
from pathlib import Path

TYPED_ENV_FUNCS = {"env_str", "env_bool", "env_int", "env_float", "env_list"}
FLAG_RE = re.compile(r"^ZOE_[A-Z0-9_]+$")
DYNAMIC = "dynamic"
REQUIRED = "(required)"
NO_DEFAULT = "-"


def _literal_repr(node: ast.expr | None) -> str:
    """Render a default expression: literal → repr, anything else → dynamic."""
    if node is None:
        return NO_DEFAULT
    try:
        return repr(ast.literal_eval(node))
    except (ValueError, SyntaxError):
        return DYNAMIC


class _FlagVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        # name -> list of (default_str, via_typed_env)
        self.reads: list[tuple[str, str, bool]] = []

    @staticmethod
    def _call_name(func: ast.expr) -> str:
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
        return ""

    @staticmethod
    def _is_environ_get(func: ast.expr) -> bool:
        # os.environ.get(...)
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "environ"
        )

    def visit_Call(self, node: ast.Call) -> None:
        name = self._call_name(node.func)
        typed = name in TYPED_ENV_FUNCS
        is_get = name == "getenv" or self._is_environ_get(node.func)
        if (typed or is_get) and node.args:
            key = node.args[0]
            if isinstance(key, ast.Constant) and isinstance(key.value, str) and FLAG_RE.match(key.value):
                default: ast.expr | None = None
                if len(node.args) > 1:
                    default = node.args[1]
                else:
                    for kw in node.keywords:
                        if kw.arg == "default":
                            default = kw.value
                self.reads.append((key.value, _literal_repr(default), typed))
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
        if isinstance(v, ast.Attribute) and v.attr == "environ":
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str) and FLAG_RE.match(sl.value):
                self.reads.append((sl.value, REQUIRED, False))
        self.generic_visit(node)


def _is_test_path(rel: str) -> bool:
    parts = Path(rel).parts
    return "tests" in parts or "test" in parts or Path(rel).name.startswith("test_")


def _section(rel: str) -> str | None:
    if _is_test_path(rel):
        return None
    return "lab" if Path(rel).parts[:1] == ("labs",) else "prod"


def scan_repo(repo: Path, files: list[str] | None = None) -> dict:
    """Scan Python files; return {section: {flag: info}} plus env-example set."""
    if files is None:
        try:
            out = subprocess.run(
                ["git", "ls-files", "*.py"], cwd=repo, capture_output=True, text=True, check=True
            )
        except subprocess.CalledProcessError as exc:
            raise SystemExit(
                f"git ls-files failed in {repo} (not a git worktree?): "
                f"{exc.stderr.strip() or exc} — run from the repo root or pass files explicitly"
            ) from exc
        files = [f for f in out.stdout.splitlines() if f]

    env_example = repo / ".env.example"
    documented: set[str] = set()
    if env_example.exists():
        for line in env_example.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*#?\s*(ZOE_[A-Z0-9_]+)\s*=", line)
            if m:
                documented.add(m.group(1))

    inventory: dict[str, dict[str, dict]] = {"prod": {}, "lab": {}}
    for rel in sorted(files):
        section = _section(rel)
        if section is None:
            continue
        path = repo / rel
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        visitor = _FlagVisitor()
        visitor.visit(tree)
        for flag, default, typed in visitor.reads:
            info = inventory[section].setdefault(
                flag, {"defaults": [], "readers": [], "typed_env": False, "in_env_example": flag in documented}
            )
            if default not in info["defaults"]:
                info["defaults"].append(default)
            if rel not in info["readers"]:
                info["readers"].append(rel)
            info["typed_env"] = info["typed_env"] or typed

    for section in inventory.values():
        for info in section.values():
            info["defaults"].sort()
            info["readers"].sort()
    return {"flags": {k: dict(sorted(v.items())) for k, v in inventory.items()}}


def render_markdown(data: dict, generated_on: str) -> str:
    lines = [
        "---",
        "type: Reference",
        "title: ZOE_* flag inventory (GENERATED)",
        "description: Auto-generated inventory of every ZOE_* environment flag read in the codebase — defaults, readers, typed_env adoption, and .env.example coverage.",
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
        "Default `dynamic` = not statically extractable; `(required)` = bare",
        "`os.environ[...]` subscript (raises if unset); `-` = no default argument.",
        "",
    ]
    for section, heading in (("prod", "Production flags"), ("lab", "Lab flags (`labs/` — not prod)")):
        flags = data["flags"][section]
        n_undoc = sum(1 for i in flags.values() if not i["in_env_example"])
        lines += [
            f"## {heading}",
            "",
            f"{len(flags)} flags; {n_undoc} not documented in `.env.example`.",
            "",
            "| Flag | Default(s) | typed_env | .env.example | Readers |",
            "|---|---|---|---|---|",
        ]
        for flag, info in flags.items():
            defaults = ", ".join(f"`{d}`" for d in info["defaults"]) or NO_DEFAULT
            readers = "<br>".join(f"`{r}`" for r in info["readers"])
            lines.append(
                f"| `{flag}` | {defaults} | {'yes' if info['typed_env'] else 'no'} "
                f"| {'yes' if info['in_env_example'] else 'NO'} | {readers} |"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--json", type=Path, default=None, help="JSON output (default: docs/knowledge/flag-inventory.json)")
    ap.add_argument("--markdown", type=Path, default=None, help="Markdown output (default: docs/knowledge/flag-inventory.md)")
    ap.add_argument("--check-only", action="store_true", help="Scan and print summary; write nothing")
    args = ap.parse_args(argv)

    data = scan_repo(args.repo)
    prod, lab = data["flags"]["prod"], data["flags"]["lab"]
    undoc = sum(1 for i in prod.values() if not i["in_env_example"])
    typed = sum(1 for i in prod.values() if i["typed_env"])
    print(
        f"prod flags: {len(prod)} ({typed} via typed_env, {undoc} missing from .env.example); "
        f"lab flags: {len(lab)}"
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
