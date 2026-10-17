"""Unit tests for box/frpc_config.py (lab cell T6, and the T9 trustedCaFile half)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "box"))
import frpc_config as fc  # noqa: E402

HID = "ha11111111"
KW = dict(hid=HID, secret="s3cret", relay_addr="203.0.113.10", relay_port=443, relay_ca_file="/etc/zoe/relay-ca.pem", relay_server_name="relay.example")


def violations(text, **kw):
    return fc.check(text, expect_hid=HID, expect_relay_addr="203.0.113.10", **kw)


class FrpcConfigTests(unittest.TestCase):
    def test_generated_config_passes(self):
        self.assertEqual(violations(fc.render(**KW)), [])

    def test_zoe_data_loopback_target_fails_and_says_why(self):
        errs = violations(fc.render(**KW, target_port=8000))
        self.assertTrue(any("zoe-data" in e and "8000" in e for e in errs), errs)

    def test_any_other_target_fails(self):
        for host, port in (("127.0.0.1", 5432), ("127.0.0.1", 8123), ("localhost", 8443), ("0.0.0.0", 8443), ("10.0.0.5", 8443), ("::1", 8443)):
            with self.subTest(target=(host, port)):
                self.assertTrue(violations(fc.render(**KW, target_host=host, target_port=port)))

    def test_listener_is_the_parameter_not_a_constant(self):
        self.assertEqual(violations(fc.render(**KW, target_port=9443), listener=("127.0.0.1", 9443)), [])
        self.assertTrue(violations(fc.render(**KW, target_port=9443)))

    def test_frpc_https2http_plugin_fails(self):
        extra = '\n[proxies.plugin]\ntype = "https2http"\nlocalAddr = "127.0.0.1:8000"\ncrtPath = "a"\nkeyPath = "b"\n'
        self.assertTrue(any("plugin" in e for e in violations(fc.render(**KW, extra_proxy=extra))))

    def test_missing_trusted_ca_fails(self):
        self.assertTrue(any("trustedCaFile" in e for e in violations(fc.render(**KW, trusted_ca=False))))

    def test_wrong_type_second_proxy_foreign_domain_fail(self):
        self.assertTrue(violations(fc.render(**KW).replace('type = "https"', 'type = "tcp"')))
        self.assertTrue(violations(fc.render(**KW, extra_proxy='\n[[proxies]]\nname = "x"\ntype = "https"\nlocalPort = 8443\n')))
        self.assertTrue(violations(fc.render(**KW, custom_domains=["hb22222222.zoe.internal"])))
        self.assertTrue(violations(fc.render(**KW, custom_domains=["*.zoe.internal"])))

    def test_admin_api_and_includes_fail(self):
        self.assertTrue(violations(fc.render(**KW, extra_top='webServer.port = 7400\n')))
        self.assertTrue(violations(fc.render(**KW, extra_top='includes = ["/tmp/x.toml"]\n')))

    def test_login_must_keep_retrying(self):
        self.assertTrue(any("loginFailExit" in e for e in violations(fc.render(**KW).replace("loginFailExit = false\n", ""))))
        self.assertTrue(any("loginFailExit" in e for e in violations(fc.render(**KW).replace("loginFailExit = false", "loginFailExit = true"))))

    def test_garbage_fails(self):
        self.assertTrue(violations("not = [toml"))


if __name__ == "__main__":
    unittest.main()
