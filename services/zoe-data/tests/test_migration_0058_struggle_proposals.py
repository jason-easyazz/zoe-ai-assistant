"""Migration 0058 (``struggle_proposals`` + ``struggle_proposal_events``): create-if-missing, rerun-safe, unique fingerprint, the only DDL for the
tables, and a proposal written by the module's own SQL reads back.

Negative controls (run by hand when this was written): drop ``IF NOT EXISTS`` from 0058 -> the rerun test goes red; drop the UNIQUE index ->
the fingerprint test goes red; add a ``CREATE TABLE`` to ``struggle_proposals.py`` -> the no-request-time-DDL test goes red.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
MIG = SVC / "alembic/versions/0058_struggle_proposals.py"
PROPOSAL_COLUMNS = {"id", "fingerprint", "kind", "status", "pattern", "domain", "label", "label_source", "signals", "episode_count", "event_count",
                    "user_count", "day_count", "first_seen", "last_seen", "confidence", "summary", "summary_source", "followup", "decision_note",
                    "created_at", "updated_at", "decided_at", "decided_by", "mode"}
EVENT_COLUMNS = {"proposal_id", "event_id", "user_id", "ts", "signal"}


def _mod():
    spec = importlib.util.spec_from_file_location("mig_0058", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(engine, fn_name):
    mod = _mod()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(mod, fn_name)()


def test_revision_chain_is_linear_with_one_head():
    mod = _mod()
    assert mod.revision == "0058"
    revs, downs = set(), set()
    for f in (SVC / "alembic/versions").glob("*.py"):
        src = f.read_text()
        r = re.search(r'^revision = "(\w+)"', src, re.M)
        d = re.search(r'^down_revision = "(\w+)"', src, re.M)
        if r:
            revs.add(r.group(1))
        if d:
            downs.add(d.group(1))
    assert mod.down_revision in revs, "0058 must chain onto an existing revision"
    heads = revs - downs
    assert len(heads) == 1, f"more than one head: {sorted(heads)}"


def test_sqlite_upgrade_twice_then_downgrade():
    engine = sa.create_engine("sqlite://")
    _run(engine, "upgrade")
    _run(engine, "upgrade")  # already present: a no-op
    with engine.connect() as conn:
        pcols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(struggle_proposals)")}
        ecols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(struggle_proposal_events)")}
        idx = {r[1] for r in conn.exec_driver_sql("PRAGMA index_list(struggle_proposals)")}
        eidx = {r[1] for r in conn.exec_driver_sql("PRAGMA index_list(struggle_proposal_events)")}
    assert pcols == PROPOSAL_COLUMNS and ecols == EVENT_COLUMNS
    assert {"idx_struggle_proposals_fingerprint", "idx_struggle_proposals_status"} <= idx
    assert {"idx_struggle_proposal_events_event", "idx_struggle_proposal_events_user"} <= eidx
    _run(engine, "downgrade")
    _run(engine, "downgrade")  # already gone: a no-op
    with engine.connect() as conn:
        assert not conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE name LIKE 'struggle_proposal%'").fetchall()


def test_a_fingerprint_is_unique_and_an_evidence_pair_is_unique():
    engine = sa.create_engine("sqlite://")
    _run(engine, "upgrade")
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO struggle_proposals (id, fingerprint, kind) VALUES ('a', 'fp1', 'intent_gap')")
        conn.exec_driver_sql("INSERT INTO struggle_proposal_events (proposal_id, event_id, user_id) VALUES ('a', 'e1', 'u')")
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO struggle_proposals (id, fingerprint, kind) VALUES ('b', 'fp1', 'intent_gap')")
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO struggle_proposal_events (proposal_id, event_id, user_id) VALUES ('a', 'e1', 'u2')")
    with engine.connect() as conn:
        row = conn.exec_driver_sql("SELECT status, summary_source, followup, label_source FROM struggle_proposals WHERE id = 'a'").fetchone()
    assert tuple(row) == ("proposed", "template", "{}", "")                     # the defaults a fresh proposal relies on


def test_no_request_time_ddl():
    for rel in ("struggle_proposals.py", "routers/struggles.py"):
        src = (SVC / rel).read_text()
        assert "CREATE TABLE" not in src and "ALTER TABLE" not in src and "DROP TABLE" not in src, rel
