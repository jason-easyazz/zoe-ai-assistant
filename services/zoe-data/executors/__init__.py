"""Executor adapters for Multica-dispatched work.

Multica is the agnostic source of truth for issues; each executor adapter
knows how to run an issue on a specific backend (Hermes Kanban today;
OpenClaw / Codex / Cursor are future drop-ins). Adapters implement a tiny
contract so Zoe core stays backend-agnostic:

    dispatch(issue) -> dict   # start work, return an external reference
    poll(external_ref) -> dict  # report {status, pr_url, blocker}
"""
