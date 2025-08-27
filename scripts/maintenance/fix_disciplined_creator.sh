#!/bin/bash
# Fix disciplined creator registration

echo "🔧 FIXING DISCIPLINED CREATOR REGISTRATION"
echo "=========================================="

cd /home/pi/zoe

# First, check if the file was created
echo "📋 Checking if disciplined_creator.py exists..."
docker exec zoe-core ls -la /app/services/zoe-core/routers/ | grep disciplined

# Fix the import in main.py
echo "📝 Fixing main.py imports..."
docker exec zoe-core bash -c "cat > /app/main_fix.py << 'EOF'
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info('🚀 Starting Zoe AI Core')
    yield
    logger.info('👋 Shutting down')

app = FastAPI(title='Zoe AI Core', version='6.1', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# Import all routers
try:
    from routers import developer
    app.include_router(developer.router)
    logger.info('✅ Developer router loaded')
except Exception as e:
    logger.error(f'Developer router error: {e}')

try:
    from routers import disciplined_creator
    app.include_router(disciplined_creator.router)
    logger.info('✅ Disciplined creator loaded')
except Exception as e:
    logger.error(f'Disciplined creator error: {e}')

try:
    from routers import settings
    app.include_router(settings.router)
except:
    pass

try:
    from routers import chat
    app.include_router(chat.router)
except:
    pass

@app.get('/')
async def root():
    return {'service': 'Zoe AI Core', 'version': '6.1'}

@app.get('/health')
async def health():
    return {'status': 'healthy'}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
EOF"

# Replace main.py
docker exec zoe-core mv /app/main.py /app/main_old.py
docker exec zoe-core mv /app/main_fix.py /app/main.py

# Restart
echo "🔄 Restarting zoe-core..."
docker restart zoe-core
sleep 10

# Test the endpoint
echo "🧪 Testing disciplined creator endpoint..."
curl -s http://localhost:8000/api/disciplined_creator/rules | jq '.' || echo "Still loading..."

# Check logs
echo ""
echo "📋 Container logs:"
docker logs zoe-core --tail 10 | grep -E "(creator|Disciplined|loaded)"

echo ""
echo "✅ Fix applied!"
echo ""
echo "Try accessing: http://192.168.1.60:8080/disciplined_creator.html"
