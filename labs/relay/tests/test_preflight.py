"""Unit tests for relay/preflight.py (lab cell T2, config half)."""
import http.server
import json
import os
import sys
import threading
import tomllib
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "relay"))
import preflight  # noqa: E402

TMPL = os.path.join(HERE, "..", "relay", "frps.toml.tmpl")


def good() -> dict:
    with open(TMPL, "rb") as f:
        return tomllib.loads(f.read().decode().replace("@@ADMIN_PASSWORD@@", "x"))


class ConfigTests(unittest.TestCase):
    def test_shipped_template_passes(self):
        self.assertEqual(preflight.check_config(good()), [])

    def test_no_plugin_fails(self):
        c = good()
        del c["httpPlugins"]
        self.assertTrue(any("no [[httpPlugins]]" in e for e in preflight.check_config(c)))

    def test_a_second_plugin_is_refused(self):
        c = good()
        c["httpPlugins"].append(dict(c["httpPlugins"][0], name="second", addr="127.0.0.1:9101"))
        self.assertTrue(any("exactly one" in e for e in preflight.check_config(c)))

    def test_each_required_op_is_required(self):
        for op in ("Login", "NewProxy", "NewUserConn"):  # literal on purpose: do not derive from the code under test
            c = good()
            c["httpPlugins"][0]["ops"] = [o for o in c["httpPlugins"][0]["ops"] if o != op]
            with self.subTest(op=op):
                self.assertTrue(any(op in e for e in preflight.check_config(c)))

    def test_plugin_must_be_loopback_plain_http(self):
        for addr in ("10.0.0.1:9100", "0.0.0.0:9100", "plugin.example.com:9100", "https://127.0.0.1:9100", "127.0.0.1"):
            c = good()
            c["httpPlugins"][0]["addr"] = addr
            with self.subTest(addr=addr):
                self.assertTrue(preflight.check_config(c))

    def test_loopback_forms_accepted(self):
        for addr in ("127.0.0.1:9100", "localhost:9100", "http://127.0.0.1:9100"):
            c = good()
            c["httpPlugins"][0]["addr"] = addr
            with self.subTest(addr=addr):
                self.assertEqual(preflight.check_config(c), [])

    def test_open_surfaces_fail(self):
        for key, val in (("vhostHTTPPort", 80), ("tcpmuxHTTPConnectPort", 1337), ("subDomainHost", "x.example.com"), ("kcpBindPort", 7000)):
            c = good()
            c[key] = val
            with self.subTest(key=key):
                self.assertTrue(preflight.check_config(c))
        c = good()
        c["sshTunnelGateway"] = {"bindPort": 2200}
        self.assertTrue(preflight.check_config(c))
        c = good()
        c["transport"]["tls"]["force"] = False
        self.assertTrue(preflight.check_config(c))
        c = good()
        c["webServer"]["addr"] = "0.0.0.0"
        self.assertTrue(preflight.check_config(c))


def serve(handler_answer):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            raw = json.dumps(handler_answer).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class ProbeTests(unittest.TestCase):
    def test_strict_plugin_passes(self):
        srv = serve({"reject": True, "reject_reason": "no", "unchange": True})
        self.addCleanup(srv.shutdown)
        self.assertIsNone(preflight.probe_plugin(f"127.0.0.1:{srv.server_port}", "/handler"))

    def test_permissive_plugin_is_refused(self):
        srv = serve({"reject": False, "unchange": True})
        self.addCleanup(srv.shutdown)
        self.assertIn("ACCEPTED", preflight.probe_plugin(f"127.0.0.1:{srv.server_port}", "/handler"))

    def test_nothing_listening_is_refused(self):
        self.assertIn("unreachable", preflight.probe_plugin("127.0.0.1:1", "/handler", timeout=0.5))


if __name__ == "__main__":
    unittest.main()
