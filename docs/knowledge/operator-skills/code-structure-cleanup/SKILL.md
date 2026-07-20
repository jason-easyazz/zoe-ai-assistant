---
name: code-structure-cleanup
description: Use after a Zoe feature works but duplicated runtime mechanics, repeated provider calls, parsing, validation, or command execution should be moved into small reusable service-layer functions without changing behavior.
version: 1.0.0
author: Zoe
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [zoe, refactor, service-layer, cleanup, agentic-engineering]
    related_skills: [zoe-engineering, requesting-code-review, github-greptile-loop]
---

# Code Structure Cleanup

Run this after the feature works. Do not use it as permission to redesign Zoe.

The goal is to reduce repeated mechanics so future agents can understand and repair the code safely.

## When To Use

- A feature works, but similar helper logic appears in multiple files.
- Provider calls, parsing, validation, command execution, or payload transforms are repeated.
- A bug fix in one path would not reach another path doing the same operation.
- Greptile or a human review calls out duplication that affects correctness or maintainability.

Do not extract logic that is used once or is clearly domain-specific.

## Zoe Boundary Rule

Routes, intents, actions, and UI handlers own product policy:

- user and scope checks,
- auth and RBAC decisions,
- status transitions,
- proposal/approval flow,
- user-facing wording,
- AG-UI response shape.

Service-layer functions own reusable mechanics:

- provider or SDK calls,
- request/response normalization,
- validation that is not domain policy,
- command execution details,
- retry/readiness mechanics,
- payload transforms.

Design service functions as small capability blocks:

- accept required data as explicit parameters,
- return structured outputs instead of loose strings or hidden side effects,
- avoid reaching into database state unless the service owns that persistence boundary,
- make failure explicit with structured results or clear exceptions,
- keep API shapes consistent across related helpers.

## Process

1. Inspect only the files touched by the feature and their immediate callers.
2. Name the duplicated mechanics clearly.
3. Extract the smallest repeated non-domain block into an existing service module when one fits.
4. Replace one caller, verify behavior, then replace the remaining callers.
5. Keep names and call shapes close to existing Zoe style.
6. Run focused tests and Zoe validators.

## Guardrails

- Do not create duplicate routers or files ending in `_new`, `_fixed`, `_v2`, `_old`, or `_backup`.
- Do not move domain policy into shared services.
- Do not create a god service that hides all control flow.
- Do not create leaky services that mutate unrelated domain state or depend on hidden globals.
- Do not extract one-off logic only to make the code look more abstract.
- Do not touch retired production code under `services/zoe-core/` except as archive context.
- Do not change user-facing behavior unless the user explicitly asked for that change.

## Verification

For Zoe repo work, start with:

```bash
cd /home/zoe/assistant
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
```

Then run focused tests for the touched area. For live user-facing changes, also check:

```bash
curl -sf http://127.0.0.1:8000/health
curl -sf http://127.0.0.1:8000/api/system/status
```
