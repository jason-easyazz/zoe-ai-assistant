"""The Zoe OS kernel as the AUTHORITY for list add (os_authority.py): the kernel decides, and every kernel failure is a counted
fallback to the old path that never fails or stalls the turn.

The kernel is the shadow tests' FAKE (v1 socket protocol from its spec, nothing from ``labs/``) plus the authority ops; a hanging
kernel is a fake that sleeps before every answer. The contract against the real lab kernel is ``test_os_authority_contract.py``.
Names and items are invented.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path

import pytest

import os_authority
import os_shadow
import os_speaker
import user_erase_gate
from test_os_shadow import MEMBER, SVC, FakeKernel
from test_os_shadow_hook import _DB

pytestmark = pytest.mark.ci_safe

ITEM = "oat milk"


SURFACES = ("voice", "brain", "panel")
_SPEAKER_DOMAIN = b"zoe-os speaker assertion v1\0"


class AuthorityFake(FakeKernel):
    """The shadow fake plus the authority's rules as the kernel README states them: ``erase_member`` only from the ``eraser``,
    ``erase_item`` only with a ``subject`` and a valid ``forget.scoped`` speaker assertion for it (MAC, audience, person, once),
    ``list_digest`` only for the ``reconciler``; list rows modelled per (member, list, item) from verified adds and removes."""

    def __init__(self):
        super().__init__()
        self.creds.update(eraser=secrets.token_bytes(32), reconciler=secrets.token_bytes(32))
        self.identity = secrets.token_bytes(32)
        self.generation = secrets.token_hex(16)            # this kernel RUN's id: a restart changes it with the secret
        self.digest_key = secrets.token_bytes(32)
        self.health = {"alive": True, "schema": 1, "last_write_ok": True, "recovered": 0, "rows": 0}
        self.erase_verified = True
        self.refuse_items: dict = {}           # item -> refusal reason for its list.add
        self.remove_mode = "ok"                # "refuse": every list.remove is refused (a take-back that cannot verify)
        self.rows: Counter = Counter()         # (member, list, item) -> count
        self.used_nonces: set = set()
        self.op_delay: dict = {}               # op -> seconds this kernel sits on it before answering

    def acted(self):
        return [a["action"] for _, op, a in self.calls if op == "act"]

    def unproven(self, a, person, audience):
        tok = a.get("speaker")
        if not isinstance(tok, str) or tok.count(".") != 1:
            return True
        body, mac = tok.split(".")
        if not hmac.compare_digest(hmac.new(self.identity, _SPEAKER_DOMAIN + body.encode(), hashlib.sha256).hexdigest(), mac):
            return True
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if set(claims) != {"a", "g", "n", "p", "t"} or claims["g"] != self.generation:
            return True
        if claims["p"] != person or claims["a"] != audience or claims["n"] in self.used_nonces:
            return True
        self.used_nonces.add(claims["n"])
        return False

    def _answer(self, conn, skill, req):
        op, a = req["op"], req["args"]
        if self.op_delay.get(op):
            time.sleep(self.op_delay[op])
        if op == "health":
            return self._ok(conn, req, self.health)
        if op == "erase_member":
            if skill != "eraser":
                return self._refuse(conn, req, "speaker unproven")
            gone = sum(n for k, n in self.rows.items() if k[0] == a["subject"])
            self.rows = Counter({k: n for k, n in self.rows.items() if k[0] != a["subject"]})
            return self._ok(conn, req, {"erased": 2 + gone, "receipt_seq": 9, "verified": self.erase_verified})
        if op == "erase_item":
            if not a.get("subject"):
                return self._refuse(conn, req, "name whose item")
            if skill != "eraser" and self.unproven(a, a["subject"], "forget.scoped"):
                return self._refuse(conn, req, "speaker unproven")
            return self._ok(conn, req, {"erased": 2, "receipt_seq": 9, "verified": self.erase_verified})
        if op == "list_digest":
            if skill != "reconciler":
                return self._refuse(conn, req, "only the reconciler")
            return self._ok(conn, req, {"digests": sorted(os_speaker.keyed_digest(self.digest_key, *k) for k, n in self.rows.items()
                                                          for _ in range(n))})
        if op == "act" and a["action"] == "list.add" and a["args"]["item"] in self.refuse_items:
            return self._refuse(conn, req, self.refuse_items[a["args"]["item"]])
        if op == "act" and a["action"] == "list.remove" and self.remove_mode == "refuse":
            return self._refuse(conn, req, "no grant from that person")
        if op == "act" and self.act_mode == "ok" and a["action"] in ("list.add", "list.remove"):
            key = (a["on_behalf_of"], a["args"].get("list", "shopping"), a["args"]["item"].strip().lower())
            if a["action"] == "list.add":
                self.rows[key] += 1
            else:
                self.rows.pop(key, None)
        return super()._answer(conn, skill, req)

    def ops(self):
        return [op for _, op, _ in self.calls]


def _secret_file(path: Path, payload: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh)


def provision(state: Path, k: AuthorityFake) -> None:
    """What the authority kernel writes at its start: creds.json (surfaces), erase-creds.json, identity-creds.json and
    reconcile-creds.json (each 0600)."""
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: k.creds[s].hex() for s in SURFACES}}))
    _secret_file(state / "erase-creds.json", {"socket": k.path, "creds": {"eraser": k.creds["eraser"].hex()}})
    _secret_file(state / "identity-creds.json", {"socket": k.path, "creds": {"speaker-verifier": k.identity.hex()},
                                                   "generation": k.generation, "ttl_s": 60})
    _secret_file(state / "reconcile-creds.json", {"socket": k.path, "creds": {"reconciler": k.creds["reconciler"].hex()},
                                                  "digest_key": k.digest_key.hex()})


@pytest.fixture
def ak(monkeypatch):
    k = AuthorityFake()
    state = Path(tempfile.mkdtemp(prefix="osa"))
    k.state = state
    provision(state, k)
    (state / "people.json").write_text(json.dumps({"people": [MEMBER]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_LIST_ADD_AUTHORITY", "kernel")
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")         # both on: a kernel-decided add must not ALSO be shadowed
    os_shadow._reset_for_tests()
    os_authority._reset_for_tests()
    user_erase_gate.reset()
    yield k
    os_authority._reset_for_tests()
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.close()
    shutil.rmtree(state, ignore_errors=True)


async def propose(user=MEMBER, item=ITEM):
    async with os_authority.all_or_none():         # what add_items_to_list wraps its transaction in
        return await os_authority.propose_add(user, list_type="shopping", list_name="Shopping", item=item)


@pytest.mark.asyncio
async def test_outside_an_all_or_none_unit_the_kernel_is_never_asked(ak):
    """A grant nothing could take back is never made: the old path writes, the kernel is not touched."""
    assert await os_authority.propose_add(MEMBER, list_type="shopping", list_name="Shopping", item=ITEM) == ("fallback", "no_unit")
    assert ak.connections == 0


# ---- the decision -----------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_flag_off_decides_nothing_and_touches_no_socket(ak, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_ADD_AUTHORITY", "off")
    assert await propose() == os_authority.OFF and ak.connections == 0
    monkeypatch.setenv("ZOE_OS_LIST_ADD_AUTHORITY", "on")              # an unlisted value is off, never a guess
    assert await propose() == os_authority.OFF and ak.connections == 0


@pytest.mark.asyncio
async def test_a_verified_receipt_decides_and_health_is_read_once_per_window(ak):
    assert await propose() == ("kernel", "verified")
    assert await propose(item="rice") == ("kernel", "verified")
    assert ak.ops() == ["health", "publish", "act", "publish", "act"]
    assert [a["args"]["item"] for _, op, a in ak.calls if op == "act"] == [ITEM, "rice"]


@pytest.mark.asyncio
async def test_a_real_refusal_is_honoured(ak):
    ak.act_mode = "refuse"                                    # "no grant from that person": the member withdrew the grant
    assert await propose() == ("refused", "no_grant")
    assert os_authority.REAL_REFUSALS == {"no_grant", "forgotten"}


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["derives from a forgotten event", "forgotten_action_arguments", "carries forgotten content"])
async def test_a_forgotten_refusal_is_honoured_and_never_falls_back(ak, reason):
    """Falling back would let the old path write the forgotten text straight back (forgotten means forever)."""
    ak.act_mode = "refuse:" + reason
    assert await propose() == ("refused", "forgotten")
    assert os_authority.refusal_reply("forgotten") == os_authority.FORGOTTEN_REPLY != os_authority.REFUSAL_REPLY
    assert os_shadow.reason_code("forgotten_action_arguments") == "forgotten"      # not "params": "argument" is in the text


@pytest.mark.asyncio
@pytest.mark.parametrize("act_mode,code", [
    ("refuse:wrong argument type", "refused_params"),         # the kernel's own limit (an item over 500 characters)
    ("refuse:asks for more than its manifest", "refused_manifest"),
    ("refuse:cites an event that does not exist", "refused_cause"),
    ("refuse:something new", "refused_other"),
    ("unverified", "unverified"), ("uncertain", "unverified"), ("item_differs", "unverified"), ("wrong_shape", "unverified"),
    ("malformed", "protocol"), ("close", "unavailable"),
])
async def test_every_answer_that_is_not_a_verified_receipt_or_a_real_refusal_falls_back(ak, act_mode, code):
    ak.act_mode = act_mode
    assert await propose() == ("fallback", code)


@pytest.mark.asyncio
@pytest.mark.parametrize("health,code", [({"last_write_ok": False}, "write_failed"), ({"schema": 2}, "unhealthy"),
                                         ({"alive": False}, "unhealthy")])
async def test_an_unhealthy_kernel_is_never_asked_to_act(ak, health, code):
    ak.health.update(health)
    assert await propose() == ("fallback", code) and "act" not in ak.ops()


@pytest.mark.asyncio
async def test_a_hanging_kernel_falls_back_inside_the_budget_and_the_circuit_then_skips_it(ak):
    ak.delay = 3.0                                            # accepts, answers nothing for 3 s
    t0 = time.perf_counter()
    assert await propose() == ("fallback", "timeout")
    assert time.perf_counter() - t0 < os_authority.TIMEOUT_S + 0.1
    t1 = time.perf_counter()
    assert await propose() == ("fallback", "circuit")
    assert time.perf_counter() - t1 < 0.01


@pytest.mark.asyncio
async def test_kernel_down_falls_back(ak):
    ak.srv.close()
    Path(ak.path).unlink()
    assert await propose() == ("fallback", "unavailable")


@pytest.mark.asyncio
async def test_who_the_kernel_cannot_speak_for_falls_back_without_a_socket(ak):
    for who in ("member-z", "guest", "__household__", ""):    # a member added after the roster, a guest, the household
        assert (await propose(user=who)).verdict == "fallback"
    with user_erase_gate.erase_span(MEMBER):
        assert await propose() == ("fallback", "erasing")
    assert ak.connections == 0


@pytest.mark.asyncio
async def test_the_decision_logs_codes_only(ak, caplog):
    caplog.set_level(logging.INFO, logger="os_authority")
    await propose(item="secret recipe word")
    lines = [r.getMessage() for r in caplog.records if "OS_AUTHORITY" in r.getMessage()]
    assert len(lines) == 1 and "secret" not in lines[0] and "result=kernel code=verified" in lines[0]
    assert os_authority.counters() == {"kernel:verified": 1}


# ---- the turn's budget: nothing blocks the loop, ONE deadline for the whole proposal -------------------------------------------------
def drain():
    """Wait for the worker to finish what the last proposal left running on it (the pool has one thread)."""
    os_authority._pool.submit(lambda: None).result(5)


@pytest.mark.asyncio
async def test_a_slow_roster_read_never_stalls_the_event_loop_and_the_budget_holds(ak, monkeypatch):
    """Review item: the roster (people.json) was read ON the loop before the wait began. A 250 ms read now runs on the worker:
    the loop keeps ticking through it and the turn falls back inside its budget."""
    import asyncio
    real = os_shadow.roster

    def slow_roster():
        time.sleep(0.25)
        return real()
    monkeypatch.setattr(os_shadow, "roster", slow_roster)
    ticks = 0
    stop = False

    async def ticker():
        nonlocal ticks
        while not stop:
            await asyncio.sleep(0.005)
            ticks += 1
    t = asyncio.ensure_future(ticker())
    t0 = time.perf_counter()
    d = await propose()
    took = time.perf_counter() - t0
    stop = True
    await t
    assert d == ("fallback", "timeout") and took < os_authority.TIMEOUT_S + 0.05
    assert ticks >= 10, ticks                                  # the loop ran all through the read (about 30 ticks of 5 ms)
    drain()
    assert ak.acted() == []                                    # the worker found its deadline spent: it never acted


def test_no_file_stat_or_credential_read_inside_this_modules_coroutines():
    """Structure pin, like the shadow module's: every file the authority touches is read in a function the worker runs."""
    import ast
    tree = ast.parse((SVC / "os_authority.py").read_text("utf-8"))
    banned = {"open", "roster", "provisioned", "pending_rollbacks", "_pending_add", "_pending_drop", "_pending_write", "read_text",
              "write_text", "stat", "exists", "is_file", "unlink", "state_dir", "_load_creds", "eraser_creds", "reconciler_creds",
              "identity", "speaker_arg", "mint", "_record_late", "_abandoned"}
    bad = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.AsyncFunctionDef):
            for n in ast.walk(fn):
                if isinstance(n, ast.Call):
                    name = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                    if name in banned:
                        bad.append((fn.name, name, n.lineno))
    assert bad == []


@pytest.mark.asyncio
async def test_no_act_is_started_at_the_edge_of_the_deadline(ak):
    ak.op_delay["publish"] = 0.14                              # health fast, publish answers ~10 ms before the deadline
    assert await propose() == ("fallback", "timeout")
    drain()
    assert "publish" in ak.ops() and ak.acted() == []


@pytest.mark.asyncio
async def test_slow_health_and_slow_publish_share_one_deadline_not_one_each(ak):
    ak.op_delay.update(health=0.09, publish=0.09)              # each inside 150 ms, together past it
    t0 = time.perf_counter()
    assert await propose() == ("fallback", "timeout")
    drain()
    assert time.perf_counter() - t0 < os_authority.TIMEOUT_S + 0.06       # the worker too stopped at the one deadline
    assert ak.acted() == []


@pytest.mark.asyncio
async def test_an_act_answered_past_the_deadline_is_recorded_and_the_old_path_result_stands(ak):
    """The kernel commits the add after the turn gave up (its answer arrives at 300 ms). Production falls back and writes the row;
    the grant is in no ledger, so it is recorded ids-only for the reconcile (the kernel may hold it), and a later rollback of the
    turn does not pretend to take it back."""
    ak.op_delay["act"] = 0.3
    db = _TxDB()
    out = await add_many(db, "oat milk")
    assert out["added"] == ["oat milk"] and [w[0] for w in db.writes] == ["INSERT INTO list_items"]
    assert not out["outcomes"][0]["kernel"]
    drain()
    time.sleep(0.3)
    assert ak.rows[(MEMBER, "Shopping", "oat milk")] == 1                  # the kernel did commit it, late
    (row,) = os_authority.pending_rollbacks()
    assert row["member"] == MEMBER and row["n"] == 1 and row["why"] == "uncertain"
    assert os_authority.counters()["late:uncertain"] == 1


def test_a_grant_verified_after_the_turn_left_is_taken_back_or_recorded(ak):
    gone = os_authority._Handoff()
    gone.gone = True
    d = os_authority._propose(MEMBER, ITEM, "Shopping", time.monotonic() + 1.0, gone)
    assert d == ("fallback", "late") and ak.acted() == ["list.add", "list.remove"] and +ak.rows == Counter()
    assert os_authority.pending_rollbacks() == [] and os_authority.counters()["late:taken_back"] == 1
    ak.remove_mode = "refuse"
    gone = os_authority._Handoff()
    gone.gone = True
    assert os_authority._propose(MEMBER, ITEM, "Shopping", time.monotonic() + 1.0, gone) == ("fallback", "late")
    (row,) = os_authority.pending_rollbacks()
    assert row["why"] == "late" and row["n"] == 1 and ITEM not in json.dumps(row)


# ---- the add path (list_service.add_items_to_list) ---------------------------------------------------------------------------------
async def spoken_add():
    import list_service
    db = _DB()
    out = await list_service.add_items_to_list(db, text="oat milk and rice", user_id=MEMBER, list_type="shopping",
                                               list_name="Shopping", new_list_visibility="family")
    return db.writes, out


class _TxDB(_DB):
    """``_DB`` whose ``transaction()`` is real: a block that raises drops every write made inside it (a nested block is a
    savepoint). ``commit_fails``: the OUTERMOST block raises on its way out with no error inside it (the commit failed)."""

    def __init__(self, commit_fails=False):
        super().__init__()
        self.depth, self.commit_fails = 0, commit_fails

    def transaction(self):
        db = self

        class _T:
            async def __aenter__(s):
                s.mark = len(db.writes)
                db.depth += 1
                return s

            async def __aexit__(s, et, *a):
                db.depth -= 1
                if et is None and db.depth == 0 and db.commit_fails:
                    del db.writes[s.mark:]
                    raise ConnectionError("commit failed")
                if et is not None:
                    del db.writes[s.mark:]
                return False
        return _T()


async def add_many(db, text):
    import list_service
    return await list_service.add_items_to_list(db, text=text, user_id=MEMBER, list_type="shopping", list_name="Shopping",
                                                new_list_visibility="family")


def acts(ak):
    return [a["action"] for _, op, a in ak.calls if op == "act"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text,refused,n", [("oat milk and rice", "rice", 2), ("oat milk, rice and tea", "tea", 3)])
async def test_first_items_verified_then_one_refused_leaves_neither_side_holding_any(ak, text, refused, n):
    """The review's case: the kernel verified item 1 (and 2), refused the last. Production rolls back every row; the kernel must not
    keep what it granted. All or none, on both sides."""
    ak.refuse_items[refused] = "derives from a forgotten event"
    db = _TxDB()
    with pytest.raises(os_authority.ListAddRefused) as e:
        await add_many(db, text)
    assert e.value.code == "forgotten"
    assert db.writes == []                                               # production: nothing
    assert +ak.rows == Counter()                                         # the kernel: nothing
    assert acts(ak) == ["list.add"] * n + ["list.remove"] * (n - 1)      # every grant taken back, newest first
    assert os_authority.pending_rollbacks() == [] and os_authority.counters()["rollback:done"] == 1


@pytest.mark.asyncio
async def test_a_failed_commit_after_every_item_verified_is_taken_back_in_the_kernel(ak):
    """``AsyncpgCompat.commit`` is a no-op: the all-or-none claim rests on the real transaction, and its failure at the very end
    (every item already verified) must not leave the kernel holding the items."""
    db = _TxDB(commit_fails=True)
    with pytest.raises(ConnectionError):
        await add_many(db, "oat milk and rice")
    assert db.writes == [] and +ak.rows == Counter() and acts(ak) == ["list.add", "list.add", "list.remove", "list.remove"]


@pytest.mark.asyncio
async def test_a_take_back_that_cannot_verify_is_recorded_without_words_and_the_add_is_not_done(ak):
    ak.refuse_items["rice"] = "derives from a forgotten event"
    ak.remove_mode = "refuse"
    db = _TxDB()
    with pytest.raises(os_authority.ListAddRefused):
        await add_many(db, "oat milk and rice")
    assert db.writes == []
    (row,) = os_authority.pending_rollbacks()
    assert row["member"] == MEMBER and row["n"] == 1 and isinstance(row["at"], int)
    path = ak.state / os_authority.ROLLBACK_PENDING
    raw = path.read_text()
    assert "oat" not in raw and "Shopping" not in raw and path.stat().st_mode & 0o777 == 0o600
    assert os_authority.counters()["rollback:incomplete"] == 1
    assert await os_authority.erase_member(MEMBER) >= 2 and os_authority.pending_rollbacks() == []   # a member erase prunes it


@pytest.mark.asyncio
async def test_a_real_refusal_writes_nothing_and_raises_for_the_caller_to_say(ak):
    import list_service
    ak.act_mode = "refuse"
    db = _TxDB()
    with pytest.raises(os_authority.ListAddRefused):
        await list_service.add_items_to_list(db, text="oat milk and rice", user_id=MEMBER, list_type="shopping",
                                             list_name="Shopping", new_list_visibility="family")
    assert db.writes == [] and acts(ak) == ["list.add"]               # refused at the first item: nothing granted, nothing to take back


@pytest.mark.asyncio
async def test_a_hanging_kernel_never_stalls_the_add_and_the_old_path_writes_it_exactly_as_today(ak, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_ADD_AUTHORITY", "off")
    base, base_out = await spoken_add()
    monkeypatch.setenv("ZOE_OS_LIST_ADD_AUTHORITY", "kernel")
    ak.delay = 3.0
    t0 = time.perf_counter()
    writes, out = await spoken_add()
    assert time.perf_counter() - t0 < os_authority.TIMEOUT_S + 0.25       # one timeout, then the circuit: never 3 s
    assert [w[1][1:] for w in writes] == [w[1][1:] for w in base] and out["added"] == base_out["added"] == ["oat milk", "rice"]
    assert os_authority.counters() == {"fallback:timeout": 1, "fallback:circuit": 1}


@pytest.mark.asyncio
async def test_a_kernel_decided_add_writes_the_read_model_and_is_not_shadowed_again(ak):
    writes, out = await spoken_add()
    assert [w[0] for w in writes] == ["INSERT INTO list_items"] * 2 and all(o["kernel"] for o in out["outcomes"])
    time.sleep(0.3)                                            # the shadow worker would have proposed by now
    assert [op for op in ak.ops() if op == "act"] == ["act", "act"] and os_shadow.counters() == {}


def test_the_spoken_callers_speak_the_refusal():
    for name in ("intent_router.py", "mcp_server.py"):
        src = (SVC / name).read_text()
        assert "except os_authority.ListAddRefused" in src and "os_authority.refusal_reply(" in src, name


# ---- erase reach --------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_erase_member_goes_as_the_eraser_and_is_confirmed_by_the_kernels_read_back_or_fails(ak):
    assert await os_authority.erase_member(MEMBER) == 2
    ((skill, sent),) = [(sk, a) for sk, op, a in ak.calls if op == "erase_member"]
    assert skill == "eraser" and sent == {"subject": MEMBER}             # the member-delete credential: no speaker claimed
    ak.erase_verified = False
    with pytest.raises(os_authority.AuthorityEraseFailed):
        await os_authority.erase_member(MEMBER)
    ak.srv.close()
    Path(ak.path).unlink()
    os_authority._reset_for_tests()
    with pytest.raises(os_authority.AuthorityEraseFailed):
        await os_authority.erase_member(MEMBER)
    assert not isinstance(os_authority.AuthorityEraseFailed("x"), RuntimeError)


@pytest.mark.asyncio
@pytest.mark.parametrize("gone,code", [("creds.json", "not_provisioned"), ("erase-creds.json", "KernelUnavailable")])
async def test_in_kernel_mode_an_unprovisioned_kernel_or_no_eraser_fails_the_member_delete(ak, gone, code):
    """Review item: with the flag ``kernel`` and no kernel credentials the delete used to finish while the kernel still held the
    member's lists. Unreachable, unprovisioned or without its eraser: the delete fails (and is retried), never 'done'."""
    (ak.state / gone).unlink()
    with pytest.raises(os_authority.AuthorityEraseFailed) as e:
        await os_authority.erase_member(MEMBER)
    assert e.value.code == code and not e.value.refused
    assert [op for op in ak.ops() if op == "erase_member"] == []        # never asked through a surface instead


@pytest.mark.asyncio
async def test_a_member_erase_through_the_panel_or_with_a_stale_eraser_credential_fails_the_delete(ak):
    import asyncio
    with pytest.raises(os_shadow.KernelRefused):                         # the fake kernel's rule: only the eraser erases a member
        await asyncio.get_running_loop().run_in_executor(os_authority._pool, lambda: os_authority._call(
            "panel", "erase_member", 1.0, subject=MEMBER))
    _secret_file(ak.state / "erase-creds.json", {"socket": ak.path, "creds": {"eraser": secrets.token_bytes(32).hex()}})
    with pytest.raises(os_authority.AuthorityEraseFailed):
        await os_authority.erase_member(MEMBER)


@pytest.mark.asyncio
async def test_erase_with_the_flag_off_touches_nothing(ak, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_ADD_AUTHORITY", "off")
    assert await os_authority.erase_member(MEMBER) == 0 and await os_authority.erase_item(MEMBER, ITEM) == 0 and ak.connections == 0


@pytest.mark.asyncio
async def test_erase_item_names_the_member_carries_one_assertion_and_never_the_words_or_the_token_in_a_log(ak, caplog):
    caplog.set_level(logging.DEBUG)
    assert await os_authority.erase_item(MEMBER, "Oat Milk ") == 2
    ((skill, sent),) = [(sk, a) for sk, op, a in ak.calls if op == "erase_item"]
    assert skill == "panel" and set(sent) == {"digest", "subject", "speaker"}
    assert sent["digest"] == os_authority.digest(ITEM) and sent["subject"] == MEMBER and ITEM not in json.dumps(ak.calls)
    assert sent["speaker"] not in caplog.text and ak.identity.hex() not in caplog.text
    assert await os_authority.erase_item(MEMBER, ITEM) == 2              # a fresh assertion per request (the nonce is spent)


@pytest.mark.asyncio
async def test_an_unproven_erase_item_is_a_real_refusal_and_a_restarted_secret_is_read_again(ak):
    ak.identity, ak.generation = secrets.token_bytes(32), secrets.token_hex(16)   # restarted: new secret and run, file not yet rewritten
    with pytest.raises(os_authority.AuthorityEraseFailed) as e:
        await os_authority.erase_item(MEMBER, ITEM)
    assert e.value.refused and e.value.code == "refused_unproven"
    assert [op for op in ak.ops() if op == "erase_item"] == ["erase_item"] * 2     # retried once, with the file read again
    provision(ak.state, ak)                                              # the supervisor rewrote it
    assert await os_authority.erase_item(MEMBER, ITEM) == 2
    with pytest.raises(os_authority.AuthorityEraseFailed) as e:
        await os_authority.erase_item("", ITEM)
    assert e.value.code == "no_member"


@pytest.mark.asyncio
async def test_kernel_digests_reads_as_the_reconciler_and_keys_each_row_by_member_list_and_item(ak):
    async with os_authority.all_or_none():
        await os_authority.propose_add(MEMBER, list_type="shopping", list_name="Shopping", item=ITEM)
    rows = os_authority.kernel_digests()
    assert rows.digests == [rows.digest(MEMBER, "Shopping", ITEM)]
    assert rows.digest(MEMBER, "Shopping", ITEM) not in (rows.digest("member-b", "Shopping", ITEM), rows.digest(MEMBER, "Todo", ITEM))
    assert ak.digest_key.hex() not in repr(rows) and [sk for sk, op, _ in ak.calls if op == "list_digest"] == ["reconciler"]


def test_delete_user_calls_the_authority_erase_and_fails_closed_on_it():
    src = (SVC / "memory_service.py").read_text()
    body = src[src.index("    async def delete_user("):]
    body = body[:body.index("\n    async def ", 10)]
    at = body.index("await os_authority.erase_member(user_id)")
    assert "raise MemoryServiceError" in body[at:at + 300]
