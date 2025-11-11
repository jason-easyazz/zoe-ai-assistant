FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy service files
COPY mem_agent_service.py /app/

# Install Python dependencies
RUN pip install --no-cache-dir \
    fastapi==0.104.1 \
    uvicorn[standard]==0.24.0 \
    sentence-transformers>=2.2.0 \
    faiss-cpu==1.7.4 \
    "numpy<2.0" \
    pydantic>=2.0.0

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run service
CMD ["python3", "-m", "uvicorn", "mem_agent_service:app", "--host", "0.0.0.0", "--port", "8000"]

