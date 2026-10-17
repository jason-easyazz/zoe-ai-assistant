---
type: Runbook
title: Panel relay R1 - the box side (zoe-data ingress rules, nginx relay listener, household CA, frpc)
description: What PR feature/relay-r1-ingress built for the owner's own panel to reach Zoe through the shared panel relay (design docs/architecture/panel-anywhere.md sections 5-6 and 10 R1), every flag and file, the measured results, the exact owner steps in order, the rollback, and what is still pending (C3 Chromium client certificate, the panel side, frpc install).
tags: [panel, relay, frp, mtls, nginx, auth, runbook]
timestamp: 2026-10-17T14:00:00+08:00
---

# Panel relay R1 - the box side

Design: [panel-anywhere.md](../architecture/panel-anywhere.md) (sections 5, 6, 10 R1). The relay itself (frps, plugin, the411) is the F0 lab / `labs/relay/RUNBOOK-R1.md` (PR #2094). This page is
the other end: what the household's box does when a request arrives through the relay. **Everything is flag-dark and the LAN is byte-identical with the flags off** (measured: 68 of 68 LAN probes
reach the upstream with the same headers as `origin/main`, except that a client-sent `X-Zoe-Ingress` / `X-Zoe-Client-Cert-Fp` is now dropped, where it used to pass straight through).

## What is built

| Piece | Where | Rule |
|---|---|---|
| zoe-data ingress gate | `services/zoe-data/relay_ingress.py`, `auth.get_current_user`, `panel_principal.py`, `main.py` | `ZOE_RELAY_INGRESS` (default off) + `ZOE_RELAY_HOSTNAME` (`<hid>.zoe.internal`). A request is relayed only when the flag is on, the TCP peer is our nginx (`ZOE_TRUSTED_PROXY_NETS`) and it carries `X-Zoe-Ingress: relay`. Relayed = never guest (no device token / kiosk credential for the certificate's panel = 401), no address, the panel principal only, a member only with a fresh sign-in at the panel, no home control without a member. |
| certificate binding | alembic `0055` (`panel_client_certs`), `routers/panel_relay.py` | the SHA-1 nginx forwards is bound to ONE panel; revoke = refused at once; a fingerprint never moves or un-revokes. |
| nginx relay listener | `services/zoe-ui/nginx.d/relay-listener.conf` (+ `ingress-lan.inc`, `ingress-headers.inc`, `lan-only.inc`), `nginx.conf` glob, `docker-compose.yml` `127.0.0.1:9443` | TLS 1.3, `ssl_verify_client on` against the household CA, PROXY protocol, headers set absolutely, no address, off unless `ssl/relay/listener.conf` exists. |
| box tooling | `scripts/setup/relay/{frpc_config,household_pki,enable_relay_listener}.py` | the frpc config + its safety check (never `127.0.0.1:8000`), the household CA / leaf / panel certificate, the guarded enable. |

## Measured (2026-10-17, Jetson, nginx:alpine, throwaway PKI)

- `tests/unit/test_relay_nginx_listener.py`, `test_relay_frpc_config.py`, `services/zoe-data/tests/test_relay_ingress.py`: green in the `ci_safe` lanes. Every negative control was run by breaking the code under test:
  24 zoe-data mutations (trust any peer, ignore the flag, keep the address, drop the credential / mismatch / freshness / revocation / inactive-panel checks, skip the websocket gate, un-install the middleware, drop the origin, ...)
  all went red, and 9 nginx mutations (a location without the ingress headers, an empty marker, optional client certificate + no 444, forwarded X-Forwarded-For / X-Real-IP, `/ha/` reachable, TLS 1.2 allowed, LAN blocks forwarding the marker, fingerprint taken from the client header) all went red in the static file and in the live file.
  One live mutation (`ssl_verify_client optional`) survives alone BY DESIGN: the `$ssl_client_verify != SUCCESS` -> 444 backstop catches it; removing both is red.
- `services/zoe-data/tests/test_relay_nginx_live.py` (opt-in, `ZOE_RELAY_NGINX_TEST=1`, docker): 12 cells against a real nginx container in front of the real zoe-data auth code: T5 header overwrite (relay) / drop (LAN) / no address, no certificate and a foreign-CA certificate never reach zoe-data (hit counter), TLS 1.2 and a missing PROXY header refused, T10 (credential-less 401, `POST /api/ha/control` refused, the LAN request behaves per #2089), T11, the fingerprint mismatch and its revocation, 404 for Music Assistant / HA UI / Multica / LiveKit, the websocket upgrade through the same gate, HTTP/2 + client certificate.
- Not run: the full F0 lab (frps/frpc hop). `labs/relay` is PR #2094, not in this branch's base; the lab runner needs `MemAvailable >= 1500 MB` and the daytime Jetson sat at ~1.3-1.5 GB. Plan once #2094 and this PR have landed: replace the lab listener stub's fake upstream with the stack from `test_relay_nginx_live.py` and add cells T5 / T10 / T11 to `run_lab.py`.

## Owner steps, in order (nothing here has been run on the live box)

1. Merge #2089 (the home-control / device-token fix) first, then this PR. Set alembic `0055`'s `down_revision` to `"0054"` when main has it (the single-head test in `test_relay_ingress.py` goes red until you do). Run the migration (`alembic upgrade head`, as for any migration).
2. In the zoe-data env: `ZOE_RELAY_INGRESS=1`, `ZOE_RELAY_HOSTNAME=<hid>.zoe.internal`, `ZOE_PANEL_KIOSK_CREDENTIAL=1` (the kiosk browser's only credential over the relay). Restart zoe-data and poll `/health`. (`/api/panels/{id}/client-certs` now answers.)
3. `python3 scripts/setup/relay/household_pki.py init-ca` then `issue-leaf --hid <hid>`; back up `~/.zoe/relay-ca/ca.key` offline. On the panel generate a key + CSR (`openssl ecparam ...; openssl req -new ...`); `household_pki.py sign-csr --csr panel.csr --panel-id <panel_id>` prints the fingerprint.
4. Bind it: `PUT /api/panels/<panel_id>/client-certs/<fingerprint>` with an admin session. Copy `panel.pem` and `ssl/relay/ca.pem` to the panel.
5. `python3 scripts/setup/relay/enable_relay_listener.py` (refuses unless step 2 and 3 are done), run the printed `nginx -t` pre-flight, `docker compose up -d --no-deps zoe-ui && docker restart zoe-ui`. Check `docker exec zoe-ui ls /etc/nginx/ssl/relay` and `ss -ltn | grep 9443` (loopback only).
6. Install frpc v0.71.0 (arm64 tarball, verify the published sha256), render its config with `scripts/setup/relay/frpc_config.py` `render()`, and run `python3 scripts/setup/relay/frpc_config.py frpc.toml <hid> <relay-ip>` BEFORE starting it (non-zero = do not start). The systemd unit is not shipped yet.
7. Panel side (see pending): switch the hosts mapping, install the CA root and client certificate, `VERIFY_SSL=true` with the CA pin, X-Device-Token as before.
8. Replay gate and the L6 time-to-first-audio comparison before calling R1 live (design section 10).

**Rollback:** `enable_relay_listener.py --disable` + `docker restart zoe-ui` removes the listener; unset `ZOE_RELAY_INGRESS` and restart zoe-data (the header becomes inert; the admin routes 404); revoke a single panel with `DELETE /api/panels/<panel_id>/client-certs/<fingerprint>`. Order matters when turning ON: flag first, then the listener.

## Still pending

- **C3** (design section 10): kiosk Chromium presents its client certificate with no prompt (`AutoSelectCertificateForUrls` policy for `https://<hid>.zoe.internal`); needs headless/real Chromium on the panel and the memory for it. R1 does not go live without it; the fallback is a loopback forwarder in the kiosk gate.
- **Panel side**: the voice daemon (`scripts/setup/zoe_voice_daemon.py`: `requests` with `verify=<household CA>` and `cert=(client cert, key)`, the hosts mapping, `VERIFY_SSL=false` retired) and the kiosk gate are NOT changed here (the daemon is a voice-path file; replay-gated). `curl --cacert` is not a pin on Debian (lab finding 7).
- **Voice lanes over the relay**: home control by voice is not member-gated (the resolver is called with a bare user id), and `voice_tts.py`'s `livekit-token` computes `livekit_available` from addresses (nginx answers "unavailable" for the relay instead). Both are voice-path changes: open-problems ledger 2026-10-17.
- **`/ws/push?panel_id=`** is not narrowed to the certificate's panel (the kiosk may connect under an alias id).
- The full lab cells T5 / T10 / T11 inside `labs/relay` (needs #2094 + MemAvailable >= 1500 MB).
