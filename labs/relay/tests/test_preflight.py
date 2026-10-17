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
        # "localhost" is a NAME: the system resolver (and /etc/hosts) can map it to a routable address, so it is refused
        for addr in ("10.0.0.1:9100", "0.0.0.0:9100", "plugin.example.com:9100", "localhost:9100", "https://127.0.0.1:9100", "127.0.0.1"):
            c = good()
            c["httpPlugins"][0]["addr"] = addr
            with self.subTest(addr=addr):
                self.assertTrue(preflight.check_config(c))

    def test_loopback_forms_accepted(self):
        for addr in ("127.0.0.1:9100", "http://127.0.0.1:9100", "[::1]:9100"):
            c = good()
            c["httpPlugins"][0]["addr"] = addr
            with self.subTest(addr=addr):
                self.assertEqual(preflight.check_config(c), [])

    def test_every_extra_listener_fails(self):
        # table-driven over the list the code forbids AND over a literal list, so shrinking the code's list is caught
        literal = (("vhostHTTPPort", 80), ("tcpmuxHTTPConnectPort", 1337), ("kcpBindPort", 7000), ("quicBindPort", 7002))
        for key, val in literal:
            c = good()
            c[key] = val
            with self.subTest(key=key):
                self.assertTrue(any(key in e for e in preflight.check_config(c)))
        for key in preflight.FORBIDDEN_LISTENERS:
            self.assertIn(key, [k for k, _ in literal] + ["sshTunnelGateway.bindPort"])

    def test_open_surfaces_fail(self):
        c = good()
        c["subDomainHost"] = "x.example.com"
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


def serve(handler_answer, status=200, raw_body=None):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            raw = raw_body if raw_body is not None else json.dumps(handler_answer).encode()
            self.send_response(status)
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

    def test_a_server_that_is_not_the_session_master_is_refused(self):
        # healthy on /healthz but 404 / 401 / 500 on the real route: every real login would be refused, so not ready
        for status in (404, 401, 500):
            srv = serve({}, status=status)
            self.addCleanup(srv.shutdown)
            with self.subTest(status=status):
                self.assertIn(str(status), preflight.probe_plugin(f"127.0.0.1:{srv.server_port}", "/handler"))

    def test_malformed_answers_are_refused(self):
        for body in (b"not json", b"[]", b'"reject"', b'{"reject": "true"}', b"{}"):
            srv = serve(None, raw_body=body)
            self.addCleanup(srv.shutdown)
            with self.subTest(body=body):
                self.assertIsNotNone(preflight.probe_plugin(f"127.0.0.1:{srv.server_port}", "/handler"))

    def test_nothing_listening_is_refused(self):
        self.assertIn("unreachable", preflight.probe_plugin("127.0.0.1:1", "/handler", timeout=0.5))


if __name__ == "__main__":
    unittest.main()
