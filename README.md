# 🤖 Zoe - Local AI Best Friend & Life Hub

<div align="center">

![Zoe AI Assistant](https://img.shields.io/badge/Zoe-AI%20Assistant-blue?style=for-the-badge&logo=robot)
![Platform](https://img.shields.io/badge/Platform-Raspberry%20Pi%205-red?style=for-the-badge&logo=raspberry-pi)
![Privacy](https://img.shields.io/badge/Privacy-100%25%20Offline-green?style=for-the-badge&logo=shield)

*Your personal AI companion that stays home* 🏠❤️

</div>

## ✨ What Makes Zoe Special

Zoe is a **fully offline, privacy-first AI assistant** designed specifically for Raspberry Pi 5. She's not just a chatbot - she's your personal companion that evolves into a central brain for your life.

### 🎯 Core Features

- **🔒 100% Privacy** - All AI runs locally, no data leaves your network
- **🗣️ Natural Voice** - Whisper STT + Coqui TTS for real conversations  
- **🧠 Personal Memory** - Remembers your routines, moods, and preferences
- **🏠 Smart Home** - Home Assistant integration for voice control
- **⚡ Automation** - n8n workflows for proactive assistance
- **📝 Life Management** - Journaling, tasks, and intelligent organization

## 🚀 Quick Start

```bash
# Clone and setup
git clone https://github.com/jason-easyazz/zoe-ai-assistant.git
cd zoe-ai-assistant

# Run setup
chmod +x scripts/zoe-core.sh
./scripts/zoe-core.sh

# Start Zoe
docker compose up -d

# Access interface
open http://your-pi-ip:8080
```

## 📁 Project Structure

```
zoe/
├── 🐳 docker-compose.yml    # Main orchestration
├── 🔧 scripts/             # Setup & maintenance
├── 🏗️ services/            # All service containers
├── ⚙️ config/              # Configuration files
├── 🧪 tests/               # Test suites
└── 🧠 zoe-core/            # Core application
```

## 🛠️ Key Commands

```bash
# System status
docker compose ps

# View logs
docker compose logs -f zoe-core

# Create backup
./scripts/backup-system.sh full

# Monitor system
./scripts/monitoring.sh
```

## 🎭 What Makes Zoe Unique

- **Learns your personality** and adapts responses
- **Contextual awareness** from journal entries and conversations
- **Proactive suggestions** based on patterns and habits
- **Modular architecture** - easily extend and customize
- **Privacy-first design** - your data never leaves home

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## 📄 License

MIT License - feel free to customize for your needs!

---

**Built with ❤️ for the Raspberry Pi community**

*Zoe - Your AI companion that truly stays home* 🏠🤖
