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
        self.delay = 0.0
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
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            nonce = secrets.token_bytes(32)
            _send(conn, {"v": 1, "nonce": nonce.hex()})
            hello = _recv(conn)
            skill = hello.get("skill")
            want = hmac.new(self.creds.get(skill, b"x"), nonce, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(want, str(hello.get("proof"))):
                _send(conn, {"v": 1, "id": None, "ok": False, "error": {"code": "unauthenticated", "reason": "bad credential"}})
                return
            _send(conn, {"v": 1, "ok": True})
            while True:
                req = _recv(conn)
                self.calls.append((skill, req["op"], req["args"]))
                if self.delay:
                    time.sleep(self.delay)
                self._answer(conn, skill, req)
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            conn.close()

    def _ok(self, conn, req, result):
        _send(conn, {"v": 1, "id": req["id"], "ok": True, "result": result})

    def _refuse(self, conn, req, reason):
        _send(conn, {"v": 1, "id": req["id"], "ok": False, "error": {"code": "refused", "reason": reason}})

    def _answer(self, conn, skill, req):
        op, a = req["op"], req["args"]
        if op == "publish":
            self._seq += 1
            return self._ok(conn, req, {"seq": self._seq, "kind": a["kind"], "subject": a.get("subject")})
        if op == "forget":
            if self.forget_mode == "refuse_all":
                return self._refuse(conn, req, "not theirs to forget")
            if self.forget_mode == "refuse_scoped" and ("entity" in a or "topic" in a):
                return self._refuse(conn, req, "nothing to match")
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
        if mode == "wrong_shape":
            return self._ok(conn, req, {"hello": 1})
        proof = {"item": a["args"]["item"]}
        verified, outcome = True, "verified"
        if mode == "unverified":
            verified, outcome, proof = False, "unverified", None
        elif mode == "uncertain":
            verified, outcome, proof = False, "uncertain", None
        elif mode == "item_differs":
            proof = {"item": a["args"]["item"] + " x"}
        elif mode == "list_differs":
            proof["list"] = "somewhere else"
        elif mode == "no_proof_item":
            proof = {"other": 1}
        self._ok(conn, req, {"id": f"r{self._seq}", "seq": self._seq + 1, "action": a["action"], "skill": skill,
                             "on_behalf_of": a["on_behalf_of"], "grant_id": "g1", "proof": proof, "verified": verified,
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
    (line,) = [r.getMessage() for r in caplog.records if "OS_SHADOW_LIST_ADD" in r.getMessage()]
    assert line.startswith("OS_SHADOW_LIST_ADD src=spoken cls=shopping live=added kernel=verified result=match reason=- hop_ms=")


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
    (line,) = [r.getMessage() for r in caplog.records if "OS_SHADOW_LIST_ADD" in r.getMessage()]
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
    lines = [r.getMessage() for r in caplog.records if "OS_SHADOW_LIST_ADD" in r.getMessage()]
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
    add()                                             # swallowed, and nothing was queued
    assert os_shadow._q.qsize() == 0


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


def test_empty_member_or_item_is_ignored(kernel):
    add(item="  ")
    add(user="")
    assert os_shadow.counters() == {} and os_shadow._q.qsize() == 0


def test_the_log_line_carries_no_words(kernel, caplog):
    caplog.set_level(logging.DEBUG)
    kernel.act_mode = "refuse:cannot add Quillon's tangerines"       # a kernel reason is never echoed
    add(item="tangerines for Quillon", list_name="Quillon's list")
    settle(1)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "OS_SHADOW_LIST_ADD" in text and not any(w in text for w in ("Quillon", "tangerine"))


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


def test_zoe_data_never_imports_the_lab_kernel():
    src = (SVC / "os_shadow.py").read_text("utf-8")
    imports = {a.name.split(".")[0] for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import) for a in n.names}
    imports |= {n.module.split(".")[0] for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.module}
    assert not ({"zoe_os", "labs"} & imports) and "sys.path" not in src
    for p in _service_files():
        assert "from zoe_os" not in p.read_text("utf-8") and "import zoe_os" not in p.read_text("utf-8"), p.name


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
    hooks = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("note_")]
    assert hooks
    for fn in hooks:
        called = {getattr(c.func, "id", getattr(c.func, "attr", "")) for c in ast.walk(fn) if isinstance(c, ast.Call)}
        assert not called & {"_ensure_worker", "revive", "Thread", "start"}, fn.name


def test_the_worker_starts_at_import_while_shadow_so_no_turn_starts_it(kernel):
    os_shadow._reset_for_tests()
    assert os_shadow._worker is None
    os_shadow._start_at_import()
    assert os_shadow._worker is not None and os_shadow._worker.is_alive()
    src = (SVC / "os_shadow.py").read_text("utf-8")
    assert src.rstrip().endswith("_start_at_import()        # service start-up, never a turn (see _ensure_worker)")


def test_the_import_start_is_a_no_op_with_the_flag_off(kernel, monkeypatch):
    os_shadow._reset_for_tests()
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    os_shadow._start_at_import()
    assert os_shadow._worker is None
