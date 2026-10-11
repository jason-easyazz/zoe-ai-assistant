"""The lane-parity cells: one utterance script per lane, one verdict function, run through ``lane_parity_rig.Rig``.

A cell is PASS on a lane when the lane reached the capability the household expects (the stored fact changed, the contact exists, the
claim was checked, the feedback row exists), not when the two reply strings are byte-equal: a spoken write is read back before it is
made (the Pi hears a name; a typed name is exact), so the voice script of a write may carry one extra turn ("Yes."). PARITY is both
lanes PASS. Demo identities only; every store is the rig's throwaway one.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from lane_parity_rig import BRAIN_SENTINEL, Outcome, Rig

WORLD_Q = "Who won the 1987 Australian Open men's final?"
SISTER_Q = "What is my sister called?"
SISTER_A = "Your sister is called Marisa."
WORLD_A = "Stefan Edberg won the 1987 Australian Open men's final in five sets, beating Pat Cash."


async def approved_texts(rig: Rig) -> list[str]:
    """The user's approved memory rows (the throwaway palace), newest state."""
    from memory_service import get_memory_service

    rows = await get_memory_service().list_by_status(user_id=rig.user, status="approved", limit=500)
    return [r.text for r in rows]


@dataclass
class Cell:
    cid: str
    title: str
    chat: list[str]
    voice: list[str]
    check: Callable[[Rig, list[Outcome], str], Awaitable[tuple[bool, str]]]
    brain_reply: Optional[dict] = None          # {utterance: stubbed model answer}
    serve: tuple = ()                           # utterances whose brain turn "recalled" the user's notes (what /for-prompt does live)
    voice_only: bool = False                    # a safety row that only has a meaning on the spoken lane
    note: str = ""


def _no_brain(o: Outcome) -> bool:
    return not o.brain_reached


async def check_correction(rig, outs, lane):
    texts = await approved_texts(rig)
    new = [t for t in texts if "marisol" in t.lower()]
    old = [t for t in texts if "marisa" in t.lower() and "marisol" not in t.lower()]
    last = outs[-1]
    ok = bool(new) and not old and _no_brain(last) and "marisol" in last.reply.lower()
    return ok, f"stage={last.stage} new_rows={len(new)} old_rows={len(old)} reply={last.reply[:70]!r}"


async def check_contact_add(rig, outs, lane):
    last = outs[-1]
    rows = [(p["name"], p["relationship"]) for p in rig.db.people]
    ok = rows == [("Percival", "brother")] and _no_brain(last)
    return ok, f"people={rows} reply={last.reply[:70]!r}"


async def check_contact_list(rig, outs, lane):
    last = outs[-1]
    return (_no_brain(last) and "percival" in last.reply.lower()), f"stage={last.stage} reply={last.reply[:70]!r}"


async def check_contact_lookup(rig, outs, lane):
    last = outs[-1]
    return (_no_brain(last) and "brother" in last.reply.lower()), f"stage={last.stage} reply={last.reply[:70]!r}"


async def check_verify(rig, outs, lane):
    last = outs[-1]
    checked = bool(rig.searches) and ("live web check" in last.brain_message or last.stage == "seam_reply")
    return checked, f"searches={len(rig.searches)} stage={last.stage}"


def _rated_the_answer(rig) -> bool:
    """The row is about the WORLD_A reply (the last answer before the verdict), not the verdict turn or another message."""
    answers = [m for m in rig.db.messages if m["role"] == "assistant" and m["content"].startswith("Stefan Edberg")]
    return bool(answers) and all(f["params"][1] == answers[-1]["id"] and f["params"][2] == rig.user for f in rig.db.feedback)


async def check_feedback_down(rig, outs, lane):
    kinds = [f["params"][3] for f in rig.db.feedback]
    return (kinds == ["thumbs_down"] and _rated_the_answer(rig)), f"feedback={kinds} about_the_answer={_rated_the_answer(rig)} stage={outs[-1].stage}"


async def check_feedback_up(rig, outs, lane):
    kinds = [f["params"][3] for f in rig.db.feedback]
    last = outs[-1]
    ok = kinds == ["thumbs_up"] and _no_brain(last) and _rated_the_answer(rig)
    return ok, f"feedback={kinds} stage={last.stage} reply={last.reply[:50]!r}"


async def check_forget(rig, outs, lane):
    texts = await approved_texts(rig)
    left = [t for t in texts if "marisa" in t.lower()]
    last = outs[-1]
    return (not left and _no_brain(last)), f"rows_left={len(left)} stage={last.stage} reply={last.reply[:70]!r}"


async def check_provenance(rig, outs, lane):
    last = outs[-1]
    ok = last.stage.startswith("tier:provenance") and "i don't have a record" not in last.reply.lower() and "marisa" in last.reply.lower()
    return ok, f"stage={last.stage} reply={last.reply[:90]!r}"


async def check_exact_words(rig, outs, lane):
    # the exact-words index is read by the recall packet (an HTTP read in production); what a lane owns is NOTING the turn the owner
    # asked for their own words, so the packet quotes them. Both lanes reach the same seam call with the same text.
    last = outs[-1]
    noted = [t for t in rig.exact_turns if "exactly" in t.lower()]
    return (bool(noted) and last.brain_reached), f"stage={last.stage} noted={len(noted)}"


async def check_no_write_on_question(rig, outs, lane):
    # a spoken "are you sure?" / "no, that's not correct" while a write is waiting must not make the write
    return (rig.db.people == []), f"people={[p['name'] for p in rig.db.people]} last={outs[-1].stage}"


CELLS: list[Cell] = [
    Cell("correction", "'that's wrong, my sister is Marisol not Marisa' changes the stored note and reads it back",
         ["Remember that my sister is called Marisa.", "That's wrong, my sister is Marisol not Marisa."],
         ["Remember that my sister is called Marisa.", "That's wrong, my sister is Marisol not Marisa."], check_correction),
    Cell("contact_add", "'add my brother Percival' ends in a people row (voice reads it back first)",
         ["Add my brother Percival."], ["Add my brother Percival.", "Yes."], check_contact_add),
    Cell("contact_list", "'list my contacts' is answered from the contacts, not by the model",
         ["Add my brother Percival.", "List my contacts."], ["Add my brother Percival.", "Yes.", "List my contacts."], check_contact_list),
    Cell("contact_lookup", "'who is Percival' is answered from the contacts, not by the model",
         ["Add my brother Percival.", "Who is Percival?"], ["Add my brother Percival.", "Yes.", "Who is Percival?"], check_contact_lookup),
    Cell("verify_on_challenge", "'are you sure?' after a world-fact answer checks it on the web",
         [WORLD_Q, "Are you sure?"], [WORLD_Q, "Are you sure?"], check_verify, brain_reply={WORLD_Q: WORLD_A}),
    Cell("feedback_down", "'that was wrong' leaves a thumbs-down on the reply it is about",
         [WORLD_Q, "That was wrong."], [WORLD_Q, "That was wrong."], check_feedback_down, brain_reply={WORLD_Q: WORLD_A}),
    Cell("feedback_up", "'good answer' leaves a thumbs-up and costs no model turn",
         [WORLD_Q, "Good answer."], [WORLD_Q, "Good answer."], check_feedback_up, brain_reply={WORLD_Q: WORLD_A}),
    Cell("provenance", "'why did you say that' names the note and the owner's own words",
         ["Remember that my sister is called Marisa.", SISTER_Q, "Why did you say that?"],
         ["Remember that my sister is called Marisa.", SISTER_Q, "Why did you say that?"], check_provenance,
         brain_reply={SISTER_Q: SISTER_A}, serve=(SISTER_Q,)),
    Cell("forget_it", "'forget it' right after 'why did you say that' forgets that note",
         ["Remember that my sister is called Marisa.", SISTER_Q, "Why did you say that?", "Forget it."],
         ["Remember that my sister is called Marisa.", SISTER_Q, "Why did you say that?", "Forget it."], check_forget,
         brain_reply={SISTER_Q: SISTER_A}, serve=(SISTER_Q,)),
    Cell("exact_words", "'what exactly did I say' carries the owner's own words to the answer",
         ["Remember that my sister is called Marisa.", "What exactly did I say about my sister?"],
         ["Remember that my sister is called Marisa.", "What exactly did I say about my sister?"], check_exact_words),
    Cell("pending_not_a_yes", "a waiting spoken write is not confirmed by 'are you sure?'",
         ["Add my brother Percival."], ["Add my brother Percival.", "Are you sure?"], check_no_write_on_question, voice_only=True),
    Cell("pending_not_correct", "a waiting spoken write is not confirmed by 'no, that's not correct'",
         ["Add my brother Percival."], ["Add my brother Percival.", "No, that's not correct, it's Percy."], check_no_write_on_question,
         voice_only=True),
]


@dataclass
class CellResult:
    cid: str
    title: str
    chat_ok: Optional[bool]
    voice_ok: bool
    chat_detail: str
    voice_detail: str
    voice_only: bool = False

    @property
    def parity(self) -> bool:
        return self.voice_ok and (self.chat_ok is None or self.chat_ok)


async def run_lane(cell: Cell, lane: str, monkeypatch, *, user_suffix: str = "", flags: Optional[dict] = None) -> tuple[bool, str]:
    # a fresh identity per run: the throwaway palace outlives one run inside a process, and a note left by an earlier run is state
    rig = Rig(user=f"demo_lane_parity_{lane}_{cell.cid}_{uuid.uuid4().hex[:6]}{user_suffix}").install(monkeypatch, flags=flags)
    script = cell.chat if lane == "chat" else cell.voice
    outs: list[Outcome] = []
    for utt in script:
        rig.brain_reply = (cell.brain_reply or {}).get(utt, BRAIN_SENTINEL)
        rig.serve_on_brain = utt in cell.serve
        outs.append(await (rig.chat(utt) if lane == "chat" else rig.voice(utt)))
    return await cell.check(rig, outs, lane)


async def run_cells(monkeypatch_factory, only: Optional[set] = None, flags: Optional[dict] = None) -> list[CellResult]:
    """Every cell, both lanes, each in a fresh rig (fresh fake store, fresh demo identity). ``flags`` overrides the live flag mirror
    (the instrument's negative controls turn a tier OFF and the cell it guards must go red)."""
    results = []
    for cell in CELLS:
        if only and cell.cid not in only:
            continue
        mp = monkeypatch_factory()
        try:
            c_ok, c_detail = (None, "n/a (spoken-lane safety row)") if cell.voice_only else await run_lane(cell, "chat", mp, flags=flags)
        finally:
            mp.undo()
        mp = monkeypatch_factory()
        try:
            v_ok, v_detail = await run_lane(cell, "voice", mp, flags=flags)
        finally:
            mp.undo()
        results.append(CellResult(cell.cid, cell.title, c_ok, v_ok, c_detail, v_detail, cell.voice_only))
    return results


def render(results: list[CellResult]) -> str:
    def mark(v):
        return "n/a " if v is None else ("PASS" if v else "FAIL")

    lines = [f"{'cell':22} {'chat':5} {'voice':5} parity", "-" * 44]
    for r in results:
        lines.append(f"{r.cid:22} {mark(r.chat_ok):5} {mark(r.voice_ok):5} {'YES' if r.parity else 'NO'}")
    n = sum(1 for r in results if r.parity)
    lines.append(f"parity {n}/{len(results)}")
    return "\n".join(lines)
