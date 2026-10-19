"""The synthetic items. Every secret value is generated per run and contains the run's CANARY marker."""

from __future__ import annotations

from hosts import (BANK, BASE_LOGIN, BASE_WWW, EVIL, HOMOGRAPH, LOOKALIKE, PRIV_EVIL, PRIV_GOOD, RIGHT, SHOP,
                   SUFFIXED, WIDGET)

R = f"https://{RIGHT}"
HOST, BASE, REGEX = 1, 0, 4


def go(target: str) -> str:
    return f"{R}/go?to={target}"


# key: (org, uris [(uri, match)], login_url, category, host that validates the credentials, needs_otp)
ZOE_ITEMS = {
    "lib": ("A", [(R, None)], f"{R}/login", "other", RIGHT, False),
    "otp": ("A", [(R, None)], f"{R}/login-otp", "other", RIGHT, True),
    "inject": ("A", [(R, None)], f"{R}/login-inject", "other", RIGHT, False),
    "hold": ("A", [(R, None)], f"{R}/login-hold", "other", RIGHT, False),
    "hold_long": ("A", [(R, None)], f"{R}/login-hold-long", "other", RIGHT, False),
    "bank": ("A", [(f"https://{BANK}", HOST)], f"https://{BANK}/login", "money", BANK, False),
    "widget": ("A", [(f"https://{SHOP}", HOST), (f"https://{WIDGET}", HOST)], f"https://{SHOP}/checkout-login",
               "shopping", WIDGET, False),
    "basepos": ("A", [(f"https://{BASE_WWW}", BASE)], f"https://{BASE_LOGIN}/login", "other", BASE_LOGIN, False),
    "v1neg": ("A", [("https://other.example.test", HOST)], f"{R}/login", "other", RIGHT, False),
    # V2: each starts on the right site and ends on a look-alike or in a frame the rule must refuse
    "lk_examp1e": ("A", [(R, None)], go(f"https://{LOOKALIKE}/login"), "other", LOOKALIKE, False),
    "lk_homograph": ("A", [(R, None)], go(f"https://{HOMOGRAPH}/login"), "other", HOMOGRAPH, False),
    "lk_suffixed": ("A", [(R, None)], go(f"https://{SUFFIXED}/login"), "other", SUFFIXED, False),
    "lk_priv": ("A", [(f"https://{PRIV_GOOD}", BASE)], f"https://{PRIV_GOOD}/go?to=https://{PRIV_EVIL}/login",
                "other", PRIV_EVIL, False),
    # the same sibling, but named directly as the login page: refused before any navigation (login_url not covered)
    "lk_priv2": ("A", [(f"https://{PRIV_GOOD}", BASE)], f"https://{PRIV_EVIL}/login", "other", PRIV_EVIL, False),
    "lk_http":("A", [(R, None)], go(f"http://{RIGHT}/login"), "other", RIGHT, False),
    "lk_framed": ("A", [(R, None)], f"{R}/framed-evil", "other", EVIL, False),
    "lk_blank": ("A", [(R, None)], f"{R}/frame-blank", "other", RIGHT, False),
    "lk_srcdoc": ("A", [(R, None)], f"{R}/frame-srcdoc", "other", RIGHT, False),
    "lk_data": ("A", [(R, None)], f"{R}/frame-data", "other", RIGHT, False),
    "lk_blob": ("A", [(R, None)], f"{R}/frame-blob", "other", RIGHT, False),
    "lk_sandbox": ("A", [(R, None)], f"{R}/frame-sandbox", "other", RIGHT, False),
    # a disallowed hop that bounces back: right site -> evil.test -> right site's login page (review finding 2)
    "lk_bounce": ("A", [(R, None)], go(f"https://{EVIL}/go?to={R}/login"), "other", RIGHT, False),
    # round 3: a hop through a BASE-DOMAIN sibling (www.example.org) the browser never cross-checks
    "lk_basehop": ("A", [(f"https://{BASE_WWW}", BASE)],
                   f"https://{BASE_LOGIN}/go?to=https://{BASE_WWW}/go?to=https://{BASE_LOGIN}/login", "other",
                   BASE_LOGIN, False),
    "lk_redirect": ("A", [(R, None)], go(f"https://{EVIL}/login"), "other", EVIL, False),
    # 6.4 form checks
    "fa_action": ("A", [(R, None)], f"{R}/login-formaction", "other", RIGHT, False),
    "fa_button": ("A", [(R, None)], f"{R}/login-buttonaction", "other", RIGHT, False),
    "fa_get": ("A", [(R, None)], f"{R}/login-get", "other", RIGHT, False),
    # V13
    "race_nav": ("A", [(R, None)], f"{R}/race/nav", "other", RIGHT, False),
    "race_swap": ("A", [(R, None)], f"{R}/race/swap", "other", RIGHT, False),
    "race_oopif": ("A", [(f"https://{SHOP}", HOST), (f"https://{WIDGET}", HOST)], f"https://{SHOP}/race-widget",
                   "other", WIDGET, False),
    # V4 / V6 / V0
    "rev_a": ("A", [(R, None)], f"{R}/login", "other", RIGHT, False),
    "rev_b": ("A", [(R, None)], f"{R}/login", "other", RIGHT, False),
    "rev_c": ("A", [(R, None)], f"{R}/login", "other", RIGHT, False),
    "rev_d": ("A", [(R, None)], f"{R}/login", "other", RIGHT, False),
    "rev_e": ("A", [(R, None)], f"{R}/login", "other", RIGHT, False),
    "rev_f": ("A", [(R, None)], f"{R}/login", "other", RIGHT, False),
    # round 3: approved with one URI, then the owner adds evil.test; its login page bounces through evil.test
    "cap_d": ("A", [(R, None)], go(f"https://{EVIL}/go?to={R}/login"), "other", RIGHT, False),
    "cap_a": ("A", [(R, None)], f"{R}/login", "other", RIGHT, False),
    "cap_b": ("A", [(R, None)], f"{R}/login", "other", RIGHT, False),
    "cap_c": ("A", [(R, None)], f"{R}/login", "other", RIGHT, False),
    "c2host": ("A", [(R, HOST)], f"{R}/login", "other", RIGHT, False),
    "c2regex": ("A", [(r"^https://.*example.*$", REGEX)], f"{R}/login", "other", RIGHT, False),
    "bob_lib": ("B", [(R, None)], f"{R}/login", "other", RIGHT, False),
    "house": ("H", [(R, None)], f"{R}/login", "utility", RIGHT, False),
}

# Look-alike URLs for the client-alone matching matrix (V0, cell V2c)
LOOKALIKE_URLS = {
    "examp1e": f"https://{LOOKALIKE}/login",
    "homograph": f"https://{HOMOGRAPH}/login",
    "suffixed": f"https://{SUFFIXED}/login",
    "http": f"http://{RIGHT}/login",
    "evil": f"https://{EVIL}/login",
}

FAMILY_BULK = {"alice": 400, "bob": 300, "carol": 200, "kid": 50, "F": 100}
