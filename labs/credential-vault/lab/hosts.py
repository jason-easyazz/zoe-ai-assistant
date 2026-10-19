"""Every host name in the lab. They resolve ONLY inside the lab's internal Docker network (network aliases on the
sites / Vaultwarden containers); the network has no route out, so none of these names ever reaches the internet."""

VAULT = "vault.lab.test"
VAULT_URL = "https://vault.lab.test:8443"

RIGHT = "login.example.test"                 # the item's site
SHOP = "shop.example.test"                   # a page that embeds a hosted sign-in widget
WIDGET = "widget.test"                       # the hosted sign-in widget (cross-site, so out-of-process)
BANK = "bank.example.test"                   # a sensitive-category item
LOOKALIKE = "examp1e.test"
HOMOGRAPH_UNICODE = "login.exаmple.test"  # Cyrillic a
HOMOGRAPH = "login.xn--exmple-4nf.test"       # its punycode, which is what the browser commits
SUFFIXED = "login.example.test.evil.test"
EVIL = "evil.test"
# Base-domain rule: real PSL entries, so Chromium's own PSL and the broker's agree (or visibly disagree).
BASE_WWW = "www.example.org"
BASE_LOGIN = "login.example.org"
PRIV_GOOD = "alice.github.io"                # github.io is in the PSL's PRIVATE section
PRIV_EVIL = "mallory.github.io"
WITNESS = "witness.lab.test"                 # the sites' record endpoint, read by the lab only

SITE_HOSTS = [RIGHT, SHOP, WIDGET, BANK, LOOKALIKE, HOMOGRAPH, SUFFIXED, EVIL, BASE_WWW, BASE_LOGIN, PRIV_GOOD,
              PRIV_EVIL, WITNESS]
ALL_TLS_HOSTS = [VAULT, *SITE_HOSTS]
