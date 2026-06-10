# Zoe Capability Profiles

## Purpose

Capability profiles are Zoe's first executable self-model. They describe what Zoe can do, where the capability lives, how trusted it is, what approvals it needs, which offline/runtime dependencies it has, and what evidence keeps it honest.

This is the foundation for governed self-evolution. Zoe should look up existing capabilities before proposing a new tool, and cleanup should not remove or replace a capability without profile evidence.

## Harness

Files:

- `services/zoe-data/zoe_capability_profile.py`
- `services/zoe-data/zoe_capability_profile_patch_writer.py`
- `services/zoe-data/zoe_capability_profile_promotion.py`
- `services/zoe-data/zoe_capability_profile_promotion_handoff.py`
- `services/zoe-data/zoe_capability_outcome_profile_handoff.py`
- `services/zoe-data/zoe_capability_profile_ticket_gate.py`
- `services/zoe-data/zoe_capability_profile_ticket_writer.py`
- `services/zoe-data/zoe_capability_profile_pr_edit_gate.py`
- `services/zoe-data/zoe_capability_trust_update.py`
- `services/zoe-data/zoe_capability_trust_review.py`
- `services/zoe-data/tests/test_zoe_capability_profile.py`
- `services/zoe-data/tests/test_zoe_capability_profile_patch_writer.py`
- `services/zoe-data/tests/test_zoe_capability_profile_promotion.py`
- `services/zoe-data/tests/test_zoe_capability_profile_promotion_handoff.py`
- `services/zoe-data/tests/test_zoe_capability_outcome_profile_handoff.py`
- `services/zoe-data/tests/test_zoe_capability_profile_ticket_gate.py`
- `services/zoe-data/tests/test_zoe_capability_profile_ticket_writer.py`
- `services/zoe-data/tests/test_zoe_capability_profile_pr_edit_gate.py`
- `services/zoe-data/tests/test_zoe_capability_trust_update.py`
- `services/zoe-data/tests/test_zoe_capability_trust_review.py`

Initial covered profiles:

- production chat router;
- MemPalace memory baseline;
- Hindsight reflective memory candidate;
- Graphiti relational memory candidate;
- Graphify code/system map;
- Multica governance;
- Hermes escalation;
- OpenClaw fallback;
- Pi external runtime candidate with read-only runtime probe;
- Home Assistant control.

## Trust Rules

Trusted and privileged profiles must include:

- evidence references;
- tests or live checks;
- rollback path.

Privileged profiles must also include explicit approval requirements.

Experimental profiles can exist before full proof, but they must not be promoted to trusted without measurements and review.

Capability trust updates are review candidates, not automatic profile writes.
`zoe_capability_trust_update.py` can propose trust updates only after a
verified self-evolution outcome was admitted and retained through Hindsight.
Pending, blocked, failed, or unretained outcomes produce blockers instead of
profile mutations.

Capability trust reviews are governed, in-memory application plans.
`zoe_capability_trust_review.py` can accept or reject proposed trust updates
for existing capability profiles only when reviewer identity, approval refs,
matching current trust level, and evidence are present. It returns updated
profile objects for a later writer; it does not write profile files or mutate
production runtime state.

Capability profile promotion plans are explicit writer gates, not writes.
`zoe_capability_profile_promotion.py` can render a deterministic promotion
manifest only from a clean trust review with PR refs, rollback refs, and
verification refs. Blocked plans carry no records and cannot be rendered.

Capability profile patch plans are explicit source patch gates, not direct
file mutations. `zoe_capability_profile_patch_writer.py` consumes an applyable
promotion manifest plus current source text and renders a deterministic unified
diff only when the source profile exists and its current trust level still
matches the reviewed promotion.

Capability profile promotion handoff plans close the pure runtime handoff loop.
`zoe_capability_profile_promotion_handoff.py` consumes a reviewed trust result,
PR/rollback/verification refs, and current source text, then builds the
promotion manifest, patch plan, and inert Multica handoff packet together. It
does not create tickets, write files, or mutate profiles.

Capability outcome profile handoff plans compose the retained-outcome path.
`zoe_capability_outcome_profile_handoff.py` starts with an admitted and retained
evolution outcome, builds trust-update candidates, applies an explicit trust
review in memory, then hands the reviewed profile promotion into the same
manifest, patch, and inert Multica handoff gates. Explicit reviewer, approval,
PR, rollback, and verification refs are required before any handoff payload can
be created.

Capability profile ticket gates are the final pure guard before any future
Multica writer. `zoe_capability_profile_ticket_gate.py` accepts only a createable
profile handoff plus explicit operator approval refs, verifies the promotion
manifest and patch hashes against ticket metadata, and returns a ticket payload
without submitting it.

Capability profile ticket writers are the only approved Multica creation path
for profile handoffs. `zoe_capability_profile_ticket_writer.py` evaluates the
gate internally, refuses blocked handoffs without calling Multica, and creates a
backlog ticket containing the gate metadata, promotion manifest, and profile
patch only after explicit operator approval. It still does not apply profile
edits; those remain PR-backed.

Capability profile PR edit gates connect created profile tickets to reviewable
profile edits without mutating files. `zoe_capability_profile_pr_edit_gate.py`
accepts only ticket-writer metadata, matching current source and patch hashes,
and explicit PR, rollback, verification, and Greptile refs before rendering the
patch text for a profile-edit PR. Blocked plans carry no patch text.

## Next Use

The next self-evolution slices should use these profiles to:

- generate a current capability inventory view;
- score candidate Pi/MCP/GitHub/skill/API adoption;
- run `scripts/maintenance/pi_runtime_probe.py --json` before any Pi install or delegated execution proposal;
- block privileged execution when profile approval rules are unmet;
- add outcome eval traces against profile IDs;
- keep retained-outcome profile promotions routed through the composed
  outcome-to-handoff plan, ticket gate, profile ticket writer, and PR edit gate
  before any profile edit PR is prepared;
- require non-use, replacement, or failure evidence before retirement.
