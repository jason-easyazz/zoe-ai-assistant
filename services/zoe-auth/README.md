# Zoe Authentication Service

A comprehensive authentication and authorization service for the Zoe AI Assistant ecosystem, featuring passcode support, role-based access control (RBAC), and SSO integration.

## Features

### Core Authentication
- **Password Authentication**: Secure bcrypt-based password hashing with configurable policies
- **Passcode Authentication**: 4-8 digit PIN codes optimized for touch panels with Argon2 hashing
- **Multi-Factor Support**: Password + passcode combinations with session escalation
- **Session Management**: JWT-based sessions with different security levels

### Role-Based Access Control (RBAC)
- **Hierarchical Roles**: Admin, User, Family, Child, Guest with inheritance
- **Granular Permissions**: Resource-based permissions with wildcard support
- **Dynamic Permission Checking**: Context-aware permissions based on session type, device, and time
- **Custom Roles**: Create custom roles with specific permission sets

### Touch Panel Optimization
- **Offline Support**: Local caching for authentication when server unavailable
- **Quick User Switching**: Fast passcode-based user switching
- **Device-Specific Caching**: Per-device cache management
- **Automatic Sync**: Background synchronization with central auth server

### Security Features
- **Rate Limiting**: Configurable rate limiting per action, IP, and user
- **Audit Logging**: Comprehensive audit trail for all authentication events
- **Security Monitoring**: Real-time detection of brute force, enumeration, and credential stuffing
- **Threat Detection**: Suspicious IP detection and pattern analysis

### SSO Integration
- **Home Assistant**: Custom auth provider with role mapping
- **n8n**: External authentication integration with permission mapping
- **Matrix**: Synapse auth provider with room auto-join
- **Extensible**: Framework for adding additional service integrations

## Architecture

```
┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│   Touch Panels      │    │   Web Interface     │    │   API Clients       │
└─────────────────────┘    └─────────────────────┘    └─────────────────────┘
           │                           │                           │
           └─────────────────────────────────────────────────────────┘
                                       │
                         ┌─────────────────────────┐
                         │   Zoe Auth Service      │
                         │   (FastAPI)             │
                         └─────────────────────────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    │                  │                  │
           ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
           │   Database      │ │   Cache Layer   │ │   SSO Services  │
           │   (SQLite)      │ │   (Memory)      │ │   (HA/n8n/Matrix)│
           └─────────────────┘ └─────────────────┘ └─────────────────┘
```

## Quick Start

### Prerequisites
- Python 3.11+
- Docker and Docker Compose
- SQLite 3

### Installation

1. **Clone and Navigate**
   ```bash
   cd /home/pi/zoe/services/zoe-auth
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Environment Configuration**
   ```bash
   export ZOE_AUTH_SECRET_KEY="your-secret-key-here"
   export ENVIRONMENT="development"
   ```

4. **Run with Docker Compose**
   ```bash
   cd /home/pi/zoe
   docker-compose up zoe-auth
   ```

### Initial Setup

1. **Create Admin User**
   ```bash
   curl -X POST http://localhost:8002/api/auth/register \
     -H "Content-Type: application/json" \
     -d '{
       "username": "admin",
       "email": "admin@zoe.local",
       "password": "SecureAdminPass123!",
       "role": "admin"
     }'
   ```

2. **Login and Get Session**
   ```bash
   curl -X POST http://localhost:8002/api/auth/login \
     -H "Content-Type: application/json" \
     -d '{
       "username": "admin",
       "password": "SecureAdminPass123!",
       "device_info": {"type": "web"}
     }'
   ```

## API Reference

### Authentication Endpoints

#### Password Login
```bash
POST /api/auth/login
{
  "username": "user",
  "password": "password",
  "device_info": {"type": "web"},
  "remember_me": false
}
```

#### Passcode Login
```bash
POST /api/auth/login/passcode
{
  "username": "user",
  "passcode": "1234",
  "device_info": {"type": "touch_panel", "device_id": "kitchen"}
}
```

#### Session Escalation
```bash
POST /api/auth/escalate
Headers: X-Session-ID: <passcode-session-id>
{
  "password": "full-password"
}
```

### User Management

#### Create User (Admin)
```bash
POST /api/admin/users
Headers: X-Session-ID: <admin-session>
{
  "username": "newuser",
  "email": "user@example.com",
  "password": "SecurePass123!",
  "role": "user"
}
```

#### Setup Passcode
```bash
POST /api/auth/passcode/setup
Headers: X-Session-ID: <session-id>
{
  "passcode": "2468",
  "expires_at": "2024-12-31T23:59:59Z"
}
```

### Touch Panel Endpoints

#### Touch Panel Authentication
```bash
POST /api/touch-panel/auth
{
  "username": "user",
  "passcode": "1234",
  "device_id": "kitchen-panel",
  "location": "kitchen"
}
```

#### Quick User Switch
```bash
POST /api/touch-panel/quick-switch
{
  "current_session_id": "session123",
  "new_username": "other-user",
  "new_passcode": "5678",
  "device_id": "kitchen-panel"
}
```

## Configuration

### Role Configuration
Default roles and their permissions:

- **Super Admin**: Full system access (`*`)
- **Admin**: User management, system monitoring, audit access
- **User**: Personal data access, standard features
- **Family**: Shared resources, calendar, lists
- **Child**: Restricted access, supervised AI, time limits
- **Guest**: Temporary access, weather, basic controls

### Rate Limiting
Configure rate limits in `core/security.py`:

```python
"login": RateLimitRule("login", 5, 300, 900, "ip")
# 5 attempts per 5 minutes, 15 minute block, IP-scoped
```

### SSO Configuration

#### Home Assistant
```yaml
# configuration.yaml
auth_providers:
  - type: command_line
    command: /usr/local/bin/zoe-auth-verify
    args: ["--username", "{{ username }}", "--password", "{{ password }}"]
```

#### n8n
```yaml
# n8n configuration
authentication:
  type: external
  settings:
    authUrl: http://zoe-auth:8002/api/sso/n8n/auth
```

#### Matrix Synapse
```yaml
# homeserver.yaml
password_providers:
  - module: zoe_auth_provider.ZoeAuthProvider
    config:
      auth_url: http://zoe-auth:8002/api/sso/matrix/auth
```

## Database Schema

### Key Tables

#### Users
- `user_id` (PRIMARY KEY)
- `username`, `email`, `password_hash`
- `role`, `is_active`, `is_verified`
- `created_at`, `updated_at`, `last_login`
- `failed_login_attempts`, `locked_until`
- `settings`, `metadata`

#### Passcodes
- `user_id` (PRIMARY KEY, FOREIGN KEY)
- `passcode_hash`, `algorithm`, `salt`
- `created_at`, `last_used`, `expires_at`
- `failed_attempts`, `max_attempts`, `is_active`

#### Sessions
- `session_id` (PRIMARY KEY)
- `user_id`, `session_type`, `auth_method`
- `device_info`, `created_at`, `last_activity`, `expires_at`
- `permissions_cache`, `role_cache`, `metadata`

#### Audit Logs
- `log_id` (PRIMARY KEY)
- `user_id`, `action`, `resource`, `result`
- `ip_address`, `user_agent`, `details`, `timestamp`

## Testing

### Run Tests
```bash
# All tests
python -m pytest tests/

# Specific test categories
python -m pytest tests/test_auth.py      # Authentication tests
python -m pytest tests/test_rbac.py      # RBAC tests
python -m pytest tests/test_security.py  # Security tests

# Coverage report
python -m pytest --cov=zoe_auth tests/
```

### Test Categories

1. **Authentication Tests** (`test_auth.py`)
   - Password validation and policies
   - Passcode creation and verification
   - Account lockout mechanisms
   - Password change workflows

2. **RBAC Tests** (`test_rbac.py`)
   - Permission checking and inheritance
   - Role assignment and validation
   - Context-aware permissions
   - Custom role creation

3. **Security Tests** (`test_security.py`)
   - Rate limiting functionality
   - Security event detection
   - Audit logging verification
   - Threat pattern analysis

## Security Considerations

### Password Security
- Minimum 8 characters with complexity requirements
- bcrypt hashing with configurable rounds
- Password history to prevent reuse
- Optional password expiry policies

### Passcode Security
- Argon2 hashing for PIN codes
- Rate limiting with exponential backoff
- Uniqueness enforcement across users
- Common pattern detection and blocking

### Session Security
- Different session types with varying permissions
- Automatic session timeout and cleanup
- Device-specific session tracking
- Session escalation for sensitive operations

### Audit and Monitoring
- Comprehensive audit logging
- Real-time security event detection
- Rate limiting with abuse prevention
- IP-based threat detection

## SSO Integration Guide

### Adding New Services

1. **Create Integration Module**
   ```python
   # sso/new_service.py
   class NewServiceIntegration:
       async def authenticate_user(self, username, password):
           # Implementation
   ```

2. **Add API Endpoints**
   ```python
   # api/sso.py
   @router.post("/new-service/auth")
   async def new_service_auth(request: SSOAuthRequest):
       # Implementation
   ```

3. **Configure Service**
   ```python
   # main.py startup
   new_service_integration = NewServiceIntegration(config)
   ```

## Performance Tuning

### Database Optimization
- Indexes on frequently queried columns
- Connection pooling for high load
- Audit log rotation policies
- Permission cache tuning

### Touch Panel Optimization
- Local SQLite caching per device
- Background sync processes
- Offline authentication support
- Cache expiry management

### Memory Usage
- Permission cache with TTL
- Session cleanup processes
- Rate limiting memory management
- Audit log archival

## Monitoring

### Health Checks
- `/health` endpoint for service status
- Database connectivity verification
- Cache system status
- SSO service availability

### Metrics
- Authentication success/failure rates
- Session creation and expiry
- Permission check performance
- Rate limiting effectiveness

### Logging
- Structured JSON logging
- Configurable log levels
- Security event classification
- Performance metrics

## Troubleshooting

### Common Issues

1. **Database Locks**
   ```bash
   # Check for long-running transactions
   sqlite3 /app/data/zoe_auth.db ".schema"
   ```

2. **Permission Cache Issues**
   ```bash
   # Clear permission cache
   curl -X POST http://localhost:8002/api/admin/cache/clear
   ```

3. **Rate Limiting False Positives**
   ```bash
   # Check rate limit status
   curl http://localhost:8002/api/admin/rate-limits/status
   ```

### Debug Mode
```bash
export ENVIRONMENT=development
export LOG_LEVEL=DEBUG
```

## Contributing

1. Follow existing code structure and patterns
2. Add comprehensive tests for new features
3. Update documentation for API changes
4. Ensure security review for auth-related changes
5. Test SSO integration with target services

## License

This authentication service is part of the Zoe AI Assistant project. See the main project LICENSE file for details.

## Support

For issues and questions:
- Check the logs: `docker logs zoe-auth`
- Review the health endpoint: `http://localhost:8002/health`
- Examine audit logs: `/api/admin/audit-logs`
- Consult the security summary: `/api/admin/stats`

