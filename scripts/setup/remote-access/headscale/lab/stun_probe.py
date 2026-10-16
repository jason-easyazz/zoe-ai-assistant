"""Send one STUN Binding request (with the SOFTWARE + FINGERPRINT attributes
Tailscale's STUN server requires) to HOST:3478/udp and exit 0 iff a Binding
success response comes back. Used by lab_test.sh to prove the embedded DERP's
STUN listener answers, directly and through the lab's UDP relay.

    python3 -I stun_probe.py 10.77.250.2
"""

import os
import socket
import struct
import sys
import zlib


def binding_request() -> bytes:
    soft = struct.pack("!HH", 0x8022, 8) + b"tailnode"
    # Length covers the attributes including the 8-byte FINGERPRINT.
    header = struct.pack("!HHI", 0x0001, len(soft) + 8, 0x2112A442) + os.urandom(12)
    body = header + soft
    crc = (zlib.crc32(body) & 0xFFFFFFFF) ^ 0x5354554E
    return body + struct.pack("!HHI", 0x8028, 4, crc)


def main() -> int:
    host = sys.argv[1]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3)
    sock.sendto(binding_request(), (host, 3478))
    try:
        data, _ = sock.recvfrom(1024)
    except OSError:
        return 1
    # 0x0101 = Binding success response.
    return 0 if data[:2] == b"\x01\x01" else 1


if __name__ == "__main__":
    sys.exit(main())
