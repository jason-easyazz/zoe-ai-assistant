"""Guards the service worker's local-first Workbox vendoring.

Zoe runs on a LAN box that may be offline, so `dist/sw.js` must boot Workbox from
our own origin (`dist/workbox/`), never from Google's CDN. That is both an
availability property (an offline panel must still get a working SW) and a
privacy one (a CDN import pings Google from every client on every SW boot).

The subtle part these tests exist to protect: `workbox-sw.js` is only a lazy
LOADER. On first access of `workbox.core` / `workbox.routing` / ... it
importScripts()es that module, and its built-in default base URL is the CDN.
So vendoring workbox-sw.js is NOT sufficient - `modulePathPrefix` is what
actually keeps the module loads local. Dropping it would silently restore the
CDN dependency while every test that only looked at the importScripts line
still passed.

Stdlib-only + marked ci_safe so it runs in the fast GitHub lane.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
SW = REPO / "services" / "zoe-ui" / "dist" / "sw.js"
WORKBOX_DIR = REPO / "services" / "zoe-ui" / "dist" / "workbox"
NGINX = REPO / "services" / "zoe-ui" / "nginx.conf"

# workbox-sw.js's own namespace -> module-file-name map (from the 7.0.0 bundle).
# Keep in sync with upstream if a new namespace is used by sw.js.
NAMESPACE_TO_MODULE = {
    "backgroundSync": "background-sync",
    "broadcastUpdate": "broadcast-update",
    "cacheableResponse": "cacheable-response",
    "core": "core",
    "expiration": "expiration",
    "googleAnalytics": "offline-ga",
    "navigationPreload": "navigation-preload",
    "precaching": "precaching",
    "rangeRequests": "range-requests",
    "routing": "routing",
    "strategies": "strategies",
    "streams": "streams",
    "recipes": "recipes",
}


def _sw_source() -> str:
    return SW.read_text(encoding="utf-8")


def test_sw_does_not_import_scripts_from_any_external_origin():
    """No importScripts('https://...') - the CDN bootstrap must stay gone."""
    external = re.findall(
        r"""importScripts\(\s*['"](https?://[^'"]+)['"]""", _sw_source()
    )
    assert external == [], f"sw.js imports scripts from external origins: {external}"


def test_sw_sets_module_path_prefix_to_local_workbox():
    """The load-bearing setting: without it Workbox lazy-loads modules from the CDN."""
    src = _sw_source()
    match = re.search(r"""modulePathPrefix\s*:\s*['"]([^'"]+)['"]""", src)
    assert match is not None, (
        "sw.js must set workbox.setConfig({ modulePathPrefix: '/workbox/' }); "
        "without it workbox-sw.js fetches each module from Google's CDN."
    )
    assert match.group(1) == "/workbox/", (
        f"unexpected modulePathPrefix {match.group(1)!r}; vendored modules live "
        "at /workbox/"
    )


def test_sw_pins_the_prod_build_variant():
    """Only .prod.js files are vendored, so debug must stay false."""
    src = _sw_source()
    assert re.search(r"debug\s*:\s*false", src), (
        "sw.js must keep debug:false - debug:true makes Workbox request "
        ".dev.js modules, which are intentionally not vendored."
    )


def test_every_workbox_namespace_used_by_sw_is_vendored_locally():
    """Using a namespace we haven't vendored = a silent runtime CDN fetch / 404."""
    src = _sw_source()
    used = set(re.findall(r"workbox\.([a-zA-Z]+)", src))
    used &= set(NAMESPACE_TO_MODULE)  # ignore setConfig/loadModule/etc.
    assert used, "expected sw.js to use at least one workbox module namespace"

    missing = sorted(
        f"workbox-{NAMESPACE_TO_MODULE[ns]}.prod.js"
        for ns in used
        if not (WORKBOX_DIR / f"workbox-{NAMESPACE_TO_MODULE[ns]}.prod.js").is_file()
    )
    assert missing == [], (
        f"sw.js uses workbox namespaces whose modules are not vendored: {missing}. "
        f"Add them to {WORKBOX_DIR.relative_to(REPO)} "
        "(npx workbox-cli@7.0.0 copyLibraries)."
    )


def test_workbox_loader_is_vendored():
    assert (WORKBOX_DIR / "workbox-sw.js").is_file(), (
        "dist/workbox/workbox-sw.js missing - sw.js importScripts('/workbox/workbox-sw.js')"
    )


def test_nginx_csp_no_longer_allows_the_workbox_cdn():
    """sw.js was the only consumer; keep the allowance from creeping back."""
    conf = NGINX.read_text(encoding="utf-8")
    script_srcs = re.findall(r"script-src[^;]*;", conf)
    assert script_srcs, "expected a CSP script-src directive in nginx.conf"
    offenders = [d for d in script_srcs if "storage.googleapis.com" in d]
    assert offenders == [], (
        "nginx CSP still allows https://storage.googleapis.com in script-src; "
        "Workbox is vendored locally so the allowance should stay removed."
    )
