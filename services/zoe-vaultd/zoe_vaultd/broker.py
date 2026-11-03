"""`zoe-vaultd`, the core: the only Zoe code that ever decrypts (credential-vault.md section 5).

A Unix socket server. Every connection is identified by SO_PEERCRED and mapped to a ROLE:
  - `client`  (zoe-data's uid): labels, propose, capabilities, login, redeem, lock, status, notices
  - `phone`   (zoe-vault-phone's uid): card, approve, otp, unlock, lock
Any other uid is refused before a verb is read. There is NO verb that returns a username, password, note, TOTP seed
or custom field value (cell V7). `login` is the only verb that uses a credential, and it needs a capability the broker
minted after the credential owner approved on the broker's own phone page (cells V6, V5).

Protocol: one JSON object per line in, one JSON object per line out.
"""

from __future__ import annotations

import grp
import hashlib
import json
import os
import re
import secrets
import socket
import socketserver
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import origin as originmod
from .audit import AuditLog
from .cdp import CDP, CDPError
from .fill import Refusal, run_login
from .peer import peer_uid
from .policy import (CATEGORIES, OTP_TIMEOUT_S, DeviceRegistry, Policy, PolicyRefusal, Step, UnlockGate, _key, is_sensitive)
from .psl import PublicSuffixList
from .vault import BwClient, VaultError, item_meta

CLIENT_VERBS = frozenset({"labels", "propose", "capabilities", "login", "redeem", "lock", "status", "notices"})
PHONE_VERBS = frozenset({"card", "approve", "otp", "unlock", "lock", "pair_complete"})
ADMIN_VERBS = frozenset({"pair", "status", "lock"})
VERBS = CLIENT_VERBS | PHONE_VERBS | ADMIN_VERBS
ROLE_VERBS = {"client": CLIENT_VERBS, "phone": PHONE_VERBS, "admin": ADMIN_VERBS}

MAX_REQUEST = 64 * 1024
HANDLE_TTL_S = 300
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
CODE_RE = re.compile(r"^[A-Za-z0-9]{4,12}$")


def _clamp(value, lo, hi, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


@dataclass
class Config:
    server: str
    email: str
    bw_path: str
    appdata_dir: str
    socket_path: str
    socket_group: str
    cdp_socket: str
    audit_path: str
    psl_path: str
    client_uids: set[int]
    phone_uids: set[int]
    members: dict
    orgs: list[dict]
    unlock_mode: str = "key_file"          # key_file (U1) | attended (U3)
    password_file: str | None = None
    extra_ca: str | None = None
    lab_profile: bool = False
    mem_floor_mb: int = 1500
    approval_timeout_s: float = 120.0
    capability_ttl_s: float = 600.0
    unlock_token_ttl_s: float = 600.0
    rate_limits: tuple[int, int, int] = (3, 6, 12)
    admin_uids: set[int] = field(default_factory=set)

    @classmethod
    def load(cls, path: str) -> "Config":
        raw = json.loads(Path(path).read_text())
        unlock = raw.get("unlock") or {}
        mode = unlock.get("mode", "key_file")
        if mode not in ("key_file", "attended"):
            raise ValueError("unlock.mode must be key_file or attended")
        client_uids = {int(u) for u in raw["client_uids"]}
        phone_uids = {int(u) for u in raw["phone_uids"]}
        admin_uids = {int(u) for u in raw.get("admin_uids") or []}
        if (client_uids & phone_uids or client_uids & admin_uids or phone_uids & admin_uids
                or 0 in client_uids | phone_uids | admin_uids):
            raise ValueError("client, phone and admin uids must be disjoint and must not include root")
        return cls(
            server=raw["server"], email=raw["email"], bw_path=raw["bw_path"], appdata_dir=raw["appdata_dir"],
            socket_path=raw["socket"]["path"], socket_group=raw["socket"]["group"], cdp_socket=raw["cdp_socket"],
            audit_path=raw["audit_path"], psl_path=raw["psl_path"],
            client_uids=client_uids, phone_uids=phone_uids, admin_uids=admin_uids,
            members=raw["members"], orgs=raw["orgs"], unlock_mode=mode, password_file=unlock.get("password_file"),
            extra_ca=raw.get("extra_ca"), lab_profile=bool(raw.get("lab_profile", False)),
            mem_floor_mb=int(raw.get("mem_floor_mb", 1500)),
            approval_timeout_s=_clamp(raw.get("approval_timeout_s"), 1, 120, 120.0),
            capability_ttl_s=_clamp(raw.get("capability_ttl_s"), 1, 600, 600.0),
            unlock_token_ttl_s=_clamp(raw.get("unlock_token_ttl_s"), 1, 600, 600.0),
            rate_limits=tuple(int(_clamp((raw.get("rate_limits") or {}).get(k), 1, 1000, d))
                              for k, d in (("per_item", 3), ("per_member", 6), ("household", 12))))


@dataclass
class _OtpWait:
    member: str
    item: str
    expires: float
    token: str
    event: threading.Event = field(default_factory=threading.Event)
    code: str | None = None


def safe_category(fields: dict) -> str | None:
    """The item category, ONLY as one of the fixed constants (credential-vault.md section 4). `zoe:category` is text the
    member typed; anything that is not exactly a known category becomes None, which every rule treats as sensitive,
    and the raw value is never echoed to zoe-data, a phone card or the audit log (round-3 review)."""
    value = fields.get("zoe:category")
    return value if isinstance(value, str) and value in CATEGORIES else None


def policy_digest(meta) -> str:
    """SHA-256 over everything in an item that decides WHERE its password may go: every URI with its match type and
    every zoe: field (login_url, category, selectors). A capability is bound to it (round-3 review): an item that
    gained a URI after the owner approved is refused with policy_changed, never filled under the new rules."""
    canon = {"uris": [[u.get("uri"), u.get("match")] for u in meta.uris],
             "fields": sorted([k, v] for k, v in meta.fields.items())}
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


def mem_available_mb() -> int:
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


class Core:
    def __init__(self, cfg: Config, *, bw: BwClient | None = None, clock=time.monotonic) -> None:
        self.cfg = cfg
        self.clock = clock
        self.psl = PublicSuffixList.load(cfg.psl_path)
        self.audit = AuditLog(cfg.audit_path)
        self.bw = bw or BwClient(bw_path=cfg.bw_path, appdata_dir=cfg.appdata_dir, server=cfg.server,
                                 email=cfg.email, extra_ca=cfg.extra_ca)
        self.policy = Policy(clock=clock, approval_timeout_s=cfg.approval_timeout_s,
                             capability_ttl_s=cfg.capability_ttl_s, limits=cfg.rate_limits)
        self.gate = UnlockGate(clock=clock, ttl_s=cfg.unlock_token_ttl_s)
        self.devices = DeviceRegistry(os.path.join(os.path.dirname(cfg.audit_path) or ".", "devices.json"),
                                      clock=clock)
        self.locked = True
        self._epoch = 0                    # bumped by every lock; a login that started earlier can mint nothing
        self._active_cdp: CDP | None = None
        self._browser_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._otp: dict[str, _OtpWait] = {}
        self._handles: dict[str, dict] = {}
        self.counters = {"logins_ok": 0, "logins_refused": 0, "proposals": 0, "peer_refused": 0}
        self.org_by_id = {o["id"]: o for o in cfg.orgs}

    # -- lifecycle -----------------------------------------------------------------------------------------------
    def start(self) -> None:
        self.audit.write("start", mode=self.cfg.unlock_mode)
        if self.cfg.unlock_mode == "key_file":
            self.bw.unlock(password_file=self.cfg.password_file)
            self.locked = False
            self.audit.write("unlock", decision="ok", mode="key_file")
        else:
            self.gate.mint()

    def _lock(self, why: str, peer: int) -> None:
        # First stop anything in flight: no new handle can be minted past this point, and a sign-in that is inside
        # the browser right now loses its DevTools connection (the launcher then kills that browser).
        with self._state_lock:
            self.locked = True
            self._epoch += 1
            cdp, self._active_cdp = self._active_cdp, None
        if cdp is not None:
            cdp.close()
        self.bw.lock()
        self.policy.wipe()
        with self._state_lock:
            self._handles.clear()
            for w in self._otp.values():
                w.event.set()
            self._otp.clear()
        self.locked = True
        if self.cfg.unlock_mode == "attended":
            self.gate.mint()
        self.audit.write("lock", reason=why, peer_uid=peer)

    # -- peers and identity (sections 5.1 and 8) ------------------------------------------------------------------
    def role_for(self, uid: int) -> str | None:
        """SO_PEERCRED uid -> role. Anyone else is refused before a verb is read (cell V8)."""
        if uid in self.cfg.client_uids:
            return "client"
        if uid in self.cfg.phone_uids:
            return "phone"
        if uid in self.cfg.admin_uids:
            return "admin"
        return None

    def _stale(self, epoch: int) -> bool:
        """True once a lock has happened since `epoch` was taken (or the broker is locked now)."""
        return self.locked or self._epoch != epoch

    @staticmethod
    def _policy_matches(meta, cap) -> bool:
        return bool(cap.policy) and policy_digest(meta) == cap.policy

    def _is_admin(self, member: str | None) -> bool:
        info = self.cfg.members.get(member) if isinstance(member, str) else None
        return isinstance(info, dict) and bool(info.get("admin")) and bool(info.get("adult"))

    def member_orgs(self, member: str) -> list[dict]:
        info = self.cfg.members.get(member) if isinstance(member, str) else None
        if not isinstance(info, dict) or not info.get("adult"):
            # guests, unknown voices and minors never trigger a credential use (O7)
            raise PolicyRefusal("not_allowed")
        out = []
        for o in self.cfg.orgs:
            if o.get("kind") == "member" and o.get("owner") == member:
                out.append(o)
            elif o.get("kind") == "household":
                out.append(o)
        return out

    def _usable(self, meta, orgs: list[dict]) -> bool:
        if meta.deleted:
            return False
        for o in orgs:
            if meta.org_id == o["id"] and o.get("collection") in meta.collection_ids:
                return True
        return False

    def _domain(self, rules) -> str | None:
        for r in rules:
            return self.psl.registrable_domain(r.origin.host) or r.origin.host
        return None

    # -- verbs ---------------------------------------------------------------------------------------------------
    def dispatch(self, role: str, uid: int, req: dict) -> dict:
        verb = req.get("verb")
        if verb not in VERBS:
            return {"ok": False, "error": "unknown_verb"}
        if verb not in ROLE_VERBS.get(role, frozenset()):
            self.audit.write("refuse", verb=str(verb), reason="verb_not_allowed", peer_uid=uid)
            return {"ok": False, "error": "verb_not_allowed"}
        handler = getattr(self, "verb_" + verb)
        try:
            return handler(uid, req)
        except PolicyRefusal as r:
            return {"ok": False, "error": r.reason}
        except VaultError as e:
            return {"ok": False, "error": e.reason}

    def _need_unlocked(self) -> None:
        if self.locked:
            raise PolicyRefusal("locked")

    def verb_status(self, uid: int, req: dict) -> dict:
        return {"ok": True, "locked": self.locked, "unlock_mode": self.cfg.unlock_mode,
                "unlock_locked_out": self.gate.locked_out(), "last_sync": self.bw.last_sync,
                "counters": dict(self.counters)}

    def verb_notices(self, uid: int, req: dict) -> dict:
        links = self.policy.pending_links()
        now = self.clock()
        with self._state_lock:
            for w in self._otp.values():
                if now < w.expires and w.code is None:
                    links.append({"kind": "otp", "member": w.member, "path": "/v/n/" + w.token})
        if self.locked and self.cfg.unlock_mode == "attended":
            link = self.gate.current_link()
            if link:
                links.append({"kind": "unlock", "member": "admins", "path": link})
        return {"ok": True, "links": links}

    def verb_labels(self, uid: int, req: dict) -> dict:
        self._need_unlocked()
        member = req.get("member")
        orgs = self.member_orgs(member)
        self.bw.sync()
        out = []
        for o in orgs:
            for meta in self.bw.list_org_items(o["id"]):
                if not self._usable(meta, [o]):
                    continue
                rules, _ = originmod.item_rules(meta.uris, lab_profile=self.cfg.lab_profile)
                out.append({"item": meta.item_id, "label": meta.name[:80],
                            "category": safe_category(meta.fields), "domain": self._domain(rules)})
        self.audit.write("labels", member=member, count=len(out), peer_uid=uid)
        return {"ok": True, "items": out}

    def _resolve_step(self, member: str, orgs: list[dict], item_id: str, step_id: str) -> tuple[Step, str]:
        raw = self.bw.get_item(item_id)
        if raw is None:
            raise PolicyRefusal("item_unavailable")
        meta = item_meta(raw)
        del raw
        if not self._usable(meta, orgs):
            raise PolicyRefusal("item_unavailable")
        rules, _ = originmod.item_rules(meta.uris, lab_profile=self.cfg.lab_profile)
        login_url, why = originmod.expected_login_origin(meta.fields, rules, psl=self.psl,
                                                        lab_profile=self.cfg.lab_profile)
        if login_url is None:
            raise PolicyRefusal(why)
        o = originmod.origin_of_url(login_url)
        org = next(x for x in orgs if x["id"] == meta.org_id)
        approver = org.get("owner") if org.get("kind") == "member" else member
        category = safe_category(meta.fields)
        return Step(step_id=step_id, item_id=meta.item_id, label=meta.name[:80], category=category, origin=str(o),
                    login_url=login_url, domain=self._domain(rules), policy=policy_digest(meta)), approver

    def verb_propose(self, uid: int, req: dict) -> dict:
        self._need_unlocked()
        member, errand, goal = req.get("member"), req.get("errand"), req.get("goal")
        steps_in = req.get("steps")
        if not (isinstance(errand, str) and ID_RE.match(errand)) or not isinstance(goal, str) or not goal.strip():
            raise PolicyRefusal("bad_request")
        if not isinstance(steps_in, list) or not 1 <= len(steps_in) <= 8:
            raise PolicyRefusal("bad_request")
        orgs = self.member_orgs(member)
        # No sync here (a `bw sync` costs ~6 s on this box, measured in V0): the card is built from the cached vault
        # and `login` syncs before every use and refuses with `origin_changed` / `item_unavailable` if it moved.
        steps, approvers = [], set()
        for s in steps_in:
            if not isinstance(s, dict) or not ID_RE.match(str(s.get("step", ""))) or \
                    not ID_RE.match(str(s.get("item", ""))):
                raise PolicyRefusal("bad_request")
            step, approver = self._resolve_step(member, orgs, s["item"], s["step"])
            steps.append(step)
            approvers.add(approver)
        if len(approvers) != 1:
            # one proposal goes to one phone; split the errand per credential owner
            raise PolicyRefusal("several_approvers")
        goal_clean = "".join(ch for ch in goal if ch.isprintable())[:200]
        p = self.policy.new_proposal(member=member, errand=errand, goal=goal_clean, steps=steps,
                                     approver=approvers.pop())
        self.counters["proposals"] += 1
        for st in steps:
            self.audit.write("propose", member=member, errand=errand, step=st.step_id, item=st.item_id,
                             label=st.label, domain=st.domain, origin=st.origin, proposal=p.proposal_id,
                             approver=p.approver, category=st.category or "none", peer_uid=uid)
        return {"ok": True, "proposal": p.proposal_id, "approver": p.approver, "path": "/v/n/" + p.notice,
                "expires_in": int(self.cfg.approval_timeout_s)}

    # -- the phone side: every answer needs a PAIRED phone of the right member ------------------------------------
    def verb_pair(self, uid: int, req: dict) -> dict:
        """Admin role only (never zoe-data's uid): a single-use, 10-minute link that pairs ONE phone to a member."""
        member = req.get("member")
        info = self.cfg.members.get(member) if isinstance(member, str) else None
        if not isinstance(info, dict) or not info.get("adult"):
            raise PolicyRefusal("not_allowed")
        token = self.devices.begin(member)
        self.audit.write("pair", member=member, decision="link", peer_uid=uid)
        return {"ok": True, "path": "/v/pair/" + token}

    def verb_pair_complete(self, uid: int, req: dict) -> dict:
        member, secret = self.devices.complete(req.get("token") or "")
        self.audit.write("pair", member=member, decision="paired", peer_uid=uid)
        return {"ok": True, "member": member, "device": secret}

    def _device(self, req: dict) -> str | None:
        return self.devices.member_of(req.get("device"))

    def _otp_by_notice(self, notice: str) -> "_OtpWait | None":
        with self._state_lock:
            return self._otp.get(_key(notice or ""))

    def verb_card(self, uid: int, req: dict) -> dict:
        """What a notice is, shown only to the phone it is for. An approval card is built here from vault data and
        the owner's goal, never from model text."""
        notice = req.get("notice") or ""
        device_member = self._device(req)
        w = self._otp_by_notice(notice)
        if w is not None:
            if device_member != w.member:
                raise PolicyRefusal("not_your_phone")
            return {"ok": True, "kind": "otp"}
        if self.gate.is_current(notice):
            if not self._is_admin(device_member):
                raise PolicyRefusal("not_your_phone")
            return {"ok": True, "kind": "unlock"}
        p = self.policy.by_notice(notice)
        if p is None:
            raise PolicyRefusal("unknown_notice")
        if device_member is None or device_member != p.approver:
            raise PolicyRefusal("not_your_phone")
        if p.state != "pending":
            raise PolicyRefusal("proposal_" + p.state)
        steps = []
        for s in p.steps:
            o = originmod.parse_origin(s.origin)
            try:
                unicode_host = o.host.encode("ascii").decode("idna") if o else ""
            except UnicodeError:
                unicode_host = o.host if o else ""
            steps.append({"label": s.label, "origin": s.origin,
                          "looks_like": unicode_host if o and unicode_host != o.host else None,
                          "category": s.category or "none", "sensitive": is_sensitive(s.category)})
        return {"ok": True, "kind": "approve", "member": p.member, "approver": p.approver, "goal": p.goal,
                "errand": p.errand,
                "steps": steps, "expires_in": max(0, int(p.expires - self.clock()))}

    def verb_approve(self, uid: int, req: dict) -> dict:
        decision = req.get("decision")
        if decision not in ("approve", "deny", "deny_lock"):
            raise PolicyRefusal("bad_request")
        p = self.policy.decide(req.get("notice") or "", decision == "approve", device_member=self._device(req))
        self.audit.write("approve" if decision == "approve" else "deny", member=p.member, errand=p.errand,
                         proposal=p.proposal_id, approver=p.approver, decision=decision, peer_uid=uid)
        if decision == "deny_lock":
            self._lock("deny_and_lock", uid)
        return {"ok": True, "state": p.state}

    def verb_capabilities(self, uid: int, req: dict) -> dict:
        state, caps = self.policy.issue(str(req.get("proposal") or ""))
        self.audit.write("capabilities", proposal=str(req.get("proposal"))[:80], decision=state, count=len(caps),
                         peer_uid=uid)
        if state != "ok":
            return {"ok": False, "error": state}
        return {"ok": True, "capabilities": [{"step": s, "capability": c} for s, c in caps]}

    def _otp_wait(self, member: str, item: str):
        def wait() -> str | None:
            token = secrets.token_urlsafe(32)
            w = _OtpWait(member=member, item=item, expires=self.clock() + OTP_TIMEOUT_S, token=token)
            with self._state_lock:
                self._otp[_key(token)] = w
            w.event.wait(timeout=OTP_TIMEOUT_S)
            with self._state_lock:
                self._otp.pop(_key(token), None)
            code, w.code = w.code, None
            return code
        return wait

    def verb_otp(self, uid: int, req: dict) -> dict:
        code = req.get("code")
        if not isinstance(code, str) or not CODE_RE.match(code):
            raise PolicyRefusal("bad_code")
        device_member = self._device(req)
        with self._state_lock:
            w = self._otp.get(_key(req.get("notice") or ""))
            if w is None or self.clock() >= w.expires or w.code is not None:
                raise PolicyRefusal("unknown_notice")
            if device_member != w.member:
                raise PolicyRefusal("not_your_phone")
            w.code = code
            w.event.set()
        self.audit.write("otp", member=w.member, item=w.item, decision="received", peer_uid=uid)
        return {"ok": True}

    def verb_login(self, uid: int, req: dict) -> dict:
        started = self.clock()
        member, errand, step, item = (req.get(k) for k in ("member", "errand", "step", "item"))
        audit = {"member": member if isinstance(member, str) else None,
                 "errand": errand if isinstance(errand, str) else None,
                 "step": step if isinstance(step, str) else None, "item": item if isinstance(item, str) else None,
                 "peer_uid": uid}

        def refuse(reason: str, **extra) -> dict:
            self.counters["logins_refused"] += 1
            self.audit.write("refuse", verb="login", reason=reason,
                             duration_ms=int((self.clock() - started) * 1000), **audit, **extra)
            return {"ok": False, "error": reason}

        # The lock epoch is taken BEFORE the capability is spent and re-checked after every stage: a login that
        # waited (for the browser, say) across a lock, even one followed by a fresh unlock, can never continue.
        epoch = self._epoch
        if self.locked:
            return refuse("locked")
        try:
            cap = self.policy.consume(req.get("capability"), member=member, errand=errand, step=step, item=item)
        except PolicyRefusal as r:
            return refuse(r.reason)
        audit.update(label=cap.label, origin=cap.origin, category=cap.category or "none")
        if mem_available_mb() < self.cfg.mem_floor_mb:
            return refuse("ram_floor")
        if not self._browser_lock.acquire(timeout=60):
            return refuse("browser_busy")
        username = password = None
        try:
            if self._stale(epoch):
                return refuse("locked")
            try:
                orgs = self.member_orgs(cap.member)
                self.bw.sync()                      # sync before every use: a revoked share stops now (5.4)
            except (PolicyRefusal, VaultError) as e:
                return refuse(getattr(e, "reason", "vault_unreachable"))
            if self._stale(epoch):
                return refuse("locked")
            raw = self.bw.get_item(cap.item_id)
            if raw is None:
                return refuse("item_unavailable")
            meta = item_meta(raw)
            if not self._usable(meta, orgs):
                del raw
                return refuse("item_unavailable")
            rules, _ = originmod.item_rules(meta.uris, lab_profile=self.cfg.lab_profile)
            login_url, why = originmod.expected_login_origin(meta.fields, rules, psl=self.psl,
                                                            lab_profile=self.cfg.lab_profile)
            if login_url is None:
                del raw
                return refuse(why)
            if str(originmod.origin_of_url(login_url)) != cap.origin:
                del raw
                return refuse("origin_changed")
            if not self._policy_matches(meta, cap):
                del raw
                return refuse("policy_changed")
            if self._stale(epoch):
                del raw
                return refuse("locked")
            login = raw.get("login") or {}
            username, password = login.get("username") or "", login.get("password") or ""
            del raw, login
            if not password:
                return refuse("no_password")
            try:
                cdp = CDP.connect_unix(self.cfg.cdp_socket)
            except OSError:
                return refuse("browser_unavailable")
            with self._state_lock:
                if self._stale(epoch):                       # locked while we synced: never open the browser
                    cdp.close()
                    return refuse("locked")
                self._active_cdp = cdp
            try:
                result = run_login(cdp, psl=self.psl, rules=rules, approved_origin=cap.origin, login_url=login_url,
                                   username=username, password=password, lab_profile=self.cfg.lab_profile,
                                   otp_wait=self._otp_wait(cap.member, cap.item_id))
            except Refusal as r:
                return refuse("locked" if self._stale(epoch) else r.reason, frame_origin=r.frame_origin)
            except CDPError as exc:
                if self._stale(epoch):
                    return refuse("locked")
                # the DevTools method and Chromium's short reason only (letters and spaces); never a parameter value
                why = re.sub(r"[^A-Za-z. ]", "", str(exc))[:80]
                return refuse("browser_error", detail=why)
            finally:
                with self._state_lock:
                    if self._active_cdp is cdp:
                        self._active_cdp = None
                cdp.close()
        except VaultError as e:
            return refuse(e.reason)
        except Exception as exc:  # fail closed, and still exactly one audit line per login call
            return refuse("internal_error", detail=type(exc).__name__)
        finally:
            username = password = None
            self._browser_lock.release()
        if self._stale(epoch):
            # locked while the browser ran: whatever the page did after its DevTools pipe was cut is not an outcome
            result.cookies.clear()
            return refuse("locked")
        if result.outcome != "ok":
            return refuse(result.outcome, frame_origin=result.frame_origin)
        handle = secrets.token_urlsafe(24)
        with self._state_lock:
            if self._stale(epoch):
                result.cookies.clear()
                minted = False
            else:
                self._handles[_key(handle)] = {"uid": uid, "expires": self.clock() + HANDLE_TTL_S,
                                               "cookies": result.cookies}
                minted = True
        if not minted:
            return refuse("locked")
        self.counters["logins_ok"] += 1
        self.audit.write("login", decision="ok", frame_origin=result.frame_origin, count=len(result.cookies),
                         duration_ms=int((self.clock() - started) * 1000), **audit)
        return {"ok": True, "outcome": "ok", "handle": handle}

    def verb_redeem(self, uid: int, req: dict) -> dict:
        """The sealed handle from `login`: the item-domain cookies, once, only to the uid that ran the login."""
        key = _key(req.get("handle") or "")
        with self._state_lock:
            h = self._handles.get(key)
            if h is None or h["uid"] != uid:
                raise PolicyRefusal("unknown_handle")        # another uid can neither read nor burn it
            del self._handles[key]
        if self.clock() >= h["expires"]:
            raise PolicyRefusal("unknown_handle")
        self.audit.write("redeem", count=len(h["cookies"]), peer_uid=uid)
        return {"ok": True, "cookies": h["cookies"]}

    def verb_lock(self, uid: int, req: dict) -> dict:
        self._lock("requested", uid)
        return {"ok": True, "state": "locked"}

    def verb_unlock(self, uid: int, req: dict) -> dict:
        """U3 only: an ADMIN's paired phone hands over Zoe's vault password against the current single-use unlock
        notice (5.2, cell V18)."""
        if self.cfg.unlock_mode != "attended":
            raise PolicyRefusal("unlock_not_attended")
        if not self._is_admin(self._device(req)):
            self.audit.write("unlock", decision="refused", reason="not_your_phone", peer_uid=uid)
            raise PolicyRefusal("not_your_phone")
        if not self.locked:
            self.audit.write("unlock", decision="refused", reason="not_locked", peer_uid=uid)
            raise PolicyRefusal("not_locked")
        try:
            self.gate.take(req.get("notice") or "")
        except PolicyRefusal as r:
            self.audit.write("unlock", decision="refused", reason=r.reason, peer_uid=uid)
            raise
        password = req.get("password")
        if not isinstance(password, str) or not password or len(password) > 1024:
            self.gate.mint()
            raise PolicyRefusal("bad_request")
        try:
            self.bw.unlock(password=password)
        except VaultError:
            password = None
            locked_out = self.gate.failed()
            if not locked_out:
                self.gate.mint()
            self.audit.write("unlock", decision="refused", reason="unlock_failed" + ("_locked_out" if locked_out
                                                                                   else ""), peer_uid=uid)
            raise PolicyRefusal("unlock_failed")
        password = None
        self.gate.succeeded()
        self.locked = False
        self.audit.write("unlock", decision="ok", mode="attended", peer_uid=uid)
        return {"ok": True, "state": "unlocked"}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        core: Core = self.server.core  # type: ignore[attr-defined]
        try:
            uid = peer_uid(self.connection)
        except OSError:
            return
        role = core.role_for(uid)
        if role is None:
            core.counters["peer_refused"] += 1
            core.audit.write("peer_refused", peer_uid=uid)
            self._send({"ok": False, "error": "peer_refused"})
            return
        while True:
            line = self.rfile.readline(MAX_REQUEST + 1)
            if not line:
                return
            if len(line) > MAX_REQUEST:
                self._send({"ok": False, "error": "too_large"})
                return
            try:
                req = json.loads(line)
                if not isinstance(req, dict):
                    raise ValueError
            except ValueError:
                self._send({"ok": False, "error": "bad_json"})
                continue
            self._send(core.dispatch(role, uid, req))

    def _send(self, obj: dict) -> None:
        self.wfile.write(json.dumps(obj, separators=(",", ":")).encode() + b"\n")
        self.wfile.flush()


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(core: Core, *, block: bool = True):
    path = core.cfg.socket_path
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    old = os.umask(0o117)
    try:
        srv = _Server(path, _Handler)
    finally:
        os.umask(old)
    os.chown(path, -1, grp.getgrnam(core.cfg.socket_group).gr_gid)
    os.chmod(path, 0o660)
    srv.core = core  # type: ignore[attr-defined]
    if not block:
        return srv
    srv.serve_forever()
    return srv


def request(path: str, obj: dict, *, timeout: float = 300.0) -> dict:
    """A tiny client, for zoe-data, the phone unit and the lab."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        s.sendall(json.dumps(obj).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    return json.loads(buf) if buf else {"ok": False, "error": "no_reply"}
