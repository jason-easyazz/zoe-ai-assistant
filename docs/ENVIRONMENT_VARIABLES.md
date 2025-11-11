# Environment Variables Configuration

## Zoe Core Service Environment Variables

### CORS Configuration

**`ALLOWED_ORIGINS`** (Required for security)
- **Description**: Comma-separated list of allowed origins for API access
- **Default**: `http://localhost:3000,http://localhost:8080,http://localhost:5000`
- **Production**: Set to your actual frontend domains (e.g., `https://zoe.yourdomain.com,https://app.yourdomain.com`)
- **Development**: Include all localhost ports you're using

**Example**:
```bash
# Development
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080,http://localhost:5000

# Production
ALLOWED_ORIGINS=https://zoe.example.com,https://app.example.com
```

### File Upload Configuration

**`UPLOAD_DIR`**
- **Description**: Directory path for file uploads
- **Default**: `/app/data/uploads`
- **Example**: `UPLOAD_DIR=/app/data/uploads`

### Database Configuration

**`DB_PATH`**
- **Description**: Path to main SQLite database
- **Default**: `/app/data/zoe.db`
- **Example**: `DB_PATH=/app/data/zoe.db`

**`MEMORY_DB_PATH`**
- **Description**: Path to memory system database
- **Default**: `/app/data/memory.db`
- **Example**: `MEMORY_DB_PATH=/app/data/memory.db`

## Docker Compose Configuration

To set these variables in Docker Compose:

```yaml
services:
  zoe-core:
    environment:
      - ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080
      - UPLOAD_DIR=/app/data/uploads
      - DB_PATH=/app/data/zoe.db
      - MEMORY_DB_PATH=/app/data/memory.db
```

## SystemD Service Configuration

To set these variables in a systemd service:

```ini
[Service]
Environment="ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080"
Environment="UPLOAD_DIR=/app/data/uploads"
Environment="DB_PATH=/app/data/zoe.db"
Environment="MEMORY_DB_PATH=/app/data/memory.db"
```

## Shell/Local Development

Create a `.env` file in `/home/zoe/assistant/services/zoe-core/`:

```bash
# .env
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080,http://localhost:5000
UPLOAD_DIR=/app/data/uploads
DB_PATH=/app/data/zoe.db
MEMORY_DB_PATH=/app/data/memory.db
```

Then load it before running:
```bash
export $(cat .env | xargs) && uvicorn main:app --reload
```



