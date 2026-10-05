#!/usr/bin/env python3
"""Virtual-panel preflight: prove the Mac, the tunnel and the token BEFORE the daemon runs.

    python preflight.py devices      # list PortAudio devices (pick AUDIO_DEVICE / AUDIO_OUTPUT_DEVICE)
    python preflight.py check        # mic level + speaker tone + one round trip to zoe-data
    python preflight.py check --no-audio --no-server    # any subset

Reads the same environment the daemon reads (the `run` command sources .env.voice
first): ZOE_URL, DEVICE_TOKEN, PANEL_ID, VERIFY_SSL, CF_ACCESS_CLIENT_ID/SECRET,
AUDIO_DEVICE, AUDIO_OUTPUT_DEVICE. It never prints a secret.

The pure functions (``classify_probe``, ``level_report``, ``build_headers``) carry the
logic and are unit-tested (tests/unit/test_mac_virtual_panel.py); the PortAudio and
HTTP edges are thin and take their collaborators as arguments.
"""
from __future__ import annotations

import argparse
import base64
import io
import math
import os
import sys
import wave

# ── pure logic ────────────────────────────────────────────────────────────────


def url_may_carry_access_secret(url: str) -> bool:
    """Mirror of zoe_voice_daemon._zoe_url_may_carry_access_secret (a parity test pins
    them together): https and a public host, never a LAN/loopback/.local address."""
    import ipaddress
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if parts.scheme != "https" or not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." in host and not host.endswith((".local", ".localhost", ".lan", ".internal", ".home.arpa"))
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified
                or ip.is_multicast or ip.is_reserved)


def build_headers(env) -> dict:
    """The request headers the daemon builds: device token, plus the Cloudflare Access
    service-token pair when BOTH halves are set AND ZOE_URL may carry it (mirrors
    zoe_voice_daemon._headers)."""
    headers = {"X-Device-Token": env.get("DEVICE_TOKEN", ""), "Content-Type": "application/json"}
    cid = (env.get("CF_ACCESS_CLIENT_ID") or "").strip()
    secret = (env.get("CF_ACCESS_CLIENT_SECRET") or "").strip()
    if cid and secret and url_may_carry_access_secret(env.get("ZOE_URL", "")):
        headers["CF-Access-Client-Id"] = cid
        headers["CF-Access-Client-Secret"] = secret
    return headers


def classify_probe(status: int, location: str = "", content_type: str = "", body: str = "",
                   sent_access_headers: bool = True) -> tuple:
    """Turn the answer to the authenticated probe into (ok, one-line diagnosis).

    The failure that matters is Cloudflare Access: it does not return 401 to an API
    client, it 302s to its login page (or 403s a bad service token), and `requests`
    would follow the redirect and hand the daemon an HTML page where it expects JSON.
    """
    loc = (location or "").lower()
    body_l = (body or "")[:2000].lower()
    if status in (301, 302, 303, 307, 308):
        if "cloudflareaccess.com" in loc or "/cdn-cgi/access" in loc:
            if not sent_access_headers:
                return False, ("Cloudflare Access redirected to its login: set CF_ACCESS_CLIENT_ID and "
                               "CF_ACCESS_CLIENT_SECRET (a Service Auth policy must cover /api/voice/*)")
            return False, ("Cloudflare Access redirected to its login DESPITE the service token: the "
                           "token is wrong/expired, or no Service Auth policy covers /api/voice/*")
        return False, f"unexpected redirect to {location!r} (is ZOE_URL the tunnel host, https?)"
    if status == 403 and ("cloudflare" in body_l or "access" in body_l):
        return False, ("Cloudflare Access refused the request (403): the service token is not "
                       "in a Service Auth policy for this path, or it expired")
    if status == 401:
        return False, ("zoe-data rejected the device token (401): wrong, revoked or expired "
                       "DEVICE_TOKEN for this PANEL_ID")
    if status in (502, 503, 504, 520, 521, 522, 523, 524):
        return False, f"origin unreachable behind the tunnel (HTTP {status}): zoe-data/zoe-ui down or restarting"
    if status == 200:
        if "json" not in (content_type or "").lower():
            return False, ("200 but not JSON - an Access/captive-portal HTML page answered instead "
                           "of zoe-data")
        return True, "zoe-data accepted the device token"
    return False, f"unexpected HTTP {status}"


def level_report(peak: int, rms: float, seconds: float) -> tuple:
    """(ok, diagnosis) for a short mic capture of 16-bit samples."""
    if peak == 0:
        return False, ("the microphone delivered pure digital silence - macOS microphone permission "
                       "is almost certainly denied for this terminal app (System Settings > Privacy & "
                       "Security > Microphone), or the wrong input device is selected")
    if rms < 8:
        return False, f"mic level is extremely low (rms {rms:.1f}/32768) - wrong input or muted"
    return True, f"mic OK over {seconds:.1f}s (peak {peak}, rms {rms:.0f} of 32768)"


def tone_wav(freq: float = 660.0, seconds: float = 0.35, rate: int = 16000, amp: float = 0.2) -> bytes:
    n = int(rate * seconds)
    frames = bytearray()
    for i in range(n):
        env = min(1.0, i / (rate * 0.01)) * min(1.0, (n - i) / (rate * 0.03))
        s = int(32767 * amp * env * math.sin(2 * math.pi * freq * i / rate))
        frames += s.to_bytes(2, "little", signed=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


def probe_server(env, post, *, text: str = "Virtual panel check.") -> tuple:
    """One authenticated round trip: POST /api/voice/speak (device-token auth + a real
    TTS render). ``post(url, json=, headers=, timeout=, verify=, allow_redirects=)`` is
    ``requests.post`` in production. Returns (ok, diagnosis, audio_wav_bytes_or_None)."""
    base = (env.get("ZOE_URL") or "").rstrip("/")
    if not base.startswith("https://") and not base.startswith("http://"):
        return False, "ZOE_URL is not set to an http(s) URL", None
    if not env.get("DEVICE_TOKEN"):
        return False, "DEVICE_TOKEN is empty (provision mac-dev first; see docs/knowledge/mac-virtual-panel.md)", None
    if ((env.get("CF_ACCESS_CLIENT_ID") or "").strip() and (env.get("CF_ACCESS_CLIENT_SECRET") or "").strip()
            and not url_may_carry_access_secret(base)):
        return False, ("CF_ACCESS_CLIENT_ID/SECRET are set but ZOE_URL is not a public https URL: the daemon "
                       "refuses to send the Access secret there (it would reach a LAN host, in the clear for http)"), None
    headers = build_headers(env)
    verify = (env.get("VERIFY_SSL", "true").lower() not in ("false", "0", "no"))
    try:
        r = post(f"{base}/api/voice/speak", json={"text": text}, headers=headers, timeout=30,
                 verify=verify, allow_redirects=False)
    except Exception as exc:  # DNS, TLS, timeout: say which, never a traceback
        return False, f"could not reach {base}: {type(exc).__name__}: {exc}", None
    ok, why = classify_probe(
        r.status_code, r.headers.get("Location", ""), r.headers.get("Content-Type", ""),
        getattr(r, "text", "") if r.status_code != 200 else "",
        sent_access_headers="CF-Access-Client-Id" in headers,
    )
    if not ok:
        return False, why, None
    try:
        audio = base64.b64decode(r.json().get("audio_base64") or "")
    except Exception:
        audio = b""
    return True, why + (f"; TTS returned {len(audio)} bytes" if audio else "; TTS returned no audio"), audio or None


# ── PortAudio edges ───────────────────────────────────────────────────────────


def list_devices(pa) -> list:
    """Rows of (index, name, in_channels, out_channels, default_rate, flags)."""
    try:
        d_in = pa.get_default_input_device_info()["index"]
    except Exception:
        d_in = None
    try:
        d_out = pa.get_default_output_device_info()["index"]
    except Exception:
        d_out = None
    rows = []
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        flags = ("default-in " if i == d_in else "") + ("default-out" if i == d_out else "")
        rows.append((i, str(info.get("name", "?")), int(info.get("maxInputChannels", 0)),
                     int(info.get("maxOutputChannels", 0)), int(info.get("defaultSampleRate", 0)), flags.strip()))
    return rows


def capture_level(pa, pyaudio_mod, device_index=None, seconds: float = 2.0, rate: int = 16000, chunk: int = 1280):
    import numpy as np
    kw = dict(format=pyaudio_mod.paInt16, channels=1, rate=rate, input=True, frames_per_buffer=chunk)
    if device_index is not None:
        kw["input_device_index"] = device_index
    stream = pa.open(**kw)
    try:
        data = b"".join(stream.read(chunk, exception_on_overflow=False)
                        for _ in range(max(1, int(seconds * rate / chunk))))
    finally:
        stream.stop_stream()
        stream.close()
    a = np.frombuffer(data, dtype=np.int16).astype(np.float64)
    return int(np.abs(a).max()) if a.size else 0, float(np.sqrt((a ** 2).mean())) if a.size else 0.0


def _resolve_input_index(pa, spec: str):
    """Same rule as the daemon's main(): digits = index, otherwise name substring."""
    if not spec or spec == "default":
        return None
    if spec.isdigit():
        return int(spec)
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if int(info.get("maxInputChannels", 0)) > 0 and spec in str(info.get("name", "")):
            return i
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("devices", "check"))
    ap.add_argument("--no-audio", action="store_true", help="skip the mic level + speaker tone")
    ap.add_argument("--no-server", action="store_true", help="skip the zoe-data round trip")
    args = ap.parse_args(argv)
    env = os.environ
    failures = 0
    backend = None

    if args.command == "devices" or not args.no_audio:
        import pyaudio
        pa = pyaudio.PyAudio()
    if args.command == "devices":
        print(f"{'idx':>3}  {'in':>2} {'out':>3} {'rate':>6}  name")
        for i, name, n_in, n_out, rate, flags in list_devices(pa):
            print(f"{i:>3}  {n_in:>2} {n_out:>3} {rate:>6}  {name}  {flags}")
        print("\nSet AUDIO_DEVICE / AUDIO_OUTPUT_DEVICE in .env.voice to an index or a name substring.")
        pa.terminate()
        return 0

    if not args.no_audio:
        idx = _resolve_input_index(pa, env.get("AUDIO_DEVICE", "default"))
        try:
            peak, rms = capture_level(pa, pyaudio, idx)
            ok, why = level_report(peak, rms, 2.0)
        except Exception as exc:
            ok, why = False, f"could not open the microphone: {exc}"
        print(("[ ok ] " if ok else "[FAIL] ") + why)
        failures += 0 if ok else 1
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from mac_backend import MacBackend
            backend = MacBackend(pyaudio_module=pyaudio, output_device=env.get("AUDIO_OUTPUT_DEVICE", "default"))
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(tone_wav())
            backend.play_file_blocking(f.name, timeout=5)
            os.unlink(f.name)
            print("[ ok ] played a test tone - did you hear it?")
        except Exception as exc:
            print(f"[FAIL] could not play a test tone: {exc}")
            failures += 1

    if not args.no_server:
        import requests
        ok, why, audio = probe_server(env, requests.post)
        print(("[ ok ] " if ok else "[FAIL] ") + why)
        failures += 0 if ok else 1
        if ok and audio and backend is not None:
            try:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    f.write(audio)
                backend.play_file_blocking(f.name, timeout=30)
                os.unlink(f.name)
                print("[ ok ] played Zoe's TTS reply through the Mac speakers")
            except Exception as exc:
                print(f"[FAIL] could not play the TTS reply: {exc}")
                failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
