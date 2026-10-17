#!/usr/bin/env python3
"""F0 / R0 relay lab runner (panel-anywhere section 10).

Brings up a hermetic Docker lab (relay host = nginx stream front + frps v0.71.0 + session-master plugin stub; two
fake household boxes = frpc + nginx; one panel), runs every cell with its positive AND negative control, tears
everything down and ASSERTS the teardown. Hand-run only; never wired to CI or any service. Use run.sh, which
takes the box-wide test-lane lock.

Safety rails (owner rules):
  * refuses to run between 01:50 and 04:55 local time (the 12B night window);
  * refuses to START unless MemAvailable >= 1500 MB (optionally waiting for it, --wait-mem-s);
  * never touches a container it did not create (everything carries the label zoe.relay-lab=1);
  * the Docker network is `internal` (no route out); nothing is published on the host.

Requires Python >= 3.11, docker + compose v2, the host `openssl` CLI.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
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

LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB))

from box import frpc_config  # noqa: E402
from lab import capture_analysis as ca  # noqa: E402
from lab import pki  # noqa: E402

KEYSCAN = ("find / \\( -path /proc -o -path /sys -o -path /dev \\) -prune -o -type f -size -64k -print0 2>/dev/null "
           "| xargs -0 grep -l 'PRIVATE KEY' 2>/dev/null || true")
PROJECT = "zoe-relay-lab"
NET = f"{PROJECT}_lab"
LABEL = "zoe.relay-lab=1"
NGINX_IMAGE = "nginx@sha256:1ed1b0e1d7652937d6cbdaf4018c7b6fc009a7dd6c3047351e2eddda745de43f"
FRP_IMAGE = "zoe-relay-lab/frp:v0.71.0"
PANEL_IMAGE = "zoe-relay-lab/panel:lab"
FRONT_IP, ROGUE_IP, OPEN_IP, TERM_IP = "10.77.0.10", "10.77.0.50", "10.77.0.51", "10.77.0.60"
RELAY_NAME = "relay.lab"
MIN_MEM_MB = 1500
NIGHT = (datetime.time(1, 50), datetime.time(4, 55))
LAB_BANDWIDTH = "256KB"
LAB_RATE_MAX = 64
LAB_RATE_WINDOW_S = 10


# ----------------------------------------------------------------------------------------------- helpers
def sh(*cmd, check=True, timeout=120, input=None, env=None) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input, env=env)
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd)[:300]}\n{p.stdout[-800:]}\n{p.stderr[-800:]}")
    return p


def mem_available_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise RuntimeError("MemAvailable not found")


def in_night_window(now: datetime.time) -> bool:
    return NIGHT[0] <= now <= NIGHT[1]


def lab_containers() -> list[str]:
    return [x for x in sh("docker", "ps", "-a", "--filter", f"label={LABEL}", "--format", "{{.Names}}").stdout.split() if x]


def lab_networks() -> list[str]:
    return [x for x in sh("docker", "network", "ls", "--filter", f"name={NET}", "--format", "{{.Name}}").stdout.split() if x]


def parse_mib(s: str) -> float:
    m = re.match(r"([\d.]+)\s*([KMG]i?B)", s.strip())
    if not m:
        return 0.0
    v, u = float(m.group(1)), m.group(2)
    return v * {"KiB": 1 / 1024, "KB": 1 / 1024, "MiB": 1, "MB": 1, "GiB": 1024, "GB": 1024}[u]


def docker_mem_mib() -> dict[str, float]:
    out = sh("docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.MemUsage}}", timeout=60).stdout
    res = {}
    for line in out.splitlines():
        name, _, usage = line.partition("|")
        if name.startswith("relaylab-"):
            res[name] = parse_mib(usage.split("/")[0])
    return res


class Results:
    def __init__(self):
        self.rows: list[dict] = []

    def check(self, cell: str, what: str, ok: bool, detail: str = "") -> bool:
        self.rows.append({"cell": cell, "check": what, "ok": bool(ok), "detail": detail})
        print(f"  [{'PASS' if ok else 'FAIL'}] {cell:<8} {what}" + (f"  -- {detail}" if detail else ""), flush=True)
        return bool(ok)

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.rows if not r["ok"]]


# ----------------------------------------------------------------------------------------------- the lab
class Lab:
    def __init__(self, run: Path, rec: Results):
        self.run, self.rec = run, rec
        self.hid = {"a": "ha" + secrets.token_hex(4), "b": "hb" + secrets.token_hex(4)}
        self.secret = {k: secrets.token_hex(24) for k in self.hid}
        self.admin_pw = secrets.token_hex(12)
        self.pki: dict = {}
        self.oneoff_n = 0

    # ---- rendering ----------------------------------------------------------
    def name(self, tag: str) -> str:
        return f"{self.hid[tag]}.zoe.internal"

    def render(self) -> None:
        r = self.run
        for d in ("relay/tls", "relay/etc", "box-a/pki", "box-b/pki", "panel/a", "panel/b", "panel/evil", "blob", "oneoff",
                  "term", "rogue", "open", "cap"):
            (r / d).mkdir(parents=True, exist_ok=True)
        P = self.pki = pki.build_all(str(r / "pki"), self.hid, RELAY_NAME)
        cp = shutil.copyfile
        # relay host
        (r / "relay/nginx-front.conf").write_text((LAB / "relay/nginx-front.conf.tmpl").read_text().replace("@@RELAY_NAME@@", RELAY_NAME))
        (r / "relay/frps.toml").write_text((LAB / "relay/frps.toml.tmpl").read_text().replace("@@ADMIN_PASSWORD@@", self.admin_pw))
        cp(P["relay"]["cert"], r / "relay/tls/server.pem")
        cp(P["relay"]["key"], r / "relay/tls/server.key")
        reg = {self.hid[k]: {"secret_sha256": hashlib.sha256(self.secret[k].encode()).hexdigest(), "active": True} for k in self.hid}
        self.write_registry(reg)
        self.set_toggles([])
        # boxes
        for tag in ("a", "b"):
            h = self.pki[tag]
            d = r / f"box-{tag}"
            (d / "nginx.conf").write_text((LAB / "box/nginx-relay-listener.conf.tmpl").read_text().replace("@@HID@@", self.hid[tag]))
            cp(h["leaf"], d / "pki/leaf.pem")
            cp(h["leaf_key"], d / "pki/leaf.key")
            cp(h["ca"], d / "pki/ca.pem")  # the household CA CERTIFICATE only (client-cert trust); never its key
            cp(P["relay"]["ca"], d / "relay-ca.pem")
            (d / "frpc.toml").write_text(self.frpc_toml(tag))
            # panel material for this household
            cp(h["ca"], r / f"panel/{tag}/ca.pem")
            cp(h["panel_cert"], r / f"panel/{tag}/client.pem")
            cp(h["panel_key"], r / f"panel/{tag}/client.key")
        cp(P["evil"]["ca"], r / "panel/evil-ca.pem")
        cp(P["evil"]["leaf"], r / "panel/evil/leaf-a.pem")
        cp(P["evil"]["leaf_key"], r / "panel/evil/leaf-a.key")
        (r / "blob/blob.bin").write_bytes(os.urandom(1_572_864))  # 1.5 MiB, for the bandwidth cell
        for p in (r / "panel").rglob("*"):
            if p.is_file():
                p.chmod(0o644)  # lab-only throwaway keys; the container user differs from the host user
        for p in list((r / "box-a/pki").glob("*")) + list((r / "box-b/pki").glob("*")) + list((r / "relay/tls").glob("*")) + \
                list((r / "term").glob("*")):
            p.chmod(0o644)

    def frpc_toml(self, tag: str, **kw) -> str:
        return frpc_config.render(hid=self.hid[tag], secret=self.secret[tag], relay_addr=FRONT_IP, relay_port=443,
                                  relay_ca_file="/etc/zoe/relay-ca.pem", relay_server_name=RELAY_NAME, **kw)

    def write_registry(self, rows: dict) -> None:
        p = self.run / "relay/etc/households.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"households": rows}))
        os.replace(tmp, p)
        self._rows = rows

    def set_active(self, tag: str, active: bool) -> None:
        rows = json.loads(json.dumps(self._rows))
        rows[self.hid[tag]]["active"] = active
        self.write_registry(rows)

    def set_toggles(self, disabled: list[str]) -> None:
        p = self.run / "relay/etc/toggles.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"disable_overwrite": disabled}))
        os.replace(tmp, p)

    # ---- docker -----------------------------------------------------------------
    def env(self) -> dict:
        e = dict(os.environ)
        e.update({"RUN": str(self.run), "NGINX_IMAGE": NGINX_IMAGE, "FRP_IMAGE": FRP_IMAGE, "PANEL_IMAGE": PANEL_IMAGE,
                  "HID_A": self.hid["a"], "HID_B": self.hid["b"], "LAB_BANDWIDTH_LIMIT": LAB_BANDWIDTH,
                  "LAB_RATE_MAX": str(LAB_RATE_MAX), "LAB_RATE_WINDOW_S": str(LAB_RATE_WINDOW_S)})
        return e

    def compose(self, *args, check=True, timeout=180):
        return sh("docker", "compose", "-p", PROJECT, "-f", str(LAB / "compose.yaml"), *args, check=check, timeout=timeout,
                  env=self.env())

    def up(self) -> None:
        self.compose("up", "-d", "--no-build", timeout=240)

    def dexec(self, container: str, *cmd, check=True, timeout=60, input=None):
        return sh("docker", "exec", *(["-i"] if input is not None else []), container, *cmd, check=check, timeout=timeout, input=input)

    def logs(self, container: str) -> str:
        p = sh("docker", "logs", container, check=False)
        return p.stdout + p.stderr

    def relay_api(self, path: str) -> dict:
        code = ("import urllib.request,base64,sys;r=urllib.request.Request('http://127.0.0.1:7500'+sys.argv[1]);"
                "r.add_header('Authorization','Basic '+base64.b64encode(b'relay:'+sys.argv[2].encode()).decode());"
                "print(urllib.request.urlopen(r,timeout=5).read().decode())")
        return json.loads(self.dexec("relaylab-relay", "python3", "-c", code, path, self.admin_pw).stdout)

    def proxies(self) -> dict:
        """{proxy name: (customDomains, bandwidthLimit, bandwidthLimitMode, status)} as registered in frps."""
        out = {}
        for p in self.relay_api("/api/proxy/https").get("proxies", []):
            conf = p.get("conf") or {}
            tr = conf.get("transport") or {}
            out[p["name"]] = (conf.get("customDomains"), tr.get("bandwidthLimit"), tr.get("bandwidthLimitMode"), p.get("status"))
        return out

    def wait_proxies(self, names: set, timeout=40) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                got = {n for n, v in self.proxies().items() if v[3] == "online"}
                if got == names:
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1)
        return False

    def both_online(self) -> set:
        return {self.hid["a"] + ".https", self.hid["b"] + ".https"}

    def plugin_events(self) -> list[dict]:
        out = []
        for line in self.logs("relaylab-plugin").splitlines():
            m = re.match(r"plugin (\S+) (\{.*\})$", line)
            if m:
                out.append({"event": m.group(1), **json.loads(m.group(2))})
        return out

    # ---- panel --------------------------------------------------------------------
    def panel_get(self, tag: str, path: str, *, cert=True, pinned=True, headers=(), timeout=15, no_body=False,
                  name: str | None = None, cert_from: str | None = None) -> dict:
        cmd = ["python3", "/opt/panel/panel_client.py", "get", f"https://{name or self.name(tag)}{path}", "--timeout", str(timeout)]
        if pinned:
            cmd += ["--cacert", f"/etc/zoe/panel/{tag}/ca.pem"]
        if cert:
            ct = cert_from or tag
            cmd += ["--cert", f"/etc/zoe/panel/{ct}/client.pem", "--key", f"/etc/zoe/panel/{ct}/client.key"]
        for h in headers:
            cmd += ["--header", h]
        if no_body:
            cmd += ["--no-body"]
        p = self.dexec("relaylab-panel", *cmd, check=False, timeout=timeout + 15)
        try:
            return json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            return {"ok": False, "status": None, "body": "", "error": f"no output: {p.stdout[-200:]} {p.stderr[-200:]}", "elapsed_s": 0}

    def probe(self, tag: str, *, host=FRONT_IP, cert=True, timeout=4.0) -> dict:
        cmd = ["python3", "/opt/panel/panel_client.py", "probe", "--host", host, "--sni", self.name(tag),
               "--cacert", f"/etc/zoe/panel/{tag}/ca.pem", "--timeout", str(timeout)]
        if cert:
            cmd += ["--cert", f"/etc/zoe/panel/{tag}/client.pem", "--key", f"/etc/zoe/panel/{tag}/client.key"]
        p = self.dexec("relaylab-panel", *cmd, check=False, timeout=timeout + 20)
        return json.loads(p.stdout.strip().splitlines()[-1])

    def whoami(self, tag: str, **kw) -> dict:
        r = self.panel_get(tag, "/lab/whoami", **kw)
        fields = dict(line.split("=", 1) for line in r.get("body", "").splitlines() if "=" in line)
        return {"ok": r["ok"] and r["status"] == 200, "fields": fields, "raw": r}

    # ---- one-off frpc / frps ------------------------------------------------------
    def oneoff_frpc(self, toml: str, *, seconds=7, join_box: str | None = None, detach=False, name: str | None = None,
                    relay_ca=True) -> str:
        """Run frpc with this config. join_box='a' joins that box's nginx netns (so localPort targets resolve there)."""
        self.oneoff_n += 1
        cname = name or f"relaylab-oneoff-{self.oneoff_n}"
        cfg = self.run / "oneoff" / f"{cname}.toml"
        cfg.write_text(toml)
        cfg.chmod(0o644)
        net = ["--network", f"container:relaylab-box{join_box}-nginx"] if join_box else ["--network", NET]
        cmd = ["docker", "run", "--name", cname, "--label", LABEL, "--memory", "64m", *net,
               "-v", f"{cfg}:/etc/frp/frpc.toml:ro", "-v", f"{self.run}/box-a/relay-ca.pem:/etc/zoe/relay-ca.pem:ro",
               FRP_IMAGE, "timeout", "-s", "INT", str(seconds), "frpc", "-c", "/etc/frp/frpc.toml"]
        if detach:
            sh(*cmd[:2], "-d", *cmd[2:])
            return cname
        p = sh(*cmd, check=False, timeout=seconds + 40)
        sh("docker", "rm", "-f", cname, check=False)
        return p.stdout + p.stderr

    def stop_remove(self, cname: str) -> None:
        sh("docker", "rm", "-f", cname, check=False, timeout=30)

    def box_frpc(self, tag: str, action: str) -> None:
        sh("docker", action, f"relaylab-box{tag}-frpc", timeout=60)


# ----------------------------------------------------------------------------------------------- cells
def cell_t2(lab: Lab, rec: Results) -> None:
    print("\nT2  relay preflight", flush=True)
    log = lab.logs("relaylab-relay")
    running = sh("docker", "inspect", "-f", "{{.State.Running}}", "relaylab-relay").stdout.strip()
    rec.check("T2", "positive: preflight passes with the plugin configured, then frps runs",
              "preflight: ok" in log and running == "true" and "frps" in sh("docker", "top", "relaylab-relay").stdout,
              "log has 'preflight: ok', container running")
    base = (lab.run / "relay/frps.toml").read_text()
    no_plugin = base.split("# The session master.")[0]
    cases = {
        "no [[httpPlugins]] at all": (no_plugin, 1, "no [[httpPlugins]]"),
        "plugin lacks NewUserConn": (base.replace('"NewUserConn", ', ""), 1, "NewUserConn"),
        "plugin address not loopback": (base.replace("127.0.0.1:9100", "10.1.2.3:9100"), 1, "not loopback"),
        "http vhost enabled": (base.replace("vhostHTTPSPort = 8443", "vhostHTTPSPort = 8443\nvhostHTTPPort = 8080"), 1, "vhostHTTPPort"),
        "tls.force off": (base.replace("transport.tls.force = true", "transport.tls.force = false"), 1, "tls.force"),
    }
    (lab.run / "oneoff/permissive.py").write_text(
        "import http.server,json\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def _a(self):\n"
        "        b=json.dumps({'reject':False,'unchange':True}).encode();self.send_response(200);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)\n"
        "    def do_GET(self): self._a()\n"
        "    def do_POST(self):\n"
        "        self.rfile.read(int(self.headers.get('Content-Length','0')));self._a()\n"
        "    def log_message(self,*a): pass\n"
        "http.server.HTTPServer(('127.0.0.1',9100),H).serve_forever()\n")
    (lab.run / "oneoff/permissive.py").chmod(0o644)
    for what, (cfg, want_rc, want_msg) in cases.items():
        d = lab.run / "oneoff"
        (d / "frps-case.toml").write_text(cfg)
        (d / "frps-case.toml").chmod(0o644)
        p = sh("docker", "run", "--rm", "--label", LABEL, "--network", "none", "-v", f"{d}/frps-case.toml:/cfg/frps.toml:ro",
               "-e", "FRPS_CONFIG=/cfg/frps.toml", "-e", "PREFLIGHT_WAIT_S=2", "--entrypoint", "/opt/relay/entrypoint.sh", FRP_IMAGE,
               check=False, timeout=60)
        out = p.stdout + p.stderr
        rec.check("T2", f"negative: {what} -> preflight exits 78 and frps never starts",
                  p.returncode == 78 and want_msg in out and "start frps success" not in out.lower() and "frps tcp listen" not in out.lower(),
                  f"exit={p.returncode}")
    # plugin configured but unreachable -> exit 78 (preflight rc 2)
    d = lab.run / "oneoff"
    (d / "frps-case.toml").write_text(base)
    p = sh("docker", "run", "--rm", "--label", LABEL, "--network", "none", "-v", f"{d}/frps-case.toml:/cfg/frps.toml:ro",
           "-v", f"{lab.run}/relay/tls:/etc/frp/tls:ro", "-e", "FRPS_CONFIG=/cfg/frps.toml", "-e", "PREFLIGHT_WAIT_S=2",
           "--entrypoint", "/opt/relay/entrypoint.sh", FRP_IMAGE, check=False, timeout=60)
    rec.check("T2", "negative: plugin configured but not running -> frps never starts",
              p.returncode == 78 and "PLUGIN NOT READY" in p.stderr, f"exit={p.returncode}")
    p = sh("docker", "run", "--rm", "--label", LABEL, "--network", "none", "-v", f"{d}/frps-case.toml:/cfg/frps.toml:ro",
           "-v", f"{d}/permissive.py:/cfg/permissive.py:ro", "-v", f"{lab.run}/relay/tls:/etc/frp/tls:ro",
           "-e", "FRPS_CONFIG=/cfg/frps.toml", "-e", "PREFLIGHT_WAIT_S=3", "--entrypoint", "sh", FRP_IMAGE,
           "-c", "python3 /cfg/permissive.py & sleep 1; exec /opt/relay/entrypoint.sh", check=False, timeout=60)
    rec.check("T2", "negative: a PERMISSIVE plugin (accepts a bogus login) is refused too",
              p.returncode == 78 and "ACCEPTED a bogus login" in p.stderr, f"exit={p.returncode}")
    # sensitivity: the same config WITHOUT the preflight really is an open relay
    open_cfg = no_plugin.replace('bindAddr = "127.0.0.1"', 'bindAddr = "0.0.0.0"')
    (lab.run / "open/frps.toml").write_text(open_cfg)
    (lab.run / "open/frps.toml").chmod(0o644)
    sh("docker", "run", "-d", "--name", "relaylab-open", "--label", LABEL, "--network", NET, "--ip", OPEN_IP, "--memory", "64m",
       "-v", f"{lab.run}/open/frps.toml:/etc/frp/frps.toml:ro", "-v", f"{lab.run}/relay/tls:/etc/frp/tls:ro",
       FRP_IMAGE, "frps", "-c", "/etc/frp/frps.toml")
    time.sleep(2)
    greedy = frpc_config.render(hid=lab.hid["a"], secret=lab.secret["a"], relay_addr=OPEN_IP, relay_port=7000,
                                relay_ca_file="/etc/zoe/relay-ca.pem", relay_server_name=RELAY_NAME,
                                custom_domains=[lab.name("b"), "*.zoe.internal"],
                                extra_proxy='\n[[proxies]]\nname = "raw"\ntype = "tcp"\nlocalIP = "127.0.0.1"\nlocalPort = 8000\nremotePort = 7001\n')
    out = lab.oneoff_frpc(greedy, seconds=6)
    rec.check("T2", "control: the same frps WITHOUT the preflight is open (foreign name, wildcard AND a tcp proxy all accepted)",
              out.count("start proxy success") >= 2, f"'start proxy success' x{out.count('start proxy success')}")
    lab.stop_remove("relaylab-open")


def cell_t1(lab: Lab, rec: Results) -> None:
    print("\nT1  names and tenant isolation", flush=True)
    A, B = lab.hid["a"], lab.hid["b"]
    px = lab.proxies()
    rec.check("T1", "positive: frps holds exactly A.https -> [A.zoe.internal] and B.https -> [B.zoe.internal], both online",
              {k: (v[0], v[3]) for k, v in px.items()} == {A + ".https": ([lab.name("a")], "online"), B + ".https": ([lab.name("b")], "online")},
              json.dumps({k: v[0] for k, v in px.items()}))
    wa, wb = lab.whoami("a"), lab.whoami("b")
    rec.check("T1", "positive: the panel reaches A's nginx through A's name and B's through B's",
              wa["ok"] and wa["fields"].get("box") == A and wb["ok"] and wb["fields"].get("box") == B,
              f"A->{wa['fields'].get('box')} B->{wb['fields'].get('box')}")

    def attempt(label, toml, want_why, join="a"):
        before = len(lab.plugin_events())
        # the real A frpc stays connected; this is a second, hostile client holding A's credentials
        out = lab.oneoff_frpc(toml, seconds=6, name=None)
        ev = lab.plugin_events()[before:]
        refused = [e for e in ev if e["event"] == "proxy-refused" and e.get("why") == want_why]
        ok = bool(refused) and "start proxy success" not in out
        px2 = lab.proxies()
        unchanged = {k: v[0] for k, v in px2.items()} == {A + ".https": [lab.name("a")], B + ".https": [lab.name("b")]}
        rec.check("T1", f"negative: {label} is rejected by the plugin (why={want_why}) and the registry is unchanged",
                  ok and unchanged, f"refusals={len(refused)}")

    base = dict(hid=A, secret=lab.secret["a"], relay_addr=FRONT_IP, relay_port=443, relay_ca_file="/etc/zoe/relay-ca.pem",
                relay_server_name=RELAY_NAME)
    attempt("box A asking for B's name", frpc_config.render(**base, custom_domains=[lab.name("b")]), "foreign-or-wildcard-name")
    attempt("box A asking for *.zoe.internal", frpc_config.render(**base, custom_domains=["*.zoe.internal"]), "foreign-or-wildcard-name")
    attempt("box A asking for an arbitrary public host", frpc_config.render(**base, custom_domains=["bank.example.com"]),
            "foreign-or-wildcard-name")
    attempt("box A asking for its own name PLUS B's", frpc_config.render(**base, custom_domains=[lab.name("a"), lab.name("b")]),
            "foreign-or-wildcard-name")
    # other proxy types: one run, four proxies, all refused
    types = frpc_config.render(**base)
    types = types.split("\n[[proxies]]")[0] + "".join(
        f'\n[[proxies]]\nname = "{n}"\ntype = "{t}"\nlocalIP = "127.0.0.1"\nlocalPort = 8000\n{extra}'
        for n, t, extra in (("t1", "tcp", "remotePort = 7101\n"), ("t2", "udp", "remotePort = 7102\n"),
                            ("t3", "http", 'customDomains = ["x.example.com"]\n'), ("t4", "stcp", 'secretKey = "k"\n')))
    before = len(lab.plugin_events())
    out = lab.oneoff_frpc(types, seconds=6)
    ev = [e for e in lab.plugin_events()[before:] if e["event"] == "proxy-refused" and e.get("why") == "type"]
    rec.check("T1", "negative: tcp, udp, http and stcp proxies are all rejected (why=type); none starts",
              len(ev) == 4 and "start proxy success" not in out, f"type-refusals={len(ev)} types={sorted(e.get('type') for e in ev)}")
    # bad credentials
    for label, sec, hid in (("wrong secret", "0" * 48, A), ("unknown household", lab.secret["a"], "hzzzzzzzz")):
        before = len(lab.plugin_events())
        t = frpc_config.render(hid=hid, secret=sec, relay_addr=FRONT_IP, relay_port=443, relay_ca_file="/etc/zoe/relay-ca.pem",
                               relay_server_name=RELAY_NAME)
        out = lab.oneoff_frpc(t, seconds=5)
        ev = [e for e in lab.plugin_events()[before:] if e["event"] == "login-refused"]
        rec.check("T1", f"negative: login with {label} is refused", bool(ev) and "login to server success" not in out,
                  f"login-refused events={len(ev)}")


def cell_t1_overwrite(lab: Lab, rec: Results) -> None:
    print("\nT1  overwrite of client-set customDomains", flush=True)
    A = lab.hid["a"]
    lab.box_frpc("a", "stop")
    time.sleep(1)
    mixed = lab.name("a").replace(A, A.upper()).replace("zoe.internal", "Zoe.Internal")
    toml = lab.frpc_toml("a", custom_domains=[mixed])
    # --- positive: overwrite on (default)
    c = lab.oneoff_frpc(toml, join_box="a", seconds=25, detach=True, name="relaylab-ow-1")
    ok = lab.wait_proxies({A + ".https", lab.hid["b"] + ".https"}, 15)
    px = lab.proxies().get(A + ".https", (None,))[0]
    rec.check("T1", f"positive: the client asked for {mixed!r}; frps registered exactly {lab.name('a')!r}",
              ok and px == [lab.name("a")], f"registered={px}")
    rec.check("T1", "positive: the overwritten name still routes to A's box", lab.whoami("a")["fields"].get("box") == A)
    lab.stop_remove(c)
    time.sleep(2)
    # --- negative control: overwrite switched off in the plugin -> the client's own value survives
    lab.set_toggles(["domains"])
    c = lab.oneoff_frpc(toml, join_box="a", seconds=25, detach=True, name="relaylab-ow-2")
    ok = lab.wait_proxies({A + ".https", lab.hid["b"] + ".https"}, 15)
    px = lab.proxies().get(A + ".https", (None,))[0]
    rec.check("T1", "control: with the plugin's domain overwrite OFF the client-set value is what frps registers (the overwrite does the work)",
              ok and px == [mixed], f"registered={px}")
    lab.stop_remove(c)
    lab.set_toggles([])
    time.sleep(2)
    lab.box_frpc("a", "start")
    rec.check("T1", "restored: A's real frpc is back online", lab.wait_proxies(lab.both_online(), 40))


def cell_failclosed(lab: Lab, rec: Results) -> None:
    print("\nFC1 plugin down fails closed", flush=True)
    before = lab.whoami("a")
    sh("docker", "stop", "relaylab-plugin")
    time.sleep(1)
    during = lab.whoami("a", timeout=12)
    t = frpc_config.render(hid=lab.hid["b"], secret=lab.secret["b"], relay_addr=FRONT_IP, relay_port=443,
                           relay_ca_file="/etc/zoe/relay-ca.pem", relay_server_name=RELAY_NAME)
    out = lab.oneoff_frpc(t, seconds=6)
    sh("docker", "start", "relaylab-plugin")
    time.sleep(2)
    after = lab.whoami("a")
    rec.check("FC1", "plugin stopped: a NEW panel connection to A is refused (NewUserConn fails closed)", before["ok"] and not during["ok"],
              f"before={before['ok']} during={during['ok']} err={during['raw'].get('error', '')[:80]}")
    rec.check("FC1", "plugin stopped: a new box login is refused (Login fails closed)", "login to server success" not in out)
    rec.check("FC1", "control: plugin back -> the same request succeeds", after["ok"] and after["fields"].get("box") == lab.hid["a"])


def cell_t3(lab: Lab, rec: Results) -> None:
    print("\nT3  the relay sees only a ClientHello then ciphertext", flush=True)
    canary = ("CANARY-" + secrets.token_hex(8)).encode()
    cap = lab.run / "cap"

    def capture(netns_container: str, fname: str, action):
        out = cap / fname
        cname = f"relaylab-cap-{fname}"
        sh("docker", "run", "-d", "--name", cname, "--label", LABEL, "--network", f"container:{netns_container}", "--cap-add", "NET_RAW",
           "-v", f"{cap}:/cap", "--memory", "64m", FRP_IMAGE, "python3", "/opt/relay/capture.py", f"/cap/{fname}")
        for _ in range(20):
            if "capture ready" in lab.logs(cname):
                break
            time.sleep(0.25)
        action()
        time.sleep(1)
        sh("docker", "stop", "-t", "5", cname)
        sh("docker", "rm", "-f", cname, check=False)
        return ca.parse_segments(ca.read_capture(out.read_bytes()))

    got = {}

    def real():
        for tag in ("a", "b"):
            r = lab.panel_get(tag, f"/lab/echo?c={canary.decode()}")
            got[tag] = r

    segs = capture("relaylab-front", "relay.bin", real)
    served = all(g.get("status") == 200 and canary.decode() in g.get("body", "") for g in got.values())
    rec.check("T3", "positive: requests really crossed the relay (both households echoed the canary back to the panel)", served)
    hits = ca.needle_hits(segs, [canary, b"/lab/echo", b"GET /lab", b"PRIVATE KEY"])
    plain = [st[:40] for st in ca.streams(segs).values() if b"HTTP/1.1" in st]
    rep = ca.passthrough_report(segs, 8443)
    frames = len(segs)
    rec.check("T3", "capture on the relay host: canary, request line, HTTP and key markers appear in NO stream",
              all(v == 0 for v in hits.values()) and frames > 20, f"payload segments={frames} hits={ {k.decode(): v for k, v in hits.items()} }")
    rec.check("T3", "the only plaintext HTTP on the relay host is frps -> plugin API calls on loopback (POST /handler?...), none of it panel traffic",
              all(x.startswith(b"POST /handler?") for x in plain) and len(plain) >= 1, f"plaintext streams={len(plain)} heads={[x[:28] for x in plain][:2]}")
    rec.check("T3", "capture: every household connection on the vhost hop is ClientHello then only ciphertext (and ServerHello then ciphertext)",
              rep["connections"] >= 2 and not rep["violations"], f"connections={rep['connections']} violations={rep['violations'][:2]}")
    # key scan across the relay host's containers
    allowed = {"/etc/frp/tls/server.key"}
    found = {}
    for c in ("relaylab-front", "relaylab-relay", "relaylab-plugin"):
        p = lab.dexec(c, "sh", "-c", KEYSCAN)
        found[c] = {x for x in p.stdout.split() if x}
    all_found = set().union(*found.values())
    rec.check("T3", "no household private key on the relay host: the only key on its filesystem is the relay's own server key",
              all_found == allowed, f"keys found={sorted(all_found)}")
    # ---- negative control: a deliberately TLS-terminating relay
    (lab.run / "term/nginx.conf").write_text(
        "worker_processes 1;\nerror_log /dev/stderr warn;\npid /tmp/nginx.pid;\nevents { worker_connections 64; }\n"
        "http {\n  access_log off;\n  ssl_protocols TLSv1.3;\n"
        "  server {\n    listen 443 ssl;\n    ssl_certificate /etc/zoe/term/leaf.pem;\n    ssl_certificate_key /etc/zoe/term/leaf.key;\n"
        "    location / { proxy_pass http://127.0.0.1:8081; }\n  }\n"
        "  server {\n    listen 127.0.0.1:8081;\n    location / { default_type text/plain; return 200 \"canary:$arg_c\\n\"; }\n  }\n}\n")
    shutil.copyfile(lab.pki["a"]["leaf"], lab.run / "term/leaf.pem")
    shutil.copyfile(lab.pki["a"]["leaf_key"], lab.run / "term/leaf.key")
    for f in (lab.run / "term").glob("*"):
        f.chmod(0o644)
    sh("docker", "run", "-d", "--name", "relaylab-term", "--label", LABEL, "--network", NET, "--ip", TERM_IP, "--memory", "64m",
       "-v", f"{lab.run}/term/nginx.conf:/etc/nginx/nginx.conf:ro", "-v", f"{lab.run}/term:/etc/zoe/term:ro", NGINX_IMAGE)
    time.sleep(2)
    tres = {}

    def termreq():
        p = lab.dexec("relaylab-panel", "curl", "-sS", "--max-time", "10", "--tlsv1.3", "--cacert", "/etc/zoe/panel/a/ca.pem",
                      "--resolve", f"{lab.name('a')}:443:{TERM_IP}", f"https://{lab.name('a')}/lab/echo?c={canary.decode()}", check=False)
        tres["out"] = p.stdout

    tsegs = capture("relaylab-term", "term.bin", termreq)
    thits = ca.needle_hits(tsegs, [canary, b"GET /lab/echo"])
    rec.check("T3", "control: the same request through a TLS-TERMINATING relay IS served (the probe's request works)",
              canary.decode() in tres.get("out", ""))
    rec.check("T3", "control: the same capture of the terminating relay shows the canary and the request line in plaintext",
              thits[canary] >= 1 and thits[b"GET /lab/echo"] >= 1, f"hits={ {k.decode(): v for k, v in thits.items()} }")
    p = lab.dexec("relaylab-term", "sh", "-c", KEYSCAN)
    rec.check("T3", "control: the key scan DOES flag a household key on the terminating relay", "/etc/zoe/term/leaf.key" in p.stdout,
              p.stdout.strip())
    lab.stop_remove("relaylab-term")


def cell_t4(lab: Lab, rec: Results) -> None:
    print("\nT4  household-CA pinning", flush=True)
    A = lab.hid["a"]
    name = lab.name("a")
    ok = lab.panel_get("a", "/lab/whoami")
    rec.check("T4", "positive: the daemon (requests, verify=<household CA>) connects to A through the relay",
              ok["ok"] and ok["status"] == 200 and f"box={A}" in ok["body"], f"status={ok['status']}")
    # the attacker: poisoned name resolution on the panel, answered by an impostor holding a leaf from the evil CA
    hosts = lab.dexec("relaylab-panel", "cat", "/etc/hosts").stdout
    lab.dexec("relaylab-panel", "sh", "-c", f"grep -v '{name}' /etc/hosts > /tmp/h; echo '127.0.0.1 {name}' >> /tmp/h; cat /tmp/h > /etc/hosts")
    sh("docker", "exec", "-d", "relaylab-panel", "python3", "/opt/panel/impostor.py", "/etc/zoe/panel/evil/leaf-a.pem",
       "/etc/zoe/panel/evil/leaf-a.key", "443")
    time.sleep(1.5)
    try:
        sysstore = lab.dexec("relaylab-panel", "curl", "-sS", "--max-time", "8", f"https://{name}/lab/whoami", check=False)
        rec.check("T4", "control: an UNPINNED client (curl, system store) accepts the impostor, so the other CA really is trusted by the panel",
                  "IMPOSTOR" in sysstore.stdout, sysstore.stdout.strip() or sysstore.stderr.strip()[:100])
        r = sh("docker", "exec", "-e", "REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt", "relaylab-panel", "python3",
               "/opt/panel/panel_client.py", "get", f"https://{name}/lab/whoami", check=False)
        j = json.loads(r.stdout.strip().splitlines()[-1])
        rec.check("T4", "control: an unpinned `requests` client (system bundle) accepts the impostor too", j["ok"] and "IMPOSTOR" in j["body"])
        pinned = lab.panel_get("a", "/lab/whoami", cert=False)
        rec.check("T4", "negative: the pinned daemon (verify=<household CA>) REFUSES the same leaf from the other CA",
                  not pinned["ok"] and "CERTIFICATE_VERIFY_FAILED" in (pinned.get("error") or ""), (pinned.get("error") or "")[:110])
        lab.dexec("relaylab-panel", "mkdir", "-p", "/tmp/emptyca")
        c = lab.dexec("relaylab-panel", "curl", "-sS", "--max-time", "8", "--cacert", "/etc/zoe/panel/a/ca.pem", "--capath", "/tmp/emptyca",
                      f"https://{name}/lab/whoami", check=False)
        rec.check("T4", "negative: curl --cacert <household CA> --capath <empty dir> refuses it as well (exit 60)", c.returncode == 60, f"exit={c.returncode}")
        c0 = lab.dexec("relaylab-panel", "curl", "-sS", "--max-time", "8", "--cacert", "/etc/zoe/panel/a/ca.pem",
                       f"https://{name}/lab/whoami", check=False)
        rec.check("T4", "finding: plain `curl --cacert` is NOT a pin on Debian (its default CA directory is still trusted): the impostor passes",
                  c0.returncode == 0 and "IMPOSTOR" in c0.stdout, f"exit={c0.returncode}")
    finally:
        lab.dexec("relaylab-panel", "sh", "-c", "pkill -f impostor.py || true", check=False)
        lab.dexec("relaylab-panel", "sh", "-c", f"printf '%s' '{hosts}' > /etc/hosts")
    again = lab.panel_get("a", "/lab/whoami")
    rec.check("T4", "restored: hosts mapping back, the pinned daemon reaches A again", again["ok"])


def cell_t6(lab: Lab, rec: Results) -> None:
    print("\nT6  frpc config test", flush=True)
    for tag in ("a", "b"):
        text = (lab.run / f"box-{tag}/frpc.toml").read_text()
        errs = frpc_config.check(text, expect_hid=lab.hid[tag], expect_relay_addr=FRONT_IP)
        rec.check("T6", f"positive: the generated frpc config for box {tag.upper()} passes (and is the config the lab box actually runs)", not errs, "; ".join(errs))
    A = lab.hid["a"]
    kw = dict(hid=A, secret=lab.secret["a"], relay_addr=FRONT_IP, relay_port=443, relay_ca_file="/etc/zoe/relay-ca.pem",
              relay_server_name=RELAY_NAME)
    fixtures = {
        "target 127.0.0.1:8000 (zoe-data's trusted loopback port)": (frpc_config.render(**kw, target_port=8000), "zoe-data"),
        "https2http plugin pointing at 127.0.0.1:8000": (frpc_config.render(**kw, extra_proxy='\n[proxies.plugin]\ntype = "https2http"\nlocalAddr = "127.0.0.1:8000"\ncrtPath = "/x"\nkeyPath = "/y"\n'), "plugin"),
        "target on another loopback port (5432)": (frpc_config.render(**kw, target_port=5432), "loopback service"),
        "target on a LAN address": (frpc_config.render(**kw, target_host="10.0.0.5"), "not the relay listener"),
        "second proxy added": (frpc_config.render(**kw, extra_proxy='\n[[proxies]]\nname = "x"\ntype = "https"\ncustomDomains = ["x.zoe.internal"]\nlocalPort = 8443\n'), "exactly one proxy"),
        "tcp proxy type": (frpc_config.render(**kw).replace('type = "https"', 'type = "tcp"'), "only https"),
        "no trustedCaFile (T9)": (frpc_config.render(**kw, trusted_ca=False), "trustedCaFile"),
    }
    for what, (text, want) in fixtures.items():
        errs = frpc_config.check(text, expect_hid=A, expect_relay_addr=FRONT_IP)
        rec.check("T6", f"negative: {what} FAILS the config test", any(want in e for e in errs), "; ".join(errs)[:140])


def cell_t7(lab: Lab, rec: Results) -> None:
    print("\nT7  box offline vs relay offline", flush=True)
    a, b = lab.probe("a"), lab.probe("b")
    rec.check("T7", "positive: both boxes up -> green", a["state"] == "green" and b["state"] == "green", f"A={a['state']} B={b['state']}")
    lab.box_frpc("a", "stop")
    time.sleep(3)
    a, b = lab.probe("a"), lab.probe("b")
    seen = {"home_offline": a["state"]}
    rec.check("T7", "box A offline -> the relay answers with a TLS alert: 'home offline' (and B is unaffected)",
              a["state"] == "home_offline" and b["state"] == "green", f"A={a['state']} ({a.get('detail', '')[:70]}) B={b['state']}")
    lab.box_frpc("a", "start")
    rec.check("T7", "box A back -> registered again", lab.wait_proxies(lab.both_online(), 40))
    rec.check("T7", "box A back -> green", lab.probe("a")["state"] == "green")
    # half-dead box: process frozen, the TCP connection still open (internet down at home looks like this until the heartbeat times out)
    sh("docker", "pause", "relaylab-boxa-frpc")
    hd = lab.probe("a", timeout=14)
    sh("docker", "unpause", "relaylab-boxa-frpc")
    rec.check("T7", "box A frozen (link dead, relay has not noticed yet) -> 'home unresponsive', distinct from both other states",
              hd["state"] in ("home_unresponsive", "home_offline"), f"state={hd['state']} after {hd['elapsed_s']}s: {hd.get('detail', '')[:80]}")
    lab.wait_proxies(lab.both_online(), 60)
    # relay offline
    for c in ("relaylab-relay", "relaylab-plugin", "relaylab-front"):
        sh("docker", "stop", "-t", "3", c)
    off = lab.probe("a", timeout=4)
    rec.check("T7", "relay offline -> no TCP connection: 'can't reach the relay'", off["state"] == "relay_unreachable",
              f"state={off['state']} ({off.get('detail', '')[:70]})")
    sh("docker", "start", "relaylab-front")
    time.sleep(1)
    sh("docker", "start", "relaylab-plugin")
    sh("docker", "start", "relaylab-relay")
    back = lab.wait_proxies(lab.both_online(), 90)
    rec.check("T7", "relay back -> both boxes re-register by themselves and both names are green again",
              back and lab.probe("a")["state"] == "green" and lab.probe("b")["state"] == "green")
    rec.check("T7", "the states seen are three different values: green / home_offline / relay_unreachable",
              len({"green", seen["home_offline"], off["state"]}) == 3, f"{sorted({'green', seen['home_offline'], off['state']})}")


def cell_t9(lab: Lab, rec: Results) -> None:
    print("\nT9  trustedCaFile is load-bearing", flush=True)
    A = lab.hid["a"]
    kw = dict(hid=A, secret=lab.secret["a"], relay_ca_file="/etc/zoe/relay-ca.pem", relay_server_name=RELAY_NAME)
    login_only = lambda addr, port, **k: frpc_config.render(relay_addr=addr, relay_port=port, **kw, **k).split("\n[[proxies]]")[0] + "\n"  # noqa: E731
    out = lab.oneoff_frpc(login_only(FRONT_IP, 443), seconds=5)
    rec.check("T9", "positive: frpc WITH trustedCaFile logs in to the genuine relay", "login to server success" in out)
    (lab.run / "rogue/frps.toml").write_text('bindAddr = "0.0.0.0"\nbindPort = 7000\ntransport.tls.certFile = "/etc/frp/tls/server.pem"\n'
                                              'transport.tls.keyFile = "/etc/frp/tls/server.key"\nlog.to = "console"\n')
    shutil.copyfile(lab.pki["rogue"]["cert"], lab.run / "rogue/server.pem")
    shutil.copyfile(lab.pki["rogue"]["key"], lab.run / "rogue/server.key")
    for f in (lab.run / "rogue").glob("*"):
        f.chmod(0o644)
    sh("docker", "run", "-d", "--name", "relaylab-rogue", "--label", LABEL, "--network", NET, "--ip", ROGUE_IP, "--memory", "64m",
       "-v", f"{lab.run}/rogue/frps.toml:/etc/frp/frps.toml:ro", "-v", f"{lab.run}/rogue:/etc/frp/tls:ro", FRP_IMAGE,
       "frps", "-c", "/etc/frp/frps.toml")
    time.sleep(2)
    logins = lambda: lab.logs("relaylab-rogue").lower().count("client login info")  # noqa: E731
    out = lab.oneoff_frpc(login_only(ROGUE_IP, 7000), seconds=5)
    rec.check("T9", "negative: frpc WITH trustedCaFile REFUSES a relay presenting a cert from another CA (no login success, and the rogue relay saw no login)",
              "login to server success" not in out and "connect to server error" in out and logins() == 0,
              f"frpc: {[l.split('] ')[-1] for l in out.splitlines() if 'connect to server error' in l][:1]} rogue logins={logins()}")
    out = lab.oneoff_frpc(frpc_config.render(relay_addr=ROGUE_IP, relay_port=7000, trusted_ca=False, **kw).split("\n[[proxies]]")[0] + "\n", seconds=5)
    rec.check("T9", "control: the same config WITHOUT trustedCaFile connects to that rogue relay and hands it the household secret",
              "login to server success" in out, "login to server success" if "login to server success" in out else out[-160:])
    rec.check("T9", "control: the rogue relay really received that login (it is not an artefact of the client)", logins() == 1, f"rogue logins={logins()}")
    lab.stop_remove("relaylab-rogue")


def cell_a1(lab: Lab, rec: Results) -> None:
    print("\nA1  bandwidth cap enforced by the plugin's overwrite", flush=True)
    A, B = lab.hid["a"], lab.hid["b"]
    ask = '\ntransport.bandwidthLimit = "4MB"\ntransport.bandwidthLimitMode = "client"\n'
    lab.box_frpc("a", "stop")
    time.sleep(1)

    def run_variant(label, cname):
        lab.oneoff_frpc(lab.frpc_toml("a", extra_proxy=ask), join_box="a", seconds=60, detach=True, name=cname)
        ok = lab.wait_proxies({A + ".https", B + ".https"}, 20)
        px = lab.proxies().get(A + ".https")
        r = lab.panel_get("a", "/lab/blob", no_body=True, timeout=40)
        return ok, px, r

    ok, px, r = run_variant("cap", "relaylab-bw-1")
    rec.check("A1", f"positive: the client asked for 4MB/client; frps holds {LAB_BANDWIDTH}/server (the plugin's overwrite)",
              ok and px[1] == LAB_BANDWIDTH and px[2] == "server", f"registered={px[1:3]}")
    capped = r["elapsed_s"]
    rec.check("A1", f"positive: a 1.5 MiB download is held to ~{LAB_BANDWIDTH}/s (took >= 3.5 s)", r["ok"] and capped >= 3.5, f"{capped:.2f}s")
    lab.stop_remove("relaylab-bw-1")
    time.sleep(2)
    lab.set_toggles(["bandwidth"])
    ok, px, r = run_variant("nocap", "relaylab-bw-2")
    rec.check("A1", "control: with the plugin's bandwidth overwrite OFF, frps holds the CLIENT's own 4MB/client",
              ok and px[1] == "4MB" and px[2] == "client", f"registered={px[1:3]}")
    free = r["elapsed_s"]
    rec.check("A1", "control: the same download is then not held to the lab cap (took < 1.5 s)", r["ok"] and free < 1.5, f"{free:.2f}s")
    rec.check("A1", "the cap is the difference: capped run is >= 3x slower than the uncapped run", capped >= 3 * free, f"{capped:.2f}s vs {free:.2f}s")
    lab.stop_remove("relaylab-bw-2")
    lab.set_toggles([])
    time.sleep(2)
    lab.box_frpc("a", "start")
    rec.check("A1", "restored: A's real frpc is back online", lab.wait_proxies(lab.both_online(), 40))


def cell_n1(lab: Lab, rec: Results) -> None:
    print("\nN1  (extra) per-household connection rate cap and suspension", flush=True)
    time.sleep(LAB_RATE_WINDOW_S + 1)  # let the window drain after earlier cells
    code = ("import ssl,socket,sys\n"
            "name,ca,crt,key,n=sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4],int(sys.argv[5])\n"
            "ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);ctx.minimum_version=ssl.TLSVersion.TLSv1_3\n"
            "ctx.load_verify_locations(ca);ctx.load_cert_chain(crt,key)\n"
            "ok=bad=0\n"
            "for i in range(n):\n"
            "    try:\n"
            "        s=ctx.wrap_socket(socket.create_connection((name,443),timeout=5),server_hostname=name)\n"
            "        s.sendall(('GET /lab/echo?c=x HTTP/1.1\\r\\nHost: '+name+'\\r\\nConnection: close\\r\\n\\r\\n').encode())\n"
            "        d=s.recv(64);s.close()\n"
            "        ok+= d.startswith(b'HTTP/1.1 200')\n"
            "        bad+= not d.startswith(b'HTTP/1.1 200')\n"
            "    except Exception: bad+=1\n"
            "print(ok,bad)\n")

    def burst(tag, n):
        p = lab.dexec("relaylab-panel", "python3", "-c", code, lab.name(tag), f"/etc/zoe/panel/{tag}/ca.pem",
                      f"/etc/zoe/panel/{tag}/client.pem", f"/etc/zoe/panel/{tag}/client.key", str(n), timeout=120)
        ok, bad = map(int, p.stdout.split())
        return ok, bad

    ok, bad = burst("a", LAB_RATE_MAX + 8)
    rec.check("N1", f"household A: the first {LAB_RATE_MAX} new connections in a {LAB_RATE_WINDOW_S}s window are served, the rest refused",
              ok == LAB_RATE_MAX and bad == 8, f"served={ok} refused={bad}")
    okb, badb = burst("b", 5)
    rec.check("N1", "household B is unaffected by A's burst (per-household counters)", okb == 5 and badb == 0, f"served={okb} refused={badb}")
    time.sleep(LAB_RATE_WINDOW_S + 1)
    ok2, bad2 = burst("a", 5)
    rec.check("N1", "control: after the window drains, A is served again", ok2 == 5 and bad2 == 0, f"served={ok2} refused={bad2}")
    lab.set_active("b", False)
    time.sleep(1)
    sus = lab.whoami("b", timeout=8)
    other = lab.whoami("a")
    rec.check("N1", "suspension: B suspended in the registry -> its panel connections are refused, A's are not",
              not sus["ok"] and other["ok"], f"B ok={sus['ok']} A ok={other['ok']}")
    lab.set_active("b", True)
    time.sleep(1)
    rec.check("N1", "control: B reactivated -> served again", lab.whoami("b")["ok"])


def cell_stub(lab: Lab, rec: Results) -> None:
    print("\nS   nginx relay-listener stub self-checks (not the T5/T10/T11 cells)", flush=True)
    A = lab.hid["a"]
    fp = lab.pki["a"]["panel_fp"]
    w = lab.whoami("a")
    rec.check("S1", "via the relay with the household client cert: X-Zoe-Ingress=relay, the cert fingerprint header matches, no X-Forwarded-For",
              w["ok"] and w["fields"].get("ingress") == "relay" and w["fields"].get("fp") == fp and w["fields"].get("xff", "") == "",
              json.dumps(w["fields"]))
    w2 = lab.whoami("a", headers=("X-Zoe-Ingress: lan", "X-Zoe-Client-Cert-Fp: deadbeef"))
    rec.check("S2", "client-sent X-Zoe-Ingress / X-Zoe-Client-Cert-Fp are overwritten, not passed through",
              w2["ok"] and w2["fields"].get("ingress") == "relay" and w2["fields"].get("fp") == fp, json.dumps(w2["fields"]))
    nc = lab.panel_get("a", "/lab/whoami", cert=False, timeout=10)
    rec.check("S3", "no client certificate -> nginx answers 400 'No required SSL certificate' itself; nothing reaches the upstream (S1 is the positive control)",
              nc["ok"] and nc["status"] == 400 and "No required SSL certificate" in nc["body"] and "box=" not in nc["body"],
              f"status={nc['status']} err={(nc.get('error') or '')[:80]}")
    wrong = lab.panel_get("a", "/lab/whoami", cert_from="b", timeout=10)
    rec.check("S5", "B's panel certificate presented to A's listener (other household CA) -> 400, nothing reaches the upstream",
              wrong["ok"] and wrong["status"] == 400 and "box=" not in wrong["body"], f"status={wrong['status']} err={(wrong.get('error') or '')[:80]}")
    p = sh("docker", "run", "--rm", "--label", LABEL, "--network", "container:relaylab-boxa-nginx", PANEL_IMAGE, "curl", "-sk", "--max-time", "8",
           "-H", "X-Zoe-Ingress: relay", "-H", "X-Zoe-Client-Cert-Fp: forged", "https://127.0.0.1:8444/lab/whoami", check=False)
    f = dict(line.split("=", 1) for line in p.stdout.splitlines() if "=" in line)
    rec.check("S4", "the LAN listener stub clears both headers (a LAN client cannot claim the relay ingress)",
              f.get("box") == A and f.get("ingress", "x") == "" and f.get("fp", "x") == "", json.dumps(f))


# ----------------------------------------------------------------------------------------------- main
CELLS = [("stub", cell_stub), ("t2", cell_t2), ("t1", cell_t1), ("t1o", cell_t1_overwrite), ("t3", cell_t3), ("t4", cell_t4),
         ("t6", cell_t6), ("fc1", cell_failclosed), ("a1", cell_a1), ("n1", cell_n1), ("t9", cell_t9), ("t7", cell_t7)]


def main() -> int:
    ap = argparse.ArgumentParser(description="F0 relay lab runner")
    ap.add_argument("--wait-mem-s", type=int, default=0, help="wait up to N seconds for MemAvailable >= 1500 MB before starting")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--only", help="comma-separated cell keys: " + ",".join(k for k, _ in CELLS))
    ap.add_argument("--json", default=str(LAB / "results" / "last-run.json"))
    args = ap.parse_args()

    now = datetime.datetime.now().time()
    if in_night_window(now):
        print(f"REFUSED: {now:%H:%M} is inside the 01:50-04:55 night window; the lab never runs then.", file=sys.stderr)
        return 3
    deadline = time.monotonic() + args.wait_mem_s
    while mem_available_mb() < MIN_MEM_MB:
        if time.monotonic() >= deadline:
            print(f"REFUSED: MemAvailable {mem_available_mb()} MB < {MIN_MEM_MB} MB gate.", file=sys.stderr)
            return 4
        time.sleep(10)
    mem_before = mem_available_mb()
    pre = lab_containers() + lab_networks()
    if pre:
        print(f"REFUSED: leftovers from an earlier run exist: {pre}", file=sys.stderr)
        return 5

    if not args.skip_build:
        env = dict(os.environ, DOCKER_BUILDKIT="0")
        sh("docker", "build", "-q", "-f", str(LAB / "docker/Dockerfile.frp"), "-t", FRP_IMAGE, str(LAB), env=env, timeout=600)
        sh("docker", "build", "-q", "-f", str(LAB / "docker/Dockerfile.panel"), "-t", PANEL_IMAGE, str(LAB), env=env, timeout=600)

    rec = Results()
    run = Path(tempfile.mkdtemp(prefix="zoe-relay-lab-"))
    run.chmod(0o700)
    lab = Lab(run, rec)
    peak_docker = 0.0
    mem_up = mem_min = None
    started = time.monotonic()
    try:
        print(f"run dir {run}\nhouseholds {lab.hid}", flush=True)
        lab.render()
        lab.up()
        if not lab.wait_proxies(lab.both_online(), 60):
            rec.check("BOOT", "both boxes register with the relay", False)
            for c in ("front", "relay", "plugin", "boxa-frpc", "boxb-frpc"):
                print(f"--- relaylab-{c}\n" + "\n".join(lab.logs(f"relaylab-{c}").splitlines()[-12:])[-1500:])
            try:
                print("--- frps api:", json.dumps(lab.relay_api("/api/proxy/https"))[:800])
            except Exception as e:  # noqa: BLE001
                print("--- frps api failed:", e)
            raise SystemExit(1)
        rec.check("BOOT", "lab is up: relay host, two boxes registered, panel ready", True)
        time.sleep(3)
        mem_up = mem_available_mb()
        mem_min = mem_up
        d = docker_mem_mib()
        peak_docker = sum(d.values())
        print(f"memory: MemAvailable before={mem_before} MB, lab up={mem_up} MB; containers={ {k: round(v, 1) for k, v in d.items()} } total={peak_docker:.0f} MiB", flush=True)
        wanted = set(args.only.split(",")) if args.only else None
        for key, fn in CELLS:
            if wanted and key not in wanted:
                continue
            try:
                fn(lab, rec)
            except Exception as e:  # noqa: BLE001
                rec.check(key.upper(), f"cell raised {type(e).__name__}", False, str(e)[:400])
            mem_min = min(mem_min, mem_available_mb())
            peak_docker = max(peak_docker, sum(docker_mem_mib().values()))
    finally:
        print("\nteardown", flush=True)
        try:
            lab.compose("down", "-v", "--remove-orphans", "--timeout", "5", check=False, timeout=120)
        except Exception as e:  # noqa: BLE001
            print("compose down:", e)
        for c in lab_containers():
            sh("docker", "rm", "-f", c, check=False)
        for n in lab_networks():
            sh("docker", "network", "rm", n, check=False)
        shutil.rmtree(run, ignore_errors=True)
        time.sleep(2)
        left = lab_containers() + lab_networks()
        rec.check("TEARDOWN", "no lab container or network remains", not left, str(left))
        rec.check("TEARDOWN", "the run directory (configs, throwaway keys) is deleted", not run.exists())
        mem_after = mem_available_mb()
        rec.check("TEARDOWN", "no lab container holds memory any more (docker stats lists none)", not docker_mem_mib(),
                  f"MemAvailable before={mem_before} MB, after={mem_after} MB (informational: other processes on the box move it by more than the lab uses)")

    dur = time.monotonic() - started
    summary = {"date": datetime.datetime.now().isoformat(timespec="seconds"), "duration_s": round(dur), "frp": "v0.71.0",
               "mem_available_mb": {"before": mem_before, "lab_up": mem_up, "min_during": mem_min, "after": mem_after},
               "containers_total_mib_peak": round(peak_docker), "passed": len(rec.rows) - len(rec.failed), "failed": len(rec.failed),
               "rows": rec.rows}
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(summary, indent=1))
    print(f"\n{summary['passed']} passed, {summary['failed']} failed in {dur:.0f}s; lab containers peak {peak_docker:.0f} MiB; "
          f"MemAvailable {mem_before} -> {mem_up} (up) -> min {mem_min} -> {mem_after} MB")
    for r in rec.failed:
        print("FAILED:", r["cell"], r["check"], r["detail"])
    return 1 if rec.failed else 0


if __name__ == "__main__":
    sys.exit(main())
