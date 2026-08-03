FROM python:3.11-slim-bookworm

WORKDIR /app

# Install system dependencies (unzip is needed to unpack the Deno archive below)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    ca-certificates \
    unzip \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

# JavaScript runtime for yt-dlp. Modern YouTube / YouTube Music extraction has to
# solve the player's nsig / EJS challenge, which yt-dlp offloads to an external JS
# engine; Deno is its documented default runtime (auto-detected on PATH). Without a
# JS runtime in the image, playback regresses at runtime (CI cannot catch this).
# Pinned + checksum-verified, and arch-selected so the SAME Dockerfile builds on the
# Jetson (arm64) and on x86_64 dev hosts. Deno is a single static binary — leaner
# than pulling nodejs + its dependency tree via apt.
ARG DENO_VERSION=2.9.4
RUN set -eux; \
    arch="$(uname -m)"; \
    case "$arch" in \
        aarch64|arm64) target="aarch64-unknown-linux-gnu" ;; \
        x86_64|amd64)  target="x86_64-unknown-linux-gnu" ;; \
        *) echo "unsupported arch for Deno: $arch" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-${target}.zip"; \
    curl -fsSL "$url" -o /tmp/deno.zip; \
    curl -fsSL "${url}.sha256sum" -o /tmp/deno.zip.sha256sum; \
    echo "$(cut -d' ' -f1 /tmp/deno.zip.sha256sum)  /tmp/deno.zip" | sha256sum -c -; \
    unzip -o /tmp/deno.zip -d /usr/local/bin; \
    chmod +x /usr/local/bin/deno; \
    rm -f /tmp/deno.zip /tmp/deno.zip.sha256sum; \
    deno --version

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose MCP service port
EXPOSE 8100

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8100/health || exit 1

# Run the service
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100", "--log-level", "info"]
