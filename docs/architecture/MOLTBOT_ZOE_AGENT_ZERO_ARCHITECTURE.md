# 🏗️ Three-System Architecture: Moltbot + Zoe + Agent Zero

**Complete Architecture Overview and Integration Design**

---

## 📊 System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        USER INTERFACES                               │
│  WhatsApp │ Telegram │ Discord │ Slack │ Signal │ iMessage │ Voice  │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│                          🦞 MOLTBOT                                   │
│                     (User Interface Layer)                           │
├──────────────────────────────────────────────────────────────────────┤
│  • Gateway WebSocket Control Plane                                   │
│  • Multi-channel message routing                                     │
│  • Voice wake word & talk mode                                       │
│  • Browser automation (Chromium/Chrome)                              │
│  • Community skills marketplace                                      │
│  • Session management                                                │
│  • File operations & local system access                             │
│                                                                       │
│  Runtime: Node.js | Memory: ~300MB | Port: Gateway WS               │
└────────────────┬──────────────────────────┬──────────────────────────┘
                 │                          │
      ┌──────────▼──────────┐    ┌──────────▼──────────┐
      │  Smart Routing      │    │  Intelligent Router │
      │  Decision Engine    │    │  Skill: zoe-backend │
      └──────────┬──────────┘    └──────────┬──────────┘
                 │                          │
        ┌────────▼─────────┐       ┌───────▼────────┐
        │ Research/Planning│       │ Context/Control│
        │   Tasks?         │       │   Tasks?       │
        │                  │       │                │
        │ • "research..."  │       │ • "turn on..." │
        │ • "plan..."      │       │ • "remember..."│
        │ • "analyze..."   │       │ • "add to..."  │
        │ • "compare..."   │       │ • "workflow..."│
        └────────┬─────────┘       └───────┬────────┘
                 │                         │
                 │                         │
                 ▼                         ▼
┌─────────────────────────────┐  ┌────────────────────────────────┐
│      🤖 AGENT ZERO          │  │       🧠 ZOE CORE              │
│   (Research & Planning)     │  │  (Context & Intelligence)      │
├─────────────────────────────┤  ├────────────────────────────────┤
│                             │  │                                │
│ Container: zoe-agent0       │  │ Services (14):                 │
│ Port: 50001 (UI)            │  │ • zoe-core (API: 8000)         │
│ Bridge: 8101 (API)          │  │ • zoe-mcp-server (8003)        │
│                             │  │ • zoe-auth (JWT/RBAC)          │
│ Capabilities:               │  │ • zoe-mem-agent (RAG)          │
│ ✅ Deep web research        │  │ • homeassistant-mcp-bridge     │
│ ✅ Multi-step planning      │  │ • n8n-mcp-bridge               │
│ ✅ Technical analysis       │  │ • zoe-redis (cache)            │
│ ✅ Detailed comparisons     │  │ • livekit (voice)              │
│ ✅ Code execution (sandbox) │  │ • zoe-llamacpp (GPU/CPU)       │
│                             │  │                                │
│ Model: Claude 3.5 Sonnet    │  │ Capabilities:                  │
│ API: Anthropic              │  │ ✅ Multi-user auth             │
│ Response Time: 30-120s      │  │ ✅ Memory/RAG search           │
│ Cost: $0.03-0.75/query      │  │ ✅ Smart home control          │
│                             │  │ ✅ Workflow automation         │
│ Safety Modes:               │  │ ✅ Quick queries               │
│ • Grandma (default)         │  │ ✅ Household data              │
│ • Developer (full access)   │  │ ✅ Local LLM inference         │
│                             │  │                                │
│ Runtime: Docker Container   │  │ Runtime: Docker Compose        │
│ Memory: ~500MB              │  │ Memory: ~8GB                   │
└─────────────────────────────┘  └────────────┬───────────────────┘
                                              │
                                              ▼
                            ┌──────────────────────────────────┐
                            │       INTEGRATIONS               │
                            ├──────────────────────────────────┤
                            │ • Home Assistant (smart home)    │
                            │ • N8N (workflows)                │
                            │ • LiveKit (WebRTC voice)         │
                            │ • Redis (caching)                │
                            │ • SQLite (3 databases)           │
                            └──────────────────────────────────┘

```

---

## 🎯 Routing Decision Matrix (Detailed)

### Smart Home Control → Zoe
**Trigger Patterns:**
- "turn on/off [device]"
- "set [device] to [value]"
- "dim/brighten [lights]"
- "what devices are available"
- "what's the temperature in [room]"

**Routing Logic:**
```javascript
if (message.includes("turn") || message.includes("device") || message.includes("temperature")) {
  route_to_zoe("homeassistant/control");
}
```

**Response Time:** < 2 seconds
**Cost:** Free (local)

---

### Memory & Context → Zoe
**Trigger Patterns:**
- "what did I say about..."
- "do you remember..."
- "tell me about my conversation..."
- "when did we discuss..."

**Routing Logic:**
```javascript
if (message.includes("remember") || message.includes("said about")) {
  route_to_zoe("memories/search");
}
```

**Response Time:** < 1 second
**Cost:** Free (local RAG)

---

### Deep Research → Agent Zero
**Trigger Patterns:**
- "research [topic]"
- "investigate [subject]"
- "find information about..."
- "what are the best [products]"
- "look up [technology]"

**Routing Logic:**
```javascript
if (message.includes("research") || message.includes("investigate") || message.includes("find information")) {
  route_to_agent_zero("tools/research", {depth: "thorough"});
  warn_user("This will take 1-2 minutes...");
}
```

**Response Time:** 30-120 seconds
**Cost:** $0.03-0.75 per query

---

### Task Planning → Agent Zero
**Trigger Patterns:**
- "plan my [project]"
- "how do I [complex task]"
- "create a roadmap for..."
- "step-by-step guide to..."

**Routing Logic:**
```javascript
if (message.includes("plan") || message.includes("roadmap") || message.includes("step-by-step")) {
  route_to_agent_zero("tools/plan");
  warn_user("Creating detailed plan...");
}
```

**Response Time:** 30-90 seconds
**Cost:** $0.10-0.50 per plan

---

### Technical Analysis → Agent Zero
**Trigger Patterns:**
- "analyze [file/config]"
- "review [code]"
- "check my [system]"
- "examine [technical thing]"

**Routing Logic:**
```javascript
if (message.includes("analyze") || message.includes("review") || message.includes("examine")) {
  route_to_agent_zero("tools/analyze");
}
```

**Response Time:** 30-60 seconds
**Cost:** $0.05-0.30 per analysis

---

### Comparisons → Agent Zero
**Trigger Patterns:**
- "compare [A] vs [B]"
- "which is better: [X] or [Y]"
- "difference between [A] and [B]"
- "[A] versus [B]"

**Routing Logic:**
```javascript
if (message.includes("compare") || message.includes("vs") || message.includes("versus") || message.includes("which is better")) {
  route_to_agent_zero("tools/compare");
  warn_user("Comparing options...");
}
```

**Response Time:** 45-90 seconds
**Cost:** $0.15-0.60 per comparison

---

### Browser Automation → Moltbot
**Trigger Patterns:**
- "find flights to [destination]"
- "book [service] online"
- "search [site] for [query]"
- "fill out [form]"

**Routing Logic:**
```javascript
if (message.includes("book") || message.includes("find flights") || message.includes("fill out")) {
  use_moltbot_browser_skill();
}
```

**Response Time:** 10-60 seconds
**Cost:** Free (local browser automation)

---

### Quick Queries → Zoe
**Trigger Patterns:**
- "what's on my calendar"
- "show shopping list"
- "who is [person]"
- "my reminders"

**Routing Logic:**
```javascript
if (message.includes("calendar") || message.includes("shopping list") || message.includes("reminders")) {
  route_to_zoe("chat");
}
```

**Response Time:** < 2 seconds
**Cost:** Free (local)

---

### Workflows → Zoe
**Trigger Patterns:**
- "run [workflow name]"
- "execute [automation]"
- "start [routine]"

**Routing Logic:**
```javascript
if (message.includes("run") || message.includes("execute") || message.includes("workflow")) {
  route_to_zoe("workflows/execute");
}
```

**Response Time:** 2-10 seconds
**Cost:** Free (local N8N)

---

## 📡 API Integration Details

### Moltbot → Zoe API Calls

#### Smart Home Control
```javascript
// In zoe-backend skill (SKILL.md)
async function controlDevice(entityId, action, params) {
  const response = await fetch('http://192.168.1.218:8000/api/homeassistant/control', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${process.env.ZOE_AUTH_TOKEN}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      entity_id: entityId,
      action: action,
      ...params
    })
  });
  return await response.json();
}
```

#### Memory Search
```javascript
async function searchMemories(query, userId) {
  const response = await fetch(
    `http://192.168.1.218:8003/api/memories/search?q=${encodeURIComponent(query)}&user_id=${userId}`,
    {
      headers: {
        'Authorization': `Bearer ${process.env.ZOE_AUTH_TOKEN}`
      }
    }
  );
  return await response.json();
}
```

---

### Moltbot → Agent Zero API Calls

#### Research
```javascript
// In agent-zero-research skill (SKILL.md)
async function performResearch(query, depth, userId) {
  await notifyUser("🔍 Starting research (1-2 min)...");
  
  const response = await fetch('http://192.168.1.218:8101/tools/research', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      query: query,
      depth: depth,  // quick|thorough|comprehensive
      user_id: userId
    })
  });
  
  return await response.json();
}
```

#### Planning
```javascript
async function createPlan(task, userId) {
  await notifyUser("📋 Creating plan...");
  
  const response = await fetch('http://192.168.1.218:8101/tools/plan', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      task: task,
      user_id: userId
    })
  });
  
  return await response.json();
}
```

---

## 💾 Resource Allocation (Jetson Orin NX 16GB)

| System | CPU % | Memory | GPU % | Disk | Network |
|--------|-------|--------|-------|------|---------|
| **Zoe (14 services)** | 20-40% | 8 GB | 30-50% | 10 GB | Internal only |
| **Agent Zero** | <5% | 500 MB | 0% | 1 GB | API calls out |
| **Moltbot** | <5% | 300 MB | 0% | 500 MB | Multi-channel in/out |
| **System Reserve** | 10% | 4 GB | 20% | - | - |
| **Total Used** | 35-55% | ~9 GB | 50-70% | ~12 GB | - |
| **Available** | 45-65% | ~7 GB | 30-50% | ~50 GB | - |

✅ **Status:** Comfortably within limits

---

## 💰 Cost Analysis

### Monthly Operating Costs (Moderate Usage)

| System | Cost Model | Estimated Monthly Cost |
|--------|------------|------------------------|
| **Zoe** | Free (local GPU inference) | $0 |
| **Agent Zero** | Claude API @ $0.03-0.75/query | $20-40 |
| **Moltbot** | Claude/GPT API (variable) | $10-30 |
| **Integrations** | Home Assistant (free), N8N (free) | $0 |
| **Infrastructure** | Local hardware (electricity) | ~$5-10 |
| **Total** | - | **$35-80/month** |

### Usage Assumptions:
- 30-50 Agent Zero research queries/month
- 100-200 Moltbot conversations/month
- Zoe handles 80% of queries locally (free)

### Cost Optimization Strategies:
1. **Route simple queries to Zoe** (local, free)
2. **Cache Agent Zero results** in Zoe's memory
3. **Use quick research** when thorough isn't needed
4. **Batch related queries** to Agent Zero
5. **Monitor API usage** via Anthropic/OpenAI consoles

---

## 🔒 Security Architecture

### Authentication Flow

```
User → Moltbot → Requires ZOE_AUTH_TOKEN → Zoe APIs
                ├→ No auth required → Agent Zero Bridge (internal network)
                └→ Channel-specific auth → WhatsApp/Telegram/Discord
```

### Security Layers

#### 1. Network Isolation
- **Zoe:** Internal network only (192.168.1.x)
- **Agent Zero:** Internal network only
- **Moltbot:** Exposes Gateway WebSocket locally, channels handle external auth

#### 2. Authentication
- **Zoe APIs:** JWT tokens with RBAC
- **Agent Zero:** Optional API key (MCP server token)
- **Moltbot:** Channel-specific (WhatsApp QR, Telegram bot token, Discord token)

#### 3. User Context Isolation
- Each user gets separate context_id in Agent Zero
- Multi-user sessions in Zoe with user_id isolation
- Moltbot maintains per-channel/per-user sessions

#### 4. Safe Defaults
- Agent Zero runs in "Grandma Mode" (research only, no code execution)
- Zoe sandboxes code execution
- Moltbot respects tool allowlists

---

## 🚀 Deployment Sequence

### Phase 1: Pre-Installation (5 min)
```bash
# 1. Verify systems running
docker ps | grep -E "zoe|agent"

# 2. Check available resources
free -h
df -h

# 3. Verify network access
curl http://192.168.1.218:8000/health
curl http://192.168.1.218:8101/health
```

### Phase 2: Install Moltbot (10-15 min)
```bash
# Install with one-liner (requires sudo)
curl -fsSL https://molt.bot/install.sh | bash

# Or clone for customization
git clone https://github.com/moltbot/moltbot.git /home/zoe/moltbot
cd /home/zoe/moltbot
pnpm install && pnpm build

# Run onboarding
pnpm run clawdbot onboard
```

### Phase 3: Configure Integration (10 min)
```bash
# 1. Copy skills to Moltbot workspace
cp -r /home/zoe/moltbot-skills/* ~/clawd/skills/

# 2. Get Zoe JWT token
curl -X POST http://192.168.1.218:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your-password"}'

# 3. Add token to Moltbot config
nano ~/.clawdbot/moltbot.json
# Add: "env": {"ZOE_AUTH_TOKEN": "your-token-here"}

# 4. Restart Moltbot gateway
clawdbot gateway restart
```

### Phase 4: Configure Channels (15-30 min)
```bash
# WhatsApp
clawdbot channels login whatsapp
# Scan QR code

# Telegram (get bot token from @BotFather)
# Add to ~/.clawdbot/moltbot.json

# Discord (create app at discord.com/developers)
# Add bot token to config
```

### Phase 5: Testing (10 min)
```bash
# Test 1: Direct Zoe API
curl -X POST http://192.168.1.218:8000/api/chat \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "What devices are available?", "user_id": "test"}'

# Test 2: Direct Agent Zero
curl -X POST http://192.168.1.218:8101/tools/research \
  -H "Content-Type: application/json" \
  -d '{"query": "test query", "depth": "quick", "user_id": "test"}'

# Test 3: Via Moltbot (send messages via channels)
# - "Turn on the lights" → Should route to Zoe
# - "Research solar panels" → Should route to Agent Zero
```

**Total Setup Time:** 50-70 minutes

---

## 🐛 Troubleshooting Guide

### Issue: Moltbot Can't Reach Zoe
**Symptoms:** "Connection refused" or "Unauthorized" errors

**Debug:**
```bash
# Check Zoe is running
docker ps | grep zoe-core

# Check network connectivity
curl http://192.168.1.218:8000/health

# Verify JWT token
curl http://192.168.1.218:8000/api/auth/verify \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Solution:**
1. Restart Zoe: `docker-compose restart zoe-core`
2. Regenerate JWT token
3. Update token in `~/.clawdbot/moltbot.json`
4. Restart Moltbot gateway

---

### Issue: Agent Zero Slow/Timeout
**Symptoms:** Research takes > 2 minutes or times out

**Debug:**
```bash
# Check Agent Zero containers
docker ps | grep agent
docker logs agent-zero-bridge
docker logs zoe-agent0

# Check API key
curl http://192.168.1.218:8101/tools/status
```

**Solution:**
1. Verify Claude API key in Agent Zero UI (port 50001)
2. Check Anthropic API quota/rate limits
3. Increase timeout in Moltbot config
4. Use "quick" depth for faster responses

---

### Issue: Skills Not Loading in Moltbot
**Symptoms:** Moltbot doesn't recognize Zoe/Agent Zero commands

**Debug:**
```bash
# Check skills directory
ls -la ~/clawd/skills/

# Verify SKILL.md syntax
cat ~/clawd/skills/zoe-backend/SKILL.md
```

**Solution:**
1. Ensure skills are in `~/clawd/skills/` directory
2. Check SKILL.md formatting (valid markdown)
3. Restart Moltbot gateway: `clawdbot gateway restart`
4. Check logs: `clawdbot logs`

---

## 📈 Performance Optimization

### 1. Response Time Optimization

**Strategy:** Route queries intelligently to minimize wait time

```javascript
// Pseudo-code routing logic
if (query.is_simple() || query.is_household_context()) {
  // Fast: < 2 seconds
  route_to_zoe();
} else if (query.requires_research()) {
  // Slow: 30-120 seconds, warn user
  warn_user("This will take 1-2 minutes...");
  route_to_agent_zero();
} else {
  // Default: Moltbot handles with community skills
  use_moltbot_default();
}
```

### 2. Cost Optimization

**Cache Agent Zero Results in Zoe:**
```python
# After Agent Zero research
research_result = await agent_zero.research(query, user_id)

# Store in Zoe's memory for future quick access
await zoe_memory.store(
    content=research_result["summary"],
    metadata={"source": "agent_zero", "query": query},
    user_id=user_id
)
```

**Benefits:**
- Future similar queries: < 1 second (from Zoe's memory)
- Reduced API costs: Reuse expensive research
- Better user experience: Instant recall

### 3. Resource Optimization

**GPU Allocation (Jetson):**
```yaml
# docker-compose.yml
services:
  zoe-llamacpp:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              capabilities: [gpu]
              device_ids: ['0']  # Allocate GPU to Zoe
```

**Memory Limits:**
```yaml
services:
  zoe-core:
    deploy:
      resources:
        limits:
          memory: 4G  # Cap Zoe core
  agent-zero-bridge:
    deploy:
      resources:
        limits:
          memory: 512M  # Cap bridge
```

---

## 🔮 Future Enhancements

### Planned Improvements

#### 1. Hybrid Research Mode
**Concept:** Start with Zoe's local memory, escalate to Agent Zero if needed

```python
async def smart_research(query, user_id):
    # First: Quick memory search
    memory_results = await zoe_memory.search(query, user_id)
    
    if memory_results.confidence > 0.8:
        return memory_results  # Good enough from memory
    else:
        # Escalate to Agent Zero for fresh research
        return await agent_zero.research(query, user_id)
```

**Benefits:**
- Faster: 90% of queries answered from memory (< 1s)
- Cheaper: Only use Agent Zero when needed
- Smarter: Learns from past research

#### 2. Proactive Agent Zero Summaries
**Concept:** Agent Zero periodically researches topics of interest

```yaml
# In Moltbot cron
cron_jobs:
  - name: "morning_tech_briefing"
    schedule: "0 8 * * *"  # Daily at 8 AM
    action: agent_zero.research("latest AI news", user_id="zoe")
```

#### 3. Multi-Agent Collaboration
**Concept:** Zoe and Agent Zero work together on complex tasks

```python
# Example: Home automation planning
plan = await agent_zero.plan("smart home setup", user_id)
devices = await zoe.get_available_devices(user_id)
combined = merge_plan_with_devices(plan, devices)
```

#### 4. Cost Controls
**Concept:** Per-user API budgets and limits

```python
# Monthly budget enforcement
MAX_RESEARCH_PER_USER_PER_MONTH = 50
if user.research_count_this_month() >= MAX_RESEARCH_PER_USER_PER_MONTH:
    return "Monthly research limit reached. Searching local memory instead..."
```

---

## 📚 Related Documentation

- [Complete Setup Guide](../guides/MOLTBOT_ZOE_AGENT_ZERO_SETUP.md)
- [Moltbot Skills Documentation](https://docs.molt.bot/skills)
- [Agent Zero API Implementation Plan](/home/zoe/.cursor/plans/agent_zero_api_implementation_84777cf3.plan.md)
- [Zoe Architecture Overview](./README.md)
- [Home Assistant MCP Bridge](../integrations/homeassistant.md)
- [N8N Workflow Integration](../integrations/n8n.md)

---

## ✅ Success Criteria Checklist

### Installation
- [ ] Moltbot installed and onboarded
- [ ] At least one channel configured (WhatsApp/Telegram/Discord)
- [ ] Zoe JWT token obtained and configured
- [ ] Skills copied to `~/clawd/skills/`

### Integration
- [ ] Moltbot can reach Zoe APIs (test with curl)
- [ ] Moltbot can reach Agent Zero bridge (test with curl)
- [ ] Skills load successfully in Moltbot gateway
- [ ] Routing logic working (smart home → Zoe, research → Agent Zero)

### Performance
- [ ] Total memory usage < 12 GB
- [ ] Zoe response time < 2 seconds
- [ ] Agent Zero response time < 120 seconds
- [ ] No container restarts or crashes

### User Experience
- [ ] Natural conversation flow across all three systems
- [ ] Clear feedback when Agent Zero tasks take time
- [ ] Correct routing 95%+ of the time
- [ ] Error handling graceful (no crashes on invalid input)

---

**Document Version:** 1.0
**Last Updated:** 2026-01-29
**Platform:** Jetson Orin NX 16GB
**Zoe Version:** 0.1.0 Alpha
**Agent Zero Version:** 1.0
**Moltbot Version:** Latest
**Author:** Zoe AI Assistant Team
