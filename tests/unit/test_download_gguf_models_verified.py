"""download_gguf_models.sh: verified model download (pinned sha256 + size, space check, resume, .ready marker).

Runs the real script with a FAKE curl and FAKE df on PATH and a tiny fake models.lock in a temp dir; no network, no
real model. Pinned behaviours: a good file passes and gets .ready; a corrupt file is refused with one plain sentence,
no .ready and the old good file survives; a .part is resumed (curl -C -) and only renamed after the hash matches; too
little space is refused before curl is ever called; an already-verified file is not fetched again; an UNSET pin warns
and still downloads.

Negative controls run (break the code, see it fail, restore): (a) skipping the hash comparison in verified_download
made test_corrupt_download_is_refused fail; (b) removing the free-space check made test_too_little_space_refused_before_download
fail; (c) dropping the .ready short-circuit/hash match made test_already_verified_is_not_fetched_again fail.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/setup/download_gguf_models.sh"
BRAIN = "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
MTP = "mtp-gemma-4-E4B-it.gguf"
BRAIN_BYTES = b"brain-bytes-" * 100
MTP_BYTES = b"drafter-" * 50

FAKE_CURL = r"""#!/usr/bin/env bash
out=""; resume=0; url=""
while [ $# -gt 0 ]; do
  case "$1" in -o) out="$2"; shift 2;; -C) resume=1; shift 2;; -*) shift;; *) url="$1"; shift;; esac
done
echo "curl resume=$resume url=$url" >> "$FAKE_LOG"
src="$FAKE_SERVER/$(basename "$url")"
[ -f "$src" ] || exit 22
if [ "$resume" = 1 ] && [ -f "$out" ]; then tail -c +$(( $(stat -c %s "$out") + 1 )) "$src" >> "$out"; else cp "$src" "$out"; fi
"""
FAKE_DF = '#!/usr/bin/env bash\nprintf "Filesystem 1024-blocks Used Available Capacity Mounted\\nfake 1 1 %s 1%% /\\n" "${FAKE_FREE_KB}"\n'


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture()
def env(tmp_path):
    bindir, server, models = tmp_path / "bin", tmp_path / "server", tmp_path / "models"
    for d in (bindir, server):
        d.mkdir()
    (server / BRAIN).write_bytes(BRAIN_BYTES)
    (server / MTP).write_bytes(MTP_BYTES)
    for n, body in (("curl", FAKE_CURL), ("df", FAKE_DF)):
        (bindir / n).write_text(body)
        (bindir / n).chmod(0o755)
    lock = tmp_path / "models.lock"

    def write_lock(brain_sha=None, brain_bytes=len(BRAIN_BYTES)):
        lock.write_text(json.dumps({"models": [
            {"role": "brain", "path": f"models/gemma4-e4b-qat/{BRAIN}", "sha256": brain_sha or _sha(BRAIN_BYTES), "bytes": brain_bytes},
            {"role": "mtp", "path": f"models/gemma4-e4b-qat/{MTP}", "sha256": _sha(MTP_BYTES), "bytes": len(MTP_BYTES)},
        ]}, indent=2))
    write_lock()
    e = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", FAKE_SERVER=str(server), FAKE_LOG=str(tmp_path / "log"),
             FAKE_FREE_KB="100000000", ZOE_MODELS_LOCK=str(lock), ZOE_MODEL_BASE_URL="https://example.invalid/r", HOME=str(tmp_path))
    (tmp_path / "log").write_text("")

    def run():
        return subprocess.run(["bash", str(SCRIPT), "--models-dir", str(models)], env=e, capture_output=True, text=True, timeout=60)
    return type("E", (), dict(run=staticmethod(run), models=models, log=tmp_path / "log", env=e, write_lock=staticmethod(write_lock), server=server))


def calls(env):
    return [line for line in env.log.read_text().splitlines() if line]


def test_good_download_passes_and_writes_ready_last(env):
    r = env.run()
    assert r.returncode == 0, r.stderr
    assert (env.models / BRAIN).read_bytes() == BRAIN_BYTES
    assert not (env.models / f"{BRAIN}.part").exists()
    assert (env.models / f"{BRAIN}.ready").read_text().split()[0] == _sha(BRAIN_BYTES)
    assert (env.models / f"{MTP}.ready").exists()


def test_corrupt_download_is_refused(env):
    env.write_lock(brain_sha="0" * 64)
    r = env.run()
    assert r.returncode != 0
    assert "damaged or is not the expected file" in r.stderr
    assert not (env.models / f"{BRAIN}.ready").exists()
    assert not (env.models / BRAIN).exists()
    assert "Traceback" not in r.stderr


def test_corrupt_download_keeps_existing_good_file(env):
    env.models.mkdir()
    (env.models / BRAIN).write_bytes(b"old-good-file")
    env.write_lock(brain_sha="0" * 64)  # nothing the server has can match
    r = env.run()
    assert r.returncode != 0
    assert (env.models / BRAIN).read_bytes() == b"old-good-file"
    assert not (env.models / f"{BRAIN}.ready").exists()


def test_partial_is_resumed(env):
    env.models.mkdir()
    (env.models / f"{BRAIN}.part").write_bytes(BRAIN_BYTES[:300])
    r = env.run()
    assert r.returncode == 0, r.stderr
    assert (env.models / BRAIN).read_bytes() == BRAIN_BYTES
    assert any("resume=1" in c and BRAIN in c for c in calls(env))


def test_too_little_space_refused_before_download(env):
    env.env["FAKE_FREE_KB"] = "1"
    r = env.run()
    assert r.returncode != 0
    assert "Not enough disk space" in r.stderr and "needs" in r.stderr and "free" in r.stderr
    assert calls(env) == []
    assert not (env.models / f"{BRAIN}.part").exists()


def test_already_verified_is_not_fetched_again(env):
    assert env.run().returncode == 0
    n = len(calls(env))
    r = env.run()
    assert r.returncode == 0
    assert len(calls(env)) == n
    assert "already verified" in r.stdout


def test_existing_good_file_without_ready_is_adopted_not_refetched(env):
    env.models.mkdir()
    (env.models / BRAIN).write_bytes(BRAIN_BYTES)
    (env.models / MTP).write_bytes(MTP_BYTES)
    assert env.run().returncode == 0
    assert calls(env) == []
    assert (env.models / f"{BRAIN}.ready").exists()


def test_unset_pin_warns_and_still_downloads(env):
    lock = json.loads(Path(env.env["ZOE_MODELS_LOCK"]).read_text())
    lock["models"][0]["sha256"] = "UNSET"
    Path(env.env["ZOE_MODELS_LOCK"]).write_text(json.dumps(lock))
    r = env.run()
    assert r.returncode == 0, r.stderr
    assert "no pinned checksum yet" in r.stderr
    assert (env.models / BRAIN).exists() and not (env.models / f"{BRAIN}.ready").exists()
