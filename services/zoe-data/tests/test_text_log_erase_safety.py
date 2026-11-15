"""Synthetic interleavings and filesystem boundaries for the extra erase reach."""
from __future__ import annotations

import ast
import json
import logging
import os
import re
from pathlib import Path

import pytest

import local_text_logs as ltl

pytestmark = pytest.mark.ci_safe
SVC = Path(__file__).resolve().parents[1]


def _voice_helpers():
    """Execute the committed writer helpers without importing models/the router."""
    tree = ast.parse((SVC / "routers/voice_tts.py").read_text())
    names = {"_rotate_voice_stt_log", "_log_voice_stt_sample", "_voice_stt_log_path"}
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    from datetime import datetime, timezone
    from types import SimpleNamespace

    scope = dict(Path=Path, logger=logging.getLogger(__name__), json=json,
                 datetime=datetime, timezone=timezone, _env_int=lambda *_: 1,
                 env_str=lambda name, default="": os.environ.get(name, default), moonshine_arch=lambda: "synthetic",
                 _stt_backend_var=SimpleNamespace(get=lambda: "synthetic"),
                 traffic_class=SimpleNamespace(current=lambda: "ci"))
    exec(compile(tree, "voice_tts.py", "exec"), scope)
    return scope


def test_stt_rotation_inside_inode_check_replace_window(monkeypatch, tmp_path):
    path = tmp_path / "voice.jsonl"
    monkeypatch.setenv("ZOE_VOICE_STT_LOG", str(path))
    monkeypatch.setenv("ZOE_FORGET_REACH_EXTRA", "on")
    path.write_text(json.dumps({"transcript": "synthetic sentinel"}) + "\n")
    rotate = _voice_helpers()["_rotate_voice_stt_log"]
    replace = os.replace
    attempts = []

    def interleave(src, dst, *args, **kwargs):
        if Path(dst) == path and str(src).endswith(".tmp"):
            # rewrite has checked the inode and is about to replace it. Call
            # the ACTUAL rotation helper here; old code leaks into .1.
            attempts.append(True)
            rotate(path)
        return replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", interleave)
    assert ltl.erase_entity(re.compile("synthetic sentinel")) == 1
    assert attempts == [True]
    assert all("synthetic sentinel" not in p.read_text() for p in tmp_path.iterdir() if p.is_file())


def test_stt_writer_rotates_and_appends_under_erase_lock(monkeypatch, tmp_path):
    path = tmp_path / "voice.jsonl"
    monkeypatch.setenv("ZOE_VOICE_STT_LOG", str(path))
    path.write_text('{"transcript": "old synthetic"}\n')
    write = _voice_helpers()["_log_voice_stt_sample"]
    args = dict(route="synthetic", panel_id="synthetic", audio_bytes=4, suffix="wav", transcript="new synthetic")
    with ltl.locked(ltl.VOICE_STT):
        write(**args)
        assert not path.with_suffix(".jsonl.1").exists()
        assert "new synthetic" not in path.read_text()
    write(**args)
    assert "new synthetic" in path.read_text()
    assert "old synthetic" in path.with_suffix(".jsonl.1").read_text()


@pytest.mark.parametrize("raw", ["captures", "../captures", "/", "//", "/tmp/../captures"])
def test_capture_refuses_unanchored_or_root_paths(monkeypatch, raw):
    monkeypatch.setenv("ZOE_TELEGRAM_STT_CAPTURE_DIR", raw)
    with pytest.raises(ValueError):
        ltl.telegram_capture_dir()


def test_stt_rotation_and_append_obey_file_lock(monkeypatch, tmp_path):
    import fcntl

    path = tmp_path / "voice.jsonl"
    monkeypatch.setenv("ZOE_VOICE_STT_LOG", str(path))
    path.write_text('{"transcript": "synthetic original"}\n')
    helpers = _voice_helpers()
    with open(ltl.lock_path(ltl.VOICE_STT), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        helpers["_rotate_voice_stt_log"](path)
        helpers["_log_voice_stt_sample"](route="ci", panel_id="synthetic", audio_bytes=4, suffix="wav",
                                           transcript="synthetic new")
        assert path.read_text() == '{"transcript": "synthetic original"}\n'
        assert not path.with_name(path.name + ".1").exists()


@pytest.mark.parametrize("link_at", ["root", "ancestor", "transcript", "audio"])
def test_capture_never_follows_symlinks(monkeypatch, tmp_path, link_at):
    outside = tmp_path / "outside"
    outside.mkdir()
    for suffix in (".txt", ".wav"):
        (outside / ("tg-canary" + suffix)).write_text("synthetic canary")
    root = tmp_path / "capture"
    if link_at == "root":
        root.symlink_to(outside, target_is_directory=True)
    elif link_at == "ancestor":
        (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
        root = tmp_path / "alias" / "outside"
    else:
        root.mkdir()
        for suffix in (".txt", ".wav"):
            dest = root / ("tg-canary" + suffix)
            if suffix == (".txt" if link_at == "transcript" else ".wav"):
                dest.symlink_to(outside / dest.name)
            else:
                dest.write_text("synthetic canary")
    monkeypatch.setenv("ZOE_TELEGRAM_STT_CAPTURE_DIR", str(root))
    with pytest.raises(OSError):
        ltl.erase_capture(everything=True)
    assert all(p.read_text() == "synthetic canary" for p in outside.iterdir())


def test_capture_directory_replacement_cannot_redirect_delete(monkeypatch, tmp_path):
    root, moved, outside = (tmp_path / name for name in ("capture", "moved", "outside"))
    root.mkdir()
    outside.mkdir()
    for directory in (root, outside):
        (directory / "tg-canary.txt").write_text("synthetic canary")
    monkeypatch.setenv("ZOE_TELEGRAM_STT_CAPTURE_DIR", str(root))
    listdir = os.listdir
    glob = Path.glob
    swaps = []

    def swap_root():
        if not swaps:
            root.rename(moved)
            root.symlink_to(outside, target_is_directory=True)
            swaps.append(True)

    def swap(directory):
        names = listdir(directory)
        swap_root()
        return names

    def old_path_enumeration(directory, pattern):
        names = list(glob(directory, pattern))
        swap_root()
        return iter(names)

    monkeypatch.setattr(os, "listdir", swap)
    monkeypatch.setattr(Path, "glob", old_path_enumeration)
    assert ltl.erase_capture(everything=True) == 1
    assert swaps == [True]
    assert (outside / "tg-canary.txt").read_text() == "synthetic canary"
    assert not (moved / "tg-canary.txt").exists()


def test_standalone_sweep_loads_extra_settings(monkeypatch, tmp_path):
    import runpy

    mod = runpy.run_path(str(SVC.parents[1] / "scripts/maintenance/rotate_service_logs.py"))
    settings = {"ZOE_FORGET_REACH_EXTRA": "on", "ZOE_VOICE_STT_LOG": str(tmp_path / "voice.jsonl"),
                "ZOE_TELEGRAM_STT_CAPTURE_DIR": str(tmp_path / "capture")}
    env_file = tmp_path / "synthetic.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in settings.items()))
    apply = mod["apply_env_files"]
    monkeypatch.setitem(apply.__globals__, "ENV_FILES", (env_file,))
    for name in settings:
        monkeypatch.delenv(name, raising=False)
    apply()
    assert all(os.environ[name] == value for name, value in settings.items())
    assert ltl.extra_reach_on()
    assert ltl.log_path(ltl.VOICE_STT) == Path(settings["ZOE_VOICE_STT_LOG"])
    assert ltl.telegram_capture_dir() == Path(settings["ZOE_TELEGRAM_STT_CAPTURE_DIR"])
    monkeypatch.setenv("ZOE_TEXT_LOG_RETENTION_DAYS", "0")
    log = Path(settings["ZOE_VOICE_STT_LOG"])
    log.write_text('{"ts": "2000-01-01T00:00:00Z", "transcript": "synthetic old"}\n')
    capture = Path(settings["ZOE_TELEGRAM_STT_CAPTURE_DIR"])
    capture.mkdir()
    (capture / "tg-old.txt").write_text("synthetic old")
    (capture / "tg-old.wav").write_bytes(b"RIFF")
    result = ltl.sweep()
    assert result["voice_stt"] == 1 and result["telegram_capture"] == 2
    assert log.read_text() == "" and list(capture.iterdir()) == []
    monkeypatch.setenv("ZOE_FORGET_REACH_EXTRA", "off")
    apply()
    assert not ltl.extra_reach_on()  # process environment still wins
