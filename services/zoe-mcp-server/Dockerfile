FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directory
RUN mkdir -p /app/data

# Set environment variables
ENV PYTHONPATH=/app
ENV ZOE_DB_PATH=/app/data/zoe.db
ENV ZOE_API_URL=http://zoe-core:8000

# Expose port (if needed for health checks)
EXPOSE 8003

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sqlite3; conn = sqlite3.connect('/app/data/zoe.db'); conn.close()" || exit 1

# Run the MCP HTTP server (not STDIO server)
CMD ["uvicorn", "http_mcp_server:app", "--host", "0.0.0.0", "--port", "8003"]

