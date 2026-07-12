#!/usr/bin/env python3
"""
Zoe Touch Panel — local provisioning HTTP server.

Runs on the Pi during first boot in two modes:

  --mode wifi-setup  (port 80 on 192.168.4.1)
      Serves the WiFi captive portal. Accepts SSID/password,
      connects to home WiFi via NetworkManager (nmcli), saves
      server_url to config.json.

  --mode provision   (port 8888 on localhost)
      Serves provision.html + qrcode.min.js.
      Proxies API calls to the Zoe server (ssl.CERT_NONE for self-signed).
      Saves token + panel_id to config.json on completion.

Pure Python stdlib — no pip dependencies.
"""

import argparse
import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import pathlib
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("provision-server")

CONFIG_PATH = pathlib.Path("/opt/TouchKio/config.json")
WIFI_DONE   = pathlib.Path("/opt/TouchKio/.setup_wifi_done")
PROVISIONED = pathlib.Path("/opt/TouchKio/.provisioned")
STATIC_DIR  = pathlib.Path("/opt/TouchKio")

# SSL context that accepts self-signed certs (Zoe uses a self-signed cert on LAN)
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def _read_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _write_config(updates: dict) -> None:
    cfg = _read_config()
    cfg.update(updates)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def _get_mac(iface: str = "wlan0") -> str:
    try:
        return pathlib.Path(f"/sys/class/net/{iface}/address").read_text().strip()
    except Exception:
        return "00:00:00:00:00:00"


def _get_local_subnet(iface: str = "wlan0") -> str | None:
    """Return the /24 subnet of the connected interface, e.g. '192.168.1.0/24'."""
    try:
        result = subprocess.run(
            ["ip", "-4", "addr", "show", iface],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                cidr = line.split()[1]  # e.g. 192.168.1.100/24
                network = ipaddress.ip_interface(cidr).network
                return str(network)
    except Exception:
        pass
    return None


async def _probe_host(host: str, port: int, path: str, timeout: float) -> str | None:
    """Return host URL if /health returns {"service":"zoe-data"}, else None."""
    scheme = "https" if port == 443 else "http"
    url = f"{scheme}://{host}:{port}{path}"
    try:
        ctx = _ssl_ctx if port == 443 else None
        loop = asyncio.get_event_loop()

        def _do_request():
            req = urllib.request.Request(url, headers={"User-Agent": "ZoeProvisioner/1"})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                body = json.loads(resp.read(512))
                return body.get("service") == "zoe-data"

        ok = await loop.run_in_executor(None, _do_request)
        if ok:
            return f"{scheme}://{host}"
    except Exception:
        pass
    return None


async def _discover_zoe_server() -> str | None:
    """
    Discover Zoe server on the LAN.
    Strategy: parallel scan of the local /24 subnet on ports 443 and 80,
    checking /health for {"service":"zoe-data"}.
    Falls back to None (caller shows manual URL input).
    """
    tasks = []
    for iface in ("wlan0", "eth0"):
        subnet = _get_local_subnet(iface)
        if subnet:
            log.info("Scanning subnet %s for Zoe...", subnet)
            network = ipaddress.ip_network(subnet, strict=False)
            for host in network.hosts():
                ip = str(host)
                for port in (443, 80):
                    tasks.append(_probe_host(ip, port, "/health", timeout=1.5))
            break  # only scan first found interface

    if not tasks:
        log.warning("No suitable interface found for subnet scan")
        return None

    # Run all probes concurrently with a 20s hard timeout
    results = await asyncio.wait_for(
        asyncio.gather(*tasks, return_exceptions=True),
        timeout=20.0,
    )
    for r in results:
        if isinstance(r, str) and r:
            log.info("Found Zoe server at %s", r)
            return r
    return None


# ──────────────────────────────────────────────────────────────────────────────
# WiFi-setup mode
# ──────────────────────────────────────────────────────────────────────────────

PORTAL_HTML = STATIC_DIR / "wifi-portal" / "portal.html"


class WiFiSetupHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info("WiFiPortal: " + fmt % args)

    def _send_json(self, obj: dict, status: int = 200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_file(PORTAL_HTML, "text/html")
        elif path == "/scan":
            self._scan_wifi()
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/connect":
            self._connect_wifi()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _serve_file(self, path: pathlib.Path, ctype: str):
        try:
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404, f"File not found: {path}")

    def _scan_wifi(self):
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
                capture_output=True, text=True, timeout=15
            )
            networks = []
            seen = set()
            for line in result.stdout.splitlines():
                parts = line.split(":")
                if len(parts) >= 2:
                    ssid = parts[0].strip()
                    if ssid and ssid not in seen:
                        seen.add(ssid)
                        signal = parts[1] if len(parts) > 1 else "0"
                        security = parts[2] if len(parts) > 2 else ""
                        networks.append({"ssid": ssid, "signal": signal, "security": security})
            networks.sort(key=lambda n: -int(n["signal"] or 0))
            self._send_json({"networks": networks[:20]})
        except Exception as exc:
            self._send_json({"error": str(exc), "networks": []})

    def _connect_wifi(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        ssid = body.get("ssid", "").strip()
        password = body.get("password", "").strip()
        zoe_url = body.get("zoe_url", "").strip()

        if not ssid:
            self._send_json({"ok": False, "error": "ssid is required"}, 400)
            return

        log.info("Connecting to WiFi SSID: %s", ssid)
        try:
            cmd = ["nmcli", "dev", "wifi", "connect", ssid]
            if password:
                cmd += ["password", password]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip())

            # Save server_url to config
            if zoe_url:
                _write_config({"server_url": zoe_url})
            WIFI_DONE.touch()
            log.info("WiFi connected. .setup_wifi_done touched.")
            self._send_json({"ok": True})
        except Exception as exc:
            log.error("WiFi connect failed: %s", exc)
            self._send_json({"ok": False, "error": str(exc)}, 500)


# ──────────────────────────────────────────────────────────────────────────────
# Provision mode
# ──────────────────────────────────────────────────────────────────────────────

PROVISION_HTML = STATIC_DIR / "provision.html"
QRCODE_JS      = STATIC_DIR / "qrcode.min.js"


class ProvisionHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info("Provision: " + fmt % args)

    def _send_json(self, obj: dict, status: int = 200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: pathlib.Path, ctype: str):
        try:
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404, f"File not found: {path}")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_file(PROVISION_HTML, "text/html")
        elif path == "/static/qrcode.min.js":
            self._serve_file(QRCODE_JS, "application/javascript")
        elif path == "/config":
            self._get_config()
        elif path.startswith("/proxy/provision/") and not path.endswith("/request"):
            # /proxy/provision/{code} — poll status
            code = path.split("/")[-1]
            self._proxy_get(f"/api/panels/provision/{code}")
        elif path == "/discover":
            self._discover()
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(length) if length else b"{}"
        if path == "/proxy/provision/request":
            self._proxy_post("/api/panels/provision/request", body_bytes)
        elif path == "/save-token":
            self._save_token(json.loads(body_bytes))
        else:
            self.send_error(404)

    def _get_config(self):
        cfg = _read_config()
        mac = _get_mac("wlan0")
        self._send_json({
            "server_url": cfg.get("server_url", ""),
            "device_id": mac,
            "hostname": socket.gethostname(),
        })

    def _discover(self):
        """Synchronously run the async subnet scan and return found URL."""
        try:
            loop = asyncio.new_event_loop()
            url = loop.run_until_complete(_discover_zoe_server())
            loop.close()
            if url:
                _write_config({"server_url": url})
                self._send_json({"found": True, "server_url": url})
            else:
                self._send_json({"found": False})
        except Exception as exc:
            self._send_json({"found": False, "error": str(exc)})

    def _proxy_get(self, api_path: str):
        cfg = _read_config()
        server_url = cfg.get("server_url", "")
        if not server_url:
            self._send_json({"error": "server_url not set"}, 503)
            return
        url = server_url.rstrip("/") + api_path
        try:
            req = urllib.request.Request(url)
            ctx = _ssl_ctx if url.startswith("https") else None
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                body = resp.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 502)

    def _proxy_post(self, api_path: str, body_bytes: bytes):
        cfg = _read_config()
        server_url = cfg.get("server_url", "")
        if not server_url:
            self._send_json({"error": "server_url not set"}, 503)
            return
        url = server_url.rstrip("/") + api_path
        try:
            req = urllib.request.Request(
                url,
                data=body_bytes,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            ctx = _ssl_ctx if url.startswith("https") else None
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                body = resp.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 502)

    def _save_token(self, data: dict):
        token    = data.get("token", "")
        panel_id = data.get("panel_id", "")
        if not token:
            self._send_json({"ok": False, "error": "token is required"}, 400)
            return
        try:
            cfg = _read_config()
            cfg["token"] = token
            if panel_id:
                cfg["panel_id"] = panel_id
            # Rebuild kiosk URL from server_url + panel_id
            server_url = cfg.get("server_url", "")
            pid = panel_id or cfg.get("panel_id", "zoe-panel")
            if server_url:
                cfg["url"] = f"{server_url}/touch/home.html?panel_id={pid}&kiosk=1"
            CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
            PROVISIONED.touch()
            log.info("Token saved. .provisioned touched. Panel: %s", pid)
            self._send_json({"ok": True})
        except Exception as exc:
            log.error("save-token failed: %s", exc)
            self._send_json({"ok": False, "error": str(exc)}, 500)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["wifi-setup", "provision"], required=True)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    if args.mode == "wifi-setup":
        host = args.host or "192.168.4.1"
        port = args.port or 80
        handler = WiFiSetupHandler
        log.info("Starting WiFi setup portal on %s:%d", host, port)
    else:
        host = args.host or "127.0.0.1"
        port = args.port or 8888
        handler = ProvisionHandler
        log.info("Starting provision server on %s:%d", host, port)

    server = HTTPServer((host, port), handler)
    log.info("Ready.")
    server.serve_forever()


if __name__ == "__main__":
    main()
