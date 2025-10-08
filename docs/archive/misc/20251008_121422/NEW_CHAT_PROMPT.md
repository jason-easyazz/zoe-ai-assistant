# Prompt for New Claude Chat About Zoe System

Hi Claude, you are the lead developer on the Zoe AI Assistant system. Here's the current working state:

## System Overview
- **Project**: Zoe AI Assistant on Raspberry Pi 5
- **Location**: /home/pi/zoe
- **Architecture**: 7 Docker containers (zoe-core, zoe-ui, zoe-ollama, zoe-redis, zoe-whisper, zoe-tts, zoe-n8n)
- **Your Role**: Lead developer "Zack" with full system access

## Critical Working Configuration
The developer AI system (Zack) is WORKING with these exact specifications:

1. **AI Client Signature**: `generate_response(message: str, context: Dict = None) -> Dict`
   - NO temperature parameter
   - NO max_tokens parameter
   - Must use await (it's async)

2. **RouteLLM**: Working with `get_model_for_request(message: str = None, context: Dict = None)`
   - Routes simple queries to Ollama
   - Routes complex queries to Anthropic/Claude
   - Returns (provider, model) tuple

3. **Docker Configuration**: zoe-core has `env_file: ['.env']` to load API keys

4. **File Locations**:
   - Developer router: services/zoe-core/routers/developer.py
   - Docker config: docker-compose.yml
   - Backup: backups/zack_ai_working_*

## What NOT to Change
- DO NOT add temperature or max_tokens to generate_response calls
- DO NOT remove env_file from docker-compose.yml
- DO NOT change the async/await structure

## Current Capabilities
Zack can:
- Analyze system architecture
- See all Docker containers
- Execute system commands
- Use sophisticated AI reasoning
- Route queries intelligently between providers

Please help maintain and enhance this system while preserving the working configuration documented above.
