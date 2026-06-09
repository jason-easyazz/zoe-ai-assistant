# Zoe Capability Profiles

## Purpose

Capability profiles are Zoe's first executable self-model. They describe what Zoe can do, where the capability lives, how trusted it is, what approvals it needs, which offline/runtime dependencies it has, and what evidence keeps it honest.

This is the foundation for governed self-evolution. Zoe should look up existing capabilities before proposing a new tool, and cleanup should not remove or replace a capability without profile evidence.

## Harness

Files:

- `services/zoe-data/zoe_capability_profile.py`
- `services/zoe-data/zoe_capability_trust_update.py`
- `services/zoe-data/zoe_capability_trust_review.py`
- `services/zoe-data/tests/test_zoe_capability_profile.py`
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

## Next Use

The next self-evolution slices should use these profiles to:

- generate a current capability inventory view;
- score candidate Pi/MCP/GitHub/skill/API adoption;
- run `scripts/maintenance/pi_runtime_probe.py --json` before any Pi install or delegated execution proposal;
- block privileged execution when profile approval rules are unmet;
- add outcome eval traces against profile IDs;
- connect reviewed trust promotions to an explicit profile writer with PR and
  rollback evidence;
- require non-use, replacement, or failure evidence before retirement.
