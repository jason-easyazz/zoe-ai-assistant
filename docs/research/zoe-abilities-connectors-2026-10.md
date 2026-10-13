---
type: research
title: Zoe abilities and connected accounts - how others link agents to email, Telegram, WhatsApp, phone and calendars (2026-10-14)
date: 2026-10-14
status: research-only; no code, flag, unit or live service touched. The design that follows from it is docs/architecture/zoe-abilities.md.
description: Research record for the owner's ask to let household members connect their own services (email, Telegram, WhatsApp, phone/SMS, calendars, Signal, Discord) so Zoe can keep an eye on them, and later act with approval. Part 1 is the field - Flue 2.1.1's own guidance on tools, MCP, skills and channel blueprints (read from the pinned source), the MCP authorization spec and registries, the MCP servers that exist per service with licence and maturity, MCP security incidents, connector platforms (Composio, Nango, Pipedream Connect, Arcade, Merge, Paragon, Klavis, Activepieces) judged against local-first, Home Assistant's integrations and OAuth redirect pattern, automation hubs and notify layers, and how ChatGPT, Claude, Gemini, Copilot, Lindy and OpenClaw handle connect, consent and approval. Per service it covers push versus poll, auth on a home box (the OAuth device flow and which providers allow it), AU availability and cost, privacy and terms, and RAM. Part 2 is what Zoe already has, with file:line. It builds on the action-agent record (PR #2011) and does not repeat its errand, approval and T0-T5 design.
---

# Zoe abilities and connected accounts - field research (2026-10-14)

Owner request (2026-10-14), verbatim: *"research linking Zoe to other services, like a user's e-mail etc, so
she can keep an eye on these things; there is a lot out there to learn from about adding these abilities to
agents; Flue has guidance plus many other projects. Figure out a way for users to add abilities to Zoe:
e-mail, Telegram, WhatsApp, telephone etc."*

The design is [docs/architecture/zoe-abilities.md](../architecture/zoe-abilities.md). This record is its
evidence. It builds on [zoe-action-agent-2026-10.md](./zoe-action-agent-2026-10.md) (PR #2011, merged
2026-10-12), which owns errands, approvals, the T0-T5 permission tiers, the egress doctrine, email **as
Zoe** (her own mailbox), phone calls and payments. This record is about the other direction: **a member's
own accounts, watched for them**.

**Evidence tags.** **[verified]**: read on a primary page (vendor docs, a spec, the repo itself, or the
pinned source on disk) this session. **[secondary]**: a blog, news item, third-party guide or search
summary. **[unverified]**: could not be confirmed. Repository numbers (licence, stars, last push) are from
the GitHub REST API on 2026-10-14 unless stated. Pages were accessed 2026-10-14 unless a date is given.
Nothing here is legal advice; points that need a lawyer are marked **[LAWYER]**.

## 0. Answer first

- **Zoe can do a lot here, and most of it is adoption, not building** (section 5, the connector bake-off).
  Two jobs, two answers. **Talk to Zoe on any app:** Flue, which already runs Zoe's brain, ships verified
  channel packages for WhatsApp (Cloud API), SMS (Twilio), email (Resend), Slack, Discord, Messenger,
  Teams and Google Chat; each is a route plus an official SDK in one channels sidecar behind the existing
  Cloudflare tunnel, linked to members the way Telegram already is. **Watch my own accounts:** an
  in-process IMAP/Graph adapter for email (no maintained service keeps tokens on the box per member), the
  mautrix bridges on a small Matrix homeserver for WhatsApp, Signal, SMS/RCS and Messenger (maintained
  weekly upstream; gated on a RAM measurement), a Telegram Business connection of Zoe's existing bot,
  Home Assistant for shared household calendars, Apprise for notifications out, and rclone for files.
  OpenClaw and Hermes got their many channels the same way: one adapter per network over a maintained
  library, MIT-licensed, so their email sender-authentication and IMAP watcher code is borrowable.
- **The field has converged on one pattern, and Zoe already owns half of it.** Every serious product
  (ChatGPT apps, Claude connectors, Arcade, Composio, Microsoft 365 Copilot) does the same five things:
  a per-user "connect" link the agent can surface when a tool needs an account; OAuth (or a pairing code)
  finished in a browser outside the model; tokens stored per user in a vault the model never sees; reads
  run freely while writes need a confirmation; and a "connected apps" list with disconnect. Zoe already has
  the hardest UX piece of that, built to the owner's own rule: `auth_handoff.py` is one engine for "QR on
  the panel, finish on the phone" with live status back to the panel and a Telegram "send to my phone"
  link. It has one registered kind (music). What Zoe lacks is the **vault**, the **manifest** that says
  what an ability may do, and the **watcher** that turns an inbox into a short list worth telling you.
- **Flue's guidance is short and agrees with Zoe's existing rules.** Tools are application policy; a tool's
  arguments are "model-selected inputs, not an authorization boundary"; trusted code binds "the token,
  repository, or destination"; avoid "generic provider tools that expose arbitrary destinations or API
  methods" [verified, flueframework.com/docs/guide/tools]. Flue mounts MCP servers (remote only:
  streamable HTTP or SSE, no stdio) but "never stores or manages your tokens": the application owns OAuth,
  storage and refresh [verified, docs/guide/mcp and `@flue/runtime` 2.1.1 `mcp-types.ts`]. Its channel
  blueprints (Telegram, WhatsApp, Twilio, Resend, Slack and 14 more) are **webhook-ingress** guides for an
  agent that has a public URL. They are the right tool for "talk to Zoe" on those apps, behind one
  tunnel path each (section 5.1); they do not watch a member's own account, so the watcher side comes from
  elsewhere.
- **Do not put the brain in front of third-party MCP servers.** MCP is the right protocol to adopt
  maintained adapters through, but the policy point must be zoe-data, not the 4B model: MCP tool
  annotations are explicitly untrusted hints, registries do "minimal-to-no moderation", and the documented
  attacks (tool poisoning, the WhatsApp MCP exfiltration, the GitHub MCP leak, the malicious
  `postmark-mcp` package) all rely on a model that holds both private data and an outbound tool.
- **Connector platforms do not fit local-first.** Composio, Pipedream Connect, Arcade, Merge and Paragon
  keep the tokens (and usually the traffic) in their cloud, or self-host only on enterprise tiers. Nango's
  free self-host is a token vault and proxy only. Borrow their *patterns* (per-user connected accounts,
  just-in-time authorisation per tool), not their services.
- **Per service, the honest picture:**
  - **Email** is the best first ability. IMAP IDLE gives push from any provider with no public endpoint. Gmail's
    API is the hard path: its read scopes are *restricted*, its device flow does not allow Gmail, and a
    self-hosted client in Testing mode loses its refresh token every 7 days. An **app password over IMAP**
    (Gmail with 2-Step Verification, iCloud, Fastmail, most hosts) works today, and Home Assistant's own IMAP
    integration takes exactly that route. Microsoft accounts are the reverse: no app passwords for
    Outlook.com, but the device-code flow works for IMAP and Graph.
  - **Telegram**: Zoe's bot already exists and is the right way to *talk to* Zoe. An ordinary bot never sees a
    person's other chats. The sanctioned way to let it is a **Telegram Business connection**: the member adds
    Zoe's existing bot as a chatbot from their own phone and the Bot API delivers `business_message` updates
    (needs Telegram Premium, per vendor pages). The fallback is the member's own account over MTProto
    (Telethon or TDLib, QR login), which Telegram allows with the user's knowledge and consent but places
    "under observation". Telegram's API terms also forbid using its data to train or develop AI models, a
    clause for a lawyer to read.
  - **WhatsApp**: there is no official way to read a personal WhatsApp. The Cloud API is for businesses, and Meta
    has barred general-purpose AI assistants from it. The only route is an unofficial linked-device client
    (whatsmeow, Baileys), which breaks Meta's terms and puts the member's real account at risk. That is an
    owner decision, not an engineering one.
  - **Phone and SMS** belong to the action-agent record's Phase 5 (legal and carrier eligibility). For
    *watching* SMS, a spare Android phone running an open-source SMS gateway is the private option.
  - **Calendars**: CalDAV sync tokens and Home Assistant's existing calendar integrations make this cheap.
  - **Signal**: `signal-cli` as a linked device works and is the most privacy-aligned messenger, at the RAM cost
    of a JVM or native binary.
  - **Discord**: user-account automation breaks its terms; only bots in servers the member controls.
- **Recommended first slice** (design doc section 9): A0 (registry, vault, Connected accounts page, moving
  the existing Telegram and Music connects onto it, no new egress) then A1 (read-only email for the owner,
  app password on the phone, in-process IMAP IDLE watcher, tool-less summariser, pull queue and morning
  brief). Everything after that waits for a measured A1 and owner decisions.

## 1. Flue's own guidance (pinned source: `@flue/*` 2.1.1)

The brain sidecar pins `@flue/runtime` 2.1.1 (`labs/flue-zoe-brain-2x/package.json`). Source read from the
opensrc cache (`opensrc path npm:@flue/runtime@2.1.1` resolves to `withastro/flue` tag 2.1.1) plus the live
docs site.

| Topic | What Flue says or does | Evidence |
|---|---|---|
| Tools | "Tools are functions you write and describe to the model ... your code determines the outcome." Vague descriptions are "the most common cause of a tool being called incorrectly or not at all." Durable tools wrap side effects in `step.do(name, fn)`: "exactly-once-recorded, at-least-once-executed", so effects must be idempotent. Conditional tools may be mounted only after, say, an approval in persistent state, but "Changing the tool set rewrites the provider's tools array, which invalidates its prompt cache." | [verified] [docs/guide/tools](https://flueframework.com/docs/guide/tools/) |
| Authorization | "A tool's arguments are model-selected inputs, not an authorization boundary." "Your application should decide which customer, account, repository, or credential a tool can use." Bind "the token, repository, or destination" in trusted code; avoid "generic provider tools that expose arbitrary destinations or API methods" unless there is an explicit authorization design. | [verified] same page |
| MCP | `useMcpConnection({name, url, transport, auth, tools, optional})`; tools mount as `mcp__<server>__<tool>`. Transports are `'streamable-http'` and `'sse'` only (no stdio). `auth` is a static bearer or a resolver called **per request** ("so rotating and per-user credentials stay fresh"); "tokens are never persisted". `tools` is an allowlist that fails loudly on a typo. `optional: true` lets a run continue without a failed server and tells the model. Annotations are carried through but "untrusted unless the server is trusted; the runtime does not change execution based on them." | [verified] `packages/runtime/src/mcp-types.ts`, `hooks/use-mcp-connection.ts` (2.1.1); [docs/guide/mcp](https://flueframework.com/docs/guide/mcp/) |
| MCP and OAuth | "Flue never stores or manages your tokens." The application owns "any OAuth flow, token storage, and refresh token logic"; mid-conversation authorisation is done by declaring the connection conditionally on a persistent flag that flips after the user authorises. "An MCP server you connect to can influence your agent": its descriptions enter the prompt and its results the conversation. | [verified] docs/guide/mcp |
| Skills | Open Agent Skills format (`SKILL.md`, `name` + `description`), progressively disclosed: one catalogue line per skill, the model calls `activate_skill` for the body, so the cached prompt prefix survives. "Use a skill when the agent needs to know *how*, a tool when it needs to *do*." | [verified] [docs/guide/skills](https://flueframework.com/docs/guide/skills/) |
| Channel blueprints | `flue add channel <name>` prints a Markdown guide for a coding agent (19 named channels incl. telegram, whatsapp, twilio, resend, discord, slack). Contract: verified webhook ingress (signature over the exact raw body), a provider SDK client, "Define only requested tools, with trusted destinations outside model arguments", stable delivery ids for idempotency, keep "raw payloads, webhook response URLs, interaction tokens, credentials ... out of model-visible or durable input". Deduplication is the project's job. | [verified] `blueprints/README.md`, `blueprints/channel.md`, `channel--whatsapp.md`, `channel--telegram.md`, `channel--resend.md` (2.1.1) |
| Inbound events | Channels hand normalised events to an agent with `dispatch(Agent, {id, message: {kind: 'signal', type, body, attributes}})`; the instance id identifies a destination and "is not an authorization capability". | [verified] `channel--whatsapp.md` example |

**What Zoe should take from Flue.** (1) The authorization rule, already applied in Zoe's brain
(`zoe-tools.ts` header: identity "bound in TRUSTED CODE, never from model args"). (2) Keep tool sets static
per agent and gate at call time, because a changing tool block breaks the prompt cache (and, on llama.cpp,
the exact-prefix KV reuse that Zoe's `services/AGENTS.md` KV-prefix contract protects). (3) Allowlist MCP
tools by name and never trust annotations. (4) Treat inbound events as signals with a stable delivery id
and dedupe in the application. (5) **Adopt the channel packages for "talk to Zoe"** (section 5.1): they
need a public webhook, which one Cloudflare-tunnel path per channel provides, with each package verifying
the provider's signature. Watchers are different: they read a member's own account, so they should stay
outbound-only (IMAP IDLE, long polls, sync tokens), as Zoe's Telegram lane already long-polls
(`labs/flue-zoe-telegram-2x/src/app.ts:201`).

**One compatibility unknown.** The MCP spec's current revision is **2026-07-28**, which removes the
`initialize` handshake and session ids (stateless requests carrying protocol version and capabilities in
`_meta`), formally deprecates HTTP+SSE, and replaces server-initiated elicitation with "multi round-trip
requests" [verified, [versioning](https://modelcontextprotocol.io/specification/versioning),
[changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)]. The brain pins
`@modelcontextprotocol/sdk` 1.31.0 and `@modelcontextprotocol/client` 2.2.0 through `overrides`; whether
Flue 2.1.1's client speaks the stateless dialect is **[unverified]**. It does not matter for this design,
because the brain does not connect to MCP servers (design section 2.3).

## 2. MCP: authorization, registries, servers, incidents

### 2.1 Authorization (spec 2025-06-18 baseline, 2025-11-25, 2026-07-28 current)

- Authorization is optional; HTTP transports SHOULD follow it, stdio SHOULD NOT (credentials come from
  the environment). The MCP server is an OAuth 2.1 **resource server** [verified,
  [authorization](https://modelcontextprotocol.io/specification/draft/basic/authorization)].
- Servers MUST publish RFC 9728 Protected Resource Metadata; clients MUST use it to find the
  authorization server, MUST use PKCE S256, and MUST send the RFC 8707 `resource` parameter; servers MUST
  validate the token audience [verified, same, and
  [security considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)].
- Client registration: Client ID Metadata Documents (an HTTPS URL as `client_id`, added 2025-11-25) are
  preferred; Dynamic Client Registration is deprecated in 2026-07-28 [verified, changelogs].
- **No token passthrough.** A server MUST NOT pass the client's token upstream and MUST accept only tokens
  issued for itself [verified].
- **URL-mode elicitation** (2025-11-25) is the sanctioned way for an MCP server that is itself the OAuth
  client of Gmail or Slack to send the user to a browser: third-party credentials "MUST NOT transit the MCP
  client", the server stores them bound to the user, the URL must not be pre-authenticated, and the server
  MUST check that the person who opens the link is the person who started the request [verified,
  [elicitation](https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation)]. That is the
  same shape as Zoe's handoff engine (single-use token in the URL fragment, owner bound at `start`).
- **Tool annotations** (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) are hints:
  "Clients should never make tool use decisions based on ToolAnnotations received from untrusted servers"
  [verified, `schema/2025-11-25/schema.ts`]; "clients MUST consider tool annotations to be untrusted unless
  they come from trusted servers" [verified,
  [tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)].

### 2.2 Registries

| Registry | Gives | Does not give |
|---|---|---|
| Official MCP Registry (registry.modelcontextprotocol.io), preview since 2025-09-08 | `server.json` metadata, reverse-DNS namespaces verified by GitHub/DNS/HTTP, a REST API | Vetting: "consumers should assume minimal-to-no moderation"; it will not remove servers "with security vulnerabilities" [verified, [moderation policy](https://modelcontextprotocol.io/registry/moderation-policy)] |
| Docker MCP Catalog | 300+ servers; Docker builds and signs "local" servers with provenance and SBOM; one container per server [verified, [docs](https://docs.docker.com/ai/mcp-catalog-and-toolkit/catalog/)] | Behavioural review |
| Smithery | Hosted catalogue; "handles OAuth setup, token refresh" with "encrypted, write-only storage" [verified, [docs](https://smithery.ai/docs)] | Local-first: a third party holds the tokens |
| Glama, mcp.so | Large directories (Glama ~97.6k listings with A-F grades) [secondary] | Security review |

A registry is a phone book, not an audit. Zoe's existing rule already covers adoption: scan with
`skillspector`, pin, review (root AGENTS.md, "Skill & extension safety").

### 2.3 MCP servers per service (GitHub API, 2026-10-14)

| Service | Server | Licence / stars / last push | Transport, auth | Verdict for Zoe |
|---|---|---|---|---|
| Gmail + Calendar | Google's hosted Workspace MCP servers (`gmailmcp.googleapis.com`) | hosted, **Developer Preview** | remote HTTP, OAuth with a Cloud client marked "AI-powered agent"; read, search, draft, label (no send tool seen) [verified, [Google docs](https://developers.google.com/workspace/guides/configure-mcp-servers)] | Egress to Google's agent endpoint and preview-only; not for a home box |
| Gmail + Workspace | taylorwilsdon/google_workspace_mcp | MIT, 3,336, 2026-10-09 (v2.1.0) | stdio and streamable HTTP; OAuth 2.1 + PKCE with your own client; `--read-only`, tool tiers [verified, README] | The most mature Gmail adapter; still meets Gmail's restricted-scope wall (section 4.1) |
| Gmail | GongRzhe/Gmail-MCP-Server | MIT, 1,168, **archived** | stdio | Avoid |
| IMAP/SMTP | Wh1isper/mcp-email-server | BSD-3, 359, 2026-10-13 (1.13.1) | stdio; password in an SQLite store [verified, README] | Idea donor; a Python IMAP library in-process is lighter |
| IMAP/SMTP | codefuturist/email-mcp | LGPL-3.0, 127, 2026-10-10 (v0.6.0) | stdio only; send rate limiter and audit trail [verified, README] | Pre-1.0 |
| Outlook / M365 | Softeria/ms-365-mcp-server | MIT, 1,037, 2026-10-13 (v0.165.0) | stdio and streamable HTTP; device code by default; `--read-only`; personal accounts via tenant `consumers` [verified, README] | Best maintained Microsoft adapter; 300+ tools means a strict allowlist |
| Outlook / M365 | Microsoft Work IQ MCP | hosted, GA (tenant-oriented) | Entra [verified, [Learn, 2026-10-08](https://learn.microsoft.com/microsoft-agent-365/tooling-servers-overview)] | Work tenants, not households |
| Telegram (user) | chigwell/telegram-mcp | Apache-2.0, 1,815, 2026-10-13 (v3.2.80) | stdio, HTTP, SSE; Telethon session; README warns the session string is full account access and the HTTP endpoint is unauthenticated [verified, README] | Idea donor for the user lane; Zoe would wrap Telethon directly |
| WhatsApp (personal) | lharries/whatsapp-mcp | MIT, 6,584, **last push 2025-07-13** | whatsmeow linked device, SQLite history, re-pair about every 20 days [verified, README] | Stale; the README itself cites the lethal trifecta |
| SMS / voice | twilio-labs/mcp | MIT, 113, 2026-02-14, "Alpha" | stdio, API key [verified, README] | Out of scope here (action-agent Phase 5) |
| Signal | rymurr/signal-mcp, Sealjay/mcp-signal, Transmitt0r/signal-mcp-server | tiny, community | wrap `signal-cli` [secondary] | Immature; wrap `signal-cli` JSON-RPC directly |
| Calendar | nspady/google-calendar-mcp (MIT, 1,200); CalDAV servers (all small) | | own OAuth client / app passwords [secondary] | Home Assistant's calendar integrations are the better organ |
| stdio-to-HTTP bridges | supercorp-ai/supergateway (MIT, 2,898, v4.1.0); sparfenyuk/mcp-proxy (MIT, 2,782, v0.13.0) | | supergateway has `--apiKey` inbound auth and is 2026-07-28-aware; mcp-proxy binds 127.0.0.1 by default [verified, READMEs] | Only if a stdio server is ever adopted; each bridge is another process |

Only Google (preview), Microsoft (tenant GA) and Twilio (alpha) publish their own servers. Everything Zoe
would wrap for a household is community-maintained.

### 2.4 Incidents and research (one line each)

- **Tool poisoning, rug pull, shadowing** (Invariant Labs, 2025-04-01): hidden instructions in tool
  descriptions; a description changed after approval; one server's description steering another's tools
  [verified, [post](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks)].
- **WhatsApp MCP exfiltration** (Invariant Labs, 2025-04-07): an injected message (or a second malicious
  server) makes an agent connected to the WhatsApp MCP leak chat history *through WhatsApp itself*;
  sandboxing the malicious server does not help [verified, [post](https://invariantlabs.ai/blog/whatsapp-mcp-exploited)].
  This is the exact shape of a "watch my messages and also reply" agent.
- **GitHub MCP leak** (Invariant Labs, 2025-05-26): a public issue made an agent leak private repository
  data into a public PR [verified, [post](https://invariantlabs.ai/blog/mcp-github-vulnerability)].
- **postmark-mcp** (Koi Security, September 2025): a look-alike npm package added a BCC to the attacker on
  every email sent [secondary, [The Hacker News](https://thehackernews.com/2025/09/first-malicious-mcp-server-found.html)].
- **The lethal trifecta** (Simon Willison, 2025-06-16): private data + untrusted content + a way to
  communicate out, in one agent, is exploitable; remove one leg [verified,
  [post](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)]. An email watcher that can also
  send has all three.
- **ClawHavoc** (early 2026): 341 of 2,857 audited ClawHub skills malicious (mostly an infostealer behind
  fake "prerequisites" commands); later scans report higher counts; numbers vary by source [secondary,
  [The Hacker News](https://thehackernews.com/2026/02/researchers-find-341-malicious-clawhub.html),
  [Antiy](https://www.antiy.net/p/clawhavoc-analysis-of-large-scale-poisoning-campaign-targeting-the-openclaw-skill-market-for-ai-agents/)].
  Zoe's own records already noted the ClawHub poisoning (self-building-skills, 2026-10-04).

## 3. Connector platforms, automation hubs, Home Assistant, agent products

### 3.1 Connector platforms against local-first

| Platform | Licence / self-host | Where tokens live | Verdict |
|---|---|---|---|
| **Composio** | SDK MIT; backend closed; self-host enterprise-only [secondary] | Composio's vault by default; a "self-managed credentials" mode passes your own tokens at execution time and "neither stores nor refreshes them" [verified, [pricing](https://composio.dev/pricing), [auth docs](https://docs.composio.dev/docs/authentication)] | Cloud execution; no |
| **Nango** | Elastic License; **free self-host = Auth + Proxy only** (no syncs, tools, MCP server, audit) [verified, [self-hosting](https://nango.dev/docs/guides/platform/self-hosting)] | Your server | The only real self-hosted token vault; but Postgres + Redis + server for something a table and a key file do |
| **Pipedream Connect** | Proprietary, no self-host [verified, [docs](https://pipedream.com/docs/connect.md)] | Pipedream, encrypted at rest | No |
| **Arcade.dev** | `arcade-mcp` MIT; the Engine is cloud ("keeps handling identity, routing, and governance" even on-prem workers) [verified, [docs](https://docs.arcade.dev/en/guides/deployment-hosting/on-prem)] | Arcade | No, but its **tool-level just-in-time authorisation** is the pattern to copy: `tools.authorize(tool, user_id)` returns a URL when the user has not connected; the call proceeds once they have [verified, [docs](https://docs.arcade.dev/en/guides/tool-calling/custom-apps/auth-tool-calling)] |
| Merge Agent Handler, Paragon ActionKit | Proprietary; Paragon self-host enterprise-only [verified, [pricing](https://www.useparagon.com/pricing-2)] | Vendor | No |
| **Klavis** | Apache-2.0; per-server Docker images and a local "Strata" router [verified, [repo](https://github.com/Klavis-AI/klavis)] | Local | OAuth handling undocumented [unverified]; watch |
| **Activepieces** | CE MIT, `packages/ee` commercial; Docker + Postgres + Redis; pieces exposed as MCP [verified, [repo](https://github.com/activepieces/activepieces)] | Local, but OAuth may be cloud-brokered (`AP_CLOUD_AUTH_ENABLED`) [secondary] | Too heavy for the job |

### 3.2 Automation hubs and notify layers

- **n8n**: Sustainable Use License (household use allowed) [verified, [LICENSE](https://github.com/n8n-io/n8n/blob/master/LICENSE.md)];
  IMAP trigger node with a forced-reconnect timer, no documented IDLE or OAuth [verified, [node docs](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.emailimap/)];
  about 180 MiB on its own, ~2 GB is the usual practical host minimum [secondary]. (n8n was already retired from Zoe in March 2026, `services/AGENTS.md`.)
- **Node-RED** (Apache-2.0): `node-red-node-email` fetches unseen IMAP mail; OAuth2 tokens must be supplied
  upstream; Outlook needs OAuth2 [verified, [node](https://github.com/node-red/node-red-nodes/tree/master/social/email)]; about 80 MB idle on a Pi [secondary].
- **Huginn** (MIT): IMAP/RSS/webhook watchers; 0.5 GB RAM + 0.5 GB swap minimum, 2 GB recommended [secondary, manual mirror].
- **Apprise** (BSD-2, 17.6k stars) is a single library for 100+ notification targets incl. Telegram, email,
  ntfy and SMS gateways [verified, [repo](https://github.com/caronc/apprise)]; **ntfy** (Apache-2.0/GPLv2)
  is a self-hostable push server with phone apps [verified, [repo](https://github.com/binwiederhier/ntfy)].

Verdict: none of these should host Zoe's watchers. Each is a second always-on runtime with its own
credential store, UI and RAM, for a job that a few asyncio tasks in zoe-data do, and none gives per-member
isolation. Apprise is worth remembering if Zoe ever needs to notify through a channel she has no client for;
Zoe's own push, bell and Telegram already cover the household.

### 3.3 Home Assistant (a hidden organ Zoe already runs)

- **Application Credentials**: the household enters its own OAuth client id and secret; "Cloud account
  linking is the preferred method" where a provider supports it [verified, [docs](https://www.home-assistant.io/integrations/application_credentials/)].
- **The OAuth redirect trick, without Nabu Casa**: Google Mail and Google Calendar tell you to register
  `https://my.home-assistant.io/redirect/oauth` as the redirect URI of a **Web application** client ("This
  is not a placeholder"); my.home-assistant.io stores the instance URL **in the browser** and bounces back
  to the local instance [verified, [google_mail](https://www.home-assistant.io/integrations/google_mail/),
  [google](https://www.home-assistant.io/integrations/google/), [my FAQ](https://my.home-assistant.io/faq/)].
  This is the cleanest known answer to "OAuth with a public HTTPS redirect, but the token lands on a LAN
  box". Zoe could use her own Cloudflare-tunnel hostname the same way (only a callback path exposed).
- **Google Calendar** in HA: publish the app, otherwise the credentials "expire every 7 days" [verified].
- **Google Mail** in HA is a **send/draft notify** integration; no read access is documented [verified].
- **IMAP** in HA: IDLE push by default (polls otherwise); an `imap_content` event with sender, subject,
  headers and optional text clipped to 2,048 bytes; `imap.fetch` for a full body; **no OAuth2**, so no
  Outlook.com or Microsoft 365; Gmail works with an app password [verified, [imap](https://www.home-assistant.io/integrations/imap/)].
- **CalDAV** (app-specific passwords, ~15-minute polling) [verified, [caldav](https://www.home-assistant.io/integrations/caldav/)];
  **Telegram bot** (polling needs no inbound exposure) [verified, [telegram_bot](https://www.home-assistant.io/integrations/telegram_bot/)];
  no official Outlook calendar integration (community HACS only) [secondary].

**Should Zoe use HA's IMAP integration as the email organ?** It is the "adopt" answer and it already does
IDLE. Against it: HA is household-scoped, so every member's mail would land on one event bus that any HA
automation or admin can read, which breaks per-member isolation; its credentials sit in HA's own config
storage rather than a per-member vault; and it has no OAuth, so Outlook still needs a second path. The
design takes HA for **calendars** (household data by nature, and HA's calendar integrations are mature) and
keeps **email** in a thin per-member adapter. That trade is recorded as an owner decision in the design.

### 3.4 Agent products with connected accounts

| Product | Connect | Approval of writes | Disconnect / visibility |
|---|---|---|---|
| ChatGPT apps (Apps SDK, MCP) | OAuth 2.1 + PKCE; linking UI appears when a tool returns an auth challenge [verified, [auth](https://developers.openai.com/apps-sdk/build/auth)] | Annotations "help ChatGPT choose confirmation behavior" but "do not replace authorization, validation, or confirmation in your server"; tools without `readOnlyHint` are treated as writes [verified/secondary] | settings page per app |
| Claude custom connectors | Remote MCP from Anthropic's cloud (server must be reachable); sign in now / when needed / none | per-tool-call approval with "Allow always"; tools can be disabled; "Connectors aren't verified by Anthropic" [verified, [help](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)] | Customize > Connectors |
| Gemini apps | same Workspace account; admins must allow [verified, [help](https://support.google.com/gemini/answer/15229592)] | not stated | gemini.google.com/apps |
| Microsoft 365 Copilot | federated MCP connectors under the user's identity | create/update/delete with explicit confirmation by default, GA October 2026 [secondary] | admin centre can disable write tools |
| Lindy | per-skill OAuth | "confirm before sending" setting [secondary] | - |
| OpenClaw (MIT, self-hosted; retired in Zoe) | 20+ channels; WhatsApp through Baileys with **QR-only login**; unknown senders get a pairing code the owner approves (`dmPolicy: pairing`); loopback bind; inbound treated as untrusted; one trust boundary per gateway [verified, [channels](https://docs.openclaw.ai/channels), [security](https://docs.openclaw.ai/gateway/security)] | - | - |

**The common pattern** (what the design copies): connect from an in-chat or settings link; authorisation
per user and per tool/scope; reads run, writes confirm; a connected-apps list with disconnect; unknown
inbound senders paired before they are trusted. **What Zoe adds** that none of them has: the connect step
starts by voice on a shared wall screen and finishes on a personal phone, and the data never leaves the box
except to the provider itself.

## 4. Per service: watching, auth on a home box, AU, privacy, RAM

**Two cross-cutting facts decide most rows.**

- **The OAuth device flow (RFC 8628) is the natural "QR on the panel, finish on the phone" grant, but
  providers limit it.** RFC 8628's `verification_uri_complete` is meant for "non-textual transmission"
  such as a QR code, and section 5.4 warns about remote phishing [verified, [RFC 8628](https://datatracker.ietf.org/doc/html/rfc8628)].
  **Google's device flow allows only** `openid`/`email`/`profile`, Drive `drive.appdata`/`drive.file` and
  YouTube scopes, so **no Gmail and no Calendar** [verified, [limited-input device](https://developers.google.com/identity/protocols/oauth2/limited-input-device), updated 2026-09-14];
  custom URI schemes and copy-paste (OOB) are gone [verified, [native apps](https://developers.google.com/identity/protocols/oauth2/native-app)].
  **Microsoft's device flow works for personal accounts** (tenant `consumers`/`common`, 15-minute code)
  but does **not** support `verification_uri_complete`, so the QR carries only the sign-in URL and the
  panel shows a short code the person types on their phone [verified, [Entra device code](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code), updated 2026-06-15].
- **The Cloudflare tunnel gives Zoe a public HTTPS hostname**, so an ordinary web OAuth redirect (and,
  if ever wanted, a Graph or Google Calendar webhook) is possible without opening a port. Home Assistant
  does the same thing with a static redirector (section 3.3). The design still prefers **outbound-only**
  watching (IDLE, delta polls, sync tokens, long polls) so the tunnel exposes at most one callback path.

No primary RAM figure was found for any client component below (TDLib, signal-cli, whatsmeow, Asterisk);
every RAM line is to be measured (design cell W10).

### 4.1 Email

| Option | Keep an eye on it | Auth on a home box | AU / cost | Privacy, terms | Client cost |
|---|---|---|---|---|---|
| **IMAP IDLE** (any provider) | Push. Re-issue IDLE at least every 29 minutes or the server may log the client off [verified, [RFC 2177](https://www.rfc-editor.org/rfc/rfc2177)]; IDLE watches the selected mailbox, so one connection per watched folder (inference); catch up by UID after any reconnect | App password typed on the phone | Gmail allows up to 15 simultaneous IMAP clients per account and IMAP is always on for personal accounts since January 2025 [verified, [Gmail help](https://support.google.com/mail/answer/7126229)]; Fastmail needs an app password and **its Basic plan has no IMAP, CalDAV or CardDAV** [verified, [Fastmail](https://www.fastmail.help/hc/en-us/articles/1500000278342)]; Telstra/BigPond mail: IMAP with an app-specific password per third-party guides [secondary] | Read with `BODY.PEEK` so `\Seen` never changes; an app password is long-lived and broad (full mailbox) | One TLS socket per account; a Python async IMAP library in zoe-data (measure) |
| **JMAP push** (Fastmail, Stalwart) | Push over an outbound `text/event-stream` (EventSource URL with `types`, `closeafter`, `ping`) [verified, [RFC 8620](https://www.rfc-editor.org/rfc/rfc8620)]; Stalwart batches with `eventSourceThrottle` [verified, [Stalwart](https://www.stalw.art/docs/http/jmap/push), 2026-10-03] | Fastmail personal API token as a bearer (simplest), or OAuth + PKCE with a registered client [verified, [Fastmail dev](https://www.fastmail.com/dev/)] | Fastmail paid plans | Token can be scoped read-only at creation (Fastmail UI; [unverified] for scope granularity) | One HTTP stream per account |
| **Gmail API** | `users.watch` publishes to a Cloud Pub/Sub topic; renew at least every 7 days (daily recommended); at most one notification per second per user; notifications carry only a `historyId`, may be delayed or lost, so poll as well [verified, [push](https://developers.google.com/workspace/gmail/api/guides/push)]. A **pull** subscription is client-initiated, so no public endpoint is needed [verified, [Pub/Sub pull](https://docs.cloud.google.com/pubsub/docs/pull); inference that it suits a home box]. Quota: `history.list` 2 units, `messages.get` 20, per-user 6,000/min [verified, [quota](https://developers.google.com/workspace/gmail/api/reference/quota)] | **The hard path.** `gmail.readonly`, `gmail.metadata`, `gmail.modify` and `https://mail.google.com/` are **restricted** [verified, [scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)]. A restricted-scope app needs verification and a yearly security assessment (CASA), with exceptions for **personal use** ("only user or a few known users") and internal use [verified, [restricted-scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)]. In **Testing**, refresh tokens expire after 7 days; in **Production unverified**, users see a warning screen and the app is capped at 100 new users for life; Internal needs a Cloud Organization and rejects `@gmail.com` [verified, [Cloud help](https://support.google.com/cloud/answer/15549945)]. No device flow for Gmail, so web OAuth via the tunnel hostname or an HA-style redirector | Pub/Sub free tier 10 GiB/month [verified, [pricing](https://cloud.google.com/pubsub/pricing)]; whether a billing account is needed: [unverified]; CASA from about US$540 at one authorised lab [secondary, TAC Security] | Every notification passes through Google Pub/Sub (Google already has the mail); the household's own Cloud project holds the client | HTTP polling; no resident socket beyond Pub/Sub StreamingPull |
| **Gmail via IMAP + XOAUTH2** | as IMAP IDLE | Needs the restricted `https://mail.google.com/` scope [verified, [XOAUTH2](https://developers.google.com/workspace/gmail/imap/xoauth2-protocol)], so the same verification wall as the API | - | - | as IMAP |
| **Gmail app password** | as IMAP IDLE | Requires 2-Step Verification; unavailable with security-key-only 2SV, work/school accounts or Advanced Protection; Google calls them "unnecessary in most cases" and "not recommended" [verified, [app passwords](https://support.google.com/accounts/answer/185833)] | free | Full-mailbox credential; revocable in the Google account | as IMAP |
| **Microsoft Graph** (Outlook.com, Microsoft 365) | Delta query on a folder with a saved `deltaLink` (also returns read-state changes and removals) [verified, [message delta](https://learn.microsoft.com/en-us/graph/api/message-delta), 2026-06-19]; change notifications need a public `notificationUrl`, live at most 10,080 minutes (1,440 with resource data), average latency under a minute [verified, [change notifications](https://learn.microsoft.com/en-us/graph/change-notifications-overview), 2026-04-07] | **Device code** with `Mail.ReadBasic` (least privilege, no body) or `Mail.Read` [verified]; Outlook.com IMAP "requires the use of Modern Auth / OAuth2" [verified, [Outlook.com settings](https://support.microsoft.com/en-us/office/pop-imap-and-smtp-settings-for-outlook-com-d088b986-291d-42b8-9564-9c414e2aa040)]; the device grant is documented for IMAP with `IMAP.AccessAsUser.All` over XOAUTH2 for both M365 and Outlook.com [verified, [Exchange OAuth](https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth)]; Basic auth is off in every work/school tenant [verified, 2026-07-16]; tenant admins may block device-code flow [secondary] | free (the household registers a public-client app in Entra) | `Mail.ReadBasic` lets triage run on headers without bodies | HTTP polling |

**Client libraries** (GitHub API, 2026-10-14): `imap-tools` (ikvk/imap_tools, Apache-2.0, 847 stars,
pushed 2026-10-08; synchronous, built on stdlib `imaplib`, IDLE support per its docs [secondary]) and
`aioimaplib` (bamthomas/aioimaplib, **GPL-3.0**, 170 stars, pushed 2026-10-13, PyPI 2.0.3; asyncio, IDLE).
The GPL licence of the async option needs a check against this public repo's licence before adoption; the
build PR picks one by a small bake-off (RSS per idle connection, reconnect behaviour, `BODY.PEEK` support).

**Email verdict.** A thin in-process IMAP watcher (plus JMAP for Fastmail and Graph delta for Microsoft)
covers every household provider with outbound-only connections. For Gmail, an **app password** is the
working path today; the API is possible only as a household-owned project published in Production under
Google's personal-use exception (warning screen, under 100 users), which is an owner decision because it
means a Google Cloud project and a scary consent screen. HA's IMAP integration does IDLE but has no OAuth
and is household-scoped (section 3.3).

### 4.2 Telegram

- **Bot API** (what Zoe runs): a bot sees its own private chats and, in groups with privacy mode on, only
  commands and replies to it; `getUpdates` and webhooks are exclusive; about 1 message/s per chat and 30/s
  overall [verified, [bots FAQ](https://core.telegram.org/bots/faq)]. It can never see a member's other chats.
- **Telegram Business "chatbots"**: Bot API updates `business_connection`, `business_message`,
  `edited_business_message`, `deleted_business_messages` for "a connected business account" [verified,
  [Bot API](https://core.telegram.org/bots/api), Bot API 10.0, 2026-05-08]. The account owner adds the bot
  under Settings > Business > Chatbots, which **needs Telegram Premium** [secondary, vendor pages]. This is
  the **only sanctioned way for a bot to see a person's private chats**, it reuses Zoe's existing bot and
  long-poll sidecar (add the update types to `allowed_updates`), and the member opts in from their own phone.
  Replies would go out under the member's name, so the design keeps it read-only.
- **User account over MTProto** (Telethon, TDLib): QR login is native: `auth.exportLoginToken` gives a
  token that expires in about 30 seconds, rendered as `tg://login?token=...`, accepted on the phone, refreshed
  until accepted [verified, [QR login](https://core.telegram.org/api/qr-login)]; TDLib exposes the same as
  `authorizationStateWaitOtherDeviceConfirmation` [verified, `td_api.tl`]. Terms: each app needs its own
  `api_id`; accounts using unofficial clients "are automatically put under observation"; acting for users
  without their knowledge or consent is prohibited [verified, [API terms](https://core.telegram.org/api/terms)].
  **API Terms 1.5 prohibits using Telegram data to train or develop AI/ML models** [verified]; summarising
  for the member is not obviously training, but it is a clause to read carefully **[LAWYER]**. Libraries:
  TDLib (BSL-1.0, build only `tdjson` on small devices) [verified, [repo](https://github.com/tdlib/td)];
  Telethon 1.45.0 on PyPI [verified, version]; Pyrogram archived 2024-12-23 [verified].
- **Verdict:** keep the bot for talking to Zoe; for watching, prefer the **Business connection** of the
  existing bot (no new process, sanctioned, needs Premium); the MTProto user lane only if the member has no
  Premium and the owner accepts a session that is full account access.

### 4.3 WhatsApp

- **Cloud API** (official): business numbers, Meta Business account, per-template pricing since 2025-07-01,
  free non-template replies inside the 24-hour service window; Australia sits in "Rest of Asia Pacific" and
  an AUD rate card exists only as a download (rates not retrieved) [verified, [pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)].
  It cannot read a person's personal WhatsApp. **Meta's terms (last modified 2026-09-23) 4.7: AI providers
  are "strictly prohibited" when AI is "the primary (rather than incidental or ancillary) functionality"**
  [verified, [terms](https://www.facebook.com/legal/Meta-Terms-for-WhatsApp-Business-Platform)]; the
  2026-01-15 effective date is press-reported [secondary, [respond.io](https://respond.io/blog/whatsapp-general-purpose-chatbots-ban)].
  A general household assistant is the target of that clause. Coexistence onboards WhatsApp *Business app*
  numbers only, not personal numbers [secondary].
- **Unofficial linked device**: `whatsmeow` (Go, MPL-2.0, QR pairing via `GetQRChannel` or a pair code,
  untagged pseudo-version 2026-10-13) [verified, [repo](https://github.com/tulir/whatsmeow)];
  `mautrix-whatsapp` (AGPL-3.0, needs a Matrix homeserver, too heavy here) [verified]; Baileys (JS) is "not
  affiliated with WhatsApp", "use at your own discretion" [verified, [repo](https://github.com/WhiskeySockets/Baileys)];
  OpenClaw uses Baileys with QR-only login [verified]. **Ban risk**: no primary Meta statement found; vendors
  who sell the official API report bans, mostly tied to proactive messaging [secondary, biased]. A passive
  read-only linked device is plausibly lower risk, unproven. The 2025 WhatsApp-MCP exfiltration demo
  (section 2.4) is the security case for keeping it read-only.
- **Verdict:** no official route exists for the ask. Offer it only with a written owner yes, per member,
  read-only, whatsmeow in a capped sidecar.

### 4.4 Phone and SMS (Australia)

Calls stay with the action-agent record's Phase 5 (carrier eligibility, the WA Surveillance Devices Act).
For *watching* SMS:

- **Twilio AU**: outbound SMS 0.0515, inbound 0.0075, a mobile number 8.25/month (currency not shown on the
  page) [verified, [pricing](https://www.twilio.com/en-us/sms/pricing/au)]; alphanumeric sender IDs are
  send-only and must be pre-registered [verified, [guidelines](https://www.twilio.com/en-us/guidelines/au/sms)];
  the AU number rules say business use only (action-agent record section 3.1).
- **ACMA SMS Sender ID Register**: live, enforcement from 2026-07-01; unregistered *alphanumeric* senders
  are labelled "Unverified"; registration needs an ABN [secondary, ACMA page timed out]. Irrelevant for a
  receive-only long number.
- **ClickSend** (Australian) says inbound replies are free [verified]; prices did not render.
- **SMS Gateway for Android** (capcom6, Apache-2.0): a local-server mode on the phone with an
  `sms:received` webhook (sender, text, SIM, time), or a private self-hosted server [verified,
  [repo](https://github.com/capcom6/android-sms-gateway)]. **The private option**: a spare phone and SIM,
  no carrier API account, nothing in a vendor cloud. It sees every SMS on that SIM, including bank codes,
  so it must be a dedicated number, never a member's main SIM by default.
- A USB 4G modem with ModemManager or gammu works on Linux but has known receive-state bugs [secondary].
- SIP for a later inbound phone line: Crazytel offers registration-based SIP credentials [secondary];
  Asterisk RAM is anecdotal (about 60 MB at start, one report of a leak) [secondary].

### 4.5 Calendars

- **CalDAV** with `sync-collection` (RFC 6578): a sync token per collection, server may invalidate it and
  force a resync [verified, [RFC 6578](https://www.rfc-editor.org/rfc/rfc6578)]; cheap to poll every few
  minutes. iCloud uses app-specific passwords (two-factor required, at most 25, all revoked on an Apple
  password change) [verified, [Apple](https://support.apple.com/en-us/102654)]; Fastmail CalDAV with an app
  password [verified].
- **Google Calendar**: `events.watch` needs an HTTPS endpoint and manual renewal [verified,
  [push](https://developers.google.com/workspace/calendar/api/guides/push)]; polling with `syncToken` (a 410
  means full resync) needs nothing public [verified, [sync](https://developers.google.com/workspace/calendar/api/guides/sync)].
  `calendar.readonly` is a **sensitive**, not restricted, scope (lighter verification, no CASA)
  [secondary, sensitive-scope guide]. No device flow for Calendar (above), and HA's own Calendar
  integration says Device Auth "still works" but is no longer recommended [verified].
- **Microsoft**: `Calendars.Read` delegated for personal accounts, delta or notifications [verified].
- **Home Assistant** already has Google Calendar and CalDAV integrations (section 3.3): the cheapest adopt.

### 4.6 Signal

- `signal-cli` (GPL-3.0): `link` registers it as a secondary device (a `tsdevice:` URL to render as a QR),
  daemon mode speaks JSON-RPC; needs JRE 25, releases older than three months may stop working; GraalVM
  native build is "EXPERIMENTAL" [verified, [repo](https://github.com/AsamK/signal-cli)].
  `signal-cli-rest-api` (MIT, Docker) has `json-rpc` and `json-rpc-native` modes and a ready QR endpoint
  `/v1/qrcodelink`; its REST `receive` consumes messages, so the daemon modes are the safe ones [verified,
  [repo](https://github.com/bbernhard/signal-cli-rest-api)].
- Terms prohibit "unauthorized" use, automated account creation and bulk/auto-messaging [verified, summary
  of [signal.org/legal](https://signal.org/legal/)]; a read-and-notify linked device is a grey area; no
  enforcement reports found.
- **Verdict:** the most privacy-aligned messenger and a natural second channel to talk to Zoe; the cost is a
  JVM (or experimental native) sidecar whose RAM must be measured, and the "update within three months"
  maintenance tax.

### 4.7 Discord

Bots see only servers they are invited to and cannot read user-to-user DMs; message content is a
privileged intent (free below 100 servers) [verified, [gateway](https://docs.discord.com/developers/events/gateway)];
self-bots are prohibited and risk account termination [secondary, support article 403]. **Verdict:** not
worth an ability for a household; revisit only if a member runs a server they want watched.

### 4.8 Summary

| Service | Watch (outbound-only) | Auth method (design 4.2) | Main blocker |
|---|---|---|---|
| Gmail | IMAP IDLE | C app password; B for the API only as a personal-use project | restricted scopes, 7-day Testing tokens |
| Outlook / M365 | Graph delta or IMAP IDLE with XOAUTH2 | A device code | tenant admins can block device code (work accounts) |
| Fastmail | JMAP EventSource | C (API token or app password) | Basic plan has no IMAP |
| iCloud, other IMAP | IMAP IDLE | C | long-lived app password |
| Calendars | CalDAV sync-collection, Google `syncToken`, Graph delta, or HA | HA's flow, C, A | Google push needs a public endpoint (avoid) |
| Telegram (watch) | Bot API long-poll with Business updates | E plus the member's in-app opt-in | needs Premium; API Terms 1.5 |
| WhatsApp | whatsmeow linked device | D | Meta terms 4.7 and unofficial-client ban risk |
| SMS | local Android gateway webhook on the LAN | phone pairing | a phone that stays on; sees every SMS on that SIM |
| Signal | signal-cli JSON-RPC daemon | D | RAM (unmeasured), JRE 25, update cadence |
| Discord | bot gateway | E-like | sees only invited servers |


## 5. Connector-layer bake-off (owner follow-up, 2026-10-14)

Owner, on the first draft: *"there are projects and agents that have connectors galore out there, and I'm
sure even Flue has a bunch of them"*, and *"if openclaw and hermes can do it and flue has
[its ecosystem](https://flueframework.com/docs/ecosystem/) then we should be able to do heaps."* He is
right. This section is the head-to-head. Desk research only: nothing was installed or run on the Jetson,
and **every RAM figure is from upstream docs or issues and unmeasured on our box** (most upstreams publish
none). Pins: Flue 2.1.1 (opensrc cache; npm `latest` is 2.3.0), Activepieces 0.92.3, OpenClaw v2026.10.3,
Hermes Agent v0.21.7, mautrix bridges v0.2609.0, mautrix-go v0.31.0, Apprise 2.0.1, imap-tools 1.15.1.
Where `opensrc` hit GitHub's anonymous rate limit, the same tagged files were read through `gh api
.../contents?ref=<tag>` or the tagged tarball.

### 5.1 What Zoe CAN do: two different jobs, both large

There are two jobs here, and the field has a strong answer for each.

1. **"Talk TO Zoe on X."** A member messages Zoe from an app they already use, and Zoe answers there. This
   is what Flue's 17 channels, OpenClaw's 20-odd channels and Hermes' gateway platforms are. Zoe's brain
   already runs on Flue 2.x and her Telegram lane is live, so **every Flue channel below is a near-free
   adoption**: a verified webhook, an official SDK and Zoe's existing brain call.
2. **"Zoe WATCHES my own account."** Zoe reads the member's personal inbox, personal WhatsApp or Telegram
   chats, and tells them what matters. No Flue channel does this: a channel is a *bot or business
   identity* that people message. Watching a personal account needs an IMAP/Graph client or a
   linked-device bridge. The bridges exist and are well maintained (5.4).

**Talk to Zoe: what each Flue channel needs** (blueprints and packages read at 2.1.1; every Flue channel
package is a webhook verifier of roughly 100-400 lines, and the outbound SDK, tools, dedup and identity
mapping are the application's job by Flue's own contract, `blueprints/README.md` lines 101-112 [verified]):

| Channel (Flue package) | What a member does | What the household needs | Fit and cost |
|---|---|---|---|
| **Telegram** | messages Zoe's bot | exists | **Live** (long-poll sidecar, not the webhook package) |
| **WhatsApp** (`@flue/whatsapp`, Meta Cloud API) | messages Zoe's WhatsApp *business* number from their own WhatsApp | a dedicated number not on consumer WhatsApp, a Meta Business account, an access token | Replies inside the 24-hour window after the member writes are free; templates (proactive) are billed per message [verified, Meta pricing]. **Meta's Business terms 4.7 bar AI providers whose AI is the primary function** [verified] - an owner and lawyer question, not a technical one |
| **SMS** (`@flue/twilio`, messaging only - the blueprint has no voice [verified]) | texts Zoe's number | a Twilio AU number (US$8.25/month, SMS out 0.0515, in 0.0075 [verified]); Twilio's AU rules say business use only (action-agent record 3.1) | Works with any phone, no app |
| **Email to Zoe** (`@flue/resend`) | emails Zoe's address | a domain with Resend receiving (MX) and the official `resend` SDK [verified] | Hosted (US) provider; this is the same "Zoe's own mailbox" question as action-agent Phase 3 |
| **Slack** (`@flue/slack`, Events API) | DMs or mentions Zoe in the household workspace | a Slack app in a workspace the household owns | Free; Slack's Socket Mode (outbound WebSocket) would avoid the public webhook but is not in Flue's blueprint |
| **Discord** (`@flue/discord`) | uses Zoe's **slash commands** | a Discord application | The blueprint is HTTP *interactions* only and says "Do not add Discord Gateway" [verified]: no plain DMs without a separate gateway client |
| **Messenger** (`@flue/messenger`) | messages a Facebook Page | a Page and a Meta app (Page access token) | Meta app review before non-admins can message [secondary] |
| **Teams**, **Google Chat** | messages a bot | an Azure Bot registration; a Google Workspace domain | Work-account products; low household value |
| GitHub, Linear, Notion, Intercom, Zendesk, Shopify, Stripe, Salesforce | - | - | Business tools; not household channels |

**Constraints that apply to all of them**, and how to meet them:

- **Public webhook ingress.** Every Flue channel is called by its provider. Zoe already runs a Cloudflare
  tunnel (`zoe-cloudflared`) with Cloudflare Access in front of the household hostnames
  (`docs/knowledge/mac-virtual-panel.md`). The fit is one public hostname that routes **only**
  `/channels/<provider>/*` to a channels sidecar, with an Access bypass for those paths alone (the provider
  cannot pass an Access login; each package verifies the provider's signature over the exact body
  instead). Nothing else is exposed.
- **Identity.** A message from an unknown sender is a guest. A member links each channel once, exactly as
  Telegram linking works today (`telegram_link.py`: a signed single-use token in a deep link or QR, the
  provider's verified sender id stored on the member). OpenClaw's default for unknown senders is a pairing
  code the owner approves (`dmPolicy: pairing`) [verified]; Hermes does the same (`gateway/pairing.py`,
  files `chmod 0600`, codes never logged) [verified]. Zoe should copy that default.
- **Deduplication** is the application's job (Flue's Telegram package: "does not deduplicate
  `update_id` values" [verified]): one `channel_deliveries` table keyed by the provider delivery id.
- **One process, not one per channel.** Generalise the live Telegram sidecar into a channels sidecar that
  mounts several Flue channel packages; adding a channel is a route and a client, not a new service.

### 5.2 How OpenClaw and Hermes got so many channels

Both did it the same way: **one adapter per network over a maintained client library**, inside their own
gateway. Neither has a magic connector layer.

- **OpenClaw** (MIT, `openclaw/openclaw` v2026.10.3) [verified]: `extensions/whatsapp` (Baileys 7.0.0-rc14,
  WhatsApp Web linked device, ~36k lines with tests, 197 files importing `openclaw/plugin-sdk`),
  `extensions/telegram` (grammY), `extensions/signal` (signal-cli daemon or `signal-cli-rest-api`),
  `extensions/sms` (Twilio webhook), `extensions/imessage` (macOS / BlueBubbles), `extensions/imap`, plus
  Discord, Slack, Matrix, Teams, Google Chat and others. Gmail and Calendar are *skills* wrapping CLIs
  (`skills/gog`, `skills/himalaya`), not channels. The adapters are tightly bound to the plugin SDK. The
  exception is the **IMAP watcher**: `extensions/imap/src/watcher.ts` (428 lines, opens the mailbox
  `readOnly: true`, IDLE with poll fallback) and `src/sender-gate.ts` (217 lines, SPF/DKIM/DMARC via
  `mailauth`), with about six SDK touch-points.
- **Hermes Agent** (MIT, `NousResearch/hermes-agent` v0.21.7, Python) [verified]: `plugins/platforms/`
  (telegram on python-telegram-bot, discord on discord.py, slack on slack_bolt Socket Mode, whatsapp via a
  Baileys Node bridge subprocess, sms on Twilio REST, **email on stdlib `imaplib`/`smtplib`**) and
  `gateway/platforms/` (`signal.py` on httpx against signal-cli, `whatsapp_cloud.py`, `bluebubbles.py`).
  The email adapter peeks headers with `BODY.PEEK`, drops bulk senders (`List-Unsubscribe`,
  `Precedence`, `Auto-Submitted`) and **requires authenticated senders by default** (topmost
  `Authentication-Results` pinned to an operator `authserv-id`; advisory GHSA-rxqh-5572-8m77). It is a
  conversational gateway that replies by SMTP, not a read-only watcher.

**What to reuse:** both are MIT, so code can be borrowed with attribution. Hermes' email sender-auth and
header logic is Python and drops straight into a zoe-data adapter; OpenClaw's IMAP watcher and sender gate
are the reference design (TypeScript). Their big messaging adapters are not worth lifting: they are
thousands of lines bound to their gateways, and for the *watch* job the mautrix bridges (5.4) are the
better-maintained source of the same capability.

### 5.3 The other candidates, head to head

| Candidate | What it really is (pinned source) | Local-first | Per-member isolation | RAM (upstream, unmeasured) | Verdict |
|---|---|---|---|---|---|
| **Flue channels** (2.1.1) | Webhook ingress verifiers; no outbound client, no polling, no OAuth, no email/IMAP watcher, no calendar. "Connectors" on the site is an alias of blueprints (`apps/www/src/pages/cli/connectors/[name].md.ts` re-exports the blueprints route). npm `@flue/connectors@0.3.7` exports only a Daytona sandbox adapter [verified] | Yes (verification on the box) | App's job | Part of one Node process | **Adopt for "talk to Zoe"** (5.1) |
| **Activepieces CE** 0.92.3 | 733 community pieces incl. gmail (polling triggers), imap (polling, 5-minute default), google-calendar, microsoft-outlook, telegram-bot, whatsapp, slack, discord, twilio; no CalDAV, no Signal. Its MCP server (`/mcp`, stateless streamable HTTP) is a *flow builder*; `ap_run_action` runs one piece action with a stored connection [verified] | **No for Google-class OAuth**: the default `CLOUD_OAUTH2` connection posts the auth code, and later the **refresh token**, to `https://secrets.activepieces.com/claim` and `/refresh` (`cloud-oauth2-service.ts`); bring-your-own OAuth app is an Enterprise feature per its docs; piece catalogue syncs from `cloud.activepieces.com` hourly by default [verified] | Connections are project-scoped; RBAC, global connections and project structuring are Enterprise [verified] | Docs: at least 2 vCPU and 4 GB; default compose runs app + 5 workers + Postgres + Redis; a single-container PGlite mode exists but "cannot scale" [verified docs] | **Avoid.** Tokens transit its cloud, isolation is paid, and it is the biggest process we could add |
| **Klavis** (Apache-2.0) | ~100 MCP servers; Gmail server is Node, takes an access token per request (`x-auth-data`), no refresh, **no read-only mode**, OAuth via Klavis' hosted broker; last push 2026-06-01 [verified] | Partly | Header per request | Node per server | **Avoid**; borrow Strata's progressive-disclosure idea only |
| **Official MCP servers** | Reference implementations "not production-ready"; no email, calendar or messaging server ever shipped (gdrive and slack were archived 2025-05-28) [verified] | - | - | - | **Avoid** for connectors |
| **Smithery, mcp-get, awesome lists** | Smithery = hosted runtime (CLI AGPL-3.0; a 2025 build-pipeline token exposure [secondary]); mcp-get deprecated; awesome lists unvetted (thousands of entries) [verified] | No (Smithery) | - | - | **Search index only** |
| **Best single MCP servers** | `Wh1isper/mcp-email-server` (BSD-3: per-account grants `draft/organize/delete/send/append`, recipient allowlists, keyring with a plaintext-TOML fallback); `google_workspace_mcp` (MIT: `--read-only`, real multi-user OAuth 2.1); `chigwell/telegram-mcp` (session = full account) [verified] | Yes | Mixed | A process each | **Borrow the policy designs**, do not run as processes |
| **LlamaHub / LangChain loaders** | One-shot loaders; Gmail/Calendar readers use the quickstart `token.json` in the working directory (plaintext) and a local-server login; heavy framework dependencies (`langchain-google-community`: 42 requirements) [verified] | Yes | No | Heavy | **Avoid** |
| **Apprise** 2.0.1 (BSD-2) | Send-only library, ~150 services incl. Telegram, SMTP, Signal (via signal-cli-rest-api), WhatsApp Cloud, ClickSend, Twilio, ntfy, Matrix; `N_MGR.enable_only(*schemas)` allowlists schemes; URLs carry secrets, redacted in `url(privacy=True)` [verified] | Yes (library) | Per URL | Light deps (requests, PyYAML, markdown...) | **Adopt** for notifications-out, behind approval |
| **Home Assistant** | IMAP sensor with IDLE (no OAuth), Google Calendar via application credentials and the `my.home-assistant.io` redirect, CalDAV [verified, section 3.3] | Yes | **No** (one household event bus) | Already running | **Keep where HA already owns it** (shared household calendars); not for personal inboxes |
| **IMAP and API libraries** | `imap-tools` 1.15.1 (Apache-2.0, **zero dependencies**, IDLE via `imap_tools/idle.py` `IdleManager`, blocking so run in a thread); `aioimaplib` 2.0.3 (GPL-3.0); `google-api-python-client` (Apache-2.0, five deps); `caldav` 3.4.0 (python-caldav) [verified] | Yes | Ours (vault per member) | In-process | **Adopt** as the email/calendar watcher base |
| **rclone** 1.75.2 (MIT) | Drive, OneDrive, Nextcloud/WebDAV; `--read-only` mount and serve; `drive.readonly` scope; config encryption [verified docs] | Yes | One config per member | A Go subprocess per call | **Adopt** for docs/files, as a short-lived subprocess |

### 5.4 Chat networks you watch: Matrix + mautrix bridges

This is the "connectors galore" answer for messaging, and it is a good one.

- **The bridges** (`github.com/mautrix/*`, AGPL-3.0, all on the shared Go **bridgev2** framework in
  `mautrix/go` v0.31.0, MPL-2.0) [verified at v0.2609.0, 2026-09-16 unless noted]:

  | Bridge | Login | Notes from its own docs | Maturity |
  |---|---|---|---|
  | whatsapp (whatsmeow) | QR or phone pairing code | "WhatsApp is known to ban accounts that are too suspicious"; risk comes from emulators, VoIP numbers, messaging strangers; the phone must come online every ~2 weeks | High |
  | telegram (now Go, gotd) | phone + code, QR, or bot token; own `api_id` | established accounts "perfectly safe" | High |
  | signal (libsignal via CGO) | linked-device QR | one-time history transfer; docs silent on bans | Medium-high |
  | gmessages (Google Messages SMS/RCS) | Google cookies + phone confirmation | the phone must stay online | Medium |
  | meta (Messenger, Instagram) | cookies or password modes | "Meta may decide your account has suspicious activity and block you" | Medium, least stable |
  | discord (legacy, v0.7.7) | QR, user token or bot | a user token is a self-bot in practice | Medium |
  | slack (v0.2609.1) | browser tokens or app tokens | admins may see it | Medium |
  | gvoice (v0.2605.0) | Google login | small repo | Low-medium |
  | imessage | needs a Mac | not usable on this box | - |

  No bridge or the bridgev2 setup page documents RAM [verified]. The bridge itself is the network client,
  running on the box.
- **The homeserver.** For one household on ARM64 the pick is **Tuwunel** (Apache-2.0, v1.9.4 2026-10-10,
  aarch64 assets, "official successor to conduwuit", funded by the Swiss government, MSC4190 marked done
  and the mautrix device-key issue #327 closed 2026-05-01) [verified]. Continuwuity (v26.9.1) is second;
  conduwuit is archived (2026-05-29); Synapse (AGPL-3.0) is the compatibility fallback and the heaviest.
  **Warning sign to measure:** Tuwunel issue #294, a user with mautrix-discord reported runaway memory on
  v1.5.0 (closed 2026-02-09) [verified].
- **Settings that make it fit Zoe** [verified in the bridgev2 example config, design inferences marked]:
  `encryption.allow: false` (everything is on one box, so end-to-bridge encryption adds Olm device
  management for no threat; rooms are then plaintext in the homeserver database, which must sit on
  encrypted disk); **`split_portals: true` before the first login** (otherwise members in the same
  WhatsApp group share one Matrix room, which breaks per-member isolation; the config calls changing it
  later "IRREVERSIBLE AND POTENTIALLY DESTRUCTIVE"); one Matrix account per member; relay mode off (the
  default); backfill off (the default). **There is no read-only mode**: a Matrix user with no bridge login
  and no relay cannot send (`bridgev2/portal.go` returns `ErrNotLoggedIn`) [verified], and Zoe's reader
  token should additionally pass through a small allow-list proxy that permits `/sync` and message reads
  and denies `PUT .../send` [design, unverified].
- **How Zoe reads it:** `mautrix-python` 0.21.1 (MPL-2.0, async, fits zoe-data) as a client with one
  read-only token; `matrix-nio` is a revived but lightly maintained alternative [verified].
- **Not useful here:** Beeper's `bbctl` (Apache-2.0) only works against Beeper's hosted homeserver ("can
  not be used with any other Matrix homeserver") [verified], so messages leave the box; matterbridge is a
  relay, last released 2023; Element Server Suite is sized for Kubernetes.
- **Versus direct libraries** (whatsmeow alone, Telethon, signal-cli - whose README bundles libsignal only
  for x86_64 Linux, so ARM64 needs our own build [verified]): direct is lighter for one or two networks;
  the homeserver amortises from about three or four, and every network then arrives in one event shape
  with maintained reconnect, media and catch-up. The deciding unknown is Tuwunel's resident memory.

### 5.5 Ranked recommendation per service class

| Class | 1st (adopt) | Runner-up | Deciding reason |
|---|---|---|---|
| **Talk to Zoe on chat apps** | **Flue channel packages** in one channels sidecar (WhatsApp Cloud number, Twilio SMS, Slack, Discord slash commands, Resend email-to-Zoe), behind a tunnel path with signature verification and the Telegram-style link flow | Hermes / OpenClaw adapters as code to borrow (MIT) | Zoe already runs Flue; each channel is a verified route plus an official SDK, no new runtime |
| **Watch my email** | **Thin in-process adapter on `imap-tools`** (IDLE, `BODY.PEEK`) + Graph via device code for Microsoft; borrow Hermes' sender-auth (MIT), OpenClaw's read-only IDLE design, `mcp-email-server`'s per-account grants | Home Assistant IMAP for the owner's mailbox only | No maintained service keeps tokens per member in our vault; the maintained component *is* the library. Activepieces sends Google refresh tokens to its cloud; MCP servers each bring their own token store and process |
| **Calendars** | **HA** for calendars the household already keeps in HA (shared, household-scope); **in-process `caldav` / Google Calendar API (`calendar.readonly`) / Graph** for a member's personal calendars | Activepieces calendar pieces (rejected on the same cloud-OAuth ground) | Isolation: HA is one household bus, so personal calendars stay per member in zoe-data |
| **Chat networks I watch** (WhatsApp, Signal, SMS/RCS via Google Messages, Messenger) | **Tuwunel + mautrix bridgev2**, encryption off, `split_portals`, read-only proxy, read via mautrix-python - **gated on a lab RSS measurement** | Direct per-network sidecars (whatsmeow, signal-cli) | Maintained weekly upstream, one event shape for every network; direct wins only if the household wants one or two networks or the homeserver's memory fails the gate |
| **Telegram I watch** | **Business connection of Zoe's existing bot** (no new process; needs Premium) | mautrix-telegram on the homeserver above | Sanctioned by Telegram and costs nothing extra to run |
| **SMS** | Talk to Zoe: **Flue Twilio channel**. Watch my SMS: **mautrix-gmessages** if the homeserver is adopted | Watch: SMS Gateway for Android (capcom6, Apache-2.0) on a spare phone in local mode | gmessages reuses the chat stack; the gateway phone avoids any Google pairing |
| **Voice calls** | Not a connector: action-agent Phase 5 (Twilio Media Streams over the tunnel, or LiveKit SIP). Flue's Twilio channel is messaging only | - | Legal and carrier eligibility come first |
| **Notifications out** | **Apprise** in zoe-data with `enable_only(...)`, URLs in the vault, every send behind the approval object | Zoe's own push, bell and Telegram send (exist) | One maintained library covering ~150 services with light dependencies; no daemon |
| **Docs and files** | **rclone** read-only, per-member config, short-lived subprocess | Provider APIs directly (Drive API, WebDAV) | One maintained tool with read-only flags and scoped OAuth across Drive, OneDrive and Nextcloud |

## 6. What Zoe has today (read-only survey, HEAD b02a04a7b, 2026-10-14)

Line numbers are from that HEAD and will drift. Defaults quoted are code defaults
([flag-inventory.md](../knowledge/flag-inventory.md)), not the live `.env`; nothing live was queried.

| Area | What exists | Gap for connected accounts |
|---|---|---|
| **"QR on the panel, finish on the phone" engine** | `services/zoe-data/auth_handoff.py` (443 lines): handoff record, single-use QR handle (`setup_qr`), status `pending > awaiting_phone > completing > done/error`, live push to the panel (`_notify_panel`, `_wake_panel`), Telegram "send to my phone" (`send_to_phone`). The phone URL keeps the provider token in its `#fragment` and in process memory only. Routes in `routers/handoff.py` (`/start`, `/{id}/status`, `/{id}/send-to-phone`, `/{id}/qr/{handle}`). One shared copy of the signed single-use token mechanics (`SignedTokens`, `SingleUseLedger`) for music, smart-home and Telegram linking | Exactly one registered kind: `_KINDS = {"music": ...}` (`:147`). Each new auth method is a new kind plus a phone page; the engine itself is reusable |
| **Reference auth flow: Music Assistant** | `music_setup.py` (15-minute single-use token, `ZOE_MUSIC_SETUP_TTL_S`), `routers/music_setup.py` (phone form `/form` + `/save`, `/oauth/start` + `/oauth/status`, a browser sign-in path), `music_oauth.py`: MA runs the provider OAuth through its own hosted callback; "No secret ever leaves the LAN - Zoe only relays the URL and the token goes straight into MA" (module header) | The organ (MA) holds the token, not Zoe. That is the ideal; where no organ exists, Zoe needs her own vault |
| **Telegram** | Bot sidecar `labs/flue-zoe-telegram-2x` (`flue-zoe-telegram.service`, memory-capped per its unit comments), long-poll with `allowed_updates: ['message']` (`src/app.ts:201`). Member linking: `telegram_link.py` (stateless HMAC link token, `t.me/<bot>?start=<token>`, the verified sender id stored on the member's profile, `routers/user_profile.py:106-121`); desktop Settings > Telegram shows the QR (`services/zoe-ui/dist/settings.html:1011-1037`) | A way to talk to Zoe, not to watch a member's chats. No `callback_query` (approvals, action-agent Phase 2) and no `business_*` updates (section 4.2) |
| **Home Assistant** | `services/homeassistant-mcp-bridge/main.py` (FastAPI: entities, devices/control, automations, scenes, scripts, `/voice/*`), `ha_tool_names.py` (HA 2026.9 domain-prefixed tool names); zoe-data drives HA through the `smart_home` intent | No calendar, IMAP or notify use of HA from Zoe; no HA event subscription (`subscribe_events` appears nowhere in zoe-data or the bridge) |
| **zoe-data's own MCP server** | `services/zoe-data/mcp_server.py` (3,493 lines): a **stdio** MCP server "that Hermes skills and local agents can call"; tools include `panel_announce` (`:830`, handler `:2799`); `ZOE_MCP_STRICT_USER_ID` default false (falls back to family-admin with a warning) | Zoe exposing tools outward. Nothing in zoe-data is an MCP **client** |
| **Brain tool model** | `labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts`: 22 always-registered tools (`zoeTools`, `:1255`) plus flag-gated `web_search`; identity bound per turn in trusted code; writes go through `POST /api/system/intent-dispatch` against the closed allowlist `_DISPATCHABLE_INTENTS` (`services/zoe-data/routers/system.py:2772`), dry-run unless `ZOE_BRAIN_ALLOW_WRITES`. Progressive disclosure in `src/tools/tool-groups.ts`: always-on core (`get_time`, `recall_memory`, `activate_abilities`), static `TOOL_GROUPS` (`:135`: weather, lists, timers, reminders, calendar, notes, journal, people, media, home, memory) with `GROUP_PURPOSES` (`:160`) and keyword triggers; `activate_abilities` (`zoe-tools.ts:1110-1150`) is a picklist over the group names. Disclosure filters the wire copy of `context.tools`; every tool stays registered | Groups are compile-time; a new ability is a new static group plus a zoe-data endpoint. Nothing reads per-member connection state |
| **Untrusted content (W15)** | `labs/flue-zoe-brain-2x/src/untrusted-content.ts`: fencing (delimited block, preamble, `<`/`>` stripped, length caps) and a per-turn taint that makes state-changing tools and `web_search` refuse for the rest of the turn | `UntrustedSource = 'web'` only (`:33`). Email and messages need their own sources and fixtures (gap G31, W15.1) |
| **Proactive and notifications** | `proactive/engine.py`: `fire_notification` (`:80`) with quiet hours (`ZOE_QUIET_START_HOUR` 22, `ZOE_QUIET_END_HOUR` 7) and push; `proactive/pull.py` "pull, not push" (`ZOE_PULL_NOT_PUSH`, default on: an orb "there's something" state and a "what's up?" tier; restrained items held off the spoken lane for an unverified speaker); `proactive/selector.py` caps (`ZOE_PROACTIVE_RAISE_PER_DAY` 2, `ZOE_PROACTIVE_RAISE_GAP_S` 7200); `brief_first_turn.py` (`ZOE_BRIEF_ON_FIRST_TURN`: the [Today] block on the first turn of the morning); web push `routers/push.py:103` `send_push_to_user` (VAPID); the `notifications` table and bell (`routers/notifications.py`); `panel_announce` | A watcher has three surfaces ready (pull queue, [Today] brief, notification); it only writes candidates into them |
| **Night window** | The 12B window stops zoe-data and the 4B brain (`docs/knowledge/night-window.md`); `night_mind.py` reflects the day inside the digest | In-process watchers stop with zoe-data and must catch up from a cursor |
| **Secrets** | Service tokens are environment variables (`ZOE_TELEGRAM_BOT_TOKEN`, `MUSIC_ASSISTANT_TOKEN`, the HA token, `ZOE_INTERNAL_TOKEN`); a member's Telegram link is a numeric id in preferences; panel device tokens are stored **hashed** with scopes, expiry and revocation (`device_tokens`, `alembic/versions/0001_initial_schema.py:533`); `services/AGENTS.md` already says "credentials are per user and per scope, never in global env vars" | **No encrypted store for a reusable per-member secret.** An IMAP app password or an OAuth refresh token must be recoverable, so hashing is not enough. zoe-data pins `py-vapid==1.9.4` (which depends on `cryptography`; presence on the box [unverified]) |
| **Per-member settings** | Desktop `settings.html`: Profile, PIN, Push Notifications, Proactive Schedule, Music (`:944`), Touch Panels, Telegram (`:1011`), Bluetooth devices; `touch/settings.html` groups Music and Telegram among others | No single "Connected accounts" list; each integration has its own section and flow |
| **Roles and policy** | `guest_policy.py` fixed roles `admin`, `user`, `guest` (`:126`) and an admin-editable capability matrix (pages, features, voice intents, UI action classes; `routers/capability_matrix.py`, table `role_capability_matrix`); member modes incl. minors; `restraint.py` (health, money, family conflict, another member); forget stack (`memory_forgotten.py`, `memory_forget_cascade.py`, `user_erase_gate.py`); capability profiles (`docs/architecture/zoe-capability-profiles.md`) describe what Zoe can do and its trust level | An ability's allowed roles fit the matrix's `features`; each ability gets a capability profile; every new table needs an eraser |
| **Egress controls** | Per-feature flags (`ZOE_WEB_SEARCH_TOOL`, `ZOE_SEARCH_PROVIDER` for Tavily, `ZOE_TELEGRAM_MEDIA`); `agent_safety.assert_public_url` (`:179`) is an **SSRF** guard (blocks private targets) | No outbound allowlist and no declarative egress record; the manifest's `egress` block is the first |
| **Approvals and tiers** | Designed, not built: the `approvals` object, T0-T5 and the errand spine (PR #2011, section 6.0) | Abilities reuse it for anything T2 or above |
| **Email** | None. W9 (`docs/architecture/samantha-evolution-plan.md:745`): read-only triage into the brief, then admission, then draft-with-human-send ("Auto-send is forbidden"); W15 ships first (`:900`) | Everything |

**Prior records this builds on.** [telegram-calls-voice-photos-2026-10-04.md](./telegram-calls-voice-photos-2026-10-04.md)
(the Bot API has no calls; a Zoe *userbot* is NO-GO); [pull-not-push-inbox-2026-10-04.md](./pull-not-push-inbox-2026-10-04.md)
(the pull queue a watcher feeds); [self-building-skills-2026-10-04.md](./self-building-skills-2026-10-04.md)
(skills are markdown, code arrives by PR, the ClawHub numbers); [zoe-action-agent-2026-10.md](./zoe-action-agent-2026-10.md).

## 7. How this shaped the design

1. **Copy the converged connect pattern and run it through the existing handoff engine.** Five auth
   methods (device code, code + PKCE via a household redirect, app password on a phone form, linked-device
   QR, bot link) all fit "QR on the panel, finish on the phone" and all land in `auth_handoff.py` as kinds.
2. **One manifest per ability, data not code**: capabilities with T0-T5 tiers, consent, egress, storage,
   RAM budget and install scope (member or household) under an admin allowlist.
3. **zoe-data is the only policy point and the only MCP client.** The brain gets static, narrow, read-only
   tool groups; connection state is checked at call time, so a missing account becomes a spoken offer to
   connect it.
4. **Watchers are deterministic first**: change detection, metadata-only rows, code triage, a tool-less
   summariser for the few items that matter, then the existing pull queue, brief and notification paths.
5. **A vault with envelope encryption**, keyed outside the database; scoped, expiring, revocable, audited.
6. **Per service class** (ranked in section 5.5): Flue channels for talking to Zoe; an in-process IMAP/Graph
   adapter for email; HA for shared calendars and in-process CalDAV/Google/Graph for personal ones; a
   Telegram Business connection; Matrix (Tuwunel) + mautrix bridges for the chat networks a member wants
   watched, gated on a RAM measurement and, for WhatsApp, a written owner yes; Apprise for notifications
   out; rclone for files; calls after the action-agent legal checks.

## 8. Honest limits of this record

- Repository numbers and vendor prices move. Composio's pricing sources disagree, the WhatsApp AUD rates and
  Twilio's currency were not retrieved, and Telegram Business's Premium requirement is from vendor pages.
- No connector, bridge or watcher was run or measured on the Jetson. Every RAM statement is a vendor or
  third-party figure or "not found"; the design's lab cells exist to replace them with measurements.
- Meta's, Google's and Telegram's terms are quoted as of the access date and change often; the Telegram
  AI-training clause and WhatsApp's AI clause are flagged for a lawyer, not interpreted.
- Google's personal-use exception for restricted scopes in a Production-unverified app is inferred from the
  verification page and the consent-screen limits; Google does not state it for this exact case.
- Flue 2.1.1's compatibility with the 2026-07-28 MCP revision is unverified (it does not affect the design).
- The bake-off (section 5) is desk research. No homeserver, bridge, Activepieces or channel was run; no
  upstream publishes RAM for the mautrix bridges, and Tuwunel's only memory data point is an issue report.
  Several repos were read via `gh api` at the pinned tag because `opensrc` hit GitHub's anonymous rate limit.
- The Zoe survey is read-only at HEAD b02a04a7b; live flags were not read.

## 9. Sources

Primary pages and repositories are linked inline where they are used; access date 2026-10-14 unless a
date is shown. Pinned source on disk: `withastro/flue` tag 2.1.1 via opensrc
(`packages/runtime/src/mcp-types.ts`, `packages/runtime/src/hooks/use-mcp-connection.ts`,
`blueprints/README.md`, `blueprints/channel.md`, `blueprints/channel--whatsapp.md`,
`blueprints/channel--telegram.md`, `blueprints/channel--resend.md`, `blueprints/channel--twilio.md`,
`examples/node-schedules`). Flue docs: [tools](https://flueframework.com/docs/guide/tools/),
[MCP](https://flueframework.com/docs/guide/mcp/), [skills](https://flueframework.com/docs/guide/skills/).
MCP spec: [versioning](https://modelcontextprotocol.io/specification/versioning),
[authorization](https://modelcontextprotocol.io/specification/draft/basic/authorization),
[elicitation](https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation),
[tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools),
[registry moderation](https://modelcontextprotocol.io/registry/moderation-policy).
