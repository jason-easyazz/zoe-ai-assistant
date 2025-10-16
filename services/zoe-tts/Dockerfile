FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    espeak-ng \
    libespeak-ng1 \
    ffmpeg \
    libsndfile1 \
    git \
    build-essential \
    cmake \
    && rm -rf /var/lib/apt/lists/*

# Set espeak library path for phonemizer
ENV PHONEMIZER_ESPEAK_LIBRARY=/usr/lib/aarch64-linux-gnu/libespeak-ng.so.1
ENV PHONEMIZER_ESPEAK_PATH=/usr/bin/espeak-ng

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
# Note: llama-cpp-python will build from source for ARM64 optimization
RUN pip install --no-cache-dir -r requirements.txt

# Clone NeuTTS Air and install its dependencies
RUN git clone https://github.com/neuphonic/neutts-air.git /app/neutts-air && \
    pip install --no-cache-dir -r /app/neutts-air/requirements.txt

# Add NeuTTS Air to Python path
ENV PYTHONPATH="/app/neutts-air:${PYTHONPATH}"

# Copy application code
COPY app.py .
COPY voice_manager.py .

# Create directories for voice profiles and samples
RUN mkdir -p /app/voice_profiles /app/samples /tmp/tts_cache

# Download and cache sample voices (optional, can be mounted as volume)
# These will be replaced by actual samples when mounted
RUN echo "This is a sample voice" > /app/samples/dave.txt && \
    echo "This is another sample voice" > /app/samples/jo.txt && \
    echo "Hi, I'm Zoe, your AI assistant" > /app/samples/zoe.txt

EXPOSE 9002

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:9002/health').raise_for_status()"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9002", "--timeout-keep-alive", "300"]
