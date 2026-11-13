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
    assert set(st) <= {"entries", "totals", "broken"}
    (e,) = st["entries"]
    assert set(e) == {"code", "text", "first_ts", "last_raised_ts", "raised", "tries", "next_ts"}
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
