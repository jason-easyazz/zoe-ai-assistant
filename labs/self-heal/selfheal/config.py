"""Self-heal configuration: one JSON file, every knob explicit, safe defaults.

Defaults are the SAFE side of every choice: dispatch `dry`, landing `github` (which only
ever opens a DRAFT PR), daily cap 3, per-fault cap 2, a 300-line diff cap.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

HOME = Path.home()

# Paths the agent's diff may never touch (fnmatch patterns against repo-relative paths, plus prefixes).
DEFAULT_FORBIDDEN = [
    ".github/workflows/*",
    "docker-compose*.yml", "docker-compose*.yaml", "*/docker-compose*.yml", "*/docker-compose*.yaml",
    "compose*.yml", "compose*.yaml", "*/compose*.yml", "*/compose*.yaml",
    "*.service", "*.timer", "*.socket", "*.path", "scripts/setup/systemd/*",
    "scripts/landing/*",
    ".env*", "*/.env*",
    "services/zoe-data/alembic/*",
    "services/zoe-auth/*",
    "docs/CANONICAL.md",
    "services/zoe-data/tests/test_canonical_invariants.py",
    # the voice gate and every script the deploy / voice-gate workflows call. A LITERAL list on
    # purpose: computing it from the workflow at run time would let the workflow (which the engine
    # must not influence) define the floor. tests/test_self_heal_round3.py keeps it complete.
    "scripts/maintenance/voice_gate_check.py",
    "scripts/maintenance/voice_regression_probe.py",
    "scripts/maintenance/deploy_live.sh",
    "scripts/maintenance/requirements_drift_check.py",
    "scripts/maintenance/wait_household_quiet.sh",
    "scripts/deploy/*",
    "scripts/night/night_window.py", "scripts/night/night_window.sh",
    "scripts/setup/build_py312_venv.sh",
    "tools/audit/flag_inventory.py",
    # the live Flue sidecars (the deploy workflow runs their dist/server.mjs): the brain and the Telegram bot
    "labs/flue-zoe-brain-2x/*", "labs/flue-zoe-telegram-2x/*",
    # DOX contracts: an autonomous loop never edits an AGENTS.md (root AGENTS.md "Knowledge vs. Records")
    "AGENTS.md", "*/AGENTS.md", "CLAUDE.md", "*/CLAUDE.md",
    # model files
    "models/*", "services/zoe-data/models/*", "*.gguf", "*.safetensors", "*.onnx", "*.pt", "*.bin", "*.npy",
    # the loop itself
    "labs/self-heal/*",
    # test-runner config: a "fix" that reconfigures pytest is not a fix
    "conftest.py", "*/conftest.py", "pytest.ini", "*/pytest.ini", "pyproject.toml", "*/pyproject.toml",
    "setup.cfg", "*/setup.cfg", "tox.ini", "*/tox.ini",
]

# --- HARD FLOOR: config may tighten these, never loosen them (Config.validate refuses) ----------
HARD_FORBIDDEN = tuple(DEFAULT_FORBIDDEN)
MAX_DAILY_CAP, MAX_PER_FAULT_CAP, MAX_DIFF_LINES, MAX_PARK_AFTER = 3, 2, 300, 2
MAX_SANDBOX_MEM_MB, MAX_SANDBOX_PROCS = 2048, 64
HARD_QUIET = ("01:30", "05:00")
# The live checkout of THIS box, refused as repo / integration / apply target / worktree parent
# whatever the config says (`live_checkout` in the config is an ADDITIONAL refused path).
HARD_LIVE_CHECKOUT = "/home/zoe/assistant"
NETWORK_BINARIES = {"curl", "wget", "gh", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "netcat", "socat",
                    "telnet", "ftp", "git", "docker", "pip", "pip3", "npm", "npx", "node", "bash", "sh",
                    "env", "sudo"}


# The ONE interpreter the loop runs: the one running it. Config cannot point "python" elsewhere.
PINNED_PYTHON = os.path.abspath(sys.executable)
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


def is_pinned_python(p: str) -> bool:
    return bool(p) and (os.path.abspath(p) == PINNED_PYTHON
                        or os.path.realpath(p) == os.path.realpath(PINNED_PYTHON))


def hard_kill_switch() -> Path:
    """Always honoured, computed at call time (HOME may differ under test)."""
    return Path.home() / ".zoe" / "self_heal_paused"


def refused_roots(extra: str = "") -> list:
    roots = []
    for p in (HARD_LIVE_CHECKOUT, extra):
        if p:
            roots += [Path(os.path.abspath(p)), Path(os.path.realpath(p))]
    return roots


def inside_refused(path: str, extra: str = "") -> bool:
    cands = {Path(os.path.abspath(path)), Path(os.path.realpath(path))}
    return any(c == r or r in c.parents for c in cands for r in refused_roots(extra))


def _minutes(hm: str) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def window_minutes(start: str, end: str) -> set:
    a, b = _minutes(start), _minutes(end)
    return set(range(a, b)) if a <= b else set(range(a, 1440)) | set(range(0, b))


# A diff touching one of these needs a voice replay gate (fail-closed when none is configured).
DEFAULT_VOICE_PATHS = [
    "services/zoe-data/*", "labs/flue-zoe-brain-2x/*", "services/zoe-core/*",
]


@dataclass
class Config:
    # where things live
    repo: str = str(HOME / ".worktrees" / "self-heal-base")  # any non-live checkout or bare clone
    live_checkout: str = "/home/zoe/assistant"                 # NEVER written; guarded
    worktree_root: str = str(HOME / ".worktrees")
    state_dir: str = str(HOME / ".zoe" / "self-heal")
    kill_switches: list = field(default_factory=lambda: [str(HOME / ".zoe" / "self_heal_paused")])

    # behaviour
    dispatch: str = "dry"            # dry | local | full
    landing: str = "github"          # github | local   (used only in `full`)
    engine: str = "codex"            # flue_executor | codex | claude | none
    engine_fallbacks: list = field(default_factory=list)   # tried in order when `engine` is unavailable
    multica_kill_switch: str = str(HOME / ".zoe" / "multica_dispatch_paused")   # read, never touched
    engine_timeout_s: int = 600
    codex_bin: str = "codex"         # basename must be `codex`
    codex_args: list = field(default_factory=list)    # REFUSED unless empty: no extra engine flags
    claude_bin: str = "claude"       # basename must be `claude`
    claude_args: list = field(default_factory=list)   # REFUSED unless empty
    model: str = ""                  # the one engine option: a bare model name (MODEL_RE)

    board: str = "off"               # off | multica  (best-effort mirror; never required)

    # caps
    daily_cap: int = 3
    per_fault_cap: int = 2
    diff_line_cap: int = 300
    not_healed_park_after: int = 2

    # schedule / busy
    quiet_start: str = "01:30"
    quiet_end: str = "05:00"
    voice_lander_pattern: str = r"^bash .*/land_voice_pr\.sh"
    night_busy_script: str = "scripts/landing/night_busy.sh"   # repo-relative or absolute; "" = skip

    # detection (failing-test detector)
    detect_cmd: list = field(default_factory=lambda: [
        PINNED_PYTHON, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-m", "ci_safe", "tests/unit"])
    detect_timeout_s: int = 1800
    ignore_nodeids: list = field(default_factory=list)
    python: str = PINNED_PYTHON      # must BE the running interpreter; anything else is refused

    # sandbox for every gate that runs repo code (selfheal/sandbox.py); limits may only be lowered
    sandbox: str = "auto"            # auto | bwrap | none  (none is refused while bwrap works here)
    sandbox_mem_mb: int = MAX_SANDBOX_MEM_MB
    sandbox_procs: int = MAX_SANDBOX_PROCS

    # gates
    test_lock: str = "/tmp/zoe-test-lane.lock"   # "" = no flock (scratch repos)
    test_lock_wait_s: int = 14000
    gate_timeout_s: int = 900
    gates: list = field(default_factory=lambda: ["validate_structure", "py_compile", "relevant_pytest"])
    local_gates: list = field(default_factory=lambda: [
        "validate_structure", "py_compile", "relevant_pytest", "offline_lane", "voice_replay"])
    offline_lane_cmd: list = field(default_factory=lambda: [
        PINNED_PYTHON, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-m", "ci_safe",
        "services/zoe-data/tests", "tests/unit"])
    voice_replay_cmd: list = field(default_factory=list)   # a deployed unit sets its local replay here
    voice_paths: list = field(default_factory=lambda: list(DEFAULT_VOICE_PATHS))
    forbidden: list = field(default_factory=lambda: list(DEFAULT_FORBIDDEN))

    # github landing
    base_branch: str = "main"
    pr_label: str = "self-heal"
    cross_review_script: str = "scripts/maintenance/cross_review.sh"
    cross_review_timeout_s: int = 2400

    # local landing
    integration_branch: str = "main"
    health_checks: list = field(default_factory=list)   # argv lists, allow-listed (see check_health_argv)
    health_timeout_s: int = 120
    apply_target: str = ""                               # lab: none; refused if it is the live checkout

    @classmethod
    def load(cls, path: str | None, **overrides) -> "Config":
        data = json.loads(Path(path).read_text()) if path else {}
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        data.update({k: v for k, v in overrides.items() if v is not None})
        cfg = cls(**data)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.dispatch not in ("dry", "local", "full"):
            raise ValueError(f"dispatch must be dry|local|full, not {self.dispatch!r}")
        if self.landing not in ("github", "local"):
            raise ValueError(f"landing must be github|local, not {self.landing!r}")
        if self.board not in ("off", "multica"):
            raise ValueError(f"board must be off|multica, not {self.board!r}")
        for name in ("repo", "worktree_root", "state_dir", "apply_target"):
            v = getattr(self, name)
            if v and inside_refused(v, self.live_checkout):
                raise ValueError(f"{name} ({v}) is the live checkout or inside it; refusing")
        # hard floor: only ever tighter than the built-ins
        missing = [p for p in HARD_FORBIDDEN if p not in self.forbidden]
        if missing:
            raise ValueError(f"forbidden may only ADD paths; it drops {missing[:5]}")
        for name, cap in (("daily_cap", MAX_DAILY_CAP), ("per_fault_cap", MAX_PER_FAULT_CAP),
                          ("diff_line_cap", MAX_DIFF_LINES), ("not_healed_park_after", MAX_PARK_AFTER),
                          ("sandbox_mem_mb", MAX_SANDBOX_MEM_MB), ("sandbox_procs", MAX_SANDBOX_PROCS)):
            v = getattr(self, name)
            if not isinstance(v, int) or v < 0 or v > cap:
                raise ValueError(f"{name}={v!r}: caps may only be lowered (0..{cap})")
        if not window_minutes(*HARD_QUIET) <= window_minutes(self.quiet_start, self.quiet_end):
            raise ValueError(f"quiet hours {self.quiet_start}-{self.quiet_end} must include "
                             f"{HARD_QUIET[0]}-{HARD_QUIET[1]} (they may only widen)")
        from .sandbox import best_backend   # noqa: PLC0415 (sandbox imports this module)
        if self.sandbox not in ("auto", "bwrap", "none"):
            raise ValueError(f"sandbox must be auto|bwrap|none, not {self.sandbox!r}")
        if self.sandbox == "none" and best_backend()[0] != "none":
            raise ValueError("sandbox none is weaker than the best backend available here; refused")
        if not isinstance(self.kill_switches, list):
            raise ValueError("kill_switches must be a list (the built-in switch is always honoured too)")
        # the interpreter, the engines and every command the loop runs are pinned
        if not is_pinned_python(self.python):
            raise ValueError(f"python must be the running interpreter ({PINNED_PYTHON}); refused {self.python!r}")
        self.python = PINNED_PYTHON
        if self.codex_args or self.claude_args:
            raise ValueError("extra engine arguments (codex_args / claude_args) are refused: "
                             "the safety flags are built in and cannot be extended")
        if self.model and not MODEL_RE.match(self.model):
            raise ValueError(f"model must be a bare model name, not {self.model!r}")
        for name, want in (("codex_bin", "codex"), ("claude_bin", "claude")):
            if os.path.basename(getattr(self, name)) != want:
                raise ValueError(f"{name} must be a `{want}` binary, not {getattr(self, name)!r}")
        for name in ("detect_cmd", "offline_lane_cmd"):
            check_pytest_argv(getattr(self, name), name)
        if self.voice_replay_cmd:
            check_script_argv(self.voice_replay_cmd, "voice_replay_cmd")
        for argv in self.health_checks:
            check_health_argv(argv, self.python)

    def all_kill_switches(self) -> list:
        return [str(hard_kill_switch()), *self.kill_switches]

    def effective_forbidden(self) -> list:
        return list(dict.fromkeys([*HARD_FORBIDDEN, *self.forbidden]))


def _rel_args_ok(args: list) -> bool:
    return not any(a.startswith("/") or ".." in a.split("/") or a == "-c" for a in args)


def check_pytest_argv(argv, name: str) -> None:
    """detect / offline lanes: `<pinned python> -m pytest <relative args>`, nothing else."""
    if not (isinstance(argv, list) and len(argv) >= 3 and all(isinstance(a, str) for a in argv)
            and is_pinned_python(argv[0]) and argv[1:3] == ["-m", "pytest"] and _rel_args_ok(argv[3:])):
        raise ValueError(f"{name} must be `<running python> -m pytest <relative args>`: {argv!r}")


def check_script_argv(argv, name: str) -> None:
    if not (isinstance(argv, list) and len(argv) >= 2 and all(isinstance(a, str) for a in argv)
            and is_pinned_python(argv[0]) and argv[1].endswith(".py") and _rel_args_ok(argv[1:])):
        raise ValueError(f"{name} must be `<running python> <rel/path.py> ...`: {argv!r}")


def check_health_argv(argv, python: str = PINNED_PYTHON) -> None:
    """Local-landing health checks are an ALLOW-LIST, refused at load otherwise. Allowed forms:
      ["true"] / ["false"]                                   (tests, no-ops)
      ["systemctl", "--user", "is-active", "<unit>"]          (read-only unit state)
      [<python>, "-m", "<module>"] / [<python>, "<rel/path.py>"] run from a fresh worktree of the
                                                             integration tip (never the live checkout)
    No shell strings, no network-capable binary, no absolute or `..` paths, no `python -c`."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise ValueError(f"health check must be an argv list of strings, not {argv!r}")
    exe = os.path.basename(argv[0])
    if exe in NETWORK_BINARIES:
        raise ValueError(f"health check {exe!r} is network-capable or a shell; refused")
    if argv in (["true"], ["false"]):
        return
    if argv[0] == "systemctl":
        if len(argv) == 4 and argv[1:3] == ["--user", "is-active"] and argv[3].replace("-", "").replace(".", "").replace("@", "").isalnum():
            return
        raise ValueError(f"systemctl health check must be `systemctl --user is-active <unit>`: {argv!r}")
    if is_pinned_python(argv[0]):
        if len(argv) >= 3 and argv[1] == "-m" and argv[2].replace(".", "").replace("_", "").isalnum():
            rest = argv[3:]
        elif len(argv) >= 2 and argv[1].endswith(".py"):
            rest = argv[1:]
        else:
            raise ValueError(f"python health check must be `-m <module>` or `<rel/path.py>`: {argv!r}")
        for a in rest:
            if a.startswith("/") or ".." in a.split("/") or a == "-c":
                raise ValueError(f"health check argument {a!r} leaves the integration checkout; refused")
        return
    raise ValueError(f"health check {argv[0]!r} is not on the allow-list (python must be the running interpreter)")
