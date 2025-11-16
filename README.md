# 🤖 Zoe AI Assistant

> Privacy-first, multi-user AI assistant with intelligent memory and real-time capabilities

[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](CHANGELOG.md)
[![Platform](https://img.shields.io/badge/platform-Jetson%20|%20Pi%205-green.svg)](#platform-support)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-compose-blue.svg)](docker-compose.yml)

## ⚡ Status: Alpha - Active Development

Currently deployed and optimized for **NVIDIA Jetson Orin NX**. Raspberry Pi 5 support tested and ready.

---

## 🌟 Core Features

### ✅ Working Now

- **🔐 Multi-User Authentication** - JWT-based auth with Role-Based Access Control (RBAC)
- **🧠 Intelligent Memory System** - Light RAG with vector embeddings for semantic search
- **🏠 Smart Home Integration** - Home Assistant MCP bridge for device control
- **🔧 Workflow Automation** - N8N MCP bridge for automation workflows
- **🤖 Hardware-Adaptive AI** - llama.cpp with GPU acceleration on Jetson, CPU-optimized for Pi
- **💻 Code Execution Sandbox** - Safe code execution environment
- **📊 Real-Time Dashboard** - Modern web UI with responsive design
- **🗄️ Multi-Database Architecture** - zoe.db (main), memory.db (RAG), training.db (ML)

### 🚧 In Development

- **🎤 Voice Pipeline** - Infrastructure exists (Whisper STT, TTS, LiveKit WebRTC) - deployment in progress

---

## 💻 Platform Support

| Platform | Status | GPU Support | Use Case |
|----------|--------|-------------|----------|
| **NVIDIA Jetson Orin NX 16GB** | ✅ Primary | GPU-accelerated | Production deployment |
| **Raspberry Pi 5 16GB** | ✅ Tested | CPU-only | Edge/development |
| **Mac Mini M4** | 📝 Planned | CPU-only | Future (ARM64 compatible) |

---

## 🚀 Quick Start

### Prerequisites

**All Platforms:**
- Docker & Docker Compose
- 16GB RAM minimum
- Git

**Jetson Orin NX:**
- JetPack 5.1.3+ (R36.2.0+)
- NVIDIA Container Runtime
- CUDA 12.6+

### Installation

```bash
# Clone repository
git clone https://github.com/YOUR-USERNAME/zoe-ai-assistant.git
cd zoe-ai-assistant

# Copy environment template
cp .env.example .env
# Edit .env with your API keys

# Initialize databases (first-time only)
./scripts/setup/init_databases.sh
```

### Start Services

**On Jetson Orin NX:**
```bash
docker-compose -f docker-compose.yml -f docker-compose.jetson.yml up -d
```

**On Raspberry Pi 5:**
```bash
docker-compose -f docker-compose.yml -f docker-compose.pi.yml up -d
```

### Access

- **Web UI**: `http://localhost` (port 80) or `https://localhost` (port 443)
- **API**: `http://localhost:8000`
- **API Docs**: `http://localhost:8000/docs`
- **Health Check**: `http://localhost:8000/health`

---

## 🏗️ Architecture

### Services (14 Running)

```
┌─────────────┬──────────────────────────────────────┐
│ Core        │ zoe-core, zoe-auth, zoe-ui          │
│ AI/LLM      │ zoe-llamacpp, zoe-mem-agent         │
│ MCP Bridges │ zoe-mcp-server, homeassistant-mcp,  │
│             │ n8n-mcp-bridge                       │
│ Integration │ homeassistant, zoe-n8n, livekit     │
│ Storage     │ zoe-redis                            │
│ Tools       │ zoe-code-execution                   │
│ Tunnel      │ cloudflared                          │
└─────────────┴──────────────────────────────────────┘
```

### Tech Stack

- **LLM Inference**: llama.cpp (dustynv/llama_cpp:r36.2.0 for Jetson)
- **Backend**: FastAPI microservices
- **Frontend**: Nginx serving modern web UI
- **Database**: SQLite (3 databases)
- **Orchestration**: Docker Compose with platform-specific overrides
- **Authentication**: JWT with RBAC
- **Memory**: Light RAG with sentence-transformers
- **Integrations**: MCP (Model Context Protocol)

### Databases

- **zoe.db** (9.7 MB) - Main application data, users, conversations
- **memory.db** (280 KB) - Light RAG vectors and semantic memories
- **training.db** (472 KB) - ML training data and model performance

---

## 📚 Documentation

- **[Setup Guides](docs/guides/)** - Platform-specific installation
  - [Jetson Orin NX Setup](docs/guides/JETSON_SETUP.md)
  - [Raspberry Pi 5 Setup](docs/guides/PI_SETUP.md)
- **[Architecture](docs/architecture/)** - System design and components
- **[Hardware Compatibility](HARDWARE_COMPATIBILITY.md)** - Platform comparison
- **[Contributing](CONTRIBUTING.md)** - How to contribute
- **[Changelog](CHANGELOG.md)** - Version history

---

## 🔧 Configuration

### Environment Variables

See [.env.example](.env.example) for all configurable options. Key variables:

```bash
# Core API Keys
OPENAI_API_KEY=your-key
ANTHROPIC_API_KEY=your-key

# Home Assistant
HA_BASE_URL=http://homeassistant:8123
HA_ACCESS_TOKEN=your-token

# N8N
N8N_BASE_URL=http://n8n:5678
N8N_API_KEY=your-key

# Authentication
ZOE_AUTH_SECRET_KEY=your-secret-key
```

### Models

**Jetson Orin NX** (GPU-accelerated):
- Primary: llama-3.2-3b, qwen2.5-7b, qwen2.5-coder-7b
- Format: GGUF with Q4_K_M quantization
- GPU Layers: All (99 layers on GPU)

**Raspberry Pi 5** (CPU-optimized):
- Primary: phi3:mini, llama3.2:3b
- Format: GGUF with Q4_K_M quantization
- Threads: 4 (match CPU cores)

---

## 🎯 Key Features in Detail

### Multi-User Authentication

- JWT-based authentication with secure sessions
- Role-Based Access Control (RBAC)
- User data isolation (all queries filtered by user_id)
- Session management with zoe-auth service

### Intelligent Memory System

- **Light RAG** - Vector embeddings for semantic search
- **Cross-Conversation Persistence** - Memories survive restarts
- **Wikilink Syntax** - `[[entity]]` for linking
- **Relationship Awareness** - Understands connections between entities

### MCP Integrations

- **Home Assistant** - Control smart home devices via natural language
- **N8N** - Trigger and manage workflow automations
- **Tool Calling** - Structured actions via MCP protocol

### Hardware-Aware AI

- **Automatic Platform Detection** - Detects Jetson vs Pi
- **Adaptive Model Selection** - Chooses appropriate models per platform
- **Performance Optimization** - GPU acceleration on Jetson, CPU tuning on Pi

---

## ⚠️ Known Issues

- Voice services (Whisper STT, TTS, voice-agent) infrastructure exists but not currently deployed
- See [GitHub Issues](https://github.com/YOUR-USERNAME/zoe-ai-assistant/issues) for tracking

---

## 🤝 Contributing

Contributions welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Development Workflow

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Copy `.env.example` to `.env` and configure
4. Make your changes
5. Test locally
6. Submit a Pull Request

### Reporting Issues

Use [GitHub Issues](https://github.com/YOUR-USERNAME/zoe-ai-assistant/issues) for:
- Bug reports
- Feature requests
- Documentation improvements
- Platform testing feedback

---

## 📜 License

MIT License - See [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- **llama.cpp** - High-performance LLM inference
- **dusty-nv** - NVIDIA Jetson-optimized builds
- **FastAPI** - Modern Python web framework
- **Docker** - Containerization platform
- **Home Assistant** - Smart home platform
- **N8N** - Workflow automation

---

## 📞 Support

- **Documentation**: See [docs/](docs/) directory
- **API Reference**: `http://localhost:8000/docs` (when running)
- **GitHub Issues**: Bug reports and feature requests
- **Discussions**: Community support and questions

---

**Built with ❤️ for privacy-first, self-hosted AI** 🚀
