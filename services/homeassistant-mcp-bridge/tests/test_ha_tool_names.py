"""HA LLM tool-name scheme: legacy (< 2026.9) vs domain-prefixed (>= 2026.9).

Negative controls throughout: a legacy HA must NOT get prefixed names, a 2026.9 HA
must NOT get legacy ones, and an unknown name must be rejected rather than passed
through — the failure mode this module exists to prevent is a silent
``HassTurnOn`` reaching a 2027.3 HA that no longer knows it.
"""

import importlib.util
import logging
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

BRIDGE_DIR = Path(__file__).resolve().parents[1]


def _load(name: str, module_name: str | None = None):
    """Load a bridge module by path. It is registered in sys.modules first because
    ``dataclasses`` on Python 3.10 resolves ``cls.__module__`` through sys.modules."""
    module_name = module_name or name
    spec = importlib.util.spec_from_file_location(module_name, BRIDGE_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tn():
    return _load("ha_tool_names")


# ── version parsing / scheme selection ────────────────────────────────────────

@pytest.mark.parametrize(
    "version,expected",
    [
        ("2026.5.2", (2026, 5, 2)),   # the live box today
        ("2026.9.3", (2026, 9, 3)),
        ("2026.10.0b3", (2026, 10, 0)),
        ("2027.3.0.dev20270201", (2027, 3, 0)),
        ("2026.9", (2026, 9, 0)),
    ],
)
def test_parse_ha_version(tn, version, expected):
    assert tn.parse_ha_version(version) == expected


@pytest.mark.parametrize("bad", ["", "stable", "9.3", "v2026.9.3", None])
def test_parse_ha_version_rejects_garbage(tn, bad):
    with pytest.raises(ValueError):
        tn.parse_ha_version(bad)


@pytest.mark.parametrize(
    "version,scheme",
    [
        ("2026.5.2", "legacy"),
        ("2026.8.9", "legacy"),      # last unprefixed monthly
        ("2026.9.0", "prefixed"),    # first prefixed
        ("2026.9.3", "prefixed"),
        ("2026.10.1", "prefixed"),   # month compare is numeric, not lexical (10 > 9)
        ("2027.3.0", "prefixed"),
    ],
)
def test_scheme_for_version(tn, version, scheme):
    assert tn.scheme_for_version(version) == scheme


# ── outbound: base name → what the connected HA expects ───────────────────────

def test_legacy_ha_gets_legacy_names(tn):
    assert tn.ha_tool_name("HassTurnOn", "legacy") == "HassTurnOn"
    assert tn.ha_tool_name("GetLiveContext", "legacy") == "GetLiveContext"
    assert tn.ha_tool_name("GetDateTime", "legacy") == "GetDateTime"
    # negative control: nothing prefixed leaks into the legacy scheme
    assert "__" not in "".join(tn.tool_table("legacy").values())


def test_prefixed_ha_gets_prefixed_names(tn):
    assert tn.ha_tool_name("HassTurnOn", "prefixed") == "intent__HassTurnOn"
    assert tn.ha_tool_name("GetLiveContext", "prefixed") == "homeassistant__GetLiveContext"
    assert tn.ha_tool_name("GetDateTime", "prefixed") == "llm__GetDateTime"
    # negative control: every prefixed spelling carries a namespace
    assert all("__" in v for v in tn.tool_table("prefixed").values())


def test_intents_are_prefixed_by_owning_domain_not_intent(tn):
    """The release notes' 'intent__' example does not generalise to every Hass* intent."""
    assert tn.ha_tool_name("HassLightSet", "prefixed") == "light__HassLightSet"
    assert tn.ha_tool_name("HassClimateSetTemperature", "prefixed") == "climate__HassClimateSetTemperature"
    assert tn.ha_tool_name("HassBroadcast", "prefixed") == "assist_satellite__HassBroadcast"
    assert tn.ha_tool_name("HassListAddItem", "prefixed") == "todo__HassListAddItem"
    assert tn.ha_tool_name("HassMediaPause", "prefixed") == "media_player__HassMediaPause"
    assert tn.ha_tool_name("HassLightSet", "prefixed") != "intent__HassLightSet"


def test_renamed_tools(tn):
    assert tn.ha_tool_name("calendar_get_events", "legacy") == "calendar_get_events"
    assert tn.ha_tool_name("calendar_get_events", "prefixed") == "calendar__get_events"
    assert tn.ha_tool_name("todo_get_items", "prefixed") == "todo__get_items"


def test_unknown_base_name_is_rejected_in_both_schemes(tn):
    for scheme in ("legacy", "prefixed"):
        with pytest.raises(tn.UnknownHaToolError):
            tn.ha_tool_name("HassMakeCoffee", scheme)
        with pytest.raises(tn.UnknownHaToolError):
            tn.ha_tool_name("intent__HassTurnOn", scheme)  # already-prefixed is not a base name


def test_bogus_scheme_is_rejected(tn):
    with pytest.raises(ValueError):
        tn.ha_tool_name("HassTurnOn", "v2")


def test_script_tool_names(tn):
    assert tn.script_tool_name("script.morning", "legacy") == "morning"
    assert tn.script_tool_name("morning", "legacy") == "morning"
    assert tn.script_tool_name("script.morning", "prefixed") == "script__morning"
    # legacy underscore rule for digit-leading ids; dropped once script__ leads
    assert tn.script_tool_name("script.3am_check", "legacy") == "_3am_check"
    assert tn.script_tool_name("script.3am_check", "prefixed") == "script__3am_check"
    with pytest.raises(tn.UnknownHaToolError):
        tn.script_tool_name("script.Bad Name", "prefixed")


# ── inbound: accept either scheme, reject unknown ─────────────────────────────

@pytest.mark.parametrize(
    "inbound,base",
    [
        ("HassTurnOn", "HassTurnOn"),
        ("intent__HassTurnOn", "HassTurnOn"),
        ("GetLiveContext", "GetLiveContext"),
        ("homeassistant__GetLiveContext", "GetLiveContext"),
        ("llm__GetDateTime", "GetDateTime"),
        ("light__HassLightSet", "HassLightSet"),
        ("calendar_get_events", "calendar_get_events"),
        ("calendar__get_events", "calendar_get_events"),
        ("script__morning", "script.morning"),
    ],
)
def test_normalize_accepts_both_schemes(tn, inbound, base):
    assert tn.normalize_tool_name(inbound) == base


@pytest.mark.parametrize(
    "bad",
    [
        "HassMakeCoffee",              # unknown core-looking name
        "intent__HassLightSet",        # right tool, WRONG domain — must not pass
        "homeassistant__HassTurnOn",   # wrong domain the other way
        "foo__bar",                    # unknown namespace + tool
        "script__Bad Name",            # script id that HA could never register
        "",
        None,
    ],
)
def test_normalize_rejects_unknown(tn, bad):
    with pytest.raises(tn.UnknownHaToolError):
        tn.normalize_tool_name(bad)


def test_table_round_trips(tn):
    """Every outbound spelling in both schemes normalises back to its base."""
    for scheme in ("legacy", "prefixed"):
        for base, spelled in tn.tool_table(scheme).items():
            assert tn.normalize_tool_name(spelled) == base
            assert tn.normalize_tool_name(spelled, scheme=scheme) == base


def test_script_names_round_trip_in_both_schemes(tn):
    """script_tool_name(...) → normalize_tool_name(..., scheme) is the identity on script ids."""
    for scheme in ("legacy", "prefixed"):
        for sid in ("morning", "3am_check", "night_mode_2"):
            spelled = tn.script_tool_name(f"script.{sid}", scheme)
            assert tn.normalize_tool_name(spelled, scheme=scheme) == f"script.{sid}"


def test_normalize_legacy_bare_script_names_only_under_legacy_scheme(tn):
    """A legacy HA emits bare script names (``morning``, ``_3am_check``); those are
    recognisable ONLY when the connected HA is known to be legacy — a bare unknown
    name under the prefixed scheme (or with no scheme) is still rejected."""
    assert tn.normalize_tool_name("morning", scheme="legacy") == "script.morning"
    assert tn.normalize_tool_name("_3am_check", scheme="legacy") == "script.3am_check"
    for bad_scheme in (None, "prefixed"):
        with pytest.raises(tn.UnknownHaToolError):
            tn.normalize_tool_name("morning", scheme=bad_scheme)
        with pytest.raises(tn.UnknownHaToolError):
            tn.normalize_tool_name("_3am_check", scheme=bad_scheme)
    with pytest.raises(ValueError):
        tn.normalize_tool_name("morning", scheme="v2")


def test_normalize_base_table_wins_over_legacy_script_shape(tn):
    """``calendar_get_events`` / ``todo_get_items`` are script-shaped bare names; the
    base table must be consulted first so they never come back as scripts."""
    assert tn.normalize_tool_name("calendar_get_events", scheme="legacy") == "calendar_get_events"
    assert tn.normalize_tool_name("todo_get_items", scheme="legacy") == "todo_get_items"
    assert tn.normalize_tool_name("HassTurnOn", scheme="legacy") == "HassTurnOn"


@pytest.mark.parametrize(
    "bad",
    [
        "3am_check",     # legacy HA spells this ``_3am_check``; the bare form is never emitted
        "_morning",      # leading ``_`` is only the digit rule; HA object ids cannot start with ``_``
        "morning_",      # trailing ``_`` is not a valid object id either
        "Morning",       # object ids are lowercase
        "my__script",    # ``__`` is not allowed inside an entity id (it is the prefix separator)
        "HassMakeCoffee",
    ],
)
def test_normalize_legacy_scheme_still_rejects_non_script_shapes(tn, bad):
    with pytest.raises(tn.UnknownHaToolError):
        tn.normalize_tool_name(bad, scheme="legacy")


# ── detection: once, cached, logged once ──────────────────────────────────────

class _Fetch:
    def __init__(self, version=None, exc=None):
        self.version, self.exc, self.calls = version, exc, 0

    async def __call__(self):
        self.calls += 1
        if self.exc:
            raise self.exc
        return {"version": self.version, "location_name": "Home"}


async def test_detect_legacy_from_live_version(tn, caplog):
    fetch = _Fetch("2026.5.2")
    det = tn.HaToolNameSchemeDetector(fetch, env={})
    with caplog.at_level(logging.INFO, logger="ha_tool_names"):
        first = await det.detect()
        second = await det.detect()
    assert first.scheme == "legacy" and first.ha_version == "2026.5.2" and first.source == "api"
    assert second is first
    assert fetch.calls == 1, "version must be read from /api/config exactly once"
    assert sum("HA LLM tool-name scheme" in r.getMessage() for r in caplog.records) == 1


async def test_detect_concurrent_first_calls_fetch_once(tn, caplog):
    """Two cold detect()s racing must share ONE /api/config call and ONE log line."""
    import asyncio

    gate = asyncio.Event()

    class _GatedFetch(_Fetch):
        async def __call__(self):
            self.calls += 1
            await gate.wait()  # hold the first fetch open so the second detect() overlaps it
            return {"version": self.version}

    fetch = _GatedFetch("2026.9.3")
    det = tn.HaToolNameSchemeDetector(fetch, env={})
    with caplog.at_level(logging.INFO, logger="ha_tool_names"):
        tasks = [asyncio.create_task(det.detect()) for _ in range(3)]
        await asyncio.sleep(0)  # let every task reach the cache check / fetch
        gate.set()
        results = await asyncio.gather(*tasks)
    assert fetch.calls == 1, f"detection ran {fetch.calls} times for concurrent first calls"
    assert all(r is results[0] for r in results)
    assert sum("HA LLM tool-name scheme" in r.getMessage() for r in caplog.records) == 1


async def test_detect_prefixed_after_upgrade(tn):
    det = tn.HaToolNameSchemeDetector(_Fetch("2026.9.3"), env={})
    assert (await det.detect()).scheme == "prefixed"


async def test_detect_reset_re_reads(tn):
    fetch = _Fetch("2026.5.2")
    det = tn.HaToolNameSchemeDetector(fetch, env={})
    assert (await det.detect()).scheme == "legacy"
    fetch.version = "2026.9.3"
    assert (await det.detect()).scheme == "legacy", "cache must hold until reset"
    det.reset()
    assert (await det.detect()).scheme == "prefixed"
    assert fetch.calls == 2


async def test_detect_env_pin_wins_and_skips_api(tn):
    fetch = _Fetch("2026.5.2")
    det = tn.HaToolNameSchemeDetector(fetch, env={"HA_TOOL_NAME_SCHEME": "prefixed"})
    got = await det.detect()
    assert got.scheme == "prefixed" and got.source == "env"
    assert fetch.calls == 0


async def test_detect_env_pin_rejects_garbage(tn):
    det = tn.HaToolNameSchemeDetector(_Fetch("2026.5.2"), env={"HA_TOOL_NAME_SCHEME": "v2"})
    with pytest.raises(ValueError):
        await det.detect()


async def test_detect_failure_is_loud_unless_fail_open(tn):
    det = tn.HaToolNameSchemeDetector(_Fetch(exc=RuntimeError("HA down")), env={})
    with pytest.raises(RuntimeError):
        await det.detect()
    assert det.cached is None, "a failed detection must not be cached as a verdict"
    got = await det.detect(fail_open=True)
    assert got.scheme == "legacy" and got.source == "default"


async def test_detect_unparseable_version_is_loud(tn):
    det = tn.HaToolNameSchemeDetector(_Fetch("stable"), env={})
    with pytest.raises(ValueError):
        await det.detect()


# ── bridge endpoints ──────────────────────────────────────────────────────────

@pytest.fixture()
def bridge(monkeypatch):
    # main.py imports ha_tool_names by bare name (it lives beside it at /app); mirror that here.
    monkeypatch.syspath_prepend(str(BRIDGE_DIR))
    sys.modules.pop("ha_tool_names", None)
    return _load("main", "ha_mcp_bridge_main_tool_names")


async def test_tools_names_endpoint_legacy_and_prefixed(bridge):
    for version, scheme, expect in (
        ("2026.5.2", "legacy", "HassTurnOn"),
        ("2026.9.3", "prefixed", "intent__HassTurnOn"),
    ):
        bridge.tool_name_scheme = bridge.HaToolNameSchemeDetector(_Fetch(version), env={})
        body = await bridge.get_tool_names()
        assert body["scheme"] == scheme and body["ha_version"] == version
        assert body["tools"]["HassTurnOn"] == expect
        assert body["unprefixed_breaks_in"] == "2027.3"


async def test_resolve_endpoint_accepts_both_and_404s_unknown(bridge):
    bridge.tool_name_scheme = bridge.HaToolNameSchemeDetector(_Fetch("2026.9.3"), env={})
    a = await bridge.resolve_tool_name("HassTurnOn")
    b = await bridge.resolve_tool_name("intent__HassTurnOn")
    assert a == b and a["active"] == "intent__HassTurnOn" and a["legacy"] == "HassTurnOn"
    s = await bridge.resolve_tool_name("script__morning")
    assert s["base"] == "script.morning" and s["active"] == "script__morning"
    with pytest.raises(bridge.HTTPException) as ei:
        await bridge.resolve_tool_name("HassMakeCoffee")
    assert ei.value.status_code == 404
    assert "HassMakeCoffee" in ei.value.detail  # named in the error, never echoed as valid


async def test_resolve_endpoint_legacy_bare_script_names(bridge):
    bridge.tool_name_scheme = bridge.HaToolNameSchemeDetector(_Fetch("2026.5.2"), env={})
    got = await bridge.resolve_tool_name("morning")
    assert got["base"] == "script.morning" and got["legacy"] == "morning"
    assert got["prefixed"] == "script__morning" and got["active"] == "morning"
    digit = await bridge.resolve_tool_name("_3am_check")
    assert digit["base"] == "script.3am_check" and digit["active"] == "_3am_check"
    assert digit["prefixed"] == "script__3am_check"
    # prefixed spelling is accepted on a legacy HA too (either scheme, as promised) …
    assert (await bridge.resolve_tool_name("script__morning"))["active"] == "morning"
    # … but a bare unknown name is still 404, never a script
    with pytest.raises(bridge.HTTPException) as ei:
        await bridge.resolve_tool_name("HassMakeCoffee")
    assert ei.value.status_code == 404
    # negative control: a prefixed HA never emits bare script names — 404, not a guess
    bridge.tool_name_scheme = bridge.HaToolNameSchemeDetector(_Fetch("2026.9.3"), env={})
    with pytest.raises(bridge.HTTPException) as ei:
        await bridge.resolve_tool_name("morning")
    assert ei.value.status_code == 404


async def test_root_health_reports_cached_scheme_without_extra_call(bridge, monkeypatch):
    class FakeHa:
        async def get_states(self):
            return [{"entity_id": "light.x"}]

    monkeypatch.setattr(bridge, "ha_bridge", FakeHa())
    fetch = _Fetch("2026.9.3")
    bridge.tool_name_scheme = bridge.HaToolNameSchemeDetector(fetch, env={})
    assert (await bridge.root())["tool_name_scheme"] == "undetected"
    await bridge.tool_name_scheme.detect()
    assert (await bridge.root())["tool_name_scheme"] == "prefixed"
    assert fetch.calls == 1
