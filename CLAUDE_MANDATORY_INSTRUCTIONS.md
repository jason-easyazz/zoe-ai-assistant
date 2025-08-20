# MANDATORY INSTRUCTIONS FOR CLAUDE - ALWAYS FOLLOW

## 🔴 BEFORE STARTING ANY WORK:
1. Check GitHub first:
   - Visit: https://github.com/jason-easyazz/zoe-ai-assistant
   - Read CLAUDE_CURRENT_STATE.md for latest status
   - Check recent commits to see last changes

2. Run status check:
   ```bash
   cd /home/pi/zoe
   git pull
   docker ps | grep zoe-
   ```

## 🟢 AFTER COMPLETING EACH STEP:
1. Save progress:
   ```bash
   git add .
   git commit -m "✅ [What was done]"
   git push
   ```

2. Update state:
   ```bash
   bash scripts/permanent/maintenance/update_state.sh
   ```

## 🔵 EVERY SCRIPT MUST:
- Start with: cd /home/pi/zoe
- Show location: echo "📍 Working in: $(pwd)"
- End with: git push
- Test immediately
- Show success/failure

## 🟡 NEVER:
- Skip GitHub sync
- Rebuild zoe-ollama
- Create duplicate docker-compose files
- Make changes without checking state

## 🟣 ALWAYS:
- Check GitHub before starting
- Update GitHub after changes
- Test immediately
- Backup before major changes
- Use zoe- prefix for containers
