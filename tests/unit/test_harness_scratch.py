"""P-2: the harnesses (the bar, the day-sim, the person bench) write ONLY to a throwaway Zoe (``scripts/perf/harness_scratch.py``).

The pure parts run in CI: the live-DSN guard, the environment the scratch zoe-data and Flue get (no live secret, no live location,
every house-control endpoint black-holed, egress through a black-hole proxy), and the wiring of the three harness mains (the scratch
stack comes up after the quiet-hours guard and BEFORE any live object exists; the live DSN is never read except to compare).
The real stack (docker, a second zoe-data) is opt-in: ``ZOE_SCRATCH_PG_TEST=1`` runs a scratch Postgres, migrates it, exercises the
evolution queue on it through asyncpg and tears it down (~20 s, ~40 MB).

Negative controls (run by hand when this was written; each turns the named test red):
  * ``is_live_dsn`` ignores the live port            -> test_the_live_database_is_refused
  * copy ``ZOE_INTERNAL_TOKEN`` into ``flag_env``     -> test_the_scratch_instance_gets_no_live_secret_and_no_live_location
  * drop ``MUSIC_ASSISTANT_URL`` from ``HOUSE_URLS``  -> test_the_house_is_out_of_reach
  * build ``DayLive`` from the service .env again     -> test_every_harness_main_builds_its_live_client_from_the_scratch_stack
  * drop the quiet-hours call in ``scratch_stack_for`` -> test_nothing_starts_in_waking_hours
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

import harness_scratch as hs  # noqa: E402

LIVE = "postgresql://zoe:livepassword123@localhost:5432/zoe"
LIVE_ENV = {
    "POSTGRES_URL": LIVE, "ZOE_INTERNAL_TOKEN": "live-internal", "ZOE_BRAIN_TOKEN": "live-brain", "HA_TOKEN": "live-ha",
    "ZOE_TELEGRAM_BOT_TOKEN": "live-tg", "ZOE_RESTRAINT": "shadow", "ZOE_NIGHT_MIND": "serve", "ZOE_LOG_DIR": "/home/zoe/.zoe-logs",
    "ZOE_ROUTER_RETRAIN_DIR": "/home/zoe/.zoe/router_retrain", "ZOE_HA_BRIDGE_URL": "http://127.0.0.1:8007",
    "MUSIC_ASSISTANT_URL": "http://192.168.1.5:8095", "ZOE_ROUTER_SIDECAR_URL": "http://127.0.0.1:11436",
    "ZOE_WEBHOOK_SECRET": "live-hook", "ZOE_VAPID_PRIVATE_KEY": "k", "ZOE_FLUE_BRAIN_URL": "http://127.0.0.1:3579",
    "ZOE_SYNTHETIC_USER_ALLOWLIST": "demo_bar_ab0e0001", "OPENROUTER_API_KEY": "sk-live",
}


@pytest.mark.parametrize("dsn,live", [
    (LIVE, True),
    ("postgresql://zoe:x@127.0.0.1:5432/other_db", True),            # another database on the live server is still the live server
    ("postgresql://zoe_scratch:x@127.0.0.1:43363/zoe_scratch", False),
    ("postgresql://zoe_scratch:x@localhost:55432/zoe_scratch", False),
    ("postgresql://u:x@zoe-database:5432/zoe", True),                # the compose service name
    ("", True), ("not a url", True), ("postgresql://u:x@127.0.0.1:notaport/db", True),
])
def test_the_live_database_is_refused(dsn, live):
    assert hs.is_live_dsn(dsn, LIVE) is live
    if live:
        with pytest.raises(hs.Refused):
            hs.assert_not_live(dsn, LIVE)
    else:
        hs.assert_not_live(dsn, LIVE)


def test_a_moved_live_server_is_still_refused():
    moved = "postgresql://zoe:x@127.0.0.1:6543/zoe"
    assert hs.is_live_dsn("postgresql://a:b@localhost:6543/scratch", moved)
    assert not hs.is_live_dsn("postgresql://a:b@localhost:6544/scratch", moved)
    assert hs.is_live_dsn("postgresql://a:b@localhost:5432/scratch", None)        # no live DSN known: 5432 here is refused anyway


def _envs(tmp_path):
    root = tmp_path / "stack"
    z = hs.harness_env(LIVE_ENV, root=root, dsn="postgresql://zoe_scratch:pw@127.0.0.1:40001/zoe_scratch", token="scratch-t",
                       brain_token="scratch-b", flue_url="http://127.0.0.1:40003", self_url="http://127.0.0.1:40002")
    f = hs.flue_env({"ZOE_BRAIN_TOKEN": "live-brain", "ZOE_INTERNAL_TOKEN": "live-internal", "ZOE_DATA_URL": "http://127.0.0.1:8000",
                     "ZOE_BRAIN_MAX_TOOL_ITERS": "6", "ZOE_BRAIN_BASE_URL": "http://127.0.0.1:11434/v1"},
                    root=root, port=40003, data_url="http://127.0.0.1:40002", token="scratch-t", brain_token="scratch-b")
    return root, z, f


def test_the_scratch_instance_gets_no_live_secret_and_no_live_location(tmp_path):
    root, z, f = _envs(tmp_path)
    for env in (z, f):
        blob = "\n".join(env.values())
        for secret in ("livepassword123", "live-internal", "live-brain", "live-ha", "live-tg", "live-hook", "sk-live"):
            assert secret not in blob, secret
        assert "/home/zoe/.zoe-logs" not in blob and "/home/zoe/.zoe/router_retrain" not in blob
        assert "OPENROUTER_API_KEY" not in env and "HA_TOKEN" not in env
    assert z["POSTGRES_URL"].endswith("@127.0.0.1:40001/zoe_scratch") and not hs.is_live_dsn(z["POSTGRES_URL"], LIVE)
    assert (z["ZOE_INTERNAL_TOKEN"], z["ZOE_BRAIN_TOKEN"], z["ZOE_FLUE_BRAIN_URL"]) == ("scratch-t", "scratch-b", "http://127.0.0.1:40003")
    for key in ("HOME", "MEMPALACE_DATA_DIR", "ZOE_DATA_DB", "ZOE_LOG_DIR", "ZOE_ROUTER_RETRAIN_DIR", "ZOE_VOICE_SAMPLE_DIR"):
        assert z[key].startswith(str(root)), key
    assert (z["ZOE_RESTRAINT"], z["ZOE_NIGHT_MIND"], z["ZOE_SYNTHETIC_USER_ALLOWLIST"]) == ("shadow", "serve", "demo_bar_ab0e0001")
    assert z["ZOE_ROUTER_SIDECAR_URL"] == "http://127.0.0.1:11436"          # shared compute is kept
    assert z["ZOE_HARNESS"] == "1" and z["ZOE_VOICE_SAVE_AUDIO"] == "0" and z["ZOE_MULTICA"] == "false"
    assert (f["ZOE_DATA_URL"], f["ZOE_INTERNAL_TOKEN"], f["ZOE_BRAIN_TOKEN"], f["PORT"]) == (
        "http://127.0.0.1:40002", "scratch-t", "scratch-b", "40003")
    assert f["ZOE_BRAIN_DB"].startswith(str(root)) and f["ZOE_BRAIN_MAX_TOOL_ITERS"] == "6"
    assert f["ZOE_BRAIN_BASE_URL"] == "http://127.0.0.1:11434/v1"


def test_the_house_is_out_of_reach(tmp_path):
    _root, z, _f = _envs(tmp_path)
    for name in ("ZOE_HA_BRIDGE_URL", "MUSIC_ASSISTANT_URL", "HA_BASE_URL", "LIVEKIT_URL", "ZOE_AUTH_URL", "ZOE_KOKORO_SIDECAR_URL"):
        assert z[name] == hs.BLACK_HOLE, name
    for name in hs.SELF_URLS:                                                 # it calls ITSELF, never the live :8000
        assert z[name] == "http://127.0.0.1:40002", name
    assert z["HTTPS_PROXY"] == z["https_proxy"] == hs.BLACK_HOLE and "127.0.0.1" in z["NO_PROXY"]
    assert z["ZOE_PI_HOST"] == "invalid." and z["MULTICA_WEBHOOK_TARGET_URL"] == hs.BLACK_HOLE
    assert z["ZOE_SELF_URL"] == "http://127.0.0.1:40002"
    assert z["MOONSHINE_VOICE_CACHE"].endswith("no-stt-models")


def test_every_endpoint_with_a_live_default_is_overridden():
    """The CLASS behind a found leak (``ZOE_OPENCLAW_GW``, cross-review of #2165): every env name zoe-data reads with an http(s) / ws
    default (whose default is a real endpoint) is black-holed, pointed at the scratch instance itself, or is shared compute; and a
    copied flag never carries a URL value. Negative control: drop ``ZOE_OPENCLAW_GW`` from ``HOUSE_URLS`` -> red."""
    import re

    pat = re.compile(r"""(?:environ\.get|getenv|env_str|env_int|env_bool)\(\s*["']([A-Z0-9_]+)["']\s*,\s*["'](?:https?|wss?)://""")
    svc = REPO / "services" / "zoe-data"
    names = set()
    for path in svc.rglob("*.py"):
        if "tests" not in path.relative_to(svc).parts:
            names |= set(pat.findall(path.read_text(errors="replace")))
    assert "ZOE_HA_BRIDGE_URL" in names and "MUSIC_ASSISTANT_URL" in names, "control: the scan finds the known ones"
    handled = set(hs.HOUSE_URLS) | set(hs.SELF_URLS) | set(hs.COMPUTE_URLS)
    assert sorted(names - handled) == []
    assert hs.flag_env(dict(ZOE_SOMETHING_GW="http://127.0.0.1:18789", ZOE_MODE="on")) == dict(ZOE_MODE="on")


def test_no_hardcoded_self_call_reaches_the_live_service():
    """A loopback call to :8000 is TRUSTED by the live service (``require_internal_token`` admits loopback), so a harness instance
    must never make one: every self-call reads ``ZOE_SELF_URL`` (the scratch instance sets it). Read-only GETs included.
    Negative control: put ``_BROADCAST_URL = "http://127.0.0.1:8000/..."`` back in ``mcp_server.py`` -> red."""
    import re

    svc = REPO / "services" / "zoe-data"
    hits = []
    for path in svc.rglob("*.py"):
        if "tests" in path.relative_to(svc).parts:
            continue
        lines = path.read_text(errors="replace").splitlines()
        for n, line in enumerate(lines, 1):
            context = " ".join(lines[max(0, n - 3):n])           # an env default may sit on the line under its os.environ.get(
            if re.search(r"https?://(127\.0\.0\.1|localhost):8000\b", line) and "environ" not in context and "SELF_URL" not in line \
                    and "host_hdr" not in line:
                hits.append(f"{path.name}:{n}")
    assert hits == []


def test_the_flag_filter():
    flags = hs.flag_env(LIVE_ENV)
    assert set(flags) == {"ZOE_RESTRAINT", "ZOE_NIGHT_MIND", "ZOE_SYNTHETIC_USER_ALLOWLIST"}
    for name in ("ZOE_X_TOKEN", "ZOE_X_SECRET", "ZOE_X_PASSWORD", "ZOE_X_KEY", "ZOE_X_URL", "ZOE_X_DIR", "ZOE_X_PATH", "ZOE_X_DB"):
        assert hs.flag_env({name: "v"}) == {}, name


def test_the_env_file_parser(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# c\nA=1\nexport B='two'\nC=\"3\"\nbad line\n1X=no\n")
    assert hs.parse_env_file(p) == {"A": "1", "B": "two", "C": "3"}
    assert hs.parse_env_file(tmp_path / "missing") == {}


def test_a_tag_is_safe():
    for bad in ("", "UPPER", "../x", "a" * 40, "a b"):
        with pytest.raises(hs.Refused):
            hs.state_path(bad)
    assert hs.state_path("samantha-bar-123").name == "samantha-bar-123.json"


def test_a_killed_run_is_swept_and_a_live_one_is_not(tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "STATE_DIR", tmp_path)
    for tag in ("samantha-bar-999999999", "manual", f"samantha-bar-{os.getpid()}"):
        (tmp_path / f"{tag}.json").write_text("{}")
    downed = []
    monkeypatch.setattr(hs, "down", lambda tag, log=print: downed.append(tag) or True)
    assert hs.sweep_stale(lambda m: None) == ["samantha-bar-999999999"] and downed == ["samantha-bar-999999999"]


def test_the_redaction():
    assert hs._redact("connect postgresql://zoe_scratch:secretpw@127.0.0.1:1/db failed") == \
        "connect postgresql://zoe_scratch:***@127.0.0.1:1/db failed"


def test_public_carries_no_dsn_and_no_token():
    st = hs.ScratchStack(Path("/x"), tag="t1")
    st.state = {"tag": "t1", "dsn": LIVE, "token": "tok", "brain_token": "b", "ram": {"total_mb": 1.0}, "pg_port": 1}
    assert "dsn" not in st.public() and "token" not in st.public() and "brain_token" not in st.public()
    assert st.public()["ram"] == {"total_mb": 1.0}


def test_a_failing_end_of_run_measure_still_tears_the_stack_down(monkeypatch):
    """The end-of-run ``docker stats`` can raise; the scratch postgres/network/zoe-data/Flue must still be removed."""
    downed = []

    def boom(state):
        raise RuntimeError("docker stats unavailable")

    monkeypatch.setattr(hs, "measure", boom)
    monkeypatch.setattr(hs, "down", lambda tag, log=print, **kw: downed.append(tag) or True)
    st = hs.ScratchStack(Path("/x"), tag="t2", log=lambda *a, **k: None)
    st.state = {"tag": "t2", "dsn": LIVE}
    st.__exit__(None, None, None)                      # must not raise
    assert downed == ["t2"]
    assert "docker stats unavailable" in st.state["ram_at_end"]["error"]


# -- the wiring of the three harness mains ------------------------------------------

HARNESSES = {"samantha_bar.py": "Live(", "samantha_day_sim.py": "DayLive(", "samantha_person.py": "PersonLive("}


def _main_source(fname: str) -> str:
    text = (REPO / "scripts" / "perf" / fname).read_text()
    tree = ast.parse(text)
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_main"][0]
    return ast.get_source_segment(text, fn)


@pytest.mark.parametrize("fname", sorted(HARNESSES))
def test_every_harness_main_builds_its_live_client_from_the_scratch_stack(fname):
    src = _main_source(fname)
    ctor = HARNESSES[fname]
    assert src.count("scratch_stack_for(") == 1, "the run takes exactly one scratch stack"
    calls = [i for i in range(len(src)) if src.startswith(ctor, i) and not src[i - 1].isalnum() and src[i - 1] != "_"]
    assert calls, "the live client is built in _main"
    assert all(src.index("scratch_stack_for(") < i for i in calls), "no live client before the scratch stack"
    assert all(src[i:i + 120].split(",")[0].replace(ctor, "").strip() == "stack.token" for i in calls), \
        "the client carries the SCRATCH token"
    assert 'env_file_value(service_dir, "POSTGRES_URL")' not in src and 'env_file_value(service_dir, "ZOE_INTERNAL_TOKEN")' not in src
    text = (REPO / "scripts" / "perf" / fname).read_text()
    assert "close_scratch(ctx)" in text and "return _main(argv, ctx)" in text, "main tears the stack down in finally"


def test_the_live_dsn_is_read_only_to_be_refused():
    text = (REPO / "scripts" / "perf" / "samantha_bar.py").read_text()
    assert text.count('env_file_value(service_dir, "POSTGRES_URL")') == 1
    assert 'hs.assert_not_live(stack.dsn, env_file_value(service_dir, "POSTGRES_URL"))' in text


class _FakeHS:
    Refused = hs.Refused
    assert_not_live = staticmethod(hs.assert_not_live)

    def __init__(self, dsn="postgresql://zoe_scratch:x@127.0.0.1:41001/zoe_scratch"):
        self.dsn, self.started = dsn, []

    def ScratchStack(self, service_dir, *, serve, tag, log):          # noqa: N802 - mirrors the class
        outer = self

        class _S:
            def __init__(s):
                s.tag, s.dsn, s.token, s.base_url = tag, outer.dsn, "scratch-t", "http://127.0.0.1:41002"
                s.exited = False

            def __enter__(s):
                outer.started.append((tag, serve))
                return s

            def __exit__(s, *a):
                s.exited = True

            def public(s):
                return {"tag": tag, "ram": {"total_mb": 700.0}}
        return _S()


@pytest.fixture
def bar(monkeypatch, tmp_path):
    import samantha_bar as sb

    (tmp_path / ".env").write_text("POSTGRES_URL=" + LIVE + "\n")
    monkeypatch.setattr(sb, "DATA_BASE", "http://127.0.0.1:8000")
    monkeypatch.setattr(sb, "in_nightly_window", lambda now: False)          # scratch_stack_for gates on these before it starts anything
    monkeypatch.setattr(sb, "deploy_in_progress", lambda: (False, "0"))
    return sb, tmp_path


def test_nothing_starts_in_waking_hours(bar, monkeypatch):
    sb, svc = bar
    fake = _FakeHS()
    monkeypatch.setattr(sb, "_harness_scratch", lambda: fake)
    monkeypatch.setattr(sb, "harness_quiet_refusal", lambda name=None: "waking hours (07:00-22:00)")
    ctx: dict = {}
    stack, refusal = sb.scratch_stack_for(svc, "samantha_bar", ctx, print)
    assert stack is None and "waking hours" in refusal and fake.started == [] and ctx == {}
    assert sb.DATA_BASE == "http://127.0.0.1:8000"


def test_the_harness_is_pointed_at_the_scratch_instance_and_torn_down(bar, monkeypatch):
    sb, svc = bar
    fake = _FakeHS()
    monkeypatch.setattr(sb, "_harness_scratch", lambda: fake)
    monkeypatch.setattr(sb, "harness_quiet_refusal", lambda name=None: None)
    ctx: dict = {}
    stack, refusal = sb.scratch_stack_for(svc, "samantha_day_sim", ctx, print)
    assert refusal is None and fake.started == [(stack.tag, True)] and stack.tag.startswith("samantha-day-sim-")
    assert sb.DATA_BASE == "http://127.0.0.1:41002" and stack.token == "scratch-t"
    sb.close_scratch(ctx)
    assert stack.exited and ctx == {}


def test_a_stack_on_the_live_server_is_refused(bar, monkeypatch):
    sb, svc = bar
    fake = _FakeHS(dsn="postgresql://zoe:x@localhost:5432/zoe")                # a stack that somehow came up on the live server
    monkeypatch.setattr(sb, "_harness_scratch", lambda: fake)
    monkeypatch.setattr(sb, "harness_quiet_refusal", lambda name=None: None)
    ctx: dict = {}
    stack, refusal = sb.scratch_stack_for(svc, "samantha_person", ctx, print)
    assert stack is None and "live database" in refusal
    assert sb.DATA_BASE == "http://127.0.0.1:8000", "never pointed at it"
    sb.close_scratch(ctx)


def test_the_scratch_db_gets_the_auth_tables_from_the_real_ddl(monkeypatch):
    """forget-synthetic reads auth_users, which the zoe-data Alembic chain never creates."""
    ddl = hs.AUTH_DDL.read_text()
    assert hs.AUTH_DDL == REPO / "scripts" / "setup" / "migrate_auth_to_postgres.sql"
    assert "CREATE TABLE IF NOT EXISTS auth_users" in ddl
    assert "insert into" not in ddl.lower(), "the DDL must seed no row: an empty table means no id is registered"
    calls = []
    monkeypatch.setattr(hs, "_run", lambda cmd, **kw: calls.append((cmd, kw)))
    hs.apply_auth_ddl("zoe-harness-x")
    (cmd, kw), = calls
    assert cmd[:4] == ["docker", "exec", "-i", "zoe-harness-x"] and "psql" in cmd
    assert "ON_ERROR_STOP=1" in cmd and hs.PG_DB in cmd and hs.PG_USER in cmd
    assert kw["stdin"] == ddl                          # the shipped file itself, not a hand-copied table


def test_up_applies_the_auth_ddl_after_alembic_and_before_serving():
    """Negative control: delete the apply_auth_ddl(...) call from up() and this goes red."""
    up = next(n for n in ast.parse(Path(hs.__file__).read_text()).body
              if isinstance(n, ast.FunctionDef) and n.name == "up")
    order = []
    for n in ast.walk(up):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else None
            if name in ("apply_auth_ddl", "_serve"):
                order.append((n.lineno, name))
            elif name == "_run" and any(isinstance(a, ast.Constant) and a.value == "alembic"
                                        for arg in n.args if isinstance(arg, ast.List) for a in arg.elts):
                order.append((n.lineno, "alembic"))
    assert [name for _, name in sorted(order)] == ["alembic", "apply_auth_ddl", "_serve"]


def _fake_serve_tree(tmp_path, monkeypatch, spawn):
    svc = tmp_path / "services" / "zoe-data"
    svc.mkdir(parents=True)
    (tmp_path / "labs" / "flue-zoe-brain-2x" / "dist").mkdir(parents=True)
    (tmp_path / "labs" / "flue-zoe-brain-2x" / "dist" / "server.mjs").write_text("")
    monkeypatch.setattr(hs.shutil, "which", lambda *a, **k: "/usr/bin/node")
    monkeypatch.setattr(hs, "parse_env_file", lambda p: {})
    monkeypatch.setattr(hs, "flue_env", lambda *a, **k: {})
    monkeypatch.setattr(hs, "harness_env", lambda *a, **k: {"POSTGRES_URL": "postgresql://scratch"})
    monkeypatch.setattr(hs, "assert_not_live", lambda *a, **k: None)
    monkeypatch.setattr(hs, "_spawn", spawn)
    writes = []
    monkeypatch.setattr(hs, "_write_state", lambda st: writes.append(sorted(st.get("procs", {}))))
    root = tmp_path / "root"
    root.mkdir()
    return svc, {"tag": "t", "root": str(root), "dsn": "postgresql://scratch", "procs": {}}, writes


def test_serve_refuses_when_the_flue_sidecar_never_answers(tmp_path, monkeypatch):
    """A Flue that died at start must fail the readiness gate even though zoe-data's /health is green.
    Negative control: drop the flue_url /health wait from _serve() -> red."""
    svc, state, _ = _fake_serve_tree(tmp_path, monkeypatch, lambda *a, **k: {"pid": 1, "start": 1, "cmd": []})
    ports = iter([4001, 4002])                         # zoe-data, then flue
    monkeypatch.setattr(hs, "free_port", lambda: next(ports))
    monkeypatch.setattr(hs, "_http_ok", lambda url: url.startswith("http://127.0.0.1:4001"))   # only zoe-data answers

    def wait(pred, timeout_s, what):
        if not pred():
            raise hs.Refused(f"{what} not ready")
    monkeypatch.setattr(hs, "_wait", wait)
    with pytest.raises(hs.Refused, match="Flue"):
        hs._serve(state, service_dir=svc, live_env={}, python="python3", log=lambda *_: None)


def test_serve_persists_each_process_so_a_failed_second_spawn_can_still_stop_the_first(tmp_path, monkeypatch):
    """Negative control: write the state only once after both spawns (the old shape) -> red."""
    count = []

    def spawn(cmd, **kw):
        if count:
            raise OSError("zoe-data failed to start")
        count.append(1)
        return {"pid": 1, "start": 1, "cmd": []}
    svc, state, writes = _fake_serve_tree(tmp_path, monkeypatch, spawn)
    with pytest.raises(OSError):
        hs._serve(state, service_dir=svc, live_env={}, python="python3", log=lambda *_: None)
    assert writes and writes[-1] == ["flue"], "Flue's pid must already be on disk when the next spawn fails"


# -- the real stack (opt-in: docker, ~40 MB, ~20 s) ------------------------------------

@pytest.mark.skipif(os.environ.get("ZOE_SCRATCH_PG_TEST") != "1", reason="opt-in: ZOE_SCRATCH_PG_TEST=1 (docker, a throwaway pg17)")
def test_a_real_scratch_postgres_migrates_runs_the_queue_and_goes():
    import asyncpg

    sys.path.insert(0, str(REPO / "services" / "zoe-data"))
    import db_pool
    import evolution_queue as eq

    tag = f"pytest-{os.getpid()}"
    st = hs.up(tag, service_dir=REPO / "services" / "zoe-data", serve=False, min_mem_mb=300)
    try:
        assert not hs.is_live_dsn(st["dsn"], LIVE) and st["ram"]["pg_mb"] < 384

        async def go():
            conn = await asyncpg.connect(st["dsn"])
            db = db_pool.AsyncpgCompat(conn)
            pid = await eq.propose(db, source="pytest", change_class="harness", component="manner", title="t")
            await eq.decide(db, pid, "approved", actor="a", guard={"decision": "", "autonomy_class": "prepare"})
            await eq.transition(db, pid, "applied", actor="a", require_decision="approved")
            with pytest.raises(eq.IllegalTransition):
                await eq.transition(db, pid, "probed", actor="a")
            with pytest.raises(Exception):                                     # the CHECK constraint backs the code
                await db.execute("UPDATE evolution_proposals SET status = $1 WHERE id = $2", "pending", pid)
            assert await conn.fetchval("SELECT 1 FROM auth_users WHERE user_id = $1 LIMIT 1", "nobody") is None
            status = await conn.fetchval("SELECT status FROM evolution_proposals WHERE id = $1", pid)
            await conn.close()
            return status
        assert asyncio.run(go()) == "applied"
    finally:
        assert hs.down(tag)
    assert hs.load_state(tag) is None


def test_the_scratch_instance_does_not_wait_for_the_black_holed_voice_services():
    """/readyz fails in kokoro mode when the (black-holed) Kokoro sidecar is unreachable, so all three text harnesses would time out.
    Negative control: drop ZOE_TTS_MODE from harness_env -> red."""
    env = hs.harness_env({**LIVE_ENV, "ZOE_TTS_MODE": "kokoro"}, root=Path("/tmp/x"), dsn="postgresql://s", token="t", brain_token="b",
                         flue_url="http://127.0.0.1:1", self_url="http://127.0.0.1:2")
    assert env["ZOE_TTS_MODE"] == "offline"


def test_the_scratch_brain_listens_on_loopback_and_is_never_open():
    """A copied ZOE_BRAIN_OPEN=1 drops the bearer gate and Flue binds every interface by default.
    Negative control: drop the two settings from flue_env -> red."""
    f = hs.flue_env({"ZOE_BRAIN_OPEN": "1", "ZOE_LISTEN_HOST": "0.0.0.0"}, root=Path("/tmp/x"), port=1, data_url="http://127.0.0.1:2",
                    token="t", brain_token="b")
    assert f["ZOE_BRAIN_OPEN"] == "0" and f["ZOE_LISTEN_HOST"] == "127.0.0.1"
    assert f["NODE_OPTIONS"] == "--import=" + str(REPO / "scripts" / "lib" / "node_listen_host.mjs")


def test_the_scratch_stack_runs_this_checkouts_code_not_the_settings_dirs(tmp_path, monkeypatch):
    """The settings dir may be the MAIN checkout (a worktree has no .env); the migrations, zoe-data and Flue it runs are the candidate's.
    Negative control: spawn with cwd=service_dir again -> red."""
    settings = tmp_path / "main" / "services" / "zoe-data"
    settings.mkdir(parents=True)
    code = tmp_path / "wt" / "services" / "zoe-data"
    (tmp_path / "wt" / "labs" / "flue-zoe-brain-2x" / "dist").mkdir(parents=True)
    (tmp_path / "wt" / "labs" / "flue-zoe-brain-2x" / "dist" / "server.mjs").write_text("")
    code.mkdir(parents=True)
    spawned = []
    monkeypatch.setattr(hs.shutil, "which", lambda *a, **k: "/usr/bin/node")
    monkeypatch.setattr(hs, "parse_env_file", lambda p: {})
    monkeypatch.setattr(hs, "flue_env", lambda *a, **k: {})
    monkeypatch.setattr(hs, "harness_env", lambda *a, **k: {"POSTGRES_URL": "postgresql://scratch"})
    monkeypatch.setattr(hs, "assert_not_live", lambda *a, **k: None)
    monkeypatch.setattr(hs, "_write_state", lambda st: None)
    monkeypatch.setattr(hs, "_wait", lambda *a, **k: None)
    monkeypatch.setattr(hs, "free_port", iter(range(5001, 5010)).__next__)
    monkeypatch.setattr(hs, "_spawn", lambda cmd, **kw: spawned.append((cmd, kw["cwd"])) or {"pid": 1, "start": 1, "cmd": []})
    root = tmp_path / "root"
    root.mkdir()
    hs._serve({"tag": "t", "root": str(root), "dsn": "postgresql://scratch", "procs": {}}, service_dir=settings, live_env={},
              python="python3", log=lambda *_: None, code_dir=code)
    assert [cwd for _c, cwd in spawned] == [code.parent.parent / "labs" / "flue-zoe-brain-2x", code]
    assert 'code_dir = REPO / "services" / "zoe-data"' in Path(hs.__file__).read_text()


def test_a_refused_run_starts_nothing_in_the_nightly_window_or_during_a_deploy(bar, monkeypatch):
    """Every gate runs BEFORE Postgres/zoe-data/the brain warm-up start. Negative control: drop either check from scratch_stack_for -> red."""
    sb, svc = bar
    fake = _FakeHS()
    monkeypatch.setattr(sb, "_harness_scratch", lambda: fake)
    monkeypatch.setattr(sb, "harness_quiet_refusal", lambda name=None: None)
    monkeypatch.setattr(sb, "in_nightly_window", lambda now: True)
    monkeypatch.setattr(sb, "deploy_in_progress", lambda: (False, "0"))
    stack, refusal = sb.scratch_stack_for(svc, "samantha_day_sim", {}, print)
    assert stack is None and "nightly window" in refusal and fake.started == []
    monkeypatch.setattr(sb, "in_nightly_window", lambda now: False)
    monkeypatch.setattr(sb, "deploy_in_progress", lambda: (True, "1 run"))
    stack, refusal = sb.scratch_stack_for(svc, "samantha_person", {}, print)
    assert stack is None and "deploy check" in refusal and fake.started == []
    monkeypatch.setattr(sb, "deploy_in_progress", lambda: (None, "gh unavailable"))
    stack, refusal = sb.scratch_stack_for(svc, "samantha_person", {}, print)
    assert stack is None and "deploy check" in refusal and fake.started == []


def test_the_scratch_instance_does_not_wait_for_a_stt_model_it_cannot_load():
    """The empty scratch model cache makes Moonshine's warm-up fail and /readyz stay red, so all three text harnesses would time out.
    Negative control: drop ZOE_STT_DISABLED from harness_env -> red."""
    env = hs.harness_env(LIVE_ENV, root=Path("/tmp/x"), dsn="postgresql://s", token="t", brain_token="b",
                         flue_url="http://127.0.0.1:1", self_url="http://127.0.0.1:2")
    assert env["ZOE_STT_DISABLED"] == "1"


@pytest.mark.parametrize("fname", sorted(HARNESSES))
def test_revision_and_cleanliness_come_from_the_checkout_actually_served(fname, bar):
    """The scratch stack runs THIS checkout's code; service_dir (settings) may be the main checkout in a linked worktree.
    Negative control: service_revision(service_dir) in a harness main -> red."""
    sb, _svc = bar
    assert sb.code_checkout_dir() == hs.REPO / "services" / "zoe-data"
    src = _main_source(fname)
    assert "service_revision(service_dir)" not in src and "load_source_fallback_markers(service_dir)" not in src
    assert "service_revision(" in src and "code_checkout_dir()" in src


def test_up_marks_the_scratch_palace_so_the_live_store_guard_treats_it_as_throwaway():
    """Negative control: drop the sentinel write in up() -> red (the guard would classify the palace live)."""
    guard = (Path(__file__).resolve().parents[2] / "services" / "zoe-data" / "live_store_guard.py").read_text()
    assert 'SCRATCH_SENTINEL = ".zoe-harness-scratch"' in guard
    assert '(root / "palace" / ".zoe-harness-scratch").write_text' in Path(hs.__file__).read_text()


def _fake_docker(monkeypatch, *, rm_rc, ps_rc, ps_out):
    def run(cmd, **kw):
        if cmd[:2] == ["docker", "ps"]:
            return types.SimpleNamespace(returncode=ps_rc, stdout=ps_out, stderr="")
        return types.SimpleNamespace(returncode=rm_rc if cmd[:2] == ["docker", "rm"] else 0, stdout="", stderr="")
    monkeypatch.setattr(hs, "_run", run)


def test_down_keeps_the_state_until_removal_is_confirmed_so_the_sweep_can_retry(tmp_path, monkeypatch):
    """Codex P2 on #2165. Negative control: unlink the state unconditionally -> red."""
    monkeypatch.setattr(hs, "STATE_DIR", tmp_path)
    tag = "samantha-bar-999999999"
    (tmp_path / f"{tag}.json").write_text(json.dumps({"tag": tag, "container": "c", "network": "n", "root": str(tmp_path / tag)}))
    _fake_docker(monkeypatch, rm_rc=1, ps_rc=0, ps_out="abc123\n")                 # docker rm failed: the container is still listed
    assert hs.down(tag, log=lambda m: None) is False
    assert (tmp_path / f"{tag}.json").exists()                                        # the state survives for a retry
    assert hs.sweep_stale(lambda m: None) == []                                       # and a failed teardown is not reported as swept
    _fake_docker(monkeypatch, rm_rc=0, ps_rc=0, ps_out="")                            # the retry succeeds
    assert hs.sweep_stale(lambda m: None) == [tag] and not (tmp_path / f"{tag}.json").exists()


def test_down_keeps_the_state_when_docker_cannot_answer_the_listing(tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "STATE_DIR", tmp_path)
    (tmp_path / "t9.json").write_text(json.dumps({"tag": "t9", "container": "c", "network": "n", "root": str(tmp_path / "t9")}))
    _fake_docker(monkeypatch, rm_rc=1, ps_rc=1, ps_out="")
    assert hs.down("t9", log=lambda m: None) is False and (tmp_path / "t9.json").exists()


def test_the_scratch_path_carries_the_directories_of_pi_and_node(monkeypatch):
    """Codex P2 on #2165: a core-backed scratch instance spawns ``pi`` (an NVM node script). Negative control: the fixed PATH -> red."""
    found = {"pi": "/home/u/.nvm/versions/node/v22/bin/pi", "node": "/home/u/.nvm/versions/node/v22/bin/node"}
    monkeypatch.setattr(hs.shutil, "which", lambda name, *a, **k: found.get(name))
    env = hs.harness_env(LIVE_ENV, root=Path("/tmp/x"), dsn="postgresql://s", token="t", brain_token="b",
                         flue_url="http://127.0.0.1:1", self_url="http://127.0.0.1:2")
    parts = env["PATH"].split(":")
    assert parts[0] == "/home/u/.nvm/versions/node/v22/bin" and parts.count("/home/u/.nvm/versions/node/v22/bin") == 1
    assert parts[-3:] == ["/usr/local/bin", "/usr/bin", "/bin"]
