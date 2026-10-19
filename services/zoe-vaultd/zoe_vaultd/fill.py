"""Login mode: sign in inside the broker's own browser (credential-vault.md sections 6.1, 6.2 and 6.4).

The core drives `zoe-vault-browser`'s Chromium over the private DevTools pipe and does the origin check itself:

1. subscribe first (auto-attach to child targets; every navigation / document / context / target event is recorded);
2. find the field per frame, from the broker's OWN isolated world in that frame, so the owning frame is known by
   construction; zero candidates or candidates in several frames means no fill;
3. bind the fill to (session, target, frame, loaderId, isolated-world context);
4. check the frame's committed origin from the browser (URL, securityOrigin, domainAndRegistry and the frame's own
   `self.origin`) against the capability's approved origin and the item's URIs, plus every ancestor frame; check the
   form's resolved submit targets;
5. immediately before each insert: re-read the frame entry and the form targets, focus the field, confirm it is
   `document.activeElement`, confirm the binding saw no event, and only then `Input.insertText`; afterwards confirm the
   value landed in that field and nowhere else;
6. submit, watch the outcome, export the cookies of the item's registrable domain, close the context.

Nothing about a value leaves this module except "filled". The URL navigated to comes from the vault, never a caller.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field

from . import origin as originmod
from .cdp import CDP, CDPError
from .psl import PublicSuffixList

# Events that invalidate a binding when they touch a watched frame, the bound context, or the bound/top target.
FRAME_EVENTS = {
    "Page.frameNavigated", "Page.frameStartedLoading", "Page.frameRequestedNavigation", "Page.frameScheduledNavigation",
    "Page.frameStartedNavigating", "Page.navigatedWithinDocument", "Page.documentOpened", "Page.frameDetached",
    "Page.frameSubtreeWillBeDetached",
}

FINDER_JS = r"""
(() => {
  const vis = (el) => {
    if (!el.isConnected || el.disabled || el.readOnly) return false;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' &&
           Number(cs.opacity || '1') > 0.05;
  };
  globalThis.__zvVis = vis;
  // A page can CLOBBER form and document properties with named elements (<input name=action> makes form.action an
  // element). Read them through the pristine prototype getters of this isolated world instead.
  const get = (proto, name) => Object.getOwnPropertyDescriptor(proto, name).get;
  globalThis.__zvG = {
    action: get(HTMLFormElement.prototype, 'action'), method: get(HTMLFormElement.prototype, 'method'),
    elements: get(HTMLFormElement.prototype, 'elements'), connected: get(Node.prototype, 'isConnected'),
    active: get(Document.prototype, 'activeElement'), url: get(Document.prototype, 'URL'),
    hasFocus: Document.prototype.hasFocus, requestSubmit: HTMLFormElement.prototype.requestSubmit,
    submit: HTMLFormElement.prototype.submit,
  };
  const inputs = Array.from(document.querySelectorAll('input'));
  const pw = inputs.filter(e => (e.type || '').toLowerCase() === 'password' && vis(e));
  const otp = inputs.filter(e => (e.autocomplete || '').toLowerCase().split(/\s+/).includes('one-time-code') &&
                                 ['text', 'tel', 'number'].includes((e.type || 'text').toLowerCase()) && vis(e));
  globalThis.__zvPw = pw;
  globalThis.__zvOtp = otp;
  return JSON.stringify({pw: pw.length, otp: otp.length, origin: self.origin, secure: self.isSecureContext,
                         url: document.URL});
})()
"""

PICK_JS = r"""
((mode) => {
  const vis = globalThis.__zvVis;
  const list = (mode === 'otp' ? globalThis.__zvOtp : globalThis.__zvPw) || [];
  let el = null;
  if (list.length === 1) el = list[0];
  else {
    const want = mode === 'otp' ? 'one-time-code' : 'current-password';
    const hinted = list.filter(e => (e.autocomplete || '').toLowerCase().split(/\s+/).includes(want));
    if (hinted.length === 1) el = hinted[0];
  }
  if (!el || !vis(el)) return JSON.stringify({ok: false, reason: 'ambiguous_fields'});
  const G = globalThis.__zvG;
  const form = el.form;
  let user = null;
  if (mode !== 'otp') {
    const scope = form ? Array.from(G.elements.call(form)) : Array.from(document.querySelectorAll('input'));
    const textish = scope.filter(e => e.tagName === 'INPUT' &&
                                 ['text', 'email', 'tel'].includes((e.type || 'text').toLowerCase()) && vis(e));
    const hinted = textish.filter(e => /\b(username|email)\b/.test((e.autocomplete || '').toLowerCase()));
    if (hinted.length === 1) user = hinted[0];
    else {
      const before = textish.filter(e => e.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
      if (before.length) user = before[before.length - 1];
    }
  }
  globalThis.__zvField = el;
  globalThis.__zvUser = user;
  globalThis.__zvForm = form || null;
  return JSON.stringify({ok: true, user: !!user, form: !!form});
})
"""

# The form's resolved submit targets and methods, read fresh every time (a page script may change them at any moment).
TARGETS_JS_FN = r"""
(() => {
  const G = globalThis.__zvG;
  const form = globalThis.__zvForm;
  if (!form) return JSON.stringify({form: false, actions: [], methods: [], connected: true});
  const actions = [G.action.call(form)];
  const methods = [(G.method.call(form) || 'get').toLowerCase()];
  for (const b of Array.from(G.elements.call(form))) {
    const t = (b.type || '').toLowerCase();
    if ((b.tagName === 'BUTTON' && (t === 'submit' || t === '')) || (b.tagName === 'INPUT' && (t === 'submit' || t === 'image'))) {
      if (b.hasAttribute('formaction')) actions.push(b.formAction);
      if (b.hasAttribute('formmethod')) methods.push((b.formMethod || '').toLowerCase());
    }
  }
  return JSON.stringify({form: true, actions, methods, connected: G.connected.call(form)});
})
"""
TARGETS_JS = "(" + TARGETS_JS_FN.strip() + ")()"

FOCUSED_JS = r"""
((which) => {
  const G = globalThis.__zvG;
  const el = which === 'user' ? globalThis.__zvUser : globalThis.__zvField;
  const ok = !!el && G.connected.call(el) && G.active.call(document) === el;
  if (ok && typeof el.select === 'function') el.select();   // replace any prefilled value, never append to it
  return JSON.stringify({connected: !!el && G.connected.call(el), active: ok && G.active.call(document) === el,
                         focus: G.hasFocus.call(document), url: G.url.call(document), origin: self.origin,
                         type: el ? (el.type || '').toLowerCase() : ''});
})
"""

LANDED_JS = r"""
((which, n) => {
  const G = globalThis.__zvG;
  const el = which === 'user' ? globalThis.__zvUser : globalThis.__zvField;
  return JSON.stringify({ok: !!el && G.connected.call(el) && G.active.call(document) === el && el.value.length === n});
})
"""

_SUBMIT_JS = r"""
((checked) => {
  const G = globalThis.__zvG;
  const form = globalThis.__zvForm;
  if (!form) return 'nokey';
  // Atomic with the submit: a page timer cannot change the target between the broker's check and this call.
  const now = JSON.parse((TARGETS_JS_FN)());
  if (JSON.stringify(now.actions) !== JSON.stringify(checked.actions) ||
      JSON.stringify(now.methods) !== JSON.stringify(checked.methods)) return 'changed';
  if (typeof G.requestSubmit === 'function') { G.requestSubmit.call(form); return 'requested'; }
  G.submit.call(form);
  return 'submitted';
})
"""
# A submit handler on the page could still change the action while requestSubmit runs; that is page code of the
# approved origin, which can read the filled value anyway (6.4: a compromised legitimate site is out of scope).
SUBMIT_JS = _SUBMIT_JS.replace("(TARGETS_JS_FN)", "(" + TARGETS_JS_FN.strip() + ")")


class Refusal(Exception):
    def __init__(self, reason: str, frame_origin: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.frame_origin = frame_origin


@dataclass
class Binding:
    session: str
    target_id: str
    frame_id: str
    loader_id: str
    context_id: int
    watched: set[str]
    targets: set[str]
    sessions: set[str]
    target_urls: dict[str, str]
    index: int


@dataclass
class LoginResult:
    outcome: str                       # ok | needs_otp_timeout | login_failed | needs_owner
    frame_origin: str | None = None
    cookies: list[dict] = field(default_factory=list)
    filled: int = 0


@dataclass
class _Frame:
    session: str
    target_id: str
    frame: dict
    parent_id: str | None


class LoginRun:
    """One sign-in, in one fresh browser. Not thread-safe; the core runs one at a time."""

    def __init__(self, cdp: CDP, *, psl: PublicSuffixList, rules: list, approved_origin: str, lab_profile: bool,
                 nav_timeout_s: float = 15.0, settle_s: float = 0.6) -> None:
        self.cdp = cdp
        self.psl = psl
        self.rules = rules
        self.approved_origin = originmod.parse_origin(approved_origin)
        if self.approved_origin is None:
            raise Refusal("bad_approved_origin")
        self.lab_profile = lab_profile
        self.nav_timeout_s = nav_timeout_s
        self.settle_s = settle_s
        self.world = "zoe-vaultd-" + secrets.token_hex(6)
        self.top_session: str | None = None
        self.top_target: str | None = None
        self.context_id: str | None = None
        self.sessions: dict[str, dict] = {}     # sessionId -> {target_id, type, parent_frame_id, url}
        self._new_sessions: list[str] = []
        self._new_popups: list[str] = []
        # every URL the TOP frame's document went through (each redirect hop and each committed navigation)
        self.hops: list[str] = []
        self._submit_hop_index = 0
        cdp.on_event(self._on_event)

    # -- event plumbing ------------------------------------------------------------------------------------------
    def _on_event(self, method: str, params: dict, sid: str | None) -> None:
        if method == "Target.attachedToTarget":
            info = params.get("targetInfo") or {}
            child = params.get("sessionId")
            if child and info.get("type") in ("iframe", "page") and child not in self.sessions:
                self.sessions[child] = {"target_id": info.get("targetId"), "type": info.get("type"),
                                        "parent_frame_id": info.get("parentFrameId"), "url": info.get("url"),
                                        "parent_session": sid}
                self._new_sessions.append(child)
        elif method == "Target.detachedFromTarget":
            self.sessions.pop(params.get("sessionId"), None)
        elif method == "Target.targetCreated":
            # A window the page opened (a popup) is attached so the broker SEES it, and is then never filled.
            info = params.get("targetInfo") or {}
            if info.get("type") == "page" and (info.get("openerId") or info.get("openerFrameId")) \
                    and info.get("browserContextId") == self.context_id:
                self._new_popups.append(info.get("targetId"))
        elif sid is not None and sid == self.top_session:
            if method == "Network.requestWillBeSent" and params.get("type") == "Document" \
                    and params.get("frameId") == self.top_target:
                self.hops.append((params.get("request") or {}).get("url") or "")
            elif method == "Page.frameNavigated" and not (params.get("frame") or {}).get("parentId"):
                self.hops.append((params.get("frame") or {}).get("url") or "")

    def in_popup(self, sid: str) -> bool:
        """True when the session belongs to any page target other than the one the broker opened."""
        seen = 0
        while sid and seen < 16:
            seen += 1
            if sid == self.top_session:
                return False
            info = self.sessions.get(sid) or {}
            if info.get("popup") or (info.get("type") == "page" and sid != self.top_session):
                return True
            sid = info.get("parent_session")
        return True

    def _service_sessions(self) -> None:
        while self._new_popups:
            tid = self._new_popups.pop(0)
            if tid == self.top_target:          # the page the broker created is never its own popup
                continue
            try:
                sid = self.cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
            except CDPError:
                continue
            self.sessions.setdefault(sid, {"target_id": tid, "type": "page", "parent_frame_id": None, "url": "",
                                           "parent_session": None})
            self.sessions[sid]["popup"] = True
            if sid not in self._new_sessions:
                self._new_sessions.append(sid)
        while self._new_sessions:
            sid = self._new_sessions.pop(0)
            try:
                self._enable(sid)
            except CDPError:
                pass

    def _enable(self, sid: str) -> None:
        self.cdp.send("Page.enable", session=sid)
        self.cdp.send("Runtime.enable", session=sid)
        self.cdp.send("Page.setLifecycleEventsEnabled", {"enabled": True}, session=sid)
        self.cdp.send("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True},
                      session=sid)

    # -- browser setup -------------------------------------------------------------------------------------------
    def open(self) -> None:
        self.cdp.send("Target.setDiscoverTargets", {"discover": True})
        self.context_id = self.cdp.send("Target.createBrowserContext", {"disposeOnDetach": True})["browserContextId"]
        target = self.cdp.send("Target.createTarget", {"url": "about:blank", "browserContextId": self.context_id})
        self.top_target = target["targetId"]
        sid = self.cdp.send("Target.attachToTarget", {"targetId": self.top_target, "flatten": True})["sessionId"]
        self.top_session = sid
        self.sessions[sid] = {"target_id": self.top_target, "type": "page", "parent_frame_id": None,
                              "url": "about:blank", "parent_session": None}
        self._enable(sid)
        self.cdp.send("Network.enable", session=sid)     # to see every redirect hop of the top document
        self.cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True}, session=sid)

    def close(self) -> None:
        try:
            if self.context_id:
                self.cdp.send("Target.disposeBrowserContext", {"browserContextId": self.context_id}, timeout=5)
        except CDPError:
            pass

    def hops_ok(self, start: int) -> tuple[bool, str | None]:
        """Every hop of the top document since `start` must be the approved origin itself or an origin an item URI
        names EXACTLY (a host rule). A base-domain rule is never honoured for a hop: a redirect has no committed
        document, so Chromium never gives its registrable domain, and the broker comparing its PSL with itself is
        no cross-check (round-3 review). One bad hop, even one that later bounces back to the right site, refuses."""
        for url in list(self.hops[start:]):
            if url == "about:blank":
                continue
            o = originmod.origin_of_url(url)
            if o is None:
                return False, url.split(":", 1)[0] + ":"
            if originmod.origin_equal(o, self.approved_origin):
                continue
            ok, _ = originmod.origin_allowed(o, self.rules, psl=self.psl, browser_registrable=None,
                                             lab_profile=self.lab_profile, host_rules_only=True)
            if not ok:
                return False, str(o)
        return True, None

    def navigate(self, url: str) -> None:
        start = self.cdp.event_count()
        hop_start = len(self.hops)
        res = self.cdp.send("Page.navigate", {"url": url}, session=self.top_session)
        if res.get("errorText"):
            raise Refusal("navigation_failed")
        self._wait_settled(start)
        ok, bad = self.hops_ok(hop_start)
        if not ok:
            raise Refusal("redirect_hop", bad)

    def _wait_settled(self, start: int) -> None:
        deadline = time.monotonic() + self.nav_timeout_s
        self.cdp.wait_event(lambda m, p, s: m == "Page.loadEventFired" and s == self.top_session, start=start,
                            timeout=self.nav_timeout_s)
        busy = {"Page.frameStartedLoading", "Page.frameNavigated", "Target.attachedToTarget", "Page.loadEventFired",
                "Page.frameStoppedLoading", "Page.lifecycleEvent"}
        idx = self.cdp.event_count()
        while time.monotonic() < deadline:
            self._service_sessions()
            ev = self.cdp.wait_event(lambda m, p, s: m in busy, start=idx, timeout=self.settle_s)
            idx = self.cdp.event_count()
            if ev is None and not self._new_sessions:
                return
        # not settled: carry on; a frame still loading has no committed document and is refused by the origin rule

    # -- frames --------------------------------------------------------------------------------------------------
    def _frames(self) -> list[_Frame]:
        self._service_sessions()
        seen: dict[str, _Frame] = {}
        for sid in list(self.sessions):
            info = self.sessions.get(sid)
            if not info:
                continue
            try:
                tree = self.cdp.send("Page.getFrameTree", session=sid)["frameTree"]
            except CDPError:
                continue
            stack = [(tree, info.get("parent_frame_id") if tree is not None else None)]
            while stack:
                node, parent = stack.pop()
                fr = node["frame"]
                fid = fr["id"]
                # An out-of-process iframe is reported by its own target, whose root frame id is the target id;
                # prefer that (local) entry over any placeholder in the parent's tree.
                local_root = info.get("target_id") == fid
                if fid not in seen or local_root:
                    seen[fid] = _Frame(session=sid, target_id=info["target_id"], frame=fr,
                                       parent_id=fr.get("parentId") or parent)
                for child in node.get("childFrames") or []:
                    stack.append((child, fid))
        return list(seen.values())

    def _eval(self, session: str, context_id: int, expression: str) -> dict:
        res = self.cdp.send("Runtime.evaluate", {"expression": expression, "contextId": context_id,
                                                 "returnByValue": True, "awaitPromise": False}, session=session)
        if res.get("exceptionDetails"):
            raise Refusal("frame_script_failed")
        value = (res.get("result") or {}).get("value")
        try:
            return json.loads(value) if isinstance(value, str) else {}
        except ValueError:
            return {}

    def _scan(self, mode: str) -> tuple[_Frame, int, dict, list[_Frame]]:
        frames = self._frames()
        by_id = {f.frame["id"]: f for f in frames}
        hits = []
        for f in frames:
            try:
                ctx = self.cdp.send("Page.createIsolatedWorld", {"frameId": f.frame["id"], "worldName": self.world,
                                                                 "grantUniveralAccess": False},
                                    session=f.session)["executionContextId"]
                facts = self._eval(f.session, ctx, FINDER_JS)
            except (CDPError, Refusal, KeyError):
                continue
            count = facts.get("otp" if mode == "otp" else "pw", 0)
            if count:
                hits.append((f, ctx, facts))
        if any(self.in_popup(f.session) for f, _, _ in hits):
            # Only the page the broker opened is ever filled (round-3 review): a popup has its own history of hops
            # and its own top document, and the simplest rule that cannot be bypassed is to never fill one.
            raise Refusal("popup_refused")
        if not hits:
            raise Refusal("no_login_form")
        if len(hits) > 1:
            raise Refusal("ambiguous_fields")
        f, ctx, facts = hits[0]
        ancestors = []
        pid = f.parent_id
        guard = 0
        while pid and guard < 32:
            guard += 1
            anc = by_id.get(pid)
            if anc is None:
                raise Refusal("ancestor_unknown")
            ancestors.append(anc)
            pid = anc.parent_id
        return f, ctx, facts, ancestors

    @staticmethod
    def _facts(f: _Frame, world: dict | None = None) -> originmod.FrameFacts:
        fr = f.frame
        return originmod.FrameFacts(frame_id=fr["id"], url=fr.get("url", "") + (fr.get("urlFragment") or ""),
                                    security_origin=fr.get("securityOrigin", ""),
                                    domain_and_registry=fr.get("domainAndRegistry"),
                                    world_origin=(world or {}).get("origin") if world is not None else None,
                                    secure=(world or {}).get("secure") if world is not None else None)

    def check_origin(self, f: _Frame, facts: dict, ancestors: list[_Frame]) -> originmod.Origin:
        fact = self._facts(f, facts)
        anc_facts = [self._facts(a) for a in ancestors]
        ok, reason, o = originmod.check_frame(fact, anc_facts, self.rules, psl=self.psl, lab_profile=self.lab_profile)
        if not ok:
            raise Refusal(reason, str(o) if o else None)
        top = anc_facts[-1] if anc_facts else fact
        top_origin, why = originmod.committed_origin(top, lab_profile=self.lab_profile)
        if not originmod.origin_equal(top_origin, self.approved_origin):
            raise Refusal("origin_not_approved", str(o))
        return o

    def check_targets(self, f: _Frame, ctx: int, frame_origin: originmod.Origin) -> dict:
        t = self._eval(f.session, ctx, TARGETS_JS)
        if t.get("form") and not t.get("connected", False):
            raise Refusal("navigated")
        for m in t.get("methods") or []:
            if m not in ("post", "dialog"):
                raise Refusal("form_method_get")
        for action in t.get("actions") or []:
            ok, reason = originmod.form_target_allowed(action, frame_origin, self.rules, psl=self.psl,
                                                       lab_profile=self.lab_profile)
            if not ok:
                raise Refusal(reason, str(frame_origin))
        return t

    # -- the binding ---------------------------------------------------------------------------------------------
    def bind(self, f: _Frame, ctx: int, ancestors: list[_Frame], index: int) -> Binding:
        watched = {f.frame["id"], *(a.frame["id"] for a in ancestors)}
        targets = {f.target_id, self.top_target}
        sessions = {f.session, self.top_session}
        urls = {}
        for t in targets:
            try:
                urls[t] = self.cdp.send("Target.getTargetInfo", {"targetId": t})["targetInfo"].get("url", "")
            except (CDPError, KeyError):
                raise Refusal("navigated")
        return Binding(session=f.session, target_id=f.target_id, frame_id=f.frame["id"],
                       loader_id=f.frame.get("loaderId", ""), context_id=ctx, watched=watched, targets=targets,
                       sessions=sessions, target_urls=urls, index=index)

    def binding_valid(self, b: Binding) -> bool:
        for method, params, sid in self.cdp.events_since(b.index):
            if method in FRAME_EVENTS:
                fid = params.get("frameId") or (params.get("frame") or {}).get("id")
                if fid in b.watched:
                    return False
            elif method == "Runtime.executionContextDestroyed":
                if sid == b.session and params.get("executionContextId") == b.context_id:
                    return False
            elif method == "Runtime.executionContextsCleared":
                if sid in b.sessions:
                    return False
            elif method in ("Target.targetDestroyed", "Target.targetCrashed"):
                if params.get("targetId") in b.targets:
                    return False
            elif method == "Target.detachedFromTarget":
                if params.get("sessionId") in b.sessions or params.get("targetId") in b.targets:
                    return False
            elif method == "Target.targetInfoChanged":
                info = params.get("targetInfo") or {}
                tid = info.get("targetId")
                if tid in b.targets and b.target_urls.get(tid) and info.get("url") != b.target_urls.get(tid):
                    return False
            elif method == "Inspector.targetCrashed" and sid in b.sessions:
                return False
        return True

    def frame_unchanged(self, b: Binding) -> bool:
        try:
            tree = self.cdp.send("Page.getFrameTree", session=b.session)["frameTree"]
        except CDPError:
            return False
        stack = [tree]
        while stack:
            node = stack.pop()
            fr = node["frame"]
            if fr["id"] == b.frame_id:
                return fr.get("loaderId", "") == b.loader_id
            stack.extend(node.get("childFrames") or [])
        return False

    def insert(self, b: Binding, f: _Frame, which: str, value: str, frame_origin: originmod.Origin) -> None:
        """Focus, re-check everything, insert, confirm. Refuses with `navigated` if anything moved."""
        if not self.binding_valid(b) or not self.frame_unchanged(b):
            raise Refusal("navigated", str(frame_origin))
        self.check_targets(f, b.context_id, frame_origin)
        obj = self.cdp.send("Runtime.evaluate", {"expression": "globalThis.__zvUser" if which == "user"
                                                 else "globalThis.__zvField", "contextId": b.context_id},
                            session=f.session)
        object_id = (obj.get("result") or {}).get("objectId")
        if not object_id:
            raise Refusal("navigated", str(frame_origin))
        try:
            self.cdp.send("DOM.focus", {"objectId": object_id}, session=f.session)
        except CDPError:
            self._eval(f.session, b.context_id, "(() => { const el = globalThis.%s; el && el.focus(); return '{}'; })()"
                       % ("__zvUser" if which == "user" else "__zvField"))
        state = self._eval(f.session, b.context_id, f"({FOCUSED_JS})({json.dumps(which)})")
        if not (state.get("connected") and state.get("active")):
            raise Refusal("navigated", str(frame_origin))
        if which == "field" and state.get("type") != "password" and self._mode == "password":
            raise Refusal("not_a_password_field", str(frame_origin))
        world_origin = originmod.parse_origin(state.get("origin"))
        if world_origin != frame_origin:
            raise Refusal("navigated", str(frame_origin))
        # The focus handler above is page code; anything it started (navigation, document.open, detach) is an event
        # that arrived before this evaluate's reply on the same pipe, so this check is deterministic, not a race.
        if not self.binding_valid(b) or not self.frame_unchanged(b):
            raise Refusal("navigated", str(frame_origin))
        try:
            self.cdp.send("Input.insertText", {"text": value}, session=f.session)
        except CDPError:
            if f.session == self.top_session:
                raise
            # an out-of-process frame's target may not expose Input; the page target routes to the focused frame,
            # and the landed check below proves where the text went
            self.cdp.send("Input.insertText", {"text": value}, session=self.top_session)
        landed = self._eval(f.session, b.context_id, f"({LANDED_JS})({json.dumps(which)}, {len(value)})")
        if not landed.get("ok") or not self.binding_valid(b):
            raise Refusal("fill_misdirected", str(frame_origin))

    _mode = "password"

    def fill_and_submit(self, mode: str, values: list[tuple[str, str]]) -> str:
        """values: [("user", username), ("field", secret)] or [("field", code)]. Returns the frame origin."""
        self._mode = mode
        index = self.cdp.event_count()
        f, ctx, facts, ancestors = self._scan(mode)
        frame_origin = self.check_origin(f, facts, ancestors)
        picked = self._eval(f.session, ctx, f"({PICK_JS})({json.dumps(mode)})")
        if not picked.get("ok"):
            raise Refusal(picked.get("reason") or "ambiguous_fields", str(frame_origin))
        b = self.bind(f, ctx, ancestors, index)
        try:
            self.check_targets(f, ctx, frame_origin)
            for which, value in values:
                if which == "user" and (not picked.get("user") or not value):
                    continue
                self.insert(b, f, which, value, frame_origin)
            if not self.binding_valid(b) or not self.frame_unchanged(b):
                raise Refusal("navigated", str(frame_origin))
            checked = self.check_targets(f, ctx, frame_origin)
        except (CDPError, Refusal) as exc:
            # A DevTools call that fails because the page moved under us (its context destroyed, its frame gone) is
            # a navigation, and is reported as one; anything else keeps its own reason.
            if isinstance(exc, Refusal) and exc.reason != "frame_script_failed":
                raise
            if not self.binding_valid(b):
                raise Refusal("navigated", str(frame_origin)) from None
            raise
        start = self.cdp.event_count()
        self._submit_hop_index = len(self.hops)
        how = self.cdp.send("Runtime.evaluate", {"expression": f"({SUBMIT_JS})({json.dumps(checked)})",
                                                 "contextId": ctx, "returnByValue": True}, session=f.session)
        verdict = (how.get("result") or {}).get("value")
        if verdict == "changed":
            raise Refusal("form_action_mismatch", str(frame_origin))
        if verdict not in ("nokey", "requested", "submitted"):
            raise Refusal("frame_script_failed", str(frame_origin))
        if verdict == "nokey":
            for kind in ("keyDown", "keyUp"):
                self.cdp.send("Input.dispatchKeyEvent", {"type": kind, "key": "Enter", "code": "Enter",
                                                          "windowsVirtualKeyCode": 13, "text": "\r"},
                              session=f.session)
        self._wait_settled(start)
        return str(frame_origin)

    def outcome(self) -> str:
        """ok | needs_otp | login_failed | needs_owner (an unknown page: never guessed, never solved) |
        redirect_hop (after the submit the top document passed through an origin the item does not allow)."""
        ok, _ = self.hops_ok(self._submit_hop_index)
        if not ok:
            return "redirect_hop"
        for mode, result in (("password", "login_failed"), ("otp", "needs_otp")):
            try:
                self._scan(mode)
                return result
            except Refusal as r:
                if r.reason == "ambiguous_fields":
                    return "needs_owner"
        top = [f for f in self._frames() if f.session == self.top_session and not f.parent_id]
        if not top:
            return "needs_owner"
        o, _ = originmod.committed_origin(self._facts(top[0]), lab_profile=self.lab_profile)
        if o is None:
            return "needs_owner"
        ok, _ = originmod.origin_allowed(o, self.rules, psl=self.psl,
                                         browser_registrable=top[0].frame.get("domainAndRegistry"),
                                         lab_profile=self.lab_profile)
        return "ok" if ok else "needs_owner"

    def cookies(self) -> list[dict]:
        """The context's cookies, filtered to the approved origin's registrable domain (or exact host)."""
        res = self.cdp.send("Storage.getCookies", {"browserContextId": self.context_id})
        host = self.approved_origin.host
        want = self.psl.registrable_domain(host)
        out = []
        for c in res.get("cookies") or []:
            dom = (c.get("domain") or "").lstrip(".").lower()
            if want is not None:
                if self.psl.registrable_domain(dom) != want:
                    continue
            elif dom != host:
                continue
            out.append({k: c.get(k) for k in ("name", "value", "domain", "path", "expires", "httpOnly", "secure",
                                              "sameSite")})
        return out


def run_login(cdp: CDP, *, psl: PublicSuffixList, rules: list, approved_origin: str, login_url: str,
              username: str, password: str, lab_profile: bool, otp_wait=None) -> LoginResult:
    """The whole of 6.1 in one browser. `otp_wait()` blocks for a phone-typed code (or returns None)."""
    run = LoginRun(cdp, psl=psl, rules=rules, approved_origin=approved_origin, lab_profile=lab_profile)
    run.open()
    try:
        run.navigate(login_url)
        frame_origin = run.fill_and_submit("password", [("user", username), ("field", password)])
        result = run.outcome()
        filled = 1
        if result == "needs_otp":
            code = otp_wait() if otp_wait else None
            if not code:
                return LoginResult("needs_otp_timeout", frame_origin, filled=filled)
            frame_origin = run.fill_and_submit("otp", [("field", code)])
            filled += 1
            result = run.outcome()
        if result != "ok":
            return LoginResult(result, frame_origin, filled=filled)
        return LoginResult("ok", frame_origin, cookies=run.cookies(), filled=filled)
    finally:
        run.close()
