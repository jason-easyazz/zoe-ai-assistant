# Zack (Developer AI) - Working State Documentation
Last Updated: $(date)

## ✅ CONFIRMED WORKING STATE

### System Overview
- **Status**: FULLY OPERATIONAL
- **Containers**: 7/7 running
- **Abilities**: All enabled
- **Auto-execution**: Working

### Core Components

#### 1. Developer Router (`services/zoe-core/routers/developer.py`)
- **Location**: `/home/pi/zoe/services/zoe-core/routers/developer.py`
- **Key Functions**:
  - `execute_command()` - Executes system commands
  - `developer_chat()` - Handles chat with auto-execution
  - `get_status()` - Returns system status
- **Docker Format**: Uses `{{.Names}}:{{.Status}}` format
- **Parsing**: Splits on `:` delimiter

#### 2. Working Docker Commands
```python
# This format WORKS from inside container:
docker ps -a --format '{{.Names}}:{{.Status}}'
```

#### 3. Verified Endpoints
- `POST /api/developer/chat` - Chat with auto-execution
- `POST /api/developer/execute` - Direct command execution  
- `GET /api/developer/status` - System status

### What Zack Can Do
1. ✅ **See all Docker containers** with real-time status
2. ✅ **Execute any system command** with timeout protection
3. ✅ **Monitor system health** (memory, disk, CPU)
4. ✅ **Access file system** (read/write project files)
5. ✅ **Auto-fix issues** (restart stopped containers)
6. ✅ **Provide real data** (no mock responses)

### Critical Files - DO NOT BREAK
1. `services/zoe-core/routers/developer.py` - Main logic
2. `docker-compose.yml` - Has Docker socket mount
3. `services/zoe-core/main.py` - Router registration

### Docker Socket Mount (REQUIRED)
```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```

### Test Commands That Must Work
```bash
# These must all return valid data:
curl -X POST http://localhost:8000/api/developer/chat \
  -d '{"message": "show docker containers"}'

curl -X POST http://localhost:8000/api/developer/execute \
  -d '{"command": "docker ps"}'

curl http://localhost:8000/api/developer/status
```

## ⚠️ DO NOT CHANGE
1. The Docker command format in developer.py
2. The parsing logic using `:` delimiter  
3. The execute_command function signature
4. The Docker socket mount in docker-compose.yml

## 🔧 If Issues Arise

### Restore Working Version
```bash
# Backups are stored with timestamps
ls -la services/zoe-core/routers/developer.backup_*
# Restore most recent working backup
cp services/zoe-core/routers/developer.backup_[DATE] services/zoe-core/routers/developer.py
docker restart zoe-core
```

### Quick Diagnostic
```bash
# Check if Docker works from container
docker exec zoe-core docker ps

# Check status endpoint
curl http://localhost:8000/api/developer/status | jq '.'

# Test chat endpoint
curl -X POST http://localhost:8000/api/developer/chat \
  -d '{"message": "show docker containers"}' | jq '.response'
```

## 📋 Working Backup Created
A backup of the working developer.py has been saved to:
`services/zoe-core/routers/developer.working_$(date +%Y%m%d).py`
