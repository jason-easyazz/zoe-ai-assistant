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
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

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


def say(text: str) -> None:
    """Speak through the panel: server TTS (Zoe's real voice) -> aplay;
    espeak-ng fallback so guidance never goes silent."""
    print(f"[zoe says] {text}", flush=True)
    try:
        audio = _api("/api/voice/synthesize", {"text": text}, raw=True)
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
    except Exception:
        subprocess.run(["espeak-ng", "-s", "150", text], check=False, timeout=20)


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
            picked = face._pick_best_face(dets, kps)
            if picked is None:
                continue
            det, kp = picked
            v = float((det[2] - det[0]) * (det[3] - det[1]) * det[4])
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


def enroll_voice(user: str, name: str, daemon_service: str = "zoe-voice") -> int:
    say(f"Hi {name}. Let's teach me your voice. I'll ask you to say three short "
        "phrases. I store a voice signature — numbers, not recordings.")
    # The Jabra can't hold two input streams — pause the daemon for the window.
    subprocess.run(["systemctl", "--user", "stop", daemon_service], check=False)
    time.sleep(2)
    uploaded = 0
    try:
        for i, phrase in enumerate(VOICE_PHRASES, 1):
            say(f"Phrase {i}. After the beep, please say: {phrase}")
            time.sleep(0.4)
            chime = os.path.join(HERE, "chime.wav")
            if os.path.exists(chime):
                subprocess.run(["aplay", "-q", chime], check=False, timeout=5)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
            try:
                if not _record_wav(6, wav_path):
                    say("I didn't catch that. Let's move on.")
                    continue
                out = _api("/api/voice/enroll", {
                    "audio_base64": base64.b64encode(open(wav_path, "rb").read()).decode(),
                    "user_id": user, "display_name": name, "consent": True,
                })
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
