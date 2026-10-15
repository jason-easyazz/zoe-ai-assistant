"""Voice enrolment MODE for the Zoe panel daemon - the state machine, pure stdlib (no pyaudio / numpy / requests / network).

Why this exists. Voice enrolment used to be ``zoe_enroll_flow.py voice``: somebody ssh'd to the Pi, ran a command, and the script STOPPED the
daemon to get the microphone. Household members do not run commands. Now Zoe offers (server side, ``voice_enrollment.py``), the member says
yes, and the server queues a ``voice_enroll`` COMMAND on the daemon's existing announcement channel. The daemon already owns the microphone, so
it runs enrolment itself, IN-PROCESS, without stopping: this module is that run, with every side effect injected so it is unit-testable in slim
CI (``tests/unit/test_voice_enroll_mode.py``) with fake audio and a fake voice. The daemon wires the real callables in its clearly separated
"Enrollment mode" section.

The run, bounded (``max_total_s``, default 90 s - the panel is deaf to its wake word only for that long, and ``cancelled()`` /
the words "stop" / "cancel" end it at once):

  1. say the intro - which states what is kept (a voice signature, never recordings). If Zoe's voice is not available the run ENDS: the
     person must hear that before anything is recorded (consent is spoken, never assumed);
  2. for each of the script's phrases: say it ("Phrase 1. After the beep, say: ..."), beep + record, and judge the capture
     (``assess_wav``: long enough, loud enough, not clipped). A bad capture gets spoken guidance and a retry, up to
     ``attempts_per_phrase``; each capture is also listened to for a cancel word;
  3. only when EVERY phrase has a good capture, upload them (``/api/voice/enroll``, consent=true because the person is performing it).
     Capturing before uploading means a cancel or a timeout leaves nothing stored - the spoken "I haven't kept anything" is true;
  4. confirm aloud, and report the outcome to the server (``done`` | ``partial`` | ``cancelled`` | ``failed`` | ``timeout``).

ON-SCREEN PHRASES. The person is asked to remember a phrase they only HEAR, which is hard, so the run also tells the panel's talk screen
what to show, through the injected ``screen(state, **fields)`` (the daemon posts it to ``/api/enrollment/screen``; the server pushes it to
THAT panel only). It is shown IN ADDITION to the spoken prompt, never instead of it, and it goes up BEFORE Zoe starts speaking the phrase and
stays up through the recording. States: ``phrase`` (say this), ``recording``, ``accepted`` (a tick), ``reask`` (that one sounded different),
``saving``, ``done`` | ``partial`` | ``failed`` (the closing line), ``clear`` (the run ended any other way - stop, timeout, shutdown, no voice, a crash).
Fields are the phrase text, ``index`` / ``total`` and one localized ``line`` from the script (``screen_*``): never the member's name, never audio.
A run that did not show a closing line ALWAYS ends with ``clear``.

Recordings live only in memory in this object: never written to disk here, dropped when the run ends (the daemon's recorder returns bytes;
the server derives the embedding and discards the audio, exactly as before).

All words come from the ``script`` the server sends in the command, in the member's language. ``DEFAULT_SCRIPT`` (English) exists only so a
command that arrives without one still works.
"""
from __future__ import annotations

import array
import io
import math
import re
import sys
import time
import wave
from dataclasses import dataclass
from typing import Callable, Optional

DONE, PARTIAL, CANCELLED, FAILED, TIMEOUT = "done", "partial", "cancelled", "failed", "timeout"
SHUTDOWN = "shutdown"      # the daemon is stopping / restarting: NOT the person declining

#: the same three phrases ``zoe_enroll_flow.py`` always used (kept in step by tests/unit/test_voice_enroll_mode.py)
DEFAULT_PHRASES = (
    "Hey Zoe, what's the weather looking like today?",
    "Add milk, bread, and coffee to my shopping list.",
    "Remind me to call my mum tomorrow afternoon.",
)
DEFAULT_SCRIPT = {
    "intro": "Hi {name}. I'll ask you to say three short phrases. I keep a voice signature, never the recordings. Say stop at any time.",
    "prompt": "Phrase {n}. After the beep, say: {phrase}",
    "retry_short": "I only caught a little of that. Let's try the same phrase again.",
    "retry_quiet": "That was a bit quiet. A little louder, please.",
    "retry_noise": "That one was too noisy or too loud. Let's try once more.",
    "got": "Got it.",
    "done": "That's it, {name}. I'll know your voice from now on.",
    "partial": "I only saved part of it, but I can already use it. If I get you wrong, say forget my voice and we'll start again.",
    "cancelled": "Okay, I've stopped. I haven't kept anything.",
    "timeout": "That's taking a while, so I'll stop here. I haven't kept anything. Ask me any time and we'll try again.",
    "failed": "I couldn't get a clear recording. I haven't kept anything. We can try again in a quiet moment, just ask.",
    "store_failed": "I couldn't save it just now. I haven't kept anything.",
    # what the panel's talk screen shows (one short line per state; no name; read at a glance)
    "screen_say": "Say:",
    "screen_recording": "Recording...",
    "screen_odd": "That one sounded different - let's try it again",
    "screen_saving": "Saving...",
    "screen_done": "Done - I'll recognise your voice",
    "screen_partial": "Saved - I can already use it",
    "screen_failed": "That didn't work - nothing was kept",
    "cancel_words": ["stop", "cancel", "never mind", "nevermind", "forget it", "not now", "quit", "enough"],
    "phrases": list(DEFAULT_PHRASES),
}

#: the two extra phrases and lines a 5-clip (ResNet34) enrolment needs when the server's script does not carry them (an older server)
DEFAULT_PHRASES_EXTRA = (
    "Turn the living room lights down to half.",
    "Play some quiet music in the kitchen.",
)
DEFAULT_SCRIPT.update(
    intro_many="Hi {name}. I'll ask you to say {count} short phrases, in your normal voice. I keep a voice signature, never the recordings. Say stop at any time.",
    retry_odd="That one sounded different from the others, so let's do it once more.",
    phrases_extra=list(DEFAULT_PHRASES_EXTRA),
)

# Capture quality (16 kHz mono int16 WAV, as the daemon records). Thresholds are deliberately forgiving: a retry costs the person ten
# seconds, a rejected honest capture costs their patience.
MIN_TOTAL_S = 1.2        # shorter than this is a clipped or empty capture
MIN_VOICED_S = 0.6       # speech-level audio inside it
QUIET_PEAK = 1200        # a peak below this (of 32767) is a whisper or a dead mic
VOICED_RMS = 350         # a 30 ms window at or above this counts as speech-level
CLIP_LEVEL = 32000
MAX_CLIP_RATIO = 0.02    # more than 2% of samples at full scale: too loud / distorted
MAX_VOICED_RATIO = 0.98  # speech-level audio in (nearly) every window of a 2.5 s+ capture: a fan, a TV, a room full of noise
_WIN_S = 0.03
_CANCEL_MAX_WORDS = 6    # a cancel is a short utterance; the same word inside a long phrase is not one


def assess_wav(wav_bytes: bytes) -> tuple:
    """(ok, reason): ``reason`` is ``"ok"`` | ``"short"`` | ``"quiet"`` | ``"noise"``. Pure; stdlib only; a WAV it cannot parse is ``short``."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            channels, width, rate, frames = wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()
            raw = wf.readframes(frames)
    except Exception:  # noqa: BLE001 - not a WAV at all
        return False, "short"
    if width != 2 or rate <= 0:
        return True, "ok"            # a format this cannot judge is not a bad capture
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if channels > 1:
        samples = samples[::channels]
    duration = len(samples) / float(rate)
    if duration < MIN_TOTAL_S:
        return False, "short"
    win = max(1, int(rate * _WIN_S))
    windows = voiced = clipped = 0
    peak = 0
    for start in range(0, len(samples) - win + 1, win):
        seg = samples[start:start + win]
        windows += 1
        energy = 0
        for x in seg:
            a = -x if x < 0 else x
            energy += x * x
            if a > peak:
                peak = a
            if a >= CLIP_LEVEL:
                clipped += 1
        if math.sqrt(energy / win) >= VOICED_RMS:
            voiced += 1
    if windows == 0 or peak < QUIET_PEAK:
        return False, "quiet"
    if clipped / float(windows * win) > MAX_CLIP_RATIO:
        return False, "noise"
    if duration >= 2.5 and voiced / float(windows) > MAX_VOICED_RATIO:
        return False, "noise"
    if voiced * _WIN_S < MIN_VOICED_S:
        return False, "quiet" if peak < 3000 else "short"
    return True, "ok"


def is_cancel(transcript: str, words) -> bool:
    """True when a SHORT transcript contains one of the cancel words (whole word / phrase)."""
    t = re.sub(r"[^\w' ]+", " ", (transcript or "").lower().replace("’", "'"))
    t = re.sub(r"\s+", " ", t).strip()
    if not t or len(t.split()) > _CANCEL_MAX_WORDS:
        return False
    return any(w and re.search(r"(?<!\w)" + re.escape(str(w).lower()) + r"(?!\w)", t) for w in words or ())


def merge_script(script: Optional[dict]) -> dict:
    """The server's script over the English defaults (a missing or empty key falls back; ``phrases`` must be a non-empty list)."""
    out = dict(DEFAULT_SCRIPT)
    for key, value in (script or {}).items():
        if value not in (None, "", []):
            out[key] = value
    if not isinstance(out.get("phrases"), list) or not out["phrases"]:
        out["phrases"] = list(DEFAULT_PHRASES)
    return out


def _fill(text: str, **slots) -> str:
    for key, value in slots.items():
        text = text.replace("{" + key + "}", str(value))
    return text


@dataclass
class Result:
    outcome: str
    samples: int = 0
    reason: str = ""


class EnrollmentRun:
    """One enrolment run. Every side effect is injected:

    ``say(text) -> bool``       speak in Zoe's normal voice and BLOCK until it has played; False = her voice is unavailable
    ``record() -> bytes|None``  beep, then capture ONE utterance as a WAV; None = nothing usable came from the mic
    ``transcribe(wav) -> str``  optional: what was said (used only to hear a cancel word); may raise
    ``upload(wav) -> bool``     send one capture to the server for the signature; False = refused / failed
    ``report(outcome, n, why)`` tell the server how the run ended (never raises into the run); ``why`` separates the PERSON
                                saying stop / tapping stop (``said_stop`` / ``tapped_stop``) from a daemon ``shutdown``
    ``cancelled() -> str|bool`` an external stop: a reason string (``tapped_stop`` | ``shutdown``) or just True
    ``clock() -> float``        monotonic seconds
    """

    def __init__(self, script: Optional[dict], *, say: Callable[[str], bool], record: Callable[[], Optional[bytes]],
                 upload: Callable[[bytes], bool], name: str = "", transcribe: Optional[Callable[[bytes], str]] = None,
                 report: Optional[Callable[[str, int, str], None]] = None, cancelled: Callable[[], object] = lambda: False,
                 assess: Callable[[bytes], tuple] = assess_wav, clock: Callable[[], float] = time.monotonic,
                 max_total_s: float = 90.0, attempts_per_phrase: int = 3, log: Optional[Callable[[str], None]] = None,
                 clips: Optional[int] = None, embed: Optional[Callable[[bytes], object]] = None,
                 build: Optional[Callable[[list], tuple]] = None, upload_profile: Optional[Callable[[object, int], bool]] = None,
                 legacy_upload: bool = True, reask_rounds: int = 2,
                 screen: Optional[Callable[..., None]] = None):
        self.script = merge_script(script)
        # ResNet34 enrolment (flag-dark, ZOE_SPEAKER_MODEL): ``clips`` captures are asked for; ``embed(wav)`` gives each capture's
        # embedding on the panel (None = unusable); ``build(embeddings) -> (profile | None, dropped_positions)`` rejects a capture
        # that does not sound like the rest; ``upload_profile(profile, n_clips)`` stores the finished profile. A capture that is
        # dropped is RE-ASKED (``retry_odd``), at most ``reask_rounds`` times. ``legacy_upload`` keeps the WAV upload the Resemblyzer
        # profile is built from (``dual`` does both from the same clips; ``resnet34`` alone sends no audio at all).
        self.clips = int(clips) if clips else None
        self._embed, self._build, self._upload_profile = embed, build, upload_profile
        self.legacy_upload = bool(legacy_upload)
        self.reask_rounds = max(0, int(reask_rounds))
        self.name = name
        self._say, self._record, self._upload = say, record, upload
        self._transcribe, self._report, self._cancelled = transcribe, report, cancelled
        self._assess, self._clock = assess, clock
        self.max_total_s = max(10.0, float(max_total_s))
        self.attempts = max(1, int(attempts_per_phrase))
        self._log = log or (lambda msg: None)
        self._deadline = 0.0
        self._wavs: list = []
        self._screen = screen
        self._screen_closed = False     # the screen was told how the run ended (closing line or clear)
        self._total = 0

    # ── helpers ────────────────────────────────────────────────────────────────────────────────────────────────────────
    def _line(self, key: str, **slots) -> str:
        return _fill(str(self.script.get(key) or ""), name=self.name, **slots)

    def _speak(self, key: str, **slots) -> bool:
        text = self._line(key, **slots)
        if not text:
            return True
        try:
            return bool(self._say(text))
        except Exception as exc:  # noqa: BLE001 - a voice that raises is a voice that is unavailable
            self._log(f"say failed: {type(exc).__name__}")
            return False

    def _show(self, state: str, index: int = 0, phrase: str = "") -> None:
        """Tell the panel's talk screen what to show. Best effort: a screen that cannot be reached never changes the run, and the
        spoken prompt (consent, guidance) is the contract - the screen only helps the person remember what to say."""
        if self._screen is None:
            return
        key = {"phrase": "screen_say", "recording": "screen_recording", "reask": "screen_odd", "saving": "screen_saving",
               "done": "screen_done", "partial": "screen_partial", "failed": "screen_failed"}.get(state)
        fields: dict = {}
        if key:
            fields["line"] = self._line(key)
        if state in ("phrase", "recording", "accepted", "reask"):
            fields.update(phrase=str(phrase), index=int(index), total=int(self._total))
        try:
            self._screen(state, **fields)
        except Exception as exc:  # noqa: BLE001
            self._log(f"screen failed: {type(exc).__name__}")
        if state in ("done", "partial", "failed", "clear"):
            self._screen_closed = True      # the screen has been told how this run ended (a closing line, or a clear): say nothing more

    def _expired(self) -> bool:
        return self._clock() >= self._deadline

    def _stop_requested(self) -> str:
        """"" when nothing asked to stop, else WHY: ``cancelled()`` may return a reason string (``tapped_stop`` | ``shutdown``) or just
        True (``stopped``, unspecified)."""
        try:
            value = self._cancelled()
        except Exception:  # noqa: BLE001
            return ""
        if not value:
            return ""
        return value if isinstance(value, str) else "stopped"

    def _stopped(self, reason: str) -> Result:
        """The run ends because something asked it to. A daemon SHUTDOWN is silent (nobody is being addressed) and is not the person
        cancelling; the server only treats ``said_stop`` / ``tapped_stop`` as a "no"."""
        return self._finish(CANCELLED, 0, reason, "" if reason == SHUTDOWN else "cancelled")

    def _heard_cancel(self, wav: bytes) -> bool:
        if self._transcribe is None:
            return False
        try:
            return is_cancel(self._transcribe(wav), self.script.get("cancel_words"))
        except Exception as exc:  # noqa: BLE001 - not hearing is not cancelling
            self._log(f"cancel check failed: {type(exc).__name__}")
            return False

    def _finish(self, outcome: str, samples: int = 0, reason: str = "", line: str = "") -> Result:
        # The closing line goes up WITH the spoken one. Any other ending (stop, timeout, shutdown) clears the screen at once.
        if outcome in (DONE, PARTIAL):
            self._show("done" if outcome == DONE else "partial")
        elif outcome == FAILED and line:
            self._show("failed")
        else:
            self._show("clear")
        if line:
            self._speak(line)
        return Result(outcome, samples, reason)

    # ── the run ────────────────────────────────────────────────────────────────────────────────────────────────────────
    def run(self) -> Result:
        self._deadline = self._clock() + self.max_total_s
        result = Result(FAILED, 0, "not_run")
        try:
            result = self._run()
        except Exception as exc:  # noqa: BLE001 - the daemon must come back to listening whatever happens here
            self._log(f"enrolment run crashed: {type(exc).__name__}")
            result = Result(FAILED, 0, "crashed")
        finally:
            self._wavs.clear()          # recordings never outlive the run
            if not self._screen_closed:
                self._show("clear")     # whatever ended the run, the wall never keeps a stale phrase
            if self._report is not None:
                try:
                    self._report(result.outcome, result.samples, result.reason)
                except Exception:  # noqa: BLE001
                    pass
        return result

    def _phrases(self) -> list:
        """The phrases to ask for: the script's own (legacy), or - when ``clips`` is set - exactly that many: the script's, then its
        ``phrases_extra``, then repeats from the top (an older server's 3-phrase script still yields 5 captures)."""
        base = [str(p) for p in self.script["phrases"]]
        if not self.clips:
            return base
        out = list(base[: self.clips])
        for p in (str(x) for x in (self.script.get("phrases_extra") or [])):
            if len(out) >= self.clips:
                break
            out.append(p)
        i = 0
        while len(out) < self.clips and base:
            out.append(base[i % len(base)])
            i += 1
        return out

    def _capture_phrase(self, index: int, phrase: str, reask: bool = False):
        """Ask for ONE phrase, up to ``attempts`` tries with spoken guidance. The WAV, or the terminal ``Result`` (a stop, the
        deadline, no voice, three bad captures)."""
        for attempt in range(self.attempts):
            if self._stop_requested():
                return self._stopped(self._stop_requested())
            if self._expired():
                return self._finish(TIMEOUT, 0, "timeout", "timeout")
            # On screen BEFORE she speaks it (read along as she says it), and it stays up through the recording.
            self._show("reask" if (reask and attempt == 0) else "phrase", index, phrase)
            if not self._speak("prompt", n=index, phrase=phrase):
                return Result(FAILED, 0, "no_voice")
            if self._stop_requested():
                return self._stopped(self._stop_requested())
            self._show("recording", index, phrase)
            wav = self._record()
            if self._stop_requested():
                return self._stopped(self._stop_requested())
            if wav is None:
                ok, why = False, "short"
            else:
                if self._heard_cancel(wav):
                    return self._finish(CANCELLED, 0, "said_stop", "cancelled")
                ok, why = self._assess(wav)
            if ok:
                self._show("accepted", index, phrase)
                return wav
            if self._expired():
                return self._finish(TIMEOUT, 0, "timeout", "timeout")
            if attempt + 1 < self.attempts:
                self._speak("retry_" + why if ("retry_" + why) in self.script else "retry_short")
        return self._finish(FAILED, 0, "bad_capture", "failed")

    def _run(self) -> Result:
        phrases = self._phrases()
        self._total = len(phrases)
        many = self.clips is not None and self.clips != len(self.script["phrases"]) and bool(self.script.get("intro_many"))
        if not (self._speak("intro_many", count=len(phrases)) if many else self._speak("intro")):
            return Result(FAILED, 0, "no_voice")           # consent is spoken: no voice, no recording
        for index, phrase in enumerate(phrases, start=1):
            got = self._capture_phrase(index, phrase)
            if isinstance(got, Result):
                return got
            self._wavs.append(got)
            self._speak("got")
        if self._embed is not None and self._build is not None and self._upload_profile is not None:
            return self._profile_and_upload(phrases)
        return self._upload_all()

    def _upload_all(self) -> Result:
        # No time check here: the budget bounds the CAPTURE (how long the panel listens to the person). Every phrase is already in hand, and
        # the upload has its own timeouts - abandoning a finished enrolment because the last retry ran long would be the wrong trade.
        self._show("saving")
        uploaded = 0
        for wav in self._wavs:
            try:
                if not self._upload(wav):
                    break
            except Exception as exc:  # noqa: BLE001
                self._log(f"upload failed: {type(exc).__name__}")
                break
            uploaded += 1
        if uploaded == 0:
            return self._finish(FAILED, 0, "upload_failed", "store_failed")
        if uploaded < len(self._wavs):
            return self._finish(PARTIAL, uploaded, "partial_upload", "partial")
        return self._finish(DONE, uploaded, "ok", "done")

    # ── the ResNet34 profile: embed on the panel, reject the odd one out, re-ask it, upload the profile ─────────────────────
    def _safe_embed(self, wav: bytes):
        try:
            return self._embed(wav)
        except Exception as exc:  # noqa: BLE001 - an embedder that raises gave no embedding
            self._log(f"embed failed: {type(exc).__name__}")
            return None

    def _judge(self, embs: list) -> tuple:
        """``(profile | None, kept, bad)`` over the captures (indices). A capture with no embedding is bad; the builder drops the rest."""
        good = [i for i, e in enumerate(embs) if e is not None]
        bad = [i for i, e in enumerate(embs) if e is None]
        if not good:
            return None, [], bad
        try:
            profile, dropped = self._build([embs[i] for i in good])
        except Exception as exc:  # noqa: BLE001
            self._log(f"profile build failed: {type(exc).__name__}")
            return None, [], list(range(len(embs)))
        bad += [good[j] for j in dropped]
        return profile, [i for i in good if i not in bad], sorted(set(bad))

    def _profile_and_upload(self, phrases: list) -> Result:
        embs = [self._safe_embed(w) for w in self._wavs]
        profile, kept, bad = self._judge(embs)
        for _ in range(self.reask_rounds):
            if not bad or self._expired():
                break
            for i in bad:
                if self._expired():
                    break
                self._show("reask", i + 1, phrases[i % len(phrases)])      # up as she says it; the prompt re-shows it
                self._speak("retry_odd")
                got = self._capture_phrase(i + 1, phrases[i % len(phrases)], reask=True)
                if isinstance(got, Result):
                    return got
                self._wavs[i] = got
                embs[i] = self._safe_embed(got)
            profile, kept, bad = self._judge(embs)
        embs.clear()                                   # embeddings, like recordings, never outlive the run
        if profile is None:
            return self._finish(TIMEOUT if self._expired() else FAILED, 0, "timeout" if self._expired() else "bad_capture",
                                "timeout" if self._expired() else "failed")
        self._show("saving")
        # The ACTING model's profile is uploaded FIRST (retried once): it is the one that recognises the member. If it does not land,
        # nothing else is stored either - so "I haven't kept anything" stays true - and the run is a storage failure, never "partial":
        # a shadow-model profile alone is something nobody can use yet. ``dual`` acts on Resemblyzer (the kept WAVs); ``resnet34`` on
        # the panel-built profile.
        if self.legacy_upload:
            legacy_ok = self._upload_wavs(kept)
            if legacy_ok == 0:
                return self._finish(FAILED, 0, "upload_failed", "store_failed")
            profile_ok = self._try_upload(self._upload_profile, profile, len(kept), label="profile")
            if profile_ok and legacy_ok == len(kept):
                return self._finish(DONE, len(kept), "ok", "done")
            return self._finish(PARTIAL, len(kept), "partial_upload", "partial")   # the acting profile exists; the shadow one is incomplete
        if not self._try_upload(self._upload_profile, profile, len(kept), label="profile", attempts=2):
            return self._finish(FAILED, 0, "upload_failed", "store_failed")
        return self._finish(DONE, len(kept), "ok", "done")

    def _try_upload(self, fn, *args, label: str = "upload", attempts: int = 1) -> bool:
        for _ in range(max(1, attempts)):
            try:
                if fn(*args):
                    return True
            except Exception as exc:  # noqa: BLE001
                self._log(f"{label} failed: {type(exc).__name__}")
        return False

    def _upload_wavs(self, kept: list) -> int:
        """Upload the kept captures for the Resemblyzer profile in order; the first one is retried once. Returns how many landed."""
        done = 0
        for n, i in enumerate(kept):
            if not self._try_upload(self._upload, self._wavs[i], attempts=2 if n == 0 else 1):
                break
            done += 1
        return done
