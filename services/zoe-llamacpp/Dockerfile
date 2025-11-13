# Use pre-built llama.cpp image from dusty-nv (already has CUDA support)
FROM dustynv/llama_cpp:r36.2.0

# Create app directory
WORKDIR /app

# Copy startup script
COPY entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

# Expose API port
EXPOSE 11434

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:11434/health || exit 1

# Start llama-server
CMD ["/app/entrypoint.sh"]

