"""Pins zoe-data's open-files limit (A10, infra audit 2026-10-03, D10).

The live zoe-data ran with a soft RLIMIT_NOFILE of 1024 (the user manager's
default). EMFILE on accept() leaves uvicorn alive while the listen queue fills,
which is the runbook §1 accept-queue-hang signature. The fix ships twice: in the
template for fresh hosts, and as a tracked drop-in for installed ones (a template
copy over an installed unit is forbidden). Both must carry the same value.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

UNIT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "systemd"
TEMPLATE = UNIT_DIR / "zoe-data.service"
DROP_IN = UNIT_DIR / "zoe-data.service.d" / "30-nofile.conf"
MIN_SOFT_NOFILE = 65536


def _service_value(path: Path, key: str) -> str | None:
    """Last-wins value of `key` in the [Service] section only (systemd ignores
    resource limits placed in [Unit]/[Install])."""
    value, section = None, None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "Service" and line.partition("=")[0].strip() == key:
            value = line.partition("=")[2].strip()
    return value


def _soft_nofile(path: Path) -> int | None:
    value = _service_value(path, "LimitNOFILE")
    if value is None:
        return None
    soft = value.split(":", 1)[0]
    return 1 << 62 if soft == "infinity" else int(soft)


@pytest.mark.parametrize("path", [TEMPLATE, DROP_IN], ids=lambda p: p.name)
def test_zoe_data_soft_nofile_is_raised(path):
    soft = _soft_nofile(path)
    assert soft is not None and soft >= MIN_SOFT_NOFILE, (
        f"{path.name} must set LimitNOFILE >= {MIN_SOFT_NOFILE} in [Service]; the "
        "user-manager default soft limit is 1024"
    )


def test_template_and_drop_in_agree():
    assert _service_value(TEMPLATE, "LimitNOFILE") == _service_value(DROP_IN, "LimitNOFILE")


def test_limit_outside_service_section_does_not_count(tmp_path):
    """Negative control: the same line under [Unit] is ignored by systemd."""
    probe = tmp_path / "probe.conf"
    probe.write_text("[Unit]\nLimitNOFILE=65536\n[Service]\nRestart=always\n")
    assert _soft_nofile(probe) is None
    probe.write_text("[Service]\nLimitNOFILE=1024:1048576\n")
    assert _soft_nofile(probe) < MIN_SOFT_NOFILE
