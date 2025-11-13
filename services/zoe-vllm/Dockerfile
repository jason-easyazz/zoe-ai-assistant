# Use dusty-nv's vLLM container for Jetson R36.4
# This matches our driver version (540.4.0)
FROM dustynv/vllm:r36.4-cu129-24.04

WORKDIR /app

# Copy all entrypoint scripts
COPY entrypoint-jetson-v1.sh /app/
COPY entrypoint-jetson-v2.sh /app/
COPY entrypoint-jetson-v3.sh /app/
RUN chmod +x /app/entrypoint-*.sh

# Copy test scripts
COPY test_minimal.py /app/
COPY test_server.py /app/
RUN chmod +x /app/test_*.py

EXPOSE 11434

# Default to Config V1 (highest success rate)
CMD ["/bin/bash", "/app/entrypoint-jetson-v1.sh"]
