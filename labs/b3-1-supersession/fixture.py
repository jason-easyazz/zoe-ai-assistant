"""50-pair reconciliation fixture — SYNTHETIC facts only.

Every subject is "Person A/B/C" or "user"; every employer, town, pet and
relative is invented. Nothing here is household data.

Each case: the NEW fact, its top-k NEIGHBOURS (integer ids, optional
validity windows), the EXPECTED decision, and a category tag. ``expect_live``
optionally pins what must remain currently-valid after ``apply``.

Categories (counts):
  correction        10  same attribute, different value, overlapping windows → SUPERSEDE
  paraphrase        10  same attribute, same value, other phrasing → NONE
  richer            8   4× candidate richer → UPDATE, 4× existing richer → NONE
  historical        10  same attribute, different value, DISJOINT windows → ADD
  transition        7   "switched from X to Y" → SUPERSEDE X (or ADD both, closed + open)
  unrelated         5   different attribute → ADD
"""
from __future__ import annotations

CASES: list[dict] = [
    # ── correction (10) ──────────────────────────────────────────────────────
    {"cat": "correction", "new": "Person A works at Globex now",
     "nb": [(1, "person a works at acme corporation", None, None)],
     "expect": ("SUPERSEDE", 1), "expect_live": ["person a works at globex now"]},
    {"cat": "correction", "new": "User is employed by Initech",
     "nb": [(1, "user works at acme", None, None), (2, "user has a dog named rex", None, None)],
     "expect": ("SUPERSEDE", 1)},
    {"cat": "correction", "new": "Person B lives in Northport",
     "nb": [(1, "person b lives in Southbay", None, None)],
     "expect": ("SUPERSEDE", 1)},
    {"cat": "correction", "new": "Person A's father's name is Tom",
     "nb": [(1, "person a's father's name is neil", None, None)],
     "expect": ("SUPERSEDE", 1)},
    {"cat": "correction", "new": "Person A's dad's name is Joe",
     "nb": [(1, "person a's dad's name is jo", None, None)],
     "expect": ("SUPERSEDE", 1)},
    {"cat": "correction", "new": "User drives a blue Wexford hatchback",
     "nb": [(1, "user drives a blue Tarrant sedan", None, None)],
     "expect": ("SUPERSEDE", 1)},
    {"cat": "correction", "new": "Person C's favourite colour is green",
     "nb": [(1, "person c's favourite colour is red", None, None)],
     "expect": ("SUPERSEDE", 1)},
    {"cat": "correction", "new": "User's phone number is 0400 111 222",
     "nb": [(1, "user's phone number is 0400 999 888", None, None)],
     "expect": ("SUPERSEDE", 1)},
    {"cat": "correction", "new": "Person A's dog is called Max",
     "nb": [(1, "person a has a dog named rex", None, None)],
     "expect": ("SUPERSEDE", 1)},
    {"cat": "correction", "new": "Person B works for Vandelay Industries",
     "nb": [(1, "person b is employed by Kruger Logistics", "2024-01-01", None),
            (2, "person b lives in Southbay", None, None)],
     "expect": ("SUPERSEDE", 1)},

    # ── paraphrase duplicates (10) → NONE ────────────────────────────────────
    {"cat": "paraphrase", "new": "Person A is employed by Globex",
     "nb": [(1, "person a works at globex", None, None)], "expect": ("NONE", 1)},
    {"cat": "paraphrase", "new": "User's father is called Neil",
     "nb": [(1, "user's dad's name is neil", None, None)], "expect": ("NONE", 1)},
    {"cat": "paraphrase", "new": "Person A has a father named Neil",
     "nb": [(1, "person a's father's name is neil", None, None)], "expect": ("NONE", 1)},
    {"cat": "paraphrase", "new": "User has a dog called Rex",
     "nb": [(1, "user's dog's name is rex", None, None)], "expect": ("NONE", 1)},
    {"cat": "paraphrase", "new": "Person B is living in Southbay",
     "nb": [(1, "person b lives in southbay", None, None)], "expect": ("NONE", 1)},
    {"cat": "paraphrase", "new": "Person A's employer is Globex",
     "nb": [(1, "person a works at globex", None, None), (2, "person a lives in northport", None, None)],
     "expect": ("NONE", 1)},
    {"cat": "paraphrase", "new": "Person C's job is at Initech",
     "nb": [(1, "person c works for initech", None, None)], "expect": ("NONE", 1)},
    {"cat": "paraphrase", "new": "User works at Globex",
     "nb": [(1, "user works at globex", None, None)], "expect": ("NONE", 1)},
    # judge-only paraphrases: the new text has NO recognised framing
    {"cat": "paraphrase", "new": "Neil, Person A's dad, says hi",
     "nb": [(1, "person a's father's name is neil", None, None)], "expect": ("NONE", 1)},
    {"cat": "paraphrase", "new": "Rex the dog belongs to Person A",
     "nb": [(1, "person a has a dog named rex", None, None)], "expect": ("NONE", 1)},

    # ── richer vs distilled (8) ──────────────────────────────────────────────
    {"cat": "richer", "new": "User's dad's name is Neil, spelled N-E-I-L",
     "nb": [(1, "user's dad's name is neil", None, None)], "expect": ("UPDATE", 1)},
    {"cat": "richer", "new": "Person A works at Globex as a senior mechanical engineer",
     "nb": [(1, "person a works at globex", None, None)], "expect": ("UPDATE", 1)},
    {"cat": "richer", "new": "Person B lives in Southbay on Harbour Street near the marina",
     "nb": [(1, "person b lives in southbay", None, None)], "expect": ("UPDATE", 1)},
    {"cat": "richer", "new": "User has a dog named Rex, a three-year-old kelpie cross",
     "nb": [(1, "user has a dog named rex", None, None)], "expect": ("UPDATE", 1)},
    # existing richer → the distilled newcomer must NOT win
    {"cat": "richer", "new": "User's father's name is Neil",
     "nb": [(1, "user's dad's name is neil, spelled n-e-i-l", None, None)], "expect": ("NONE", 1)},
    {"cat": "richer", "new": "Person A works at Globex",
     "nb": [(1, "person a works at globex as a senior mechanical engineer", None, None)],
     "expect": ("NONE", 1)},
    {"cat": "richer", "new": "Person B lives in Southbay",
     "nb": [(1, "person b lives in southbay on harbour street near the marina", None, None)],
     "expect": ("NONE", 1)},
    {"cat": "richer", "new": "User has a dog named Rex",
     "nb": [(1, "user has a dog named rex, a three-year-old kelpie cross", None, None)],
     "expect": ("NONE", 1)},

    # ── historical, disjoint windows (10) → ADD, never SUPERSEDE ─────────────
    {"cat": "historical", "new": "Person A works at Globex", "new_from": "2024-03-01",
     "nb": [(1, "person a works at acme corporation", "2018-01-01", "2021-06-30")],
     "expect": ("ADD", None), "expect_live_count": 1},
    {"cat": "historical", "new": "Person A works at Acme Corporation",
     "new_from": "2018-01-01", "new_until": "2021-06-30",
     "nb": [(1, "person a works at globex", "2024-03-01", None)],
     "expect": ("ADD", None), "expect_live": ["person a works at globex"]},
    {"cat": "historical", "new": "Person B lives in Northport", "new_from": "2025-01-01",
     "nb": [(1, "person b lives in southbay", "2010-01-01", "2019-12-31")],
     "expect": ("ADD", None)},
    {"cat": "historical", "new": "Person B lives in Southbay",
     "new_from": "2010-01-01", "new_until": "2019-12-31",
     "nb": [(1, "person b lives in northport", "2025-01-01", None)],
     "expect": ("ADD", None), "expect_live": ["person b lives in northport"]},
    {"cat": "historical", "new": "User drives a red Tarrant sedan", "new_from": "2026-01-01",
     "nb": [(1, "user drives a green Wexford hatchback", "2015-01-01", "2020-01-01")],
     "expect": ("ADD", None)},
    {"cat": "historical", "new": "User works at Initech", "new_from": "2026-02-01",
     "nb": [(1, "user works at acme", "2019-01-01", "2022-01-01"),
            (2, "user works at globex", "2022-01-01", "2026-01-31")],
     "expect": ("ADD", None), "expect_live_count": 1},
    {"cat": "historical", "new": "Person C studies at Northport University", "new_from": "2026-01-01",
     "nb": [(1, "person c studies at southbay college", "2016-01-01", "2019-12-31")],
     "expect": ("ADD", None)},
    {"cat": "historical", "new": "Person A's favourite colour is blue", "new_from": "2026-01-01",
     "nb": [(1, "person a's favourite colour is red", "2000-01-01", "2010-01-01")],
     "expect": ("ADD", None)},
    {"cat": "historical", "new": "Person B works at Kruger Logistics",
     "new_from": "2012-01-01", "new_until": "2016-12-31",
     "nb": [(1, "person b works at vandelay industries", "2017-01-01", None)],
     "expect": ("ADD", None), "expect_live": ["person b works at vandelay industries"]},
    # boundary: old window ends exactly when the new one starts → history, not contradiction
    {"cat": "historical", "new": "Person A lives in Northport", "new_from": "2021-07-01",
     "nb": [(1, "person a lives in southbay", "2015-01-01", "2021-07-01")],
     "expect": ("ADD", None)},

    # ── transitions (7) ──────────────────────────────────────────────────────
    {"cat": "transition", "new": "Person A switched from Acme to Globex",
     "nb": [(1, "person a works at acme", None, None)],
     "expect": ("SUPERSEDE", 1), "expect_live": ["person a works at globex"]},
    {"cat": "transition", "new": "User moved jobs from Initech to Vandelay Industries",
     "nb": [(1, "user works at initech", "2020-01-01", None), (2, "user lives in northport", None, None)],
     "expect": ("SUPERSEDE", 1), "expect_live": ["user works at vandelay industries", "user lives in northport"]},
    {"cat": "transition", "new": "Person B changed from Kruger Logistics to Initech in 2025-06",
     "nb": [(1, "person b works at kruger logistics", "2019-01-01", None)],
     "expect": ("SUPERSEDE", 1)},
    {"cat": "transition", "new": "Person C switched from Acme to Globex",
     "nb": [(1, "person c has a cat named Tabby", None, None)],
     "expect": ("ADD", None), "expect_rows": 3, "expect_live": ["person c has a cat named tabby", "person c works at globex"]},
    {"cat": "transition", "new": "User switched from Acme to Initech",
     "nb": [], "expect": ("ADD", None), "expect_rows": 2, "expect_live": ["user works at initech"]},
    {"cat": "transition", "new": "Person A moved from Southbay Town to Northport Town",
     "nb": [(1, "person a lives in southbay town", None, None)],
     "expect": ("SUPERSEDE", 1), "expect_live": ["person a lives in northport town"]},
    {"cat": "transition", "new": "Person B went from Initech to Globex",
     "nb": [(1, "person b works at initech", None, None), (2, "person b's dog is called Max", None, None)],
     "expect": ("SUPERSEDE", 1)},

    # ── unrelated (5) → ADD ──────────────────────────────────────────────────
    {"cat": "unrelated", "new": "Person A has a cat named Tabby",
     "nb": [(1, "person a has a dog named rex", None, None)], "expect": ("ADD", None)},
    {"cat": "unrelated", "new": "User's mother's name is Anne",
     "nb": [(1, "user's father's name is neil", None, None)], "expect": ("ADD", None)},
    {"cat": "unrelated", "new": "Person B is allergic to peanuts",
     "nb": [(1, "person b works at initech", None, None), (2, "person b lives in southbay", None, None)],
     "expect": ("ADD", None)},
    {"cat": "unrelated", "new": "Person C's birthday is 14 March",
     "nb": [(1, "person c's favourite colour is red", None, None)], "expect": ("ADD", None)},
    {"cat": "unrelated", "new": "User lives in Northport",
     "nb": [(1, "user works at globex", None, None)], "expect": ("ADD", None)},
]

assert len(CASES) == 50, len(CASES)
