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


def test_the_runbooks_tracked_hashes_match_the_tracked_files():
    """A recorded hash that does not match is worse than no hash at all.

    The runbook tells the operator to refuse to trust or dispatch through a
    fence whose live file does not equal the recorded tracked hash. So a stale
    entry does not merely mislead — it makes a byte-correct install
    un-installable, and the operator's only options are to skip the check or
    edit the runbook, both of which defeat it.

    This has now gone stale twice by hand (once elided to a prefix, once left
    behind by a follow-up commit), which is exactly the shape that wants a test
    rather than more care. Every 64-hex literal in the runbook that sits beside
    a fence filename must be the CURRENT hash of that file.
    """
    import hashlib

    doc = _text(RUNBOOK)
    current = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (WRAPPER, SUPERVISOR, OVERLAY)
    }

    # Historical baselines are deliberately kept, so only the block introduced by
    # the LATEST hashes is checked: the last recorded hash per filename wins.
    recorded: dict[str, str] = {}
    for digest, name in re.findall(r"\b([0-9a-f]{64})\s+(\S+)", doc):
        if name in current:
            recorded[name] = digest

    assert set(recorded) == set(current), (
        f"the runbook must record a hash for every fence artifact; got {sorted(recorded)}"
    )
    for name, digest in recorded.items():
        assert digest == current[name], (
            f"runbook records {digest} for {name} but the tracked file hashes to "
            f"{current[name]}. Update the runbook in the SAME commit that changes "
            "the file — an operator following it would refuse a correct install."
        )


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


def test_wrapper_parses_the_key_file_and_never_sources_it():
    """``. "$KEY_FILE"`` executed the key file as SHELL, after the section-2
    credential scrub and after every section-1 precondition — so an extra line in
    it landed inside the fence with nothing left to catch it. Measured on the
    pre-fix wrapper with a crafted file: ``export ANTHROPIC_API_KEY=…`` reached
    the child, and ``SUPERVISOR=/tmp/evil`` re-pointed the final exec.

    Per-LINE matching throughout, deliberately (same rule as the exit-78 guards):
    every assertion is evaluated against ONE code line, so no pattern can borrow
    a neighbour's text."""
    lines = _code_lines(_text(WRAPPER))
    for ln in lines:
        assert not re.match(r"^(\.|source)\s", ln), (
            f"the wrapper sources a file ({ln!r}); the key file must be PARSED — "
            "sourcing runs arbitrary shell inside the fence, after the scrub"
        )
    assert any(re.search(r"^while\s+read\s+-r\s+_line\b", ln) for ln in lines), (
        "the key file must be consumed by a read loop, not executed"
    )
    assert any(re.search(r'^done\s*<\s*"\$KEY_FILE"', ln) for ln in lines), (
        "the read loop must take its input from $KEY_FILE by redirection (scoped "
        "to the loop — the script's own fd 0 is the ACP channel)"
    )


def test_wrapper_key_parse_fail_closes_on_anything_but_the_dedicated_key():
    """Exactly one variable may come out of the key file. Another credential, an
    ``OMP_BIN=``/``SUPERVISOR=`` re-point, a stray command, a duplicate, an empty
    value or an absent one must all exit 78 rather than run a half-fenced omp.

    Every match below is against a SINGLE code line, so an assertion can never
    satisfy itself with an ``exit 78`` that belongs to an adjacent guard — the
    #1650 failure, where a pattern crossing one newline let each precondition
    borrow the next one's exit code."""
    lines = _code_lines(_text(WRAPPER))

    assert any(ln.startswith("OPENROUTER_API_KEY_OMP=*)") for ln in lines), (
        "no case arm accepting the one permitted assignment"
    )
    assert any(
        ln.startswith("*)") and "$KEY_FILE" in ln and "exit 78" in ln for ln in lines
    ), (
        "the catch-all arm of the key-file parse must exit 78 ON ITS OWN LINE; "
        "any line that is not an OPENROUTER_API_KEY_OMP assignment is a fence breach"
    )
    assert any('"$_key_seen" -eq 1' in ln and "exit 78" in ln for ln in lines), (
        "an absent OPENROUTER_API_KEY_OMP must exit 78"
    )
    assert any('"$_key_seen" -eq 0' in ln and "exit 78" in ln for ln in lines), (
        "a duplicate OPENROUTER_API_KEY_OMP assignment must exit 78 — an ambiguous "
        "key file is not a fenced one"
    )
    assert any(
        re.search(r'\[\s*-n\s*"\$_key_value"\s*\]', ln) and "exit 78" in ln
        for ln in lines
    ), "an empty OPENROUTER_API_KEY_OMP must exit 78"
    assert any(
        ln.startswith("*[!") and "exit 78" in ln for ln in lines
    ), (
        "the parsed value must be charset-checked — `KEY=x; export OTHER=y` is a "
        "single assignment to a naive parser and must not be accepted as a key"
    )


def test_the_dedicated_key_name_is_itself_scrubbed():
    """The scrubber's globs do not catch our OWN variable name.

    ``OPENROUTER_API_KEY_OMP`` ends in ``_OMP``, so ``*_API_KEY``/``*_TOKEN``/…
    all miss it. An ambient copy exported by the runner would survive section
    2's scrub and reach the child ALONGSIDE the key parsed from the file —
    handing omp a second, possibly stale secret under a name it might read
    (cross-review, #1655).

    Pinned in BOTH places it is unset: section 2 (before the parse, which reads
    the FILE and is unaffected) and again after promotion, so exactly one
    OpenRouter credential leaves this script under exactly one name.
    """
    lines = _code_lines(_text(WRAPPER))
    unsets = [ln for ln in lines if ln.startswith("unset ") and "OPENROUTER_API_KEY_OMP" in ln]

    assert len(unsets) >= 2, (
        "OPENROUTER_API_KEY_OMP must be unset in section 2 AND after promotion; "
        f"found {len(unsets)}: {unsets}"
    )

    # And the premise is DERIVED FROM THE SCRIPT, not restated here: pull the
    # scrub globs out of the section-2 case arm and confirm none of them match.
    # If a glob is ever broadened to cover the name, this test says so instead of
    # silently asserting a redundant unset.
    import fnmatch
    arm = next(ln for ln in lines if ln.startswith("*_API_KEY|"))
    scrub_globs = arm.split(")")[0].split("|")
    assert len(scrub_globs) >= 5, f"could not parse the scrub globs from: {arm!r}"
    assert not any(fnmatch.fnmatch("OPENROUTER_API_KEY_OMP", g) for g in scrub_globs), (
        "a credential glob now catches OPENROUTER_API_KEY_OMP — this test's "
        "premise is stale, re-derive it rather than deleting it"
    )


def test_the_key_line_case_has_exactly_two_arms_in_the_right_order():
    """Arm PRESENCE is not arm REACHABILITY — and `case` is first-match-wins.

    The assertions above check that both arms exist. They stay green if a
    permissive arm is inserted BEFORE the rejecting one: a leading `*) : ;;`
    makes every hostile line fall through and turns the `exit 78` arm into dead
    code, with the whole test still passing. That was demonstrated as a live
    mutation during the cross-review of #1655. So pin the arm LIST: exactly two
    arms, accept first, catch-all reject second, nothing in between.
    """
    lines = _code_lines(_text(WRAPPER))
    # `$_line` is cased several times (blank/comment skip, `export ` strip).
    # The ACCEPTANCE block is the one carrying the permitted assignment — found
    # by its content, so reordering the earlier blocks cannot misdirect this.
    blocks = []
    for i, ln in enumerate(lines):
        if ln != 'case "$_line" in':
            continue
        end = next(j for j in range(i + 1, len(lines)) if lines[j] == "esac")
        arms = [a for a in lines[i + 1:end] if re.match(r"^[^\s(]+\)", a)]
        if any(a.startswith("OPENROUTER_API_KEY_OMP=*)") for a in arms):
            blocks.append(arms)

    assert len(blocks) == 1, (
        f"expected exactly one acceptance case for the key line, found {len(blocks)}"
    )
    arms = blocks[0]

    assert len(arms) == 2, f"the key-line case must have exactly two arms, got: {arms}"
    assert arms[0].startswith("OPENROUTER_API_KEY_OMP=*)"), (
        "the FIRST arm must be the one permitted assignment"
    )
    assert arms[1].startswith("*)") and "exit 78" in arms[1], (
        "the SECOND and last arm must be the catch-all that exits 78; anything "
        "matching earlier leaves the reject arm unreachable"
    )


def test_wrapper_diagnostics_never_carry_key_material():
    """A rejected line may BE the key, and stderr goes to omnigent's logs. Every
    fail-closed message names the file and a line NUMBER, never line text.

    Matched in BRACE as well as bare form (`${_line}`, not only `$_line`). The
    brace form is the natural thing to reach for when appending text to a
    variable, it defeated the bare-only pattern in a live mutation during the
    cross-review of #1655, and the leak it permits is the whole key.
    """
    for ln in _code_lines(_text(WRAPPER)):
        # Only the emitted TEXT — the guard's own condition may of course name
        # the variable it is testing. Matched within one line; `.` never spans a
        # newline, so this cannot reach into a neighbouring statement.
        emitted = re.search(r"\b(?:echo|printf)\b(.*?)>&2", ln)
        if not emitted:
            continue
        for var in ("_key_value", "_line", "OPENROUTER_API_KEY"):
            assert not re.search(rf"\$\{{?{var}\b", emitted.group(1)), (
                f"diagnostic prints key material via ${var}: {ln!r}"
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


def _supervisor_main() -> ast.FunctionDef:
    for node in _supervisor_tree().body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("the supervisor no longer defines main()")


def _linenos(scope: ast.AST, pred) -> list[int]:
    return sorted(node.lineno for node in ast.walk(scope) if pred(node))


def _is_call_to(node: ast.AST, name: str) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == name
    return isinstance(func, ast.Name) and func.id == name


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


def test_supervisor_captures_the_expected_parent_before_arming_pdeathsig():
    """ORDER, not presence — and it is the one case where BOTH parent-death
    detectors fail together.

    Armed-then-captured (shipped until 2026-08-06): if the runner dies inside
    that window we are reparented to init, ``original_ppid`` records **1**, and
    the getppid poll then compares 1 against 1 forever — nothing reparents an
    already-init-owned process again — while ``Popen`` still launches the METERED
    child. Reproduced against the pre-fix copy by double-forking to ppid 1: the
    child launched, silently.

    AST node positions, so nothing here can borrow an adjacent line."""
    main = _supervisor_main()
    capture = _linenos(
        main,
        lambda n: isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "original_ppid" for t in n.targets)
        and _is_call_to(n.value, "getppid"),
    )
    assert capture, "main() no longer captures original_ppid from os.getppid()"
    assert any(
        isinstance(stmt, ast.Assign) and stmt.lineno == capture[0] for stmt in main.body
    ), "the parent capture must be an unconditional statement of main(), not branch-local"

    arm = _linenos(main, lambda n: _is_call_to(n, "_set_pdeathsig"))
    assert arm, "main() no longer arms PR_SET_PDEATHSIG"
    popen = _linenos(main, lambda n: _is_call_to(n, "Popen"))
    assert popen, "main() no longer spawns the metered child"

    assert capture[0] < arm[0], (
        "original_ppid must be captured BEFORE PR_SET_PDEATHSIG is armed; the "
        "reverse order records ppid 1 when the runner dies in the window and the "
        "getppid watcher then waits on init forever"
    )
    assert arm[0] < popen[0], (
        "parent-death detection must be armed before the metered child is spawned"
    )


def test_supervisor_refuses_to_spawn_when_the_parent_has_already_gone():
    """Detecting the race is not enough — it must REFUSE. A failed dispatch is a
    harness error; a launched child with no working kill switch is unbounded
    spend (trial-002: 49s of work past the kill, 8 minutes orphaned)."""
    main = _supervisor_main()
    popen_line = _linenos(main, lambda n: _is_call_to(n, "Popen"))[0]

    guards = []
    for node in ast.walk(main):
        if not isinstance(node, ast.If) or node.lineno >= popen_line:
            continue
        test_src = ast.dump(node.test)
        if "original_ppid" not in test_src:
            continue
        returns = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
        if any(
            isinstance(r.value, ast.Constant) and r.value.value not in (0, None)
            for r in returns
        ):
            guards.append(node)

    orphaned = [g for g in guards if not any(_is_call_to(n, "getppid") for n in ast.walk(g.test))]
    changed = [g for g in guards if any(_is_call_to(n, "getppid") for n in ast.walk(g.test))]

    assert orphaned, (
        "no pre-spawn guard returning non-zero when original_ppid is already init; "
        "an orphan at startup must not launch a metered child at all"
    )
    assert any(
        isinstance(n, ast.Constant) and n.value == 1
        for g in orphaned
        for n in ast.walk(g.test)
    ), "the orphan guard must compare the captured parent against PID 1"

    arm_line = _linenos(main, lambda n: _is_call_to(n, "_set_pdeathsig"))[0]
    assert any(g.lineno > arm_line for g in changed), (
        "getppid() must be re-checked AFTER arming PR_SET_PDEATHSIG and BEFORE "
        "Popen; arming cannot cover a parent that was already dead when armed"
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
