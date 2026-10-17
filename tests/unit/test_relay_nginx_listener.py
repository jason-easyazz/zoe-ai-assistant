"""The nginx half of the panel relay (panel-anywhere R1, section 5.1): the relay listener's contract and its safe-by-default wiring.

Pure text pins over ``services/zoe-ui/nginx.conf``, ``nginx.d/*``, ``docker-compose.yml`` and ``scripts/setup/relay/*`` (plus the
host ``openssl`` for the PKI / enable-script cases), in the style of ``test_nginx_delivery_layer.py``. The behaviour of a real nginx
(headers overwritten, no client certificate gets nowhere) is ``services/zoe-data/tests/test_relay_nginx_live.py`` (opt-in, needs
docker); the zoe-data half is ``services/zoe-data/tests/test_relay_ingress.py``.

The rules pinned here:
  * LAN behaviour is unchanged with the relay off: the listener file is not included by name, only through a glob that matches
    nothing without ``ssl/relay/listener.conf``; both LAN server blocks keep their body.
  * a LAN client can NEVER claim the relay ingress: every proxying location sets both ingress headers from server variables that
    the LAN blocks leave empty;
  * the listener requires a household-CA client certificate (no optional phase), TLS 1.3, sets the headers absolutely, forwards no
    address, and 404s every service that carries no Zoe auth of its own;
  * the only host port published is on loopback.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "services" / "zoe-ui"
CONF = (UI / "nginx.conf").read_text(encoding="utf-8")
LOCATIONS = (UI / "nginx.d" / "locations.inc").read_text(encoding="utf-8")
LISTENER = (UI / "nginx.d" / "relay-listener.conf").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
SETUP = ROOT / "scripts" / "setup" / "relay"

# Services a relayed request may reach: zoe-data authenticates every request itself (RelayIngressMiddleware); zoe-auth knows nothing
# about client certificates, so its locations must ask zoe-data first (auth-gate.inc -> auth_request).
RELAY_OK_TARGETS = ("host.docker.internal:8000", "zoe-auth:8002")
AUTH_GATE_INCLUDE = "include /etc/nginx/zoe/auth-gate.inc;"


def _strip_comments(text: str) -> str:
    return "\n".join(re.sub(r"\s#.*$", "", ln) if not ln.lstrip().startswith("#") else "" for ln in text.splitlines())


def _locations(text: str) -> list[tuple[str, str]]:
    """(header line, body) for every ``location ... {`` block of locations.inc (it has no nested braces except `if` blocks)."""
    out, cur, head, depth = [], [], None, 0
    for ln in text.splitlines():
        if head is None:
            m = re.match(r"^location\b.*\{\s*$", ln)
            if m:
                head, cur, depth = ln.strip(), [], 1
            elif re.match(r"^location\b.*\{.*\}\s*$", ln):
                out.append((ln.strip(), ln))  # one-line block: location ~ x { return 404; }
            continue
        depth += ln.count("{") - ln.count("}")
        if depth <= 0:
            out.append((head, "\n".join(cur)))
            head = None
        else:
            cur.append(ln)
    return out


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SETUP / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── wiring: off by default, LAN unchanged ────────────────────────────────────────────────────────────────────────────────


def test_the_listener_is_only_reachable_through_a_glob_that_matches_nothing_by_default():
    assert len(re.findall(r"^server\s*\{", CONF, re.M)) == 2, "the LAN blocks are still exactly :80 and :443"
    assert CONF.count("include /etc/nginx/ssl/relay/*.conf;") == 1
    assert "relay-listener" not in _strip_comments(CONF) and "9443" not in _strip_comments(CONF), \
        "nginx.conf must never name the listener file: a missing file is a startup failure, a glob is not"
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^ssl/relay/$", gi, re.M), "household CA / leaf / rendered listener must never be committable"


def test_both_lan_blocks_include_the_lan_ingress_variables_before_the_body():
    for block in re.findall(r"^server\s*\{.*?^\}", CONF, re.M | re.S):
        assert "include /etc/nginx/zoe/ingress-lan.inc;" in block
        assert block.index("ingress-lan.inc") < block.index("locations.inc")
    lan = (UI / "nginx.d" / "ingress-lan.inc").read_text(encoding="utf-8")
    code = _strip_comments(lan)
    assert 'set $zoe_ingress "";' in code and 'set $zoe_client_cert_fp "";' in code
    assert "set $zoe_real_ip $remote_addr;" in code and "set $zoe_xff $proxy_add_x_forwarded_for;" in code  # LAN: exactly as before


def test_every_proxying_location_sets_both_ingress_headers_from_the_server_variables():
    blocks = [(h, b) for h, b in _locations(LOCATIONS) if "proxy_pass" in b]
    assert len(blocks) >= 15, "the parser found the proxy locations"
    for head, body in blocks:
        assert "include /etc/nginx/zoe/ingress-headers.inc;" in body, "a client could pass X-Zoe-Ingress through %s" % head
    inc = _strip_comments((UI / "nginx.d" / "ingress-headers.inc").read_text(encoding="utf-8"))
    assert "proxy_set_header X-Zoe-Ingress $zoe_ingress;" in inc
    assert "proxy_set_header X-Zoe-Client-Cert-Fp $zoe_client_cert_fp;" in inc
    # No location may hard-code the marker or set the headers on its own (it would shadow the variable).
    assert not re.search(r"X-Zoe-Ingress|X-Zoe-Client-Cert-Fp", LOCATIONS.replace("ingress-headers.inc", ""), re.I)
    # The address headers go through the variables too, so the relay listener can blank them.
    code = _strip_comments(LOCATIONS)
    assert "$remote_addr" not in code and "$proxy_add_x_forwarded_for" not in code
    assert "proxy_set_header X-Real-IP $zoe_real_ip;" in code and "proxy_set_header X-Forwarded-For $zoe_xff;" in code


def test_services_with_no_zoe_auth_are_not_reachable_over_the_relay():
    for head, body in _locations(LOCATIONS):
        target = re.search(r"proxy_pass\s+https?://([^/\s;]+)", body)
        if not target:
            continue
        if target.group(1) == "zoe-auth:8002":
            assert AUTH_GATE_INCLUDE in body, "%s reaches zoe-auth over the relay with no certificate check" % head
        if target.group(1) in RELAY_OK_TARGETS:
            continue
        first = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("#")][0]
        assert first == "include /etc/nginx/zoe/lan-only.inc;", "%s proxies to %s and must 404 on the relay listener" % (head, target.group(1))
    lan_only = _strip_comments((UI / "nginx.d" / "lan-only.inc").read_text(encoding="utf-8"))
    assert 'if ($zoe_ingress != "")' in lan_only and "return 404;" in lan_only


def test_the_auth_gate_asks_zoe_data_on_the_relay_and_nothing_on_the_lan():
    gate = _strip_comments((UI / "nginx.d" / "auth-gate.inc").read_text(encoding="utf-8"))
    assert gate.strip() == "auth_request /__zoe_auth_gate;", "auth_request's URI is not variable-expanded: a fixed location per server"
    lan = _strip_comments((UI / "nginx.d" / "ingress-lan.inc").read_text(encoding="utf-8"))
    assert re.search(r"location = /__zoe_auth_gate \{\s*internal;\s*return 204;\s*\}", lan), "LAN: an internal always-204, never an upstream hop"
    relay = _strip_comments(LISTENER)
    block = re.search(r"location = /__zoe_auth_gate \{(.*?)\}", relay, re.S).group(1)
    assert "internal;" in block and "proxy_pass http://host.docker.internal:8000/api/relay/check;" in block
    assert "proxy_pass_request_body off;" in block and "include /etc/nginx/zoe/ingress-headers.inc;" in block


def test_every_server_that_includes_the_locations_sets_all_four_ingress_variables_and_defines_the_auth_gate():
    servers = [b for b in re.findall(r"^server\s*\{.*?^\}", CONF, re.M | re.S)] + [_strip_comments(LISTENER)]
    assert len(servers) == 3
    lan_vars = _strip_comments((UI / "nginx.d" / "ingress-lan.inc").read_text(encoding="utf-8"))
    for block in servers:
        if "locations.inc" not in block:
            continue
        text = block + ("\n" + lan_vars if "ingress-lan.inc" in block else "")
        for var in ("zoe_ingress", "zoe_client_cert_fp", "zoe_real_ip", "zoe_xff"):
            assert re.search(r"set \$%s\b" % var, text), "a server includes locations.inc without setting $%s" % var
        assert "location = /__zoe_auth_gate {" in text, "a server includes locations.inc (zoe-auth locations) without the auth gate"


# ── the listener's contract ───────────────────────────────────────────────────────────────────────────────────────────────


def test_the_listener_is_mtls_tls13_proxy_protocol_on_the_published_port():
    code = _strip_comments(LISTENER)
    assert "listen 9443 ssl proxy_protocol;" in code
    assert re.search(r"^\s*ssl_protocols TLSv1\.3;", code, re.M), "TLS 1.3 only"
    assert "ssl_verify_client on;" in code and "optional" not in code, "mTLS from R1 - no optional phase"
    assert "ssl_client_certificate /etc/nginx/ssl/relay/ca.pem;" in code
    assert "ssl_certificate /etc/nginx/ssl/relay/leaf.pem;" in code and "ssl_certificate_key /etc/nginx/ssl/relay/leaf.key;" in code
    assert "$ssl_client_verify != SUCCESS" in code and "return 444;" in code
    assert code.count("server {") == 1 and "listen 80" not in code and "listen 443" not in code


def test_the_listener_sets_the_ingress_headers_absolutely_and_forwards_no_address():
    code = _strip_comments(LISTENER)
    assert 'set $zoe_ingress "relay";' in code
    assert "set $zoe_client_cert_fp $ssl_client_fingerprint;" in code
    assert 'set $zoe_real_ip "";' in code and 'set $zoe_xff "";' in code
    assert "ingress-lan.inc" not in code, "the LAN variables would blank the marker"
    assert "$proxy_protocol_addr" not in code and "$remote_addr" not in code, "an address is not an identity"
    assert "include /etc/nginx/zoe/locations.inc;" in code and "include /etc/nginx/zoe/security-headers.inc;" in code
    # The variables are set at SERVER level, before the locations are included - and not overridden inside any location.
    assert code.index('set $zoe_ingress "relay";') < code.index("locations.inc")
    assert not re.search(r"proxy_set_header\s+X-Zoe-", code)


def test_livekit_is_answered_not_proxied_over_the_relay():
    code = _strip_comments(LISTENER)
    assert "location = /api/voice/livekit-token" in code and '"livekit_available":false' in code


# ── compose: one loopback publish ────────────────────────────────────────────────────────────────────────────────────────


def test_only_a_loopback_port_is_published_for_the_listener():
    ports = re.findall(r"^\s*-\s*['\"]?([^'\"\s#]*9443[^'\"\s#]*)", COMPOSE, re.M)
    assert ports == ["127.0.0.1:9443:9443"], ports


# ── the PKI and the enable script ────────────────────────────────────────────────────────────────────────────────────────

needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="host openssl not installed")
HID = "h0a1b2c3d4"


def _x509(*args):
    return subprocess.run(["openssl", "x509", *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def pki(tmp_path):
    mod = _load("household_pki")
    ca_dir, relay_dir = tmp_path / "ca", tmp_path / "ssl" / "relay"
    mod.init_ca(ca_dir)
    mod.issue_leaf(HID, ca_dir, relay_dir)
    return mod, ca_dir, relay_dir


@needs_openssl
def test_pki_ca_key_stays_out_of_the_docker_mounted_tree_and_is_private(pki):
    mod, ca_dir, relay_dir = pki
    assert (ca_dir / "ca.key").stat().st_mode & 0o077 == 0 and (ca_dir.stat().st_mode & 0o077) == 0
    assert (relay_dir / "leaf.key").stat().st_mode & 0o077 == 0
    assert sorted(p.name for p in relay_dir.iterdir()) == ["ca.pem", "leaf.key", "leaf.pem"], "no ca.key beside the leaf"
    with pytest.raises(SystemExit):
        mod.init_ca(ca_dir)  # never overwrite a CA
    text = _x509("-in", str(relay_dir / "leaf.pem"), "-noout", "-ext", "subjectAltName,extendedKeyUsage")
    assert HID + ".zoe.internal" in text and "TLS Web Server Authentication" in text
    with pytest.raises(SystemExit):
        mod.issue_leaf("Bad Name", ca_dir, relay_dir)


@needs_openssl
def test_a_failed_leaf_renewal_leaves_the_serving_pair_untouched(pki, tmp_path):
    mod, ca_dir, relay_dir = pki
    before = {n: (relay_dir / n).read_bytes() for n in ("leaf.key", "leaf.pem", "ca.pem")}
    with pytest.raises(SystemExit):
        mod.issue_leaf(HID, tmp_path / "no-such-ca", relay_dir)  # wrong --ca-dir
    broken = tmp_path / "broken-ca"
    broken.mkdir()
    (broken / "ca.pem").write_bytes((ca_dir / "ca.pem").read_bytes())
    (broken / "ca.key").write_text("not a key")
    with pytest.raises(SystemExit):
        mod.issue_leaf(HID, broken, relay_dir)  # openssl fails mid-way
    assert {n: (relay_dir / n).read_bytes() for n in before} == before, "key and certificate must still match what nginx serves"
    assert sorted(p.name for p in relay_dir.iterdir()) == ["ca.pem", "leaf.key", "leaf.pem"], "no half-written temp files"
    mod.issue_leaf(HID, ca_dir, relay_dir)  # control: a good renewal does replace them
    assert (relay_dir / "leaf.pem").read_bytes() != before["leaf.pem"]


def test_the_flag_check_reads_every_env_file_in_service_order_and_the_last_assignment_wins(tmp_path):
    en = _load("enable_relay_listener")
    a, b, c = tmp_path / "a.env", tmp_path / "b.env", tmp_path / "c.env"
    a.write_text("ZOE_RELAY_INGRESS=1\n")
    b.write_text("OTHER=1\n")
    assert en.flag_on([a, b]) is True
    c.write_text("ZOE_RELAY_INGRESS=0\n")
    assert en.flag_on([a, b, c]) is False, "a later file (zoe-data.service loads services/zoe-data/.env after .env) wins"
    assert en.flag_on([c, a]) is True
    a.write_text("ZOE_RELAY_INGRESS=1\nZOE_RELAY_INGRESS=0\n")
    assert en.flag_on([a]) is False, "the first =1 in a file does not win over a later =0"
    a.write_text("# ZOE_RELAY_INGRESS=1\n")
    assert en.flag_on([a]) is False and en.flag_on([tmp_path / "missing.env"]) is False


@needs_openssl
def test_sign_csr_issues_client_auth_only_and_ignores_requested_extensions(pki, tmp_path):
    mod, ca_dir, _ = pki
    key, csr = tmp_path / "panel.key", tmp_path / "panel.csr"
    subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key)], check=True, capture_output=True)
    # The panel ASKS for a CA certificate that is also a server certificate.
    cfg = tmp_path / "req.cnf"
    cfg.write_text("[req]\ndistinguished_name=dn\nreq_extensions=ext\nprompt=no\n[dn]\nCN=panel\n"
                   "[ext]\nbasicConstraints=critical,CA:TRUE\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:evil.zoe.internal\n")
    subprocess.run(["openssl", "req", "-new", "-key", str(key), "-config", str(cfg), "-out", str(csr)], check=True, capture_output=True)
    asked = subprocess.run(["openssl", "req", "-in", str(csr), "-noout", "-text"], capture_output=True, text=True).stdout
    assert "CA:TRUE" in asked, "sensitivity: the CSR really requested it"
    out = tmp_path / "panel.pem"
    fp = mod.sign_csr(csr, "kitchen-panel", ca_dir, out)
    text = _x509("-in", str(out), "-noout", "-text")
    assert "CA:FALSE" in text and "CA:TRUE" not in text
    assert "TLS Web Client Authentication" in text and "TLS Web Server Authentication" not in text
    assert "evil.zoe.internal" not in text
    # nginx's $ssl_client_fingerprint is the lower-case hex SHA-1 of the DER certificate.
    assert re.fullmatch(r"[0-9a-f]{40}", fp)
    assert fp == _x509("-in", str(out), "-noout", "-fingerprint", "-sha1").split("=", 1)[1].strip().replace(":", "").lower()
    verified = subprocess.run(["openssl", "verify", "-CAfile", str(ca_dir / "ca.pem"), "-purpose", "sslclient", str(out)],
                              capture_output=True, text=True)
    assert verified.returncode == 0, verified.stdout + verified.stderr
    with pytest.raises(SystemExit):
        mod.sign_csr(csr, "../etc", ca_dir, out)


@needs_openssl
def test_enable_installs_the_listener_only_when_everything_is_in_place(pki, tmp_path):
    _, _, relay_dir = pki
    en = _load("enable_relay_listener")
    env = tmp_path / ".env"
    # Flag off in the env file: refuse (listener up + flag off = relayed requests judged as LAN).
    env.write_text("ZOE_RELAY_INGRESS=0\n# ZOE_RELAY_INGRESS=1\n")
    assert any("ZOE_RELAY_INGRESS" in p for p in en.problems(relay_dir, env))
    assert en.main(["--repo", str(ROOT), "--ssl-dir", str(relay_dir.parent), "--env-file", str(env)]) == 2
    assert not (relay_dir / "listener.conf").exists()
    # Flag on: installed, byte-identical to the shipped server block.
    env.write_text("export ZOE_RELAY_INGRESS=\"1\"  # relay\n")
    assert en.problems(relay_dir, env) == []
    assert en.main(["--repo", str(ROOT), "--ssl-dir", str(relay_dir.parent), "--env-file", str(env)]) == 0
    assert (relay_dir / "listener.conf").read_text() == LISTENER
    # --disable removes it again.
    assert en.main(["--repo", str(ROOT), "--ssl-dir", str(relay_dir.parent), "--disable"]) == 0
    assert not (relay_dir / "listener.conf").exists()


@needs_openssl
def test_enable_refuses_incomplete_mismatched_or_wrong_purpose_material(pki, tmp_path):
    mod, ca_dir, relay_dir = pki
    en = _load("enable_relay_listener")
    # missing file
    (relay_dir / "leaf.key").rename(tmp_path / "moved.key")
    assert any("leaf.key" in p for p in en.problems(relay_dir, None))
    (tmp_path / "moved.key").rename(relay_dir / "leaf.key")
    assert en.problems(relay_dir, None) == []
    # a leaf from ANOTHER CA
    other = tmp_path / "other"
    mod.init_ca(other)
    mod.issue_leaf(HID, other, tmp_path / "otherssl")
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    for name in ("leaf.pem", "leaf.key"):
        (mixed / name).write_bytes((tmp_path / "otherssl" / name).read_bytes())
    (mixed / "ca.pem").write_bytes((relay_dir / "ca.pem").read_bytes())
    assert any("does not verify" in p for p in en.problems(mixed, None))
    # a key that is not the leaf's
    (mixed / "leaf.pem").write_bytes((relay_dir / "leaf.pem").read_bytes())
    assert any("not the private key" in p for p in en.problems(mixed, None))
    # a client-auth certificate presented as the server leaf
    key, csr, crt = tmp_path / "c.key", tmp_path / "c.csr", mixed / "leaf.pem"
    subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key)], check=True, capture_output=True)
    subprocess.run(["openssl", "req", "-new", "-key", str(key), "-subj", "/CN=x", "-out", str(csr)], check=True, capture_output=True)
    mod.sign_csr(csr, "x", ca_dir, crt)
    (mixed / "leaf.key").write_bytes(key.read_bytes())
    bad = en.problems(mixed, None)
    assert any("zoe.internal" in p for p in bad) and any("serverAuth" in p for p in bad)
    # an expiring leaf
    short = tmp_path / "short"
    mod.issue_leaf(HID, ca_dir, short, days=3)
    assert any("expires within 7 days" in p for p in en.problems(short, None))
