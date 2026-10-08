"""The labelled set DRIVES the structural floors (docs/research/structural-floors-2026-10-09.md section 6.1).

``fixtures/structural_floors_labelled_set.json`` (337 rows: the ledger phrasings of the #1912/#1913/#1916 review rounds, held-out English,
and es / fr / de / zh / ja translations) + ``fixtures/structural_floors_claims.json`` (the claim row an honest extractor emits for each
sentence, and the id triples each role row means) run through the REAL functions, per language:

* support rows -> ``structural_claims.decide`` (claim row + owner turn + fact) beside the lexical stack (``memory_authority.supports`` /
  ``entailing_span``); role rows -> ``role_triples.TripleSet.supports`` and, for English, the real reply reader
  (``role_guess_guard.neutralise`` with ``ZOE_STRUCTURAL_CLAIMS=enforce``).
* every row is asserted individually (the expectation is the fixture label unless the claims fixture records a reasoned override or this file
  lists a measured, fail-closed MISS); the per-language summaries are asserted as bars.
* WHAT THIS MEASURES: the claim rows are author-written ORACLE readings, so these numbers are the floor's logic given a correct reading of the
  sentence - NOT the extractor's accuracy (that is measured live, in shadow, from the ``STRUCTURAL_FLOOR`` log lines). The sloppy-extractor
  test below is the part that does not lean on the oracle: it feeds each negative row a careless claim and counts what still leaks.
* RED WHEN REMOVED: the controls take one check out at a time (``CONTROLS``); the meta-tests prove every row KIND is pinned by a control that
  turns at least one of its rows red, and that no control is dead. Rows moved out of code and into data keep the guarantee the old
  parametrized tables gave.

Synthetic names only. Run it for the numbers: ``pytest -s test_structural_floors_labelled.py -k summary``.
"""
from __future__ import annotations

import collections
import contextlib
import dataclasses
import json
from pathlib import Path

import pytest

import lexicons
import memory_authority as ma
import role_guess_guard as rg
import role_triples as rt
import structural_claims as sc

pytestmark = pytest.mark.ci_safe

FIXDIR = Path(__file__).parent / "fixtures"
ITEMS = json.loads((FIXDIR / "structural_floors_labelled_set.json").read_text(encoding="utf-8"))["items"]
CLAIMS = json.loads((FIXDIR / "structural_floors_claims.json").read_text(encoding="utf-8"))
SUPPORT = [i for i in ITEMS if i["task"] == "support"]
ROLE = [i for i in ITEMS if i["task"] == "role"]

#: rows where the FIXTURE LABEL is an artefact the structural reading rightly disagrees with (recorded in the claims fixture)
OVERRIDES = {k: v["expect"] == "promote" for k, v in CLAIMS["meta"]["overrides"].items()}
#: measured, FAIL-CLOSED misses: the owner did say it, the structural floor holds it (the row stays model_from_turn, as it does today).
#: These are the residue the off-path verifier exists for; each is stated so a change in either direction is noticed.
KNOWN_MISSES = {
    "sup-130": "'I've kicked the habit, no more cigarettes' - the value the owner used (cigarettes) is not in the fact's wording (smokes)",
    "sup-141": "'the seventh of August' vs '7 August' - no number-word reading; the number in the fact is not in the quote",
}


def _expected(item: dict) -> bool:
    if item["id"] in OVERRIDES:
        return OVERRIDES[item["id"]]
    if item["id"] in KNOWN_MISSES:
        return False
    return bool(item["label"])


def _row_claims(item: dict) -> list:
    out = []
    for raw in CLAIMS["support"][item["id"]]["claims"]:
        r = dict(raw)
        r["quote"] = r.get("quote") or item["said"]
        claim, why = sc.parse_claim(r)
        assert claim is not None, (item["id"], why)
        out.append(claim)
    return out


def structural(item: dict) -> sc.Decision:
    claims = _row_claims(item)
    own, sibs = sc.pick_claim_for_fact(item["fact"], claims)
    if own is None:
        own, sibs = claims[0], tuple(claims[1:])
    return sc.decide(item["fact"], own, item["said"], siblings=sibs)


def structural_pred(item: dict) -> bool:
    d = structural(item)
    return d.anchored if item["kind"] == "anchoring" else d.promoted


def lexical_pred(item: dict) -> bool:
    if item["kind"] == "anchoring":
        return ma.supports(item["fact"], item["said"])
    return ma.entailing_span(item["fact"], item["said"]) is not None


def _group(item: dict) -> str:
    return item["lang"] if item["split"] == "xling" else item["split"]


# ── role rows ─────────────────────────────────────────────────────────────────────────────────────────────

def _triples(item: dict) -> rt.TripleSet:
    spec = CLAIMS["role"][item["id"]]
    ts = rt.TripleSet(names=dict(spec["people"]))
    ts.triples = [rt.Triple(p, k, o) for p, k, o in spec["triples"]]
    return ts


def _owner(owner):
    if owner == "user":
        return "user"
    if "name" in owner:
        return ("name", tuple(t.casefold() for t in owner["name"].split()))
    return ("rel", tuple(owner["rel"]))


def role_structural_guess(item: dict) -> bool:
    """Is the reply's claim an unsupported GUESS per the id triples? (the claim as READ; a loose label is not policed)"""
    spec = CLAIMS["role"][item["id"]]
    claim = spec["claim"]
    if not claim or claim.get("negated") or lexicons.loose_code(lexicons.kin_code(claim["kin"]) or ""):
        return False
    ts = _triples(item)
    pid = next((p for p, n in ts.names.items() if n == claim["person"]), "")
    return not ts.supports(pid, claim["kin"], _owner(claim["owner"]))


def role_english_reader_guess(item: dict, monkeypatch) -> bool:
    """The real reply reader + the triple floor in enforce: does ``neutralise`` rewrite the reply?"""
    monkeypatch.setenv("ZOE_STRUCTURAL_CLAIMS", "enforce")
    ts = _triples(item)
    names = [n for n in ts.names.values() if n == "Anika Reyes"]
    ids = {n: p for p, n in ts.names.items()}
    return bool(rg.neutralise(item["reply"], names, "", item["user_text"], triples=ts, ids=ids)[1])


#: the reply READER (the unsolved half of the role decision, E4) does not read these phrasings at all today, so the reply passes: measured
#: misses of the existing English reader, pinned so closing one is a visible change (flip the expectation when it is closed)
ROLE_READER_GAPS = {
    "rol-293": "'Anika Reyes is, I believe, your aunt' - a parenthetical hedge between the copula and the role",
    "rol-294": "'Anika Reyes? That would be your daughter' - an elliptical answer",
    "rol-296": "'..., is family' - a vague family claim names no role word",
    "rol-301": "'Anika Reyes is the one who raised you' - a periphrasis names no role word",
}

#: the one divergence by design: the owner's SAME-TURN statement is not an id triple (the extractor writes it after the turn)
ROLE_SAME_TURN = {"rol-277": "the owner said 'Anika Reyes is my mother' in the very turn being answered: evidence only once stored"}


def _role_expected(item: dict) -> bool:
    return True if item["id"] in ROLE_SAME_TURN else bool(item["label"])


def _reader_expected(item: dict) -> bool:
    return False if item["id"] in ROLE_READER_GAPS else _role_expected(item)


# ── the rows, one by one ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("item", SUPPORT, ids=lambda i: f"{i['id']}-{_group(i)}-{i['kind']}")
def test_support_row(item):
    got = structural_pred(item)
    assert got == _expected(item), (item["fact"], item["said"], structural(item))


@pytest.mark.parametrize("item", ROLE, ids=lambda i: f"{i['id']}-{_group(i)}-{i['kind']}")
def test_role_row_triple_floor(item):
    assert role_structural_guess(item) == _role_expected(item), (item["reply"], CLAIMS["role"][item["id"]])


@pytest.mark.parametrize("item", [i for i in ROLE if i["lang"] == "en"], ids=lambda i: f"{i['id']}-{i['kind']}")
def test_role_row_english_reader_with_triples_enforced(item, monkeypatch):
    assert role_english_reader_guess(item, monkeypatch) == _reader_expected(item), (item["reply"], item["user_text"])


# ── per-language summaries (the numbers of the PR body) ───────────────────────────────────────────────────

def _confusion(rows, pred_fn) -> dict:
    c = collections.Counter()
    for it in rows:
        want, got = bool(it["label"]), pred_fn(it)
        c["n"] += 1
        c["tp"] += want and got
        c["tn"] += (not want) and (not got)
        c["fp"] += (not want) and got
        c["fn"] += want and (not got)
    pos, neg = c["tp"] + c["fn"], c["tn"] + c["fp"]
    c["ba"] = round(((c["tp"] / pos if pos else 1.0) + (c["tn"] / neg if neg else 1.0)) / 2, 3)
    return dict(c)


def summary() -> dict:
    out = {}
    for g in ("ledger", "heldout_en", "en", "es", "fr", "de", "zh", "ja"):
        rows = [i for i in SUPPORT if _group(i) == g]
        agree = sum(structural_pred(i) == lexical_pred(i) for i in rows)
        out[g] = {"n": len(rows), "lexical": _confusion(rows, lexical_pred), "structural": _confusion(rows, structural_pred),
                  "agree_with_lexical": round(agree / len(rows), 3)}
    return out


def test_summary_per_language_and_bars(capsys):
    s = summary()
    with capsys.disabled():
        print("\ngroup        n  lexical(ba fp fn)   structural(ba fp fn)   agreement")
        for g, v in s.items():
            lx, st = v["lexical"], v["structural"]
            print(f"{g:<11} {v['n']:>3}  {lx['ba']:.2f} {lx['fp']:>2} {lx['fn']:>2}          {st['ba']:.2f} {st['fp']:>2} {st['fn']:>2}"
                  f"           {v['agree_with_lexical']:.2f}")
    for g, v in s.items():
        st, lx = v["structural"], v["lexical"]
        assert st["fp"] <= {"ledger": 1}.get(g, 0), (g, st)          # the one ledger FP is the fixture artefact sup-071
        if g in ("es", "fr", "de", "zh", "ja"):
            assert st["fn"] == 0 and st["ba"] == 1.0, (g, st)
            assert lx["tp"] == 0, (g, lx)                              # the lexical floor never fires outside English
        else:
            assert st["ba"] >= 0.94, (g, st)          # held-out English carries the two stated fail-closed misses (KNOWN_MISSES)
    assert s["heldout_en"]["structural"]["ba"] > s["heldout_en"]["lexical"]["ba"] + 0.2


def test_shadow_agreement_with_the_lexical_floor_on_the_tuned_phrasings():
    """Shadow changes nothing, so it must be quiet where the lexical floor is right: on the phrasings it was tuned on the two decisions
    agree on at least 95% of rows (the disagreements are the artefact row and the rows the lexical floor gets wrong)."""
    s = summary()
    assert s["ledger"]["agree_with_lexical"] >= 0.95, s["ledger"]
    assert s["en"]["agree_with_lexical"] == 1.0


def test_enabled_languages_are_exactly_those_whose_fixture_passes():
    """CI rule from the research (E5): a language is ``enabled`` in its lexicon file only while its fixture rows pass."""
    s = summary()
    for lang in lexicons.LANGS:
        st = s["en" if lang == "en" else lang]["structural"]
        passes = st["fp"] == 0 and st["fn"] == 0 and st["ba"] == 1.0
        if lang == "en":
            passes = st["fp"] == 0 and st["ba"] >= 0.94
        assert lexicons.enabled(lang) == passes, (lang, st)


# ── the sloppy extractor: what the cross-checks catch WITHOUT leaning on the oracle ───────────────────────

def _sloppy(item: dict) -> sc.Claim:
    """A careless extractor: it copies the fact (the speaker as subject, polarity from the fact's wording, plain, current) and quotes the
    sentence whole. The oracle's VALUE is kept (a careless reading still finds the place or the name)."""
    oracle = _row_claims(item)
    own, _ = sc.pick_claim_for_fact(item["fact"], oracle)
    own = own or oracle[0]
    flang = lexicons.detect(item["fact"])
    not_hold = sc.text_polarity(item["fact"], flang).cls == sc.NOT_HOLD
    subj = own.subj if own.subj.startswith("rel:") and lexicons.kin_in(item["fact"], own.subj[4:], flang) else "user"
    return sc.Claim(subj, own.pred, own.obj, "ended" if not_hold else "affirm", "asserted", "current",
                    item["said"].rstrip(".?？。"), "")


#: measured false promotions of a sloppy claim row on the 150 negative rows, per group (an upper bound: improving it is fine)
SLOPPY_LEAK_BOUND = {"ledger": 5, "heldout_en": 4, "en": 1, "es": 3, "fr": 2, "de": 2, "zh": 2, "ja": 2}


def test_sloppy_extractor_leak_counts_are_bounded_and_the_prefilters_do_catch():
    leaks, total = collections.Counter(), collections.Counter()
    for it in SUPPORT:
        if it["label"]:
            continue
        g = _group(it)
        total[g] += 1
        d = sc.decide(it["fact"], _sloppy(it), it["said"])
        leaks[g] += int(d.anchored if it["kind"] == "anchoring" else d.promoted)
    for g, bound in SLOPPY_LEAK_BOUND.items():
        assert leaks[g] <= bound, (g, leaks[g], bound)
    # the pre-filters are doing work: a sloppy claim row would otherwise be promoted wherever the quote contains the value
    assert sum(leaks.values()) < sum(total.values()) / 3, (leaks, total)


# ── RED WHEN REMOVED: controls and the meta-tests ────────────────────────────────────────────────────────

@contextlib.contextmanager
def _without(monkeypatch, *fns):
    monkeypatch.setattr(sc, "_BLOCK_CHECKS", [f for f in sc._BLOCK_CHECKS if f not in fns])
    monkeypatch.setattr(sc, "_HOLD_CHECKS", [f for f in sc._HOLD_CHECKS if f not in fns])
    yield


def _control_no_quote_check(mp): return _without(mp, sc._check_quote)
def _control_no_value_check(mp): return _without(mp, sc._check_value)
def _control_no_modality_check(mp): return _without(mp, sc._check_modality, sc._hold_plain, sc._hold_prefilters)
def _control_no_subject_check(mp): return _without(mp, sc._check_subject)
def _control_no_polarity_check(mp): return _without(mp, sc._check_polarity)
def _control_no_tense_check(mp): return _without(mp, sc._check_tense_supported, sc._hold_tense)


@contextlib.contextmanager
def _control_hold_all(mp):
    """A floor that never promotes: proves a row kind can actually be promoted (the positive control for all-True kinds)."""
    mp.setattr(sc, "_BLOCK_CHECKS", sc._BLOCK_CHECKS + [lambda c, out, amb: out.append("hold_all")])
    yield


@contextlib.contextmanager
def _control_no_sibling_accounting(mp):
    orig = sc.text_polarity
    mp.setattr(sc, "text_polarity", lambda text, lang, obj="", siblings=(): orig(text, lang, obj, ()))
    yield


@contextlib.contextmanager
def _control_no_unaccounted_negation_hold(mp):
    orig = sc.text_polarity
    mp.setattr(sc, "text_polarity", lambda text, lang, obj="", siblings=(): dataclasses.replace(orig(text, lang, obj, siblings), flags=()))
    yield


@contextlib.contextmanager
def _control_exact_values_only(mp):
    """Take the edit-distance / inflection tolerance out: a value must be a literal substring."""
    mp.setattr(sc, "value_in", lambda value, text: sc.norm(sc._edge_strip(value)) in sc.norm(text))
    yield


@contextlib.contextmanager
def _control_no_date_normalised_quotes(mp):
    mp.setattr(sc, "quote_basis", lambda quote, turn: "raw" if sc.norm(sc._edge_strip(quote)) in sc.norm(turn) else "")
    yield


CONTROLS = {
    "no_quote_check": _control_no_quote_check, "no_value_check": _control_no_value_check,
    "no_modality_check": _control_no_modality_check, "no_subject_check": _control_no_subject_check,
    "no_polarity_check": _control_no_polarity_check, "no_tense_check": _control_no_tense_check,
    "hold_all": _control_hold_all, "no_sibling_accounting": _control_no_sibling_accounting,
    "no_unaccounted_negation_hold": _control_no_unaccounted_negation_hold, "exact_values_only": _control_exact_values_only,
    "no_date_normalised_quotes": _control_no_date_normalised_quotes,
}


def invented_quote_pred(item: dict) -> bool:
    """The claim row's quote is the extractor's PARAPHRASE (the fact), not the owner's words: it must never anchor anything."""
    claims = _row_claims(item)
    own, sibs = sc.pick_claim_for_fact(item["fact"], claims)
    own = own or claims[0]
    fake = dataclasses.replace(own, quote=item["fact"])
    d = sc.decide(item["fact"], fake, item["said"], siblings=sibs)
    return d.anchored if item["kind"] == "anchoring" else d.promoted


#: a derived row per fixture row whose fact is not a substring of the owner's words: same sentence, the quote swapped for the fact
INVENTED = [i for i in SUPPORT if sc.norm(i["fact"]) not in sc.norm(i["said"])]


def test_an_invented_quote_is_never_anchored_or_promoted():
    assert INVENTED and all(not invented_quote_pred(i) for i in INVENTED)


def _red_rows(control: str, monkeypatch=None) -> set:
    with pytest.MonkeyPatch.context() as mp, CONTROLS[control](mp):          # a fresh patch set per control: controls never stack
        red = {i["id"] for i in SUPPORT if structural_pred(i) != _expected(i)}
        red |= {i["id"] + "~quote" for i in INVENTED if invented_quote_pred(i)}
        return red


#: row KIND -> the control that must turn at least one of its rows red (the check that pins that kind). Declared by hand; the test proves it.
KIND_CONTROL = {
    "anchoring": "no_subject_check", "change_of_mind": "no_subject_check", "contrast_or_denial": "no_polarity_check",
    "descriptive_denial": "no_unaccounted_negation_hold", "lead_in_label": "no_subject_check", "other": "no_polarity_check",
    "retraction_or_end_state": "no_polarity_check", "contrast": "no_sibling_accounting", "contrast_like": "hold_all",
    "denial": "no_polarity_check", "future": "no_tense_check", "hedge": "no_modality_check", "hypothetical": "no_modality_check",
    "paraphrase": "hold_all", "question": "no_modality_check", "retraction": "exact_values_only",
    "retraction_hypothetical": "no_modality_check", "retraction_negated": "no_polarity_check", "temporal_denial": "no_polarity_check",
    "third_party": "no_subject_check", "plain": "hold_all", "tense": "no_tense_check", "retraction_live": "no_polarity_check",
    "invented_quote": "no_quote_check",
}


def test_every_support_row_kind_has_a_control_that_turns_it_red(monkeypatch):
    kinds = {i["kind"] for i in SUPPORT} | {"invented_quote"}
    assert kinds <= set(KIND_CONTROL), sorted(kinds - set(KIND_CONTROL))
    red_by_control = {c: _red_rows(c, monkeypatch) for c in set(KIND_CONTROL.values())}
    unpinned = []
    for kind in sorted(kinds):
        ids = {i["id"] for i in SUPPORT if i["kind"] == kind} if kind != "invented_quote" else {i["id"] + "~quote" for i in INVENTED}
        if not (red_by_control[KIND_CONTROL[kind]] & ids):
            unpinned.append((kind, KIND_CONTROL[kind]))
    assert unpinned == [], f"these row kinds are not pinned by their declared control: {unpinned}"


def test_no_support_control_is_dead(monkeypatch):
    dead = [c for c in CONTROLS if not _red_rows(c, monkeypatch)]
    assert dead == [], f"controls that turn no row red (the check they remove is not pinned): {dead}"


def test_controls_turn_the_incident_rows_red(monkeypatch):
    """The two false promotions the research measured on the lexical stack stay held; take the check out and they promote."""
    ids = {"mum_moving": "sup-117", "not_in_the_town": "sup-118", "third_party_nurse": "sup-138"}
    assert not structural_pred(next(i for i in SUPPORT if i["id"] == ids["mum_moving"]))
    assert not structural_pred(next(i for i in SUPPORT if i["id"] == ids["not_in_the_town"]))
    assert sc_red(monkeypatch, "no_tense_check", ids["mum_moving"])
    assert sc_red(monkeypatch, "no_unaccounted_negation_hold", ids["not_in_the_town"])
    assert sc_red(monkeypatch, "no_subject_check", ids["third_party_nurse"])


def sc_red(monkeypatch, control: str, row_id: str) -> bool:
    return row_id in _red_rows(control, monkeypatch)


# role controls ------------------------------------------------------------------------------------------

@contextlib.contextmanager
def _role_accept_all(mp):
    mp.setattr(rt.TripleSet, "supports", lambda self, *a, **k: True)
    yield


@contextlib.contextmanager
def _role_ignore_owner(mp):
    orig = rt.TripleSet.supports
    mp.setattr(rt.TripleSet, "supports", lambda self, pid, kin, owner="user": orig(self, pid, kin, None))
    yield


@contextlib.contextmanager
def _role_by_first_name(mp):
    """Match a person by FIRST name only (what ``people_roles`` did): a namesake's triple licenses the claim."""
    orig = rt.TripleSet.supports

    def supports(self, pid, kin, owner="user"):
        first = self.names.get(pid, "").split()[0:1]
        return any(orig(self, other, kin, owner) for other, n in self.names.items() if n.split()[0:1] == first)

    mp.setattr(rt.TripleSet, "supports", supports)
    yield


@contextlib.contextmanager
def _role_deny_all(mp):
    mp.setattr(rt.TripleSet, "supports", lambda self, *a, **k: False)
    yield


@contextlib.contextmanager
def _role_ignore_specificity(mp):
    """Let a gender-neutral edge license a gendered claim (a 'spouse' edge says 'wife')."""
    mp.setattr(rt, "implies", lambda have, want: True if {have, want} <= {"spouse", "wife", "husband", "partner"} or have == want else False)
    yield


ROLE_CONTROLS = {"accept_all": _role_accept_all, "ignore_owner": _role_ignore_owner, "by_first_name": _role_by_first_name,
                 "deny_all": _role_deny_all}
ROLE_KIND_CONTROL = {
    "basic": "accept_all", "apposition": "accept_all", "role_first": "accept_all", "hedged": "accept_all", "pronoun": "accept_all",
    "other_owner_unsupported": "accept_all", "owner_mismatch": "ignore_owner", "other_role": "accept_all", "namesake": "by_first_name",
    "question_not_evidence": "accept_all", "nested_owner": "ignore_owner", "elliptical": "accept_all", "false_attribution": "accept_all",
    "vague_family": "accept_all", "hedged_third_owner": "accept_all", "periphrasis": "accept_all", "compound_role": "accept_all",
    "stated": "deny_all", "stated_hedged": "deny_all", "stated_role_first": "deny_all", "owner_match": "deny_all",
    "stated_synonym": "deny_all", "compound_role_stated": "deny_all",
    # kinds a SET-MEMBERSHIP control cannot pin: they are decided by policy before membership is asked - a reply with no claim, a loose label
    # (friend / colleague is not a family role), a denied role (not a claim), the owner's same-turn statement (evidence only once stored).
    # test_role_policy_rows below pins each of those directly.
    "no_role": None, "no_name": None, "loose_label": None, "negated_claim": None, "no_claim": None, "user_just_stated": None,
    "possessive_role": None,
}


def _role_red(control: str, monkeypatch=None) -> set:
    with pytest.MonkeyPatch.context() as mp, ROLE_CONTROLS[control](mp):
        return {i["id"] for i in ROLE if role_structural_guess(i) != _role_expected(i)}


def test_every_role_row_kind_with_a_claim_has_a_control_that_turns_it_red(monkeypatch):
    kinds = {i["kind"] for i in ROLE}
    assert kinds <= set(ROLE_KIND_CONTROL), sorted(kinds - set(ROLE_KIND_CONTROL))
    red = {c: _role_red(c, monkeypatch) for c in ROLE_CONTROLS}
    bad = []
    for kind in sorted(kinds):
        control = ROLE_KIND_CONTROL[kind]
        if control is None:
            continue          # a reply with no claim to check: nothing for a triple floor to do (the reader is the control, below)
        ids = {i["id"] for i in ROLE if i["kind"] == kind}
        if not (red[control] & ids):
            bad.append((kind, control))
    assert bad == [], bad


def test_role_policy_rows():
    """The kinds a set-membership control cannot pin, pinned one by one."""
    by_kind = collections.defaultdict(list)
    for i in ROLE:
        by_kind[i["kind"]].append(i)
    for kind in ("loose_label", "possessive_role", "negated_claim", "no_role", "no_name", "no_claim"):
        for i in by_kind[kind]:
            assert role_structural_guess(i) is False, (kind, i["reply"])       # not a guess: never rewritten
    for i in by_kind["user_just_stated"]:
        assert role_structural_guess(i) is True        # by design: the same-turn statement is not an id triple (yet)


def test_role_reader_control_no_claims_read_means_every_guess_passes(monkeypatch):
    """Replace the reply reader with 'no claims' and every row that IS a guess goes red - the English rows through the real reader."""
    monkeypatch.setattr(rg, "_claim_patterns", lambda alt: [])
    guesses = [i for i in ROLE if i["lang"] == "en" and _role_expected(i)]
    assert guesses
    assert all(not role_english_reader_guess(i, monkeypatch) for i in guesses)
