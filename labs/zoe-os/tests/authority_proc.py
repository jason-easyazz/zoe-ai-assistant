"""The authority daemon as its own process, with ONE kill point or ONE negative control armed (lab launcher for test_authority.py).

    python3 authority_proc.py --dir STATE  [OS_LAB_DIE_AT=after_intent|after_do|after_commit]  [OS_LAB_CONTROL=name]

``OS_LAB_DIE_AT`` SIGKILLs the whole kernel at one exact point of a ``list.add`` whose item is ``die``, a reminder write whose note is
``die``, an alarm write whose label is ``die``, or a snooze of 13 minutes:
  after_intent  the write-ahead intent is durable; the driver has not run
  after_do      the driver changed its rows; the receipt is not stored
  after_commit  the verified receipt is stored; the reply has not been sent
``OS_LAB_CONTROL`` removes exactly one guarantee, so a test can show it catches the loss. The controls live HERE, never in the daemon.
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent)]

import authority  # noqa: E402
from zoe_os.rows import DurableRows  # noqa: E402

DIE = os.environ.get("OS_LAB_DIE_AT", "")


def _die(when: str, args) -> None:
    if DIE == when and ("die" in (args.get("item"), args.get("note"), args.get("label")) or args.get("minutes") == 13):
        os.kill(os.getpid(), signal.SIGKILL)


def _wrap(cls, name, when, before=True):
    original = getattr(cls, name)

    def hooked(self, *a):
        args = a[1] if len(a) > 1 else {}
        if before:
            _die(when, args)
        out = original(self, *a)
        if not before:
            _die(when, args)
        return out
    setattr(cls, name, hooked)


_wrap(authority.DurableLists, "snapshot", "after_intent")       # the dispatcher snapshots after the intent, before the driver
_wrap(authority.DurableLists, "added", "after_do")              # the read-back: the rows changed, the receipt not yet stored
_wrap(DurableRows, "target", "after_intent")                    # the durable reminder rows: the snapshot names its row first
_wrap(DurableRows, "read_back", "after_do")
_orig_act = authority.AuthorityServer._op_act


def _act_then_die(self, conn, a):
    out = _orig_act(self, conn, a)
    _die("after_commit", a.get("args") or {})
    return out


authority.AuthorityServer._op_act = _act_then_die

CONTROLS = {
    "no_rebuild": lambda: setattr(authority.DurableLists, "rebuild", lambda self, person=None: 0),
    "no_rem_rebuild": lambda: setattr(DurableRows, "rebuild", lambda self: 0),
    "no_member_forget": lambda: setattr(authority.AuthorityServer, "_op_erase_member", _erase_without_forget),
}


def _erase_without_forget(self, conn, a):
    """The erase op with the forget step removed: the read-back must catch it (verified false)."""
    left = len(self.lists.rows.get(a["subject"], [])) + self._live_events(a["subject"])
    return {"erased": 0, "receipt_seq": None, "verified": left == 0, "known": True}


if os.environ.get("OS_LAB_CONTROL"):
    CONTROLS[os.environ["OS_LAB_CONTROL"]]()

if __name__ == "__main__":
    raise SystemExit(authority.main(sys.argv[1:]))
