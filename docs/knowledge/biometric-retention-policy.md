---
type: Policy
title: Biometric Retention & Deletion Policy
description: What Zoe stores for voice and face identification, what she never stores, how long she keeps it, how a household member deletes their own profile, and who can see or delete what. Required by the W5 gate before anyone enrols.
tags: [biometrics, privacy, retention, consent, speaker-id, face-id, governance]
timestamp: 2026-07-22T00:00:00Z
---

# Biometric Retention & Deletion Policy

The policy of record for Zoe's two biometric surfaces: **speaker identification**
(voiceprints, `speaker_profiles`) and **face identification** (faceprints,
`face_profiles`). Required by the W5 Gates/DoD in
[samantha-evolution-plan.md](../architecture/samantha-evolution-plan.md) **before
anyone enrols**. Every statement below was verified against the code on 2026-07-22.

This is an OKF **record**, not a DOX contract: it describes what the system does and
the retention decision that was taken. Enforcement lives in the code
(`services/zoe-data/biometric_scope.py`, pinned by
`tests/test_biometric_ownership_scope.py`) and the binding rule lives in
`services/zoe-data/AGENTS.md`. Where this document and the code disagree, the code
is right and this document is a bug — fix it here.

Scope note: this covers *enrolled identity biometrics*. Ambient-capture retention
(W6) is a separate question and is not settled here.

## 1. What is stored

Per modality, one row per profile in Postgres (the zoe-data DB):

| | Speaker (`speaker_profiles`, migration 0001 + 0023) | Face (`face_profiles`, migration 0024) |
|---|---|---|
| Biometric payload | `embedding_blob` — one resemblyzer 256-dim float32 vector, weight-averaged across enrol samples | `embedding_blob` — a buffalo_sc / MobileFaceNet-class ArcFace vector (`dim` ∈ {128, 256, 512}) |
| Rows per person | exactly one (re-enrolling averages into it) | up to 8 (pose variety; oldest dropped past the cap) |
| Metadata | `user_id`, `display_name`, `panel_id`, `sample_count`, `enrolled_at`, `consent_at` | `user_id`, `display_name`, `panel_id`, `model_name`, `dim`, `created_at`, `consent_at`, `active` |

**What is NOT stored, anywhere, ever:**

- **No raw audio.** `/api/voice/enroll` receives a WAV, writes it to a temp file
  only long enough to compute the embedding, and unlinks it in a `finally` block.
  Nothing durable.
- **No raw frames, and no images of any kind.** The Jetson runs no vision model.
  Panels detect, liveness-check, and embed faces locally; only the resulting
  vector is POSTed to `/api/face/enroll`. A frame never reaches this service.
- **No reversible original.** An embedding is a lossy one-way projection — it is
  not a recording and cannot be played back or rendered as a face.

A second copy of the *embeddings only* lives on each panel, so matching can happen
on-device: `~/.zoe-voice/speaker_profiles.json` and `~/.zoe-voice/face_profiles.json`
on the Pi, written `0600` inside a `0700` directory. See §4 for what that means for
deletion.

## 2. Retention rule: kept until deleted

**A profile is retained until its owner deletes it, or until the `users` row it
belongs to is deleted. There is no time-based expiry.**

Why not auto-expiry: a voiceprint or faceprint is not activity data, it is an
enrolled *identity*. A family member who is correctly recognised for six months and
then silently stops being recognised because a timer fired has suffered a
regression, not a privacy win — and the only remedy is to re-enrol, which means
capturing *more* biometrics, more often. Auto-expiry would degrade recognition and
increase collection at the same time. The privacy control that actually matters
here is deletion-on-demand plus consent (§3, §4), not a clock.

Two automatic deletions do exist, and both are correct:

- **User deletion cascades.** Both tables declare
  `FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE`, so removing a
  household member removes their biometrics with them.
- **The face gallery is capped at 8 rows per person.** Enrolling a ninth pose drops
  the oldest. This is a cap, not a retention clock: the person stays enrolled.

## 3. Consent

Consent is captured at enrolment and is a stored timestamp, not an assumption.

- **Face: consent is mandatory and structural.** `face_profiles.consent_at` is
  `NOT NULL`, and `/api/face/enroll` returns **400** without an explicit
  `consent: true`. There is no unconsented face state to leak — a faceprint either
  consented or does not exist.
- **Speaker: consent is a tri-state stamp, and it is revocable.**
  `speaker_profiles.consent_at` is nullable. On `/api/voice/enroll`,
  `consent: true` stamps it, `consent: false` **revokes** it (`SET NULL`), and
  omitting the field leaves it untouched. Rows enrolled before the consent gate
  existed stay inert until re-consented.

**Matching excludes non-consenting rows at the source.** `GET /api/voice/profiles/sync`
— the only feed that hands embeddings to a panel — filters
`WHERE consent_at IS NOT NULL` in the SQL, and `/api/voice/identify` applies the
same filter. So revoking consent removes a person from the match pool without
deleting anything; the face feed needs no such filter because every stored row is
consented by construction. Both `/sync` endpoints are **device-token only** and
403 a browser session: embeddings never reach a browser.

## 4. Deleting your profile

**Server side (implemented and tested).**
`DELETE /api/voice/profiles/{id}` and `DELETE /api/face/profiles/{id}` hard-`DELETE`
the row — no soft-delete, no tombstone, no archive copy. The embedding is gone from
the database. Authorisation is per §5.

**UI (voice only).** The desktop **Settings → Voice Identity** section
(`services/zoe-ui/dist/settings.html`) lists the signed-in member's voice profiles
and offers a per-profile Delete button, which calls the endpoint above.

**Panel copies lag by up to one hour.** Each Pi daemon keeps the synced embeddings
in memory and on disk, refreshing from `/profiles/sync` on a TTL of **3600 s**
(`SPEAKER_ID_SYNC_TTL_S`, `FACE_ID_SYNC_TTL_S`). A deleted profile can therefore
still be matched on-panel until the next sync. Restarting the panel daemon after a
deletion clears it immediately.

**Derived state is NOT retroactively unwound.** Deleting a profile stops *future*
recognition; it does not rewrite history. Turns already attributed to that person
keep their `identified_user_id`, and downstream rows keyed on `user_id` — memories,
the `music_play_history` journal, contacts — are untouched. Those are ordinary
account data governed by account deletion, not by this policy. Only the biometric
vector is removed.

## 5. Who can see and delete what

This table matches `services/zoe-data/biometric_scope.py` exactly.

| Caller | List profiles | Delete a profile | Enrol |
|---|---|---|---|
| **Owner** (signed-in member) | their own rows only (filtered in SQL) | their own rows | themselves |
| **Admin** (`admin` / `family-admin`) | household-wide | any row | on another member's behalf |
| **Other household member** | cannot see others' rows | **403** — and nothing is deleted | cannot enrol under another's id |
| **Device token** (panel) | **403** | **403** | on behalf of the person at the panel |
| **Guest / unauthenticated** | 401 | 401 | 401 |

Notes:

- A device token is a *shared panel credential*, not a person. It owns nothing, so
  it can neither enumerate nor delete profiles. Its legitimate access is the
  embedding feed `/profiles/sync`, which is device-token-only.
- Admin power is **explicit and fail-closed** (`auth.is_admin_role`), never implied
  by reaching the endpoint. It honours the `family-admin` alias; anything else —
  including a non-string role — is not an admin.
- An unknown profile id returns **404**; a real id owned by someone else returns
  **403**. That ordering matches `routers/proactive.py` and `routers/lists.py`.

## 6. Live status

Both surfaces are **merged but dark** — nobody is enrolled:

- Face: the whole server surface is gated by `ZOE_FACE_ID_ENABLED` (default off;
  every endpoint 503s until the operator flips it).
- Speaker: the server endpoints are always mounted, but the panel daemon only uses
  them when the Pi-side `SPEAKER_ID_ENABLED` is on (default off).

## 7. Remaining work (not yet true)

Named honestly so nobody reads a capability into this document that does not exist:

- **There is no face-profile UI at all.** No page enrols, lists, or deletes a
  faceprint; the only route today is a direct API call. Face enrolment must not go
  live for the household until self-service face deletion exists in the UI — the
  right to delete has to be reachable by the person it belongs to, not just by an
  API client.
- **The touch panel has no biometric-identity UI**, voice included. Voice deletion
  is desktop-Settings-only, which conflicts with the standing rule that physical
  enrolment flows are driven from the panel.
- **Deletion does not push to panels.** It waits for the ≤1 h sync TTL. An
  invalidation signal (or a shorter TTL) would close the window.
- **No revocation-without-deletion for faces.** Speaker profiles can be
  consent-revoked (§3); the face surface offers only delete. Acceptable while every
  face row is consent-mandatory, but worth revisiting if face consent ever becomes
  tri-state.

## Related

- [Runtime topology](runtime-topology.md) — where zoe-data and the panel run.
- [Voice pipeline](voice-pipeline.md) — the STT → brain → TTS path the speaker
  surface hangs off.
- [samantha-evolution-plan.md](../architecture/samantha-evolution-plan.md) — the W5
  gate this policy satisfies.
- [panel-identity-plan.md](../architecture/panel-identity-plan.md) — how panel
  identity was built.
