FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*
RUN pip install litellm[proxy] fastapi uvicorn
COPY config.yaml .
COPY minimal_config.yaml .
CMD ["litellm", "--config", "minimal_config.yaml", "--port", "8001"]
