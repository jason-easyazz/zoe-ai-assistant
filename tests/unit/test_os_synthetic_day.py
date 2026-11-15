"""The Zoe OS synthetic day: generator coverage + vocabulary, the driver against a local fake server, and the report's pass mark
(scripts/maintenance/os_synthetic_day.py, os_synthetic_day_report.py)."""
import importlib.util
import json
import re
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe
MAINT = Path(__file__).resolve().parents[2] / "scripts" / "maintenance"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, MAINT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


drv, rpt = _load("os_synthetic_day"), _load("os_synthetic_day_report")
PLANTED = "PLANTED_REPLY_TEXT_zq9"


# ---- the generator -------------------------------------------------------------------------------------------------------------------
def test_deterministic_for_a_seed_and_covers_every_verb_class_role():
    a, b, c = drv.build_plan(7), drv.build_plan(7), drv.build_plan(8)
    assert a == b and a != c
    assert [r["id"] for r in a] == [f"r{i + 1:04d}" for i in range(len(a))]
    for verb in drv.VERBS[:4]:
        assert drv.planned_acts(a, verb, "kernel") >= 250, verb
        assert sum(1 for r in a if r["verb"] == verb and r["cls"] == "refuse" and r["role"] != "guest") > 0
    assert all(drv.planned_acts(a, v, "not_expressible") > 0 for v in ("reminder_create", "alarm_create"))
    assert {r["role"] for r in a} == set(drv.ROLES) and {r["cls"] for r in a} == set(drv.CLASSES)
    assert all(r["cls"] in ("refuse", "none") for r in a if r["role"] == "guest")
    s = drv.plan_summary(a)
    assert s["requests"] == len(a) and sum(s["by_role"].values()) == len(a)


@pytest.mark.parametrize("seed", range(6))
def test_no_template_emits_anything_outside_the_synthetic_vocabulary(seed):
    for r in drv.build_plan(seed):
        assert set(drv.TOKEN.findall(r["text"].lower())) <= drv.ALLOWED | {t for t in drv.TOKEN.findall(r["text"]) if t.isdigit()}, r["text"]


def test_the_vocabulary_is_invented_not_real():
    words = [w for g in (drv.ADJ, drv.NOUN, drv.CRITTER) for w in g] + [w for g in (drv.LISTS, drv.NEVER_MADE) for x in g for w in x.split()]
    assert all(re.fullmatch(r"[a-z]+", w) for w in words)
    real = {"john", "mary", "alex", "sam", "google", "amazon", "sydney", "london", "paris", "zoe"}
    assert not real & set(words) and not real & drv.ALLOWED


# ---- the driver against a fake server ------------------------------------------------------------------------------------------------
class Fake(ThreadingHTTPServer):
    def __init__(self, **kw):
        super().__init__(("127.0.0.1", 0), Handler)
        self.kw, self.now, self.max, self.calls, self.owned, self.lock, self.forgot = kw, 0, 0, [], {}, threading.Lock(), set()
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _go(self):
        srv, path = self.server, self.path
        uid = self.headers.get("X-Zoe-User-Id", "")
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        with srv.lock:
            srv.now += 1
            srv.max = max(srv.max, srv.now)
            srv.calls.append((self.command, path.split("?")[0], uid))
        time.sleep(srv.kw.get("delay", 0.01))
        out = {}
        if path.startswith("/api/chat"):
            srv.owned.setdefault(uid, {"lists": 1, "alarms": 1})
            out = {"response": PLANTED}
            if srv.kw.get("boom"):
                self.send_response(500)
                self.end_headers()
                with srv.lock:
                    srv.now -= 1
                return
        elif self.command == "GET" and path.startswith("/api/lists/shopping"):
            out = {"lists": [{"id": 5, "user_id": uid}] * srv.owned.get(uid, {}).get("lists", 0)}
        elif self.command == "GET" and path.startswith("/api/alarms"):
            out = {"alarms": [{"id": 6, "owner": uid}] * srv.owned.get(uid, {}).get("alarms", 0)}
        elif self.command == "DELETE" and not srv.kw.get("stuck"):
            srv.owned[uid] = {"lists": 0, "alarms": 0}
        elif "forget-synthetic" in path:
            first = uid not in srv.forgot
            srv.forgot.add(uid)
            out = {"removed": 2 if first else 0}
        body = json.dumps(out).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)
        with srv.lock:
            srv.now -= 1

    do_GET = do_POST = do_DELETE = _go


@pytest.fixture
def fake():
    servers = []

    def make(**kw):
        servers.append(Fake(**kw))
        return servers[-1]

    yield make
    for s in servers:
        s.shutdown()


def args(tmp_path, srv, *extra):
    return drv.parse_args(["run", "--state", str(tmp_path / "s.json"), "--out", str(tmp_path / "r.jsonl"), "--base-url", srv.url, "--pace", "0.05",
                           "--poll", "0.01", "--batch", "2", "--skip-roster-check", *extra])


def reqs(tmp_path):
    return [json.loads(x) for x in (tmp_path / "r.jsonl").read_text().splitlines() if '"type": "req"' in x]


def test_paces_never_overlaps_and_cleans_up(fake, tmp_path):
    srv = fake()
    t0 = time.monotonic()
    rc = drv.run(args(tmp_path, srv, "--max-count", "6"), guards=lambda: None)
    assert rc == 0 and time.monotonic() - t0 >= 0.25 and srv.max == 1
    assert len([r for r in reqs(tmp_path) if r["id"].startswith("r")]) == 6
    assert sum(1 for c in srv.calls if "forget-synthetic" in c[1]) == 6          # 3 users, forgotten then verified with a second forget
    assert any(r["id"] == "fin_forget" for r in reqs(tmp_path))
    assert json.loads((tmp_path / "s.json").read_text())["erased"] is True


def test_hard_stop_ends_early_and_resume_continues_from_the_checkpoint(fake, tmp_path):
    srv = fake()
    assert drv.run(args(tmp_path, srv, "--hard-stop", "0.2", "--cleanup", "never"), guards=lambda: None) == 0
    first = [r["id"] for r in reqs(tmp_path)]
    assert 2 <= len(first) <= 6 and not any("forget-synthetic" in c[1] for c in srv.calls)
    assert drv.run(args(tmp_path, srv, "--max-count", "3", "--cleanup", "never"), guards=lambda: None) == 0
    ids = [r["id"] for r in reqs(tmp_path)]
    assert ids == [f"r{i + 1:04d}" for i in range(len(ids))] and len(ids) == len(first) + 3     # contiguous: nothing repeated or skipped


def test_pauses_on_a_guard_and_never_sends_while_it_holds(fake, tmp_path):
    srv, seen = fake(), iter(["household_talking", "night_busy", "memory", None, None, None, None])
    assert drv.run(args(tmp_path, srv, "--max-count", "2"), guards=lambda: next(seen, None)) == 0
    pauses = [json.loads(x)["why"] for x in (tmp_path / "r.jsonl").read_text().splitlines() if '"type": "pause"' in x]
    assert pauses == ["household_talking", "night_busy", "memory"]


def test_cleanup_runs_after_an_exception_and_a_failed_cleanup_is_non_zero(fake, tmp_path, capsys):
    srv = fake()

    def boom():
        raise RuntimeError("guard crashed")

    assert drv.run(args(tmp_path, srv), guards=boom) == 1
    assert any("forget-synthetic" in c[1] for c in srv.calls)
    stuck = fake(stuck=True)
    tmp2 = tmp_path / "b"
    tmp2.mkdir()
    assert drv.run(args(tmp2, stuck, "--max-count", "1"), guards=lambda: None) == 1
    err = capsys.readouterr().err
    assert "INCOMPLETE demo_osd_" in err and "left=" in err and PLANTED not in err


def test_results_and_state_hold_no_request_or_reply_text(fake, tmp_path):
    srv = fake()
    drv.run(args(tmp_path, srv, "--max-count", "8"), guards=lambda: None)
    blob = (tmp_path / "r.jsonl").read_text() + (tmp_path / "s.json").read_text()
    assert PLANTED not in blob
    for r in drv.build_plan(drv.parse_args(["run"]).seed)[:20]:
        assert r["text"] not in blob


def test_roster_check_refuses_demo_ids_the_kernel_does_not_know(fake, tmp_path):
    srv = fake()
    a = args(tmp_path, srv)
    a.skip_roster_check, a.roster_file = False, str(tmp_path / "people.json")
    assert drv.run(a, guards=lambda: None) == 2 and not srv.calls


def test_guard_primitives(tmp_path):
    assert drv.night_clear("false") and not drv.night_clear("true") and not drv.night_clear("/nonexistent/nope")
    now = time.time()
    fmt = lambda t, lg: f"{datetime.fromtimestamp(t, timezone.utc):%Y-%m-%dT%H:%M:%S%z} INFO     {lg}: BODY_TEXT_NEVER_READ\n"   # noqa: E731
    log = tmp_path / "app.log"
    log.write_text(fmt(now - 60, "routers.voice_tts"))
    assert drv.household_talking(str(log), 10, ("routers.voice_tts",), now)
    assert not drv.household_talking(str(log), 10, ("routers.chat",), now)
    log.write_text(fmt(now - 3600, "routers.voice_tts") + fmt(now - 30, "main"))
    assert not drv.household_talking(str(log), 10, ("routers.voice_tts",), now) and not drv.household_talking(str(tmp_path / "x"), 10, ("a",), now)


# ---- the report ------------------------------------------------------------------------------------------------------------------------
T0 = 1_800_000_000.0


def stamp(t):
    return f"{datetime.fromtimestamp(t, timezone.utc):%Y-%m-%dT%H:%M:%S%z}"


def shadow(t, verb, result="match", reason="-", hop=2.0, kernel="verified"):
    return (f"{stamp(t)} INFO     os_shadow: OS_SHADOW_ACT verb={verb} src=spoken cls=shopping live=added kernel={kernel} result={result} "
            f"reason={reason} hop_ms={hop} q_ms=0.1\n")


def fixture(tmp_path, tweak=None, extra=()):
    plan = drv.build_plan(3)
    rows = [{"type": "meta", "seed": 3, "run_id": "x", "started": T0, "requests": len(plan)}]
    rows += [{"type": "req", "id": r["id"], "kind": r["kind"], "verb": r["verb"], "cls": r["cls"], "role": r["role"], "acts": r["acts"],
              "outcome": "ok", "ms": 100, "t": T0 + i} for i, r in enumerate(plan[:300])]
    rows += [{"type": "req", "id": "fin_forget", "verb": "none", "outcome": "ok", "ms": 5, "t": T0 + 400}]
    rows += [{"type": "cleanup", "role": r, "t": T0 + 410, "forget_http": 200, "removed": 1, "left": {"lists": 0}, "second_http": 200,
              "second_removed": 0} for r in ("adult_a", "adult_b", "child")]
    lines, t = [], T0 + 5
    for verb in drv.VERBS[:4]:
        lines += [shadow(t + i * 0.1, verb) for i in range(260)]
        ne = drv.planned_acts(plan, verb, "not_expressible")
        lines += [shadow(t + 30 + i * 0.1, verb, "not_expressible", "recurring") for i in range(ne)]
    lines += list(extra)
    if tweak:
        lines = tweak(lines)
    (tmp_path / "r.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (tmp_path / "log").write_text("".join(lines))
    return rpt.report(str(tmp_path / "r.jsonl"), [str(tmp_path / "log")], {})


def test_report_passes_a_clean_day_and_fails_each_condition_by_name(tmp_path):
    ok = fixture(tmp_path)
    assert ok["pass"] and all(v["judged"] == 260 for v in ok["verbs"].values())
    few = fixture(tmp_path, lambda ls: [x for x in ls if not ("verb=alarm_create" in x and "result=match" in x)] + [shadow(T0 + 9, "alarm_create")] * 150)
    assert not few["pass"] and any("judged" in f for f in few["verbs"]["alarm_create"]["fails"])
    bad = fixture(tmp_path, extra=[shadow(T0 + 50, "list_add", "kernel_refused_live_did", "no_grant", kernel="refused")] * 3)
    assert any(f.startswith("agreement") for f in bad["verbs"]["list_add"]["fails"]) and any("unexplained" in f for f in bad["verbs"]["list_add"]["fails"])
    assert bad["disagreement_request_ids"]["list_add"] and all(i.startswith("r") for i in bad["disagreement_request_ids"]["list_add"])
    one = fixture(tmp_path, extra=[shadow(T0 + 50, "list_remove", "item_differs")])
    assert one["verbs"]["list_remove"]["fails"] == ["unexplained disagreement: item_differs:-"]
    slow = fixture(tmp_path, lambda ls: [x.replace("hop_ms=2.0", "hop_ms=12.0") if "verb=reminder_create" in x else x for x in ls])
    assert any("hop p90" in f for f in slow["verbs"]["reminder_create"]["fails"])
    down = fixture(tmp_path, extra=[shadow(T0 + 50, "alarm_create", "kernel_unavailable", "circuit", 0.0, "none")] * 9)
    assert any("unavailable" in f for f in down["verbs"]["alarm_create"]["fails"])
    assert any("forget NOT confirmed" in f for f in fixture(tmp_path, extra=[f"{stamp(T0 + 60)} ERROR    os_shadow: OS_SHADOW forget NOT confirmed code=X\n"])["overall_fails"])


def test_an_unknown_result_code_counts_as_a_disagreement_and_old_evidence_is_outside_the_window(tmp_path):
    r = fixture(tmp_path, extra=[shadow(T0 + 50, "list_add", "brand_new_code")] * 4 + [shadow(T0 - 9999, "list_add", "item_differs")] * 5)
    assert r["verbs"]["list_add"]["disagree"] == 4 and any("unknown:-" in f for f in r["verbs"]["list_add"]["fails"])
    assert rpt.report(str(tmp_path / "r.jsonl"), [str(tmp_path / "log")], {"unknown:-": "planted"})["verbs"]["list_add"]["fails"] == [
        f for f in r["verbs"]["list_add"]["fails"] if "unexplained" not in f]
