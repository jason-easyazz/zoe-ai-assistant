"""Pin the omnigent entrypoint's cursor-agent selection to verify-before-link.

`modules/omnigent/entrypoint.sh` chooses which mounted cursor-agent to put on
PATH. It must NEVER link a binary that fails to report the declared pin:

  * the preferred host-pin symlink is accepted only if it reports the pin — a
    host symlink pointing at a below-minimum build (e.g. 2026.01.28) must be
    rejected, not linked on `-x` alone (PR #1593, round 2);
  * with no usable host pin it activates ONLY the repo-declared pin (verified),
    never "the newest directory" — the removed newest-wins fallback;
  * when nothing verifies it fails closed NON-FATALLY: no link, a warning, and
    the container keeps running (it must not exit / restart-loop).

The REAL entrypoint is executed. Its post-selection tail (server start, host
attach, the workspace/OpenRouter python patches) is neutralised with no-op
`omnigent` / `curl` / `python3` stubs so nothing but the seam-redirected
selection logic touches disk. The cursor paths are redirected via the script's
own CURSOR_* testability overrides into tmp_path — no /root or /home/zoe writes.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# stdlib + bash/coreutils only (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
_ENTRYPOINT = ROOT / "modules" / "omnigent" / "entrypoint.sh"
PIN = "2026.07.23-e383d2b"
BELOW_MIN = "2026.01.28"  # a real below-minimum build; the incident it must never link


def _write_agent(versions_root: Path, version: str) -> Path:
    """Create ``<versions_root>/<version>/cursor-agent`` reporting ``version``."""
    binp = versions_root / version / "cursor-agent"
    binp.parent.mkdir(parents=True, exist_ok=True)
    binp.write_text(f"#!/usr/bin/env bash\necho '{version}'\n")
    binp.chmod(0o755)
    return binp


def _neutralise_tail(stub_dir: Path) -> None:
    """No-op stubs so the entrypoint's post-selection boot tail does nothing:
    omnigent/curl exit 0; python3 drains its heredoc and exits 0 (so the live
    omnigent workspace patch never runs)."""
    stub_dir.mkdir(parents=True, exist_ok=True)
    for name in ("omnigent", "curl"):
        p = stub_dir / name
        p.write_text("#!/usr/bin/env bash\nexit 0\n")
        p.chmod(0o755)
    py = stub_dir / "python3"
    py.write_text("#!/usr/bin/env bash\ncat >/dev/null 2>&1 || true\nexit 0\n")
    py.chmod(0o755)


def _run(tmp_path: Path, *, versions_root: Path, pin_link: Path | None):
    stub = tmp_path / "stub"
    _neutralise_tail(stub)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    link_dir = tmp_path / "linkdir"
    env = {
        "PATH": f"{stub}:/usr/bin:/bin:/usr/local/bin",
        "HOME": str(home),
        "CURSOR_VERSIONS_ROOT": str(versions_root),
        "CURSOR_LINK_DIR": str(link_dir),
    }
    if pin_link is not None:
        env["CURSOR_PIN_LINK"] = str(pin_link)
    proc = subprocess.run(
        ["bash", str(_ENTRYPOINT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return proc, link_dir


def _linked_version(link_dir: Path) -> str:
    link = link_dir / "cursor-agent"
    if not link.exists():
        return "none"
    return subprocess.run(
        [str(link), "--version"], capture_output=True, text=True
    ).stdout.strip()


def test_host_symlink_to_below_min_build_is_not_linked(tmp_path: Path):
    """THE REGRESSION: a host pin symlink pointing at a below-minimum build must
    be rejected — not linked because the file happens to be executable."""
    versions = tmp_path / "versions"
    _write_agent(versions, BELOW_MIN)
    # host pin symlink -> .../versions/<BELOW_MIN>/cursor-agent
    pin_link = tmp_path / "host_bin" / "cursor-agent"
    pin_link.parent.mkdir(parents=True)
    pin_link.symlink_to(versions / BELOW_MIN / "cursor-agent")
    # NOTE: the declared-pin dir is deliberately ABSENT, so there is no fallback.

    proc, link_dir = _run(tmp_path, versions_root=versions, pin_link=pin_link)

    assert _linked_version(link_dir) != BELOW_MIN, (
        "a below-minimum host-pinned build was linked active:\n"
        + proc.stdout
        + proc.stderr
    )
    assert not (link_dir / "cursor-agent").exists(), (
        "nothing should be linked when no verified pin exists:\n"
        + proc.stdout
        + proc.stderr
    )
    # Fail-closed must be NON-FATAL (server still boots) and clearly warned.
    assert proc.returncode == 0, "fail-closed must not kill the container:\n" + proc.stderr
    assert "no verified cursor-agent pin available" in proc.stderr, proc.stderr


def test_host_symlink_to_pinned_build_is_honoured(tmp_path: Path):
    """The gate must not over-block: a host pin that DOES report the pin is
    accepted and linked."""
    versions = tmp_path / "versions"
    _write_agent(versions, PIN)
    pin_link = tmp_path / "host_bin" / "cursor-agent"
    pin_link.parent.mkdir(parents=True)
    pin_link.symlink_to(versions / PIN / "cursor-agent")

    proc, link_dir = _run(tmp_path, versions_root=versions, pin_link=pin_link)

    assert _linked_version(link_dir) == PIN, proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stderr


def test_no_host_pin_selects_declared_pin_not_newest(tmp_path: Path):
    """Newest-wins is gone: with no host pin and BOTH a bogus newer dir and the
    declared pin present, only the verified declared pin is linked."""
    versions = tmp_path / "versions"
    _write_agent(versions, PIN)
    _write_agent(versions, "2099.01.01")  # lexically/-V newest, but not the pin

    # No host pin symlink at all (point CURSOR_PIN_LINK at a non-existent path).
    proc, link_dir = _run(
        tmp_path, versions_root=versions, pin_link=tmp_path / "absent" / "cursor-agent"
    )

    assert _linked_version(link_dir) == PIN, (
        "expected the declared pin, not the newest directory:\n"
        + proc.stdout
        + proc.stderr
    )
    assert proc.returncode == 0, proc.stderr
