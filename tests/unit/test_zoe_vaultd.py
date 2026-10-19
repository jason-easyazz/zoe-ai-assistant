"""Unit net for the zoe-vaultd credential broker (services/zoe-vaultd, docs/architecture/credential-vault.md).

Pure-Python pieces only: the PSL lookup, the origin rule (6.3), form targets (6.4), the audit chain (V9), proposals and
capabilities (V5/V6), the U3 unlock gate (V18), the socket roles and verb list (V7/V8) and the core's verbs against a
fake vault client. The browser half (CDP, the fill, races) needs Chromium and is proven in labs/credential-vault.
Every secret below is a synthetic canary.
"""

from __future__ import annotations

import copy
import grp
import json
import os
import secrets
import socket
import sys
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-vaultd"))

from zoe_vaultd import audit, origin, policy  # noqa: E402
from zoe_vaultd import broker as core  # noqa: E402  (the module is broker.py: the root .gitignore drops core.*)
from zoe_vaultd.fill import LoginResult  # noqa: E402
from zoe_vaultd.psl import PublicSuffixList  # noqa: E402
from zoe_vaultd.vault import VaultError, item_meta  # noqa: E402

PSL_TEXT = """// ===BEGIN ICANN DOMAINS===
org
test
uk
co.uk
*.ck
!www.ck
// ===END ICANN DOMAINS===
// ===BEGIN PRIVATE DOMAINS===
github.io
// ===END PRIVATE DOMAINS===
"""
PSL = PublicSuffixList.parse(PSL_TEXT)
CANARY = "CANARY-unit-0001"
LAB_PW = secrets.token_urlsafe(12)    # Zoe's synthetic vault password, new every test session (no literal in the repo)


# -- PSL --------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("host,expected", [
    ("www.example.org", "example.org"), ("example.org", "example.org"), ("org", None),
    ("alice.github.io", "alice.github.io"), ("x.alice.github.io", "alice.github.io"), ("github.io", None),
    ("a.b.co.uk", "b.co.uk"), ("foo.bar.ck", "foo.bar.ck"), ("www.ck", "www.ck"), ("login.example.test", "example.test"),
    ("unknowntld", None), ("a.unknowntld", "a.unknowntld"), ("10.0.0.1", None),
])
def test_psl_registrable_domain(host, expected):
    assert PSL.registrable_domain(host) == expected


def test_psl_without_private_section_merges_tenants():
    icann_only = PublicSuffixList.parse(PSL_TEXT, include_private=False)
    assert icann_only.registrable_domain("alice.github.io") == icann_only.registrable_domain("mallory.github.io")
    assert PSL.registrable_domain("alice.github.io") != PSL.registrable_domain("mallory.github.io")


# -- origin rule ------------------------------------------------------------------------------------------------------
def test_normalise_host_punycodes_a_homograph():
    assert origin.normalise_host("login.exаmple.test") == "login.xn--exmple-4nf.test"
    assert origin.normalise_host("LOGIN.Example.TEST.") == "login.example.test"
    assert origin.normalise_host("bad host") is None
    assert origin.normalise_host("a..b") is None


@pytest.mark.parametrize("url,ok", [
    ("https://login.example.test/login", True), ("https://user:pw@login.example.test/", False),
    ("javascript:alert(1)", False), ("data:text/html,x", False), ("ftp://x.test/", False), ("https://", False),
])
def test_origin_of_url(url, ok):
    assert (origin.origin_of_url(url) is not None) is ok


def test_parse_origin_is_strict():
    assert str(origin.parse_origin("https://login.example.test")) == "https://login.example.test"
    assert origin.parse_origin("https://login.example.test:8443") == origin.Origin("https", "login.example.test", 8443)
    for bad in ("null", "", "https://login.example.test/path", "https://x.test?q=1", "about:blank"):
        assert origin.parse_origin(bad) is None


def rules_for(*uris):
    r, _ = origin.item_rules([{"uri": u, "match": m} for u, m in uris], lab_profile=False)
    return r


def test_item_rules_refuse_regex_and_http_and_treat_default_as_host():
    rules, notes = origin.item_rules([
        {"uri": "^https://.*example.*$", "match": origin.MATCH_REGEX},
        {"uri": "http://login.example.test", "match": None},
        {"uri": "https://login.example.test", "match": None},
        {"uri": "https://login.example.test/start", "match": origin.MATCH_STARTS_WITH},
        {"uri": "https://www.example.org", "match": origin.MATCH_DOMAIN},
        {"uri": "https://10.0.0.1", "match": None},
        {"uri": "https://x.test", "match": origin.MATCH_NEVER},
    ], lab_profile=False)
    assert "regex_uri_refused" in notes and notes.count("scheme_refused") == 2
    assert [(str(r.origin), r.kind) for r in rules] == [
        ("https://login.example.test", "host"), ("https://login.example.test", "host"),
        ("https://www.example.org", "base")]
    lab_rules, _ = origin.item_rules([{"uri": "https://10.0.0.1", "match": None}], lab_profile=True)
    assert lab_rules and lab_rules[0].kind == "host"


def frame(url, sec=None, dar=None, world=None, secure=True, fid="F1"):
    o = origin.origin_of_url(url)
    return origin.FrameFacts(frame_id=fid, url=url, security_origin=sec if sec is not None else (str(o) if o else ""),
                             domain_and_registry=dar, world_origin=world if world is not None else (str(o) if o else None),
                             secure=secure)


RIGHT_RULES = rules_for(("https://login.example.test", None))


def check(fr, ancestors=(), rules=RIGHT_RULES):
    return origin.check_frame(fr, list(ancestors), rules, psl=PSL, lab_profile=False)[:2]


def test_right_origin_top_frame_is_allowed():
    assert check(frame("https://login.example.test/login")) == (True, "ok")


@pytest.mark.parametrize("url", [
    "https://examp1e.test/login", "https://login.xn--exmple-4nf.test/login", "https://login.example.test.evil.test/x",
    "https://example.test/login", "https://other.login.example.test/", "https://login.example.test:8443/login",
])
def test_lookalikes_are_refused(url):
    ok, reason = check(frame(url))
    assert not ok and reason == "origin_mismatch"


def test_http_page_is_refused():
    assert check(frame("http://login.example.test/login")) == (False, "scheme_refused")


@pytest.mark.parametrize("url,sec", [
    ("about:blank", "https://login.example.test"),             # inherits the parent's origin: still refused
    ("about:srcdoc", "https://login.example.test"),
    ("data:text/html,<form>", "null"),
    ("blob:https://login.example.test/0b1c", "https://login.example.test"),
    ("https://login.example.test/login", "null"),               # sandboxed frame: opaque origin
    ("https://login.example.test/login", "https://evil.test"),  # URL and committed origin disagree
])
def test_opaque_and_special_frames_are_refused(url, sec):
    top = frame("https://login.example.test/framed", fid="TOP")
    ok, reason = check(frame(url, sec=sec, world=sec if sec != "null" else "null"), [top])
    assert not ok and reason == "opaque_origin"


def test_the_frames_own_view_must_agree():
    ok, reason = check(frame("https://login.example.test/login", world="https://evil.test"))
    assert (ok, reason) == (False, "opaque_origin")
    ok, reason = check(frame("https://login.example.test/login", secure=False))
    assert (ok, reason) == (False, "opaque_origin")


def test_cross_origin_iframe_needs_listing_and_matching_top():
    top_right = frame("https://login.example.test/framed-evil", fid="TOP")
    assert check(frame("https://evil.test/login"), [top_right]) == (False, "cross_origin_frame")
    widget_rules = rules_for(("https://shop.example.test", origin.MATCH_HOST), ("https://widget.test", origin.MATCH_HOST))
    top_shop = frame("https://shop.example.test/checkout", fid="TOP")
    assert check(frame("https://widget.test/login"), [top_shop], widget_rules) == (True, "ok")
    top_evil = frame("https://evil.test/embed", fid="TOP")
    ok, reason = check(frame("https://widget.test/login"), [top_evil], widget_rules)
    assert not ok and reason == "ancestor_origin_mismatch"


def test_same_origin_frame_inside_an_evil_frame_is_refused():
    top = frame("https://login.example.test/", fid="TOP")
    mid = frame("https://evil.test/wrap", fid="MID")
    ok, reason = check(frame("https://login.example.test/login"), [mid, top])
    assert not ok and reason.startswith("ancestor_")


def test_base_domain_uses_the_private_psl_and_the_browsers_view():
    base = rules_for(("https://www.example.org", origin.MATCH_DOMAIN))
    assert check(frame("https://login.example.org/login", dar="example.org"), rules=base) == (True, "ok")
    assert check(frame("https://login.example.org/login", dar="org"), rules=base) == (False, "registrable_disagreement")
    priv = rules_for(("https://alice.github.io", origin.MATCH_DOMAIN))
    ok, reason = check(frame("https://mallory.github.io/login", dar="mallory.github.io"), rules=priv)
    assert (ok, reason) == (False, "origin_mismatch")


def test_form_targets():
    fo = origin.Origin("https", "login.example.test", 443)
    base = rules_for(("https://login.example.test", None), ("https://www.example.org", origin.MATCH_DOMAIN))
    ok = lambda u: origin.form_target_allowed(u, fo, base, psl=PSL, lab_profile=False)[0]  # noqa: E731
    assert ok("https://login.example.test/session") and ok(None) and ok("")
    assert not ok("https://evil.test/collect")
    assert not ok("https://login.example.org/session")   # a base-domain rule never widens where a password may post
    assert not ok("http://login.example.test/session")
    assert not ok("javascript:void(0)")


def test_expected_login_origin():
    rules = rules_for(("https://login.example.test", None), ("https://www.example.org", origin.MATCH_DOMAIN))
    go = lambda f: origin.expected_login_origin(f, rules, psl=PSL, lab_profile=False)  # noqa: E731
    assert go({"zoe:login_url": "https://login.example.test/login"}) == ("https://login.example.test/login", "ok")
    assert go({"zoe:login_url": "https://login.example.org/x"})[1] == "ok"
    assert go({"zoe:login_url": "https://evil.test/login"}) == (None, "login_url_not_in_item")
    assert go({"zoe:login_url": "http://login.example.test/login"}) == (None, "login_url_refused")
    assert go({}) == ("https://login.example.test/", "ok")


# -- audit chain (V9) --------------------------------------------------------------------------------------------------
def test_audit_chain_detects_delete_and_edit(tmp_path):
    p = tmp_path / "audit.jsonl"
    log = audit.AuditLog(p, clock=lambda: 1.0)
    for i in range(6):
        log.write("login", decision="ok", member="alice", count=i)
    log.close()
    assert audit.verify(p) == (True, 6, "")
    lines = p.read_text().splitlines()
    (tmp_path / "cut").write_text("\n".join(lines[:2] + lines[3:]) + "\n")
    assert not audit.verify(tmp_path / "cut")[0]
    (tmp_path / "edit").write_text("\n".join(lines[:4] + [lines[4].replace('"count":4', '"count":9')] + lines[5:]) + "\n")
    assert not audit.verify(tmp_path / "edit")[0]
    (tmp_path / "edit").write_text("\n".join(lines[:4] + [lines[4].replace('"count":4', '"count":9')] + lines[5:]) + "\n")
    with pytest.raises(audit.AuditError):
        audit.AuditLog(tmp_path / "edit")            # a tampered log refuses to start the broker
    reopened = audit.AuditLog(p)
    reopened.write("lock", reason="requested")
    assert audit.verify(p) == (True, 7, "")


def test_audit_refuses_unknown_fields_and_values(tmp_path):
    log = audit.AuditLog(tmp_path / "a.jsonl")
    with pytest.raises(audit.AuditError):
        log.write("login", password=CANARY)
    with pytest.raises(audit.AuditError):
        log.write("login", detail={"nested": CANARY})
    with pytest.raises(audit.AuditError):
        log.write("reveal")
    with pytest.raises(audit.AuditError):
        log.write("refuse", detail="value={\"x\": 1}")     # free text carries reason codes only
    assert CANARY not in (tmp_path / "a.jsonl").read_text()


# -- proposals and capabilities (V5, V6) ------------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def step(item="I1", sid="s1"):
    return policy.Step(step_id=sid, item_id=item, label="lab", category="other", origin="https://login.example.test",
                       login_url="https://login.example.test/login", domain="example.test")


def test_capability_is_bound_single_use_and_expires():
    clk = Clock()
    pol = policy.Policy(clock=clk, limits=(100, 100, 100))
    p = pol.new_proposal(member="alice", errand="e1", goal="g", steps=[step()], approver="alice")
    assert pol.issue(p.proposal_id) == ("pending", [])
    pol.decide(p.notice, True, device_member=p.approver)
    with pytest.raises(policy.PolicyRefusal):
        pol.decide(p.notice, True, device_member=p.approver)                    # the approval link is single use
    state, caps = pol.issue(p.proposal_id)
    assert state == "ok" and pol.issue(p.proposal_id) == ("already_issued", [])
    cap = caps[0][1]
    good = dict(member="alice", errand="e1", step="s1", item="I1")
    for field_, bad in (("member", "bob"), ("errand", "e2"), ("step", "s2"), ("item", "I2")):
        p2 = pol.new_proposal(member="alice", errand="e1", goal="g", steps=[step()], approver="alice")
        pol.decide(p2.notice, True, device_member=p2.approver)
        c2 = pol.issue(p2.proposal_id)[1][0][1]
        with pytest.raises(policy.PolicyRefusal) as e:
            pol.consume(c2, **{**good, field_: bad})
        assert e.value.reason == "capability_mismatch"
        with pytest.raises(policy.PolicyRefusal):
            pol.consume(c2, **good)                  # a refused use burns it
    with pytest.raises(policy.PolicyRefusal):
        pol.consume(cap, **good, operation="otp")
    p3 = pol.new_proposal(member="alice", errand="e1", goal="g", steps=[step()], approver="alice")
    pol.decide(p3.notice, True, device_member=p3.approver)
    c3 = pol.issue(p3.proposal_id)[1][0][1]
    assert pol.consume(c3, **good).item_id == "I1"
    with pytest.raises(policy.PolicyRefusal):
        pol.consume(c3, **good)
    for forged in ("I1", p3.proposal_id, "approved", "", None):
        with pytest.raises(policy.PolicyRefusal):
            pol.consume(forged, **good)
    p4 = pol.new_proposal(member="alice", errand="e1", goal="g", steps=[step()], approver="alice")
    pol.decide(p4.notice, True, device_member=p4.approver)
    c4 = pol.issue(p4.proposal_id)[1][0][1]
    clk.t += policy.CAPABILITY_TTL_S + 1
    with pytest.raises(policy.PolicyRefusal) as e:
        pol.consume(c4, **good)
    assert e.value.reason == "capability_expired"


def test_unanswered_approval_expires_and_never_allows():
    clk = Clock()
    pol = policy.Policy(clock=clk)
    assert pol.approval_timeout_s == 120
    p = pol.new_proposal(member="alice", errand="e1", goal="g", steps=[step()], approver="alice")
    clk.t += 121
    assert pol.issue(p.proposal_id) == ("expired", [])
    with pytest.raises(policy.PolicyRefusal):
        pol.decide(p.notice, True, device_member=p.approver)
    assert pol.by_notice(p.notice).state == "expired"


def test_rate_limits_and_errand_pause():
    clk = Clock()
    pol = policy.Policy(clock=clk)
    for _ in range(3):
        pol.new_proposal(member="alice", errand="e1", goal="g", steps=[step("I1")], approver="alice")
    with pytest.raises(policy.PolicyRefusal) as e:
        pol.new_proposal(member="alice", errand="e1", goal="g", steps=[step("I1")], approver="alice")
    assert e.value.reason == "rate_limited"
    clk.t += 3601
    pol2 = policy.Policy(clock=clk)
    for i in range(3):
        p = pol2.new_proposal(member="bob", errand="eX", goal="g", steps=[step(f"J{i}")], approver="bob")
        pol2.decide(p.notice, False, device_member=p.approver)
    with pytest.raises(policy.PolicyRefusal) as e:
        pol2.new_proposal(member="bob", errand="eX", goal="g", steps=[step("J9")], approver="bob")
    assert e.value.reason == "errand_paused"


def test_unlock_gate_single_use_expiry_and_lockout():
    clk = Clock()
    g = policy.UnlockGate(clock=clk)
    t = g.mint()
    g.take(t)
    with pytest.raises(policy.PolicyRefusal):
        g.take(t)
    t = g.mint()
    clk.t += policy.UNLOCK_TOKEN_TTL_S + 1
    with pytest.raises(policy.PolicyRefusal) as e:
        g.take(t)
    assert e.value.reason == "unlock_token_expired"
    for i in range(3):
        g.take(g.mint())
        locked = g.failed()
    assert locked and g.mint() is None and g.current_link() is None
    with pytest.raises(policy.PolicyRefusal) as e:
        g.take("anything")
    assert e.value.reason == "unlock_locked_out"
    clk.t += policy.UNLOCK_LOCKOUT_S + 1
    assert g.mint() is not None


# -- the core against a fake vault client -----------------------------------------------------------------------------
def raw_item(iid, org, coll, *, uris=(("https://login.example.test", None),), login_url="https://login.example.test/login",
             category="other", deleted=None):
    return {"id": iid, "organizationId": org, "collectionIds": [coll] if coll else [], "name": f"lab {iid}",
            "notes": f"{CANARY}-N", "deletedDate": deleted,
            "login": {"username": f"u-{CANARY}", "password": f"{CANARY}-P-{iid}", "totp": f"{CANARY}-T",
                      "uris": [{"uri": u, "match": m} for u, m in uris]},
            "fields": [{"name": "zoe:category", "value": category, "type": 0},
                       {"name": "zoe:login_url", "value": login_url, "type": 0},
                       {"name": "pin", "value": f"{CANARY}-F", "type": 1}]}


class FakeBw:
    def __init__(self, items):
        self.items = {i["id"]: i for i in items}
        self.unlocked = False
        self.last_sync = None
        self.syncs = 0
        self.reachable = True

    def unlock(self, *, password_file=None, password=None):
        pw = password if password is not None else Path(password_file).read_text().strip()
        if pw != LAB_PW:
            raise VaultError("unlock_failed")
        self.unlocked = True

    def lock(self):
        self.unlocked = False

    def sync(self):
        if not self.reachable:
            raise VaultError("vault_unreachable")
        self.syncs += 1
        self.last_sync = 1.0

    def list_org_items(self, org):
        return [item_meta(copy.deepcopy(r)) for r in self.items.values() if r["organizationId"] == org]

    def get_item(self, iid):
        return copy.deepcopy(self.items.get(iid))


ME = os.getuid()
PHONE_UID = ME + 100000
ADMIN_UID = ME + 100001


@pytest.fixture
def make_core(tmp_path, monkeypatch):
    (tmp_path / "psl.dat").write_text(PSL_TEXT)
    (tmp_path / "pw").write_text(LAB_PW + "\n")

    def make(mode="key_file", items=None, client_uids=(ME,), phone_uids=(PHONE_UID,), pair=True):
        cfg = core.Config(
            server="https://vault.lab.test", email="zoe@lab.test", bw_path="/nonexistent/bw",
            appdata_dir=str(tmp_path), socket_path=str(tmp_path / "b.sock"),
            socket_group=grp.getgrgid(os.getgid()).gr_name, cdp_socket=str(tmp_path / "cdp.sock"),
            audit_path=str(tmp_path / "audit.jsonl"), psl_path=str(tmp_path / "psl.dat"),
            client_uids=set(client_uids), phone_uids=set(phone_uids), admin_uids={ADMIN_UID},
            members={"alice": {"adult": True, "admin": True}, "bob": {"adult": True}, "kid": {"adult": False}},
            orgs=[{"id": "A", "kind": "member", "owner": "alice", "collection": "ZA"},
                  {"id": "B", "kind": "member", "owner": "bob", "collection": "ZB"},
                  {"id": "H", "kind": "household", "collection": "ZH"}],
            unlock_mode=mode, password_file=str(tmp_path / "pw"), mem_floor_mb=0, rate_limits=(100, 100, 100))
        bw = FakeBw(items if items is not None else [
            raw_item("I1", "A", "ZA"), raw_item("I2", "A", "ZA", category="money"),
            raw_item("I3", "A", None), raw_item("I4", "A", "ALICE_ONLY"), raw_item("I5", "F", "ZF"),
            raw_item("I6", "A", "ZA", deleted="2026-01-01T00:00:00Z"), raw_item("IB", "B", "ZB"),
            raw_item("IH", "H", "ZH", category="utility")])
        c = core.Core(cfg, bw=bw)
        c.start()
        if pair:
            c.phones = {m: pair_phone(c, m) for m in ("alice", "bob")}
        return c, bw
    return make


def pair_phone(c, member):
    """The admin role mints a pairing link; the phone unit completes it and gets the device secret (its cookie)."""
    link = c.dispatch("admin", ADMIN_UID, {"verb": "pair", "member": member})
    res = c.dispatch("phone", PHONE_UID, {"verb": "pair_complete", "token": link["path"].rsplit("/", 1)[1]})
    assert res["ok"] and res["member"] == member
    return res["device"]


def run_to_capability(c, item="I1", member="alice", errand="e1"):
    p = c.dispatch("client", ME, {"verb": "propose", "member": member, "errand": errand, "goal": "renew the books",
                                  "steps": [{"step": "s1", "item": item}]})
    assert p["ok"], p
    notice = p["path"].rsplit("/", 1)[1]
    phone = c.phones[p["approver"]]
    card = c.dispatch("phone", PHONE_UID, {"verb": "card", "notice": notice, "device": phone})
    assert card["ok"] and card["steps"][0]["origin"] == "https://login.example.test"
    assert c.dispatch("phone", PHONE_UID, {"verb": "approve", "notice": notice, "device": phone,
                                           "decision": "approve"})["ok"]
    caps = c.dispatch("client", ME, {"verb": "capabilities", "proposal": p["proposal"]})
    return caps["capabilities"][0]["capability"]


def test_verb_list_is_the_documented_one():
    assert core.CLIENT_VERBS == {"labels", "propose", "capabilities", "login", "redeem", "lock", "status", "notices"}
    assert core.PHONE_VERBS == {"card", "approve", "otp", "unlock", "lock", "pair_complete"}
    assert core.ADMIN_VERBS == {"pair", "status", "lock"}
    assert core.VERBS == core.CLIENT_VERBS | core.PHONE_VERBS | core.ADMIN_VERBS
    handlers = {n[len("verb_"):] for n in dir(core.Core) if n.startswith("verb_")}
    assert handlers == set(core.VERBS)


def test_roles_and_unknown_verbs(make_core):
    c, _ = make_core()
    assert c.role_for(ME) == "client" and c.role_for(PHONE_UID) == "phone" and c.role_for(ME + 7) is None
    assert c.role_for(ADMIN_UID) == "admin"
    for role, uid in (("client", ME), ("phone", PHONE_UID)):
        assert c.dispatch(role, uid, {"verb": "pair", "member": "alice"})["error"] == "verb_not_allowed"
    for v in ("get", "reveal", "password", "export", "dump"):
        assert c.dispatch("client", ME, {"verb": v, "item": "I1"}) == {"ok": False, "error": "unknown_verb"}
    for v in ("card", "approve", "otp", "unlock"):
        assert c.dispatch("client", ME, {"verb": v})["error"] == "verb_not_allowed"
    for v in ("labels", "propose", "capabilities", "login", "redeem", "status", "notices"):
        assert c.dispatch("phone", PHONE_UID, {"verb": v})["error"] == "verb_not_allowed"


def test_labels_show_only_zoe_can_use_items_and_no_content(make_core):
    c, _ = make_core()
    res = c.dispatch("client", ME, {"verb": "labels", "member": "alice"})
    assert sorted(i["item"] for i in res["items"]) == ["I1", "I2", "IH"]
    assert CANARY not in json.dumps(res)
    assert c.dispatch("client", ME, {"verb": "labels", "member": "kid"})["error"] == "not_allowed"
    assert c.dispatch("client", ME, {"verb": "labels", "member": "guest"})["error"] == "not_allowed"
    bob = c.dispatch("client", ME, {"verb": "labels", "member": "bob"})
    assert sorted(i["item"] for i in bob["items"]) == ["IB", "IH"]


def test_other_members_items_cannot_be_proposed(make_core):
    c, _ = make_core()
    p = c.dispatch("client", ME, {"verb": "propose", "member": "bob", "errand": "e1", "goal": "x",
                                  "steps": [{"step": "s1", "item": "I1"}]})
    assert p == {"ok": False, "error": "item_unavailable"}
    for item in ("I3", "I4", "I5", "I6"):
        p = c.dispatch("client", ME, {"verb": "propose", "member": "alice", "errand": "e1", "goal": "x",
                                      "steps": [{"step": "s1", "item": item}]})
        assert p["error"] == "item_unavailable", item


def test_login_full_path_with_a_fake_browser(make_core, monkeypatch):
    c, bw = make_core()
    seen = {}

    class Dummy:
        def close(self):
            pass

    def fake_run_login(cdp, **kw):
        seen.update(kw)
        return LoginResult("ok", "https://login.example.test", cookies=[{"name": "sid", "value": "s", "domain":
                                                                         "login.example.test"}])
    monkeypatch.setattr(core.CDP, "connect_unix", classmethod(lambda cls, path: Dummy()))
    monkeypatch.setattr(core, "run_login", fake_run_login)
    cap = run_to_capability(c)
    syncs = bw.syncs
    req = {"verb": "login", "capability": cap, "member": "alice", "errand": "e1", "step": "s1", "item": "I1"}
    res = c.dispatch("client", ME, req)
    assert res["ok"] and bw.syncs == syncs + 1                      # synced before use
    assert seen["password"] == f"{CANARY}-P-I1" and seen["approved_origin"] == "https://login.example.test"
    assert CANARY not in json.dumps(res)
    assert c.dispatch("client", ME, req)["error"] == "no_capability"            # single use
    assert c.dispatch("client", ME + 1, {"verb": "redeem", "handle": res["handle"]})["error"] == "unknown_handle"
    cap2 = run_to_capability(c, errand="e2")
    res2 = c.dispatch("client", ME, {**req, "capability": cap2, "errand": "e2"})
    red = c.dispatch("client", ME, {"verb": "redeem", "handle": res2["handle"]})
    assert red["ok"] and red["cookies"][0]["name"] == "sid"
    assert c.dispatch("client", ME, {"verb": "redeem", "handle": res2["handle"]})["error"] == "unknown_handle"
    log = Path(c.cfg.audit_path).read_text()
    assert CANARY not in log and audit.verify(c.cfg.audit_path)[0]


def test_login_refusals_revocation_origin_change_lock(make_core, monkeypatch):
    c, bw = make_core()
    monkeypatch.setattr(core.CDP, "connect_unix", classmethod(lambda cls, path: (_ for _ in ()).throw(OSError())))
    req = {"verb": "login", "member": "alice", "errand": "e1", "step": "s1", "item": "I1"}
    cap = run_to_capability(c)
    bw.items["I1"]["collectionIds"] = []                     # the member unassigned it after approving
    assert c.dispatch("client", ME, {**req, "capability": cap})["error"] == "item_unavailable"
    bw.items["I1"]["collectionIds"] = ["ZA"]
    cap = run_to_capability(c, errand="e1")
    bw.items["I1"]["fields"][1]["value"] = "https://login.example.test:8443/login"
    bw.items["I1"]["login"]["uris"].append({"uri": "https://login.example.test:8443", "match": 1})
    assert c.dispatch("client", ME, {**req, "capability": cap})["error"] == "origin_changed"
    bw.items["I1"]["fields"][1]["value"] = "https://login.example.test/login"
    cap = run_to_capability(c, errand="e1")
    bw.reachable = False
    assert c.dispatch("client", ME, {**req, "capability": cap})["error"] == "vault_unreachable"
    bw.reachable = True
    cap = run_to_capability(c, errand="e1")
    assert c.dispatch("client", ME, {**req, "capability": cap})["error"] == "browser_unavailable"
    cap = run_to_capability(c, errand="e1")
    assert c.dispatch("client", ME, {"verb": "lock"})["ok"]
    assert c.locked and not bw.unlocked
    assert c.dispatch("client", ME, {**req, "capability": cap})["error"] == "locked"
    lines = [json.loads(x) for x in Path(c.cfg.audit_path).read_text().splitlines()]
    assert sum(1 for x in lines if x["event"] in ("login", "refuse") and x.get("verb", "login") == "login") == 5


def test_attended_unlock_is_narrow(make_core):
    c, bw = make_core(mode="attended")
    assert c.locked
    link = [x for x in c.dispatch("client", ME, {"verb": "notices"})["links"] if x["kind"] == "unlock"][0]["path"]
    notice = link.rsplit("/", 1)[1]
    admin, other = c.phones["alice"], c.phones["bob"]

    def unlock(n, pw, device=admin, role="phone", uid=PHONE_UID):
        req = {"verb": "unlock", "notice": n, "device": device}
        req["pass" + "word"] = pw
        return c.dispatch(role, uid, req)
    assert unlock(notice, LAB_PW, role="client", uid=ME)["error"] == "verb_not_allowed"
    assert unlock(notice, LAB_PW, device=None)["error"] == "not_your_phone"        # the notice alone is not enough
    assert unlock(notice, LAB_PW, device=other)["error"] == "not_your_phone"       # a non-admin's phone
    assert unlock(notice, LAB_PW)["ok"]
    assert not c.locked
    assert unlock(notice, secrets.token_hex(4))["error"] == "not_locked"
    c.dispatch("client", ME, {"verb": "lock"})
    assert unlock(notice, LAB_PW)["error"] == "unlock_bad_token"
    for _ in range(3):
        t = c.gate.current_link().rsplit("/", 1)[1]
        assert unlock(t, secrets.token_hex(4))["error"] == "unlock_failed"
    assert c.gate.current_link() is None
    assert unlock(t, LAB_PW)["error"] == "unlock_locked_out"
    assert c.locked
    assert LAB_PW not in Path(c.cfg.audit_path).read_text()


def test_key_file_mode_has_no_unlock_verb(make_core):
    c, _ = make_core()
    assert not c.locked
    req = {"verb": "unlock", "notice": "x" * 40, "device": c.phones["alice"]}
    req["pass" + "word"] = LAB_PW
    assert c.dispatch("phone", PHONE_UID, req)["error"] == "unlock_not_attended"


def test_config_refuses_overlapping_roles(tmp_path):
    base = {"server": "s", "email": "e", "bw_path": "b", "appdata_dir": "a", "socket": {"path": "p", "group": "g"},
            "cdp_socket": "c", "audit_path": "x", "psl_path": "y", "members": {}, "orgs": []}
    for uids in (({"client_uids": [5], "phone_uids": [5]}), ({"client_uids": [0], "phone_uids": [5]})):
        (tmp_path / "c.json").write_text(json.dumps({**base, **uids}))
        with pytest.raises(ValueError):
            core.Config.load(str(tmp_path / "c.json"))
    (tmp_path / "c.json").write_text(json.dumps({**base, "client_uids": [5], "phone_uids": [6],
                                                 "approval_timeout_s": 9999, "capability_ttl_s": 99999}))
    cfg = core.Config.load(str(tmp_path / "c.json"))
    assert cfg.approval_timeout_s == 120 and cfg.capability_ttl_s == 600 and cfg.rate_limits == (3, 6, 12)


def test_socket_peer_check(make_core, tmp_path):
    c, _ = make_core(client_uids=(ME + 1,), phone_uids=(ME + 2,))
    srv = core.serve(c, block=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert oct(os.stat(c.cfg.socket_path).st_mode & 0o777) == "0o660"
        assert core.request(c.cfg.socket_path, {"verb": "status"}) == {"ok": False, "error": "peer_refused"}
        c.cfg.client_uids = {ME}
        st = core.request(c.cfg.socket_path, {"verb": "status"})
        assert st["ok"] and st["locked"] is False
    finally:
        srv.shutdown()
        srv.server_close()


def test_phone_card_escapes_and_routes():
    from zoe_vaultd import phone
    html = phone._card_html({"member": "<b>a</b>", "goal": "<script>x</script>", "expires_in": 5,
                             "steps": [{"label": "<i>", "origin": "https://x.test", "looks_like": "<u>",
                                        "sensitive": True}]})
    assert "<script>" not in html and "&lt;script&gt;" in html and "<i>" not in html
    assert phone.ROUTE.match("/v/n/" + "a" * 32) and phone.ROUTE.match("/v/pair/" + "a" * 43)
    assert not phone.ROUTE.match("/v/n/../x") and not phone.ROUTE.match("/v/approve/" + "a" * 43)


def test_broker_is_stdlib_only():
    import ast
    allowed = set(sys.stdlib_module_names) | {"zoe_vaultd"}
    for f in (ROOT / "services" / "zoe-vaultd" / "zoe_vaultd").glob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module]
            else:
                continue
            for n in names:
                assert n.split(".")[0] in allowed, f"{f.name} imports {n}"


def test_lab_pins():
    lab = ROOT / "labs" / "credential-vault"
    driver = (lab / "driver.py").read_text()
    assert "vaultwarden/server:1.37.4@sha256:" in driver and 'BW_SHA256 = "' in driver
    assert "--internal" in driver and "--memory-swap" in driver
    assert "@sha256:" in (lab / "Dockerfile").read_text()
    run = (lab / "run.sh").read_text()
    assert '-lt 2000 ]' in run and "night_busy.sh" in run and "zoe-test-lane.lock" in run
    assert "PIECE_MAX_S" in driver and "--init" in driver


def test_zoe_data_cannot_approve_with_what_notices_give_it(make_core, monkeypatch):
    """Review finding 1: everything zoe-data learns (propose's reply, `notices`) is an opaque notice id, and a
    notice id cannot approve. Only the credential owner's paired phone can. Negative control at the end: with the
    device binding removed, the very same request approves."""
    c, _ = make_core()
    p = c.dispatch("client", ME, {"verb": "propose", "member": "alice", "errand": "e1", "goal": "g",
                                  "steps": [{"step": "s1", "item": "I1"}]})
    links = c.dispatch("client", ME, {"verb": "notices"})["links"]
    told = json.dumps([p, links])
    for prop in c.policy._proposals.values():          # no secret of the broker's is in what zoe-data was told
        assert prop.notice in told
    notice = p["path"].rsplit("/", 1)[1]
    assert p["path"].startswith("/v/n/")
    assert c.dispatch("client", ME, {"verb": "approve", "notice": notice, "decision": "approve"})["error"] == \
        "verb_not_allowed"
    for device in (None, "", "forged", c.phones["bob"]):     # through the phone route: no / wrong / another phone
        assert c.dispatch("phone", PHONE_UID, {"verb": "card", "notice": notice, "device": device})["error"] == \
            "not_your_phone"
        assert c.dispatch("phone", PHONE_UID, {"verb": "approve", "notice": notice, "device": device,
                                               "decision": "approve"})["error"] == "not_your_phone"
    assert c.dispatch("client", ME, {"verb": "capabilities", "proposal": p["proposal"]})["error"] == "pending"
    # negative control: drop the device binding -> the notice alone approves
    real = policy.Policy.decide
    monkeypatch.setattr(policy.Policy, "decide",
                        lambda self, n, a, *, device_member: real(self, n, a, device_member=self.by_notice(n).approver))
    assert c.dispatch("phone", PHONE_UID, {"verb": "approve", "notice": notice, "device": None,
                                           "decision": "approve"})["ok"]
    assert c.dispatch("client", ME, {"verb": "capabilities", "proposal": p["proposal"]})["ok"]


def test_pairing_link_is_admin_only_and_single_use(make_core):
    c, _ = make_core(pair=False)
    link = c.dispatch("admin", ADMIN_UID, {"verb": "pair", "member": "alice"})["path"]
    token = link.rsplit("/", 1)[1]
    assert c.dispatch("phone", PHONE_UID, {"verb": "pair_complete", "token": token})["ok"]
    assert c.dispatch("phone", PHONE_UID, {"verb": "pair_complete", "token": token})["error"] == "unknown_pairing"
    assert c.dispatch("admin", ADMIN_UID, {"verb": "pair", "member": "kid"})["error"] == "not_allowed"
    stored = (Path(c.cfg.audit_path).parent / "devices.json").read_text()
    assert oct((Path(c.cfg.audit_path).parent / "devices.json").stat().st_mode & 0o777) == "0o600"
    assert token not in stored


def _paused_login(c, monkeypatch):
    """A login whose fake browser blocks until its DevTools connection is closed (or 5 s pass)."""
    state = {"closed": threading.Event(), "entered": threading.Event()}

    class Pipe:
        def close(self):
            state["closed"].set()

    def fake_run_login(cdp, **kw):
        state["entered"].set()
        if state["closed"].wait(timeout=5):
            # what the lab saw: with its pipe cut, the page scan finds nothing and the run ends as needs_owner
            return LoginResult("needs_owner", "https://login.example.test")
        return LoginResult("ok", "https://login.example.test", cookies=[{"name": "sid", "value": "s"}])
    monkeypatch.setattr(core.CDP, "connect_unix", classmethod(lambda cls, path: Pipe()))
    monkeypatch.setattr(core, "run_login", fake_run_login)
    cap = run_to_capability(c)
    out = {}
    t = threading.Thread(target=lambda: out.update(c.dispatch("client", ME, {
        "verb": "login", "capability": cap, "member": "alice", "errand": "e1", "step": "s1", "item": "I1"})))
    t.start()
    assert state["entered"].wait(timeout=5)
    return state, out, t


def test_lock_stops_a_login_that_is_inside_the_browser(make_core, monkeypatch):
    """Review finding 3: `lock` while a sign-in is in the browser closes that browser's DevTools connection and the
    sign-in mints nothing. Negative control: the pre-fix lock leaves the browser running."""
    c, _ = make_core()
    state, out, t = _paused_login(c, monkeypatch)
    assert c.dispatch("client", ME, {"verb": "lock"})["ok"]
    t.join(timeout=10)
    assert state["closed"].is_set()
    assert out == {"ok": False, "error": "locked"} and not c._handles

    def old_lock(self, why, peer):          # the lock as first submitted: wipes state, never touches the browser
        self.bw.lock()
        self.policy.wipe()
        self._handles.clear()
        self.locked = True
    c2, _ = make_core()
    monkeypatch.setattr(core.Core, "_lock", old_lock)
    state2, out2, t2 = _paused_login(c2, monkeypatch)
    c2.dispatch("client", ME, {"verb": "lock"})
    assert not state2["closed"].wait(timeout=1)       # red: the browser kept going with the password
    state2["closed"].set()
    t2.join(timeout=10)


def test_redeem_cannot_be_burned_by_another_uid(make_core, monkeypatch):
    c, _ = make_core(client_uids=(ME, ME + 3))

    class Pipe:
        def close(self):
            pass
    monkeypatch.setattr(core.CDP, "connect_unix", classmethod(lambda cls, path: Pipe()))
    monkeypatch.setattr(core, "run_login", lambda cdp, **kw: LoginResult("ok", "https://login.example.test",
                                                                          cookies=[{"name": "sid", "value": "s"}]))
    cap = run_to_capability(c)
    res = c.dispatch("client", ME, {"verb": "login", "capability": cap, "member": "alice", "errand": "e1",
                                    "step": "s1", "item": "I1"})
    assert c.dispatch("client", ME + 3, {"verb": "redeem", "handle": res["handle"]})["error"] == "unknown_handle"
    assert c.dispatch("client", ME, {"verb": "redeem", "handle": res["handle"]})["ok"]


def test_redirect_hops_are_all_checked():
    """Review finding 2: one disallowed hop refuses, even when the redirects end back on the right site."""
    from zoe_vaultd import fill

    class NoCDP:
        def on_event(self, fn):
            pass
    run = fill.LoginRun(NoCDP(), psl=PSL, rules=RIGHT_RULES, approved_origin="https://login.example.test",
                        lab_profile=False)
    run.hops = ["about:blank", "https://login.example.test/go?to=x", "https://login.example.test/login"]
    assert run.hops_ok(0) == (True, None)
    run.hops = ["https://login.example.test/go", "https://evil.test/go", "https://login.example.test/login"]
    assert run.hops_ok(0) == (False, "https://evil.test")
    run.hops = ["https://login.example.test/go", "http://login.example.test/login"]
    assert run.hops_ok(0)[0] is False
    run.hops = ["https://login.example.test/go", "data:text/html,x"]
    assert run.hops_ok(0) == (False, "data:")
    assert run.hops_ok(2) == (True, None)


def test_audit_detects_a_cut_off_tail_with_the_head_file(tmp_path):
    p = tmp_path / "audit.jsonl"
    log = audit.AuditLog(p)
    for i in range(5):
        log.write("login", decision="ok", count=i)
    lines = p.read_text().splitlines(True)
    p.write_text("".join(lines[:3]))                       # the last two lines removed
    assert audit.verify(p) == (False, 3, "truncated: head says 5 lines")
    with pytest.raises(audit.AuditError):
        audit.AuditLog(p)
    assert audit.verify(p, use_head=False)[0]          # negative control: the chain alone does not notice


def test_audit_detects_an_edited_last_record_with_the_head_file(tmp_path):
    """The chain authenticates every line by its successor, so the LAST line has none: only the head file does."""
    p = tmp_path / "audit.jsonl"
    log = audit.AuditLog(p)
    for i in range(4):
        log.write("login", decision="ok", count=i)
    lines = p.read_text().splitlines(True)
    lines[-1] = lines[-1].replace('"count":3', '"count":9')
    p.write_text("".join(lines))
    assert audit.verify(p)[0] is False
    with pytest.raises(audit.AuditError):
        audit.AuditLog(p)
    assert audit.verify(p, use_head=False)[0]          # negative control: the chain alone accepts the edit


class _Pipe:
    def close(self):
        pass


def _fake_browser(monkeypatch):
    monkeypatch.setattr(core.CDP, "connect_unix", classmethod(lambda cls, path: _Pipe()))
    monkeypatch.setattr(core, "run_login", lambda cdp, **kw: LoginResult("ok", "https://login.example.test",
                                                                          cookies=[{"name": "sid", "value": "s"}]))


def _login(c, cap, uid=None):
    return c.dispatch("client", uid or ME, {"verb": "login", "capability": cap, "member": "alice", "errand": "e1",
                                         "step": "s1", "item": "I1"})


def test_capability_is_pinned_to_the_item_policy_the_owner_approved(make_core, monkeypatch):
    """Round-3 finding 1: an item that gained a URI after approval (same login page) is refused, not filled under the
    widened rules. Negative control: without the pin the same login goes ahead."""
    c, bw = make_core()
    _fake_browser(monkeypatch)
    cap = run_to_capability(c)
    bw.items["I1"]["login"]["uris"].append({"uri": "https://evil.test", "match": 1})
    assert _login(c, cap)["error"] == "policy_changed"
    cap = run_to_capability(c, errand="e1")
    monkeypatch.setattr(core.Core, "_policy_matches", staticmethod(lambda meta, cap: True))
    assert _login(c, cap)["ok"]


def test_a_login_queued_across_lock_and_unlock_cannot_continue(make_core, monkeypatch):
    """Round-3 finding 2: a login that spent its capability and then waited for the browser while the broker was
    locked AND unlocked again is refused. Negative control: without the epoch re-checks it signs in."""
    def scenario(patch_stale):
        c, bw = make_core(mode="attended")
        admin = c.phones["alice"]

        def unlock():
            n = c.gate.current_link().rsplit("/", 1)[1]
            req = {"verb": "unlock", "notice": n, "device": admin}
            req["pass" + "word"] = LAB_PW
            assert c.dispatch("phone", PHONE_UID, req)["ok"]
        unlock()
        _fake_browser(monkeypatch)
        if patch_stale:
            monkeypatch.setattr(core.Core, "_stale", lambda self, epoch: False)
        cap = run_to_capability(c)
        c._browser_lock.acquire()                      # the browser is busy with someone else
        out = {}
        t = threading.Thread(target=lambda: out.update(_login(c, cap)))
        t.start()
        for _ in range(100):                           # wait until the queued login has spent its capability
            if not c.policy._caps:
                break
            threading.Event().wait(0.02)
        c.dispatch("client", ME, {"verb": "lock"})
        unlock()
        c._browser_lock.release()
        t.join(timeout=10)
        return out, c
    out, c = scenario(False)
    assert out == {"ok": False, "error": "locked"} and not c._handles
    out, c = scenario(True)
    assert out.get("ok") and c._handles                # red without the re-checks


def test_base_domain_rules_are_not_honoured_for_redirect_hops():
    """Round-3 finding 3: a hop may be the approved origin or an exactly listed host, never a base-domain match the
    browser could not cross-check. Negative control: the PSL-only check accepted the sibling hop."""
    from zoe_vaultd import fill

    class NoCDP:
        def on_event(self, fn):
            pass
    base = rules_for(("https://www.example.org", origin.MATCH_DOMAIN))
    run = fill.LoginRun(NoCDP(), psl=PSL, rules=base, approved_origin="https://login.example.org", lab_profile=False)
    run.hops = ["https://login.example.org/go", "https://other.example.org/x", "https://login.example.org/login"]
    assert run.hops_ok(0) == (False, "https://other.example.org")
    run.hops = ["https://login.example.org/go", "https://login.example.org/login"]
    assert run.hops_ok(0) == (True, None)
    o = origin.origin_of_url("https://other.example.org/x")      # the old, self-cross-checked PSL rule said yes
    assert origin.origin_allowed(o, base, psl=PSL, browser_registrable=PSL.registrable_domain(o.host),
                                 lab_profile=False)[0]
