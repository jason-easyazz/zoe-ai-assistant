# Zoe AI Assistant - Documentation Index

## Core Documentation

### System State
- **[Zoe's Current State](ZOE_CURRENT_STATE.md)** - Complete system overview and status
- **[Zoe's Current State](ZOES_CURRENT_STATE.md)** - Development state tracking
- **[System Ready](SYSTEM-READY.md)** - System readiness checklist
- **[Authentication Ready](AUTHENTICATION-READY.md)** - Authentication system status

### Quick Start Guides
- **[Quick Start Guide](QUICK-START.md)** - Getting started with Zoe
- **[Knowledge Management Summary](KNOWLEDGE_MANAGEMENT_SUMMARY.md)** - Knowledge system overview

## Touch Interface System

### Widget System
- **[Widget System Documentation](../services/zoe-ui/dist/developer/WIDGET_SYSTEM.md)** - Complete widget development guide
- **Widget Development Page**: `/developer/widgets.html` - Interactive widget development tools
- **Widget Templates**: `/developer/templates.html` - Pre-built widget templates

### Touch Interface Features
- **Touch Dashboard**: `/touch/dashboard.html` - Main touch interface
- **Touch Lists**: `/touch/lists.html` - Touch-optimized lists
- **Touch Calendar**: `/touch/calendar.html` - Touch calendar interface
- **Touch Index**: `/touch/index.html` - Touch interface entry point

### Touch Panel Management
- **Touch Panel Config**: `/touch-panel-config/` - Configuration interface
- **Auto-Discovery Service**: Touch panel discovery and management
- **TouchKio Integration**: Modified TouchKio setup for Zoe

## Authentication & Security

### Authentication System
- **Auth Service**: Complete RBAC system with SSO integration
- **Touch Panel Auth**: Quick authentication for touch panels
- **SSO Providers**: Matrix, HomeAssistant, N8N integration
- **Session Management**: Secure session handling

### Security Features
- **SSL/TLS**: HTTPS with certificates
- **RBAC**: Role-based access control
- **Passcode System**: Multi-factor authentication
- **Tunnel Service**: Cloudflare tunnel for remote access

## Development & API

### Developer Tools
- **Developer Interface**: `/developer/` - Complete development tools
- **API Configuration**: `/developer/api-config.js` - API endpoint configuration
- **Widget Development**: `/developer/widgets.html` - Widget creation tools
- **Settings Management**: `/developer/settings.html` - System settings

### API Endpoints
- **Core API**: `/api/` - Main API endpoints
- **Widget API**: `/api/widgets/` - Widget management
- **Auth API**: `/api/auth/` - Authentication endpoints
- **Touch Panel API**: `/api/touch-panel/` - Touch panel management

## Family & Multi-User System

### Family Management
- **Family Dashboard**: `/dashboard_family.html` - Family interface
- **Family Calendar**: `/calendar_family.html` - Family calendar
- **Family Settings**: `/family_settings.html` - Family configuration
- **Event Permissions**: Advanced event permission system

### User Management
- **User Authentication**: Complete user management system
- **Role Management**: User roles and permissions
- **Session Handling**: Multi-user session management

## System Architecture

### Services
- **zoe-core**: Main API backend (port 8000)
- **zoe-ui**: Web interface with SSL (ports 80/443)
- **zoe-litellm**: LLM routing and management (port 8001)
- **zoe-auth**: Authentication service (port 8002)
- **zoe-whisper**: Speech-to-text service (port 9001)
- **zoe-tts**: Text-to-speech service (port 9002)
- **zoe-ollama**: Local AI models (port 11434)
- **zoe-redis**: Data caching (port 6379)
- **zoe-n8n**: Workflow automation (port 5678)
- **zoe-cloudflared**: Tunnel service
- **touch-panel-discovery**: Touch panel auto-discovery

### Data Management
- **Knowledge Base**: SQLite databases for tasks, memory, learning
- **Performance Tracking**: Metrics and analytics
- **Backup Systems**: Automated snapshots and recovery
- **User Data**: Authentication and user management

## Deployment & Scripts

### Deployment Scripts
- **Touch Panel Deployment**: `deploy-touch-update.sh` - Touch panel deployment
- **TouchKio Update**: `update-touchkio-to-touch-interface.sh` - TouchKio integration
- **GitHub Sync**: `push_to_github.sh` - Automated GitHub synchronization
- **System Updates**: Various system update scripts

### Maintenance Scripts
- **SSL Certificates**: `generate-ssl-certificates.sh` - SSL certificate generation
- **Time Sync**: `time_sync_service.py` - System time synchronization
- **Model Optimization**: `optimize_models.sh` - AI model optimization
- **System Cleanup**: Various cleanup and maintenance scripts

## Troubleshooting & Support

### Common Issues
- **Touch Panel Issues**: Display and rotation fixes
- **Authentication Problems**: Auth service troubleshooting
- **Widget Problems**: Widget development and debugging
- **Network Issues**: Service discovery and connectivity

### Debug Tools
- **Developer Console**: Browser developer tools
- **Widget Inspector**: Built-in widget debugging
- **System Monitoring**: Performance and health monitoring
- **Log Files**: Comprehensive logging system

## Future Enhancements

### Planned Features
- **Widget Marketplace**: Community widget sharing
- **Advanced Analytics**: Usage and performance metrics
- **Plugin System**: Third-party widget support
- **GraphQL Support**: More efficient data fetching

### API Improvements
- **WebSocket Updates**: Real-time widget communication
- **Advanced Testing**: Automated widget testing
- **Widget Dependencies**: Complex widget relationships

---

## Quick Links

### Touch Interface
- [Touch Dashboard](/touch/dashboard.html)
- [Touch Lists](/touch/lists.html)
- [Touch Calendar](/touch/calendar.html)

### Developer Tools
- [Developer Interface](/developer/)
- [Widget Development](/developer/widgets.html)
- [API Configuration](/developer/api-config.js)

### Family System
- [Family Dashboard](/dashboard_family.html)
- [Family Calendar](/calendar_family.html)
- [Family Settings](/family_settings.html)

### System Management
- [System Settings](/settings.html)
- [Authentication](/auth.html)
- [Self-Awareness](/self-awareness.html)

---

*Last Updated: September 29, 2025*
*Version: 3.0.0*


