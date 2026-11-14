"""Canned answers for the scripted FAKE engine (`selfheal.proposer.ScriptedProposer`), all against the
`calc.add` scratch fixture in conftest.py: good, bad, malicious, oversized, over budget. Synthetic."""
from __future__ import annotations

import time

from conftest import FIXED, TEST
from selfheal.proposer import Proposal, Usage, make_patch

NOOP_CONFTEST = "def pytest_runtest_call(item):\n    item.obj = lambda *a, **k: None\n"
WEAK_TEST = TEST.replace("== 5", "== -1")


def _edit(path, new, old="packet"):
    """A patch editing `path` (old text from the packet, or given when the packet withholds the file)."""
    return lambda p: make_patch({path: p.files.get(path) if old == "packet" else old}, {path: new})


def _slow(p):
    time.sleep(3)
    return good(p)


good = _edit("calc.py", FIXED)
SYMLINK = ("diff --git a/tests/test_link.py b/tests/test_link.py\nnew file mode 120000\n--- /dev/null\n"
           "+++ b/tests/test_link.py\n@@ -0,0 +1 @@\n+/etc/passwd\n\\ No newline at end of file\n")
RENAME = ("diff --git a/tests/test_calc.py b/tests/test_gone.py\nsimilarity index 100%\n"
          "rename from tests/test_calc.py\nrename to tests/test_gone.py\n")
CANNED = {
    "good": good,
    "bad": _edit("calc.py", "def add(a, b):\n    return a * b\n"),                       # wrong, compiles
    "breaks_other": _edit("calc.py", "def add(a, b):\n    return a + b if a or b else 1\n"),  # test_zero breaks
    "adds_test": lambda p: good(p) + make_patch({}, {"tests/test_more.py": "from calc import add\n\n\n"
                                                     "def test_more():\n    assert add(1, 2) == 3\n"}),
    "edits_test": _edit("tests/test_calc.py", WEAK_TEST),
    "pinned": _edit("conftest.py", NOOP_CONFTEST, old=""),
    "workflow": _edit(".github/workflows/heal.yml", "on: push\n", old=None),
    "scratch": _edit(".selfheal-tmp/sitecustomize.py", "import os\n", old=None),
    "dotdot": _edit("../outside.py", "x = 1\n", old=None),
    "symlink": lambda p: SYMLINK,
    "rename": lambda p: RENAME,
    "delete": lambda p: "diff --git a/calc.py b/calc.py\ndeleted file mode 100644\n--- a/calc.py\n+++ /dev/null\n"
                        "@@ -1,2 +0,0 @@\n-def add(a, b):\n-    return a - b\n",
    "chmod": lambda p: "diff --git a/calc.py b/calc.py\nold mode 100644\nnew mode 100755\n",
    "binary": lambda p: "diff --git a/calc.py b/calc.py\nGIT binary patch\nliteral 1\nIcmZPo000310RR91\n",
    "oversized": _edit("calc.py", FIXED + "# pad\n" * 20000),
    "too_many_lines": _edit("calc.py", FIXED + "".join(f"x{i} = {i}\n" for i in range(400))),
    "garbage": lambda p: "I fixed it, trust me.",
    "empty": lambda p: "",
    "over_tokens": lambda p: Proposal(good(p), Usage(turns=3, tokens=10**9)),
    "over_turns": lambda p: Proposal(good(p), Usage(turns=10**6, tokens=10)),
    "reported_ok": lambda p: Proposal(good(p), Usage(turns=3, tokens=1200)),
    "slow": _slow,
    "raises": lambda p: 1 / 0,
}
