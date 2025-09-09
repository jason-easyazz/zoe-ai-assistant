# Zoe AI Assistant - Continuation Prompt

## FOR NEW CHAT: Load this first!

You are continuing development of Zoe AI Assistant on Raspberry Pi 5.

## CURRENT STATUS:
- **Location**: /home/pi/zoe
- **GitHub**: https://github.com/jason-easyazz/zoe-ai-assistant
- **Web UI**: http://localhost:8080 (working)
- **API**: http://localhost:8000 (working)

## WHAT'S WORKING:
✅ All Docker services running (zoe-core, zoe-ui, zoe-ollama, zoe-redis)
✅ AI chat with llama3.2:3b model
✅ Basic purple gradient UI
✅ SQLite database persistence
✅ Event system
✅ GitHub sync

## ARCHITECTURE RULES:
- ALWAYS use zoe- prefix for containers
- NEVER rebuild zoe-ollama (keeps model)
- Single docker-compose.yml only
- All changes in /home/pi/zoe directory
- Test immediately after changes
- Create timestamped backups

## NEXT ENHANCEMENTS READY:
1. Glass-morphic UI upgrade
2. Natural language calendar
3. Developer dashboard
4. Memory system
5. Voice integration

## KEY COMMANDS:
- Check status: docker ps
- View logs: docker logs zoe-core -n 20
- Restart service: docker compose restart zoe-core
- Sync to GitHub: cd /home/pi/zoe && git add . && git commit -m "message" && git push

Load documentation/dynamic/current_state.md for detailed status.
