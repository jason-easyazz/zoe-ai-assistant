"""The capability-axis corpora: synthetic, seeded, no household text.

Four axes the first bench did not measure (docs/knowledge/zoe-memory-bench.md, "Capability axes"):

* (j) **exact words** - ``exact_corpus``: 20 sentences the owner SAID (the needle is the sentence itself), each with the question that asks
  for it back ("what exactly did I say about the dentist", "read me back what I told you about X's kids", "did I tell X Tuesday or
  Thursday") and the day it was said. Scored by exact substring.
* (k) **reflection** - ``life``: thirty days of a household (threads that develop over weeks, two facts that change, a decoy for every
  link a model could invent) plus the GOLD every derived observation is judged against: true / false / neutral, no model, no judge.
* (l) **long-range associative recall** - ``hop_corpus``: 20 questions that need TWO facts said weeks apart (a date compared with a date,
  a place joined with a person).
* (m) **memory protocol** - ``protocol_corpus``: prompts that need memory, prompts that must not touch it, and questions the store is
  silent about; with the three protocols' trigger policies (``POLICIES``) as deterministic stand-ins for what each protocol tells a brain.

Everything is a function of ``(seed, ...)``: the same seed gives the same corpus on every arm; a held-out seed gives a new one of the same
shapes. All names are invented. Every answer token exists nowhere else in a corpus, so a hit is a string match and never a judgement.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from . import needles as needlemod

# ── pools (disjoint from world.py's and needles.py's name pools, so a leak test can tell them apart) ─────────────────────────────────
_PEOPLE = ("Aurelio", "Bettina", "Corvin", "Dagny", "Emeric", "Fiora", "Gustav", "Halima", "Ilario", "Jorunn", "Kasimir", "Linnea",
           "Matteo", "Nerys", "Osric", "Perrin", "Rhona", "Stellan", "Tamsin", "Ulla", "Viktor", "Wilhelmina", "Xanthe", "Yorick",
           "Zora", "Abelard", "Brynja", "Caius", "Dorotea", "Elspeth", "Faramir", "Gwenllian", "Hadrian", "Isolde", "Jarvis", "Keziah")
_KIDS = ("Alder", "Briar", "Cosmo", "Dune", "Ember", "Flint", "Gale", "Hazel", "Indigo", "Jovie", "Kellan", "Lark", "Moss", "Nell",
         "Onyx", "Pip", "Quince", "Rowan", "Sorrel", "Tansy", "Umber", "Vesper", "Wick", "Yarrick")
_DOCTORS = ("Okafor", "Lindgren", "Marchetti", "Haverford", "Quennell", "Ashdown", "Brightwater", "Castellan", "Dunmore", "Ellsworth",
            "Fairweather", "Grimshaw", "Holloway", "Ironside")
_TOPICS = ("dentist", "optician", "plumber", "vet", "physio", "hairdresser", "mechanic", "accountant", "tutor", "electrician",
           "chiropodist", "gardener", "locksmith", "tailor")
_SCHOOLS = ("Fernhill", "Oakbourne", "Marwick", "Stonecross", "Larkfield", "Redmere", "Thornby", "Willowcombe", "Ashby Vale", "Kingsmoor")
_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
_RELS = ("sister", "brother", "cousin", "aunt", "uncle", "mother", "father")
_ORGS = ("Quillmoor", "Harrowdale Labs", "Brackenridge", "Saltmarsh Press", "Cobbleford", "Tidewater Co", "Evenwood", "Pinecrest Mills",
         "Larchmont", "Driftwood Audio")
_PLACES2 = ("Bergvik", "Oldmere", "Tarnholt", "Quinford", "Saltreach", "Wenlow", "Marlowby", "Ashgrove", "Pellham", "Cragmoor",
            "Dunwich", "Eldermoss", "Fallowby", "Gannet Bay", "Hollowick", "Ironbridge")
_SPORTS = ("netball", "five-a-side", "squash", "hockey", "rowing")
_INSTR = ("cello", "clarinet", "ukulele", "trumpet", "viola")

#: every entity string any generator here can emit, for the leak test and the foreign-entity check
POOL_STRINGS = tuple(sorted(set(_PEOPLE) | set(_KIDS) | set(_DOCTORS) | set(_SCHOOLS) | set(_ORGS) | set(_PLACES2)))

#: words a protocol's hedging / attribution scorers read
HEDGES = ("probably", "seems", "seem", "might", "maybe", "perhaps", "i think", "i guess", "appears", "possibly", "likely", "suppose")
HISTORY_MARKS = ("used to", "before", "previously", "formerly", "moved from", "was in", "no longer", "until", "had been", "earlier",
                 "left ", "leaving", "quit", "fed up", "switched from", "changed from", "ex-")
STATED_MARKS = ("you told me", "you said", "you mentioned", "as you said", "you've told me")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


# ═══ (j) exact words ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ExactNeedle:
    style: str              # said | readback | choice
    sentence: str           # what the owner said: the span a correct answer carries, verbatim
    question: str           # how the owner asks for it back
    subject: str            # the person / topic the question names
    day_offset: int         # days ago the sentence was said (1..27, unique per needle)
    decoy: str = ""         # a same-shape sentence about someone else that must not be mistaken for it ("" = none)


def exact_corpus(seed: str, n: int = 20) -> "list[ExactNeedle]":
    """``n`` (<= 20) exact-words needles: 7 ``said`` + 7 ``readback`` + 6 ``choice``, cycled when ``n`` is smaller."""
    if not 1 <= n <= 20:
        raise ValueError("n must be 1..20")
    rng = random.Random(f"zmb-j-exact:{seed}")
    people = rng.sample(_PEOPLE, len(_PEOPLE))
    kids = rng.sample(_KIDS, len(_KIDS))
    topics = rng.sample(_TOPICS, len(_TOPICS))
    docs = rng.sample(_DOCTORS, len(_DOCTORS))
    schools = rng.sample(_SCHOOLS, len(_SCHOOLS))
    days_ago = rng.sample(range(1, 28), 20)
    order = ["said"] * 7 + ["readback"] * 7 + ["choice"] * 6
    rng.shuffle(order)
    out: "list[ExactNeedle]" = []
    p = t = d = s = k = 0
    for i in range(n):
        style = order[i]
        a, b = rng.sample(_DAYS, 2)
        if style == "said":
            topic, doc = topics[t], docs[d]
            t, d = t + 1, d + 1
            out.append(ExactNeedle(style, f"I told Dr {doc} I can only manage {a} afternoons for the {topic} and not {b}.",
                                   f"what exactly did I say about the {topic} last week", topic, days_ago[i]))
        elif style == "readback":
            person = people[p]
            p += 1
            k1, k2, school = kids[k], kids[k + 1], schools[s]
            k, s = k + 2, s + 1
            month = rng.choice(_MONTHS)
            out.append(ExactNeedle(style, f"{person}'s two kids are {k1} and {k2} and they both start at {school} in {month}.",
                                   f"read me back what I told you about {person}'s kids", person, days_ago[i]))
        else:
            person, other, topic = people[p], people[p + 1], topics[t]
            p, t = p + 2, t + 1
            out.append(ExactNeedle(style, f"I told {person} I would come on {a} not {b} for the {topic}.",
                                   f"did I tell {person} {a} or {b} for the {topic}", person, days_ago[i],
                                   decoy=f"I told {other} I would come on {b} not {a} for the {topic}."))
    return out


def exact_turns(seed: str, n: int = 20) -> "list[dict]":
    """The turns that teach the corpus: decoys first (they are older chatter), then the needles oldest first. ``{"text", "day_offset"}``."""
    corpus = exact_corpus(seed, n)
    turns = [{"text": x.decoy, "day_offset": min(28, x.day_offset + 1)} for x in corpus if x.decoy]
    turns += [{"text": x.sentence, "day_offset": x.day_offset} for x in sorted(corpus, key=lambda y: -y.day_offset)]
    return sorted(turns, key=lambda t: -t["day_offset"])


# ═══ (l) long-range associative recall ═══════════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class HopItem:
    kind: str                     # date | join
    fact_a: str                   # said first (weeks before B)
    fact_b: str
    day_a: int                    # days ago
    day_b: int
    question: str
    a_need: "tuple[str, str]"     # a row must hold BOTH strings to count as fact A (subject, answer token)
    b_need: "tuple[str, str]"


def hop_corpus(seed: str, n: int = 20) -> "list[HopItem]":
    """``n`` (<= 20) two-fact questions, half date comparisons and half joins. Fact A is 14-28 days old, fact B 1-9 days old."""
    if not 2 <= n <= 20:
        raise ValueError("n must be 2..20")
    rng = random.Random(f"zmb-l-hops:{seed}")
    people = rng.sample(_PEOPLE, len(_PEOPLE))
    kids = rng.sample(_KIDS, len(_KIDS))
    topics = rng.sample(_TOPICS, len(_TOPICS))
    places = rng.sample(_PLACES2, len(_PLACES2))
    dates, seen = [], set()
    while len(dates) < 20:
        d, m = rng.randint(2, 28), rng.choice(_MONTHS)
        if (d, m) not in seen:
            seen.add((d, m))
            dates.append(f"{d} {m}")
    out: "list[HopItem]" = []
    n_date = (n + 1) // 2
    for i in range(n):
        da, db = rng.randint(14, 28), rng.randint(1, 9)
        if i < n_date:
            person, topic = people[i], topics[i]
            ta, tb = dates[2 * i], dates[2 * i + 1]
            out.append(HopItem("date", f"{person}'s birthday is on {ta}.", f"User's {topic} appointment is on {tb}.", da, db,
                               f"is {person}'s birthday before my {topic} appointment", (person, ta), (topic, tb)))
        else:
            j = i - n_date
            kid, place, rel, person = kids[j], places[j], rng.choice(_RELS), people[len(people) - 1 - j]
            out.append(HopItem("join", f"User's child {kid}'s school is in {place}.", f"User's {rel} {person} lives in {place}.", da, db,
                               f"who in my family lives near {kid}'s school", (kid, place), (person, place)))
    return out


# ═══ (k) reflection: thirty days of a household, and the gold its observations are judged against ═══════════════════════════════════

@dataclass(frozen=True)
class Thread:
    """A story that develops over weeks. An observation COVERS it when it holds every ``identity`` token and the ``key`` token."""
    id: str
    identity: "tuple[str, ...]"
    key: str
    last_day: int                 # the day (of 30) of its latest turn
    ask: str = ""                 # the question whose answer should surface it ("" = none)


@dataclass
class Life:
    turns: "list[dict]" = field(default_factory=list)          # {"day": 1..30, "speaker": typed|taught, "text": str}  (day 30 = today)
    people: "tuple[str, ...]" = ()
    entities: "frozenset[str]" = frozenset()                   # every name / place / org this life uses (lower-cased)
    pairs: "frozenset[frozenset[str]]" = frozenset()           # entity pairs the owner put in the SAME sentence (the only links that are true)
    claims: "list[tuple[str, ...]]" = field(default_factory=list)          # token groups the owner stated, lower-cased
    threads: "list[Thread]" = field(default_factory=list)
    stale: "list[tuple[str, str, str]]" = field(default_factory=list)      # (subject, old value, current value), lower-cased
    questions: "list[tuple[str, tuple[str, ...]]]" = field(default_factory=list)   # (question, thread ids it should surface)
    #: what a nightly model might PROPOSE on top of the truth (scripted into the arms that have no model of their own): every kind of mistake
    proposals_true: "list[str]" = field(default_factory=list)
    proposals_fabricated: "list[str]" = field(default_factory=list)       # a link / attribute nobody stated
    proposals_stale: "list[str]" = field(default_factory=list)            # an invalidated fact restated as current
    proposals_hedged: "list[str]" = field(default_factory=list)           # a user-stated fact restated as an inference
    proposals_presented_as_said: "list[str]" = field(default_factory=list)   # an inference presented as something the user said


def life(seed: str) -> Life:
    """Thirty days (day 1 oldest, day 30 today) of an invented household with four developing threads and two changed facts."""
    rng = random.Random(f"zmb-k-life:{seed}")
    ppl = rng.sample(_PEOPLE, 8)
    f1, col, sis, nbr, other, ghost = ppl[0], ppl[1], ppl[2], ppl[3], ppl[4], ppl[5]
    kid = rng.choice(_KIDS)
    orgs = rng.sample(_ORGS, 3)
    places = rng.sample(_PLACES2, 6)
    schools = rng.sample(_SCHOOLS, 1)[0]
    sport, instr = rng.choice(_SPORTS), rng.choice(_INSTR)
    doc_a, doc_b = rng.sample(_DOCTORS, 2)
    org1, org2, org3 = orgs
    pl_old_s, pl_new_s, pl_from, pl_to, pl_ghost, pl_other = places
    # (day, how it was said, text): "typed" = ordinary speech; "taught" = the owner also says "remember that ..." for the two facts that CHANGE
    turns: "list[tuple[int, str, str]]" = [
        # thread 1: a friend changes jobs
        (3, "typed", f"{f1} told me she is fed up at {org1} and might look around."),
        (10, "typed", f"{f1} has an interview at {org2} on Thursday."),
        (17, "typed", f"{f1} got the offer from {org2}!"),
        (24, "typed", f"{f1} starts at {org2} next Monday."),
        (24, "taught", f"{f1} works at {org2}."),
        # thread 2: a colleague moves house
        (5, "typed", f"My colleague {col} is thinking about moving from {pl_from} to {pl_to}."),
        (12, "typed", f"{col} put an offer on a house in {pl_to}."),
        (20, "typed", f"{col}'s offer on the {pl_to} house was accepted."),
        # thread 3: the owner's own knee
        (8, "typed", f"My knee has been sore since {sport} on Sunday."),
        (15, "typed", f"I saw Dr {doc_a} about my knee and the physio starts soon."),
        (25, "typed", "My knee is so much better after the physio."),
        # thread 4: a child's concert
        (6, "typed", f"{kid} has a school concert at {schools} on the 14th."),
        (13, "typed", f"{kid} is learning the {instr} for the concert."),
        (27, "typed", f"{kid}'s concert went really well."),
        # a fact that changes: the sister moves (the friend's job above is the other)
        (2, "typed", f"My sister {sis} lives in {pl_old_s}."),
        (18, "typed", f"{sis} has moved to {pl_new_s}."),
        (18, "taught", f"{sis} lives in {pl_new_s}."),
        # a dentist who changes, a neighbour (a pair that must never be linked to anyone else), and plain life
        (4, "typed", f"My dentist is Dr {doc_a}."),
        (21, "typed", f"I switched dentists, I see Dr {doc_b} now."),
        (9, "typed", f"My neighbour {nbr} waters our plants when we are away."),
        (16, "typed", f"{other} lives in {pl_other}."),
        (1, "typed", "I like slow mornings and strong tea."),
        (7, "typed", "Remind me to take the bins out."),
        (14, "typed", "I want to cook more on Sundays."),
        (22, "typed", "I need to book the car in for a service."),
        (29, "typed", "I'm tired after a long week but happy."),
    ]
    turns.sort(key=lambda t: t[0])
    lf = Life(turns=[{"day": d, "speaker": sp, "text": t} for d, sp, t in turns], people=tuple(ppl))
    ents = set(ppl) | {kid} | set(orgs) | set(places) | {schools, doc_a, doc_b}
    ents |= {e.split()[0] for e in ents if " " in e}             # "Pinecrest Mills" is also "Pinecrest" (what a model says)
    lf.entities = frozenset(e.lower() for e in ents)
    # links the owner stated: entities named in one sentence (a model that joins any two others has invented a link)
    ent_tokens = {e: e.lower() for e in ents}
    pairs, claims = set(), []
    for _d, _sp, text in turns:
        low = text.lower()
        named = sorted({v for v in ent_tokens.values() if re.search(r"(?<![a-z])" + re.escape(v) + r"(?![a-z])", low)})
        for i in range(len(named)):
            for j in range(i + 1, len(named)):
                pairs.add(frozenset((named[i], named[j])))
        if len(named) >= 1:
            claims.append(tuple(named))
    lf.pairs, lf.claims = frozenset(pairs), claims
    lf.threads = [
        Thread("job", (f1.lower(),), org2.lower().split()[0], 24, ask=f"what's been going on with {f1} lately"),
        Thread("move", (col.lower(),), pl_to.lower().split()[0], 20, ask=f"what's been going on with {col} lately"),
        Thread("knee", ("knee",), "physio", 25, ask="how has my week been"),
        Thread("concert", (kid.lower(),), "concert", 27, ask="how has my week been"),
    ]
    lf.stale = [(sis.lower(), pl_old_s.lower().split()[0], pl_new_s.lower().split()[0]),
                (f1.lower(), org1.lower().split()[0], org2.lower().split()[0])]
    lf.questions = [(f"what's been going on with {f1} lately", ("job",)), (f"what's been going on with {col} lately", ("move",)),
                    ("how has my week been", ("knee", "concert", "job"))]
    lf.proposals_true = [f"{f1} accepted the offer from {org2} and starts there soon.", f"{col}'s offer on the {pl_to} house was accepted.",
                         f"The knee is recovering after the physio.", f"{kid}'s school concert at {schools} went well."]
    lf.proposals_fabricated = [f"{ghost} is {f1}'s mother and lives in {pl_ghost}.", f"{nbr} works at {org2}.", f"{col} is {sis}'s husband."]
    lf.proposals_stale = [f"{sis} lives in {pl_old_s}.", f"{f1} works at {org1}."]
    lf.proposals_hedged = [f"{sis} probably lives in {pl_new_s}.", f"{f1} seems to be starting at {org2}."]
    lf.proposals_presented_as_said = [f"You told me {ghost} moved to {pl_ghost}.", f"You said {nbr} is changing jobs to {org1}."]
    return lf


# ── (k) the night mind's extra lives (K6-K12): a dense day, drift and a flat week, a restraint set, a resolution set, labelled moments ───────────────────

#: routine household commands (never memory-worthy): a real day is mostly these (the pilot found a recency tail held 0 of 9 durable facts)
ROUTINE_TEMPLATES = ("turn on the {room} lights", "turn off the {room} lamp", "set a timer for {k} minutes", "pause the music", "play {genre} in the {room}",
                     "what's the weather like in {city}", "what time is it in {city}", "skip this song", "turn the volume down a bit", "stop the timer",
                     "dim the {room} lights to {k} percent", "set an alarm for seven fifteen", "how many ounces in a cup", "turn on the porch light")
_ROOMS = ("kitchen", "lounge", "bedroom", "hall", "study")
_GENRES2 = ("jazz", "classical", "folk", "acoustic", "ambient", "soul")
_CITIES = ("Lisbon", "Oslo", "Perth", "Cork", "Turin")


def routine_commands(seed: str, day: int, n: int) -> "list[str]":
    """``n`` routine commands for one day (seeded; none names a person, a date or a feeling, so stage 1 of the night mind drops every one)."""
    rng = random.Random(f"zmb-k-routine:{seed}:{day}")
    return [rng.choice(ROUTINE_TEMPLATES).format(room=rng.choice(_ROOMS), k=rng.randint(2, 45), genre=rng.choice(_GENRES2), city=rng.choice(_CITIES))
            for _ in range(n)]


@dataclass(frozen=True)
class LayoutItem:
    kind: str          # life | cmd
    day: int           # 1..30 (30 = today)
    text: str


def dense_layout(seed: str, per_day: int = 40) -> "tuple[list[LayoutItem], list[str]]":
    """The DENSE life (K7): the same thirty days, with ``per_day`` routine commands around the life turns of every day. The turns of the 'knee' and
    'concert' threads are said at the START of their day; the turns of the 'job' and 'move' threads are PLANTED LATE, after 90 % of the day's commands -
    where a transcript cut at 3,000 characters never reaches. Returns ``(items in time order, the late thread ids)``."""
    lf = life(seed)
    late_ids = ["job", "move"]
    late_tokens = [t.identity[0] for t in lf.threads if t.id in late_ids]
    items: "list[LayoutItem]" = []
    cut = int(per_day * 0.9)
    for day in range(1, 31):
        mine = [t for t in lf.turns if t["day"] == day and t["speaker"] == "typed"]
        late = [t for t in mine if any(tok in t["text"].lower() for tok in late_tokens)]
        early = [t for t in mine if t not in late]
        cmds = routine_commands(seed, day, per_day)
        items += [LayoutItem("life", day, t["text"]) for t in early] + [LayoutItem("cmd", day, c) for c in cmds[:cut]]
        items += [LayoutItem("life", day, t["text"]) for t in late] + [LayoutItem("cmd", day, c) for c in cmds[cut:]]
    return items, late_ids


@dataclass
class Variant:
    """A small synthetic month for one K cell: turns (day 1..30, text) and the gold the cell reads."""
    name: str
    turns: "list[dict]" = field(default_factory=list)
    gold: "dict[str, object]" = field(default_factory=dict)


def variant_life(name: str, seed: str) -> Variant:
    """``drift`` (a thread goes quiet, another changes), ``flat`` (the same cadence with neither), ``restraint`` (sensitive threads and one benign), ``resolution``
    (a plan with no outcome, a plan that finished, a thread nobody mentions again). Names are drawn from the pools above (invented)."""
    rng = random.Random(f"zmb-k-variant:{name}:{seed}")
    ppl, places, kids = rng.sample(_PEOPLE, 10), rng.sample(_PLACES2, 4), rng.sample(_KIDS, 3)
    friend, nbr, sis, uncle, third, club_host, cousin = ppl[0], ppl[1], ppl[2], ppl[3], ppl[4], ppl[5], ppl[6]
    pl1, pl2 = places[0], places[1]
    schools, instr = rng.sample(_SCHOOLS, 1)[0], rng.choice(_INSTR)
    v = Variant(name)

    def say(day: int, text: str) -> None:
        v.turns.append({"day": day, "text": text})
    if name in ("drift", "flat"):
        for d in (2, 9, 16) + ((23, 29) if name == "flat" else ()):
            say(d, {2: f"I went for a run with {friend} this morning.", 9: f"Running with {friend} again, we are building up to the fun run.",
                    16: f"{friend} and I ran 5k today.", 23: f"{friend} and I ran along the river this morning.", 29: f"Another run with {friend} before work."}[d])
        say(5, f"We are going to {pl1} on the 12th for {kids[0]}'s birthday.")
        say(20, f"The {pl1} trip is off, we are going to {pl2} instead." if name == "drift" else f"Booked the train to {pl1} for {kids[0]}'s birthday.")
        for d, t in ((3, f"My neighbour {nbr} gave me seedlings for the garden."), (10, f"{nbr} helped me plant the garden beds."),
                     (17, f"The garden beds are doing well, thanks to {nbr}."), (24, f"{nbr} brought round more seedlings for the garden."),
                     (29, f"Picked the first tomatoes from the garden with {nbr}.")):
            say(d, t)
        v.gold = ({"quiet": [friend.lower()], "changed": [pl1.lower()], "steady": [nbr.lower()]} if name == "drift"
                  else {"quiet": [], "changed": [], "steady": [nbr.lower(), friend.lower(), pl1.lower()]})
    elif name == "restraint":
        for d, t in ((17, "My knee has been sore since rowing on Sunday."), (24, "I saw Dr Okafor about my knee and the physio starts soon."),
                     (19, f"My uncle {uncle} died last week and I miss him."), (22, f"The funeral for uncle {uncle} is on Friday."),
                     (20, f"I'm worried about the loan repayments, I can't afford them."), (26, "The loan is behind again and I owe more now."),
                     (21, f"I argued with my sister {sis} again about mum's house."), (27, f"{sis} and I fought about the house again."),
                     (18, f"I'm worried about {third}'s test results."), (23, f"Don't bring up {third}'s test results again, that is between us."),
                     (16, f"{kids[1]} has a school concert at {schools} on the 20th."), (23, f"{kids[1]} is learning the {instr} for the concert."),
                     (28, f"{kids[1]} practised the {instr} for an hour today.")):
            say(d, t)
        v.gold = {"leave": ["knee", uncle.lower(), "loan", sis.lower(), third.lower()], "benign": [kids[1].lower()],
                  "asks": [("how is my knee", "knee"), (f"what did I say about uncle {uncle}", uncle.lower())]}
    elif name == "resolution":
        say(3, f"I am planning a surprise party for {third} on Saturday.")
        say(6, f"The {club_host} quiz night is on the 20th.")
        say(22, f"The {club_host} quiz night went really well, we came second.")
        say(4, f"My cousin {cousin} is visiting from {pl1} next month.")
        v.gold = {"open": [third.lower()], "resolved": [club_host.lower()], "absent": [cousin.lower()]}
    else:
        raise ValueError(f"unknown variant life {name!r} (known: drift, flat, restraint, resolution)")
    v.turns.sort(key=lambda t: t["day"])
    return v


#: K12: turns with the kind / feeling / weight a careful reader gives them (the labels are written, not generated; the cell scores the model against them)
LABELLED_MOMENTS = (
    ("I'm really worried about my mum's operation on Thursday.", "health", "worried", 3),
    ("Tamsin got the offer from Pinecrest Mills!", "progress", "none", 3),
    ("I'm tired after a long week but happy.", "feeling", "happy", 2),
    ("The Saltreach trip is off, we are going to Oldmere instead.", "change", "none", 2),
    ("My knee is so much better after the physio.", "health", "relieved", 2),
    ("Dagny's offer on the house was accepted.", "progress", "none", 3),
    ("I'm stressed about the deadline on Friday.", "feeling", "stressed", 2),
    ("Rowan has a school concert on the 14th.", "plan", "none", 2),
    ("Faramir lives in Quinford.", "person", "none", 1),
    ("I'm so proud of how Hazel played at the recital.", "feeling", "proud", 2),
    ("I'm dreading the dentist appointment next Tuesday.", "health", "worried", 2),
    ("My sister Brynja has moved to Pellham.", "change", "none", 2),
)


def labelled_moments() -> "list[dict]":
    return [{"text": t, "kind": k, "feeling": f, "weight": w} for t, k, f, w in LABELLED_MOMENTS]


def entity_tokens(text: str, entities: "frozenset[str]") -> "set[str]":
    """The life's entities a text names (whole-word, case-blind, multi-word names matched whole)."""
    low = " " + re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower()) + " "
    low = re.sub(r"'s\b", "", low)
    return {e for e in entities if re.search(r"(?<![a-z0-9])" + re.escape(e) + r"(?![a-z0-9])", low)}


# ═══ (m) the memory protocol ═════════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ProtocolPrompt:
    kind: str                     # needed | unneeded | silent
    text: str
    gold: str = ""                # the token a correct answer carries (needed)


_FAVOURITES = (("tea", "Quillberry"), ("biscuit", "Hollowmeal"), ("cheese", "Marrowdale"), ("soup", "Ferngill"))


def protocol_corpus(seed: str) -> "tuple[list[str], list[ProtocolPrompt]]":
    """(the fact sentences to teach, the prompts): 14 questions the store CAN answer (10 about named people, 4 about the owner's own tastes),
    10 device commands / small talk / world questions that must not touch memory, 8 questions about people the owner never mentioned."""
    facts = needlemod.corpus(seed, 10)
    rng = random.Random(f"zmb-m-prompts:{seed}")
    sentences = [n.fact for n in facts]
    prompts = [ProtocolPrompt("needed", n.direct, n.answer) for n in facts]
    for what, token in _FAVOURITES:
        sentences.append(f"User's favourite {what} is {token}.")
        prompts.append(ProtocolPrompt("needed", f"what is my favourite {what}", token))
    unneeded = ["turn on the kitchen lights", "set a timer for ten minutes", "pause the music", "good morning", "thanks that's all",
                "turn the volume down a bit", "stop the timer", "skip this song", "what's the weather like in Lisbon today",
                "what time is it in Lisbon"]
    prompts += [ProtocolPrompt("unneeded", u) for u in unneeded]
    names = needlemod.distractor_names(seed)
    shapes = ("where does {n} live", "what does {n} drive", "where does {n} work", "what instrument does {n} play",
              "what is {n} allergic to", "where does {n} live", "what does {n} drive", "where does {n} work")
    for name, shape in zip(rng.sample(names, 8), shapes):
        prompts.append(ProtocolPrompt("silent", shape.format(n=name)))
    return sentences, prompts


_PERSONAL = re.compile(r"\b(my|me|i|i'm|told|said|remember|know about|last|yesterday|ago)\b")
_QUESTION = re.compile(r"^(what|where|who|when|which|how|did|do|does|is|are|was|were|can you tell)\b")
_COMMAND = re.compile(r"^(turn|set|play|pause|stop|skip|dim|add|remind|read|good morning|thanks|thank you|volume|mute)\b")
_WORLD = re.compile(r"\b(weather|time|rain|forecast|news|headlines?|timer|alarm|calendar|temperature)\b")
_CAPITAL = re.compile(r"(?<!^)\b[A-Z][a-z]{2,}\b")
_PAST = re.compile(r"\b(told|said|last|yesterday|ago|remember)\b")


def _zoe_policy(prompt: str) -> bool:
    """Zoe's recall doctrine (soul.ts:35, agents/zoe.ts:75,135): anything personal, about a person or about a past conversation -> recall_memory
    FIRST; device commands, small talk and the world's weather / time are not memory questions (a tool answers those)."""
    low = prompt.strip().lower()
    if _COMMAND.match(low) or _WORLD.search(low):
        return False
    return bool(_PERSONAL.search(low) or _QUESTION.match(low))


def _mempalace5_policy(prompt: str) -> bool:
    """MemPalace's PALACE_PROTOCOL rules 2-3 (mcp_server/tools_read.py:370): before responding about any PERSON, project or past event, search
    first; unsure of a name or relationship -> "let me check". A named entity or a past-event marker triggers it; a first-person question with
    neither (``what is my favourite tea``) does not."""
    low = prompt.strip().lower()
    if _COMMAND.match(low):
        return False
    return bool(_CAPITAL.search(prompt.strip()) or _PAST.search(low))


def _hindsight_policy(prompt: str) -> bool:
    """Hindsight's recommended usage: recall before generating EVERY response (the retain / recall / reflect loop); no routing."""
    return True


#: protocol name -> (trigger policy, where its rule comes from). The lab half scores each one; the brain half (tier ``full``) scores what a real
#: brain does under each protocol's text. These are deterministic STAND-INS for the protocol's rule, never a measurement of a brain.
POLICIES = {
    "zoe": (_zoe_policy, "Zoe's imperative recall doctrine (soul.ts, agents/zoe.ts: recall_memory fired 67% -> 97% once imperative)"),
    "mempalace5": (_mempalace5_policy, "MemPalace PALACE_PROTOCOL, five rules (rules 2 and 3: search before responding about a person, project or past event)"),
    "hindsight": (_hindsight_policy, "Hindsight's recommended usage: recall on every turn, reflect for synthesis"),
}

#: what a reply that knows nothing says (the scorers count it as "I don't know")
DECLINE = "I don't have that saved."


def anchored_reader(rows: "list[dict]", anchor: "tuple[str, ...]", *, sycophantic: bool = False) -> str:
    """The scripted READER of the protocol cells (brain-free): it answers from the first packet row that names EVERY anchor token (the identity of what
    was asked: a person's name, or "favourite" + "tea") and declines otherwise. ``sycophantic`` = the instrument's negative control: it answers from the
    nearest row whatever it says. An instrument check, never a statement about a brain's reply."""
    if sycophantic and rows:
        return rows[0].get("text", "")
    for r in rows:
        low = (r.get("text") or "").lower()
        if anchor and all(re.search(r"(?<![a-z0-9])" + re.escape(a.lower()) + r"(?![a-z0-9])", low) for a in anchor):
            return r.get("text", "")
    return DECLINE


def prompt_anchor(p: "ProtocolPrompt") -> "tuple[str, ...]":
    """The tokens a packet row must hold for the reader to answer ``p`` (the capitalised name; or "favourite" + the thing)."""
    m = re.search(r"favourite (\w+)", p.text)
    if m:
        return ("favourite", m.group(1))
    names = re.findall(r"\b[A-Z][a-z]{2,}\b", p.text)
    return tuple(names[:1])


def gold_for_scoring(lf: "Life") -> "dict":
    """What ``scorers.classify_observation`` needs, as plain data (the scorer module imports nothing from here)."""
    foreign = {x.lower() for x in POOL_STRINGS} - set(lf.entities)
    moved = [sorted((old, new)) for _subj, old, new in lf.stale]       # "moved from A to B" links the two places: a change is not a fabricated link
    return {"entities": sorted(lf.entities), "pairs": [sorted(p) for p in lf.pairs] + moved, "foreign": sorted(foreign),
            "stale": [list(s) for s in lf.stale], "claims": [list(c) for c in lf.claims if len(c) >= 2],
            "threads": [{"id": t.id, "identity": list(t.identity), "key": t.key} for t in lf.threads],
            "hedges": list(HEDGES), "history": list(HISTORY_MARKS), "stated": list(STATED_MARKS)}
