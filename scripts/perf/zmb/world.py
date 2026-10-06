"""The seeded synthetic household. Gold is GENERATED from it, never annotated by hand.

Every name, place, number and date a cell uses comes from ``World(seed)``: pools of invented names
(nobody real), drawn without replacement so two roles never share a name, and recorded in the artifact as
``corpus_seed``. The baseline uses ``BASELINE_SEED``; ``--seed fresh`` mints a held-out world of the same
shapes (the Goodhart guard: a fix that only works for the baseline's strings shows up as a gap between the
two runs). A scenario template names a slot (``{owner}``, ``{home}``, ``{dob_text}`` ...) and the world
fills it, so the SAME spec drives every seed and every arm.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any

BASELINE_SEED = "zmb-v1"

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")

# invented people - disjoint pools so a role guess from a first name is detectable
_FEMALE = ("Dana", "Tove", "Priya", "Marisol", "Anika", "Odile", "Ines", "Saoirse")
_MALE = ("Leo", "Ravi", "Teodor", "Percival", "Ignatius", "Barnaby", "Oskar", "Tomas")
_NEUTRAL = ("Mika", "Wren", "Sage", "Kit", "Ari", "Noor")
_PETS = ("Biscuit", "Pepper", "Waffles", "Juniper", "Clover", "Bramble")
_INTRUDERS = ("Dev", "Cato", "Zane", "Fenn")  # the speech-to-text fragment's "name"
_SURNAMES = ("Whitfield", "Nair", "Quillfeather", "Vale", "Okonkwo", "Lindqvist")
_HOMES = ("Hobart", "Dunedin", "Lisbon", "Perth", "Cork", "Bergen", "Tauranga", "Ghent")
_JOBS = ("the observatory", "a ferry company", "the botanic garden", "a bakery", "the harbour office",
         "a bookbinder")
_CLINICS = ("optometrist", "podiatrist", "physiotherapist")


def _names(rng: random.Random, pool: "tuple[str, ...]", n: int) -> list[str]:
    return rng.sample(pool, n)


@dataclass(frozen=True)
class World:
    seed: str
    slots: "dict[str, Any]"

    def render(self, text: str) -> str:
        """Fill ``{slot}`` placeholders. An unknown slot raises (a typo in a scenario must not ship a
        literal ``{owner}`` into a store)."""
        try:
            return text.format_map(_Strict(self.slots))
        except KeyError as exc:
            raise KeyError(f"scenario text names an unknown world slot {exc.args[0]!r}: {text[:60]!r}") from None

    def render_deep(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.render(value)
        if isinstance(value, list):
            return [self.render_deep(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self.render_deep(v) for v in value)
        if isinstance(value, dict):
            return {k: self.render_deep(v) for k, v in value.items()}
        return value

    def all_strings(self) -> list[str]:
        """Every household string the world minted (for the artifact's no-household-text check)."""
        generic = {"clinic"}  # an ordinary noun ('optometrist'), not a household string
        return sorted({str(v) for k, v in self.slots.items()
                       if k not in generic and isinstance(v, (str, int)) and len(str(v)) > 2})


class _Strict(dict):
    def __missing__(self, key):
        raise KeyError(key)


def make_world(seed: "str | int" = BASELINE_SEED) -> World:
    """The household for ``seed`` (same seed, same world, byte for byte)."""
    rng = random.Random(f"zmb:{seed}")
    f = _names(rng, _FEMALE, 4)
    m = _names(rng, _MALE, 3)
    n = _names(rng, _NEUTRAL, 3)
    owner, spouse, friend, sibling = f
    spouse_intruder = m[0]
    kid1, kid2, kid3 = n
    pet, pet_intruder = _names(rng, _PETS, 2)
    intruder = rng.choice(_INTRUDERS)
    homes = _names(rng, _HOMES, 4)
    jobs = _names(rng, _JOBS, 2)
    # a numeric date where day and month are both <= 12 and DIFFERENT: it reads two ways, and the
    # household order (day first) is the only right one
    while True:
        d, mo = rng.randint(2, 12), rng.randint(1, 12)
        if d != mo:
            break
    y = rng.randint(1962, 2004)
    d2, mo2 = (d % 12) + 1, (mo % 12) + 1
    age = rng.randint(31, 58)
    slots: dict[str, Any] = {
        "owner": owner, "spouse": spouse, "friend": friend, "sibling": sibling,
        "male": m[1], "male2": m[2],
        "spouse_intruder": spouse_intruder,
        "kid1": kid1, "kid2": kid2, "kid3": kid3, "pet": pet, "pet_intruder": pet_intruder,
        "intruder": intruder, "surname": rng.choice(_SURNAMES),
        "home": homes[0], "home_intruder": homes[1], "home_old": homes[2], "lima": homes[3],
        "job": jobs[0], "job_intruder": jobs[1],
        "age": age, "age_intruder": age + rng.randint(3, 9),
        "dob_d": d, "dob_m": mo, "dob_y": y,
        "dob_text": f"{d} {_MONTHS[mo - 1]} {y}",           # the correct, day-first reading
        "dob_flip": f"{_MONTHS[d - 1]} {mo}",               # what a month-first reader says ("July 8")
        "dob_numeric": f"{d}/{mo}/{y}",
        "dob_intruder": f"{d2} {_MONTHS[mo2 - 1]} {y}",
        "clinic": rng.choice(_CLINICS), "clinic_name": f"Dr {rng.choice(_SURNAMES)}",
        "canary": f"zorbl-{rng.randint(10, 99)}",
    }
    slots["kids"] = f"{kid1} and {kid2}"
    # drawn AFTER every slot above, so adding one never shifts a name an existing cell already uses
    appt_day, appt_mo = rng.randint(2, 28), rng.randint(1, 12)
    slots["appt_date"] = f"{appt_day} {_MONTHS[appt_mo - 1]}"      # "when is my dentist appointment" (C3)
    slots["since_year"] = rng.randint(2011, 2021)                   # "I have lived here since 2019" (C4)
    canary2 = slots["canary"]
    while canary2 == slots["canary"]:
        canary2 = f"zorbl-{rng.randint(10, 99)}"
    slots["canary2"] = canary2                                      # a second planted token (poisoning I1b / I4)
    return World(seed=str(seed), slots=slots)


def pool_strings() -> list[str]:
    """Every invented name / place / employer a world can draw from (the public pools, not one world's pick):
    spec titles, ids and skip reasons must never name one, or an artifact would carry household-shaped text."""
    pools = (_FEMALE, _MALE, _NEUTRAL, _PETS, _INTRUDERS, _SURNAMES, _HOMES, _JOBS)
    return sorted({x for pool in pools for x in pool})


def fresh_seed() -> str:
    """A held-out seed (recorded in the artifact so a surprising run can be replayed)."""
    import secrets
    return "fresh-" + secrets.token_hex(4)


_PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")


def placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER.findall(text))


def month_name(n: int) -> str:
    return _MONTHS[n - 1]
