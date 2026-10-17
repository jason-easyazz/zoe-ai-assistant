---
type: runbook
title: R1 relay deployment on the411 (a second public IP, frps in its own container)
date: 2026-10-17
status: DOCS ONLY. Nothing here has been run on the411 (no SSH, no DNS change was made while writing it). Every command marked [lab] was exercised in the F0 lab; everything else is from DigitalOcean's documentation (read 2026-10-17) or from kernel/nginx behaviour and is checked by the verification steps below.
owner_decisions: D1 frp v0.71.0; D2 second public IP on the existing the411 DigitalOcean server, the411's sites untouched; D4 relay.the411.life for now
design: docs/architecture/panel-anywhere.md (sections 2, 4, 5, 10, 11)
---

# R1 deployment runbook: the relay on the411

Placeholders (this repo is public, so no real addresses are written here):

| Placeholder | Meaning |
|---|---|
| `<PRIMARY_IP>` | the411's existing public IPv4, where its sites are served today |
| `<RESERVED_IP>` | the new DigitalOcean Reserved IP (the relay's public address) |
| `<ANCHOR_IP>` | the Droplet's anchor IP, a DigitalOcean-internal address on `eth0` (see step 2) |
| `<RELAY_HOST>` | `relay.the411.life` |
| `<HID>` | the household's relay id, e.g. `h` + 8 hex characters; random, never derived from a name |
| `<TAG>` | the git tag or commit of this repo that the relay is built from |

Who does what: steps marked **OWNER** need the DigitalOcean console, the DNS provider or SSH to the411. Nothing in
this runbook can be done by an agent without that access. Roughly 90 minutes the first time, of which the411's sites
are down for about a second (step 4).

## 0. What this does and does not touch

- Adds one Reserved IP to the411's Droplet and one container stack under `/opt/zoe-relay`.
- Restarts the411's nginx once, to narrow `listen 443` from every address to `<PRIMARY_IP>` (step 4).
  That restart is the only change to the411's own services.
- Does not change the411's sites, certificates, databases or DNS records for the411's own names.
- Does not start any household. A household goes live only with the R1 zoe-data PR (relay listener, ingress rules)
  merged and its cells T5, T10, T11 and C3 passing: this runbook gets the *relay* ready and proven empty.

**Stop conditions** (do not continue, tell the operator):
- the Droplet already has a Reserved IP (DigitalOcean allows one per Droplet; see step 1);
- the411's nginx is not a host nginx under `/etc/nginx`, or it listens on 443 from more than one process;
- the lab (`labs/relay/run.sh lab`) does not pass on the commit being deployed.

## 1. Reconnaissance on the411 (read-only)

**OWNER**, over SSH. These commands change nothing.

```sh
# Droplet identity, anchor IP and whether a Reserved IP is already attached (DigitalOcean metadata service)
curl -s http://169.254.169.254/metadata/v1/id; echo
curl -s http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address; echo     # <PRIMARY_IP>
curl -s http://169.254.169.254/metadata/v1/interfaces/public/0/anchor_ipv4/address; echo   # <ANCHOR_IP>
curl -s http://169.254.169.254/metadata/v1/reserved_ip/ipv4/active; echo               # must print: false
ip -4 -br addr show eth0        # <PRIMARY_IP> and <ANCHOR_IP> both appear on eth0

# who listens on 443 / 80 right now, and how nginx is configured
sudo ss -ltnp 'sport = :443 or sport = :80'
sudo nginx -T 2>/dev/null | grep -nE '^\s*listen\s' | sort -u
sudo grep -rnE '^\s*listen\s+(\[::\]:)?443' /etc/nginx/ | head -50

# firewall, Docker, headroom
sudo ufw status verbose; sudo iptables -S DOCKER-USER 2>/dev/null | head
docker version --format '{{.Server.Version}}'; docker compose version
free -m; df -h /opt
```

Decisions this fixes:
- `reserved_ip/ipv4/active` is `true`: **stop** (one Reserved IP per Droplet). The alternative is a second small
  Droplet for the relay (design D2's own fallback), which makes steps 2 to 4 unnecessary.
- `ss` shows nginx on `0.0.0.0:443` (as expected): continue; step 4 narrows it.
- No Docker on the411: install it from DigitalOcean's/Docker's apt repository first (outside this runbook).
- A Droplet created before 2015-10-20 has no anchor IP (the metadata call returns nothing): DigitalOcean's "enable
  reserved IPs manually" page applies. A Droplet made from a custom image maps the Reserved IP straight to the
  public address instead of an anchor IP; then `<ANCHOR_IP>` below means `<PRIMARY_IP>` and step 4 cannot work.
  Stop and tell the operator in either case.
- RAM: frps + the stream front + the plugin use about 60 MB idle and 150 MB at peak (measured in the lab, README "Measured"). The Droplet
  needs that much free.

## 2. Add the second public IP (a DigitalOcean Reserved IP)

How DigitalOcean delivers it (DigitalOcean documentation, "Reserved IPs" and "Find Droplet Anchor IPs"):
- A Droplet cannot hold a second ordinary public IPv4. The supported way to add one is a **Reserved IP**, free while
  it is assigned to a Droplet ($5/month if left unassigned; not billed under $1 accrued).
- Traffic sent to `<RESERVED_IP>` is NAT-ed by DigitalOcean to the Droplet's **anchor IP**, a second address that is
  already on `eth0` (step 1). The Droplet never has `<RESERVED_IP>` on an interface, and every port of the
  Reserved IP is forwarded to the anchor address.
- DigitalOcean's own advice: bind services that must be reached through the Reserved IP **to the anchor IP**. An
  anchor IP is reachable only through its Reserved IP and from the Droplet itself.
- Limits to know: one Reserved IP per Droplet, three per account by default, no reverse DNS, SMTP blocked. None of
  these matters for the relay.

**OWNER**: in the DigitalOcean control panel, Networking, Reserved IPs, create one in the **same region** as the
411's Droplet and assign it to that Droplet. Or:

```sh
doctl compute reserved-ip create --region <REGION> --droplet-id <DROPLET_ID>
```

Record `<RESERVED_IP>`. Nothing on the411 changes yet. If the Droplet sits behind a DigitalOcean Cloud Firewall,
inbound TCP 443 must be allowed there (it already is if the411's HTTPS works).

Check: `curl -s http://169.254.169.254/metadata/v1/reserved_ip/ipv4/active` on the411 now prints `true` and
`.../reserved_ip/ipv4/ip_address` prints `<RESERVED_IP>`.

**Rollback:** unassign and delete the Reserved IP (`doctl compute reserved-ip delete <RESERVED_IP>`). No other step
depends on it being kept.

## 3. Firewall first (before anything listens on the anchor IP)

Because *every* port of `<RESERVED_IP>` is forwarded to the anchor address, a service of the411 that listens on
`0.0.0.0` (sshd, nginx port 80, a database someone bound wide) becomes reachable through the new IP unless the
firewall says otherwise. Allow only the relay's port on the anchor IP and refuse the rest:

```sh
sudo ufw allow in on eth0 to <ANCHOR_IP> port 443 proto tcp comment 'zoe relay front'
sudo ufw deny  in on eth0 to <ANCHOR_IP> comment 'relay IP: nothing but 443'
sudo ufw status numbered
```

Order matters: ufw evaluates in rule order, so the allow must sit above the deny. If ufw is inactive on the411,
turn it on only after adding an allow for SSH from the operator's address and for 80/443 to `<PRIMARY_IP>`, or use
the DigitalOcean Cloud Firewall (inbound TCP 443 only) on the Droplet instead.

Honest limit: Docker publishes container ports with iptables NAT; that traffic is forwarded to the container and never
traverses the INPUT chain where ufw's rules live, so ufw does not govern the one port Docker publishes (`<ANCHOR_IP>:443`, step 6). That is the intended
exposure; the `deny` above exists for everything that is *not* published (sshd and the411's own services).

Check, from a machine that is not the411 (before step 6, everything must be closed):
`nc -vz -w 3 <RESERVED_IP> 22`, `... 80`, `... 443` all fail.

**Rollback:** `sudo ufw status numbered`, then `sudo ufw delete <n>` for the two rules.

## 4. Narrow the411's nginx from every address to `<PRIMARY_IP>`

The relay's stream front needs `<ANCHOR_IP>:443`. Linux refuses a bind to a specific address while another socket
listens on the wildcard address and the same port, so nginx's `listen 443` (all addresses) must be narrowed first.

Checked on the lab box (Linux 5.15, Python sockets, the same semantics nginx uses): with a wildcard socket
listening, a bind to a specific address on the same port fails with `EADDRINUSE`. Consequence: **a plain
`nginx -s reload` of the narrowed config fails** (it tries to open the new specific socket while the old master
still holds the wildcard one; nginx then keeps running the old config), so the change needs a **restart**, which
drops the411's HTTPS for about a second. Pick a quiet time.

**OWNER**:

```sh
sudo cp -a /etc/nginx /etc/nginx.pre-relay.$(date +%Y%m%d)          # the rollback copy
sudo grep -rnE '^\s*listen\s+(\[::\]:)?443' /etc/nginx/ --include='*' | grep -v pre-relay
```

In every `server` block that has `listen 443 ...;` (IPv4 form, no address), prefix `<PRIMARY_IP>:`:

```
listen 443 ssl http2;          ->    listen <PRIMARY_IP>:443 ssl http2;
```

Leave `listen [::]:443 ...;` lines alone (nginx's IPv6 sockets are IPv6-only by default and do not cover IPv4) and
leave every `listen 80` alone (step 3's `deny` keeps port 80 on the anchor address closed). Keep `default_server`,
`ssl`, `http2`, `reuseport` exactly as they were; only the address changes. One `reuseport` per address:port is
allowed, and moving it to a specific address is fine.

```sh
sudo nginx -t                                   # must say: syntax is ok / test is successful
sudo systemctl restart nginx                    # restart, NOT reload (see above)
sudo ss -ltnp 'sport = :443'                    # expect <PRIMARY_IP>:443 (and [::]:443); no 0.0.0.0:443 and no *:443
curl -sI https://the411.life/ | head -3         # the411's own site still answers
```

If the411's nginx does not come back: `sudo mv /etc/nginx /etc/nginx.narrowed && sudo cp -a /etc/nginx.pre-relay.<date> /etc/nginx &&
sudo nginx -t && sudo systemctl restart nginx`.

Watch for: certbot's `--nginx` installer rewrites `listen` lines only when it installs a *new* certificate; renewals do
not touch them. After any future certbot install on the411, re-run the `ss` check above.

**Rollback:** restore `/etc/nginx` from the copy and `systemctl restart nginx`. The relay stack (step 6) must be
stopped first (`docker compose down`), because it will then be holding `<ANCHOR_IP>:443` which a wildcard
nginx cannot share.

## 5. DNS

**OWNER**, at the411.life's DNS provider:

| Record | Value | Notes |
|---|---|---|
| `relay.the411.life` `A` | `<RESERVED_IP>` | TTL 300. **DNS only, not proxied** (if the zone is on Cloudflare: grey cloud). A proxy would terminate TLS and break both the passthrough and frp's own protocol. |
| `relay.the411.life` `AAAA` | none | the relay has no IPv6 (design section 13) |

`<hid>.zoe.internal` names never touch public DNS:
- `.internal` is a reserved private-use suffix; public DNS will not resolve it and public CAs will not issue for it.
- The panel's agent maps `<hid>.zoe.internal` to the box's LAN address at home and to the relay address away, in its
  own hosts file (the #2080 D4 home/away switch). The relay answers on the SNI it reads, not on a DNS name.
- Nothing about these names is published: no record, no certificate-transparency entry.
- What the relay and any on-path observer do see: the SNI string `<hid>.zoe.internal` in each ClientHello, the
  panel's and box's public addresses, timing and byte counts (design section 8).

Check: `dig +short relay.the411.life` returns `<RESERVED_IP>` from two resolvers. Then, from outside, expect
`nc -vz -w 3 relay.the411.life 443` to still **fail** until step 6.

**Rollback:** delete the record.

## 6. The relay stack (frps in its own container, plugin on loopback)

Topology (identical to the F0 lab, which is what was tested [lab]):

```
<RESERVED_IP>:443 --DO NAT--> <ANCHOR_IP>:443 --docker publish--> front (nginx stream, ssl_preread)
front owns ONE network namespace; frps and the plugin join it:
    front  :443  routes by SNI   *.zoe.internal -> 127.0.0.1:8443 (frps https vhost, SNI passthrough)
                                 (no SNI)       -> 127.0.0.1:7000 (frps control; frp's TLS first byte hides the SNI)
                                 anything else  -> refused
    frps   127.0.0.1:7000, :8443, :7500 (dashboard)         plugin 127.0.0.1:9100
```

Only `<ANCHOR_IP>:443` is published to the host. frps, the plugin and the dashboard are unreachable from outside the
namespace.

### 6.1 Files

On the411, as a dedicated unprivileged login or root:

```sh
sudo install -d -m 0750 /opt/zoe-relay /opt/zoe-relay/etc /opt/zoe-relay/tls
git clone --depth 1 --branch <TAG> https://github.com/jason-easyazz/zoe-ai-assistant.git /opt/zoe-relay/src   # the public repo; or copy a tarball
cd /opt/zoe-relay/src/labs/relay
DOCKER_BUILDKIT=0 docker build -f docker/Dockerfile.frp -t zoe-relay/frp:v0.71.0 .          # verifies the release sha256 for amd64
```

The Dockerfile pins frp v0.71.0 by release sha256 per architecture and the base images by multi-arch index digest [lab:
arm64 built and run; the amd64 hash is the release's published value but was not built on this box]. Do not move to
v0.71.1 before **2026-10-28** (14-day rule; it was released 2026-10-14).

The relay's own CA and server certificate (frpc pins this CA, T9). Generate once, keep `relay-ca.key` OFF the 411
(offline or the operator's laptop); only the server key and the two public certificates go to the server:

```sh
# on the operator's machine
openssl ecparam -name prime256v1 -genkey -noout -out relay-ca.key
openssl req -new -x509 -key relay-ca.key -sha256 -days 1825 -subj "/CN=zoe relay CA" \
  -addext basicConstraints=critical,CA:TRUE,pathlen:0 -addext keyUsage=critical,keyCertSign,cRLSign -out relay-ca.pem
openssl ecparam -name prime256v1 -genkey -noout -out server.key
openssl req -new -key server.key -subj "/CN=relay.the411.life" -out server.csr
printf 'subjectAltName=DNS:relay.the411.life\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n' > ext.cnf
openssl x509 -req -in server.csr -CA relay-ca.pem -CAkey relay-ca.key -CAcreateserial -days 365 -sha256 -extfile ext.cnf -out server.pem
scp server.pem server.key <the411>:/opt/zoe-relay/tls/        # relay-ca.pem goes to the households' boxes, not to the relay
```

Renew `server.pem` before it expires (365 days) with the same CA; boxes keep trusting the CA. Rotate the CA only with a
new `relay-ca.pem` pushed to every box first.

The household registry, one row per household (secrets are random, shown once, stored hashed):

```sh
SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(24))')   # give this to the household's box; never log it
HID=h$(python3 -c 'import secrets;print(secrets.token_hex(4))')
printf '{"households":{"%s":{"secret_sha256":"%s","active":true}}}\n' "$HID" \
  "$(printf %s "$SECRET" | sha256sum | cut -d' ' -f1)" | sudo tee /opt/zoe-relay/etc/households.json >/dev/null
```

`frps.toml`: render `relay/frps.toml.tmpl` (replace `@@ADMIN_PASSWORD@@` with a random string) to
`/opt/zoe-relay/etc/frps.toml`; change `transport.tls.certFile/keyFile` only if the mount path differs. Copy
`relay/nginx-front.conf.tmpl` to `/opt/zoe-relay/etc/nginx-front.conf` with `@@RELAY_NAME@@` replaced by `relay.the411.life` (the lab found that frpc's TLS ClientHello carries the relay's name as its SNI, so that exact name must route to frps' control port). Set `log.to = "console"` (default in the
template) so `docker logs` is the log.

### 6.2 Compose

`/opt/zoe-relay/compose.yaml` (the lab's `compose.yaml`, minus the boxes and the panel, with the network no longer
`internal` and the front's port published on the anchor address only):

```yaml
name: zoe-relay
services:
  front:
    image: nginx@sha256:1ed1b0e1d7652937d6cbdaf4018c7b6fc009a7dd6c3047351e2eddda745de43f   # pinned index digest; re-pin deliberately
    restart: unless-stopped
    ports: ["<ANCHOR_IP>:443:443"]          # the ONLY published port, on the anchor address only
    volumes: ["/opt/zoe-relay/etc/nginx-front.conf:/etc/nginx/nginx.conf:ro"]
    mem_limit: 64m
  relay:
    image: zoe-relay/frp:v0.71.0
    restart: unless-stopped
    network_mode: service:front
    depends_on: [front]
    entrypoint: ["/opt/relay/entrypoint.sh"]      # the preflight: no plugin configured, no frps
    environment: { PREFLIGHT_WAIT_S: "40" }
    volumes:
      - /opt/zoe-relay/etc/frps.toml:/etc/frp/frps.toml:ro
      - /opt/zoe-relay/tls:/etc/frp/tls:ro
    mem_limit: 128m
  plugin:
    image: zoe-relay/frp:v0.71.0
    restart: unless-stopped
    network_mode: service:front
    depends_on: [front]
    command: ["python3", "/opt/relay/plugin.py"]
    environment:                                   # NO RELAY_LAB_MODE here: that variable enables the lab's overwrite switches
      RELAY_REGISTRY: /etc/relay/households.json
      RELAY_BANDWIDTH_LIMIT: "2441KB"             # 20 Mbit/s (design D10)
      RELAY_RATE_MAX: "64"                        # new connections per window per household (see README, finding 1)
      RELAY_RATE_WINDOW_S: "10"
    volumes: ["/opt/zoe-relay/etc:/etc/relay:ro"]
    mem_limit: 64m
```

(The lab's `networks:` block is dropped on purpose: default bridge networking provides the route out for replies and
the publish; `front` is the namespace owner and the only service that needs the published port.)

```sh
cd /opt/zoe-relay && docker compose up -d
docker compose logs relay | head -20      # expect: "preflight: ok ...", then frps "frps started successfully"
docker compose ps
```

A relay that logs `preflight FAILED, refusing to start frps` is the safety working: fix the named problem, do not
bypass the entrypoint.

Per-household suspension and secret rotation are edits to `households.json` (`"active": false`, or a new
`secret_sha256`); the plugin re-reads the file on every call [lab: suspension cell N1]. A running box keeps its
control connection until its next login or user connection.

### 6.3 Verify the relay is up and empty (no household yet)

From a machine that is not the411:

```sh
nc -vz -w 3 relay.the411.life 443                                    # open
openssl s_client -connect relay.the411.life:443 -servername example.com </dev/null 2>&1 | head -3   # closed, no certificate
openssl s_client -connect relay.the411.life:443 -servername h00000000.zoe.internal </dev/null 2>&1 | head -5   # a TLS alert (home offline), no certificate
nc -vz -w 3 relay.the411.life 80; nc -vz -w 3 relay.the411.life 22   # both closed (step 3's deny)
```

From the411: `curl -sI https://the411.life/ | head -1` (the sites still answer), `docker stats --no-stream`
(expect roughly 60 MB across the three containers at idle), `sudo ss -ltnp 'sport = :443'` (nginx on `<PRIMARY_IP>:443`,
`docker-proxy` on `<ANCHOR_IP>:443`, nothing on `0.0.0.0:443`).

## 7. The household's box and the go-live gate

Not part of this runbook's relay-side work, listed so the order is clear:

1. The R1 zoe-data PR is merged: relay listener in the zoe-ui nginx (`labs/relay/README.md`, "Interface for the R1 PR"),
   `ZOE_RELAY_INGRESS`, the never-guest rule, the panel-principal-only device token, the origin entry.
2. Cells T5, T10, T11 and C3 pass against that code in the lab, then against the live relay with a lab panel.
3. On the box: install frpc v0.71.0 (arm64 release tarball, verify its sha256), render `frpc.toml` with the generator
   in `labs/relay/box/frpc_config.py` and run its config test (T6) before the unit starts, create the household CA
   and the leaf for `<HID>.zoe.internal`, and sign the panel's client certificate from its CSR.
4. Start frpc; the relay logs `login` and a proxy named `<HID>.https`; the panel's link probe turns green.
5. Replay gate and L6 time-to-first-audio against the LAN baseline (design section 10, R1).

## 8. Rollback, whole stack, in reverse

```sh
cd /opt/zoe-relay && docker compose down                       # the relay stops answering; households fall back to the LAN path
# DNS: delete relay.the411.life
sudo ufw status numbered; sudo ufw delete <n>                  # the two relay rules (anchor 443 allow, anchor deny)
# optional: restore the411's nginx listens (step 4 rollback) - nothing requires it, the narrowing is harmless on its own
doctl compute reserved-ip delete <RESERVED_IP>                 # or unassign + release in the console
sudo rm -rf /opt/zoe-relay ; docker image rm zoe-relay/frp:v0.71.0
```

Nothing is left on the411 except an nginx that listens on its primary address only (if step 4 is not rolled back).

## 9. Owner steps, in order

1. SSH to the411 and run the step 1 read-only checks; send the output (or confirm the stop conditions are clear).
2. Create and assign the Reserved IP (step 2); note `<RESERVED_IP>` and `<ANCHOR_IP>`.
3. Add the two ufw rules (step 3).
4. Narrow nginx and restart it in a quiet minute (step 4).
5. Add the `relay.the411.life` A record, DNS only (step 5).
6. Generate the relay CA and server certificate off the server, copy the public parts (step 6.1).
7. Bring the stack up and run the step 6.3 checks.
8. Wait for the R1 zoe-data PR; then the go-live gate in step 7.

## 10. Operations notes

- **Logs hold hids and client addresses** (the front logs each connection's SNI and source address; the plugin logs
  hid and decision, never a secret). Treat them as personal data; set Docker log rotation
  (`max-size`/`max-file` in the compose `logging:` key) and do not ship them anywhere.
- **Back up** `households.json` and the CA private key (offline). Nothing else on the relay is state.
- **Upgrade frp** only to a release at least 14 days old, by changing the pinned version and both sha256 values in
  `Dockerfile.frp`, rebuilding, and re-running the lab (`./run.sh lab`) first.
- **This plugin is the lab stub** with a registry file: right for one to a few households (R1). R2 replaces it with a
  registry-backed session master and enrolment codes; the contract tests in `labs/relay/tests` carry over.
- **Single point of failure by design:** relay down means away panels show "can't reach the relay"; at home the LAN
  path is untouched.
