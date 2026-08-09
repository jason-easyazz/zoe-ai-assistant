"""Guard: every /api/auth/* path referenced by the UIs exists in zoe-auth's route table.

This prevents the class of bug fixed in fix/ui-auth-endpoint-404s, where the touch
UI called `/api/auth/change-password` (real route: `/api/auth/password/change`) and
`/api/auth/face-recognition` (no handler anywhere). Both 404'd silently in the browser.

The zoe-auth route table is statically enumerable — every router is a module-level
`APIRouter(prefix=...)` with `@router.<method>("/path")` decorators — so this check
needs no running service and is safe for the slim CI lane.

Both UIs are scanned. Per the desktop-is-desktop/touch-is-touch doctrine this asserts
nothing about cross-linking; it only checks that each UI's own calls resolve.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTH_SRC = REPO_ROOT / "services" / "zoe-auth"
UI_DIST = REPO_ROOT / "services" / "zoe-ui" / "dist"

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}

# Literal `/api/auth/...` occurrences in JS/HTML. Stops at the first character that
# cannot appear in a static path, so `/api/auth/sessions/${id}` yields
# `/api/auth/sessions/`.
_REF_RE = re.compile(r"/api/auth/[A-Za-z0-9_./-]*")


def _router_prefix(tree: ast.Module) -> str | None:
    """Return the prefix of a module-level `router = APIRouter(prefix=...)`."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "router" for t in node.targets):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name != "APIRouter":
            continue
        for kw in call.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
        return ""  # APIRouter() with no prefix (the OIDC router)
    return None


def _served_paths() -> set[str]:
    """Every path zoe-auth serves, harvested from router decorators."""
    paths: set[str] = set()
    files = sorted(AUTH_SRC.glob("api/*.py")) + sorted(AUTH_SRC.glob("oidc/*.py"))
    for py in files:
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        prefix = _router_prefix(tree)
        if prefix is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                f = dec.func
                if not isinstance(f, ast.Attribute) or f.attr not in _HTTP_METHODS:
                    continue
                if not (isinstance(f.value, ast.Name) and f.value.id == "router"):
                    continue
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    paths.add(prefix + str(dec.args[0].value))
    return paths


def _auth_paths_served() -> set[str]:
    return {p for p in _served_paths() if p.startswith("/api/auth/")}


def _ui_files() -> list[Path]:
    return [
        p
        for p in UI_DIST.rglob("*")
        if p.is_file() and p.suffix in {".js", ".html"} and "node_modules" not in p.parts
    ]


def _param_regex(route: str) -> re.Pattern[str]:
    """`/api/auth/sessions/{session_id}` -> matches `/api/auth/sessions/<anything>`."""
    return re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", re.escape(route).replace(r"\{", "{").replace(r"\}", "}")) + "$")


def _is_resolvable(ref: str, served: set[str]) -> bool:
    if ref in served:
        return True
    # A reference truncated before a template literal (e.g. `/api/auth/sessions/`)
    # or a bare interceptor prefix (`/api/auth/`) is a prefix of a real route.
    if any(s.startswith(ref) for s in served):
        return True
    return any(_param_regex(s).match(ref) for s in served if "{" in s)


def test_auth_route_table_is_enumerable():
    """Negative control: the harvester must actually find the known routes."""
    served = _auth_paths_served()
    assert len(served) >= 15, f"route harvest looks broken, got {sorted(served)}"
    # Spot-check a route that must exist, and one that must not.
    assert "/api/auth/password/change" in served
    assert "/api/auth/change-password" not in served


def test_ui_files_are_scanned():
    """Negative control: the UI scan must actually be reading files."""
    files = _ui_files()
    assert len(files) > 10, f"UI scan found too few files: {len(files)}"
    refs = {r for f in files for r in _REF_RE.findall(f.read_text(encoding="utf-8", errors="ignore"))}
    assert "/api/auth/login" in refs, "scanner failed to see a known reference"


def test_no_ui_references_a_missing_auth_route():
    served = _auth_paths_served()
    broken: list[str] = []
    for f in _ui_files():
        text = f.read_text(encoding="utf-8", errors="ignore")
        for ref in sorted(set(_REF_RE.findall(text))):
            if not _is_resolvable(ref, served):
                broken.append(f"{f.relative_to(REPO_ROOT)}: {ref}")
    assert not broken, "UI calls auth endpoints that zoe-auth does not serve:\n" + "\n".join(broken)


def test_guard_detects_a_fabricated_bad_path():
    """Negative control: prove the guard goes red on a path that does not exist."""
    served = _auth_paths_served()
    assert not _is_resolvable("/api/auth/face-recognition", served)
    assert not _is_resolvable("/api/auth/change-password", served)
