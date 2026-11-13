"""Coding-engine adapters: the sold-Zoe seam. A buyer links ONE subscription (ChatGPT -> codex,
Claude -> claude) and the loop drives that CLI headless inside the heal worktree.

Flags come from `codex exec --help` (codex-cli 0.163.0) and `claude --help` (2.1.321) on this
box; nothing here is invented. The engine edits files only; it never commits or pushes (the
runner does, after the diff policy and the gates).
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

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


def _run(argv: list, cwd: str, timeout: int) -> EngineResult:
    t0 = time.monotonic()
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


def _bin_ok(binary: str) -> bool:
    return bool(shutil.which(binary)) or (os.path.isabs(binary) and os.access(binary, os.X_OK))


class CodexEngine:
    """`codex exec` (ChatGPT subscription). workspace-write sandbox: writes confined to -C dir,
    no network for model-run commands."""
    name = "codex"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def available(self) -> tuple[bool, str]:
        if not _bin_ok(self.cfg.codex_bin):
            return False, "codex_not_found"
        r = subprocess.run([self.cfg.codex_bin, "login", "status"], capture_output=True, text=True, timeout=30)
        return r.returncode == 0, "logged_in" if r.returncode == 0 else "codex_not_logged_in"

    def argv(self, worktree: str, prompt: str) -> list:
        a = [self.cfg.codex_bin, "exec", "-C", worktree, "-s", "workspace-write", "--ephemeral",
             "--color", "never", *self.cfg.codex_args]
        if self.cfg.model:
            a += ["-m", self.cfg.model]
        return a + [prompt]

    def run(self, ticket: Ticket, worktree: str) -> EngineResult:
        return _run(self.argv(worktree, build_prompt(ticket, self.cfg)), worktree, self.cfg.engine_timeout_s)


class ClaudeEngine:
    """`claude -p` (Claude subscription). dontAsk + an allow-list: file tools and pytest only,
    so anything else (git, gh, curl) is denied rather than prompted."""
    name = "claude"
    TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "Bash(python3 -m pytest:*)"]

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def available(self) -> tuple[bool, str]:
        if not _bin_ok(self.cfg.claude_bin):
            return False, "claude_not_found"
        r = subprocess.run([self.cfg.claude_bin, "auth", "status"], capture_output=True, text=True, timeout=30)
        try:
            ok = r.returncode == 0 and json.loads(r.stdout).get("loggedIn") is True
        except ValueError:
            ok = False
        return ok, "logged_in" if ok else "claude_not_logged_in"

    def argv(self, worktree: str, prompt: str) -> list:
        a = [self.cfg.claude_bin, "-p", "--permission-mode", "dontAsk", "--allowedTools", *self.TOOLS,
             "--no-session-persistence", "--output-format", "text", *self.cfg.claude_args]
        if self.cfg.model:
            a += ["--model", self.cfg.model]
        return a + ["--", prompt]

    def run(self, ticket: Ticket, worktree: str) -> EngineResult:
        return _run(self.argv(worktree, build_prompt(ticket, self.cfg)), worktree, self.cfg.engine_timeout_s)


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
        r = subprocess.run(["systemctl", "--user", "cat", "flue-executor.service"], capture_output=True, text=True)
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
