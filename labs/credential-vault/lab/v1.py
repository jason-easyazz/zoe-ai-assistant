"""V1: the zoe-vaultd broker in the lab (credential-vault.md section 16, phase V1). Gate: cells V1-V9, V13, V17, V18,
each with its negative control. Runs inside the lab client container as root and starts every unit as its own uid:

  zoe-vault-browser  python3 -m zoe_vaultd browser   (the login browser, Chromium over a private DevTools pipe)
  zoe-vault          python3 -m zoe_vaultd core      (the broker)
  zoe-vault-phone    python3 -m zoe_vaultd phone     (approve / code / unlock pages)
  zoe-svc            stands in for zoe-data: every request it sends and every reply it gets is kept (V3, V7)
  lab-nginx          stands in for nginx in front of the phone socket (the member's phone)

A negative control restarts one unit through labcore.py with ONE protection monkeypatched off.
"""

from __future__ import annotations

import json
import os
import pwd
import grp
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, "/opt/cvlab/lab")
from common import (CA, MEASURE, RESULTS, Bw, as_user, cell, emit_result, load_state, scan_paths,  # noqa: E402
                    spawn_as, wait_for, witness_records, witness_reset)
from hosts import RIGHT, VAULT_URL  # noqa: E402

LAB = "/opt/cvlab/lab"
CORE_SOCK = "/run/zoe-vault/broker.sock"
CDP_SOCK = "/run/zoe-vault-browser/cdp.sock"
PHONE_SOCK = "/run/zoe-vault-phone/phone.sock"
CONFIG = "/etc/zoe-vaultd/config.json"
AUDIT = "/var/lib/zoe-vault/audit.jsonl"
LOGS = Path("/run/cvlab-logs")
DOCUMENTED_CLIENT = {"labels", "propose", "capabilities", "login", "redeem", "lock", "status", "notices"}
DOCUMENTED_PHONE = {"card", "approve", "otp", "unlock", "lock"}
PROBE_VERBS = ["get", "reveal", "item", "password", "export", "dump", "debug", "secret", "show", "fill", "totp",
               "notes", "fields", "username", "cookies", "help", "verbs"]

ZOE_SVC = []            # everything zoe-data's uid sent and received (V3, V7)
PHONE_PAGES = []        # every page the phone unit served
ST: dict = {}
IDS: dict = {}
LOGIN_CALLS = [0]


# ---------------------------------------------------------------------------------------------------------------
def uid(name: str) -> int:
    return pwd.getpwnam(name).pw_uid


def gid(name: str) -> int:
    return grp.getgrnam(name).gr_gid


def mkdir(path: str, owner: str, group: str, mode: int) -> None:
    os.makedirs(path, exist_ok=True)
    os.chown(path, uid(owner), gid(group))
    os.chmod(path, mode)


def setup() -> None:
    mkdir("/run/zoe-vault", "zoe-vault", "zoe-vault-clients", 0o750)
    mkdir("/run/zoe-vault-browser", "zoe-vault-browser", "zoe-vault-cdp", 0o750)
    mkdir("/run/zoe-vault-browser/profiles", "zoe-vault-browser", "zoe-vault-browser", 0o700)
    mkdir("/run/zoe-vault-phone", "zoe-vault-phone", "zoe-vault-web", 0o750)
    mkdir("/var/lib/zoe-vault/bw", "zoe-vault", "zoe-vault", 0o700)
    LOGS.mkdir(mode=0o700, exist_ok=True)
    os.makedirs("/etc/zoe-vaultd", exist_ok=True)
    nss = "/home/zoe-vault-browser/.pki/nssdb"
    if not os.path.exists(nss + "/cert9.db"):
        as_user("zoe-vault-browser", ["mkdir", "-p", nss])
        r1 = as_user("zoe-vault-browser", ["certutil", "-N", "--empty-password", "-d", "sql:" + nss])
        r2 = as_user("zoe-vault-browser", ["certutil", "-A", "-d", "sql:" + nss, "-t", "C,,", "-n", "cvlab-ca",
                                           "-i", CA])
        if r1.returncode or r2.returncode:
            raise SystemExit("nssdb setup failed: " + r1.stderr + r2.stderr)


def write_config(**over) -> None:
    cfg = {
        "server": VAULT_URL, "email": ST["emails"]["zoe"], "bw_path": "/usr/local/bin/bw",
        "appdata_dir": "/var/lib/zoe-vault/bw", "extra_ca": CA,
        "socket": {"path": CORE_SOCK, "group": "zoe-vault-clients"}, "cdp_socket": CDP_SOCK,
        "audit_path": AUDIT, "psl_path": "/usr/share/publicsuffix/public_suffix_list.dat",
        "client_uids": [uid("zoe-svc")], "phone_uids": [uid("zoe-vault-phone")],
        "members": {"alice": {"adult": True}, "bob": {"adult": True}, "carol": {"adult": True},
                    "kid": {"adult": False}},
        "orgs": [{"id": ST["orgs"]["A"]["id"], "kind": "member", "owner": "alice", "collection": ST["orgs"]["A"]["zoe"]},
                 {"id": ST["orgs"]["B"]["id"], "kind": "member", "owner": "bob", "collection": ST["orgs"]["B"]["zoe"]},
                 {"id": ST["orgs"]["H"]["id"], "kind": "household", "collection": ST["orgs"]["H"]["zoe"]}],
        "unlock": {"mode": "key_file", "password_file": "/run/cvlab-secrets/zoe.pw"},
        "lab_profile": True, "mem_floor_mb": 600,
        "rate_limits": {"per_item": 1000, "per_member": 1000, "household": 1000},
    }
    for k, v in over.items():
        cfg[k] = v
    Path(CONFIG).write_text(json.dumps(cfg, indent=1))
    os.chmod(CONFIG, 0o644)


class Stack:
    def __init__(self) -> None:
        self.procs: dict[str, subprocess.Popen] = {}
        self.run = 0

    def _start(self, name: str, user: str, argv: list[str], patch: str | None, sock: str) -> None:
        self.run += 1
        cmd = ["python3", f"{LAB}/labcore.py"] + (["--patch", patch] if patch else []) + ["--", *argv]
        log = str(LOGS / f"{name}-{self.run:02d}{'-' + patch if patch else ''}.log")
        try:
            os.unlink(sock)
        except FileNotFoundError:
            pass
        self.procs[name] = spawn_as(user, cmd, log=log, env={"HOME": pwd.getpwnam(user).pw_dir})
        if not wait_for(lambda: os.path.exists(sock), 60, 0.2):
            raise SystemExit(f"{name} did not start; see {log}")

    def start_browser(self, patch: str | None = None) -> None:
        self.stop("browser")
        self._start("browser", "zoe-vault-browser",
                    ["browser", "--socket", CDP_SOCK, "--group", "zoe-vault-cdp", "--profile-root",
                     "/run/zoe-vault-browser/profiles", "--allowed-uid", str(uid("zoe-vault")),
                     # Docker's seccomp profile blocks Chromium's own sandbox here; the unit runs it with its own uid
                     # instead. HTTPS-Upgrades is off so the http:// look-alike really commits as http (cell V2).
                     "--extra-flag=--no-sandbox",
                     "--extra-flag=--disable-features=Translate,OptimizationHints,MediaRouter,"
                     "AutofillServerCommunication,PasswordManagerOnboarding,HttpsUpgrades"], patch, CDP_SOCK)

    def start_core(self, patch: str | None = None, **config) -> None:
        self.stop("core")
        write_config(**config)
        self._start("core", "zoe-vault", ["core", "--config", CONFIG], patch, CORE_SOCK)

    def start_phone(self) -> None:
        self.stop("phone")
        self._start("phone", "zoe-vault-phone", ["phone", "--socket", PHONE_SOCK, "--group", "zoe-vault-web",
                                                 "--core-socket", CORE_SOCK], None, PHONE_SOCK)

    def stop(self, name: str | None = None) -> None:
        for n in ([name] if name else list(self.procs)):
            p = self.procs.pop(n, None)
            if p is None:
                continue
            try:
                os.killpg(p.pid, 15)
            except OSError:
                pass
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, 9)
                p.wait(timeout=5)


STACK = Stack()


def svc(req: dict, user: str = "zoe-svc") -> dict:
    raw = json.dumps(req)
    r = as_user(user, ["python3", f"{LAB}/client.py", CORE_SOCK, raw], timeout=900)
    if user == "zoe-svc":
        ZOE_SVC.append(raw)
        ZOE_SVC.append(r.stdout)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": "client_crash", "detail": r.stderr[-300:]}


def phone(method: str, path: str, body: str | None = None) -> dict:
    args = ["python3", f"{LAB}/phonecli.py", PHONE_SOCK, method, path] + (["-"] if body is not None else [])
    r = as_user("lab-nginx", args, input=body, timeout=900)
    PHONE_PAGES.append(r.stdout)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"status": 0, "body": r.stderr[-200:]}


def wait_ready(locked_ok: bool = False) -> dict:
    st = wait_for(lambda: (lambda s: s if s.get("ok") and (locked_ok or not s.get("locked")) else None)(
        svc({"verb": "status"})), 120, 1.0)
    if not st:
        raise SystemExit("core never became ready")
    return st


def propose(key: str, *, member: str = "alice", errand: str | None = None, step: str = "s1") -> tuple[dict, str]:
    errand = errand or f"e-{key}-{secrets.token_hex(3)}"
    p = svc({"verb": "propose", "member": member, "errand": errand, "goal": f"lab errand for {key}",
             "steps": [{"step": step, "item": IDS[key]}]})
    return p, errand


def approve(p: dict, decision: str = "approve") -> dict:
    page = phone("GET", p["path"])
    res = phone("POST", p["path"], f"decision={decision}")
    return {"card": page, "post": res}


def capability(key: str, **kw) -> dict:
    p, errand = propose(key, **kw)
    if not p.get("ok"):
        return {"stage": "propose", "error": p.get("error"), "errand": errand}
    approve(p)
    c = svc({"verb": "capabilities", "proposal": p["proposal"]})
    if not c.get("ok"):
        return {"stage": "capabilities", "error": c.get("error"), "errand": errand}
    return {"stage": "ok", "cap": c["capabilities"][0]["capability"], "errand": errand, "proposal": p["proposal"]}


def login(key: str, cap: str, errand: str, *, member: str = "alice", step: str = "s1") -> dict:
    LOGIN_CALLS[0] += 1
    return svc({"verb": "login", "capability": cap, "member": member, "errand": errand, "step": step,
                "item": IDS[key]})


def sign_in(key: str, *, otp_code: str | None = None, **kw) -> dict:
    c = capability(key, **kw)
    if c["stage"] != "ok":
        return {"ok": False, "stage": c["stage"], "error": c["error"]}
    stop = threading.Event()
    if otp_code:
        def feed():
            while not stop.is_set():
                links = svc({"verb": "notices"}).get("links") or []
                for link in links:
                    if link["kind"] == "otp":
                        phone("POST", link["path"], f"code={otp_code}")
                        return
                time.sleep(0.5)
        threading.Thread(target=feed, daemon=True).start()
    res = login(key, c["cap"], c["errand"], member=kw.get("member", "alice"), step=kw.get("step", "s1"))
    stop.set()
    res["stage"] = "login"
    return res


def leaks(records: list[dict]) -> list[dict]:
    """Witness records that carried the canary somewhere other than an accepted sign-in on the item's own site."""
    return [r for r in records if r.get("canary") and not r.get("login_ok")]


def fills(records: list[dict]) -> list[dict]:
    return [r for r in records if r.get("canary")]


# ---------------------------------------------------------------------------------------------------------------
V2_ITEMS = ["lk_examp1e", "lk_homograph", "lk_suffixed", "lk_priv", "lk_priv2", "lk_http", "lk_framed", "lk_blank",
            "lk_srcdoc", "lk_data", "lk_blob", "lk_sandbox", "lk_redirect", "fa_action", "fa_button", "fa_get"]
V2_NC = {"substring": ["lk_suffixed"], "nopsl": ["lk_priv2"], "inherit": ["lk_blank", "lk_srcdoc"]}
V13_ITEMS = ["race_nav", "race_swap", "race_oopif"]


def battery(items: list[str]) -> dict:
    out = {}
    for key in items:
        witness_reset()
        res = sign_in(key)
        recs = witness_records()
        st = os.statvfs("/tmp")
        out[key] = {"ok": bool(res.get("ok")), "stage": res.get("stage"), "error": res.get("error"),
                    "witness_canary": len(fills(recs)), "leaks": len(leaks(recs)),
                    # leftovers after the session: /tmp use and processes still owned by the browser uid
                    "tmp_used_mb": (st.f_blocks - st.f_bfree) * st.f_frsize // (1 << 20),
                    "browser_uid_procs": sum(1 for d in Path("/proc").iterdir() if d.name.isdigit()
                                             and _owner(d) == uid("zoe-vault-browser")) - 1}
    return out


def run(groups: str) -> None:
    global ST, IDS
    ST = load_state()
    IDS = {k: v["id"] for k, v in ST["items"].items()}
    marker = ST["marker"]
    otp_codes = [v["otp"] for v in ST["items"].values() if v.get("otp")]
    setup()
    if os.path.exists(AUDIT):
        os.unlink(AUDIT)
    STACK.start_browser()
    STACK.start_core()
    STACK.start_phone()
    status = wait_ready()
    MEASURE["core_unlock_mode"] = status.get("unlock_mode")
    from v0 import member_bw
    member_bw("alice", ST)                 # makes alice's private bw data dir when V0 did not run in this piece
    alice = Bw("alice", "lab-member", "/home/lab-member/bw/alice", ST["emails"]["alice"],
               "/run/cvlab-secrets/alice.pw")
    if not alice.unlock():
        alice.login()

    if "A" in groups:
        # V5 starts first (a real 120 s wait, overlapped with V1 and V2) and is checked before the first core restart
        v5_prop, v5_errand = propose("bank", errand="e-v5-timeout")
        v5_started = time.monotonic()
        v5_card_open = phone("GET", v5_prop["path"])["status"] == 200 if v5_prop.get("ok") else False

    if "A" in groups:
        # -- V1 right origin -----------------------------------------------------------------------------------------
        witness_reset()
        r_lib = sign_in("lib")
        recs = witness_records()
        site_ok = [x for x in recs if x.get("login_ok") and x.get("item") == "lib" and x["host"] == RIGHT]
        cookies = svc({"verb": "redeem", "handle": r_lib.get("handle", "")}) if r_lib.get("ok") else {}
        cookie_hosts = sorted({c["domain"] for c in cookies.get("cookies") or []})
        r_base = sign_in("basepos")
        r_widget = sign_in("widget")
        r_house = sign_in("house")
        r_neg = sign_in("v1neg")
        cell("V1 right origin", bool(r_lib.get("ok") and site_ok and cookie_hosts == [RIGHT] and r_base.get("ok")
                                     and r_widget.get("ok") and r_house.get("ok")),
             f"the broker signs in at https://{RIGHT} in its own browser and the site accepts exactly that item's "
             f"credentials; the redeemed handle holds cookies for {cookie_hosts} only; a base-domain item "
             f"(www.example.org -> login.example.org): {r_base.get('ok')}; a hosted widget the item lists "
             f"(shop.example.test framing widget.test): {r_widget.get('ok')}; a Household item: {r_house.get('ok')}",
             nc=f"item bound to another host: {r_neg.get('stage')}={r_neg.get('error')}, no fill"
                + (" (red as expected)" if not r_neg.get("ok") else " (NOT red)"))

    if "B" in groups:
        # -- V2 look-alikes, frames, form targets ------------------------------------------------------------------
        v2 = battery(V2_ITEMS)
        MEASURE["v2"] = v2
        refused_all = all(not v["ok"] and v["leaks"] == 0 and v["witness_canary"] == 0 for v in v2.values())

    if "A" in groups:
        # -- V5 positive half: checked BEFORE the first core restart (the proposal lives only in the core's memory) --
        wait_s = 121 - (time.monotonic() - v5_started)
        if wait_s > 0:
            time.sleep(wait_s)
        caps5 = svc({"verb": "capabilities", "proposal": v5_prop.get("proposal", "")})
        card5 = phone("GET", v5_prop.get("path", "/v/approve/" + "x" * 30))
        v5_elapsed = int(time.monotonic() - v5_started)

    if "B" in groups:
        nc_out = {}
        for patch, keys in V2_NC.items():
            STACK.start_core(patch=patch)
            wait_ready()
            res = battery(keys)
            nc_out[patch] = [k for k, v in res.items() if v["ok"] or v["witness_canary"]]
            MEASURE.setdefault("v2_nc_detail", {})[patch] = res
        STACK.start_core()
        wait_ready()
        MEASURE["v2_nc"] = nc_out
        cell("V2 look-alike refused", refused_all,
             "no fill and no canary at any host for: " + ", ".join(f"{k}={v['error']}" for k, v in v2.items()),
             nc="; ".join(f"{p}: filled {v or 'NOTHING'}" for p, v in nc_out.items())
                + (" (red as expected)" if all(nc_out.values()) else " (a relaxation did NOT go red)"))

    if "C" in groups:
        # -- V3 data: an OTP sign-in through the phone code page, and the injected 'repeat the password' page -------
        r_otp = sign_in("otp", otp_code=ST["items"]["otp"]["otp"])
        r_inject = sign_in("inject")

    if "C" in groups:
        # -- V4 revoked share (broker) -------------------------------------------------------------------------------
        c_d = capability("rev_d")
        alice.sync()
        alice.run("edit", "item-collections", IDS["rev_d"], stdin=alice.encode([]), label="edit collections")
        r_d = login("rev_d", c_d["cap"], c_d["errand"])
        alice.sync()
        alice_keeps = alice.get(IDS["rev_d"]) is not None
        c_f = capability("rev_f")
        from v0 import full_unshare_for  # the same clone-verify-delete as V0
        unshared, why = full_unshare_for(alice, IDS["rev_f"])
        r_f = login("rev_f", c_f["cap"], c_f["errand"])
        alice.sync()
        in_trash = IDS["rev_f"] in {i["id"] for i in alice.items("--trash", "--organizationid", ST["orgs"]["A"]["id"])}
        restored = alice.run("restore", "item", IDS["rev_f"], label="restore").returncode == 0
        # negative control: serve from the cache without a sync
        STACK.start_core(patch="nosync")
        wait_ready()
        c_e = capability("rev_e")
        alice.sync()
        alice.run("edit", "item-collections", IDS["rev_e"], stdin=alice.encode([]), label="edit collections")
        witness_reset()
        r_e = login("rev_e", c_e["cap"], c_e["errand"])
        STACK.start_core()
        wait_ready()
        nc_unshare, nc_why = full_unshare_for(alice, IDS["rev_f"], skip_clone=True)
        cell("V4 revoked share stops, no copy lost",
             r_d.get("error") == "item_unavailable" and alice_keeps and unshared and r_f.get("error") == "item_unavailable"
             and in_trash and restored,
             f"(a) a capability minted BEFORE alice unassigned the item is refused at use ({r_d.get('error')}): the broker "
             f"syncs first; alice still has the item. (b) {why}; the next login is refused ({r_f.get('error')}); the "
             f"original is in the org trash and restores",
             nc=f"(a) cache without sync: the unassigned item {'SIGNED IN (red as expected)' if r_e.get('ok') else 'was still refused (NOT red)'}; "
                f"(b) clone skipped: {nc_why}" + (" (red as expected)" if not nc_unshare else " (NOT red)"))

    if "D" in groups:
        # -- V6 capability bound to the payload -------------------------------------------------------------------
        checks = {}
        c = capability("cap_a")
        checks["other item"] = login("cap_b", c["cap"], c["errand"]).get("error")
        c = capability("cap_a")
        checks["other errand"] = login("cap_a", c["cap"], c["errand"] + "-x").get("error")
        c = capability("cap_a")
        checks["other member"] = login("cap_a", c["cap"], c["errand"], member="bob").get("error")
        c = capability("cap_a")
        checks["other step"] = login("cap_a", c["cap"], c["errand"], step="s9").get("error")
        c = capability("cap_a")
        checks["other operation (as an OTP token)"] = phone("POST", "/v/code/" + c["cap"], "code=123456")["status"]
        checks["other operation (as a redeem handle)"] = svc({"verb": "redeem", "handle": c["cap"]}).get("error")
        c = capability("cap_a")
        first = login("cap_a", c["cap"], c["errand"])
        checks["second use"] = login("cap_a", c["cap"], c["errand"]).get("error")
        c = capability("cap_c")
        alice.sync()
        item_c = alice.get(IDS["cap_c"])
        for f in item_c.get("fields") or []:
            if f.get("name") == "zoe:login_url":
                f["value"] = f"https://{RIGHT}:8443/login"
        item_c["login"]["uris"].append({"uri": f"https://{RIGHT}:8443", "match": 1})
        alice.run("edit", "item", IDS["cap_c"], stdin=alice.encode(item_c), label="edit item")
        checks["other origin (item moved after approval)"] = login("cap_c", c["cap"], c["errand"]).get("error")
        p, errand = propose("cap_a")
        approve(p)
        checks["bare item id"] = login("cap_a", IDS["cap_a"], errand).get("error")
        checks["proposal id"] = login("cap_a", p["proposal"], errand).get("error")
        LOGIN_CALLS[0] += 1
        checks["'approved' flag"] = svc({"verb": "login", "approved": True, "member": "alice", "errand": errand,
                                         "step": "s1", "item": IDS["cap_a"]}).get("error")
        STACK.start_core(capability_ttl_s=3)
        wait_ready()
        c = capability("cap_a")
        time.sleep(4)
        checks["after expiry (ttl 3 s for this check)"] = login("cap_a", c["cap"], c["errand"]).get("error")
        STACK.start_core(patch="trust_approval_id")
        wait_ready()
        p, errand = propose("cap_a")
        approve(p)
        forged = login("cap_a", p["proposal"], errand)
        STACK.start_core()
        wait_ready()
        refused = all(v not in (None, 200) for v in checks.values())
        MEASURE["v6"] = checks
        cell("V6 capability bound to the payload", bool(first.get("ok")) and refused,
             "the right tuple signs in once; refused: " + "; ".join(f"{k} -> {v}" for k, v in checks.items()),
             nc="accept a caller-asserted approval (proposal) id: the forged tuple "
                + ("SIGNED IN (red as expected)" if forged.get("ok") else f"was refused ({forged.get('error')}, NOT red)"))

    if "E" in groups:
        # -- V7 no reveal verb -------------------------------------------------------------------------------------
        sys.path.insert(0, "/opt/zoe-vaultd")
        from zoe_vaultd import core as corem
        probe = {v: svc({"verb": v, "item": IDS["lib"]}).get("error") for v in PROBE_VERBS}
        phone_from_client = {v: svc({"verb": v}).get("error") for v in sorted(DOCUMENTED_PHONE - DOCUMENTED_CLIENT)}
        verbs_ok = set(corem.CLIENT_VERBS) == DOCUMENTED_CLIENT and set(corem.PHONE_VERBS) == DOCUMENTED_PHONE
        probes_ok = all(e == "unknown_verb" for e in probe.values()) and all(
            e == "verb_not_allowed" for e in phone_from_client.values())
        content_hits = [x for x in ZOE_SVC if marker in x]
        STACK.start_core(patch="debug_get")
        wait_ready()
        nc_get = svc({"verb": "get", "item": IDS["lib"]})
        nc_hit = marker in json.dumps(nc_get)
        ZOE_SVC.pop()
        ZOE_SVC.pop()   # the NC's deliberate leak is not part of V3's transcript
        STACK.start_core()
        wait_ready()
        cell("V7 no reveal verb", verbs_ok and probes_ok and not content_hits,
             f"client verbs = {sorted(DOCUMENTED_CLIENT)}, phone verbs = {sorted(DOCUMENTED_PHONE)}; {len(PROBE_VERBS)} "
             f"reveal-style verbs -> unknown_verb; phone verbs from zoe-data's uid -> verb_not_allowed; no item content in "
             f"any of {len(ZOE_SVC) // 2} replies to zoe-data",
             nc="a debug `get` verb added: " + ("item content returned (red as expected)" if nc_hit else "NOT red"))

    if "E" in groups:
        # -- V8 socket peer check ----------------------------------------------------------------------------------
        good = svc({"verb": "labels", "member": "alice"})
        grouped = svc({"verb": "labels", "member": "alice"}, user="lab-grouped")
        intruder = svc({"verb": "labels", "member": "alice"}, user="lab-intruder")
        STACK.start_core(patch="nopeer")
        wait_ready()
        nc_grouped = svc({"verb": "labels", "member": "alice"}, user="lab-grouped")
        STACK.start_core()
        wait_ready()
        cell("V8 socket peer check", bool(good.get("ok")) and grouped.get("error") == "peer_refused"
             and str(intruder.get("error", "")).startswith("connect:"),
             f"zoe-data's uid gets labels ({len(good.get('items') or [])} items); a uid IN the socket's group but not on "
             f"the SO_PEERCRED allow-list -> {grouped.get('error')}; a uid outside the group -> {intruder.get('error')}",
             nc="no SO_PEERCRED check: the grouped uid " + ("GOT LABELS (red as expected)" if nc_grouped.get("ok")
                                                             else f"was still refused ({nc_grouped.get('error')})"))

    if "F" in groups:
        # -- V13 navigation race ------------------------------------------------------------------------------------
        v13 = battery(V13_ITEMS)
        STACK.start_core(patch="norecheck")
        wait_ready()
        v13_nc = battery(V13_ITEMS)
        STACK.start_core()
        wait_ready()
        MEASURE["v13"], MEASURE["v13_nc"] = v13, v13_nc
        # red only when the value really went somewhere it must not: a canary at a site that did not accept a sign-in
        # (the swapped document's text box beacons to evil.test), or the broker reporting a sign-in on a race page
        nc_red = [k for k, v in v13_nc.items() if v["ok"] or v["leaks"]]
        cell("V13 navigation race", all(v["error"] == "navigated" and not v["witness_canary"] for v in v13.values()),
             "a focus handler that navigates the frame / document.open()s a new document with a text box posting to "
             "evil.test / navigates an out-of-process widget cross-site: " + ", ".join(
                 f"{k}={v['error']}" for k, v in v13.items()) + "; nothing inserted, nothing at evil.test",
             nc="no binding and no re-check before insert: " + ", ".join(
                 f"{k}: {'filled' if v['ok'] else v['error']}, canary at a site {v['witness_canary']}x"
                 for k, v in v13_nc.items()) + (" (red as expected)" if nc_red else " (NOT red)"))

    if "F" in groups:
        # -- V17 no DevTools for zoe-data ---------------------------------------------------------------------------
        att = {}

        def attack(seconds: float, store: str, shared: str | None = None) -> None:
            r = as_user("zoe-svc", ["python3", f"{LAB}/attacker.py", marker, CDP_SOCK, "9222", str(seconds)]
                        + ([shared] if shared else []), timeout=seconds + 30)
            att[store] = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"error": r.stderr}

        t = threading.Thread(target=attack, args=(12.0, "pos"))
        t.start()
        time.sleep(0.5)
        r_hold = sign_in("hold")
        t.join()
        handle_cookies = svc({"verb": "redeem", "handle": r_hold.get("handle", "")})
        cookie_clean = marker not in json.dumps(handle_cookies)
        os.chmod("/run/zoe-vault-browser", 0o755)
        STACK.start_browser(patch="group_widened")
        attack(3.0, "group")
        STACK.start_browser(patch="shared_devtools")
        t = threading.Thread(target=attack, args=(25.0, "nc", "/run/zoe-vault-browser/shared.sock"))
        t.start()
        time.sleep(0.5)
        r_hold_nc = sign_in("hold")
        t.join()
        att["nc_login"] = {"ok": r_hold_nc.get("ok"), "error": r_hold_nc.get("error")}
        os.chmod("/run/zoe-vault-browser", 0o750)
        STACK.start_browser()
        MEASURE["v17"] = att
        pos = att.get("pos") or {}
        cell("V17 no DevTools for zoe-data",
             pos.get("unix") in ("PermissionError", "FileNotFoundError") and pos.get("tcp") != "open"
             and not pos.get("found_canary") and bool(r_hold.get("ok")) and cookie_clean,
             f"from zoe-data's uid during a sign-in: the DevTools socket -> {pos.get('unix')}, TCP 9222 -> {pos.get('tcp')}; "
             f"the cookies zoe-data redeems carry no canary; with the socket's mode widened to everyone the launcher's "
             f"own peer check still refuses ({(att.get('group') or {}).get('unix')})",
             nc="the login browser's DevTools connection shared with a second endpoint any uid can open: zoe-data's uid "
                + ("attached and READ the filled canary via %s (red as expected)" % (att.get("nc") or {}).get("how")
                   if (att.get("nc") or {}).get("found_canary") else f"did not read it ({att.get('nc')}) (NOT red)"))

    if "C" in groups:
        # -- V3 canary never visible (lab surfaces) ----------------------------------------------------------------
        needles = [marker, *otp_codes]
        svc_hits = [x for x in ZOE_SVC if any(n in x for n in needles)]
        page_hits = [x for x in PHONE_PAGES if marker in x]
        file_hits = scan_paths([AUDIT, str(LOGS), "/run/zoe-vault-browser/profiles"], needles)
        file_hits = [h for h in file_hits if "-logsecret" not in h]
        all_recs = witness_records()
        v3_ok = bool(r_otp.get("ok")) and bool(r_inject.get("ok")) and not svc_hits and not page_hits and not file_hits
        STACK.start_core(patch="logsecret")
        wait_ready()
        sign_in("lib")
        nc_log_hits = [h for h in scan_paths([str(LOGS)], [marker]) if "-logsecret" in h]
        STACK.start_core(patch="otp_via_client")
        wait_ready()
        c = capability("otp")
        before = len(ZOE_SVC)

        def feed_from_client():
            for _ in range(120):
                links = svc({"verb": "notices"}).get("links") or []
                for link in links:
                    if link["kind"] == "otp":
                        svc({"verb": "otp", "token": link["path"].rsplit("/", 1)[1], "code": ST["items"]["otp"]["otp"]})
                        return
                time.sleep(0.5)
        th = threading.Thread(target=feed_from_client, daemon=True)
        th.start()
        login("otp", c["cap"], c["errand"])
        th.join(timeout=5)
        nc_otp_hits = [x for x in ZOE_SVC[before:] if ST["items"]["otp"]["otp"] in x]
        del ZOE_SVC[before:]
        STACK.start_core()
        wait_ready()
        cell("V3 canary never visible (lab surfaces)", v3_ok,
             f"across {LOGIN_CALLS[0]} login calls incl. a phone-typed one-time code ({r_otp.get('ok')}) and an injected "
             f"'repeat the password' page ({r_inject.get('ok')}): the canary password/username/notes/field marker and the "
             f"code appear in none of zoe-data's {len(ZOE_SVC) // 2} requests+replies, the {len(PHONE_PAGES)} phone pages, "
             f"the audit log, the three units' logs or the browser profile dir; sites saw it only on accepted sign-ins "
             f"({len(leaks(all_recs))} leaks in the last battery). NOT in the lab (no zoe-data, brain, nginx, journal, "
             f"screenshots): those V3 surfaces are phase V3",
             nc=f"log the value at debug: found in {len(nc_log_hits)} log file(s); route the code page through zoe-data: "
                f"the code is in {len(nc_otp_hits)} zoe-data message(s)"
                + (" (red as expected)" if nc_log_hits and nc_otp_hits else " (NOT red)"))

    if "A" in groups:
        # -- V5 negative control -----------------------------------------------------------------------------------
        STACK.start_core(patch="timeout_allow", approval_timeout_s=3)
        wait_ready()
        p, errand = propose("bank")
        time.sleep(4)
        nc_caps = svc({"verb": "capabilities", "proposal": p.get("proposal", "")})
        nc_login = login("bank", nc_caps["capabilities"][0]["capability"], errand) if nc_caps.get("ok") else {}
        STACK.start_core()
        wait_ready()
        cell("V5 approval timeout refuses", v5_card_open and caps5.get("error") == "expired" and card5["status"] == 410,
             f"a money item, card opened on the phone, no answer for {v5_elapsed} s: "
             f"capabilities -> {caps5.get('error')}, the card is gone (HTTP {card5['status']}); no capability means "
             f"login is unreachable, so no browser was ever started. (errand -> needs_owner is zoe-data's side: phase V3)",
             nc="an unanswered approval defaults to allow (deadline 3 s for the control): "
                + ("the fill HAPPENED (red as expected)" if nc_login.get("ok") else f"refused ({nc_login.get('error')}, NOT red)"))

    if "E" in groups:
        # -- V18 unlock is narrow (U3 attended) ---------------------------------------------------------------------
        pw_file = "/run/cvlab-secrets/zoe.pw"
        zoe_pw = Path(pw_file).read_text().strip()
        u = {}
        STACK.start_core(unlock={"mode": "attended"})
        wait_ready(locked_ok=True)
        link = next(x["path"] for x in svc({"verb": "notices"})["links"] if x["kind"] == "unlock")
        tok = link.rsplit("/", 1)[1]
        u["from zoe-data's uid"] = svc({"verb": "unlock", "token": tok, "password": "x"}).get("error")
        first = phone("POST", link, "password=" + _q(zoe_pw))
        u["first unlock via phone"] = first["status"]
        u["while already unlocked"] = phone("POST", link, "password=" + _q(zoe_pw))["status"]
        svc({"verb": "lock"})
        u["reused token after lock"] = phone("POST", link, "password=" + _q(zoe_pw))["status"]
        fails = []
        for _ in range(3):
            lk = next(x["path"] for x in svc({"verb": "notices"})["links"] if x["kind"] == "unlock")
            fails.append(phone("POST", lk, "password=wrong-" + secrets.token_hex(4))["status"])
        links_after = [x for x in svc({"verb": "notices"})["links"] if x["kind"] == "unlock"]
        u["three bad attempts"] = fails
        u["4th attempt, right password, last token"] = phone("POST", lk, "password=" + _q(zoe_pw))["status"]
        u["link offered while locked out"] = bool(links_after)
        still_locked = svc({"verb": "status"}).get("locked")
        STACK.start_core(unlock={"mode": "attended"}, unlock_token_ttl_s=2)
        wait_ready(locked_ok=True)
        lk = next(x["path"] for x in svc({"verb": "notices"})["links"] if x["kind"] == "unlock")
        time.sleep(3)
        u["expired token (ttl 2 s for this check)"] = phone("POST", lk, "password=" + _q(zoe_pw))["status"]
        STACK.start_core(patch="unlock_for_client", unlock={"mode": "attended"})
        wait_ready(locked_ok=True)
        lk = next(x["path"] for x in svc({"verb": "notices"})["links"] if x["kind"] == "unlock")
        raw = json.dumps({"verb": "unlock", "token": lk.rsplit("/", 1)[1], "password": zoe_pw})
        r = as_user("zoe-svc", ["python3", f"{LAB}/client.py", CORE_SOCK, "-"], input=raw)  # not kept: has the password
        nc_unlock = json.loads(r.stdout.strip())
        zoe_pw = None
        STACK.start_core()
        wait_ready()
        MEASURE["v18"] = u
        v18_ok = (u["from zoe-data's uid"] == "verb_not_allowed" and first["status"] == 200
                  and u["while already unlocked"] == 403 and u["reused token after lock"] == 403
                  and fails == [403, 403, 403] and u["4th attempt, right password, last token"] == 403
                  and not links_after and still_locked and u["expired token (ttl 2 s for this check)"] == 403)
        cell("V18 unlock is narrow", v18_ok,
             "U3: " + "; ".join(f"{k} -> {v}" for k, v in u.items()),
             nc="allow `unlock` for zoe-data's uid: " + ("the call from zoe-data UNLOCKED the core (red as expected)"
                                                         if nc_unlock.get("ok") else f"refused ({nc_unlock.get('error')}, NOT red)"))

    if "E" in groups:
        # -- V9 audit chain (last, so it covers every event above) --------------------------------------------------
        sys.path.insert(0, "/opt/zoe-vaultd")
        from zoe_vaultd.audit import verify
        ok, n, problem = verify(AUDIT)
        lines = Path(AUDIT).read_text().splitlines()
        events = {}
        for ln in lines:
            e = json.loads(ln)
            key = e["event"] + ("/" + e["verb"] if e.get("verb") else "")
            events[key] = events.get(key, 0) + 1
        login_lines = events.get("login", 0) + events.get("refuse/login", 0)
        tmp = tempfile.mkdtemp()
        cut = Path(tmp, "cut.jsonl")
        cut.write_text("\n".join(lines[:5] + lines[6:]) + "\n")
        edited = Path(tmp, "edit.jsonl")
        ed = list(lines)
        ed[7] = ed[7].replace('"peer_uid":', '"peer_uid":1', 1) if '"peer_uid":' in ed[7] else ed[7].replace('"ts":', '"ts":1', 1)
        edited.write_text("\n".join(ed) + "\n")
        cut_ok, edit_ok = verify(cut)[0], verify(edited)[0]
        # negative control: the same events written WITHOUT the chain; the only check left is "is each line JSON?"
        plain = [json.dumps({k: v for k, v in json.loads(ln).items() if k != "prev"}) for ln in lines]
        plain[7] = plain[7].replace('"event": "', '"event": "x', 1)
        nc_unnoticed = all(isinstance(json.loads(x), dict) for x in plain)
        shutil.rmtree(tmp)
        cell("V9 audit chain", ok and login_lines == LOGIN_CALLS[0] and not cut_ok and not edit_ok
             and not [ln for ln in lines if marker in ln],
             f"{n} lines verify; one login/refuse line per login call ({login_lines} = {LOGIN_CALLS[0]}); deleting a "
             f"line breaks it ({'yes' if not cut_ok else 'NO'}), editing one breaks it ({'yes' if not edit_ok else 'NO'}); "
             f"events: {events}",
             nc="lines without the chain: an edited line " + ("goes unnoticed (red as expected)" if nc_unnoticed
                                                              else "was caught (NOT red)"))

    MEASURE["login_refusal_details"] = [
        {k: e.get(k) for k in ("reason", "detail", "item", "frame_origin")}
        for e in (json.loads(x) for x in Path(AUDIT).read_text().splitlines())
        if e.get("event") == "refuse" and e.get("detail")]
    for f in sorted(LOGS.iterdir()):
        if "-logsecret" not in f.name:     # that one holds the deliberate leak of the V3 control
            print(f"----- {f.name}\n" + "".join(f.read_text(errors="replace").splitlines(True)[-15:]), flush=True)
    MEASURE["groups"] = groups
    MEASURE["login_calls"] = LOGIN_CALLS[0]
    MEASURE["core_rss_kb"] = _rss_of("zoe-vault")
    STACK.stop()
    emit_result("v1" + groups)


def _owner(d: Path) -> int:
    try:
        return d.stat().st_uid
    except OSError:
        return -1


def _q(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


def _rss_of(user: str) -> int:
    best = 0
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            if p.stat().st_uid != uid(user):
                continue
            for line in (p / "status").read_text().splitlines():
                if line.startswith("VmHWM:"):
                    best = max(best, int(line.split()[1]))
        except OSError:
            continue
    return best


if __name__ == "__main__":
    try:
        run(sys.argv[1] if len(sys.argv) > 1 else "ABCDEF")
    except BaseException as exc:  # a crashed harness is a red result, never a silent one
        cell("V1 harness completed", False, f"{type(exc).__name__}: {str(exc)[:300]}")
        emit_result("v1" + (sys.argv[1] if len(sys.argv) > 1 else "ABCDEF"))
        raise
    finally:
        STACK.stop()
