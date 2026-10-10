"""The night-mind cells are a MEASUREMENT of a model that samples, so a verdict is a vote over runs, reproducible per run (2026-10-10).

The same cell scored PASS and FAIL on the same 4B within the hour (K3, K6, K9, K10 FAIL in one full run, PASS in the next), so ``zoe-night-mind.py --cells`` now runs
``--runs N`` (default 3), pins ``temperature`` and a per-run ``seed`` on every call (llama.cpp takes both per request) and reports the MAJORITY verdict with the per-run votes.
The member pass keeps its production temperature and no seed. The standalone CLI also sets ITS OWN process's ``ZOE_NIGHT_MIND`` so the day loader reads 600 turns, not 200.

RED-BEFORE-GREEN (each was run reverted): a tie that passed; a SKIP counted as a vote; the seed not sent; the temperature literal back in ``_complete``; the CLI env line removed.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))
sys.path.insert(0, str(REPO / "scripts" / "perf"))

import night_mind as nm  # noqa: E402
from zmb.night_brain import FakeNightBrain, FakeNightServer  # noqa: E402


def _cli():
    spec = importlib.util.spec_from_file_location("zoe_night_mind_cli_runs", REPO / "scripts" / "maintenance" / "zoe-night-mind.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_py(args):
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "PYTHONPATH", "TZ", "LANG")}
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, env=env, timeout=300, cwd=str(REPO))


@pytest.mark.parametrize("votes,verdict", [
    (["PASS", "PASS", "PASS"], "PASS"), (["PASS", "FAIL", "PASS"], "PASS"), (["FAIL", "PASS", "FAIL"], "FAIL"),
    (["PASS", "FAIL"], "FAIL"),                          # a tie is not a pass: the bar is never lower than one run's
    (["PASS", "PASS", "ERROR"], "PASS"), (["PASS", "ERROR", "ERROR"], "ERROR"), (["PASS", "FAIL", "ERROR"], "FAIL"),
    (["PASS", "SKIP", "SKIP"], "PASS"),                  # a cell the budget did not start in a run casts no vote
    (["FAIL", "SKIP", "SKIP"], "FAIL"), (["SKIP", "SKIP"], "SKIP"), (["PASS"], "PASS"), (["FAIL"], "FAIL"),
])
def test_the_majority_verdict(votes, verdict):
    assert _cli().majority(votes) == verdict


def test_the_config_carries_temperature_and_seed_and_the_member_pass_keeps_the_production_values():
    prod = nm.config_from_env(url="http://127.0.0.1:1")
    assert prod.temperature == nm.PRODUCTION_TEMPERATURE == 0.1 and prod.seed is None
    pinned = nm.config_from_env(url="http://127.0.0.1:1", temperature=0.0, seed=7)
    assert pinned.temperature == 0.0 and pinned.seed == 7


def test_complete_sends_the_pinned_sampling_and_no_seed_when_none():
    srv = FakeNightServer(FakeNightBrain())
    try:
        import asyncio
        msgs = [{"role": "system", "content": nm.MOMENTS_SYSTEM}, {"role": "user", "content": "TASK: MOMENTS\n[m1] d: hi"}]
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        asyncio.run(nm._complete(msgs, 50, nm.config_from_env(url=srv.url), usage))
        asyncio.run(nm._complete(msgs, 50, nm.config_from_env(url=srv.url, temperature=0.0, seed=41), usage))
        plain, pinned = srv.bodies
        assert plain["temperature"] == 0.1 and "seed" not in plain
        assert pinned["temperature"] == 0.0 and pinned["seed"] == 41
    finally:
        srv.close()


def test_the_cli_runs_the_cells_n_times_pins_a_seed_per_run_and_reports_votes():
    srv = FakeNightServer(FakeNightBrain())
    try:
        r = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", srv.url, "--ctx-tokens", "8192", "--cells", "--only", "K9,K12", "--runs", "2", "--cell-seed", "500"])
        assert r.returncode == 0, r.stderr[-800:]
        assert len(r.stdout.strip().splitlines()) == 1
        c = json.loads(r.stdout)["cells"]
        assert c["runs_n"] == 2 and c["votes"] == {"K9": [c["votes"]["K9"][0], c["votes"]["K9"][1]], "K12": [c["votes"]["K12"][0], c["votes"]["K12"][1]]}
        assert len(c["runs"]) == 2 and c["K9"] in ("PASS", "FAIL") and c["pass"] + c["fail"] + c["error"] + c["skip"] == 2
        seeds = sorted({b["seed"] for b in srv.bodies if "seed" in b})
        assert seeds == [500, 501], seeds                                    # run 0 -> 500, run 1 -> 501, on every call of the cells
        assert all(b["seed"] in (500, 501) and b["temperature"] == 0.1 for b in srv.bodies), "every cell call carries the pinned sampling"
        assert isinstance(c["cell_calls"], list) and len(c["cell_calls"]) == 2 and set(c["cell_calls"][0]) == {"K9", "K12"}
    finally:
        srv.close()


def test_one_run_reports_the_same_shape_and_the_temperature_flag_reaches_the_server():
    srv = FakeNightServer(FakeNightBrain())
    try:
        r = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", srv.url, "--cells", "--only", "K12", "--runs", "1", "--cell-temperature", "0"])
        c = json.loads(r.stdout)["cells"]
        assert c["runs_n"] == 1 and c["votes"]["K12"] == [c["K12"]] and c["temperature"] == 0.0
        assert all(b["temperature"] == 0.0 for b in srv.bodies) and srv.bodies
    finally:
        srv.close()


def test_the_member_pass_sends_the_production_temperature_and_no_seed(tmp_path):
    srv = FakeNightServer(FakeNightBrain())
    f = tmp_path / "t.json"
    f.write_text(json.dumps([{"id": f"t{i}", "text": t, "at": "2026-10-08T01:00:00+00:00"} for i, t in enumerate(
        ["Tamsin got the offer from Pinecrest Mills and I am so glad for her!", "I am running the 5k on Saturday with Jorunn.", "My knee has been sore since the hike."] * 2)]))
    try:
        r = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", srv.url, "--user", "demo_bar_00000001", "--transcript-file", str(f), "--dry-run"])
        assert r.returncode == 0, r.stderr[-800:]
        assert srv.bodies and all(b["temperature"] == 0.1 and "seed" not in b for b in srv.bodies)
    finally:
        srv.close()


def test_the_standalone_cli_turns_its_own_flag_on_so_the_day_loader_reads_600_turns(tmp_path):
    f = tmp_path / "t.json"
    f.write_text("[]")
    code = (
        "import asyncio, importlib.util, os, sys\n"
        "os.environ.pop('ZOE_NIGHT_MIND', None)\n"
        f"sys.path.insert(0, {str(REPO / 'services' / 'zoe-data')!r})\n"
        f"spec = importlib.util.spec_from_file_location('cli', {str(REPO / 'scripts' / 'maintenance' / 'zoe-night-mind.py')!r})\n"
        "cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)\n"
        f"srv_url = {{url!r}}\n"
        f"asyncio.run(cli.amain(['--model-url', srv_url, '--user', 'demo_bar_00000001', '--transcript-file', {str(f)!r}, '--dry-run']))\n"
        "import memory_digest\n"
        "print('FLAG', os.environ.get('ZOE_NIGHT_MIND'), memory_digest._turn_limit())\n")
    srv = FakeNightServer(FakeNightBrain())
    try:
        r = subprocess.run([sys.executable, "-c", code.format(url=srv.url)], capture_output=True, text=True, timeout=120, cwd=str(REPO),
                           env={k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TZ", "LANG")})
        assert "FLAG shadow 600" in r.stdout, r.stdout + r.stderr[-600:]
    finally:
        srv.close()
