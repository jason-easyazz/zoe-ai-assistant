"""change_notices + comm_prefs + telegram_notify - Phase 1 of change notices (the owner's Telegram PR feed).

Everything runs against a FAKE GitHub (``FakeGitHub``: open / merged PRs and which merge commits the deployed sha
contains) and a FAKE bot ``/internal/notify`` (``FakeBot`` behind ``httpx.MockTransport``, mirroring
``labs/flue-zoe-telegram-2x/src/notify.ts``: bearer token, linked members only, 1000-char text). No network, no DB.

Negative controls (in-suite; each proves its test can go red):
  * ``test_control_without_dedup_the_same_pr_is_sent_twice``      - the seen-key dedup removed
  * ``test_control_ignoring_quiet_hours_sends_at_night``         - quiet hours ignored
  * ``test_control_mode_on_does_send``                           - the flag-off test's setup DOES send when on
  * ``test_control_without_the_role_filter_a_member_is_notified`` - non-owner roles given the owner defaults
  * ``test_control_requeueing_sending_rows_duplicates``           - a crashed ``sending`` row re-sent on restart
  * ``test_control_large_batch_threshold_sends_one_each``         - batching disabled
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import json
from datetime import datetime, timedelta, timezone

import httpx

import change_notices as cn
import comm_prefs
import telegram_notify

TOKEN = "test-internal-token"
OWNER_TID = "111"


# ── fakes ─────────────────────────────────────────────────────────────────────
class FakeGitHub:
    def __init__(self) -> None:
        self.open: list[dict] = []
        self.merged: list[dict] = []
        self.ancestors: set[str] = set()
        self.calls = 0

    def raise_pr(self, n: int, title: str, body: str = "", labels=(), draft: bool = False) -> None:
        self.open.append({"number": n, "title": title, "body": body, "isDraft": draft,
                          "labels": [{"name": lb} for lb in labels], "url": f"https://github.com/o/r/pull/{n}"})

    def merge(self, n: int, title: str, body: str = "", *, at: datetime, live: bool = True) -> None:
        self.open = [p for p in self.open if p["number"] != n]
        sha = f"{n:040x}"
        self.merged.append({"number": n, "title": title, "body": body, "url": f"https://github.com/o/r/pull/{n}",
                            "mergedAt": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "mergeCommit": {"oid": sha}})
        if live:
            self.ancestors.add(sha)

    async def open_prs(self) -> list[dict]:
        self.calls += 1
        return [dict(p) for p in self.open]

    async def merged_prs(self, since: str) -> list[dict]:
        self.calls += 1
        return [dict(p) for p in self.merged]

    async def contains(self, deployed: str, sha: str):
        return sha in self.ancestors

    async def latest_deploy_run(self):
        return {"conclusion": "failure", "url": "https://github.com/o/r/actions/runs/1"}


class FakeBot:
    """The bot's /internal/notify, per notify.ts. ``script`` = per-call behaviours: ok | 502 | connect | timeout."""

    def __init__(self, linked=(OWNER_TID,)) -> None:
        self.linked = set(linked)
        self.delivered: list[dict] = []
        self.calls = 0
        self.script: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        how = self.script.pop(0) if self.script else "ok"
        if how == "connect":
            raise httpx.ConnectError("bot down", request=request)
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"ok": False})
        body = json.loads(request.content)
        if not str(body.get("telegram_id", "")).isdigit() or not body.get("text") or len(body["text"]) > 1000:
            return httpx.Response(400, json={"ok": False})
        if body["telegram_id"] not in self.linked:
            return httpx.Response(403, json={"ok": False, "error": "not a linked member"})
        if how == "502":
            return httpx.Response(502, json={"ok": False})
        self.delivered.append(body)
        if how == "timeout":  # it reached Telegram, the reply was lost
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"ok": True})

    def sender(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

        async def send(tid: str, msg: cn.Message) -> str:
            return await telegram_notify.send(tid, msg.text, msg.url, msg.label, client=client)

        return send


def owner(prefs=None, tid: str = OWNER_TID) -> cn.Recipient:
    return cn.Recipient("u-owner", "admin", tid, comm_prefs.normalise(prefs, "admin"))


def at(local_hhmm: str, day: int = 19) -> datetime:
    """A UTC instant for HH:MM Perth time (UTC+8) on 2026-10-<day>."""
    h, m = (int(x) for x in local_hhmm.split(":"))
    return datetime(2026, 10, day, h, m, tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)


async def _noop_sleep(_s: float) -> None:
    return None


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_CHANGE_NOTICES_STATE_DIR", str(tmp_path / "cn"))
    monkeypatch.setenv("ZOE_DEPLOY_STATE_DIR", str(tmp_path / "deploy"))
    monkeypatch.setenv("ZOE_INTERNAL_TOKEN", TOKEN)
    monkeypatch.delenv("ZOE_TELEGRAM_NOTIFY_URL", raising=False)
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.setenv("ZOE_QUIET_START_HOUR", "22")
    monkeypatch.setenv("ZOE_QUIET_END_HOUR", "7")
    monkeypatch.setenv("ZOE_GITHUB_REPO", "o/r")
    monkeypatch.delenv("ZOE_CHANGE_NOTICES", raising=False)
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "last-deployed-sha").write_text("ab" * 20 + "\n")
    return tmp_path


async def run(gh, bot, when, *, people=None, mode="on"):
    async def recipients():
        return people if people is not None else [owner()]

    return await cn.tick(gh=gh, sender=bot.sender(), recipients=recipients, now=when, sleep=_noop_sleep,
                         run_mode=mode)


async def booted(gh, bot, when=None):
    await run(gh, bot, when or at("09:00"))  # the first tick is silent: everything that exists is "seen"
    assert bot.calls == 0


# ── flag ──────────────────────────────────────────────────────────────────────
async def test_flag_off_sends_nothing():
    gh, bot = FakeGitHub(), FakeBot()
    gh.raise_pr(1, "fix: x", labels=["needs-owner"])
    assert cn.mode() == "off"  # the default
    for when in (at("09:00"), at("12:00"), at("19:30")):
        out = await cn.tick(gh=gh, sender=bot.sender(), recipients=lambda: [owner()], now=when, sleep=_noop_sleep)
        assert out == {"mode": "off"}
    assert bot.calls == 0 and gh.calls == 0
    assert not (cn.state_dir() / "state.json").exists()


async def test_control_mode_on_does_send(monkeypatch):
    monkeypatch.setenv("ZOE_CHANGE_NOTICES", "on")
    gh, bot = FakeGitHub(), FakeBot()
    await cn.tick(gh=gh, sender=bot.sender(), recipients=_owner_list, now=at("09:00"), sleep=_noop_sleep)
    gh.raise_pr(1, "fix: x", labels=["needs-owner"])
    await cn.tick(gh=gh, sender=bot.sender(), recipients=_owner_list, now=at("12:00"), sleep=_noop_sleep)
    assert len(bot.delivered) == 1


async def _owner_list():
    return [owner()]


def test_mode_parsing(monkeypatch):
    for raw, want in (("on", "on"), ("SHADOW", "shadow"), ("off", "off"), ("yes", "off"), ("", "off")):
        monkeypatch.setenv("ZOE_CHANGE_NOTICES", raw)
        assert cn.mode() == want


# ── the main flow ─────────────────────────────────────────────────────────────
async def test_bootstrap_silent_then_live_prs_in_one_evening_digest():
    gh, bot = FakeGitHub(), FakeBot()
    gh.raise_pr(5, "docs: old open PR")
    gh.merge(4, "fix: merged before the feature was on", at=at("08:00"))
    await booted(gh, bot)
    gh.merge(10, "feat(brief): first-turn brief", "## What\n\nThe brief now opens your first chat. It skips cancelled "
             "events. A third sentence never shows.\n\n- detail", at=at("10:00"))
    gh.merge(11, "fix(music): resume after a call", "## What\n\nMusic resumes after a call ends.", at=at("10:05"))
    gh.merge(12, "test: pin a date", at=at("10:10"))
    gh.merge(13, "fix: not deployed yet", at=at("19:00"), live=False)
    await run(gh, bot, at("12:00"))
    assert bot.calls == 0  # fixed / ability / under_the_hood wait for the evening digest
    await run(gh, bot, at("19:30"))
    assert len(bot.delivered) == 1
    text = bot.delivered[0]["text"]
    assert "3 changes went live" in text
    for n in (10, 11, 12):
        assert text.count(f"#{n} ") == 1
    assert "#13" not in text and "#4" not in text and "#5" not in text
    assert "• #10 brief: first-turn brief - The brief now opens your first chat." in text
    assert "It skips" not in text  # a digest line carries one sentence
    assert bot.delivered[0]["telegram_id"] == OWNER_TID
    await run(gh, bot, at("19:40"))
    assert len(bot.delivered) == 1  # once only


async def test_raised_prs_are_a_digest_count_and_a_merge_supersedes_its_raised_line():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    gh.raise_pr(20, "feat: a", draft=True)
    gh.raise_pr(21, "fix: b")
    gh.raise_pr(22, "fix: c")
    await run(gh, bot, at("10:00"))
    gh.merge(22, "fix: c", at=at("11:00"))
    await run(gh, bot, at("12:00"))
    await run(gh, bot, at("19:30"))
    text = bot.delivered[0]["text"]
    assert "Raised, not merged: 2 new (1 draft)." in text
    assert "Fixed (1)\n• #22 c" in text  # as a live change, not as a raised one


async def test_needs_owner_is_instant_and_three_at_once_are_batched():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    gh.raise_pr(30, "fix: one", "Needs a look.", labels=["needs-owner"])
    await run(gh, bot, at("12:00"))
    assert len(bot.delivered) == 1
    assert bot.delivered[0]["text"].startswith("Raised, waiting on you: #30 fix: one")
    assert bot.delivered[0]["url"].endswith("/pull/30")
    for n in (31, 32, 33, 34):
        gh.raise_pr(n, f"fix: n{n}", labels=["needs-owner"])
    await run(gh, bot, at("13:00"))
    assert len(bot.delivered) == 2
    assert bot.delivered[1]["text"].startswith("4 updates from Zoe:")


async def test_control_large_batch_threshold_sends_one_each(monkeypatch):
    monkeypatch.setattr(cn, "BATCH_AT", 99)
    monkeypatch.setattr(cn, "SAME_KIND_SPACING", timedelta(0))
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    for n in (31, 32, 33, 34):
        gh.raise_pr(n, f"fix: n{n}", labels=["needs-owner"])
    await run(gh, bot, at("13:00"))
    assert len(bot.delivered) == 4


async def test_same_kind_within_30_min_and_the_daily_cap_roll_into_the_digest():
    gh, bot = FakeGitHub(), FakeBot()
    me = [owner({"daily_instant_cap": 2})]
    await run(gh, bot, at("09:00"), people=me)
    gh.raise_pr(40, "fix: a", labels=["needs-owner"])
    await run(gh, bot, at("12:00"), people=me)
    gh.raise_pr(41, "fix: b", labels=["needs-owner"])
    await run(gh, bot, at("12:10"), people=me)  # same category 10 min later: digest
    gh.raise_pr(42, "fix: c", labels=["needs-owner"])
    await run(gh, bot, at("13:00"), people=me)  # 2nd instant today
    gh.raise_pr(43, "fix: d", labels=["needs-owner"])
    await run(gh, bot, at("14:00"), people=me)  # over the cap: digest
    assert [d["text"].split("\n")[0] for d in bot.delivered] == [
        "Raised, waiting on you: #40 fix: a", "Raised, waiting on you: #42 fix: c"]
    await run(gh, bot, at("19:30"), people=me)
    digest = bot.delivered[-1]["text"]
    assert "Waiting on you (2)\n• #41 b\n• #43 d" in digest and "#40" not in digest


# ── dedup ─────────────────────────────────────────────────────────────────────
async def _dedup_scenario():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    gh.raise_pr(50, "fix: urgent", labels=["needs-owner"])
    await run(gh, bot, at("12:00"))
    await run(gh, bot, at("13:00"))
    await run(gh, bot, at("14:00"))
    return bot


async def test_dedup_the_same_pr_is_sent_once():
    bot = await _dedup_scenario()
    assert len(bot.delivered) == 1


async def test_control_without_dedup_the_same_pr_is_sent_twice(monkeypatch):
    real = cn._new_notice

    def forgetful(c, item, *, silent):
        c.st["seen"].pop(item["key"], None)
        for k in [k for k in c.st["deliveries"] if k.endswith("|" + item["key"])]:
            c.st["deliveries"].pop(k)
        real(c, item, silent=silent)

    monkeypatch.setattr(cn, "_new_notice", forgetful)
    bot = await _dedup_scenario()
    assert len(bot.delivered) >= 2


# ── quiet hours ───────────────────────────────────────────────────────────────
async def _quiet_scenario():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot, at("20:00", day=18))
    gh.raise_pr(60, "fix: broke at night", labels=["needs-owner"])
    await run(gh, bot, at("23:00", day=18))
    after_night = len(bot.delivered)
    await run(gh, bot, at("06:50"))
    before_end = len(bot.delivered)
    await run(gh, bot, at("07:10"))
    await run(gh, bot, at("08:00"))
    return after_night, before_end, bot


async def test_quiet_hours_hold_then_deliver_once_at_the_end():
    after_night, before_end, bot = await _quiet_scenario()
    assert (after_night, before_end) == (0, 0)
    assert len(bot.delivered) == 1
    audit = (cn.state_dir() / "audit.jsonl").read_text()
    assert audit.count('"event": "held_quiet"') == 1


async def test_control_ignoring_quiet_hours_sends_at_night(monkeypatch):
    monkeypatch.setattr(comm_prefs, "in_quiet_hours", lambda prefs, minute: False)
    after_night, _before, _bot = await _quiet_scenario()
    assert after_night == 1


async def test_member_quiet_hours_override_the_household():
    gh, bot = FakeGitHub(), FakeBot()
    me = [owner({"quiet_hours": {"start": "12:00", "end": "13:00"}})]
    await run(gh, bot, at("09:00"), people=me)
    gh.raise_pr(61, "fix: x", labels=["needs-owner"])
    await run(gh, bot, at("12:30"), people=me)
    assert bot.delivered == []
    await run(gh, bot, at("23:30"), people=me)  # the household's night is not this member's quiet window
    assert len(bot.delivered) == 1


# ── recipients: owner only, never a non-member ────────────────────────────────
class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    """Answers the two reads ``load_recipients`` makes: the users table and ``user_preferences``."""

    def __init__(self, users, prefs):
        self.users, self.prefs = users, prefs

    async def execute(self, sql, params=()):
        if "FROM users" in sql:
            return _Cursor([u for u in self.users if u["role"] in ("admin", "family-admin")]
                           if "role IN" in sql else list(self.users))
        if "FROM user_preferences" in sql:
            p = self.prefs.get(params[0])
            return _Cursor([{"prefs": json.dumps(p)}] if p is not None else [])
        raise AssertionError(sql)


def _household_db():
    users = [{"id": "u-owner", "role": "admin"}, {"id": "u-adult", "role": "member"},
             {"id": "u-kid", "role": "child"}, {"id": "guest", "role": "guest"}]
    prefs = {"u-owner": {"telegram_id": "111"}, "u-adult": {"telegram_id": "222"}, "u-kid": {"telegram_id": "333"},
             "guest": {"telegram_id": "444"}, "u-stranger": {"telegram_id": "555"}}
    return FakeDB(users, prefs)


async def _household_scenario():
    gh, bot = FakeGitHub(), FakeBot(linked={"111", "222", "333", "444", "555"})
    db = _household_db()

    async def recipients():
        return await cn.load_recipients(db)

    await cn.tick(gh=gh, sender=bot.sender(), recipients=recipients, now=at("09:00"), sleep=_noop_sleep, run_mode="on")
    gh.raise_pr(70, "fix: x", labels=["needs-owner"])
    await cn.tick(gh=gh, sender=bot.sender(), recipients=recipients, now=at("12:00"), sleep=_noop_sleep, run_mode="on")
    return {d["telegram_id"] for d in bot.delivered}


async def test_only_the_owner_is_ever_notified():
    rs = await cn.load_recipients(_household_db())
    assert [(r.user_id, r.telegram_id) for r in rs] == [("u-owner", "111")]
    assert await _household_scenario() == {"111"}


async def test_control_without_the_role_filter_a_member_is_notified(monkeypatch):
    owner_doc = comm_prefs.defaults_for_role("admin")
    monkeypatch.setattr(comm_prefs, "defaults_for_role", lambda role: dict(owner_doc))
    db = _household_db()

    async def every_user(_db=None):  # the users query without its role filter
        out = []
        for u in db.users:
            tid = db.prefs[u["id"]]["telegram_id"]
            out.append(cn.Recipient(u["id"], u["role"], tid, comm_prefs.normalise(None, u["role"])))
        return out

    monkeypatch.setattr(cn, "load_recipients", every_user)
    assert "222" in await _household_scenario()


async def test_an_owner_without_a_linked_telegram_is_skipped():
    db = FakeDB([{"id": "u-owner", "role": "admin"}], {"u-owner": {}})
    assert await cn.load_recipients(db) == []


async def test_the_bot_refusing_an_unlinked_chat_is_final_not_retried():
    gh, bot = FakeGitHub(), FakeBot(linked=set())
    await booted(gh, bot)
    gh.raise_pr(71, "fix: x", labels=["needs-owner"])
    for when in (at("12:00"), at("13:00"), at("14:00")):
        await run(gh, bot, when)
    assert bot.calls == 1 and bot.delivered == []
    st = cn.load_state()
    assert st["deliveries"]["u-owner|pr:71:decision"]["status"] == "refused"


# ── failures: retry without duplicates, at most once ──────────────────────────
async def test_failed_send_retries_without_duplicates():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    gh.raise_pr(80, "fix: x", labels=["needs-owner"])
    bot.script = ["502", "connect"]
    await run(gh, bot, at("12:00"))  # 502: not delivered
    await run(gh, bot, at("12:05"))  # backoff (10 min) not elapsed: no attempt
    assert bot.calls == 1
    await run(gh, bot, at("12:11"))  # connect error: not delivered; backoff 20 min
    await run(gh, bot, at("12:32"))  # delivered
    await run(gh, bot, at("13:30"))
    await run(gh, bot, at("19:30"))
    assert bot.calls == 3
    assert len(bot.delivered) == 1 and "#80" in bot.delivered[0]["text"]
    d = cn.load_state()["deliveries"]["u-owner|pr:80:decision"]
    assert d["status"] == "sent" and d["attempts"] == 2


async def test_a_timeout_after_sending_is_never_resent_and_is_mentioned_once():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    gh.raise_pr(81, "fix: x", labels=["needs-owner"])
    bot.script = ["timeout"]
    await run(gh, bot, at("12:00"))
    await run(gh, bot, at("13:00"))
    assert len(bot.delivered) == 1  # the one that may or may not have arrived
    await run(gh, bot, at("19:30"))
    await run(gh, bot, at("19:30", day=20))
    mentions = [d["text"] for d in bot.delivered[1:]]
    assert len(mentions) == 1 and "may not have reached you earlier: #81" in mentions[0]


class _Crash(BaseException):
    pass


async def _crash_scenario():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    gh.raise_pr(82, "fix: x", labels=["needs-owner"])

    async def crash(_tid, _msg):  # the process dies after the claim, before it learns the outcome
        raise _Crash

    with pytest.raises(_Crash):
        await cn.tick(gh=gh, sender=crash, recipients=_owner_list, now=at("12:00"), sleep=_noop_sleep, run_mode="on")
    assert cn.load_state()["deliveries"]["u-owner|pr:82:decision"]["status"] == "sending"
    await run(gh, bot, at("12:30"))
    await run(gh, bot, at("19:30"))
    return bot


async def test_a_crash_mid_send_never_sends_twice():
    bot = await _crash_scenario()
    assert all("Raised, waiting on you: #82" not in d["text"] for d in bot.delivered)
    assert len(bot.delivered) == 1 and "may not have reached you earlier: #82" in bot.delivered[0]["text"]


async def test_control_requeueing_sending_rows_duplicates(monkeypatch):
    def requeue(c):
        for d in c.st["deliveries"].values():
            if d["status"] == "sending":
                d["status"] = "queued"

    monkeypatch.setattr(cn, "_recover", requeue)
    bot = await _crash_scenario()
    assert any("Raised, waiting on you: #82" in d["text"] for d in bot.delivered)


# ── stuck deploys, shadow, kill switch ────────────────────────────────────────
async def test_merged_but_not_live_after_an_hour_is_one_broke_notice():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    gh.merge(90, "fix: a", at=at("10:00"), live=False)
    gh.merge(91, "fix: b", at=at("10:05"), live=False)
    await run(gh, bot, at("10:30"))
    assert bot.delivered == []
    await run(gh, bot, at("11:10"))
    await run(gh, bot, at("11:20"))
    assert len(bot.delivered) == 1
    t = bot.delivered[0]["text"]
    assert "Merged but not live after 60 min: #90, #91" in t and "failure" in t
    assert bot.delivered[0]["url"].endswith("/actions/runs/1")


async def test_shadow_mode_logs_and_never_sends_and_on_does_not_replay():
    gh, bot = FakeGitHub(), FakeBot()
    await run(gh, bot, at("09:00"), mode="shadow")
    gh.raise_pr(95, "fix: x", labels=["needs-owner"])
    await run(gh, bot, at("12:00"), mode="shadow")
    assert bot.calls == 0
    audit = [json.loads(line) for line in (cn.state_dir() / "audit.jsonl").read_text().splitlines()]
    shadow = [a for a in audit if a["event"] == "shadow_send"]
    assert len(shadow) == 1 and "#95" in shadow[0]["text"]
    await run(gh, bot, at("13:00"), mode="on")
    assert bot.calls == 0


async def test_kill_switch_stops_every_send():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    cn.set_killed(True)
    gh.raise_pr(96, "fix: x", labels=["needs-owner"])
    out = await run(gh, bot, at("12:00"))
    assert out["killed"] and bot.calls == 0
    cn.set_killed(False)
    await run(gh, bot, at("12:10"))
    assert len(bot.delivered) == 1


async def test_a_long_digest_is_split_under_the_route_limit_and_every_pr_is_accounted_for():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    body = "## What\n\n" + ("A long sentence about the change that keeps going for a while. " * 3)
    for n in range(100, 160):
        gh.merge(n, f"fix(area): change number {n} with a reasonably long title", body, at=at("10:00"))
    await run(gh, bot, at("19:30"))
    assert 1 < len(bot.delivered) <= cn.MAX_CHUNKS
    assert all(len(d["text"]) <= 1000 for d in bot.delivered)
    assert "more on GitHub" in bot.delivered[-1]["text"]
    st = cn.load_state()
    statuses = {d["status"] for k, d in st["deliveries"].items() if ":live" in k}
    assert statuses == {"sent"}


# ── rendering ─────────────────────────────────────────────────────────────────
def test_summary_from_body_trailer_and_title():
    assert cn.summarise("fix: x", "## What\n\nFirst `thing` is **fixed**. Then [docs](http://x) too. Third.") == \
        "First thing is fixed. Then docs too."
    assert cn.summarise("fix: x", "Body text.\n\nFor-The-Household: Music resumes after a call.") == \
        "Music resumes after a call."
    assert cn.summarise("feat(brief): first-turn brief skips a cancelled event", "") == \
        "First-turn brief skips a cancelled event."
    assert cn.summarise("fix: x", "## What\n\n- bullet one is here.\n- bullet two") == "bullet one is here. bullet two"
    assert len(cn.summarise("fix: x", "word " * 400)) <= 280
    assert cn.summarise("fix: x", "Co-Authored-By: a <a@b>\n\nOwner ask: keep me posted.") == "Owner ask: keep me posted."


def test_category_from_the_conventional_commit_type():
    assert cn.category_for_title("feat(x): y") == "ability"
    assert cn.category_for_title("fix(security): y") == "fixed"
    assert cn.category_for_title("docs: y") == "under_the_hood"
    assert cn.category_for_title('Revert "feat: y" (#12)') == "broke"
    assert cn.category_for_title("something else") == "fixed"


# ── preferences ───────────────────────────────────────────────────────────────
def test_only_owner_roles_have_preferences():
    assert comm_prefs.normalise(None, "admin")["preset"] == "owner_technical"
    assert comm_prefs.normalise(None, "family-admin") is not None
    for role in ("member", "user", "child", "guest", "", None):
        assert comm_prefs.normalise({"preset": "everything"}, role) is None


def test_invalid_fields_fall_back_and_floors_hold():
    doc = comm_prefs.normalise({"language": "fr", "daily_instant_cap": "lots", "quiet_hours": {"start": "25:00"},
                                "categories": {"security": {"cadence": "never"}, "decision": {"cadence": "never"},
                                               "fixed": {"cadence": "instant", "channels": ["telegram", "pager"]}}},
                               "admin")
    assert doc["language"] == "en" and doc["daily_instant_cap"] == 6 and doc["quiet_hours"] is None
    assert doc["categories"]["security"]["cadence"] == "digest"
    assert doc["categories"]["decision"]["cadence"] == "digest"
    assert doc["categories"]["fixed"] == {"cadence": "instant", "channels": ["telegram"], "detail": "technical"}
    assert doc["preset"] == "custom"
    assert comm_prefs.normalise(doc, "admin") == doc  # stable round trip


def test_presets_and_merge():
    nothing = comm_prefs.normalise({"preset": "nothing"}, "admin")
    assert nothing["preset"] == "nothing" and nothing["categories"]["fixed"]["cadence"] == "never"
    assert nothing["categories"]["security"]["cadence"] == "digest"
    assert comm_prefs.normalise(nothing, "admin") == nothing
    stored = comm_prefs.normalise({"categories": {"fixed": {"cadence": "instant"}}}, "admin")
    merged = comm_prefs.normalise(comm_prefs.merge(stored, {"quiet_hours": {"start": "21:30", "end": "06:30"}}), "admin")
    assert merged["categories"]["fixed"]["cadence"] == "instant" and merged["quiet_hours"]["start"] == "21:30"
    weekly = comm_prefs.normalise(comm_prefs.merge(merged, {"preset": "weekly"}), "admin")
    assert weekly["preset"] == "weekly" and weekly["categories"]["fixed"]["cadence"] == "weekly"


def test_quiet_window_wraps_midnight():
    doc = comm_prefs.normalise(None, "admin")
    assert comm_prefs.in_quiet_hours(doc, 23 * 60) and comm_prefs.in_quiet_hours(doc, 6 * 60 + 59)
    assert not comm_prefs.in_quiet_hours(doc, 7 * 60) and not comm_prefs.in_quiet_hours(doc, 21 * 60 + 59)


# ── the real gh source and the notify client ──────────────────────────────────
async def test_gh_cli_reads_and_maps_compare_status():
    calls = []
    answers = {"pr": (0, json.dumps([{"number": 1}])), "api": (0, "ahead\n"), "run": (1, "")}

    async def runner(args):
        calls.append(args)
        return answers[args[0]]

    gh = cn.GhCli("o/r", runner)
    assert await gh.open_prs() == [{"number": 1}]
    assert await gh.merged_prs("2026-10-17") == [{"number": 1}]
    assert "merged:>=2026-10-17" in calls[-1]
    assert await gh.contains("dep", "abc") is True
    assert calls[-1][:2] == ["api", "repos/o/r/compare/abc...dep"]
    for status, want in (("identical", True), ("behind", False), ("diverged", False), ("", None)):
        answers["api"] = (0, status)
        assert await gh.contains("dep", "abc") is want
    answers["api"] = (1, "")
    assert await gh.contains("dep", "abc") is None
    assert await gh.latest_deploy_run() is None


async def test_notify_client_is_unavailable_without_the_shared_secret(monkeypatch):
    monkeypatch.delenv("ZOE_INTERNAL_TOKEN")
    assert telegram_notify.notify_url() == "" and not telegram_notify.available()
    assert await telegram_notify.send("111", "hi") == "retry"
    monkeypatch.setenv("ZOE_INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("ZOE_TELEGRAM_NOTIFY_URL", "off")
    assert telegram_notify.notify_url() == ""
    monkeypatch.delenv("ZOE_TELEGRAM_NOTIFY_URL")
    assert telegram_notify.notify_url() == telegram_notify.DEFAULT_NOTIFY_URL
    assert await telegram_notify.send("not-digits", "hi") == "refused"


def test_router_registers_admin_only_routes():
    from routers import change_notices as r

    paths = {(sorted(rt.methods)[0], rt.path) for rt in r.router.routes}
    assert paths == {("GET", "/api/change-notices/preferences"), ("PUT", "/api/change-notices/preferences"),
                     ("GET", "/api/change-notices/status"), ("POST", "/api/change-notices/kill")}
    for rt in r.router.routes:
        assert any(d.call is r._require_human_admin for d in rt.dependant.dependencies)


# ── edges ─────────────────────────────────────────────────────────────────────
async def test_a_digest_time_inside_quiet_hours_moves_to_the_end_of_the_window():
    gh, bot = FakeGitHub(), FakeBot()
    me = [owner({"digest": {"daily_at": "23:00"}})]  # inside 22:00-07:00, so the digest is due at 07:00
    await run(gh, bot, at("09:00"), people=me)
    gh.merge(97, "fix: x", at=at("10:00"))
    await run(gh, bot, at("12:00"), people=me)
    assert len(bot.delivered) == 1 and "#97" in bot.delivered[0]["text"]
    gh.merge(98, "fix: y", at=at("13:00"))
    for when in (at("14:00"), at("23:10"), at("06:55", day=20)):
        await run(gh, bot, when, people=me)
    assert len(bot.delivered) == 1
    await run(gh, bot, at("07:05", day=20), people=me)
    assert len(bot.delivered) == 2 and "#98" in bot.delivered[1]["text"]


async def test_no_route_configured_holds_without_burning_retries(monkeypatch):
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    monkeypatch.setenv("ZOE_TELEGRAM_NOTIFY_URL", "off")
    gh.raise_pr(98, "fix: x", labels=["needs-owner"])
    for when in (at("12:00"), at("13:00")):
        await cn.tick(gh=gh, sender=cn.telegram_sender, recipients=_owner_list, now=when, sleep=_noop_sleep,
                      run_mode="on")
    d = cn.load_state()["deliveries"]["u-owner|pr:98:decision"]
    assert d["status"] == "queued" and d["attempts"] == 0


async def test_a_pr_open_for_months_is_announced_once():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    gh.raise_pr(99, "fix: slow burner")
    for day in range(0, 70, 5):
        await run(gh, bot, at("19:30") + timedelta(days=day))
    assert len([d for d in bot.delivered if "Raised, not merged" in d["text"]]) == 1
