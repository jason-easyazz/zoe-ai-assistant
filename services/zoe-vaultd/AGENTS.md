# services/zoe-vaultd — the credential broker

## Purpose

`zoe-vaultd` is the only Zoe code that ever decrypts a household login
(`docs/architecture/credential-vault.md` sections 5-7 and 12). It signs in to a site inside its OWN short-lived
browser with a single-use capability it minted after the credential owner approved on its own phone page, and hands
back cookies, never a secret. Three units, three uids: `core` (`zoe-vault`: vault client, keys, policy, audit),
`browser` (`zoe-vault-browser`: Chromium over a private DevTools pipe) and `phone` (`zoe-vault-phone`: approve /
code / unlock pages).

**Status: lab-only (phase V1).** Nothing installs, imports or starts it outside `labs/credential-vault/`. Phase V2
(Vaultwarden on the box, units, onboarding) waits for P0 (`docs/architecture/p0-deroot.md`) and owner decisions.

## Ownership

- `zoe_vaultd/origin.py` + `psl.py` — the origin rule (6.3) and form targets (6.4). Pure functions.
- `zoe_vaultd/fill.py` + `cdp.py` — login mode (6.1, 6.2): per-frame isolated-world field finder, the binding, the
  re-check before every insert, outcome, cookie export.
- `zoe_vaultd/browser.py` — the login-browser launcher (one fresh Chromium per connection, 1:1 pipe bridge).
- `zoe_vaultd/broker.py` (the `core` unit; not `core.py`, which the root `.gitignore` drops as a core dump) + `policy.py` — the socket API, roles, proposals, capabilities, U3 unlock gate, rate limits.
- `zoe_vaultd/vault.py` — the adopted `bw` CLI wrapper (session key only in env, never argv).
- `zoe_vaultd/audit.py` — the hash-chained, field-allowlisted audit log.
- `zoe_vaultd/phone.py` — the three phone pages.

## Local Contracts

- **No reveal verb.** The verb list is `CLIENT_VERBS` + `PHONE_VERBS` exactly as documented in design 5.2 (plus the
  lab-proven `card`, `notices`, `redeem`); `tests/unit/test_zoe_vaultd.py` pins it and lab cell V7 probes it. A new
  verb that returns a username, password, note, TOTP or custom-field value is a design change, not a code change.
- **Every input to an origin decision comes from the vault or from the browser** — never from a caller. Every exact
  origin comparison goes through `origin.origin_equal`; frames are judged by `origin.check_frame`.
- **Roles come from SO_PEERCRED only.** `client_uids`, `phone_uids` and `admin_uids` are disjoint and never root (config refuses).
  `unlock` is phone-only and U3-only.
- **A capability is minted only by `policy.Policy.issue` after a phone approval.** Every bound field must match;
  any use burns it.
- **zoe-data never holds anything that can answer a notice.** It gets opaque `/v/n/<notice>` ids; approve, code and
  unlock need the device secret of a phone paired to the right member (`policy.DeviceRegistry`), and a pairing link
  comes only from the admin role.
- **Every redirect hop of the top document is checked** (`fill.LoginRun.hops_ok`), not just the final page; a hop
  must be the approved origin or an exactly listed host (base-domain rules never apply to hops).
- **A capability is pinned to the item policy the owner approved** (`broker.policy_digest`): any change to URIs,
  match types or zoe: fields after approval is `policy_changed`.
- **`lock` stops in-flight work:** it closes a running sign-in's DevTools connection, and a sign-in that started
  before a lock can continue or mint anything, even one queued for the browser across a later unlock (the epoch is
  taken before the capability is spent; `broker.Core._stale` is checked after every stage).
- **Secrets never reach argv, logs or the audit log.** The `bw` session key and the U3 password go to the child
  through its environment; the audit writer accepts only allowlisted scalar fields.
- **Standard library only** (pinned by `test_broker_is_stdlib_only`), so the whole broker stays reviewable.
- **No negative-control switches in this package.** Breaking a protection for a lab control is a monkeypatch in
  `labs/credential-vault/lab/labcore.py`, never a flag here.

## Forbidden

- Installing it: no systemd unit, no Docker image, no import from zoe-data, no CI job that starts it, until phase V2
  is approved after P0.
- Filling into any browser that zoe-data or the model drives (design 6.2a), or opening a TCP DevTools port.
- Taking a URL, selector, origin or "approved" flag from a caller; fetching the PSL at run time.
- Real credentials anywhere in tests or the lab (synthetic values with a canary marker only).

## Work Guidance

- Keep it small: this is security code that must stay reviewable whole. Cross-vendor review (Codex) plus an
  independent review of `origin.py` / `fill.py` before phase V3.
- A browser-side change needs the lab: `labs/credential-vault/run.sh` (gated: night_busy, MemAvailable, lane lock).

## Verification

- `flock -x -w 3600 /tmp/zoe-test-lane.lock nice -n 10 python3 -m pytest -q tests/unit/test_zoe_vaultd.py`
  (ci_safe, stdlib only).
- `labs/credential-vault/run.sh` — cells V1-V9, V13, V17, V18 (+ V0) with negative controls; record in its README.

## Child DOX Index

None.
