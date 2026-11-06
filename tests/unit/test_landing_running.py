"""landing_running (scripts/landing/landing_lib.sh) decides "is a real lander alive" from /proc argv, never from `pgrep -f`.

2026-11-05: 37 agent waiter shells (`bash -c '... until ! pgrep -f land_voice_pr.sh ...'`) matched the unanchored pgrep, so autoland
believed a landing was always running and stopped landing. Unique script names keep these tests blind to the live loops on the box.
"""
import os
import re
import secrets
import shutil
import subprocess
import time

import pytest

from tests.unit.landing_fakes import LANDING

pytestmark = pytest.mark.ci_safe

HAVE = shutil.which("bash") and shutil.which("pgrep") and os.path.isdir("/proc/self")
needs = pytest.mark.skipif(not HAVE, reason="needs bash, pgrep and /proc")


def running(*names):
    r = subprocess.run(["bash", "-c", f'. "{LANDING}/landing_lib.sh"; landing_running "$@"', "x", *names], capture_output=True)
    return r.returncode == 0


@pytest.fixture()
def procs(tmp_path):
    started = []

    def spawn(argv):
        p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=tmp_path)
        started.append(p)
        time.sleep(0.3)
        return p

    yield spawn
    for p in started:
        p.terminate()
        p.wait()


def script(tmp_path, name):
    f = tmp_path / name
    f.write_text("#!/bin/bash\nsleep 30\n")
    f.chmod(0o755)
    return f


@needs
def test_waiter_mentioning_the_name_does_not_count_but_the_old_pattern_would(tmp_path, procs):
    name = f"zt_{secrets.token_hex(4)}.sh"
    waiter = procs(["bash", "-c", f"until ! pgrep -f 'scripts/landing/{name}' >/dev/null; do sleep 1; done; sleep 30"])
    assert not running(name), "a bash -c waiter that merely mentions the script must not count as a lander"
    # negative control: the unanchored pgrep autoland used to run matches that very waiter, so the fix is what changed the answer
    old = subprocess.run(["pgrep", "-f", name.replace(".", r"\.")], capture_output=True, text=True).stdout.split()
    assert str(waiter.pid) in old


@needs
def test_real_script_processes_count(tmp_path, procs):
    name = f"zt_{secrets.token_hex(4)}.sh"
    f = script(tmp_path, name)
    assert not running(name)
    procs(["bash", str(f), "123"])
    assert running(name)
    assert running("other.sh", name), "any one of several names matching is enough"
    assert not running("other.sh")


@needs
def test_direct_exec_and_option_flags_count_but_dash_c_never(tmp_path, procs):
    a, b, c = (f"zt_{secrets.token_hex(4)}.sh" for _ in range(3))
    procs([str(script(tmp_path, a))])
    assert running(a)
    procs(["bash", "-x", str(script(tmp_path, b))])
    assert running(b)
    procs(["bash", "-c", f"{c} 1; sleep 30"])
    assert not running(c)


@needs
def test_a_non_shell_mentioning_the_name_does_not_count(tmp_path, procs):
    name = f"zt_{secrets.token_hex(4)}.sh"
    procs(["tail", "-f", "/dev/null", name])
    procs(["sleep", "30"])
    assert not running(name)


def test_the_unanchored_scans_are_gone():
    # the class: no landing script may `pgrep -f` a lander name bare; bar_reliability's pattern is anchored too
    for f in LANDING.glob("*.sh"):
        for line in f.read_text().splitlines():
            if line.lstrip().startswith("#"):
                continue
            assert not re.search(r"pgrep\s+-f.*(land_voice_pr|land_queue|docs_merge_chain)", line), f"{f.name}: {line}"
    from scripts.perf import bar_reliability as br
    pat = re.compile(br.LANDER_PATTERN)
    assert not pat.search("bash -c until ! pgrep -f land_voice_pr.sh; do sleep 5; done")
    assert pat.search("bash /x/scripts/landing/land_voice_pr.sh 12 a")
