#!/usr/bin/env python3
"""Packet-capture sidecar for lab cell T3 (runs INSIDE a container that shares the relay host's network namespace).

Opens an AF_PACKET socket on every interface of that namespace (eth0 AND lo: the loopback hop between the stream front
and frps is where a TLS-terminating relay would show plaintext) and appends raw frames to a file as
  [2-byte iface code: 0 = lo, 1 = other][4-byte length][frame bytes]
Needs CAP_NET_RAW (a Docker default). No tcpdump: nothing to install, nothing unpinned. SIGTERM flushes and exits.
Parsing lives in capture_analysis.py (host side, unit-tested).
"""
import signal
import socket
import struct
import sys

ETH_P_ALL = 3
LO = 0


def main() -> int:
    out_path = sys.argv[1]
    stop = False

    def _stop(*_a):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    s.settimeout(0.2)
    n = 0
    with open(out_path, "wb", buffering=0) as f:
        print("capture ready", flush=True)
        while not stop:
            try:
                data, addr = s.recvfrom(65535)
            except socket.timeout:
                continue
            f.write(struct.pack(">HI", LO if addr[0] == "lo" else 1, len(data)) + data)
            n += 1
    print(f"capture done frames={n}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
