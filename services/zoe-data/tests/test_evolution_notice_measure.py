import json
import logging
import sys
import types

import pytest

import evolution_notice
from zoe_evolution_proposal_adapter import dump_legacy_evolution_proposal_contract, dump_mcp_evolution_proposal_contract

pytestmark = pytest.mark.ci_safe


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


class _WriterDb:
    def __init__(self):
        self.fetch_calls = []
        self.execute_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return []

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))


class _NoticeDb(_WriterDb):
    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        if "FROM llm_call_log" in sql:
            return [{"agent_tier": "gemma4", "total": 20, "errors": 3}]
        return []


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


def test_load_target_patterns_extracts_contract_metadata():
    raw = dump_legacy_evolution_proposal_contract(
        proposal_id="prop_patterns",
        title="Intent gap",
        description="Contract envelopes should preserve measurement patterns.",
        evidence="trace:patterns",
        proposal_type="intent_pattern",
        legacy_writer="evolution_notice:intent_miss_cluster",
        target_patterns=("turn lights on", "switch lights on"),
    )

    assert evolution_notice._load_target_patterns(raw) == ["turn lights on", "switch lights on"]


def test_attach_notice_trace_failure_returns_original_row(caplog):
    row = {"evidence": "not-json"}
    signal = types.SimpleNamespace(
        scope="system",
        user_id=None,
        evidence_refs=("trace:bad-json",),
        signal_id="signal_bad_json",
        source="evolution_notice:test_bad_json",
    )

    with caplog.at_level(logging.WARNING):
        updated = evolution_notice._attach_notice_trace_to_row(
            row,
            proposal_id="prop_bad_json",
            proposal_type="test_bad_json",
            title="Bad JSON proposal",
            signal=signal,
        )

    assert updated is row
    assert updated["evidence"] == "not-json"
    assert "trace collection failed for proposal_id=prop_bad_json" in caplog.text


def test_attach_notice_trace_logs_rejected_collector_result(caplog):
    row = {
        "evidence": json.dumps({
            "candidate_ids": ["existing_zoe_bad_scope_triage"],
        })
    }
    signal = types.SimpleNamespace(
        scope="bad_scope",
        user_id=None,
        evidence_refs=("trace:bad-scope",),
        signal_id="signal_bad_scope",
        source="evolution_notice:test_bad_scope",
    )

    with caplog.at_level(logging.WARNING):
        updated = evolution_notice._attach_notice_trace_to_row(
            row,
            proposal_id="prop_bad_scope",
            proposal_type="test_bad_scope",
            title="Bad scope proposal",
            signal=signal,
        )

    evidence = json.loads(updated["evidence"])
    collection = evidence["observation_trace_collection"]
    assert collection["ok"] is False
    assert collection["persisted"] is False
    assert collection["accepted_count"] == 0
    assert "unknown scope" in collection["rejected"][0]["reason"]
    assert "trace collection rejected for proposal_id=prop_bad_scope" in caplog.text


@pytest.mark.asyncio
async def test_record_frustration_signal_stores_runtime_intake_contract_snapshot(monkeypatch):
    db = _WriterDb()
    sync_calls = []

    monkeypatch.setitem(sys.modules, "db_pool", types.SimpleNamespace(get_db_ctx=lambda: db))

    async def fake_sync_evolution_proposal_to_multica(**kwargs):
        sync_calls.append(kwargs)
        return None

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(sync_evolution_proposal_to_multica=fake_sync_evolution_proposal_to_multica),
    )

    await evolution_notice.record_frustration_signal(
        user_id="jason",
        normalized_message="why can't you remember the bins",
        session_id="sess-1",
        repeat_count=3,
    )

    assert len(db.execute_calls) == 1
    assert len(sync_calls) == 1
    insert_sql, insert_args = db.execute_calls[0]
    assert "target_patterns" in insert_sql
    evidence = json.loads(insert_args[3])
    contract = json.loads(insert_args[4])
    assert insert_args[1] == "User frustration: 'why can't you remember the bins'"
    assert evidence["source"] == "runtime_evolution_intake"
    assert evidence["signal"]["source"] == "evolution_notice:user_frustration"
    assert evidence["signal"]["scope"] == "personal"
    assert evidence["signal"]["user_id"] == "jason"
    assert evidence["signal"]["metadata"]["session_id"] == "sess-1"
    assert evidence["signal"]["metadata"]["repeat_count"] == 3
    assert evidence["candidate_ids"] == ["existing_zoe_frustration_triage"]
    trace_collection = evidence["observation_trace_collection"]
    assert trace_collection["ok"] is True
    assert trace_collection["persisted"] is False
    assert trace_collection["accepted_count"] == 1
    trace = trace_collection["traces"][0]
    assert trace["trace_type"] == "proposal"
    assert trace["surface"] == "multica"
    assert trace["scope"] == "personal"
    assert trace["user_id"] == "jason"
    assert trace["related_ids"] == ["existing_zoe_frustration_triage"]
    assert trace["metadata"]["signal_source"] == "evolution_notice:user_frustration"
    assert contract["schema"] == "zoe_evolution_proposal"
    assert contract["legacy_writer"] == "runtime_evolution_intake"
    proposal = contract["proposal"]
    assert proposal["metadata"]["legacy_proposal_type"] == "user_frustration"
    assert proposal["metadata"]["legacy_writer"] == "evolution_notice:user_frustration"
    assert proposal["metadata"]["legacy_target_patterns"] == ["why can't you remember the bins"]
    assert "user_id" not in proposal["metadata"]
    assert "session_id" not in proposal["metadata"]
    assert "repeat_count" not in proposal["metadata"]
    assert "message_excerpt" not in proposal["metadata"]
    assert proposal["metadata"]["selected_candidate_id"] == "existing_zoe_frustration_triage"
    assert proposal["metadata"]["candidate_search"][0]["candidate_id"] == "existing_zoe_frustration_triage"
    assert proposal["approval_gate"]["allowed_to_execute"] is False
    assert sync_calls[0]["proposal_id"] == insert_args[0]
    assert sync_calls[0]["proposal_type"] == "user_frustration"
    assert sync_calls[0]["contract_snapshot"] == insert_args[4]


@pytest.mark.asyncio
async def test_record_user_issue_stores_runtime_intake_contract_snapshot(monkeypatch):
    db = _WriterDb()
    sync_calls = []

    monkeypatch.setitem(sys.modules, "db_pool", types.SimpleNamespace(get_db_ctx=lambda: db))

    async def fake_sync_evolution_proposal_to_multica(**kwargs):
        sync_calls.append(kwargs)
        return None

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(sync_evolution_proposal_to_multica=fake_sync_evolution_proposal_to_multica),
    )

    await evolution_notice.record_user_issue("weather failed again", "jason")

    assert len(db.execute_calls) == 1
    assert len(sync_calls) == 1
    insert_sql, insert_args = db.execute_calls[0]
    assert "target_patterns" in insert_sql
    evidence = json.loads(insert_args[3])
    contract = json.loads(insert_args[4])
    assert insert_args[1] == "User report: 'weather failed again'"
    assert evidence["source"] == "runtime_evolution_intake"
    assert evidence["signal"]["source"] == "evolution_notice:user_issue_report"
    assert evidence["signal"]["scope"] == "personal"
    assert evidence["signal"]["user_id"] == "jason"
    assert evidence["candidate_ids"] == ["existing_zoe_user_issue_triage"]
    trace_collection = evidence["observation_trace_collection"]
    assert trace_collection["ok"] is True
    assert trace_collection["persisted"] is False
    assert trace_collection["accepted_count"] == 1
    trace = trace_collection["traces"][0]
    assert trace["trace_type"] == "proposal"
    assert trace["surface"] == "multica"
    assert trace["scope"] == "personal"
    assert trace["user_id"] == "jason"
    assert trace["related_ids"] == ["existing_zoe_user_issue_triage"]
    assert trace["metadata"]["signal_source"] == "evolution_notice:user_issue_report"
    assert contract["schema"] == "zoe_evolution_proposal"
    assert contract["legacy_writer"] == "runtime_evolution_intake"
    proposal = contract["proposal"]
    assert proposal["metadata"]["legacy_proposal_type"] == "user_issue_report"
    assert proposal["metadata"]["legacy_writer"] == "evolution_notice:user_issue_report"
    assert proposal["metadata"]["legacy_target_patterns"] == ["weather failed again"]
    assert "user_id" not in proposal["metadata"]
    assert "message_excerpt" not in proposal["metadata"]
    assert proposal["metadata"]["selected_candidate_id"] == "existing_zoe_user_issue_triage"
    assert proposal["metadata"]["candidate_search"][0]["candidate_id"] == "existing_zoe_user_issue_triage"
    assert proposal["approval_gate"]["allowed_to_execute"] is False
    assert sync_calls[0]["proposal_id"] == insert_args[0]
    assert sync_calls[0]["proposal_type"] == "user_issue_report"
    assert sync_calls[0]["label_name"] == "user-feedback"
    assert sync_calls[0]["contract_snapshot"] == insert_args[4]


@pytest.mark.asyncio
async def test_run_evolution_notice_stores_contract_snapshots(monkeypatch):
    db = _NoticeDb()

    monkeypatch.setattr(
        evolution_notice,
        "_load_recent_misses",
        lambda: ["turn on kitchen lights", "turn on kitchen lights", "turn on kitchen lights"],
    )
    monkeypatch.setitem(sys.modules, "db_pool", types.SimpleNamespace(get_db_ctx=lambda: db))

    sync_calls = []

    async def fake_sync_evolution_proposal_to_multica(**kwargs):
        sync_calls.append(kwargs)
        return None

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(sync_evolution_proposal_to_multica=fake_sync_evolution_proposal_to_multica),
    )

    result = await evolution_notice.run_evolution_notice()

    assert result == {"created": 2, "skipped_dedup": 0, "clusters": 1}
    assert len(db.execute_calls) == 2
    intent_sql, intent_args = db.execute_calls[0]
    health_sql, health_args = db.execute_calls[1]
    assert "target_patterns" in intent_sql
    assert "target_patterns" in health_sql

    intent_evidence = json.loads(intent_args[4])
    intent_contract = json.loads(intent_args[5])
    health_evidence = json.loads(health_args[3])
    health_contract = json.loads(health_args[4])
    assert len(sync_calls) == 2
    assert intent_evidence["source"] == "runtime_evolution_intake"
    assert intent_evidence["signal"]["source"] == "evolution_notice:intent_miss_cluster"
    assert intent_evidence["signal"]["scope"] == "system"
    assert intent_evidence["signal"]["metadata"]["miss_count"] == 3
    assert intent_evidence["candidate_ids"] == ["existing_zoe_intent_pattern"]
    intent_trace_collection = intent_evidence["observation_trace_collection"]
    assert intent_trace_collection["ok"] is True
    assert intent_trace_collection["persisted"] is False
    assert intent_trace_collection["traces"][0]["trace_type"] == "proposal"
    assert intent_trace_collection["traces"][0]["scope"] == "system"
    assert intent_trace_collection["traces"][0]["related_ids"] == ["existing_zoe_intent_pattern"]
    assert intent_trace_collection["traces"][0]["metadata"]["signal_source"] == "evolution_notice:intent_miss_cluster"
    assert intent_contract["legacy_writer"] == "runtime_evolution_intake"
    assert intent_contract["proposal"]["metadata"]["legacy_writer"] == "evolution_notice:intent_miss_cluster"
    assert intent_contract["proposal"]["metadata"]["legacy_target_patterns"] == [
        "turn on kitchen lights",
        "turn on kitchen lights",
        "turn on kitchen lights",
    ]
    assert intent_contract["proposal"]["metadata"]["selected_candidate_id"] == "existing_zoe_intent_pattern"
    assert intent_contract["proposal"]["approval_gate"]["allowed_to_execute"] is False
    assert sync_calls[0]["proposal_id"] == intent_args[0]
    assert sync_calls[0]["proposal_type"] == "intent_pattern"
    assert sync_calls[0]["contract_snapshot"] == intent_args[5]
    assert health_evidence["source"] == "runtime_evolution_intake"
    assert health_evidence["signal"]["source"] == "evolution_notice:agent_health"
    assert health_evidence["signal"]["scope"] == "system"
    assert health_evidence["signal"]["metadata"] == {"agent_tier": "gemma4", "total": 20, "errors": 3}
    assert health_evidence["candidate_ids"] == ["existing_zoe_agent_health_triage"]
    health_trace_collection = health_evidence["observation_trace_collection"]
    assert health_trace_collection["ok"] is True
    assert health_trace_collection["persisted"] is False
    assert health_trace_collection["traces"][0]["trace_type"] == "proposal"
    assert health_trace_collection["traces"][0]["scope"] == "system"
    assert health_trace_collection["traces"][0]["related_ids"] == ["existing_zoe_agent_health_triage"]
    assert health_trace_collection["traces"][0]["metadata"]["signal_source"] == "evolution_notice:agent_health"
    assert health_contract["legacy_writer"] == "runtime_evolution_intake"
    health_proposal = health_contract["proposal"]
    assert health_proposal["metadata"]["legacy_proposal_type"] == "agent_health"
    assert health_proposal["metadata"]["legacy_writer"] == "evolution_notice:agent_health"
    assert health_proposal["metadata"]["selected_candidate_id"] == "existing_zoe_agent_health_triage"
    assert health_proposal["metadata"]["candidate_search"][0]["candidate_id"] == "existing_zoe_agent_health_triage"
    assert health_proposal["approval_gate"]["allowed_to_execute"] is False
    assert sync_calls[1]["proposal_id"] == health_args[0]
    assert sync_calls[1]["proposal_type"] == "agent_health"
    assert sync_calls[1]["contract_snapshot"] == health_args[4]
