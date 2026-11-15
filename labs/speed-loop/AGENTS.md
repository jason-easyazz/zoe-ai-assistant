# speed-loop

## Purpose
[lab] An unwired, engine-agnostic response-speed experiment loop, synthetic by default.

## Ownership
The coding agent builds lab artifacts; the operator owns windows and any future PR.

## Forbidden
- No production edits, environment edits, service control, network or model loads.
- No safety-floor, forget/erase, auth, policy or model-selection changes.
- No automatic PR, commit, deployment or application of a KEEP proposal.
- No real replay until an exclusive box window is granted; the adapter stays a stub.
- No household text in any artifact; ledger strings are fixed identifiers/enums only.

## Work Guidance
Follow program.md for one experiment. Candidate dictionaries are the only mutable
surface. windows.json is a proposal for operator editing, never an occupancy claim.

## Verification
Hand-run test_speed_loop.py with the root test-lane and quiet-wait rules. These
lab tests deliberately have no ci_safe marker and are not wired into CI.

## Child DOX Index
README.md explains measurement and adapter limits; surface.json is the declared
surface; windows.json is the schedule; fake-tally.json is synthetic proof only.
