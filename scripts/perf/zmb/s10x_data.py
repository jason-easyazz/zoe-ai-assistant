"""S10x data: the Samantha bar's S10 "one-word change of state", as inputs (synthetic: invented people and things, no household text).

``PAIRS`` is the 30 (old stored fact, what the owner SAYS, the fact a digest would extract) triples of the MemPalace deep dive's pilot
(``scripts/perf/zmb/pilot/mempalace_deepdive.py s10`` on PR #1904: docs/research/mempalace-deep-dive-2026-10-06.md section 4.4); the old-row and
extracted-fact texts are the pilot's, so the retrieval numbers are comparable. ``NON_CHANGES`` / ``HARD_NON_CHANGES`` are the pilot's 30 + 10
statements about the same objects that end nothing, re-said in the owner's FIRST PERSON (the pilot wrote them as extracted facts: "User played the
cello"); the hard ten carry a cue word ("almost gave up ... but kept going", "not giving up running", "stopped running to tie a shoelace") and are what a
cue table or a retrieval top-1 cannot separate. ``GENERIC`` is 40 unrelated rows; ``other_person_copies`` re-subjects each old row to a sister
("User's sister Priya plays the cello ..."), the row an "I gave up the cello" must never retire.

``HELD_OUT_*`` is a second, smaller list written for the PREFILTER after its cue vocabulary was fixed from the first one: its recall is the honest
estimate (the 30 pairs above were seen while the cues were written).
"""
from __future__ import annotations

#: (old stored fact, what the owner says, the fact an extractor would write)
PAIRS = [
    ("User plays the cello in a community orchestra on Tuesday evenings.", "I gave up the cello.", "User gave up the cello."),
    ("User runs five kilometres every morning before work.", "I stopped running.", "User stopped running."),
    ("User has a season ticket for the Harbour Rovers.", "I let my season ticket lapse.", "User let their Harbour Rovers season ticket lapse."),
    ("User drives a blue Corolla.", "I sold the Corolla.", "User sold the Corolla."),
    ("User works at Northgate Library three days a week.", "I left the library.", "User left Northgate Library."),
    ("User takes piano lessons on Thursdays with Ms Halvorsen.", "I quit piano lessons.", "User quit piano lessons."),
    ("User is vegetarian.", "I eat meat again.", "User eats meat again."),
    ("User smokes a pipe in the evening.", "I have quit smoking.", "User quit smoking."),
    ("User goes to the climbing gym on Saturdays.", "I cancelled my climbing gym membership.", "User cancelled their climbing gym membership."),
    ("User has a weekly pottery class at the community hall.", "I finished the pottery class.", "User finished the pottery class."),
    ("User keeps two goldfish in the lounge.", "The goldfish died.", "User's goldfish died."),
    ("User subscribes to the Daily Ledger newspaper.", "I cancelled the newspaper.", "User cancelled the Daily Ledger subscription."),
    ("User is learning Portuguese with an app every night.", "I dropped Portuguese.", "User dropped Portuguese."),
    ("User coaches the under-tens netball team.", "I stepped down as netball coach.", "User stepped down as netball coach."),
    ("User grows tomatoes in the back garden.", "I ripped out the tomatoes.", "User ripped out the tomatoes."),
    ("User takes a daily blood pressure tablet.", "The doctor took me off the tablets.", "User no longer takes blood pressure tablets."),
    ("User volunteers at the food bank on Mondays.", "I stopped volunteering at the food bank.", "User stopped volunteering at the food bank."),
    ("User drinks coffee with breakfast.", "I switched to tea.", "User switched from coffee to tea."),
    ("User has a membership at the Quay Street pool.", "I no longer go swimming.", "User no longer goes swimming."),
    ("User writes in a gratitude journal each night.", "I gave up journaling.", "User gave up journaling."),
    ("User walks the dog, Biscuit, at six every evening.", "Mika took over walking Biscuit.", "Mika walks Biscuit now."),
    ("User plays tennis on Sunday mornings.", "I hung up my racquet.", "User stopped playing tennis."),
    ("User is training for the Harbour Half Marathon in March.", "I dropped out of the half marathon.", "User dropped out of the Harbour Half Marathon."),
    ("User sings in the church choir.", "I left the choir.", "User left the choir."),
    ("User rents a flat on Elm Street.", "We bought a house.", "User bought a house."),
    ("User has a standing Friday lunch with Tove.", "Tove and I stopped doing Friday lunches.", "User stopped the Friday lunches with Tove."),
    ("User uses a standing desk at work.", "I got rid of the standing desk.", "User got rid of the standing desk."),
    ("User plays chess online every night.", "I deleted my chess account.", "User deleted their chess account."),
    ("User takes the 7:40 bus to work.", "I cycle to work now.", "User cycles to work now."),
    ("User paints watercolours at the weekend.", "I haven't painted in months, I gave it up.", "User gave up painting."),
]

#: one statement per pair (index-aligned) about the SAME object that ends nothing: a mention. First person.
NON_CHANGES = [
    "I played the cello at the orchestra last night.", "I ran faster than usual this morning.",
    "I watched the Harbour Rovers on Saturday.", "I washed the Corolla at the weekend.",
    "I took a book back to Northgate Library.", "I bought new piano lesson books.",
    "I cooked a vegetarian curry for the neighbours.", "I lit the pipe of a visiting uncle.",
    "I took Mika to the climbing gym.", "I showed the pottery class bowl to Tove.",
    "I fed the goldfish extra food.", "I read the Daily Ledger crossword.",
    "I practised Portuguese with a neighbour.", "I watched the netball team win.",
    "I picked the tomatoes in the garden.", "I collected the blood pressure tablets from the chemist.",
    "I drove to the food bank on Monday.", "I drank coffee at the cafe.",
    "I went to the Quay Street pool with Leo.", "I wrote a long journal entry tonight.",
    "I walked Biscuit to the park.", "I watched tennis on television.",
    "I ran the Harbour Half Marathon course on foot.", "I sang in the church choir at Christmas.",
    "I walked past the flat on Elm Street.", "I had Friday lunch with Priya.",
    "I moved the standing desk to the window.", "I played chess online with Ravi.",
    "I took the 7:40 bus on Tuesday.", "I painted a watercolour for Tove.",
]

#: (statement, the index in PAIRS of the row it is about): carry a cue word, end nothing
HARD_NON_CHANGES = [
    ("I almost gave up the cello but kept going.", 0), ("I am not giving up running.", 1),
    ("I nearly sold the Corolla but changed my mind.", 3), ("I stopped running to tie a shoelace.", 1),
    ("I quit the meeting early.", 4), ("I dropped Mika at the climbing gym.", 8),
    ("I finished the pottery bowl.", 9), ("I gave up a seat on the bus.", 0),
    ("I left the library early.", 4), ("I cancelled the dentist appointment.", 9),
]

GENERIC = [
    "User likes listening to jazz in the kitchen.", "User's daughter plays the violin in the school orchestra.",
    "User owns a red bicycle.", "User prefers window seats on flights.", "User's mother lives in Perth.",
    "User is allergic to kiwi.", "User's favourite film is Local Hero.", "User has two children, Mika and Leo.",
    "User's dentist is Dr Voss.", "User's birthday is on the 12th of March.", "User likes the lounge lights dimmed.",
    "User's car insurance renews in June.", "User's plumber is Kofi Mensah.", "User takes sugar in tea.",
    "User's gate code is private.", "User wants to visit Lisbon next spring.", "User's sister Priya lives in Auckland.",
    "User likes oat milk.", "User has a standing dentist check-up every six months.", "User reads the news on the radio.",
    "User's neighbour is called Ravi.", "User wakes at six thirty.", "User doesn't like loud music late at night.",
    "User's boiler was serviced in October.", "User is saving for a new sofa.", "User likes jasmine tea.",
    "User's favourite colour is green.", "User has a library card at Northgate.", "User keeps spare keys with the neighbour.",
    "User likes quiet mornings.", "User's phone is on the family plan.", "User prefers cash at the market.",
    "User's partner is Tove.", "User's dog is called Biscuit.", "User likes folk music on Sundays.",
    "User takes the bins out on Thursday.", "User's wifi password is on the fridge.", "User likes to cook on Friday.",
    "User is learning to bake sourdough.", "User's favourite mug is the blue one.",
]

#: the prefilter's HELD-OUT list (see the module docstring): plain statements of change, and plain statements that are not
HELD_OUT_CHANGES = [
    "I resigned from the bakery.", "We moved house last month.", "I have stopped drinking coffee.",
    "My knee is better, so I do not need the brace any more.", "I sold my bike.", "The cat passed away on Sunday.",
    "I switched banks.", "I returned the leased car.", "I am no longer on the committee.", "I closed my savings account.",
    "I gave my guitar away.", "I dropped the evening course.", "I stopped taking the vitamin.", "We got rid of the trampoline.",
    "I retired in June.",
]
HELD_OUT_MENTIONS = [
    "I saw a cello today.", "I walked to the shops.", "The weather is lovely.", "I made soup for lunch.",
    "I need to buy milk.", "Dana called about the weekend.", "I love that song.", "The meeting ran long.",
    "I read a good book.", "What is the capital of Portugal?",
]


def other_person_copies() -> "list[str]":
    """The old rows re-subjected to a sister: what an owner's "I gave up the cello" must never retire."""
    return [old.replace("User's", "User's sister Priya's", 1) if old.startswith("User's")
            else old.replace("User", "User's sister Priya", 1) for old, _say, _fact in PAIRS]


def pool() -> "list[str]":
    """The 100 stored rows of the pilot's pool: 40 generic, 30 old facts, 30 other-person copies. Seeded in THIS order, so the newest rows
    are the copies: a store whose retrieval is broken (newest first) offers the copies, which is what makes a copy cell able to go red."""
    return list(GENERIC) + [p[0] for p in PAIRS] + other_person_copies()
