"""V0: the vault and the official client alone (credential-vault.md section 16, phase V0). Runs inside the lab client
container as root, switching to the lab uids for every client call. Phases (driver.py calls them in order):

  seed        register a synthetic family, Zoe, the per-member "<member> + Zoe" organisations, the items, a
              family-sized bulk; tell the witness site which (hashed) credentials are valid
  cells       V0 measurements + cells V1c-V4c with the bw client alone (no broker)
  trash       list which backdated trash items survive (driver restarts Vaultwarden between calls)
  scan-db     (separate container, Vaultwarden's volume read-only) does the canary marker appear in the database?
  backdate    (separate container, Vaultwarden stopped) set deleted_at on the trash items
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, "/opt/cvlab/lab")
from bwseed import Api, Member  # noqa: E402
from common import (CA, MEASURE, SECRETS, Bw, cell, emit_result, https_json, load_state, save_state,  # noqa: E402
                    scan_paths, wait_for, write_secret)
from hosts import RIGHT, VAULT_URL, WITNESS  # noqa: E402
from items import FAMILY_BULK, LOOKALIKE_URLS, ZOE_ITEMS  # noqa: E402

MEMBERS = {"alice": True, "bob": True, "carol": True, "kid": False}   # name -> adult
ZOE_V0_APPDATA = "/var/lib/zoe-vault/bw-v0"


def sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def setup_dirs() -> None:
    for d, mode in (("/run/cvlab", 0o700), ("/run/cvlab-time", 0o1733), (str(SECRETS), 0o711)):
        os.makedirs(d, exist_ok=True)
        os.chmod(d, mode)


def member_bw(name: str, st: dict) -> Bw:
    appdata = f"/home/lab-member/bw/{name}"
    if not os.path.isdir(appdata):
        os.makedirs(appdata, mode=0o700)
        os.chown(appdata, 2008, 2008)
    b = Bw(name, "lab-member", appdata, st["emails"][name], str(SECRETS / f"{name}.pw"))
    return b


def zoe_bw(st: dict, appdata: str = ZOE_V0_APPDATA) -> Bw:
    if not os.path.isdir(appdata):
        os.makedirs(appdata, mode=0o700)
        os.chown(appdata, 2001, 2001)
    return Bw("zoe", "zoe-vault", appdata, st["emails"]["zoe"], str(SECRETS / "zoe.pw"))


# ---------------------------------------------------------------------------------------------------------------
def seed() -> None:
    setup_dirs()
    api = Api(VAULT_URL, CA)
    ctx = ssl.create_default_context(cafile=CA)
    up = wait_for(lambda: _alive(ctx), 90, 1.0)
    if not up:
        raise SystemExit("vaultwarden never came up")
    run = secrets.token_hex(4)
    marker = f"CANARY-{run}"
    st: dict = {"run": run, "marker": marker, "emails": {}, "orgs": {}, "items": {}, "family": {}}
    people = {}
    t0 = time.monotonic()
    for name in [*MEMBERS, "zoe"]:
        pw = secrets.token_urlsafe(18)
        st["emails"][name] = f"{name}@lab.test"
        write_secret(SECRETS / f"{name}.pw", pw, "zoe-vault" if name == "zoe" else "lab-member")
        m = Member(api, st["emails"][name], name, pw)
        m.register()
        m.login()
        people[name] = m
    MEASURE["seed_register_s"] = round(time.monotonic() - t0, 1)
    alice, bob, zoe = people["alice"], people["bob"], people["zoe"]
    a_id, a_key, a_zoe = alice.create_org("alice + Zoe", "Zoe can use")
    a_only = alice.create_collection(a_id, a_key, "alice only")
    h_id, h_key, h_zoe = alice.create_org("Household + Zoe", "Zoe can use")
    f_id, f_key, f_shared = alice.create_org("alice family", "Shared")
    b_id, b_key, b_zoe = bob.create_org("bob + Zoe", "Zoe can use")
    orgs = {"A": (a_id, a_key, a_zoe, alice), "B": (b_id, b_key, b_zoe, bob), "H": (h_id, h_key, h_zoe, alice)}
    for org_id, key, coll, owner in orgs.values():
        owner.invite(org_id, zoe, [coll], read_only=True)
        owner.confirm(org_id, key, zoe)
    alice.invite(f_id, bob, [f_shared], read_only=False)
    alice.confirm(f_id, f_key, bob)
    st["orgs"] = {"A": {"id": a_id, "zoe": a_zoe, "only": a_only, "owner": "alice"},
                  "B": {"id": b_id, "zoe": b_zoe, "owner": "bob"},
                  "H": {"id": h_id, "zoe": h_zoe, "owner": "alice"},
                  "F": {"id": f_id, "shared": f_shared, "owner": "alice"}}

    expect: dict[str, list] = {}
    for key, (org, uris, login_url, category, host, needs_otp) in ZOE_ITEMS.items():
        org_id, okey, coll, owner = orgs[org]
        username = f"u-{key}-{marker}-U"
        password = f"{marker}-P-{key}-{secrets.token_urlsafe(9)}"
        if category == "CANARY":
            category = f"{marker}-C-{key}"
        fields = [("zoe:category", category, 0), ("zoe:login_url", login_url, 0), ("pin", f"{marker}-F-{key}", 1)]
        item_id = owner.create_login(name=f"lab {key}", username=username, password=password, uris=uris,
                                     fields=fields, notes=f"{marker}-N-{key}", org=(org_id, okey), collections=[coll])
        entry = {"id": item_id, "org": org}
        exp = {"item": key, "user": sha(username), "pw": sha(password)}
        if needs_otp:
            code = "".join(secrets.choice("0123456789") for _ in range(8))
            entry["otp"] = code
            exp["otp"] = sha(code)
        expect.setdefault(host, []).append(exp)
        st["items"][key] = entry
    # Items Zoe must never see: alice's personal vault, alice's own collection in her sharing org, the family org.
    st["items"]["a_personal"] = {"id": alice.create_login(
        name="lab alice personal", username=f"u-ap-{marker}-U", password=f"{marker}-P-ap", uris=[(f"https://{RIGHT}",
                                                                                                    None)])}
    st["items"]["a_hidden"] = {"id": alice.create_login(
        name="lab alice only", username=f"u-ah-{marker}-U", password=f"{marker}-P-ah",
        uris=[(f"https://{RIGHT}", None)], org=(a_id, a_key), collections=[a_only])}
    st["items"]["fam"] = {"id": alice.create_login(
        name="lab family", username=f"u-fam-{marker}-U", password=f"{marker}-P-fam",
        uris=[(f"https://{RIGHT}", None)], org=(f_id, f_key), collections=[f_shared])}
    for t in ("t400", "t31", "t29"):
        st["items"][t] = {"id": alice.create_login(
            name=f"lab trash {t}", username=f"u-{t}-{marker}-U", password=f"{marker}-P-{t}",
            uris=[(f"https://{RIGHT}", None)], org=(a_id, a_key), collections=[a_zoe])}

    t1 = time.monotonic()
    bulk = 0
    for owner_name, n in FAMILY_BULK.items():
        for i in range(n):
            if owner_name == "F":
                alice.create_login(name=f"family site {i:04d}", username=f"fam{i}", password=secrets.token_urlsafe(12),
                                   uris=[(f"https://site-{i:04d}.example.net", None)], org=(f_id, f_key),
                                   collections=[f_shared], notes="synthetic")
            else:
                people[owner_name].create_login(name=f"{owner_name} site {i:04d}", username=f"{owner_name}{i}",
                                                password=secrets.token_urlsafe(12),
                                                uris=[(f"https://site-{i:04d}.example.net", None)], notes="synthetic")
            bulk += 1
    MEASURE["family_items"] = bulk + len(st["items"])
    MEASURE["seed_bulk_s"] = round(time.monotonic() - t1, 1)
    https_json("POST", f"https://{WITNESS}/__lab/expect", {"marker": marker, "expect": expect})
    save_state(st)
    print(f"seeded run={run} items={MEASURE['family_items']}", flush=True)
    emit_result("seed")


def _alive(ctx) -> bool:
    try:
        with urllib.request.urlopen(f"{VAULT_URL}/alive", context=ctx, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _same_item(a: dict | None, b: dict | None) -> bool:
    if not a or not b:
        return False
    la, lb = a.get("login") or {}, b.get("login") or {}
    return (all(la.get(k) == lb.get(k) for k in ("username", "password", "totp"))
            and [u.get("uri") for u in la.get("uris") or []] == [u.get("uri") for u in lb.get("uris") or []]
            and a.get("notes") == b.get("notes")
            and [(f.get("name"), f.get("value")) for f in a.get("fields") or []]
            == [(f.get("name"), f.get("value")) for f in b.get("fields") or []])


def full_unshare_for(member: Bw, item_id: str, *, skip_clone: bool = False) -> tuple[bool, str]:
    """Bitwarden's documented unshare, as the member: clone into the personal vault, check the clone field by field,
    and ONLY THEN delete the organisation-owned original (it goes to the org trash). `skip_clone` is the negative
    control: the pre-delete check must then fail and nothing may be deleted."""
    member.sync()
    orig = member.get(item_id)
    clone_id = None
    if orig and not skip_clone:
        obj = {k: orig.get(k) for k in ("type", "name", "notes", "fields", "login", "reprompt", "favorite")}
        obj.update({"organizationId": None, "collectionIds": None, "folderId": None})
        r = member.run("create", "item", stdin=member.encode(obj), label="create item")
        clone_id = json.loads(r.stdout)["id"] if r.returncode == 0 else None
    clone = member.get(clone_id) if clone_id else None
    if not (clone and clone.get("organizationId") is None and _same_item(clone, orig)):
        return False, "pre-delete check: no identical clone in the personal vault, original NOT deleted"
    if member.run("delete", "item", item_id, label="delete").returncode != 0:
        return False, "delete failed"
    return True, "cloned, verified field by field, then the original deleted to the org trash"


# ---------------------------------------------------------------------------------------------------------------
def post_login(username: str, password: str) -> bool:
    """Client alone: POST the credentials straight to the right site (no browser). Returns the site's verdict."""
    ctx = ssl.create_default_context(cafile=CA)
    data = urllib.parse.urlencode({"username": username, "password": password}).encode()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), NoRedirect)
    try:
        r = opener.open(urllib.request.Request(f"https://{RIGHT}/session", data=data, method="POST"), timeout=20)
        return r.status == 303
    except urllib.error.HTTPError as e:
        return e.code == 303


def cells() -> None:
    st = load_state()
    marker = st["marker"]
    ids = {k: v["id"] for k, v in st["items"].items()}
    A, B, F, H = (st["orgs"][k]["id"] for k in ("A", "B", "F", "H"))
    zoe = zoe_bw(st)
    alice = member_bw("alice", st)

    # -- M1 unattended unlock (--passwordfile), plus its negative control ------------------------------------------
    zoe.login()
    zoe.run("lock", label="lock")
    zoe.session = None
    ok_unlock = zoe.unlock()
    bad = SECRETS / "zoe-wrong.pw"
    write_secret(bad, secrets.token_urlsafe(18), "zoe-vault")
    wrong = Bw("zoe", "zoe-vault", ZOE_V0_APPDATA, st["emails"]["zoe"], str(bad))
    wrong.run("lock", label="lock")
    bad_unlock = wrong.unlock()
    os.unlink(bad)
    zoe.unlock()
    cell("M1 unattended unlock", ok_unlock and not bad_unlock,
         "bw login + unlock --passwordfile (0600 file, uid zoe-vault) return a session key with no prompt",
         nc=f"a wrong password file: unlock {'SUCCEEDED (bad)' if bad_unlock else 'refused, red as expected'}")

    # -- M2 organisation filtering and what Zoe can see at all -------------------------------------------------
    zoe.sync()
    all_items = {i["id"] for i in zoe.items()}
    by_org = {k: {i["id"] for i in zoe.items("--organizationid", st["orgs"][k]["id"])} for k in ("A", "B", "H")}
    f_items = zoe.items("--organizationid", F)
    expect_a = {v["id"] for k, v in st["items"].items() if v.get("org") == "A"} | {ids[t] for t in
                                                                                 ("t400", "t31", "t29")}
    expect_b = {v["id"] for v in st["items"].values() if v.get("org") == "B"}
    expect_h = {v["id"] for v in st["items"].values() if v.get("org") == "H"}
    invisible = {ids["a_personal"], ids["a_hidden"], ids["fam"]}
    ok = (by_org["A"] == expect_a and by_org["B"] == expect_b and by_org["H"] == expect_h and not f_items
          and all_items == expect_a | expect_b | expect_h and not (all_items & invisible))
    MEASURE["zoe_visible_items"] = len(all_items)
    cell("M2 organisation filtering", ok,
         f"--organizationid returns exactly that org's Zoe-can-use items (A={len(by_org['A'])}, B={len(by_org['B'])}, "
         f"H={len(by_org['H'])}); Zoe sees nothing of alice's personal vault, her 'alice only' collection or the "
         f"family org she is not in; total={len(all_items)}",
         nc="filter by the family org Zoe is not in: " + ("empty, as it must be" if not f_items else "LEAKED"))

    # -- V1c right origin, client alone ---------------------------------------------------------------------------
    hits = zoe.items("--url", f"https://{RIGHT}/login", "--organizationid", A)
    lib = next((i for i in hits if i["id"] == ids["lib"]), None)
    site_ok = bool(lib) and post_login(lib["login"]["username"], lib["login"]["password"])
    neg_hit = any(i["id"] == ids["v1neg"] for i in hits)
    cell("V1c right origin (client alone)", site_ok and not neg_hit,
         "bw list --url https://login.example.test/login returns the bound item; its credentials POSTed to the site "
         "are accepted by the site",
         nc="the item bound to another host (v1neg) is " + ("NOT returned for this URL: no fill, red as expected"
                                                            if not neg_hit else "returned (bad)"))

    # -- V2c look-alikes, client alone (URL level only; frames are broker-only) ---------------------------------
    matrix = {}
    for name, url in LOOKALIKE_URLS.items():
        got = {i["id"] for i in zoe.items("--url", url, "--organizationid", A)}
        matrix[name] = {"host_match_item": ids["c2host"] in got, "default_match_item": ids["lib"] in got,
                        "regex_item": ids["c2regex"] in got}
    MEASURE["v2c_bw_url_matrix"] = matrix
    # The client's matching compares HOSTS; the scheme is not part of it (measured: an http:// URL matches a host
    # item). So the client alone refuses look-alike hosts, and the http refusal is the broker's own (cell V2).
    host_leaks = [n for n, m in matrix.items() if m["host_match_item"]]
    host_clean = not [n for n in host_leaks if n != "http"]
    regex_leaks = [n for n, m in matrix.items() if m["regex_item"]]
    default_leaks = [n for n, m in matrix.items() if m["default_match_item"]]
    cell("V2c look-alike hosts refused (client alone, host match)", host_clean,
         "an item with match=host is returned for none of examp1e / punycode homograph / "
         f"login.example.test.evil.test / evil.test. FINDING: bw ignores the scheme: returned for {host_leaks or 'none'}; "
         f"the default (base-domain) match returns the item for {default_leaks or 'none'}. The broker refuses http "
         "and treats the default as exact host (V2)",
         nc=f"an item with a regex URI: returned for {regex_leaks or 'none'} "
            + ("(red as expected: regex is a foot-gun, the broker refuses it)" if regex_leaks else "(NOT red)"))

    # -- V3c canary never visible at rest (client alone) ---------------------------------------------------------
    stderr_hits = [s for s in zoe.stderr_log + alice.stderr_log if marker in s]
    disk_hits = scan_paths([ZOE_V0_APPDATA], [marker])
    # negative control: a decrypted copy of one item left in the data dir (what a careless cache would do)
    export = os.path.join(ZOE_V0_APPDATA, "nc-plain.json")
    Path(export).write_text(zoe.run("get", "item", ids["lib"], label="get item").stdout)
    nc_hits = scan_paths([ZOE_V0_APPDATA], [marker])
    if os.path.exists(export):
        os.unlink(export)
    cell("V3c canary not at rest (client alone)", not stderr_hits and not disk_hits,
         "the run's canary marker is in no bw stderr and nowhere in Zoe's bw data directory (ciphertext only); the "
         "Vaultwarden database scan is cell V3c-db (driver)",
         nc=f"a decrypted item copy written into the data dir: scan finds {len(nc_hits)} file(s) "
            + ("(red as expected)" if nc_hits else "(NOT red)"))

    # -- V4c (a) unassign from 'Zoe can use': next sync no longer returns it ----------------------------------------
    alice.login()
    alice.sync()
    r = alice.run("edit", "item-collections", ids["rev_a"], stdin=alice.encode([]), label="edit collections")
    unassigned_ok = r.returncode == 0
    stale = {i["id"] for i in zoe.items("--organizationid", A)}        # no sync: the cache
    zoe.sync()
    fresh = {i["id"] for i in zoe.items("--organizationid", A)}
    alice.sync()
    alice_still = alice.get(ids["rev_a"]) is not None
    r2 = alice.run("edit", "item-collections", ids["rev_c"], stdin=alice.encode([st["orgs"]["A"]["only"]]),
                   label="edit collections")
    zoe.sync()
    moved_gone = ids["rev_c"] not in {i["id"] for i in zoe.items("--organizationid", A)}
    MEASURE["unassigned_item_visible_to_can_view_member"] = ids["rev_a"] in fresh
    MEASURE["moved_to_other_collection_visible"] = not moved_gone
    cell("V4c(a) unassign stops Zoe, member keeps it", unassigned_ok and ids["rev_a"] not in fresh and alice_still
         and r2.returncode == 0 and moved_gone,
         "alice leaves the item UNASSIGNED (no collection): after Zoe's next sync it is gone from her list; alice still "
         "has it; moving it to 'alice only' instead behaves the same",
         nc="Zoe reads her cache without syncing: the item is " + ("STILL listed (red as expected: sync before use "
                                                                    "is required)" if ids["rev_a"] in stale
                                                                    else "gone (control did not go red)"))

    # -- V4c (b) the full unshare: clone, verify, then delete; restorable from the org trash ----------------------
    done, why = full_unshare_for(alice, ids["rev_b"])
    zoe.sync()
    gone = ids["rev_b"] not in {i["id"] for i in zoe.items("--organizationid", A)}
    alice.sync()
    in_trash = ids["rev_b"] in {i["id"] for i in alice.items("--trash", "--organizationid", A)}
    restored = alice.run("restore", "item", ids["rev_b"], label="restore").returncode == 0
    alice.sync()
    back = alice.get(ids["rev_b"]) is not None
    # negative control: skip the clone -> the pre-delete assertion must go red and must NOT delete
    nc_done, nc_why = full_unshare_for(alice, ids["rev_b"], skip_clone=True)
    alice.sync()
    still_there = alice.get(ids["rev_b"]) is not None and ids["rev_b"] not in {
        i["id"] for i in alice.items("--trash", "--organizationid", A)}
    cell("V4c(b) clone, verify, delete; restorable", done and gone and in_trash and restored and back,
         f"{why}; Zoe's next sync no longer returns it; alice finds it in the org trash and restores it",
         nc=f"clone step skipped: {nc_why}" + (" (red as expected, original kept)" if not nc_done and still_there
                                              else " (NOT red, or the original was lost)"))

    # -- trash items for the retention measurement: alice deletes three (driver backdates and restarts) -----------
    for t in ("t400", "t31", "t29"):
        alice.run("delete", "item", ids[t], label="delete")

    # -- M3 sync, M4 RAM and time per call ---------------------------------------------------------------------
    calls = zoe.calls + alice.calls
    per = {}
    for c in calls:
        if "rss_mb" in c:
            per.setdefault(c["cmd"], []).append(c)
    MEASURE["bw_per_call"] = {k: {"n": len(v), "max_rss_mb": max(x["rss_mb"] for x in v),
                                  "median_s": sorted(x["s"] for x in v)[len(v) // 2]} for k, v in per.items()}
    sync_calls = per.get("sync", [])
    cell("M3 sync and RAM per call measured", bool(sync_calls) and all(c["rc"] == 0 for c in sync_calls),
         "; ".join(f"{k}: max {v['max_rss_mb']} MB, median {v['median_s']} s"
                   for k, v in sorted(MEASURE["bw_per_call"].items())))
    st["zoe_v0_done"] = True
    save_state(st)
    emit_result("v0")


def trash() -> None:
    st = load_state()
    alice = member_bw("alice", st)
    alice.unlock()
    alice.sync()
    trash_ids = {i["id"] for i in alice.items("--trash", "--organizationid", st["orgs"]["A"]["id"])}
    out = {t: st["items"][t]["id"] in trash_ids for t in ("t400", "t31", "t29")}
    print("TRASH " + json.dumps(out), flush=True)


def scan_db(data_dir: str, marker: str) -> None:
    hits = scan_paths([data_dir], [marker])
    print("SCANDB " + json.dumps({"hits": hits}), flush=True)


def backdate(db: str, spec: str) -> None:
    """spec: id=days,id=days. Vaultwarden is stopped while this runs."""
    con = sqlite3.connect(db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(ciphers)")]
    if "deleted_at" not in cols:
        raise SystemExit("ciphers.deleted_at missing: " + ",".join(cols))
    for part in spec.split(","):
        cid, days = part.split("=")
        con.execute("UPDATE ciphers SET deleted_at = datetime('now', ?) WHERE uuid = ?", (f"-{int(days)} days", cid))
    con.commit()
    rows = con.execute("SELECT uuid, deleted_at FROM ciphers WHERE deleted_at IS NOT NULL").fetchall()
    con.close()
    print("BACKDATED " + json.dumps(rows), flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "seed":
        seed()
    elif cmd == "cells":
        cells()
    elif cmd == "trash":
        trash()
    elif cmd == "scan-db":
        scan_db(sys.argv[2], os.environ["CVLAB_MARKER"])
    elif cmd == "backdate":
        backdate(sys.argv[2], sys.argv[3])
    elif cmd == "ids":
        st = load_state()
        print("IDS " + json.dumps({k: st["items"][k]["id"] for k in ("t400", "t31", "t29")}), flush=True)
    else:
        raise SystemExit("unknown phase")
