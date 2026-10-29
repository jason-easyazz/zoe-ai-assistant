"""frpc config generator + safety check for the household box (panel-anywhere sections 2 and 5.4; relay lab cell T6, and the T9
``trustedCaFile`` half). Ported with the generator from labs/relay/box/frpc_config.py (PR #2094).

What the check exists for: the ONE proxy's local target must be the nginx RELAY LISTENER and nothing else. A loopback target that
is not the listener - above all zoe-data's ``127.0.0.1:8000``, which ``auth._is_internal_request`` trusts and which fronts every
``require_internal_token`` endpoint - would publish that service to the whole relay. And ``transport.tls.trustedCaFile`` must be
set, because frpc silently skips verifying the relay without it (the relay secret would go to whoever answers).

The negative fixture the design asks for is the config pointing at ``127.0.0.1:8000``. Every negative here has its positive
control in the same file (the generated config passes).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

PATH = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "relay" / "frpc_config.py"
spec = importlib.util.spec_from_file_location("relay_frpc_config", PATH)
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)

HID = "ha11111111"
KW = dict(hid=HID, secret="s3cret", relay_addr="203.0.113.10", relay_port=443, relay_ca_file="/etc/zoe/relay-ca.pem",
          relay_server_name="relay.example")


def violations(text, **kw):
    return fc.check(text, expect_hid=HID, expect_relay_addr="203.0.113.10", **kw)


def test_generated_config_passes_and_targets_the_published_listener():
    text = fc.render(**KW)
    assert violations(text) == []
    assert 'localIP = "127.0.0.1"' in text and "localPort = 9443" in text
    assert (fc.LISTENER_HOST, fc.LISTENER_PORT) == ("127.0.0.1", 9443)


def test_listener_port_matches_the_compose_publish_and_the_nginx_listener():
    root = PATH.parents[3]
    assert "127.0.0.1:%d:%d" % (fc.LISTENER_PORT, fc.LISTENER_PORT) in (root / "docker-compose.yml").read_text(encoding="utf-8")
    listener = (root / "services/zoe-ui/nginx.d/relay-listener.conf").read_text(encoding="utf-8")
    assert "listen %d ssl proxy_protocol;" % fc.LISTENER_PORT in listener


def test_zoe_data_loopback_target_fails_and_says_why():
    errs = violations(fc.render(**KW, target_port=8000))
    assert any("zoe-data" in e and "8000" in e for e in errs), errs


def test_the_checks_own_fixture_for_8000_is_a_real_config_that_otherwise_passes():
    # Sensitivity: the ONLY thing wrong with the fixture is the port, so the failure above is the port rule and nothing else.
    bad = fc.render(**KW, target_port=8000)
    assert violations(bad.replace("localPort = 8000", "localPort = 9443")) == []


def test_any_other_target_fails():
    for host, port in (("127.0.0.1", 5432), ("127.0.0.1", 8123), ("localhost", 9443), ("0.0.0.0", 9443), ("10.0.0.5", 9443),
                       ("::1", 9443), ("127.0.0.1", 8443)):
        assert violations(fc.render(**KW, target_host=host, target_port=port)), (host, port)


def test_listener_is_the_parameter_not_a_constant():
    assert violations(fc.render(**KW, target_port=9500), listener=("127.0.0.1", 9500)) == []
    assert violations(fc.render(**KW, target_port=9500))


def test_frpc_https2http_plugin_fails():
    extra = '\n[proxies.plugin]\ntype = "https2http"\nlocalAddr = "127.0.0.1:8000"\ncrtPath = "a"\nkeyPath = "b"\n'
    assert any("plugin" in e for e in violations(fc.render(**KW, extra_proxy=extra)))
    errs = violations(fc.render(**KW, extra_proxy="\n[proxies.plugin]\n"))  # an empty plugin table is still a declaration
    assert any("plugin" in e for e in errs), errs


def test_missing_trusted_ca_fails():
    assert any("trustedCaFile" in e for e in violations(fc.render(**KW, trusted_ca=False)))


def test_only_one_https_proxy_for_exactly_the_households_own_name():
    assert violations(fc.render(**KW).replace('type = "https"', 'type = "tcp"'))
    assert violations(fc.render(**KW, extra_proxy='\n[[proxies]]\nname = "x"\ntype = "https"\nlocalPort = 9443\n'))
    assert violations(fc.render(**KW, custom_domains=["hb22222222.zoe.internal"]))
    assert violations(fc.render(**KW, custom_domains=["*.zoe.internal"]))
    assert violations(fc.render(**KW, extra_proxy='subdomain = "x"\n'))


def test_admin_api_and_includes_fail():
    assert violations(fc.render(**KW, extra_top="webServer.port = 7400\n"))
    assert violations(fc.render(**KW, extra_top='includes = ["/tmp/x.toml"]\n'))


def test_login_must_keep_retrying():
    assert any("loginFailExit" in e for e in violations(fc.render(**KW).replace("loginFailExit = false\n", "")))
    assert any("loginFailExit" in e for e in violations(fc.render(**KW).replace("loginFailExit = false", "loginFailExit = true")))


def test_unexpected_shapes_are_violations_not_exceptions():
    base = fc.render(**KW)
    header = base.split("\n[[proxies]]")[0] + "\n"
    shapes = {
        "[proxies] as a table": header + "[proxies]\nname = 'x'\n",
        "proxies as a scalar": "proxies = 3\n" + header,
        "proxies as strings": header + "proxies = ['a']\n",
        "transport as a scalar": "transport = 3\nuser = 'x'\n",
        "empty file": "",
        "garbage": "not = [toml",
    }
    for label, text in shapes.items():
        assert violations(text), label  # a list of violations: never an exception and never []


def test_the_checker_failing_itself_means_refusal():
    errs = fc.check(fc.render(**KW), expect_hid=HID, listener=None)
    assert len(errs) == 1 and "could not be checked" in errs[0]


def test_cli_exit_codes(tmp_path):
    import subprocess
    import sys

    good, bad = tmp_path / "good.toml", tmp_path / "bad.toml"
    good.write_text(fc.render(**KW))
    bad.write_text(fc.render(**KW, target_port=8000))
    run = lambda f: subprocess.run([sys.executable, str(PATH), str(f), HID, "203.0.113.10"], capture_output=True, text=True)  # noqa: E731
    assert run(good).returncode == 0
    r = run(bad)
    assert r.returncode == 1 and "8000" in r.stderr
