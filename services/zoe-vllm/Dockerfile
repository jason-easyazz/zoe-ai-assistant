# Use dusty-nv's vLLM container for Jetson R36.4
# This matches our driver version (540.4.0)
FROM dustynv/vllm:r36.4-cu129-24.04

WORKDIR /app

# Copy entrypoint script
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 11434

CMD ["./entrypoint.sh"]
