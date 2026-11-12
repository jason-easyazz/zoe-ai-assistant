"""Self-heal configuration: one JSON file, every knob explicit, safe defaults.

Defaults are the SAFE side of every choice: dispatch `dry`, landing `github` (which only
ever opens a DRAFT PR), daily cap 3, per-fault cap 2, a 300-line diff cap.
"""
from __future__ import annotations

import json
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
    # the voice gate and every script the deploy / voice-gate workflows call
    "scripts/maintenance/voice_gate_check.py",
    "scripts/maintenance/voice_regression_probe.py",
    "scripts/maintenance/deploy_live.sh",
    "scripts/maintenance/requirements_drift_check.py",
    "scripts/maintenance/wait_household_quiet.sh",
    "scripts/deploy/*",
    "scripts/night/night_window.py", "scripts/night/night_window.sh",
    "scripts/setup/build_py312_venv.sh",
    # model files
    "models/*", "services/zoe-data/models/*", "*.gguf", "*.safetensors", "*.onnx", "*.pt", "*.bin", "*.npy",
    # the loop itself
    "labs/self-heal/*",
]

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
    codex_bin: str = "codex"
    codex_args: list = field(default_factory=lambda: ["--ignore-user-config"])
    claude_bin: str = "claude"
    claude_args: list = field(default_factory=list)
    model: str = ""

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
        "python3", "-m", "pytest", "-p", "no:cacheprovider", "-q", "-m", "ci_safe", "tests/unit"])
    detect_timeout_s: int = 1800
    ignore_nodeids: list = field(default_factory=list)
    python: str = "python3"

    # gates
    test_lock: str = "/tmp/zoe-test-lane.lock"   # "" = no flock (scratch repos)
    test_lock_wait_s: int = 14000
    gate_timeout_s: int = 900
    gates: list = field(default_factory=lambda: ["validate_structure", "py_compile", "relevant_pytest"])
    local_gates: list = field(default_factory=lambda: [
        "validate_structure", "py_compile", "relevant_pytest", "offline_lane", "voice_replay"])
    offline_lane_cmd: list = field(default_factory=lambda: [
        "python3", "-m", "pytest", "-p", "no:cacheprovider", "-q", "-m", "ci_safe",
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
    health_checks: list = field(default_factory=list)   # list of argv lists run after apply

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
        live = Path(self.live_checkout).resolve()
        for name in ("repo", "worktree_root", "state_dir"):
            p = Path(getattr(self, name)).resolve()
            if p == live or live in p.parents:
                raise ValueError(f"{name} ({p}) is the live checkout or inside it; refusing")
