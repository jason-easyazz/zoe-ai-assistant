---
type: research
title: Credential vault - a household password manager Zoe can use for automated sign-in without the model ever seeing a secret (field research, 2026-10-18)
date: 2026-10-18
status: research-only; nothing built, nothing installed, nothing changed on any live system. The design is docs/architecture/credential-vault.md.
description: Research record behind the credential-vault design. It covers the candidates (Vaultwarden, official Bitwarden standard and Lite, KeePassXC, Infisical, OpenBao) with licence, release cadence, arm64, RAM, backup and admin hardening, and Vaultwarden's audit and advisory history; how a broker reads a Bitwarden-protocol vault (the bw CLI and bw serve, rbw, the Bitwarden SDK licences); Bitwarden's agent projects and 1Password's agentic autofill; origin binding (Bitwarden URI match types, the Public Suffix List, CDP frame origins, the iframe and extension-clickjacking attack history); the 2FA trade-off; and a read-only survey of the live box (systemd, TPM, privileges, disk encryption, RAM).
builds_on: docs/research/zoe-action-agent-2026-10.md (section 3.3 "logging in without Zoe knowing the password", section 6.0 spine), docs/architecture/zoe-abilities.md section 5 (the proposed account_secrets vault), services/zoe-data/browser_broker.py, services/zoe-data/agent_safety.py
---

# Credential vault: research (2026-10-18)

Owner question (2026-10-18, verbatim): *"should we build in a password database into zoe, so website passwords etc can
be saved securely for automated access?"* Then: *"yes start the design PR"*.

The design built on this record is [credential-vault.md](../architecture/credential-vault.md). Nothing was installed
or changed to write it. No real account, site, credential or household detail appears here.

**Evidence tags.**
- **[verified]**: read in a primary source this session. That means a release API, a registry manifest, an official
  doc page, or source code fetched with `opensrc`, or a read-only command on the box.
- **[secondary]**: a third-party article, a search summary or a forum thread.
- **[unverified]**: inference or memory, stated so the lab can check it.

Source versions read: vaultwarden `main` (release 1.37.4), bitwarden/clients 2026.10.0, doy/rbw v1.15.0,
bitwarden/sdk and sdk-internal `main`, keepassxc `develop`, playwright-python 1.59.0. All were fetched with `opensrc`
into `~/.opensrc/repos/`.

## 0. What the brief asked, and the short answers

| Question | Answer | Section |
|---|---|---|
| Build a password database into Zoe? | No. Adopt a Bitwarden-protocol server so the family uses the official apps; Zoe is a member that can use only what is shared with her | 1 |
| Which server? | Vaultwarden (small, free features, arm64); runner-up Bitwarden Lite (official, heavier, licence for sharing) | 1.1, 1.2 |
| How does the broker read the vault? | The official `bw` CLI, privately; not `bw serve` on TCP; rbw is stale for this; the SDK licences exclude Vaultwarden | 2 |
| Origin binding | Take the frame's committed origin from the browser over CDP, exact host by default, PSL with the private section for base domain, never fill a cross-origin iframe | 3 |
| Is there an off-the-shelf "agent uses a password without seeing it"? | Bitwarden Agent Access (early preview, hands the secret to the agent process) and 1Password agentic autofill (proprietary, cloud): right idea, not a fit | 4 |
| 2FA | Send to the phone; autonomous TOTP only by explicit opt-in per item | 5 |
| Can a separate broker user protect against a compromised zoe-data today? | **No.** The `zoe` user has passwordless sudo and is in the `docker` group | 6 |

## 1. Candidates

### 1.1 Vaultwarden

- **Licence and project.**
  - AGPL-3.0-only (`Cargo.toml`) [verified].
  - About 69k stars; last push 2026-10-16 [verified, GitHub API].
  - The README says the project is "not associated with Bitwarden", although one maintainer is employed by Bitwarden
    and contributes independently [verified, README].
  - By commit count, two maintainers carry most of the work (dani-garcia, BlackDex) [verified, contributors API].
  - Running it unmodified for a household raises no AGPL obligation [unverified; not legal advice].
- **Release cadence** [verified, releases API]:
  - The latest release is 1.37.4 (2026-10-05).
  - Between 2025-07-30 and 2026-10-05 there were 1.34.3, 1.35.0-1.35.8, 1.36.0 and 1.37.0-1.37.4. That is about 15
    releases, with a five-month gap from August to December 2025.
- **Images.**
  - Published at `docker.io/vaultwarden/server`, `ghcr.io/dani-garcia/vaultwarden` and `quay.io/vaultwarden/server`
    [verified, wiki].
  - The 1.37.4 manifests list `linux/arm64` (plus amd64, arm/v7, arm/v6) on both Docker Hub and GHCR [verified,
    registry APIs].
- **RAM.**
  - There is no official figure [verified, README and wiki].
  - One `docker stats` test on a fresh install with one user: Vaultwarden on SQLite 58 MB, Bitwarden Lite about
    470 MB, the standard Bitwarden stack about 2.4 GB [secondary, ssdnodes.com, seen through a search summary].
  - Other guides say about 50 MB at idle [secondary].
  - The image defaults to 10 worker threads (`ROCKET_WORKERS`) [verified, wiki].
  - Lab V0 measures it with a family-sized vault.
- **Databases.** SQLite by default; MySQL/MariaDB and PostgreSQL are optional [verified, wiki].
- **Backups** [verified, wiki "Backing up your vault"]:
  - Required: `db.sqlite3`, taken with `sqlite3 … ".backup …"` or `VACUUM INTO`, never a plain copy of a live file;
    and `attachments`.
  - Optional: `sends` and `icon_cache`.
  - Recommended: `config.json`, which holds some plaintext sensitive settings; and `rsa_key*`, whose private key
    should be encrypted.
  - On restore, stop the server and delete a stale `db.sqlite3-wal` first.
- **Admin page** [verified, wiki "Enabling admin page" and `src/main.rs`]:
  - `ADMIN_TOKEN` should be an Argon2id PHC string from `vaultwarden hash` (`--preset owasp` exists).
  - In docker-compose, `$` must be escaped as `$$`.
  - The page is **disabled** when `ADMIN_TOKEN` is unset and `config.json` has no `admin_token`.
  - A token saved through the admin UI overrides the environment variable.
- **Signups and hardening** [verified, wiki "Disable registration", "Hardening guide"]:
  - `SIGNUPS_ALLOWED=false` stops open registration, but organisation admins can still invite users. Invitations are
    disabled separately.
  - The hardening guide recommends fail2ban and running as non-root (`-u 1000:1000`); the image runs as root by
    default.
- **HTTPS.**
  - The web vault needs a secure context for WebCrypto [verified, wiki "Enabling HTTPS"].
  - `DOMAIN` must include the scheme [verified, `src/config.rs`].
- **Mobile push.**
  - It is off by default (`PUSH_ENABLED=false`) [verified, `.env.template`].
  - When on, the server gets a token from `identity.bitwarden.com` and posts registration and send events to
    `push.bitwarden.com`. Each event carries user, organisation and device ids, the update type and the installation
    id [verified, `src/api/push.rs`]. Whether item contents can ever be in a payload was not traced [unverified].
  - Mobile apps do not use WebSockets, so without push they sync on open and on refresh [verified, wiki].
  - **Push metadata would leave the box, so the design keeps it off.**
- **Client compatibility** [verified, release notes; secondary, discussion #7615]:
  - There is no version matrix.
  - 1.37.0 (2026-07-24) was "required for … clients 2026.7.0+" and 1.37.2 (2026-08-22) for 2026.8.0+.
  - Users reported sign-in failures, stuck sync and TOTP rejections from 2026-08-22 until early October.
  - The fixes were to log out and back in, or to reinstall the extension.
- **Audits** [verified, wiki "Audits"]:
  - No Cure53-style audit programme.
  - A German BSI code analysis (CAOS) of 1.30.3, published 2024-06.
  - An ERNW partial pentest (2024-10) that found three vulnerabilities, including an authentication bypass.
  - Private audits whose reports were not published.
- **Advisories** [verified, GitHub security advisories API; the API returned 30 and may be incomplete]:
  - 2024-12: group update or delete by a known UUID (high).
  - 2025-01: admin-panel RCE (high), privilege escalation (high), admin CSRF (high).
  - 2026-02: **cipher enumeration ignoring collection permissions**.
  - 2026-03: manager-role collection escalation (high ×2), partial update of another user's cipher, and a 2FA
    rate-limit bypass.
  - 2026-04: brute-force protection bypass, an unconfirmed owner purging an organisation vault, **cross-organisation
    group binding granting read/write**, refresh tokens not invalidated on a stamp rotation, and a WebAuthn assertion
    processed before its signature was checked.
  - 2026-05: four SSO advisories, two of them high.
  - 2026-10-03: eleven medium advisories fixed in 1.37.0.
  - Release 1.37.4 lists seven more private advisories, one of them high, about **organisation member revocation**.
  - **Pattern: most of the 2026 findings are organisation, collection and role authorisation bugs.** The design
    therefore does not rely on collection permissions to keep items away from Zoe, and uses one small organisation
    per member [inference].
- **Features** [verified, README and `src/db/models/cipher.rs`]:
  - Organisations with collections, groups, roles, event logs and policies; emergency access; Sends; attachments;
    TOTP, YubiKey, Duo and FIDO2 two-factor login; an admin backend.
  - Per collection, `hide_passwords` becomes the client hint `viewPassword`, and the server still sends the encrypted
    cipher. Bitwarden's own help page says hidden passwords do "not completely prevent user access" [verified,
    bitwarden.com/help/collection-permissions].
  - **Org keys:** every member of an organisation holds its key, so any organisation item that reaches a member can
    be decrypted by that member. Collections are a server-side filter. This is from the Bitwarden security white
    paper's org-key model [verified for the model; inference for the consequence].

### 1.2 Official Bitwarden self-hosted

- **Standard install:**
  - At least 2 GB RAM (4 GB recommended), 12 GB disk, x64, MSSQL [verified, bitwarden.com/help/install-on-premise-linux].
  - **Too heavy for this box, and not arm64.**
- **Bitwarden Lite** (formerly "unified"; out of beta 2025-12; `ghcr.io/bitwarden/lite`):
  - At least 200 MB RAM and 1 GB disk.
  - SQLite, PostgreSQL, MySQL or MSSQL.
  - Its documentation says it is "for personal use and home labs" and can run on ARM [verified,
    bitwarden.com/help/install-and-deploy-lite]. The manifest includes `linux/arm64` [verified, GHCR].
  - The about-470 MB measurement above is [secondary].
- **Licence.**
  - The server is AGPL-3.0, except `/bitwarden_license` (SSO and commercial features), which is source-available
    and needs a paid subscription. The clients are GPL-3.0 [verified, `LICENSE_FAQ.md`].
  - Paid features on a self-hosted server are unlocked by a licence file from the cloud account [secondary].
  - Which plan a household needs for an organisation on Lite was not confirmed [unverified]. Assume a Families
    subscription for sharing.
- **Audits.** Cure53 almost every year from 2018 to 2025, plus ETH Zurich (2025, core cryptography), Unit 42,
  IOActive, Mandiant, Fracture Labs and Insight Risk Consulting [verified, bitwarden.com/help/is-bitwarden-audited].
  These cover the clients and the server code that Lite is built from.
- **Verdict:** a credible runner-up. It is the vendor's own code with an audit history, but it is heavier in RAM,
  needs a licence file and a likely subscription for sharing, and was a beta until 2025-12.

### 1.3 KeePassXC plus a `.kdbx` file

- KeePassXC 2.7.12 (2026-03-10), GPL-3 [verified].
- `keepassxc-cli show -a Password db.kdbx Entry` prints a named attribute. It unlocks with a key file, a password
  on stdin, or a YubiKey [verified, man page].
- pykeepass 4.2.0 (2026-08-09) is GPL-3.0 [verified, PyPI].
- **Family sync** needs KeePassDX or Strongbox plus Syncthing or WebDAV. A `.kdbx` is one file, so concurrent edits
  make conflict copies that someone merges by hand (KeePassXC's Database > Merge) [verified, user guide contents;
  unverified, the end-to-end behaviour].
- **Per-member sharing with Zoe** would need one file per member and a key for Zoe on each.
- **Verdict:** fine for one technical user. It fails the "users never run code" and "family uses their phones" rules.

### 1.4 Infisical and OpenBao (service secrets only)

- **Infisical** v0.166.8 (2026-10-15): the core is MIT, `ee/` is under another licence; there is an arm64 image of
  about 600 MB compressed [verified]. Official sizing for a small deployment is an app at 4 GB, Postgres at 8 GB and
  Redis at 4 GB [verified, Infisical docs]. **Not a fit.**
- **OpenBao** v2.7.1 (2026-10-01), MPL-2.0; the arm64 binary is 68.7 MB [verified].
  - The static seal keeps a 32-byte key in an environment variable or a file [verified, openbao.org docs]. So without
    a cloud KMS, the unseal key sits on the same disk.
  - Its RAM was not measured [unverified].
  - **Overkill for about twenty service secrets on one box.**
- **sops** v3.13.3 (MPL-2.0) and **age** v1.3.2 (BSD-3) [verified]: light tools for encrypted files and backups.
  The age identity still has to live somewhere. The design uses age for the backup archive, with the key off the
  box.
- **Neither tool is a family password manager.** For service secrets the design prefers per-service systemd
  credentials after the privilege fix (design section 13), not a new server.

## 2. How a broker reads a Bitwarden-protocol vault

- **`bw` CLI** (`@bitwarden/cli` 2026.10.0, 2026-10-15, roughly monthly) [verified, npm and the releases API]:
  - It is GPL-3.0 outside `bitwarden_license/` [verified, `LICENSE.txt`].
  - It needs Node ≥ 22; Node 22.23.3 is on the box [verified].
  - Linux arm64 single-file builds of about 44 MB exist [verified, releases].
  - `bw config server <url>` targets a self-hosted server [verified, help page and `program.ts`].
  - `bw login --apikey` (`BW_CLIENTID` / `BW_CLIENTSECRET`) still needs `bw unlock` with the master password. Unlock
    can be non-interactive with `--passwordfile` or `--passwordenv` and returns a session key (`BW_SESSION`)
    [verified, help page and `unlock.command.ts`].
  - `bw list items --organizationid <id>` and `--collectionid <id>` filter natively [verified, `vault.program.ts`].
  - RAM and latency per call were not measured. One third-party figure is about 0.85 s per `bw get` on a desktop
    [secondary]. Measure in V0.
- **`bw serve`** [verified, `serve.command.ts`, `oss-serve-configurator.ts`]:
  - It defaults to `localhost:8087`, with `--hostname all` available, and it accepts `unix://<path>`.
  - **It has no authentication.** Its only protection is "origin protection": a 403 if the `Host` header is not
    allowed or if any `Origin` header is present. `--disable-origin-protection` turns that off.
  - So any local process that reaches the port can read the unlocked vault.
  - **The design rejects it on TCP.** At most it may listen on a 0600 Unix socket inside the broker's private runtime
    directory.
- **rbw** v1.15.0 (MIT) [verified, GitHub API, `CHANGELOG`, source]:
  - **No commits since 2025-12-31.** Two open PRs (2026-06, 2026-09) are unmerged.
  - Its agent holds the master and org keys in `mlock`ed, zeroised buffers. mlock failure is only a warning. The agent
    sets `PR_SET_DUMPABLE=0` and `RLIMIT_CORE=0` ("not perfect protection", says its source).
  - Organisation items are decrypted, but there is **no collection concept** in the code.
  - Unlock goes through `pinentry`, which a script can stand in for; non-interactive unlock is only in an open PR.
  - The defaults are a 3600 s lock timeout and a 3600 s sync interval.
  - **Verdict:** a good reference implementation in Rust, not the adopted client.
- **Bitwarden SDKs** [verified, `LICENSE` files]:
  - `bitwarden/sdk` is the Secrets Manager SDK under the Bitwarden SDK License v1. It is not OSI, and §3.3 forbids
    "applications for use with software other than Bitwarden (including non-compatible implementations of
    Bitwarden)".
  - The Python package `bitwarden-sdk` 2.1.0 exposes only Secrets Manager [verified, PyPI].
  - `bitwarden/sdk-internal` (the password-manager core) is GPL-3.0 or the SDK License v2, at the user's choice. It
    has Swift, Kotlin and WASM bindings but no Python one [verified].
  - **The SDK licence is a real risk with Vaultwarden. The design does not use it.**
- **A pure-Python client** does not exist as a maintained package. `bitwarden-keyring` only wraps `bw`
  [verified, PyPI]. The cryptography is described in the Bitwarden security white paper:
  - PBKDF2 with 600,000 iterations by default, or Argon2id;
  - an HKDF stretch to 512 bits;
  - AES-256-CBC with HMAC;
  - RSA-OAEP for organisation keys.
  The exact EncString format is best read from rbw or the clients. Porting it is feasible with `cryptography`, but it
  would be a "build". **It is a fallback only if `bw` fails V0.**

## 3. Origin binding

### 3.1 Getting the real origin at fill time

- **CDP:** `Page.getFrameTree` returns frames with `id`, `parentId`, `url`, `securityOrigin` and
  `domainAndRegistry`, the browser's own eTLD+1. `Target.getTargetInfo` exists [verified, `browser_protocol.json`].
  A cross-origin iframe usually runs out of process as its own target, so the broker must look at child targets too
  [unverified; lab V1 checks it].
- **Playwright** 1.59: `page.main_frame`, `page.frames`, `frame.url`, `frame.parent_frame` (null for main or detached
  frames), and `frame_locator` to enter an iframe [verified, source and docs]. Playwright frame URLs are fine for
  tests. The broker uses CDP's `securityOrigin` because it is the browser's committed origin, not a string anyone
  passed in.
- **Today's browser code** launches CloakBrowser (a Playwright-compatible Chromium) inside zoe-data, one context per
  call, behind `assert_public_url` and `guard_browser_page`. That guard re-checks every redirect hop against private
  addresses [verified, `services/zoe-data/browser_broker.py`, `agent_safety.py`].
- **Click, type and fill do not exist yet.** They are action Phase 4b. `requires_confirmation` and
  `requires_live_takeover` are declared but never set [verified, code; consistent with the action record 3.3].

### 3.2 Matching rules

- **Bitwarden's URI match types** are base domain (the default), host, starts with, regular expression, exact and
  never. The help page warns that starts-with and regex "can be quite dangerous if used incorrectly" [verified,
  bitwarden.com/help/uri-match-detection].
- **Base domain uses the Public Suffix List with its private section.** The clients call `tldts` `parse(...,
  { allowPrivateDomains: true })` in `libs/common/src/platform/misc/utils.ts` [verified, source]. A private suffix
  such as a shared static-hosting domain is therefore its own eTLD, and tenants under it do not match each other.
- **The design** makes exact host the broker's default, honours base domain only where the member chose it, treats
  starts-with as exact, refuses regex, and cross-checks its own PSL result against the browser's `domainAndRegistry`.

### 3.3 Attack history worth designing against

- **Iframe autofill (Flashpoint, 2023-03).** The Bitwarden extension's autofill-on-page-load filled credentials into
  cross-origin iframes and into subdomains under the base-domain rule. It was first reported in 2018, kept for
  legitimate embedded logins, and is off by default with a warning [secondary, Flashpoint via BleepingComputer].
  → **The design never fills a cross-origin frame unless the item names it.**
- **AutoSpill (2023-12).** Android WebView autofill leaked credentials into the host app. Several managers were
  affected [secondary]. → It shows that "which frame owns the field" is the whole question.
- **DOM-based extension clickjacking (Marek Tóth, DEF CON 33, 2025-08)** [verified, marektoth.com]:
  - Invisible overlays tricked extension autofill.
  - Subdomain filling by default meant an XSS on any subdomain could steal parent-domain credentials.
  - Bitwarden was fixed in 2025.8.2.
  - → **The broker has no user-click UI to hijack. The subdomain lesson is why exact host is the default.**
- **The action record's own survey** cites OpenAI (2025-12) and Brave (2025-10) on prompt injection through pages
  and screenshots [secondary, action 3.3]. → **The model never holds the secret and never chooses the origin.**

## 4. Agent-credential prior art

- **Bitwarden Agent Access** (`bitwarden/agent-access`) [verified, GitHub API and README; secondary, press release
  2026-03-24]:
  - Apache-2.0, about 175 stars; created 2025-12-19, last push 2026-10-15, latest tag seen v0.11.0.
  - Self-described as an "early preview stage".
  - A provider runs `aac listen`. An agent pairs over a Noise-protocol tunnel through a relay (demo only) and asks for
    a credential by domain or item id.
  - `aac connect` returns JSON **including the password**. `aac run` injects it into a child process's environment.
  - Its provider is `bw`, so Vaultwarden plausibly works [unverified].
  - Each request is approved by a human [secondary].
  - → The approval idea is right, but **the secret is handed to the agent process**. The design instead keeps it in
    the broker, which fills directly. Watch the project; do not adopt it.
- **Bitwarden MCP server** (`bitwarden/mcp-server`, GPL-3.0) [verified, README]:
  - It wraps `bw` and **returns secrets to the model**; its README warns about this.
  - → **Rejected outright:** it is the opposite of the owner's rule.
- **1Password Secure Agentic Autofill** [secondary, 1password.dev, search summary]:
  - The user approves a prompt. The item travels encrypted over a Noise channel to a 1Password extension inside the
    agent's headless browser, which fills the form.
  - Login items with TOTP, not passkeys.
  - Early access with Browserbase from 2025-10, then a browser-agent integration in 2026-07.
  - → It is **the same shape as this design's fill mode**, which is good evidence the pattern is right. It is
    proprietary and cloud-coupled, so it is not adoptable here.

## 5. One-time codes: the trade-off

- **TOTP in the same vault as the password** turns two factors into one secret on one box. Bitwarden itself lets
  users do this, and it is a known convenience-versus-separation trade-off [unverified as a cited claim; it follows
  from the model].
- **For Zoe**, a broker compromise or a stolen box (with unattended unlock) would then mean full account takeover
  for every item with a seed.
- **The alternative** keeps the second factor on the member's phone. Zoe pauses and asks for the code, which is short
  lived and single use. A day-time errand loses about thirty seconds; a 3 a.m. errand waits until morning.
- **Recommendation:** send to the phone, and allow autonomous TOTP only per item, never for money, email, government
  or health logins.

## 6. The live box (read-only survey, 2026-10-18)

| Fact | Evidence | Consequence |
|---|---|---|
| systemd 249 | `systemctl --version` [verified] | `LoadCredential=` exists (v247+); `LoadCredentialEncrypted=` and `systemd-creds` arrived in v250, so they are absent [verified, systemd NEWS and the missing binary] |
| A firmware TPM 2.0 exists | `/dev/tpm0`, `/dev/tpmrm0`, `tpm_version_major` = 2, the `tpm_ftpm_tee` driver, Jetson Linux R36.4.3 [verified]; NVIDIA documents the OP-TEE fTPM for Orin [verified, NVIDIA docs] | sealing is possible with `tpm2-tools`; the fTPM's keys change across major Jetson Linux releases [secondary], so a sealed secret needs re-sealing on upgrade; a whole-box thief holds the TPM too |
| Root filesystem not encrypted | `lsblk`: `/` is plain ext4 on NVMe [verified] | anything at rest is readable by someone with the disk |
| **`zoe` has passwordless sudo** | `sudo -n true` exits 0 [verified] | any code execution as `zoe` is root |
| **`zoe` is in the `docker` group** | `id` [verified] | the Docker socket is root-equivalent on its own |
| zoe-data, the Flue sidecars, Kokoro and llama.cpp run as **user** services of `zoe` | `systemctl --user list-units` [verified] | they share that root-equivalent uid with coding agents and the CI runner context |
| zoe-data reads secrets from `EnvironmentFile=` | its unit file (values not read) [verified] | every `zoe` process can read the same files |
| About 0.9-1 GB available memory with the voice stack up, at the time of the check | `free -m` [verified] | a Chromium sign-in must wait for headroom; Vaultwarden must be memory-capped |

**The headline:** a broker running as its own user is still defeated by any code execution as `zoe` until the
privilege fix (design P0) lands. Before P0 the broker still protects against web content and the model, which are
the realistic attackers for an agent that browses.

## 7. Service-secret topology (names only)

From the repo's records ([zoe-abilities.md](../architecture/zoe-abilities.md) section 5, the abilities research
record section 6, and the operator's own topology notes):
- **Where they live:** the service tokens (the Postgres password, the Telegram bot token, the Home Assistant token,
  Music Assistant, provider keys, the forget-ledger HMAC salt) sit in several `.env` files: the repo root,
  `services/zoe-data/`, and agent-profile env files under the home directory. One gateway token also has an
  authoritative copy in a JSON config.
- **Rotation** means editing every copy and recreating, not just restarting, any container that baked the old value
  at creation. That gap has caused an outage before. Moving every secret into one rotation record (design S1)
  answers this.
- **Exposure:** every one of those files is readable by every process running as `zoe`, which includes coding
  agents. The owner brief names a GitHub token leak on 2026-10-16 as the motivating example. This record does not
  describe that incident: it is not recorded in the repository, and it should not be.
- **Recommendation:** do not make services fetch secrets from a vault at boot. That couples the voice stack's start
  to a password manager, and it creates a circular dependency the moment the vault uses Postgres. Instead:
  - the owner's vault holds the master copy and rotation notes;
  - after P0, each unit gets only its own secrets through `LoadCredential=` or Docker secrets;
  - long-lived agent tokens become short-lived ones.

## 8. Honest limits of this record

- Nothing was run. RAM figures for Vaultwarden, Lite and `bw` per call are third-party or unmeasured. Lab V0 measures
  them on the box's architecture.
- Whether the Bitwarden mobile apps trust a user-installed household CA was not checked.
- How Bitwarden licensing applies to a household organisation on Lite was not confirmed.
- The seven private Vaultwarden advisories in 1.37.4 were not visible.
- Whether Agent Access works against Vaultwarden was not tested.
- The OOPIF-target behaviour of cross-origin frames under CloakBrowser is a lab question.

## 9. Sources (accessed 2026-10-18)

- Vaultwarden: `github.com/dani-garcia/vaultwarden` (README, `Cargo.toml`, `src/main.rs`, `src/config.rs`,
  `src/api/push.rs`, `src/db/models/cipher.rs`, releases and security-advisories APIs); wiki pages "Which container
  image to use", "Backing up your vault", "Enabling admin page", "Disable registration of new users", "Hardening
  Guide", "Enabling HTTPS", "Enabling Mobile Client push notification", "Enabling WebSocket notifications",
  "Changing the number of workers", "Audits", "Differences from the upstream API implementation"; discussion #7615.
- Bitwarden: bitwarden.com/help/install-on-premise-linux, /install-and-deploy-lite, /is-bitwarden-audited,
  /collection-permissions, /uri-match-detection, /cli, /bitwarden-security-white-paper; `bitwarden/server`
  `LICENSE_FAQ.md`; `bitwarden/clients` 2026.10.0 (`apps/cli/src/program.ts`, `serve.command.ts`,
  `unlock.command.ts`, `vault.program.ts`, `libs/common/src/platform/misc/utils.ts`, `LICENSE.txt`); `bitwarden/sdk`
  and `sdk-internal` LICENSE files; `bitwarden/agent-access` README; `bitwarden/mcp-server` README; npm registry
  `@bitwarden/cli`; PyPI `bitwarden-sdk`, `bitwarden-keyring`.
- rbw: `github.com/doy/rbw` v1.15.0 (`README`, `CHANGELOG`, `src/config.rs`, `src/locked.rs`, `src/actions.rs`,
  `rbw-agent`).
- KeePassXC: releases API, `docs/man/keepassxc-cli.1.adoc`, user guide; PyPI `pykeepass`.
- Infisical: releases API, LICENSE, infisical.com/docs/self-hosting/configuration/requirements. OpenBao: releases
  API, openbao.org/docs/configuration/seal/static. sops and age: releases APIs.
- systemd: NEWS "CHANGES WITH 250"; freedesktop `systemd-creds` man page. NVIDIA: Jetson Linux r36 Developer Guide
  "Firmware TPM".
- Chromium DevTools protocol `browser_protocol.json` (Page.Frame, Target); playwright-python 1.59.0 source and docs.
- Attacks: flashpoint.io (Bitwarden iframe autofill, 2023, secondary); AutoSpill coverage (2023-12, secondary);
  marektoth.com/blog/dom-based-extension-clickjacking (2025-08, updated 2026-01-14).
- Third-party RAM figures: ssdnodes.com Vaultwarden vs Bitwarden comparison and selfhosting.sh guides (secondary).
- 1Password agentic autofill: 1password.dev (secondary, search summary). Bitwarden Agent Access press release,
  BusinessWire 2026-03-24 (secondary).
