"""Panel daemon: VERIFY_SSL=false must not write a warning per request.

2026-10-04 log review: the Pi's daemon runs VERIFY_SSL=false (self-signed
Jetson cert), and urllib3 registers SecurityWarning as "always", so the 5 s
announce poll alone wrote an InsecureRequestWarning (two stderr lines) every
poll - 3,343 of the 4h45m's 8,158 journal lines (~17,000 a day). The fix keeps
verification OFF (the operator's choice) and says so once.

Driven against the REAL module import with a stubbed pyaudio, like the other
voice-daemon rigs: no mic, no network, no TLS server.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import warnings
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

urllib3 = pytest.importorskip("urllib3")

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"
_NOTICE = "TLS certificate verification is OFF"


def _load_daemon(name: str):
    stubs = {n: MagicMock() for n in ("pyaudio",) if n not in sys.modules}
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(name, _DAEMON_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev


def _emit_like_urllib3() -> list:
    """Raise InsecureRequestWarning exactly as urllib3 does per request, and
    return what the warnings machinery let through."""
    with warnings.catch_warnings(record=True) as caught:
        # Re-assert the baseline inside the recorder WITHOUT touching an
        # "ignore" filter the daemon installed: append, never insert.
        warnings.simplefilter("always", urllib3.exceptions.SecurityWarning, append=True)
        for _ in range(3):  # per request, not once
            warnings.warn("Unverified HTTPS request", urllib3.exceptions.InsecureRequestWarning)
    return [w for w in caught if issubclass(w.category, urllib3.exceptions.InsecureRequestWarning)]


@pytest.fixture
def isolated_warnings():
    """Each case mutates the process-wide warning filters on import: restore them."""
    with warnings.catch_warnings():
        # urllib3's own import-time registration (SecurityWarning -> always) is
        # the behaviour that produced the flood; make it explicit for the test.
        warnings.simplefilter("always", urllib3.exceptions.SecurityWarning)
        yield


def test_verify_off_silences_the_per_request_warning_and_says_so_once(
        monkeypatch, caplog, isolated_warnings):
    monkeypatch.setenv("VERIFY_SSL", "false")
    with caplog.at_level(logging.INFO):
        daemon = _load_daemon("zoe_voice_daemon_tls_off")
    assert daemon.VERIFY_SSL is False
    assert daemon._TLS_WARNINGS_SILENCED is True
    assert _emit_like_urllib3() == [], "every request would still write a traceback to the journal"
    notices = [r for r in caplog.records if _NOTICE in r.getMessage()]
    assert len(notices) == 1 and notices[0].levelno == logging.INFO


def test_verify_on_keeps_the_warning_machinery_untouched(monkeypatch, caplog, isolated_warnings):
    """Negative control: with verification ON nothing is silenced, nothing is
    announced - the guard must be gated on VERIFY_SSL, not unconditional."""
    monkeypatch.setenv("VERIFY_SSL", "true")
    with caplog.at_level(logging.INFO):
        daemon = _load_daemon("zoe_voice_daemon_tls_on")
    assert daemon.VERIFY_SSL is True
    assert daemon._TLS_WARNINGS_SILENCED is False
    assert len(_emit_like_urllib3()) == 3
    assert not [r for r in caplog.records if _NOTICE in r.getMessage()]


def test_silencing_the_notice_does_not_switch_verification(monkeypatch, isolated_warnings):
    """The flag the requests calls pass is still the operator's value."""
    monkeypatch.setenv("VERIFY_SSL", "false")
    assert _load_daemon("zoe_voice_daemon_tls_flag_off").VERIFY_SSL is False
    monkeypatch.setenv("VERIFY_SSL", "true")
    assert _load_daemon("zoe_voice_daemon_tls_flag_on").VERIFY_SSL is True


def test_a_broken_urllib3_never_costs_the_daemon(monkeypatch, isolated_warnings):
    monkeypatch.setenv("VERIFY_SSL", "true")
    daemon = _load_daemon("zoe_voice_daemon_tls_broken")
    monkeypatch.setitem(sys.modules, "urllib3", None)  # `import urllib3` -> ImportError
    assert daemon._silence_insecure_request_warnings(False) is False
