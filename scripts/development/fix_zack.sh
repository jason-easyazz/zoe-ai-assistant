#!/bin/bash
# FIX_ZACK_REVIEW.sh
# Fixes the indentation error and makes review work

set -e

echo "🔧 FIXING ZACK REVIEW FEATURE"
echo "============================="

cd /home/pi/zoe

# Pull the file out
docker cp zoe-core:/app/routers/developer.py /tmp/developer_broken.py

# Fix the indentation with sed
sed -i '313s/^elif/    elif/' /tmp/developer_broken.py

# Copy it back
docker cp /tmp/developer_broken.py zoe-core:/app/routers/developer.py

# Restart
docker compose restart zoe-core
sleep 5

# Test it
echo -e "\n📊 Testing review feature..."
curl -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Review the project"}' | jq -r '.response' | head -20

echo -e "\n✅ Fixed!"
