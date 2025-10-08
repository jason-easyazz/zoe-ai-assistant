FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY enhanced_mem_agent_service.py .
COPY mem_agent_service.py .

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 11435

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:11435/health || exit 1

# Run the enhanced service
CMD ["python", "enhanced_mem_agent_service.py"]

