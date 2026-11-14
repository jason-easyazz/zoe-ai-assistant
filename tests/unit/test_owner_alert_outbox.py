"""The shared owner-alert helper (``scripts/maintenance/zoe_crash_loop_watch.deliver`` / ``drain``).

2026-10-28..11-03: nine owner alerts were raised, failed with HTTP 401 and were simply lost - nothing retried them and
nothing said alerting itself was broken. These pin: a failing transport QUEUES (durable outbox), a later success drains
it ONCE, nothing is sent twice, an auth failure records "alerting is broken" (and a delivery heals it), and the outbox
holds only a code, timestamps, counts and the ops text. The transport is always injected: no network, no live paths.
"""
from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "maintenance" / "zoe_crash_loop_watch.py"
T0 = 1_790_000_000.0


def _load():
    spec = importlib.util.spec_from_file_location("zoe_crash_loop_watch_outbox", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(m)
    return m


mod = _load()


class Transport:
    """Plays a script of outcome classes; records every text it was asked to send."""

    def __init__(self, *script: str):
        self.script = list(script)
        self.sent: list[str] = []

    def __call__(self, text: str) -> str:
        self.sent.append(text)
        return self.script.pop(0) if self.script else "delivered"


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "OUTBOX_PATH", str(tmp_path / "alert-outbox.json"))
    lines: list[str] = []
    sleeps: list[float] = []
    return {"path": tmp_path / "alert-outbox.json", "log": lines.append, "lines": lines,
            "sleep": sleeps.append, "sleeps": sleeps}


def _read(box) -> dict:
    return json.loads(box["path"].read_text())


def _events(box, event: str) -> list[str]:
    return [ln for ln in box["lines"] if f"event={event} " in ln]


def test_a_failing_transport_queues_and_a_later_success_drains_it_once(box):
    down = Transport("network", "network", "network")
    out = mod.deliver("kv_miss_first", "ops text", transport=down, now=T0, sleep=box["sleep"], log=box["log"])
    assert out == ("queued", "network")
    assert len(down.sent) == 3 and box["sleeps"] == [2.0, 6.0], "bounded inline retries with backoff"
    assert [e["code"] for e in _read(box)["entries"]] == ["kv_miss_first"]

    up = Transport("delivered")
    assert mod.drain(transport=up, now=T0 + 30, log=box["log"]) == {}, "not due yet: backoff holds"
    assert mod.drain(transport=up, now=T0 + mod.RETRY_BASE_S * 8, log=box["log"]) == {"kv_miss_first": ("delivered", "-")}
    assert up.sent == ["ops text"]
    assert _read(box)["entries"] == []
    again = Transport()
    assert mod.drain(transport=again, now=T0 + 99999, log=box["log"]) == {} and again.sent == [], "never sent twice"
    t = _read(box)["totals"]
    assert t["raised"] == 1 and t["delivered"] == 1 and t["queued"] == 1


def test_a_delivered_alert_is_not_kept(box):
    tr = Transport("delivered")
    assert mod.deliver("health", "x", transport=tr, now=T0, log=box["log"]) == ("delivered", "-")
    assert _read(box)["entries"] == [] and len(tr.sent) == 1
    assert _events(box, "raised") and _events(box, "delivered")


def test_auth_failure_records_broken_is_not_retried_inline_and_a_delivery_heals_it(box):
    tr = Transport("auth")
    assert mod.deliver("backup", "x", transport=tr, now=T0, sleep=box["sleep"], log=box["log"]) == ("queued", "auth")
    assert len(tr.sent) == 1 and box["sleeps"] == [], "a refused credential does not get better in 8 seconds"
    b = mod.alerting_broken()
    assert b and b["reason"] == "auth" and b["since"] == T0
    assert _events(box, "broken") and "reason=auth" in _events(box, "broken")[0]
    mod.drain(transport=Transport("delivered"), now=T0 + 3600, log=box["log"])
    assert mod.alerting_broken() is None and _events(box, "healed")


def test_a_new_alert_with_a_pending_code_supersedes_it_and_is_sent_once(box):
    mod.deliver("health", "old", transport=Transport("auth"), now=T0, log=box["log"])
    tr = Transport("delivered")
    assert mod.deliver("health", "new", transport=tr, now=T0 + 10, log=box["log"])[0] == "delivered"
    assert tr.sent == ["new"] and _read(box)["entries"] == []


def test_uncertain_is_never_resent(box):
    tr = Transport("uncertain")
    assert mod.deliver("health", "x", transport=tr, now=T0, sleep=box["sleep"], log=box["log"]) == ("uncertain", "-")
    assert len(tr.sent) == 1 and _read(box)["entries"] == []


def test_an_old_undeliverable_alert_gives_up_loudly(box):
    mod.deliver("health", "x", transport=Transport("auth"), now=T0, log=box["log"])
    out = mod.drain(transport=Transport("auth"), now=T0 + mod.GIVE_UP_S + 1, log=box["log"])
    assert out == {"health": ("gave_up", "auth")}
    assert _read(box)["entries"] == [] and _read(box)["totals"]["gave_up"] == 1 and _events(box, "gave_up")


def test_the_outbox_holds_only_code_times_counts_and_the_text(box):
    mod.deliver("Bad Code!", "ops text", transport=Transport("network", "network", "network"), now=T0,
                sleep=box["sleep"], log=box["log"])
    st = _read(box)
    assert set(st) <= {"entries", "totals", "broken", "spells"}
    (e,) = st["entries"]
    assert set(e) == {"code", "text", "first_ts", "last_raised_ts", "raised", "tries", "next_ts", "rev"}
    assert e["code"] == "other", "a code outside the fixed vocabulary shape is never stored verbatim"
    for ln in box["lines"]:
        assert ln.startswith("ALERT_DELIVERY event=") and "ops text" not in ln, "markers carry counts only"


def test_the_live_outbox_is_unreachable_under_pytest():
    tr = Transport()
    assert mod.deliver("health", "x", transport=tr) == ("failed", "test_guard") and tr.sent == []
    assert mod.drain(transport=tr) == {}


def test_http_401_from_telegram_is_classified_auth(tmp_path, monkeypatch):
    envf = tmp_path / "tg.env"
    envf.write_text("TELEGRAM_BOT_TOKEN=file-token\nZOE_ALERT_TELEGRAM_CHAT_IDS=42\n")
    monkeypatch.setattr(mod, "TELEGRAM_ENV", str(envf))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ZOE_ALERT_TELEGRAM_CHAT_IDS", raising=False)

    def refuse(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))

    monkeypatch.setattr(mod.urllib.request, "urlopen", refuse)
    lines: list[str] = []
    assert mod._telegram_result("x", report=lines.append) == "auth"
    assert lines and "file-token" not in " ".join(lines), "the token never reaches a report line"

    def unreachable(req, timeout=None):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(mod.urllib.request, "urlopen", unreachable)
    assert mod._telegram_result("x", report=lines.append) == "network"
    assert mod._telegram("x", report=lines.append) is False


# ── durability and concurrency: the lock is never held across a send ──

def test_the_entry_is_durable_before_the_send_and_the_lock_is_free_during_it(box):
    seen = {}

    def transport(text):
        seen["on_disk"] = [e["code"] for e in _read(box)["entries"]]
        with mod._outbox_locked():                  # would deadlock (flock is per open file) if the sender held it
            seen["lock_free"] = True
        return "delivered"

    assert mod.deliver("health", "x", transport=transport, now=T0, log=box["log"])[0] == "delivered"
    assert seen == {"on_disk": ["health"], "lock_free": True}


def test_a_transport_that_raises_leaves_the_entry_and_the_next_drain_sends_it_once(box):
    def boom(text):
        raise RuntimeError("socket blew up")

    assert mod.deliver("health", "x", transport=boom, now=T0, sleep=box["sleep"], log=box["log"])[0] == "queued"
    assert [e["code"] for e in _read(box)["entries"]] == ["health"]
    up = Transport()
    mod.drain(transport=up, now=T0 + 99999, log=box["log"])
    mod.drain(transport=up, now=T0 + 99999 + 1, log=box["log"])
    assert up.sent == ["x"]


def test_a_sender_killed_mid_send_leaves_a_claimed_entry_that_a_later_drain_sends_exactly_once(box):
    def killed(text):
        raise SystemExit(137)

    with pytest.raises(SystemExit):
        mod.deliver("crash_loop", "x", transport=killed, now=T0, log=box["log"])
    (e,) = _read(box)["entries"]
    assert e["code"] == "crash_loop" and e.get("claim"), "durable, and claimed by the dead sender"
    up = Transport()
    assert mod.drain(transport=up, now=T0 + 10, log=box["log"]) == {} and up.sent == [], "lease not expired yet"
    assert mod.drain(transport=up, now=T0 + mod.CLAIM_LEASE_S + 1, log=box["log"]) == {"crash_loop": ("delivered", "-")}
    mod.drain(transport=up, now=T0 + mod.CLAIM_LEASE_S + 2, log=box["log"])
    assert up.sent == ["x"] and _read(box)["entries"] == []


def test_two_interleaved_senders_keep_each_others_entries(box):
    """Sender A is mid-send (lock released) when sender B raises and fails; A's write-back must re-read, not overwrite."""
    def b_transport(text):
        return "network"

    def a_transport(text):
        if text == "a":
            out = mod.deliver("backup", "b", transport=b_transport, now=T0 + 1, sleep=box["sleep"], log=box["log"])
            assert out[0] == "queued"
        return "delivered"

    assert mod.deliver("health", "a", transport=a_transport, now=T0, log=box["log"]) == ("delivered", "-")
    st = _read(box)
    assert [e["code"] for e in st["entries"]] == ["backup"], "B's held alert survived A's write-back"
    assert st["totals"]["raised"] == 2 and st["totals"]["delivered"] == 1


def test_a_superseded_entry_whose_old_text_was_delivered_still_sends_the_new_text(box):
    def a_transport(text):
        if text == "old":
            mod.deliver("health", "new", transport=Transport(), now=T0 + 1, log=box["log"])   # in flight: not sent twice
        return "delivered"

    mod.deliver("health", "old", transport=a_transport, now=T0, log=box["log"])
    up = Transport()
    mod.drain(transport=up, now=T0 + 2, log=box["log"])
    assert up.sent == ["new"] and _read(box)["entries"] == []


def test_the_outbox_file_is_0600_whatever_the_umask(box):
    import os
    import stat

    old = os.umask(0)
    try:
        mod.deliver("health", "x", transport=Transport("network", "network", "network"), now=T0, sleep=box["sleep"],
                    log=box["log"])
    finally:
        os.umask(old)
    assert stat.S_IMODE(box["path"].stat().st_mode) == 0o600


def test_broken_spells_are_recorded_and_kept_after_heal(box):
    mod.deliver("health", "x", transport=Transport("auth"), now=T0, log=box["log"])
    assert mod.alerting_spells() == [{"since": T0, "reason": "auth", "healed": None}]
    mod.deliver("health", "y", transport=Transport("auth"), now=T0 + 5, log=box["log"])
    assert len(mod.alerting_spells()) == 1, "one spell while it stays broken"
    mod.drain(transport=Transport("delivered"), now=T0 + 99999, log=box["log"])
    assert mod.alerting_broken() is None
    assert mod.alerting_spells() == [{"since": T0, "reason": "auth", "healed": T0 + 99999}]


# ── review round 2 (PR 2458) ──────────────────────────────────────────────────────────────────────────────────────
def test_every_recipient_is_sent_to_and_a_partial_delivery_is_counted(tmp_path, monkeypatch):
    """The first HTTP 200 used to end the loop, so the other recipients never heard. All are tried; delivered = at least
    one got it; a recipient that did not is counted, never silent, and nobody who has it is sent it twice."""
    env = tmp_path / "bot.env"
    env.write_text("TELEGRAM_BOT_TOKEN=file-token\nZOE_ALERT_TELEGRAM_CHAT_IDS=101,102,103\n")
    monkeypatch.setattr(mod, "TELEGRAM_ENV", str(env))
    monkeypatch.delenv("ZOE_ALERT_TELEGRAM_CHAT_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    asked: list[str] = []

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=0):
        chat = dict(p.split("=") for p in req.data.decode().split("&"))["chat_id"]
        asked.append(chat)
        if chat == "102":
            raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b""))
        return Resp()

    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    assert mod._telegram_all("x", report=lambda _l: None) == ("delivered", 2, 1)
    assert asked == ["101", "102", "103"]                       # nobody is skipped after the first success
    assert mod._telegram_result("x", report=lambda _l: None) == "delivered"


def test_a_partial_delivery_is_marked_and_totalled_and_not_resent(box):
    lines: list[str] = []
    calls: list[str] = []

    def transport(text):
        calls.append(text)
        return ("delivered", 1, 2)

    assert mod.deliver("health", "one", transport=transport, now=T0, sleep=lambda _s: None, log=lines.append)[0] == "delivered"
    st = mod.outbox_status()
    assert st["pending"] == 0 and st["totals"].get("partial") == 1 and st["totals"].get("partial_recipients") == 2
    assert any("event=partial" in ln and "failed_recipients_2" in ln for ln in lines)
    assert mod.drain(transport=transport, now=T0 + 9999, sleep=lambda _s: None, log=lines.append) == {}
    assert calls == ["one"]                                     # the ones who have it never get it twice


def test_all_recipients_failing_is_not_delivered(box):
    out = mod.deliver("health", "one", transport=lambda _t: ("network", 0, 2), now=T0, sleep=lambda _s: None, log=lambda _l: None)
    assert out[0] == "queued" and mod.outbox_status()["pending"] == 1 and not mod.outbox_status()["totals"].get("partial")


def test_a_recovery_never_overtakes_its_outage_alert_and_other_units_are_not_blocked(box):
    """The loop alert is in backoff when the recovery is raised: the recovery must wait for it. Another unit's alert
    is not held up by that."""
    quiet = dict(sleep=lambda _s: None, log=lambda _l: None)
    down = Transport("auth")
    assert mod.deliver("unit_crash_loop.kokoro-tts", "alpha down", transport=down, now=T0, **quiet)[0] == "queued"
    sent: list[str] = []

    def ok(text):
        sent.append(text)
        return "delivered"

    # a drain BEFORE the loop alert is due again: the recovery (due now) and nothing of that family may go
    box_path = Path(mod.OUTBOX_PATH)
    data = json.loads(box_path.read_text())
    data["entries"].append({"code": "unit_recovered.kokoro-tts", "text": "alpha back", "first_ts": T0 + 1, "last_raised_ts": T0 + 1,
                            "raised": 1, "tries": 0, "next_ts": T0 + 1, "rev": 0})
    data["entries"].append({"code": "unit_crash_loop.llama-server", "text": "beta down", "first_ts": T0 + 2, "last_raised_ts": T0 + 2,
                            "raised": 1, "tries": 0, "next_ts": T0 + 2, "rev": 0})
    box_path.write_text(json.dumps(data))
    mod.drain(transport=ok, now=T0 + 5, **quiet)
    assert sent == ["beta down"]                                 # alpha's recovery waited; beta was not blocked
    mod.drain(transport=ok, now=T0 + 99999, **quiet)
    assert sent == ["beta down", "alpha down", "alpha back"]     # the outage first, then its recovery
    assert mod._family("crash_loop") == mod._family("recovered") == "main"
    assert mod._family("unit_crash_loop.kokoro-tts") == mod._family("unit_recovered.kokoro-tts") != mod._family("unit_recovered.llama-server")
    assert mod._family("health") == "health"


# ── review round 3 (PR 2458) ──────────────────────────────────────────────────────────────────────────────────────
def test_an_alert_that_cannot_be_stored_is_never_sent(box, monkeypatch):
    """Durable before any send is the contract: if the outbox cannot be written, nothing goes out (an unrecorded send
    can be neither retried nor counted), the failure is loud, and the caller keeps its own last resort."""
    def boom(_box):
        raise OSError("disk full")

    monkeypatch.setattr(mod, "_write_outbox", boom)
    lines: list[str] = []
    t = Transport()
    assert mod.deliver("health", "one", transport=t, now=T0, sleep=lambda _s: None, log=lines.append) == ("failed", "outbox_write")
    assert t.sent == []
    assert any("event=failed" in ln and "reason=outbox_write" in ln for ln in lines)


def test_only_a_reply_timeout_is_uncertain_a_connect_timeout_is_retried(tmp_path, monkeypatch):
    """urllib wraps in URLError only what fails BEFORE the request is handed over (DNS, refused, a connect timeout): none
    of it can have been delivered, so it is "network" and stays in the outbox. A timeout while waiting for the reply is
    raised bare and is the only "uncertain" (never re-sent: it may have been delivered)."""
    import socket

    env = tmp_path / "bot.env"
    env.write_text("TELEGRAM_BOT_TOKEN=file-token\nZOE_ALERT_TELEGRAM_CHAT_IDS=101\n")
    monkeypatch.setattr(mod, "TELEGRAM_ENV", str(env))
    monkeypatch.delenv("ZOE_ALERT_TELEGRAM_CHAT_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    for reason in (socket.timeout("timed out"), TimeoutError("timed out"), ConnectionRefusedError("refused"),
                   socket.gaierror("no such host")):
        def wrapped(req, timeout=0, _r=reason):
            raise urllib.error.URLError(_r)

        monkeypatch.setattr(mod.urllib.request, "urlopen", wrapped)
        assert mod._telegram_all("x", report=lambda _l: None) == ("network", 0, 1), reason

    def reply_lost(req, timeout=0):
        raise socket.timeout("timed out")           # bare: getresponse() timed out after the request went out

    monkeypatch.setattr(mod.urllib.request, "urlopen", reply_lost)
    assert mod._telegram_all("x", report=lambda _l: None) == ("uncertain", 0, 1)


def test_a_real_connect_timeout_keeps_the_alert_in_the_outbox(box, tmp_path, monkeypatch):
    """The real urlopen with the socket layer timing out at connect: the alert was never sent, so it must stay queued."""
    import socket

    env = tmp_path / "bot.env"
    env.write_text("TELEGRAM_BOT_TOKEN=file-token\nZOE_ALERT_TELEGRAM_CHAT_IDS=101\n")
    monkeypatch.setattr(mod, "TELEGRAM_ENV", str(env))
    monkeypatch.delenv("ZOE_ALERT_TELEGRAM_CHAT_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    for var in ("https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)

    def no_connect(*_a, **_k):
        raise socket.timeout("timed out")

    monkeypatch.setattr(socket, "create_connection", no_connect)
    assert mod._telegram_all("x", report=lambda _l: None) == ("network", 0, 1)
    out = mod.deliver("crash_loop", "x", now=T0, sleep=box["sleep"], log=box["log"])
    assert out[0] not in ("delivered", "uncertain"), out
    assert "crash_loop" in Path(mod.OUTBOX_PATH).read_text()   # still queued for the next drain

def test_the_lease_outlives_the_longest_possible_send_and_recipients_are_capped(tmp_path, monkeypatch):
    longest = (mod.MAX_RECIPIENTS * mod.SEND_TIMEOUT_S * mod.INLINE_ATTEMPTS + sum(mod.INLINE_BACKOFF_S)
               + (mod.DRAIN_LIMIT - 1) * mod.MAX_RECIPIENTS * mod.SEND_TIMEOUT_S)
    assert mod.CLAIM_LEASE_S > longest
    env = tmp_path / "bot.env"
    ids = ",".join(str(200 + i) for i in range(mod.MAX_RECIPIENTS + 3))
    env.write_text(f"TELEGRAM_BOT_TOKEN=file-token\nZOE_ALERT_TELEGRAM_CHAT_IDS={ids}\n")
    monkeypatch.setattr(mod, "TELEGRAM_ENV", str(env))
    monkeypatch.delenv("ZOE_ALERT_TELEGRAM_CHAT_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    asked: list[float] = []

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=0):
        asked.append(timeout)
        return Resp()

    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    reports: list[str] = []
    assert mod._telegram_all("x", report=reports.append) == ("delivered", mod.MAX_RECIPIENTS, 0)
    assert asked == [mod.SEND_TIMEOUT_S] * mod.MAX_RECIPIENTS          # capped, each with the bounded timeout
    assert any("only the first" in r for r in reports)                  # the cap is said, never silent


@pytest.mark.parametrize("given,kept", [
    ("crash_loop", "crash_loop"), ("recovered", "recovered"), ("health", "health"), ("backup", "backup"),
    ("kv_miss_first", "kv_miss_first"), ("unit_crash_loop.kokoro-tts", "unit_crash_loop.kokoro-tts"),
    ("unit_recovered.llama-server", "unit_recovered.llama-server"), ("unit_crash_loop", "unit_crash_loop"),
    ("private_person_name", "other"), ("unit_crash_loop.not-a-watched-unit", "other"), ("Health", "other"),
    ("", "other"), (None, "other"), ("health; rm", "other"),
])
def test_alert_codes_are_a_closed_list(box, given, kept):
    """A code reaches logs and the outbox: only the fixed codes and the watched units' forms survive; anything else,
    however well-formed, becomes ``other``."""
    assert mod._safe_code(given) == kept
    lines: list[str] = []
    mod.deliver(given, "x", transport=Transport(), now=T0, sleep=lambda _s: None, log=lines.append)
    assert all(f"code={kept} " in ln for ln in lines if ln.startswith("ALERT_DELIVERY"))
    if given and kept == "other":
        assert not any(str(given) in ln for ln in lines)
