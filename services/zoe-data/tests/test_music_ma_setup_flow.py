"""Music Assistant 2.8.x vs 2.10+ provider-config API — the version switch.

MA 2.10 moved provider credentials into setup-flow-owned `setup_data`. On it,
the pre-2.10 `config/providers/save` silently ignores a new cookie (the panel's
Reconnect "succeeds" and changes nothing) and refuses a first connect;
`get_entries` takes `instance_id` only; `music/recommendations` is gone.
zoe-data reads the server version from `/info` and speaks the matching API.

Two fake MA servers stand in for the real ones at the HTTP layer
(`_ma_response` + `_ma_info`). Their shapes are taken from the 2.8.7 and
2.10.3 image sources (controllers/config/{providers,flows}.py,
music_assistant_models ProviderConfig.update / SetupFlowStep), NOT from
zoe-data's own assumptions:

- Fake210.save models ProviderConfig.update: only DECLARED option entries are
  written, so `cookie` is dropped; no instance_id -> error.
- Fake210 flows: setup/reconfigure -> FORM (secure prefill stripped), submit ->
  FINISH (reconfigure merges setup_data, clears last_error) or the same FORM
  with errors when the provider fails to load; a finished flow is dropped from
  the registry, so flows/get on it errors.

Negative control (run by hand, recorded in the PR): force `_ma_is_210_plus` to
False in `test_210_reconnect_applies_fresh_cookie_in_place` and it goes red on
the cookie assertion -- `test_legacy_path_against_210_is_a_silent_noop` keeps
that failure mode pinned permanently.
"""
from __future__ import annotations

import itertools
import logging

import pytest

import music_oauth
import music_service
import music_setup
from routers import music_setup as ms_router

pytestmark = pytest.mark.ci_safe


class _Resp:
    def __init__(self, status: int, body=None, text: str = ""):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        return self._body


_ERR = _Resp(500, text="Internal server error")


def _invalid(command):
    return _Resp(400, text=f"Invalid Command: {command}")


class _FakeMA:
    """Common store: instances keyed by instance_id."""

    version = "0.0.0"
    schema = 0

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.instances: dict[str, dict] = {}
        self._ids = itertools.count(1)

    async def info(self):
        return {"server_version": self.version, "schema_version": self.schema}

    async def response(self, command, timeout_s=None, **args):
        self.calls.append((command, args))
        handler = getattr(self, "cmd_" + command.replace("/", "__"), None)
        if handler is None:
            return _invalid(command)
        try:
            return handler(**args)
        except TypeError:  # wrong/missing args: MA's parse_arguments -> error response
            return _ERR

    def install(self, monkeypatch):
        monkeypatch.setattr(music_service, "_ma_info", self.info)
        monkeypatch.setattr(music_service, "_ma_response", self.response)
        return self

    def sent(self, command):
        return [a for c, a in self.calls if c == command]

    # shared read commands
    def cmd_config__providers(self, **_):
        return _Resp(200, [self._public(i) for i in self.instances.values()])

    def cmd_providers(self, **_):
        return _Resp(200, [{"domain": i["domain"], "available": not i.get("last_error")}
                           for i in self.instances.values()])

    def _public(self, inst):
        return {k: v for k, v in inst.items() if k != "setup_data"}


# Option entries (library sync etc.) that both versions keep in `values`.
_OPTIONS = {"library_sync_playlists": True, "log_level": "GLOBAL"}
_YTM_CRED_ENTRIES = [
    {"key": "username", "type": "string", "required": True, "default_value": None},
    {"key": "cookie", "type": "secure_string", "required": True, "default_value": None},
    {"key": "po_token_server_url", "type": "string", "required": True,
     "default_value": "http://127.0.0.1:4416"},
]


class Fake287(_FakeMA):
    version, schema = "2.8.7", 29

    def cmd_config__providers__get_entries(self, provider_domain=None, instance_id=None, action=None, values=None):
        if provider_domain is None:
            return _ERR  # 2.8.7 requires provider_domain
        return _Resp(200, list(_YTM_CRED_ENTRIES) + [
            {"key": k, "type": "boolean" if isinstance(v, bool) else "string", "default_value": v}
            for k, v in _OPTIONS.items()])

    def cmd_config__providers__save(self, provider_domain, values, instance_id=None):
        if instance_id is None:
            instance_id = f"{provider_domain}--new{next(self._ids)}"
            self.instances[instance_id] = {"instance_id": instance_id, "domain": provider_domain,
                                           "name": provider_domain, "values": {}}
        inst = self.instances[instance_id]
        inst["values"] = dict(values)  # 2.8.x: credentials live in values
        inst["last_error"] = None
        return _Resp(200, self._public(inst))

    def cmd_music__recommendations(self, **_):
        return _Resp(200, [{"name": "Listen again", "items": [
            {"media_type": "track", "name": "Song", "uri": "ytmusic://track/1", "item_id": "1",
             "artists": [{"name": "A"}]}]}])


class Fake210(_FakeMA):
    version, schema = "2.10.3", 32
    # providers whose setup is a form (the rest are zero-input, e.g. radiobrowser)
    FORM_PROVIDERS = {"ytmusic": _YTM_CRED_ENTRIES}

    def __init__(self):
        super().__init__()
        self.flows: dict[str, dict] = {}
        self._fids = itertools.count(1)
        # set by a test: the OAuth provider's authorize URL (EXTERNAL step)
        self.oauth_url: str | None = None

    # -- options-only API ----------------------------------------------------
    def cmd_config__providers__get_entries(self, instance_id):  # instance_id ONLY
        return _Resp(200, [{"key": k, "type": "boolean", "default_value": v} for k, v in _OPTIONS.items()])

    def cmd_config__providers__save(self, provider_domain, values, instance_id=None):
        if instance_id is None:
            return _ERR  # "Adding a provider is only possible through the setup flow"
        inst = self.instances[instance_id]
        for k, v in values.items():  # ProviderConfig.update: undeclared keys skipped
            if k in inst["values"]:
                inst["values"][k] = v
        return _Resp(200, self._public(inst))

    def cmd_config__providers__get(self, instance_id):
        inst = self.instances.get(instance_id)
        return _Resp(200, self._public(inst)) if inst else _ERR

    # -- setup flows ---------------------------------------------------------
    def _step(self, flow, **kw):
        step = {"flow_id": flow["id"], "step_id": kw.pop("step_id", "user"), "entries": [],
                "errors": {}, "result": None, "reason": None, "url": None}
        step.update(kw)
        flow["step"] = step
        return _Resp(200, step)

    def _form(self, flow, errors=None):
        prefill = flow.get("prefill") or {}
        entries = []
        for e in self.FORM_PROVIDERS[flow["domain"]]:
            e = dict(e)
            # reconfigure prefills from setup_data, but a SECRET is stripped
            e["value"] = None if e["type"] == "secure_string" else prefill.get(e["key"])
            entries.append(e)
        return self._step(flow, type="form", entries=entries, errors=errors or {})

    def _new_flow(self, domain, instance_id=None):
        flow = {"id": f"flow{next(self._fids)}", "domain": domain, "instance_id": instance_id}
        if instance_id:
            flow["prefill"] = dict(self.instances[instance_id]["setup_data"])
        self.flows[flow["id"]] = flow
        return flow

    def _create(self, domain, setup_data):
        iid = f"{domain}--new{next(self._ids)}"
        self.instances[iid] = {"instance_id": iid, "domain": domain, "name": domain,
                               "values": dict(_OPTIONS), "setup_data": dict(setup_data),
                               "last_error": None}
        return iid

    def cmd_config__providers__setup(self, provider_domain):
        if provider_domain not in self.FORM_PROVIDERS and self.oauth_url is None:
            iid = self._create(provider_domain, {})  # zero-input: created right away
            return _Resp(200, {"flow_id": "synth", "step_id": "finish", "type": "finish",
                               "result": {"instance_id": iid}})
        flow = self._new_flow(provider_domain)
        if self.oauth_url is not None:
            return self._step(flow, type="external", step_id="authorize", url=self.oauth_url)
        return self._form(flow)

    def cmd_config__providers__reconfigure(self, instance_id):
        inst = self.instances.get(instance_id)
        if inst is None:
            return _ERR
        flow = self._new_flow(inst["domain"], instance_id)
        if self.oauth_url is not None:
            return self._step(flow, type="external", step_id="authorize", url=self.oauth_url)
        return self._form(flow)

    def cmd_config__flows__submit(self, flow_id, values):
        flow = self.flows.get(flow_id)
        if flow is None or flow["step"]["type"] != "form":
            return _ERR
        merged = {}
        errors = {}
        for e in flow["step"]["entries"]:
            v = values.get(e["key"], e.get("value"))
            if v in (None, "") and e.get("required"):
                errors[e["key"]] = "required"
            merged[e["key"]] = v
        if errors:
            return self._form(flow, errors)
        if merged.get("cookie") == "REJECTED":  # the provider fails to load
            return self._form(flow, {"base": "Login failed"})
        return self._finish(flow, merged)

    def _finish(self, flow, merged):
        if flow["instance_id"]:
            inst = self.instances[flow["instance_id"]]
            inst["setup_data"] = {**inst["setup_data"], **merged}
            inst["last_error"] = None
            iid = flow["instance_id"]
        else:
            iid = self._create(flow["domain"], merged)
        self.flows.pop(flow["id"], None)  # finished flows leave the registry
        return _Resp(200, {"flow_id": flow["id"], "step_id": "finish", "type": "finish",
                           "result": {"instance_id": iid}})

    def cmd_config__flows__get(self, flow_id):
        flow = self.flows.get(flow_id)
        return _Resp(200, flow["step"]) if flow else _ERR

    def cmd_config__flows__abort(self, flow_id):
        self.flows.pop(flow_id, None)
        return _Resp(200, None)

    def complete_oauth(self, flow_id):
        """MA's hosted callback arrived: the flow finishes server-side."""
        self._finish(self.flows[flow_id], {"refresh_token": "tok"})


def _ytmusic_instance(fake, *, cookie="STALE", last_error="No stream formats found"):
    iid = "ytmusic--HemJN6vc"
    inst = {"instance_id": iid, "domain": "ytmusic", "name": "YouTube Music",
            "values": {**_OPTIONS, "library_sync_playlists": False}, "last_error": last_error}
    if isinstance(fake, Fake210):
        inst["setup_data"] = {"username": "jason", "cookie": cookie,
                              "po_token_server_url": "http://localhost:4416"}
    else:
        inst["values"].update({"username": "jason", "cookie": cookie,
                               "po_token_server_url": "http://localhost:4416"})
    fake.instances[iid] = inst
    return iid


@pytest.fixture(autouse=True)
def _fresh_version_cache(monkeypatch):
    monkeypatch.delenv("ZOE_YTMUSIC_POTOKEN_URL", raising=False)
    music_service._invalidate_ma_version()
    monkeypatch.setattr(music_service, "_recs_absent_logged", False)
    yield
    music_service._invalidate_ma_version()


# ── version detection ────────────────────────────────────────────────────────

@pytest.mark.parametrize("info,expected", [
    ({"server_version": "2.8.7", "schema_version": 29}, (2, 8, 7)),
    ({"server_version": "2.10.3", "schema_version": 32}, (2, 10, 3)),
    ({"server_version": "2.11.0b2", "schema_version": 33}, (2, 11, 0)),
    ({"server_version": "0.0.0", "schema_version": 32}, (2, 10, 0)),
    ({"server_version": "0.0.0", "schema_version": 29}, (2, 8, 0)),
    ({"server_version": "garbage"}, None),
    (None, None),
])
def test_parse_ma_version(info, expected):
    assert music_service._parse_ma_version(info) == expected


@pytest.mark.asyncio
async def test_version_is_cached_and_dropped_on_transport_failure(monkeypatch):
    reads = {"n": 0}

    async def info():
        reads["n"] += 1
        return {"server_version": "2.10.3", "schema_version": 32}
    monkeypatch.setattr(music_service, "_ma_info", info)
    assert await music_service._ma_is_210_plus() is True
    assert await music_service._ma_is_210_plus() is True
    assert reads["n"] == 1, "version must be cached per process"

    class Boom:  # MA went away (e.g. being re-created)
        def __init__(self, *a, **k): pass
        async def __aenter__(self): raise OSError("connection refused")
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(music_service.httpx, "AsyncClient", Boom)
    assert await music_service._ma_response("players/all") is None
    assert await music_service._ma_is_210_plus() is True
    assert reads["n"] == 2, "a transport failure must force a re-read of /info"


@pytest.mark.asyncio
async def test_unknown_version_takes_the_pre_210_path(monkeypatch):
    async def info():
        return None
    monkeypatch.setattr(music_service, "_ma_info", info)
    assert await music_service._ma_is_210_plus() is False
    assert music_service._ma_version_cache is None, "a failed read must not be cached"


# ── 2.10: reconnect / first connect / form / recommendations ─────────────────

@pytest.mark.asyncio
async def test_210_reconnect_applies_fresh_cookie_in_place(monkeypatch):
    fake = Fake210().install(monkeypatch)
    iid = _ytmusic_instance(fake)

    saved = await music_service.save_provider(
        "ytmusic", {"username": "jason", "cookie": "FRESH"}, instance_id=iid)

    assert saved is not None and saved["instance_id"] == iid
    inst = fake.instances[iid]
    assert inst["setup_data"]["cookie"] == "FRESH", "the new cookie was not applied"
    assert inst["setup_data"]["po_token_server_url"] == "http://localhost:4416"
    assert inst["last_error"] is None
    assert len(fake.instances) == 1, "reconnect minted a duplicate instance"
    assert inst["values"]["library_sync_playlists"] is False, "reconnect reset an instance setting"
    assert fake.sent("config/providers/reconfigure") == [{"instance_id": iid}]
    assert not fake.sent("config/providers/save"), "2.10 re-auth must not use providers/save"
    assert not fake.flows, "a finished flow was left running"


@pytest.mark.asyncio
async def test_210_panel_reconnect_end_to_end(monkeypatch):
    """The phone's /save (the panel Reconnect's last hop) on a 2.10 server."""
    fake = Fake210().install(monkeypatch)
    iid = _ytmusic_instance(fake)

    async def up(url):
        return True
    monkeypatch.setattr(music_service, "_potoken_reachable", up)
    monkeypatch.setattr(music_setup, "consume", lambda t: {"p": "ytmusic"})

    r = await ms_router.setup_save({"token": "t", "provider": "ytmusic",
                                    "values": {"username": "jason", "cookie": "FRESH"}})
    assert r["ok"] is True and r["reconnected"] is True
    assert fake.instances[iid]["setup_data"]["cookie"] == "FRESH"


@pytest.mark.asyncio
async def test_210_rejected_cookie_reports_failure_and_aborts(monkeypatch):
    fake = Fake210().install(monkeypatch)
    iid = _ytmusic_instance(fake)

    saved = await music_service.save_provider(
        "ytmusic", {"username": "jason", "cookie": "REJECTED"}, instance_id=iid)

    assert saved is None, "a rejected re-auth must not read as success"
    assert fake.instances[iid]["setup_data"]["cookie"] == "STALE"
    assert fake.sent("config/flows/abort"), "the rejected flow was left running"
    assert not fake.flows


@pytest.mark.asyncio
async def test_210_first_connect_uses_setup_flow(monkeypatch):
    fake = Fake210().install(monkeypatch)

    saved = await music_service.save_provider("ytmusic", {"username": "jason", "cookie": "NEW"})

    assert saved is not None
    (inst,) = fake.instances.values()
    assert inst["domain"] == "ytmusic" and inst["setup_data"]["cookie"] == "NEW"
    assert inst["setup_data"]["po_token_server_url"] == "http://localhost:4416"
    assert fake.sent("config/providers/setup") == [{"provider_domain": "ytmusic"}]


@pytest.mark.asyncio
async def test_210_free_provider_first_connect(monkeypatch):
    """Zero-input providers (radio) are created by `setup` itself (FINISH)."""
    fake = Fake210().install(monkeypatch)
    saved = await music_service.save_provider("radiobrowser", {})
    assert saved is not None and [i["domain"] for i in fake.instances.values()] == ["radiobrowser"]


@pytest.mark.asyncio
async def test_210_setup_form_reads_flow_entries_not_get_entries(monkeypatch):
    fake = Fake210().install(monkeypatch)

    form = await music_service.provider_setup_form("ytmusic")

    assert form is not None
    assert {f["key"] for f in form["fields"]} == {"username", "cookie"}  # PO-token field hidden
    assert all("provider_domain" not in a for a in fake.sent("config/providers/get_entries")), \
        "get_entries(provider_domain=) does not exist on 2.10"
    assert not fake.flows, "the form-read flow was not aborted"


@pytest.mark.asyncio
async def test_210_setup_form_for_free_and_oauth_starts_no_flow(monkeypatch):
    """A zero-input `setup` CREATES the instance — asking for a form must not."""
    fake = Fake210().install(monkeypatch)
    for provider in ("radiobrowser", "spotify"):
        form = await music_service.provider_setup_form(provider)
        assert form is not None and form["fields"] == []
    assert not fake.instances and not fake.sent("config/providers/setup")


@pytest.mark.asyncio
async def test_210_recommendations_absent_degrades_to_empty_shelf(monkeypatch, caplog):
    fake = Fake210().install(monkeypatch)
    caplog.set_level(logging.INFO, logger="music_service")

    first = await music_service.get_recommendations()
    second = await music_service.get_recommendations()

    assert first == second == {"available": False, "folders": []}
    assert not fake.sent("music/recommendations"), "a known-absent command was sent"
    lines = [r for r in caplog.records if "music/recommendations" in r.getMessage()]
    assert len(lines) == 1, "expected exactly one log line per process"


@pytest.mark.asyncio
async def test_legacy_path_against_210_is_a_silent_noop(monkeypatch):
    """WHY the switch exists: the pre-2.10 save against a 2.10 server 'succeeds'
    and leaves the stale cookie. If this ever stops holding, re-check the fakes."""
    fake = Fake210().install(monkeypatch)
    iid = _ytmusic_instance(fake)

    async def pre210(fresh=False):
        return False
    monkeypatch.setattr(music_service, "_ma_is_210_plus", pre210)

    saved = await music_service.save_provider(
        "ytmusic", {"username": "jason", "cookie": "FRESH"}, instance_id=iid)

    assert saved is not None  # reported as success...
    assert fake.instances[iid]["setup_data"]["cookie"] == "STALE"  # ...changed nothing


# ── 2.8.7: the pre-2.10 path is unchanged ────────────────────────────────────

@pytest.mark.asyncio
async def test_287_reconnect_uses_providers_save(monkeypatch):
    fake = Fake287().install(monkeypatch)
    iid = _ytmusic_instance(fake)

    saved = await music_service.save_provider(
        "ytmusic", {"username": "jason", "cookie": "FRESH"}, instance_id=iid)

    assert saved is not None
    (args,) = fake.sent("config/providers/save")
    assert args["instance_id"] == iid and args["provider_domain"] == "ytmusic"
    assert args["values"]["cookie"] == "FRESH"
    assert args["values"]["library_sync_playlists"] is False, "existing setting not preserved"
    assert args["values"]["po_token_server_url"] == "http://localhost:4416"
    assert fake.sent("config/providers/get_entries") == [{"provider_domain": "ytmusic"}]
    assert not fake.sent("config/providers/reconfigure") and not fake.sent("config/flows/submit")


@pytest.mark.asyncio
async def test_287_first_connect_and_form(monkeypatch):
    fake = Fake287().install(monkeypatch)
    form = await music_service.provider_setup_form("ytmusic")
    assert {f["key"] for f in form["fields"]} == {"username", "cookie"}
    saved = await music_service.save_provider("ytmusic", {"username": "j", "cookie": "c"})
    assert saved is not None
    (args,) = fake.sent("config/providers/save")
    assert "instance_id" not in args
    assert not fake.sent("config/providers/setup")


@pytest.mark.asyncio
async def test_287_recommendations_still_served(monkeypatch):
    fake = Fake287().install(monkeypatch)
    r = await music_service.get_recommendations()
    assert r["available"] is True and r["folders"][0]["name"] == "Listen again"
    assert fake.sent("music/recommendations")


# ── 2.10 OAuth (music_oauth) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_210_oauth_surfaces_url_and_connects_after_callback(monkeypatch):
    fake = Fake210().install(monkeypatch)
    fake.oauth_url = "https://accounts.spotify.com/authorize?x=1"
    monkeypatch.setattr(music_oauth, "_FLOW_POLL_S", 0)

    polls = {"n": 0}
    real_get = fake.cmd_config__flows__get

    def get_then_callback(flow_id):
        polls["n"] += 1
        if polls["n"] == 2:  # the user finished signing in on the phone
            fake.complete_oauth(flow_id)
        return real_get(flow_id)
    fake.cmd_config__flows__get = get_then_callback

    res = await music_oauth.start_oauth("spotify")
    assert res["auth_url"] == fake.oauth_url
    await music_oauth._flows[res["oauth_id"]]["task"]
    st = music_oauth.oauth_status(res["oauth_id"])
    assert st["state"] == "connected", st
    assert [i["domain"] for i in fake.instances.values()] == ["spotify"]
    music_oauth._flows.pop(res["oauth_id"], None)


@pytest.mark.asyncio
async def test_210_oauth_reauth_is_in_place(monkeypatch):
    fake = Fake210().install(monkeypatch)
    fake.instances["spotify--EX"] = {"instance_id": "spotify--EX", "domain": "spotify",
                                     "name": "Spotify", "values": dict(_OPTIONS),
                                     "setup_data": {"refresh_token": "old"},
                                     "last_error": "token expired"}
    fake.oauth_url = "https://accounts.spotify.com/authorize?x=2"
    monkeypatch.setattr(music_oauth, "_FLOW_POLL_S", 0)
    real_get = fake.cmd_config__flows__get

    def get_then_callback(flow_id):
        if flow_id in fake.flows:
            fake.complete_oauth(flow_id)
        return real_get(flow_id)
    fake.cmd_config__flows__get = get_then_callback

    res = await music_oauth.start_oauth("spotify")
    await music_oauth._flows[res["oauth_id"]]["task"]
    assert music_oauth.oauth_status(res["oauth_id"])["state"] == "connected"
    assert list(fake.instances) == ["spotify--EX"], "OAuth re-auth minted a duplicate"
    assert fake.instances["spotify--EX"]["setup_data"]["refresh_token"] == "tok"
    assert fake.sent("config/providers/reconfigure") == [{"instance_id": "spotify--EX"}]
    music_oauth._flows.pop(res["oauth_id"], None)


# ── review fixes: fresh version on writes; a failed poll is not completion ───

@pytest.mark.asyncio
async def test_write_path_rereads_version_despite_warm_cache(monkeypatch):
    """MA re-created 2.8.7 -> 2.10.3 with no failed call in between: the warm
    cache still says 2.8.7, but a reconnect must re-read /info and take the
    2.10 path (the cached answer is the silent-no-op reconnect)."""
    fake = Fake210().install(monkeypatch)
    iid = _ytmusic_instance(fake)

    async def old_info():
        return {"server_version": "2.8.7", "schema_version": 29}
    monkeypatch.setattr(music_service, "_ma_info", old_info)
    assert await music_service.ma_server_version() == (2, 8, 7)  # cache primed
    monkeypatch.setattr(music_service, "_ma_info", fake.info)     # ...MA is now 2.10.3

    await music_service.save_provider("ytmusic", {"username": "jason", "cookie": "FRESH"}, instance_id=iid)

    assert fake.instances[iid]["setup_data"]["cookie"] == "FRESH", "reconnect used the stale cached version"


async def _run_oauth(monkeypatch, fake, provider="spotify"):
    monkeypatch.setattr(music_oauth, "_FLOW_POLL_S", 0)
    res = await music_oauth.start_oauth(provider)
    await music_oauth._flows[res["oauth_id"]]["task"]
    return res


@pytest.mark.asyncio
async def test_210_oauth_failed_polls_are_not_completion(monkeypatch):
    """flows/get failing (MA restarting, timeouts) with no provider-state change
    must end as an error — never 'connected' — and must not spend the token."""
    fake = Fake210().install(monkeypatch)
    fake.oauth_url = "https://accounts.spotify.com/authorize?x=3"
    fake.cmd_config__flows__get = lambda flow_id: _ERR  # every poll fails; flow still alive

    res = await _run_oauth(monkeypatch, fake)
    st = music_oauth.oauth_status(res["oauth_id"])
    assert st["state"] != "connected" and st["error"], st
    assert not fake.instances

    consumed = []
    monkeypatch.setattr(music_setup, "verify", lambda t: {"p": "spotify"})
    monkeypatch.setattr(music_setup, "consume", lambda t: consumed.append(t))
    r = await ms_router.oauth_status(oauth_id=res["oauth_id"], token="tok")
    assert r["state"] != "connected" and consumed == [], "setup token spent on an unconfirmed sign-in"
    music_oauth._flows.pop(res["oauth_id"], None)


@pytest.mark.asyncio
async def test_210_oauth_healthy_reauth_vanished_flow_is_not_completion(monkeypatch):
    """A healthy instance (no last_error) shows no transition, so a flow that
    disappears without reporting FINISH (e.g. it was aborted) is not success."""
    fake = Fake210().install(monkeypatch)
    fake.instances["spotify--EX"] = {"instance_id": "spotify--EX", "domain": "spotify",
                                     "name": "Spotify", "values": dict(_OPTIONS),
                                     "setup_data": {"refresh_token": "old"}, "last_error": None}
    fake.oauth_url = "https://accounts.spotify.com/authorize?x=4"
    real_get = fake.cmd_config__flows__get

    def get_after_abort(flow_id):
        fake.flows.pop(flow_id, None)  # the flow ended WITHOUT finishing
        return real_get(flow_id)
    fake.cmd_config__flows__get = get_after_abort

    res = await _run_oauth(monkeypatch, fake)
    assert music_oauth.oauth_status(res["oauth_id"])["state"] != "connected"
    assert fake.instances["spotify--EX"]["setup_data"]["refresh_token"] == "old"
    music_oauth._flows.pop(res["oauth_id"], None)
