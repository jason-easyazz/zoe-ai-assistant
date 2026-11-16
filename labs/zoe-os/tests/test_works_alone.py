"""Negative control for the works-alone guard in ``labs/zoe-os/conftest.py`` (AGENTS.md rule 10, audit row K10).

A connect to a public address is refused before any packet leaves (so this needs no network to pass, and passes with none);
loopback TCP and a Unix socket still work, which is what every kernel test uses."""
import os
import socket
import tempfile

import pytest

PUBLIC = ("9.9.9.9", 53)
WORKS_ALONE = "works alone"   # conftest.NetworkRefused (an OSError), matched without importing "conftest"


def test_a_connect_to_a_public_address_is_refused(works_alone):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(OSError, match=WORKS_ALONE):
        s.connect(PUBLIC)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(OSError, match=WORKS_ALONE):
        s.connect_ex(PUBLIC)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(OSError, match=WORKS_ALONE):
        s.connect(("example.com", 443))   # refused before any DNS lookup
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s, pytest.raises(OSError, match=WORKS_ALONE):
        s.sendto(b"x", PUBLIC)
    assert len(works_alone) == 4           # each attempt is recorded; teardown would fail the test on any of them
    works_alone.clear()                    # this test expects them; any other test with an attempt fails


def test_a_swallowed_attempt_is_still_recorded(works_alone):
    try:
        socket.create_connection(PUBLIC, timeout=1)
    except OSError:
        pass                               # code that hides the failure does not hide the attempt
    assert works_alone and works_alone[0].startswith("connect")
    with pytest.raises(pytest.fail.Exception, match="works-alone"):
        works_alone.check()                # what the fixture's teardown runs: the swallowed attempt fails the test
    works_alone.clear()


def test_loopback_and_unix_sockets_still_work(works_alone):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        with socket.create_connection(srv.getsockname(), timeout=2):
            pass
    d = tempfile.mkdtemp(prefix="zos-", dir="/tmp")
    path = os.path.join(d, "s")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as srv:
            srv.bind(path)
            srv.listen(1)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
                c.connect(path)
    finally:
        os.unlink(path)
        os.rmdir(d)
    assert works_alone == []


def test_a_hostname_lookup_is_refused_before_any_socket_call(works_alone):
    """``socket.create_connection`` resolves the name first; the resolver is the network, so the lookup itself is refused and recorded
    (a numeric address never reaches the resolver, which is why the first control could not see this)."""
    import socket

    for call in (lambda: socket.create_connection(("example.invalid", 80), timeout=0.2),
                 lambda: socket.getaddrinfo("example.invalid", 80),
                 lambda: socket.gethostbyname("example.invalid")):
        with pytest.raises(OSError):                  # NetworkRefused is an OSError: a caller that catches OSError swallows it
            call()
    assert len(works_alone) == 3 and all("example.invalid" in a for a in works_alone)
    works_alone.clear()                               # consumed here; teardown would (rightly) fail the test otherwise


def test_localhost_still_resolves(works_alone):
    import socket

    assert socket.getaddrinfo("localhost", 0) and socket.getaddrinfo("127.0.0.1", 0) and socket.getaddrinfo(None, 0)
    assert works_alone == []
