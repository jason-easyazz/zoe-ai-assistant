# services/homeassistant-mcp-bridge/ — Home Assistant MCP bridge

## Purpose

Small containerized bridge exposing Home Assistant control to Zoe over MCP. Single `main.py` plus Dockerfile.

## Local Contracts

- Smart-home concepts stay in skills and scopes; this bridge exposes tools, it must not push household assumptions into kernel code.
- HA credentials come from environment configuration, never hardcoded.
- The live Home Assistant runtime tree at `homeassistant/` (repo root) is data, not code — do not edit it from here.

## Work Guidance

(empty)

## Verification

Rebuild the container and exercise one HA tool call through Zoe chat.

## Child DOX Index

No child AGENTS.md files.
