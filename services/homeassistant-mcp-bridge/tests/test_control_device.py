import importlib.util
from pathlib import Path

import pytest


def _load_bridge_module():
    module_path = Path(__file__).resolve().parents[1] / "main.py"
    spec = importlib.util.spec_from_file_location("ha_mcp_bridge_main", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_set_temperature_calls_climate_set_temperature(monkeypatch):
    bridge = _load_bridge_module()
    calls = []

    class FakeBridge:
        async def call_service(self, domain, service, entity_id=None, data=None):
            calls.append((domain, service, entity_id, data))
            return {"ok": True}

    monkeypatch.setattr(bridge, "ha_bridge", FakeBridge())

    result = await bridge.control_device(
        bridge.DeviceControlRequest(
            entity_id="climate.living_room",
            action="set_temperature",
            data={"temperature": 21.5},
        )
    )

    assert calls == [
        (
            "climate",
            "set_temperature",
            "climate.living_room",
            {"entity_id": "climate.living_room", "temperature": 21.5},
        )
    ]
    assert result["message"] == "Successfully executed set_temperature on climate.living_room"
