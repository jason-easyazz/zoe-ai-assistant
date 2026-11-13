"""Display-preferences defaults — the panel must never power the screen fully
off; it drifts to a dimmed sleep screen instead.

Regression: `off_enabled` used to default True, so after off_seconds the
panel-agent ramped the backlight to 0 (black screen). No `display_preferences`
rows are stored, so the default governs the live panel.
"""
import pytest

pytestmark = pytest.mark.ci_safe

from routers.system import _row_to_prefs, _DEFAULT_DISPLAY_PREFS


def test_default_never_powers_screen_off():
    prefs = _row_to_prefs(None)          # no stored row → code defaults
    assert prefs["off_enabled"] is False


def test_default_enables_dimmed_sleep_drift():
    prefs = _row_to_prefs(None)
    assert prefs["sleep_enabled"] is True
    assert prefs["sleep_seconds"] == 180
    # the idle DIM (not off) still applies before sleep
    assert prefs["idle_enabled"] is True
    assert 0 < prefs["idle_brightness"] < 100


def test_stored_row_can_re_enable_off():
    # off_enabled stays a togglable pref: a stored True is honoured.
    prefs = _row_to_prefs({"off_enabled": True})
    assert prefs["off_enabled"] is True


# ── every advertised pref must actually be persisted ─────────────────────────

def test_every_advertised_pref_is_persisted_by_the_put():
    """Regression + guard for the whole bug class.

    `sleep_enabled`/`sleep_seconds` were added to `_DEFAULT_DISPLAY_PREFS` and read
    by the panel at boot (home.html sizes its idle-sleep window from them), but the
    table had no columns and the PUT's INSERT enumerates columns EXPLICITLY — so
    every write silently dropped them and the operator's sleep choice could never
    be saved. It hid because a GET looked correct: `_row_to_prefs` falls back to the
    defaults, which is exactly what the caller expected to see.

    An enumerated column list can't be trusted to keep up with the defaults dict by
    hand, so assert the invariant instead of two specific columns: anything the API
    advertises must be persisted. This fails the day someone adds the next pref
    without a column.
    """
    import inspect
    import re
    from routers import system

    src = inspect.getsource(system.put_display_preferences)
    m = re.search(r"INSERT INTO display_preferences \(\s*(.*?)\)\s*VALUES", src, re.S)
    assert m, "couldn't find the display_preferences INSERT — did the writer move?"
    persisted = {c.strip() for c in m.group(1).replace("\n", " ").split(",") if c.strip()}

    advertised = set(system._DEFAULT_DISPLAY_PREFS)
    missing = advertised - persisted
    assert not missing, (
        f"advertised in _DEFAULT_DISPLAY_PREFS but never written by the PUT: "
        f"{sorted(missing)} — a PUT would silently drop them and the setting "
        f"would always read back as its default"
    )


def test_every_advertised_pref_is_refreshed_on_conflict():
    """Same invariant as the INSERT test, applied to the upsert's set-list.

    The INSERT is an upsert, so a device that already has a row takes the
    ON CONFLICT path. A column listed in the INSERT but missing from the DO UPDATE
    set-list persists the FIRST write and silently ignores every later edit — a
    nastier variant of the bug this PR fixes, because it looks like it works once.

    Asserting the invariant (everything advertised must be refreshed) rather than
    naming sleep_enabled/sleep_seconds: hardcoding the two columns would be exactly
    the narrowness that let the original bug through, and would pass unchanged the
    day someone adds the next pref.
    """
    import inspect
    import re
    from routers import system

    src = inspect.getsource(system.put_display_preferences)
    m = re.search(r"ON CONFLICT\(device_id\) DO UPDATE SET(.*?)\"\"\"", src, re.S)
    assert m, "couldn't find the ON CONFLICT set-list — did the writer move?"
    refreshed = {mm.group(1) for mm in re.finditer(r"(\w+)\s*=\s*excluded\.", m.group(1))}

    advertised = set(system._DEFAULT_DISPLAY_PREFS)
    missing = advertised - refreshed
    assert not missing, (
        f"advertised in _DEFAULT_DISPLAY_PREFS but not refreshed on conflict: "
        f"{sorted(missing)} — the first write would stick and every later edit "
        f"would be silently dropped for any device that already has a row"
    )


# ── "All panels (default)" is read by a panel that has no row of its own ────────────────────────

class _Rows(dict):
    def keys(self):
        return super().keys()


class _FakeDb:
    def __init__(self, rows, panels=None):
        self.rows = rows
        self.panels = panels or {}      # registered address -> [panel_id, ...] (active panels only)

    async def execute(self, sql, params):
        if "FROM panels" in sql:
            found = [_Rows(panel_id=p) for p in self.panels.get(params[0], [])]
        else:
            found = [self.rows[params[0]]] if params[0] in self.rows else []

        class _Cur:
            async def fetchone(self_inner):
                return found[0] if found else None

            async def fetchall(self_inner):
                return found
        return _Cur()


def _request(peer="192.0.2.10", headers=()):
    from starlette.requests import Request
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [(k.encode(), v.encode()) for k, v in headers],
                    "client": (peer, 40000), "query_string": b""})


def _get(db, device_id, request=None):
    import asyncio

    from routers import system
    return asyncio.run(system.get_display_preferences(request=request, device_id=device_id, db=db))


def test_a_panel_without_its_own_row_reads_the_saved_default():
    db = _FakeDb({"default": _Rows(off_enabled=True, day_brightness=40, pi_host="10.0.0.9")})
    out = _get(db, "panel-kitchen")
    assert out["inherited"] is True and out["preferences"]["off_enabled"] is True and out["preferences"]["day_brightness"] == 40
    from routers import system
    assert out["pi_host"] == system._DEFAULT_PI_HOST          # the reload host is never inherited


def test_a_panels_own_row_wins_over_the_default_and_no_rows_means_code_defaults():
    db = _FakeDb({"default": _Rows(day_brightness=40), "panel-kitchen": _Rows(day_brightness=75)})
    own = _get(db, "panel-kitchen")
    assert own["inherited"] is False and own["preferences"]["day_brightness"] == 75
    none = _get(_FakeDb({}), "panel-kitchen")
    assert none["inherited"] is False and none["preferences"]["off_enabled"] is False     # control: the code default
    assert _get(db, "default")["inherited"] is False


# ── a per-panel row reaches the panel AGENT, which polls with its own device id ────────────────────────────────────

def test_the_agent_reads_its_panels_row_by_the_panels_registered_address():
    """Settings saves a panel's row under the panel id; the agent on that panel polls with ``pi-<MAC>``. The machine at the
    panel's registered address gets the panel's row, not the default."""
    db = _FakeDb({"default": _Rows(day_brightness=40), "panel-kitchen": _Rows(day_brightness=75, pi_host="192.0.2.10")},
                 panels={"192.0.2.10": ["panel-kitchen"]})
    out = _get(db, "pi-0000000000aa", _request("192.0.2.10"))
    assert out["inherited"] is False and out["preferences"]["day_brightness"] == 75 and out["pi_host"] == "192.0.2.10"
    # controls: another address, no request, or the public tunnel get the default; two panels at one address are not guessed
    assert _get(db, "pi-0000000000aa", _request("192.0.2.99"))["preferences"]["day_brightness"] == 40
    assert _get(db, "pi-0000000000aa")["preferences"]["day_brightness"] == 40
    assert _get(db, "pi-0000000000aa", _request("192.0.2.10", [("cf-ray", "x")]))["preferences"]["day_brightness"] == 40
    db.panels = {"192.0.2.10": ["panel-kitchen", "panel-hall"]}
    assert _get(db, "pi-0000000000aa", _request("192.0.2.10"))["preferences"]["day_brightness"] == 40


def test_the_devices_own_row_still_wins_and_a_panel_without_a_row_falls_to_the_default():
    db = _FakeDb({"default": _Rows(day_brightness=40), "pi-0000000000aa": _Rows(day_brightness=20),
                  "panel-kitchen": _Rows(day_brightness=75)}, panels={"192.0.2.10": ["panel-kitchen"]})
    assert _get(db, "pi-0000000000aa", _request("192.0.2.10"))["preferences"]["day_brightness"] == 20
    db = _FakeDb({"default": _Rows(day_brightness=40)}, panels={"192.0.2.10": ["panel-kitchen"]})
    out = _get(db, "pi-0000000000aa", _request("192.0.2.10"))
    assert out["inherited"] is True and out["preferences"]["day_brightness"] == 40


# ── the unauthenticated route believes an address header only from a proxy of ours (panel_principal.trusted_client_ip) ──
# trusted_client_ip: the TCP peer itself, unless the peer is in ZOE_TRUSTED_PROXY_NETS (default loopback + docker 172.16/12),
# when nginx's X-Real-IP is believed; X-Forwarded-For and Forwarded are never read (a client writes them); a relayed request
# has no address. The route also refuses any request carrying a Cloudflare edge header (_via_public_tunnel).

_SPOOF = [("x-real-ip", "192.0.2.10"), ("x-forwarded-for", "192.0.2.10"), ("forwarded", "for=192.0.2.10")]


def _kitchen_db():
    return _FakeDb({"default": _Rows(day_brightness=40), "panel-kitchen": _Rows(day_brightness=75)},
                   panels={"192.0.2.10": ["panel-kitchen"]})


@pytest.fixture
def _default_proxy_nets(monkeypatch):
    monkeypatch.delenv("ZOE_TRUSTED_PROXY_NETS", raising=False)
    monkeypatch.delenv("ZOE_RELAY_INGRESS", raising=False)


@pytest.mark.parametrize("header", _SPOOF)
def test_an_address_header_from_an_untrusted_peer_never_selects_a_panel(_default_proxy_nets, header):
    import panel_principal

    req = _request("192.0.2.50", [header])
    assert panel_principal.trusted_client_ip(req) == "192.0.2.50"                       # the peer, never the header
    assert _get(_kitchen_db(), "pi-0000000000aa", req)["preferences"]["day_brightness"] == 40
    # control: the same peer AT the panel's address is the panel
    assert _get(_kitchen_db(), "pi-0000000000aa", _request("192.0.2.10"))["preferences"]["day_brightness"] == 75


def test_a_trusted_proxy_is_believed_for_x_real_ip_only(_default_proxy_nets):
    import panel_principal

    via_nginx = _request("172.18.0.5", [("x-real-ip", "192.0.2.10")])
    assert panel_principal.trusted_client_ip(via_nginx) == "192.0.2.10"
    assert _get(_kitchen_db(), "pi-0000000000aa", via_nginx)["preferences"]["day_brightness"] == 75
    for header in _SPOOF[1:]:                     # X-Forwarded-For / Forwarded are not nginx's word, even from nginx's side
        req = _request("172.18.0.5", [header])
        assert panel_principal.trusted_client_ip(req) == "172.18.0.5"
        assert _get(_kitchen_db(), "pi-0000000000aa", req)["preferences"]["day_brightness"] == 40
    tunnel = _request("172.18.0.5", [("x-real-ip", "192.0.2.10"), ("cf-connecting-ip", "198.51.100.7")])
    assert _get(_kitchen_db(), "pi-0000000000aa", tunnel)["preferences"]["day_brightness"] == 40
