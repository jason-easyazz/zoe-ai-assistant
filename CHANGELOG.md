# Changelog

All notable changes to the Zoe AI Assistant project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.1] - 2025-10-25

### Added
- Initial release of Zoe AI Assistant
- **Enhanced MEM Agent** - Fully functional multi-expert system with 8 specialists
- **Light RAG Intelligence** - Vector embeddings with semantic search
- Core chat functionality with AI integration
- Authentication system with JWT and user management
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
- Voice integration (TTS/STT)
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
- JWT-based authentication with RBAC
- Real-time chat with AI models
- File upload and media management
- SSL/TLS support
- Comprehensive API documentation
- 50+ API endpoints across multiple services
- **Enhanced MEM Agent** running on port 11435 with 8 experts
- **Light RAG System** operational with all-MiniLM-L6-v2 embeddings

### Repository Consolidation
- Consolidated from dual directory structure to single source of truth
- Cleaned up unnecessary files and directories
- Optimized for Claude AI integration
- Single `/home/pi/zoe` directory contains everything
- Removed duplicate `zoe-clean` directory

### Working Systems
- ✅ **Enhanced MEM Agent** - 8 experts, action execution, port 11435
- ✅ **Light RAG Intelligence** - Vector embeddings, semantic search
- ✅ **Core API** - 50+ endpoints, FastAPI, port 8000
- ✅ **Authentication** - JWT, RBAC, port 8002
- ✅ **Web UI** - Responsive interface, ports 80/443
- ✅ **MCP Server** - Model Context Protocol, port 8003
- ✅ **Voice Services** - TTS/STT, ports 9001/9002
- ✅ **Integration Services** - HomeAssistant, N8N, LiveKit

### Known Limitations
- Chat UI integration with enhancement systems needs improvement
- Some complex multi-step tasks may timeout
- Temporal memory not fully integrated into chat responses
- Database initialization required for new installations