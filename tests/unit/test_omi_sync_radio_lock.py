"""omi-sync yields the radio to the Settings > Bluetooth agent (and the agent to omi-sync): one shared advisory lock.

Why it matters: the pendant accepts ONE connection. If the sync service connected in the gap after a firmware reset, the update could
not be confirmed. Source mutations worth re-running by Edit: drop the ``try_acquire`` check in ``ble_link.connect`` -> the busy test
goes red; drop ``radio.release()`` in its ``finally`` -> the release test goes red.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "omi-sync"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "panel-bluetooth"))

from omi_sync import ble_link  # noqa: E402
from omi_sync.radio_lock import RadioLock as SyncLock  # noqa: E402
from panel_bt.radio_lock import RadioLock as AgentLock  # noqa: E402


@pytest.fixture(autouse=True)
def lock_path(tmp_path, monkeypatch):
    p = tmp_path / "radio.lock"
    monkeypatch.setenv("ZOE_BLE_RADIO_LOCK", str(p))
    return p


async def test_sync_does_not_connect_while_the_bluetooth_agent_holds_the_radio(lock_path):
    agent = AgentLock(lock_path, "panel-bluetooth")
    assert agent.try_acquire()
    try:
        with pytest.raises(ble_link.ConnectError, match="radio busy.*panel-bluetooth"):
            async with ble_link.connect("E6:11:22:33:44:55", lecc=False):
                pytest.fail("must not connect")
    finally:
        agent.release()


async def test_a_failed_connect_gives_the_radio_back(lock_path):
    # no bleak on the CI box: the import fails AFTER the lock is taken - it must not be left held
    try:
        async with ble_link.connect("E6:11:22:33:44:55", lecc=False, attempts=1):
            pass
    except BaseException:
        pass
    assert AgentLock(lock_path).try_acquire(), "the lock must be free again"


def test_the_two_implementations_share_one_file_and_exclude_each_other(lock_path):
    s, a = SyncLock(), AgentLock(lock_path, "panel-bluetooth")
    assert s.path == lock_path
    assert s.try_acquire() and not a.try_acquire() and a.current_holder() == "omi-sync"
    s.release()
    assert a.try_acquire() and s.current_holder() == "panel-bluetooth" and not s.try_acquire()
