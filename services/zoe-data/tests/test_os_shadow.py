"""The Zoe OS list-add shadow client (os_shadow.py): every real list add is ALSO proposed to a kernel, off the turn.

The kernel here is a FAKE speaking the v1 socket protocol from its documented spec (length-prefixed JSON, HMAC handshake, ops
publish / act / forget): in-process, a unix socket in a temp dir, nothing from ``labs/`` imported. The contract against the real
lab kernel is ``test_os_shadow_contract.py``. Names and items below are invented.
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import json
import logging
import queue
import secrets
import shutil
import socket
import struct
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

import os_shadow
import user_erase_gate

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
MEMBER = "member-a"
ITEM = "oat milk"


# ---- a fake kernel ---------------------------------------------------------------------------------------------------------------------
def _send(sock, obj):
    body = json.dumps(obj, separators=(",", ":")).encode()
    sock.sendall(struct.pack(">I", len(body)) + body)


def _recv_n(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError
        buf += chunk
    return buf


def _recv(sock):
    (n,) = struct.unpack(">I", _recv_n(sock, 4))
    return json.loads(_recv_n(sock, n))


class FakeKernel:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="osk"))
        self.path = str(self.dir / "k.sock")
        self.creds = {s: secrets.token_bytes(32) for s in ("voice", "brain", "panel")}
        self.calls: list = []                  # (skill, op, args)
        self.connections = 0
        self.act_mode = "ok"
        self.forget_mode = "ok"
        self.known = None                      # the members the kernel knows (None = everyone): a stranger's forget is refused
        self.reply_mode = "ok"
        self.close_on = None
        self.old_kernel = False                        # a kernel from before remind.set took due_at
        self.delay = 0.0
        self.stall: dict = {}                  # (stage, connection number) -> seconds the kernel sits on that stage
        self._seq = 0
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(8)
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self._stop:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._serve, args=(conn, self.connections), daemon=True).start()

    def _wait(self, stage, n):
        if self.stall.get((stage, n)):
            time.sleep(self.stall[(stage, n)])

    def _serve(self, conn, n=0):
        try:
            nonce = secrets.token_bytes(32)
            self._wait("hello", n)
            _send(conn, {"v": 1, "nonce": nonce.hex()})
            hello = _recv(conn)
            skill = hello.get("skill")
            want = hmac.new(self.creds.get(skill, b"x"), nonce, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(want, str(hello.get("proof"))):
                _send(conn, {"v": 1, "id": None, "ok": False, "error": {"code": "unauthenticated", "reason": "bad credential"}})
                return
            self._wait("auth", n)
            _send(conn, {"v": 1, "ok": True})
            while True:
                req = _recv(conn)
                self.calls.append((skill, req["op"], req["args"]))
                if self.delay:
                    time.sleep(self.delay)
                self._wait(req["op"], n)
                if req["op"] == self.close_on:
                    raise ConnectionError                  # accepted, authenticated, then closed mid-request
                self._answer(conn, skill, req)
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            conn.close()

    def _frame(self, req, body):
        """The reply envelope; ``reply_mode`` corrupts the ACT reply's: the previous request's id, no id, another version."""
        out = {"v": 1, "id": req["id"], **body}
        if req["op"] == "act" and self.reply_mode == "stale_id":
            out["id"] = req["id"] - 1
        elif req["op"] == "act" and self.reply_mode == "no_id":
            del out["id"]
        elif req["op"] == "act" and self.reply_mode == "wrong_v":
            out["v"] = 2
        return out

    def _ok(self, conn, req, result):
        _send(conn, self._frame(req, {"ok": True, "result": result}))

    def _refuse(self, conn, req, reason):
        _send(conn, self._frame(req, {"ok": False, "error": {"code": "refused", "reason": reason}}))

    def _answer(self, conn, skill, req):
        op, a = req["op"], req["args"]
        if op == "publish":
            self._seq += 1
            return self._ok(conn, req, {"seq": self._seq, "kind": a["kind"], "subject": a.get("subject")})
        if op == "forget":
            if self.known is not None and a.get("subject") not in self.known:
                return self._refuse(conn, req, "only a person can ask to forget")     # the lab kernel's words for a stranger
            if self.forget_mode == "refuse_all":
                return self._refuse(conn, req, "not theirs to forget")
            if self.forget_mode == "refuse_scoped" and ("entity" in a or "topic" in a):
                return self._refuse(conn, req, "nothing to match")
            if self.forget_mode.startswith("reply:"):          # the forget's result as raw JSON text
                body = ('{"v":1,"id":%d,"ok":true,"result":%s}' % (req["id"], self.forget_mode.split(":", 1)[1])).encode()
                return conn.sendall(struct.pack(">I", len(body)) + body)
            return self._ok(conn, req, {"count": 3, "upto": self._seq})
        if op != "act":
            return self._refuse(conn, req, "unknown op")
        mode = self.act_mode
        if mode.startswith("refuse"):
            return self._refuse(conn, req, mode.split(":", 1)[1] if ":" in mode else "no grant from that person")
        if mode == "malformed":
            conn.sendall(b"\x00\x00\x00\x05junk!")
            return
        if mode == "close":
            raise ConnectionError
        if a["action"] == "remind.set":                   # the kernel's check_params (labs/zoe-os/zoe_os/receipts.py)
            known = {"note", "in_minutes", "asked"} | (set() if self.old_kernel else {"due_at"})
            if set(a["args"]) - known:
                return self._refuse(conn, req, "unknown argument")      # an older kernel: due_at is an unknown argument
            if ("in_minutes" in a["args"]) == ("due_at" in a["args"]):
                return self._refuse(conn, req, "needs exactly one of its alternative arguments")
        if mode.startswith("raw_result:"):                # the act's result as raw JSON text (NaN, Infinity, 1e400 ...)
            body = ('{"v":1,"id":%d,"ok":true,"result":%s}' % (req["id"], mode.split(":", 1)[1])).encode()
            return conn.sendall(struct.pack(">I", len(body)) + body)
        if mode == "error_code":                         # an error envelope whose code is free text from the kernel
            return _send(conn, self._frame(req, {"ok": False, "error": {"code": self.error_code, "reason": self.error_code}}))
        if mode == "wrong_shape":
            return self._ok(conn, req, {"hello": 1})
        # the read-back each lab driver returns (labs/zoe-os/zoe_os/brain/drivers.py): list -> item, reminder -> note, alarm -> time.
        # A reminder's read-back says when, as the STORED absolute instant (due_at as given, or the fake kernel's clock at receipt + the
        # minutes), like the kernel's ``Reminders.added``; an older kernel's read-back has the note only.
        pkey, akey = {"remind.set": ("note", "note"), "alarm.set": ("alarm", "time")}.get(a["action"], ("item", "item"))
        proof = {pkey: a["args"][akey]}
        if a["action"] == "remind.set" and not self.old_kernel:
            proof["due_at"] = a["args"]["due_at"] if "due_at" in a["args"] else int(os_shadow._wall()) + 60 * a["args"]["in_minutes"]
        verified, outcome, member = True, "verified", a["on_behalf_of"]
        if mode == "unverified":
            verified, outcome, proof = False, "unverified", None
        elif mode == "uncertain":
            verified, outcome, proof = False, "uncertain", None
        elif mode == "item_differs":
            proof[pkey] = a["args"][akey] + " x"
        elif mode == "wrong_time":                      # the WHEN differs, the WHAT is right
            if a["action"] == "remind.set":
                proof["due_at"] += 300
            elif a["action"] == "alarm.set":
                proof["alarm"] = "23:59" if a["args"]["time"] != "23:59" else "00:00"
        elif mode == "late_by_delay" and "due_at" in proof:           # minutes taken from the commit, not the send
            proof["due_at"] += self.late_s
        elif mode == "no_schedule":
            proof.pop("due_at", None)
        elif mode == "wrong_member":
            member = "someone-else"
        elif mode == "no_member":
            member = None
        elif mode == "list_differs":
            proof["list"] = "somewhere else"
        elif mode == "no_proof_item":
            proof = {"other": 1}
        self._ok(conn, req, {"id": f"r{self._seq}", "seq": self._seq + 1, "action": a["action"], "skill": skill,
                             "on_behalf_of": member, "grant_id": "g1", "proof": proof, "verified": verified,
                             "outcome": outcome, "reversible": True})

    def close(self):
        self._stop = True
        self.srv.close()
        shutil.rmtree(self.dir, ignore_errors=True)


@pytest.fixture
def kernel(monkeypatch):
    k = FakeKernel()
    state = Path(tempfile.mkdtemp(prefix="oss"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [MEMBER]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
    monkeypatch.delenv("ZOE_OS_SHADOW_VERBS", raising=False)       # the default: list add only
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    os_shadow._reset_for_tests()
    assert os_shadow.revive()                                       # what start-up does: the worker is up before any turn
    user_erase_gate.reset()
    k.state = state
    yield k
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.close()
    shutil.rmtree(state, ignore_errors=True)


def settle(total, timeout=4.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if sum(os_shadow.counters().values()) >= total:
            return
        time.sleep(0.01)
    raise AssertionError(f"counters never reached {total}: {os_shadow.counters()}")


def add(item=ITEM, **kw):
    kw.setdefault("source", "spoken")
    kw.setdefault("list_type", "shopping")
    kw.setdefault("list_name", "Shopping")
    os_shadow.note_add(kw.pop("user", MEMBER), item=item, **kw)


# ---- off: nothing is touched -----------------------------------------------------------------------------------------------------------
def test_flag_off_constructs_nothing_and_touches_no_socket(kernel, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    add()
    add(landed=False)
    time.sleep(0.1)
    assert kernel.connections == 0 and os_shadow.counters() == {} and os_shadow._q.qsize() == 0


def test_an_unknown_mode_is_off(kernel, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "on")        # there is no 'on': the kernel is never authoritative here
    add()
    time.sleep(0.1)
    assert os_shadow.mode() == "off" and kernel.connections == 0


# ---- the comparison ---------------------------------------------------------------------------------------------------------------------
def test_a_real_add_is_recorded_as_an_event_and_a_receipt_and_matches(kernel, caplog):
    caplog.set_level(logging.INFO, logger="os_shadow")
    add()
    settle(1)
    assert os_shadow.counters() == {"match": 1}
    ops = [(s, op) for s, op, _ in kernel.calls]
    assert ops == [("voice", "publish"), ("brain", "act")]            # the surface records the cause; the brain acts on it
    pub, act = kernel.calls[0][2], kernel.calls[1][2]
    assert pub["kind"] == "utterance" and pub["subject"] == MEMBER and pub["payload"] == {"text": os_shadow.UTTERANCE_TEXT}
    assert act["action"] == "list.add" and act["on_behalf_of"] == MEMBER and act["caused_by"] == [1]
    assert act["args"] == {"item": ITEM, "list": "Shopping", "asked": 1}
    (line,) = [r.getMessage() for r in caplog.records if "OS_SHADOW_ACT verb=list_add" in r.getMessage()]
    assert line.startswith("OS_SHADOW_ACT verb=list_add src=spoken cls=shopping live=added kernel=verified result=match reason=- hop_ms=")


@pytest.mark.parametrize("mode,result,kstate,reason", [
    ("refuse", "kernel_refused_live_did", "refused", "no_grant"),
    ("refuse:asks for more than its manifest", "kernel_refused_live_did", "refused", "manifest"),
    ("refuse:wrong argument type", "kernel_refused_live_did", "refused", "params"),
    ("refuse:the cited event is not about that person", "kernel_refused_live_did", "refused", "cause"),
    ("refuse:something new", "kernel_refused_live_did", "refused", "other"),
    ("unverified", "kernel_unverified_live_did", "unverified", "-"),
    ("uncertain", "kernel_unverified_live_did", "uncertain", "-"),
    ("item_differs", "item_differs", "verified", "-"),
    ("list_differs", "list_differs", "verified", "-"),
    ("no_proof_item", "kernel_malformed", "verified", "-"),
    ("wrong_shape", "kernel_malformed", "none", "protocol"),
    ("malformed", "kernel_malformed", "none", "protocol"),
])
def test_each_mismatch_class_is_named_and_logged_as_codes(kernel, caplog, mode, result, kstate, reason):
    caplog.set_level(logging.INFO, logger="os_shadow")
    kernel.act_mode = mode
    add()
    settle(1)
    assert os_shadow.counters() == {result: 1}
    (line,) = [r.getMessage() for r in caplog.records if "OS_SHADOW_ACT verb=list_add" in r.getMessage()]
    assert f"kernel={kstate} result={result} reason={reason} " in line


def test_compare_table_covers_the_live_refused_direction():
    sent = {"item": "a", "list": "L"}
    done = {"verified": True, "outcome": "verified", "proof": {"item": "a"}}
    assert os_shadow.compare("refused", {"refused": "x"}, sent) == ("match", "refused")
    assert os_shadow.compare("refused", done, sent) == ("kernel_did_live_refused", "verified")
    assert os_shadow.compare("added", None, sent) == ("kernel_unavailable", "none")
    assert os_shadow.compare("added", done, sent) == ("match", "verified")


def test_a_dropped_connection_mid_call_is_unavailable_not_a_crash(kernel):
    kernel.act_mode = "close"
    add()
    settle(1)
    assert os_shadow.counters() == {"kernel_unavailable": 1}


# ---- availability, speed, safety -------------------------------------------------------------------------------------------------------
def test_kernel_down_is_one_code_per_add_and_the_circuit_stops_reconnecting(kernel, caplog):
    caplog.set_level(logging.INFO, logger="os_shadow")
    kernel.srv.close()
    Path(kernel.path).unlink()
    for _ in range(3):
        add()
    settle(3)
    assert os_shadow.counters() == {"kernel_unavailable": 3}
    lines = [r.getMessage() for r in caplog.records if "OS_SHADOW_ACT verb=list_add" in r.getMessage()]
    assert len(lines) == 3 and all("kernel=none result=kernel_unavailable" in ln for ln in lines)
    assert "reason=circuit" in lines[1]               # the second add did not even try to connect


def test_a_slow_kernel_times_out_and_the_caller_is_not_held(kernel):
    kernel.delay = 1.0
    t0 = time.perf_counter()
    add()
    assert time.perf_counter() - t0 < 0.05            # the hook only queued it
    settle(1, timeout=5)
    assert os_shadow.counters() == {"timeout": 1}


def test_the_hook_never_raises_and_never_blocks(kernel, monkeypatch):
    monkeypatch.setattr(os_shadow, "_worker_alive", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    add()                                             # swallowed, nothing was queued, and still counted and written: the shadow's own fault
    assert os_shadow._q.qsize() == 0 and os_shadow.counters() == {"shadow_error": 1}


def test_a_full_queue_is_counted_and_never_blocks(kernel, monkeypatch):
    os_shadow._reset_for_tests()                                          # no worker: nothing drains the queue
    monkeypatch.setattr(os_shadow, "_worker_alive", lambda: True)
    for _ in range(os_shadow.QUEUE_MAX + 5):
        add()
    assert os_shadow.counters() == {"queue_full": 5}


def test_a_member_outside_the_roster_is_never_proposed(kernel):
    add(user="someone-else")
    settle(1)
    assert os_shadow.counters() == {"member_not_in_roster": 1} and kernel.calls == []


@pytest.mark.parametrize("entries,expected", [
    ([MEMBER], {MEMBER}),
    ([{"id": MEMBER, "role": "adult"}], {MEMBER}),
    ([{"id": MEMBER, "role": "adult"}, "member-b"], {MEMBER, "member-b"}),
    ([{"id": MEMBER, "role": "adult"}, {}, {"role": "adult"}, {"id": None},
      {"id": 12}, {"id": []}, {"id": "   "}, None, 12, ""], {MEMBER}),
], ids=["plain-id", "object-id", "mixed", "malformed-ignored"])
def test_roster_entries_are_ids_and_listed_members_are_proposed(kernel, entries, expected):
    (kernel.state / "people.json").write_text(json.dumps({"people": entries}))
    os_shadow._roster_cache = (0.0, None, frozenset())
    assert os_shadow.roster() == frozenset(expected)
    for member in sorted(expected):
        add(user=member)
    settle(len(expected))
    assert os_shadow.counters() == {"match": len(expected)}
    assert {args["on_behalf_of"] for _, op, args in kernel.calls if op == "act"} == expected


def test_write_roster_extracts_ids_from_mixed_entries(tmp_path):
    dest = tmp_path / "people.json"
    assert os_shadow.write_roster([
        {"id": MEMBER, "role": "adult"}, " member-b ", MEMBER, {}, {"id": None},
        {"id": 12}, {"id": []}, "", None,
    ], dest) == [MEMBER, "member-b"]
    assert json.loads(dest.read_text()) == {"people": [MEMBER, "member-b"]}
    assert dest.stat().st_mode & 0o777 == 0o600


def test_a_member_erased_after_the_add_was_queued_is_dropped(kernel, monkeypatch):
    kernel.delay = 0.2                                # the first add holds the worker while the erase happens
    add()
    time.sleep(0.05)
    add(item="rice")
    with user_erase_gate.erase_span(MEMBER):
        pass                                          # an erase ran and finished: the queued second add belonged to the world before it
    settle(2, timeout=5)
    assert os_shadow.counters().get("dropped_erased") == 1
    assert all(args.get("args", {}).get("item") != "rice" for _, op, args in kernel.calls if op == "act")


def test_an_add_during_an_erase_is_dropped_at_the_door(kernel):
    with user_erase_gate.erase_span(MEMBER):
        add()
    assert os_shadow.counters() == {"dropped_erased": 1} and os_shadow._q.qsize() == 0
    user_erase_gate.reset()


def test_a_no_op_add_is_counted_and_never_proposed(kernel):
    add(landed=False)
    assert os_shadow.counters() == {"live_noop": 1} and os_shadow._q.qsize() == 0 and kernel.calls == []


def test_empty_member_or_item_is_never_proposed_but_is_counted(kernel):
    add(item="  ")
    add(user="")
    assert os_shadow.counters() == {"no_input": 2} and os_shadow._q.qsize() == 0


def test_the_log_line_carries_no_words(kernel, caplog):
    caplog.set_level(logging.DEBUG)
    kernel.act_mode = "refuse:cannot add Quillon's tangerines"       # a kernel reason is never echoed
    add(item="tangerines for Quillon", list_name="Quillon's list")
    settle(1)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "OS_SHADOW_ACT verb=list_add" in text and not any(w in text for w in ("Quillon", "tangerine"))


def test_the_hook_costs_almost_nothing(kernel, monkeypatch, capsys):
    """THE NUMBER: added time on the caller, per add. Off = one flag read; on = one flag read + one queue put."""
    def p95(n=2000):
        xs = []
        for _ in range(n):
            t = time.perf_counter()
            add()
            xs.append((time.perf_counter() - t) * 1000)
        xs.sort()
        return xs[int(n * 0.95)], xs[n // 2]

    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    off95, off50 = p95()
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
    kernel.delay = 0.0
    on95, on50 = p95()
    print(f"OS_SHADOW_HOOK_COST off p50={off50:.4f}ms p95={off95:.4f}ms | shadow p50={on50:.4f}ms p95={on95:.4f}ms")
    assert off95 < 1.0 and on95 < 5.0                  # the target: under 5 ms at p95 with the flag on, none with it off


# ---- structure pins -----------------------------------------------------------------------------------------------------------------
def _service_files():
    skip = {"tests", "alembic", "node_modules", "__pycache__"}
    return [p for p in SVC.rglob("*.py") if not skip.intersection(p.relative_to(SVC).parts)]


def _lab_imports(source, *, forbid_labs=False, forbid_path=False):
    tree = ast.parse(source)
    imports = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imports |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    path = any(isinstance(n, ast.Attribute) and n.attr == "path" and isinstance(n.value, ast.Name)
               and n.value.id == "sys" for n in ast.walk(tree))
    return bool(imports & ({"zoe_os", "labs"} if forbid_labs else {"zoe_os"}) or forbid_path and path)


def test_zoe_data_never_imports_the_lab_kernel():
    assert not _lab_imports((SVC / "os_shadow.py").read_text("utf-8"), forbid_labs=True, forbid_path=True)
    for p in _service_files():
        assert not _lab_imports(p.read_text("utf-8")), p.name
    for statement in ("import zoe_os", "from zoe_os import Kernel", "import zoe_os as k", "import labs", "sys.path.append('lab')"):
        assert _lab_imports(statement, forbid_labs=True, forbid_path=True)
        assert not _lab_imports("# " + statement + "\n" + repr(statement), forbid_labs=True, forbid_path=True)


def test_the_worker_is_the_only_thread_and_it_is_a_daemon(kernel):
    add()
    settle(1)
    assert os_shadow._worker.daemon and os_shadow._worker.name == "os-shadow"
    assert isinstance(os_shadow._q, queue.Queue) and os_shadow._q.maxsize == os_shadow.QUEUE_MAX


# ---- the worker start never costs the turn ------------------------------------------------------------------------------------------
def test_a_turn_with_a_dead_worker_starts_no_thread_returns_at_once_and_is_counted(kernel, caplog):
    os_shadow._reset_for_tests()                     # the worker died (or the start-up start failed)
    caplog.set_level(logging.WARNING, logger="os_shadow")
    before = {t.ident for t in threading.enumerate()}
    t0 = time.perf_counter()
    add()
    add(item="rice")
    took = time.perf_counter() - t0
    assert {t.ident for t in threading.enumerate()} == before, "a turn started a thread"
    assert os_shadow._worker is None and took < 0.05
    assert os_shadow.counters() == {"worker_down": 2} and os_shadow._q.qsize() == 0 and kernel.calls == []
    assert sum("OS_SHADOW worker down" in r.getMessage() for r in caplog.records) == 1     # said once, not per add
    assert os_shadow.revive()                        # off the turn: start-up or a forget brings it back
    add()
    settle(3)
    assert os_shadow.counters() == {"worker_down": 2, "match": 1}


def test_revive_never_waits_on_a_contended_start_and_many_revives_start_one_worker(kernel):
    os_shadow._reset_for_tests()
    assert os_shadow._worker_guard.acquire(timeout=1)
    try:
        t0 = time.perf_counter()
        assert os_shadow.revive() is False           # another caller holds the guard: no wait, no second start
        assert time.perf_counter() - t0 < 0.05
    finally:
        os_shadow._worker_guard.release()
    barrier = threading.Barrier(16)
    callers = [threading.Thread(target=lambda: (barrier.wait(), os_shadow.revive()), daemon=True) for _ in range(16)]
    for c in callers:
        c.start()
    for c in callers:
        c.join(2)
    assert [t.name for t in threading.enumerate()].count("os-shadow") == 1


def test_no_turn_path_calls_the_worker_start():
    """Structure pin: the hook functions never call _ensure_worker / revive / Thread (only start-up and the forget path may)."""
    tree = ast.parse((SVC / "os_shadow.py").read_text("utf-8"))
    hooks = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and (n.name.startswith("note_") or n.name in ("_note", "_door"))]
    assert hooks
    for fn in hooks:
        called = {getattr(c.func, "id", getattr(c.func, "attr", "")) for c in ast.walk(fn) if isinstance(c, ast.Call)}
        assert not called & {"_ensure_worker", "revive", "Thread", "start"}, fn.name


def _import_start_stmt(source):
    """The statement that runs when the module is IMPORTED and is the last thing it does: the module's last statement, or, when
    that is the ``if __name__ == "__main__": ... else: ...`` guard, the single statement of its ``else`` (the import branch)."""
    last = ast.parse(source).body[-1]
    if (isinstance(last, ast.If) and isinstance(last.test, ast.Compare) and isinstance(last.test.left, ast.Name)
            and last.test.left.id == "__name__" and len(last.orelse) == 1):
        return last.orelse[0]
    return last


def _ends_with_import_start(source):
    last = _import_start_stmt(source)
    return (isinstance(last, ast.Expr) and isinstance(last.value, ast.Call)
            and isinstance(last.value.func, ast.Name) and last.value.func.id == "_start_at_import"
            and not last.value.args and not last.value.keywords)


def test_the_worker_starts_at_import_while_shadow_so_no_turn_starts_it(kernel):
    os_shadow._reset_for_tests()
    assert os_shadow._worker is None
    os_shadow._start_at_import()
    assert os_shadow._worker is not None and os_shadow._worker.is_alive()
    src = (SVC / "os_shadow.py").read_text("utf-8")
    assert _ends_with_import_start(src)
    lines = src.splitlines(keepends=True)
    last = _import_start_stmt(src)
    pad = " " * last.col_offset
    for replacement in ("pass  # _start_at_import()", repr("_start_at_import()")):       # the call gone, its name kept in text
        mutated = ("".join(lines[:last.lineno - 1]) + pad + replacement
                   + "  # service start-up, never a turn (see _ensure_worker)\n" + "".join(lines[last.end_lineno:]))
        ast.parse(mutated)                                    # the mutation is valid Python, so the check below is a real refusal
        assert not _ends_with_import_start(mutated)


def test_the_import_start_is_a_no_op_with_the_flag_off(kernel, monkeypatch):
    os_shadow._reset_for_tests()
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    os_shadow._start_at_import()
    assert os_shadow._worker is None


def test_a_forgotten_refusal_is_classed_forgotten_even_when_it_names_arguments():
    """The kernel's 'forgotten_action_arguments' also contains 'argument': first match must be 'forgotten' (final), never 'params'."""
    assert os_shadow.reason_code("forgotten_action_arguments") == "forgotten"
    assert os_shadow.reason_code("refused: forgotten_action_arguments") == "forgotten"
    assert os_shadow.reason_code("wrong argument type") == "params"
    assert os_shadow.reason_code("no grant from that person") == "no_grant"


# ---- every outcome is ONE line, and the closed set is exactly what can be written --------------------------------------------------
def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("OS_SHADOW_ACT ")]


def _result(line):
    return line.split(" result=", 1)[1].split(" ", 1)[0]


FIELDS = ["verb", "src", "cls", "live", "kernel", "result", "reason", "hop_ms", "q_ms"]


def _sweep(kernel, monkeypatch):
    """Drive every outcome a list add can end in, ONE call each; returns the result each call must have written."""
    want = []

    def one(code, fn, n=1):
        fn()
        want.extend([code] * n)
        settle(len(want))

    one("match", add)
    for mode, code in (("refuse", "kernel_refused_live_did"), ("unverified", "kernel_unverified_live_did"),
                       ("item_differs", "item_differs"), ("list_differs", "list_differs"), ("malformed", "kernel_malformed"),
                       ("wrong_member", "member_differs"), ("no_member", "member_unproven")):
        kernel.act_mode = mode
        one(code, add)
    kernel.act_mode = "ok"
    one("kernel_did_live_refused", lambda: os_shadow._q.put(          # no call site feeds a live refusal yet: the worker can still say it
        ("act", "list_add", MEMBER, "spoken", "shopping", "refused", {"item": ITEM, "list": "Shopping"},
         user_erase_gate.generation(MEMBER), time.perf_counter(), time.time())))
    one("member_not_in_roster", lambda: add(user="someone-else"))
    one("live_noop", lambda: add(landed=False))
    one("no_input", lambda: add(item=" "))
    one("no_input", lambda: add(user=""))
    with user_erase_gate.erase_span(MEMBER):
        one("dropped_erased", add)                                    # at the door
    user_erase_gate.reset()
    one("dropped_erased", lambda: os_shadow._q.put(                   # in the worker: queued before an erase finished
        ("act", "list_add", MEMBER, "spoken", "shopping", "added", {"item": ITEM, "list": "Shopping"}, -1, time.perf_counter(),
         time.time())))
    with monkeypatch.context() as m:
        m.setattr(os_shadow, "_worker_alive", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        one("shadow_error", add)                                      # reason=hook
    with monkeypatch.context() as m:
        m.setattr(os_shadow, "roster", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        one("shadow_error", add)                                      # reason=worker
    with monkeypatch.context() as m:
        m.setattr(os_shadow, "kernel_args", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
        one("not_expressible", add)                                   # a field the worker could not read: reason=parse
    kernel.delay = 1.0
    one("timeout", add)
    kernel.delay = 0.0
    with monkeypatch.context() as m:
        full: queue.Queue = queue.Queue(maxsize=1)
        full.put_nowait(("stop",))
        m.setattr(os_shadow, "_q", full)
        one("queue_full", add)
    os_shadow._q.put(("stop",))                                       # the worker dies (counters kept)
    os_shadow._worker.join(5)
    one("worker_down", add)
    kernel.srv.close()
    Path(kernel.path).unlink()
    assert os_shadow.revive()
    one("kernel_unavailable", add)
    return want


def test_every_note_add_outcome_writes_exactly_one_line(kernel, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="os_shadow")
    want = _sweep(kernel, monkeypatch)
    lines = _lines(caplog)
    assert sorted(_result(ln) for ln in lines) == sorted(want), "an outcome was counted without its line, or written twice"
    assert os_shadow.counters() == dict(Counter(want))                # and every line is counted once
    for ln in lines:                                                  # the same field set, in the same order, on every line
        assert [kv.split("=", 1)[0] for kv in ln.split()[1:]] == FIELDS, ln
    errors = [ln for ln in lines if _result(ln) == "shadow_error"]
    assert sorted(ln.split(" reason=", 1)[1].split()[0] for ln in errors) == ["hook", "worker"]
    when_only = {"schedule_differs", "schedule_unproven"}            # a list add has no WHEN: driven per verb in test_os_shadow_verbs.py
    assert set(want) | when_only == set(os_shadow.RESULTS), set(os_shadow.RESULTS) ^ (set(want) | when_only)


def test_the_flag_off_writes_no_line(kernel, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="os_shadow")
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    add()
    add(landed=False)
    add(item="")
    assert _lines(caplog) == []


def test_a_code_outside_results_is_written_as_shadow_error_not_dropped(kernel, caplog):
    caplog.set_level(logging.INFO, logger="os_shadow")
    os_shadow._finish("list_add", "brand_new_code", source="spoken", cls="shopping", live="added")
    (line,) = _lines(caplog)
    assert " result=shadow_error reason=code " in line and os_shadow.counters() == {"shadow_error": 1}


@pytest.mark.parametrize("stage,conn", [("hello", 1), ("auth", 1), ("publish", 1), ("hello", 2), ("auth", 2), ("act", 2)])
def test_one_deadline_per_add_covers_every_socket_stage(kernel, caplog, monkeypatch, stage, conn):
    """A kernel that sits on ANY stage (connect handshake, auth, publish, the act connection, act) for longer than the add's budget,
    but under the per-operation timeout, ends the add as ``timeout`` WITHIN the budget, with its one line."""
    caplog.set_level(logging.INFO, logger="os_shadow")
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 1.0)              # no single operation would time out on its own
    monkeypatch.setattr(os_shadow, "JOB_DEADLINE_S", 0.2)
    kernel.stall[(stage, conn)] = 0.6
    add()
    settle(1, timeout=3)
    assert os_shadow.counters() == {"timeout": 1}
    (line,) = _lines(caplog)
    hop = float(line.split(" hop_ms=", 1)[1].split()[0])
    assert hop < 350, f"the add ran {hop} ms against a 200 ms budget"


def _module_tree():
    return ast.parse((SVC / "os_shadow.py").read_text("utf-8"))


def _fn(tree, name):
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _consts(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _consts(node.body) | _consts(node.orelse)
    return set()


def test_no_literal_code_outside_results_reaches_the_emitter():
    """Static: every string that can become a result (``_finish``'s first argument, a ``_door`` return, a ``result`` in an outcome
    dict, an assignment to ``result``/``outcome``, ``compare``'s first element) is in RESULTS."""
    tree = _module_tree()
    found = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_finish" and len(n.args) > 1:
            found |= _consts(n.args[1])                               # _finish(verb, result, ...)
        if isinstance(n, ast.Dict):
            found |= {v for k, val in zip(n.keys, n.values) if isinstance(k, ast.Constant) and k.value == "result" for v in _consts(val)}
        if isinstance(n, ast.Assign):
            for t in n.targets:
                names = [t] if isinstance(t, ast.Name) else (t.elts if isinstance(t, ast.Tuple) else [])
                vals = [n.value] if isinstance(t, ast.Name) else (n.value.elts if isinstance(n.value, ast.Tuple) else [])
                for tn, v in zip(names, vals):
                    if getattr(tn, "id", "") in ("result", "outcome"):
                        found |= _consts(v)
    for r in ast.walk(_fn(tree, "_door")):                            # (outcome, cls)
        if isinstance(r, ast.Return) and isinstance(r.value, ast.Tuple):
            found |= _consts(r.value.elts[0])
    for r in ast.walk(_fn(tree, "compare")):
        if isinstance(r, ast.Return) and isinstance(r.value, ast.Tuple):
            found |= _consts(r.value.elts[0])
    found.discard("")                                                 # `result = ""`: "not decided yet", resolved before the emitter
    assert len(found) >= 12 and found <= set(os_shadow.RESULTS), found - set(os_shadow.RESULTS)


def test_every_outcome_path_goes_through_the_one_emitter():
    """Structure: only ``_finish`` counts or logs a write; it is called once in ``_note`` and once in ``_do_act``, each from a
    ``finally``; every return of the outcome functions names an outcome (falling off the end would write nothing)."""
    tree = _module_tree()
    callers: dict = {}
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for c in ast.walk(fn):
            if isinstance(c, ast.Call) and getattr(c.func, "id", "") in ("_tally", "_log", "_finish"):
                callers.setdefault(c.func.id, []).append(fn.name)
    assert callers["_tally"] == ["_finish"] and callers["_log"] == ["_finish"]
    assert sorted(callers["_finish"]) == ["_do_act", "_note"]
    for name in ("_note", "_do_act"):
        tries = [t for t in ast.walk(_fn(tree, name)) if isinstance(t, ast.Try) and t.finalbody]
        assert len(tries) == 1 and any(getattr(c.func, "id", "") == "_finish" for f in tries[0].finalbody for c in ast.walk(f)
                                       if isinstance(c, ast.Call)), name
        assert not [r for r in ast.walk(_fn(tree, name)) if isinstance(r, ast.Return)], f"{name} must have one exit"
    for name in ("_door", "_act_outcome"):
        fn = _fn(tree, name)
        assert isinstance(fn.body[-1], ast.Return) and all(r.value is not None for r in ast.walk(fn) if isinstance(r, ast.Return)), name


def test_every_code_a_verb_check_can_return_is_a_result():
    for action, checks in os_shadow.VERBS.values():
        for _pkey, _akey, differs, missing in checks:
            assert differs in os_shadow.RESULTS and (missing is None or missing in os_shadow.RESULTS), action


# ---- for whom, and whose reply ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode,result", [("wrong_member", "member_differs"), ("no_member", "member_unproven"), ("ok", "match")])
def test_a_verified_receipt_for_another_member_is_never_a_match(kernel, mode, result):
    kernel.act_mode = mode
    add()
    settle(1)
    assert os_shadow.counters() == {result: 1}


@pytest.mark.parametrize("reply_mode", ["stale_id", "no_id", "wrong_v"])
@pytest.mark.parametrize("act_mode", ["ok", "refuse"])
def test_a_reply_for_another_request_or_version_is_never_the_answer(kernel, caplog, reply_mode, act_mode):
    """The act reply carries the previous request's id, no id, or another wire version: never read as this request's answer (a
    match, or a refusal), always ``kernel_malformed reason=crossed``."""
    caplog.set_level(logging.INFO, logger="os_shadow")
    kernel.reply_mode, kernel.act_mode = reply_mode, act_mode
    add()
    settle(1)
    assert os_shadow.counters() == {"kernel_malformed": 1}
    (line,) = _lines(caplog)
    assert " result=kernel_malformed reason=crossed " in line


def test_compare_checks_the_member_only_when_told():
    done = {"verified": True, "outcome": "verified", "proof": {"item": "a"}, "on_behalf_of": "m1"}
    sent = {"item": "a", "list": "L"}
    assert os_shadow.compare("added", done, sent, member="m1") == ("match", "verified")
    assert os_shadow.compare("added", done, sent, member="m2") == ("member_differs", "verified")
    assert os_shadow.for_whom({"verified": True}, "m1") == "member_unproven"


# ---- nothing a caller hands the hook reaches a log line or a counter ------------------------------------------------------------
MARK = "Zq7Marker"


@pytest.mark.parametrize("field", ["source", "item", "list_name", "list_type", "kernel_reason", "kernel_error_code"])
def test_marker_text_from_any_caller_argument_never_reaches_a_log_line_or_a_counter(kernel, caplog, field):
    caplog.set_level(logging.DEBUG)
    kw = {"source": "spoken", "list_type": "shopping", "list_name": "Shopping", "item": "rice"}
    if field in kw:
        kw[field] = f"{MARK} x=1 result=match"                  # text, and a forged key=value pair, through the argument
    elif field == "kernel_reason":
        kernel.act_mode = f"refuse:{MARK} reason=match"
    else:
        kernel.act_mode = "error_code"
        kernel.error_code = MARK
    os_shadow.note_add(MEMBER, landed=True, **kw)
    settle(1)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert MARK not in text and not any(MARK in str(k) for k in os_shadow.counters())
    (line,) = _lines(caplog)
    assert line.count("result=") == 1 and line.count("reason=") == 1
    if field == "source":
        assert " src=other " in line


# ---- unavailability opens the circuit, whatever the stage --------------------------------------------------------------------------
def test_a_kernel_that_accepts_then_closes_mid_request_opens_the_circuit(kernel, caplog):
    caplog.set_level(logging.INFO, logger="os_shadow")
    kernel.close_on = "publish"
    add()
    add()
    settle(2)
    assert os_shadow.counters() == {"kernel_unavailable": 2}
    assert kernel.connections == 1, "the second add reconnected: the circuit stayed closed"
    assert " reason=circuit " in _lines(caplog)[1]


@pytest.mark.parametrize("creds", ["not json", '{"socket": "x"}', '{"socket": "x", "creds": {}}'])
def test_unreadable_or_missing_credentials_open_the_circuit(kernel, monkeypatch, creds):
    (kernel.state / "creds.json").write_text(creds)
    loads = []
    real = os_shadow._load_creds
    monkeypatch.setattr(os_shadow, "_load_creds", lambda: (loads.append(1), real())[1])
    add()
    add()
    settle(2)
    assert os_shadow.counters() == {"kernel_unavailable": 2} and len(loads) == 1


@pytest.mark.parametrize("raw", ['{"verified": NaN, "outcome": "verified"}', '{"verified": true, "outcome": "verified", "n": Infinity}',
                                 '{"verified": true, "outcome": "verified", "n": -Infinity}', '{"verified": true, "outcome": "verified", "n": 1e400}'])
def test_a_reply_with_a_non_standard_number_is_malformed_at_the_decoder(kernel, raw):
    kernel.act_mode = "raw_result:" + raw
    add()
    settle(1)
    assert os_shadow.counters() == {"kernel_malformed": 1}
