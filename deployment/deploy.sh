#!/usr/bin/env bash
set -euo pipefail

docker compose -f configs/docker-compose.enhanced.yml up -d
