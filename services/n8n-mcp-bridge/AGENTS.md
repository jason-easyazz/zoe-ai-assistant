# services/n8n-mcp-bridge/ — n8n MCP bridge

## Purpose

Small containerized bridge exposing n8n workflow triggers to Zoe over MCP. Single `main.py` plus Dockerfile.

## Local Contracts

- n8n endpoints/credentials come from environment configuration, never hardcoded.
- Workflow side effects are world-changing actions: route through the proposal path, not silent execution.

## Work Guidance

(empty)

## Verification

Rebuild the container and trigger one workflow through Zoe chat.

## Child DOX Index

No child AGENTS.md files.
