# Zoe Capability Profiles

## Purpose

Capability profiles are Zoe's first executable self-model. They describe what Zoe can do, where the capability lives, how trusted it is, what approvals it needs, which offline/runtime dependencies it has, and what evidence keeps it honest.

This is the foundation for governed self-evolution. Zoe should look up existing capabilities before proposing a new tool, and cleanup should not remove or replace a capability without profile evidence.

## Harness

Files:

- `services/zoe-data/zoe_capability_profile.py`
- `services/zoe-data/tests/test_zoe_capability_profile.py`

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

## Next Use

The next self-evolution slices should use these profiles to:

- generate a current capability inventory view;
- score candidate Pi/MCP/GitHub/skill/API adoption;
- run `scripts/maintenance/pi_runtime_probe.py --json` before any Pi install or delegated execution proposal;
- block privileged execution when profile approval rules are unmet;
- add outcome eval traces against profile IDs;
- require non-use, replacement, or failure evidence before retirement.
