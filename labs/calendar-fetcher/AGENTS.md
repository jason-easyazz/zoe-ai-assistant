# labs/calendar-fetcher — the calendar subscription fetcher (broker-bound, unwired)

## Purpose

The real fetcher behind `services/zoe-data/calendar_feed.py`'s injected seam: given a subscription link it returns a
`FetchResult`-shaped answer under the contract in `docs/knowledge/connected-accounts.md` ("the calendar feed fetcher
contract"). A subscription link is a credential, so this code is written to be hosted by the credential broker
(`services/zoe-vaultd`, after P0) and never by zoe-data.

## Ownership

- `calendar_fetcher.py` — the fetcher (address policy, pinned connect, redirects, caps, bounds, validators, adapter).
- `tests/test_calendar_fetcher.py` — its regression net (fake resolver + in-process socket pair; no real network).
- `lab_happy_path.py` — the loopback end-to-end lab with the engine on a scratch store (`--lab` required).

## Local Contracts

- Standard library only (the broker's rule). Never import zoe-data from `calendar_fetcher.py`.
- The ONLY relaxation is the `lab_loopback_http` constructor argument (http and any port, to loopback only). Never an
  environment variable; a test pins that the module reads no environment.
- No address, host, path, token or response text in any log line, exception leaving `fetch`, or result field; a host is
  logged as `host_tag()` at most. `fetch` never raises for a network, provider or policy problem.
- `STATUSES` and the result fields equal `calendar_feed.FETCH_STATUSES` / `FetchResult` (a test reads the engine's source).
- Bounds can only be tightened by a caller (the constructor refuses a looser total or redirect limit).

## Forbidden

- Importing or calling it from zoe-data, a scheduler, a route or a unit; storing a link anywhere; a real link in tests or
  the lab (synthetic `*.example.test` hosts, loopback and sentinel tokens only).
- Any switch that disables an address, scheme, redirect or size check in shipped code (negative controls monkeypatch).

## Work Guidance

- Moving it into the broker is its own PR (see `docs/knowledge/connected-accounts.md`, "where it runs").

## Verification

- `flock -x -w 14000 /tmp/zoe-test-lane.lock timeout 180 python3 -m pytest -p no:cacheprovider -q labs/calendar-fetcher/tests`
  (local only; labs are not in CI by design).
- `python3 labs/calendar-fetcher/lab_happy_path.py --lab` with `icalendar==7.3.0` on `PYTHONPATH` (exit 0 = all checks).

## Child DOX Index

None.
