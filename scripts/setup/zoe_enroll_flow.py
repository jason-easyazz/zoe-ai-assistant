#!/usr/bin/env python3
"""Guided biometric enrollment for the Zoe touch panel — WITH spoken instructions.

Runs ON the panel Pi. Zoe talks the person through every step from the panel
speaker ("Stand in front of the screen and look at the camera… got it — now
turn your head slightly to the left…"), confirms each capture out loud, and
retries with corrective guidance when a step fails. No more being silently
puppeted while someone drives the camera over ssh.

    # face: 3 spoken-guided poses -> embeddings -> POST /api/face/enroll
    venv/bin/python zoe_enroll_flow.py face --user jason --name Jason

    # voice: 3 spoken-prompted phrases -> WAVs -> POST /api/voice/enroll
    # (stops the zoe-voice daemon for the recording window — the Jabra can't
    #  hold two input streams — and restarts it afterwards)
    venv/bin/python zoe_enroll_flow.py voice --user jason --name Jason

Consent is explicit: the flow states what will be stored (an embedding /
voice signature, never images or recordings) and requires --user; every
enroll call sends consent=true because the person is actively performing
the enrollment.

Identity (2026-10-19): SIGN IN WITH YOUR PIN AT THE PANEL FIRST. The server
never takes --user on the device token's word: it enrols only the member with
a fresh PIN sign-in at this panel (or the member of Zoe's own "learn my voice"
command) and answers 403 for anyone else - a voiceprint is a password, and a
copied panel token must not enrol a stranger onto a member. --user must name
that signed-in member.

Privacy: frames and recordings are process-local temp files, deleted after
embedding/upload. Only embeddings (face) and enrollment WAVs (voice — the
server derives the embedding and discards audio) leave the device, over the
existing device-token channel.

Camera notes: capture at the device's NATIVE resolution — forcing 720p on
the Jabra PanaCast browned out the Pi's USB port on 2026-07-19 (USB
disconnect + connect-debounce failure needing a physical replug). Never
set CAP_PROP_FRAME_WIDTH/HEIGHT here.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ZOE_URL = os.environ.get("ZOE_URL", "https://zoe.local")
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() not in ("0", "false", "no")

FACE_POSES = [
    ("front", "Stand about a metre from the screen and look straight at the camera."),
    ("left", "Now keep facing the screen, and turn your head just slightly to the left."),
    ("right", "And now turn your head just slightly to the right."),
]
VOICE_PHRASES = [
    "Hey Zoe, what's the weather looking like today?",
    "Add milk, bread, and coffee to my shopping list.",
    "Remind me to call my mum tomorrow afternoon.",
]


def _load_env_file(path: str) -> None:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass


def _api(path: str, payload: dict, timeout: int = 60, raw: bool = False):
    import ssl
    ctx = None
    if not VERIFY_SSL:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        f"{ZOE_URL}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Device-Token": DEVICE_TOKEN},
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        body = r.read()
    return body if raw else json.loads(body)


# The server's TTS can be briefly unavailable (the Kokoro sidecar restarting answers 503 for ~25 s). The robot voice the owner heard on
# 2026-10-12 was the espeak-ng fallback below firing on the FIRST attempt, not a different synthesiser: the flow uses the same
# /api/voice/synthesize waterfall as every normal reply. So wait for Kokoro (bounded) before settling for espeak-ng.
SYNTH_ATTEMPTS = 6
SYNTH_RETRY_S = (2, 3, 4, 5, 6)


def say(text: str) -> None:
    """Speak through the panel: server TTS (Zoe's real voice) -> aplay. A failed synthesis is RETRIED for a bounded wait (the
    Kokoro sidecar restarts in ~25 s); only when that is exhausted does espeak-ng speak, so guidance never goes silent."""
    print(f"[zoe says] {text}", flush=True)
    for attempt in range(SYNTH_ATTEMPTS):
        try:
            audio = _api("/api/voice/synthesize", {"text": text}, raw=True)
        except Exception as exc:  # noqa: BLE001 - 503 while the TTS sidecar restarts, a transport blip
            print(f"[zoe says] TTS attempt {attempt + 1}/{SYNTH_ATTEMPTS} failed: {type(exc).__name__}", file=sys.stderr, flush=True)
            if attempt < SYNTH_ATTEMPTS - 1:
                time.sleep(SYNTH_RETRY_S[min(attempt, len(SYNTH_RETRY_S) - 1)])
            continue
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio)
            path = f.name
        try:
            player = ["aplay", "-q"] if audio[:4] == b"RIFF" else ["mpg123", "-q"]
            subprocess.run(player + [path], check=False, timeout=30)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        return
    print("[zoe says] server TTS unavailable - falling back to espeak-ng (robot voice)", file=sys.stderr, flush=True)
    subprocess.run(["espeak-ng", "-s", "150", text], check=False, timeout=20)


def _chime_wav_path() -> str:
    """The listen chime: ``chime.wav`` beside this script when one is deployed, else a two-tone chirp written to a temp file.
    The Pi has never had a ``chime.wav``, so the old ``if os.path.exists(chime)`` played NOTHING while the prompts said "after the
    beep". Same two tones as the daemon's wake beep."""
    shipped = os.path.join(HERE, "chime.wav")
    if os.path.exists(shipped):
        return shipped
    rate, amp = 16000, 0.35
    frames = bytearray()
    for freq, ms in ((880, 120), (0, 28), (1170, 110)):
        n = max(1, int(rate * ms / 1000))
        for i in range(n):
            t = i / max(1, n - 1)
            env = min(1.0, t * 18.0) * min(1.0, (1.0 - t) * 22.0)
            sample = 0 if freq == 0 else int(32767 * amp * env * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<hh", sample, sample)  # stereo duplicate: USB speakerphones reject mono playback
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
    return path


def play_chime() -> None:
    """The beep before each recording."""
    path = _chime_wav_path()
    try:
        subprocess.run(["aplay", "-q", path], check=False, timeout=5)
    finally:
        if os.path.basename(path) != "chime.wav":
            try:
                os.unlink(path)
            except OSError:
                pass


def _load_face_module():
    spec = importlib.util.spec_from_file_location("zoe_face_id", os.path.join(HERE, "zoe_face_id.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _open_camera():
    """Open the camera ONCE for the whole session. The Jabra PanaCast drops
    off the USB bus when opened/closed in quick succession (observed live
    2026-07-19: first open fine, second open within seconds → USB disconnect
    needing a physical replug), so the enrollment flow holds one handle across
    every pose instead of cycling per capture."""
    import cv2

    for attempt in range(3):
        cap = cv2.VideoCapture(int(os.environ.get("FACE_CAMERA_INDEX", "0")))
        if cap.isOpened():
            return cap
        cap.release()
        time.sleep(2)
    return None


def enroll_face(user: str, name: str) -> int:
    face = _load_face_module()
    if not face.models_available():
        say("Face models aren't installed on this panel yet, so I can't do face setup.")
        return 1
    # All instructions are given UP FRONT and the recording itself is silent:
    # the PanaCast camera and the Jabra speaker share the Pi's USB power
    # budget, and speaking while streaming video browns the camera out
    # (observed live 2026-07-19 — every camera drop followed a TTS playback).
    say(f"Hi {name}. Let's teach me to recognise your face. "
        "I'll store a face signature — a set of numbers, never a photo. "
        "Here's how it works: I'll record for about twelve seconds, in silence. "
        "Look straight at the camera first. When you hear nothing for a few "
        "seconds, slowly turn your head a little to the left, hold it there, "
        "then slowly to the right and hold. Starting now.")
    time.sleep(0.5)
    cap = _open_camera()
    if cap is None:
        say("I can't reach the camera. Check it's plugged in, then try again.")
        return 1
    # One continuous silent recording: ~12s of frames, timestamped.
    recorded = []  # (t, frame)
    try:
        t0 = time.time()
        while time.time() - t0 < 12.0:
            ok, fr = cap.read()
            if ok and fr is not None:
                recorded.append((time.time() - t0, fr))
            time.sleep(0.15)
    finally:
        cap.release()
    say("Recording done — give me a moment to process it.")
    # Segment into pose windows (front / left / right) and take the best
    # detector hit per window; embed only the winners.
    windows = [("front", 0.0, 4.0), ("left", 4.0, 8.0), ("right", 8.0, 12.0)]
    staged = []
    for pose, lo, hi in windows:
        best = None
        for t, fr in recorded:
            if not (lo <= t < hi):
                continue
            dets, kps = face.detect_faces(fr)
            picked = face.pick_best_face(dets, kps)
            if picked is None:
                continue
            det, kp, v = picked
            if best is None or v > best[0]:
                best = (v, det, kp, fr)
        if best is None:
            continue
        emb = face.embed_face(face.align_face(best[3], best[2]))
        if emb is not None:
            staged.append((pose, emb))
    recorded.clear()  # frames discarded — embeddings only from here
    if len(staged) < 2:
        say("I couldn't capture enough good angles. Let's try again another time.")
        return 1
    ok = 0
    for pose, emb in staged:
        try:
            out = _api("/api/face/enroll", {
                "embedding_base64": base64.b64encode(emb.astype("float32").tobytes()).decode(),
                "user_id": user, "display_name": name,
                "model_name": face.REC_MODEL_NAME, "dim": face.EMBED_DIM,
                "consent": True,
            })
            ok += bool(out.get("ok"))
        except Exception as exc:
            print(f"enroll pose {pose} failed: {exc}", file=sys.stderr)
    if ok:
        say(f"All done, {name} — I'll recognise your face from now on. "
            f"I saved {ok} angles.")
        try:
            face.sync_profiles(force=True)
        except Exception:
            pass
        return 0
    say("I couldn't save your face profile — the server said no. Nothing was stored.")
    return 1


def _record_wav(seconds: float, path: str) -> bool:
    """Record from the default mic to 16 kHz mono WAV via arecord."""
    proc = subprocess.run(
        ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1",
         "-d", str(int(seconds)), path],
        check=False, timeout=seconds + 15,
    )
    return proc.returncode == 0 and os.path.getsize(path) > 32000  # >1s of audio


VOICE_PHRASES_EXTRA = [
    "Turn the living room lights down to half.",
    "Play some quiet music in the kitchen.",
]


def _speaker_mode() -> str:
    """``ZOE_SPEAKER_MODEL`` (the daemon's own flag): ``resemblyzer`` (default) | ``dual`` | ``resnet34``."""
    raw = os.environ.get("ZOE_SPEAKER_MODEL", "").strip().lower()
    return raw if raw in ("dual", "resnet34") else "resemblyzer"


def _load_rn34():
    """The panel ResNet34 module, loaded from beside this script (None when it is not deployed)."""
    path = os.path.join(HERE, "zoe_speaker_resnet34.py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("zoe_speaker_resnet34", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["zoe_speaker_resnet34"] = mod
    spec.loader.exec_module(mod)
    return mod


def enroll_voice_resnet34(user: str, name: str, mode: str, rn, daemon_service: str = "zoe-voice") -> int:
    """5-clip enrolment for ResNet34 (``resnet34``) or for BOTH models from the same clips (``dual``): the mic is held with the
    daemon stopped, each clip is embedded HERE, the one that does not sound like the others is re-asked, and ONE 256-float profile
    is uploaded (``dual`` also uploads the kept WAVs for the Resemblyzer profile). Nothing is stored until every clip is in hand."""
    import numpy as np

    try:
        emb = rn.Resnet34Embedder.load()
    except rn.Unavailable as exc:
        print(f"ResNet34 embedder unavailable: {exc}", file=sys.stderr)
        if mode == "resnet34":
            say("I can't build your voice signature on this panel right now. Nothing was stored.")
            return 1
        say("My new voice model isn't ready on this panel, so I'll only do the old one for now.")
        return _enroll_voice_legacy(user, name, daemon_service)
    phrases = (VOICE_PHRASES + VOICE_PHRASES_EXTRA)[: rn.ENROLL_CLIPS]
    say(f"Hi {name}. Let's teach me your voice. I'll ask you to say {len(phrases)} short phrases, in your normal voice. "
        "I store a voice signature, numbers, not recordings.")
    wavs: list = [None] * len(phrases)
    embs: list = [None] * len(phrases)

    def capture(i: int) -> bool:
        for _ in range(3):
            say(f"Phrase {i + 1}. After the beep, please say: {phrases[i]}")
            time.sleep(0.4)
            play_chime()
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                path = f.name
            try:
                if _record_wav(6, path):
                    with open(path, "rb") as fh:
                        data = fh.read()
                    vec = emb.embed_wav(data)[0]
                    if vec is not None:
                        wavs[i], embs[i] = data, vec
                        say("Got it.")
                        return True
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            say("I didn't catch that clearly. Let's try the same phrase again.")
        return False

    ok = False
    try:
        subprocess.run(["systemctl", "--user", "stop", daemon_service], check=False)
        time.sleep(2)
        if not all(capture(i) for i in range(len(phrases))):
            say("I couldn't get clear recordings. Nothing was stored.")
            return 1
        for _round in range(3):
            built = rn.build_profile(embs)
            bad = sorted(built.dropped)
            if built.ok and not bad:
                break
            if _round == 2 or not bad:
                break
            for i in bad:
                say("That one sounded different from the others, so let's do it once more.")
                if not capture(i):
                    say("I couldn't get clear recordings. Nothing was stored.")
                    return 1
        if not built.ok:
            say("The recordings didn't sound consistent enough. Nothing was stored.")
            return 1

        def upload_profile() -> bool:
            try:
                return bool(_api("/api/voice/enroll", dict(
                    embedding_base64=base64.b64encode(np.asarray(built.vector, dtype=np.float32).tobytes()).decode(),
                    model_id=rn.MODEL_ID, sample_count=len(built.kept), user_id=user, display_name=name, consent=True)).get("ok"))
            except Exception as exc:  # noqa: BLE001
                print(f"profile upload failed: {exc}", file=sys.stderr)
                return False

        def upload_wav(i: int) -> bool:
            try:
                return bool(_api("/api/voice/enroll", dict(audio_base64=base64.b64encode(wavs[i]).decode(), user_id=user,
                                                           display_name=name, consent=True)).get("ok"))
            except Exception as exc:  # noqa: BLE001
                print(f"clip {i + 1} upload failed: {exc}", file=sys.stderr)
                return False

        # The ACTING model first: in dual that is Resemblyzer (the WAVs), in resnet34 the profile. If it does not land nothing else is
        # stored, so the failure line is true; a shadow-model profile alone recognises nobody.
        if mode == "dual":
            acting = upload_wav(built.kept[0]) or upload_wav(built.kept[0])
            ok = acting and all(upload_wav(i) for i in built.kept[1:]) and upload_profile()
        else:
            acting = upload_profile() or upload_profile()
            ok = acting
        if not acting:
            say("I couldn't save your voice signature just now. Nothing was stored. Let's try again in a moment.")
            return 1
    finally:
        wavs.clear()
        embs.clear()
        subprocess.run(["systemctl", "--user", "start", daemon_service], check=False)
    if ok:
        say(f"Done, {name} - I'll know your voice from now on.")
        return 0
    say(f"I'll know your voice now, {name}, but part of the new signature didn't save. We can redo it any time.")
    return 1


def enroll_voice(user: str, name: str, daemon_service: str = "zoe-voice") -> int:
    mode = _speaker_mode()
    if mode != "resemblyzer":
        rn = _load_rn34()
        if rn is not None:
            return enroll_voice_resnet34(user, name, mode, rn, daemon_service)
        if mode == "resnet34":
            # ResNet34 is the ACTING model and cannot run here: a Resemblyzer-only enrolment would "succeed" with a profile nothing
            # uses to recognise them. Refuse honestly (the daemon does the same).
            print("ZOE_SPEAKER_MODEL=resnet34 but zoe_speaker_resnet34.py is not beside this script - refusing to enrol", file=sys.stderr)
            say(f"I can't learn your voice on this panel right now, {name}. Nothing was stored.")
            return 1
        print("ZOE_SPEAKER_MODEL=dual but zoe_speaker_resnet34.py is not beside this script - enrolling Resemblyzer only", file=sys.stderr)
    return _enroll_voice_legacy(user, name, daemon_service)


def _enroll_voice_legacy(user: str, name: str, daemon_service: str = "zoe-voice") -> int:
    say(f"Hi {name}. Let's teach me your voice. I'll ask you to say three short "
        "phrases. I store a voice signature — numbers, not recordings.")
    uploaded = 0
    # The stop lives INSIDE the try: everything from here on must be covered by
    # the finally that restarts the daemon. A Ctrl+C during the settle sleep —
    # or a failure in the stop itself — would otherwise exit with the daemon
    # down, leaving the panel deaf to wake-words until someone restarts it by
    # hand. `start` on an already-running unit is a no-op, so covering the stop
    # too is free.
    try:
        # The Jabra can't hold two input streams — pause the daemon for the window.
        subprocess.run(["systemctl", "--user", "stop", daemon_service], check=False)
        time.sleep(2)
        for i, phrase in enumerate(VOICE_PHRASES, 1):
            say(f"Phrase {i}. After the beep, please say: {phrase}")
            time.sleep(0.4)
            play_chime()
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
            try:
                if not _record_wav(6, wav_path):
                    say("I didn't catch that. Let's move on.")
                    continue
                try:
                    out = _api("/api/voice/enroll", {
                        "audio_base64": base64.b64encode(open(wav_path, "rb").read()).decode(),
                        "user_id": user, "display_name": name, "consent": True,
                    })
                except Exception as exc:
                    print(f"phrase {i} upload failed: {exc}", file=sys.stderr)
                    say("That one didn't save. Moving on.")
                    continue
                if out.get("ok"):
                    uploaded += 1
                    say("Got it.")
                else:
                    say("That one didn't save. Moving on.")
            finally:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
    finally:
        subprocess.run(["systemctl", "--user", "start", daemon_service], check=False)
    if uploaded:
        say(f"Done, {name} — your voice is enrolled with {uploaded} samples.")
        return 0
    say("I couldn't record any usable samples. Nothing was stored.")
    return 1


def main() -> int:
    _load_env_file(os.path.expanduser("~/.zoe-voice/.env.voice"))
    global ZOE_URL, DEVICE_TOKEN, VERIFY_SSL
    ZOE_URL = os.environ.get("ZOE_URL", ZOE_URL)
    DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", DEVICE_TOKEN)
    VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() not in ("0", "false", "no")

    ap = argparse.ArgumentParser(description="Guided face/voice enrollment with spoken instructions")
    ap.add_argument("mode", choices=["face", "voice"])
    ap.add_argument("--user", required=True, help="Zoe user id to enroll (e.g. jason)")
    ap.add_argument("--name", default=None, help="Display name (defaults to --user, capitalised)")
    args = ap.parse_args()
    name = args.name or args.user.capitalize()
    if args.mode == "face":
        return enroll_face(args.user, name)
    return enroll_voice(args.user, name)


if __name__ == "__main__":
    sys.exit(main())
