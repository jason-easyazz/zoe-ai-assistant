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
