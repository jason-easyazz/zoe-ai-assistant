"""A report is a diagnostic: closed codes, ids and counts only - never text that came from an engine, a
fault packet, a patch or a command line. A fake engine plants an invented marker in EVERY free-text place
it controls, for every outcome the loop can reach; the marker must be in no report, ledger line, audit
row, state file, captured log record or exception message afterwards. Synthetic; scratch repos only."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from canned import FAULT_JUNIT
from conftest import BUGGY, FIXED, TEST, make_cfg, seed_repo, sh
from selfheal import loop as loopmod
from selfheal import proposer
from selfheal.gitops import add_worktree
from selfheal.loop import SCHEMA, HealLoop, check_report, pr_steps
from selfheal.proposer import (DryRunProposer, Proposal, ProposerEngine, ScriptedProposer, Usage, clean_usage,
                               clean_would_run, engine_code, make_patch)
from test_self_heal import NOON
from test_self_heal_propose import _ticket
from test_self_heal_sandbox import needs_bwrap

MARK = "Zq81planted_marker_4471"        # invented; token-shaped on purpose (it passes the token filters)


class Named(ScriptedProposer):
    name = MARK                           # the engine's own name is engine-supplied text too


class Boom(Exception):
    pass


Boom.__name__ = "Boom" + MARK


def _fix(p, tail=""):
    return make_patch({"calc.py": p.files["calc.py"]}, {"calc.py": FIXED + f"# {MARK}\n" + tail})


def _raises(p):
    raise Boom(MARK)


def _slow(p):
    time.sleep(3)
    return Proposal(_fix(p), Usage(turns=MARK, tokens=MARK))


SCENARIOS = {   # name -> (engine answer, the reason the loop must report, config overrides)
    "accepted": (lambda p: Proposal(_fix(p), Usage(turns=3, tokens=1200)), "", {}),
    "usage_text": (lambda p: Proposal(_fix(p), Usage(turns=MARK, tokens=MARK)), "usage_malformed", {}),
    "usage_bool": (lambda p: Proposal(_fix(p), Usage(turns=True, tokens=1)), "usage_malformed", {}),
    "usage_negative": (lambda p: Proposal(_fix(p), Usage(turns=-1, tokens=1)), "usage_malformed", {}),
    "budget_turns": (lambda p: Proposal(_fix(p), Usage(turns=10**6, tokens=10)), "budget_turns", {}),
    "dry_run": (lambda p: Proposal(would_run=[{"step": "engine", "argv": [MARK, "--key", MARK], "cwd": MARK},
                                              {"step": MARK, "argv": [MARK]}, MARK], usage=Usage(MARK, MARK)),
                "engine_dry_run", {}),
    "patch_text": (lambda p: f"I fixed it, trust me. {MARK}", "patch_malformed", {}),
    "patch_path": (lambda p: make_patch({}, {f".github/workflows/{MARK}.yml": f"on: push  # {MARK}\n"}),
                   "patch_outside_allowlist", {}),
    "engine_error": (_raises, "engine_error", {}),
    "gate_failed": (lambda p: Proposal(make_patch({"calc.py": p.files["calc.py"]},
                                                  {"calc.py": f'def add(a, b):\n    raise RuntimeError("{MARK}")\n'}),
                                       Usage(turns=2, tokens=5)), "gate_failed", {}),
    "timeout": (_slow, "budget_time", {"engine_timeout_s": 1}),
}


def _scratch(tmp_path, **kw):
    """A repo whose fault packet is full of the marker: the failing assertion's message, a file's text."""
    live, repo = tmp_path / "live", tmp_path / "repo.git"
    seed_repo(live, TEST.replace("== 5\n", f'== 5, "{MARK}"\n', 1))
    (live / "calc.py").write_text(BUGGY + f"# {MARK}\n")
    sh("git", "add", "-A", cwd=live)
    sh("git", "commit", "-q", "-m", "plant", cwd=live)
    sh("git", "clone", "-q", "--bare", str(live), str(repo))
    sh("git", "-C", str(repo), "remote", "remove", "origin")
    (tmp_path / "fault.xml").write_text(FAULT_JUNIT)
    return make_cfg(tmp_path, repo=repo, live=live, dispatch="full", unit_shape="sold_unit", **kw), str(tmp_path / "fault.xml")


@needs_bwrap
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_a_planted_marker_reaches_no_report_ledger_audit_state_log_or_exception(tmp_path, monkeypatch, caplog, name):
    answer, reason, kw = SCENARIOS[name]
    monkeypatch.setenv("ZOE_SELFHEAL_TEST_ENV_VALUE", MARK)     # an environment value the run could echo
    caplog.set_level(0)
    cfg, fault = _scratch(tmp_path, **kw)
    fake = Named(answer)
    raised = ""
    try:
        res = HealLoop(cfg, fake, now=lambda: NOON, max_rounds=1).run(fault)
    except BaseException as e:   # noqa: BLE001 - an exception message is a place too
        raised, res = f"{type(e).__name__}: {e}", {}
    assert fake.packets and MARK in fake.packets[0].to_json()    # control: the marker really was in the packet
    assert not raised, raised
    assert res["rounds"][0]["reason"] == reason and res["healed"] == (reason == "")
    assert res["engine"] == "other"                              # its own name is not repeated
    blob = json.dumps(res) + caplog.text + "".join(
        p.read_text(errors="replace") for p in Path(cfg.state_dir).rglob("*") if p.is_file())
    assert (Path(cfg.state_dir) / "audit.jsonl").exists()
    assert MARK not in blob and MARK.lower() not in blob.lower()
    check_report(res)                                            # the same closed schema, again, from outside


@needs_bwrap
def test_usage_is_reported_as_numbers_when_well_formed_and_counted_when_malformed(tmp_path):
    cfg, fault = _scratch(tmp_path)
    ok = HealLoop(cfg, Named(SCENARIOS["accepted"][0]), now=lambda: NOON, max_rounds=1).run(fault)
    assert (ok["rounds"][0]["turns"], ok["rounds"][0]["tokens"], ok["rounds"][0]["usage"]) == (3, 1200, "reported")
    bad_dir = tmp_path / "b"
    bad_dir.mkdir()
    cfg, fault = _scratch(bad_dir)
    bad = HealLoop(cfg, Named(SCENARIOS["usage_text"][0]), now=lambda: NOON, max_rounds=1).run(fault)
    r = bad["rounds"][0]
    assert (r["turns"], r["tokens"], r["usage"]) == (None, None, "usage_malformed")
    assert bad["refusals_by_reason"] == {"usage_malformed": 1}


@needs_bwrap
@pytest.mark.parametrize("usage", [Usage(turns=MARK, tokens=1), Usage(turns=1, tokens=MARK), Usage(10**6, 1)])
def test_a_refused_proposal_leaves_no_engine_text_on_the_engine_last(local_scratch, usage):
    cfg = local_scratch.cfg
    wt = str(Path(cfg.worktree_root) / "heal")
    add_worktree(cfg.repo, wt, base="main")
    eng = ProposerEngine(cfg, Named(lambda p: Proposal(_fix(p), usage)))
    res = eng.run(_ticket(cfg, wt), wt)
    assert not res.ok and MARK not in repr(eng.last) and MARK not in repr(res) and eng.name == "other"
    assert set(eng.last) == {"reason", "turns", "tokens", "usage", "would_run", "files"}


# --- no bwrap needed: the reducers and the schema ----------------------------------------------------

@pytest.mark.parametrize("usage,want", [
    (Usage(3, 1200), (3, 1200, "reported")), (Usage(), (None, None, "unreported")), (Usage(0, None), (0, None, "reported")),
    (Usage(True, 1), (None, None, "usage_malformed")), (Usage(1.5, 1), (None, None, "usage_malformed")),
    (Usage(-1, 1), (None, None, "usage_malformed")), (Usage(10**9 + 1, 1), (None, None, "usage_malformed")),
    (Usage(MARK, 1), (None, None, "usage_malformed")), (Usage(1, [MARK]), (None, None, "usage_malformed")),
    (None, (None, None, "unreported")), (MARK, (None, None, "unreported"))])
def test_usage_reduces_to_validated_integers_or_a_code(usage, want):
    assert clean_usage(usage) == want and MARK not in repr(clean_usage(usage))


def test_would_run_reduces_to_step_kinds_and_the_engine_name_to_a_closed_name():
    steps = [{"step": "engine", "argv": [MARK]}, {"step": MARK}, MARK, {"argv": [MARK]}, {"step": "collect"}]
    assert clean_would_run(steps) == ["engine", "other", "other", "other", "collect"]
    assert clean_would_run(None) == [] and clean_would_run(MARK) == ["other"] and len(clean_would_run(steps * 9)) == 8
    assert pr_steps([["git", "push", "origin", MARK], ["gh", "pr", "create", "--title", MARK], [MARK], MARK]) \
        == ["git_push", "gh_pr_create_draft", "other", "other"]
    assert [engine_code(n) for n in ("fake", "dryrun-codex", MARK, None, 3)] == ["fake", "dryrun-codex", "other", "other", "other"]
    assert set(DryRunProposer.CLIS) == {"codex", "claude"} and all(f"dryrun-{c}" in proposer.ENGINE_NAMES for c in DryRunProposer.CLIS)


GOOD = {"healed": False, "engine": "fake", "shape": "sold_unit", "max_rounds": 2, "fault_to_verified_fix_s": None,
        "attempts": 1, "rollbacks": 0, "refusals_by_reason": {"patch_malformed": 1},
        "rounds": [{"round": 1, "outcome": "discarded", "reason": "patch_malformed", "gate": "", "ticket": "20261113-120000-ab12cd",
                    "seconds": 1.5, "turns": None, "tokens": 7, "usage": "unreported", "would_run": ["engine"]}],
        "ledger": {"chain_ok": True, "entries": 0}, "proof": "", "would_run": []}


def _mut(**path_values):
    rep = json.loads(json.dumps(GOOD))
    for path, v in path_values.items():
        node = rep
        *head, last = path.split("__")
        for h in head:
            node = node[int(h)] if h.isdigit() else node[h]
        node[int(last) if last.isdigit() else last] = v
    return rep


def test_the_report_writer_accepts_only_closed_codes_ids_and_numbers():
    assert check_report(json.loads(json.dumps(GOOD))) == GOOD
    for bad in (_mut(engine=MARK), _mut(shape=MARK), _mut(proof=MARK), _mut(refusals_by_reason={MARK: 1}),
                _mut(refusals_by_reason={"patch_malformed": MARK}), _mut(rounds__0__ticket=MARK),
                _mut(rounds__0__turns=MARK), _mut(rounds__0__tokens=True), _mut(rounds__0__outcome=MARK),
                _mut(rounds__0__reason=MARK), _mut(rounds__0__would_run=[["git", "push"]]),
                _mut(rounds__0__would_run=[MARK]), _mut(would_run=[["git", "push", "origin"]]), _mut(rollbacks=-1),
                _mut(rounds__0__seconds="1.5"), _mut(ledger__entries=MARK), _mut(healed="yes")):
        with pytest.raises(ValueError) as e:
            check_report(bad)
        assert MARK not in str(e.value)          # the message names the key, never the value
    for key in ("note", "error", "detail", "message"):   # a NEW free-text field cannot be added unnoticed
        with pytest.raises(ValueError):
            check_report({**GOOD, key: "anything"})
        with pytest.raises(ValueError):
            check_report({**GOOD, "rounds": [{**GOOD["rounds"][0], key: "anything"}]})
    with pytest.raises(ValueError):
        check_report({k: v for k, v in GOOD.items() if k != "proof"})


def test_every_leaf_of_the_schema_is_a_number_a_bool_or_a_closed_set():
    def leaves(schema):
        for spec in schema.values():
            if isinstance(spec, tuple) and spec[0] in ("rows", "row"):
                yield from leaves(spec[1])
            else:
                yield spec
    for spec in leaves(SCHEMA):
        flat = spec if isinstance(spec, tuple) else (spec,)
        assert all(s in (int, float, bool, None) or isinstance(s, (frozenset, type(loopmod.PROOF))) or s == "map"
                   for s in flat), spec
        assert str not in flat and dict not in flat and list not in flat    # no free text, ever
