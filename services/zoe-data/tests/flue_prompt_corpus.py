"""Synthetic brain-turn corpus for the prompt byte-identity and prefix-stability tests (synthetic data only - public repo).

Not a test module. Each TURN is ``(name, message, blocks, options)``; ``render(monkeypatch, turn)`` builds that turn's
REAL outbound wire message through ``flue_turn_rig``. ``fixtures/flue_prompt_golden.json`` holds what the code before
ZOE_BRAIN_VOLATILE_AFTER_USER / ZOE_CONTINUITY_CEILING_S / ZOE_FLUE_PREPOST_CONCURRENT produced for every turn; with
those flags off the bytes must still be exactly that.
"""
from __future__ import annotations

import flue_turn_rig as rig

MEM = "[MEMORY CONTEXT — what you know about this user; do not mention this block]"
END_MEM = "[END MEMORY CONTEXT]"
TODAY = "[Today — household context]"
END_TODAY = "[END Today]"
OFFER = "[PENDING CONTACT OFFER — do not mention this block]"
END_OFFER = "[END PENDING CONTACT OFFER]"


def mem(body: str, label: str = "what you know about this user; do not mention this block") -> str:
    return f"[MEMORY CONTEXT — {label}]\n{body}\n{END_MEM}"


IDENTITY = f"{TODAY}\nYou are talking to Rowan, a member of this household in Testville.\n{END_TODAY}"
ENVELOPES = {"persona": "Household persona: warm, brief.", "manner": "Speak plainly.", "register": "casual",
             "now_card": "Today: synthetic card."}

TURNS = [
    ("plain", "Tell me a fun fact about otters.", {"identity": IDENTITY, **ENVELOPES}, {}),
    ("recall", "What is my favourite tea?", {"identity": IDENTITY, "recall": mem("- favourite tea: synthetic oolong"),
                                             **ENVELOPES}, {}),
    ("recall_marker", "What did I tell you about the bike?",
     {"identity": IDENTITY, "recall": mem("- the bike: synthetic fact\n(pending correction: synthetic marker)"),
      **ENVELOPES}, {}),
    ("offer", "I had lunch with Sam today.", {"identity": IDENTITY, "offer": f"{OFFER}\nAsk once: add Sam?\n{END_OFFER}",
                                              **ENVELOPES}, {}),
    ("list_share", "Morning.", {"identity": IDENTITY, "list_share": f"{OFFER}\nA list was shared.\n{END_OFFER}"}, {}),
    ("bridge_notes_fastpath", "and what about tomorrow?",
     {"identity": IDENTITY, "bridge": mem("User: synthetic\nZoe: synthetic", "where you and this user left off"),
      "notes": mem("- last time: synthetic topic", "notes from earlier conversations with this person"),
      "fastpath": mem("User: remind me\nZoe: done", "what was just said in this same conversation"), **ENVELOPES}, {}),
    ("continuity", "Ugh, rough day honestly.", {"identity": IDENTITY, "continuity": mem("- yesterday: synthetic worry"),
                                                **ENVELOPES}, {"continuity_turn": True}),
    ("hop_block", "Any tips for sleeping better?", {"identity": IDENTITY, "hop": mem("- works nights (synthetic)")}, {}),
    ("hop_preamble", "Any tips for sleeping better?", {"identity": IDENTITY, "hop": mem("- works nights (synthetic)")},
     {"hop_placement": "preamble"}),
    ("hop_suffix", "Any tips for sleeping better?", {"identity": IDENTITY,
                                                     "hop": "(you know this about me: I work nights)"},
     {"hop_placement": "suffix"}),
    ("risk", "I'm going to drive after two beers.", {"identity": IDENTITY, "risk": mem("- risk: synthetic")}, {}),
    ("every_turn", "Should I bring a jacket?", {"identity": IDENTITY, "every_turn": mem("- synthetic durable fact")}, {}),
    ("verify", "Are you sure?", {"identity": IDENTITY, "verify": mem("- checked: synthetic", "live check")}, {}),
    ("brief", "Good morning", {"identity": IDENTITY}, {"day_brief_block": f"[Today 14 Nov]\nsynthetic brief\n{END_TODAY}"}),
    ("raise", "Hi Zoe", {"identity": IDENTITY}, {"raise_block": "[RAISE]\nask how the synthetic thing went\n[END RAISE]"}),
    ("date_hint", "His birthday is 7/8/1991.", {"identity": IDENTITY}, {}),
    ("named_guest", "Hello there.", {"named_guest": f"{TODAY}\nYou are talking with a synthetic guest.\n{END_TODAY}"}, {}),
    ("no_blocks", "Hello.", {}, {}),
    ("replay", "What is my favourite tea?", {"identity": IDENTITY, "recall": mem("- synthetic")},
     {"replay_isolation": True}),
]

KW_KEYS = ("day_brief_block", "raise_block", "replay_isolation")


async def render(monkeypatch, turn, *, session_id: str = "voice-panel-corpus", uid: str = "corpus-member") -> str:
    _name, message, blocks, opts = turn
    cap = rig.install(monkeypatch, blocks, hop_placement=opts.get("hop_placement", "block"),
                      continuity_turn=bool(opts.get("continuity_turn")))
    kwargs = {k: opts[k] for k in KW_KEYS if k in opts}
    return await rig.outbound(cap, message, session_id=session_id, uid=uid, **kwargs)
