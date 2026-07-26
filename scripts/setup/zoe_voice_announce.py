"""Announcement poll/claim/speak decision logic for the Zoe voice daemon (P-W2.3).

Pure stdlib — no pyaudio/numpy/requests — so the decision logic is unit-testable
in slim CI (`services/zoe-data/tests/test_voice_announce_daemon_logic.py`).
The daemon (`zoe_voice_daemon.py`, same directory on the panel) wires the real
callables: fetch (claim `GET /api/voice/announcements` with the device token),
speak (TTS via `/api/voice/speak` + the existing playback path), is_busy (live
turn / TTS / cooldown state).

Deploy BOTH files to `~/.zoe-voice/` on the panel — the daemon degrades loudly
(announce polling disabled, one WARNING) when this module is missing, so a
partial deploy can't crash the voice path.

Contract:
  * Never speak while the daemon is busy (recording / a turn's TTS playing /
    post-play cooldown): DEFER, don't overlap. A busy poll cycle doesn't even
    fetch — an unclaimed announcement stays pending server-side, where the TTL
    is authoritative.
  * Never speak past the TTL: the server returns `expires_in_s` (computed
    server-side — the Pi's clock is never compared to the Jetson's) and a
    deferred announcement that outlives it is dropped as EXPIRED, not played.
  * Never let the poll hurt the voice path: every cycle's exceptions are
    caught and logged; repeated failures (e.g. zoe-data restarting on a
    deploy) back off quietly up to `backoff_max_s` and recover on the next
    good poll. The daemon must never crash-loop because the server is down.
"""
from __future__ import annotations

import time
from typing import Callable, Iterable, Optional


def decide(busy: bool, remaining_s: float) -> str:
    """One announcement, one moment → what to do with it.

    * remaining_s <= 0 → "expire" (a stale "good morning" at noon is worse
      than silence — never played, even if the daemon just became idle);
    * busy → "defer" (never overlap a live turn or reply);
    * otherwise → "speak".

    Expiry is checked FIRST so an announcement that is both stale and deferred
    dies instead of waiting for idle.
    """
    if remaining_s <= 0:
        return "expire"
    if busy:
        return "defer"
    return "speak"


class AnnouncePoller:
    """Poll → claim → speak/defer/expire loop, with injected side effects.

    Parameters (all callables injected so tests need no network/audio):
      fetch()        -> list[dict]  — claim pending announcements; each item
                        carries at least `text` and `expires_in_s` (and `id`
                        for logging). Raises on transport failure.
      speak(ann)     -> bool        — synthesize + play one announcement
                        through the daemon's existing TTS path. False = failed.
      is_busy()      -> bool        — live turn / TTS playing / cooldown.
      poll_interval_s               — idle cadence between polls.
      defer_wait_s                  — re-check cadence while deferring.
      backoff_max_s                 — cap for the failure backoff (each
                        consecutive failed poll doubles the wait from
                        poll_interval_s up to this cap; one good poll resets).
    """

    def __init__(
        self,
        *,
        fetch: Callable[[], Iterable[dict]],
        speak: Callable[[dict], bool],
        is_busy: Callable[[], bool],
        poll_interval_s: float = 5.0,
        defer_wait_s: float = 1.0,
        backoff_max_s: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
        logger=None,
    ):
        self._fetch = fetch
        self._speak = speak
        self._is_busy = is_busy
        self.poll_interval_s = max(0.5, float(poll_interval_s))
        self.defer_wait_s = max(0.1, float(defer_wait_s))
        self.backoff_max_s = max(self.poll_interval_s, float(backoff_max_s))
        self._monotonic = monotonic
        self._log = logger
        self._consecutive_failures = 0

    # ── logging (optional) ──────────────────────────────────────────────────
    def _info(self, msg: str, *args) -> None:
        if self._log is not None:
            self._log.info(msg, *args)

    def _warning(self, msg: str, *args) -> None:
        if self._log is not None:
            self._log.warning(msg, *args)

    # ── one announcement ────────────────────────────────────────────────────
    def deliver(self, ann: dict, wait: Optional[Callable[[float], None]] = None) -> str:
        """Speak one CLAIMED announcement, deferring while busy, until its TTL.

        Returns "spoken" | "speak_failed" | "expired". `wait(seconds)` is the
        defer sleep (injectable for tests; defaults to time.sleep).
        """
        wait = wait or time.sleep
        try:
            remaining = float(ann.get("expires_in_s", 0))
        except (TypeError, ValueError):
            remaining = 0.0
        deadline = self._monotonic() + remaining
        while True:
            action = decide(self._is_busy(), deadline - self._monotonic())
            if action == "expire":
                self._info("announce %s: expired before it could be spoken (never played)",
                           ann.get("id", "?"))
                return "expired"
            if action == "speak":
                ok = bool(self._speak(ann))
                return "spoken" if ok else "speak_failed"
            wait(self.defer_wait_s)

    # ── one poll cycle ──────────────────────────────────────────────────────
    def poll_once(self, wait: Optional[Callable[[float], None]] = None) -> list[str]:
        """One cycle: skip (without claiming) while busy, else claim + deliver.

        Busy-skip is deliberate: an unclaimed announcement stays pending
        server-side where the TTL keeps counting, so a long conversation
        naturally expires stale announces instead of hoarding them locally.
        """
        if self._is_busy():
            return ["busy"]
        anns = list(self._fetch() or [])
        return [self.deliver(ann, wait=wait) for ann in anns]

    def next_wait_s(self) -> float:
        """Idle wait before the next poll, honouring the failure backoff."""
        if self._consecutive_failures <= 0:
            return self.poll_interval_s
        return min(
            self.backoff_max_s,
            self.poll_interval_s * (2 ** min(self._consecutive_failures, 10)),
        )

    # ── the loop ────────────────────────────────────────────────────────────
    def run(self, shutdown_wait: Callable[[float], bool]) -> None:
        """Poll forever; `shutdown_wait(timeout)` is threading.Event.wait —
        returns True when the daemon is shutting down.

        A failed cycle (server down — e.g. zoe-data restarting on a deploy)
        is logged and backed off; it must never escape and kill the thread,
        and it never touches the wake/turn machinery.
        """
        while not shutdown_wait(self.next_wait_s()):
            try:
                outcomes = self.poll_once()
                self._consecutive_failures = 0
                spoken = [o for o in outcomes if o != "busy"]
                if spoken:
                    self._info("announce poll: %s", ",".join(spoken))
            except Exception as exc:
                self._consecutive_failures += 1
                # First failure at WARNING, the rest quietly (a multi-minute
                # server restart shouldn't flood the daemon log).
                if self._consecutive_failures == 1:
                    self._warning(
                        "announce poll failed (%s) — backing off up to %.0fs until the server returns",
                        exc, self.backoff_max_s,
                    )
