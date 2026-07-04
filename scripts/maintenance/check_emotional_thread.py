#!/usr/bin/env python3
"""Progress check for Samantha criterion #2 (emotional-thread continuity).

Read-only. Reports whether the Flue brain has started emitting `emotional_moment`
memories (the substrate the memory-side wiring is blocked on) and whether an
emotional query recalls them. See docs/architecture/zoe-memory-emotional-thread-handoff.md

Exit 0 = substrate present (memory-side wiring is UNBLOCKED); 1 = still blocked.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

MEMPALACE = os.environ.get("MEMPALACE_PATH", "/home/zoe/.mempalace")
DATA_URL = os.environ.get("ZOE_DATA_URL", "http://127.0.0.1:8000")
EMO_QUERIES = ["how have I been feeling lately", "what have I been stressed about"]


def _substrate():
    """Non-archived emotional_moment rows across all users."""
    import chromadb

    col = chromadb.PersistentClient(path=MEMPALACE).get_collection("mempalace_drawers")
    got = col.get(where={"memory_type": "emotional_moment"}, include=["documents", "metadatas"])
    rows = [
        (d, m)
        for d, m in zip(got["documents"], got["metadatas"])
        if str(m.get("status", "")).lower() != "archived"
    ]
    return rows


def _recall(user_id):
    hits = []
    for q in EMO_QUERIES:
        url = f"{DATA_URL}/api/memories/for-prompt?user_id={user_id}&message={urllib.parse.quote(q)}"
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                packet = json.load(r).get("packet", "")
        except Exception as e:
            hits.append((q, f"<error: {e}>"))
            continue
        lines = [ln for ln in packet.split("\n") if ln.startswith("- ")]
        top = lines[0][:70] if lines else "<empty>"
        hits.append((q, top))
    return hits


def main():
    try:
        rows = _substrate()
    except Exception as e:
        print(f"[check] could not read MemPalace: {e}", file=sys.stderr)
        return 2

    print(f"[emotional-thread] non-archived emotional_moment rows: {len(rows)}")
    if not rows:
        print("[emotional-thread] STILL BLOCKED — no substrate; Flue signal not landing yet.")
        return 1

    users = sorted({str(m.get("user_id")) for _, m in rows})
    print(f"[emotional-thread] UNBLOCKED — substrate present for users: {users}")
    for d, m in rows[:5]:
        print(f"    [{m.get('user_id')}/{m.get('status')}] {d[:70]!r}")
    for u in users:
        print(f"  recall check (user={u}):")
        for q, top in _recall(u):
            print(f"    {q!r} -> {top!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
