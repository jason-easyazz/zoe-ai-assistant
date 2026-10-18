#!/usr/bin/env python3
"""Lab stand-in for a Zoe service (zoe-data / flue-zoe-brain-2x / llama-server) under its P0 drop-in.

It does the job a real service needs (read its state + its own credential, write its state dir, listen on its
port) and answers GET /probe with what it can and cannot do FROM INSIDE ITS OWN SANDBOX: sudo, the raw Docker
socket, the allowlisted proxy, the other services' secrets, the hidden real /home, the read-only checkout.
Stdlib only. Lab-only: never imported or run by anything in production.
"""
import http.server
import json
import os
import socket
import subprocess
import sys

ROLE, PORT = "", 0  # set in __main__, so cells.py can import unix_http without side effects
SECRETS = {"zoe-data": "/etc/zoe/env/zoe-data.env", "flue-zoe-brain-2x": "/etc/zoe/env/flue-zoe-brain-2x.env"}
PROXY = "/run/zoe-docker-proxy/docker.sock"


def unix_http(path, method, url):
    """One HTTP/1.0 request over a unix socket; returns the status code, or the errno name on connect failure."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect(path)
        s.sendall(f"{method} {url} HTTP/1.0\r\nHost: docker\r\nContent-Length: 0\r\n\r\n".encode())
        return int(s.recv(4096).split(b" ", 2)[1])
    except OSError as e:
        return type(e).__name__
    finally:
        s.close()


def can_read(path):
    try:
        with open(path) as f:
            f.read(1)
        return True
    except OSError as e:
        return type(e).__name__


def can_write(path):
    try:
        with open(path, "w") as f:
            f.write("x")
        os.unlink(path)
        return True
    except OSError as e:
        return type(e).__name__


def probe():
    sudo = subprocess.run(["sudo", "-n", "true"], capture_output=True, text=True)
    state_dir = {"zoe-data": "/home/zoe/.zoe", "flue-zoe-brain-2x": "/home/zoe/assistant/labs/flue-zoe-brain-2x/data",
                 "llama-server": None}[ROLE]
    job_input = {"zoe-data": "/home/zoe/.zoe/state.json", "llama-server": "/home/zoe/models/lab.gguf",
                 "flue-zoe-brain-2x": "/home/zoe/assistant/labs/flue-zoe-brain-2x/dist/server.mjs"}[ROLE]
    return {
        "job_input": can_read(job_input),
        "role": ROLE, "uid": os.getuid(), "user": os.environ.get("USER") or str(os.getuid()),
        "groups": sorted(os.getgroups()), "home": os.environ.get("HOME"),
        "own_secret": os.environ.get("P0LAB_SECRET", "") != "",
        "sudo_rc": sudo.returncode,
        "docker_sock": unix_http("/var/run/docker.sock", "GET", "/v1.47/containers/json"),
        "proxy_ma_start": unix_http(PROXY, "POST", "/v1.47/containers/zoe-music-assistant/start"),
        "proxy_create": unix_http(PROXY, "POST", "/v1.47/containers/create"),
        "proxy_ha_exec": unix_http(PROXY, "POST", "/v1.47/containers/homeassistant/exec"),
        "other_secrets": {k: can_read(v) for k, v in SECRETS.items() if k != ROLE},
        "real_home_ssh": can_read("/home/zoe/.ssh/id_lab"),
        "checkout_read": can_read("/home/zoe/assistant/README.md"),
        "checkout_write": can_write("/home/zoe/assistant/p0lab-write-test"),
        "state_write": can_write(os.path.join(state_dir, "p0lab-write-test")) if state_dir else None,
        "etc_write": can_write("/etc/p0lab-write-test"),
    }


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(probe() if self.path == "/probe" else {"ok": True, "role": ROLE}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ROLE, PORT = sys.argv[1], int(sys.argv[2])
    print(f"{ROLE}: up on 127.0.0.1:{PORT} uid={os.getuid()}", flush=True)
    http.server.HTTPServer(("127.0.0.1", PORT), H).serve_forever()
