FROM dustynv/l4t-pytorch:r36.4.0

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python build dependencies FIRST
RUN pip3 install --no-cache-dir \
    setuptools_scm \
    setuptools>=49.6.0 \
    wheel \
    ninja \
    packaging

# Clone and build vLLM from source (required for Jetson ARM64)
# This takes 60-90 minutes but only needs to be done once
RUN git clone https://github.com/vllm-project/vllm.git /tmp/vllm && \
    cd /tmp/vllm && \
    MAX_JOBS=4 VLLM_TARGET_DEVICE=cuda pip3 install -e . --no-build-isolation && \
    cd / && \
    rm -rf /tmp/vllm/.git

# Install FastAPI dependencies
RUN pip3 install --no-cache-dir \
    fastapi \
    uvicorn[standard] \
    pydantic

COPY vllm_server.py .

EXPOSE 11434

CMD ["uvicorn", "vllm_server:app", "--host", "0.0.0.0", "--port", "11434"]
