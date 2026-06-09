import json
import sys
import types

import pytest

import evolution_notice
from zoe_evolution_proposal_adapter import dump_mcp_evolution_proposal_contract


class _Db:
    def __init__(self, rows):
        self.rows = rows
        self.fetch_calls = []
        self.execute_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        if "FROM evolution_proposals" in sql and "status='deployed'" in sql:
            return self.rows
        if "SELECT multica_issue_id FROM evolution_proposals" in sql:
            return [{"multica_issue_id": None}]
        if "FROM chat_messages" in sql:
            return [{"cnt": 0}]
        return []

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))


@pytest.mark.asyncio
async def test_measure_phase_treats_contract_target_patterns_as_no_patterns(monkeypatch):
    contract = dump_mcp_evolution_proposal_contract(
        proposal_id="prop_contract",
        title="Contract proposal",
        description="Contract envelopes in target_patterns should not crash measure phase.",
        evidence="trace:contract",
        proposal_type="code_improvement",
    )
    db = _Db(
        [
            {
                "id": "prop_contract",
                "type": "code_improvement",
                "title": "Contract proposal",
                "target_patterns": contract,
                "deployed_at": 1000.0,
            }
        ]
    )

    monkeypatch.setattr(evolution_notice.time, "time", lambda: 1000.0 + evolution_notice._MEASURE_WINDOW_S + 1)
    monkeypatch.setitem(sys.modules, "db_pool", types.SimpleNamespace(get_db_ctx=lambda: db))

    async def fake_update_multica_issue_on_proposal_status_change(*_args, **_kwargs):
        raise AssertionError("no linked Multica issue should be updated")

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(update_multica_issue_on_proposal_status_change=fake_update_multica_issue_on_proposal_status_change),
    )

    result = await evolution_notice.run_measure_phase()

    assert result == {"evaluated": 1, "validated": 1, "failed": 0}
    assert len(db.execute_calls) == 1
    update_sql, update_args = db.execute_calls[0]
    assert "validation_result" in update_sql
    validation_result = json.loads(update_args[1])
    assert validation_result["miss_before_48h"] == 0
    assert validation_result["miss_after_48h"] == 0
    assert validation_result["verdict"] == "validated"
    assert not any("FROM chat_messages" in sql for sql, _args in db.fetch_calls)


def test_load_target_patterns_accepts_only_legacy_arrays():
    assert evolution_notice._load_target_patterns('["a", "b"]') == ["a", "b"]
    assert evolution_notice._load_target_patterns('{"schema":"zoe_evolution_proposal"}') == []
    assert evolution_notice._load_target_patterns("not-json") == []
    assert evolution_notice._load_target_patterns(None) == []
