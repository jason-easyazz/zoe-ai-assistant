"""Host tests for the pure firmware code (ring-encryption core + pairing gesture). Hand-run, never collected by CI:

    nice -n 10 python3 -m pytest labs/omi-firmware/host-test -q -p no:cacheprovider

Builds ``shim.c`` + ``../overlay/omi/src/zoe/zoe_ring_crypt.c`` with the host ``cc`` into a temp ``.so`` and drives it with
ctypes; AES-128-CTR comes from python ``cryptography`` through a callback, and the EXPECTED bytes are computed independently
in Python from the documented IV layout (salt(4) || seq(8) || block(4), big-endian) - so a layout or call-order bug in the
C shows up as a mismatch, not as a self-consistent round trip.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

cryptography = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

HERE = Path(__file__).resolve().parent
ZOE_SRC = HERE.parent / "overlay" / "omi" / "src" / "zoe"
KEY = bytes(range(16))
PACKET = 444  # RAW_AUDIO_PACKET_BYTES: 4-byte timestamp + 440 payload


def ref_iv(salt: int, seq: int) -> bytes:
    return salt.to_bytes(4, "big") + seq.to_bytes(8, "big") + b"\0\0\0\0"


def ref_ctr(iv: bytes, data: bytes) -> bytes:
    enc = Cipher(algorithms.AES(KEY), modes.CTR(iv)).encryptor()
    return enc.update(data) + enc.finalize()


CTR_FN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(ctypes.c_uint8),
                          ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t)


@pytest.fixture(scope="module")
def lib():
    if shutil.which("cc") is None:
        pytest.skip("no host C compiler")
    tmp = tempfile.mkdtemp(prefix="zoe-fw-host-")
    so = os.path.join(tmp, "libzoehost.so")
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-I", str(ZOE_SRC), "-o", so,
                    str(HERE / "shim.c"), str(ZOE_SRC / "zoe_ring_crypt.c")], check=True)
    L = ctypes.CDLL(so)

    @CTR_FN
    def backend(iv, src, dst, n):
        out = ref_ctr(bytes(iv[:16]), bytes(src[:n]))
        ctypes.memmove(dst, out, n)
        return 0

    L._keep = backend                       # keep the callback alive
    L.host_set_backend(backend)
    L.zrc_xcrypt_packet.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_char_p, ctypes.c_size_t]
    L.zrc_rekey.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_uint64, ctypes.c_uint32,
                            ctypes.c_uint32, ctypes.c_uint32]
    L.zrc_iv.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint64]
    L.host_led_frame.argtypes = [ctypes.c_int, ctypes.c_uint32, ctypes.c_bool, ctypes.POINTER(ctypes.c_uint8)]
    L.host_led_period.restype = ctypes.c_uint32
    L.host_key_decision.argtypes = [ctypes.c_int, ctypes.c_bool, ctypes.c_int, ctypes.c_bool, ctypes.c_int, ctypes.c_bool,
                                    ctypes.c_uint32]
    L.host_new_key_epoch.argtypes = [ctypes.c_bool, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    L.host_gesture_double_tap.argtypes = [ctypes.c_int64]
    L.host_gesture_double_tap.restype = ctypes.c_bool
    L.host_window_open.argtypes = [ctypes.c_int64, ctypes.c_int64]
    L.host_window_open.restype = ctypes.c_bool
    yield L
    shutil.rmtree(tmp, ignore_errors=True)


def packet(i: int) -> bytes:
    return bytes((i * 7 + j) & 0xFF for j in range(PACKET))


def test_iv_layout_is_salt_seq_block_big_endian(lib):
    buf = ctypes.create_string_buffer(16)
    lib.zrc_iv(buf, 0x01020304, 0x1122334455667788)
    assert buf.raw == bytes.fromhex("01020304" "1122334455667788" "00000000")


def test_packet_ciphertext_matches_an_independent_reference(lib):
    p = packet(1)
    buf = ctypes.create_string_buffer(p, PACKET)
    assert lib.zrc_xcrypt_packet(0xDEADBEEF, 4242, buf, PACKET) == 0
    assert buf.raw[:PACKET] == ref_ctr(ref_iv(0xDEADBEEF, 4242), p)
    assert buf.raw[:PACKET] != p
    assert lib.zrc_xcrypt_packet(0xDEADBEEF, 4242, buf, PACKET) == 0      # CTR: the same call decrypts
    assert buf.raw[:PACKET] == p


def test_wrong_seq_or_salt_does_not_decrypt(lib):
    """Negative control: the seq and the salt are both in the IV - an off-by-one in either is garbage, not audio."""
    p = packet(2)
    enc = ctypes.create_string_buffer(p, PACKET)
    lib.zrc_xcrypt_packet(7, 100, enc, PACKET)
    for salt, seq in ((7, 101), (8, 100)):
        b = ctypes.create_string_buffer(enc.raw[:PACKET], PACKET)
        lib.zrc_xcrypt_packet(salt, seq, b, PACKET)
        assert b.raw[:PACKET] != p


def test_refuses_salt_zero_and_oversized_packets(lib):
    b = ctypes.create_string_buffer(PACKET)
    assert lib.zrc_xcrypt_packet(0, 1, b, PACKET) != 0       # 0 = a stock (unencrypted) batch header: never a key stream
    assert lib.zrc_xcrypt_packet(1, 1, ctypes.create_string_buffer(600), 600) != 0
    assert lib.zrc_xcrypt_packet(1, 1, b, 0) != 0


def test_rekey_moves_a_tail_batch_to_the_new_salt(lib):
    base, n, old, new = 3600, 5, 0x11111111, 0x22222222
    plain = b"".join(packet(i) for i in range(n))
    enc = b"".join(ref_ctr(ref_iv(old, base + i), packet(i)) for i in range(n))
    buf = ctypes.create_string_buffer(enc, len(enc))
    assert lib.zrc_rekey(buf, PACKET, PACKET, base, n, old, new) == 0
    want = b"".join(ref_ctr(ref_iv(new, base + i), packet(i)) for i in range(n))
    assert buf.raw[:len(enc)] == want
    assert buf.raw[:len(enc)] != enc
    assert lib.zrc_rekey(buf, PACKET, PACKET, base, n, new, new) != 0       # same salt = no re-key: refused
    assert plain != want


def test_why_rekey_exists_reusing_salt_and_seq_leaks_the_xor(lib):
    """The hazard the per-batch salt + re-key prevents: two plaintexts under one (salt, seq) XOR to the plaintext XOR."""
    a, b = packet(3), packet(4)
    ca = ref_ctr(ref_iv(5, 9), a)
    cb = ref_ctr(ref_iv(5, 9), b)
    assert bytes(x ^ y for x, y in zip(ca, cb)) == bytes(x ^ y for x, y in zip(a, b))
    cb2 = ref_ctr(ref_iv(6, 9), b)
    assert bytes(x ^ y for x, y in zip(ca, cb2)) != bytes(x ^ y for x, y in zip(a, b))


def test_gesture_needs_two_double_taps_inside_the_window(lib):
    lib.host_gesture_reset()
    assert lib.host_gesture_double_tap(10_000) is False      # one double-tap: nothing
    assert lib.host_gesture_double_tap(12_400) is True       # second within 2.5 s: open
    assert lib.host_gesture_double_tap(12_450) is False      # firing disarms: a fifth/sixth tap does not re-fire
    lib.host_gesture_reset()
    assert lib.host_gesture_double_tap(0) is False
    assert lib.host_gesture_double_tap(2_501) is False       # too slow: this one only arms
    assert lib.host_gesture_double_tap(4_000) is True
    lib.host_gesture_reset()
    assert lib.host_gesture_double_tap(5_000) is False
    assert lib.host_gesture_double_tap(4_000) is False       # a clock that went backwards never opens pairing


def test_pairing_window_bounds(lib):
    assert lib.host_window_open(0, 5) is False               # never opened
    assert lib.host_window_open(1_000, 999) is True
    assert lib.host_window_open(1_000, 1_000) is False       # closes exactly at the deadline


# --- the salt allocator: monotonic, persisted ahead, never a fallback ------------------------------------------------
def test_salts_are_strictly_increasing_and_survive_reboots_without_reuse(lib):
    lib.host_epoch_reset(0)
    lib.host_epoch_boot()
    seen = []
    s = ctypes.c_uint32()
    for _ in range(1500):                                  # crosses a 1024 reservation boundary
        assert lib.host_epoch_take(ctypes.byref(s)) == 0
        seen.append(s.value)
    assert seen == sorted(set(seen)) and 0 not in seen
    assert lib.host_epoch_calls() == 2                     # one persist per 1024 salts, BEFORE they are used
    lib.host_epoch_boot()                                  # power cut: resume from what was persisted
    assert lib.host_epoch_take(ctypes.byref(s)) == 0 and s.value > max(seen)


def test_no_salt_without_a_persisted_reservation(lib):
    """Negative control for finding 3: a failed persist yields NO salt (the ring stops) - never a guessed one."""
    lib.host_epoch_reset(0)
    lib.host_epoch_boot()
    lib.host_epoch_set_fail(1)
    s = ctypes.c_uint32(7)
    assert lib.host_epoch_take(ctypes.byref(s)) < 0 and s.value == 7
    lib.host_epoch_set_fail(0)
    assert lib.host_epoch_take(ctypes.byref(s)) == 0 and s.value == 1


def test_the_salt_space_refuses_to_wrap(lib):
    lib.host_epoch_reset(0)
    lib.host_epoch_force(0xFFFFFFF0, 0xFFFFFFF0)
    s = ctypes.c_uint32()
    assert lib.host_epoch_take(ctypes.byref(s)) < 0


def test_the_key_decision(lib):
    USE, NEW = 0, 1
    d = lib.host_key_decision
    assert d(0, True, 0, True, 0, True, 5) == NEW              # wipe pending: the stored key is never used again
    assert d(0, False, 0, False, 0, True, 5) == NEW
    assert d(0, False, 0, True, 0, False, 5) == NEW
    assert d(0, False, 0, True, 0, True, 0) == NEW
    assert d(0, False, 0, True, 0, True, 5) == USE
    # a read ERROR is never "absent" (codex round 2, finding 1): refuse, load nothing, make nothing
    assert d(-5, False, 0, True, 0, True, 5) < 0
    assert d(0, False, 0, True, -5, False, 0) < 0
    assert d(0, False, -5, False, 0, True, 5) < 0


def test_a_new_key_never_wraps_the_salt_space(lib):
    """Codex round 2, finding 4: at exhaustion a new key FAILS - it never restarts the reservation at 1."""
    out = ctypes.c_uint32()
    assert lib.host_new_key_epoch(False, 0, ctypes.byref(out)) == 0 and out.value == 1
    assert lib.host_new_key_epoch(True, 2048, ctypes.byref(out)) == 0 and out.value == 2048 + 1024
    out.value = 7
    assert lib.host_new_key_epoch(True, 0xFFFFFF00, ctypes.byref(out)) < 0 and out.value == 7


# --- zoe_ble_sec.c itself, against fake Zephyr: pairing gate, crypto-wipe on new bond, power loss --------------------
STUBS = HERE / "stubs"


@pytest.fixture(scope="module")
def ble():
    if shutil.which("cc") is None:
        pytest.skip("no host C compiler")
    tmp = tempfile.mkdtemp(prefix="zoe-fw-ble-")
    so = os.path.join(tmp, "libzoeble.so")
    subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Wno-unused-parameter", "-Werror", "-shared", "-fPIC",
                    "-I", str(STUBS), "-I", str(ZOE_SRC), "-o", so, str(HERE / "ble_harness.c"),
                    str(ZOE_SRC / "zoe_ring_crypt.c")], check=True)
    L = ctypes.CDLL(so)
    L.h_has.argtypes = [ctypes.c_char_p]
    L.h_set_fail_read.argtypes = [ctypes.c_char_p]
    yield L
    shutil.rmtree(tmp, ignore_errors=True)


PI, THIEF = 1, 2


def test_pairing_needs_the_gesture_even_with_no_bond(ble):
    """Finding 2: no automatic first-setup window - a reset or freshly flashed pendant pairs only after the taps."""
    ble.h_reset()
    assert ble.h_boot() == 0
    assert not ble.h_window_open()
    assert ble.h_accept_now(1) != 0                        # refused: no window
    ble.h_gesture()
    assert ble.h_window_open()


def test_a_failed_stock_bond_wipe_fails_closed(ble):
    """Finding 1: if the stock-era bonds cannot be deleted, init fails (transport_start then does not advertise)."""
    ble.h_reset()
    ble.h_clear_marker()
    ble.h_set_fail_unpair(1)
    assert ble.h_init() != 0 and ble.h_haptic() == 2000
    ble.h_set_fail_unpair(0)
    assert ble.h_init() == 0 and ble.h_has(b"zoe/bw")


def test_a_new_bond_crypto_wipes_the_pendant_first(ble):
    ble.h_reset()
    ble.h_gesture()
    assert ble.h_pair(THIEF, -1) == 0
    assert ble.h_bonded(THIEF) and not ble.h_bonded(PI)
    assert ble.h_ram_key() not in (0, 1) and ble.h_flash_key() == ble.h_ram_key()
    assert ble.h_ring_key() == 0                           # the ring is empty under the fresh key
    assert not ble.h_has(b"zoe/wk") and not ble.h_has(b"zoe/wb")


def test_a_reconnect_with_the_existing_bond_does_not_wipe(ble):
    ble.h_reset()
    assert ble.h_reconnect(PI) == 0                        # no persistent operation at all
    assert ble.h_ram_key() == 1 and ble.h_ring_key() == 1 and ble.h_bonded(PI)


def test_no_window_or_no_bonding_request_means_no_wipe(ble):
    ble.h_reset()
    assert ble.h_accept_now(1) != 0 and ble.h_ops() == 0 and ble.h_ram_key() == 1
    ble.h_gesture()
    assert ble.h_accept_now(0) != 0 and ble.h_ops() == 0 and ble.h_ram_key() == 1


@pytest.mark.parametrize("nth", [1, 2])
def test_a_wipe_that_cannot_record_its_intent_refuses_the_pairing(ble, nth):
    ble.h_reset()
    ble.h_gesture()
    ble.h_set_fail_save(nth)                               # the key-wipe intent (1st) or the bond-wipe intent (2nd)
    assert ble.h_pair(THIEF, -1) == 2 and not ble.h_bonded(THIEF)
    ble.h_set_fail_save(0)


def test_power_loss_at_every_step_never_leaves_the_old_key_next_to_a_new_bond(ble):
    """Cut power before each persistent operation of pair -> bond -> re-key, boot, and check:
    - never the old key (generation 1) usable while the new peer is bonded, and never both peers bonded;
    - once the wipe intents are on flash, the boot ALWAYS finishes it: old key gone, ring not readable under it,
      and the old bond gone."""
    ble.h_reset()
    ble.h_gesture()
    assert ble.h_pair(THIEF, -1) == 0
    total = ble.h_ops()
    assert total >= 8
    for cut in range(total + 1):
        ble.h_reset()
        ble.h_gesture()
        r = ble.h_pair(THIEF, cut)
        assert r in (0, 1), cut
        assert ble.h_boot() == 0, cut
        thief, pi = ble.h_bonded(THIEF), ble.h_bonded(PI)
        assert not (thief and pi), cut
        if thief:
            assert ble.h_ram_key() != 1 and ble.h_flash_key() != 1 and ble.h_ring_key() != 1, cut
        if cut >= 1:                                       # the key-wipe intent was written first
            assert ble.h_ram_key() != 1 and ble.h_flash_key() != 1, cut
            assert ble.h_ring_key() in (0, ble.h_ram_key()), cut
        if cut >= 2:                                       # ... then the bond-wipe intent
            assert not pi, cut
        assert not ble.h_has(b"zoe/wk") and not ble.h_has(b"zoe/wb"), cut


# --- codex round 2: unreadable records and a failed ring clear (fakes) ---------------------------------------------------
def test_unreadable_flags_stop_the_boot_before_advertising(ble):
    """Findings 1-2: an unreadable stock-wipe marker or new-bond intent is never read as "not set"."""
    ble.h_reset()
    ble.h_gesture()
    assert ble.h_pair(THIEF, 2) == 1                       # power cut with both intents on flash
    ble.h_set_fail_read(b"zoe/wb")
    assert ble.h_boot() != 0                               # init fails: transport_start does not advertise
    assert ble.h_bonded(PI)                                # nothing guessed, nothing half-done
    ble.h_reset()
    ble.h_set_fail_read(b"zoe/bw")
    assert ble.h_boot() != 0


def test_an_unreadable_wipe_intent_never_loads_the_old_key(ble):
    ble.h_reset()
    ble.h_gesture()
    assert ble.h_pair(THIEF, 1) == 1                       # cut after the key-wipe intent, before the key is erased
    assert ble.h_flash_key() == 1                          # the old key is still on flash ...
    ble.h_set_fail_read(b"zoe/wk")
    ble.h_boot()
    assert ble.h_ram_key() == 0 and ble.h_ring_blocked()   # ... but it is never loaded: no key, ring blocked
    ble.h_set_fail_read(b"")
    assert ble.h_boot() == 0 and ble.h_ram_key() not in (0, 1) and not ble.h_has(b"zoe/wk")


def test_an_unreadable_key_is_not_silently_replaced(ble):
    ble.h_reset()
    ble.h_set_fail_read(b"zoe/rk")
    ble.h_boot()
    assert ble.h_ram_key() == 0 and ble.h_ring_blocked() and ble.h_flash_key() == 1


def test_a_failed_ring_clear_keeps_the_intent_and_blocks_the_ring(ble):
    """Finding 3: a new key whose ring could not be emptied is not used; the intent stays, and a later boot finishes."""
    ble.h_reset()
    ble.h_gesture()
    ble.h_set_fail_clear(1)
    assert ble.h_pair(THIEF, -1) == 0
    assert ble.h_ring_blocked() and ble.h_ram_key() == 0 and ble.h_has(b"zoe/wk") and ble.h_ring_key() == 1
    ble.h_set_fail_clear(0)
    assert ble.h_boot() == 0
    assert not ble.h_ring_blocked() and ble.h_ring_key() == 0 and not ble.h_has(b"zoe/wk")
    assert ble.h_ram_key() not in (0, 1)


def test_salt_exhaustion_blocks_the_new_key(ble):
    ble.h_reset()
    ble.h_put_epoch(0xFFFFFF00)
    ble.h_gesture()
    assert ble.h_pair(THIEF, -1) == 0
    assert ble.h_ram_key() == 0 and ble.h_ring_blocked() and ble.h_flash_key() == 0   # old key gone, no new one


# --- the status LED (owner design 2026-10-19): state -> colour/pattern, and the priority order ---------------------------
class LedIn(ctypes.Structure):
    _fields_ = [("off", ctypes.c_bool), ("fault", ctypes.c_bool), ("pairing_window", ctypes.c_bool), ("wiping", ctypes.c_bool),
                ("paired_celebration", ctypes.c_bool), ("charging", ctypes.c_bool), ("battery_pct", ctypes.c_uint8),
                ("storage_pct", ctypes.c_uint8), ("clock_set", ctypes.c_bool), ("syncing", ctypes.c_bool)]


NORMAL, SYNCING, WARNING, CHARGING, CHARGED, PAIRED, WIPE, PAIRING, FAULT, OFF = range(10)


def pick(lib, **kw):
    base = dict(battery_pct=80, storage_pct=10, clock_set=True)
    base.update(kw)
    return lib.host_led_pick(ctypes.byref(LedIn(**base)))


def frame(lib, state, t, white=False):
    buf = (ctypes.c_uint8 * 3)()
    lib.host_led_frame(state, t, white, buf)
    return tuple(buf)


def test_led_priority_order(lib):
    """fault > pairing window > wipe > paired > charging > warning > syncing > normal (and off above everything)."""
    everything = dict(off=True, fault=True, pairing_window=True, wiping=True, paired_celebration=True, charging=True,
                      battery_pct=10, storage_pct=95, clock_set=False, syncing=True)
    order = [("off", OFF), ("fault", FAULT), ("pairing_window", PAIRING), ("wiping", WIPE), ("paired_celebration", PAIRED),
             ("charging", CHARGING)]
    kw = dict(everything)
    for flag, want in order:
        assert pick(lib, **kw) == want, flag
        kw[flag] = False
    assert pick(lib, **kw) == WARNING
    kw.update(battery_pct=80, storage_pct=10, clock_set=True)
    assert pick(lib, **kw) == SYNCING
    kw["syncing"] = False
    assert pick(lib, **kw) == NORMAL


def test_led_warnings_and_charging_thresholds(lib):
    assert pick(lib, battery_pct=14) == WARNING and pick(lib, battery_pct=15) == NORMAL
    assert pick(lib, battery_pct=0) == NORMAL                      # 0 = not read yet, never a false warning
    assert pick(lib, storage_pct=90) == WARNING and pick(lib, storage_pct=89) == NORMAL
    assert pick(lib, clock_set=False) == WARNING
    assert pick(lib, charging=True, battery_pct=97) == CHARGING and pick(lib, charging=True, battery_pct=98) == CHARGED
    assert pick(lib, charging=True, battery_pct=5) == CHARGING    # low battery on the charger is charging, not a warning


def test_led_patterns(lib):
    assert frame(lib, NORMAL, 0) == (0, 0, 15) == frame(lib, NORMAL, 12345)        # dim, steady blue
    assert frame(lib, NORMAL, 0, white=True) == (15, 15, 15)
    assert max(frame(lib, NORMAL, 0)) <= 20                                          # the battery-saving dim duty
    assert [frame(lib, PAIRING, t) for t in (0, 250, 500, 750, 1000)] == \
        [(0, 0, 30), (0, 30, 0), (30, 0, 30), (30, 30, 0), (0, 0, 30)]            # blue > green > purple > yellow
    assert frame(lib, FAULT, 50)[0] > 0 and frame(lib, FAULT, 150) == (0, 0, 0) and frame(lib, FAULT, 50)[1:] == (0, 0)
    assert frame(lib, WARNING, 100)[0] > 0 and frame(lib, WARNING, 1000) == (0, 0, 0)
    w_on = frame(lib, WIPE, 10)
    assert w_on[0] == w_on[1] == w_on[2] > 0 and frame(lib, WIPE, 150) == (0, 0, 0)   # white flashes
    assert frame(lib, SYNCING, 100) == (0, 0, 15) and frame(lib, SYNCING, 600) == (0, 0, 0)
    assert frame(lib, CHARGED, 0) == (0, 20, 0)
    levels = [frame(lib, CHARGING, t)[1] for t in range(0, 3000, 250)]
    assert levels[0] == 0 and max(levels) >= 30 and levels[3] < levels[6] and levels[1] == levels[11]  # breathing
    assert all(frame(lib, CHARGING, t)[0] == frame(lib, CHARGING, t)[2] == 0 for t in range(0, 3000, 100))
    assert [frame(lib, PAIRED, t)[1] > 0 for t in (0, 300, 400, 700, 800)] == [True, False, True, False, True]
    assert frame(lib, OFF, 0) == (0, 0, 0)
    assert lib.host_led_period(NORMAL) == 1000 and lib.host_led_period(CHARGING) <= 100     # steady states barely wake
