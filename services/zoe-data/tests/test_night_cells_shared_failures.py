"""The night mind's cells that BOTH Gemma models failed (2026-10-09: K9 change-and-quiet, K9f flat week, K10 restraint, K12 weight calibration).

Two models of different size failing the same cell points at the code, so each failure below is reproduced with a SCRIPTED model that behaves the way the measured replies did
(synthetic names, no network, no live store) and the fix is pinned by it. Every test names the evidence it reproduces and goes red when its fix is reverted:

* the reply stopped at its 8-moment cap (or was cut off mid-object) and the NEWEST lines were never read: the plan change at the end of the month was missing, so a
  thread read "quiet" (K9f: a false notice on a steady story) and a "changed" thread never appeared (K9);
* the model writes the OWNER into ``who`` as "self", and ``@self`` glued a sore knee and a loan into one thread (K10: 4 leave threads where the life has 5);
* a moment's ``later: done`` on "Jorunn and I ran 5k today." resolved a running habit the THREADS call had called ``open`` (K9: the thread left the quiet check).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re

import pytest

import night_mind as nm
import night_store as ns

pytestmark = pytest.mark.ci_safe

UID = "demo_bar_00000002"
NOW = dt.datetime(2026, 10, 9, 3, 0, tzinfo=dt.timezone.utc)
_LINE = re.compile(r"^\[(m\d+)\] [^:]*: (.*)$")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def lab():
    backend = ns.MemoryBackend()
    ns.set_backend(backend)
    nm._reset_state()
    nm.FAULTS.clear()
    prev = nm.set_config(nm.Config(ctx_tokens=8192, chunk_tokens=900, max_calls=7, url="http://127.0.0.1:1"))
    yield backend
    nm.set_llm(None)
    nm.set_config(prev)
    nm.FAULTS.clear()
    ns.set_backend(None)


def transcript(turns):
    import memory_digest

    pairs = [(f"t{i:03d}", t) for i, (t, _d) in enumerate(turns)]
    return memory_digest.Transcript("\n".join(t for _i, t in pairs), pairs, [(NOW - dt.timedelta(days=d)).isoformat() for _t, d in turns])


def moment(alias, text, **kw):
    return {"ids": [alias], "quote": text, "kind": kw.get("kind", "progress"), "who": kw.get("who", []), "feeling": "none", "weight": 2, "later": kw.get("later", "open")}


def lines_of(prompt):
    return [m.groups() for m in (_LINE.match(ln) for ln in prompt.splitlines()) if m]


#: the drift month in miniature (invented names): a running habit that STOPS, a steady garden, a trip that is called off on day 20 - the line the 4B never reached
MONTH = [("I went for a run with Jorunn this morning.", 28), ("My neighbour Gustav gave me seedlings for the garden.", 27),
         ("We are going to Hollowick on the 12th for Dune's birthday.", 25), ("Running with Jorunn again, we are building up to the fun run.", 21),
         ("Gustav helped me plant the garden beds.", 20), ("Jorunn and I ran 5k today.", 14), ("The garden beds are doing well, thanks to Gustav.", 13),
         ("Gustav brought round more seedlings for the garden.", 10), ("The Hollowick trip is off, we are going to Tarnholt instead.", 9),
         ("Picked the first tomatoes from the garden with Gustav.", 1)]


class Scripted:
    """A model that picks the first ``take`` lines it is shown (line order, like the measured replies) and stops: at its cap, or cut off mid-object."""

    def __init__(self, take=8, cut=False, threads="[]", later="open"):
        self.take, self.cut, self.threads, self.later = take, cut, threads, later
        self.moment_prompts = []

    def __call__(self, messages, max_tokens):
        user = messages[-1]["content"]
        if user.startswith("TASK: THREADS"):
            return '{"threads":' + self.threads + ',"unchanged":[]}'
        rows = lines_of(user)
        self.moment_prompts.append([t for _a, t in rows])
        ms = [moment(a, t, kind="change" if " off," in t else "progress", later=self.later, who=[w for w in ("Jorunn", "Gustav", "Dune") if w in t][:1]) for a, t in rows[:self.take]]
        raw = json.dumps({"moments": ms}, separators=(",", ":"))
        return raw[:-25] if self.cut and len(self.moment_prompts) == 1 else raw      # only the FIRST reply is cut


def night(model, turns=MONTH, **cfg):
    if cfg:
        nm.set_config(nm.Config(ctx_tokens=8192, chunk_tokens=900, url="http://127.0.0.1:1", **cfg))
    nm.set_llm(model)
    return run(nm.run_for_user(UID, transcript(turns), None, now=NOW, force_mode="enforce"))


def threads(backend):
    return {t["title"]: t for t in run(backend.threads(UID))}


def thread_about(backend, anchor):
    return next(t for t in run(backend.threads(UID)) if anchor in str(t["anchors"]).split())


# ── K9 / K9f: the reply that stopped short loses the newest part of the day ──────────────────────────────────────────

@pytest.mark.parametrize("cut", [False, True], ids=["stopped_at_the_cap", "cut_off_mid_object"])
def test_the_lines_a_reply_never_got_to_are_asked_for_once_more_so_a_plan_change_is_seen(lab, cut):
    """Evidence (4B and 12B, K9): 7 of 10 lines came back, the call was cut at 640 tokens; the day-20 'trip is off' line was in the unread tail."""
    model = Scripted(take=7 if cut else 8, cut=cut)
    res = night(model)
    assert res["moments_calls"] == 2 and res["tail_calls"] == 1 and res["tail_lost"] == 0
    assert len(model.moment_prompts) == 2 and model.moment_prompts[1] == [t for t, _d in MONTH][-len(model.moment_prompts[1]):]     # the second call holds ONLY the unread tail
    assert any("off," in q for q in model.moment_prompts[1])
    assert thread_about(lab, "@hollowick")["status"] == "changed"                                                              # K9: the plan that changed is 'changed'


def test_without_a_spare_call_the_tail_is_lost_and_counted_the_control_for_the_tail_ask(lab):
    """The control: with no call to spare the pass does NOT exceed its cap - the tail stays unread, and says so (`tail_lost`)."""
    res = night(Scripted(take=8), max_calls=2)
    assert res["tail_calls"] == 0 and res["tail_lost"] == 2
    assert thread_about(lab, "@hollowick")["status"] != "changed"


def test_the_tail_ask_never_takes_a_call_a_later_chunk_or_the_threads_call_needs(lab):
    """Six chunks in a seven-call night leave no spare: the continuation would starve the last chunk, so it is not made."""
    nm.set_config(nm.Config(ctx_tokens=8192, chunk_tokens=120, max_calls=7, url="http://127.0.0.1:1"))
    nm.set_llm(Scripted(take=1))
    res = run(nm.run_for_user(UID, transcript(MONTH * 3), None, now=NOW, force_mode="enforce"))
    assert res["calls"] <= 7 and res["tail_calls"] <= max(0, 7 - 1 - res["chunks"])


def test_a_steady_thread_is_not_called_quiet_when_its_recent_lines_are_read_K9f(lab):
    """Evidence (K9f, 12B and 4B): the running story's days 23-29 were beyond the cap, so its last day read as day 16 -> 'quiet' on a flat week. Read in full it is open."""
    runs = [("I went for a run with Jorunn this morning.", 28), ("Running with Jorunn again, we are building up to the fun run.", 21), ("Jorunn and I ran 5k today.", 14),
            ("Jorunn and I ran along the river this morning.", 7), ("Another run with Jorunn before work.", 1)]
    garden = [(f"Gustav brought plant number {i} to the garden.", d) for i, d in enumerate((27, 25, 19, 12, 10), 1)]
    flat = sorted(runs + garden, key=lambda t: -t[1])                          # lines 9 and 10 (the runs of day 7 and day 1) are the ones a cap of 8 never reaches
    night(Scripted(take=8), flat)
    assert thread_about(lab, "@jorunn")["status"] == "open"


# ── K10: the owner is nobody's name ─────────────────────────────────────────────────────────────────────────────────

def test_the_owner_written_as_self_is_not_a_name_that_glues_two_stories_K10(lab):
    """Evidence (K10): who=['self'] on a sore knee and on the loan -> the anchor '@self' joined them into one 'Financial worries' thread (4 leave threads, the life has 5)."""
    turns = [("My knee has been sore since rowing on Sunday.", 6), ("I'm worried about the loan repayments, I can't afford them.", 5), ("I saw Dr Okafor about my knee and the physio starts soon.", 1)]

    def model(messages, _max):
        user = messages[-1]["content"]
        if user.startswith("TASK: THREADS"):
            qs = re.findall(r"^(q\d+) ", user, re.M)
            return json.dumps({"threads": [{"op": "create", "title": "Financial worries", "moments": qs, "status": "open", "reason": "both worry me"}], "unchanged": []})
        return json.dumps({"moments": [moment(a, t, who=["self"]) for a, t in lines_of(user)]})
    nm.set_llm(model)
    run(nm.run_for_user(UID, transcript(turns), None, now=NOW, force_mode="enforce"))
    got = run(lab.threads(UID))
    assert len(got) == 2 and not any("@self" in str(t["anchors"]) for t in got)


@pytest.mark.parametrize("who", ["self", "Self", "me", "user", "owner", "myself", "you"])
def test_first_person_words_are_never_names(who):
    mo = nm.Moment(turn=nm.Turn("t1", "x", NOW), quote="My knee is sore today", who=[who])
    assert nm._names_of(mo) == set()


# ── K9: a finished event is not a finished story ─────────────────────────────────────────────────────────────────────

def test_a_done_moment_alone_does_not_resolve_a_thread_the_threads_call_calls_open_K9(lab):
    """Evidence (K9, 4B): every running / garden line carried later='done' ('I ran 5k today'), the THREADS call said status 'open', and code resolved both threads - which then
    never reached the quiet check. A finished event with no dated plan before it and no 'resolved' from the THREADS call leaves the thread open."""
    group = json.dumps([{"op": "create", "title": "Running with Jorunn", "moments": ["q1", "q2", "q3"], "status": "open", "reason": "same person"}])
    night(Scripted(take=8, later="done", threads=group), MONTH[:1] + MONTH[3:4] + MONTH[5:6])
    assert thread_about(lab, "@jorunn")["status"] in ("open", "quiet")


def test_a_done_moment_after_a_dated_plan_resolves_it_even_when_the_threads_call_says_open_K11(lab):
    """Evidence (K11, 4B, replayed): 'The Isolde quiz night is on the 20th' (plan, open) then 'went really well' (done) - the THREADS call answered status 'open'; the story did finish."""
    quiz = [("The quiz night is on the 20th at the Isolde club.", 20), ("The Isolde quiz night went really well, we came second.", 8), ("Gustav helped me plant the garden beds.", 5)]

    class Quiz(Scripted):
        def __call__(self, messages, max_tokens):
            out = super().__call__(messages, max_tokens)
            if messages[-1]["content"].startswith("TASK: MOMENTS"):
                ms = json.loads(out)["moments"]
                for m in ms:
                    m["kind"] = "plan" if "is on the 20th" in m["quote"] else "other"
                    m["later"] = "done" if "went really well" in m["quote"] else "open"
                out = json.dumps({"moments": ms})
            return out
    night(Quiz(take=8, threads=json.dumps([{"op": "create", "title": "Isolde quiz night", "moments": ["q1", "q2"], "status": "open", "reason": "same event"}])), quiz)
    assert [t["status"] for t in run(lab.threads(UID)) if "quiz" in str(t["anchors"])] == ["resolved"]


def test_a_done_moment_resolves_when_the_threads_call_agrees(lab):
    group = json.dumps([{"op": "create", "title": "Running with Jorunn", "moments": ["q1", "q2", "q3"], "status": "resolved", "reason": "the fun run happened"}])
    night(Scripted(take=8, later="done", threads=group), MONTH[:1] + MONTH[3:4] + MONTH[5:6])
    assert thread_about(lab, "@jorunn")["status"] == "resolved"


def test_the_merge_keeps_what_either_reading_asserted():
    assert nm.merge_status("open", "resolved") == "resolved" and nm.merge_status("changed", "open") == "changed" and nm.merge_status("open", "open") == "open"
    assert nm.merge_status("resolved", "changed") == "resolved"


# ── the reply format ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_prompt_asks_for_one_line_json_and_shows_two_moments():
    assert "ONE line" in nm.MOMENTS_USER and nm.MOMENTS_USER.count('"ids"') == 2


def test_the_output_limit_follows_the_cap_and_never_drops_below_the_eight_moment_limit():
    assert nm.moment_max_tokens() == nm.MOMENT_MAX_TOKENS == nm.moment_max_tokens(8)
    assert nm.moment_max_tokens(12) > nm.MOMENT_MAX_TOKENS and nm.moment_max_tokens(12) >= 12 * nm.MOMENT_TOKENS_EACH
    assert nm.moment_max_tokens(1) == nm.MOMENT_MAX_TOKENS


def test_parse_moments_honours_a_larger_cap_and_defaults_to_eight():
    turns = [nm.Turn(f"t{i}", f"Jorunn ran {i} laps around the park today", NOW) for i in range(1, 13)]
    raw = json.dumps({"moments": [moment(f"m{i}", t.text) for i, t in enumerate(turns, 1)]})
    counts = {k: 0 for k in nm.COUNT_KEYS}
    assert len(nm.parse_moments(raw, turns, 0, counts)) == 8 and len(nm.parse_moments(raw, turns, 0, counts, 12)) == 12
