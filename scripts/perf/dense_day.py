"""A dense synthetic day: >= 200 owner turns of routine chatter with real statements PLANTED in it, the late ones in the last 15 %.

Why (docs/research/night-mind-2026-10-09.md, experiment E2 / cell K7): the nightly digest cut the day's transcript to its first 3,000 characters,
so on a busy day everything the owner said after the first ~45 turns was never read. This is the fixture that proves it (legacy mode misses the late plants)
and that the chunked pack step fixes it (``ZOE_DIGEST_CHUNKED``), offline: no model, no database, no live service.

* ``dense_day(seed, n_turns)`` - the day: ``turns`` (list of strings) and ``plants`` (what was planted, where, and what a model that READS it should return).
* ``oracle_reply(system, prompt, day)`` - a stand-in model that reads EXACTLY the transcript it is shown in the prompt and reports every plant it can see
  (facts with their verbatim quote, emotional moments, open loops). It cannot see what the prompt does not contain, so what it returns is a function of the
  digest's PACKING alone: the thing under test. It says nothing about how well the real 4B reads a chunk (that is the night-mind cells' job).
* The two day-sim seeds the Samantha bar asks about (scenario 4 "when did I tell you about the dentist", scenario 5 "what did I say about the project")
  are planted with the day-sim's own sentences (``samantha_day_sim.SAY``) so the fixture breaks if those seeds are reworded.

All names are invented. Deterministic in ``seed``.
"""
from __future__ import annotations

import importlib.util
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

PERF = Path(__file__).resolve().parent

_ROOMS = ("kitchen", "lounge", "hallway", "study", "back deck")
_GENRES = ("jazz", "lo-fi beats", "acoustic folk", "classical piano", "70s soul")
_ITEMS = ("oat milk", "lemons", "basil", "tinned tomatoes", "dish tabs", "pasta", "olive oil", "bin bags")
_CITIES = ("Lisbon", "Osaka", "Denver", "Cape Town", "Reykjavik")
_FILLER = (
    "set a timer for {n} minutes for the pasta",
    "turn the {room} lights off, I'm heading out",
    "turn the {room} lights back on, it's getting dark",
    "play some {genre} in the {room}",
    "what's the weather doing this afternoon, is it going to rain",
    "skip this song, it's not really doing it for me",
    "add {item} to the shopping list for the weekend",
    "turn the volume up a little in the {room}",
    "remind me to put the bins out at {n} o'clock",
    "how long do I boil an egg for if I want it runny",
    "what time is it in {city} right now",
    "pause the music for a second, someone's at the door",
    "dim the {room} lights to {n} percent",
    "how many tablespoons are in a quarter cup of flour",
    "resume the music in the {room} where it left off",
    "convert {n} degrees celsius to fahrenheit",
)
_LEAD = ("Hey Zoe, ", "Zoe, ", "Okay, ", "Right, ", "Can you ", "Could you ", "Quick one, ", "")
_TAIL = (" please.", " thanks.", " when you get a chance.", ".", ".", " if you can.")


@dataclass
class Plant:
    key: str                  # a stable name
    text: str                 # what the owner said (the turn is exactly this)
    index: int                # which turn of the day
    kinds: tuple              # ("fact",) / ("emotion",) / ("loop",) combinations a reading model reports
    fact: str = ""            # what a reading model returns as the fact
    moment: str = ""          # ... as the emotional moment
    loop: str = ""            # ... as the open loop
    hint: str = ""
    late: bool = False        # in the last 15 % of the day's turns
    needle: str = ""          # a lower-case token that identifies this plant in stored text


@dataclass
class DenseDay:
    seed: str
    turns: list = field(default_factory=list)
    plants: list = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.turns)

    def late_plants(self):
        return [p for p in self.plants if p.late]


def day_sim_say() -> dict:
    """The Samantha day-sim's seed sentences (``samantha_day_sim.SAY``), loaded without running anything."""
    sys.path.insert(0, str(PERF))
    spec = importlib.util.spec_from_file_location("samantha_day_sim", PERF / "samantha_day_sim.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["samantha_day_sim"] = mod
    spec.loader.exec_module(mod)
    return dict(mod.SAY)


def dense_day(seed: str = "dense-v1", n_turns: int = 240, say: "dict | None" = None) -> DenseDay:
    rng = random.Random(seed)
    say = say or day_sim_say()
    day = DenseDay(seed)
    for _ in range(n_turns):
        body = rng.choice(_FILLER).format(n=rng.randint(2, 45), room=rng.choice(_ROOMS), genre=rng.choice(_GENRES),
                                          item=rng.choice(_ITEMS), city=rng.choice(_CITIES))
        lead = rng.choice(_LEAD)
        day.turns.append((lead + body if lead else body.capitalize()) + rng.choice(_TAIL))
    late_from = int(n_turns * 0.85)
    plans = [
        # (position as a fraction of the day, key, text, kinds, fact, moment, loop, hint, needle)
        (0.03, "diet", say["d1-diet"], ("fact",), "User is pescatarian and eats no meat", "", "", "", "pescatarian"),
        (0.30, "assessment", "I'm still waiting on the results of Mika's school assessment, they said by the 20th.", ("loop",),
         "", "", "User is waiting on the results of Mika's school assessment by the 20th", "ask about the school assessment", "assessment"),
        (0.45, "race", say["d1-race"], ("fact",), "User is training for the Rottnest half-marathon in February", "", "", "", "rottnest"),
        (0.70, "shift", say["d1-shift"], ("fact",), "User works night shifts in the hospital pharmacy", "", "", "", "pharmacy"),
        (0.88, "project", say["d1-project"], ("fact",),
         "User is leading the Kestrel billing migration, which goes live on 14 November", "", "", "", "kestrel"),
        (0.91, "marathon", "I finally finished the Wattle Creek marathon today and I'm so proud I could cry.", ("fact", "emotion"),
         "User finished the Wattle Creek marathon", "User finished the Wattle Creek marathon and felt very proud", "", "", "wattle"),
        (0.94, "dentist", say["d3-dentist"], ("fact", "emotion", "loop"),
         "User has a dentist appointment on Friday for a cracked molar", "User is nervous about a dentist appointment on Friday for a cracked molar",
         "User is nervous about the dentist on Friday for a cracked molar", "ask how the dentist visit went", "molar"),
        (0.97, "permit", "I'm still waiting to hear back from the Halloran council about the fence permit, they said by next Tuesday.", ("loop",),
         "", "", "User is waiting to hear back from the Halloran council about the fence permit by next Tuesday", "ask if the Halloran council replied", "halloran"),
    ]
    used = set()
    for pos, key, text, kinds, fact, moment, loop, hint, needle in plans:
        i = min(n_turns - 1, int(pos * n_turns))
        while i in used:
            i += 1
        used.add(i)
        day.turns[i] = text
        day.plants.append(Plant(key, text, i, kinds, fact, moment, loop, hint, i >= late_from, needle))
    return day


def _seen(prompt: str, plant: Plant) -> bool:
    return plant.text in prompt


def oracle_reply(system: str, prompt: str, day: DenseDay) -> str:
    """The stand-in model's answer: every plant whose sentence is in ``prompt``, reported the way the pass asks for it. Returns the JSON text."""
    s = system.lower()
    seen = [p for p in day.plants if _seen(prompt, p)]
    if "fact extractor" in s:
        return json.dumps([{"type": "profile", "fact": p.fact, "quote": p.text} for p in seen if "fact" in p.kinds and p.fact])
    if "empathetic" in s:
        return json.dumps([{"moment": p.moment, "emotion": "anxiety" if p.key == "dentist" else "pride", "significance": 3}
                           for p in seen if "emotion" in p.kinds])
    if "open loops" in s:
        return json.dumps([{"loop_text": p.loop, "follow_up_hint": p.hint, "emotional_weight": 3, "follow_up_in_days": 1}
                           for p in seen if "loop" in p.kinds])
    return "[]"
