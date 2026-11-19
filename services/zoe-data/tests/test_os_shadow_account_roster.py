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


READS: list = []


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
        if query == os_shadow.ROSTER_SQL:                  # ONE statement for everything (one snapshot); nothing per member
            READS.append(query)
            def row(uid):
                role, mode = ACCOUNTS.get(uid, (None, None))
                return (uid, 1 if uid in ACCOUNTS else 0, role, 1 if mode else 0, mode[0] if mode else None, mode[1] if mode else None)
            return _Cursor([row(uid) for uid in MIRROR] + [row(uid) for uid in ACCOUNTS])
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


def test_a_read_that_fails_writes_nothing_and_the_roster_in_force_stays(state, monkeypatch, capsys):
    """A rewrite must never DOWNGRADE: everything a role is decided from comes from one statement, and if that fails nothing is
    written. Control: with a failed read answered as "nobody has an account" the file is rewritten with plain ids and this fails."""
    state.write_text(json.dumps({"people": [{"id": "fixture-admin", "role": "adult"}]}))
    before = state.read_text()

    class Broken(_DB):
        async def execute(self, query, params=()):
            raise RuntimeError("database fault")

    monkeypatch.setattr(db_compat, "get_compat_db", Broken)
    assert os_shadow.main(["--write-roster-from-accounts"]) == 1
    out = capsys.readouterr()
    assert state.read_text() == before and "not changed" in out.err and out.out == ""
    assert not any(uid in out.err for uid in ACCOUNTS)


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


def test_everything_a_role_is_decided_from_is_read_in_one_statement(state):
    """Separate queries have no shared snapshot: an account created, removed or replaced between them would be written with the
    wrong role. So ids, account roles and stored modes come from ONE statement and nothing is looked up per member after it.
    Control: any second query (the stand-in database refuses every other statement) fails this."""
    READS.clear()
    os_shadow.write_roster_from_accounts()
    assert READS == [os_shadow.ROSTER_SQL] and ";" not in os_shadow.ROSTER_SQL
    for table in ("users", "auth_users", "member_modes"):
        assert table in os_shadow.ROSTER_SQL
    got = _by_id(state)
    assert got["fixture-adult"] == "adult" and got["fixture-minor"] == "child" and got["fixture-no-account"] is None


@pytest.mark.parametrize("stated, has_mode, mode, minor, role", [
    ("admin", 0, None, None, "adult"), ("USER ", 0, None, None, "adult"), ("child", 0, None, None, "child"),
    ("user", 1, "kid", 1, "child"), ("user", 1, "kid", 0, "child"), ("user", 1, "not-a-mode", 0, "child"),
    ("mystery", 0, None, None, None), (None, 0, None, None, None)])
def test_the_role_rule_is_the_account_facts_rule(stated, has_mode, mode, minor, role):
    """One row decides as ``enrollment_state._account_facts`` + the roster rule did: a stored mode that says minor, or is not a
    valid mode, is a child; else the account's role; an unknown role is least access."""
    assert os_shadow._roster_role(stated, has_mode, mode, minor) == role
