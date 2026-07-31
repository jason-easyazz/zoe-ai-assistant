"""Pin the cursor-agent installer's verify-before-link contract.

`scripts/setup/install_cursor_agent.sh` must NEVER point the active symlink
(``~/.local/bin/cursor-agent``) at a binary that has not been verified to report
the declared pin. The old "already present" shortcut trusted an existing final
dir on ``-x`` alone and verified only AFTER relinking, so an executable-but-
wrong-version dir could become the active symlink (PR #1593, round 2).

Real filesystem in ``tmp_path``; a stubbed ``curl`` emits a tarball carrying a
GOOD (pin-reporting) cursor-agent so the recovery path runs end to end offline.
No mocks of the script's logic, no network.

The installer verifies the fetched artifact against a HARDCODED per-arch SHA-256 and
has NO env/argv override for it — deliberately, so nothing an inherited environment
can reach may swap the pinned digest (a spoofed ``--version`` would otherwise restore
a fake authenticity). An offline fixture tarball therefore cannot match the real
pinned digest, so the download-path test runs a TEMP COPY of the script whose per-arch
digest constants are string-replaced with the fixture's own sha256 (see
``_script_with_fixture_digest``). The production script on disk is never modified and
exposes no seam; the negative-control test runs the UNMODIFIED script to prove the
pinned check still aborts a non-matching artifact before extraction.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import re
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
    when called (proves a code path never downloads).

    The real installer downloads to a file with ``curl -fSL <url> -o <path>``, so
    the stub must honour ``-o`` and write the tarball THERE — not to stdout. A
    stdout cat would (a) never populate the file the script then hashes and
    extracts, and (b) spray gzip bytes into the captured stream."""
    d = tmp_path / "stub"
    d.mkdir()
    curl = d / "curl"
    if tarball is None:
        curl.write_text("#!/usr/bin/env bash\necho 'curl must not run' >&2\nexit 1\n")
    else:
        curl.write_text(
            "#!/usr/bin/env bash\n"
            "out=\"\"\n"
            'while [ "$#" -gt 0 ]; do\n'
            '  case "$1" in\n'
            '    -o) out="$2"; shift 2 ;;\n'
            '    *) shift ;;\n'
            "  esac\n"
            "done\n"
            f'src="{tarball}"\n'
            'if [ -n "$out" ]; then cp "$src" "$out"; else cat "$src"; fi\n'
        )
    curl.chmod(0o755)
    return d


def _script_with_fixture_digest(dst_dir: Path, fixture_sha: str) -> Path:
    """A temp COPY of the installer whose hardcoded per-arch SHA-256 constants are
    string-replaced with ``fixture_sha`` — the ONLY way to make the pinned digest
    check pass offline without a production override. Both arch constants are patched
    so the copy verifies whichever arch the test host resolves via ``uname -m``. The
    production script on disk is untouched and exposes no env/argv seam."""
    text = _SCRIPT.read_text()
    patched, n = re.subn(
        r'(CURSOR_SHA256_(?:arm64|x64)=")[0-9a-fA-F]{64}(")',
        rf"\g<1>{fixture_sha}\g<2>",
        text,
    )
    assert n == 2, f"expected to patch 2 pinned digest constants, patched {n}"
    copy = dst_dir / "install_cursor_agent.sh"
    copy.write_text(patched)
    copy.chmod(0o755)
    return copy


def _run(
    home: Path, stub_bin: Path, script: Path = _SCRIPT
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        # Belt-and-suspenders: the -o-honouring curl stub already keeps binary out
        # of the stream, but decode defensively so any stray bytes surface as a
        # readable assertion instead of an UnicodeDecodeError from the harness.
        errors="replace",
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
    fixture_sha = hashlib.sha256(tarball.read_bytes()).hexdigest()
    script = _script_with_fixture_digest(tmp_path, fixture_sha)

    proc = _run(home, _stub_dir_with_curl(tmp_path, tarball), script=script)

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


def test_wrong_digest_aborts_before_extract(tmp_path: Path):
    """SECURITY: verify-before-extract fires on the UNMODIFIED production script. A
    fetched artifact whose sha256 does not match the hardcoded per-arch pin must be
    rejected BEFORE extraction — nothing extracted, no version dir published, symlink
    never written — even though the fixture binary would report the pin on --version
    (a spoof the digest check exists to stop). Run against ``_SCRIPT`` directly with
    NO digest patch, proving the pin cannot be bypassed via env/argv/inherited state."""
    home = tmp_path / "home"
    tarball = tmp_path / "pkg.tar.gz"
    _make_good_tarball(tarball)  # good binary, but its sha != the real pinned digest

    proc = _run(home, _stub_dir_with_curl(tmp_path, tarball))  # unmodified script

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "sha256 mismatch" in (proc.stdout + proc.stderr), proc.stdout + proc.stderr
    # Zero version dirs published (the staging temp dir is trap-removed on abort) and
    # no active symlink — the artifact was never extracted or linked.
    versions = home / ".local/share/cursor-agent/versions"
    published = (
        [p for p in versions.iterdir() if not p.name.startswith(".")]
        if versions.exists()
        else []
    )
    assert published == [], f"artifact was extracted despite digest mismatch: {published}"
    assert not (home / ".local/bin/cursor-agent").exists(), proc.stdout + proc.stderr


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
