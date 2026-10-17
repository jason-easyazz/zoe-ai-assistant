"""Unit tests for relay/plugin.py (the session-master stub). Hand-run: python3.12 -m unittest discover -s labs/relay/tests -t labs/relay"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "relay"))
import plugin  # noqa: E402
plugin._log = lambda *a, **k: None  # keep test output quiet

HID_A, HID_B = "ha11111111", "hb22222222"
SEC_A, SEC_B = "secret-a", "secret-b"


def make_policy(tmp, **kw):
    reg = os.path.join(tmp, "households.json")
    with open(reg, "w") as f:
        json.dump({"households": {
            HID_A: {"secret_sha256": plugin.sha256_hex(SEC_A), "active": True},
            HID_B: {"secret_sha256": plugin.sha256_hex(SEC_B), "active": True}}}, f)
    return plugin.Policy(plugin.Registry(reg), **kw), reg


def user(hid=HID_A, secret=SEC_A):
    return {"user": hid, "metas": {"hid": hid, "secret": secret}, "run_id": "r"}


def proxy(hid=HID_A, **over):
    c = {"user": user(hid, SEC_A if hid == HID_A else SEC_B), "proxy_name": hid + ".https", "proxy_type": "https",
         "custom_domains": [hid + ".zoe.internal"]}
    c.update(over)
    return c


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p, _ = make_policy(self.tmp)

    def login(self, hid=HID_A, secret=SEC_A, **over):
        c = {"user": hid, "metas": {"hid": hid, "secret": secret}}
        c.update(over)
        return self.p.handle("Login", c)

    def test_good_login_accepted(self):
        self.assertFalse(self.login()["reject"])

    def test_wrong_secret_unknown_hid_and_malformed_rejected(self):
        self.assertTrue(self.login(secret="nope")["reject"])
        self.assertTrue(self.login(hid="hzzzzzzzzz")["reject"])
        self.assertTrue(self.login(hid="../etc/passwd")["reject"])
        self.assertTrue(self.p.handle("Login", {"user": HID_A})["reject"])  # no metas at all
        self.assertTrue(self.p.handle("Login", "not a dict")["reject"])

    def test_secret_of_another_household_rejected(self):
        self.assertTrue(self.login(hid=HID_A, secret=SEC_B)["reject"])

    def test_user_must_equal_hid_and_pool_is_capped(self):
        self.assertTrue(self.login(user=HID_B)["reject"])
        self.assertTrue(self.login(pool_count=500)["reject"])

    def test_reason_does_not_reveal_which_part_was_wrong(self):
        self.assertEqual(self.login(secret="x")["reject_reason"], self.login(hid="hzzzzzzzzz")["reject_reason"])

    def test_suspended_household_cannot_login(self):
        _, reg = make_policy(self.tmp)
        rows = json.load(open(reg))
        rows["households"][HID_A]["active"] = False
        json.dump(rows, open(reg, "w"))
        os.utime(reg, ns=(1, 2_000_000_000))  # force a distinct mtime
        self.assertTrue(self.login()["reject"])

    def test_edit_that_keeps_the_timestamp_is_still_seen(self):
        reg = os.path.join(self.tmp, "households.json")
        self.assertFalse(self.login()["reject"])  # loads and caches the registry
        st = os.stat(reg)
        with open(reg) as f:
            rows = json.load(f)
        rows["households"][HID_A]["active"] = False
        rows["households"][HID_A]["pad"] = "x" * 10  # different size
        with open(reg, "w") as f:
            json.dump(rows, f)
        os.utime(reg, ns=(st.st_atime_ns, st.st_mtime_ns))  # restore the old mtime: only size and ctime differ
        self.assertTrue(self.login()["reject"])

    def test_missing_registry_fails_closed(self):
        os.unlink(os.path.join(self.tmp, "households.json"))
        with self.assertRaises(FileNotFoundError):  # the HTTP layer turns this into a 500, which frps treats as a refusal
            self.login()


class NewProxyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p, _ = make_policy(self.tmp, bandwidth_limit="256KB")

    def test_own_https_proxy_accepted_and_rewritten(self):
        r = self.p.handle("NewProxy", proxy(subdomain="evil", group="g", group_key="k", locations=["/x"], headers={"a": "b"},
                                            metas={"x": "y"}, use_compression=True, bandwidth_limit="10MB", bandwidth_limit_mode="client"))
        self.assertFalse(r["reject"])
        self.assertFalse(r["unchange"])
        c = r["content"]
        self.assertEqual(c["custom_domains"], [HID_A + ".zoe.internal"])
        self.assertEqual((c["subdomain"], c["group"], c["group_key"], c["locations"], c["headers"], c["metas"]), ("", "", "", [], {}, {}))
        self.assertEqual((c["bandwidth_limit"], c["bandwidth_limit_mode"]), ("256KB", "server"))
        self.assertFalse(c["use_compression"])
        self.assertEqual(c["user"]["metas"]["hid"], HID_A)  # identity untouched

    def test_case_and_trailing_dot_variants_of_own_name_are_canonicalised(self):
        r = self.p.handle("NewProxy", proxy(custom_domains=[HID_A.upper() + ".Zoe.Internal."]))
        self.assertEqual(r["content"]["custom_domains"], [HID_A + ".zoe.internal"])

    def test_foreign_wildcard_and_arbitrary_names_rejected(self):
        for names in ([HID_B + ".zoe.internal"], ["*.zoe.internal"], ["*"], ["bank.example.com"],
                      [HID_A + ".zoe.internal", HID_B + ".zoe.internal"], [HID_A + ".zoe.internal.evil.com"]):
            with self.subTest(names=names):
                self.assertTrue(self.p.handle("NewProxy", proxy(custom_domains=names))["reject"])

    def test_every_type_but_https_rejected(self):
        for t in ("tcp", "udp", "http", "stcp", "xtcp", "sudp", "tcpmux", "HTTPS", ""):
            with self.subTest(t=t):
                self.assertTrue(self.p.handle("NewProxy", proxy(proxy_type=t))["reject"])

    def test_proxy_name_must_be_hid_dot_https(self):
        self.assertTrue(self.p.handle("NewProxy", proxy(proxy_name=HID_B + ".https"))["reject"])
        self.assertTrue(self.p.handle("NewProxy", proxy(proxy_name="https"))["reject"])

    def test_unauthenticated_user_block_rejected(self):
        c = proxy()
        c["user"]["metas"]["secret"] = "forged"
        self.assertTrue(self.p.handle("NewProxy", c)["reject"])
        c = proxy()
        c["user"]["user"] = HID_B
        self.assertTrue(self.p.handle("NewProxy", c)["reject"])

    def test_lab_toggle_is_ignored_without_lab_mode(self):
        tog = os.path.join(self.tmp, "t.json")
        json.dump({"disable_overwrite": ["bandwidth", "domains"]}, open(tog, "w"))
        p, _ = make_policy(self.tmp, bandwidth_limit="256KB", toggles_path=tog, lab_mode=False)
        c = p.handle("NewProxy", proxy(bandwidth_limit="9MB", bandwidth_limit_mode="client"))["content"]
        self.assertEqual((c["bandwidth_limit"], c["bandwidth_limit_mode"]), ("256KB", "server"))

    def test_negative_control_toggle_in_lab_mode_disables_the_overwrite(self):
        tog = os.path.join(self.tmp, "t.json")
        json.dump({"disable_overwrite": ["bandwidth"]}, open(tog, "w"))
        p, _ = make_policy(self.tmp, bandwidth_limit="256KB", toggles_path=tog, lab_mode=True)
        c = p.handle("NewProxy", proxy(bandwidth_limit="9MB", bandwidth_limit_mode="client"))["content"]
        self.assertEqual((c["bandwidth_limit"], c["bandwidth_limit_mode"]), ("9MB", "client"))  # the overwrite is what enforces the cap


class NewUserConnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.t = [0.0]
        self.p, self.reg = make_policy(self.tmp, rate_max=3, rate_window_s=10, clock=lambda: self.t[0])

    def conn(self, hid=HID_A, **over):
        c = {"user": user(hid, SEC_A if hid == HID_A else SEC_B), "proxy_name": hid + ".https", "proxy_type": "https",
             "remote_addr": "1.2.3.4:5"}
        c.update(over)
        return self.p.handle("NewUserConn", c)

    def test_connection_must_belong_to_the_authenticated_household(self):
        self.assertFalse(self.conn()["reject"])
        bad = {
            "A's identity with B's hid in the metas": {"user": {"user": HID_A, "metas": {"hid": HID_B, "secret": SEC_B}}},
            "wrong secret": {"user": user(HID_A, "nope")},
            "proxy of another household": {"proxy_name": HID_B + ".https"},
            "not an https proxy": {"proxy_type": "tcp"},
        }
        for label, over in bad.items():
            with self.subTest(bad=label):
                self.assertTrue(self.conn(**over)["reject"])
        self.assertEqual([self.conn(HID_B)["reject"] for _ in range(4)], [False, False, False, True])  # B's own counter, untouched by A's bad tries

    def test_rate_cap_is_per_household_and_drains(self):
        self.assertEqual([self.conn()["reject"] for _ in range(5)], [False, False, False, True, True])
        self.assertFalse(self.conn(HID_B)["reject"])  # B has its own counter
        self.t[0] = 11.0
        self.assertFalse(self.conn()["reject"])  # window drained

    def test_suspended_household_refused_and_missing_hid_refused(self):
        rows = json.load(open(self.reg))
        rows["households"][HID_A]["active"] = False
        json.dump(rows, open(self.reg, "w"))
        os.utime(self.reg, ns=(1, 3_000_000_000))
        self.assertTrue(self.conn()["reject"])
        self.assertFalse(self.conn(HID_B)["reject"])
        self.assertTrue(self.p.handle("NewUserConn", {"user": {"metas": {}}})["reject"])

    def test_unknown_operation_is_refused(self):
        self.assertTrue(self.p.handle("NewWorkConn", {})["reject"])


if __name__ == "__main__":
    unittest.main()
