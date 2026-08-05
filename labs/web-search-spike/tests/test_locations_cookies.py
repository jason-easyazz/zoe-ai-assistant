"""Cookie-jar scoping — the NEGATIVE CONTROLS for cross-domain leakage.

A jar holds live session cookies for third-party retail sites. The failure that
matters is not "the jar is empty", it is "the jar handed Liquorland's session to
BWS". So most of this file is about what must NOT come out.

Offline: no network, no browser, `ZOE_STORE_COOKIE_DIR` pointed at tmp_path.
"""

from __future__ import annotations

import json
import time

import pytest

from websearch.locations.cookies import CookieJar, host_of, in_scope


def ck(name: str, value: str, domain: str) -> dict:
    return {"name": name, "value": value, "domain": domain, "path": "/"}


# ------------------------------------------------------------- scope algebra


@pytest.mark.parametrize(
    "cookie_domain,base,expected",
    [
        ("bws.com.au", "bws.com.au", True),
        (".bws.com.au", "bws.com.au", True),          # leading-dot spelling
        ("api.bws.com.au", "bws.com.au", True),       # subdomain: same site
        (".api.bws.com.au", "bws.com.au", True),
        ("liquorland.com.au", "bws.com.au", False),
        # THE ATTACK a substring test would allow. `"bws.com.au" in
        # "notbws.com.au"` is True, so a naive implementation leaks here.
        ("notbws.com.au", "bws.com.au", False),
        ("evilbws.com.au", "bws.com.au", False),
        # ...and the reverse: a parent must not receive a child's key.
        ("bws.com.au", "api.bws.com.au", False),
        ("", "bws.com.au", False),
        ("bws.com.au", "", False),
    ],
)
def test_in_scope_matches_on_a_label_boundary(cookie_domain, base, expected):
    assert in_scope(cookie_domain, base) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://api.bws.com.au/apis/ui/Product/1", "api.bws.com.au"),
        ("bws.com.au", "bws.com.au"),
        ("HTTPS://WWW.BWS.COM.AU/x", "www.bws.com.au"),
        ("https://user:pw@bws.com.au:443/x", "bws.com.au"),
        ("https://bws.com.au:8443", "bws.com.au"),
    ],
)
def test_host_of(raw, expected):
    assert host_of(raw) == expected


# ------------------------------------------------------------ the jar itself


def test_save_keeps_only_the_retailers_own_cookies(tmp_path):
    """A browser context holds cookies for every host the page touched."""
    jar = CookieJar(tmp_path)
    jar.save(
        "bws.com.au",
        [
            ck("store", "4328", ".bws.com.au"),
            ck("sess", "abc", "api.bws.com.au"),
            ck("_ga", "junk", ".doubleclick.net"),        # ad network
            ck("cf", "x", ".edgmedia.bws.com.au"),        # own CDN: in scope
            ck("other", "y", "liquorland.com.au"),        # a DIFFERENT retailer
        ],
        store_id="4328",
        store_label="BWS Geraldton",
    )
    names = {c["name"] for c in jar.load("bws.com.au")}
    assert names == {"store", "sess", "cf"}
    assert "_ga" not in names and "other" not in names


def test_cookie_jar_never_leaks_across_domains(tmp_path):
    """THE negative control, asserted in BOTH directions.

    (1) A correctly-saved jar must not answer for another domain.
    (2) A jar file POISONED on disk must not leak on READ either — the file is
        not a trusted input, so the read-side filter has to be real.
    """
    jar = CookieJar(tmp_path)
    jar.save("bws.com.au", [ck("store", "4328", ".bws.com.au")], store_id="4328")
    jar.save("liquorland.com.au", [ck("ll_store", "999", ".liquorland.com.au")])

    # (1) each jar answers only for itself
    assert [c["name"] for c in jar.load("bws.com.au")] == ["store"]
    assert [c["name"] for c in jar.load("liquorland.com.au")] == ["ll_store"]

    # ...and asking a jar for a foreign URL yields NOTHING, even though the jar
    # itself is populated. A caller cannot misuse a correctly-loaded jar.
    assert jar.as_header("bws.com.au", target_url="https://www.liquorland.com.au/x") == ""
    assert "store=4328" in jar.as_header("bws.com.au", target_url="https://api.bws.com.au/x")

    # (2) POISON the file directly, as a hand-edit or an older writer would.
    path = jar.path_for("bws.com.au")
    data = json.loads(path.read_text())
    data["cookies"].append(ck("stolen", "sekrit", ".liquorland.com.au"))
    path.write_text(json.dumps(data))

    loaded = {c["name"] for c in jar.load("bws.com.au")}
    assert "stolen" not in loaded, "read-side domain filter is not enforcing"
    assert "sekrit" not in jar.as_header("bws.com.au")


def test_stale_jar_returns_nothing_rather_than_a_stale_store(tmp_path):
    """An expired selection must degrade to 'no session', not to 'old session'.

    A cookie that outlives the selection it encoded is how a store-less price
    gets a confident store label attached to it — the exact lie this package
    exists to prevent.
    """
    jar = CookieJar(tmp_path, max_age_s=60)
    jar.save("bws.com.au", [ck("store", "4328", ".bws.com.au")], store_id="4328")
    assert jar.load("bws.com.au")
    assert jar.is_fresh("bws.com.au")

    path = jar.path_for("bws.com.au")
    data = json.loads(path.read_text())
    data["saved_at"] = time.time() - 3600  # an hour ago, cap is 60 s
    path.write_text(json.dumps(data))

    assert jar.is_fresh("bws.com.au") is False
    assert jar.load("bws.com.au") == []
    assert jar.meta("bws.com.au") == {}
    assert jar.as_header("bws.com.au") == ""


def test_missing_and_corrupt_jars_are_empty_not_explosive(tmp_path):
    jar = CookieJar(tmp_path)
    assert jar.load("never-saved.com.au") == []
    assert jar.meta("never-saved.com.au") == {}
    assert jar.age_s("never-saved.com.au") is None

    tmp_path.mkdir(exist_ok=True)
    jar.path_for("broken.com.au").write_text("{not json")
    assert jar.load("broken.com.au") == []


def test_jar_path_is_sanitised(tmp_path):
    """A domain string arrives from a URL. It must not become a path."""
    jar = CookieJar(tmp_path)
    p = jar.path_for("https://bws.com.au/../../etc/passwd")
    assert p.parent == tmp_path
    assert p.name == "bws.com.au.json"
    with pytest.raises(ValueError):
        jar.path_for("///")


def test_meta_round_trips_the_store_identity(tmp_path):
    jar = CookieJar(tmp_path)
    jar.save(
        "bws.com.au",
        [ck("store", "4328", ".bws.com.au")],
        store_id="4328",
        store_label="BWS Geraldton",
        local_storage={"selectedStore": "4328"},
    )
    meta = jar.meta("bws.com.au")
    assert meta["store_id"] == "4328"
    assert meta["store_label"] == "BWS Geraldton"
    assert meta["local_storage"] == {"selectedStore": "4328"}
    assert meta["age_s"] < 10


def test_clear_and_domains(tmp_path):
    jar = CookieJar(tmp_path)
    jar.save("bws.com.au", [ck("a", "1", ".bws.com.au")])
    jar.save("liquorland.com.au", [ck("b", "2", ".liquorland.com.au")])
    assert jar.domains() == ["bws.com.au", "liquorland.com.au"]
    assert jar.clear("bws.com.au") is True
    assert jar.clear("bws.com.au") is False
    assert jar.domains() == ["liquorland.com.au"]
