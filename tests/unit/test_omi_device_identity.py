"""ONE stable identity per pendant, derived on the Pi, never exposing the address.

The Pi has two services that name a device to zoe-data: ``omi_sync`` (the upload header ``X-Omi-Device``, the spool key) and
``panel_bt`` (the id Settings > Bluetooth lists, connects and assigns by). They live in separate venvs and cannot import each other, so
each has its own copy of the derivation - and this file is what stops the two copies drifting. If they drifted, the id an admin picks in
Settings > Bluetooth would be a different device than the one uploading, and the registry would assign an id nobody ever sends.

Golden value: the derivation is part of the id, so changing it orphans every stored recording and every spooled one. A change to the
golden below is a data migration, not a refactor.

Source mutation worth re-running by Edit: change ``ID_NAMESPACE`` in ``panel_bt/model.py`` (or the prefix in ``uploader.device_hash``) ->
``test_both_services_derive_the_same_id`` and ``test_the_golden_value_pins_the_derivation`` go red.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "omi-sync"))
sys.path.insert(0, str(REPO / "services" / "panel-bluetooth"))

from omi_sync.uploader import device_hash  # noqa: E402
from panel_bt import model  # noqa: E402

ADDRESSES = ["AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff", "  Aa:Bb:Cc:Dd:Ee:Ff ", "E6:11:22:33:44:55", "00:00:00:00:00:00"]
GOLDEN = ("AA:BB:CC:DD:EE:FF", "3fab2c58b1939692")        # a made-up address; the value is sha256("omi-sync:aa:bb:cc:dd:ee:ff")[:16]


@pytest.mark.parametrize("addr", ADDRESSES)
def test_both_services_derive_the_same_id(addr):
    assert device_hash(addr) == model.device_id(addr)
    assert re.fullmatch(r"[0-9a-f]{16}", device_hash(addr))
    assert model.DEVICE_ID_RX.match(device_hash(addr)), "the id omi-sync sends is a valid id for Settings > Bluetooth"


def test_the_golden_value_pins_the_derivation():
    addr, want = GOLDEN
    assert device_hash(addr) == want and model.device_id(addr) == want


def test_case_and_padding_do_not_change_the_identity_and_different_pendants_differ():
    assert len({device_hash(a) for a in ADDRESSES[:3]}) == 1
    assert len({device_hash(a) for a in (ADDRESSES[0], ADDRESSES[3], ADDRESSES[4])}) == 3


@pytest.mark.parametrize("addr", ADDRESSES)
def test_the_address_is_not_recoverable_from_the_id(addr):
    ident = device_hash(addr)
    assert addr.strip().lower().replace(":", "") not in ident and addr.strip().lower() not in ident
