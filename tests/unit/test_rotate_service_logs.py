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
    seg = tmp_path / "zoe-data.stderr.1.log.gz"
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
    names = sorted(p.name for p in tmp_path.glob("x.*.log.gz"))
    assert names == ["x.1.log.gz", "x.2.log.gz", "x.3.log.gz"]  # bounded: keep=3
    assert gzip.decompress((tmp_path / "x.1.log.gz").read_bytes())[:5] == b"gen4\n"
    assert gzip.decompress((tmp_path / "x.3.log.gz").read_bytes())[:5] == b"gen2\n"  # gen1 fell off


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
        if str(dst).endswith(".1.log.gz"):  # the copy is published; the writer sneaks one in
            with open(log, "ab") as f:
                f.write(b"late-line\n")

    monkeypatch.setattr(mod.os, "replace", replace_then_append)
    assert mod.rotate_file(log, max_bytes=MB, keep=2) == "rotated"
    assert log.read_bytes() == b"late-line\n"
    assert b"late-line" not in gzip.decompress((tmp_path / "svc.1.log.gz").read_bytes())


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


def test_segment_names_cannot_match_the_old_host_rotators_prune_glob(tmp_path):
    """~/bin/zoe-logs-rotate.sh runs `ls -1t zoe-data.stdout.log.*.gz | tail -n +4 | xargs rm`.
    Our segments must be invisible to that glob or the two rotators would eat each other."""
    import fnmatch

    log = tmp_path / "zoe-data.stdout.log"
    for gen in (b"a\n", b"b\n", b"c\n", b"d\n", b"e\n"):
        _fill(log, 2 * MB, gen)
        mod.rotate_file(log, max_bytes=MB, keep=4)
    segs = sorted(p.name for p in tmp_path.glob("*.gz"))
    assert segs == [f"zoe-data.stdout.{n}.log.gz" for n in (1, 2, 3, 4)]
    assert not [n for n in segs if fnmatch.fnmatch(n, "zoe-data.stdout.log.*.gz")]


def _patch_gzip_enospc(monkeypatch):
    import errno

    real = mod.gzip.GzipFile.write
    state = {"n": 0}

    def flaky(self, data):
        state["n"] += 1
        if state["n"] >= 2:  # let the first chunk through: a MID-copy failure
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, data)

    monkeypatch.setattr(mod.gzip.GzipFile, "write", flaky)


def test_failed_gzip_leaves_archive_and_live_file_untouched(tmp_path, monkeypatch):
    """The archive must not erode one segment per failed tick, and the live file
    must not be truncated when no verified archive exists (P1, PR #1849 review)."""
    log = tmp_path / "svc.log"
    for gen in (b"gen1\n", b"gen2\n", b"gen3\n"):
        _fill(log, 2 * MB, gen)
        assert mod.rotate_file(log, max_bytes=MB, keep=3) == "rotated"
    before = {p.name: p.read_bytes() for p in tmp_path.glob("svc.*.log.gz")}
    assert len(before) == 3
    _fill(log, 3 * MB, b"live\n")
    live = log.read_bytes()

    _patch_gzip_enospc(monkeypatch)
    for _ in range(4):  # four failed ticks, as in the reproduction
        with pytest.raises(OSError):
            mod.rotate_file(log, max_bytes=MB, keep=3)

    assert {p.name: p.read_bytes() for p in tmp_path.glob("svc.*.log.gz")} == before
    assert log.read_bytes() == live
    assert not list(tmp_path.glob("*.tmp"))


def test_main_reports_a_failed_rotation_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    log = tmp_path / "zoe-data.stderr.log"
    _fill(log, 3 * MB)
    _patch_gzip_enospc(monkeypatch)
    rc = mod.main(["--dir", str(tmp_path), "--max-mb", "1", "--file", "zoe-data.stderr.log"])
    assert rc == 1
    assert "FAILED" in capsys.readouterr().err
    assert log.stat().st_size == 3 * MB
