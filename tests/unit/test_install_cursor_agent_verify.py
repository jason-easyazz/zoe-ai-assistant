"""Pin the cursor-agent installer's verify-before-link contract.

`scripts/setup/install_cursor_agent.sh` must NEVER point the active symlink
(``~/.local/bin/cursor-agent``) at a binary that has not been verified to report
the declared pin. The old "already present" shortcut trusted an existing final
dir on ``-x`` alone and verified only AFTER relinking, so an executable-but-
wrong-version dir could become the active symlink (PR #1593, round 2).

Real filesystem in ``tmp_path``; a stubbed ``curl`` emits a tarball carrying a
GOOD (pin-reporting) cursor-agent so the recovery path runs end to end offline.
The script itself is exercised unmodified — no mocks of its logic, no network.
"""
from __future__ import annotations

import gzip
import io
import subprocess
import tarfile
from pathlib import Path

import pytest

# stdlib + bash/coreutils only (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "setup" / "install_cursor_agent.sh"
PIN = "2026.07.23-e383d2b"
WRONG = "2026.01.28"  # a real below-minimum build; the incident in the header comment


def _agent_stub(version: str) -> str:
    """A cursor-agent stand-in: prints its version for ``--version``."""
    return f"#!/usr/bin/env bash\necho '{version}'\n"


def _write_agent(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_agent_stub(version))
    path.chmod(0o755)


def _make_good_tarball(path: Path) -> None:
    """A ``.tar.gz`` whose single top dir (stripped by the installer) holds a
    cursor-agent that reports the PIN."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        data = _agent_stub(PIN).encode()
        info = tarfile.TarInfo("agent-cli-package/cursor-agent")
        info.size = len(data)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(data))
    path.write_bytes(gzip.compress(raw.getvalue()))


def _stub_dir_with_curl(tmp_path: Path, tarball: Path | None) -> Path:
    """A PATH dir holding a fake ``curl``. If ``tarball`` is None the stub FAILS
    when called (proves a code path never downloads)."""
    d = tmp_path / "stub"
    d.mkdir()
    curl = d / "curl"
    if tarball is None:
        curl.write_text("#!/usr/bin/env bash\necho 'curl must not run' >&2\nexit 1\n")
    else:
        curl.write_text(f'#!/usr/bin/env bash\ncat "{tarball}"\n')
    curl.chmod(0o755)
    return d


def _run(home: Path, stub_bin: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_SCRIPT)],
        capture_output=True,
        text=True,
        env={"PATH": f"{stub_bin}:/usr/bin:/bin:/usr/local/bin", "HOME": str(home)},
    )


def _linked_version(home: Path) -> str:
    link = home / ".local" / "bin" / "cursor-agent"
    if not link.exists():
        return "none"
    return subprocess.run(
        [str(link), "--version"], capture_output=True, text=True
    ).stdout.strip()


def test_executable_but_wrong_version_target_is_not_linked(tmp_path: Path):
    """THE REGRESSION: an existing final dir that is executable but reports the
    WRONG version must not become the active symlink — it is reinstalled and
    re-verified first. Fails on the pre-fix script, which relinks it as-is."""
    home = tmp_path / "home"
    _write_agent(
        home / ".local/share/cursor-agent/versions" / PIN / "cursor-agent", WRONG
    )
    tarball = tmp_path / "pkg.tar.gz"
    _make_good_tarball(tarball)

    proc = _run(home, _stub_dir_with_curl(tmp_path, tarball))

    linked = _linked_version(home)
    assert linked != WRONG, (
        "the active symlink resolves to the WRONG-version binary — the "
        "'already present' shortcut linked it without verifying:\n"
        + proc.stdout
        + proc.stderr
    )
    # Recovery must land on the verified pin and succeed.
    assert linked == PIN and proc.returncode == 0, (
        f"expected recovery to the verified pin, got '{linked}' rc={proc.returncode}:\n"
        + proc.stdout
        + proc.stderr
    )


def test_correct_version_target_is_reused_without_download(tmp_path: Path):
    """The verify gate must not over-fire: a correct existing install is reused
    (no curl) and stays linked — otherwise 'always reinstall' would pass the test
    above while breaking the offline/idempotent path."""
    home = tmp_path / "home"
    _write_agent(
        home / ".local/share/cursor-agent/versions" / PIN / "cursor-agent", PIN
    )

    # curl stub FAILS if called — the correct-existing path must never download.
    proc = _run(home, _stub_dir_with_curl(tmp_path, tarball=None))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _linked_version(home) == PIN, proc.stdout + proc.stderr
    assert "already present and verified" in (proc.stdout + proc.stderr)
