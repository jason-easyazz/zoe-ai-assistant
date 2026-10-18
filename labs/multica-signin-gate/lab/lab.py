"""Multica board behind Zoe's sign-in: the hermetic lab. Hand-run via ../run.sh only.

One INTERNAL docker network (no egress, nothing published) holding:
  pg       Postgres (the box's own pgvector image, tmpfs) with a ``multica`` database and a ``zoe`` database that carries the
           two tables the adults-only check reads (``auth_users``, ``member_modes``), seeded with synthetic people
  mbackend the LIVE Multica backend image (by the digest compose pins), aliased zoe-multica-backend
  mweb     the LIVE Multica web image (by the digest the running container uses), aliased zoe-multica-web
  gate     the REAL zoe-data gate router from this worktree + a zoe-auth stand-in (gate_app.py)
  nginx    the repo's REAL nginx.conf + nginx.d/ + dist/ from this worktree; only upstream HOSTS are re-pointed at the lab
  nginx-nogate  NEGATIVE CONTROL: the same conf with every board ``auth_request`` line removed
  driver   a stdlib client inside the network (driver.py)
Every container is memory-capped and removed at the end; the network too. Prints each cell and exits non-zero on a failure.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
UI = REPO / "services" / "zoe-ui"
ZOE_DATA = REPO / "services" / "zoe-data"
VENV_SITE = Path(os.environ.get("LAB_VENV_SITE", "/home/zoe/.zoe/venvs/zoe-data-py312/lib/python3.12/site-packages"))

TAG = f"msg-lab-{secrets.token_hex(3)}"
NET = TAG
# Images: the box's own copies, by what compose pins / what runs. Nothing is pulled (the network is internal anyway).
PG_IMAGE = os.environ.get("LAB_PG_IMAGE", "pgvector/pgvector:0.8.6-pg17")
BACKEND_IMAGE = os.environ.get(
    "LAB_BACKEND_IMAGE",
    "ghcr.io/multica-ai/multica-backend@sha256:3eab9527c9d555e8dfcbc0cd4b2557da99b71db9334f089d4cc902b1e9c1dbe1")
WEB_IMAGE = os.environ.get(
    "LAB_WEB_IMAGE", "ghcr.io/multica-ai/multica-web@sha256:f05d75720ca2c6e23cb3b52af56789fe331022de1d06fc711d7a215d9f457283")
PY_IMAGE = os.environ.get("LAB_PY_IMAGE", "python:3.12-slim-bookworm")
NGINX_IMAGE = os.environ.get(
    "LAB_NGINX_IMAGE", "nginx@sha256:1ed1b0e1d7652937d6cbdaf4018c7b6fc009a7dd6c3047351e2eddda745de43f")

PG_PW = secrets.token_hex(12)
GATE_SECRET = secrets.token_hex(24)
DEV_CODE = f"{secrets.randbelow(900000) + 100000}"
EMAIL = "owner@lab.example"
SESSIONS = {  # synthetic only
    "sid-ada": ["ada_uid", "user"], "sid-fam": ["fam_uid", "family-admin"], "sid-kid": ["kid_uid", "child"],
    "sid-minor": ["minor_uid", "user"], "sid-odd": ["odd_uid", "visitor"], "sid-guest": ["guest", "guest"],
}
SEED = """
CREATE TABLE auth_users (user_id TEXT PRIMARY KEY, username TEXT, role TEXT NOT NULL);
CREATE TABLE member_modes (user_id TEXT PRIMARY KEY, mode TEXT NOT NULL, minor INTEGER NOT NULL DEFAULT 0,
                           updated_by TEXT, updated_at TEXT);
INSERT INTO auth_users VALUES ('ada_uid','ada','user'), ('fam_uid','fam','family-admin'), ('kid_uid','kid','child'),
                              ('minor_uid','minor','user'), ('odd_uid','odd','visitor');
INSERT INTO member_modes VALUES ('minor_uid','kid',1,'lab','t');
"""

created: list[str] = []
results: list[tuple[str, bool, str]] = []


def sh(*args, check=True, capture=True, timeout=300):
    r = subprocess.run(list(args), capture_output=capture, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:4])}... -> {r.returncode}: {r.stderr.strip()[-800:]}")
    return r


def run_container(name, image, *extra, aliases=(), mem="256m", cmd=()):
    full = f"{TAG}-{name}"
    args = ["docker", "run", "-d", "--name", full, "--network", NET, "--network-alias", name,
            "--memory", mem, "--memory-swap", mem, "--pids-limit", "256", "--label", f"zoe-lab={TAG}"]
    for a in aliases:
        args += ["--network-alias", a]
    sh(*args, *extra, image, *cmd)
    created.append(full)
    return full


def wait(desc, fn, timeout=150):
    end = time.time() + timeout
    last = ""
    while time.time() < end:
        try:
            ok, last = fn()
            if ok:
                return
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        time.sleep(1.5)
    raise RuntimeError(f"timed out waiting for {desc}: {last[-600:]}")


def driver(phase, nginx="nginx"):
    r = sh("docker", "exec", "-e", f"LAB_NGINX={nginx}", f"{TAG}-driver", "python3", "/lab/driver.py", phase, timeout=240)
    return json.loads(r.stdout)


def cell(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""), flush=True)


def conf_dir(root: Path, *, gated: bool) -> Path:
    """A copy of the worktree's real conf with upstream HOSTS re-pointed at the lab (and, for the control, no board gate)."""
    shutil.copytree(UI / "nginx.d", root / "nginx.d")
    shutil.copy(UI / "nginx.conf", root / "default.conf")
    loc = root / "nginx.d" / "locations.inc"
    text = loc.read_text()
    # Today's upstreams are host-gateway ports; after the loopback move (#2130) they are the container names, which the
    # lab containers carry as aliases - so both forms land on the lab.
    text = (text.replace("host.docker.internal:8000", "gate:8000")
                .replace("host.docker.internal:8080", "zoe-multica-backend:8080")
                .replace("host.docker.internal:3000", "zoe-multica-web:3000"))
    if not gated:
        pat = re.compile(r"^\s*auth_request\s+/_zoe/multica-gate;\s*$", re.M)
        assert len(pat.findall(text)) == 3, "the real conf should gate exactly three board prefixes"
        text = pat.sub("", text)
    loc.write_text(text)
    ssl = root / "ssl"
    ssl.mkdir()
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=lab",
       "-keyout", str(ssl / "zoe.key"), "-out", str(ssl / "zoe.crt"))
    for p in [root, root / "nginx.d", ssl, *root.rglob("*")]:
        p.chmod(0o755 if p.is_dir() else 0o644)
    return root


def nginx(name, conf: Path):
    # Other upstream hosts in the conf (zoe-auth, Music Assistant, HA, LiveKit) resolve to a dead loopback, exactly as
    # deploy.yml's preflight does; the lab never routes to them.
    return run_container(name, NGINX_IMAGE, "--add-host", "zoe-auth:127.0.0.1",
                         "--add-host", "host.docker.internal:127.0.0.1",
                         "-v", f"{conf}/default.conf:/etc/nginx/conf.d/default.conf:ro",
                         "-v", f"{conf}/nginx.d:/etc/nginx/zoe:ro", "-v", f"{conf}/ssl:/etc/nginx/ssl:ro",
                         "-v", f"{UI / 'dist'}:/usr/share/nginx/html:ro", mem="64m")


def teardown():
    for c in reversed(created):
        subprocess.run(["docker", "rm", "-f", c], capture_output=True)
    subprocess.run(["docker", "network", "rm", NET], capture_output=True)
    left = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=zoe-lab={TAG}"], capture_output=True, text=True).stdout
    nets = subprocess.run(["docker", "network", "ls", "-q", "--filter", f"name={NET}"], capture_output=True, text=True).stdout
    if left.strip() or nets.strip():
        print(f"TEARDOWN INCOMPLETE: containers={left.split()} networks={nets.split()}", file=sys.stderr)
        sys.exit(2)
    print("teardown: all lab containers and the network are gone")


def judge_main(o):
    nx = lambda r: r.get("nginx_error_page")  # noqa: E731  - nginx wrote this answer itself: Multica never saw the request
    print("\n-- anonymous: no board, and Multica's own code login is unreachable")
    cell("A1 anonymous board page -> 302 to the Zoe launcher",
         o["anon_page"]["status"] == 302 and o["anon_page"]["location"].endswith("/board.html?next=/multica/"),
         f"{o['anon_page']['status']} {o['anon_page']['location']}")
    cell("A2 anonymous /multica-api/health -> gate 401", o["anon_api_health"]["status"] == 401 and nx(o["anon_api_health"]))
    cell("A3 anonymous /_next/ asset -> gate 401", o["anon_asset"]["status"] == 401 and nx(o["anon_asset"]))
    cell("A4 anonymous WebSocket handshake -> gate 401", o["anon_ws"]["status"] == 401 and o["anon_ws"]["nginx_error_page"],
         str(o["anon_ws"]))
    login = o["anon_login"]
    cell("A5 code login unreachable: send-code AND verify-code with the REAL dev code -> gate 401, no Multica session",
         login["send_code"]["status"] == 401 and nx(login["send_code"]) and login["verify_code"]["status"] == 401
         and nx(login["verify_code"]) and not login["got_multica_session"] and login["me"]["status"] == 401,
         json.dumps({k: login[k]["status"] for k in ("send_code", "verify_code", "me")}))
    cell("A6 the gate location is internal (direct request -> 404)", o["gate_direct"]["status"] == 404)
    cell("A7 the launcher page is served to anyone (it is static; it gates itself via auth.js)",
         o["launcher_page"]["status"] == 200 and "/api/multica-ui/session" in o["launcher_page"]["body"])

    print("\n-- an adult member signs in (Zoe session -> board cookie -> the board, then Multica's own login)")
    paths = sorted(re.search(r"Path=([^;]+)", c).group(1) for c in o["member_mint"]["set_cookie"])
    cell("B1 member mints the cookie: 200, three HttpOnly SameSite=Strict cookies on the three board paths",
         o["member_mint"]["status"] == 200 and paths == ["/_next/", "/multica-api/", "/multica/"]
         and all("HttpOnly" in c and "SameSite=strict" in c for c in o["member_mint"]["set_cookie"]),
         f"{o['member_mint']['status']} {paths}")
    cell("B2 member + cookie: the board page is Multica's (200, Next.js HTML)",
         o["member_page"]["status"] == 200 and "/_next/" in o["member_page"]["body"] and not nx(o["member_page"]),
         f"{o['member_page']['status']}")
    cell("B3 member + cookie: a /_next/ asset from that page loads", bool(o["member_asset_path"])
         and o["member_asset"]["status"] == 200, f"{o['member_asset_path']} {o['member_asset'].get('status')}")
    cell("B4 member + cookie: /multica-api/health -> 200 from Multica", o["member_api_health"]["status"] == 200
         and not nx(o["member_api_health"]))
    ml = o["member_login"]
    cell("B5 member + cookie: Multica's own dev-code login works as the SECOND step (session, /api/me = the email)",
         ml["send_code"]["status"] == 200 and ml["verify_code"]["status"] == 200 and ml["got_multica_session"]
         and ml["me"]["status"] == 200 and EMAIL in ml["me"]["body"],
         json.dumps({k: ml[k]["status"] for k in ("send_code", "verify_code", "me")}))
    cell("B6 member + cookie + Multica session: the WebSocket handshake reaches Multica (not the gate's 401)",
         o["member_ws"]["status"] != 401 and not o["member_ws"]["nginx_error_page"], str(o["member_ws"]))
    cell("B7 a caller that can set headers needs no cookie (X-Session-ID of an adult -> 200)",
         o["member_header_only"]["status"] == 200)
    cell("B8 the household admin (family-admin) is admitted too", o["admin_mint"]["status"] == 200)

    print("\n-- who is refused (each is the negative control of the member cells)")
    for who, want, why in (("kid", 403, "child role"), ("minor", 403, "adult role but member_modes says minor"),
                           ("odd", 403, "unknown role"), ("guest", 401, "a guest session")):
        mint, page, api, send = o[f"{who}_mint"], o[f"{who}_page"], o[f"{who}_api"], o[f"{who}_login"]
        cell(f"C {who} ({why}): mint 403 (require_member / adults-only), no cookie; page -> launcher; API and code login -> gate {want}",
             mint["status"] == 403 and not mint["set_cookie"] and page["status"] == 302
             and "/board.html" in page["location"] and api["status"] == want and nx(api)
             and send["status"] == want and nx(send),
             json.dumps({"mint": mint["status"], "page": page["status"], "api": api["status"], "send": send["status"]}))
    cell("C an invalid / expired Zoe session -> gate 401", o["bad_session_api"]["status"] == 401)

    print("\n-- the cookie (the 'wrong client secret' cell of this design)")
    cell("D0 control: a token signed with the lab's REAL key and the right purpose passes", o["forge_real_key"]["status"] == 200)
    cell("D1 signed with the WRONG secret -> 401", o["forge_wrong_key"]["status"] == 401 and nx(o["forge_wrong_key"]))
    cell("D2 the right key but another purpose (the Music Assistant UI cookie's) -> 401",
         o["forge_wrong_purpose"]["status"] == 401)
    cell("D3 expired -> 401", o["forge_expired"]["status"] == 401)


def judge_gatedown(o):
    print("\n-- the gate is down: closed, never open")
    cell("E1 gate unreachable, VALID cookie -> 5xx (closed)", 500 <= o["cookie_api"]["status"] < 600, str(o["cookie_api"]["status"]))
    cell("E2 gate unreachable, anonymous API -> 5xx", 500 <= o["anon_api"]["status"] < 600)
    cell("E3 gate unreachable, anonymous page -> not the board", o["anon_page"]["status"] != 200, str(o["anon_page"]["status"]))


def judge_control(o):
    print("\n-- NEGATIVE CONTROL: the same conf with the three auth_request lines removed")
    cell("N1 anonymous /multica-api/health reaches Multica (200) - so A2 measured the gate",
         o["anon_api_health"]["status"] == 200 and not o["anon_api_health"]["nginx_error_page"])
    cell("N2 anonymous board page is Multica's - so A1 measured the gate",
         o["anon_page"]["status"] == 200 and "/_next/" in o["anon_page"]["body"])
    cell("N3 anonymous send-code reaches Multica (not a gate 401) - so A5 measured the gate",
         o["anon_send_code"]["status"] != 401 and not o["anon_send_code"]["nginx_error_page"],
         str(o["anon_send_code"]["status"]))
    cell("N4 anonymous WebSocket handshake reaches Multica - so A4 measured the gate",
         not o["anon_ws"]["nginx_error_page"], str(o["anon_ws"]))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="msg-lab-"))
    try:
        sh("docker", "network", "create", "--internal", "--label", f"zoe-lab={TAG}", NET)
        run_container("pg", PG_IMAGE, "--tmpfs", "/var/lib/postgresql/data:size=256m", "-e", "POSTGRES_USER=lab",
                      "-e", f"POSTGRES_PASSWORD={PG_PW}", "-e", "POSTGRES_DB=zoe", mem="256m")
        wait("postgres", lambda: (sh("docker", "exec", f"{TAG}-pg", "pg_isready", "-U", "lab", "-d", "zoe",
                                     check=False).returncode == 0, "not ready"))
        time.sleep(2)
        sh("docker", "exec", f"{TAG}-pg", "psql", "-v", "ON_ERROR_STOP=1", "-U", "lab", "-d", "zoe", "-c", "CREATE DATABASE multica;")
        sh("docker", "exec", f"{TAG}-pg", "psql", "-v", "ON_ERROR_STOP=1", "-U", "lab", "-d", "zoe", "-c", SEED)

        run_container("mbackend", BACKEND_IMAGE, aliases=("zoe-multica-backend", "backend"),
                      *["-e", f"DATABASE_URL=postgres://lab:{PG_PW}@pg:5432/multica?sslmode=disable", "-e", "PORT=8080",
                        "-e", "APP_ENV=development", "-e", f"JWT_SECRET={secrets.token_hex(32)}",
                        "-e", f"MULTICA_DEV_VERIFICATION_CODE={DEV_CODE}", "-e", "ALLOW_SIGNUP=false",
                        "-e", f"ALLOWED_EMAILS={EMAIL}", "-e", "FRONTEND_ORIGIN=http://zoe.local",
                        "-e", "CORS_ALLOWED_ORIGINS=http://zoe.local", "-e", "ALLOWED_ORIGINS=http://zoe.local"])
        run_container("mweb", WEB_IMAGE, aliases=("zoe-multica-web",),
                      *["-e", "NEXT_PUBLIC_API_URL=http://zoe.local/multica-api",
                        "-e", "NEXT_PUBLIC_WS_URL=ws://zoe.local/multica-api"])
        run_container("gate", PY_IMAGE, "-v", f"{ZOE_DATA}:/zoe-data:ro", "-v", f"{VENV_SITE}:/venv-site:ro",
                      "-v", f"{HERE}:/lab:ro", "-w", "/zoe-data", "-e", "PYTHONPATH=/venv-site",
                      "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", f"POSTGRES_URL=postgresql://lab:{PG_PW}@pg:5432/zoe",
                      "-e", "ZOE_AUTH_URL=http://127.0.0.1:8002", "-e", f"ZOE_MULTICA_UI_SECRET={GATE_SECRET}",
                      "-e", "ZOE_UNAUTHENTICATED_ROLE=guest", "-e", f"LAB_SESSIONS={json.dumps(SESSIONS)}",
                      mem="384m", cmd=("python3", "/lab/gate_app.py"))
        run_container("driver", PY_IMAGE, "-v", f"{HERE}:/lab:ro", "-e", f"LAB_GATE_SECRET={GATE_SECRET}",
                      "-e", f"LAB_DEV_CODE={DEV_CODE}", "-e", f"LAB_EMAIL={EMAIL}", "-e", "PYTHONDONTWRITEBYTECODE=1",
                      mem="128m", cmd=("sleep", "900"))
        nginx("nginx", conf_dir(tmp / "gated", gated=True))
        nginx("nginx-nogate", conf_dir(tmp / "nogate", gated=False))

        probe = "import urllib.request,sys; urllib.request.urlopen(sys.argv[1], timeout=3)"
        for desc, url in (("gate", "http://gate:8000/lab/ready"), ("multica backend", "http://mbackend:8080/health"),
                          ("multica web", "http://mweb:3000/"), ("nginx", "http://nginx/board.html"),
                          ("control nginx", "http://nginx-nogate/board.html")):
            wait(desc, lambda url=url: (sh("docker", "exec", f"{TAG}-driver", "python3", "-c", probe, url,
                                           check=False).returncode == 0, url))
        print(f"lab {TAG}: images backend={BACKEND_IMAGE.split('@')[-1][:19]} web={WEB_IMAGE.split('@')[-1][:19]}")

        judge_main(driver("main"))
        judge_control(driver("control", nginx="nginx-nogate"))
        sh("docker", "stop", "-t", "2", f"{TAG}-gate")
        judge_gatedown(driver("gatedown"))
    except Exception as exc:  # noqa: BLE001
        print(f"LAB ERROR: {exc}", file=sys.stderr)
        for c in created:
            if c.endswith(("-gate", "-mbackend", "-nginx")):
                logs = subprocess.run(["docker", "logs", "--tail", "25", c], capture_output=True, text=True)
                print(f"--- {c} logs ---\n{(logs.stdout + logs.stderr)[-2500:]}", file=sys.stderr)
        results.append(("lab ran to completion", False, str(exc)))
    finally:
        teardown()
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} cells passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
