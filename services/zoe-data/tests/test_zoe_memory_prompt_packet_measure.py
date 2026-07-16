from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys

import pytest

from memory_service import MemoryRef
from mempalace_baseline import BASELINE_CASES
from zoe_memory_prompt_packet_measure import (
    DEFAULT_MEASURE_USER_ID,
    MEASURE_CASES,
    memory_ref_to_cached_item,
    measure_cached_prompt_packets,
    require_synthetic_measure_user_id,
    seed_prompt_packet_measure_memories,
    summarize_prompt_packet_measurements,
    synthetic_measure_user_id_allowed,
)

pytestmark = pytest.mark.ci_safe


@dataclass
class FakePromptPacketMemoryService:
    refs: list[MemoryRef]

    def __init__(self):
        self.refs = []
        self.ingests = []
        self.loaded = []

    async def ingest(self, text: str, **kwargs):
        self.ingests.append((text, kwargs))
        metadata = {
            "user_id": kwargs["user_id"],
            "scope": kwargs.get("scope", "project"),
            "status": kwargs.get("status", "approved"),
            "confidence": kwargs.get("confidence", 0.7),
            "source": kwargs.get("source"),
        }
        metadata.update(kwargs.get("metadata") or {})
        ref = MemoryRef(id=f"ref-{len(self.refs)}", text=text, metadata=metadata)
        self.refs.append(ref)
        return ref

    async def load_for_prompt(self, user_id: str, *, limit: int = 20):
        self.loaded.append((user_id, limit))
        return [ref for ref in self.refs if ref.metadata.get("user_id") == user_id][:limit]


@pytest.mark.asyncio
async def test_seed_prompt_packet_measure_memories_writes_evidence_backed_rows():
    service = FakePromptPacketMemoryService()

    count = await seed_prompt_packet_measure_memories(service, user_id="measure-user")

    assert count == len(BASELINE_CASES)
    assert len(service.ingests) == len(BASELINE_CASES)
    first_text, first_kwargs = service.ingests[0]
    assert first_text == BASELINE_CASES[0].text
    assert first_kwargs["source"] == "prompt_packet_measure"
    assert first_kwargs["scope"] == "project"
    assert first_kwargs["metadata"]["evidence_refs"] == ["fixture:mempalace_baseline:weather_failure_fix"]


def test_memory_ref_to_cached_item_parses_json_evidence_refs():
    ref = MemoryRef(
        id="mem-1",
        text="Multica gates memory writes.",
        metadata={
            "event_id": "evt-1",
            "scope": "project",
            "user_id": "jason",
            "status": "approved",
            "confidence": "0.9",
            "evidence_refs": '["trace:1", "pytest:test"]',
        },
    )

    item = memory_ref_to_cached_item(ref)

    assert item == {
        "event_id": "evt-1",
        "content": "Multica gates memory writes.",
        "scope": "project",
        "user_id": "jason",
        "status": "approved",
        "confidence": "0.9",
        "source": "MemoryService.load_for_prompt",
        "evidence_refs": ("trace:1", "pytest:test"),
    }


def test_summary_reports_cached_packet_budget_status():
    summary = summarize_prompt_packet_measurements(
        [
            {
                "read_latency_ms": 10.0,
                "compile_latency_ms": 1.0,
                "total_latency_ms": 11.0,
                "accepted_count": 2,
                "evidence_count": 2,
                "can_inject_prompt": False,
                "can_write_memory": False,
            },
            {
                "read_latency_ms": 30.0,
                "compile_latency_ms": 2.0,
                "total_latency_ms": 32.0,
                "accepted_count": 3,
                "evidence_count": 1,
                "can_inject_prompt": False,
                "can_write_memory": False,
            },
        ]
    )

    assert summary["case_count"] == 2
    assert summary["all_packet_permissions_disabled"] is True
    assert summary["all_packets_safe"] is True
    assert summary["all_packets_cited"] is True
    assert summary["min_accepted_count"] == 2
    assert summary["p50_compile_latency_ms"] == 1.5
    assert summary["p95_compile_latency_ms"] == pytest.approx(1.95)
    assert summary["cached_packet_compile_within_budget"] is True




def test_summary_marks_packets_unsafe_when_rows_are_rejected_or_uncited():
    summary = summarize_prompt_packet_measurements(
        [
            {
                "read_latency_ms": 10.0,
                "compile_latency_ms": 1.0,
                "total_latency_ms": 11.0,
                "accepted_count": 1,
                "evidence_count": 1,
                "rejected_count": 1,
                "can_inject_prompt": False,
                "can_write_memory": False,
            },
            {
                "read_latency_ms": 10.0,
                "compile_latency_ms": 1.0,
                "total_latency_ms": 11.0,
                "accepted_count": 1,
                "evidence_count": 0,
                "rejected_count": 0,
                "can_inject_prompt": False,
                "can_write_memory": False,
            },
        ]
    )

    assert summary["all_packet_permissions_disabled"] is True
    assert summary["all_packets_cited"] is False
    assert summary["all_packets_safe"] is False


def test_synthetic_measure_user_guard_allows_only_synthetic_ids():
    assert synthetic_measure_user_id_allowed(DEFAULT_MEASURE_USER_ID) is True
    assert synthetic_measure_user_id_allowed(f"{DEFAULT_MEASURE_USER_ID}-run-1") is True
    assert require_synthetic_measure_user_id(f" {DEFAULT_MEASURE_USER_ID} ") == DEFAULT_MEASURE_USER_ID

    with pytest.raises(ValueError, match="user_id must not be blank"):
        require_synthetic_measure_user_id("  ")
    with pytest.raises(ValueError, match="--seed-synthetic may only use"):
        require_synthetic_measure_user_id("jason")


@pytest.mark.asyncio
async def test_measure_cached_prompt_packets_uses_memoryservice_prompt_read_and_safe_packets():
    service = FakePromptPacketMemoryService()
    await seed_prompt_packet_measure_memories(service, user_id="measure-user")

    summary = await measure_cached_prompt_packets(service, user_id="measure-user")

    assert summary["case_count"] == len(MEASURE_CASES)
    assert summary["all_packet_permissions_disabled"] is True
    assert summary["all_packets_safe"] is True
    assert summary["all_packets_cited"] is True
    assert summary["min_accepted_count"] >= 1
    assert summary["cached_packet_compile_within_budget"] is True
    assert service.loaded == [("measure-user", 20)] * len(MEASURE_CASES)
    assert all(result["can_inject_prompt"] is False for result in summary["results"])
    assert all(result["can_write_memory"] is False for result in summary["results"])


def test_maintenance_runner_imports_measurement_module():
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "memory_prompt_packet_measure.py"
    spec = importlib.util.spec_from_file_location("memory_prompt_packet_measure_runner", script_path)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    original_sys_path = list(sys.path)
    try:
        spec.loader.exec_module(runner)
    finally:
        sys.path[:] = original_sys_path

    assert runner.measure_cached_prompt_packets is measure_cached_prompt_packets
    assert runner.seed_prompt_packet_measure_memories is seed_prompt_packet_measure_memories
    assert runner.require_synthetic_measure_user_id("zoe-prompt-packet-measure") == "zoe-prompt-packet-measure"


@pytest.mark.asyncio
async def test_maintenance_runner_rejects_seed_for_real_user_before_service_load(monkeypatch, capsys):
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "memory_prompt_packet_measure.py"
    spec = importlib.util.spec_from_file_location("memory_prompt_packet_measure_runner_guard", script_path)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    original_sys_path = list(sys.path)
    try:
        spec.loader.exec_module(runner)
    finally:
        sys.path[:] = original_sys_path

    def fail_service_load():
        raise AssertionError("service should not load before user guard")

    monkeypatch.setattr(runner, "get_memory_service", fail_service_load)
    monkeypatch.setattr(runner.sys, "argv", ["memory_prompt_packet_measure.py", "--seed-synthetic", "--user-id", "jason"])

    with pytest.raises(SystemExit) as exc:
        await runner._main()

    assert exc.value.code == 2
    assert "--seed-synthetic may only use" in capsys.readouterr().err
