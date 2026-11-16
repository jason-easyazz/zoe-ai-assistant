"""Make ``zoe_os`` importable for the lab's own tests (the directory name has a hyphen), and opt them into CI.

CI selection is by marker (``validate.yml`` runs ``... labs/zoe-os/tests tests/unit -m ci_safe``), so the lab's tests
get ``ci_safe`` here, in ONE place, instead of 32 hand-added ``pytestmark`` lines. Every test in this tree is
hosted-runner safe by construction: stdlib only, temp dirs, loopback-free Unix sockets, no model, no network, no
household data. The two that cannot run on a hosted runner are NOT dropped: the second-OS-user tests carry their
own ``skipif(geteuid() != 0)`` (a hosted runner is not root), so they show up as skipped with that reason. The
brain-lab-app Node test (``brain-lab-app/src/*.test.mjs``) is not Python and not in this lane (a follow-up).

WORKS ALONE (AGENTS.md rule 10, audit row K10): the autouse ``works_alone`` fixture makes every socket ``connect``,
``connect_ex`` and ``sendto`` to a non-loopback address raise ``NetworkRefused`` (Unix sockets and loopback still work), and
fails the test at teardown if it attempted one, even when the code swallowed the error. Limit: it patches THIS process only;
a child process the test spawns is not covered. ``tests/test_works_alone.py`` is the negative control.
"""
import ipaddress
import socket
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]


def pytest_collection_modifyitems(config, items):
    # This hook sees EVERY collected item in the session, so only touch this tree's own.
    for item in items:
        if HERE in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.ci_safe)


class NetworkRefused(OSError):
    """A lab test reached for a non-loopback address: the kernel must work with no network (rule 10)."""


def _loopback(address) -> bool:
    host = str(address[0]).strip("[]").lower().rstrip(".")
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False                       # a hostname would need DNS: that is the network too
    return ip.is_loopback or bool(getattr(ip, "ipv4_mapped", None) and ip.ipv4_mapped.is_loopback)


class Attempts(list):
    """Refused network attempts in one test; ``check`` (run at teardown) fails the test if there were any."""

    def check(self):
        if self:
            pytest.fail(f"a lab test tried to reach the network (works-alone rule 10): {list(self)}")


@pytest.fixture(autouse=True)
def works_alone(monkeypatch):
    attempts = Attempts()

    def guarded(real, addr_at):
        def call(self, *args):
            address = args[addr_at]
            if self.family != socket.AF_UNIX and not _loopback(address):
                attempts.append(f"{real.__name__} {address!r}")
                raise NetworkRefused(f"labs/zoe-os works alone: refused {real.__name__} to non-loopback {address!r}")
            return real(self, *args)
        return call

    for name, addr_at in (("connect", 0), ("connect_ex", 0), ("sendto", -1)):
        monkeypatch.setattr(socket.socket, name, guarded(getattr(socket.socket, name), addr_at))
    yield attempts
    attempts.check()
