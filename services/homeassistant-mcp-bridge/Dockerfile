# Base pinned by patch tag + index DIGEST (was the floating `3.11-slim-bookworm`).
# CPython 3.11.16 was released 2026-08-12; this digest is the official image's
# 2026-09-19 Debian refresh (multi-arch index, arm64 included). To bump: read the new
# index digest (`docker pull python:<tag>` + `docker image inspect --format
# '{{index .RepoDigests 0}}'`), update this line, rebuild, then run the bridge tests +
# GET /tools/names against the new image.
FROM python:3.11.16-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8007

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -fsS http://localhost:8007/ >/dev/null || exit 1

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8007"]

