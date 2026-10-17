---
type: lab
title: F0 / R0 relay lab - frp v0.71.0 relay, session-master plugin stub, two fake households, one panel
date: 2026-10-17
status: built and run on the Jetson (see "Measured"); lab only - nothing here is imported, built or executed by any production service, image or CI job
design: docs/architecture/panel-anywhere.md (sections 2, 4, 5, 6, 10)
decisions: owner 2026-10-17 - D1 frp v0.71.0 (adopted, not rebuilt); D2 second public IP on the existing the411 server; D4 relay.the411.life for now; defaults stand (operator-issued enrolment codes, mTLS from R1, the relay never maps to guest, 20 Mbit/s and 64 connections per household)
---

# labs/relay - the relay in the lab

Phase **F0 / R0** of [panel anywhere](../../docs/architecture/panel-anywhere.md): prove, in a hermetic Docker lab with two
fake households, that an off-the-shelf frp relay can be made multi-tenant and blind, before any code touches the
411 or zoe-data. A record, not a contract: the contract is the design doc and `labs/AGENTS.md`.

## What runs

```
                       lab network 10.77.0.0/24 (Docker "internal": no route out, nothing published on the host)

 panel (curl, requests)        relay host = ONE network namespace owned by `front`            household boxes (x2)
 --------------------          -------------------------------------------------            ---------------------
 away hosts mapping            front  nginx stream :443, ssl_preread, per-IP limit_conn      boxN-nginx  relay listener stub
  <hid>.zoe.internal --TLS-->    <hid>.zoe.internal -> 127.0.0.1:8443  (frps https vhost)     127.0.0.1:8443 ssl+PROXY, mTLS,
  household CA pin, mTLS        (no SNI)           -> 127.0.0.1:7000  (frps control)           X-Zoe-Ingress: relay
                                 anything else      -> refused                              boxN-frpc   frpc v0.71.0, joins the
                               relay  preflight.py, then frps v0.71.0 (loopback only)         box's namespace, pins the relay CA
                               plugin session-master stub on 127.0.0.1:9100 (stdlib Python)
```

| Piece | File | Notes |
|---|---|---|
| frps + frpc v0.71.0 | `docker/Dockerfile.frp` | release tarball verified by its published sha256 per architecture (arm64 and amd64), base images by multi-arch index digest |
| Stream front | `relay/nginx-front.conf.tmpl` | routes by SNI without terminating TLS; holds no key |
| frps config | `relay/frps.toml.tmpl` | https vhost only, TLS forced, loopback bind, `[[httpPlugins]]` for Login / NewProxy / NewUserConn / CloseProxy |
| Preflight | `relay/preflight.py`, `relay/entrypoint.sh` | frps does not start unless the plugin is configured for every required op, on loopback, and rejects a bogus login |
| Session-master stub | `relay/plugin.py` | authenticates the box by a per-household secret; rewrites names; https only; forces the bandwidth limit; fails closed |
| frpc config + test | `box/frpc_config.py` | the only frpc config a box may run, and the check that every generated config must pass |
| Relay-listener stub | `box/nginx-relay-listener.conf.tmpl` | the interface the R1 PR plugs into (below) |
| Panel client | `panel/panel_client.py`, `panel/impostor.py` | the daemon's pinned `requests` call, the link-home probe, the T4 impostor |
| PKI | `lab/pki.py` | throwaway EC P-256 PKI via the host `openssl`, minted per run, deleted at teardown |
| Capture | `lab/capture.py`, `lab/capture_analysis.py` | raw-socket sidecar in the relay's namespace + a unit-tested TLS-record analyser |
| Runner | `run_lab.py`, `run.sh`, `compose.yaml` | brings the lab up, runs every cell, tears down and asserts the teardown |

Headless Chromium is **not** in the panel image: it does not fit the daytime memory budget, and no F0 cell needs a
browser (C3 and T5's push socket are the next PR). The panel is curl, Python `requests` and `openssl`.

## Running it

```sh
labs/relay/run.sh unit          # 58 unit tests, no Docker (Python 3.12)
labs/relay/run.sh lab           # the hermetic lab: ~3-4 minutes, ~120 MiB of containers
```

Both take `flock -x -w 3600 /tmp/zoe-test-lane.lock` and run `nice -n 10`. The runner additionally:

- refuses to run between **01:50 and 04:55** local time;
- refuses to **start** unless `MemAvailable >= 1500 MB` (it waits up to 10 minutes for that, `--wait-mem-s`);
- refuses to start if an earlier run left labelled containers or the network behind;
- labels everything `zoe.relay-lab=1` and never touches anything else;
- always tears down (`finally`), then **asserts** that no lab container or network remains, that the run directory with
  the throwaway keys is deleted, and that `MemAvailable` is back within 300 MB of where it started.

Requirements: Python >= 3.11 (the host default is 3.10; use `python3.12`), Docker + compose v2, host `openssl`.
Images are built by `docker build` with `DOCKER_BUILDKIT=0` (this host has no buildx).

## Cells and results

Each cell has a positive control (the thing works) and a negative control (the thing it guards against is refused),
and every negative has a sensitivity check so a refusal cannot come from a broken probe. Measured on the Jetson
(arm64) on 2026-10-17 with frp v0.71.0 on the final code: **81 of 81 runner checks passed** (68 cell checks, the boot
and the teardown assertions), in 237 s. Earlier runs on the same day found and fixed: the stream front not routing
frpc's control SNI, frpc giving up at boot, a header-value bug in the probe client, two check conditions that
matched their own error messages, and a teardown assertion on `MemAvailable` that unrelated processes made flaky.

| Cell | Positive | Negative control (must be refused) | Result |
|---|---|---|---|
| **T1** names | A's login and proxy register exactly `A.zoe.internal`, B's `B.zoe.internal`; the panel reaches each box through its own name | box A asking for B's name, `*.zoe.internal`, an arbitrary public host, or its own name plus B's: rejected by the plugin (`why=foreign-or-wildcard-name`), registry unchanged. `tcp`, `udp`, `http`, `stcp` proxies: all four rejected (`why=type`). Wrong secret / unknown household: login refused | pass |
| **T1** overwrite | the client asked for `HAE42B31F1.Zoe.Internal`; frps registered exactly `hae42b31f1.zoe.internal` and it still routes | with the plugin's domain overwrite switched off the client's value is what frps registers (the overwrite does the work) | pass |
| **T2** preflight | with the plugin configured the relay passes the preflight and frps runs | no `[[httpPlugins]]`, a plugin lacking `NewUserConn`, a non-loopback plugin, the http vhost enabled, TLS not forced, a configured-but-down plugin, a **permissive** plugin that accepts a bogus login: each exits 78 and frps never starts (unit-tested too: a second plugin entry, `localhost` instead of a numeric loopback address, `quicBindPort`, a plugin that is healthy on `/healthz` but answers 404/500 on the real route). Control: the same frps launched *without* the preflight accepted a foreign name, a wildcard and a `tcp` proxy | pass |
| **T3** blind relay | requests crossed the relay (both households echoed a random canary) | capture on the relay host (raw sockets on `eth0` and `lo`, 159 payload segments): canary, request line, `PRIVATE KEY` appear in no stream; each household connection on the vhost hop is ClientHello then only ciphertext (ServerHello then ciphertext back); the only plaintext on the host is frps' own `POST /handler` calls to the plugin; the only private key on the relay filesystem is the relay's own. **Control:** a deliberately TLS-terminating relay served the same request, the same capture shows the canary and `GET /lab/echo` in plaintext (2 and 1 hits), and the key scan flags the household key it holds | pass |
| **T4** CA pinning | `requests` with `verify=<household CA>` reaches A | a leaf for the same name from another CA that **is** in the panel's system store: an unpinned `curl` and an unpinned `requests` accept it (so the attack is real), the pinned `requests` refuses (`CERTIFICATE_VERIFY_FAILED`), `curl --cacert ... --capath <empty>` refuses (exit 60). Finding: plain `curl --cacert` is **not** a pin on Debian (exit 0) | pass |
| **T6** frpc config test | the generated config for both boxes passes (and is the config the lab boxes run) | `127.0.0.1:8000` (message names zoe-data's trusted loopback port), an `https2http` plugin at `:8000`, `127.0.0.1:5432`, a LAN address, a second proxy, a `tcp` proxy, no `trustedCaFile`: each fails | pass |
| **T7** link states | both up: `green` | box A stopped: the relay answers the ClientHello with TLS alert **`unrecognized_name`** -> `home_offline` (B stays green); relay stopped: no TCP connection -> `relay_unreachable`; box frozen (link dead, relay has not noticed): handshake never completes, `home_unresponsive` after the probe timeout. After a relay restart both boxes re-registered by themselves | pass |
| **T9** `trustedCaFile` | frpc with `trustedCaFile` logs in to the genuine relay | pointed at a relay with a cert from another CA it refuses (`connect to server error: session shutdown`) and **the rogue relay saw 0 logins**. Control: the same config without `trustedCaFile` logs in to the rogue relay (1 login seen) and hands it the household secret | pass |
| **A1** bandwidth | the client asked for 4MB in client mode; frps holds `256KB`/`server` and a 1.5 MiB download took 5.04 s | with the plugin's bandwidth overwrite switched off frps holds the client's own `4MB`/`client` and the same download took 0.03 s | pass |
| FC1 (extra) fail closed | plugin up: A is served | plugin stopped: a new panel connection to A is refused and a new box login is refused; plugin back: served again | pass |
| N1 (extra) rate cap, suspension | A is served 64 new connections in a 10 s window | the 65th-72nd are refused; B is unaffected; after the window A is served; B suspended in the registry is refused, reactivated is served | pass |
| S1-S5 (extra) listener stub | via the relay with the household client certificate: `X-Zoe-Ingress: relay`, the fingerprint header matches the certificate, no `X-Forwarded-For` | client-sent `X-Zoe-Ingress` / `X-Zoe-Client-Cert-Fp` are overwritten (S2); no client certificate -> nginx answers 400 and nothing reaches the upstream (S3); B's certificate on A's listener -> 400 (S5); the LAN listener clears both headers (S4) | pass |
| T5, T10, T11, C3 | not in F0 (need zoe-data relay-ingress code and the kiosk) | - | next PR (interface below) |

The unit net (`./run.sh unit`, 58 tests) covers the plugin policy, the preflight, the frpc config check and the
capture analyser with their own negative controls (each was spot-checked by breaking the code under test).

## Measured

- Containers at idle: **57 MiB** across the eight lab containers (`docker stats`); peak during the cells **149 MiB**
  (relay 16 MiB, plugin 20 MiB, each frpc 3 MiB, nginx 3-5 MiB, panel 4 MiB). One run takes about 4 minutes.
- `MemAvailable` at the gate: 1830 MB; with the lab up 1598 MB; lowest during the run 1377 MB; after teardown 1386 MB.
  The box's other processes (other agents' test runs) move it by far more than the lab uses, so before/after is
  informational; the teardown assertion is "no lab container, no lab network, run directory deleted, `docker stats`
  lists none".
- The gate is real: earlier attempts refused to start, or waited most of an hour, because the daytime `MemAvailable` sat at 1.0-1.4 GB.
  `run.sh` therefore waits for the gate *before* taking the test-lane lock, so a waiting lab never holds the lane.

## Findings (where the lab corrected the design or the research)

1. **A true per-household concurrent-connection cap is not buildable on this stack.** frp's plugin API reports a new
   user connection but never its close, and nginx's `limit_conn` runs in the preaccess phase, before `ssl_preread`
   has read the SNI, so it cannot be keyed per household. D10's "64 connections" is therefore carried as a
   **new-connection rate** (64 per 10 s per household, in the plugin's `NewUserConn`, cell N1) plus a per-source-IP
   `limit_conn` at the front and the bandwidth cap. frps exposes `curConns` per proxy on its dashboard API, so a true
   concurrency cap is possible in R2 as a poller that suspends a household; it is not in the plugin API.
2. **`ssl_verify_client on` does not fail the TLS handshake.** nginx completes the handshake and then answers HTTP
   400 itself (S3, S5); nothing reaches the upstream. The design's "a stranger never completes the TLS handshake"
   should read "never reaches zoe-data", and the R1 cell T10's "no client certificate: the handshake fails" must
   expect 400 (or add `if ($ssl_client_verify != SUCCESS) { return 444; }` to drop the connection silently).
3. **`unrecognized_name` is real** (T7): the research verified the source comment, the lab verified the wire. The kiosk
   gate's "home offline" screen can key on that alert. There is a third state to design for: a frozen box (internet
   down at home) looks like a handshake that never completes until frps' heartbeat timeout fires (set to 30 s here,
   default 90), so the gate needs a timeout rule for "home unresponsive".
4. **frpc's control connection carries the relay's name as its SNI** (`transport.tls.serverName`), so the stream front
   must route that exact name to frps' control port; an unrouted name was refused and the boxes could not log in.
5. **`loginFailExit = false` is required in the box's frpc config.** frpc's default is to exit if the first login fails;
   the lab's boxes started before frps had passed its preflight wait and gave up. The generator sets it, T6 enforces it.
6. **`trustedCaFile` refusal is silent in frpc's log** (`session shutdown`, not an x509 message); the reliable evidence is
   that the rogue relay saw no login. Without it a box hands the household secret to whoever answers.
7. **`curl --cacert` is not a pin on Debian**: curl still trusts its default CA directory. Any panel script that uses
   curl must pass `--capath <empty dir>`; `requests` with `verify=<path>` is a real pin.
8. **The plugin's loopback API carries the live relay secret in plaintext** (frps includes the login `metas` in every
   `NewProxy` and `NewUserConn` call). That stays inside the relay host's trust domain, which is why the preflight
   requires a loopback plugin. By design the operator can see the relay secret; it lets someone impersonate the box's
   *login* (squat the name, deny service) but not read traffic or present a certificate the panel accepts.
9. **frps does not tell frpc why a plugin refused** with `detailedErrorsToClient = false` (the lab setting): the client
   sees only `new proxy [x] error`. Debug from the plugin's log; tenants see nothing about policy.
10. **The plugin runs before frps validates and before the proxy name is checked**, so the plugin pins the name to
    `<hid>.https` and `user == hid`; otherwise a box could collide with another household's proxy name.
11. **frpc plugins such as `https2http` terminate TLS in frpc and would bypass the nginx listener entirely**; the T6
    check forbids any proxy `plugin`.
12. **Not tested in F0:** kicking a household's previous session on re-login (frp refuses a duplicate proxy name until
    the old control connection times out; the lab only exercised clean stop/start and a relay restart), QUIC
    (`transport.protocol = "quic"`, cell V2), the per-IP `limit_conn` (configured, not exercised), amd64 builds (the
    Dockerfile carries the published amd64 sha256 but was only built and run on arm64), and any real network latency.

## Pinning

| Dependency | Pin | Age |
|---|---|---|
| frp | v0.71.0 release tarball, sha256 verified per architecture in the Dockerfile (arm64 `f33c293c...`, amd64 `84f27e39...`; the checksum file is the release's own `frp_sha256_checksums.txt`) | released 2026-08-14 (v0.71.1 is 2026-10-14: not before 2026-10-28) |
| nginx | `nginx@sha256:1ed1b0e1...` (multi-arch index, arm64 present) | image built 2026-09-22 |
| python (relay, plugin, panel) | `python@sha256:d50fb761...` (3.12-slim-bookworm, multi-arch index) | image built 2026-07-14 |
| alpine (build stage only) | `alpine@sha256:28bd5fe8...` | image built 2026-06-16 |
| panel Python deps | `requests==2.32.5`, `urllib3==2.6.3`, `certifi==2026.4.22`, `idna==3.11`, `charset-normalizer==3.4.4` | 2025-08 to 2026-04 |
| Source read | `opensrc path github.com/fatedier/frp@v0.71.0`: `pkg/plugin/server/{types,http,manager}.go`, `server/control.go`, `server/proxy/proxy.go`, `pkg/util/vhost/https.go`, `server/http/model/types.go` | |

The panel image's `apt-get install curl openssl ca-certificates` follows the pinned Debian release's security channel;
it is not version-pinned, and it affects only the lab's client side.

## Interface for the R1 PR (zoe-data relay ingress) - cells T5, T10, T11, C3

F0 stubs the nginx side so the next PR plugs in without changing the relay. What the lab pins, and what the R1 PR
must do against it. The stub is `box/nginx-relay-listener.conf.tmpl`; its self-checks S1 to S4 prove the header
mechanics only - they are **not** the design cells.

**Wire contract (nginx relay listener, pinned by the stub and S1-S4):**

| Property | Value |
|---|---|
| Listener | `127.0.0.1:<relay-listener-port>`, `ssl` + `proxy_protocol` (frpc sends PROXY v2); published to host loopback only; the lab uses 8443 |
| TLS | 1.3 only; leaf for `<hid>.zoe.internal` from the **household CA**; `ssl_verify_client on` against the same CA, **no optional phase** |
| Marker | `proxy_set_header X-Zoe-Ingress "relay"` and `X-Zoe-Client-Cert-Fp $ssl_client_fingerprint`, set absolutely (a client-sent value is overwritten: S2) |
| No address identity | `proxy_set_header X-Forwarded-For ""`; `X-Real-IP` is the relay client's private address and must not be used |
| LAN listener | every LAN `server` block sets `X-Zoe-Ingress ""` and `X-Zoe-Client-Cert-Fp ""` so a LAN client cannot claim the relay ingress (S4); it serves the same leaf so the home path verifies identically |
| frpc target | exactly the relay listener; never `127.0.0.1:8000` (zoe-data's trusted loopback port). `box/frpc_config.check()` is the test (T6); port it next to the generator in zoe-data with the same fixtures |
| Fingerprint | nginx's `$ssl_client_fingerprint` is SHA-1. If the R1 PR wants SHA-256 it must derive it differently (njs, or the certificate PEM variable); keep the header name |

**zoe-data must (flag `ZOE_RELAY_INGRESS`, default off; `ZOE_RELAY_HOSTNAME`):**

1. Treat a request as relay ingress only when the TCP peer is a trusted proxy **and** `X-Zoe-Ingress: relay`
   (T5 negative: a client-sent `X-Zoe-Ingress: lan` is overwritten by nginx).
2. **Never map relay ingress to `guest`.** No valid device token or kiosk credential means 401. The anonymous
   fallback in `auth.get_current_user` and `ZOE_UNAUTHENTICATED_ROLE` never apply to it, and a degraded zoe-auth is
   refused, not downgraded. T10 negative: a certificate with no device credential gets 401 and
   `POST /api/ha/control` is refused; the same credential-less request on the LAN listener is accepted today, which
   proves the probe reaches the route.
3. **A device token resolves to the panel principal only** (guest-class + `panel_id`), ignoring any `default` member
   in `panel_user_bindings`. Member routes need a fresh voice-verified or PIN session (T11, with its LAN control).
4. Require `X-Zoe-Client-Cert-Fp` to belong to the same `panel_id` as the token or kiosk credential; revoking a
   fingerprint takes effect at once (T8 in R3).
5. Never match a relayed request against `panels.ip_address`, never satisfy `ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE=lan`,
   report `livekit_available` false.
6. Add `https://<hid>.zoe.internal` to the allowed browser and push-socket origins, derived from
   `ZOE_RELAY_HOSTNAME` (T5: without it the push socket refuses the kiosk).
7. Ship `box/frpc_config.py` (generator + check) with its unit test in a `ci_safe` lane, plus the household-CA /
   leaf / panel-client-certificate tooling and the frpc systemd unit.

**Lab work the R1 PR owns:** replace the stub's fake upstream (`127.0.0.1:8099`) with a real zoe-data (or a test app
using the real auth dependencies) and run T5, T10 and T11 in this lab. C3 needs headless Chromium on the panel with
`AutoSelectCertificateForUrls` and fits only when `MemAvailable` allows it. R1 does not go live without T10, T11 and
C3 passing.
