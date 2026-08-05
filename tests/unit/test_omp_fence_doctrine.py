"""Pins the DOCTRINE of the oh-my-pi builder-lane fence.

The fence is three host-installed files that were untracked until 2026-08-05 —
real protection on exactly one box, in no template, reproduced by no rebuild
(the #1409 pattern). Tracking them is the fix; this test is what stops the
tracked copies from quietly losing the properties that make them a fence:

* ``omp-omnigent-fenced`` — env scrub, dedicated-key promotion, HOME +
  PI_CONFIG_FILES pinning, fail-closed preconditions, and it **execs the
  supervisor, never omp directly**.
* ``omp-acp-supervisor`` — runs omp in its OWN process group and kills the whole
  GROUP on parent death / stdin EOF. Closes a MEASURED orphan-spend hole:
  omnigent's idle watchdog cancels a coroutine and signals the ACP child
  nothing, so a metered omp child outlived its kill by 8 minutes in trial-002.
* ``omp-fence.yml`` — the PI_CONFIG_FILES overlay, which outranks the
  project-scoped config the agent under test can write.

Deliberately structural, not behavioural: these files run inside zoe-omnigent
against a metered provider, so CI asserts shape (no omp, no network, no host
paths touched). Install steps, per-file failure modes and the live-vs-tracked
drift check live in ``docs/knowledge/omp-builder-fence-install.md``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
FENCE_DIR = ROOT / "scripts" / "setup" / "omp-fence"
WRAPPER = FENCE_DIR / "omp-omnigent-fenced"
SUPERVISOR = FENCE_DIR / "omp-acp-supervisor"
OVERLAY = FENCE_DIR / "omp-fence.yml"
RUNBOOK = ROOT / "docs" / "knowledge" / "omp-builder-fence-install.md"


def _text(path: Path) -> str:
    assert path.is_file(), f"fence artifact missing from the repo: {path}"
    return path.read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    """Non-comment, non-blank lines — so prose in the (heavy) headers cannot
    satisfy an assertion about what the script actually does."""
    out = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(stripped)
    return out


# --------------------------------------------------------------------------
# All three artifacts are present and tracked
# --------------------------------------------------------------------------


def test_all_three_fence_artifacts_are_tracked():
    for path in (WRAPPER, SUPERVISOR, OVERLAY):
        assert path.is_file(), (
            f"{path.relative_to(ROOT)} is missing. The fence is only real on a host "
            "that has all three files; an untracked one is the #1409 pattern."
        )


def test_install_runbook_names_every_artifact_and_its_mode():
    doc = _text(RUNBOOK)
    for installed in (
        "/home/zoe/.local/bin/omp-omnigent-fenced",
        "/home/zoe/.local/bin/omp-acp-supervisor",
        "/home/zoe/.config/zoe/omp-fence.yml",
    ):
        assert installed in doc, f"runbook does not name the install path {installed}"
    assert "0755" in doc and "0444" in doc, "runbook must state the install modes"
    assert "sha256sum" in doc, "runbook must carry a live-vs-tracked drift check"


# --------------------------------------------------------------------------
# No secret may ever reach a tracked fence file
# --------------------------------------------------------------------------


def test_no_credential_value_in_any_tracked_fence_file():
    """The dedicated key lives in openrouter-omp.env and is referenced by PATH only."""
    key_shaped = re.compile(r"sk-[A-Za-z0-9_\-]{16,}|or-v1-[A-Za-z0-9]{16,}")
    for path in (WRAPPER, SUPERVISOR, OVERLAY):
        text = _text(path)
        assert not key_shaped.search(text), f"credential-shaped literal in {path.name}"
    # The overlay must not reference the key file at all — not even by name.
    assert "openrouter-omp.env" not in _text(OVERLAY)


# --------------------------------------------------------------------------
# Wrapper doctrine
# --------------------------------------------------------------------------


def test_wrapper_execs_the_supervisor_never_omp_directly():
    """THE load-bearing line. exec'ing omp directly re-opens the orphan-spend
    hole: nothing then puts the metered child in its own process group, and
    nothing kills that group when the harness dies."""
    lines = _code_lines(_text(WRAPPER))
    execs = [ln for ln in lines if ln.startswith("exec ")]
    assert len(execs) == 1, f"expected exactly one exec in the wrapper, got {execs}"
    exec_line = execs[0]

    assert "$SUPERVISOR" in exec_line, (
        "the wrapper must exec the lifetime supervisor; without it a metered omp "
        "child outlives omnigent's kill switch (measured: 8 minutes, trial-002)"
    )
    assert "$OMP_BIN" in exec_line, "omp must still be the command the supervisor runs"
    assert exec_line.index("$SUPERVISOR") < exec_line.index("$OMP_BIN"), (
        "omp must be an ARGUMENT to the supervisor, not the exec target"
    )
    assert not re.search(r"^exec\s+\"?\$\{?OMP_BIN", exec_line), (
        "the wrapper execs omp directly — the supervisor is bypassed"
    )


def test_wrapper_preconditions_are_fail_closed_with_exit_78():
    """A missing piece of the fence must fail the dispatch, never fall through
    to an unfenced omp running on the SHARED review-lane key."""
    # SAME-LINE match, deliberately. An earlier revision let the pattern cross
    # one newline (`[^\n]*\n?[^\n]*`); because these guards sit on consecutive
    # lines, each one then borrowed the NEXT guard's `exit 78` and the per-guard
    # property was not pinned at all. Measured (cross-review, #1650): mutating
    # the `$OMP_BIN` guard to `exit 77` left this test GREEN. Every guard must
    # carry its own `exit 78` on its own line.
    text = _text(WRAPPER)
    for var in ("$OMP_BIN", "$OVERLAY_FILE", "$KEY_FILE", "$SUPERVISOR"):
        guard = re.search(
            r"\[\s*-[rx]\s*\"" + re.escape(var) + r"\"\s*\][^\n]*exit 78",
            text,
        )
        assert guard, f"no fail-closed (exit 78) precondition guarding {var}"
    assert re.search(r"SUPERVISOR_PY[^\n]*exit 78", text), (
        "a missing python3 must fail closed too — the supervisor cannot run without it"
    )
    assert "exit 0" not in _code_lines(text), "the wrapper must never exit success early"


def test_wrapper_arms_stdin_watching_only_for_the_acp_subcommand():
    """MEASURED, not theorised: relaying stdin for one-shot `omp config get`
    (driven from `echo ... | while read`) ate the caller's data and the EOF
    killed the command — a 6/6 PASS read-back became a FAIL."""
    text = _text(WRAPPER)
    gate = re.search(
        r'if\s+\[\s*"\$\{1:-\}"\s*=\s*"acp"\s*\]\s*;\s*then\s*\n'
        r"\s*OMP_SUPERVISOR_WATCH_STDIN=1\s*\n"
        r"\s*else\s*\n"
        r"\s*OMP_SUPERVISOR_WATCH_STDIN=0",
        text,
    )
    assert gate, (
        "OMP_SUPERVISOR_WATCH_STDIN must be 1 for the `acp` subcommand and 0 for "
        "everything else; one-shot invocations must stay byte-transparent on stdin"
    )
    assert text.count("OMP_SUPERVISOR_WATCH_STDIN=1") == 1, (
        "stdin watching is armed somewhere outside the acp gate"
    )


def test_wrapper_pins_home_and_the_settings_overlay():
    lines = _code_lines(_text(WRAPPER))
    assert any(ln.startswith("PI_CONFIG_FILES=") for ln in lines), (
        "the overlay must be installed via PI_CONFIG_FILES — it is the only scope "
        "that outranks the task worktree's own .omp/config.yml"
    )
    assert any(ln.startswith("HOME=") for ln in lines), (
        "HOME must be re-pointed at a clean dir; the user-level half of MCP "
        "discovery is keyed off $HOME and /root is fully populated in zoe-omnigent"
    )


# --------------------------------------------------------------------------
# Supervisor doctrine
# --------------------------------------------------------------------------


def _supervisor_tree() -> ast.Module:
    return ast.parse(_text(SUPERVISOR), filename=str(SUPERVISOR))


def test_supervisor_starts_the_child_in_a_new_session():
    """setsid -> the child LEADS its own process group, so killpg reaches the
    whole tree. omnigent spawns without start_new_session, which is why the
    child sits in the runner's group and is in no group anyone ever signals."""
    tree = _supervisor_tree()
    popens = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Popen"
    ]
    assert popens, "the supervisor no longer spawns a child process"
    for call in popens:
        kwargs = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        assert "start_new_session" in kwargs, (
            "Popen must pass start_new_session=True; without its own process group "
            "the child cannot be killpg'd and orphans survive the harness"
        )
        value = kwargs["start_new_session"]
        assert isinstance(value, ast.Constant) and value.value is True, (
            "start_new_session must be literally True"
        )


def test_supervisor_kills_the_process_group_not_a_single_pid():
    """omnigent's one real kill path does proc.terminate() — SIGTERM to a single
    PID, never killpg — so the child's own subprocesses survive it. The whole
    point of this supervisor is to not repeat that."""
    tree = _supervisor_tree()
    killpg_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "killpg"
    ]
    assert killpg_calls, "the supervisor must kill the child's process GROUP (os.killpg)"

    signals = set()
    for call in killpg_calls:
        for arg in call.args[1:]:
            if isinstance(arg, ast.Attribute):
                signals.add(arg.attr)
    assert "SIGTERM" in signals and "SIGKILL" in signals, (
        "the group kill must escalate SIGTERM -> grace -> SIGKILL; found "
        f"{sorted(signals)}"
    )

    terminates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "terminate"
    ]
    assert not terminates, (
        "proc.terminate() signals one PID and leaves the group alive — that is the "
        "exact omnigent bug this supervisor exists to avoid"
    )


def test_supervisor_arms_parent_death_detection_two_ways():
    text = _text(SUPERVISOR)
    assert "PR_SET_PDEATHSIG" in text, (
        "PR_SET_PDEATHSIG is the trigger that closes the orphan hole — it fires "
        "even if this process is wedged in a syscall"
    )
    assert "getppid" in text, (
        "the getppid poll is the belt for PDEATHSIG's corner cases (it keys on the "
        "CREATING thread and is Linux-only)"
    )


# --------------------------------------------------------------------------
# Overlay doctrine
# --------------------------------------------------------------------------


def _overlay() -> dict:
    data = yaml.safe_load(_text(OVERLAY))
    assert isinstance(data, dict), "the overlay must parse as a YAML mapping"
    return data


def test_overlay_pins_the_model_allowlist():
    """trial-001: unpinned, omp resolved openai/gpt-5.5 on its own and spent
    ~$2-4 on a kata. The pin belongs here, not in omnigent's acp: block — the
    overlay outranks the project scope, the acp: block's `model:` does not."""
    models = _overlay().get("enabledModels")
    assert isinstance(models, list) and models, (
        "enabledModels must pin a non-empty allowlist; unpinned omp picks its own "
        "(and its own is not the cheap one)"
    )
    assert all(isinstance(m, str) and m for m in models), "model ids must be strings"


def test_overlay_disables_autoqa():
    """dev.autoqa defaults TRUE and posts model-authored free text (which can
    carry repo fragments) plus a persistent install UUID."""
    dev = _overlay().get("dev")
    assert isinstance(dev, dict), "the overlay must carry a `dev:` mapping"
    assert dev.get("autoqa") is False, "dev.autoqa must be explicitly false"


def test_overlay_disables_plugin_auto_update_as_a_quoted_string():
    """The dangerous mode reaches upgradeAllPlugins() and executes new Function()
    over fetched plugin source; there is no global plugin kill switch and NO env
    override, so the overlay is the only lever."""
    marketplace = _overlay().get("marketplace")
    assert isinstance(marketplace, dict), "the overlay must carry a `marketplace:` mapping"
    value = marketplace.get("autoUpdate")
    assert value is not True, "marketplace.autoUpdate must not be enabled"
    assert isinstance(value, str), (
        "autoUpdate is a string enum (off|notify|auto) — an UNQUOTED `off` is a "
        "YAML 1.1 boolean and silently fails to match the enum"
    )
    assert value == "off", f"marketplace.autoUpdate must be 'off', got {value!r}"


def test_overlay_drops_project_scoped_mcp_config():
    """cwd is the task worktree, which the agent under test can write; without
    this it auto-loads the repo's own .mcp.json (serena + codebase-memory)."""
    mcp = _overlay().get("mcp")
    assert isinstance(mcp, dict), "the overlay must carry an `mcp:` mapping"
    assert mcp.get("enableProjectConfig") is False, (
        "mcp.enableProjectConfig must be false"
    )
