#!/usr/bin/env python3
"""Build the FunctionGemma fine-tune dataset (LAB ONLY — see README.md).

Emits data/train.jsonl: one canonical record per example:
    {"text": <utterance>, "tool": <tool name|null>, "args": {..}, "source": ..}
`tool: null` == chat negative (no tool call).  The training script renders
this canonical form into either the *plain* FunctionGemma target or the
*functional-token* (Octopus-style) target — one dataset, two variants.

Sources:
  1. Hand-authored templates with slot pools (backbone — args are known
     exactly by construction, no LLM labeling error).
  2. setfit-router train.jsonl `chat` utterances as chat negatives (text-only,
     no args needed, so label reuse is safe).
  3. Optional paraphrases from gen_paraphrases.py merged in if present
     (data/paraphrases.jsonl).

HELD-OUT GUARD: any generated text that normalizes equal to a text in the
81-case needle-benchmark corpus is dropped. We never train on the eval set.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
EVAL_CORPUS = REPO / "labs" / "needle-benchmark" / "corpus.jsonl"
PER_TOOL = 120
CHAT_TARGET = 320
SEED = 20260713

# ---------------------------------------------------------------- slot pools
ITEMS = ["milk", "eggs", "bread", "coffee", "bananas", "dog food", "paper towels",
         "olive oil", "toothpaste", "cheese", "chicken", "laundry detergent",
         "batteries", "apples", "butter", "orange juice", "rice", "pasta",
         "shampoo", "trash bags", "yogurt", "onions", "tomatoes", "cereal"]
LISTS = ["shopping", "tasks", "to-do", "personal", "work", "birthday"]
ROOMS = ["living room", "kitchen", "bedroom", "office", "hallway", "bathroom",
         "garage", "porch"]
MINUTES = [1, 2, 3, 5, 8, 10, 12, 15, 20, 25, 30, 45, 60, 90]
TIMER_LABELS = ["pasta", "laundry", "tea", "eggs", "pizza", "workout", "nap",
                "bread", "meeting prep", "the oven"]
DATES = ["today", "tomorrow", "friday", "saturday", "monday", "next tuesday",
         "this weekend", "june 3rd", "the 15th", "next week"]
TIMES = ["7am", "8:30", "noon", "3pm", "5:45pm", "seven tonight", "9 in the morning",
         "half past six", "10pm"]
EVENTS = ["dentist appointment", "team meeting", "haircut", "car service",
          "dinner with mum", "soccer practice", "doctor checkup", "lunch with sam",
          "parent teacher night", "yoga class"]
REMIND_TASKS = ["take the bins out", "call the plumber", "pay the electricity bill",
                "water the plants", "pick up the kids", "take my medication",
                "feed the dog", "renew the car rego", "email the accountant",
                "defrost the chicken"]
NOTE_TOPICS = ["the wifi password", "the garage door code", "that recipe idea",
               "the plumber's quote", "my shoe size", "the car insurance",
               "holiday plans", "the paint colour", "the meeting notes"]
PEOPLE_NAMES = ["Sarah", "Mike", "Emma", "David", "Lucy", "Tom", "Grace", "Ben",
                "Chloe", "Jack"]
RELATIONSHIPS = ["my sister", "my neighbour", "my boss", "my cousin", "a friend from work",
                 "my brother in law", "my daughter's teacher", "my old roommate"]
SONGS = ["some jazz", "the beatles", "classical music", "my chill playlist",
         "something upbeat", "fleetwood mac", "rain sounds", "80s hits",
         "acoustic guitar", "taylor swift"]
CITIES = ["Perth", "Geraldton", "Sydney", "Melbourne", "London", "Tokyo"]
MOODS = ["happy", "tired", "stressed", "grateful", "anxious", "calm", "excited"]
FACTS = ["my favourite colour is green", "I'm allergic to peanuts",
         "my anniversary is October 12th", "I park on level 3 at work",
         "my locker code is 4417", "I take my coffee black",
         "my daughter's birthday is May 9", "I prefer window seats",
         "my GP is Dr Patel", "the spare key is under the pot plant"]

Case = dict  # {"text", "tool", "args", "source"}

# generic voice-style wrappers — applied after generation to multiply unique
# combos; natural for a voice assistant ("hey zoe", trailing "please", …)
PREFIXES = ["", "", "", "hey zoe ", "zoe ", "hey ", "okay zoe ", "please ",
            "could you ", "can you ", "hey zoe can you ", "um "]
SUFFIXES = ["", "", "", " please", " for me", " thanks", " when you get a chance",
            " real quick", " zoe"]
NO_WRAP = {"remember_emotional_moment"}  # utterance IS the arg; keep verbatim
TEXT_IS_ARG = {"get_time": "query"}  # arg must track the wrapped text


def wrap(rng, text: str) -> str:
    return (rng.choice(PREFIXES) + text + rng.choice(SUFFIXES)).strip()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def _mk(text: str, tool: str | None, args: dict, src: str = "template") -> Case:
    return {"text": text, "tool": tool, "args": args, "source": src}


# ------------------------------------------------------------- per-tool gens
# Each generator yields (text, args) tuples; sampling loop dedups + trims.

def gen_get_time(rng):
    qs = ["what time is it", "what's the time", "do you have the time",
          "what's today's date", "what day is it today", "tell me the time please",
          "what's the date", "hey what time is it right now", "current time please",
          "what day of the week is it", "is it past {t} yet", "how long until midnight",
          "what's the time love", "time check", "give me today's date"]
    q = rng.choice(qs).format(t=rng.choice(TIMES))
    return q, {"query": q}


def gen_recall_memory(rng):
    topics = ["my name", "my favourite colour", "where I park at work", "my allergies",
              "my anniversary", "what I told you about my sister", "my coffee order",
              "my doctor's name", "what I said about the holiday", "my shoe size"]
    pats = ["what do you know about {x}", "do you remember {x}", "what did I tell you about {x}",
            "can you recall {x}", "remind me what {x} is", "what's {x} again",
            "do you know {x}", "tell me what you remember about {x}"]
    x = rng.choice(topics)
    return rng.choice(pats).format(x=x), {"query": x}


def gen_shopping_list_add(rng):
    item = rng.choice(ITEMS)
    pats = ["add {i} to the shopping list", "put {i} on the shopping list",
            "we need {i}", "can you add {i} to my groceries", "stick {i} on the list",
            "shopping list {i}", "we're out of {i}", "add {i} to groceries please",
            "chuck {i} on the shopping list", "don't let me forget {i} at the shops"]
    return rng.choice(pats).format(i=item), {"item": item}


def gen_get_weather(rng):
    if rng.random() < 0.4:
        city = rng.choice(CITIES)
        pats = ["what's the weather in {c}", "is it raining in {c}",
                "how hot is it in {c} today", "what's the forecast for {c} tomorrow",
                "will it rain in {c} this weekend"]
        text = rng.choice(pats).format(c=city)
        fc = "tomorrow" in text or "weekend" in text
        return text, {"forecast": fc, "location": city}
    pats = [("what's the weather like", False), ("is it going to rain today", False),
            ("do I need a jacket today", False), ("what's the forecast for tomorrow", True),
            ("will it be hot this weekend", True), ("how cold is it outside", False),
            ("what's the weather looking like this week", True),
            ("should I bring an umbrella", False)]
    text, fc = rng.choice(pats)
    return text, {"forecast": fc, "location": ""}


def gen_list_reminders(rng):
    pats = [("what are my reminders", ""), ("what do I have to do today", "today"),
            ("show me my reminders", ""), ("any reminders for tomorrow", "tomorrow"),
            ("what am I supposed to do this week", "this week"),
            ("do I have anything to remember today", "today"),
            ("read out my reminders", ""), ("what's on my to-do for tomorrow", "tomorrow")]
    t, q = rng.choice(pats)
    return t, {"qualifier": q}


def gen_show_calendar(rng):
    pats = [("what's on my calendar", ""), ("what's my schedule today", "today"),
            ("what have I got on tomorrow", "tomorrow"),
            ("am I busy on friday", "friday"), ("what's happening this weekend", "this weekend"),
            ("do I have any appointments next week", "next week"),
            ("what does my week look like", "this week"),
            ("anything on the calendar for monday", "monday")]
    t, q = rng.choice(pats)
    return t, {"qualifier": q}


def gen_show_list(rng):
    lt = rng.choice(LISTS)
    pats = ["what's on my {l} list", "show me the {l} list", "read out my {l} list",
            "what's left on the {l} list", "can I see my {l} list",
            "what do we have on the {l} list"]
    return rng.choice(pats).format(l=lt), {"list_type": lt}


def gen_set_timer(rng):
    m = rng.choice(MINUTES)
    if rng.random() < 0.5:
        lab = rng.choice(TIMER_LABELS)
        pats = ["set a {m} minute timer for {l}", "start a {m} minute {l} timer",
                "time {l} for {m} minutes", "give me {m} minutes for {l}"]
        return rng.choice(pats).format(m=m, l=lab), {"minutes": m, "label": lab}
    pats = ["set a timer for {m} minutes", "start a {m} minute timer",
            "countdown {m} minutes", "timer {m} minutes please",
            "wake me in {m} minutes", "can you set a {m} minute timer"]
    return rng.choice(pats).format(m=m), {"minutes": m, "label": ""}


def gen_add_reminder(rng):
    task = rng.choice(REMIND_TASKS)
    d = rng.choice(DATES)
    t = rng.choice(TIMES)
    style = rng.random()
    if style < 0.4:
        return f"remind me to {task} at {t}", {"title": task, "date": "", "time": t}
    if style < 0.7:
        return f"remind me to {task} {d}", {"title": task, "date": d, "time": ""}
    pats = ["set a reminder to {k} {d} at {t}", "don't let me forget to {k} {d} at {t}",
            "I need a reminder to {k} on {d} at {t}"]
    return rng.choice(pats).format(k=task, d=d, t=t), {"title": task, "date": d, "time": t}


def gen_add_calendar_event(rng):
    ev = rng.choice(EVENTS)
    d = rng.choice(DATES)
    t = rng.choice(TIMES)
    pats = ["add a {e} to my calendar for {d} at {t}", "put {e} on the calendar {d}",
            "schedule a {e} for {d} at {t}", "book in a {e} {d}",
            "new calendar event {e} {d} at {t}", "I've got a {e} {d} can you add it"]
    text = rng.choice(pats).format(e=ev, d=d, t=t)
    args = {"title": ev, "date": d, "time": t if f"at {t}" in text else "", "category": ""}
    return text, args


def gen_create_note(rng):
    contents = ["pick up dry cleaning friday", "the gate code is 2580",
                "car service due in august", "sarah's coffee order is a flat white",
                "the good plumber is on 0400 123 456", "wifi password is on the fridge",
                "we liked the second paint sample", "return the library books"]
    c = rng.choice(contents)
    pats = ["make a note: {c}", "take a note that {c}", "note down {c}",
            "jot this down {c}", "save a note saying {c}", "write this down: {c}"]
    return rng.choice(pats).format(c=c), {"content": c, "title": ""}


def gen_add_to_list(rng):
    lt = rng.choice([l for l in LISTS if l != "shopping"])
    tasks = ["finish the report", "call the bank", "book flights", "buy a gift for mum",
             "fix the fence", "update my resume", "clean the gutters", "renew passport"]
    it = rng.choice(tasks)
    pats = ["add {i} to my {l} list", "put {i} on the {l} list",
            "stick {i} on my {l} list", "can you add {i} to the {l} list"]
    return rng.choice(pats).format(i=it, l=lt), {"item": it, "list_type": lt}


def gen_list_remove(rng):
    lt = rng.choice(LISTS)
    it = rng.choice(ITEMS) if lt == "shopping" else rng.choice(
        ["call the bank", "book flights", "fix the fence", "old task"])
    pats = ["remove {i} from the {l} list", "take {i} off my {l} list",
            "delete {i} from the {l} list", "cross {i} off the {l} list",
            "{i} is done take it off the {l} list"]
    return rng.choice(pats).format(i=it, l=lt), {"item": it, "list_type": lt}


def gen_note_search(rng):
    topic = rng.choice(NOTE_TOPICS)
    pats = ["find my note about {t}", "search my notes for {t}",
            "do I have a note about {t}", "what did I write about {t}",
            "look up my note on {t}", "where's that note about {t}"]
    return rng.choice(pats).format(t=topic), {"query": topic}


def gen_journal(rng):
    r = rng.random()
    if r < 0.55:
        mood = rng.choice(MOODS)
        entries = ["today was a really good day", "work was rough today",
                   "had a lovely walk on the beach", "feeling a bit off lately",
                   "the kids made me laugh so hard tonight", "big win at work today"]
        e = rng.choice(entries)
        pats = ["journal entry: {e}", "add to my journal {e}", "dear diary {e}",
                "write in my journal that {e}", "log in my diary: {e}"]
        return rng.choice(pats).format(e=e), {"action": "create", "content": e, "mood": mood if rng.random() < 0.4 else ""}
    if r < 0.8:
        pats = ["read me my last journal entry", "what did I write in my journal yesterday",
                "show my recent diary entries", "what was my last journal entry about"]
        return rng.choice(pats), {"action": "read", "content": "", "mood": ""}
    pats = ["give me a journal prompt", "what should I write about tonight",
            "I want to journal but don't know what to write"]
    return rng.choice(pats), {"action": "prompt", "content": "", "mood": ""}


def gen_people(rng):
    name = rng.choice(PEOPLE_NAMES)
    rel = rng.choice(RELATIONSHIPS)
    r = rng.random()
    if r < 0.45:
        pats = ["remember {n}, {rel}", "save {n} to my contacts, {rel}",
                "add a person: {n} is {rel}", "meet {n} - {rel}, keep track of them"]
        return rng.choice(pats).format(n=name, rel=rel), {
            "action": "create", "name": name, "relationship": rel, "query": "", "notes": ""}
    if r < 0.8:
        pats = ["what do you know about {n}", "tell me about {n}",
                "who is {n} again", "what have I told you about {n}"]
        return rng.choice(pats).format(n=name), {
            "action": "query", "name": name, "relationship": "", "query": name, "notes": ""}
    notes = ["just got a new job", "is coming to visit next month", "had her baby",
             "moved to Brisbane", "is training for a marathon"]
    nt = rng.choice(notes)
    return f"{name} {nt}, make a note of that", {
        "action": "update", "name": name, "relationship": "", "query": "", "notes": nt}


def gen_media(rng):
    r = rng.random()
    if r < 0.45:
        s = rng.choice(SONGS)
        pats = ["play {s}", "put on {s}", "can you play {s}", "start playing {s}",
                "I want to hear {s}", "throw on {s}"]
        return rng.choice(pats).format(s=s), {
            "action": "play", "query": s, "command": "", "level": "", "direction": ""}
    cmds = [("pause the music", "pause"), ("stop the music", "stop"),
            ("skip this song", "next"), ("next track please", "next"),
            ("go back a song", "previous"), ("resume the music", "resume")]
    if r < 0.75:
        t, c = rng.choice(cmds)
        return t, {"action": "control", "query": "", "command": c, "level": "", "direction": ""}
    vols = [("turn the music up", "up"), ("turn it down a bit", "down"),
            ("volume up", "up"), ("make the music quieter", "down"),
            ("crank the volume", "up")]
    t, d = rng.choice(vols)
    return t, {"action": "volume", "query": "", "command": "", "level": "", "direction": d}


def gen_home(rng):
    room = rng.choice(ROOMS)
    acts = [("turn on the {r} lights", "on"), ("turn off the {r} light", "off"),
            ("switch off the lights in the {r}", "off"), ("lights on in the {r}", "on"),
            ("dim the {r} lights", "dim"), ("brighten the {r} a bit", "brighten"),
            ("kill the lights in the {r}", "off"), ("can you turn the {r} lamp on", "on")]
    t, a = rng.choice(acts)
    if rng.random() < 0.2:
        generic = [("turn off all the lights", "off", ""), ("lights out everywhere", "off", ""),
                   ("turn the lights on", "on", "")]
        t, a, room = rng.choice(generic)
        return t, {"action": a, "room": room}
    return t.format(r=room), {"action": a, "room": room}


def gen_remember_fact(rng):
    f = rng.choice(FACTS)
    pats = ["remember that {f}", "don't forget {f}", "keep in mind that {f}",
            "for future reference {f}", "remember this: {f}", "store this, {f}"]
    return rng.choice(pats).format(f=f), {"fact": f}


def gen_remember_emotional_moment(rng):
    moments = [("I'm still really torn up about losing dad", "negative", "high"),
               ("today marks a year since we lost the dog and it still hurts", "negative", "medium"),
               ("Emma finally called me after two years, I cried", "positive", "high"),
               ("I got the all-clear from the doctor, huge relief", "positive", "high"),
               ("the divorce was finalised today, feeling hollow", "negative", "high"),
               ("we found out we're having a baby!", "positive", "high"),
               ("my best mate is moving overseas and I'm gutted", "negative", "medium"),
               ("I finally forgave my brother, feels like a weight lifted", "positive", "medium")]
    slotted = [("{n} told me she's proud of me and it meant everything", "positive", "high"),
               ("I had a huge falling out with {n} today", "negative", "high"),
               ("{n} graduated today, I was in tears watching", "positive", "high"),
               ("{n} is in hospital again and I'm scared", "negative", "high"),
               ("watching {n} take her first steps today was magic", "positive", "high"),
               ("{n} and I finally talked properly after all these years", "positive", "medium"),
               ("I keep thinking about the argument with {n}, it's eating at me", "negative", "medium"),
               ("{n} surprised me for my birthday, I feel so loved", "positive", "medium"),
               ("saying goodbye to {n} at the airport broke my heart", "negative", "medium"),
               ("{n} got engaged today and the whole family is over the moon", "positive", "high")]
    if rng.random() < 0.7:
        pat, v, i = rng.choice(slotted)
        m = pat.format(n=rng.choice(PEOPLE_NAMES + ["mum", "dad", "grandma", "my son", "my niece"]))
    else:
        m, v, i = rng.choice(moments)
    return m, {"moment": m, "valence": v, "intensity": i}


GENERATORS = {
    "get_time": gen_get_time, "recall_memory": gen_recall_memory,
    "shopping_list_add": gen_shopping_list_add, "get_weather": gen_get_weather,
    "list_reminders": gen_list_reminders, "show_calendar": gen_show_calendar,
    "show_list": gen_show_list, "set_timer": gen_set_timer,
    "add_reminder": gen_add_reminder, "add_calendar_event": gen_add_calendar_event,
    "create_note": gen_create_note, "add_to_list": gen_add_to_list,
    "list_remove": gen_list_remove, "note_search": gen_note_search,
    "journal": gen_journal, "people": gen_people, "media": gen_media,
    "home": gen_home, "remember_fact": gen_remember_fact,
    "remember_emotional_moment": gen_remember_emotional_moment,
}

CHAT_BANK = [
    "how are you today", "tell me a joke", "what's your favourite colour",
    "I'm so tired today", "that movie last night was great",
    "what do you think about pineapple on pizza", "good morning",
    "goodnight zoe", "thanks for that", "you're pretty clever aren't you",
    "what's the capital of france", "how do magnets work",
    "I had a weird dream last night", "do you ever get bored",
    "what should I cook for dinner", "my back is killing me",
    "the traffic was terrible this morning", "tell me something interesting",
    "who won the cricket", "can dogs eat grapes", "what's 17 times 24",
    "I can't decide what to watch tonight", "you there?", "hello",
    "how long do you cook a soft boiled egg", "why is the sky blue",
    "recommend me a book", "I'm bored", "what's a good name for a goldfish",
    "did you miss me", "talk to me about space", "how was your day",
    "what languages do you speak", "are you listening", "never mind",
    "that's hilarious", "I love this song", "it's been a long week",
    "what's the meaning of life", "sing me a song", "tell me a fun fact",
    "how far away is the moon", "what rhymes with orange",
    "my sister is visiting next week, should be fun", "I hate mondays",
    "the garden is looking great this year", "do you dream",
    "what's a good wine with steak", "I think I'm getting a cold",
    "explain quantum computing simply", "who invented the telephone",
]


def load_setfit_chat(paths: list[Path]) -> list[str]:
    out = []
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            rec = json.loads(line)
            if rec.get("label") == "chat":
                out.append(rec["text"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setfit-train", type=Path, default=None,
                    help="setfit-router train.jsonl (chat negatives seed)")
    ap.add_argument("--per-tool", type=int, default=PER_TOOL)
    ap.add_argument("--out", type=Path, default=HERE / "data" / "train.jsonl")
    args = ap.parse_args()

    rng = random.Random(SEED)
    held_out = {_norm(json.loads(l)["text"]) for l in EVAL_CORPUS.read_text().splitlines()}

    cases: list[Case] = []
    seen: set[str] = set()

    def push(c: Case) -> bool:
        n = _norm(c["text"])
        if n in seen or n in held_out:
            return False
        seen.add(n)
        cases.append(c)
        return True

    for tool, gen in GENERATORS.items():
        got, tries = 0, 0
        while got < args.per_tool and tries < args.per_tool * 60:
            tries += 1
            text, targs = gen(rng)
            if tool not in NO_WRAP and tries > args.per_tool // 2:
                text = wrap(rng, text)
                if tool in TEXT_IS_ARG:
                    targs = {**targs, TEXT_IS_ARG[tool]: text}
            if push(_mk(text, tool, targs)):
                got += 1
        if got < args.per_tool:
            print(f"WARN {tool}: only {got}/{args.per_tool} unique examples")

    # chat negatives
    chat_texts = list(CHAT_BANK)
    if args.setfit_train:
        chat_texts += load_setfit_chat([args.setfit_train])
    rng.shuffle(chat_texts)
    got = 0
    for t in chat_texts:
        if got >= CHAT_TARGET:
            break
        if push(_mk(t, None, {}, "chat-seed")):
            got += 1
    # pad chat with simple mutations if short
    fillers = ["hey ", "so ", "um ", "zoe ", "okay "]
    i = 0
    while got < CHAT_TARGET and i < 2000:
        base = rng.choice(chat_texts) if chat_texts else rng.choice(CHAT_BANK)
        if push(_mk(rng.choice(fillers) + base, None, {}, "chat-mutated")):
            got += 1
        i += 1
    if got < CHAT_TARGET:
        print(f"WARN chat: only {got}/{CHAT_TARGET}")

    # merge paraphrases if a prior gen_paraphrases.py run left them
    para = HERE / "data" / "paraphrases.jsonl"
    if para.exists():
        kept = 0
        for line in para.read_text().splitlines():
            rec = json.loads(line)
            if rec.get("tool") in GENERATORS or rec.get("tool") is None:
                if push({**rec, "source": "brain-paraphrase"}):
                    kept += 1
        print(f"merged {kept} brain paraphrases")

    rng.shuffle(cases)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        for c in cases:
            f.write(json.dumps(c) + "\n")
    from collections import Counter
    dist = Counter(c["tool"] or "CHAT" for c in cases)
    print(f"wrote {len(cases)} examples -> {args.out}")
    for k, v in sorted(dist.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
