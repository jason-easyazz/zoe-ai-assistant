# First boot: from a blank unit to a working household

Status: **design + prototype, unwired.** The state machine (`services/zoe-data/first_boot.py`), the probe that reads it from
the existing tables (`first_boot_probe.py`), the flow script as data (`lexicons_data/first_boot.json` + `first_boot_en.json`,
checked by `first_boot_script.py`) and a simulator (`labs/first-boot/`). Nothing calls any of it; no flag, no route, no panel
screen, no daemon change. ROADMAP.md decides when any of it is built (Next 8, "a sold unit").

## The bar

A person who buys a Zoe never types a command (owner rule, 2026-10-12). In about ten minutes after power-on the household has:
an **owner**, the owner's **verified voice**, the **panel named and placed in a room**, and **one useful thing done** (a
reminder or a list). Zoe knows what is still missing and says so honestly, any day, but only inside a turn or as a panel card,
never unprompted (VISION principle 10). Physical steps are spoken from the panel; anything that needs typing or a sign-in is a
QR on the panel finished on the phone (principle 8). A sold unit may have no GitHub, and household data stays local.

## The checklist (`first_boot.ITEMS`, in order)

Each item's state is READ from the system that already holds it, never copied (`first_boot_probe`; the same rule as
`enrollment_state.get_state`). A failed read is `unknown`, never "no": Zoe does not offer to set up what she may already have.

| # | item | tier | read from | backed today |
|---|---|---|---|---|
| 1 | owner account | blocking | `auth_users`: active admin whose `password_hash` is set | **missing**: needs the bootstrap token from the service journal |
| 2 | forget-ledger secret | blocking | `memory_forgotten.configured()` (`ZOE_FORGET_LEDGER_SALT`) | **missing**: no installer or runtime step makes it |
| 3 | panel paired | blocking | `panels` + an unrevoked `device_tokens` row | exists (`panel_provision`) |
| 4 | panel named and placed | blocking | `panels.name` not the default; `panels.room_id` resolves in `rooms` | partial: the name is set only at pairing |
| 5 | time and place | blocking | `ZOE_TIMEZONE` explicitly set and valid; `household_locale.country()` | **missing**: env-only, no writer |
| 6 | owner PIN | blocking | `passcodes` (zoe-auth) | partial: no panel screen for a FIRST PIN |
| 7 | owner voice | blocking | `enrollment_state.get_state(owner).voice_enrolled` | exists, behind `ZOE_ENROLL_OFFER` (default off) |
| 8 | first useful thing | deferrable | `reminders` / `lists` for the owner | exists |
| 9 | a phone linked | deferrable | `push_subscriptions`, `user_preferences.prefs.telegram_id`, `phone_devices` | partial: three channels, no one QR |
| 10 | backup target | deferrable | `offbox.env` in `ZOE_OFFBOX_CONFIG_DIR` (`zoe_offbox.py`) | **missing** for a buyer |
| 11 | more members | optional | other active `auth_users` | partial: per-user setup token, no invite QR |
| 12 | a child marked as a child | optional | child-role accounts have `member_modes.minor = 1` (n/a with no child) | partial: no explicit guardian link |
| 13 | calendar | optional | `connected_accounts` kind `calendar`, `active` | partial |
| 14 | music | optional | `connected_accounts` kind `music`, `active` | exists (`music_setup` QR) |

States: `done`, `missing` (offerable now), `waiting` (an earlier item it needs is not done), `deferred` (the owner said later or
no, within the quiet period), `unknown`, `n/a`. `next_step` offers the first missing blocking item, then the first missing
deferrable one; a deferral is skipped, so one "no" never stalls the flow, but a deferred BLOCKING item keeps `ready` false and
comes back after the quiet period. `nudge(facts, surface)` is the any-day answer for a moment the caller already has:
`turn_close` and `panel_card` never name a deferred item, `asked` ("what's left to set up?") does.

"Later" is remembered in the existing `enrollment_offers` table (migration 0046) as `modality = 'setup:<item>'`, with
`declined_until`: no new table, no migration. Every existing reader of that table filters on `modality = 'voice'`.

## The flow script

`lexicons_data/first_boot.json` is the structure, no words: per step its mode (`voice`, `physical`, `pinpad`, `qr`, `system`),
the declared time budget, the QR hand-off it needs, the honest state of the mechanism behind it today (`backed_by`), and a
transition for every person behaviour (yes, done, later, no, silence, unclear, fail, a child answering, walking away, resume).
`first_boot_en.json` is the words: what she says, what the panel shows, the "later" line, a shared pause / help / ask-a-grown-up
set. A new language is one more `first_boot_<lang>.json` (the `voice_enrollment_<lang>.json` pattern).

The checker (`first_boot_script.problems`) fails on a missing behaviour, an unknown transition, a "later" on a step everything
else waits on (owner account, pairing: those explain why they come first and keep the QR up), missing words, a spoken line over
160 characters, and a core flow declared over the ten minutes the hello line promises. Repeats are bounded (two retries, then a
pause card); a pause resumes from the system's state, so a power loss loses only the step in progress.

A child answering cannot be DETECTED on a blank unit (no voices are enrolled yet) [inferred]. The guard is structural: every
step that matters finishes on the owner's phone or with the owner's PIN, and the script maps "a child answered" to "I need a
grown-up for this part" on each of them.

## The lab numbers

[lab] `labs/first-boot/sim.py`, nine scripted people, both modes. Times are DECLARED budgets (the script's per-step numbers plus
15 s per extra turn), not measurements: no person and no panel were timed.

**Not run yet** when this was committed: the shared box stayed under the memory floor a lab run needs. Run
`python3 labs/first-boot/sim.py` and `--today` (under `flock /tmp/zoe-precheck.lock`). What is already proven [lab, pytest]:
the script has 0 dead ends (`first_boot_script.dead_ends`) and the core flow fits its declared 600 s (`core_budget_s`).
Counted from the script by hand [code]: the core flow is declared at 570 s, and 11 steps can send the person to the help
card on a failure. With `--today`, a blank unit is expected to stop at step 1 (owner account, no mechanism) [inferred].

## What exists today vs what a sold unit needs (blunt)

Ranked by how badly each blocks a buyer.

1. **Nobody can become the owner without a shell.** The first password needs the zoe-auth bootstrap token, which is written to
   the service journal for "a local operator" to read (`services/zoe-auth/core/account_setup.py`). A buyer stops at step 1.
   Needed: a "claim this Zoe" QR, shown by the panel only while no admin exists, finished on the phone (physical presence at the
   panel is the authority, as the bootstrap token's log line is today). It can also confirm the panel's pairing in the same scan.
2. **The pairing link points at one fixed LAN address.** `routers/panel_provision.py` builds the pairing URL from `ZOE_BASE_URL`,
   whose code default is a single private address; no installer or env template sets it. A buyer's phone scans a link to a
   network that is not theirs. The phone also has to trust the box's private CA (the phone app plans a CA fingerprint in its
   pairing payload; the PWA has nothing).
3. **Household settings live only in `.env`.** Time zone, country, the forget secret, the base URL and every feature flag are
   environment variables read at start-up or per call; nothing a person can do from the panel or the phone changes them, and the
   unit falls back to one hard-coded zone. Needed: a small household-settings store the services read, written by the flow.
4. **Voice enrolment by voice is off on a stock unit.** `ZOE_ENROLL_OFFER` defaults to `off`, and even "learn my voice" needs it
   (`enrollment_state.py`). It also needs a fresh PIN sign-in at the panel, and there is no panel screen to set a FIRST PIN.
5. **Forgets are not permanent on a fresh install.** `install-jetson.sh` generates the database and service secrets but not
   `ZOE_FORGET_LEDGER_SALT`; until it exists only the 300 s tombstone shields a forgotten name. It must also be in the backup:
   rotating it orphans every stored hash.
6. **Nothing starts first boot.** There is no first-boot screen, no "setup" card and no spoken opener; a blank unit shows the
   estate with no owner.
7. **No buyer-facing backup.** `zoe_offbox.py` needs a hand-written `offbox.env`, a restic server and an age key held in the
   owner's vault; the reference target is the developer's own server.
8. **Linking a phone is three partial paths** (web push with no subscriptions, a Telegram deep link, Zoe's own app flag-dark),
   none of them one QR.
9. **The panel name is set only at pairing** (`panel_config._WRITABLE` has no `name`), so "call this the kitchen screen" later has
   no writer.
10. **The stage-2 router model cannot be installed.** `download_gguf_models.sh --with-router` fetches the BASE FunctionGemma into
    a directory nothing reads; the fine-tuned checkpoint the unit serves exists only on the reference box [code + inferred].
11. **Children.** There is no explicit guardian link (any admin acts as guardian), and a child account created with an adult
    role is treated as an adult until someone sets `member_modes.minor`.
12. **Stale setup scripts** (a retired-brain download, a SQLite initialiser) and `/home/zoe` paths in units and scripts. Pinned
    by `test_setup_canonical_drift.py` as a shrinking list.

## Wiring notes (not built here)

- Flag `ZOE_FIRST_BOOT` = `off` (default) | `shadow` (compute and log the summary, say nothing) | `on`. Register it when wired.
- `GET /api/setup/status`: `first_boot.summary(await first_boot_probe.gather(panel_id=...))`, counts and item ids only, for an
  admin session or the panel's own device token. `POST /api/setup/defer` -> `first_boot_probe.defer`.
- Voice: a deterministic tier for "what's left to set up?" (`nudge(facts, "asked")`) beside `session_status.py` in the
  `fast_tiers.resolve` chain; a `turn_close` nudge shares `voice_enrollment`'s conversation-end moment and its once-a-day gap.
  `routers/voice_tts.py` and the turn start are owned elsewhere: this is a note, not a change.
- Panel: the estate draws the `panel_card` nudge and the QR steps through the existing `setup_qr` single-use handles
  (`routers/handoff.py`). No touch screen is built in this task.
- Daemon: no edit. The voice step is the existing "learn my voice" path (`voice_enrollment.py` -> `zoe_voice_enroll_mode.py`);
  first-boot lines reach the panel as ordinary announcements.

## Build order (dependency order; ROADMAP.md says when)

1. Household-settings store (time zone, country, base URL, forget secret generated on first start and included in the backup).
2. "Claim this Zoe" owner QR, folded into panel pairing.
3. First PIN on the panel PIN pad; then `ZOE_ENROLL_OFFER` on for a fresh unit's owner.
4. `GET /api/setup/status` + the "what's left?" voice tier, flag-dark, in shadow on the reference box.
5. The first-boot panel card and spoken opener (touch work, its own task).
6. One "link my phone" QR; then a buyer-facing backup target.
7. Invite QR for members, the child/guardian link, panel rename.
