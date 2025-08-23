#!/bin/bash
# Quick dashboard fix script
cd /home/pi/zoe
docker compose restart zoe-core zoe-ui
sleep 10
curl -s http://localhost:8000/health | jq '.'
echo "Dashboard available at: http://192.168.1.60:8080/developer/"
