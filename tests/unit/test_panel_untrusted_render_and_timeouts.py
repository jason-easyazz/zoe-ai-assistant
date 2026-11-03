"""The panel's pages escape every server string they render and give every request a deadline (2026-11-02).

Two classes from the round-4 code sweep, pinned at the source so they cannot drift back:

* a learned / extracted memory string, a calendar task, a reminder, a person's label, a chat card label or a
  research result rendered RAW into ``innerHTML`` (attribute values and ``onclick`` arguments included) was a
  stored XSS into a panel session;
* a ``fetch`` with no ``AbortSignal`` wedged the executor: ``pollActions`` holds ``state.processing`` until its
  ``finally``, so one half-open request stopped every server UI action for good, silently.

The checks are functions over the file text, and the negative control plants each fault into a copy and expects
the check to turn red - a green here means the check can see the fault, not just that the text is what it is.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "services" / "zoe-ui" / "dist"
TG = ROOT / "labs" / "flue-zoe-telegram-2x" / "src"


def _read(rel: str) -> str:
    return (UI / rel).read_text(encoding="utf-8")


# ── raw render sites: each pattern is one that used to exist and must not come back ──
RAW_PATTERNS = {
    "touch/memories.html": [
        "${(item.content || '').slice(0, 180)}", "${item.content || ''}", "${item.name || ''}", "'${item.id}'",
        "${notification.message}", "${item.preview.title}",
    ],
    "touch/calendar.html": ["${task.text}", 'value="${reminder.message}"', 'value="${item.text}"', "'${task.id}'"],
    "touch/people.html": ["${r.label}</span>", "openDetail('${r.person_id}')", ".replace(/</g,'&lt;')}", "markBucketDone('${it.id}')"],   # the half-escape (only <), never the helper
    "touch/chat.html": ["${action.label}", "'${cardId}'", "'${actionId}'", "${JSON.stringify(action.data).replace(", "${JSON.stringify(data).replace(", 'Schedule "${data.title}"'],
    "touch/settings.html": ['<option value="${u.user_id}">${u.username}'],
    "js/touch-ui-executor.js": ["${u}</li>", "${pkg.query || ''}", "${(r.name || '').toString()}", "${top.name || 'N/A'}</strong>"],
}


def raw_sites(rel: str, text: str) -> list[str]:
    return [p for p in RAW_PATTERNS[rel] if p in text]


@pytest.mark.parametrize("rel", sorted(RAW_PATTERNS))
def test_no_raw_render_site_remains(rel):
    assert raw_sites(rel, _read(rel)) == []


def test_each_page_defines_and_uses_its_escape_helper():
    for rel, helper, floor in (
        ("touch/memories.html", "zoeEsc(", 15), ("touch/calendar.html", "zoeEsc(", 3), ("touch/people.html", "zoeEsc(", 10),
        ("touch/chat.html", "escAttr(", 12), ("touch/settings.html", "escAttr(", 1), ("js/touch-ui-executor.js", "escHtml(", 9),
    ):
        text = _read(rel)
        assert f"function {helper[:-1]}" in text, rel
        assert text.count(helper) - 1 >= floor, (rel, text.count(helper))


def test_the_escape_helpers_escape_both_quote_kinds():
    # an onclick="f('${x}')" argument breaks out on a single quote; innerHTML serialisation never escapes quotes
    for rel in ("touch/memories.html", "touch/calendar.html", "touch/people.html"):
        assert "replace(/'/g,'&#39;')" in _read(rel) and "replace(/\"/g,'&quot;')" in _read(rel), rel
    assert "replace(/'/g,'&#39;')" in _read("touch/chat.html") and "replace(/'/g,'&#39;')" in _read("touch/settings.html")
    assert "replace(/'/g, '&#39;')" in _read("js/touch-ui-executor.js")


# ── deadlines ──
def executor_requests_without_deadline(text: str) -> list[str]:
    """The executor's api() and its three direct fetches must each carry a signal."""
    missing = []
    api = text[text.index("async function api(path, options)"):]
    api = api[: api.index("function buildContext")]
    if "init.signal = AbortSignal.timeout(15000)" not in api:
        missing.append("api()")
    for marker in ("/ack`", "action: 'toggle' }),", "action }),"):
        i = text.index(marker)
        if "signal: requestTimeout(" not in text[i : i + 220]:
            missing.append(marker)
    return missing


def test_executor_requests_carry_a_deadline():
    assert executor_requests_without_deadline(_read("js/touch-ui-executor.js")) == []


def test_estate_helpers_carry_a_deadline_and_loadha_releases_on_failure():
    home = _read("touch/home.html")
    for fn in ("function apiGet(url)", "function apiJson(method,url,body)", "function haControl(body)"):
        i = home.index(fn)
        assert "signal:reqSig(" in home[i : i + 400], fn
    assert "function reqSig(ms)" in home and "typeof AbortSignal!=='undefined'" in home
    # a rejected entity load used to leave _haInflight set forever, so the dock, rooms and sleep surfaces stopped refreshing
    assert "_haInflight=null;cb&&cb();},function(){_haInflight=null;cb&&cb();});" in home


def sidecar_fetches_without_signal(text: str) -> int:
    return text.count("fetch(") - text.count("signal: AbortSignal.timeout(")


def test_telegram_sidecar_fetches_all_carry_a_signal_and_the_download_streams_capped():
    brain = (TG / "brain.ts").read_text(encoding="utf-8")
    telegram = (TG / "telegram.ts").read_text(encoding="utf-8")
    assert brain.count("fetch(") >= 6 and sidecar_fetches_without_signal(brain) == 0
    assert sidecar_fetches_without_signal(telegram) == 0
    assert "return readCapped(res, maxBytes);" in telegram and "new Uint8Array(await res.arrayBuffer());\n  if (bytes.byteLength > maxBytes)" not in telegram


# ── negative controls: plant each fault, the check must go red ──
def test_negative_control_raw_site_is_seen():
    planted = _read("touch/memories.html").replace("${zoeEsc((item.content || '').slice(0, 180))}", "${(item.content || '').slice(0, 180)}", 1)
    assert raw_sites("touch/memories.html", planted) == ["${(item.content || '').slice(0, 180)}"]


def test_negative_control_missing_deadline_is_seen():
    text = _read("js/touch-ui-executor.js")
    planted = text.replace("init.signal = AbortSignal.timeout(15000);", "init.signal = undefined;", 1)
    assert executor_requests_without_deadline(planted) == ["api()"]
    planted = text.replace("                signal: requestTimeout(8000),\n", "", 1)   # the ack
    assert executor_requests_without_deadline(planted) == ["/ack`"]
    brain = (TG / "brain.ts").read_text(encoding="utf-8")
    assert sidecar_fetches_without_signal(brain.replace("    signal: AbortSignal.timeout(TURN_TIMEOUT_MS),\n", "", 1)) == 1
