---
name: source-code-context
description: Use when Hermes is integrating a package, SDK, API client, framework, or open-source tool and should inspect real local source instead of guessing API names or behavior from stale docs.
version: 1.0.0
author: Zoe
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [zoe, opensrc, context-engineering, source-code, dependencies]
    related_skills: [zoe-engineering]
---

# Source Code Context

Source code is the source of truth for fast-moving developer tools. Use this skill before coding against an API you are not certain about.

## When To Use

- Integrating a package, SDK, framework, MCP server, browser tool, or open-source service.
- Existing docs are incomplete, stale, or ambiguous.
- Hermes is tempted to install an alternative dependency because an API is unclear.
- A test or review suggests a method name, payload shape, or lifecycle assumption is wrong.

## Zoe Convention

Keep reference source outside the Zoe repo:

```bash
~/.opensrc/repos/
```

Prefer:

```bash
opensrc path pypi:<package>
opensrc path owner/repo
```

Then search the returned path for real APIs, examples, and tests.

## Rules

- Do not vendor reference repos into `/home/zoe/assistant`.
- Do not paste large source trees into prompts.
- Report which package files or examples informed the implementation.
- Avoid dependencies younger than 14 days unless the operator explicitly approves.
- If `opensrc` cannot fetch or locate the source, use upstream docs or ask before substituting another package.

## Prompt Pattern

When working on a feature, first identify the relevant local source:

```text
We use <library/tool>. Find its source with opensrc, inspect the current API and examples, then implement the smallest Zoe change that uses the existing pattern.
```

## Verification

After coding, run the focused test or smoke check that proves the integration matches the real API. If no automated check exists, state the manual check needed.
