#!/usr/bin/env python3
"""
Module Validator
================

Validates module structure and safety before enabling.

Takes a module NAME, not a path — `modules/` is prepended internally.

The security checks enforce the contract in `modules/AGENTS.md`: state-changing
`/tools/*` routes are gated by a shared service token and fail closed (503)
until it is set, and module ports publish on loopback only. Those two are
checked STRUCTURALLY — the route gate through the AST, the ports through the
parsed compose — because a string search for `/tools/` or `127.0.0.1` is
satisfied by a comment. The normative reference the checks accept is the
contract section of `docs/guides/MODULE_SYSTEM.md`.

NOTE: `modules/omnigent` does NOT satisfy this validator and is not meant to.
It is a container-only module with no `main.py` or `requirements.txt`, so it
deterministically reports failure here; it is not the example to reach for and
it is not a scaffold to copy (see docs/guides/MODULE_SYSTEM.md). The security
checks above do not add to that: with no `main.py` the route/gate checks have
nothing to resolve and skip, and omnigent's single published port is already
loopback-bound, so it PASSES the ports check.

Usage:
  python tools/validate_module.py your-module-name
  python tools/validate_module.py --all
"""

import ast
import click
import ipaddress
import re
import sys
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# HTTP verbs that change state. GET/HEAD/OPTIONS are excluded: `/health` and `/`
# must stay reachable or the container healthcheck cannot pass.
STATE_CHANGING_METHODS = {"post", "put", "patch", "delete"}

# The gate's identity is its NAME, not an exact symbol: `require_service_token`
# (docs/guides/MODULE_SYSTEM.md), `verify_service_token`, and
# `service_token_dependency` are all the same contract. Matching on the concept
# keeps this from breaking every time a module spells the helper differently.
SERVICE_TOKEN_NAME = re.compile(r"service_token", re.IGNORECASE)

# FastAPI's two dependency wrappers. A name only counts as a gate if it is
# PASSED to one of these — a bare mention (or a comment) is not a dependency.
# Resolution is by NAME (through import aliases), deliberately not binding-aware:
# a module that shadows `Depends` with its own function could report a route as
# gated when it is not. That is an author sabotaging their own gate, which is
# outside what a pre-deploy helper defends against; the threat model here is an
# honest author shipping a mistake, not a hostile one hiding it.
DEPENDENCY_WRAPPERS = {"Depends", "Security"}

# Sentinel for an `add_api_route(methods=...)` we could not resolve to literals.
# Treated as state-changing so the route is still gate-checked — failing closed.
UNRESOLVED_METHODS = "methods=<unresolved>"


def _dotted_name(node) -> str:
    """Best-effort dotted name for an AST expression (`app.post`, `Depends`)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _dotted_name(node.func)
    return ""


def _import_aliases(tree: ast.AST) -> Dict[str, str]:
    """Local name -> real fastapi symbol, for `from fastapi import Depends as D`.

    Without this an aliased import reads as an unknown callable and a properly
    gated route is reported UNGATED — a false positive that rejects a legitimate
    module, which is as bad as missing a real one.
    """
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "fastapi":
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
    return aliases


def _resolves_to(name: str, aliases: Dict[str, str], targets) -> bool:
    """Does `name` (possibly aliased or dotted) refer to one of `targets`?"""
    if not name:
        return False
    base = name.rsplit(".", 1)[-1]
    return aliases.get(name, aliases.get(base, base)) in targets


def _dependency_names(node, aliases: Dict[str, str] = None) -> List[str]:
    """Names handed to `Depends(...)`/`Security(...)` anywhere under `node`."""
    aliases = aliases or {}
    names = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and _resolves_to(
            _dotted_name(sub.func), aliases, DEPENDENCY_WRAPPERS
        ):
            names.extend(_dotted_name(arg) for arg in sub.args)
    return [n for n in names if n]


def _constant_kwarg(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def _router_prefixes(tree: ast.AST) -> Dict[str, set]:
    """Map router variable -> the SET of path prefixes its routes are served at.

    A set, not a string, because a router can be mounted more than once and a
    mount can be nested. Three things contribute, and all must compose or a
    route hides behind arithmetic the validator got wrong:

      child  = APIRouter(prefix="/admin")        # own prefix, at construction
      parent.include_router(child, prefix="/x")  # nested mount
      app.include_router(parent, prefix="/tools")# outer mount

    …serves `@child.post("/wipe")` at `/tools/x/admin/wipe`. Accumulating a
    single string per router (the first version of this) both MISSED that path
    and, when one router was included twice, corrupted the accumulator into a
    concatenation of two unrelated prefixes — so the real `/tools/...` path
    disappeared and an ungated route validated clean. Found in cross-review.
    """
    own: Dict[str, str] = {}
    mounts: Dict[str, List[Tuple[str, str]]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if "APIRouter" not in _dotted_name(node.value.func):
            continue
        prefix = _constant_kwarg(node.value, "prefix") or ""
        for target in node.targets:
            if isinstance(target, ast.Name):
                own[target.id] = prefix

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "include_router" or not node.args:
            continue
        child = _dotted_name(node.args[0])
        if not child:
            continue
        parent = _dotted_name(node.func.value)
        mounts.setdefault(child, []).append((parent, _constant_kwarg(node, "prefix") or ""))

    def resolve(name: str, seen: frozenset) -> set:
        """Every full prefix `name`'s own routes are reachable under."""
        base = own.get(name, "")
        if name in seen:  # cyclic include; stop rather than recurse forever
            return {base}
        parents = mounts.get(name)
        if not parents:
            return {base}
        seen = seen | {name}
        return {
            f"{outer}{include}{base}"
            for parent, include in parents
            for outer in resolve(parent, seen)
        } or {base}

    return {name: resolve(name, frozenset()) for name in set(own) | set(mounts)}


def _string_list_literal(node) -> Optional[List[str]]:
    """Lower-cased strings from a LITERAL sequence, else None (unresolved).

    None is deliberately distinct from an empty list: the caller must fail
    closed on it rather than conclude "no methods".
    """
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [e.value for e in node.elts if isinstance(e, ast.Constant)]
        if len(values) != len(node.elts):
            return None  # partially dynamic
        return [str(v).lower() for v in values]
    return None


# NOTE — why there is NO name resolution for `methods=`.
#
# An earlier revision tried to resolve `methods=MUTATING` back to its binding.
# Cross-review took it apart across three rounds, and every round found another
# Python binding form the model did not cover: last-write-wins ignored source
# order; source order ignored function scope; module scope ignored the local in
# a `def register(app)` factory; and none of them handled a conditional
# binding, `AugAssign`, or `AnnAssign`. Each miss failed OPEN — an ungated
# state-changing route validating clean, the exact defect this file exists to
# remove.
#
# The lesson is that the feature was the bug. Resolving names correctly means
# implementing Python's scoping and dataflow rules, which is not something a
# pre-deploy structure checker should contain and cannot be got right by
# patching one binding form at a time.
#
# So: only an INLINE LITERAL is read. Anything else is UNRESOLVED and gate-
# checked. The cost is a false alarm on `methods=READ_ONLY` for a genuinely
# read-only route, whose fix is to inline the list — small, obvious, and
# reported with that instruction. The benefit is that the entire class is
# closed by construction rather than by enumeration.


def _add_api_route_calls(tree: ast.AST, prefixes: Dict[str, set]):
    """Yield (call, method, path) for `app.add_api_route(...)` registrations.

    The imperative sibling of the decorator form. Walking only `decorator_list`
    misses it entirely, so an ungated `add_api_route("/tools/wipe", …,
    methods=["POST"])` validated clean.

    FAILS CLOSED on any `methods=` that is not an inline literal — see the note
    above on why no name resolution is attempted. Reading a non-literal as "no
    methods" silently dropped the route, so an ungated POST validated clean.
    The safe direction is a false alarm, never a false pass.
    """
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "add_api_route":
            continue
        path = None
        if node.args and isinstance(node.args[0], ast.Constant):
            path = node.args[0].value
        if path is None:
            path = _constant_kwarg(node, "path")
        if not isinstance(path, str):
            continue

        methods_node = next(
            (kw.value for kw in node.keywords if kw.arg == "methods"), None
        )
        if methods_node is None:
            methods = ["get"]  # FastAPI's default for add_api_route
        else:
            methods = _string_list_literal(methods_node)
            if methods is None:
                methods = [UNRESOLVED_METHODS]

        for prefix in prefixes.get(_dotted_name(node.func.value)) or {""}:
            for method in methods:
                if method in STATE_CHANGING_METHODS or method == UNRESOLVED_METHODS:
                    yield node, method, f"{prefix}{path}"


def _route_decorators(func: ast.AST, prefixes: Dict[str, set] = None):
    """Yield (decorator, method, path) for each state-changing route decorator."""
    prefixes = prefixes or {}
    for dec in getattr(func, "decorator_list", []):
        if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
            continue
        method = dec.func.attr.lower()
        if method not in STATE_CHANGING_METHODS:
            continue
        # The path is positional in every documented form, but `path=` is legal
        # and must not be a way to skip the check.
        path = None
        if dec.args and isinstance(dec.args[0], ast.Constant):
            path = dec.args[0].value
        else:
            for kw in dec.keywords:
                if kw.arg == "path" and isinstance(kw.value, ast.Constant):
                    path = kw.value.value
        if not isinstance(path, str):
            continue
        # A router can be mounted more than once; every mount is a real path.
        for prefix in prefixes.get(_dotted_name(dec.func.value)) or {""}:
            yield dec, method, f"{prefix}{path}"


def _raises_http_503(node: ast.AST) -> bool:
    """Does `node` raise HTTPException with status 503?

    Accepts both spellings the repo's own docs use: `HTTPException(503, ...)`
    positionally and `HTTPException(status_code=503, ...)` by keyword.
    """
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Raise) or not isinstance(sub.exc, ast.Call):
            continue
        if "HTTPException" not in _dotted_name(sub.exc.func):
            continue
        for kw in sub.exc.keywords:
            if kw.arg == "status_code" and getattr(kw.value, "value", None) == 503:
                return True
        if sub.exc.args and getattr(sub.exc.args[0], "value", None) == 503:
            return True
    return False


def _fails_closed(node: ast.AST, by_name: Dict[str, ast.AST], depth: int = 2) -> bool:
    """Is a 503 reachable from a CONDITIONAL branch in `node`?

    Follows calls into module-level helpers, bounded by `depth`, because
    factoring the refusal out is ordinary style and must not be reported as a
    fail-open gate:

        if not SERVICE_TOKEN:
            _reject_unconfigured()      # the 503 lives one level down

    Boundary, stated plainly: this is branch-INSENSITIVE. It proves a 503 sits
    on some conditional path, not that the path taken when the token is unset is
    that one. Proving the latter needs evaluation, not inspection. It is still
    strictly more than "the digits 503 appear in the file".
    """
    for branch in ast.walk(node):
        if not isinstance(branch, ast.If):
            continue
        if _raises_http_503(branch):
            return True
        if depth <= 0:
            continue
        for sub in ast.walk(branch):
            if not isinstance(sub, ast.Call):
                continue
            callee = by_name.get(_dotted_name(sub.func).rsplit(".", 1)[-1])
            if callee is None or callee is node:
                continue
            if _raises_http_503(callee) or _fails_closed(callee, by_name, depth - 1):
                return True
    return False


def _published_host_ip(entry) -> Optional[str]:
    """Host IP a compose `ports:` entry binds to, or None when unspecified.

    None means "every interface". Compose's short syntax only names a host IP
    when there are three colon-separated fields, so `"8101:8101"` and a bare
    `"8101"` both bind 0.0.0.0 AND [::] — the exact LAN exposure the module
    contract forbids.
    """
    if isinstance(entry, dict):  # long syntax
        host_ip = entry.get("host_ip")
        return str(host_ip) if host_ip else None
    text = str(entry).strip()
    if text.startswith("["):  # bracketed IPv6, e.g. "[::1]:8101:8101"
        closing = text.find("]")
        if closing != -1:
            return text[1:closing]
    parts = text.split(":")
    return parts[0] if len(parts) >= 3 else None


def _is_loopback(host_ip: Optional[str]) -> bool:
    if not host_ip:
        return False
    try:
        return ipaddress.ip_address(host_ip).is_loopback
    except ValueError:
        return False


class ModuleValidator:
    """Validates module structure and safety."""
    
    def __init__(self, modules_dir: Path = None):
        self.modules_dir = modules_dir or Path("modules")
        self.errors = []
        self.warnings = []
        self.checks_passed = 0
        self.checks_failed = 0
    
    def validate_module(self, module_name: str) -> bool:
        """Validate a module. Returns True if valid."""
        self.errors = []
        self.warnings = []
        self.checks_passed = 0
        self.checks_failed = 0
        
        module_path = self.modules_dir / module_name
        
        if not module_path.exists():
            self.errors.append(f"Module directory not found: {module_path}")
            return False
        
        click.echo(f"\n🔍 Validating module: {module_name}")
        click.echo("=" * 60)
        
        # Run all validation checks
        self._check_required_files(module_path)
        self._check_dockerfile(module_path)
        self._check_docker_compose(module_path, module_name)
        self._check_main_py(module_path)
        self._check_tool_route_auth(module_path)
        self._check_requirements_txt(module_path)
        self._check_readme(module_path)
        self._check_intents(module_path)
        self._check_naming_conventions(module_name)
        self._check_security(module_path)
        
        # Print results
        click.echo("\n" + "=" * 60)
        
        if self.errors:
            click.echo(click.style(f"\n❌ VALIDATION FAILED", fg="red", bold=True))
            click.echo(f"\n{self.checks_failed} checks failed, {self.checks_passed} passed\n")
            
            click.echo("Errors:")
            for error in self.errors:
                click.echo(click.style(f"  ✗ {error}", fg="red"))
        else:
            click.echo(click.style(f"\n✅ VALIDATION PASSED", fg="green", bold=True))
            click.echo(f"\n{self.checks_passed} checks passed")
        
        if self.warnings:
            click.echo("\nWarnings:")
            for warning in self.warnings:
                click.echo(click.style(f"  ⚠ {warning}", fg="yellow"))
        
        click.echo()
        return len(self.errors) == 0
    
    def _pass_check(self, message: str):
        """Mark check as passed."""
        click.echo(click.style(f"  ✓ {message}", fg="green"))
        self.checks_passed += 1
    
    def _fail_check(self, message: str):
        """Mark check as failed."""
        click.echo(click.style(f"  ✗ {message}", fg="red"))
        self.errors.append(message)
        self.checks_failed += 1
    
    def _warn(self, message: str):
        """Add warning."""
        click.echo(click.style(f"  ⚠ {message}", fg="yellow"))
        self.warnings.append(message)
    
    def _check_required_files(self, module_path: Path):
        """Check for required files."""
        click.echo("\n📁 Required Files:")
        
        required = {
            "main.py": "FastAPI application",
            "Dockerfile": "Container configuration",
            "requirements.txt": "Python dependencies",
            "docker-compose.module.yml": "Service configuration",
            "README.md": "Documentation"
        }
        
        for filename, description in required.items():
            if (module_path / filename).exists():
                self._pass_check(f"{filename} - {description}")
            else:
                self._fail_check(f"Missing {filename} - {description}")
    
    def _check_dockerfile(self, module_path: Path):
        """Check Dockerfile."""
        click.echo("\n🐳 Dockerfile:")
        
        dockerfile = module_path / "Dockerfile"
        if not dockerfile.exists():
            return
        
        content = dockerfile.read_text()
        
        # Check for security issues
        if "sudo" in content.lower():
            self._warn("Dockerfile contains 'sudo' - may not be necessary")
        
        if "curl" in content or "wget" in content:
            self._pass_check("Has curl/wget for healthchecks")
        
        if "HEALTHCHECK" in content:
            self._pass_check("Defines healthcheck")
        else:
            self._warn("No HEALTHCHECK defined in Dockerfile")
    
    def _check_docker_compose(self, module_path: Path, module_name: str):
        """Check docker-compose.module.yml."""
        click.echo("\n🐋 Docker Compose:")
        
        compose_file = module_path / "docker-compose.module.yml"
        if not compose_file.exists():
            return
        
        try:
            compose = yaml.safe_load(compose_file.read_text())
            
            # Check services
            services = compose.get("services", {})
            if not services:
                self._fail_check("No services defined")
                return
            
            self._check_published_ports(services)

            service_name = list(services.keys())[0]
            service = services[service_name]

            # Check container name matches
            container_name = service.get("container_name", "")
            if container_name == module_name or container_name == module_name.replace("_", "-"):
                self._pass_check(f"Container name matches: {container_name}")
            else:
                self._warn(f"Container name '{container_name}' doesn't match module '{module_name}'")
            
            # Check for zoe-network.
            #
            # SCOPED to bridge-networked services. `network_mode: host` is
            # Exception B in docs/governance/DOCKER_NETWORKING_RULES.md: Compose
            # REFUSES `network_mode` together with `networks:`, so demanding
            # `networks: [zoe-network]` from a host-networked service asks for a
            # file Compose will not load — it would reject a valid deployment
            # rather than catch a broken one. (`zoe-music-assistant` is exactly
            # this shape; see docker-compose.modules.yml:47.)
            # tools/generate_module_compose.py already skips host mode here.
            if service.get("network_mode") == "host":
                if "networks" in service:
                    self._fail_check(
                        "network_mode: host cannot be combined with networks: "
                        "- Compose rejects the file"
                    )
                else:
                    self._pass_check(
                        "network_mode: host - exempt from zoe-network by design"
                    )
            else:
                networks = service.get("networks", [])
                if "zoe-network" in networks:
                    self._pass_check("On zoe-network")
                else:
                    self._fail_check("NOT on zoe-network - module will be isolated!")
            
            # Check for network definition
            if "networks" in compose:
                net_def = compose["networks"].get("zoe-network", {})
                if net_def.get("name") == "zoe-network":
                    self._pass_check("Network properly defined with name")
                else:
                    self._fail_check("Network should have 'name: zoe-network'")
            
            # Check healthcheck
            if "healthcheck" in service:
                self._pass_check("Has healthcheck defined")
            else:
                self._warn("No healthcheck in docker-compose")
            
            # Check restart policy
            restart = service.get("restart", "")
            if restart in ["unless-stopped", "always"]:
                self._pass_check(f"Restart policy: {restart}")
            else:
                self._warn(f"Restart policy '{restart}' - consider 'unless-stopped'")
            
        except Exception as e:
            self._fail_check(f"Invalid YAML: {e}")
    
    def _check_published_ports(self, services: Dict):
        """Every published port must bind loopback only (modules/AGENTS.md).

        Checked across ALL services, not just the first: a second service in the
        same file publishes to the LAN just as effectively as the first. In-cluster
        callers reach a module by service name over `zoe-network`, so nothing
        legitimate needs a wider bind. The template form is
        `"127.0.0.1:PORT:PORT"` (docs/guides/MODULE_SYSTEM.md).
        """
        published = [
            (name, entry)
            for name, svc in services.items()
            if isinstance(svc, dict)
            for entry in (svc.get("ports") or [])
        ]
        if not published:
            click.echo("  ℹ  No published ports - reachable only on zoe-network")
            return

        exposed = [
            (name, entry)
            for name, entry in published
            if not _is_loopback(_published_host_ip(entry))
        ]
        if exposed:
            for name, entry in exposed:
                self._fail_check(
                    f"SECURITY: service '{name}' publishes {entry!r} on ALL interfaces - "
                    f"module ports must be loopback-only "
                    f'(use "127.0.0.1:PORT:PORT"; see modules/AGENTS.md)'
                )
        else:
            self._pass_check(
                f"All {len(published)} published port(s) bound to loopback"
            )

    def _check_tool_route_auth(self, module_path: Path):
        """State-changing `/tools/*` routes must carry the service-token gate.

        AST-level on purpose. The older `"/tools/" in content` grep was satisfied
        by a comment, a docstring, or a URL in a log line — it proved a string was
        present, never that a route was protected. This resolves decorators and
        dependencies instead, so only a real `dependencies=[Depends(...)]` (or the
        equivalent signature-level `Depends`) counts.

        Boundary, stated honestly: this proves the gate is WIRED to each route and
        that the gate fails closed. It cannot prove the gate's comparison is
        correct — that is what `secrets.compare_digest` in the template is for.
        """
        click.echo("\n🔑 Tool route auth:")

        main_file = module_path / "main.py"
        if not main_file.exists():
            click.echo("  ℹ  No main.py - container-only module, nothing to check")
            return

        try:
            tree = ast.parse(main_file.read_text())
        except SyntaxError as exc:
            self._fail_check(f"main.py does not parse, cannot verify tool auth: {exc}")
            return

        functions = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]

        prefixes = _router_prefixes(tree)
        aliases = _import_aliases(tree)
        ungated, gated = [], []
        for func in functions:
            for dec, method, path in _route_decorators(func, prefixes):
                if not path.startswith("/tools/"):
                    continue
                # A gate counts from the decorator's `dependencies=[...]` or from
                # the handler's own signature defaults — both are real FastAPI
                # dependencies; neither can be spelled in a comment.
                names = _dependency_names(dec, aliases) + _dependency_names(func.args, aliases)
                if any(SERVICE_TOKEN_NAME.search(n) for n in names):
                    gated.append(path)
                else:
                    ungated.append(f"{method.upper()} {path}")

        # The imperative registration form, which has no decorator to inspect.
        for call, method, path in _add_api_route_calls(tree, prefixes):
            if not path.startswith("/tools/"):
                continue
            names = _dependency_names(call, aliases)
            if any(SERVICE_TOKEN_NAME.search(n) for n in names):
                gated.append(path)
            else:
                # Keep the sentinel verbatim; upper-casing it breaks the hint below.
                label = method if method == UNRESOLVED_METHODS else method.upper()
                ungated.append(f"{label} {path}")

        if not (gated or ungated):
            self._warn("No state-changing /tools/* routes found - MCP-compatible?")
            return

        if ungated:
            hint = ""
            if any(UNRESOLVED_METHODS in entry for entry in ungated):
                hint = (
                    " NOTE: a route above shows "
                    f"'{UNRESOLVED_METHODS}' — its methods= is not an inline list, "
                    "so it is treated as state-changing rather than assumed safe. "
                    "Inline the list (methods=[\"GET\"]) if the route is read-only."
                )
            self._fail_check(
                f"SECURITY: ungated state-changing /tools/* route(s): {ungated} - "
                f"each needs dependencies=[Depends(require_service_token)] "
                f"(modules/AGENTS.md).{hint}"
            )
        else:
            self._pass_check(
                f"All {len(gated)} state-changing /tools/* route(s) token-gated"
            )

        # …and the gate itself must FAIL CLOSED: an unset token is an
        # unconfigured gate, never an open one.
        gate_funcs = [f for f in functions if SERVICE_TOKEN_NAME.search(f.name)]
        if not gate_funcs:
            if gated:
                self._warn(
                    "Service-token gate is not defined in main.py - cannot verify "
                    "it fails closed with 503 when the token is unset"
                )
            return

        by_name = {f.name: f for f in functions}
        for gate in gate_funcs:
            conditional_503 = _fails_closed(gate, by_name)
            if conditional_503:
                self._pass_check(f"Gate '{gate.name}' fails closed (503) when unset")
            else:
                self._fail_check(
                    f"SECURITY: gate '{gate.name}' never raises HTTPException(503) "
                    f"on an unset token - an unconfigured gate must fail CLOSED, "
                    f"not allow the request through (modules/AGENTS.md)"
                )

    def _check_main_py(self, module_path: Path):
        """Check main.py structure."""
        click.echo("\n🐍 main.py:")
        
        main_file = module_path / "main.py"
        if not main_file.exists():
            return
        
        content = main_file.read_text()

        # Check for FastAPI. Resolved through the AST, not matched as a literal:
        # the exact string "from fastapi import FastAPI" rejected the template in
        # docs/guides/MODULE_SYSTEM.md, which imports the symbol alongside
        # others (`from fastapi import Depends, FastAPI, Header, HTTPException`)
        # — so the documented starting point failed this check on its first run.
        try:
            imports_fastapi = any(
                (isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "fastapi")
                or (
                    isinstance(node, ast.Import)
                    and any(a.name.split(".")[0] == "fastapi" for a in node.names)
                )
                for node in ast.walk(ast.parse(content))
            )
        except SyntaxError:
            imports_fastapi = "fastapi" in content
        if imports_fastapi:
            self._pass_check("Uses FastAPI")
        else:
            self._fail_check("No FastAPI import found")
        
        # Check for health endpoint
        if '@app.get("/health")' in content or '@app.get(\'/health\')' in content:
            self._pass_check("Has /health endpoint")
        else:
            self._warn("No /health endpoint - add for monitoring")
        
        # Check for MCP tool endpoints
        if "/tools/" in content:
            self._pass_check("Has MCP tool endpoints")
        else:
            self._warn("No /tools/ endpoints - module may not be MCP-compatible")
        
        # Security checks
        if "os.system" in content or "subprocess.run" in content:
            self._warn("Uses os.system/subprocess - review for security")
        
        if "eval(" in content or "exec(" in content:
            self._fail_check("SECURITY: Uses eval/exec - NOT ALLOWED")
    
    def _check_requirements_txt(self, module_path: Path):
        """Check requirements.txt."""
        click.echo("\n📦 requirements.txt:")
        
        req_file = module_path / "requirements.txt"
        if not req_file.exists():
            return
        
        content = req_file.read_text()
        lines = [l.strip() for l in content.split('\n') if l.strip() and not l.startswith('#')]
        
        if lines:
            self._pass_check(f"Defines {len(lines)} dependencies")
        else:
            self._warn("Empty requirements.txt - is this intentional?")
        
        # Check for common packages
        if any('fastapi' in l.lower() for l in lines):
            self._pass_check("Includes FastAPI")
        
        if any('pydantic' in l.lower() for l in lines):
            self._pass_check("Includes Pydantic")
    
    def _check_readme(self, module_path: Path):
        """Check README.md."""
        click.echo("\n📖 README.md:")
        
        readme = module_path / "README.md"
        if not readme.exists():
            return
        
        content = readme.read_text()
        
        if len(content) > 500:
            self._pass_check("Has comprehensive documentation")
        elif len(content) > 100:
            self._pass_check("Has basic documentation")
        else:
            self._warn("README is very short - add more details")
        
        # Check for required sections
        sections = ["features", "installation", "usage", "tools"]
        found_sections = [s for s in sections if s.lower() in content.lower()]
        
        if len(found_sections) >= 3:
            self._pass_check(f"Has good structure ({len(found_sections)}/4 sections)")
        elif found_sections:
            self._warn(f"Missing some sections ({len(found_sections)}/4)")
    
    def _check_intents(self, module_path: Path):
        """Check intents (optional)."""
        click.echo("\n🎯 Intents (Optional):")
        
        intents_dir = module_path / "intents"
        if not intents_dir.exists():
            click.echo("  ℹ  No intents directory - module only provides MCP tools")
            return
        
        # Check for YAML files
        yaml_files = list(intents_dir.glob("*.yaml"))
        if yaml_files:
            self._pass_check(f"Has {len(yaml_files)} intent definition file(s)")
        else:
            self._warn("intents/ directory exists but no .yaml files")
        
        # Check for handlers.py
        handlers = intents_dir / "handlers.py"
        if handlers.exists():
            content = handlers.read_text()
            
            if "INTENT_HANDLERS" in content:
                self._pass_check("Has INTENT_HANDLERS mapping")
            else:
                self._fail_check("Missing INTENT_HANDLERS dict in handlers.py")
            
            if "async def" in content:
                self._pass_check("Uses async handlers")
        else:
            self._warn("intents/ directory but no handlers.py")
    
    def _check_naming_conventions(self, module_name: str):
        """Check naming conventions."""
        click.echo("\n📝 Naming:")
        
        # Check module name format
        if module_name.startswith("zoe-"):
            self._pass_check("Module name starts with 'zoe-'")
        else:
            self._warn("Module name should start with 'zoe-' for consistency")
        
        # Check for -mcp-bridge suffix
        if "-mcp-bridge" in module_name:
            click.echo("  ℹ  This is an external service bridge (has -mcp-bridge)")
        elif module_name.count("-") >= 1:
            self._pass_check("Uses kebab-case naming")
        else:
            self._warn("Consider using kebab-case (e.g., zoe-my-feature)")
    
    def _check_security(self, module_path: Path):
        """Security checks."""
        click.echo("\n🔒 Security:")
        
        # Check for .env or secrets
        if (module_path / ".env").exists():
            self._fail_check(".env file found - should not be in repo!")
        else:
            self._pass_check("No .env file in repo")
        
        # Check for private keys
        private_files = list(module_path.rglob("*.pem")) + list(module_path.rglob("*.key"))
        if private_files:
            self._fail_check(f"Private key files found: {[f.name for f in private_files]}")
        else:
            self._pass_check("No private keys in repo")
        
        # Check gitignore
        gitignore = module_path / ".gitignore"
        if gitignore.exists():
            self._pass_check("Has .gitignore")
        else:
            self._warn("No .gitignore - add to exclude sensitive files")


@click.command()
@click.argument('module_name', required=False)
@click.option('--all', is_flag=True, help='Validate all modules')
def main(module_name, all):
    """Validate module structure and safety."""
    validator = ModuleValidator()
    
    if all:
        # Validate all modules
        modules_dir = Path("modules")
        if not modules_dir.exists():
            click.echo("No modules/ directory found")
            sys.exit(1)
        
        modules = [d.name for d in modules_dir.iterdir() if d.is_dir()]
        
        if not modules:
            click.echo("No modules found")
            sys.exit(0)
        
        click.echo(f"Validating {len(modules)} modules...\n")
        
        results = {}
        for mod in modules:
            results[mod] = validator.validate_module(mod)
        
        # Summary
        click.echo("\n" + "=" * 60)
        click.echo("SUMMARY")
        click.echo("=" * 60)
        
        passed = sum(1 for v in results.values() if v)
        failed = len(results) - passed
        
        for mod, result in results.items():
            status = click.style("✅ PASS", fg="green") if result else click.style("❌ FAIL", fg="red")
            click.echo(f"  {mod}: {status}")
        
        click.echo(f"\n{passed} passed, {failed} failed\n")
        
        sys.exit(0 if failed == 0 else 1)
    
    elif module_name:
        # Validate single module
        success = validator.validate_module(module_name)
        sys.exit(0 if success else 1)
    
    else:
        click.echo("Usage: validate_module.py MODULE_NAME")
        click.echo("       validate_module.py --all")
        sys.exit(1)


if __name__ == '__main__':
    main()
