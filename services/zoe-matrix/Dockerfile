FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    matrix-nio[e2e]==0.20.2 \
    fastapi==0.104.1 \
    uvicorn[standard]==0.24.0 \
    aiofiles==23.2.1 \
    cryptography==41.0.7

COPY matrix_service.py .
COPY config.json.template .

EXPOSE 9003

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:9003/health || exit 1

CMD ["python", "matrix_service.py"]
