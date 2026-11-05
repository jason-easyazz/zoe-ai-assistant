#!/usr/bin/env python3
"""Offline simulation of the panel's duck -> decide -> resume barge-in policy.

WHY THIS EXISTS
---------------
Samantha Initiative lane E, experiment E2 (docs/research/samantha-initiative-2026-11-03.md;
design: docs/research/barge-in-duck-decide-resume-2026-10-04.md). The policy
(BARGE_DUCK_ENABLED, flag-dark) has unit tests but has NEVER been run against a room. Before the
owner's lab hour (docs/knowledge/barge-in-lab-script.md) this script drives the REAL daemon code
- ``_BargeDetector`` -> ``_BargeEpisode`` -> ``_BargeDecider`` -> ``_SinkInputDucker`` - through
scripted per-chunk Silero probability profiles on a fake clock with a pactl spy that models
PulseAudio's per-stream volume, and looks for STUCK STATES:

  * ``open_episode``      an episode still undecided after the decide ceiling (+1 chunk);
  * ``stuck_mute``        the player is alive, no episode is open, and its stream volume is not
                          back at the baseline (the "everything is quiet forever" failure);
  * ``kill_without_commit`` the player was terminated by anything but a COMMIT;
  * ``commit_not_terminated`` a COMMIT that left the player running;
  * ``multi_outcome``     more than one BARGE_DECIDE line for one episode;
  * ``late_outcome``      an outcome later than the ceiling;
  * ``sentinel_commit``   a COMMIT the profile's speech chunks cannot justify (a broken VAD,
                          the -1.0 sentinel, must never commit).

Every detector has a NEGATIVE CONTROL (``mutations``): the same checker run against a
deliberately broken policy must report the matching violation, otherwise the checker is blind.

Three things it can run (``--help``):
  --lab         the owner's 10 scripted barges as synthetic profiles -> predicted outcome each;
  --fuzz N      N random profiles (speech bursts, quiet, sentinel, player exit, failing pactl);
  --corpus DIR  REAL Silero probabilities of the owner's quarantine clips (non-speech, TV
                false wakes) and of the command clips themselves, placed after a quiet lead and
                pushed through the same policy. Aggregates only; no clip names or audio leave.

The probabilities in --lab/--fuzz are SYNTHETIC: they prove the state machine, not how Silero
scores a real cough. The corpus mode does use real Silero output. Nothing live is touched.
Takes the same quiet guard as smart_turn_offline_eval.py (--lock, --min-mem-mb) for --corpus.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import logging
import random
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_ste_for_barge_sim", HERE / "smart_turn_offline_eval.py")
ste = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ste)

CHUNK_S = 0.08
LEAD = 14                # quiet chunks before the barge: past the 800 ms onset grace (10 chunks)
TAIL = 40                # quiet chunks after: the decide ceiling (2 s = 25 chunks) always elapses
BASELINE = 65536         # PulseAudio 100 %
PLAYER_PID = 4242

LISTING = (
    "Sink Input #{idx}\n\tDriver: protocol-native.c\n\tSink: 1\n"
    "\tVolume: mono: {vol} / {pct}% / 0.00 dB\n\tProperties:\n"
    "\t\tapplication.name = \"ALSA plug-in [aplay]\"\n\t\tapplication.process.id = \"{pid}\"\n"
)


class FakeProc:
    """Stands in for the aplay subprocess."""

    def __init__(self, pid: int = PLAYER_PID):
        self.pid = pid
        self.terminated = False
        self.exited = False

    def poll(self):
        return 0 if (self.terminated or self.exited) else None

    def terminate(self):
        self.terminated = True


class PactlSpy:
    """pactl with PulseAudio's volume model: ``set-sink-input-volume IDX Ndb`` is relative
    (multiplies), ``IDX N`` is absolute. ``fail_sets``: the duck lands, every later set fails (non-zero exit)."""

    def __init__(self, pid: int = PLAYER_PID, volume: int = BASELINE, fail_sets: bool = False,
                 no_sink: bool = False):
        self.pid, self.volume, self.fail_sets, self.no_sink = pid, volume, fail_sets, no_sink
        self.calls: list[tuple[str, ...]] = []
        self.sets = 0

    def __call__(self, *args: str):
        self.calls.append(args)
        if args[:2] == ("list", "sink-inputs"):
            if self.no_sink:
                return ""
            return LISTING.format(idx=40, vol=self.volume, pct=round(100 * self.volume / BASELINE), pid=self.pid)
        if args and args[0] == "set-sink-input-volume":
            self.sets += 1
            if self.fail_sets and self.sets > 1:      # the duck lands; every RESTORE after it fails
                return None
            v = args[2]
            if v.endswith("dB"):
                self.volume = int(round(self.volume * 10 ** (float(v[:-2]) / 20)))
            else:
                self.volume = int(v)
        return ""


class _Count(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.decides: list[str] = []

    def emit(self, record):
        msg = record.getMessage()
        if msg.startswith("BARGE_DECIDE "):
            self.decides.append(msg)


@contextlib.contextmanager
def _isolated(mod, pactl: PactlSpy):
    """Daemon globals the policy touches, saved and restored around one simulated playback."""
    saved = {k: getattr(mod, k) for k in ("_pactl", "_duck_leak", "_duck_heal_tried", "_PLAYOUT")}
    mod._pactl, mod._duck_leak, mod._duck_heal_tried = pactl, None, None
    mod._PLAYOUT = mod._PlayoutLedger()
    mod._barge_in_requested.clear()
    mod._set_turn_response(None)
    handler = _Count()
    mod.log.addHandler(handler)
    old_level, old_prop = mod.log.level, mod.log.propagate
    mod.log.setLevel(logging.INFO)
    mod.log.propagate = False         # the policy's own log lines are counted, not printed
    try:
        yield handler
    finally:
        mod.log.removeHandler(handler)
        mod.log.setLevel(old_level)
        mod.log.propagate = old_prop
        for k, v in saved.items():
            setattr(mod, k, v)
        mod._barge_in_requested.clear()


def simulate(mod, probs: list[float], *, exit_at: int | None = None, fail_sets: bool = False,
             no_sink: bool = False) -> dict[str, Any]:
    """One reply playing while ``probs`` (one Silero probability per 80 ms chunk, captured after
    playback began) arrive. Mirrors ``_barge_in_vad_thread``'s loop exactly. ``exit_at`` = the
    reply finishes at that chunk. Returns the raw facts; ``violations`` judges them."""
    proc, pactl = FakeProc(), PactlSpy(fail_sets=fail_sets, no_sink=no_sink)
    t0 = 1000.0
    with _isolated(mod, pactl) as handler:
        det = mod._BargeDetector()
        det.new_playback(t0)
        episode = None
        outcomes: list[str] = []
        fired = hard_stop = overdue = False
        ep_lines: list[int] = []        # BARGE_DECIDE lines written per episode
        ep_speech: list[float] = []     # speech_ms the decider had when each episode resolved
        base = 0
        onset_i = None
        for i, prob in enumerate(probs):
            cap = t0 + i * CHUNK_S
            now = cap + CHUNK_S
            if exit_at is not None and i >= exit_at:
                proc.exited = True
                if episode is not None:
                    episode.finish("ended", now)
                    ep_lines.append(len(handler.decides) - base)
                    ep_speech.append(episode.decider.speech_ms)
                    outcomes.append("ended")
                    episode = None
                break
            if episode is not None:
                out = episode.feed(prob, cap, now)
                if out:
                    ep_lines.append(len(handler.decides) - base)
                    ep_speech.append(episode.decider.speech_ms)
                    outcomes.append(out)
                    episode = None
                    if out == "commit":
                        break
                    det.rearm()
                elif episode.decider.elapsed_ms(now) > mod.BARGE_DECIDE_MAX_MS + 1.5 * CHUNK_S * 1000:
                    overdue = True        # still deciding well past the ceiling while the reply plays
                continue
            if det.feed(prob, cap):
                fired = True
                onset_i = i if onset_i is None else onset_i
                base = len(handler.decides)
                episode = mod._BargeEpisode.open("sim", proc, det, prob, cap)
                if episode is not None:
                    continue
                hard_stop = True          # duck unavailable: today's hard stop
                mod._fire_barge_in("sim", prob, det, proc)
                break
        else:
            if episode is not None:       # the reply ended while still deciding
                episode.finish("ended", t0 + len(probs) * CHUNK_S)
                ep_lines.append(len(handler.decides) - base)
                ep_speech.append(episode.decider.speech_ms)
                outcomes.append("ended")
                episode = None
        open_episode = episode is not None and not episode.done
        elapsed_open = (episode.decider.elapsed_ms(t0 + len(probs) * CHUNK_S) if open_episode else 0)
        return {
            "fired": fired, "hard_stop": hard_stop, "outcomes": outcomes, "decides": list(handler.decides),
            "terminated": proc.terminated, "exited": proc.exited, "volume": pactl.volume,
            "open_episode": open_episode, "open_ms": elapsed_open, "overdue": overdue, "leak": mod._duck_leak,
            "commit_ms": [int(m.split(" ms=")[1].split()[0]) for m in handler.decides],
            "speech_chunks": sum(1 for p in probs if p >= mod.BARGE_IN_THRESHOLD),
            "has_sentinel": any(p < 0 for p in probs), "ep_lines": ep_lines, "ep_speech_ms": ep_speech,
            "fail_sets": fail_sets,
        }


def violations(mod, r: dict[str, Any]) -> list[str]:
    """Stuck-state / contract checks over one ``simulate`` result. Empty = healthy."""
    v: list[str] = []
    ceiling_ms = mod.BARGE_DECIDE_MAX_MS + 1.5 * CHUNK_S * 1000
    if r["overdue"] or (r["open_episode"] and r["open_ms"] > ceiling_ms):
        v.append("open_episode")
    alive = not (r["terminated"] or r["exited"])
    if alive and not r["open_episode"] and abs(r["volume"] - BASELINE) > 1 and not r["hard_stop"]:
        v.append("stuck_mute")
    committed = "commit" in r["outcomes"]
    if r["terminated"] and not committed and not r["hard_stop"]:
        v.append("kill_without_commit")
    if committed and not r["terminated"]:
        v.append("commit_not_terminated")
    if any(n != 1 for n in r["ep_lines"]) or len(r["decides"]) != len(r["outcomes"]):
        v.append("multi_outcome")          # exactly ONE BARGE_DECIDE line per episode
    if any(ms > ceiling_ms for ms, o in zip(r["commit_ms"], r["outcomes"]) if o != "ended"):
        v.append("late_outcome")
    # a COMMIT must be justified by the speech THIS episode's decider heard (a broken VAD, the
    # -1.0 sentinel, is not speech) - not by speech elsewhere in the profile
    if any(o == "commit" and ms < mod.BARGE_COMMIT_SPEECH_MS for o, ms in zip(r["outcomes"], r["ep_speech_ms"])):
        v.append("sentinel_commit")
    return v


# ── the owner's 10 scripted barges (docs/knowledge/barge-in-lab-script.md) ─────────────────

def _seg(*parts: tuple[float, int]) -> list[float]:
    out: list[float] = []
    for p, n in parts:
        out += [p] * n
    return out


# (id, what is done, synthetic Silero shape, what the panel SHOULD do per the owner's intent,
#  what Phase 1 is predicted to do). "resume" = ducked then carries on; "commit" = stops.
LAB_BARGES: list[dict[str, Any]] = [
    {"id": 1, "what": "cough once", "shape": _seg((0.95, 3)), "should": "resume"},
    {"id": 2, "what": "say 'mm-hm'", "shape": _seg((0.9, 3), (0.1, 2), (0.9, 3)), "should": "resume"},
    {"id": 3, "what": "say 'uh-huh'", "shape": _seg((0.85, 4), (0.15, 2), (0.85, 3)), "should": "resume"},
    {"id": 4, "what": "scrape a chair / knock the table (non-speech thump)", "shape": _seg((0.45, 2), (0.6, 1), (0.2, 3)), "should": "resume"},
    {"id": 5, "what": "TV music or effects for 3 s (no speech)", "shape": _seg((0.3, 20), (0.55, 2), (0.3, 16)), "should": "resume"},
    {"id": 6, "what": "TV line of two words ('oh really')", "shape": _seg((0.9, 4), (0.2, 2), (0.9, 4)), "should": "resume"},
    {"id": 7, "what": "someone else says 'yeah okay' across the room", "shape": _seg((0.8, 3), (0.1, 3), (0.8, 4)), "should": "resume"},
    {"id": 8, "what": "say 'stop' (one word, firmly)", "shape": _seg((0.97, 6)), "should": "commit"},
    {"id": 9, "what": "say 'Zoe, stop talking' (about 1.2 s)", "shape": _seg((0.97, 15)), "should": "commit"},
    {"id": 10, "what": "say a full sentence over her (about 2.5 s)", "shape": _seg((0.97, 32)), "should": "commit"},
]


def lab_profile(shape: list[float]) -> list[float]:
    return [0.02] * LEAD + shape + [0.02] * TAIL


def commit_ms_of(r: dict[str, Any]) -> int | None:
    """The BARGE_DECIDE duration that belongs to the COMMIT episode (``commit_ms`` holds one per decided episode, in the order
    of ``outcomes``: a resume followed by a commit must not report the resume's time as the commit's)."""
    for outcome, ms in zip(r["outcomes"], r["commit_ms"]):
        if outcome == "commit":
            return ms
    return None


def predicted_outcome(r: dict[str, Any]) -> str:
    if "commit" in r["outcomes"]:
        return "commit"
    return "resume" if r["fired"] else "no-fire"


def run_lab(mod) -> dict[str, Any]:
    rows, correct = [], 0
    for b in LAB_BARGES:
        r = simulate(mod, lab_profile(b["shape"]))
        got = predicted_outcome(r)
        ok = (got == b["should"]) or (b["should"] == "resume" and got == "no-fire")
        correct += ok
        rows.append({"id": b["id"], "what": b["what"], "should": b["should"], "predicted": got,
                     "ok": ok, "violations": violations(mod, r),
                     "decide_ms": commit_ms_of(r)})
    return {"predicted_correct": correct, "of": len(rows), "rows": rows}


# ── fuzz + negative controls ───────────────────────────────────────────────────────────────

def random_profile(rng: random.Random) -> tuple[list[float], dict[str, Any]]:
    probs = [0.02] * rng.randint(0, 20)
    for _ in range(rng.randint(0, 6)):
        kind = rng.choice(["speech", "speech", "quiet", "quiet", "sentinel", "flicker"])
        n = rng.randint(1, 30)
        if kind == "speech":
            probs += [rng.uniform(0.6, 0.99)] * n
        elif kind == "quiet":
            probs += [rng.uniform(0.0, 0.3)] * n
        elif kind == "sentinel":
            probs += [-1.0] * n
        else:
            probs += [rng.choice([0.05, 0.9]) for _ in range(n)]
    probs += [0.02] * rng.randint(0, 60)
    opts: dict[str, Any] = {}
    if rng.random() < 0.3 and probs:
        opts["exit_at"] = rng.randint(0, len(probs))
    if rng.random() < 0.1:
        opts["fail_sets"] = True
    if rng.random() < 0.05:
        opts["no_sink"] = True
    return probs, opts


def run_fuzz(mod, n: int, seed: int = 1) -> dict[str, Any]:
    rng = random.Random(seed)
    bad: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    known_edge = 0
    for _ in range(n):
        probs, opts = random_profile(rng)
        r = simulate(mod, probs, **opts)
        for o in (r["outcomes"] or (["hard_stop"] if r["hard_stop"] else ["no-fire"])):
            outcomes[o] = outcomes.get(o, 0) + 1
        found = violations(mod, r)
        if opts.get("fail_sets") and found == ["stuck_mute"]:
            # KNOWN EDGE (documented in the lab script): the duck succeeded but the restore's pactl
            # failed while the reply keeps playing - it stays ducked until it ends. Reported
            # separately, never folded into "clean"; any OTHER violation still counts below.
            known_edge += 1
            continue
        for name in found:
            bad[name] = bad.get(name, 0) + 1
    return {"n": n, "seed": seed, "outcomes": outcomes, "violations": bad,
            "known_edge_ducked_after_failed_restore": known_edge}


def mutations(mod) -> dict[str, Callable[[], contextlib.AbstractContextManager]]:
    """Deliberately broken policies; the checker MUST flag each (negative controls)."""

    @contextlib.contextmanager
    def no_restore():
        orig = mod._SinkInputDucker.restore
        mod._SinkInputDucker.restore = lambda self: None
        try:
            yield
        finally:
            mod._SinkInputDucker.restore = orig

    @contextlib.contextmanager
    def never_decides():
        orig = mod._BargeDecider.feed
        mod._BargeDecider.feed = lambda self, prob, captured_at: ""
        try:
            yield
        finally:
            mod._BargeDecider.feed = orig

    @contextlib.contextmanager
    def kills_on_resume():
        orig = mod._BargeEpisode.finish

        def finish(self, outcome, now):
            if outcome == "resume":
                self.proc.terminate()
            return orig(self, outcome, now)
        mod._BargeEpisode.finish = finish
        try:
            yield
        finally:
            mod._BargeEpisode.finish = orig

    @contextlib.contextmanager
    def commits_on_sentinel():
        orig = mod._BargeDecider.feed

        def feed(self, prob, captured_at):
            if prob < 0 and self.onset_at is not None and not self.outcome:
                self.outcome = "commit"
                return "commit"
            return orig(self, prob, captured_at)
        mod._BargeDecider.feed = feed
        try:
            yield
        finally:
            mod._BargeDecider.feed = orig

    @contextlib.contextmanager
    def double_report():
        orig = mod._BargeEpisode.finish

        def finish(self, outcome, now):
            orig(self, outcome, now)
            self.done = False        # the guard against a second BARGE_DECIDE removed
            orig(self, outcome, now)
        mod._BargeEpisode.finish = finish
        try:
            yield
        finally:
            mod._BargeEpisode.finish = orig

    return {"stuck_mute": no_restore, "open_episode": never_decides,
            "kill_without_commit": kills_on_resume, "sentinel_commit": commits_on_sentinel,
            "multi_outcome": double_report}


# profiles that make each mutation observable
CONTROL_PROFILES = {
    "stuck_mute": lambda: lab_profile(_seg((0.95, 3))),
    "open_episode": lambda: lab_profile(_seg((0.95, 3))),
    "kill_without_commit": lambda: lab_profile(_seg((0.95, 3))),
    "sentinel_commit": lambda: lab_profile(_seg((0.95, 3), (-1.0, 20))) + [0.97] * 20,
    "multi_outcome": lambda: lab_profile(_seg((0.95, 3))),
}


def run_controls(mod) -> dict[str, bool]:
    """name -> True when the checker caught that broken policy AND stayed clean on the real one."""
    out = {}
    for name, ctx in mutations(mod).items():
        probs = CONTROL_PROFILES[name]()
        clean = name not in violations(mod, simulate(mod, probs))
        with ctx():
            caught = name in violations(mod, simulate(mod, probs))
        out[name] = bool(clean and caught)
    return out


# ── real-Silero corpus mode ───────────────────────────────────────────────────────────────

def clip_chunk_probs(vad_mod, path: Path, gt_threshold: float = 0.35) -> tuple[list[float], int] | None:
    audio = ste.read_wav_mono16(path)
    if audio is None or len(audio) < 4 * ste.HOP:
        return None
    hops = ste.hop_probs_for(vad_mod, audio)
    chunks = ste.chunk_probs_from_hops(hops, len(audio) // ste.CHUNK)
    speech = sum(1 for p in chunks if p >= gt_threshold)
    return chunks, speech


def run_corpus(mod, corpus: Path, groups: dict[str, list[Path]]) -> dict[str, Any]:
    vad_mod = ste.load_path("_zoe_voice_vad_barge_sim", ste.VOICE_VAD)
    out: dict[str, Any] = {}
    for name, files in groups.items():
        counts: dict[str, int] = {"clips": 0, "no-fire": 0, "resume": 0, "commit": 0, "stuck": 0}
        commit_ms: list[float] = []
        for path in files:
            cp = clip_chunk_probs(vad_mod, path)
            if cp is None:
                continue
            chunks, _ = cp
            r = simulate(mod, lab_profile(chunks))
            counts["clips"] += 1
            counts[predicted_outcome(r)] += 1
            counts["stuck"] += bool(violations(mod, r))
            if (ms := commit_ms_of(r)) is not None:
                commit_ms.append(ms)
        counts["commit_ms_p50"] = ste.pct(commit_ms, 50)
        out[name] = counts
    return out


def corpus_groups(corpus: Path, limit_speech: int) -> dict[str, list[Path]]:
    groups = {
        "nonspeech_quarantine": sorted((corpus / "quarantine-nonspeech-20260804").glob("*.wav")),
        "tv_false_wakes_quarantine": sorted((corpus / "quarantine-tv-falsewakes-20260719").glob("*.wav")),
    }
    speech = sorted(corpus.glob("*.wav"))
    groups["owner_commands"] = speech[-limit_speech:] if limit_speech else speech
    return groups


def speech_length_buckets(vad_mod, files: list[Path], mod) -> dict[str, Any]:
    """How the commit rate depends on how long the speech is (answers 'is a lone word enough?')."""
    edges = [(0, 480), (480, 720), (720, 960), (960, 1400), (1400, 10 ** 9)]
    tally = {f"{a}-{b if b < 10 ** 8 else 'inf'}ms": [0, 0] for a, b in edges}
    for path in files:
        cp = clip_chunk_probs(vad_mod, path)
        if cp is None:
            continue
        chunks, speech = cp
        ms = speech * 80
        key = next(f"{a}-{b if b < 10 ** 8 else 'inf'}ms" for a, b in edges if a <= ms < b)
        r = simulate(mod, lab_profile(chunks))
        tally[key][0] += 1
        tally[key][1] += "commit" in r["outcomes"]
    return {k: {"clips": n, "commit": c} for k, (n, c) in tally.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lab", action="store_true", help="the 10 scripted barges: predicted outcome each")
    ap.add_argument("--fuzz", type=int, default=0, help="N random profiles through the stuck-state checker")
    ap.add_argument("--controls", action="store_true", help="negative controls: broken policies MUST be flagged")
    ap.add_argument("--corpus", type=Path, help="real Silero over the owner's quarantine + command clips")
    ap.add_argument("--limit-speech", type=int, default=400, help="newest N command clips for --corpus (0 = all)")
    ap.add_argument("--lock", type=Path, help="flock this file around --corpus (the voice harness lock)")
    ap.add_argument("--min-mem-mb", type=int, default=800)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    mod = ste.load_daemon()
    mod.BARGE_DUCK_ENABLED = True
    summary: dict[str, Any] = {"policy": {
        "commit_speech_ms": mod.BARGE_COMMIT_SPEECH_MS, "resume_silence_ms": mod.BARGE_RESUME_SILENCE_MS,
        "decide_max_ms": mod.BARGE_DECIDE_MAX_MS, "duck_db": mod.BARGE_DUCK_DB,
        "grace_ms": mod.BARGE_GRACE_MS, "min_chunks": mod.BARGE_MIN_CHUNKS,
        "window_chunks": mod.BARGE_WINDOW_CHUNKS, "threshold": mod.BARGE_IN_THRESHOLD}}
    rc = 0
    if args.lab:
        lab = summary["lab"] = run_lab(mod)
        print(f"Scripted barges, predicted by the real policy on synthetic shapes: {lab['predicted_correct']}/{lab['of']}")
        for row in lab["rows"]:
            flag = "ok " if row["ok"] else "GAP"
            print(f"  {flag} #{row['id']:<2} should {row['should']:<6} predicted {row['predicted']:<8} "
                  f"{row['what']}{' ' + str(row['violations']) if row['violations'] else ''}")
        rc |= any(r["violations"] for r in lab["rows"])
    if args.controls:
        ctl = summary["controls"] = run_controls(mod)
        print("Negative controls (broken policy must be flagged):", ctl)
        rc |= not all(ctl.values())
    if args.fuzz:
        fz = summary["fuzz"] = run_fuzz(mod, args.fuzz)
        print(f"Fuzz {fz['n']} profiles: outcomes {fz['outcomes']}  violations {fz['violations'] or 'none'}  "
              f"known edge (ducked after a failed restore): {fz['known_edge_ducked_after_failed_restore']}")
        rc |= bool(fz["violations"])
    if args.corpus:
        groups = corpus_groups(args.corpus, args.limit_speech)

        def work():
            res = run_corpus(mod, args.corpus, groups)
            vad_mod = ste.load_path("_zoe_voice_vad_barge_sim2", ste.VOICE_VAD)
            res["commit_by_speech_length"] = speech_length_buckets(vad_mod, groups["owner_commands"], mod)
            return res
        summary["corpus"] = ste.guarded(args.lock, args.min_mem_mb, "barge corpus", work)
        print("Real Silero over the corpus (aggregates only):")
        print(json.dumps(summary["corpus"], indent=2))
        rc |= any(g.get("stuck") for g in summary["corpus"].values() if isinstance(g, dict) and "stuck" in g)
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return int(rc)


if __name__ == "__main__":
    sys.exit(main())
