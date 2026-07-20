---
name: zoe-cloakbrowser
description: Use Zoe's local CloakBrowser stealth Chromium through the zoe-tools MCP server for browser research, bot-protected pages, screenshots, and former OpenClaw browser workflows.
version: 1.0.0
author: Zoe
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [zoe, cloakbrowser, browser, playwright, research, screenshots]
    related_skills: [zoe-engineering, zoe-status-refresh]
---

# Zoe CloakBrowser

Hermes owns Zoe browser work. Do not delegate browser tasks to OpenClaw.

Use the `zoe-tools` MCP tools:

- `cloakbrowser_fetch` — open a URL in Zoe's local CloakBrowser stealth Chromium and return visible text.
- `cloakbrowser_screenshot` — open a URL and return a PNG screenshot as base64.

Use these for:

- Bot-protected pages where normal HTTP fetch fails.
- Browser-based research that needs rendered content.
- Visual verification screenshots.
- Workflows previously described as OpenClaw browser tasks.

Rules:

- Do not submit forms, make purchases, send messages, or perform world-changing actions without explicit user confirmation.
- Do not print secrets, cookies, tokens, or credentials.
- Prefer `cloakbrowser_fetch` for text evidence and `cloakbrowser_screenshot` only when visual proof is needed.
- If a login, captcha, payment, destructive confirmation, or manual 2FA is encountered, stop and report the blocker.

If the MCP tool is unavailable, ask for Hermes MCP reload/restart rather than using OpenClaw.
