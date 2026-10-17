"""Unit tests for the runner's safety rails (night window, memory-size parsing). No Docker."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import run_lab  # noqa: E402

T = datetime.time


class NightWindowTests(unittest.TestCase):
    def test_inside_the_window_is_refused(self):
        for t in (T(1, 50), T(3, 0), T(4, 55)):
            with self.subTest(t=t):
                self.assertTrue(run_lab.in_night_window(t))
                self.assertTrue(run_lab.in_night_window(t, starting=True))

    def test_outside_the_window_is_allowed(self):
        for t in (T(1, 49), T(4, 56), T(10, 0), T(23, 59), T(0, 5)):
            with self.subTest(t=t):
                self.assertFalse(run_lab.in_night_window(t))

    def test_starting_keeps_a_margin_before_the_window(self):
        self.assertTrue(run_lab.in_night_window(T(1, 40), starting=True))
        self.assertFalse(run_lab.in_night_window(T(1, 40)))  # a run already in progress is stopped only at 01:50
        self.assertFalse(run_lab.in_night_window(T(1, 30), starting=True))


class ParsingTests(unittest.TestCase):
    def test_parse_mib(self):
        self.assertAlmostEqual(run_lab.parse_mib("12.5MiB"), 12.5)
        self.assertAlmostEqual(run_lab.parse_mib("2048KiB"), 2.0)
        self.assertAlmostEqual(run_lab.parse_mib("1.5GiB"), 1536.0)
        self.assertEqual(run_lab.parse_mib("garbage"), 0.0)

    def test_gate_constant_is_the_owner_rule(self):
        self.assertEqual(run_lab.MIN_MEM_MB, 1500)

    def test_labels_scope_cleanup_to_the_lab(self):
        self.assertEqual(run_lab.LABEL, "zoe.relay-lab=1")
        self.assertTrue(run_lab.NET.startswith("zoe-relay-lab"))


if __name__ == "__main__":
    unittest.main()
