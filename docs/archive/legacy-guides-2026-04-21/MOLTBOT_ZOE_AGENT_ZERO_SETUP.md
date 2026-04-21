# 🦞 Moltbot + Zoe + Agent Zero: Complete Setup Guide

**Three-System Architecture for Maximum Capability**

```
Moltbot (Frontend) → Zoe (Context/State) + Agent Zero (Research/Planning)
```

---

## 🎯 System Roles

### Moltbot - User Interface Layer
- **What:** Production-ready multi-channel interface
- **Handles:** WhatsApp, Telegram, Discord, Slack, Signal, iMessage, Voice, Browser automation
- **Why:** 88k stars, battle-tested, huge community, works NOW

### Zoe - Intelligence & Context Layer
- **What:** Multi-user AI with household context
- **Handles:** Memory, smart home, workflows, multi-user sessions, quick queries
- **Why:** Your custom household AI, knows your context, multi-user ready

### Agent Zero - Autonomous Research Layer  
- **What:** Deep research and planning agent
- **Handles:** Web research, task planning, technical analysis, comparisons
- **Why:** Complex multi-step tasks with web search and autonomous reasoning

---

## 📊 Routing Decision Matrix

| User Request | Route To | Why |
|--------------|----------|-----|
| "Turn on living room lights" | **Zoe** | Smart home control |
| "What did I say about solar panels?" | **Zoe** | Memory search |
| "Research best solar panels 2026" | **Agent Zero** | Deep research |
| "Plan my kitchen renovation" | **Agent Zero** | Complex planning |
| "Add eggs to shopping list" | **Zoe** | Quick household data |
| "Compare React vs Vue" | **Agent Zero** | Requires research |
| "Find me flights to Tokyo" | **Moltbot** | Browser automation |
| "What's on my calendar today?" | **Zoe** | Quick context query |

---

## 🚀 Installation Steps

### Step 1: Install Moltbot

```bash
# Install Moltbot (requires sudo for Node.js)
curl -fsSL https://molt.bot/install.sh | bash

# Or for customizable installation:
git clone https://github.com/moltbot/moltbot.git /home/zoe/moltbot
cd /home/zoe/moltbot
pnpm install
pnpm build

# Run onboarding wizard
pnpm run clawdbot onboard
```

**Onboarding will ask:**
- Model provider (choose Anthropic Claude or OpenAI)
- API keys
- Channel setup (WhatsApp, Telegram, Discord, etc.)
- Workspace location (recommend: `~/clawd`)

### Step 2: Configure Channels

**WhatsApp:**
```bash
clawdbot channels login whatsapp
# Scan QR code with WhatsApp mobile app
```

**Telegram:**
```bash
# Get bot token from @BotFather on Telegram
# Add to ~/.clawdbot/moltbot.json:
{
  "channels": {
    "telegram": {
      "botToken": "YOUR_BOT_TOKEN"
    }
  }
}
```

**Discord:**
```bash
# Create bot at https://discord.com/developers/applications
# Add to ~/.clawdbot/moltbot.json:
{
  "channels": {
    "discord": {
      "token": "YOUR_BOT_TOKEN"
    }
  }
}
```

### Step 3: Create Zoe Integration Skill

```bash
# Create skill directory
mkdir -p ~/clawd/skills/zoe-backend

# Create skill (see below)
nano ~/clawd/skills/zoe-backend/SKILL.md
```

**Content for `SKILL.md`:**

```markdown
# Zoe Backend Integration

Route household and context queries to Zoe's intelligent backend.

## When to Use Zoe

### Smart Home Control
When user requests device control:
- "Turn on/off [device]"
- "Set [device] to [state]"
- "What devices are available?"
- "What's the temperature in [room]?"

**Action:** POST to http://192.168.1.218:8000/api/homeassistant/control

### Memory & Context
When user asks about past conversations or stored information:
- "What did I say about [topic]?"
- "Do you remember when [event]?"
- "What do you know about my [preference]?"

**Action:** GET http://192.168.1.218:8003/api/memories/search?q={query}

### Quick Household Queries
- Shopping lists, reminders, people, calendar
- Simple questions about household state

**Action:** POST to http://192.168.1.218:8000/api/chat

### Workflows
When user wants to run automation:
- "Run workflow [name]"
- "Execute [automation]"

**Action:** POST to http://192.168.1.218:8009/api/workflows/execute

## Authentication

All Zoe APIs require JWT token:
```bash
Authorization: Bearer ${ZOE_AUTH_TOKEN}
```

## Example HTTP Call

Use the `http` tool:

```javascript
const response = await http({
  url: "http://192.168.1.218:8000/api/homeassistant/control",
  method: "POST",
  headers: {
    "Authorization": `Bearer ${process.env.ZOE_AUTH_TOKEN}`,
    "Content-Type": "application/json"
  },
  body: {
    "entity_id": "light.living_room",
    "action": "turn_on"
  }
});
```

## Response Format

Zoe returns structured responses. Present to user naturally:
- Smart home: "✅ [Action] completed"
- Memory: "[Answer] (from conversation on [date])"
- Quick query: "[Answer]"
```

### Step 4: Create Agent Zero Integration Skill

```bash
mkdir -p ~/clawd/skills/agent-zero-research
nano ~/clawd/skills/agent-zero-research/SKILL.md
```

**Content for `SKILL.md`:**

```markdown
# Agent Zero Research Integration

Route complex research and planning tasks to Agent Zero's autonomous agent.

## When to Use Agent Zero

### Deep Research
When user asks for comprehensive research:
- "Research [topic]"
- "What are the best [products/solutions]?"
- "Investigate [subject] in depth"
- "Find information about [anything]"

**Action:** POST to http://192.168.1.218:8101/tools/research

### Task Planning
When user needs a detailed plan:
- "Plan my [project]"
- "How do I [complex task]?"
- "Create a roadmap for [goal]"

**Action:** POST to http://192.168.1.218:8101/tools/plan

### Technical Analysis
When user needs code/config review:
- "Analyze my [file/system]"
- "Review [configuration]"
- "Check [technical thing]"

**Action:** POST to http://192.168.1.218:8101/tools/analyze

### Comparisons
When user wants detailed comparisons:
- "Compare [A] vs [B]"
- "Which is better: [X] or [Y]?"
- "What's the difference between [A] and [B]?"

**Action:** POST to http://192.168.1.218:8101/tools/compare

## Authentication

Agent Zero bridge doesn't require authentication (internal network).

## Example HTTP Call

```javascript
const response = await http({
  url: "http://192.168.1.218:8101/tools/research",
  method: "POST",
  headers: {
    "Content-Type": "application/json"
  },
  body: {
    "query": "best smart home protocols 2026",
    "depth": "thorough",
    "user_id": context.userId
  }
});
```

## Important Notes

1. **Agent Zero is SLOW** (30-120 seconds) - warn user:
   "🔍 Deep research in progress, this may take 1-2 minutes..."

2. **Costs API tokens** - Claude API usage

3. **Returns detailed results** - summarize for user

## Response Format

Agent Zero returns:
```json
{
  "summary": "Main findings...",
  "details": "Detailed information...",
  "sources": ["url1", "url2"]
}
```

Present as:
"🔍 Research complete: [summary]
📚 Sources: [sources]"
```

### Step 5: Configure Moltbot Access to Zoe

Edit `~/.clawdbot/moltbot.json`:

```json
{
  "agent": {
    "model": "anthropic/claude-opus-4-5",
    "workspace": "~/clawd"
  },
  "tools": {
    "http": {
      "enabled": true,
      "allowList": [
        "http://192.168.1.218:8000/*",
        "http://192.168.1.218:8003/*",
        "http://192.168.1.218:8007/*",
        "http://192.168.1.218:8009/*",
        "http://192.168.1.218:8101/*"
      ]
    }
  },
  "env": {
    "ZOE_AUTH_TOKEN": "your-zoe-jwt-token-here"
  }
}
```

**To get Zoe JWT token:**
```bash
# Login to Zoe
curl -X POST http://192.168.1.218:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your-password"}'

# Copy the "access_token" value
```

---

## 🧪 Testing

### Test 1: Direct Zoe Access
```bash
curl -X POST http://192.168.1.218:8000/api/chat \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "What devices are available?", "user_id": "test"}'
```

### Test 2: Direct Agent Zero Access
```bash
curl -X POST http://192.168.1.218:8101/tools/research \
  -H "Content-Type: application/json" \
  -d '{"query": "smart home protocols", "depth": "quick", "user_id": "test"}'
```

### Test 3: Via Moltbot
Send message via WhatsApp/Telegram:
- "Research solar panels" → Should route to Agent Zero
- "Turn on the lights" → Should route to Zoe
- "What did I say about renovations?" → Should route to Zoe

---

## 📈 Resource Usage

### On Jetson Orin NX 16GB

| System | Memory | CPU | GPU | Notes |
|--------|--------|-----|-----|-------|
| Zoe (14 services) | ~8 GB | 20-40% | 30-50% | GPU for llama.cpp |
| Agent Zero | ~500 MB | <5% | 0% | Uses Claude API |
| Moltbot | ~300 MB | <5% | 0% | Uses Claude/GPT API |
| **Total** | **~9 GB** | **25-50%** | **30-50%** | ✅ Fits comfortably |

### API Costs

- **Zoe:** Local inference (free) + HA/N8N (free)
- **Agent Zero:** Claude API (~$0.03-0.75 per research query)
- **Moltbot:** Claude/GPT API (varies by usage)

**Estimate:** $20-50/month for moderate use

---

## 🔧 Maintenance

### Restart Services
```bash
# Restart Moltbot gateway
clawdbot gateway restart

# Restart Zoe
cd /home/zoe/assistant
docker-compose restart zoe-core

# Restart Agent Zero
docker restart zoe-agent0 agent-zero-bridge
```

### View Logs
```bash
# Moltbot logs
clawdbot logs

# Zoe logs
docker logs -f zoe-core

# Agent Zero logs
docker logs -f agent-zero-bridge
```

### Update Systems
```bash
# Update Moltbot
clawdbot update

# Update Zoe
cd /home/zoe/assistant
git pull
docker-compose up --build -d

# Update Agent Zero
docker pull agent0ai/agent-zero:latest
docker-compose -f modules/agent-zero/docker-compose.module.yml up -d
```

---

## 🎭 Example Conversations

### Smart Home (via Zoe)
```
You: Turn on the living room lights
Moltbot → Zoe → Home Assistant
Bot: ✅ Living room lights turned on
```

### Research (via Agent Zero)
```
You: Research the best solar panel brands for 2026
Moltbot → Agent Zero → Claude + Web Search
Bot: 🔍 Researching... (1-2 min)
Bot: Here's what I found about solar panels...
     Top brands: SunPower, LG, Panasonic
     📚 Sources: [10 authoritative sites]
```

### Memory (via Zoe)
```
You: What did I say about my kitchen renovation budget?
Moltbot → Zoe → RAG/Memory Search
Bot: On Jan 15, you mentioned a budget of $25k for kitchen renovation
```

### Planning (via Agent Zero)
```
You: Plan my home automation migration from SmartThings to HA
Moltbot → Agent Zero → Claude Planning
Bot: 📋 Created detailed migration plan:
     Step 1: Inventory all devices
     Step 2: Check HA compatibility
     Step 3: Set up test environment
     [10 more steps...]
```

---

## 🚨 Troubleshooting

### "Moltbot can't reach Zoe"
- Check Zoe is running: `docker ps | grep zoe`
- Check network: `curl http://192.168.1.218:8000/health`
- Verify JWT token in moltbot.json

### "Agent Zero not responding"
- Check containers: `docker ps | grep agent`
- Check bridge: `curl http://192.168.1.218:8101/health`
- View logs: `docker logs agent-zero-bridge`

### "Research taking too long"
- Normal for Agent Zero (30-120 seconds)
- Check Claude API key in Agent Zero UI
- Monitor: http://192.168.1.218:50001

### "Skills not loading"
- Check skill directory: `ls -la ~/clawd/skills/`
- Verify SKILL.md syntax
- Restart Moltbot: `clawdbot gateway restart`

---

## 📚 Additional Resources

- [Moltbot Documentation](https://docs.molt.bot)
- [Moltbot GitHub](https://github.com/moltbot/moltbot)
- [Zoe Architecture](/home/zoe/assistant/docs/architecture/)
- [Agent Zero Integration Plan](/home/zoe/.cursor/plans/agent_zero_api_implementation_84777cf3.plan.md)
- [Moltbot Skills Marketplace](https://clawd.bot/skills)

---

## 🎯 Success Criteria

✅ Can message Moltbot via WhatsApp/Telegram/Discord
✅ Moltbot routes smart home commands to Zoe
✅ Moltbot routes research queries to Agent Zero
✅ Zoe responds with household context
✅ Agent Zero returns detailed research
✅ All three systems running stable on Jetson
✅ Total resource usage < 12GB RAM

---

## 🛠️ Next Steps After Setup

1. **Add more channels** - Signal, Slack, iMessage
2. **Install community skills** - Browse ClawdHub
3. **Customize routing** - Tune decision logic
4. **Add voice features** - Voice wake + talk mode
5. **Browser automation** - Complex web tasks
6. **Proactive features** - Cron jobs, heartbeats

---

Created: 2026-01-29
Last Updated: 2026-01-29
Jetson Orin NX 16GB | Zoe v0.1.0 | Agent Zero v1.0 | Moltbot latest
