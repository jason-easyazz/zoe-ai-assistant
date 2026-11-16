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
import random
import re
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

    def merge(self, n: int, title: str, body: str = "", *, at: datetime, live: bool = True, base: str = "main") -> None:
        self.open = [p for p in self.open if p["number"] != n]
        sha = f"{n:040x}"
        self.merged.append({"number": n, "title": title, "body": body, "url": f"https://github.com/o/r/pull/{n}",
                            "mergedAt": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "mergeCommit": {"oid": sha},
                            "baseRefName": base})
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
    gh.merge(10, "feat(brief): first-turn brief", "## What\n\n- detail\n\nFor-The-Household: The brief now opens your "
             "first chat. It skips cancelled events. A third sentence never shows.", at=at("10:00"))
    gh.merge(11, "fix(music): resume after a call", "## What\n\nMusic resumes after a call ends.", at=at("10:05"))
    gh.merge(12, "test: pin a date", at=at("10:10"))
    gh.merge(13, "fix: not deployed yet", at=at("19:00"), live=False)
    await run(gh, bot, at("12:00"))
    assert bot.calls == 0  # fixed / ability / under_the_hood wait for the evening digest
    await run(gh, bot, at("19:30"))
    assert len(bot.delivered) == 1
    text = bot.delivered[0]["text"]
    assert text == ("Good evening. Here's today: three changes went live.\n"
                    "• The brief now opens your first chat. It skips cancelled events. (#10)\n"
                    "Plus two bits of housekeeping (#11, #12).\n"
                    "Nothing for you to do.")  # at most two sentences; no trailer = housekeeping, collapsed
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
    assert "Two new changes are open and not merged yet (one is a draft)." in text
    assert "One bit of housekeeping (#22), nothing you will notice." in text  # as a live change, not a raised one


async def test_needs_owner_is_instant_and_three_at_once_are_batched():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    gh.raise_pr(30, "fix: one", "Needs a look.", labels=["needs-owner"])
    await run(gh, bot, at("12:00"))
    assert len(bot.delivered) == 1
    assert bot.delivered[0]["text"] == ('I need your OK on something. It is about "One".\n'
                                        "Can you have a look when you get a minute?")
    assert bot.delivered[0]["url"].endswith("/pull/30") and bot.delivered[0]["label"] == "Open #30"
    for n in (31, 32, 33, 34):
        gh.raise_pr(n, f"fix: n{n}", labels=["needs-owner"])
    await run(gh, bot, at("13:00"))
    assert len(bot.delivered) == 2
    assert bot.delivered[1]["text"] == "Waiting on you:\n" + "\n".join(
        f'• "N{n}" needs a look from you. (#{n})' for n in (31, 32, 33, 34)) + "\nNothing else for you to do."


async def test_control_large_batch_threshold_sends_one_each(monkeypatch):
    monkeypatch.setattr(cn, "BATCH_AT", 99)
    monkeypatch.setattr(cn, "SAME_KIND_SPACING", timedelta(0))
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    for n in (31, 32, 33, 34):
        gh.raise_pr(n, f"fix: n{n}", labels=["needs-owner"])
    await run(gh, bot, at("13:00"))
    assert len(bot.delivered) == 4  # the base's single messages are unchanged (no follow-up involved)


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
    assert [d["label"] for d in bot.delivered] == ["Open #40", "Open #42"]
    assert [d["text"].split("\n")[0] for d in bot.delivered] == [  # the opener rotates: never the same twice in a row
        'I need your OK on something. It is about "A".', 'One for you to decide. It is about "C".']
    await run(gh, bot, at("19:30"), people=me)
    digest = bot.delivered[-1]["text"]
    assert ('Waiting on you:\n• "B" needs a look from you. (#41)\n• "D" needs a look from you. (#43)' in digest
            and "#40" not in digest)


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
    assert len(bot.delivered) == 1 and bot.delivered[0]["label"] == "Open #80"
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
    assert len(mentions) == 1 and 'may not have reached you earlier: "X" (#81).' in mentions[0]


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
    assert all(d["label"] != "Open #82" for d in bot.delivered)  # the decision itself is never re-sent
    assert len(bot.delivered) == 1 and 'may not have reached you earlier: "X" (#82)' in bot.delivered[0]["text"]


async def test_control_requeueing_sending_rows_duplicates(monkeypatch):
    def requeue(c):
        for d in c.st["deliveries"].values():
            if d["status"] == "sending":
                d["status"] = "queued"

    monkeypatch.setattr(cn, "_recover", requeue)
    bot = await _crash_scenario()
    assert any(d["label"] == "Open #82" for d in bot.delivered)


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
    assert bot.delivered[0]["text"] == (
        'Heads up: two changes did not go live: "A" and "B". They were merged over '
        f"{cn.stale_words(cn.stale_after())} ago and are not running "      # the configured wait in words, not a clock reading
        "yet. The last deploy failed. It may need a look. (#90, #91)")
    assert bot.delivered[0]["url"].endswith("/actions/runs/1")


async def test_a_pr_merged_into_a_stacked_branch_is_never_stuck():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    gh.merge(92, "perf: stacked", at=at("10:00"), live=False, base="feature/other")
    await run(gh, bot, at("12:00"))
    await run(gh, bot, at("12:20"))
    assert bot.delivered == []
    assert "92" not in cn.load_state()["pending_live"]


async def test_control_a_main_merge_not_live_is_still_stuck():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    gh.merge(93, "fix: on main", at=at("10:00"), live=False)
    await run(gh, bot, at("12:00"))
    assert len(bot.delivered) == 1


async def test_a_legacy_pending_entry_not_on_main_is_dropped():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    st = cn.load_state()
    st["pending_live"]["94"] = {"number": 94, "title": "perf: old", "sha": f"{94:040x}", "url": None,
                                "merged_at": "2026-11-19T10:00:00Z", "summary": "s", "category": "fixed"}
    cn.save_state(st)
    await run(gh, bot, at("12:00"))
    assert "94" not in cn.load_state()["pending_live"] and bot.delivered == []


async def test_shadow_mode_logs_and_never_sends_and_on_does_not_replay():
    gh, bot = FakeGitHub(), FakeBot()
    await run(gh, bot, at("09:00"), mode="shadow")
    gh.raise_pr(95, "fix: x", labels=["needs-owner"])
    await run(gh, bot, at("12:00"), mode="shadow")
    assert bot.calls == 0
    audit = [json.loads(line) for line in (cn.state_dir() / "audit.jsonl").read_text().splitlines()]
    shadow = [a for a in audit if a["event"] == "shadow_send"]
    assert len(shadow) == 1 and shadow[0]["url"].endswith("/pull/95") and '"X"' in shadow[0]["text"]
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
    body = "For-The-Household: " + ("A long sentence about the change that keeps going for a while, " * 3) + "done."
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
def test_household_sentence_subject_and_trailers_never_prose():
    assert cn.household("Body text.\n\nFor-The-Household: Music resumes after a call.") == "Music resumes after a call."
    assert cn.household("For-The-Household: `Alarms` **ring** [again](http://x). Second. Third.") == "Alarms ring again. Second."
    for none in ("For-The-Household: nothing visible", "For-The-Household: Nothing visible.", "no trailer", ""):
        assert cn.household(none) == ""
    assert cn.household("<!-- For-The-Household: a template example -->") == ""  # comments are not trailers
    assert len(cn.household("For-The-Household: " + "word " * 400)) <= 280
    assert cn.subject("feat(brief): first-turn brief skips a cancelled event (#12)") == \
        "First-turn brief skips a cancelled event"
    assert cn.subject('Revert "fix(music): resume after a call" (#13)') == "Resume after a call"
    assert cn.subject("chore: bump to 1a2b3c4d5e and `x`") == "Bump to and x"
    assert cn.subject("") == "A change"
    f = cn.pr_fields("fix: x", "Numbers: a 1 -> 2\nOwner-Step: Flip the `switch`\nIf-No-Answer: It stays off")
    assert f == {"household": "", "numbers": ["a 1 → 2"], "owner_step": "Flip the switch.",
                 "if_no_answer": "It stays off."}
    for key in ("Numbers", "Owner-Step", "If-No-Answer", "For-The-Household"):
        assert cn._TRAILER.match(f"{key}: anything")


def test_numbers_line_is_validated():
    assert cn.numbers("Numbers: scrubbing a long message 4.5 s -> 0.03 s; tests that guard it 603") == \
        ["scrubbing a long message 4.5 s → 0.03 s", "tests that guard it 603"]
    assert cn.numbers("Numbers: faster → much faster; fewer errors") == []  # no digit anywhere: ignored entirely
    assert cn.numbers("Numbers: a 1->2; b 3 → 4; c 5 -> 6; d 7 -> 8") == ["a 1 → 2", "b 3 → 4", "c 5 → 6"]  # max 3
    assert cn.numbers("Numbers: **load** [time](http://x) `9 s` -> 2 s; no digit here") == ["load time 9 s → 2 s"]
    long = cn.numbers("Numbers: " + "word " * 40 + "1 -> 2")
    assert len(long) == 1 and len(long[0]) <= cn.NUMBER_CHARS and long[0].endswith("…")
    assert cn.numbers("Numbers:\n\nprose 1 -> 2") == []  # the trailer value is its own line


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
    assert len([d for d in bot.delivered if "open and not merged yet" in d["text"]]) == 1


async def test_the_may_not_have_reached_you_line_is_mentioned_at_most_once_even_if_it_times_out():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    gh.raise_pr(83, "fix: x", labels=["needs-owner"])
    bot.script = ["timeout"]
    await run(gh, bot, at("12:00"))
    bot.script = ["timeout"]  # the digest carrying the mention is lost too
    await run(gh, bot, at("19:30"))
    await run(gh, bot, at("19:30", day=20))
    assert sum("may not have reached you" in d["text"] for d in bot.delivered) == 1
    assert cn.load_state()["deliveries"]["u-owner|pr:83:decision"]["status"] == "uncertain_reported"


async def test_a_merge_supersedes_a_decision_held_by_quiet_hours():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot, at("20:00", day=18))
    gh.raise_pr(84, "fix: x", labels=["needs-owner"])
    await run(gh, bot, at("23:00", day=18))
    gh.merge(84, "fix: x", at=at("01:00"))
    await run(gh, bot, at("07:10"))
    assert all(d["label"] != "Open #84" for d in bot.delivered)
    assert cn.load_state()["deliveries"]["u-owner|pr:84:decision"]["status"] == "superseded"


# ── owner env default + dot-point "went live" notices ─────────────────────────
def _env_owner(cap=50) -> cn.Recipient:
    doc = comm_prefs.normalise({"preset": "everything", "daily_instant_cap": cap}, "admin")
    return cn.Recipient("u-owner", "admin", OWNER_TID, doc, env_default=True)


async def _two_live(people):
    gh, bot = FakeGitHub(), FakeBot()
    await run(gh, bot, at("09:00"), people=people)
    gh.merge(50, "feat: one", "- Alpha thing\n- Beta thing", at=at("12:00"))
    await run(gh, bot, at("12:05"), people=people)
    gh.merge(51, "feat: two", "- Gamma thing", at=at("12:10"))
    await run(gh, bot, at("12:15"), people=people)
    return bot


async def test_live_notices_one_message_per_pr_and_the_wording_rotates():
    bot = await _two_live([_env_owner()])
    assert [d["text"] for d in bot.delivered] == [
        "Some housekeeping went in: One.\nNothing for you to do.",
        "A behind-the-scenes change is in and running: Two.\nNothing for you to do."]
    assert [d["label"] for d in bot.delivered] == ["Open #50", "Open #51"]  # the number lives on the button


async def test_control_saved_prefs_owner_keeps_the_30_minute_spacing():
    saved = await _two_live([owner({"preset": "everything", "daily_instant_cap": 50})])
    assert len(saved.delivered) == 1


async def test_batched_live_notices_keep_each_prs_sentence():
    gh, bot = FakeGitHub(), FakeBot()
    people = [_env_owner()]
    await run(gh, bot, at("09:00"), people=people)
    for n in (60, 61, 62):
        gh.merge(n, f"feat: p{n}", f"- point\n\nFor-The-Household: Thing {n} works now.", at=at("12:00"))
    await run(gh, bot, at("12:05"), people=people)
    assert [d["text"] for d in bot.delivered] == [
        "Three things went in this afternoon.\n• Thing 60 works now. (#60)\n• Thing 61 works now. (#61)\n"
        "• Thing 62 works now. (#62)\nNothing for you to do."]


async def test_a_long_live_notice_stays_under_1000_chars():
    gh, bot = FakeGitHub(), FakeBot()
    people = [_env_owner()]
    await run(gh, bot, at("09:00"), people=people)
    gh.merge(70, "feat: " + "t" * 300, "\n".join(f"- {'x' * 200} {i}" for i in range(6)), at=at("12:00"))
    await run(gh, bot, at("12:05"), people=people)
    assert 0 < len(bot.delivered[0]["text"]) <= 1000


def test_env_default_applies_only_without_saved_prefs(monkeypatch):
    monkeypatch.setenv("ZOE_CHANGE_NOTICES_OWNER_PRESET", "everything")
    monkeypatch.setenv("ZOE_CHANGE_NOTICES_INSTANT_CAP", "50")
    raw, used = comm_prefs.with_env_default(None)
    doc = comm_prefs.normalise(raw, "admin")
    assert used and doc["preset"] == "everything" and doc["daily_instant_cap"] == 50
    assert doc["categories"]["fixed"]["cadence"] == "instant"
    saved, used = comm_prefs.with_env_default({"preset": "weekly"})  # saved prefs win (negative control)
    assert not used and comm_prefs.normalise(saved, "admin")["preset"] == "weekly"
    _, used = comm_prefs.with_env_default({})  # even an empty saved document is a saved choice
    assert not used
    monkeypatch.setenv("ZOE_CHANGE_NOTICES_OWNER_PRESET", "custom")
    monkeypatch.setenv("ZOE_CHANGE_NOTICES_INSTANT_CAP", "999")
    assert comm_prefs.env_owner_default() == {}  # invalid values never apply
    monkeypatch.delenv("ZOE_CHANGE_NOTICES_OWNER_PRESET")
    monkeypatch.delenv("ZOE_CHANGE_NOTICES_INSTANT_CAP")
    assert comm_prefs.with_env_default(None) == (None, False)
    assert comm_prefs.normalise(None, "member") is None  # still owner-only


async def test_env_default_cap_holds():
    two = _env_owner(cap=2)
    bot = await _two_live([two])
    assert len(bot.delivered) == 2
    gh = FakeGitHub()
    gh.merge(52, "feat: three", "- Delta", at=at("13:00"))
    await run(gh, bot, at("13:05"), people=[two])
    assert len(bot.delivered) == 2  # cap of 2 respected: rolls to the digest


async def test_control_stock_owner_gets_no_instant_merged_work():
    assert (await _two_live([owner()])).delivered == []


async def test_quiet_hours_still_hold_overnight_with_the_env_default():
    people = [_env_owner()]
    gh, bot = FakeGitHub(), FakeBot()
    await run(gh, bot, at("09:00"), people=people)
    gh.merge(80, "feat: night", "- Late point", at=at("22:30"))
    await run(gh, bot, at("23:00"), people=people)
    assert bot.delivered == []
    await run(gh, bot, at("07:05", day=20), people=people)
    assert len(bot.delivered) == 1


# ── the assistant's voice (exact text) ────────────────────────────────────────
FIX_BODY = ("## Summary\n- widen the scrubber\n\nFor-The-Household: Long messages no longer make Zoe pause before she "
            "answers.\nNumbers: scrubbing a long message 4.5 s -> 0.03 s; tests that guard it 603\n")
MORNING = datetime(2026, 10, 19, 9, 30, tzinfo=timezone(timedelta(hours=8)))
EVENING = datetime(2026, 10, 19, 19, 0, tzinfo=timezone(timedelta(hours=8)))


def _live(n, title, body=""):
    return {"key": f"pr:{n}:live", "kind": "live", "number": n, "title": title, "url": f"https://x/{n}",
            "category": cn.category_for_title(title), **cn.pr_fields(title, body)}


def _decision_item(n=2010):
    body = ("For-The-Household: The hall panel would dim itself at 9pm.\nOwner-Step: Should it dim at 9pm, or stay "
            "bright?\nIf-No-Answer: It stays as it is.")
    return {"key": f"pr:{n}:decision", "kind": "raised", "category": "decision", "number": n,
            "title": "feat(panel): dim at night", "url": f"https://x/{n}", **cn.pr_fields("", body)}


STALE = timedelta(minutes=60)     # the stuck notice's wait, as the tests configure it


def _stuck_item():
    return {"key": "deploy:stuck:x", "kind": "stuck", "category": "broke", "title": "Merged but not live",
            "summary": cn.stuck_text([{"number": 2005, "title": "fix(reminders): repeat on weekdays only"}],
                                     STALE, {"conclusion": "failure"})}


FIX = _live(2001, "fix(memory): widen the scrubber (#2001)", FIX_BODY)
SNOOZE = _live(2002, "feat(reminders): snooze from the panel", "For-The-Household: You can snooze a reminder from the "
               "wall screen now.\nOwner-Step: Switch ZOE_REMINDER_SNOOZE on when you want to try it.")
CHORE = _live(2003, "test: pin a date in the calendar test")
REVERT = _live(2004, 'Revert "feat(reminders): snooze from the panel" (#2004)',
               "For-The-Household: The snooze button is gone again for now.")


def test_one_fix_with_numbers_technical_and_plain():
    m = cn.render_instant([FIX], "technical", "o/r", turn=0, local=MORNING)
    assert m.text == ("A fix is in and running. Long messages no longer make Zoe pause before she answers.\n"
                      "By the numbers: scrubbing a long message 4.5 s → 0.03 s; tests that guard it 603.\n"
                      "Nothing for you to do.")
    assert (m.url, m.label) == ("https://x/2001", "Open #2001")
    assert cn.render_instant([FIX], "plain", "o/r", turn=1).text == (  # layer 1 only: no numbers
        "Fixed, and live now. Long messages no longer make Zoe pause before she answers.\nNothing for you to do.")


def test_owner_step_ends_the_message_and_missing_or_nothing_visible_is_housekeeping():
    assert cn.render_instant([SNOOZE], "technical", "o/r", turn=2).text == (
        "A new change just went live. You can snooze a reminder from the wall screen now.\n"
        "One thing for you: Switch ZOE_REMINDER_SNOOZE on when you want to try it.")
    hidden = _live(2008, "refactor(router): split the table", "For-The-Household: nothing visible")
    assert cn.render_instant([hidden], "technical", "o/r", turn=0).text == (
        "Some housekeeping went in: Split the table.\nNothing for you to do.")
    assert cn.render_instant([CHORE], "technical", "o/r", turn=2).text == (
        "Housekeeping, nothing you will notice: Pin a date in the calendar test.\nNothing for you to do.")


def test_three_at_once_in_one_message():
    m = cn.render_instant([FIX, SNOOZE, CHORE], "technical", "o/r", turn=1, local=MORNING)
    assert m.text == ("This morning, three changes went live.\n"
                      "• Long messages no longer make Zoe pause before she answers. (#2001)\n"
                      "   By the numbers: scrubbing a long message 4.5 s → 0.03 s; tests that guard it 603.\n"
                      "• You can snooze a reminder from the wall screen now. (#2002)\n"
                      "And one bit of housekeeping (#2003).\n"
                      "For you:\n"
                      "• Switch ZOE_REMINDER_SNOOZE on when you want to try it.")
    assert m.label == "Open on GitHub" and len(m.keys) == 3


def test_a_revert_and_a_stuck_deploy_lead_plainly():
    assert cn.render_instant([REVERT], "technical", "o/r", turn=0).text == (
        'Heads up first: "Snooze from the panel" was rolled back and is no longer running. The snooze button is '
        "gone again for now.\nNothing for you to do.")
    assert cn.render_instant([_stuck_item()], "technical", "o/r").text == (
        'Heads up: "Repeat on weekdays only" did not go live. It was merged over '
        f"{cn.stale_words(STALE)} ago and is not running "
        "yet. The last deploy failed. It may need a look. (#2005)")
    m = cn.render_instant([FIX, CHORE, _decision_item(), _stuck_item()], "technical", "o/r", turn=0, local=MORNING)
    assert m.text == (  # bad news first, never buried; then what waits on you; then the good news
        'Heads up: "Repeat on weekdays only" did not go live. It was merged over '
        f"{cn.stale_words(STALE)} ago and is not running "
        "yet. The last deploy failed. It may need a look. (#2005)\n"
        "Waiting on you:\n"
        "• The hall panel would dim itself at 9pm. Should it dim at 9pm, or stay bright? If you leave it, it stays "
        "as it is. (#2010)\n"
        "Also, two things went in this morning.\n"
        "• Long messages no longer make Zoe pause before she answers. (#2001)\n"
        "   By the numbers: scrubbing a long message 4.5 s → 0.03 s; tests that guard it 603.\n"
        "Plus one bit of housekeeping (#2003).")  # no closing line: the stuck deploy already says it needs a look


def test_a_decision_is_one_question_with_its_default():
    m = cn.render_instant([_decision_item()], "technical", "o/r", turn=0)
    assert m.text == ("I need your OK on something. The hall panel would dim itself at 9pm.\n"
                      "Should it dim at 9pm, or stay bright?\nIf you leave it, it stays as it is.")
    assert m.label == "Open #2010"


def _digest_items():
    return [FIX, SNOOZE, CHORE, _live(2006, "chore(deps): bump httpx"), REVERT, _decision_item(),
            {"key": "pr:2020:raised", "kind": "raised", "category": "raised", "number": 2020, "title": "fix: x",
             "draft": True}]


def test_digest_detailed_and_plain_with_housekeeping_collapsed():
    msgs = cn.render_digest(_digest_items(), [], comm_prefs.normalise(None, "admin"), opener=0, repo_name="o/r",
                            local=EVENING)
    assert [m.text for m in msgs] == [
        "Good evening. Here's today: five changes went live.\n"
        "One new change is open and not merged yet (one is a draft).\n"
        'Heads up first: "Snooze from the panel" was rolled back and is no longer running. The snooze button is '
        "gone again for now. (#2004)\n\n"
        "Waiting on you:\n"
        "• The hall panel would dim itself at 9pm. Should it dim at 9pm, or stay bright? If you leave it, it stays "
        "as it is. (#2010)\n\n"
        "For you to do:\n"
        "• Switch ZOE_REMINDER_SNOOZE on when you want to try it.\n\n"
        "What went in:\n"
        "• Long messages no longer make Zoe pause before she answers. (#2001)\n"
        "   By the numbers: scrubbing a long message 4.5 s → 0.03 s; tests that guard it 603.\n"
        "• You can snooze a reminder from the wall screen now. (#2002)\n"
        "Plus two bits of housekeeping (#2003, #2006)."]
    assert sorted(k for m in msgs for k in m.keys) == sorted(i["key"] for i in _digest_items())
    plain = comm_prefs.normalise({"categories": {c: {"detail": "plain"} for c in comm_prefs.CATEGORIES}}, "admin")
    text = cn.render_digest([FIX, CHORE, _live(2006, "chore(deps): bump httpx")], [], plain, opener=1,
                            repo_name="o/r", local=EVENING)[0].text
    assert text == ("Good evening. Here's how today went: three changes went live.\n"
                    "• Long messages no longer make Zoe pause before she answers.\n"
                    "Plus two bits of housekeeping.\nNothing for you to do.")  # plain: no numbers, no #N
    weekly = cn.render_digest([CHORE], [], plain, opener=2, repo_name="o/r", period="this week")[0].text
    assert weekly == ("Here's the round-up for this week: one change went live.\n"
                      "One bit of housekeeping, nothing you will notice.\nNothing for you to do.")


def test_pointer_only_mode_is_unchanged():
    ptr = comm_prefs.normalise({"categories": {c: {"detail": "pointer"} for c in comm_prefs.CATEGORIES}}, "admin")
    msgs = cn.render_digest([FIX, CHORE], [], ptr, opener=0, repo_name="o/r", local=EVENING)
    assert [m.text for m in msgs] == ["Zoe has 2 updates for you. Ask me \"what's new?\"."]
    assert cn.render_instant([FIX], "pointer", "o/r").text == cn.POINTER_TEXT


def test_size_bounds_hold_with_long_inputs():
    big = [_live(3000 + n, "fix(area): " + "t" * 300, "For-The-Household: " + "word " * 200 + "\nNumbers: "
                 + "; ".join(f"{'x' * 90} {k} -> {k + 1}" for k in range(5)) + "\nOwner-Step: " + "do " * 120)
           for n in range(40)]
    m = cn.render_instant(big, "technical", "o/r", turn=0, local=MORNING)
    assert len(m.text) <= cn.CHUNK_CHARS and "more on GitHub." in m.text and 0 < len(m.keys) < 40
    _assert_landed(big, _drain_instant(big))  # every step shown; the notices it could not hold follow up
    assert len(cn.render_instant(big[:1], "technical", "o/r").text) <= cn.CHUNK_CHARS
    msgs = cn.render_digest(big, [], comm_prefs.normalise(None, "admin"), opener=0, repo_name="o/r", local=EVENING)
    assert 1 < len(msgs) <= cn.MAX_CHUNKS and all(len(x.text) <= cn.CHUNK_CHARS for x in msgs)
    assert msgs[-1].text.endswith(cn.FOLLOWS)  # every item here has a step: none is hidden in "...and N more"
    rest, landed = list(big), []
    while rest:  # what the next ticks do: the notices whose steps did not fit follow, each landing once
        got = [k for x in cn.render_digest(rest, [], comm_prefs.normalise(None, "admin"), opener=0, repo_name="o/r")
               for k in x.keys]
        assert got
        landed += got
        rest = [i for i in rest if i["key"] not in got]
    assert sorted(landed) == sorted(i["key"] for i in big)


def test_rotation_is_deterministic_and_never_repeats_back_to_back():
    starts = [cn.render_instant([FIX], "technical", "o/r", turn=t).text.split(".")[0] for t in range(6)]
    assert starts == ["A fix is in and running", "Fixed, and live now", "A fix just went live"] * 2
    assert all(a != b for a, b in zip(starts, starts[1:]))
    heads = [cn.render_digest([CHORE], [], comm_prefs.normalise(None, "admin"), opener=t, repo_name="o/r")[0]
             .text.split(":")[0] for t in range(3)]
    assert heads == ["Here's today", "Here's how today went", "Here's the round-up for today"]


_GUARD_CC = re.compile(r"(?:^|[•\"'(]\s?)(?:feat|fix|perf|docs?|tests?|chore|ci|build|refactor|style|deps|revert)"
                       r"(?:\([^)]*\))?!?:\s|\b(?:feat|fix|docs?|chore|refactor)\([^)]*\)!?:", re.I | re.M)
_GUARD_HEX = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")


def _guard_texts():
    titles = ["fix(memory): widen `scrub` 1a2b3c4d (#77)", 'Revert "feat(x): y" (#78)', "chore: bump 0123abcd4567",
              "docs: a", "feat!: big", "plain title with #79 inside"]
    bodies = ["", "For-The-Household: nothing visible", "For-The-Household: Now `faster` (#80) at deadbeef12.\n"
              "Numbers: `t` 4 -> 2 at cafe12345; b 1 -> 2\nOwner-Step: run `this` (#81)"]
    items = [_live(100 + i * 3 + j, t, b) for i, t in enumerate(titles) for j, b in enumerate(bodies)]
    out = [cn.render_instant([i], d, "o/r", turn=k).text for k, i in enumerate(items) for d in ("technical", "plain")]
    out += [cn.render_instant(items[k:k + 4], "technical", "o/r", turn=k).text for k in range(0, len(items), 4)]
    out += [m.text for m in cn.render_digest(items + [_stuck_item(), _decision_item()], [],
                                              comm_prefs.normalise(None, "admin"), opener=0, repo_name="o/r")]
    return out


def test_guard_no_commit_syntax_no_hash_no_backtick_and_each_number_once():
    for text in _guard_texts():
        assert not _GUARD_CC.search(text), text
        assert not _GUARD_HEX.search(text), text
        assert "`" not in text, text
        for n in set(re.findall(r"#\d+", text)):
            assert len(re.findall(re.escape(n) + r"\b", text)) == 1, (n, text)  # the PR number at most once
        for n in ("#77", "#78", "#79", "#80", "#81"):  # numbers from inside titles and bodies never leak
            assert n not in text, text


def test_control_without_dejargon_the_guard_goes_red(monkeypatch):
    monkeypatch.setattr(cn, "_dejargon", cn._plain)
    assert any(_GUARD_CC.search(t) or _GUARD_HEX.search(t) for t in _guard_texts())


# ── why a tick finds nobody: a reason code, counts only ───────────────────────
async def test_recipient_reason_codes():
    cases = [
        (FakeDB([{"id": "u-owner", "role": "admin"}], {"u-owner": {"telegram_id": "111"}}), "ok"),
        (FakeDB([{"id": "u-owner", "role": "admin"}], {"u-owner": {}}), "no_link"),
        (FakeDB([{"id": "u-owner", "role": "Admin"}], {"u-owner": {"telegram_id": "111"}}), "owner_role_spelling"),
        (FakeDB([{"id": "u-a", "role": "member"}], {"u-a": {"telegram_id": "222"}}), "no_admin"),
    ]
    for db, want in cases:
        rs = await cn.load_recipients(db)
        assert rs.diag["reason"] == want, (want, rs.diag)
    assert (await cn.load_recipients(cases[1][0])).diag == {"reason": "no_link", "admins": 1, "linked": 0,
                                                            "owner_role_other_case": 0}


async def test_no_recipients_audit_line_carries_the_reason():
    gh, bot = FakeGitHub(), FakeBot()
    db = FakeDB([{"id": "u-owner", "role": "admin"}], {"u-owner": {}})

    async def recipients():
        return await cn.load_recipients(db)

    await cn.tick(gh=gh, sender=bot.sender(), recipients=recipients, now=at("09:00"), sleep=_noop_sleep, run_mode="on")
    gh.raise_pr(500, "fix: x", labels=["needs-owner"])
    await cn.tick(gh=gh, sender=bot.sender(), recipients=recipients, now=at("12:00"), sleep=_noop_sleep, run_mode="on")
    lines = [json.loads(x) for x in (cn.state_dir() / "audit.jsonl").read_text().splitlines()]
    nr = [a for a in lines if a["event"] == "no_recipients"]
    assert nr and {k: nr[0][k] for k in ("reason", "admins", "linked", "new")} == \
        {"reason": "no_link", "admins": 1, "linked": 0, "new": 1}
    assert bot.calls == 0


# ── an owner step is never lost: every kind, every form, and through truncation ──
STEP = "Restart the panel."
REVERT_STEP = _live(2030, 'Revert "fix(panel): faster wake" (#2030)',
                    f"For-The-Household: The panel wakes as it did before.\nOwner-Step: {STEP}")


def test_a_revert_step_shows_in_every_form():
    single = cn.render_instant([REVERT_STEP], "technical", "o/r", turn=0).text
    assert single.endswith(f"One thing for you: {STEP}")
    batch = cn.render_instant([REVERT_STEP, FIX, CHORE], "technical", "o/r", turn=0, local=MORNING).text
    assert batch == ('Heads up first: "Faster wake" was rolled back and is no longer running. The panel wakes as it '
                     "did before. (#2030)\n"
                     "Also, two things went in this morning.\n"
                     "• Long messages no longer make Zoe pause before she answers. (#2001)\n"
                     "   By the numbers: scrubbing a long message 4.5 s → 0.03 s; tests that guard it 603.\n"
                     "Plus one bit of housekeeping (#2003).\n"
                     f"For you:\n• {STEP}")
    for prefs in (comm_prefs.normalise(None, "admin"),
                  comm_prefs.normalise({"categories": {c: {"detail": "plain"} for c in comm_prefs.CATEGORIES}},
                                       "admin")):
        text = "\n".join(m.text for m in cn.render_digest([REVERT_STEP, CHORE], [], prefs, opener=0, repo_name="o/r"))
        assert f"before.{' (#2030)' if '#' in text else ''}\n   For you: {STEP}" in text and cn.NOTHING_TO_DO not in text  # inline with the bad news


def _long(n, step=True, hh=240):
    body = f"For-The-Household: Item {n} " + "is a long household sentence " * (hh // 30) + "\n"
    body += "Numbers: " + "; ".join(f"measure {k} {'x' * 40} {k} -> {k + 1}" for k in range(3)) + "\n"
    if step:
        body += f"Owner-Step: Step {n} " + "please do this thing " * 8 + "now."
    return _live(4000 + n, f"fix: item {n}", body)


def test_three_long_items_keep_all_three_steps():
    items = [_long(1), _long(2), _long(3)]
    m = cn.render_instant(items, "technical", "o/r", turn=0, local=MORNING)
    assert len(m.text) <= cn.CHUNK_CHARS and len(m.keys) == 3
    assert m.text == (
        "Three things went in this morning.\n"
        "• Item 1 is a long household sentence is a long household sentence is a long household sentence is a…"
        " (#4001)\n"
        "• Item 2 is a long household sentence is a long household sentence is a long household sentence is a…"
        " (#4002)\n"
        "• Item 3 is a long household sentence is a long household sentence is a long household sentence is a…"
        " (#4003)\n"
        "For you:\n" + "\n".join(f"• {i['owner_step']}" for i in items))


def _drain_instant(items, detail="technical"):
    """What ``_deliver_instant`` does with one batch, without its 3-message cap: send until every key lands."""
    msgs, rest = [], list(items)
    for turn in range(len(items) + 1):
        if not rest:
            break
        m = cn.render_instant(rest, detail, "o/r", turn=turn, local=MORNING)
        assert m.keys, "every message lands at least one notice"
        msgs.append(m)
        rest = [i for i in rest if i["key"] not in m.keys]
    assert not rest
    return msgs


def _assert_landed(items, msgs):
    landed = [k for m in msgs for k in m.keys]
    assert sorted(landed) == sorted(i["key"] for i in items)  # (a) every key exactly once
    by_key = {i["key"]: i for i in items}
    for m in msgs:
        assert len(m.text) <= cn.CHUNK_CHARS  # (c)
        for k in m.keys:
            step = by_key[k].get("owner_step")
            assert not step or step in m.text, (k, m.text)  # (b) its step is in the message that landed it


def test_steps_too_long_for_one_message_follow_up_and_land_once():
    items = [_long(n, hh=400) for n in range(1, 7)]
    msgs = cn.render_instant(items, "technical", "o/r", turn=0, local=MORNING)
    assert 0 < len(msgs.keys) < 6  # not everything fits: the rest is NOT counted as landed
    _assert_landed(items, _drain_instant(items))


async def test_a_tick_sends_the_follow_up_and_marks_each_key_sent_once():
    gh, bot = FakeGitHub(), FakeBot()
    people = [_env_owner()]
    await run(gh, bot, at("09:00"), people=people)
    for n in range(1, 7):
        it = _long(n, hh=400)
        body = f"For-The-Household: {it['household']}\nOwner-Step: {it['owner_step']}"
        gh.merge(4100 + n, f"fix: item {n}", body, at=at("12:00"))
    await run(gh, bot, at("12:05"), people=people)
    assert 1 < len(bot.delivered) <= cn.MAX_CHUNKS
    st = cn.load_state()
    assert {d["status"] for k, d in st["deliveries"].items() if ":live" in k} == {"sent"}
    text = "\n".join(d["text"] for d in bot.delivered)
    for n in range(1, 7):
        assert text.count(f"Step {n} please") == 1


def test_property_random_batches_never_lose_a_step():
    rnd = random.Random(20261116)
    kinds = ("live", "revert", "chore", "decision", "stuck")
    for trial in range(300):
        items = []
        for n in range(rnd.randint(2, 9)):
            kind, num = rnd.choice(kinds), 5000 + trial * 10 + n
            hh = "word " * rnd.randint(0, 70)
            step = ("Do " + "this " * rnd.randint(1, 40)).strip() if rnd.random() < 0.6 else ""
            body = (f"For-The-Household: {hh}\n" if hh.strip() else "") + (f"Owner-Step: {step}\n" if step else "")
            body += "Numbers: " + "; ".join(f"m{k} {k} -> {k + 1}" for k in range(rnd.randint(0, 3)))
            if kind == "stuck":
                items.append({**_stuck_item(), "key": f"deploy:stuck:{num}"})
            elif kind == "decision":
                items.append({**_live(num, "feat: ask", body), "kind": "raised", "category": "decision",
                              "key": f"pr:{num}:decision"})
            else:
                title = {"live": "fix: thing", "revert": 'Revert "feat: thing"', "chore": "chore: thing"}[kind]
                items.append(_live(num, title, body if kind != "chore" else body.split("Numbers")[0]))
        for detail in ("technical", "plain"):
            _assert_landed(items, _drain_instant(items, detail))
        prefs = comm_prefs.normalise(None, "admin")
        rest, sent = list(items), []
        for _round in range(len(items) + 1):
            if not rest:
                break
            msgs = cn.render_digest(rest, [], prefs, opener=trial, repo_name="o/r")
            assert len(msgs) <= cn.MAX_CHUNKS
            got = {k for m in msgs for k in m.keys}
            assert got, "every digest lands at least one notice"
            sent += msgs
            rest = [i for i in rest if i["key"] not in got]
        assert not rest
        for i in items:  # (b) for the digest: a step lands in the same chunk as its key
            if i.get("owner_step"):
                assert any(i["key"] in m.keys and i["owner_step"] in m.text for m in sent), i["key"]
        landed = [k for m in sent for k in m.keys]
        assert sorted(landed) == sorted(i["key"] for i in items) and all(len(m.text) <= cn.CHUNK_CHARS for m in sent)


async def test_a_digest_whose_steps_overflow_follows_up_next_tick():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    for n in range(1, 31):
        gh.merge(4200 + n, f"fix: item {n}", f"For-The-Household: Item {n} works.\nOwner-Step: Step {n} "
                 + "please do this thing " * 7 + "now.", at=at("10:00"))
    await run(gh, bot, at("19:30"))
    first = len(bot.delivered)
    assert first == cn.MAX_CHUNKS and cn.FOLLOWS in bot.delivered[-1]["text"]
    for when in ("19:40", "19:50", "20:00", "20:10"):
        await run(gh, bot, at(when))
    text = "\n".join(d["text"] for d in bot.delivered)
    for n in range(1, 31):
        assert text.count(f"Step {n} please") == 1
    st = cn.load_state()
    assert {d["status"] for k, d in st["deliveries"].items() if ":live" in k} == {"sent"}
    before = len(bot.delivered)
    await run(gh, bot, at("20:20"))
    assert len(bot.delivered) == before  # nothing more once all landed


# ── every instant limit is rechecked before each message, follow-ups included ──
async def _six_long_steps(gh):
    for n in range(1, 7):
        it = _long(n, hh=400)
        gh.merge(4300 + n, f"fix: item {n}",
                 f"For-The-Household: {it['household']}\nOwner-Step: {it['owner_step']}", at=at("12:00"))


def _audit_events(name):
    return [json.loads(x) for x in (cn.state_dir() / "audit.jsonl").read_text().splitlines()
            if f'"event": "{name}"' in x]


async def test_cap_with_one_left_sends_one_and_the_rest_rolls_to_the_digest_with_steps():
    gh, bot = FakeGitHub(), FakeBot()
    people = [_env_owner(cap=1)]
    await run(gh, bot, at("09:00"), people=people)
    await _six_long_steps(gh)
    await run(gh, bot, at("12:05"), people=people)
    assert len(bot.delivered) == 1  # the follow-ups would have gone over the cap
    first = {k for k, d in cn.load_state()["deliveries"].items() if d["status"] == "sent"}
    rolled = {a["key"] for a in _audit_events("rolled_to_digest")}
    assert rolled and all(a["reason"] == "cap" for a in _audit_events("rolled_to_digest"))
    assert {k.split("|", 1)[1] for k in first}.isdisjoint(rolled)
    for when in ("19:30", "19:40", "19:50", "20:00"):
        await run(gh, bot, at(when), people=people)
    text = "\n".join(d["text"] for d in bot.delivered)
    for n in range(1, 7):
        assert text.count(f"Step {n} please") == 1  # every step intact, once
    st = cn.load_state()
    assert {d["status"] for k, d in st["deliveries"].items() if ":live" in k} == {"sent"}


async def test_cap_with_none_left_sends_nothing_instant():
    gh, bot = FakeGitHub(), FakeBot()
    people = [_env_owner(cap=0)]
    await run(gh, bot, at("09:00"), people=people)
    await _six_long_steps(gh)
    await run(gh, bot, at("12:05"), people=people)
    assert bot.delivered == [] and len(_audit_events("rolled_to_digest")) == 6


async def test_the_kill_file_appearing_after_the_first_message_stops_the_rest():
    gh, bot = FakeGitHub(), FakeBot()
    people = [_env_owner()]
    await run(gh, bot, at("09:00"), people=people)
    await _six_long_steps(gh)
    send = bot.sender()

    async def then_kill(tid, msg):
        out = await send(tid, msg)
        cn.set_killed(True)
        return out

    async def recipients():
        return people

    await cn.tick(gh=gh, sender=then_kill, recipients=recipients, now=at("12:05"), sleep=_noop_sleep, run_mode="on")
    assert len(bot.delivered) == 1
    st = cn.load_state()["deliveries"]
    assert sorted(d["status"] for k, d in st.items() if ":live" in k).count("queued") >= 1
    assert not _audit_events("rolled_to_digest")  # killed = stopped, not rerouted


def test_trailers_inside_tilde_fences_or_indented_code_are_not_trailers():
    assert cn.household("~~~\nFor-The-Household: an example\n~~~\nreal text") == ""
    assert cn.household("```md\nFor-The-Household: an example\n```") == ""
    assert cn.household("    For-The-Household: an indented code line") == ""
    assert cn.household("~~~\nFor-The-Household: an unclosed fence") == ""
    assert cn.household("~~~\nx\n~~~\nFor-The-Household: After the fence.") == "After the fence."  # control


async def test_property_a_random_remaining_cap_is_never_exceeded():
    rnd = random.Random(1116)
    for trial in range(120):
        cap = rnd.randint(0, 6)
        used = rnd.randint(0, cap)
        items = []
        for n in range(rnd.randint(1, 8)):
            num = 6000 + trial * 10 + n
            step = ("Do " + "this " * rnd.randint(1, 45)).strip() if rnd.random() < 0.7 else ""
            body = f"For-The-Household: {'word ' * rnd.randint(0, 70)}\n" + (f"Owner-Step: {step}" if step else "")
            items.append({**_live(num, rnd.choice(("fix: a", 'Revert "feat: b"', "feat: c")), body),
                          "key": f"pr:{num}:live"})
        r = _env_owner(cap=cap)
        st = cn.empty_state()
        st["notices"] = {i["key"]: i for i in items}
        st["deliveries"] = {f"u-owner|{i['key']}": {"status": "queued", "lane": "instant", "attempts": 0,
                                                    "next_at": None, "at": "2026-10-19T04:00:00Z"} for i in items}
        now = at("12:05")
        today = now.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        st["instant_sent"]["u-owner"] = [f"{today}T08:00:00+08:00"] * used
        sent: list[cn.Message] = []

        async def sender(_tid, msg):
            sent.append(msg)
            return "sent"

        c = cn.Ctx(st=st, now=now, mode="on", sender=sender, sleep=_noop_sleep, repo="o/r")
        await cn._deliver_instant(c, r)
        left = cap - used
        base = 0 if left <= 0 else 1 if len(items) >= cn.BATCH_AT else len(items)  # exactly what main sends
        assert base <= len(sent) <= max(base, min(left, cn.MAX_CHUNKS)), (trial, cap, used, len(sent))
        landed = [k for m in sent for k in m.keys]
        assert len(landed) == len(set(landed))
        rolled = {k.split("|", 1)[1] for k, d in st["deliveries"].items() if d["lane"] == "digest"}
        assert rolled.isdisjoint(landed)
        assert set(landed) | rolled | {k.split("|", 1)[1] for k, d in st["deliveries"].items()
                                       if d["status"] == "queued"} == {i["key"] for i in items}
        for m in sent:
            assert len(m.text) <= cn.CHUNK_CHARS
            for k in m.keys:
                step = st["notices"][k].get("owner_step")
                assert not step or step in m.text


# ── the base's instant timing is unchanged; only follow-ups are rechecked ──────
def _short(n, cat="fixed", kind="live"):
    title = {"fixed": "fix: short", "decision": "feat: ask"}[cat]
    it = {**_live(n, title, f"For-The-Household: Short {n}."), "category": cat, "kind": kind,
          "key": f"pr:{n}:{'decision' if cat == 'decision' else 'live'}", "summary": f"Short {n}.", "bullets": []}
    return it


async def _instant_run(mod, items, *, cap, used, env_default=True, last=None, batch_at=None, monkeypatch=None):
    """One ``_deliver_instant`` of ``mod`` (this module or main's) for an owner with ``cap - used`` slots left."""
    if batch_at is not None:
        monkeypatch.setattr(mod, "BATCH_AT", batch_at)
    doc = comm_prefs.normalise({"preset": "everything", "daily_instant_cap": cap}, "admin")
    r = mod.Recipient("u-owner", "admin", OWNER_TID, doc, env_default=env_default)
    st = mod.empty_state()
    st["notices"] = {i["key"]: dict(i) for i in items}
    st["deliveries"] = {f"u-owner|{i['key']}": {"status": "queued", "lane": "instant", "attempts": 0,
                                                "next_at": None, "at": "2026-10-19T04:00:00Z"} for i in items}
    now = at("12:05")
    st["instant_sent"]["u-owner"] = ["2026-10-19T08:00:00+08:00"] * used
    st["last_instant"] = dict(last or {})
    sent = []

    async def sender(_tid, msg):
        sent.append(tuple(sorted(msg.keys)))
        return "sent"

    before = (mod.state_dir() / "audit.jsonl").read_text().splitlines() if (mod.state_dir() / "audit.jsonl").exists() \
        else []
    await mod._deliver_instant(mod.Ctx(st=st, now=now, mode="on", sender=sender, sleep=_noop_sleep, repo="o/r"), r)
    after = (mod.state_dir() / "audit.jsonl").read_text().splitlines()[len(before):]
    events = sorted((a["event"], str(a.get("key", a.get("keys"))), a.get("reason", "")) for a in map(json.loads, after)
                    if a["event"] != "send")
    marked = sorted(k.split("|", 1)[1] for k, d in st["deliveries"].items() if d["status"] == "sent")
    rolled = sorted(k.split("|", 1)[1] for k, d in st["deliveries"].items() if d["lane"] == "digest")
    return sorted(sent), marked, rolled, events


def _cases():
    ten_min_ago = (at("12:05") - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {  # name: (items, kwargs, expected messages, expected rolled) - read off main's _deliver_instant
        "1 slot left, 2 ready -> 2 sent": ([_short(1), _short(2)], {"cap": 3, "used": 2},
                                          [("pr:1:live",), ("pr:2:live",)], []),
        "0 slots left -> none, all rolled": ([_short(1), _short(2), _short(3)], {"cap": 2, "used": 2}, [],
                                             ["pr:1:live", "pr:2:live", "pr:3:live"]),
        "3 ready -> one batched message": ([_short(1), _short(2), _short(3)], {"cap": 6, "used": 5},
                                           [("pr:1:live", "pr:2:live", "pr:3:live")], []),
        "batching disabled, 4 ready -> 4 sent": ([_short(n, "decision", "raised") for n in (31, 32, 33, 34)],
                                                 {"cap": 6, "used": 0, "batch_at": 99},
                                                 [(f"pr:{n}:decision",) for n in (31, 32, 33, 34)], []),
        "spacing: fixed 10 min ago rolls, the decision goes": (
            [_short(1), _short(2), _short(40, "decision", "raised")],
            {"cap": 6, "used": 0, "env_default": False, "last": {"u-owner|fixed": ten_min_ago}},
            [("pr:40:decision",)], ["pr:1:live", "pr:2:live"]),
    }


@pytest.mark.parametrize("name", list(_cases()))
async def test_base_instant_timing_table(name, monkeypatch):
    items, kw, want_msgs, want_rolled = _cases()[name]
    sent, marked, rolled, _events = await _instant_run(cn, items, monkeypatch=monkeypatch, **kw)
    assert sent == sorted(want_msgs) and rolled == want_rolled
    assert marked == sorted(k for m in want_msgs for k in m)


def _main_module(tmp_path, monkeypatch):
    import importlib.util
    import pathlib
    import subprocess
    import sys

    repo = pathlib.Path(__file__).resolve().parents[3]
    try:
        src = subprocess.run(["git", "-C", str(repo), "show", "origin/main:services/zoe-data/change_notices.py"],
                             capture_output=True, text=True, timeout=20, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("origin/main is not available here (offline or shallow checkout)")
    path = tmp_path / "change_notices_main.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("change_notices_main", path)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "change_notices_main", mod)  # dataclasses resolve their module by name
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name", list(_cases()))
async def test_differential_against_main_when_nothing_is_held_back(name, tmp_path, monkeypatch):
    main_cn = _main_module(tmp_path, monkeypatch)
    items, kw, _m, _r = _cases()[name]
    monkeypatch.setenv("ZOE_CHANGE_NOTICES_STATE_DIR", str(tmp_path / "base"))
    base = await _instant_run(main_cn, items, monkeypatch=monkeypatch, **kw)
    monkeypatch.setenv("ZOE_CHANGE_NOTICES_STATE_DIR", str(tmp_path / "new"))
    new = await _instant_run(cn, items, monkeypatch=monkeypatch, **kw)
    assert new == base  # same messages (as key sets), keys marked sent, rolls and audit events


# ── notices stored before the upgrade keep their line ─────────────────────────
def test_a_legacy_notice_falls_back_to_its_stored_summary():
    legacy = {"key": "pr:7:live", "kind": "live", "number": 7, "title": "fix(music): resume after a call",
              "summary": "Music `resumes` after a call (#7).", "bullets": ["Music resumes"], "url": "https://x/7",
              "category": "fixed"}  # the shape main stores: no household field
    assert cn.render_instant([legacy], "technical", "o/r", turn=0).text == (
        "A fix is in and running. Music resumes after a call.\nNothing for you to do.")


async def test_a_legacy_pending_merge_going_live_keeps_its_summary():
    gh, bot = FakeGitHub(), FakeBot()
    people = [_env_owner()]
    await run(gh, bot, at("09:00"), people=people)
    st = cn.load_state()
    st["pending_live"]["8"] = {"number": 8, "title": "fix(music): resume after a call", "sha": f"{8:040x}",
                               "url": "https://x/8", "merged_at": "2026-10-19T03:00:00Z", "base": "main",
                               "summary": "Music resumes after a call.", "bullets": ["Music resumes"],
                               "category": "fixed"}
    cn.save_state(st)
    gh.ancestors.add(f"{8:040x}")
    await run(gh, bot, at("12:05"), people=people)
    assert [d["text"] for d in bot.delivered] == ["A fix is in and running. Music resumes after a call.\n"
                                                  "Nothing for you to do."]


# ── no message ever exceeds the route's limit, whatever gets appended ─────────
def _ask(n, hw, aw):
    body = (f"For-The-Household: Q{n - 7000} " + "word " * hw + "\nOwner-Step: A " + "this " * aw
            + "\nIf-No-Answer: It stays as it is.")
    return {"key": f"pr:{n}:decision", "kind": "raised", "category": "decision", "number": n, "title": "feat: ask",
            "url": f"https://x/{n}", **cn.pr_fields("", body)}


# (decisions, news items, step words, household words, news words): each made a 1004-character last chunk on
# 742df7879 (found by a seeded search there), carrying the count, the follow-up AND the closing sentence.
REVIEWER_SHAPES = ((16, 15, 41, 11, 32), (15, 12, 29, 21, 14), (14, 20, 36, 14, 56))


@pytest.mark.parametrize("shape", REVIEWER_SHAPES)
def test_a_last_chunk_carrying_count_follow_up_and_closing_stays_under_the_limit(shape):
    asks, news, aw, hw, nw = shape
    items = [_ask(7000 + n, hw, aw) for n in range(asks)] + [
        _live(7100 + n, "fix: t", "For-The-Household: N " + "here " * nw) for n in range(news)]
    msgs = cn.render_digest(items, [], comm_prefs.normalise(None, "admin"), opener=0, repo_name="o/r")
    last = msgs[-1].text
    assert len(msgs) == cn.MAX_CHUNKS
    assert cn.FOLLOWS in last and "more on GitHub." in last and last.endswith(cn.NOTHING_ELSE)
    assert all(len(m.text) <= cn.CHUNK_CHARS for m in msgs), [len(m.text) for m in msgs]


async def test_an_oversize_message_is_never_sent_nor_refused_and_its_keys_wait(monkeypatch):
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    gh.raise_pr(900, "fix: x", labels=["needs-owner"])
    real = cn.render_instant

    def too_long(items, *a, **kw):
        m = real(items, *a, **kw)
        return cn.Message("x" * (cn.CHUNK_CHARS + 1), m.keys, m.url, m.label)

    monkeypatch.setattr(cn, "render_instant", too_long)
    await run(gh, bot, at("12:00"))
    monkeypatch.setattr(cn, "render_instant", real)
    assert bot.calls == 0
    assert cn.load_state()["deliveries"]["u-owner|pr:900:decision"]["status"] == "queued"
    held = _audit_events("oversize_held")
    assert held and held[0]["code"] == "over_chunk_chars" and "text" not in held[0]
    await run(gh, bot, at("12:10"))
    assert len(bot.delivered) == 1  # sent on the next tick, once the renderer behaves


def test_property_no_message_exceeds_the_route_limit():
    rnd = random.Random(1117)
    plain = comm_prefs.normalise({"categories": {c: {"detail": "plain"} for c in comm_prefs.CATEGORIES}}, "admin")
    for trial in range(250):
        items = []
        for n in range(rnd.randint(1, 40)):
            num = 8000 + trial * 50 + n
            kind = rnd.choice(("live", "revert", "chore", "decision", "stuck"))
            step = ("Do " + "this " * rnd.randint(1, 45)).strip() if rnd.random() < 0.5 else ""
            body = (f"For-The-Household: {'word ' * rnd.randint(0, 70)}\n" + (f"Owner-Step: {step}\n" if step else "")
                    + "Numbers: " + "; ".join(f"m{k} {'y' * rnd.randint(0, 80)} {k} -> {k + 1}"
                                              for k in range(rnd.randint(0, 4))))
            if kind == "stuck":
                items.append({**_stuck_item(), "key": f"deploy:stuck:{num}"})
            elif kind == "decision":
                items.append({**_live(num, "feat: ask", body), "kind": "raised", "category": "decision",
                              "key": f"pr:{num}:decision"})
            else:
                title = {"live": "fix: thing", "revert": 'Revert "feat: thing"', "chore": "chore: thing"}[kind]
                items.append(_live(num, title + " " + "t" * rnd.randint(0, 200), body))
        rnd.shuffle(items)
        prefs = rnd.choice((comm_prefs.normalise(None, "admin"), plain))
        period = rnd.choice(("today", "this week"))
        unc = [i for i in items if i["kind"] != "stuck" and rnd.random() < 0.25]  # "may not have reached you"
        rest, rest_unc, sent = [i for i in items if i not in unc], list(unc), []
        for _round in range(len(items) + 1):  # the digest and its follow-ups on later ticks
            if not rest and not rest_unc:
                break
            msgs = cn.render_digest(rest, rest_unc, prefs, opener=trial, repo_name="o/r", local=EVENING,
                                    period=period)
            got = {k for m in msgs for k in m.keys}
            assert got and len(msgs) <= cn.MAX_CHUNKS
            for m in msgs:  # an uncertain notice is marked only in a message that shows it
                for u in rest_unc:
                    if u["key"] in m.keys:
                        assert _uncertain_line(u) in m.text, (trial, u["key"])
            sent += msgs
            rest = [i for i in rest if i["key"] not in got]
            rest_unc = [i for i in rest_unc if i["key"] not in got]
        assert not rest and not rest_unc
        assert sorted(k for m in sent for k in m.keys) == sorted(i["key"] for i in items)  # each key exactly once
        digest_sent = list(sent)
        for detail in ("technical", "plain"):
            drained = _drain_instant(items, detail)
            assert sorted(k for m in drained for k in m.keys) == sorted(i["key"] for i in items)
            sent += drained
        for m in sent:
            assert len(m.text) <= telegram_notify.MAX_TEXT, (trial, len(m.text))
        by_key = {i["key"]: i for i in items}
        unc_keys = {i["key"] for i in unc}
        for m in sent:
            for k in m.keys:
                if k in unc_keys and m in digest_sent:
                    continue  # a digest mention of an uncertain notice is its subject line, checked above
                step = by_key[k].get("owner_step")
                assert not step or step in m.text, (trial, k)


# ── uncertain notices: one line each, must-keep ───────────────────────────────
def _uncertain_line(item):
    subj = cn.subject(item.get("title", ""))
    return f'"{subj}"' if len(subj) <= 60 else f'"{subj[:60]}'


def test_twenty_uncertain_notices_with_long_subjects_are_each_shown_before_they_count():
    unc = [_live(9500 + k, f"fix: a long subject number {k} " + "that goes on and on " * 8) for k in range(20)]
    by_key = {i["key"]: i for i in unc}
    rest, sent = list(unc), []
    for _round in range(25):
        if not rest:
            break
        msgs = cn.render_digest([], rest, comm_prefs.normalise(None, "admin"), opener=0, repo_name="o/r")
        got = {k for m in msgs for k in m.keys}
        assert got and len(msgs) <= cn.MAX_CHUNKS
        sent += msgs
        rest = [i for i in rest if i["key"] not in got]
    assert not rest
    assert sorted(k for m in sent for k in m.keys) == sorted(by_key)  # each exactly once
    for m in sent:
        assert len(m.text) <= telegram_notify.MAX_TEXT
        assert "more on GitHub" not in m.text  # none counted unseen
        for k in m.keys:
            line = next(x for x in m.text.split("\n") if f"(#{by_key[k]['number']})" in x)
            assert line.startswith(("• \"", "This may not")) and _uncertain_line(by_key[k]) in line  # its own line


async def test_an_uncertain_mention_that_did_not_fit_stays_uncertain_for_the_next_digest():
    gh, bot = FakeGitHub(), FakeBot()
    await booted(gh, bot)
    st = cn.load_state()
    for k in range(20):
        key = f"pr:{9600 + k}:decision"
        st["notices"][key] = {**_live(9600 + k, f"fix: a long subject number {k} " + "that goes on and on " * 8),
                              "key": key, "kind": "raised", "category": "decision", "at": "2026-10-19T03:00:00Z"}
        st["deliveries"][f"u-owner|{key}"] = {"status": "uncertain", "lane": "instant", "attempts": 0,
                                              "next_at": None, "at": "2026-10-19T03:00:00Z"}
    cn.save_state(st)
    for when in ("19:30", "19:40", "19:50", "20:00"):
        await run(gh, bot, at(when))
    text = "\n".join(d["text"] for d in bot.delivered)
    for k in range(20):
        assert text.count(f"(#{9600 + k})") == 1  # mentioned once, over the digest and its follow-up
    ds = cn.load_state()["deliveries"]
    assert {ds[f"u-owner|pr:{9600 + k}:decision"]["status"] for k in range(20)} == {"uncertain_reported"}
