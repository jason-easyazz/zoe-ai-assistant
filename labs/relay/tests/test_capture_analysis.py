"""Unit tests for lab/capture_analysis.py with synthetic frames (the T3 probe's own negative control)."""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab import capture_analysis as ca  # noqa: E402


def frame(src_port, dst_port, seq, payload, src="127.0.0.1", dst="127.0.0.1"):
    tcp = struct.pack(">HHIIBBHHH", src_port, dst_port, seq, 0, 5 << 4, 0x18, 65535, 0, 0) + payload
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), 0, 0, 64, 6, 0,
                     bytes(map(int, src.split("."))), bytes(map(int, dst.split("."))))
    return (ca.LO, b"\x00" * 12 + b"\x08\x00" + ip + tcp)


def rec(ct, body):
    return bytes([ct, 3, 3]) + struct.pack(">H", len(body)) + body


CLIENT_HELLO = rec(0x16, b"\x01" + b"\x00" * 40)
SERVER_HELLO = rec(0x16, b"\x02" + b"\x00" * 40)
CIPHER = rec(0x17, os.urandom(64))


class AnalysisTests(unittest.TestCase):
    def test_passthrough_flow_is_clean(self):
        frames = [frame(50000, 8443, 1, CLIENT_HELLO), frame(50000, 8443, 1 + len(CLIENT_HELLO), CIPHER),
                  frame(8443, 50000, 1, SERVER_HELLO), frame(8443, 50000, 1 + len(SERVER_HELLO), rec(0x14, b"\x01") + CIPHER)]
        rep = ca.passthrough_report(ca.parse_segments(frames), 8443)
        self.assertEqual((rep["connections"], rep["violations"]), (1, []))

    def test_split_across_segments_is_reassembled_and_needle_found(self):
        body = b"GET /lab/echo?c=CANARY-1 HTTP/1.1\r\n"
        frames = [frame(1, 2, 1, body[:10]), frame(1, 2, 11, body[10:])]
        segs = ca.parse_segments(frames)
        self.assertEqual(ca.needle_hits(segs, [b"CANARY-1"])[b"CANARY-1"], 1)

    def test_negative_control_plaintext_after_hello_is_a_violation(self):
        plain = rec(0x16, b"\x0b" + b"GET /lab/echo")  # a readable handshake-typed record after the hello
        frames = [frame(50000, 8443, 1, CLIENT_HELLO), frame(50000, 8443, 1 + len(CLIENT_HELLO), plain)]
        self.assertTrue(ca.passthrough_report(ca.parse_segments(frames), 8443)["violations"])

    def test_negative_control_plain_http_stream_is_a_violation_and_a_hit(self):
        http = b"GET /lab/echo?c=CANARY-2 HTTP/1.1\r\nHost: x\r\n\r\n"
        segs = ca.parse_segments([frame(50000, 8443, 1, http)])
        self.assertTrue(ca.passthrough_report(segs, 8443)["violations"])
        self.assertEqual(ca.needle_hits(segs, [b"CANARY-2"])[b"CANARY-2"], 1)

    def test_server_side_must_start_with_server_hello(self):
        frames = [frame(8443, 50000, 1, CLIENT_HELLO)]
        self.assertTrue(ca.passthrough_report(ca.parse_segments(frames), 8443)["violations"])

    def test_capture_file_roundtrip(self):
        f1, f2 = frame(1, 2, 1, b"abc"), frame(3, 4, 1, b"def")
        blob = b"".join(struct.pack(">HI", i, len(fr)) + fr for i, fr in (f1, f2))
        self.assertEqual(len(ca.read_capture(blob)), 2)


if __name__ == "__main__":
    unittest.main()
