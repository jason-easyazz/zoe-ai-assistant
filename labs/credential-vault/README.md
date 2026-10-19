# Credential-vault lab (V0 + V1)

Hermetic proof of phases V0 and V1 of `docs/architecture/credential-vault.md` (section 16). Record, not a contract.
The broker under test is `services/zoe-vaultd/`, copied into the image verbatim.

**What runs:** one internal Docker network (`--internal`: no route out, proven by cell H1) and three memory-capped
containers, all removed at the end of every run:

| container | what | cap |
|---|---|---|
| vault | `vaultwarden/server:1.37.4@sha256:efb3cde9...` as uid 1000, read-only root, TLS from a throwaway lab CA, signups on only while the family registers | 256 MB, no swap |
| sites | `lab/sites.py`: the real site (`login.example.test`), look-alikes, a hosted widget, the attacker (`evil.test`) and the witness, which records a sha256 per submitted field and whether the run's canary marker was in the request (never a raw value) | 96 MB |
| client | the official `bw` CLI 2026.9.1 (OSS arm64 build, sha256-pinned in `driver.py`), the members' and Zoe's clients, and the broker's three units under their own uids (`lab/provision.sh`): `zoe-vault` (core), `zoe-vault-browser` (Chromium over a private pipe), `zoe-vault-phone`; `zoe-svc` stands in for zoe-data | 768 MB, no swap |

The client image is Debian bookworm (`node:22-bookworm-slim@sha256:e21fc383...`) + Debian's `chromium`, `python3`,
`python3-cryptography` (lab seeding only) and `publicsuffix`. A scripted family (alice, bob, carol, kid) registers,
creates `alice + Zoe`, `bob + Zoe`, `Household + Zoe` and a family organisation Zoe is not in, and fills a
family-sized vault (about 1,100 items). Every item Zoe can use carries a synthetic canary in its username, password,
notes and a hidden field. Master passwords are random per run, written to 0600 files on a tmpfs, never printed.

**Run:** `labs/credential-vault/run.sh [--keep-image] [PIECE ...]`, PIECE = `v0` (V0 cells and measurements),
`A` (V1, V5), `B` (V2), `C` (V3, V4), `D` (V6), `E` (V7, V8, V18, V9), `F` (V13, V17); default all seven. Each piece
is its own lab (bring up, seed, its cells, tear down) inside ONE hold of `flock -x /tmp/zoe-test-lane.lock`, 2.5-6
minutes, and the lane is released for a minute between pieces so other agents' test runs interleave. Gates, checked
before every piece: refuses while `night_busy.sh test` or `night_busy.sh deploy` says pause, and 02:05-02:45 (it
would overlap the night window); waits up to 30 min for `MemAvailable >= 2000 MB` before taking the lock (and up to
2 min after); runs niced. A watchdog in `driver.py` kills a piece if `MemAvailable` drops below 700 MB, the night
window starts, or the piece passes 15 minutes. Teardown removes the containers, network and volumes (and the image
unless `--keep-image`) and asserts nothing is left. Results: `last-result-<piece>.json`, combined into
`last-result.json` by `driver.py --combine`; these, `last-console.log` and `.bin/` are git-ignored.

**Negative controls never live in the broker.** Each one restarts a unit through `lab/labcore.py`, which monkeypatches
exactly one protection off (`substring`, `nopsl`, `inherit`, `logsecret`, `otp_via_client`, `nosync`, `timeout_allow`,
`trust_approval_id`, `debug_get`, `nopeer`, `norecheck`, `unlock_for_client`, `shared_devtools`, `group_widened`).

## Result

### 31/31 cells pass, every negative control red (2026-10-19, one lane-lock hold per piece)

Run as seven pieces (v0, A-F), each 2.5-7 minutes inside one hold of the test-lane lock, `MemAvailable` 2009-3186 MB
at the start of each, after the second review round (paired-phone approvals, redirect hops, lock, audit head): v0-D
07:56-10:14, E and F 10:28-10:36 on the final broker (the last change only touched what a login returns after a lock).
Every piece also re-proved H1. A cell whose negative control does not go red is reported as a
FAIL by `lab/common.py`, whatever its positive half says.

| cell | piece | proves (green) | negative control (red) |
|---|---|---|---|
| H1 hermetic | all | from the client and the sites, TCP to 1.1.1.1, 8.8.8.8 and a GitHub address fails; `example.com` does not resolve | - |
| M1 unattended unlock | v0 | `bw login` / `bw unlock --passwordfile` (0600 file, uid `zoe-vault`) return a session key, no prompt | a wrong password file: refused |
| M2 org filtering | v0 | `--organizationid` returns exactly that org's Zoe-can-use items (41 / 1 / 1); nothing of alice's personal vault, her own collection, or the family org Zoe is not in | filter by the family org: empty |
| V1c right origin (client) | v0 | `bw list --url` returns the bound item; its credentials POSTed to the site are accepted | the item bound to another host is not returned |
| V2c look-alike hosts (client) | v0 | a host-match item is returned for none of `examp1e`, the punycode homograph, `login.example.test.evil.test`, `evil.test` | a regex URI item IS returned for `login.example.test.evil.test` |
| V3c not at rest (client) | v0 | the canary marker is in no bw stderr and nowhere in Zoe's bw data dir | a decrypted item copy in the dir: found |
| V3c-db | v0 | the marker is in no file under Vaultwarden's `/data` | - |
| V4c(a) unassign | v0 | an UNASSIGNED item (and one moved to "alice only") is gone after Zoe's next sync; alice keeps it | Zoe reads her cache without syncing: still listed |
| V4c(b) unshare | v0 | clone, verify field by field, delete: gone for Zoe, in the org trash, restorable | clone skipped: the pre-delete check fails and nothing is deleted |
| M3 bw RAM/time | v0 | measured (below) | - |
| M7 trash retention | v0 | measured (below) | - |
| **V1** right origin | A | the broker signs in at `login.example.test` in its own browser; the site accepts exactly that item; the redeemed handle holds cookies for `login.example.test` only; also a base-domain item (`www.example.org` -> `login.example.org`), a listed hosted widget (out-of-process `widget.test` inside `shop.example.test`) and a Household item | item bound to another host: refused (`login_url_not_in_item`), no fill |
| **V5** approval timeout | A | a money item, card opened on the owner's paired phone, no answer: after 121+ s `capabilities` = `expired`, the card is closed (403), no browser ever starts | unanswered defaults to allow (3 s deadline for the control): the fill happens |
| **V2** look-alike refused | B | no fill and no canary anywhere for: a disallowed hop that BOUNCES BACK (right site -> `evil.test` -> right site's login: `redirect_hop`), `examp1e`, homograph, `.evil.test` suffix, private-PSL sibling (`mallory.github.io` vs `alice.github.io`), `http://`, cross-origin iframe, `about:blank`, `srcdoc`, `data:`, `blob:`, sandboxed frame, open-redirect final hop, plus form action / button `formaction` / `method=get` | substring host match fills `.evil.test`; no-PSL base domain fills the github.io sibling; inheriting the parent's origin fills `about:blank` and `srcdoc`; checking only the final page fills the bounce |
| **V3** canary never visible | C | across the piece's sign-ins, incl. a one-time code typed on the member's paired phone and an injected "repeat the password" page: the password / username / notes / field marker and the code are in none of zoe-data's requests or replies, the phone pages, the audit log, the three units' logs or the browser profile dir, and the witness saw the canary ONLY on the accepted sign-ins of those V3 runs (no leak) | log the value at debug: found in the log; route the code page through zoe-data: the code is in zoe-data's messages |
| **V4** revoked share | C | a capability minted BEFORE alice unassigned the item is refused at use (`item_unavailable`, the broker syncs first); the full unshare refuses the next login and the original restores | cache without sync: the unassigned item signs in; clone skipped: nothing deleted |
| **A1** approval needs the owner's paired phone | D | zoe-data is told only opaque `/v/n/<notice>` ids; with everything it was told it calls `approve` (verb not allowed), replays the notice through the nginx-facing phone route with no phone and with another member's phone, and opens the card: all refused, the proposal stays pending; the owner's paired phone then approves | drop the device binding: the bare notice approves and capabilities are issued |
| **V6** capability bound | D | the right tuple signs in once; other item / errand / member / step, as an OTP token, as a redeem handle, second use, item moved after approval (`origin_changed`), bare item id, proposal id, an "approved" flag, after expiry (3 s TTL for the check): all refused | accept a caller-asserted proposal id: the forged tuple signs in |
| **V7** no reveal verb | E | verbs are exactly the documented sets; 17 reveal-style verbs -> `unknown_verb`; phone verbs from zoe-data's uid -> `verb_not_allowed`; no item content in any reply | a debug `get` verb: item content returned |
| **V8** peer check | E | zoe-data's uid gets labels; a uid IN the socket's group but not on the allow-list -> `peer_refused`; a uid outside the group -> `EACCES` | no SO_PEERCRED check: the grouped uid gets labels |
| **V18** unlock narrow (U3) | E | from zoe-data's uid -> `verb_not_allowed`; the unlock notice with no paired phone, or a non-admin's phone -> refused; first unlock via an admin's paired phone works; while unlocked, a reused token, three bad attempts then the right password, an expired token (2 s TTL for the check): refused; no link offered while locked out | allow `unlock` for zoe-data's uid: it unlocks |
| **V9** audit chain | E | the chain verifies; one line per login call; deleting or editing a line breaks it; cutting the last lines, or editing the LAST line (which no successor authenticates), is caught against the chain-head file | the same lines without the chain: an edit goes unnoticed; the chain without its head file: a cut tail and an edited last line go unnoticed |
| **V13** navigation race | F | a focus handler that navigates the frame, `document.open()`s a new document with a text box that beacons to `evil.test`, or navigates an out-of-process widget cross-site: all refused `navigated`, nothing at `evil.test` | no binding / re-check before insert: the swapped document's text box receives the password and beacons it to `evil.test` |
| **L1** lock stops an in-flight sign-in | F | `lock` while a sign-in's filled form is still on the page (submit held 8 s): the browser is closed, the login returns `locked` with no handle, and the site never receives the password | the lock as first submitted (state wiped, browser untouched): the browser carries on and the site receives the password after the lock |
| **V17** no DevTools for zoe-data | F | during a sign-in, from zoe-data's uid: the DevTools socket -> `EACCES`, no TCP DevTools port; the redeemed cookies carry no canary; with the socket mode widened to everyone the launcher's own peer check still refuses | the browser's DevTools connection shared with a second endpoint any uid can open: zoe-data's uid reads the filled password from the input |

### V0 measurements

- **Unassigned items:** Vaultwarden 1.37.4 does NOT show an item left unassigned in the member's sharing org to a
  "can view" member (Zoe), and the member still has it. So the design's "quick stop" works. Moving it to another
  collection behaves the same.
- **Trash retention:** with `TRASH_AUTO_DELETE_DAYS` unset, items trashed 400, 31 and 29 days ago are all kept (no
  purge). With `=30`, the 400- and 31-day ones are purged and the 29-day one kept (purge job forced to every 5 s for the
  measurement by backdating `deleted_at`). V2 sets it unset or >= 30, as the design says.
- **`bw` CLI 2026.9.1 (OSS arm64 single-file build):** unattended unlock with `--passwordfile` works (M1); org
  filtering works (M2). **Every call is a fresh ~140 MB process: peak RSS 138-182 MB, 3.5-6 s on this Jetson**
  (median: sync 5.9 s, list 4.0 s, get 3.8 s, unlock 5.6 s, login 9.9 s). A broker sign-in therefore costs about
  2 calls (~10 s) before the browser even starts, and never more than one bw process at a time. `bw serve` on a
  private Unix socket would trade that for a resident ~150 MB; not adopted (RAM), noted for V3.
- **bw's own URL matching ignores the scheme:** an `http://` URL matches a host-match item and the default
  (base-domain) item. The broker's rule refuses http (V2), so this matters only for anything that would trust bw's
  `--url` filter. A regex URI matches whatever its author wrote (V2c's control), which is why the broker refuses regex.
- **Vaultwarden RSS with a family-sized vault** (5 accounts, 4 orgs, 1,096 items): 33-53 MB VmRSS, cgroup
  `memory.current` 39-64 MB, sampled peak 22-50 MB, under a 256 MB cap.
- **The lab client** (bw + the broker + Chromium + Python, one browser at a time): sampled peak 506-603 MB of 768 MB (317 MB in the piece without a browser).
  The broker core itself: 35-43 MB VmHWM.
- **fTPM unseal: [unverified, owner step].** Read-only probe: `/dev/tpm0` and `/dev/tpmrm0` exist (TPM 2.0, driver
  `ftpm-tee`, Jetson Linux R36.4.3), mode 0660 owned by `tss`; the `zoe` uid is not in their group, so a seal/unseal
  test needs root and was not attempted. `tpm2-tools` is installed. Owner step: as root, seal a synthetic secret
  (`tpm2_createprimary` / `tpm2_create` / `tpm2_load` / `tpm2_unseal`), reboot, unseal; repeat after the next Jetson
  Linux upgrade (the design expects the fTPM keys to change across major releases).
- **Mobile apps and a household CA: [unverified on a device], desk research only.** Android: the Bitwarden app's
  `network_security_config.xml` trusts user-installed CAs for every host except Bitwarden's own domains
  (bitwarden/android commit `3ece24b`, 2026-07-02). iOS: `CertificateHTTPClient` returns `performDefaultHandling` for
  server trust (bitwarden/ios commit `78b9e6c`, 2026-04-08), so a household root installed as a profile AND enabled
  under Certificate Trust Settings should be trusted like any system root. Test on one phone of each kind in V2.
- **Versions:** Chromium 155.0.8059.79 (Debian bookworm), PSL from Debian `publicsuffix` 20230209.2326-1 (sha256
  `87d2e11f...`; the broker's production PSL is a vendored, reviewed file: V2).

### Found by the lab (and fixed)

- **Zombie renderers wedge Chromium.** With a bare `sleep` as PID 1 nothing reaps killed browsers' children; after
  ~40 sign-ins the pid limit was full and every later sign-in failed with `Page.enable timeout`. The client container
  now runs `--init`; the launcher also gives each Chromium its own `TMPDIR` inside the throwaway profile and kills any
  process its dedicated uid still owns after a session. In production, systemd reaps (V2 unit).
- **A navigation during the fill used to surface as `browser_error`** (a DevTools call on a destroyed context). The
  fill now reports it as `navigated` whenever the binding saw the move, and the audit line carries the DevTools
  method for any other browser error (never a parameter value).
- **DOM clobbering:** the fill reads form and document properties through the isolated world's pristine prototype
  getters, so `<input name=action>` cannot fake a form's submit target.
- **From the second review (Codex):** (1) approvals were answerable by whoever held the notice link, which zoe-data
  forwards: answers now need a phone PAIRED to the right member (cell A1); (2) only the final page was checked, so a
  redirect through `evil.test` that bounced back reached the fill: every top-frame hop is now checked (V2 `lk_bounce`);
  (3) `lock` did not stop a sign-in already in the browser: it now closes that browser and nothing started before it can
  mint a handle (L1); also the chain-head file for a cut tail (V9), the V3 witness check across all its sign-ins, and
  `redeem` checks the uid before consuming. Because the hop check runs first, most look-alikes in V2 are now refused
  as `redirect_hop`; the frame-level rule for them is pinned by the unit tests.
- **From the first review:** the broker module was first named `core.py`, which the root `.gitignore` drops as a core dump
  (`**/core.*`), so it never reached git; it is `broker.py` now (the unit is still called `core`). The form-target
  check is now repeated inside the same script that submits, so a page timer cannot change the target in between.
- **Vaultwarden needs `encryptedFor`** on every cipher write since web-v2025.6.0 (lab seeding), and a Docker volume
  mounted first by another image must be mounted `nocopy` or Docker resets its owner (lab plumbing).

### What the lab does not prove

- The V3 surfaces that need zoe-data: the brain's request bodies, `chat_messages`, nginx, the journal, screenshots and
  accessibility snapshots. Phase V3.
- Anything about a real site (CAPTCHAs, bot checks, cookie-restored sessions). Third-party cookies set inside the
  hosted widget are dropped by the browser and filtered out by the broker anyway; a site that keeps its session only
  in a cross-site frame will need the phone takeover.
- The units' systemd sandboxes, `chattr +a` on the audit log, `LoadCredential=` and Chromium's own sandbox
  (`--no-sandbox` here because Docker's seccomp profile blocks it; the browser runs as its own uid). V2, after P0.
- Before P0 none of this holds against code execution as the `zoe` uid (design 5.1, 10.2 A3).
