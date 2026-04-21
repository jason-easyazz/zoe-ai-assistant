"""
Zoe Skills System
==================

Phase 1a: Lightweight instruction layer (adopted from OpenClaw's skills pattern).

Skills are markdown files (SKILL.md) with YAML frontmatter that tell the LLM
how to handle specific request types. They complement the heavy-weight module
system by providing quick, API-only instruction sets.

Components:
- loader.py    -- Parse SKILL.md files with YAML frontmatter
- registry.py  -- Hot-reload registry with precedence and lockfile verification
- executor.py  -- Safe API-only skill execution with endpoint whitelisting
"""
