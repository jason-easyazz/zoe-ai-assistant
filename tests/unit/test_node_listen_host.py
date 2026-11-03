"""``scripts/lib/node_listen_host.mjs`` keeps the Flue sidecars (:3579 brain, :3582 Telegram) off the LAN (2026-10-19).

Flue 2.1.1's generated entry passes no hostname, so Node binds every interface; the preload rewrites a TCP listen to
``ZOE_LISTEN_HOST``. Each case starts a real Node server through the preload and reads back the bound address - with the
preload and without it (the negative control: the same call binds every interface). Also pins the drop-ins that apply it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
SHIM = ROOT / "scripts" / "lib" / "node_listen_host.mjs"
DROPINS = [ROOT / "scripts" / "setup" / "systemd" / f"{u}.service.d" / "zoe-loopback.conf"
           for u in ("flue-zoe-brain-2x", "flue-zoe-telegram")]
NODE = shutil.which("node")

# How @hono/node-server (Flue's server) calls it: listen(port, undefined, cb). Plus the other public shapes.
SHAPES = {
    "hono": "s.listen(0, undefined, done)",
    "port_cb": "s.listen(0, done)",
    "port_host": "s.listen(0, '0.0.0.0', done)",
    "options": "s.listen({ port: 0 }, done)",
    "options_host": "s.listen({ port: 0, host: '0.0.0.0' }, done)",
}


def _bound(shape: str, preload: bool, host: str = "127.0.0.1") -> str:
    script = (
        "import http from 'node:http';"
        "const s = http.createServer(() => {});"
        "const done = () => { console.log(JSON.stringify(s.address())); s.close(); };"
        + SHAPES[shape] + ";"
    )
    env = dict(os.environ)
    env.pop("NODE_OPTIONS", None)
    env["ZOE_LISTEN_HOST"] = host
    if preload:
        env["NODE_OPTIONS"] = f"--import={SHIM.as_uri()}"
    out = subprocess.run([NODE, "--input-type=module", "-e", script], env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])["address"]


@pytest.fixture(autouse=True)
def _need_node():
    if NODE is None:
        if os.environ.get("CI"):
            pytest.fail("node is required in CI: this guards the brain/Telegram LAN exposure")
        pytest.skip("node not installed")


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_tcp_listen_is_forced_onto_the_configured_host(shape):
    assert _bound(shape, preload=True) == "127.0.0.1"


@pytest.mark.parametrize("shape", ["hono", "port_cb", "options"])
def test_negative_control_without_the_preload_node_binds_every_interface(shape):
    assert _bound(shape, preload=False) in ("::", "0.0.0.0")


def test_an_empty_host_leaves_node_alone():
    assert _bound("hono", preload=True, host="") in ("::", "0.0.0.0")


def test_a_unix_socket_listen_is_untouched(tmp_path):
    sock = tmp_path / "s.sock"
    script = (
        "import net from 'node:net';"
        "const s = net.createServer(() => {});"
        f"s.listen({json.dumps(str(sock))}, () => {{ console.log(JSON.stringify(s.address())); s.close(); }});"
    )
    env = dict(os.environ, ZOE_LISTEN_HOST="127.0.0.1", NODE_OPTIONS=f"--import={SHIM.as_uri()}")
    out = subprocess.run([NODE, "--input-type=module", "-e", script], env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == str(sock)


@pytest.mark.parametrize("dropin", DROPINS, ids=lambda p: p.parent.name)
def test_the_dropins_preload_the_shim_onto_loopback(dropin):
    text = dropin.read_text()
    assert "Environment=ZOE_LISTEN_HOST=127.0.0.1" in text
    opts = [ln for ln in text.splitlines() if ln.startswith("Environment=NODE_OPTIONS=")]
    assert len(opts) == 1 and "--import=%h/assistant/scripts/lib/node_listen_host.mjs" in opts[0]
    assert "ExecStart" not in text            # a drop-in only adds the environment; the unit's own command stays
    if dropin.parent.name.startswith("flue-zoe-telegram"):
        # NODE_OPTIONS is one variable and this drop-in sorts last: it must carry the unit's Happy-Eyeballs flag too.
        assert "--network-family-autoselection-attempt-timeout=1500" in opts[0]


def test_no_other_brain_node_options_would_be_overridden():
    """The brain drop-in carries only the import: true only while the brain template and its tracked drop-ins set no other
    NODE_OPTIONS (the last drop-in wins). Adding one there means adding it to zoe-loopback.conf too."""
    base = ROOT / "scripts" / "setup" / "systemd"
    others = [base / "flue-zoe-brain-2x.service"] + sorted((base / "flue-zoe-brain-2x.service.d").glob("*.conf"))
    for f in others:
        if f.name == "zoe-loopback.conf":
            continue
        assert "NODE_OPTIONS" not in f.read_text(), f
