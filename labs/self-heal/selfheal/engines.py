"""Coding-engine adapters: the sold-Zoe seam. A buyer links ONE subscription (ChatGPT -> codex,
Claude -> claude) and the loop drives that CLI headless inside the heal worktree.

Flags come from `codex exec --help` (codex-cli 0.163.0) and `claude --help` (2.1.321) on this
box; nothing here is invented. The engine edits files only; it never commits or pushes (the
runner does, after the diff policy and the gates).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import binaries
from .config import Config
from .detectors import Ticket


@dataclass
class EngineResult:
    ok: bool
    detail: str          # "rc=N" / "timeout" ONLY: model output is never kept or logged
    seconds: float


def build_prompt(ticket: Ticket, cfg: Config) -> str:
    return "\n".join([
        "You are fixing ONE fault in this git checkout. Work only inside the current directory.",
        f"Fault class: {ticket.fault_class}",
        f"Fault: {ticket.summary}",
        f"Failing test: {ticket.evidence.get('nodeid', '')}",
        f"Reproduction (must exit 0 when fixed): {' '.join(ticket.repro_cmd)}",
        "Rules:",
        "- Fix the cause in the code under test. Do NOT weaken, skip or delete the failing test:",
        "  the fix is verified against the ORIGINAL test file.",
        f"- Keep the change small (under {cfg.diff_line_cap} changed lines).",
        "- Never edit CI workflows, compose files, systemd units, landing or deploy scripts, .env files,",
        "  migrations, auth code, model files or anything outside this directory.",
        "- Do not run git commit, git push, gh or any network command. Leave the changes uncommitted.",
        "- When done, reply with one short sentence saying what you changed.",
    ])


ENV_ALLOW = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TERM", "TMPDIR", "SHELL", "CODEX_HOME", "CLAUDE_CONFIG_DIR")


def engine_env() -> dict:
    """An ALLOW-list, not a deny-list: the engine gets no tokens (GH_*, MULTICA_*, DB URLs, API
    keys) even when the unit's environment carries them; its own login lives in its home dir."""
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOW or k.startswith(("LC_", "XDG_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(argv: list, cwd: str, timeout: int, exe: str | None = None) -> EngineResult:
    """`exe`: the resolved absolute engine binary; None = refuse (never the inherited PATH)."""
    t0 = time.monotonic()
    if not exe:
        return EngineResult(False, "rc=binary_unresolved", 0.0)
    argv = [exe, *argv[1:]]
    env = engine_env()
    p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        return EngineResult(False, "timeout", time.monotonic() - t0)
    return EngineResult(p.returncode == 0, f"rc={p.returncode}", time.monotonic() - t0)


# --- final-argv assertions: the safety flags appear exactly once, with the built-in values ------

_DANGER = ("--dangerously", "--add-dir", "--cd", "--config", "--sandbox", "--profile", "--enable",
           "--disable", "--full-auto", "--approve-for-me", "--worktree", "--oss", "--local-provider",
           "--permission-prompts", "--disallowedTools", "--disallowed-tools", "--allowed-tools",
           "--settings", "--mcp-config", "--plugin-dir", "--append-system-prompt", "--system-prompt")


class UnsafeArgv(ValueError):
    pass


def _flags(argv: list, stop: int) -> list:
    return [a for a in argv[1:stop] if a.startswith("-")]


def check_codex_argv(argv: list, worktree: str) -> None:
    body = argv[:-1]                      # the prompt is the single trailing positional
    flags = _flags(body, len(body))
    want = ["-C", "-s", "--ephemeral", "--color", "--ignore-user-config"] + (["-m"] if "-m" in flags else [])
    if argv[1] != "exec" or sorted(flags) != sorted(want):
        raise UnsafeArgv(f"codex flags {flags} != the built-in set")
    if body[body.index("-C") + 1] != worktree or body[body.index("-s") + 1] != "workspace-write":
        raise UnsafeArgv("codex -C / -s must be the heal worktree / workspace-write")
    if any(a.startswith(_DANGER) or "=" in a and a.startswith("-") for a in argv[1:-1]):
        raise UnsafeArgv("codex argv carries a forbidden flag")
    if argv[-1].startswith("-"):
        raise UnsafeArgv("codex prompt may not look like a flag")


def check_claude_argv(argv: list, tools: list) -> None:
    sep = argv.index("--")
    if sep != len(argv) - 2:
        raise UnsafeArgv("claude prompt must be the single argument after `--`")
    flags = _flags(argv, sep)
    want = ["-p", "--permission-mode", "--allowedTools", "--no-session-persistence", "--output-format"] + \
        (["--model"] if "--model" in flags else [])
    if sorted(flags) != sorted(want):
        raise UnsafeArgv(f"claude flags {flags} != the built-in set")
    i = argv.index("--permission-mode")
    if argv[i + 1] != "dontAsk":
        raise UnsafeArgv("claude permission mode must be dontAsk")
    j = argv.index("--allowedTools")
    if argv[j + 1: j + 1 + len(tools)] != tools:
        raise UnsafeArgv("claude tool allow-list changed")
    if any(a.startswith(_DANGER) for a in argv[1:sep]):
        raise UnsafeArgv("claude argv carries a forbidden flag")


def _bin(cfg: Config, name: str) -> str | None:
    """The engine CLI's absolute path from the trusted PATH (or the configured absolute path), checked."""
    try:
        return binaries.find(getattr(cfg, f"{name}_bin"), cfg)
    except binaries.UnsafeBinary:
        return None


class CodexEngine:
    """`codex exec` (ChatGPT subscription). workspace-write sandbox: writes confined to -C dir,
    no network for model-run commands."""
    name = "codex"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def available(self) -> tuple[bool, str]:
        if not _bin(self.cfg, "codex"):
            return False, "codex_not_found_or_unsafe"
        r = subprocess.run([_bin(self.cfg, "codex"), "login", "status"], capture_output=True, text=True, timeout=30)
        return r.returncode == 0, "logged_in" if r.returncode == 0 else "codex_not_logged_in"

    def argv(self, worktree: str, prompt: str) -> list:
        a = [self.cfg.codex_bin, "exec", "-C", worktree, "-s", "workspace-write", "--ephemeral",
             "--color", "never", "--ignore-user-config"]
        if self.cfg.model:
            a += ["-m", self.cfg.model]
        a.append(prompt)
        check_codex_argv(a, worktree)
        return a

    def run(self, ticket: Ticket, worktree: str) -> EngineResult:
        return _run(self.argv(worktree, build_prompt(ticket, self.cfg)), worktree, self.cfg.engine_timeout_s,
                    _bin(self.cfg, self.name))


class ClaudeEngine:
    """`claude -p` (Claude subscription). dontAsk + an allow-list: file tools and pytest only,
    so anything else (git, gh, curl) is denied rather than prompted."""
    name = "claude"
    TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "Bash(python3 -m pytest:*)"]

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def available(self) -> tuple[bool, str]:
        if not _bin(self.cfg, "claude"):
            return False, "claude_not_found_or_unsafe"
        r = subprocess.run([_bin(self.cfg, "claude"), "auth", "status"], capture_output=True, text=True, timeout=30)
        try:
            ok = r.returncode == 0 and json.loads(r.stdout).get("loggedIn") is True
        except ValueError:
            ok = False
        return ok, "logged_in" if ok else "claude_not_logged_in"

    def argv(self, worktree: str, prompt: str) -> list:
        a = [self.cfg.claude_bin, "-p", "--permission-mode", "dontAsk", "--allowedTools", *self.TOOLS,
             "--no-session-persistence", "--output-format", "text"]
        if self.cfg.model:
            a += ["--model", self.cfg.model]
        a += ["--", prompt]
        check_claude_argv(a, self.TOOLS)
        return a

    def run(self, ticket: Ticket, worktree: str) -> EngineResult:
        return _run(self.argv(worktree, build_prompt(ticket, self.cfg)), worktree, self.cfg.engine_timeout_s,
                    _bin(self.cfg, self.name))


class FlueExecutorEngine:
    """The plan-of-record seam: Multica board -> Flue executor (labs/flue-executor) -> Omnigent
    worker on the linked subscription. Selected first on this unit when configured; today it
    reports itself unavailable with the precise blockers, and the loop falls back.

    Blockers (README "Executor path"): the executor idles while the Multica dispatch kill switch
    exists (never bypassed here); its unit is not installed; and there is no heal enqueue yet
    (an `agent_task_queue` row whose work_dir the worker can see: Omnigent mounts only the live
    checkout, not ~/.worktrees, and the local Flue lane's worker is synthetic)."""
    name = "flue_executor"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def available(self) -> tuple[bool, str]:
        why = []
        if Path(self.cfg.multica_kill_switch).expanduser().exists():
            why.append("multica_kill_switch_present")
        r = subprocess.run([binaries.which("systemctl"), "--user", "cat", "flue-executor.service"], capture_output=True, text=True)
        if r.returncode != 0:
            why.append("executor_unit_not_installed")
        why.append("heal_enqueue_not_built")
        return False, ",".join(why)

    def run(self, ticket: Ticket, worktree: str) -> EngineResult:
        return EngineResult(False, "flue_executor not available", 0.0)


ENGINES = {"flue_executor": FlueExecutorEngine, "codex": CodexEngine, "claude": ClaudeEngine}


def make_engine(cfg: Config, name: str | None = None):
    name = name or cfg.engine
    if name not in ENGINES:
        raise ValueError(f"engine must be one of {sorted(ENGINES)} or none, not {name!r}")
    return ENGINES[name](cfg)


def select_engine(cfg: Config) -> tuple[object | None, list]:
    """First AVAILABLE engine of [engine, *engine_fallbacks]; None = no engine linked."""
    skipped = []
    for name in [cfg.engine, *cfg.engine_fallbacks]:
        if name == "none":
            continue
        eng = make_engine(cfg, name)
        try:
            ok, why = eng.available()
        except (OSError, subprocess.SubprocessError) as e:
            ok, why = False, type(e).__name__
        if ok:
            return eng, skipped
        skipped.append({"engine": name, "why": why})
    return None, skipped
