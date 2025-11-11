FROM dustynv/l4t-pytorch:r36.4.0

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python build dependencies from standard PyPI
RUN pip3 install --no-cache-dir \
    --index-url https://pypi.org/simple \
    setuptools_scm \
    setuptools>=49.6.0 \
    wheel \
    ninja \
    packaging

# Clone vLLM stable release (v0.6.3 - known to work on Jetson)
# Using specific tag to avoid pyproject.toml issues with main branch
RUN git clone --branch v0.6.3 --depth 1 https://github.com/vllm-project/vllm.git /tmp/vllm && \
    cd /tmp/vllm && \
    MAX_JOBS=4 VLLM_TARGET_DEVICE=cuda pip3 install --index-url https://pypi.org/simple -e . --no-build-isolation && \
    cd / && \
    rm -rf /tmp/vllm/.git

# Install FastAPI dependencies
RUN pip3 install --no-cache-dir \
    --index-url https://pypi.org/simple \
    fastapi \
    uvicorn[standard] \
    pydantic

COPY vllm_server.py .

EXPOSE 11434

CMD ["uvicorn", "vllm_server:app", "--host", "0.0.0.0", "--port", "11434"]
