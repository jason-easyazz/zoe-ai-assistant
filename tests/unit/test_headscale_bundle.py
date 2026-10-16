"""Static guards for the Headscale remote-access bundle.

The bundle (scripts/setup/remote-access/headscale/) is deployed BY HAND on a
public server, so nothing at runtime will catch a drifted pin, a widened bind,
or a leaked address. These tests pin the properties the runbook and the lab
(lab_test.sh) depend on. They are text/structure checks only: the behavioural
proof is the lab, which needs Docker and ~1.5 GB free and is run by hand.

Negative controls (each turned this file red, then reverted):
  - image tag without the @sha256 digest / floating `latest`
  - the HTTP publish changed from 127.0.0.1:18080 to 0.0.0.0:18080
  - a second auto-approved route, or the /32 widened to a /24
  - `derp.urls` given Tailscale's public map, `magic_dns: true`
  - a panel rule that adds a port beyond 443/8007
  - `http2 on;` or the bare `$connection_upgrade` name in the nginx file
  - a public-looking IPv4, a hosting-provider/region/co-tenancy detail / a hskey- or tskey- token in any bundle file
"""

from __future__ import annotations

import ipaddress
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml  # installed explicitly by validate.yml; a missing dep must redden the lane

pytestmark = pytest.mark.ci_safe

BUNDLE = Path(__file__).resolve().parents[2] / "scripts/setup/remote-access/headscale"
COMPOSE = BUNDLE / "docker-compose.yml"
CONFIG = BUNDLE / "config/config.yaml"
ACL = BUNDLE / "config/acl.hujson"
NGINX = BUNDLE / "nginx-hs.the411.life.conf"
NGINX_ACME = BUNDLE / "nginx-hs.the411.life.acme.conf"
README = BUNDLE / "README.md"
LAB = BUNDLE / "lab_test.sh"
LAB_COMPOSE = BUNDLE / "lab/docker-compose.lab.yml"

PRIVATE_ALLOWED_PREFIXES = ("10.77.250.", "10.99.99.", "192.168.1.", "100.64.", "127.", "0.0.0.0")


def _load_yaml(path: Path):
    return yaml.safe_load(path.read_text())


def _code(path: Path) -> str:
    """File text with whole-line and trailing # comments removed."""
    lines = [l for l in path.read_text().splitlines() if not l.lstrip().startswith("#")]
    return "\n".join(re.sub(r"\s+#.*$", "", l) for l in lines)


def _hujson(path: Path):
    """Parse huJSON: strip // comments and trailing commas (no // inside strings here)."""
    text = re.sub(r"(?m)^\s*//.*$", "", path.read_text())
    text = re.sub(r"(?m)\s//.*$", "", text)
    text = re.sub(r",(\s*[\]}])", r"\1", text)
    return json.loads(text)


def test_bundle_files_exist():
    for p in (COMPOSE, CONFIG, ACL, NGINX, NGINX_ACME, README, LAB, LAB_COMPOSE):
        assert p.is_file(), p


def test_headscale_image_is_pinned_by_tag_and_digest():
    svc = _load_yaml(COMPOSE)["services"]["headscale"]
    assert re.fullmatch(
        r"docker\.io/headscale/headscale:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", svc["image"]
    ), svc["image"]
    assert svc["image"].startswith("docker.io/headscale/headscale:v0.29.4@sha256:")


def test_compose_ports_are_loopback_http_and_public_stun_only():
    svc = _load_yaml(COMPOSE)["services"]["headscale"]
    ports = [str(p) for p in svc["ports"]]
    assert sorted(ports) == ["127.0.0.1:18080:8080", "3478:3478/udp"], ports


def test_compose_is_hardened_and_isolated():
    doc = _load_yaml(COMPOSE)
    svc = doc["services"]["headscale"]
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert doc["name"] == "headscale"
    blob = _code(COMPOSE)
    assert "docker.sock" not in blob
    assert "network_mode" not in blob
    # Config is mounted read-only, as a DIRECTORY (an inode-swapping editor would
    # leave a single-file mount stale).
    assert "./config:/etc/headscale:ro" in svc["volumes"]


def test_config_matches_the_runbook():
    cfg = _load_yaml(CONFIG)
    assert cfg["server_url"] == "https://hs.the411.life"
    assert cfg["listen_addr"] == "0.0.0.0:8080"  # container side; published on loopback only
    assert cfg["tls_cert_path"] == "" and cfg["tls_key_path"] == ""
    assert cfg["derp"]["server"]["enabled"] is True
    assert cfg["derp"]["server"]["stun_listen_addr"] == "0.0.0.0:3478"
    assert cfg["derp"]["server"]["verify_clients"] is True
    assert cfg["derp"]["urls"] == [], "Tailscale's public DERP map must stay out"
    assert cfg["dns"]["magic_dns"] is False
    assert cfg["policy"]["mode"] == "file"
    assert cfg["policy"]["path"] == "/etc/headscale/acl.hujson"
    assert cfg["database"]["type"] == "sqlite"
    assert cfg["logtail"]["enabled"] is False
    assert cfg["node"]["expiry"] == 0
    # gRPC/metrics never leave the container.
    assert cfg["grpc_listen_addr"].startswith("127.0.0.1:")
    assert cfg["metrics_listen_addr"].startswith("127.0.0.1:")
    assert cfg["trusted_proxies"] == ["10.77.250.0/28"]
    # ...and that is the compose network's subnet.
    net = _load_yaml(COMPOSE)["networks"]["headscale"]["ipam"]["config"][0]["subnet"]
    assert net == "10.77.250.0/28"


def test_acl_intent_is_exactly_this():
    acl = _hujson(ACL)
    assert acl["groups"] == {"group:owner": ["owner@"]}
    assert set(acl["tagOwners"]) == {"tag:zoe-box", "tag:zoe-panel"}
    # Route auto-approval: one prefix, one approver, no exit node.
    assert acl["autoApprovers"] == {"routes": {"192.168.1.218/32": ["tag:zoe-box"]}}
    rules = acl["acls"]
    assert len(rules) == 2, "default deny: nothing beyond the two stated rules"
    panel, ssh = rules
    assert panel == {
        "action": "accept",
        "proto": "tcp",
        "src": ["tag:zoe-panel"],
        "dst": ["192.168.1.218:443,8007"],
    }
    assert ssh == {
        "action": "accept",
        "proto": "tcp",
        "src": ["tag:zoe-box", "group:owner"],
        "dst": ["tag:zoe-panel:22"],
    }
    assert "ssh" not in acl, "Tailscale SSH is deliberately not enabled"
    assert acl["tests"], "the executable assertions must stay"


def test_acl_tests_block_denies_the_extra_ports():
    acl = _hujson(ACL)
    first = next(t for t in acl["tests"] if t["src"] == "tag:zoe-panel")
    assert first["accept"] == ["192.168.1.218:443", "192.168.1.218:8007"]
    assert "192.168.1.218:9999" in first["deny"]  # the lab's extra port


def test_nginx_snippet_follows_the_docs_and_avoids_host_collisions():
    text = NGINX.read_text()
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert "proxy_set_header Upgrade $http_upgrade;" in code
    assert "proxy_set_header Connection $hs_connection_upgrade;" in code
    assert "proxy_http_version 1.1;" in code
    assert "proxy_buffering off;" in code
    assert "upstream hs_upstream" in code and "server 127.0.0.1:18080" in code
    # The host already serves other vhosts: generic names would collide.
    assert "$connection_upgrade" not in code
    assert "upstream headscale" not in code
    # nginx 1.24 (Ubuntu 24.04) has no standalone `http2 on;`.
    assert "http2 on" not in code
    assert "ssl_certificate     /etc/letsencrypt/live/hs.the411.life/fullchain.pem;" in code
    assert "location ^~ /.well-known/acme-challenge/" in code


def test_nginx_stage1_is_http_only():
    code = "\n".join(
        l for l in NGINX_ACME.read_text().splitlines() if not l.lstrip().startswith("#")
    )
    assert "listen 80;" in code
    assert "443" not in code and "ssl" not in code


def test_acl_ip_matches_between_policy_and_readme_commands():
    readme = README.read_text()
    assert "--advertise-routes=192.168.1.218/32" in readme
    assert "--accept-routes --accept-dns=false" in readme
    assert "--login-server https://hs.the411.life" in readme
    assert "udp" in readme.lower() and "3478" in readme


def test_lab_pins_the_client_version_and_is_internal():
    text = _code(LAB_COMPOSE)
    assert "docker.io/tailscale/tailscale:v1.104.1" in text
    assert "--tun=userspace-networking" in text
    assert "internal: true" in text
    assert "NET_ADMIN" not in text and "/dev/net/tun" not in text and "privileged" not in text
    assert "network_mode" not in text


def test_lab_script_is_valid_bash_and_guards_memory():
    assert LAB.stat().st_mode & 0o111, "lab_test.sh must be executable"
    res = subprocess.run(["bash", "-n", str(LAB)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    text = LAB.read_text()
    assert 'MIN_MEM_MB="${HS_LAB_MIN_MEM_MB:-1500}"' in text
    assert "docker compose -p" in text or 'docker compose -p "$PROJECT"' in text
    # never touches the host tailscaled / network


def test_lab_script_never_mutates_the_host():
    code = _code(LAB)
    for pat in (
        r"\bsudo\b",
        r"\bsystemctl\b",
        r"\bresolvectl\b",
        r"\biptables\b",
        r"\bnft\b",
        r"\bip\s+(route|rule|addr|link\s+(set|add|del))\b",
        r"--privileged",
        r"--net(work)?[= ]host",
        r"/var/run/docker\.sock",
    ):
        assert not re.search(pat, code), pat
    # A direct (host-side) tailscale invocation may only be a read-only status probe;
    # everything that changes state goes through the containers (`tsc`, `exec`).
    direct = re.compile(r"(^|[;&|(]|\$\()\s*tailscale\s+(up|down|login|logout|set|serve|funnel|file|switch)\b", re.M)
    assert not direct.search(code)


def test_no_hosting_or_business_details_in_the_bundle():
    """Public repo: only the hostname hs.the411.life may name the owner's setup."""
    banned = re.compile(
        r"digitalocean|droplet|sydney|github actions|deploy key|/opt/the411|the411's|(?<!hs\.)(?<!hs\\\.)the411",
        re.I,
    )
    for path in sorted(BUNDLE.rglob("*")):
        if path.is_file():
            hit = banned.search(path.read_text())
            assert not hit, "%s in %s" % (hit.group(0), path)


def test_no_secrets_and_no_public_addresses_in_the_bundle():
    secret = re.compile(r"(hskey-auth-[A-Za-z0-9_-]{24,}|tskey-[A-Za-z0-9-]{16,}|BEGIN [A-Z ]*PRIVATE KEY)")
    ipv4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    for path in sorted(BUNDLE.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text()
        assert not secret.search(text), "secret-looking token in %s" % path
        for m in ipv4.finditer(text):
            ip = m.group(0)
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                continue  # version-like strings, e.g. 0.29.4.0
            if ip.startswith(PRIVATE_ALLOWED_PREFIXES) or addr.is_private:
                continue
            raise AssertionError("public-looking address %s in %s" % (ip, path))
