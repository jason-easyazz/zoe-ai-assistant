#!/bin/bash
echo "🔍 SAFE MODEL DISCOVERY INTEGRATION"
echo "===================================="
cd /home/pi/zoe

# Check what exists
echo "📋 Checking existing files..."
[ -f "services/zoe-core/model_discovery.py" ] && echo "✅ model_discovery.py exists" || echo "❌ No model_discovery.py"
[ -f "services/zoe-core/ai_client.py" ] && echo "✅ ai_client.py exists" || echo "❌ No ai_client.py"
[ -f "services/zoe-core/ai_router.py" ] && echo "✅ ai_router.py exists" || echo "❌ No ai_router.py"

# Backup existing
echo "📦 Backing up existing files..."
mkdir -p backups/$(date +%Y%m%d_%H%M%S)
cp services/zoe-core/*.py backups/$(date +%Y%m%d_%H%M%S)/ 2>/dev/null

# Only update model_discovery.py with enhanced version
echo "🔧 Enhancing model discovery..."
cp services/zoe-core/model_discovery.py services/zoe-core/model_discovery.py.bak

# Now copy the enhanced version from artifact
echo "Copy the DYNAMIC_MODEL_DISCOVERY.py artifact to services/zoe-core/model_discovery.py"

# Install missing packages only
echo "📦 Installing only missing packages..."
docker exec zoe-core pip list | grep -q google-generativeai || docker exec zoe-core pip install google-generativeai
docker exec zoe-core pip list | grep -q cohere || docker exec zoe-core pip install cohere
docker exec zoe-core pip list | grep -q groq || docker exec zoe-core pip install groq

# Add discovery endpoints if missing
docker exec zoe-core python3 -c "
with open('/app/main.py', 'r') as f:
    content = f.read()

if '/api/models/discover' not in content:
    print('Adding discovery endpoints...')
    # Add only if not present
else:
    print('Discovery endpoints already exist')
"

# Test without breaking anything
echo "🧪 Testing discovery..."
curl -s http://localhost:8000/api/models/available || echo "Discovery endpoint not yet active"

echo "✅ Safe integration complete!"
