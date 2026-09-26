"""Pin the omnigent entrypoint's Codex MCP config seed (B0.11).

`/root/.codex/config.toml` lives in the persisted omnigent-codex VOLUME and
nothing tracked used to own it: it kept a per-session stdio Serena spawn long
after `.mcp.json` was migrated (two private ~700 MB Serenas measured
2026-09-25), was hand-patched live, and any volume reset brought the spawn
back. The entrypoint now rewrites the managed `[mcp_servers.*]` tables from
the baked template `modules/omnigent/codex-mcp.toml` on every boot. It must:

  * replace a legacy stdio `command =` serena table with the template's `url =`
    while keeping everything else in the file (Codex's own `hooks.state`
    trusted hashes) verbatim;
  * be idempotent — an already-current file is not rewritten;
  * fail SAFE and NON-FATALLY — an unparseable file is left alone with a
    warning, and the entrypoint still exits 0 (the server must boot).

The REAL entrypoint is executed. The seed reads its paths from the script's
own CODEX_* testability overrides into tmp_path; `omnigent` / `curl` are
no-op stubs and the cursor selection is pointed at empty tmp dirs (it warns
non-fatally). python3 is the test interpreter (it has tomllib or tomli).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# stdlib + bash/coreutils only (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe

try:
    import tomllib
except ModuleNotFoundError:  # py3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "modules" / "omnigent" / "entrypoint.sh"
TEMPLATE = ROOT / "modules" / "omnigent" / "codex-mcp.toml"

# The exact pre-patch file from the live container (config.toml.bak-20260925).
LEGACY_STDIO = '''[mcp_servers.serena]
command = "/home/zoe/.local/bin/serena"
args = ["start-mcp-server", "--context", "codex", "--project", "/workspace", "--transport", "stdio", "--enable-web-dashboard", "false"]
# Neutralize any inherited venv/PYTHONPATH leak from the harness parent.
env = { PYTHONPATH = "", VIRTUAL_ENV = "", PYTHONHOME = "" }

[mcp_servers.codebase-memory]
command = "/home/zoe/.local/bin/codebase-memory-mcp"

[hooks.state."/root/.codex/hooks.json:pre_tool_use:0:0"]
trusted_hash = "sha256:dd4b4765e24762af94844ce61aeb46521838f0d9d6bd21f815859dbf18b453ee"

[hooks.state."/root/.codex/hooks.json:post_tool_use:0:0"]
trusted_hash = "sha256:f105564cc41fd61b6af206e8514fea28e1668d20b6f86f12e65dc95e56820067"
'''


def _stubs(tmp_path: Path) -> Path:
    stub = tmp_path / "stub"
    stub.mkdir()
    for name in ("omnigent", "curl"):
        p = stub / name
        p.write_text("#!/usr/bin/env bash\nexit 0\n")
        p.chmod(0o755)
    # The seed needs a REAL python with tomllib/tomli: the test interpreter.
    (stub / "python3").symlink_to(sys.executable)
    return stub


def _run(tmp_path: Path, cfg: Path, template: Path = TEMPLATE):
    stub = _stubs(tmp_path) if not (tmp_path / "stub").exists() else tmp_path / "stub"
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": f"{stub}:/usr/bin:/bin:/usr/local/bin",
        "HOME": str(home),
        # HOME is redirected, so a user-site tomli (py3.10) would vanish from the
        # seed's python3; hand it the site dir explicitly. No-op for stdlib tomllib.
        "PYTHONPATH": str(Path(tomllib.__file__).resolve().parent.parent),
        "CODEX_CONFIG_PATH": str(cfg),
        "CODEX_MCP_TEMPLATE": str(template),
        # Point the cursor selection at nothing so it warns non-fatally.
        "CURSOR_VERSIONS_ROOT": str(tmp_path / "no-versions"),
        "CURSOR_LINK_DIR": str(tmp_path / "linkdir"),
        "CURSOR_PIN_LINK": str(tmp_path / "absent" / "cursor-agent"),
    }
    return subprocess.run(
        ["bash", str(ENTRYPOINT)], capture_output=True, text=True, env=env, timeout=60
    )


def _servers(cfg: Path) -> dict:
    return tomllib.loads(cfg.read_text())["mcp_servers"]


def _template_servers() -> dict:
    return tomllib.loads(TEMPLATE.read_text())["mcp_servers"]


def test_legacy_stdio_serena_is_replaced_and_the_rest_is_kept(tmp_path: Path):
    """THE REGRESSION: the pre-patch live file must come out attached by url,
    with Codex's own hooks.state tables untouched."""
    cfg = tmp_path / "codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(LEGACY_STDIO)

    proc = _run(tmp_path, cfg)

    assert proc.returncode == 0, proc.stderr
    assert "rewrote managed [mcp_servers.*] tables" in proc.stdout, proc.stdout + proc.stderr
    data = tomllib.loads(cfg.read_text())
    assert data["mcp_servers"] == _template_servers()
    assert "command" not in data["mcp_servers"]["serena"]
    assert "stdio" not in repr(data["mcp_servers"]["serena"])
    # Everything that is not a managed table survives verbatim.
    assert data["hooks"]["state"] == tomllib.loads(LEGACY_STDIO)["hooks"]["state"]
    # A backup of the pre-seed file is kept beside it.
    assert list(cfg.parent.glob("config.toml.bak-*")), os.listdir(cfg.parent)


def test_seed_is_idempotent(tmp_path: Path):
    cfg = tmp_path / "codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(LEGACY_STDIO)
    assert _run(tmp_path, cfg).returncode == 0
    after_first = cfg.read_text()
    backups = sorted(cfg.parent.glob("config.toml.bak-*"))

    proc = _run(tmp_path, cfg)

    assert proc.returncode == 0, proc.stderr
    assert "already current" in proc.stdout, proc.stdout + proc.stderr
    assert cfg.read_text() == after_first
    assert sorted(cfg.parent.glob("config.toml.bak-*")) == backups, "no new backup"


def test_absent_config_is_created(tmp_path: Path):
    cfg = tmp_path / "codex" / "config.toml"  # directory does not exist yet
    proc = _run(tmp_path, cfg)
    assert proc.returncode == 0, proc.stderr
    assert _servers(cfg) == _template_servers()


def test_unparseable_config_is_left_alone_and_the_boot_continues(tmp_path: Path):
    """Fail safe: never clobber a file we cannot read back, never kill the boot."""
    cfg = tmp_path / "codex" / "config.toml"
    cfg.parent.mkdir()
    broken = "[mcp_servers.serena\ncommand = 'oops'\n"
    cfg.write_text(broken)

    proc = _run(tmp_path, cfg)

    assert proc.returncode == 0, "the seed must be non-fatal:\n" + proc.stderr
    assert cfg.read_text() == broken
    assert "does not parse" in proc.stderr, proc.stderr
    assert not list(cfg.parent.glob("config.toml.bak-*"))


def test_template_that_spawns_serena_is_refused(tmp_path: Path):
    """The seed is a guard, not a copier: a template that reintroduces a
    `command =` serena must be refused, leaving the current file as is."""
    cfg = tmp_path / "codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(LEGACY_STDIO)
    bad = tmp_path / "bad.toml"
    bad.write_text('[mcp_servers.serena]\ncommand = "/home/zoe/.local/bin/serena"\n')

    proc = _run(tmp_path, cfg, template=bad)

    assert proc.returncode == 0, proc.stderr
    assert cfg.read_text() == LEGACY_STDIO
    assert "refusing to seed" in proc.stderr, proc.stderr


def test_unrelated_server_whose_quoted_name_starts_with_a_managed_name_survives(tmp_path: Path):
    """Greptile #1700: `[mcp_servers."serena.debug"]` is a DIFFERENT server (a quoted
    key), not a sub-table of serena — the seed must not strip it, and the
    post-merge guard must refuse a rewrite that loses any unmanaged server."""
    cfg = tmp_path / "codex" / "config.toml"
    cfg.parent.mkdir()
    extra = '[mcp_servers."serena.debug"]\nurl = "http://127.0.0.1:9999/mcp"\n'
    cfg.write_text(LEGACY_STDIO + "\n" + extra)

    proc = _run(tmp_path, cfg)

    assert proc.returncode == 0, proc.stderr
    servers = _servers(cfg)
    assert servers["serena.debug"] == {"url": "http://127.0.0.1:9999/mcp"}, (
        "an unrelated quoted-name server was deleted:\n" + proc.stdout + proc.stderr
    )
    for name, body in _template_servers().items():
        assert servers[name] == body
