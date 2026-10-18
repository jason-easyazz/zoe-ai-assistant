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
    pol.decide(p.token, True)
    with pytest.raises(policy.PolicyRefusal):
        pol.decide(p.token, True)                    # the approval link is single use
    state, caps = pol.issue(p.proposal_id)
    assert state == "ok" and pol.issue(p.proposal_id) == ("already_issued", [])
    cap = caps[0][1]
    good = dict(member="alice", errand="e1", step="s1", item="I1")
    for field_, bad in (("member", "bob"), ("errand", "e2"), ("step", "s2"), ("item", "I2")):
        p2 = pol.new_proposal(member="alice", errand="e1", goal="g", steps=[step()], approver="alice")
        pol.decide(p2.token, True)
        c2 = pol.issue(p2.proposal_id)[1][0][1]
        with pytest.raises(policy.PolicyRefusal) as e:
            pol.consume(c2, **{**good, field_: bad})
        assert e.value.reason == "capability_mismatch"
        with pytest.raises(policy.PolicyRefusal):
            pol.consume(c2, **good)                  # a refused use burns it
    with pytest.raises(policy.PolicyRefusal):
        pol.consume(cap, **good, operation="otp")
    p3 = pol.new_proposal(member="alice", errand="e1", goal="g", steps=[step()], approver="alice")
    pol.decide(p3.token, True)
    c3 = pol.issue(p3.proposal_id)[1][0][1]
    assert pol.consume(c3, **good).item_id == "I1"
    with pytest.raises(policy.PolicyRefusal):
        pol.consume(c3, **good)
    for forged in ("I1", p3.proposal_id, "approved", "", None):
        with pytest.raises(policy.PolicyRefusal):
            pol.consume(forged, **good)
    p4 = pol.new_proposal(member="alice", errand="e1", goal="g", steps=[step()], approver="alice")
    pol.decide(p4.token, True)
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
        pol.decide(p.token, True)
    assert pol.by_token(p.token).state == "expired"


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
        pol2.decide(p.token, False)
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
        if pw != "lab-zoe-pw":
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


@pytest.fixture
def make_core(tmp_path, monkeypatch):
    (tmp_path / "psl.dat").write_text(PSL_TEXT)
    (tmp_path / "pw").write_text("lab-zoe-pw\n")

    def make(mode="key_file", items=None, client_uids=(ME,), phone_uids=(PHONE_UID,)):
        cfg = core.Config(
            server="https://vault.lab.test", email="zoe@lab.test", bw_path="/nonexistent/bw",
            appdata_dir=str(tmp_path), socket_path=str(tmp_path / "b.sock"),
            socket_group=grp.getgrgid(os.getgid()).gr_name, cdp_socket=str(tmp_path / "cdp.sock"),
            audit_path=str(tmp_path / "audit.jsonl"), psl_path=str(tmp_path / "psl.dat"),
            client_uids=set(client_uids), phone_uids=set(phone_uids),
            members={"alice": {"adult": True}, "bob": {"adult": True}, "kid": {"adult": False}},
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
        return c, bw
    return make


def run_to_capability(c, item="I1", member="alice", errand="e1"):
    p = c.dispatch("client", ME, {"verb": "propose", "member": member, "errand": errand, "goal": "renew the books",
                                  "steps": [{"step": "s1", "item": item}]})
    assert p["ok"], p
    token = p["path"].rsplit("/", 1)[1]
    card = c.dispatch("phone", PHONE_UID, {"verb": "card", "token": token})
    assert card["ok"] and card["steps"][0]["origin"] == "https://login.example.test"
    assert c.dispatch("phone", PHONE_UID, {"verb": "approve", "token": token, "decision": "approve"})["ok"]
    caps = c.dispatch("client", ME, {"verb": "capabilities", "proposal": p["proposal"]})
    return caps["capabilities"][0]["capability"]


def test_verb_list_is_the_documented_one():
    assert core.CLIENT_VERBS == {"labels", "propose", "capabilities", "login", "redeem", "lock", "status", "notices"}
    assert core.PHONE_VERBS == {"card", "approve", "otp", "unlock", "lock"}
    assert core.VERBS == core.CLIENT_VERBS | core.PHONE_VERBS
    handlers = {n[len("verb_"):] for n in dir(core.Core) if n.startswith("verb_")}
    assert handlers == set(core.VERBS)


def test_roles_and_unknown_verbs(make_core):
    c, _ = make_core()
    assert c.role_for(ME) == "client" and c.role_for(PHONE_UID) == "phone" and c.role_for(ME + 7) is None
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
    token = link.rsplit("/", 1)[1]
    assert c.dispatch("client", ME, {"verb": "unlock", "token": token, "password": "lab-zoe-pw"})["error"] == \
        "verb_not_allowed"
    assert c.dispatch("phone", PHONE_UID, {"verb": "unlock", "token": token, "password": "lab-zoe-pw"})["ok"]
    assert not c.locked
    assert c.dispatch("phone", PHONE_UID, {"verb": "unlock", "token": token, "password": "x"})["error"] == "not_locked"
    c.dispatch("client", ME, {"verb": "lock"})
    assert c.dispatch("phone", PHONE_UID, {"verb": "unlock", "token": token, "password": "lab-zoe-pw"})["error"] == \
        "unlock_bad_token"
    for _ in range(3):
        t = c.gate.current_link().rsplit("/", 1)[1]
        assert c.dispatch("phone", PHONE_UID, {"verb": "unlock", "token": t, "password": "wrong"})["error"] == \
            "unlock_failed"
    assert c.gate.current_link() is None
    assert c.dispatch("phone", PHONE_UID, {"verb": "unlock", "token": t, "password": "lab-zoe-pw"})["error"] == \
        "unlock_locked_out"
    assert c.locked
    assert "lab-zoe-pw" not in Path(c.cfg.audit_path).read_text()


def test_key_file_mode_has_no_unlock_verb(make_core):
    c, _ = make_core()
    assert not c.locked
    assert c.dispatch("phone", PHONE_UID, {"verb": "unlock", "token": "x" * 40, "password": "lab-zoe-pw"})["error"] \
        == "unlock_not_attended"


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
    assert phone.ROUTE.match("/v/approve/" + "a" * 43) and not phone.ROUTE.match("/v/approve/../x")


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
