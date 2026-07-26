---
name: agentic-engineering-workflow
description: "Use when building software with AI agents and you need Zoe's serious end-to-end workflow: Graphify-first navigation, source-backed context, minimal implementation first, cleanup pass, review loop, and explicit verification."
version: 1.0.0
author: Zoe
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [agentic-engineering, ai-coding, workflow, zoe, hermes]
    related_skills: [zoe-engineering, source-code-context, code-structure-cleanup, github-greptile-loop, zoe-graphify]
---

# Agentic Engineering Workflow

This is Zoe's local umbrella workflow for building with AI agents.

Core rule: the human owns outcomes, the agent does the mechanical work, and tests/review keep the result honest.

## When To Use

- Building an MVP, feature, integration, or internal tool with Hermes.
- Converting vague AI coding into a repeatable workflow.
- Planning or implementing work that should stay small, reviewable, and source-backed.
- Repairing or extending Zoe itself.

Do not use this for tiny one-line edits where a direct prompt is enough.

## Zoe Workflow

1. **Use the strongest suitable harness.** For Zoe engineering, Hermes is the default development agent.
2. **Keep the task small.** Prefer one feature, one fix, or one reviewable unit at a time.
3. **Use Graphify before broad repo searching.** For architecture or cross-module questions, start with `zoe-graphify`.
4. **Use real source before guessing.** For third-party packages/frameworks, use `source-code-context` and `opensrc` or an upstream reference repo.
5. **Build the minimal feature first.** Do not mix broad refactors into the first implementation pass.
6. **Run a cleanup pass.** After the feature works, use `code-structure-cleanup` to remove duplicated runtime mechanics.
7. **Run a review-fix loop.** For PR review, use `github-greptile-loop` and fix real findings until the diff is clean.
8. **Verify explicitly.** Run Zoe validators, focused tests, and live smoke checks before reporting done.
9. **Ship small usable increments.** A small reviewed improvement beats a large private branch that never reaches feedback.

## Zoe-Specific Guardrails

- Production API lives in `services/zoe-data/`.
- Keep one production chat router: `services/zoe-data/routers/chat.py`.
- `services/zoe-core/` is retired reference code.
- Do not create `_v2`, `_new`, `_fixed`, `_backup`, or duplicate router files.
- Do not hardcode secrets or print tokens.
- Avoid dependencies younger than about 14 days unless the operator explicitly approves the risk.

## Starter Prompt

```text
We are going to build this using Zoe's agentic engineering workflow.

Rules:
1. Keep the change small and reviewable.
2. Search existing Zoe code before creating new abstractions.
3. If using a package/framework, reference local source with opensrc or upstream repo before guessing APIs.
4. Build the minimal working version first.
5. After it works, run a code-structure cleanup pass.
6. Run relevant tests, validators, and live smoke checks.
7. Summarize what changed, what was tested, and what still needs human judgment.

Task:
<describe the feature or fix>
```

## Verification Checklist

- [ ] Task was kept small and reviewable.
- [ ] Relevant existing code was searched before editing.
- [ ] External library behavior was checked against source or official docs.
- [ ] Feature works locally or blocker is clearly stated.
- [ ] Cleanup pass checked for duplicated runtime mechanics.
- [ ] Tests/validators ran or the reason they could not run is stated.
- [ ] Security-sensitive changes were explicitly reviewed.
