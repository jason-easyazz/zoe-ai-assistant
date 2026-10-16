# Headscale for Zoe remote access

A self-hosted Tailscale control server (Headscale) so the Zoe touch panel can
reach Zoe at home from another city with **no Tailscale account**. The control
plane runs on the owner's DigitalOcean droplet; the Jetson ("zoe-box") and the
panel ("zoe-panel") keep using their stock Tailscale clients and point them at
`https://hs.the411.life` instead.

```
 panel (other city)                                   droplet (Sydney)
 tailscale --accept-routes ──https/websocket──▶ nginx :443 ──▶ headscale 127.0.0.1:18080
        │                                         (host)        control plane + embedded DERP
        │            ◀── UDP 3478 STUN ────────────────────────▶ headscale 0.0.0.0:3478/udp
        │
        └── WireGuard (direct, or relayed by the embedded DERP over :443)
                                  │
                                  ▼
                 box: tailscale, advertises 192.168.1.218/32
                 panel keeps using https://192.168.1.218 unchanged
```

| item | value |
|---|---|
| Headscale | `v0.29.4` (published 2026-09-23), pinned by tag **and** multi-arch digest in `docker-compose.yml` |
| Tailscale clients | `1.104.1` on the box and the panel (the lab uses `tailscale/tailscale:v1.104.1`) |
| Control plane | `hs.the411.life`, DNS-only (grey cloud) → host nginx :443 → container `127.0.0.1:18080` |
| Embedded DERP | same :443 (`/derp`), STUN on **UDP 3478** |
| Tailscale public relays | **not used** (`derp.urls: []`, reasoning in `config/config.yaml`) |
| MagicDNS | **off** (reasoning in `config/config.yaml`) |

Version choice: v0.29.5 was published 2026-10-13 (3 days old), so v0.29.4 is the
newest release past the 14-day rule. Every configuration key and CLI flag below
was read from the v0.29.4 source (`opensrc path juanfont/headscale@v0.29.4`), not
from memory.

## What is in this directory

| file | role |
|---|---|
| `docker-compose.yml` | the Headscale service: pinned image, read-only root, all capabilities dropped, loopback-only HTTP, UDP 3478 public, own network, memory and pids limits |
| `config/config.yaml` | Headscale configuration (mounted read-only) |
| `config/acl.hujson` | the access policy, including an executable `tests` block |
| `nginx-hs.the411.life.acme.conf` | nginx stage 1: HTTP only, to obtain the first certificate |
| `nginx-hs.the411.life.conf` | nginx stage 2: the final reverse proxy (websocket upgrade, DERP) |
| `lab_test.sh`, `lab/` | the local lab proof (not deployed) |

Nothing in the bundle contains a secret, a key, or the droplet's address. The
runtime state (`data/`: noise key, DERP key, `db.sqlite`) is created on the
droplet and is ignored by version control.

## Owner checklist (do these, in this order)

Everything here is the owner's step: nothing in this bundle has been deployed and
no real node has been joined. Replace `<DROPLET>` with the droplet's SSH target.

### 0. Preflight on the droplet (read-only)

```bash
ssh root@<DROPLET>
docker compose version                      # v2 plugin required
nginx -v                                    # 1.18+ is fine; no http2 directive is used
ss -ltnup | grep -E ':(18080|3478)\b'       # must print nothing: both ports free
nginx -T 2>/dev/null | grep -nE 'listen +(\[::\]:)?443|ssl_preread'
```

The last line shows how nginx owns :443. A plain `listen 443 ssl;` per vhost
(the usual setup) works with `nginx-hs.the411.life.conf` as shipped. If you see
`ssl_preread`, :443 is being SNI-routed by a `stream` block and the new vhost must
be added to that routing instead; stop and fix the snippet before step 5.

### 1. DNS (Cloudflare)

Add an `A` record `hs` → the droplet's public IPv4 (and `AAAA` if the droplet has
public IPv6), **proxy status: DNS only (grey cloud)**. The Cloudflare proxy does
not support the WebSocket POST the Tailscale protocol uses, and the embedded DERP
needs a direct path. Check: `dig +short hs.the411.life` returns the droplet.

### 2. DigitalOcean firewall

Cloud Firewall attached to the droplet: allow inbound **UDP 3478** from anywhere.
TCP 80/443 are already open for nginx. If `ufw` is active on the host as well:
`ufw allow 3478/udp`. Nothing else is published: the HTTP listener is
`127.0.0.1:18080`; gRPC and metrics are not published at all.

### 3. Copy the bundle

From a checkout of this repo (the lab files are not needed on the droplet):

```bash
rsync -a --exclude lab --exclude lab_test.sh --exclude README.md \
  scripts/setup/remote-access/headscale/ root@<DROPLET>:/opt/headscale/
```

`/opt/headscale` is deliberately separate from the411's directory: the411's
GitHub Actions deploy pulls and runs `docker compose up` inside its own directory
and never touches this one. Headscale is its own compose project (`headscale`) on
its own network (`10.77.250.0/28`), shares no volume, and has no docker socket.

### 4. Start Headscale

```bash
cd /opt/headscale
mkdir -p data                      # must be root-owned (it is, when created as root)
docker compose up -d
docker compose ps                  # STATUS: healthy after ~15 s
curl -s http://127.0.0.1:18080/health
docker compose logs headscale | tail -20
```

Expected in the log: `stun server started at [::]:3478`, `listening and serving
HTTP on: 0.0.0.0:8080`, and one warning `listening without TLS but ServerURL does
not start with http://` (nginx terminates TLS; the docs call this expected). You
will also see `policy tests failed at boot; server starting anyway`: the policy's
`tests` block cannot resolve tags or the `owner@` user until they exist. Expected;
step 9 re-runs it for real.

### 5. nginx + certificate

Install the two stages as **their own file**, never inside the411's existing file
(its deploy may rewrite that one). Use whichever directory your nginx already
includes: check `nginx -T | grep -n include` (`/etc/nginx/conf.d/` is shown here).

```bash
mkdir -p /var/www/certbot
cp /opt/headscale/nginx-hs.the411.life.acme.conf /etc/nginx/conf.d/hs.the411.life.conf
nginx -t && systemctl reload nginx

certbot certonly --webroot -w /var/www/certbot -d hs.the411.life

cp /opt/headscale/nginx-hs.the411.life.conf /etc/nginx/conf.d/hs.the411.life.conf
nginx -t && systemctl reload nginx
curl -sI https://hs.the411.life/health | head -1        # HTTP/1.1 200 OK
certbot renew --dry-run --cert-name hs.the411.life      # renewals keep working
```

Why two stages: `nginx -t` fails while the stage-2 file points at a certificate
that does not exist yet, and certbot needs a working :80 vhost for the name to
issue it. If `nginx -t` complains about a duplicate `map` or `upstream`, another
file defines the same name; the shipped names are `hs_upstream` and
`$hs_connection_upgrade` for exactly that reason.

### 6. Create the admin user and the node keys

```bash
cd /opt/headscale
alias hs='docker compose -f /opt/headscale/docker-compose.yml exec -T headscale headscale'
hs users create owner
hs preauthkeys create --tags tag:zoe-box   --expiration 1h
hs preauthkeys create --tags tag:zoe-panel --expiration 1h
```

The user **must** be named `owner`: the ACL refers to `owner@`. Each key is printed
once, is **single-use** (not `--reusable`), expires in an hour, and carries its
tag, so the node needs no `--advertise-tags`. There is no open registration: a
node with no key only gets an approval URL, and nothing joins until you approve it.

### 7. Join the box (the Jetson)

On the Jetson (its `tailscaled` is currently "Logged out"). If it was ever joined
elsewhere, `sudo tailscale logout` first.

```bash
sudo tailscale up --login-server https://hs.the411.life \
  --authkey <BOX_KEY> --hostname zoe-box \
  --advertise-routes=192.168.1.218/32 --accept-dns=false
```

`--accept-dns=false` is a second lock beside `dns.magic_dns: false`: tailscale
never rewrites the box's resolver, which the voice stack and Home Assistant
depend on. Do not pass `--accept-routes` on the box. The advertised route is the
box's own LAN address, so traffic arrives addressed to the box itself and no IP
forwarding is needed.

### 8. Join the panel (the Pi)

```bash
sudo tailscale up --login-server https://hs.the411.life \
  --authkey <PANEL_KEY> --hostname zoe-panel \
  --accept-routes --accept-dns=false
```

The panel keeps using `https://192.168.1.218` unchanged; with `--accept-routes`
that address is routed over the tailnet to the box. Confirm `tailscale version`
reads 1.104.1 on both.

**Test it at home first.** While the panel is still on the home network, confirm
its UI still works with Tailscale up. The lab cannot reproduce the home LAN, so
this is the one behaviour proven only on the real hardware: with the route
accepted, traffic to 192.168.1.218 is carried by the tailnet even at home. If it
misbehaves: `sudo tailscale set --accept-routes=false` while at home.

### 9. Check route approval, then re-run the policy tests

```bash
hs nodes list-routes
```

`zoe-box` must show `192.168.1.218/32` under both **Approved** and **Available**
with no manual step: that is the auto-approval for the /32 and nothing else. If it
is only Available, the tag did not apply; check `hs nodes list` shows
`tag:zoe-box` before approving anything by hand. (Manual fallback:
`hs nodes approve-routes --identifier <ID> --routes 192.168.1.218/32`.)

Both nodes now exist, so the policy's `tests` block can resolve. Run it:

```bash
hs policy check -f /etc/headscale/acl.hujson          # "Policy is valid"
```

### 10. Owner devices (phone, laptop)

Laptop:

```bash
sudo tailscale up --login-server https://hs.the411.life --hostname owner-laptop --accept-dns=false
```

It prints `https://hs.the411.life/register/<AUTH_ID>`. On the droplet:

```bash
hs auth register --user owner --auth-id <AUTH_ID>
```

Phone: in the Tailscale app, use the alternate/custom coordination server option
(wording varies by app version), enter `https://hs.the411.life`, then approve the
same way. Owner devices are personal nodes of user `owner` and may ssh to the
panel (tcp 22) and nothing else.

### Verify (from the panel, on another network, e.g. a phone hotspot)

```bash
tailscale status                              # zoe-box listed; direct, or relayed via "headscale"
tailscale ping zoe-box
curl -skI https://192.168.1.218/ | head -1    # answered by the Jetson, over the route
tailscale debug derp headscale                # the embedded relay works over :443
tailscale netcheck                            # UDP: true (STUN via UDP 3478), Nearest DERP: the embedded one
```

The refusals (each must hang or fail within a few seconds):

```bash
nc -zv -w 5 192.168.1.218 22                  # not 443/8007: refused by the ACL
nc -zv -w 5 $(tailscale ip -4 zoe-box) 443    # the box's own tailnet address: refused
```

From the box, `ssh pi@$(tailscale ip -4 zoe-panel)` works; so does the same from an
owner device.

## What the policy allows

Default deny. `config/acl.hujson`:

| from | to | ports |
|---|---|---|
| `tag:zoe-panel` | `192.168.1.218` (via the box's route) | tcp 443, 8007 |
| `tag:zoe-box`, `group:owner` | `tag:zoe-panel` | tcp 22 (the panel's own sshd; Tailscale SSH is not enabled) |

Route auto-approval: `192.168.1.218/32`, only from `tag:zoe-box`. An approver
covers its own prefix and anything inside it, never a wider prefix or another
/32; no exit-node auto-approver exists. Everything else a node advertises waits
for `hs nodes approve-routes`.

Policy edits: change `config/acl.hujson`, then `docker compose kill -s HUP
headscale`. Headscale runs the `tests` block at that reload and **rejects** the
new policy (keeping the old one enforced) if an assertion fails. Add your own
assertions when you widen anything. If a reload is rejected only because a node
was deleted (so a test's tag no longer resolves), re-register the node or edit
that test.

The `8007` rule assumes the Jetson's 8007 listener answers on its LAN address. It
does today (`ss -ltn` shows 443 and 8007 on `0.0.0.0`); if 8007 is ever re-bound to
loopback, the panel loses it over the tailnet exactly as it would on the LAN.

## Trust model

A control server is not a passive relay: it is the authority on **which node keys
are peers**. Traffic between peers is end-to-end WireGuard and the server never
sees it, but a compromised or malicious Headscale can:

- add a rogue node to the tailnet and distribute it to the others as a peer;
- rewrite the ACL or approve routes (the nodes enforce whatever policy it sends);
- advertise a route so panel traffic bound for 192.168.1.218 is steered to a
  node it controls (it still cannot read TLS it has no certificate for).

Anyone with root on the droplet, or the CI deploy key that reaches it, controls the
tailnet. Mitigations, in order of value:

1. **Defence in depth on the box.** Do not rely on the ACL alone. The Zoe services
   keep their own authentication; keep TLS verification on in the panel (ideally
   pin the box's certificate) so a route hijack yields nothing. On the Jetson, add
   a host firewall rule that only accepts `tailscale0` traffic from the panel's
   tailnet address to tcp 443/8007 and drops the rest of `tailscale0`.
2. **No open registration; short single-use keys.** Keys are tagged, single-use,
   1 h. Revoke leftovers: `hs preauthkeys list`, `hs preauthkeys expire --id <ID>`.
3. **Watch the node list.** `hs nodes list` should show exactly: zoe-box,
   zoe-panel, and your own devices. A cron job that diffs it and alerts on change is
   cheap and catches rogue additions.
4. **The droplet.** The container is isolated (own network, read-only root, no
   capabilities, no docker socket, loopback-only HTTP), but it shares a host with
   other services: keep the host patched, SSH key-only, and the deploy key scoped.
5. **Node-key expiry** is off for appliances (they must not fall off the tailnet in
   another city); use `hs nodes expire --identifier <ID>` to cut one off.
6. **Back up the keys** (below) off the droplet: the noise key identifies the
   server to every client.

To cut a node off immediately: `hs nodes delete --identifier <ID>` (or
`hs nodes expire`). **Stopping the Headscale container alone does not tear down
existing tunnels**: nodes keep their last network map. To kill remote access
outright, delete or expire the nodes, or run `sudo tailscale down` on the box.

## Backup and restore

State lives in `/opt/headscale/data`: `noise_private.key` (server identity),
`derp_server_private.key`, `db.sqlite` (users, nodes, keys, approved routes) and
its WAL. Config and policy are in `/opt/headscale/config`. Treat the whole archive
as a secret (mode 600, off the droplet).

```bash
cd /opt/headscale
docker compose stop headscale                      # ~10 s; existing tunnels keep working
tar czf /root/headscale-$(date +%F).tgz data config
docker compose start headscale
chmod 600 /root/headscale-*.tgz                    # then copy off the droplet
```

Restore: `docker compose down`, unpack `data/` and `config/` into
`/opt/headscale`, `docker compose up -d`, then `hs nodes list`. Restore the
database and the noise key **together**; a restored database with a freshly
generated noise key is not something this lab has exercised.

## Rollback / uninstall

Per node (the box and the panel); this returns Tailscale to its logged-out state,
and no route or resolver change persists because the join never changed DNS:

```bash
sudo tailscale down
sudo tailscale logout
```

On the droplet, leaving the411 untouched:

```bash
cd /opt/headscale && docker compose down            # stop (keeps data/)
# remove completely (destroys the tailnet identity: all nodes must re-join):
docker compose down && rm -rf /opt/headscale/data
rm /etc/nginx/conf.d/hs.the411.life.conf && nginx -t && systemctl reload nginx
certbot delete --cert-name hs.the411.life
```

Then delete the `hs` DNS record and the UDP 3478 firewall rule. Verify the411 is
unaffected: `systemctl is-active nginx` and `curl -sI https://the411.life | head -1`.

## Upgrading Headscale

Read the release notes for the target version, take a backup, bump **both** the tag
and the digest in `docker-compose.yml`, run `./lab_test.sh` on a dev box, then
`docker compose pull && docker compose up -d` on the droplet. (The client minimum
for this version is Tailscale v1.80; the clients here are 1.104.1.)

## The lab proof

`lab_test.sh` is the evidence that this bundle works, and the way to re-check it
after any change. It runs entirely in Docker on an **internal** network (no route
off the machine), with the clients in userspace-networking mode so the host's own
`tailscaled`, interfaces, routes and DNS are never touched. It runs the shipped
compose service and shipped `config/`, and renders the shipped nginx snippet; only
the hostname (`hs.lab.test`) and the Zoe box address (swapped for a dummy
container at `10.77.250.40`) are substituted, and the script asserts those are the
*only* differences.

```bash
flock -x /tmp/zoe-test-lane.lock ./lab_test.sh     # on the Zoe Jetson
```

It refuses to start below 1500 MB `MemAvailable`, aborts if that falls under 600 MB,
and tears down containers, network and volume afterwards (and asserts they are
gone). What it proves, with a positive control beside every refusal:

- the panel reaches the dummy on **443 and 8007** through the box's subnet route,
  with every peer path forced through the embedded DERP over nginx;
- the panel is **refused** on an extra port (9999) and on direct access to the box
  and the owner laptop; box and owner reach the panel on 22 only; and the refusal is
  the ACL: widening the policy opens the port, and a widened policy that
  contradicts the `tests` block is rejected at reload;
- route auto-approval covers **only** the /32: a wider /24, an unrelated /24, a
  different /32 and an exit node are all advertised but left unapproved, and an
  untagged node advertising the same /32 is not approved;
- no open registration: a keyless node gets only an approval URL, a bogus key and a
  reused single-use key are refused, and the pending laptop is absent from the node
  list until the admin approves it;
- the nginx snippet upgrades the control protocol (`POST /ts2021` returns 101) and
  DERP, the STUN listener answers, and the DERP map holds only the embedded region.

What the lab **cannot** prove, and therefore what step 8 and the Verify block check
on the real hardware: real Let's Encrypt issuance and the DO firewall letting UDP
3478 in from the internet; kernel-mode `tailscaled` on the Jetson and the Pi (the
lab uses userspace networking); the panel's behaviour at home with `--accept-routes`;
and a client-side STUN round trip: the lab proves the embedded STUN listener answers a raw
Binding request, directly and through its UDP relay, but `tailscale netcheck` run from the
userspace lab client reported `UDP: false` (cause not established), so check
`tailscale netcheck` from the panel on a real network and expect `UDP: true` and
`Nearest DERP: Headscale Embedded DERP`; if not, the usual cause is the UDP 3478 firewall rule.
TLS in the lab is a throwaway CA; production is Let's Encrypt on the host nginx.
