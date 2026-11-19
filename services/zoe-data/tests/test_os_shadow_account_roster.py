"""The roster WITH roles (`os_shadow.py --write-roster-from-accounts`): everyone Zoe knows is on it, a role comes only from the
account record, and a role is stated by hand only for a demo id.

Negative controls (each run once against the code, then put back):
* read the role for every known id as "adult" -> `test_roles_come_from_the_account_never_the_mirror` fails;
* drop the synthetic-id check in `_demo_roles` -> `test_a_hand_stated_role_is_refused_for_a_real_id` fails.
"""
import ast
import json
from pathlib import Path

import pytest

import db_compat
import os_shadow

pytestmark = pytest.mark.ci_safe

#: invented ids. account id -> (auth_users.role, member_modes row or None)
ACCOUNTS = {"fixture-admin": ("admin", None), "fixture-adult": ("user", None), "fixture-child": ("child", None),
            "fixture-minor": ("user", ("kid", 1)), "fixture-mystery": ("mystery", None)}
#: the mirror also knows ids with no account at all (and the two demo ids)
MIRROR = ["fixture-child", "fixture-no-account", "demo_bar_0001", "demo_bar_0002"]


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows


class _DB:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, query, params=()):
        if query == "SELECT user_id FROM auth_users":
            return _Cursor([(uid,) for uid in ACCOUNTS])
        if query == "SELECT id FROM users":
            return _Cursor([(uid,) for uid in MIRROR])
        if "FROM member_modes" in query:
            row = ACCOUNTS.get(params[0], (None, None))[1]
            return _Cursor([row] if row else [])
        if "FROM auth_users" in query:
            return _Cursor([(ACCOUNTS[params[0]][0],)] if params[0] in ACCOUNTS else [])
        raise AssertionError("unexpected database query")


POOL: list = []


@pytest.fixture
def state(tmp_path, monkeypatch):
    import db_pool

    async def opened():
        POOL.append("open")

    async def closed():
        POOL.append("close")

    POOL.clear()
    monkeypatch.setattr(db_pool, "init_pool", opened)
    monkeypatch.setattr(db_pool, "close_pool", closed)
    monkeypatch.setattr(db_compat, "get_compat_db", _DB)
    monkeypatch.setattr(os_shadow, "ensure_state_dir", lambda: tmp_path)
    return tmp_path / "people.json"


def _by_id(path):
    return {(e["id"] if isinstance(e, dict) else e): (e["role"] if isinstance(e, dict) else None)
            for e in json.loads(path.read_text())["people"]}


def test_roles_come_from_the_account_never_the_mirror(state, capsys):
    state.touch(mode=0o666)
    state.chmod(0o666)
    assert os_shadow.main(["--write-roster-from-accounts"]) == 0
    out = capsys.readouterr()
    assert out.out == "roster: 8 members written (2 adults, 2 children, 4 least access)\n" and out.err == ""
    assert _by_id(state) == {"fixture-admin": "adult", "fixture-adult": "adult", "fixture-child": "child", "fixture-minor": "child",
                             "fixture-mystery": None, "fixture-no-account": None, "demo_bar_0001": None, "demo_bar_0002": None}
    assert state.stat().st_mode & 0o777 == 0o600
    assert not any(uid in out.out for uid in list(ACCOUNTS) + MIRROR)       # counts only


def test_a_demo_id_may_be_given_a_role_by_hand(state):
    assert os_shadow.main(["--write-roster-from-accounts", "demo_bar_0001=adult", "demo_bar_0002=child"]) == 0
    got = _by_id(state)
    assert got["demo_bar_0001"] == "adult" and got["demo_bar_0002"] == "child" and got["fixture-child"] == "child"


@pytest.mark.parametrize("arg", ["fixture-child=adult", "fixture-no-account=adult", "demo_bar_0001=owner", "demo_bar_0001", "=adult"])
def test_a_hand_stated_role_is_refused_for_a_real_id(state, capsys, arg):
    assert os_shadow.main(["--write-roster-from-accounts", arg]) == 2
    assert not state.exists() and "refused" in capsys.readouterr().err


def test_a_failed_account_lookup_is_least_access(state, monkeypatch):
    import enrollment_state

    async def boom(uid, db=None):
        raise RuntimeError("lookup failed")

    monkeypatch.setattr(enrollment_state, "_account_facts", boom)
    os_shadow.write_roster_from_accounts()
    assert set(_by_id(state).values()) == {None}


def test_the_roles_written_are_exactly_the_kernels():
    tree = ast.parse((Path(__file__).resolve().parents[3] / "labs/zoe-os/zoe_os/capability.py").read_text())
    roles = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Tuple) and [e.id for e in t.elts] == ["ADULT", "CHILD", "GUEST"] for t in n.targets))
    assert set(os_shadow.ROSTER_ROLES) == set(roles)


def test_the_command_opens_the_database_itself_and_closes_it_even_when_the_read_fails(state, monkeypatch):
    """Run as a command there is no service to have opened the pool (found on the first live run: "db_pool not initialised").
    Control: without the ``init_pool`` call the first assert fails."""
    os_shadow.write_roster_from_accounts()
    assert POOL == ["open", "close"]

    class Broken(_DB):
        async def execute(self, query, params=()):
            raise RuntimeError("database fault")

    POOL.clear()
    monkeypatch.setattr(db_compat, "get_compat_db", Broken)
    with pytest.raises(RuntimeError):
        os_shadow.write_roster_from_accounts()
    assert POOL == ["open", "close"]
