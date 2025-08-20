# Zoe AI Assistant - State for Claude
Last Updated: See GitHub timestamp

## WORKING SETUP:
- Directory: /home/pi/zoe
- GitHub: https://github.com/jason-easyazz/zoe-ai-assistant
- Web UI: http://192.168.1.60:8080
- API: http://192.168.1.60:8000

## DOCKER SERVICES:
- zoe-core (FastAPI backend) - Port 8000
- zoe-ui (Nginx frontend) - Port 8080
- zoe-ollama (llama3.2:3b) - Port 11434
- zoe-redis (Cache) - Port 6379

## COMPLETED:
✅ Basic chat working with AI
✅ SQLite database storing conversations
✅ Event system operational
✅ GitHub sync configured
✅ Samba share working

## READY TO BUILD:
1. Enhanced glass-morphic UI
2. Natural language calendar
3. Developer dashboard
4. Memory system
5. Voice integration

## KEY RULES:
- Always work in /home/pi/zoe
- Never rebuild zoe-ollama
- Use zoe- prefix for containers
- Test immediately after changes
