---
type: Reference
title: Telegram bot ETIMEDOUT while curl works (Node Happy Eyeballs timeout)
description: A Node process on the box fails every hostname-based request to a far host with ETIMEDOUT while curl succeeds - Node's 250 ms Happy-Eyeballs attempt timeout against a ~410 ms round trip; the negative controls and the NODE_OPTIONS fix.
tags: [telegram, node, networking, incident-signature, runbook]
timestamp: 2026-10-29T00:00:00Z
---

# Node ETIMEDOUT while curl works

Operational record, measured 2026-09-25. See [index](index.md). Related: [incident-runbook.md](incident-runbook.md).

## Signature

- The Telegram sidecar (`flue-zoe-telegram`) logs `Network request for 'deleteWebhook' failed ... ETIMEDOUT` on every poll,
  `/health` reports `polling:false`, and the one-minute watchdog restarts it forever (about 4,000 restarts before it was found).
- `curl -4 https://api.telegram.org/` answers in about 0.4 s, so "the network is fine".
- In Node, `net.connect(443, '<ip>')` succeeds; `net.connect(443, 'api.telegram.org')` fails ETIMEDOUT in 300-400 ms.
  Anything hostname-based (undici `fetch`, `https.get`, `tls.connect`) fails.

## Root cause

Node 20+ Happy Eyeballs (`autoSelectFamily`) gives each resolved address `--network-family-autoselection-attempt-timeout`
(default **250 ms**) before trying the next. The round trip from this box to Telegram's data centre is about 410 ms, so the
IPv4 attempt is abandoned mid-handshake, the IPv6 attempt fails (the box has only a ULA IPv6 address and no default v6 route),
and the aggregate error is ETIMEDOUT. curl has no such timeout.

## Negative controls that settled it

| variation | result |
|---|---|
| `--dns-result-order=ipv4first` | still fails (NOT an IPv6-preference bug) |
| restricting TLS groups | still fails (NOT a post-quantum ClientHello size problem) |
| `--network-family-autoselection-attempt-timeout=1500` | HTTP 200 |
| `--no-network-family-autoselection` | HTTP 200 |

## Fix

A systemd drop-in for `flue-zoe-telegram.service` setting
`Environment=NODE_OPTIONS=--network-family-autoselection-attempt-timeout=1500`, mirrored into the tracked template
`scripts/setup/systemd/flue-zoe-telegram.service`. Verified: `/health` returns `{"ok":true,"polling":true}` and the journal
shows the bot polling.

## Rules that follow

- Any other Node process on this box that talks to a host with a round trip over 250 ms needs the same flag.
- A bot library that prints the failing URL can print the bot token into the journal: rotate the token and vacuum the journal
  after an incident like this.
- A watchdog that restarts forever with no back-off hides the cause; read the first failing request, not the restart count.
