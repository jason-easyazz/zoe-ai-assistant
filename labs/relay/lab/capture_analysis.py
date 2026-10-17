"""Host-side analysis of a capture.py file for lab cell T3 (pure Python, unit-tested in tests/).

A relay that passes TLS through shows, per direction of a household connection, exactly one plaintext handshake
record (ClientHello one way, ServerHello the other) and then only ciphertext: TLS 1.3 sends ChangeCipherSpec
(0x14, compatibility) and ApplicationData (0x17) records after that. The lab forces TLS 1.3 on both ends so that
this is checkable; any other record type, or any needle bytes, after the first record means the relay could read.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

LO = 0
CT_CCS, CT_HANDSHAKE, CT_APPDATA = 0x14, 0x16, 0x17
HS_CLIENT_HELLO, HS_SERVER_HELLO = 1, 2


@dataclass(frozen=True)
class Seg:
    iface: int
    src: str
    sport: int
    dst: str
    dport: int
    seq: int
    payload: bytes


def read_capture(data: bytes) -> list[bytes]:
    """Split a capture.py file into (iface, frame) pairs."""
    out, i = [], 0
    while i + 6 <= len(data):
        iface, n = struct.unpack(">HI", data[i:i + 6])
        i += 6
        out.append((iface, data[i:i + n]))
        i += n
    return out


def parse_segments(frames) -> list[Seg]:
    segs = []
    for iface, fr in frames:
        if len(fr) < 14 + 20 or struct.unpack(">H", fr[12:14])[0] != 0x0800:
            continue
        ip = fr[14:]
        ihl = (ip[0] & 0x0F) * 4
        if ip[9] != 6:
            continue
        total = struct.unpack(">H", ip[2:4])[0]
        tcp = ip[ihl:total]
        if len(tcp) < 20:
            continue
        sport, dport, seq = struct.unpack(">HHI", tcp[:8])
        off = (tcp[12] >> 4) * 4
        payload = tcp[off:]
        if payload:
            segs.append(Seg(iface, ".".join(map(str, ip[12:16])), sport, ".".join(map(str, ip[16:20])), dport, seq, payload))
    return segs


def streams(segs: list[Seg]) -> dict:
    """Reassemble one byte stream per (src, sport, dst, dport), ordered by sequence number."""
    by_flow: dict = {}
    for s in segs:
        by_flow.setdefault((s.src, s.sport, s.dst, s.dport), {})[s.seq] = max(
            s.payload, by_flow.get((s.src, s.sport, s.dst, s.dport), {}).get(s.seq, b""), key=len)
    out = {}
    for flow, parts in by_flow.items():
        buf, expect = bytearray(), None
        for seq in sorted(parts):
            p = parts[seq]
            if expect is not None and seq < expect:  # overlap: keep only the new tail
                p = p[expect - seq:]
            buf += p
            expect = seq + len(parts[seq])
        out[flow] = bytes(buf)
    return out


def tls_records(stream: bytes) -> list[tuple[int, int]]:
    """[(content_type, handshake_type_or_-1)] for each complete TLS record at the head of the stream."""
    recs, i = [], 0
    while i + 5 <= len(stream):
        ct = stream[i]
        n = struct.unpack(">H", stream[i + 3:i + 5])[0]
        if ct not in (0x14, 0x15, 0x16, 0x17) or i + 5 + n > len(stream):
            break
        hs = stream[i + 5] if ct == CT_HANDSHAKE and n else -1
        recs.append((ct, hs))
        i += 5 + n
    return recs


def needle_hits(segs: list[Seg], needles: list[bytes]) -> dict:
    """needle -> number of reassembled streams containing it (also checks raw segments)."""
    hits = {n: 0 for n in needles}
    for st in streams(segs).values():
        for n in needles:
            if n in st:
                hits[n] += 1
    for s in segs:
        for n in needles:
            if n in s.payload and hits[n] == 0:
                hits[n] = 1
    return hits


def passthrough_report(segs: list[Seg], vhost_port: int) -> dict:
    """Check every household connection to the frps https vhost port (on lo): ClientHello then ciphertext,
    and the reverse direction ServerHello then ciphertext. Returns counts and a list of violations."""
    st = streams(segs)
    violations, conns = [], 0
    for (src, sport, dst, dport), data in st.items():
        if dport == vhost_port:  # panel -> box direction
            want_first = HS_CLIENT_HELLO
        elif sport == vhost_port:  # box -> panel direction
            want_first = HS_SERVER_HELLO
        else:
            continue
        recs = tls_records(data)
        if dport == vhost_port:
            conns += 1
        if not recs:
            violations.append(f"{src}:{sport}->{dst}:{dport}: no TLS record at stream head")
            continue
        if recs[0] != (CT_HANDSHAKE, want_first):
            violations.append(f"{src}:{sport}->{dst}:{dport}: first record {recs[0]}, want handshake type {want_first}")
        for r in recs[1:]:
            if r[0] not in (CT_CCS, CT_APPDATA):
                violations.append(f"{src}:{sport}->{dst}:{dport}: record {r} after the hello is not ciphertext")
    return {"connections": conns, "violations": violations}
