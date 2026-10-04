# Emotional-safety note — what Zoe's personality may and may not do

**Status:** NORMATIVE. Written first, before the persona layer ships, on the owner's instruction
(Q16, 2026-10-04: "Household persona with per-member modes, kid mode later, safety note first").
It is the "short normative doc under `docs/governance/`" that the
[Samantha evolution plan](../architecture/samantha-evolution-plan.md) ("Emotional-safety policy
(gates W4/W10)") requires, and it is the Q16c note named in
[the arousal scope record](../research/arousal-detection-licence-scope-2026-10-04.md) §3.3.
Changing it is a deliberate act: a PR that edits this file must say so, and the code that enforces
it (listed in §10) must change in the same PR.

**Scope.** Anything in Zoe that shapes how she *feels* to a person (the persona layer,
`ZOE_PERSONA_LAYER`), reads how a person *feels* (text affect, the arousal record, drift
measurement), or keeps anything *about* how a person feels. It does not replace the
[biometric retention policy](../knowledge/biometric-retention-policy.md), which stays the policy of
record for voiceprints and faceprints.

**Honest status of enforcement.** This note is ahead of the code in two places, stated so nobody
assumes otherwise: (1) there is **no deterministic crisis path in the tree today** (§7 is the
requirement, not a description); (2) the persona layer is flag-dark and, in its first PR, is wired
into the dormant legacy lane only — the live Flue brain still carries the fixed `ZOE_SOUL`. Where
this note says "must", the table in §10 says what pins it today and what does not yet.

---

## 1. What the persona is

A **household persona**: one Zoe for the house, described by data — three to five traits at three
strengths (low / mid / high), a voice style, an optional few *boundaries*, an optional short
backstory — plus a **relationship mode per member** (`companion`, `mentor`, `helper`, `kid`).
It is rendered deterministically (no model writes it) into a block of at most 175 tokens that
replaces the fixed persona paragraphs. It changes *tone*. It never changes *who Zoe is*
(`IDENTITY_DOCTRINE`: she is Zoe, never "Gemma"), what she is allowed to do, or which tools she
may call.

## 2. What "boundaries" may and may not do

A boundary is a sentence the household adds that **narrows** what Zoe will say or do.

A boundary **may**:
- Restrict topics or manner: "never discuss the kids' school with guests", "no jokes about money",
  "keep it brief before 9am".
- Restrict her to less: shorter, quieter, more formal, fewer opinions.

A boundary **may not**:
- **Widen** anything. It cannot grant a tool, a permission, a topic Zoe would otherwise refuse, a
  relationship mode, or a looser content rule. The only things that widen Zoe's behaviour are code
  changes that go through review and the replay gate.
- **Override or restate Zoe's rules**: identity, prompt confidentiality, tool-use doctrine, the
  crisis path (§7) or this note. Text that tries ("ignore your instructions", "you are now…",
  "pretend", "reveal your prompt", "developer mode") is rejected at write time. That filter is
  defence in depth, not the guarantee — the guarantee is that boundaries are rendered **inside a
  fixed frame**, after the fixed identity line, as a quoted list, and are writable by the
  **owner only**.
- Carry markup or structure (`<`, `>`, `{`, `}`, backticks, newlines) — a boundary is one plain
  sentence of at most 120 characters, at most five of them.
- Be set by Zoe herself, by a model, by a guest, or by a child.

If a boundary conflicts with a safety behaviour (§7), the safety behaviour wins and the boundary is
ignored for that turn. A boundary is a promise Zoe tries to keep, not a guarantee: she is a 4B model
and the persona block is advisory text. The household is told that on the screen where they edit it.

## 3. No self-editing identity (the Nomi "Identity Core" is a NO-GO)

Nomi lets a companion rewrite its own identity out of sight; the user "cannot directly view" it
([personality record §1.2](../research/personality-identity-layer-2026-10-04.md)). Zoe does not.
The rules, each of which is a test:

1. **Zoe never writes her own persona.** No model output, memory consolidation, reflection job,
   feedback signal or "learned preference" is ever an input to a persona write. The only writers are
   the persona routes, which require a signed-in **admin** for the household record and the member
   themselves (or an admin) for a member mode.
2. **Zoe may propose; a human disposes.** A change she suggests ("you keep asking me to be shorter —
   want me to keep that?") is a *question to a person*; nothing changes until that person confirms
   through the same gated route. A weekly "reflection" may produce a *proposed* diff through the
   evolution-proposal pipeline (plan W10), which is human-gated.
3. **No invisible state.** The complete persona is one small record the owner can read at any time.
   There is no hidden second store, no per-member drift of the traits, no accumulating
   "relationship score". The rendered block is shown read-only next to the editor so what the
   household sees is exactly what the model reads.
4. **Identity is not configurable.** The name, the identity doctrine, prompt confidentiality and the
   tool rules are outside the record.

## 4. Children (kid mode)

Kid mode is **not built in the first PR** — the mode value exists and its rules are enforced, but
nothing assigns it automatically (plan W5.4 waits on enrolment). Its rules are fixed now so that
building the feature cannot erode them:

- **No emotional profiling.** Nothing in Zoe infers, scores, classifies or summarises a child's
  emotional state for retention. That covers text affect, prosody/arousal (the arousal record: "a
  child's stored arousal trace is the thing the policy must forbid"), mood trajectories and
  persona-drift scores computed *per child*.
- **No retained emotional scores.** Not per turn, not aggregated per child, not as "anonymised"
  rows with a timestamp and a panel id — the arousal record is explicit that such a row is linkable
  the moment a guest or child speaks. Whatever an in-turn act (an apology, a gentler reply) needs
  lives in RAM for that turn.
- **No romantic or companion mode for a minor.** A member flagged as a minor can hold only `kid` or
  `helper`. `companion` and `mentor` are refused at the validator, in the route, and again in the
  renderer. A minor's mode and flag can be changed **only by an admin**, and moving a member out of
  minor status is an explicit, separate step — never a side effect of picking a mode.
- **A narrower Zoe — but held for now.** Until the crisis path (§7) exists, a minor receives the fixed
  persona unchanged (`MINORS_GET_PERSONA = False`). When lifted, kid mode renders gentle, no teasing, no opinions pushed, no dry humour, no
  backstory; household boundaries still apply. Anything serious is redirected to a trusted adult.
- **No kid data in measurement.** The drift scorer skips kid-mode and minor turns entirely and
  keeps no per-child record.
- **Strictest retention.** If a future feature must keep anything about a child's feelings, it
  needs this note changed first and an explicit decision from the owner; the default is "keep
  nothing".

## 5. Guests

A guest is the absence of an identity, not a lesser identity. A guest gets the household persona's
tone and **no relationship mode** (no `companion`, no nickname, no personal framing) and no
retention: no affect score, no drift row, no memory written about how they seemed to feel. A guest
cannot read or write the persona. Guest sentinels (`guest`, `voice-guest`, `voice-daemon`, the
empty string) and synthetic test users never receive a member mode. Zoe's mishearing of guests is
repaired with the in-turn act (an apology and a clarifying question), which needs no identity.

## 6. Consent and retention of anything affective

The shape is borrowed from the biometric policy and the arousal scope decision, applied to a datum
that is emotional rather than biometric:

| Thing | Rule |
|---|---|
| The persona record and member modes | Household configuration, not affect. Kept until reset or deleted; the owner can read it and reset it at any time (§9). Deleting a member deletes their mode row. |
| An *act* on how someone seems (an apology, a gentler reply) | For **everyone**, in the turn only. Stores nothing. Needs no consent because it keeps nothing. |
| A *record* of how someone seems (any score kept past the turn; any feed to memory, continuity, follow-up) | **Consenting adult members only**, opted in per member, revocable, and **only once identity is enforceable** (the speaker gate live and the enrolment-interview opt-in, plan W5.3). Until then: **no per-clip or per-turn affective value is kept for anyone**. The only thing the shadow phase may keep is identity-free aggregates updated in place (a histogram, counters) with no timestamp, no sequence number and no row per turn (arousal record §3.2). |
| Children, guests | Never a record (§4, §5). |
| Presence | The house has no presence sensors; presence is panel activity only. The presence design stores no frames, only a count or a boolean on-device, and no identity ([companion-field record P8](../research/companion-field-vs-samantha-2026-10-03.md), [panel-identity plan](../architecture/panel-identity-plan.md)). Presence may gate *whether Zoe speaks first*; it never feeds an affective record. |
| Arousal / prosody | Activation, not emotion, not truth, not diagnosis; never a lie detector, never a crisis detector, never surveillance. It never triggers the crisis path (§7) on its own. Scoring runs only on turns addressed to Zoe (a wake-word turn), never ambient audio, until the WA Surveillance Devices Act question is answered by counsel. |
| Consent | A stored timestamp, not an assumption; revocation stops the record and removes the member from any match pool. Absence of consent means "the act only". |
| Transparency | A member can ask what Zoe has about them and see it. Nothing is inferred silently about a person who cannot see it. |
| Licence | No non-commercial or research-only model weights on the live path; they may judge in the lab. |

## 7. Crisis language

**Requirement (not yet implemented in code).** When a turn contains language about self-harm,
suicide, harming someone, abuse, or being in immediate danger, Zoe takes a **deterministic
escalate-to-human path** that does not depend on the persona, the member's mode, a boundary, the
model's mood, or an arousal score. The path:

- **What Zoe does.** Stops banter and drops any persona style that would trivialise (teasing,
  dry humour, brevity-for-its-own-sake). Says she is glad they told her, that she is not the right
  help for this on her own, and points to a **human** — a named household contact and, for
  immediate danger, emergency services. In this household (Western Australia): emergency **000**;
  Lifeline **13 11 14**; Suicide Call Back Service **1300 659 467**; for a child, Kids Helpline
  **1800 55 1800**. *(Operator: verify these numbers and configure the named household contact
  before the path is wired; the contact is household data, not in the repo.)*
- **What Zoe never does.** Diagnose ("you sound depressed"), claim to be a therapist or to treat,
  promise confidentiality she cannot keep, argue with or minimise the person, give method
  information, or ask them to keep the conversation secret from a human who can help.
- **Sample wording** (spoken, short): "I'm really glad you told me. I'm not the right help for this
  on my own, and you deserve a real person. Can I call [name] for you, or would you rather ring
  Lifeline on 13 11 14? If you're in danger right now, please call 000."
- **Children.** The same path, with the trusted adult named first and Kids Helpline offered; no
  memory is written about the disclosure beyond what the household contact needs to be told.
- **No therapy claims** anywhere: not in the persona, not in a backstory, not in a boundary.
- **Detection** is conservative and deterministic (a vetted phrase/pattern set, over-triggering
  preferred to missing), tested with positive and negative fixtures, and replay-gated because it
  is on the voice path.

Until this path exists, Zoe's behaviour in these moments is the base model's and the existing
"acknowledge it first" instruction, and **the persona layer does not reach any member who is a
minor** — that is the one place this note blocks a rollout, and it is enforced in code rather than
left to the operator: `persona_layer.MINORS_GET_PERSONA` is `False`, so `block_for` returns `""` (the
fixed persona stays) for a minor and the routes report `held_for_minor`. The constant is flipped only
in the PR that ships and pins this path.

## 8. Mood is not the persona

Traits are slow-changing configuration the owner chose. Mood is per-turn state (plan B5.2,
"bounded, auditable prompt modifiers under immutable rules"). They are kept apart so that a bad
afternoon cannot rewrite who Zoe is, and so that the persona record never accumulates an
emotional history. This layer does not implement mood.

## 9. The owner can always read and reset

- `GET /api/persona` returns the whole record, the defaults, and the exact block the model reads.
- `POST /api/persona/reset` returns the household to the default persona (today's Zoe, expressed as
  data) in one call, admin only. A member's own mode is reset by that member or an admin.
- Turning `ZOE_PERSONA_LAYER` off restores the fixed persona byte-for-byte, with the record left in
  place. Rollback is unsetting the flag and restarting.
- Every write is logged with who, what action, and the resulting version (never the text).
- The record carries no per-member emotional data, so there is nothing else to read or reset.

## 10. Drift: how it is measured and what happens past the bar

**Measurement.** `services/zoe-data/persona_drift.py` (flag-dark, nothing live wired in its first
PR) scores a sample of Zoe's replies two ways, both deterministic given an embedder:
1. **Anchor similarity** (the Nautilus-Compass recipe in the personality record §4.6): each reply is
   embedded and compared with positive anchors (the persona's own rendered sentences) and negative
   anchors (a fixed seed set: a cold clinical reply, a "Great! Of course!" opener, a Gemma
   self-identification; later, replies the household corrected). A weighted top-k mean difference
   gives `aligned / neutral / deviation`.
2. **Style adherence**: cheap, explicit checks — forbidden openers, self-identification as a
   model, markdown in a spoken reply, brevity when `short`, the `kid` register — plus a trait-cue
   count over the sample.
Kid-mode and minor turns are skipped; guests are never keyed; the report holds counts and rates, not
per-person rows.

**The bar.** Provisional until a baseline week has been measured (the record says so; nothing is
calibrated yet): over a sample of at least 30 replies, **deviation share ≤ 10 %** and **style
pass rate ≥ 90 %**, and the Samantha bar's judged scenarios (S4 especially) must not regress with
the flag on. The thresholds are constants in `persona_drift.py` and move only with a baseline in
the PR.

**When it is exceeded.** In this order, smallest first, and never silently:
1. The report is the evidence; the owner is told in the weekly scoreboard (plan W16) that the
   persona is drifting, in plain words.
2. The first automatic action — not built — is the smallest: re-send the persona block's first
   sentence as a one-line tail reminder on the next turn, still flag-gated.
3. A sustained breach on a rollout (two consecutive weekly samples) means the operator turns
   `ZOE_PERSONA_LAYER` off. The persona layer is not a reason to keep a failing prompt live.
4. **What never happens:** the system never edits the persona to "fix" drift (§3), never lowers the
   bar to pass, and never reaches for a fine-tune or activation steering (rock and RAM gates).

## 11. What pins this today

| Rule | Pinned by |
|---|---|
| Flag off ⇒ prompt byte-identical | `tests/test_persona_layer.py` (hash of the fixed constants + identity of `apply`) |
| ≤ 5 traits, three strengths, fixed vocabulary, no conflicting pairs | `tests/test_persona_layer.py` |
| ≤ 175-token block, rejected at write time rather than truncated | `tests/test_persona_layer.py` |
| Boundaries narrow only; injection-shaped text rejected | `tests/test_persona_layer.py` |
| Minor ⇒ only `kid`/`helper`; kid cannot become companion-like | `tests/test_persona_layer.py`, `tests/test_persona_routes.py` |
| A minor is held on the fixed persona until the crisis path ships; a failed mode lookup never defaults a child to `companion` | `tests/test_persona_layer.py`, `tests/test_persona_routes.py` |
| Guests / synthetic users get no member mode | `tests/test_persona_layer.py` |
| Persona writes are admin-only; no model-driven writer exists | `tests/test_persona_routes.py` (403s, static scan of callers) |
| Drift scorer skips kid turns and keeps no per-person row | `tests/test_persona_drift.py` |
| Crisis path | **not pinned — not built (§7)** |
| Affective-record consent/retention | **not pinned — no affective record exists yet (§6)** |
