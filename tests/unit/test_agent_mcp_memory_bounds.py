"""Pins how agent configs launch the two code-intel MCP servers.

Both are per-agent memory hazards on a 15.6GB box whose brain + TTS hold ~9GB,
but they need OPPOSITE fixes, and conflating them is how the bug survived:

* Serena speaks streamable-http, so the fleet shares ONE server and no agent
  config may spawn its own (`command`).
* codebase-memory-mcp 0.8.1 is stdio-only — its `--port` is the UI graph viewer,
  not a transport — so per-agent spawning is forced by the tool. It must
  therefore go through the memory-capping launcher, never the raw binary.

Measured 2026-08-02, with both regressions live at once: seven private Serenas
(~1.4GB) from two Codex sessions, and fourteen codebase-memory instances
(~1.27GB, one 473MB, one resident 2.3 days).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
MCP_JSON = ROOT / ".mcp.json"
CODEX_TOML = ROOT / ".codex" / "config.toml"
CAPPED = "scripts/maintenance/codebase_memory_capped.sh"
OMNIGENT_CODEX_TOML = ROOT / "modules" / "omnigent" / "codex-mcp.toml"
OMNIGENT_MCP_JSON = ROOT / "modules" / "omnigent" / ".mcp.json"
SHARED_SERENA = "http://127.0.0.1:9121/mcp"


def _load_toml_servers(path: Path) -> dict:
    try:
        import tomllib as toml_mod  # py3.11+
    except ModuleNotFoundError:
        import tomli as toml_mod  # py3.10
    with path.open("rb") as fh:
        return toml_mod.load(fh).get("mcp_servers", {})


def _codex_servers() -> dict:
    return _load_toml_servers(CODEX_TOML)


def _claude_servers() -> dict:
    return json.loads(MCP_JSON.read_text()).get("mcpServers", {})


@pytest.fixture(params=["claude", "codex"])
def servers(request):
    return _claude_servers() if request.param == "claude" else _codex_servers()


def test_serena_attaches_to_the_shared_server(servers):
    """A `command` entry silently reintroduces per-agent stdio spawning, and
    nothing alarms: the shared unit stays healthy and the health check passes."""
    entry = servers["serena"]
    assert "command" not in entry, (
        "serena must attach to the shared server by url, never spawn per-agent"
    )
    assert entry.get("url") == SHARED_SERENA


def test_codebase_memory_goes_through_the_capping_launcher(servers):
    """It cannot be consolidated (stdio-only), so each spawn must be bounded."""
    entry = servers["codebase-memory"]
    assert entry.get("command", "").endswith(CAPPED), (
        "codebase-memory must launch via codebase_memory_capped.sh so each "
        "per-agent spawn is memory-capped; the raw binary is unbounded"
    )


def test_no_config_references_the_raw_codebase_memory_binary():
    for path in (MCP_JSON, CODEX_TOML):
        assert "local/bin/codebase-memory-mcp" not in path.read_text(), (
            f"{path.name} still launches the raw uncapped binary"
        )


def test_capping_launcher_exists_and_is_executable():
    script = ROOT / CAPPED
    assert script.exists(), f"{CAPPED} is referenced by agent configs but missing"
    assert script.stat().st_mode & 0o111, f"{CAPPED} is not executable"


def test_launcher_caps_swap_as_well_as_rss():
    """Capping RSS without swap just relocates a leak into swap — measured on
    Serena, which leaked 2.1GB into swap under a MemoryMax that looked correct."""
    body = (ROOT / CAPPED).read_text()
    for prop in ("MemoryHigh", "MemoryMax", "MemorySwapMax"):
        assert prop in body, f"{CAPPED} must set {prop}"


def test_launcher_falls_back_rather_than_failing_closed():
    """No systemd user bus (container, no session) must degrade to an uncapped
    launch, not break code-intel entirely."""
    body = (ROOT / CAPPED).read_text()
    assert "launching uncapped" in body


def test_omnigent_container_codex_serena_attaches_by_url():
    """The zoe-omnigent container's Codex config is the SAME hazard one hop away:
    /root/.codex/config.toml lives in a volume nothing tracked used to own, was
    hand-patched live on 2026-09-25 (two private ~700MB Serenas measured), and a
    volume reset silently brought the stdio spawn back. The tracked template
    (baked into the image, seeded by the entrypoint) must attach by url — the
    zoe-codeintel gateway, not loopback, which inside the container is the
    container itself — and must agree with the container's Claude config."""
    entry = _load_toml_servers(OMNIGENT_CODEX_TOML)["serena"]
    assert "command" not in entry, (
        "modules/omnigent/codex-mcp.toml spawns a per-session Serena again"
    )
    claude_entry = json.loads(OMNIGENT_MCP_JSON.read_text())["mcpServers"]["serena"]
    assert entry.get("url") == claude_entry["url"]
    assert not entry["url"].startswith("http://127."), entry["url"]


def _launch_args(tmp_path: Path, script: Path, extra_env: dict | None = None) -> list[str]:
    """Run the launcher against a fake systemd-run and return the argv it would
    hand systemd-run. Exercises the real script, not a grep of it."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    record = tmp_path / "systemd-run.args"
    systemd_run = fake_bin / "systemd-run"
    systemd_run.write_text(
        "#!/usr/bin/env bash\n"
        # The availability probe ends in `true`; the real launch ends in the binary.
        f'[ "${{@: -1}}" = true ] && exit 0\nprintf "%s\\n" "$@" > {record}\n'
    )
    mcp = fake_bin / "codebase-memory-mcp"
    mcp.write_text("#!/usr/bin/env bash\nexit 0\n")
    for f in (systemd_run, mcp):
        f.chmod(0o755)
    env = {"PATH": f"{fake_bin}:/usr/bin:/bin", "HOME": str(tmp_path),
           "CODEBASE_MEMORY_BIN": str(mcp), **(extra_env or {})}
    subprocess.run(["bash", str(script)], env=env, check=True, timeout=30)
    return record.read_text().splitlines()


def _launch_props(tmp_path: Path, script: Path, extra_env: dict | None = None) -> dict:
    """The -p properties the launcher would hand the scope."""
    args = _launch_args(tmp_path, script, extra_env)
    return dict(args[i + 1].split("=", 1) for i, a in enumerate(args) if a == "-p")


def _throttle_band(props: dict) -> bool:
    return props["MemoryHigh"] != props["MemoryMax"]


def test_soft_cap_equals_hard_cap_by_default(tmp_path):
    """A6 (infra audit 2026-10-03, D3): memory.high never OOM-kills. A 512M high
    under a 768M max held one spawn throttled at ~2k major faults/s and ~200 MB/s
    of NVMe reads for its whole life, with `oom 0`. No throttle band means a
    runaway spawn is killed at the max instead."""
    props = _launch_props(tmp_path, ROOT / CAPPED)
    assert props == {"MemoryHigh": "768M", "MemoryMax": "768M", "MemorySwapMax": "768M"}
    assert not _throttle_band(props)


def test_raising_the_hard_cap_moves_the_soft_cap_and_swap_cap_with_it(tmp_path):
    props = _launch_props(tmp_path, ROOT / CAPPED, {"CODEBASE_MEMORY_MEM_MAX": "1G"})
    assert props == {"MemoryHigh": "1G", "MemoryMax": "1G", "MemorySwapMax": "1G"}


def test_throttle_band_check_catches_the_old_default(tmp_path):
    """Negative control: the pre-A6 launcher (512M high) must read as banded."""
    old = tmp_path / "old_capped.sh"
    body = (ROOT / CAPPED).read_text()
    old.write_text(body.replace('CODEBASE_MEMORY_MEM_HIGH:-$MEM_MAX', "CODEBASE_MEMORY_MEM_HIGH:-512M"))
    assert old.read_text() != body, "negative control did not alter the launcher"
    assert _throttle_band(_launch_props(tmp_path, old))


# --- zoe-agents.slice: the aggregate behind the member caps (agent-sessions-off-box-2026-10-04) ---
#
# Member caps bound each process; only a parent slice bounds the SUM. A scope or unit that is not
# parented to zoe-agents.slice is invisible to the aggregate, and nothing alarms when it is not -
# so the wrapper and the Serena drop-in are exercised/pinned here, beside the caps they sit above.

SLICE = "zoe-agents.slice"
SERENA_DROPIN = ROOT / "scripts" / "setup" / "systemd" / "serena-mcp.service.d" / "70-agents-slice.conf"
SERENA_UNIT = ROOT / "scripts" / "setup" / "systemd" / "serena-mcp.service"


def _directives(path: Path) -> dict[str, str]:
    """Key=value directives of a unit file, comments stripped (the headers name every key)."""
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "[")) and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def test_codebase_memory_scope_joins_the_agents_slice(tmp_path):
    args = _launch_args(tmp_path, ROOT / CAPPED)
    assert f"--slice={SLICE}" in args, "each per-client scope must be charged to the aggregate"
    assert "--scope" in args and "--user" in args


def test_slice_membership_leaves_the_member_cap_unchanged(tmp_path):
    """A parent slice adds an aggregate; it must not loosen the 768M per-spawn cap."""
    assert _launch_props(tmp_path, ROOT / CAPPED) == {
        "MemoryHigh": "768M", "MemoryMax": "768M", "MemorySwapMax": "768M"}


def test_negative_control_wrapper_without_the_slice_is_detected(tmp_path):
    """Control 4 from the record: before the wrapper change the scope lands in app.slice."""
    old = tmp_path / "old_capped.sh"
    body = (ROOT / CAPPED).read_text()
    old.write_text(body.replace(f"        --slice={SLICE} \\\n", ""))
    assert old.read_text() != body, "negative control did not alter the wrapper"
    assert f"--slice={SLICE}" not in _launch_args(tmp_path, old)


def test_serena_dropin_joins_the_agents_slice_without_touching_member_caps():
    d = _directives(SERENA_DROPIN)
    assert d == {"Slice": SLICE}, (
        "the drop-in re-parents only; a Memory* key here would silently replace the unit's member cap"
    )
    unit = _directives(SERENA_UNIT)
    assert unit["MemoryMax"] == "2G" and unit["MemorySwapMax"] == "2G", "the member cap stays on the unit"
    assert "Slice" not in unit, "parenting is the drop-in's job, so rollback is rm-the-file"
