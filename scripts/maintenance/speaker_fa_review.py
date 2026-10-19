#!/usr/bin/env python3
"""Speaker false-accept review for the panel voice password (ResNet34 acting, ``ZOE_VOICE_SESSION=1``).

Since 2026-10-18 23:13 a verified voice opens a short private session at the panel, so a false accept is a privacy leak
and a false reject is a PIN prompt. The ResNet34 thresholds (0.233 for 2 s or less, 0.265 longer; FAR 1 %) were derived
on PUBLIC impostors and are "starting values ... to be re-derived on labelled household turns"
(docs/research/speaker-resnet34-optimisation-2026-10.md 4.3). This is the tool that collects those labels.

Sub-commands:

    collect     recent panel turns -> a labelling sheet (CSV) OUTSIDE the repo, under ~/.zoe/voice-gate/speaker-fa/.
                Sources: the Pi voice.log (read-only over ssh: the speaker score, length, outcome, transcript) and the
                zoe-data app logs ~/.zoe-logs/zoe-data.app.log* (NOT journald: zoe-data logs there; the server verdict).
                The corpus clip of each turn (~/.zoe-voice-samples, when ZOE_VOICE_SAVE_AUDIO kept it) is linked by
                time and exact length. Re-running merges: rows already labelled keep their label.
    label       walk the unlabelled rows in a terminal and answer m (me) / n (not me) / s (skip) / q (quit);
                ``--play`` plays each clip with aplay first. Editing the CSV by hand works too.
    score       false-accept / false-reject rates at the LIVE thresholds from the labelled rows, with 95 % Wilson
                intervals, plus a what-if sweep of one global threshold. Aggregates only.
    offline-fa  public impostors (the speaker bake-off LibriSpeech crops and Kokoro TTS voices, clean + far-field)
                against the LIVE enrolled ResNet34 profile(s) at the live thresholds. Loads the panel embedder
                (scripts/setup/zoe_speaker_resnet34.py, sha-pinned) on this box: guarded like a voice harness.

Labels: ``me`` = the enrolled owner spoke, ``not me`` = anyone or anything else (another person, the TV, a guest).
FA = a "not me" row the gate accepted; FR = a "me" row the gate did not accept as the owner (an unscored turn, e.g.
no claim, counts as a reject - the household felt it as a guest turn).

Nothing here changes a live system. The sheet holds household data (transcript snippets, clip paths): it is written
mode 600 (never through a symlink) in a mode 700 directory, only under ~/.zoe/voice-gate/ and never inside the repo. Runbook:
docs/knowledge/wake-and-speaker-fa-instruments.md.

    python3 scripts/maintenance/speaker_fa_review.py collect
    python3 scripts/maintenance/speaker_fa_review.py label --play
    python3 scripts/maintenance/speaker_fa_review.py score --owner <enrolled member id>
    flock -x -w "$(python3 scripts/maintenance/harness_quiet.py brain-window-wait --budget-min 35)" \\
        /tmp/zoe-voice-harness.lock nice -n 10 python3 scripts/maintenance/speaker_fa_review.py offline-fa

``offline-fa`` also holds the 01:50-04:55 brain window (``harness_quiet.brain_window_verdict``, scripts/AGENTS.md "Brain-window
quiet hours"): it refuses a start inside it and a start whose expected length (clip count, or ``--budget-min``) would still be
alive at 01:50. ``--force`` skips the quiet hours, never the window.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parents[1] / "lib"))
sys.path.insert(0, str(HERE.parent))

import panel_voice_log as pvl  # noqa: E402

HOME = Path.home()
VOICE_GATE_ROOT = HOME / ".zoe" / "voice-gate"     # the ONLY place the sheet and results may land
DEFAULT_DIR = VOICE_GATE_ROOT / "speaker-fa"
PLAYERS = ("aplay", "paplay", "pw-play", "ffplay", "play")   # --play-cmd must start with one of these
APP_LOGS = str(HOME / ".zoe-logs" / "zoe-data.app.log*")
PANEL_ID = "zoe-touch-pi"
SHORT_MAX_S = 2.0
LIVE_THRESHOLDS = dict(short=0.233, long=0.265)      # far1, scripts/setup/zoe_speaker_resnet34.py THRESHOLDS
RESNET34_ACTING_SINCE = dt.datetime(2026, 10, 18, 23, 13, 39)   # ZOE_SPEAKER_MODEL=resnet34 on both hosts
DEFAULT_SINCE = dt.datetime(2026, 10, 13)            # dual-mode ResNet34 lines start here
IMPOSTORS = HOME / ".cache" / "zoe-speaker-bakeoff" / "impostors"
MODEL_DIR = HOME / ".cache" / "zoe-speaker-resnet34"
PI_PROFILES = "/home/pi/.zoe-voice/speaker_profiles.json"
FIELDS = ("turn_id", "time", "kind", "seconds", "group", "threshold", "model", "claimed_user", "score",
          "panel_accept", "server_identified", "server_verified", "outcome", "transcript", "clip", "label", "notes")



def length_group(seconds: Optional[float]) -> str:
    """Mirror of zoe_speaker_resnet34.length_group: 2 s or less is short; unknown is long (the stricter bar)."""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return "long"
    return "short" if (math.isfinite(s) and 0 < s <= SHORT_MAX_S) else "long"


def live_threshold(group: str, thresholds: dict = LIVE_THRESHOLDS) -> float:
    return thresholds["short" if group == "short" else "long"]


# --------------------------------------------------------------------------------------------- server log

_APP_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-]\d{4})? ")
_CMD = re.compile(r"voice/command panel=(\S+) session=\S+ user=(\S+) len=\d+ \[identity: identified=(\S+)")
_VERDICT = re.compile(r"voice speaker verdict panel=(\S+) verified=(True|False)")
_REJECT = re.compile(r"voice claim rejected: user=(\S+) score=([0-9.]+) < threshold=([0-9.]+)")


def server_events(lines: Iterable[str], panel: str = PANEL_ID) -> list:
    """[(ts, kind, data)] for this panel: command (user, identified), verdict (verified), reject (user, score). Times are
    naive local (the log carries the box offset). A rejected-claim line names no panel: kept, joined by time."""
    out = []
    for line in lines:
        m = _APP_TS.match(line)
        if not m:
            continue
        ts = dt.datetime.fromisoformat(m.group(1))
        if (c := _CMD.search(line)):
            if c.group(1) == panel:
                ident = None if c.group(3) in ("None", "") else c.group(3)
                out.append((ts, "command", dict(user=c.group(2), identified=ident)))
        elif (v := _VERDICT.search(line)):
            if v.group(1) == panel:
                out.append((ts, "verdict", dict(verified=v.group(2) == "True")))
        elif (r := _REJECT.search(line)):
            out.append((ts, "reject", dict(user=r.group(1), score=float(r.group(2)), threshold=float(r.group(3)))))
    out.sort(key=lambda e: e[0])
    return out


def read_app_logs(pattern: str = APP_LOGS) -> list:
    lines: list = []
    for path in sorted(glob.glob(os.path.expanduser(pattern))):
        if path.endswith(".gz"):
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines.extend(fh.read().splitlines())
    return lines


def join_server(turn_ts: dt.datetime, sev: Sequence, used: set, *, before_s: float = 2.0,
                after_s: float = 60.0, until: Optional[dt.datetime] = None) -> dict:
    """The first unused panel command line in [turn - before_s, min(turn + after_s, until)), plus the verdict / rejection
    logged within 3 s before it (they are logged on the way into the same handler). Second-resolution server clock.

    ``until`` is the next panel turn's trigger: a turn's command is logged before the next turn starts, so a turn that
    made no command (empty, rejected, retried) must not consume the NEXT turn's command inside the 60 s window."""
    out: dict = {}
    for i, (ts, kind, data) in enumerate(sev):
        if kind != "command" or i in used:
            continue
        gap = (ts - turn_ts).total_seconds()
        if gap < -before_s:
            continue
        if gap > after_s or (until is not None and ts >= until):
            break
        used.add(i)
        out["identified"] = data["identified"]
        for ts2, kind2, d2 in sev:
            if 0 <= (ts - ts2).total_seconds() <= 3.0:
                if kind2 == "verdict":
                    out["verified"] = d2["verified"]
                elif kind2 == "reject":
                    out["reject"] = d2
        return out
    return out



# --------------------------------------------------------------------------------------------- rows

def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def turn_score(t, acting_since: dt.datetime) -> Optional[tuple]:
    """(model, user, score) of the ResNet34 decision for this turn, or None when the turn has no ResNet34 meaning (the
    Resemblyzer era). In ``dual`` the ResNet34 score is the dual line (logged, not acted on); once ResNet34 acts, the
    ``Speaker claim`` line IS the ResNet34 claim, and a turn with no claim is an unscored reject."""
    if t.dual_score is not None or t.dual_user is not None:
        return ("resnet34-dual", t.dual_user, t.dual_score)
    if t.trigger_ts >= acting_since:
        return ("resnet34", t.claim_user, t.claim_score)
    return None


def build_rows(tns: Sequence, sev: Sequence, clips: dict, *, since: dt.datetime,
               acting_since: dt.datetime = RESNET34_ACTING_SINCE, thresholds: dict = LIVE_THRESHOLDS,
               transcript_chars: int = 60) -> list:
    rows: list = []
    used: set = set()
    for ti, t in enumerate(tns):
        if t.trigger_ts < since or t.trigger not in ("wake", "followup", "orb"):
            continue
        sc = turn_score(t, acting_since)
        if sc is None:
            continue
        model, user, score = sc
        group = length_group(t.duration_s)
        thr = live_threshold(group, thresholds)
        # "Command too short" is rejected on the panel before any /voice/command; every other outcome may have reached the
        # server, so those are bounded by the next panel turn instead (join_server ``until``)
        nxt = tns[ti + 1].trigger_ts if ti + 1 < len(tns) else None
        srv = {} if t.outcome == "too_short" else join_server(t.recorded_ts or t.trigger_ts, sev, used, until=nxt)
        clip = clips.get(ti)
        rows.append(dict(
            turn_id=t.trigger_ts.isoformat(timespec="milliseconds"), time=t.trigger_ts.strftime("%Y-%m-%d %H:%M:%S"),
            kind=t.trigger, seconds="" if t.duration_s is None else f"{t.duration_s:.2f}", group=group, threshold=f"{thr:.3f}", model=model,
            claimed_user="" if user in (None, "None") else user, score=_fmt(score),
            panel_accept=_fmt(score is not None and score >= thr),
            notes=("" if t.dual_accept is None or t.dual_accept == (score is not None and score >= thr)
                   else f"panel logged accept={t.dual_accept} (threshold then differed)"),
            server_identified=srv.get("identified") or ("" if not srv else "none"),
            server_verified=_fmt(srv.get("verified")), outcome=t.outcome,
            transcript=(t.transcript or "")[:transcript_chars], clip=clip.path if clip else "", label=""))
    return rows


LABELS = {"me": "me", "m": "me", "y": "me", "yes": "me", "owner": "me",
          "not me": "not me", "notme": "not me", "not_me": "not me", "n": "not me", "no": "not me", "other": "not me",
          "skip": "skip", "s": "skip", "?": "skip"}


def norm_label(raw: Optional[str]) -> str:
    return LABELS.get((raw or "").strip().lower(), "")


def merge(old: Sequence[dict], new: Sequence[dict]) -> list:
    """New rows, with the label / notes of any row already in the sheet (same turn_id) carried over; old rows that a
    shorter log window no longer covers are kept, so a label is never lost."""
    keep = {r["turn_id"]: r for r in old}
    out = []
    for r in new:
        prev = keep.pop(r["turn_id"], None)
        if prev:
            r = dict(r, label=prev.get("label", ""), notes=prev.get("notes") or r.get("notes", ""))
        out.append(r)
    out.extend(keep.values())
    out.sort(key=lambda r: r["turn_id"])
    return out



# --------------------------------------------------------------------------------------------- the private sheet

def private_dir(d: Path, root: Path = VOICE_GATE_ROOT) -> Path:
    """``d`` created 700, but only at or under ``root`` (~/.zoe/voice-gate) and never inside the checkout."""
    d = Path(d).expanduser().resolve()
    root = Path(root).expanduser().resolve()
    if d == REPO or REPO in d.parents or not (d == root or root in d.parents):
        raise SystemExit(f"refusing to keep household data outside {root} (or inside the repo): {d}")
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def write_private(path: Path, text: str) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)   # never through a symlink
    with os.fdopen(fd, "w", newline="") as fh:
        fh.write(text)
    os.chmod(path, 0o600)


def read_sheet(path: Path) -> list:
    if not Path(path).is_file():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [dict((k, (v or "")) for k, v in r.items() if k in FIELDS) for r in csv.DictReader(fh)]


def write_sheet(path: Path, rows: Sequence[dict]) -> None:
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(dict((k, r.get(k, "")) for k in FIELDS))
    write_private(Path(path), buf.getvalue())


# --------------------------------------------------------------------------------------------- scoring

def wilson(k: int, n: int, z: float = 1.96) -> tuple:
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _float(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def rates(rows: Sequence[dict], *, owner: str, thresholds: Optional[dict] = None,
          global_threshold: Optional[float] = None) -> dict:
    """FA / FR over labelled rows. A row is ACCEPTED when its score clears the threshold of its length group (the
    live pair by default, or one ``global_threshold``). FA = accepted "not me" rows; FR = "me" rows not accepted AS
    the owner. ``owner`` is ground truth from the operator (the enrolled member id), never inferred from the claims
    being scored - that would let a systematic misidentification grade itself."""
    if not owner:
        raise ValueError("owner is required (the enrolled member id the me rows belong to)")
    lab = [r for r in rows if norm_label(r.get("label")) in ("me", "not me")]
    thr = thresholds or LIVE_THRESHOLDS
    fa = fr = n_me = n_not = unscored_me = 0
    for r in lab:
        score = _float(r.get("score"))
        t = global_threshold if global_threshold is not None else live_threshold(r.get("group") or "long", thr)
        accepted = score is not None and score >= t
        if norm_label(r["label"]) == "me":
            n_me += 1
            unscored_me += score is None
            fr += not (accepted and r.get("claimed_user") == owner)
        else:
            n_not += 1
            fa += accepted
    return dict(owner=owner, n_me=n_me, n_not_me=n_not, false_rejects=fr, false_accepts=fa,
                unscored_me=unscored_me, frr=(fr / n_me) if n_me else float("nan"), frr_ci=wilson(fr, n_me),
                far=(fa / n_not) if n_not else float("nan"), far_ci=wilson(fa, n_not))


def disagreements(rows: Sequence[dict]) -> list:
    """Rows where the panel-side accept and the server identity disagree (an instrument check: they share thresholds,
    so a disagreement is a consent, model-id or join problem worth reading)."""
    out = []
    for r in rows:
        if r.get("model") != "resnet34" or not r.get("server_identified"):
            continue
        srv = r["server_identified"] not in ("", "none")
        if (r.get("panel_accept") == "yes") != srv:
            out.append(r["turn_id"])
    return out



# --------------------------------------------------------------------------------------------- offline FA (public)

def score_impostors(items: Sequence[dict], profiles: Sequence[dict], *, base: Path, embed_wav: Callable,
                    match: Callable, thresholds: dict = LIVE_THRESHOLDS, strict: Optional[dict] = None) -> tuple:
    """Each public clip against the enrolled profile(s): (results, skipped). ``embed_wav(bytes) -> (vec, seconds)`` and
    ``match(vec, profiles) -> (user, score, n)`` are the panel module functions in production."""
    results, skipped = [], 0
    for it in items:
        try:
            vec, seconds = embed_wav((Path(base) / it["path"]).read_bytes())
        except (OSError, ValueError):
            vec, seconds = None, 0.0
        if vec is None:
            skipped += 1
            continue
        user, score, _n = match(vec, profiles)
        group = length_group(seconds)
        thr = live_threshold(group, thresholds)
        row = dict(source=it.get("source", "?"), variant=it.get("variant", "?"), group=group,
                   score=None if score is None else float(score), accept=score is not None and score >= thr)
        if strict:
            row["accept_strict"] = score is not None and score >= live_threshold(group, strict)
        results.append(row)
    return results, skipped


def summarise_impostors(results: Sequence[dict]) -> dict:
    def block(rs):
        k, n = sum(1 for r in rs if r["accept"]), len(rs)
        sc = sorted(r["score"] for r in rs if r["score"] is not None)
        out = dict(n=n, accepted=k, rate=(k / n) if n else float("nan"), ci=wilson(k, n),
                   max=sc[-1] if sc else None, p99=sc[min(len(sc) - 1, int(round(0.99 * (len(sc) - 1))))] if sc else None)
        if rs and "accept_strict" in rs[0]:
            out["accepted_strict"] = sum(1 for r in rs if r.get("accept_strict"))
        return out
    out = dict(all=block(results))
    for key in ("source", "group"):
        for v in sorted({r[key] for r in results}):
            out[f"{key}={v}"] = block([r for r in results if r[key] == v])
    for s in sorted({r["source"] for r in results}):
        for v in sorted({r["variant"] for r in results}):
            rs = [r for r in results if r["source"] == s and r["variant"] == v]
            if rs:
                out[f"{s}/{v}"] = block(rs)
    return out


def fetch_profiles(local: Optional[str], *, host: str = pvl.PANEL_HOST, runner: Callable = subprocess.run) -> list:
    """The enrolled profiles (the Pi file, read-only over ssh, kept in memory only; or a local copy)."""
    if local:
        raw = Path(local).expanduser().read_text()
    else:
        res = runner(["timeout", "20", "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
                      "cat " + PI_PROFILES], capture_output=True, text=True, timeout=25, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"could not read {host}:{PI_PROFILES} (exit {res.returncode})")
        raw = res.stdout
    data = json.loads(raw)
    return list(data.get("profiles", []) if isinstance(data, dict) else data)


def mem_available_mb(path: str = "/proc/meminfo") -> Optional[float]:
    try:
        with open(path) as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        return None
    return None


SEC_PER_CLIP = 1.0     # one ResNet34 embedding per impostor clip, rounded up (the panel logs 192 ms on a Pi)
OVERHEAD_MIN = 5       # embedder load, the ssh profile read, the report


def estimate_budget_min(n_clips: int) -> int:
    """Minutes an offline-fa over ``n_clips`` clips is expected to take, for the brain-window start guard."""
    return int(math.ceil(max(0, n_clips) * SEC_PER_CLIP / 60.0)) + OVERHEAD_MIN


def window_refusal(budget_min: int, *, verdict: Optional[Callable] = None) -> Optional[str]:
    """None when a run of ``budget_min`` minutes starting now stays clear of the 01:50-04:55 brain window
    (scripts/AGENTS.md "Brain-window quiet hours"), else the refusal. The shared rule is
    ``harness_quiet.brain_window_verdict``; ``--force`` / ``ZOE_HARNESS_FORCE`` do NOT reach it."""
    if verdict is None:
        import harness_quiet
        verdict = harness_quiet.brain_window_verdict
    v = verdict("speaker_fa_review", budget_min=budget_min)
    return None if v.allowed else v.reason


def guard(*, force: bool, min_avail_mb: float, check: Optional[Callable] = None,
          mem: Optional[Callable[[], Optional[float]]] = None, budget_min: int = 0,
          window: Optional[Callable[[int], Optional[str]]] = None) -> Optional[str]:
    """None when an offline-fa may start now, else the refusal: the brain window (never forced), harness quiet hours /
    panel in use, then memory. ``budget_min`` = 0 refuses only a start inside the window; ``cmd_offline_fa`` re-checks
    with the clip-count estimate once the manifest is read."""
    why = (window or window_refusal)(budget_min)
    if why:
        return why
    if check is None:
        import harness_quiet
        check = harness_quiet.check
    v = check("speaker_fa_review", force=True if force else None)
    if not v.allowed:
        return v.reason
    m = (mem or mem_available_mb)()
    if m is not None and m < min_avail_mb:
        return f"MemAvailable {m:.0f} MB < {min_avail_mb:.0f} MB - the live voice stack comes first"
    return None



# --------------------------------------------------------------------------------------------- commands

def _pct(v, ci=None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    s = f"{100.0 * v:.1f} %"
    if ci and not math.isnan(ci[0]):
        s += f" [{100.0 * ci[0]:.1f} - {100.0 * ci[1]:.1f}]"
    return s


def cmd_collect(args) -> int:
    tns = pvl.turns(pvl.events(pvl.fetch_lines(args.voice_log)))
    clips = pvl.match_clips(tns, pvl.corpus_index(args.corpus))
    sev = server_events(read_app_logs(args.app_logs), args.panel)
    rows = build_rows(tns, sev, clips, since=args.since, acting_since=args.acting_since)
    d = private_dir(Path(args.dir))
    sheet = d / "sheet.csv"
    merged = merge(read_sheet(sheet), rows)
    write_sheet(sheet, merged)
    acting = [r for r in merged if r["model"] == "resnet34"]
    print(f"sheet: {sheet} ({len(merged)} rows; {len(acting)} with ResNet34 acting since "
          f"{args.acting_since:%Y-%m-%d %H:%M}, {len(merged) - len(acting)} dual-era rows (logged, not acted on))")
    print(f"unlabelled: {sum(1 for r in merged if not norm_label(r.get('label')))}; with a clip: "
          f"{sum(1 for r in merged if r.get('clip'))}; joined to a server line: "
          f"{sum(1 for r in merged if r.get('server_identified'))}")
    dis = disagreements(merged)
    if dis:
        print(f"panel accept vs server identity disagree on {len(dis)} row(s): {', '.join(dis[:5])}")
    print("next: label each row me / not me (the label command, or edit the CSV), then run score")
    return 0


def player_argv(play_cmd: str) -> list:
    """The player command as argv, refused unless it is a known audio player (it receives a household clip path)."""
    argv = play_cmd.split()
    if not argv or Path(argv[0]).name not in PLAYERS:
        raise SystemExit(f"--play-cmd must start with one of {', '.join(PLAYERS)}")
    return argv


def cmd_label(args) -> int:
    if args.play:
        player_argv(args.play_cmd)
    sheet = private_dir(Path(args.dir)) / "sheet.csv"
    rows = read_sheet(sheet)
    todo = [r for r in rows if not norm_label(r.get("label"))]
    print(f"{len(todo)} unlabelled of {len(rows)} in {sheet}. Answer m = me, n = not me, s = skip, q = quit.")
    for r in todo:
        print(f"\n{r['time']}  {r['kind']}  {r['seconds']} s  score {r['score'] or '-'} (needs {r['threshold']})  "
              f"panel accept {r['panel_accept'] or '-'}  server {r['server_identified'] or '-'}")
        if r.get("transcript"):
            print(f"  heard: {r['transcript']}")
        if args.play and r.get("clip") and Path(r["clip"]).is_file():
            subprocess.run(player_argv(args.play_cmd) + [r["clip"]], check=False)
        try:
            ans = input("  me / not me / skip / quit [m/n/s/q]: ").strip().lower()
        except EOFError:
            break
        if ans in ("q", "quit"):
            break
        lab = norm_label(ans)
        if lab:
            r["label"] = lab
            write_sheet(sheet, rows)
    print(f"saved {sheet}")
    return 0


def cmd_score(args) -> int:
    d = private_dir(Path(args.dir))
    rows = read_sheet(d / "sheet.csv")
    if args.acting_only:
        rows = [r for r in rows if r.get("model") == "resnet34"]
    live = rates(rows, owner=args.owner)
    print(f"Labelled: {live['n_me']} me, {live['n_not_me']} not me (owner = {live['owner']}).")
    print(f"At the live thresholds (short {LIVE_THRESHOLDS['short']} / long {LIVE_THRESHOLDS['long']}): "
          f"false accept {live['false_accepts']}/{live['n_not_me']} = {_pct(live['far'], live['far_ci'])}; "
          f"false reject {live['false_rejects']}/{live['n_me']} = {_pct(live['frr'], live['frr_ci'])} "
          f"({live['unscored_me']} of them unscored turns).")
    print("\n| one global threshold | false accept | false reject |\n|---|---|---|")
    sweep = []
    for i in range(0, 31):
        t = round(0.15 + 0.01 * i, 2)
        r = rates(rows, owner=live["owner"], global_threshold=t)
        sweep.append(dict(threshold=t, far=r["far"], frr=r["frr"]))
        if i % 2 == 0:
            print(f"| {t:.2f} | {_pct(r['far'])} | {_pct(r['frr'])} |")
    if live["n_me"] + live["n_not_me"] < 30:
        print("\n(fewer than 30 labelled rows: these are anecdotes, not rates - keep labelling)")
    write_private(d / "score.json", json.dumps(dict(live=live, sweep=sweep, thresholds=LIVE_THRESHOLDS,
                                                   at=dt.datetime.now().isoformat(timespec="seconds")), indent=1))
    return 0



def cmd_offline_fa(args) -> int:
    why = guard(force=args.force, min_avail_mb=args.min_avail_mb)
    if why:
        print(f"speaker_fa_review offline-fa: refused - {why}", file=sys.stderr)
        return 3
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    base = Path(args.impostors).expanduser()
    items = json.loads((base / "manifest.json").read_text())
    if args.limit:
        items = items[: args.limit]
    why = window_refusal(args.budget_min or estimate_budget_min(len(items)))   # the count is known: refuse a run into 01:50
    if why:
        print(f"speaker_fa_review offline-fa: refused - {why}", file=sys.stderr)
        return 3
    sys.path.insert(0, str(REPO / "scripts" / "setup"))
    import zoe_speaker_resnet34 as rn34
    if dict(rn34.THRESHOLDS["far1"]) != LIVE_THRESHOLDS:
        print("speaker_fa_review: LIVE_THRESHOLDS drifted from zoe_speaker_resnet34.THRESHOLDS far1", file=sys.stderr)
        return 2
    profiles = [p for p in fetch_profiles(args.profiles) if rn34.profile_model_id(p) == rn34.MODEL_ID]
    if not profiles:
        print("speaker_fa_review: no ResNet34 profile enrolled - nothing to attack", file=sys.stderr)
        return 2
    # the panel's centring artifacts are the repo copy (byte-identical to the Pi's); a stale copy beside a local model
    # file would be refused by the pin anyway, so name the repo one explicitly
    emb = rn34.Resnet34Embedder.load(str(Path(args.model_dir).expanduser()),
                                     artifacts_path=str(REPO / "scripts" / "setup" / rn34.ARTIFACTS_FILENAME))
    t0 = time.time()
    results, skipped = score_impostors(items, profiles, base=base, embed_wav=emb.embed_wav,
                                       match=lambda v, ps: rn34.match_profiles(v, ps, rn34.MODEL_ID),
                                       strict=dict(rn34.THRESHOLDS["far01"]))
    summ = summarise_impostors(results)
    print(f"Public impostors vs the {len(profiles)} enrolled ResNet34 profile(s), live thresholds "
          f"{LIVE_THRESHOLDS['short']} / {LIVE_THRESHOLDS['long']} ({len(results)} clips, {skipped} skipped, "
          f"{time.time() - t0:.0f} s):\n")
    print("| slice | clips | accepted | rate [95 % CI] | max score | accepted at FAR 0.1 % tier |\n|---|---|---|---|---|---|")
    for k, b in summ.items():
        mx = "n/a" if b["max"] is None else f"{b['max']:.3f}"
        print(f"| {k} | {b['n']} | {b['accepted']} | {_pct(b['rate'], b['ci'])} | {mx} | {b.get('accepted_strict', '-')} |")
    d = private_dir(Path(args.dir))
    write_private(d / "offline-fa.json", json.dumps(dict(summary=summ, n_profiles=len(profiles), skipped=skipped,
                                                         thresholds=LIVE_THRESHOLDS, model_id=rn34.MODEL_ID,
                                                         at=dt.datetime.now().isoformat(timespec="seconds")),
                                                    indent=1, default=str))
    return 0


def _date(raw: str) -> dt.datetime:
    return dt.datetime.fromisoformat(raw)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help="where the sheet and results live (outside the repo)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="recent panel turns -> the labelling sheet")
    c.add_argument("--voice-log", help="a local copy of the panel voice.log (default: read it over ssh)")
    c.add_argument("--app-logs", default=APP_LOGS, help="glob of zoe-data app logs (not journald)")
    c.add_argument("--corpus", default=pvl.CORPUS_DIR)
    c.add_argument("--panel", default=PANEL_ID)
    c.add_argument("--since", type=_date, default=DEFAULT_SINCE)
    c.add_argument("--acting-since", type=_date, default=RESNET34_ACTING_SINCE)
    lb = sub.add_parser("label", help="label unlabelled rows in the terminal")
    lb.add_argument("--play", action="store_true", help="play each clip before asking")
    lb.add_argument("--play-cmd", default="aplay -q")
    s = sub.add_parser("score", help="FA / FR at the live thresholds from the labelled rows")
    s.add_argument("--owner", required=True, help="the enrolled member id the me rows belong to (ground truth)")
    s.add_argument("--acting-only", action="store_true", help="only rows where ResNet34 was the acting model")
    o = sub.add_parser("offline-fa", help="public impostors vs the live enrolled profile (loads the model; guarded)")
    o.add_argument("--profiles", help="a local copy of speaker_profiles.json (default: the Pi, read-only)")
    o.add_argument("--impostors", default=str(IMPOSTORS))
    o.add_argument("--model-dir", default=str(MODEL_DIR))
    o.add_argument("--limit", type=int, default=0)
    o.add_argument("--min-avail-mb", type=float, default=1500.0)
    o.add_argument("--force", action="store_true",
                   help="skip the harness quiet hours (deliberate); the 01:50-04:55 brain window is NEVER skipped")
    o.add_argument("--budget-min", type=int, default=0,
                   help="expected run length for the brain-window start check (default: estimated from the clip count)")
    args = ap.parse_args(argv)
    return dict(collect=cmd_collect, label=cmd_label, score=cmd_score, **{"offline-fa": cmd_offline_fa})[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
