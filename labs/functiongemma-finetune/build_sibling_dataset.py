#!/usr/bin/env python3
"""Sibling-discrimination dataset v2 (LAB ONLY — router-90 campaign).

Targets the MEASURED confusions from the fine-tuned functok evals
(labs/two-stage-router-eval/results/functok.json and
labs/router-90-campaign/results/functok-g{a,b}.json):

  * list family: show_list vs shopping_list_add vs list_remove
    (browse/read phrasings decoded as add; removal phrasings decoded as add)
  * show_calendar vs list_reminders / people / get_weather
  * add_calendar_event vs add_reminder (timed event vs nudge)
  * add_reminder vs remember_fact (todo vs durable fact)
  * note_search vs recall_memory / create_note
  * media paraphrases (indirect play/volume/skip) decoded as chat/None
  * timer paraphrases, indirect weather/time asks
  + 100 hard chat negatives that SOUND tool-adjacent but are not commands.

Same canonical record shape as build_dataset.py ({text, tool, args, source});
args correct by construction; same held-out guard (never emits a text that
normalizes equal to an 81-case eval corpus text).  Output:
data/train_sibling.jsonl.  Train on original + this file concatenated.

Usage:  python3 labs/functiongemma-finetune/build_sibling_dataset.py
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
EVAL_CORPUS = REPO / "labs" / "needle-benchmark" / "corpus.jsonl"
OUT = HERE / "data" / "train_sibling.jsonl"
SEED = 20260714

PREFIXES = ["", "", "", "hey zoe ", "zoe ", "okay zoe ", "um ", "hey "]
SUFFIXES = ["", "", "", " please", " thanks", " for me", " when you get a sec"]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


ITEMS = ["milk", "eggs", "bread", "coffee", "bananas", "dog food", "butter",
         "cheese", "toothpaste", "washing powder", "apples", "mince",
         "toilet paper", "sunscreen", "capsicum", "yoghurt", "weetbix",
         "tomato sauce", "snags", "avocados"]
LISTS = ["shopping", "grocery", "todo", "hardware", "camping", "packing"]
EVENTS = [("dentist", "wednesday", "9am"), ("physio", "next monday", "2pm"),
          ("school assembly", "friday", "10am"), ("car service", "tuesday", "8:30"),
          ("haircut", "saturday", "11am"), ("footy training", "thursday", "5pm"),
          ("parent teacher night", "next wednesday", "6pm"),
          ("vet checkup for the cat", "monday", "3pm")]
TODOS = ["put the bins out", "pay the water bill", "call the electrician",
         "defrost the chook", "pick up the dry cleaning", "feed the fish",
         "book the car in", "hang the washing out", "ring nan",
         "grab the mail", "water the veggie patch", "charge the drill"]
FACTS = ["my shoe size is 11", "the spare key lives under the frog",
         "I take my coffee black", "our anniversary is in october",
         "the bins go out on wednesdays", "I'm lactose intolerant",
         "my boss's name is Priya", "the car takes 98 octane",
         "I barrack for the dockers", "my licence expires in march"]
NOTE_TOPICS = [("the mechanic", "quoted 450 for the brakes"),
               ("the landlord", "said rent goes up in july"),
               ("the electrician", "can come thursday week"),
               ("the school", "pupil free day is the 28th"),
               ("the pool guy", "reckons the pump needs replacing")]
SONGS_VIBES = ["something upbeat", "some old rock", "a bit of jazz",
               "the cooking playlist", "something mellow", "some 80s stuff",
               "anything acoustic", "the kids' playlist"]
TIMER_JOBS = [("the rice", 12), ("the spuds", 25), ("the washing", 40),
              ("my nap", 20), ("the sausages", 15), ("the bread", 35),
              ("steeping the tea", 4), ("the sprinkler", 30)]

T = lambda text, tool, args: {"text": text, "tool": tool, "args": args,
                              "source": "sibling_v2"}


def gen_tool_cases(rng: random.Random) -> list[dict]:
    out = []

    # ---- list family: the #1 measured confusion --------------------------
    # show_list phrasings that decoded as shopping_list_add
    show_pool = ["what's still left on the list",
                 "run me through what we need from the shops",
                 "what have we got on the list so far",
                 "anything still to get at the shops",
                 "how long is the list looking",
                 "what's the damage on the shopping list",
                 "give us a rundown of the list",
                 "what needs picking up today",
                 "read back what's on there",
                 "what's on the list before I head out"]
    out += [T(t, "show_list", {"list_type": "shopping"}) for t in show_pool]
    out += [T(f"read out the {ln} list", "show_list", {"list_type": ln})
            for ln in LISTS]
    out += [T(f"what's on the {ln} list at the moment", "show_list",
              {"list_type": ln}) for ln in LISTS]
    for it in ITEMS:
        out.append(T(f"scratch {it} off, we've got plenty", "list_remove",
                     {"item": it, "list_type": "shopping"}))
        out.append(T(rng.choice([
            f"take {it} off the list, found some in the pantry",
            f"cross {it} off, already grabbed it",
            f"you can drop {it} off the list",
            f"we don't need {it} anymore, pull it off",
        ]), "list_remove", {"item": it, "list_type": "shopping"}))
        out.append(T(rng.choice([
            f"we're nearly out of {it}",
            f"running low on {it} again",
            f"chuck {it} on the list for the shops",
            f"better get {it} next shop",
            f"we've gone through all the {it}",
        ]), "shopping_list_add", {"item": it}))

    # ---- calendar show vs reminders vs add --------------------------------
    cal_show = ["what's my day looking like tomorrow",
                "have I got anything on this arvo",
                "am I free thursday evening or is something on",
                "what's happening this weekend calendar wise",
                "any appointments before lunch",
                "how packed is next week looking",
                "is there anything on between school pickup and dinner",
                "what am I meant to be at on sunday",
                "does friday have anything booked in",
                "give us a look at next week's schedule"]
    out += [T(t, "show_calendar", {"qualifier": ""}) for t in cal_show]
    rem_list = ["what nudges have you got queued up for me",
                "list whatever you're meant to remind me about",
                "what am I supposed to not forget today",
                "any reminders hanging over me this week",
                "what have I asked you to remind me of"]
    out += [T(t, "list_reminders", {}) for t in rem_list]
    for what, day, tm in EVENTS:
        out.append(T(rng.choice([
            f"the {what} got booked for {day} at {tm}, lock it in",
            f"put the {what} in for {day} {tm}",
            f"{what} is happening {day} at {tm}, stick it in the calendar",
            f"they can fit the {what} in {day} at {tm}, book it",
        ]), "add_calendar_event", {"title": what, "date": day, "time": tm}))
    for todo in TODOS:
        out.append(T(rng.choice([
            f"don't let me forget to {todo}",
            f"give us a nudge to {todo} later",
            f"I'll never remember to {todo} unless you say something",
            f"poke me about {todo} tonight",
        ]), "add_reminder", {"title": todo, "date": "", "time": ""}))

    # ---- remember_fact vs add_reminder vs recall --------------------------
    for fact in FACTS:
        out.append(T(rng.choice([
            f"for future reference {fact}",
            f"just so you know, {fact}",
            f"keep in mind that {fact}",
            f"worth remembering: {fact}",
        ]), "remember_fact", {"fact": fact}))
    recall = ["what did I tell you about my sister's new place",
              "remind me what I said about the car trouble",
              "what do you know about my work schedule",
              "didn't I mention something about my knee last week",
              "what was that thing I told you about the neighbours",
              "refresh me on what I said about christmas plans"]
    out += [T(t, "recall_memory", {"query": t}) for t in recall]

    # ---- note_search vs recall_memory vs create_note ----------------------
    for who, what in NOTE_TOPICS:
        out.append(T(rng.choice([
            f"{who} {what}, jot that down somewhere",
            f"quick note: {who} {what}",
            f"keep this somewhere, {who} {what}",
        ]), "create_note", {"content": f"{who} {what}"}))
        out.append(T(rng.choice([
            f"what did {who} say again, I wrote it down somewhere",
            f"didn't we note what {who} said",
            f"dig up that note about {who}",
            f"check my notes for what {who} said",
        ]), "note_search", {"query": who}))

    # ---- media paraphrases (decoded as chat/None today) --------------------
    media = [("chuck on " + v, {"action": "play", "query": v}) for v in SONGS_VIBES]
    media += [
        ("that's blasting, wind it back a bit", {"action": "volume_down"}),
        ("too quiet, crank it up", {"action": "volume_up"}),
        ("not this song, next one", {"action": "next"}),
        ("give the music a rest for a sec", {"action": "pause"}),
        ("righto put the tunes back on", {"action": "play"}),
        ("bit loud for the baby, drop it down", {"action": "volume_down"}),
        ("skip ahead, heard this one to death", {"action": "next"}),
        ("throw some music on out the back", {"action": "play"}),
    ]
    out += [T(t, "media", a) for t, a in media]

    # ---- timers, weather, time indirect ------------------------------------
    for job, mins in TIMER_JOBS:
        out.append(T(rng.choice([
            f"give me {mins} minutes on the clock for {job}",
            f"{job} needs {mins} minutes, time it",
            f"count down {mins} for {job}",
            f"start {mins} minutes for {job}",
        ]), "set_timer", {"minutes": mins, "label": job}))
    weather = ["will the washing dry outside today",
               "do the kids need raincoats for school",
               "is it gonna be a scorcher tomorrow",
               "any chance of rain over the weekend",
               "reckon it's shorts weather today",
               "how windy is it getting this arvo"]
    out += [T(t, "get_weather", {"forecast": True, "location": ""}) for t in weather]
    times = ["what day is it again", "is it still morning",
             "how late is it getting", "what's today's date again",
             "have you got the time there"]
    out += [T(t, "get_time", {}) for t in times]

    # ---- people vs show_calendar / recall ----------------------------------
    people = [("who's my mate Steve again", "Steve"),
              ("tell me about my cousin Ella", "Ella"),
              ("my physio's name is Tan, keep track of him", "Tan"),
              ("what do we have on my sister in law", "sister in law")]
    out += [T(t, "people", {"query": q}) for t, q in people]
    return out


# --------------------------------------------------------------------------
# ROUND 2 (2026-07-14 pm): heavy targeted coverage for the 13 residual
# functok-gb failures (results/functok-gb.json + def-gb-mlp.json).  Families
# mirror the measured confusions; texts are paraphrase-styled but NEVER copy
# eval-corpus utterances (the held-out guard below enforces it).
# Output: data/train_round2.jsonl (train = expanded + round2 concatenated).
# --------------------------------------------------------------------------

TIMEFRAMES = ["tomorrow", "this arvo", "tonight", "thursday", "next tuesday",
              "on the weekend", "before lunch tomorrow", "after dinner",
              "friday morning", "later today", "in the morning",
              "tomorrow arvo"]
ACTIVITIES = ["the beach", "a bbq", "a picnic", "the footy", "camping",
              "a swim", "mowing the lawn", "a bike ride", "fishing",
              "a walk along the foreshore", "hanging the washing out",
              "a run"]
DAYS = ["saturday", "sunday", "tomorrow", "friday", "monday", "next weekend",
        "thursday arvo", "in the morning"]
NAMES = ["Zeke", "Marisol", "Tobias", "Anouk", "Freddie", "Priyanka",
         "Callum", "Wilhelmina", "Dusty", "Ophelia", "Barnaby", "Sunny"]
TRADES = [("plumber", "the hot water system"), ("sparky", "the switchboard"),
          ("roofer", "the gutters"), ("tiler", "the bathroom"),
          ("painter", "the outside of the house"), ("fencer", "the back fence"),
          ("arborist", "taking the gum tree down"), ("glazier", "the window")]
ARRIVALS = [("dad's train", "gets in", "friday", "4pm"),
            ("the removalists", "rock up", "tuesday", "7am"),
            ("aunty june's bus", "arrives", "saturday", "noon"),
            ("the delivery", "is due", "monday", "10am"),
            ("nan's ferry", "docks", "sunday", "5:30"),
            ("the inspection", "is set for", "wednesday", "1pm"),
            ("the boys' game", "kicks off", "saturday", "9am"),
            ("grandpa's flight", "gets in", "thursday", "8pm")]
ROOMS = ["downstairs", "in the lounge", "out the back", "upstairs",
         "in the kitchen", "in the kids' rooms", "on the patio",
         "in the study"]
VIBES2 = ["chill", "loud", "summery", "moody", "funky", "relaxing",
          "upbeat", "christmassy"]
DOING = ["I cook", "we eat", "I fold the washing", "we clean up",
         "I do the ironing", "we set the table", "I wrap presents",
         "we do the dishes"]
FORGETTABLES = [("the parking meter", "in an hour"), ("the roast", "at 6"),
                ("the school run", "at quarter to 3"),
                ("bin night", "tonight"), ("the chemist closing", "at 5"),
                ("footy pickup", "at 4:30"), ("the library books", "friday"),
                ("mum's call", "at 7")]
SCHEDULE_FACTS = ["I finish early on wednesdays", "the gym shuts at 9",
                  "the good bakery is closed mondays",
                  "our mortgage comes out on the 15th",
                  "I start at 7 on tuesdays", "school pickup moved to gate 3",
                  "the pool opens at 6 in summer",
                  "recycling week is the odd weeks"]


def gen_round2_cases(rng: random.Random) -> list[dict]:
    out = []

    # ---- A. show_calendar: "what's on / what have I got / am I free" -------
    for tf in TIMEFRAMES:
        out.append(T(rng.choice([
            f"what have I got on {tf}",
            f"have I got anything on {tf}",
            f"anything on {tf} or am I free",
            f"what's on for me {tf}",
        ]), "show_calendar", {"qualifier": tf}))
        out.append(T(rng.choice([
            f"am I double booked {tf} or are we right",
            f"is {tf} clear or have I got something",
            f"are we free {tf} or is something booked",
            f"is anything booked in for {tf}",
        ]), "show_calendar", {"qualifier": tf}))
    out += [T(t, "show_calendar", {"qualifier": ""}) for t in [
        "what's the diary looking like this week",
        "what am I up for after work",
        "have we got much on over the next few days",
        "whereabouts am I meant to be this afternoon",
        "what's the go with my schedule today",
        "do I have a full day or a quiet one tomorrow",
        "what else is on my plate today appointment wise",
        "is my morning free or booked solid",
    ]]

    # ---- A'. list_reminders contrast (nudges, not appointments) ------------
    out += [T(t, "list_reminders", {}) for t in [
        "what reminders are still outstanding",
        "run through the reminders you've got for me",
        "what have I told you to nudge me about",
        "which reminders are due today",
        "have you got any nudges waiting for me",
        "what's left on my reminders",
        "read out my reminders for this week",
        "anything you're supposed to remind me of tonight",
        "what pings have you got lined up for me",
        "show us the reminder list",
    ]]

    # ---- B. get_weather: activity-suitability paraphrases ------------------
    for act in ACTIVITIES:
        d = rng.choice(DAYS)
        out.append(T(rng.choice([
            f"reckon it'll be decent enough for {act} {d}",
            f"is the weather gonna hold for {act} {d}",
            f"will it be right for {act} {d} you reckon",
            f"any good for {act} {d} or will it bucket down",
        ]), "get_weather", {"forecast": True, "location": ""}))
    out += [T(t, "get_weather", {"forecast": True, "location": ""}) for t in [
        "is saturday looking sunny or a washout",
        "reckon sunday's a beach day",
        "will it stay dry for the school fete",
        "what are we in for weather wise this weekend",
        "is it meant to cool down by friday",
        "how's the mercury looking tomorrow",
        "gonna need the brolly today or not",
        "is tonight going to be freezing",
    ]]

    # ---- C. add_calendar_event: "stick that in / lock it in" statements ----
    for what, verb, day, tm in ARRIVALS:
        out.append(T(rng.choice([
            f"{what} {verb} {day} at {tm}, stick that in",
            f"{what} {verb} {day} {tm}, chuck it in the calendar",
            f"{what} {verb} {day} at {tm}, get that in the diary",
            f"{what} {verb} {day} at {tm}, lock that in for me",
        ]), "add_calendar_event", {"title": what, "date": day, "time": tm}))
        out.append(T(rng.choice([
            f"pop {what} in for {day} {tm}",
            f"whack {what} in the calendar, {day} at {tm}",
            f"can you diary {what} for {day} at {tm}",
        ]), "add_calendar_event", {"title": what, "date": day, "time": tm}))

    # ---- C'. add_reminder: "I'll forget X unless you say something" --------
    for thing, when in FORGETTABLES:
        out.append(T(rng.choice([
            f"I'll forget {thing} {when} unless you pipe up",
            f"I'm bound to forget {thing} unless you remind me {when}",
            f"no way I'll remember {thing}, give us a shout {when}",
            f"sing out about {thing} {when} or it won't happen",
        ]), "add_reminder", {"title": thing, "date": "", "time": when}))
        out.append(T(rng.choice([
            f"remind me about {thing} {when}",
            f"nudge me on {thing} {when} yeah",
        ]), "add_reminder", {"title": thing, "date": "", "time": when}))

    # ---- C''. remember_fact: "for future reference" durable facts ----------
    for fact in SCHEDULE_FACTS:
        out.append(T(rng.choice([
            f"for future reference {fact}",
            f"file this away: {fact}",
            f"so you know going forward, {fact}",
            f"one for the memory bank: {fact}",
        ]), "remember_fact", {"fact": fact}))

    # ---- D. note_search: "what did I say X quoted / what did I write" ------
    for trade, job in TRADES:
        out.append(T(rng.choice([
            f"what did I say the {trade} quoted for {job}",
            f"what was the {trade}'s quote on {job} again",
            f"didn't I jot down what the {trade} wanted for {job}",
            f"look up what the {trade} said about {job}",
        ]), "note_search", {"query": trade}))
    out += [T(t, "note_search", {"query": t}) for t in [
        "find the note with the wifi password for the beach house",
        "what did I write down about the insurance renewal",
        "search my notes for the paint colour we picked",
        "pull up whatever I noted from the parent teacher meeting",
        "what was in that note about the car rego",
    ]]

    # ---- D'. recall_memory contrast (personal history, not notes) ----------
    out += [T(t, "recall_memory", {"query": t}) for t in [
        "what have I told you about my brother's new job",
        "remind me what I said about the holiday plans",
        "what do you remember about my knee playing up",
        "have I mentioned anything about the neighbours' renovation",
        "what did I tell you my favourite takeaway was",
    ]]

    # ---- E. list_remove: "actually scratch X, we've got heaps" -------------
    for it in ITEMS[:12]:
        out.append(T(rng.choice([
            f"actually scratch the {it}, we've got heaps",
            f"on second thought take the {it} off, there's plenty here",
            f"actually we're right for {it}, pull it off the list",
            f"turns out we've got stacks of {it}, cross it off",
        ]), "list_remove", {"item": it, "list_type": "shopping"}))
        out.append(T(rng.choice([
            f"no more {it} in the cupboard, add it",
            f"we're clean out of {it}",
            f"gone through the last of the {it}, stick it on the list",
            f"{it} is all gone again",
        ]), "shopping_list_add", {"item": it}))

    # ---- F. media: "chuck on something X while I Y" -------------------------
    for vibe in VIBES2:
        doing = rng.choice(DOING)
        out.append(T(rng.choice([
            f"chuck on something {vibe} while {doing}",
            f"put something {vibe} on while {doing}",
            f"can we get some {vibe} tunes going while {doing}",
            f"throw on a {vibe} playlist while {doing}",
        ]), "media", {"action": "play", "query": vibe}))
    out += [T(t, "media", {"action": "play", "query": ""}) for t in [
        "get some background music happening",
        "stick some tunes on for us",
        "bit of music wouldn't hurt right now",
        "let's have some music while we wait",
    ]]

    # ---- F'. home: "kill everything downstairs / heading to bed" -----------
    for room in ROOMS:
        out.append(T(rng.choice([
            f"we're heading up, kill everything {room}",
            f"shut everything down {room}, we're done for the night",
            f"turn the lot off {room}",
            f"everything off {room} thanks, calling it a night",
        ]), "home", {"action": "off", "target": room}))
    out += [T(t, "home", {"action": "off", "target": ""}) for t in [
        "we're off to bed, shut the house down",
        "lights out everywhere, movie's starting",
        "kill the lot, we're leaving for the airport",
        "power everything down for the night",
    ]]

    # ---- G. people: "who is <name>" positives -------------------------------
    for name in NAMES:
        out.append(T(rng.choice([
            f"who is {name}",
            f"who's {name} again",
            f"do you know who {name} is",
            f"who even is {name}",
            f"remind me who {name} is",
        ]), "people", {"query": name}))
    return out


CHAT_NEGATIVES = [
    # tool-adjacent but NOT commands — the hard boundary
    "my shopping list of problems just keeps growing",
    "we're out of luck with the weather lately eh",
    "I can never remember where I put my keys, classic me",
    "time really flies when the kids are on holidays",
    "my calendar used to be empty before the kids came along",
    "remind me why we thought renovating was a good idea",
    "I should really keep better notes at work",
    "the music at that cafe was way too loud",
    "my brother never takes anything off his plate",
    "I keep forgetting people's names at parties, so embarrassing",
    "the timer on the oven is broken, need a new one eventually",
    "our grocery bill has been huge this month",
    "I love a good thunderstorm at night",
    "the days are getting shorter already",
    "you'd think I'd remember my own anniversary",
    "everyone's schedule is so packed these days",
    "my diary from high school is hilarious reading",
    "what's your favourite kind of music anyway",
    "do you ever lose track of time",
    "the neighbours' lights were on all night, weird",
    "I'm terrible at keeping plants alive",
    "cooking rice properly is harder than it looks",
    "my nan had the best memory in the family",
    "wish the weekend went for three days",
    "the kids grow up so fast honestly",
    "that reminds me of a funny story from work",
    "I've got a mental list of places I want to travel",
    "sometimes I talk to myself while cooking, is that weird",
    "the volume of emails I get is ridiculous",
    "my mate reckons he never forgets a face",
    "what would you cook with just eggs and bread",
    "is it weird to have cereal for dinner",
    "how do people keep their houses so tidy",
    "I miss when weekends felt long",
    "you're pretty handy to have around you know",
    "what's the deal with daylight saving anyway",
    "my old man never wrote anything down and never forgot a thing",
    "planning birthday parties stresses me out",
    "I always burn the first pancake, always",
    "how do you say no to a second coffee",
    "the dog looks so guilty right now, wish you could see it",
    "work drinks went way too late last night",
    "I reckon I walked ten kays around the shops today",
    "the price of butter these days honestly",
    "not sure the veggie patch is going to survive summer",
    "my sister's wedding photos finally came back, so good",
    "footy season starting is the best time of year",
    "I can't decide between the blue tiles and the white ones",
    "the baby slept through the night, miracle",
    "you ever think about how big the ocean is",
]

CHAT_OPENERS = ["", "", "honestly ", "oh ", "you know what, ", "haha ",
                "by the way ", "random thought but "]


def main() -> None:
    rng = random.Random(SEED)
    held = {_norm(json.loads(l)["text"])
            for l in EVAL_CORPUS.read_text().splitlines() if l.strip()}
    cases = gen_tool_cases(rng)
    # wrap a slice with voice-style prefixes/suffixes for phrasing variety
    wrapped = []
    for c in cases:
        t = c["text"]
        if rng.random() < 0.4:
            t = (rng.choice(PREFIXES) + t + rng.choice(SUFFIXES)).strip()
        wrapped.append({**c, "text": t})
    chats = [{"text": (rng.choice(CHAT_OPENERS) + t).strip(), "tool": None,
              "args": {}, "source": "sibling_v2_chat"}
             for t in CHAT_NEGATIVES for _ in (0, 1)]
    seen, final = set(), []
    for c in wrapped + chats:
        n = _norm(c["text"])
        if not n or n in seen or n in held:
            continue
        seen.add(n)
        final.append(c)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(c) + "\n" for c in final))
    by_tool: dict[str, int] = {}
    for c in final:
        by_tool[c["tool"] or "chat"] = by_tool.get(c["tool"] or "chat", 0) + 1
    print(f"wrote {len(final)} examples -> {OUT}")
    print(json.dumps(by_tool, indent=1))

    # round 2: same guard, own file, own prefix-wrap pass
    r2 = []
    for c in gen_round2_cases(rng):
        t = c["text"]
        if rng.random() < 0.4:
            t = (rng.choice(PREFIXES) + t + rng.choice(SUFFIXES)).strip()
        r2.append({**c, "text": t, "source": "sibling_r2"})
    seen2, final2 = set(seen), []
    for c in r2:
        n = _norm(c["text"])
        if not n or n in seen2 or n in held:
            continue
        seen2.add(n)
        final2.append(c)
    out2 = HERE / "data" / "train_round2.jsonl"
    out2.write_text("".join(json.dumps(c) + "\n" for c in final2))
    by2: dict[str, int] = {}
    for c in final2:
        by2[c["tool"] or "chat"] = by2.get(c["tool"] or "chat", 0) + 1
    print(f"wrote {len(final2)} examples -> {out2}")
    print(json.dumps(by2, indent=1))


if __name__ == "__main__":
    main()
