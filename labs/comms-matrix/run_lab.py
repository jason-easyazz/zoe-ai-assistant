#!/usr/bin/env python3
"""Comms Matrix lab runner: measures what deep dive 2026-10-18 section 8 gates 1 and 2 need, on a hermetic Docker network.

Per homeserver (Tuwunel, then Synapse + Postgres), one at a time:
  C0 boot       time to a serving /_matrix/client/versions, idle RAM after a settle
  C1 accounts   six synthetic household accounts (four members, the Zoe bot, a panel) - time each
  C2 federation federation endpoints refused / absent with federation OFF (the network itself has no route out)
  C3 load       one household room + three DMs, 300 messages, an initial /sync per account; RAM after
  C4 MSC4140    delayed events (Element Call cleans up a dead caller's membership with them): schedule, then watch it fire
  C5 MSC4143    the server advertises a LiveKit focus (well-known rtc_foci / rtc/transports)
  C6 call token OpenID token -> lk-jwt-service /sfu/get -> a LiveKit join JWT for the room (the server half of call setup)
  C7 media      `lk load-test` two audio publishers for 20 s through the SFU; LiveKit RAM while it runs (no TURN: LAN only)
RAM is sampled from each container's cgroup (memory.current, 0.5 s) for the whole run; the peak and the steady values are kept.

Synthetic data only (usernames like `member1`), throwaway secrets minted per run and never printed. Everything is torn down at
the end (`compose down -v`, the temp dir removed) and the teardown is checked (report "teardown_ok"). Refuses to start with MemAvailable below --min-mem-mb (2000).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPOSE = HERE / "compose.yaml"
PROJECT = "zoe-matrix-lab"

# Pinned images (tags checked 2026-10-19; the digests are recorded in the report from `docker image inspect`).
IMAGES = {
    "SYNAPSE_IMAGE": "matrixdotorg/synapse:v1.163.0",
    "TUWUNEL_IMAGE": "ghcr.io/matrix-construct/tuwunel:v1.9.4",
    "PG_IMAGE": "postgres:17.6-alpine",
    "LIVEKIT_IMAGE": "livekit/livekit-server:v1.13.10",
    "LKJWT_IMAGE": "ghcr.io/element-hq/lk-jwt-service:0.9.0",
    "LKCLI_IMAGE": "livekit/livekit-cli:v2.19.0",
    "NGINX_IMAGE": "nginx@sha256:1ed1b0e1d7652937d6cbdaf4018c7b6fc009a7dd6c3047351e2eddda745de43f",
}
HS = "http://10.78.0.20:8008"
LKJWT = "http://10.78.0.31:8080"
SERVER_NAME = "hs.lab"
CONTAINERS = ("mxlab-hs", "mxlab-pg", "mxlab-front", "mxlab-livekit", "mxlab-lkjwt", "mxlab-cli")
ACCOUNTS = ("member1", "member2", "member3", "member4", "zoebot", "panel1")


def mem_available_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


def wait_mem(min_mb: int, wait_s: int) -> bool:
    """True once MemAvailable >= min_mb (polls every 10 s for up to wait_s)."""
    deadline = time.monotonic() + wait_s
    while mem_available_mb() < min_mb:
        if time.monotonic() >= deadline:
            return False
        time.sleep(10)
    return True


def sh(args: list[str], env: dict | None = None, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(args, env=env, check=check, capture_output=True, text=True, timeout=timeout)


def compose(env: dict, *args: str, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
    return sh(["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE), *args], env=env, check=check, timeout=timeout)


# ── HTTP ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def http(method: str, url: str, body=None, token: str | None = None, timeout: float = 30.0) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except ValueError:
            return e.code, {"raw": raw[:200].decode(errors="replace")}
    except (urllib.error.URLError, OSError) as e:
        return 0, {"error": str(e)}


def q(s: str) -> str:
    return urllib.parse.quote(s, safe="")


# ── RAM sampling ───────────────────────────────────────────────────────────────────────────────────────────────────────

class Sampler(threading.Thread):
    """memory.current of each lab container every 0.5 s (cgroup v2, systemd driver). Peak per container + a named snapshot."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.paths: dict[str, Path] = {}
        self.peak: dict[str, int] = {}
        self.last: dict[str, int] = {}

    def refresh(self) -> None:
        for name in CONTAINERS:
            if name in self.paths:
                continue
            r = sh(["docker", "inspect", "-f", "{{.Id}}", name], check=False)
            cid = r.stdout.strip()
            if not cid:
                continue
            for cand in (Path(f"/sys/fs/cgroup/system.slice/docker-{cid}.scope/memory.current"),
                         Path(f"/sys/fs/cgroup/docker/{cid}/memory.current")):
                if cand.exists():
                    self.paths[name] = cand
                    break

    def run(self) -> None:
        while not self.stop.is_set():
            for name, p in list(self.paths.items()):
                try:
                    v = int(p.read_text())
                except (OSError, ValueError):
                    continue
                self.last[name] = v
                self.peak[name] = max(self.peak.get(name, 0), v)
            self.stop.wait(0.5)

    def snapshot(self) -> dict[str, float]:
        return {k: round(v / 2**20, 1) for k, v in sorted(self.last.items())}

    def peaks(self) -> dict[str, float]:
        return {k: round(v / 2**20, 1) for k, v in sorted(self.peak.items())}

    def reset_peaks(self) -> None:
        self.peak = dict(self.last)


# ── rendering ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def render_common(run: Path, env: dict) -> None:
    (run / "front" / "tls").mkdir(parents=True)
    sh(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "2",
        "-subj", f"/CN={SERVER_NAME}", "-addext", f"subjectAltName=DNS:{SERVER_NAME}",
        "-keyout", str(run / "front/tls/key.pem"), "-out", str(run / "front/tls/cert.pem")])
    os.chmod(run / "front/tls/key.pem", 0o644)  # read by nginx's worker inside the container; a throwaway lab key
    (run / "front/nginx.conf").write_text("""
events {}
http {
  access_log off;
  server {
    listen 8448 ssl; listen 443 ssl;
    ssl_certificate /etc/nginx/tls/cert.pem; ssl_certificate_key /etc/nginx/tls/key.pem;
    location / { proxy_pass http://10.78.0.20:8008; proxy_set_header Host hs.lab; proxy_set_header X-Forwarded-For $remote_addr; }
  }
}
""")
    (run / "livekit").mkdir()
    (run / "livekit/livekit.yaml").write_text("""
port: 7880
rtc:
  tcp_port: 7881
  port_range_start: 50000
  port_range_end: 50100
  use_external_ip: false
  node_ip: 10.78.0.30
logging:
  level: warn
""")


def render_tuwunel(run: Path, env: dict) -> None:
    d = run / "tuwunel"
    (d / "data").mkdir(parents=True)
    (d / "tuwunel.toml").write_text(f"""
[global]
server_name = "{SERVER_NAME}"
database_path = "/var/lib/tuwunel"
address = ["0.0.0.0"]
port = 8008
allow_registration = true
registration_token = "{env['REG_TOKEN']}"
allow_federation = false
trusted_servers = []
log = "warn"
create_admin_room = false

[global.well_known]
client = "https://{SERVER_NAME}"
livekit_url = "{LKJWT}"
""")


def render_synapse(run: Path, env: dict) -> None:
    d = run / "synapse"
    (d / "media_store").mkdir(parents=True)
    seed = base64.b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    (d / f"{SERVER_NAME}.signing.key").write_text(f"ed25519 a_lab {seed}\n")
    (d / "log.config").write_text("version: 1\nroot: {level: WARNING, handlers: [console]}\nhandlers:\n  console: {class: logging.StreamHandler}\n"
                                  "disable_existing_loggers: false\n")
    foci = json.dumps([{"type": "livekit", "livekit_service_url": LKJWT}])
    (d / "homeserver.yaml").write_text(f"""
server_name: "{SERVER_NAME}"
public_baseurl: "https://{SERVER_NAME}/"
pid_file: /data/homeserver.pid
listeners:
  - port: 8008
    tls: false
    type: http
    x_forwarded: true
    bind_addresses: ['0.0.0.0']
    resources:
      - names: [client, openid]
        compress: false
database:
  name: psycopg2
  args: {{user: synapse, password: "{env['PG_PASSWORD']}", database: synapse, host: 10.78.0.21, cp_min: 2, cp_max: 5}}
log_config: /data/log.config
media_store_path: /data/media_store
signing_key_path: /data/{SERVER_NAME}.signing.key
registration_shared_secret: "{env['REG_SECRET']}"
macaroon_secret_key: "{secrets.token_hex(24)}"
form_secret: "{secrets.token_hex(24)}"
report_stats: false
enable_registration: false
trusted_key_servers: []
suppress_key_server_warning: true
federation_domain_whitelist: []
max_event_delay_duration: 24h
# a lab sends 300 messages in a second: lift the per-user rate limits so the cell measures RAM, not the limiter
rc_message: {{per_second: 1000, burst_count: 1000}}
rc_joins: {{local: {{per_second: 1000, burst_count: 1000}}}}
rc_invites: {{per_room: {{per_second: 1000, burst_count: 1000}}, per_user: {{per_second: 1000, burst_count: 1000}}}}
rc_room_creation: {{per_second: 1000, burst_count: 1000}}
rc_delayed_event_mgmt: {{per_second: 1000, burst_count: 1000}}
experimental_features:
  msc3266_enabled: true
  msc4222_enabled: true
extra_well_known_client_content:
  "org.matrix.msc4143.rtc_foci": {foci}
""")


# ── cells ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def wait_up(timeout_s: float = 240.0) -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        code, _ = http("GET", f"{HS}/_matrix/client/versions", timeout=3)
        if code == 200:
            return time.monotonic() - t0
        time.sleep(1)
    raise RuntimeError("homeserver did not come up")


def register(server: str, env: dict, user: str) -> tuple[str, float]:
    pw = env["USER_PW"]
    t0 = time.monotonic()
    if server == "synapse":
        _, nonce = http("GET", f"{HS}/_synapse/admin/v1/register")
        mac = hmac.new(env["REG_SECRET"].encode(), digestmod=hashlib.sha1)
        mac.update(b"\x00".join([nonce["nonce"].encode(), user.encode(), pw.encode(), b"notadmin"]))
        code, body = http("POST", f"{HS}/_synapse/admin/v1/register",
                          {"nonce": nonce["nonce"], "username": user, "password": pw, "admin": False, "mac": mac.hexdigest()})
    else:
        code, body = http("POST", f"{HS}/_matrix/client/v3/register", {"username": user, "password": pw, "inhibit_login": False})
        if code == 401 and "session" in body:
            code, body = http("POST", f"{HS}/_matrix/client/v3/register", {
                "username": user, "password": pw, "inhibit_login": False,
                "auth": {"type": "m.login.registration_token", "token": env["REG_TOKEN"], "session": body["session"]}})
            if code == 401 and "session" in body:  # some flows add m.login.dummy after the token stage
                code, body = http("POST", f"{HS}/_matrix/client/v3/register", {
                    "username": user, "password": pw, "inhibit_login": False,
                    "auth": {"type": "m.login.dummy", "session": body["session"]}})
    dt = time.monotonic() - t0
    if code != 200 or "access_token" not in body:
        raise RuntimeError(f"register {user}: {code} {str(body)[:200]}")
    return body["access_token"], dt


def federation_off() -> dict:
    out = {}
    for path in ("/_matrix/federation/v1/version", "/_matrix/key/v2/server", "/_matrix/federation/v1/query/profile?user_id=x"):
        code, _ = http("GET", HS + path, timeout=5)
        out[path.split("?")[0]] = code
    out["refused"] = all(c in (403, 404) for c in out.values())
    return out


def load(tokens: dict[str, str], n_messages: int = 300) -> dict:
    members = list(tokens)
    owner = tokens[members[0]]
    t0 = time.monotonic()
    code, room = http("POST", f"{HS}/_matrix/client/v3/createRoom",
                      {"name": "Household", "preset": "private_chat", "invite": [f"@{u}:{SERVER_NAME}" for u in members[1:]]},
                      token=owner)
    rid = room["room_id"]
    for u in members[1:]:
        http("POST", f"{HS}/_matrix/client/v3/join/{q(rid)}", {}, token=tokens[u])
    dms = []
    for a, b in ((members[0], members[1]), (members[0], "zoebot"), (members[1], "zoebot")):
        _, dm = http("POST", f"{HS}/_matrix/client/v3/createRoom",
                     {"is_direct": True, "preset": "trusted_private_chat", "invite": [f"@{b}:{SERVER_NAME}"]}, token=tokens[a])
        http("POST", f"{HS}/_matrix/client/v3/join/{q(dm['room_id'])}", {}, token=tokens[b])
        dms.append((dm["room_id"], a, b))
    send_t = []
    for i in range(n_messages):
        if i % 4 == 3:
            r, a, b = dms[i % 3]
            sender = a if i % 2 else b
        else:
            r, sender = rid, members[i % len(members)]
        s0 = time.monotonic()
        code, _ = http("PUT", f"{HS}/_matrix/client/v3/rooms/{q(r)}/send/m.room.message/t{i}",
                       {"msgtype": "m.text", "body": f"synthetic message {i}"}, token=tokens[sender])
        send_t.append(time.monotonic() - s0)
        if code != 200:
            raise RuntimeError(f"send {i}: {code}")
    sync_t = {}
    for u, tok in tokens.items():
        s0 = time.monotonic()
        code, _ = http("GET", f"{HS}/_matrix/client/v3/sync?timeout=0", token=tok, timeout=60)
        if code != 200:  # a failed sync is a failed cell, never a timing (Codex, #2147)
            raise RuntimeError(f"initial sync {u}: {code}")
        sync_t[u] = round((time.monotonic() - s0) * 1000)
    send_t.sort()
    return {"room_id": rid, "seconds": round(time.monotonic() - t0, 1), "send_ms_p50": round(send_t[len(send_t) // 2] * 1000, 1),
            "send_ms_p95": round(send_t[int(len(send_t) * 0.95)] * 1000, 1), "initial_sync_ms": sync_t}


def delayed_events(tokens: dict[str, str], rid: str) -> dict:
    """MSC4140: schedule a call-membership state event 3 s out, check it is NOT there yet, then that it fired."""
    tok = tokens["member1"]
    key = f"_@member1:{SERVER_NAME}_LABDEVICE"
    etype = "org.matrix.msc3401.call.member"
    code, body = http("PUT", f"{HS}/_matrix/client/v3/rooms/{q(rid)}/state/{etype}/{q(key)}?org.matrix.msc4140.delay=3000",
                      {"lab": "fired"}, token=tok)
    out = {"schedule_status": code, "delay_id": bool(body.get("delay_id"))}
    if code != 200 or not body.get("delay_id"):
        out["supported"] = False
        out["detail"] = str(body)[:160]
        return out
    c_before, _ = http("GET", f"{HS}/_matrix/client/v3/rooms/{q(rid)}/state/{etype}/{q(key)}", token=tok)
    # restart once (the keep-alive a live client sends), then let it lapse
    c_restart, _ = http("POST", f"{HS}/_matrix/client/unstable/org.matrix.msc4140/delayed_events/{q(body['delay_id'])}/restart",
                        {}, token=tok)
    if c_restart in (404, 405):
        c_restart, _ = http("POST", f"{HS}/_matrix/client/unstable/org.matrix.msc4140/delayed_events/{q(body['delay_id'])}",
                            {"action": "restart"}, token=tok)
    time.sleep(5.0)
    c_after, after = http("GET", f"{HS}/_matrix/client/v3/rooms/{q(rid)}/state/{etype}/{q(key)}", token=tok)
    out.update(before_status=c_before, restart_status=c_restart, after_status=c_after,
               supported=(c_before == 404 and c_restart == 200 and c_after == 200 and after.get("lab") == "fired"))
    return out


def rtc_focus() -> dict:
    c1, wk = http("GET", f"{HS}/.well-known/matrix/client")
    c2, tr = http("GET", f"{HS}/_matrix/client/unstable/org.matrix.msc4143/rtc/transports")
    foci = wk.get("org.matrix.msc4143.rtc_foci") if isinstance(wk, dict) else None
    transports = tr.get("rtc_transports") if isinstance(tr, dict) else None
    def good(lst) -> bool:
        return isinstance(lst, list) and any(isinstance(f, dict) and f.get("type") == "livekit"
                                             and f.get("livekit_service_url") == LKJWT for f in lst)

    return {"well_known_status": c1, "rtc_foci": foci, "transports_status": c2, "transports": transports,
            "advertised": good(foci) or good(transports)}


def _jwt_claims(jwt: str, secret: str) -> dict | None:
    """Verify an HS256 LiveKit JWT with the lab's API secret; the claims, or None when the signature or shape is wrong."""
    parts = jwt.split(".")
    if len(parts) != 3:
        return None

    def b64(x: str) -> bytes:
        return base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))

    try:
        header = json.loads(b64(parts[0]))
        sig = hmac.new(secret.encode(), f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest()
        if header.get("alg") != "HS256" or not hmac.compare_digest(sig, b64(parts[2])):
            return None
        return json.loads(b64(parts[1]))
    except (ValueError, TypeError):
        return None


def call_token(tokens: dict[str, str], rid: str, secret: str) -> dict:
    """C6: each member's OpenID token -> lk-jwt /sfu/get -> a LiveKit JWT. Passes only when every JWT is SIGNED by the lab's
    LiveKit secret, names that member as its subject, grants join + publish + subscribe, and both members land in the SAME
    LiveKit room (lk-jwt derives the room name from the Matrix room)."""
    out: dict = {}
    rooms = set()
    for u in ("member1", "member2"):
        t0 = time.monotonic()
        code, oid = http("POST", f"{HS}/_matrix/client/v3/user/{q('@' + u + ':' + SERVER_NAME)}/openid/request_token", {},
                         token=tokens[u])
        if code != 200:
            out[u] = {"openid_status": code, "ok": False}
            continue
        code2, sfu = http("POST", f"{LKJWT}/sfu/get", {"room": rid, "openid_token": oid, "device_id": f"LAB{u.upper()}"},
                          timeout=40)
        dt = round((time.monotonic() - t0) * 1000)
        jwt = sfu.get("jwt", "") if isinstance(sfu, dict) else ""
        c = _jwt_claims(jwt, secret) or {}
        video = c.get("video") or {}
        cell = {"status": code2, "ms": dt, "signature_ok": bool(c), "sub_is_user": str(c.get("sub", "")).startswith(f"@{u}:"),
                "room_join": video.get("roomJoin") is True, "can_publish": video.get("canPublish") is not False,
                "can_subscribe": video.get("canSubscribe") is not False, "detail": "" if code2 == 200 else str(sfu)[:200]}
        cell["ok"] = code2 == 200 and all(cell[k] for k in ("signature_ok", "sub_is_user", "room_join", "can_publish", "can_subscribe"))
        if video.get("room"):
            rooms.add(video["room"])
        out[u] = cell
    out["same_room"] = len(rooms) == 1
    out["livekit_room"] = next(iter(rooms)) if len(rooms) == 1 else ""
    out["ok"] = out["same_room"] and all(out[u].get("ok") for u in ("member1", "member2"))
    return out


def media(env: dict, sampler: Sampler, room: str) -> dict:
    """C7: one audio publisher and one subscriber in the Matrix call's LiveKit room for 20 s; the subscriber must receive.
    lk load-test signs its own participant tokens with the API key (it takes no token), so this proves the SFU path in the
    Matrix-derived room, not a client join WITH the Matrix JWT (that needs a WebRTC client; see README)."""
    sampler.reset_peaks()
    t0 = time.monotonic()
    proc = subprocess.Popen(["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE), "--profile", "cli", "run", "--rm", "-T", "cli",
                             "load-test", "--room", room or "lab-call", "--audio-publishers", "1", "--subscribers", "1",
                             "--duration", "20s"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    proc.wait()
    text = proc.stdout.read() if proc.stdout else ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    peaks = sampler.peaks()
    return {"exit": proc.returncode, "seconds": round(time.monotonic() - t0, 1), "room": room or "lab-call",
            "livekit_peak_mb": peaks.get("mxlab-livekit"), "summary": lines[-24:]}


# ── one homeserver ─────────────────────────────────────────────────────────────────────────────────────────────────────

def run_server(server: str, run: Path, env: dict, settle_s: int, min_mem: int, wait_s: int) -> dict:
    res: dict = {"server": server, "image": env["SYNAPSE_IMAGE" if server == "synapse" else "TUWUNEL_IMAGE"]}
    if not wait_mem(min_mem, wait_s):
        res["refused"] = f"MemAvailable {mem_available_mb()} MB < {min_mem} MB"
        return res
    (render_synapse if server == "synapse" else render_tuwunel)(run, env)
    sampler = Sampler()
    try:
        compose(env, "--profile", server, "up", "-d", timeout=300)
        sampler.refresh()
        sampler.start()
        res["boot_s"] = round(wait_up(), 1)
        sampler.refresh()
        time.sleep(settle_s)
        res["ram_idle_mb"] = sampler.snapshot()
        tokens, reg_t = {}, []
        for u in ACCOUNTS:
            tok, dt = register(server, env, u)
            tokens[u] = tok
            reg_t.append(round(dt * 1000))
        res["accounts_ms"] = reg_t
        res["federation_off"] = federation_off()
        res["load"] = load(tokens)
        time.sleep(5)
        res["ram_after_load_mb"] = sampler.snapshot()
        rid = res["load"]["room_id"]
        res["msc4140_delayed_events"] = delayed_events(tokens, rid)
        res["msc4143_rtc_focus"] = rtc_focus()
        res["call_token"] = call_token(tokens, rid, env["LK_SECRET"])
        res["media"] = media(env, sampler, res["call_token"].get("livekit_room", ""))
        res["ram_peak_mb_during_media"] = sampler.peaks()  # per-container maxima over C7 only; not simultaneous
        res["min_mem_available_mb_seen"] = mem_available_mb()
    except Exception as exc:  # noqa: BLE001 - a lab cell failing is a result, not a crash
        res["error"] = f"{type(exc).__name__}: {exc}"[:400]
        logs = compose(env, "logs", "--no-color", "--tail", "25", check=False)
        res["logs_tail"] = logs.stdout.splitlines()[-25:]
    finally:
        sampler.stop.set()
        compose(env, "--profile", server, "--profile", "cli", "down", "-v", "--remove-orphans", check=False, timeout=300)
    return res


def verdict(res: dict) -> dict:
    """The cells a run must pass. MSC4140 is REPORTED, not required: a server without it is a finding, not a lab failure."""
    ct = res.get("call_token") or {}
    md = res.get("media") or {}
    checks = {
        "no_error": "error" not in res and "refused" not in res,
        "federation_refused": bool((res.get("federation_off") or {}).get("refused")),
        "load_done": bool((res.get("load") or {}).get("room_id")),
        "focus_advertised": bool((res.get("msc4143_rtc_focus") or {}).get("advertised")),
        "call_token_ok": bool(ct.get("ok")),
        "media_exit_0": md.get("exit") == 0,
        # lk load-test's subscriber summary: the one subscriber got the one published audio track
        "media_received": any("Total" in ln and "1/1" in ln for ln in (md.get("summary") or [])),
    }
    return {"checks": checks, "pass": all(checks.values())}


def lab_leftovers() -> list[str]:
    """Anything of this lab still on the box: labelled containers, the network, labelled volumes."""
    left = []
    for args in (["docker", "ps", "-a", "--filter", "label=zoe.matrix-lab=1", "--format", "{{.Names}}"],
                 ["docker", "network", "ls", "--filter", f"name={PROJECT}", "--format", "{{.Name}}"],
                 ["docker", "volume", "ls", "--filter", f"label=com.docker.compose.project={PROJECT}", "--format", "{{.Name}}"]):
        left += [ln for ln in sh(args, check=False).stdout.splitlines() if ln.strip()]
    return left


def pull_images() -> None:
    """Pull every pinned image BEFORE the report records digests, so a clean host's report carries real provenance (Codex,
    #2147). A pull failure aborts the run: a lab whose images cannot be resolved has nothing to measure."""
    for img in IMAGES.values():
        sh(["docker", "pull", "-q", img], timeout=900)


def image_digests() -> dict:
    out = {}
    for k, img in IMAGES.items():
        r = sh(["docker", "image", "inspect", "-f", "{{json .RepoDigests}}", img], check=False)
        try:
            out[img] = (json.loads(r.stdout or "[]") or [""])[0]
        except ValueError:
            out[img] = ""
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--servers", default="tuwunel,synapse")
    ap.add_argument("--min-mem-mb", type=int, default=2000)
    ap.add_argument("--settle-s", type=int, default=30)
    ap.add_argument("--wait-mem-s", type=int, default=300, help="how long to wait for the memory gate before each server")
    ap.add_argument("--report", default="")
    a = ap.parse_args()
    if not wait_mem(a.min_mem_mb, a.wait_mem_s):
        print(f"REFUSED: MemAvailable {mem_available_mb()} MB < {a.min_mem_mb} MB", file=sys.stderr)
        return 4
    run = Path(tempfile.mkdtemp(prefix="zoe-matrix-lab-"))
    os.chmod(run, 0o700)
    env = dict(os.environ, RUN=str(run), LAB_UID=str(os.getuid()), LAB_GID=str(os.getgid()), **IMAGES,
               LK_KEY="labkey", LK_SECRET=secrets.token_hex(24), PG_PASSWORD=secrets.token_hex(16),
               REG_SECRET=secrets.token_hex(24), REG_TOKEN=secrets.token_hex(12), USER_PW=secrets.token_hex(12))
    report = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "host_mem_available_mb": mem_available_mb(),
              "images": {}, "results": []}
    try:
        pull_images()
        report["images"] = image_digests()
        report["images_resolved"] = all(report["images"].values())
        render_common(run, env)
        for server in [s.strip() for s in a.servers.split(",") if s.strip()]:
            print(f"== {server}", file=sys.stderr, flush=True)
            report["results"].append(run_server(server, run, env, a.settle_s, a.min_mem_mb, a.wait_mem_s))
    finally:
        compose(env, "--profile", "tuwunel", "--profile", "synapse", "--profile", "cli", "down", "-v", "--remove-orphans",
                check=False, timeout=300)
        shutil.rmtree(run, ignore_errors=True)
    for res in report["results"]:
        res["verdict"] = verdict(res)
    report["teardown_leftovers"] = lab_leftovers()
    report["teardown_ok"] = not report["teardown_leftovers"] and not run.exists()
    report["pass"] = (report["teardown_ok"] and bool(report.get("images_resolved")) and bool(report["results"])
                      and all(r["verdict"]["pass"] for r in report["results"]))
    report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    text = json.dumps(report, indent=2)
    if a.report:
        Path(a.report).write_text(text + "\n")
    print(text)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
