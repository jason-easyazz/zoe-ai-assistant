"""Pins scripts/maintenance/rotate_service_logs.py (copytruncate rotation for the
systemd ``append:`` logs that nothing else rotates).

Runs entirely against tmp_path; never touches ~/.zoe-logs.
"""

from __future__ import annotations

import gzip
import importlib.util
import os
import stat
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "maintenance" / "rotate_service_logs.py"


def _load():
    spec = importlib.util.spec_from_file_location("rotate_service_logs", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load()

MB = 1024 * 1024


def _fill(path: Path, nbytes: int, tag: bytes = b"line\n") -> None:
    path.write_bytes((tag * (nbytes // len(tag) + 1))[:nbytes])


def test_rotates_a_big_file_and_keeps_the_content(tmp_path):
    log = tmp_path / "zoe-data.stderr.log"
    _fill(log, 3 * MB, b"abc-original\n")
    original = log.read_bytes()

    assert mod.rotate_file(log, max_bytes=2 * MB, keep=3) == "rotated"

    assert log.stat().st_size == 0  # truncated in place, same inode
    seg = tmp_path / "zoe-data.stderr.log.1.gz"
    assert gzip.decompress(seg.read_bytes()) == original
    assert stat.S_IMODE(seg.stat().st_mode) == 0o640  # logs hold household conversation


def test_small_and_missing_files_are_left_alone(tmp_path):
    log = tmp_path / "a.log"
    _fill(log, 1000)
    assert mod.rotate_file(log, max_bytes=MB, keep=3) == "small"
    assert log.stat().st_size == 1000
    assert not list(tmp_path.glob("*.gz"))
    assert mod.rotate_file(tmp_path / "nope.log", max_bytes=MB, keep=3) == "missing"


def test_segments_shift_and_the_oldest_falls_off(tmp_path):
    log = tmp_path / "x.log"
    for gen in (b"gen1\n", b"gen2\n", b"gen3\n", b"gen4\n"):
        _fill(log, 2 * MB, gen)
        assert mod.rotate_file(log, max_bytes=MB, keep=3) == "rotated"
    names = sorted(p.name for p in tmp_path.glob("x.log.*.gz"))
    assert names == ["x.log.1.gz", "x.log.2.gz", "x.log.3.gz"]  # bounded: keep=3
    assert gzip.decompress((tmp_path / "x.log.1.gz").read_bytes())[:5] == b"gen4\n"
    assert gzip.decompress((tmp_path / "x.log.3.gz").read_bytes())[:5] == b"gen2\n"  # gen1 fell off


def test_a_writer_holding_an_append_descriptor_keeps_working_and_file_is_not_sparse(tmp_path):
    """systemd keeps ONE O_APPEND descriptor for the unit's life. Rotation must not
    strand it on a renamed inode, and its next write must land at the new EOF."""
    log = tmp_path / "svc.log"
    _fill(log, 2 * MB)
    fd = os.open(log, os.O_WRONLY | os.O_APPEND)
    try:
        assert mod.rotate_file(log, max_bytes=MB, keep=2) == "rotated"
        os.write(fd, b"after-rotation\n")
    finally:
        os.close(fd)
    assert log.read_bytes() == b"after-rotation\n"  # not 2 MB of NULs + the line


def test_bytes_appended_during_the_copy_are_carried_over(tmp_path, monkeypatch):
    log = tmp_path / "svc.log"
    _fill(log, 2 * MB, b"old\n")

    real_replace = os.replace

    def replace_then_append(src, dst):
        real_replace(src, dst)
        if str(dst).endswith(".1.gz"):  # the copy is published; the writer sneaks one in
            with open(log, "ab") as f:
                f.write(b"late-line\n")

    monkeypatch.setattr(mod.os, "replace", replace_then_append)
    assert mod.rotate_file(log, max_bytes=MB, keep=2) == "rotated"
    assert log.read_bytes() == b"late-line\n"
    assert b"late-line" not in gzip.decompress((tmp_path / "svc.log.1.gz").read_bytes())


def test_symlinks_and_the_self_rotating_app_log_are_never_touched(tmp_path):
    real = tmp_path / "real.log"
    _fill(real, 2 * MB)
    link = tmp_path / "link.log"
    link.symlink_to(real)
    assert mod.rotate_file(link, max_bytes=MB, keep=2) == "skipped"
    assert real.stat().st_size == 2 * MB

    app = tmp_path / "zoe-data.app.log"  # RotatingFileHandler owns this one
    _fill(app, 2 * MB)
    assert mod.rotate_file(app, max_bytes=MB, keep=2) == "skipped"
    assert app.stat().st_size == 2 * MB


def test_dry_run_changes_nothing(tmp_path):
    log = tmp_path / "d.log"
    _fill(log, 2 * MB)
    assert mod.rotate_file(log, max_bytes=MB, keep=2, dry_run=True) == "dry-run"
    assert log.stat().st_size == 2 * MB
    assert not list(tmp_path.glob("*.gz"))


def test_main_rotates_only_listed_files_and_reports(tmp_path, capsys):
    big = tmp_path / "zoe-data.stderr.log"
    other = tmp_path / "unlisted.log"
    _fill(big, 2 * MB)
    _fill(other, 2 * MB)
    rc = mod.main(["--dir", str(tmp_path), "--max-mb", "1", "--keep", "2", "--file", "zoe-data.stderr.log"])
    assert rc == 0
    assert big.stat().st_size == 0
    assert other.stat().st_size == 2 * MB
    assert "zoe-data.stderr.log: rotated" in capsys.readouterr().out


def test_default_targets_cover_the_streams_that_grew_and_exclude_the_app_log():
    assert "zoe-data.stderr.log" in mod.DEFAULT_FILES
    assert "zoe-data.stdout.log" in mod.DEFAULT_FILES
    assert "zoe-data.app.log" not in mod.DEFAULT_FILES


def test_timer_and_service_templates_exist_and_point_at_the_script():
    svc = (ROOT / "scripts/setup/systemd/zoe-log-rotate.service").read_text()
    tmr = (ROOT / "scripts/setup/systemd/zoe-log-rotate.timer").read_text()
    assert "scripts/maintenance/rotate_service_logs.py" in svc
    assert "Type=oneshot" in svc
    assert "OnUnitActiveSec=" in tmr and "WantedBy=timers.target" in tmr
