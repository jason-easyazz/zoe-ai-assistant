"""The recall corpus (axis d): needles, household chatter, paraphrase queries. Seeded, synthetic, no household text.

``corpus(seed)`` mints the 20 NEEDLES the owner teaches in the first sessions: each is one fact sentence plus two
ways to ask for it - a DIRECT question and a PARAPHRASE that shares the subject's name but not the stored wording
(so a hit is retrieval, not string identity). ``chatter(seed, n)`` mints the FILLER turns that follow: mostly the
smart-home chatter a household actually produces (never stored), some light preferences (stored as ordinary rows) and
some **near-miss** facts - the same sentence shape as a needle about somebody else, with another answer - so the store
holds many rows that look like the needle and are not it. All names are invented (nobody real); the answer tokens exist
nowhere else in the world, so a hit is a pure string match and never a judgement.

Everything is a function of ``(seed, n)``: the same seed gives the same needles and the same filler on every arm, and a
held-out seed (``--seed fresh``) gives a new corpus of the same shapes.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

N_NEEDLES = 20

# who the facts are about: 20 needle subjects, and a disjoint pool for the near-miss distractors
_SUBJECTS = ("Aldo", "Brigid", "Caspian", "Delphine", "Evander", "Fenella", "Gideon", "Hestia", "Ivo", "Juniper",
             "Kestrel", "Lucan", "Marnie", "Nico", "Odessa", "Pascal", "Quill", "Rosalind", "Silas", "Thea",
             "Ulrich", "Verity", "Wystan", "Ximena", "Yarrow", "Zelda", "Amias", "Bryony", "Corwin", "Dalia")
_ANSWERS = ("Bergvik", "Oldmere", "Tarnholt", "Quinford", "Saltreach", "Wenlow", "Marlowby", "Ashgrove", "Pellham",
            "Cragmoor", "Dunwich", "Eldermoss", "Fallowby", "Gannet", "Harrowdale", "Ironbridge", "Jessop", "Kelmarsh",
            "Lowthorpe", "Mistley", "Netherby", "Orrinsay", "Penhallow", "Rookwood", "Stannick", "Thistleby",
            "Ullswater", "Valehead", "Whinmoor", "Yardley", "Zennor", "Applecross", "Bracken", "Cullen", "Dovedale",
            "Eskmouth", "Fairlight", "Glenrock", "Hollin", "Inverdale")

#: (fact template, direct question, paraphrase question). {n}=subject, {a}=answer. The paraphrase shares NO word
#: with the stored sentence except the subject's name.
_SHAPES = (
    ("User's friend {n} lives in {a}.", "where does {n} live", "which town is {n} based in these days"),
    ("User's neighbour {n} drives a {a}.", "what does {n} drive", "which vehicle is {n} always seen in"),
    ("User's colleague {n} works at {a}.", "where does {n} work", "which employer pays {n} every month"),
    ("User's cousin {n} plays the {a}.", "what instrument does {n} play", "which music-making kit does {n} own"),
    ("User's friend {n} is allergic to {a}.", "what is {n} allergic to", "which food must {n} steer clear of"),
)

# household chatter that never becomes a row (the extractor has nothing to take from it)
_CHATTER = (
    "turn on the kitchen lights", "turn off the lounge lamp", "set a timer for {k} minutes", "pause the music",
    "what's the weather like today", "what time is it in Lisbon", "add {item} to the shopping list",
    "dim the bedroom lights to {k} percent", "skip this song", "how long until the oven is ready",
    "what's on my calendar tomorrow", "set an alarm for seven fifteen", "turn the volume down a bit",
    "is it going to rain this weekend", "read me the headlines", "how many ounces in a cup", "stop the timer",
    "what's {k} times {m}", "remind me to take the bins out", "good morning", "thanks that's all",
    "can you turn the heating up", "play {genre} in the kitchen", "turn on the porch light",
)
_ITEMS = ("milk", "bread", "oranges", "pasta", "rice", "tea bags", "yoghurt", "eggs", "olive oil", "apples")
_GENRES = ("jazz", "classical", "folk", "acoustic", "ambient", "soul")
_ADJ = ("warm", "crisp", "quiet", "bright", "slow", "salty", "smoky", "mellow", "bold", "gentle", "plain", "spicy")
_THING = ("toast", "tea", "evenings", "porridge", "walks", "podcasts", "soup", "puzzles", "gardening", "cycling",
          "pancakes", "crosswords")


@dataclass(frozen=True)
class Needle:
    fact: str          # the sentence the owner teaches
    subject: str       # whose fact it is
    answer: str        # the token a correct recall must carry (exists nowhere else in the corpus)
    direct: str        # a direct question
    paraphrase: str    # the same question in other words (shares only the subject's name)


def corpus(seed: str, n: int = N_NEEDLES) -> "list[Needle]":
    """``n`` distinct needles for ``seed``: distinct subjects, distinct answers, shapes cycled."""
    if not 1 <= n <= N_NEEDLES:
        raise ValueError(f"n must be 1..{N_NEEDLES} (the rest of the name pool are the near-miss subjects)")
    rng = random.Random(f"zmb-d-needles:{seed}")
    subjects = rng.sample(_SUBJECTS, len(_SUBJECTS))
    answers = rng.sample(_ANSWERS, len(_ANSWERS))
    out = []
    for i in range(n):
        fact_t, direct_t, para_t = _SHAPES[i % len(_SHAPES)]
        s, a = subjects[i], answers[i]
        out.append(Needle(fact=fact_t.format(n=s, a=a), subject=s, answer=a,
                          direct=direct_t.format(n=s), paraphrase=para_t.format(n=s)))
    return out


def distractor_names(seed: str, n: int = N_NEEDLES) -> "list[str]":
    """The subjects NOT used by this seed's needles (a near-miss row is about one of these)."""
    used = {x.subject for x in corpus(seed, n)}
    return [s for s in _SUBJECTS if s not in used]


def chatter(seed: str, turns: int, salt: str = "") -> "list[dict]":
    """``turns`` filler turns: ``{"text", "speaker", "kind"}`` with kind ``chatter`` (never stored), ``preference``
    (an ordinary stored row) or ``near_miss`` (a needle-shaped fact about somebody else). Deterministic."""
    rng = random.Random(f"zmb-d-filler:{seed}:{salt}")
    near = distractor_names(seed)
    wrong = [a for a in _ANSWERS if a not in {x.answer for x in corpus(seed)}]
    out: list[dict] = []
    seen: set[str] = set()
    for i in range(turns):
        r = rng.random()
        if r < 0.55:
            text = rng.choice(_CHATTER).format(k=rng.randint(2, 45), m=rng.randint(2, 12), item=rng.choice(_ITEMS),
                                               genre=rng.choice(_GENRES))
            kind, speaker = "chatter", "owner_typed"
        elif r < 0.80:
            text = f"I like {rng.choice(_ADJ)} {rng.choice(_THING)}"
            kind, speaker = "preference", "owner_typed"
        else:
            shape = rng.choice(_SHAPES)[0]
            text = shape.format(n=rng.choice(near), a=rng.choice(wrong))
            kind, speaker = "near_miss", "owner_taught"
        if text in seen:                      # one idempotency key per distinct sentence: keep the filler varied
            text = f"{text} (number {i})" if kind != "near_miss" else shape.format(
                n=rng.choice(near), a=rng.choice(wrong)) + f" Note {i}."
        seen.add(text)
        out.append({"text": text, "speaker": speaker, "kind": kind})
    return out
