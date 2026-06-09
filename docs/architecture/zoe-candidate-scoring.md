# Zoe Candidate Scoring

## Purpose

Zoe should search and evaluate before she builds or installs. Candidate scoring gives Zoe a repeatable way to compare Pi packages, MCP servers, GitHub projects, local skills, APIs, local services, and existing Zoe capabilities.

This is an evaluation contract only. It does not install, execute, or approve any candidate.

## Harness

Files:

- `services/zoe-data/zoe_candidate_scoring.py`
- `services/zoe-data/tests/test_zoe_candidate_scoring.py`

Scoring dimensions are 0-5:

- fit;
- activity;
- license;
- offline viability;
- security;
- hardware/runtime footprint;
- tests;
- maintainability;
- overlap with existing Zoe capabilities.

The first example records compare:

- keeping MemPalace as Zoe's measured offline baseline;
- trying Graphiti with FalkorDB as a sidecar;
- reviewing Pi runtime/package reuse before any install.

## Adoption Gate

The gate blocks candidates when:

- license risk is incompatible or unknown;
- offline viability is unavailable or unknown;
- normalized score is below threshold.

This makes the default future behavior conservative: Zoe can scout and propose broadly, but adoption still requires evidence, compatible licensing, offline/local viability, and a good enough score.

## Next Use

Future self-evolution proposal records should attach a candidate score before asking to install, integrate, or replace a capability.
