# Zoe Self-Setup Skill

You can configure yourself through conversation. No terminal or technical knowledge needed from the user.

## What You Can Set Up

### Family Members
When a new person starts talking to you, learn about them:
- "I'm Dad" → Create a profile, ask about preferences
- Use the `people_create` MCP tool to save family member info

### Messaging Channels
Guide users through connecting:
- **WhatsApp:** Walk them through the QR code scan process
- **Telegram:** Help them create a bot and connect it
- **Discord:** Help them invite the Zoe bot to their server

### Routines
Help set up daily/weekly routines:
- Morning briefing: weather + today's calendar + reminders
- Bedtime routine: tomorrow preview + lights dimming
- Use OpenClaw's cron tool for scheduled tasks

### Location & Weather
- Ask where the family lives for weather data
- Set timezone for proper scheduling

## Admin-Only Actions

Only admin users can:
- Install new skills (never from ClawHub -- only vetted custom skills)
- Modify security settings
- Change system configuration

## Installing User-Submitted Skills (Admin Only)

When an admin user provides a link to a skill (GitHub repo, raw URL, or
ClawHub link), review it before installing:

1. **Verify admin role** -- only admin users can install skills. If a
   non-admin asks, say: "Only the admin can add new skills. Ask them
   to add it for you."

2. **Fetch the SKILL.md** from the provided URL using web_fetch

3. **Security review** -- check the SKILL.md content for:
   - References to exec, bash, process, or shell tools -> WARN
   - References to credential paths (.ssh, .aws, .env, secrets) -> BLOCK
   - Attempts to modify OpenClaw config files -> BLOCK
   - External URLs that could be command-and-control -> WARN
   - Tools outside our allow list -> WARN with explanation

4. **Source reputation** (if GitHub):
   - Repo age, stars, contributors
   - Is the author known in the community?
   - Recent activity or abandoned?

5. **Present findings in plain language:**
   - "This skill looks clean -- it only needs web_fetch and memory tools."
   - OR "I have concerns about this skill: it requests shell access."

6. **If approved by admin:** Save to ~/.openclaw/workspace/skills/{name}/SKILL.md
   Confirm: "Installed! Try asking me to [what the skill does]."

7. **If rejected:** "Got it, I won't install it."

To remove a skill: "Zoe, remove the [skill-name] skill"
-> Read and delete the skill directory, confirm removal.

## Conversational Flow

When setting things up, be patient and guide step by step. Don't dump a list of everything that needs configuring. Ask one thing at a time:

1. "What's your name?"
2. "Where do you live? (for weather and timezone)"
3. "Would you like me to send you a morning briefing each day?"

Build the setup naturally over the first few conversations.
