#!/usr/bin/env python3
"""The pendant NIGHT RE-PASS (services/zoe-data/omi_repass.py) as a standalone process, plus the owner's wearer-print commands.

The night window's job ``omi_repass`` runs this BEFORE the 12B loads (the 4B brain and Kokoro are stopped; nothing else needs the RAM):

    zoe-omi-repass.py --budget-s 1500                 # the night lane: faster-whisper + ECAPA over the recordings since the last pass
    zoe-omi-repass.py --dry-run                       # what is waiting and which model the budget would pick; writes nothing
    zoe-omi-repass.py --probe-gpu                     # what the GPU path could do on this stack (one JSON line); changes nothing

The OWNER's side of "who is me" (run it yourself; the listing holds what was said, so it is never pasted into the repo, a PR or a comment):

    zoe-omi-repass.py --wearer-status --user ID       # confirmed windows vs needed, and the heuristic proposals waiting
    zoe-omi-repass.py --wearer-lines --user ID --proposal 2026-11-10    # what was said in a proposal's windows: was that you?
    zoe-omi-repass.py --confirm-wearer 2026-11-10 --user ID             # yes, that was me: those windows become your pendant print
    zoe-omi-repass.py --reject-wearer 2026-11-10 --user ID              # no: delete the proposal

Nothing makes a line "me" until windows are CONFIRMED (here, or by labelling a line "me" in the app). Flag ``ZOE_OMI_NIGHT_REPASS`` = off | shadow | on.
Output: ONE JSON line (counts only) on stdout and a ``NIGHT_JOB omi_repass ...`` line on stderr. Exit 0 for any data outcome, 3 a model is missing, 1 an error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))


import os
import re

_SECRET_ENV = re.compile(r"(PASS|PASSWORD|SECRET|TOKEN|KEY|DSN|URL|CREDENTIAL)", re.I)


def redact_message(text: str, limit: int = 240) -> str:
    """An exception's text with every DSN / URL, password, token and secret-looking value scrubbed: the setup error must say WHAT failed (a missing variable, a refused
    connection) without ever echoing a credential into the journal or the morning report."""
    out = str(text)
    for k, v in os.environ.items():                      # the literal value of any secret-named variable, and the password inside a DSN-shaped one
        if v and len(v) >= 6 and _SECRET_ENV.search(k):
            out = out.replace(v, "<redacted>")
            m = re.match(r"[A-Za-z][A-Za-z0-9+.-]*://[^:/@\s]*:([^@\s]+)@", v)
            if m and len(m.group(1)) >= 3:
                out = out.replace(m.group(1), "<redacted>")
    out = re.sub(r"[A-Za-z][A-Za-z0-9+.-]*://\S+", "<url>", out)                                                           # any DSN / URL
    out = re.sub(r"(?i)\b(password|passwd|pwd|passphrase|secret|token|api[_-]?key|apikey|authorization)\b(\s*[=:]\s*)(\S+)", r"\1\2<redacted>", out)
    out = re.sub(r"(?i)\b(password|passwd|pwd|passphrase|secret|token|api[_-]?key|apikey)\b(\s+(?:is\s+|for\s+)?)(['\"])[^'\"]*\3", r"\1\2<redacted>", out)      # password 'x'
    out = re.sub(r"(?i)\b(password|passwd|pwd|passphrase|secret|token|api[_-]?key|apikey)\b(\s+)(?=\S*[\d_/+=.-])(\S{4,})", r"\1\2<redacted>", out)             # token abc123
    out = re.sub(r"(?i)\bBearer\s+\S+", "Bearer <redacted>", out)
    out = re.sub(r"\b[A-Za-z0-9_\-]{32,}\b", "<redacted>", out)                                                         # a long opaque run is a key, not a sentence
    return re.sub(r"\s+", " ", out).strip()[:limit]


async def amain(argv: "list[str] | None" = None) -> "tuple[int, dict]":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--budget-s", type=float, default=1500.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--probe-gpu", action="store_true")
    ap.add_argument("--rehearse", action="store_true", help="the DAYTIME REHEARSAL: the real job path on ONE recording (the newest 20-180 s one, or --recording ID), whatever the flag says, writing nothing. "
                    "Run it through `night_window.sh --rehearse-repass` (the same stops, compaction and restore order as the night); it must pass before ZOE_OMI_NIGHT_REPASS goes to shadow")
    ap.add_argument("--contenders", action="store_true", help="print the contender ledger's verdict line per model (counts only), then exit")
    ap.add_argument("--check-queue", action="store_true", help="with --user: how many chunks the owner's morning transcript check has waiting (counts only)")
    ap.add_argument("--recording", default="", help="with --rehearse: the recording id to use")
    ap.add_argument("--requeue-failed", action="store_true", help="after a fix: put recordings the pass gave up on (WAV still held) back in the queue")
    ap.add_argument("--wearer-status", action="store_true")
    ap.add_argument("--wearer-lines", action="store_true")
    ap.add_argument("--proposal", default="")
    ap.add_argument("--confirm-wearer", default="")
    ap.add_argument("--reject-wearer", default="")
    ap.add_argument("--user", default="")
    a = ap.parse_args(argv)
    import omi_repass as rp

    if a.contenders:
        import omi_repass_eval as eu
        lines = eu.verdicts(eu.read_ledger(rp.home_dir()))
        for ln in lines:
            print(ln, file=sys.stderr)
        return 0, {"status": "ok", "models": len(lines)}
    if a.check_queue:
        import omi_repass_eval as eu
        if not a.user:
            raise SystemExit("--check-queue needs --user ID")
        q = eu.check_queue(rp.home_dir(), a.user)
        return 0, {"status": "ok", "chunks": len(q), "low_confidence": sum(1 for x in q if x.get("low_conf")), "contested_slots": sum(len(x.get("slots") or []) for x in q)}
    if a.probe_gpu:
        t0 = time.time()
        p = subprocess.run([rp.venv_python(), str(rp.worker_path()), "--probe-gpu"], capture_output=True, text=True, timeout=120, check=False)
        try:
            out = json.loads(p.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return 3, {"status": "model_missing", "rc": p.returncode}
        return 0, {"status": "ok", **out, "probe_s": round(time.time() - t0, 1)}
    from db_pool import close_pool, init_pool

    try:
        await init_pool()
    except Exception as exc:  # noqa: BLE001
        return 1, {"status": "error", "setup_error": type(exc).__name__, "setup_message": redact_message(str(exc))}
    try:
        if a.requeue_failed:
            return 0, {"status": "ok", "requeued": await rp.requeue_failed(a.user or None)}
        owner_cmds = a.wearer_status or a.wearer_lines or a.confirm_wearer or a.reject_wearer
        if owner_cmds:
            if not a.user:
                raise SystemExit("--user ID is required for the wearer commands")
            from db_compat import get_compat_db
            async with get_compat_db() as db:
                if a.confirm_wearer:
                    return 0, {"status": "ok", "confirmed": await rp.confirm_wearer(db, a.user, a.confirm_wearer)}
                if a.reject_wearer:
                    return 0, {"status": "ok", "rejected": await rp.reject_wearer(db, a.user, a.reject_wearer)}
                if a.wearer_lines:
                    if not a.proposal:
                        raise SystemExit("--wearer-lines needs --proposal ID")
                    for ln in await rp.proposal_lines(db, a.user, a.proposal):
                        print(f"  [{ln['at_ms'] // 1000}s] {ln['text']}", file=sys.stderr)
                    return 0, {"status": "ok"}
                return 0, {"status": "ok", **await rp.wearer_status(db, a.user)}
        return 0, await rp.run_night(budget_s=a.budget_s, dry_run=a.dry_run, rehearse=a.rehearse, recording_id=a.recording)
    finally:
        await close_pool()


def main() -> int:
    rc, summary = asyncio.run(amain())
    keys = ("status", "mode", "model", "pending", "done", "replaced", "shadow", "carried", "failed", "agree", "fits", "device", "worker_rtf", "peak_rss_mb", "gpu_peak_mb", "engines", "missing_voters", "skipped", "low_conf_chunks", "sample_speech_s", "speech_min", "fused3_min", "fused2_min", "qwen_only_min", "carried_min", "window_s", "used_s", "planned", "oversize", "why")
    print("NIGHT_JOB omi_repass " + " ".join(f"{k}={summary[k]}" for k in keys if k in summary), file=sys.stderr)
    if summary.get("contenders"):
        print("NIGHT_JOB omi_repass_contenders " + str(summary["contenders"])[:260], file=sys.stderr)
    print(json.dumps(summary, sort_keys=True))
    if summary.get("status") == "model_missing":
        return 3
    if summary.get("missing_voters") and summary.get("status") in ("ok", "rehearsal_ok"):
        print(f"NIGHT_JOB omi_repass DEGRADED: expected voter(s) did not vote: {summary['missing_voters']}", file=sys.stderr)
        return 3                                  # the window reads exit 3 as `degraded`: a missing voter is in the headline, never a green night with two voters
    if summary.get("rehearse") and summary.get("status") != "rehearsal_ok":
        return 4                                  # a rehearsal that did not pass must fail loudly (the window reports exit 5); it never ends "ok" on an idle / flag / memory refusal
    return rc


if __name__ == "__main__":
    sys.exit(main())
