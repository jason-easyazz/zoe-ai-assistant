#!/bin/bash
# Secure API Key Management

# Option 1: Use environment variable instead of file
read -sp "Enter Claude API Key: " API_KEY
echo
export CLAUDE_API_KEY="$API_KEY"
docker compose up -d zoe-core

# Option 2: Use Docker secrets (more secure)
echo "$API_KEY" | docker secret create claude_api_key -
