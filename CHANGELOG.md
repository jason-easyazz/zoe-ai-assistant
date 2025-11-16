# Changelog

All notable changes to the Zoe AI Assistant project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-11-16 "Jetson Optimization & Multi-Platform"

### Added
- **llama.cpp inference engine** - Replaced vLLM/Ollama with high-performance llama.cpp
- **NVIDIA Jetson Orin NX support** - GPU-accelerated deployment with dustynv/llama_cpp image
- **Multi-user authentication system** - Complete JWT + RBAC implementation (zoe-auth service)
- **Light RAG memory system** - Vector embeddings with sentence-transformers for semantic search
- **MCP server integrations** - Model Context Protocol bridges for:
  - Home Assistant (smart home control)
  - N8N (workflow automation)
- **Hardware-aware model selection** - Automatic detection of Jetson vs Pi with adaptive configs
- **Code execution sandbox** - Safe Python/JavaScript execution environment (zoe-code-execution)
- **Multi-platform docker-compose structure** - Base + override files for Jetson and Pi
- **Voice pipeline infrastructure** - Whisper STT, TTS, LiveKit WebRTC (ready for deployment)
- **Enhanced memory agent** - Specialized AI agent for memory operations (zoe-mem-agent)

### Changed
- **Migrated from vLLM to llama.cpp** - Better performance and stability on Jetson
- **Optimized for GPU acceleration** - All model layers on GPU for Jetson (99 layers)
- **Restructured for multi-platform** - Clean separation of platform-specific configs
- **Database architecture** - Now using 3 databases (zoe.db, memory.db, training.db)
- **Model format** - Using GGUF quantized models (Q4_K_M) for efficiency
- **Docker networking** - Fixed network naming with explicit `name: zoe-network`

### Fixed
- **Docker network naming issues** - Prevented auto-prefixing with assistant_
- **Model selection for different hardware** - Proper CPU vs GPU model routing
- **Memory persistence** - Light RAG memories now survive restarts
- **Service dependencies** - Correct dependency ordering in docker-compose

### Removed
- **vLLM support** - Migration blocked by PyTorch CUDA allocator bug (documented in docs/archive/)
- **Ollama service** - Replaced by llama.cpp for better Jetson performance
- **Legacy authentication** - Old auth system replaced with proper JWT/RBAC

### Technical Details
- **Primary Models (Jetson)**:
  - llama-3.2-3b-gguf (2.0GB, GPU-accelerated)
  - qwen2.5-7b-gguf (4.7GB, GPU-accelerated)
  - qwen2.5-coder-7b-gguf (4.5GB, for code tasks)
- **Primary Models (Pi)**:
  - phi3:mini (2.2GB, CPU-optimized)
  - llama3.2:3b (2.0GB, CPU-optimized)
- **Performance**:
  - Jetson: 50+ tokens/sec with GPU
  - Pi: 8-12 tokens/sec with CPU
- **Memory Footprint**:
  - Jetson: ~8-9GB VRAM usage
  - Pi: ~4-6GB RAM usage

---

## [0.0.1] - 2025-10-25 "Initial Release"

### Added
- Initial release of Zoe AI Assistant
- Core chat functionality with AI integration
- Calendar and event management features
- List management (shopping, todos, projects)
- Journal system with photo support
- Memory system for conversation continuity
- Task management and project organization
- Multi-service architecture with Docker support
- RESTful API with FastAPI
- Web UI with responsive design
- Developer dashboard and tools
- Health monitoring and metrics
- Push notification support
- Weather integration
- Home Assistant integration
- N8N workflow integration
- Touch panel support
- Widget system for customizable dashboard

### Technical Details
- FastAPI-based microservices architecture
- SQLite database with schema-based initialization
- Docker Compose for easy deployment
- CORS-enabled API endpoints
- JWT-based authentication framework
- Real-time chat with AI models
- File upload and media management
- SSL/TLS support
- Comprehensive API documentation
- 50+ API endpoints across multiple services

### Repository Consolidation
- Consolidated from dual directory structure to single source of truth
- Cleaned up unnecessary files and directories
- Established proper project structure
- Implemented governance rules for safe operations

---

## [Unreleased]

### Planned
- **Voice services deployment** - Activate Whisper STT, TTS, and voice-agent
- **Mac Mini M4 support** - Test and validate on Apple Silicon
- **Performance benchmarks** - Comprehensive testing across platforms
- **Additional MCP bridges** - Matrix chat, Slack, Discord integrations
- **Mobile app** - Native mobile interface
- **Advanced analytics** - Usage metrics and performance dashboards
- **Plugin system** - Extensibility for community contributions

---

## Migration Notes

### Upgrading from 0.0.1 to 0.1.0

**⚠️ Breaking Changes:**
- Database structure changed (3 separate databases)
- Model format changed (GGUF instead of original formats)
- Service names updated (zoe-ollama → zoe-llamacpp)
- Docker compose structure changed (now uses platform-specific overrides)

**Migration Steps:**
1. Backup existing data: `cp -r data/ data.backup/`
2. Pull latest code: `git pull origin main`
3. Update docker-compose: Use platform-specific override file
4. Reinitialize databases: `./scripts/setup/init_databases.sh`
5. Restart services with correct override:
   - Jetson: `docker-compose -f docker-compose.yml -f docker-compose.jetson.yml up -d`
   - Pi: `docker-compose -f docker-compose.yml -f docker-compose.pi.yml up -d`

---

## Version History

- **0.1.0** (2025-11-16) - Jetson Optimization & Multi-Platform
- **0.0.1** (2025-10-25) - Initial Release

[0.1.0]: https://github.com/YOUR-USERNAME/zoe-ai-assistant/releases/tag/v0.1.0
[0.0.1]: https://github.com/YOUR-USERNAME/zoe-ai-assistant/releases/tag/v0.0.1
