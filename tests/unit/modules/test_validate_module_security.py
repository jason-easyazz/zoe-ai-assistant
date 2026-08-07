"""`tools/validate_module.py` must verify the module SECURITY contract, not spell it.

The contract (`modules/AGENTS.md`): a module's state-changing `/tools/*` routes
are gated by a shared service token and fail closed (503) until it is set, and
module ports publish on loopback only.

What the validator used to do about that: `_check_main_py` searched for the
string `"/tools/"` and PASSED, `_check_docker_compose` never looked at `ports:`
at all, and `_check_security` covered only `.env`/private keys/`.gitignore`. So a
module with `@app.post("/tools/wipe")` and no auth, published on `"8101:8101"`
(i.e. 0.0.0.0 AND [::], reachable from every host on the LAN), validated clean.

Every check here is paired with a negative control: the reference implementation
from `docs/guides/MODULE_SYSTEM.md` must go GREEN, and each violation must go
RED. A validator that only ever says "pass" is indistinguishable from no
validator, which is precisely the state this file exists to end.
"""

import pytest

from conftest import load_module_validator

pytestmark = pytest.mark.ci_safe

validate_module = load_module_validator()


# ── the reference implementation, transcribed from MODULE_SYSTEM.md ───────────
# These are the templates a module author is told to copy. The validator must
# accept exactly what they produce — a guard that rejects the documented form
# just teaches people to skip the guard.

TEMPLATE_MAIN_PY = '''\
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
import os
import secrets

app = FastAPI(title="Zoe Your-Feature Module")

SERVICE_TOKEN = os.getenv("ZOE_YOURMODULE_SERVICE_TOKEN", "")


def require_service_token(x_zoe_service_token: str = Header(default="")) -> None:
    if not SERVICE_TOKEN:
        raise HTTPException(status_code=503, detail="module service token not configured")
    if not secrets.compare_digest(x_zoe_service_token, SERVICE_TOKEN):
        raise HTTPException(status_code=401, detail="bad or missing X-Zoe-Service-Token")


class YourRequest(BaseModel):
    parameter: str


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/tools/action1", dependencies=[Depends(require_service_token)])
async def tool_action1(request: YourRequest):
    """Tool: your_module.action1"""
    return {"success": True, "result": "..."}
'''

TEMPLATE_COMPOSE = '''\
services:
  zoe-test-module:
    build: .
    container_name: zoe-test-module
    restart: unless-stopped
    ports:
      - "127.0.0.1:8101:8101"
    environment:
      - ZOE_YOURMODULE_SERVICE_TOKEN=${ZOE_YOURMODULE_SERVICE_TOKEN:-}
    networks:
      - zoe-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8101/health"]
      interval: 30s

networks:
  zoe-network:
    name: zoe-network
    external: true
'''

TEMPLATE_DOCKERFILE = '''\
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8101
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \\
    CMD curl -f http://localhost:8101/health || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8101"]
'''

TEMPLATE_README = """\
# Zoe Test Module

## Features
Does the thing the module is for, and does it over MCP.

## Installation
`python tools/zoe_module.py enable zoe-test-module`

## Usage
POST /tools/action1 with the X-Zoe-Service-Token header.

## Tools
- your_module.action1
"""

MODULE_NAME = "zoe-test-module"


def build_module(tmp_path, main_py=TEMPLATE_MAIN_PY, compose=TEMPLATE_COMPOSE):
    """Write the reference module to disk, optionally with a mutated file."""
    modules_dir = tmp_path / "modules"
    module = modules_dir / MODULE_NAME
    module.mkdir(parents=True, exist_ok=True)  # a test may rebuild in one tmp_path
    (module / "main.py").write_text(main_py)
    (module / "docker-compose.module.yml").write_text(compose)
    (module / "Dockerfile").write_text(TEMPLATE_DOCKERFILE)
    (module / "requirements.txt").write_text("fastapi==0.115.0\npydantic==2.9.0\n")
    (module / "README.md").write_text(TEMPLATE_README)
    (module / ".gitignore").write_text(".env\n*.key\n")
    return modules_dir


def run_validator(modules_dir):
    """Validate the fixture module. Returns (ok, errors, warnings)."""
    validator = validate_module.ModuleValidator(modules_dir=modules_dir)
    ok = validator.validate_module(MODULE_NAME)
    return ok, validator.errors, validator.warnings


# ── the GREEN control ─────────────────────────────────────────────────────────


def test_documented_template_module_validates_clean(tmp_path):
    """POSITIVE CONTROL: the form MODULE_SYSTEM.md tells authors to copy passes.

    Without this, every RED below could be produced by a validator that fails
    everything — which detects nothing and blocks everyone.
    """
    ok, errors, _ = run_validator(build_module(tmp_path))
    assert ok, f"the documented template module does not validate: {errors}"


# ── (a) state-changing /tools/* routes must carry the token gate ──────────────


def test_ungated_tool_route_is_an_error(tmp_path):
    """NEGATIVE CONTROL: drop the dependency and the validator must go RED."""
    ungated = TEMPLATE_MAIN_PY.replace(
        '@app.post("/tools/action1", dependencies=[Depends(require_service_token)])',
        '@app.post("/tools/action1")',
    )
    assert "dependencies=" not in ungated, "the mutation did not take"

    ok, errors, _ = run_validator(build_module(tmp_path, main_py=ungated))
    assert not ok, "an UNGATED state-changing /tools/ route validated clean"
    assert any("ungated" in e.lower() and "/tools/action1" in e for e in errors), errors


def test_a_comment_does_not_satisfy_the_gate_check(tmp_path):
    """The whole reason this is AST-level and not a grep.

    The previous check searched main.py for the literal `"/tools/"`. Mentioning
    the gate in a comment — or even naming `require_service_token` in prose —
    satisfies any text search while leaving the route wide open.
    """
    commented = TEMPLATE_MAIN_PY.replace(
        '@app.post("/tools/action1", dependencies=[Depends(require_service_token)])',
        "# dependencies=[Depends(require_service_token)]  # TODO: wire this up\n"
        '@app.post("/tools/action1")',
    )
    assert "require_service_token" in commented, "the mutation removed the string too"

    ok, errors, _ = run_validator(build_module(tmp_path, main_py=commented))
    assert not ok, "a COMMENTED-OUT dependency satisfied the gate check"
    assert any("ungated" in e.lower() for e in errors), errors


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_every_state_changing_verb_is_covered(tmp_path, method):
    """A gate on POST only is not a gate — DELETE changes state just as much."""
    mutated = TEMPLATE_MAIN_PY.replace(
        '@app.post("/tools/action1", dependencies=[Depends(require_service_token)])',
        f'@app.{method}("/tools/action1")',
    )
    ok, errors, _ = run_validator(build_module(tmp_path, main_py=mutated))
    assert not ok, f"an ungated {method.upper()} /tools/ route validated clean"
    assert any(method.upper() in e for e in errors), errors


def test_keyword_path_argument_is_not_a_bypass(tmp_path):
    """`@app.post(path="/tools/x")` is the same route as `@app.post("/tools/x")`.

    Reading only the first POSITIONAL argument would skip it entirely — silently,
    which is the failure mode this whole check exists to remove.
    """
    keyword_form = TEMPLATE_MAIN_PY.replace(
        '@app.post("/tools/action1", dependencies=[Depends(require_service_token)])',
        '@app.post(path="/tools/action1")',
    )
    ok, errors, _ = run_validator(build_module(tmp_path, main_py=keyword_form))
    assert not ok, "an ungated route declared with path= validated clean"
    assert any("/tools/action1" in e for e in errors), errors


def test_router_prefix_is_resolved(tmp_path):
    """A router mounted at `/tools` must not hide its routes from the check.

    `@router.post("/wipe")` on `APIRouter(prefix="/tools")` serves `/tools/wipe`.
    Matching the decorator's literal alone sees `/wipe`, decides it is not a
    tool route, and passes an unauthenticated state-changing endpoint.
    """
    router_form = TEMPLATE_MAIN_PY.replace(
        "from pydantic import BaseModel",
        "from fastapi import APIRouter\nfrom pydantic import BaseModel",
    ).replace(
        '@app.post("/tools/action1", dependencies=[Depends(require_service_token)])\n'
        "async def tool_action1(request: YourRequest):",
        'router = APIRouter(prefix="/tools")\n\n\n'
        '@router.post("/wipe")\nasync def tool_wipe(request: YourRequest):',
    )
    ok, errors, _ = run_validator(build_module(tmp_path, main_py=router_form))
    assert not ok, "an ungated route behind an APIRouter prefix validated clean"
    assert any("/tools/wipe" in e for e in errors), errors


def test_include_router_prefix_is_resolved(tmp_path):
    """The prefix can be set on INCLUSION, not just on construction.

    `app.include_router(router, prefix="/tools")` with a bare `APIRouter()`
    serves `/tools/wipe`. Resolving only `APIRouter(prefix=...)` leaves this
    sibling form skipped — found by cross-review, and the asymmetry was the bug.
    """
    include_form = TEMPLATE_MAIN_PY.replace(
        "from pydantic import BaseModel",
        "from fastapi import APIRouter\nfrom pydantic import BaseModel",
    ).replace(
        '@app.post("/tools/action1", dependencies=[Depends(require_service_token)])\n'
        "async def tool_action1(request: YourRequest):",
        "router = APIRouter()\n\n\n"
        '@router.post("/wipe")\nasync def tool_wipe(request: YourRequest):',
    ) + '\n\napp.include_router(router, prefix="/tools")\n'

    ok, errors, _ = run_validator(build_module(tmp_path, main_py=include_form))
    assert not ok, "an ungated route behind an include_router prefix validated clean"
    assert any("/tools/wipe" in e for e in errors), errors


def test_add_api_route_registration_is_discovered(tmp_path):
    """The imperative form has no decorator to inspect, and must still be checked.

    `app.add_api_route("/tools/wipe", handler, methods=["POST"])` registers a
    state-changing route just as much as the decorator does. Walking only
    `decorator_list` missed it entirely — found by cross-review.
    """
    imperative = TEMPLATE_MAIN_PY.replace(
        '@app.post("/tools/action1", dependencies=[Depends(require_service_token)])\n'
        "async def tool_action1(request: YourRequest):",
        "async def tool_wipe(request: YourRequest):",
    ) + '\n\napp.add_api_route("/tools/wipe", tool_wipe, methods=["POST"])\n'

    ok, errors, _ = run_validator(build_module(tmp_path, main_py=imperative))
    assert not ok, "an ungated add_api_route registration validated clean"
    assert any("/tools/wipe" in e for e in errors), errors

    # …and the gated version of the same form must be ACCEPTED.
    gated = imperative.replace(
        'app.add_api_route("/tools/wipe", tool_wipe, methods=["POST"])',
        'app.add_api_route("/tools/wipe", tool_wipe, methods=["POST"], '
        "dependencies=[Depends(require_service_token)])",
    )
    ok, errors, _ = run_validator(build_module(tmp_path, main_py=gated))
    assert ok, f"a GATED add_api_route registration was rejected: {errors}"


def test_nested_router_composition_is_resolved(tmp_path):
    """A router mounted THROUGH another router still serves under /tools.

    `parent.include_router(child, prefix="/admin")` then
    `app.include_router(parent, prefix="/tools")` serves `/tools/admin/wipe`.
    Storing one prefix per router saw only `child -> "/admin"` and skipped it.
    Found in cross-review round 2.
    """
    nested = TEMPLATE_MAIN_PY.replace(
        "from pydantic import BaseModel",
        "from fastapi import APIRouter\nfrom pydantic import BaseModel",
    ).replace(
        '@app.post("/tools/action1", dependencies=[Depends(require_service_token)])\n'
        "async def tool_action1(request: YourRequest):",
        "parent = APIRouter()\nchild = APIRouter()\n\n\n"
        '@child.post("/wipe")\nasync def tool_wipe(request: YourRequest):',
    ) + (
        '\n\nparent.include_router(child, prefix="/admin")\n'
        'app.include_router(parent, prefix="/tools")\n'
    )

    ok, errors, _ = run_validator(build_module(tmp_path, main_py=nested))
    assert not ok, "an ungated route behind a NESTED include_router validated clean"
    assert any("/tools/admin/wipe" in e for e in errors), errors


def test_router_mounted_twice_keeps_every_path(tmp_path):
    """Including one router at two prefixes must not corrupt the accumulator.

    Accumulating a single string turned `/public` + `/tools` into the fictional
    `/public/tools`, so the REAL `/tools/wipe` path vanished and the ungated
    route validated clean. Found in cross-review round 2.
    """
    double = TEMPLATE_MAIN_PY.replace(
        "from pydantic import BaseModel",
        "from fastapi import APIRouter\nfrom pydantic import BaseModel",
    ).replace(
        '@app.post("/tools/action1", dependencies=[Depends(require_service_token)])\n'
        "async def tool_action1(request: YourRequest):",
        "router = APIRouter()\n\n\n"
        '@router.post("/wipe")\nasync def tool_wipe(request: YourRequest):',
    ) + (
        '\n\napp.include_router(router, prefix="/tools")\n'
        'app.include_router(router, prefix="/public")\n'
    )

    ok, errors, _ = run_validator(build_module(tmp_path, main_py=double))
    assert not ok, "a doubly-mounted ungated /tools route validated clean"
    assert any("/tools/wipe" in e for e in errors), errors


def _with_tail(tail):
    """The template with its gated route removed and `tail` appended."""
    return TEMPLATE_MAIN_PY.replace(
        '@app.post("/tools/action1", dependencies=[Depends(require_service_token)])\n'
        "async def tool_action1(request: YourRequest):",
        "async def tool_wipe(request: YourRequest):",
    ) + tail


# ── `methods=` is read ONLY as an inline literal ─────────────────────────────
# Three cross-review rounds tried to make name resolution correct and each one
# found another binding form that failed OPEN. The resolution machinery was
# deleted; these pin the fail-closed rule that replaced it.

_NON_LITERAL_METHODS = {
    "module-level constant": '\n\nMUTATING = ["POST", "DELETE"]\n'
    'app.add_api_route("/tools/wipe", tool_wipe, methods=MUTATING)\n',
    # round 3: last-write-wins over the whole file hid this POST
    "constant reassigned later": '\n\nM = ["POST"]\n'
    'app.add_api_route("/tools/wipe", tool_wipe, methods=M)\n'
    'M = ["GET"]\n',
    # round 3: a same-named local in an UNRELATED function rewrote resolution
    "shadowed by an unrelated local": '\n\nM = ["POST"]\n\n\n'
    "def unrelated():\n"
    '    M = ["GET"]\n'
    "    return M\n\n\n"
    'app.add_api_route("/tools/wipe", tool_wipe, methods=M)\n',
    # round 4: the `def register(app)` factory pattern — the local binding is
    # invisible to module-scope collection, so resolution fell back to a stale
    # module-level ["GET"] and dropped the POST
    "bound locally in a factory function": '\n\nM = ["GET"]\n\n\n'
    "def register(app):\n"
    '    M = ["POST"]\n'
    '    app.add_api_route("/tools/wipe", tool_wipe, methods=M)\n',
    # round 4: source order takes the textually-last branch regardless of which
    # one actually runs
    "conditional binding": "\n\nimport os\n"
    'if os.environ.get("X"):\n'
    '    M = ["POST"]\n'
    "else:\n"
    '    M = ["GET"]\n'
    'app.add_api_route("/tools/wipe", tool_wipe, methods=M)\n',
    # round 4: neither AugAssign nor AnnAssign is an ast.Assign, so the stale
    # literal survived
    "augmented assignment": '\n\nM = ["GET"]\n'
    'M += ["POST"]\n'
    'app.add_api_route("/tools/wipe", tool_wipe, methods=M)\n',
    "annotated reassignment": '\n\nM = ["GET"]\n'
    'M: list = ["POST"]\n'
    'app.add_api_route("/tools/wipe", tool_wipe, methods=M)\n',
    "opaque expression": "\n\nimport os\n"
    'app.add_api_route("/tools/wipe", tool_wipe, methods=os.environ["M"].split(","))\n',
    "partially dynamic list": "\n\nimport os\n"
    'app.add_api_route("/tools/wipe", tool_wipe, methods=["POST", os.environ["M"]])\n',
}


@pytest.mark.parametrize("label", sorted(_NON_LITERAL_METHODS))
def test_non_literal_methods_always_fails_closed(tmp_path, label):
    """Every non-literal `methods=` is treated as state-changing, no exceptions.

    This single rule replaces the name-resolution machinery that cross-review
    dismantled over three rounds. Each case below is a binding form that a
    previous revision resolved WRONGLY and in the fail-OPEN direction: an
    ungated state-changing route validating clean. Resolving them correctly
    means implementing Python scoping and dataflow, which does not belong in a
    structure checker; refusing to resolve them at all closes the whole class by
    construction.
    """
    source = _with_tail(_NON_LITERAL_METHODS[label])
    ok, errors, _ = run_validator(build_module(tmp_path, main_py=source))
    assert not ok, f"an ungated route with methods=<{label}> validated clean"
    assert any("/tools/wipe" in e for e in errors), errors


def test_unresolved_methods_error_says_how_to_resolve_it(tmp_path):
    """The false-alarm cost is only acceptable if the fix is obvious.

    Failing closed on a read-only route is a deliberate trade, so the message
    has to name the remedy rather than leave the author guessing.
    """
    source = _with_tail(
        '\n\nREAD_ONLY = ["GET"]\n'
        'app.add_api_route("/tools/status", tool_wipe, methods=READ_ONLY)\n'
    )
    _, errors, _ = run_validator(build_module(tmp_path, main_py=source))
    assert any("/tools/status" in e for e in errors), (
        "a non-literal methods= must fail closed even when it is really a GET"
    )
    assert any("Inline the list" in e for e in errors), (
        f"the error does not tell the author how to resolve it: {errors}"
    )


def test_literal_methods_are_read_exactly(tmp_path):
    """FALSE-POSITIVE CONTROL: an inline literal is still read precisely.

    Failing closed on the unknown must not degrade into flagging everything —
    a checker that reports every route is as useless as one that reports none.
    """
    flagged = {
        '\n\napp.add_api_route("/tools/wipe", tool_wipe, methods=["POST"])\n': True,
        '\n\napp.add_api_route("/tools/wipe", tool_wipe, methods=("DELETE",))\n': True,
        # reads, and the FastAPI default, must not be flagged
        '\n\napp.add_api_route("/tools/wipe", tool_wipe, methods=["GET"])\n': False,
        '\n\napp.add_api_route("/tools/wipe", tool_wipe)\n': False,
        '\n\napp.add_api_route("/tools/wipe", tool_wipe, methods=["GET", "HEAD"])\n': False,
    }
    for tail, should_flag in flagged.items():
        _, errors, _ = run_validator(build_module(tmp_path, main_py=_with_tail(tail)))
        hit = any("/tools/wipe" in e for e in errors)
        assert hit == should_flag, (
            f"{'expected' if should_flag else 'did not expect'} a finding for "
            f"{tail.strip()!r}; got {errors}"
        )


def test_aliased_depends_import_is_not_a_false_positive(tmp_path):
    """FALSE-POSITIVE CONTROL: `from fastapi import Depends as D` is still Depends.

    Matching four exact spellings meant an aliased import read as an unknown
    callable and a properly gated route was reported UNGATED — rejecting a
    legitimate module, which is as damaging as missing a real hole. Found by
    cross-review.
    """
    aliased = TEMPLATE_MAIN_PY.replace(
        "from fastapi import Depends, FastAPI, Header, HTTPException",
        "from fastapi import Depends as D, FastAPI, Header, HTTPException",
    ).replace("dependencies=[Depends(require_service_token)]",
              "dependencies=[D(require_service_token)]")
    assert "Depends(" not in aliased, "the mutation did not take"

    ok, errors, _ = run_validator(build_module(tmp_path, main_py=aliased))
    assert ok, f"an aliased Depends import was reported ungated: {errors}"


def test_helper_raised_fail_closed_is_accepted(tmp_path):
    """FALSE-POSITIVE CONTROL: the 503 may live one call level down.

    Factoring the refusal into a helper is ordinary style:

        if not SERVICE_TOKEN:
            _reject_unconfigured()

    Requiring the raise to sit textually inside the gate's own `if` flagged this
    correct module as fail-open. Found by cross-review.
    """
    helper_form = TEMPLATE_MAIN_PY.replace(
        "def require_service_token(x_zoe_service_token: str = Header(default=\"\")) -> None:\n"
        "    if not SERVICE_TOKEN:\n"
        '        raise HTTPException(status_code=503, detail="module service token not configured")\n',
        "def _reject_unconfigured() -> None:\n"
        '    raise HTTPException(status_code=503, detail="module service token not configured")\n'
        "\n\n"
        "def require_service_token(x_zoe_service_token: str = Header(default=\"\")) -> None:\n"
        "    if not SERVICE_TOKEN:\n"
        "        _reject_unconfigured()\n",
    )
    assert "_reject_unconfigured()" in helper_form, "the mutation did not take"

    ok, errors, _ = run_validator(build_module(tmp_path, main_py=helper_form))
    assert ok, f"a helper-raised fail-closed gate was flagged fail-open: {errors}"


def test_read_only_tool_routes_are_not_required_to_be_gated(tmp_path):
    """FALSE-POSITIVE CONTROL: GET must not be forced through the gate.

    `/health` and `/` have to stay open or the container healthcheck cannot pass,
    and the contract is scoped to STATE-CHANGING routes. A validator that also
    demanded a token on reads would be wrong about the contract it enforces.
    """
    read_only = TEMPLATE_MAIN_PY.replace(
        '@app.post("/tools/action1", dependencies=[Depends(require_service_token)])\n'
        "async def tool_action1(request: YourRequest):",
        '@app.get("/tools/status")\nasync def tool_status():',
    ).replace("    return {\"success\": True, \"result\": \"...\"}", "    return {\"ok\": True}")

    _, errors, _ = run_validator(build_module(tmp_path, main_py=read_only))
    assert not any("ungated" in e.lower() for e in errors), (
        f"a read-only GET /tools/ route was flagged as ungated: {errors}"
    )


def test_signature_level_dependency_is_accepted(tmp_path):
    """FALSE-POSITIVE CONTROL: `token = Depends(...)` in the signature is a real gate.

    FastAPI treats a signature default and a decorator `dependencies=[...]` the
    same. Accepting only the decorator form would reject working code.
    """
    signature_form = TEMPLATE_MAIN_PY.replace(
        '@app.post("/tools/action1", dependencies=[Depends(require_service_token)])\n'
        "async def tool_action1(request: YourRequest):",
        '@app.post("/tools/action1")\n'
        "async def tool_action1(request: YourRequest, _=Depends(require_service_token)):",
    )
    ok, errors, _ = run_validator(build_module(tmp_path, main_py=signature_form))
    assert ok, f"a signature-level Depends() gate was rejected: {errors}"


# ── (c) the gate must fail CLOSED when the token is unset ─────────────────────


def test_gate_that_does_not_fail_closed_is_an_error(tmp_path):
    """NEGATIVE CONTROL: an unset token must 503, never fall open.

    This is the subtle one. The route is gated, the helper exists, the name is
    right — but with no token configured it returns None, FastAPI treats the
    dependency as satisfied, and every state-changing route is anonymous. The
    module looks hardened and is not.
    """
    fail_open = TEMPLATE_MAIN_PY.replace(
        "    if not SERVICE_TOKEN:\n"
        '        raise HTTPException(status_code=503, detail="module service token not configured")\n',
        "    if not SERVICE_TOKEN:\n"
        "        return  # no token configured, allow through\n",
    )
    assert "503" not in fail_open, "the mutation did not take"

    ok, errors, _ = run_validator(build_module(tmp_path, main_py=fail_open))
    assert not ok, "a gate that falls OPEN on an unset token validated clean"
    assert any("503" in e and "closed" in e.lower() for e in errors), errors


def test_positional_http_exception_status_is_accepted(tmp_path):
    """FALSE-POSITIVE CONTROL: `HTTPException(503, "...")` is the same 503.

    `status_code=` is a keyword in one documented template and POSITIONAL in the
    other (`docs/guides/MODULE_SYSTEM.md` step 4). Reading only the keyword would
    reject a correctly hardened module for a spelling difference — and a guard
    that rejects the documented form just teaches people to skip the guard.
    """
    positional = TEMPLATE_MAIN_PY.replace(
        'raise HTTPException(status_code=503, detail="module service token not configured")',
        'raise HTTPException(503, "module service token not configured")',
    ).replace(
        'raise HTTPException(status_code=401, detail="bad or missing X-Zoe-Service-Token")',
        'raise HTTPException(401, "bad or missing X-Zoe-Service-Token")',
    )
    assert "status_code=" not in positional, "the mutation did not take"

    ok, errors, _ = run_validator(build_module(tmp_path, main_py=positional))
    assert ok, f"the positional HTTPException(503, ...) form was rejected: {errors}"


def test_a_mentioned_503_does_not_satisfy_fail_closed(tmp_path):
    """CONSTRUCTING an HTTPException(503) is not RAISING one.

    This is the fail-closed check's own anti-grep control. A text search for
    `503` — or for `HTTPException(status_code=503)` — is satisfied by an
    exception object that is built and thrown away, by a docstring, or by a
    comment. The check resolves an `ast.Raise` inside a conditional instead, so
    only a 503 that can actually be reached on the unset-token branch counts.
    """
    stray = TEMPLATE_MAIN_PY.replace(
        "    if not SERVICE_TOKEN:\n"
        '        raise HTTPException(status_code=503, detail="module service token not configured")\n'
        "    if not secrets.compare_digest(x_zoe_service_token, SERVICE_TOKEN):\n"
        '        raise HTTPException(status_code=401, detail="bad or missing X-Zoe-Service-Token")\n',
        "    _unused = HTTPException(status_code=503, detail='module service token not configured')\n"
        "    return\n",
    )
    ok, errors, _ = run_validator(build_module(tmp_path, main_py=stray))
    assert not ok, "a 503 outside any conditional satisfied the fail-closed check"
    assert any("503" in e for e in errors), errors


# ── (b) published ports must be loopback-only ────────────────────────────────


@pytest.mark.parametrize(
    "entry",
    [
        '"8101:8101"',      # binds 0.0.0.0 AND [::] — the documented mistake
        '"8101"',           # container port only; host side is a random 0.0.0.0 port
        '"0.0.0.0:8101:8101"',
        '"192.168.1.50:8101:8101"',
        '"[::]:8101:8101"',
    ],
)
def test_non_loopback_published_port_is_an_error(tmp_path, entry):
    """NEGATIVE CONTROL: every host-wide publish shape must go RED."""
    compose = TEMPLATE_COMPOSE.replace('"127.0.0.1:8101:8101"', entry)
    assert "127.0.0.1" not in compose, "the mutation did not take"

    ok, errors, _ = run_validator(build_module(tmp_path, compose=compose))
    assert not ok, f"a module publishing {entry} on all interfaces validated clean"
    assert any("loopback" in e.lower() for e in errors), errors


@pytest.mark.parametrize(
    "entry",
    ['"127.0.0.1:8101:8101"', '"127.0.0.1::8101"', '"[::1]:8101:8101"'],
)
def test_loopback_published_ports_are_accepted(tmp_path, entry):
    """FALSE-POSITIVE CONTROL: the documented form and its IPv6 twin must pass."""
    compose = TEMPLATE_COMPOSE.replace('"127.0.0.1:8101:8101"', entry)
    ok, errors, _ = run_validator(build_module(tmp_path, compose=compose))
    assert ok, f"loopback publish {entry} was rejected: {errors}"


def test_long_syntax_ports_are_checked_too(tmp_path):
    """The long form is not a way around the check.

    `host_ip` defaults to every interface when omitted, exactly like the short
    form — so an entry with no `host_ip` must go RED, and one naming loopback
    must go GREEN.
    """
    wide = TEMPLATE_COMPOSE.replace(
        '      - "127.0.0.1:8101:8101"\n',
        "      - target: 8101\n        published: 8101\n        protocol: tcp\n",
    )
    ok, errors, _ = run_validator(build_module(tmp_path, compose=wide))
    assert not ok, "long-syntax ports without host_ip validated clean"
    assert any("loopback" in e.lower() for e in errors), errors

    narrow = TEMPLATE_COMPOSE.replace(
        '      - "127.0.0.1:8101:8101"\n',
        '      - target: 8101\n        published: 8101\n        host_ip: "127.0.0.1"\n',
    )
    ok, errors, _ = run_validator(build_module(tmp_path, compose=narrow))
    assert ok, f"long-syntax loopback publish was rejected: {errors}"


def test_every_service_is_checked_not_just_the_first(tmp_path):
    """A sidecar in the same file publishes to the LAN just as effectively.

    The surrounding checks only ever examined `services[0]`; the ports check must
    not inherit that blind spot.
    """
    two_services = TEMPLATE_COMPOSE.replace(
        "\nnetworks:\n  zoe-network:\n    name: zoe-network\n    external: true\n",
        "\n  zoe-test-module-sidecar:\n"
        "    image: redis:7\n"
        "    ports:\n"
        '      - "6379:6379"\n'
        "    networks:\n"
        "      - zoe-network\n"
        "\nnetworks:\n  zoe-network:\n    name: zoe-network\n    external: true\n",
    )
    ok, errors, _ = run_validator(build_module(tmp_path, compose=two_services))
    assert not ok, "a SECOND service publishing on all interfaces validated clean"
    assert any("sidecar" in e for e in errors), errors


def test_no_published_ports_is_fine(tmp_path):
    """FALSE-POSITIVE CONTROL: publishing nothing is the safest shape of all.

    In-cluster callers reach a module by service name over `zoe-network`, so a
    module with no `ports:` at all is correct, not suspicious.
    """
    no_ports = TEMPLATE_COMPOSE.replace(
        '    ports:\n      - "127.0.0.1:8101:8101"\n', ""
    )
    ok, errors, _ = run_validator(build_module(tmp_path, compose=no_ports))
    assert ok, f"a module with no published ports was rejected: {errors}"


# ── the live module's verdict must not change ────────────────────────────────


def test_container_only_module_gains_no_new_errors(tmp_path):
    """`modules/omnigent` is container-only BY DESIGN and must not get noisier.

    It has no `main.py`, so it already fails this validator on the required-files
    check and is documented as doing so. The security checks added here must not
    pile on: with no `main.py` there is no route or gate to resolve, and its one
    published port is already loopback-bound. Measured against the real module,
    the verdict is unchanged (2 errors, 6 warnings) and it gains one PASS.
    """
    modules_dir = build_module(tmp_path)
    module = modules_dir / MODULE_NAME
    (module / "main.py").unlink()
    (module / "requirements.txt").unlink()

    _, errors, warnings = run_validator(modules_dir)

    assert sorted(errors) == sorted(
        [
            "Missing main.py - FastAPI application",
            "Missing requirements.txt - Python dependencies",
        ]
    ), f"the security checks added errors to a container-only module: {errors}"
    assert not any("ungated" in w.lower() or "loopback" in w.lower() for w in warnings), (
        f"the security checks added warnings to a container-only module: {warnings}"
    )
