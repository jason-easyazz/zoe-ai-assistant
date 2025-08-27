"""Zoe AI Core with Fixed Imports"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
import os
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info('🚀 Starting Zoe AI Core')
    yield
    logger.info('👋 Shutting down Zoe AI Core')

app = FastAPI(
    title='Zoe AI Core',
    version='6.0-fixed',
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# Import routers safely
try:
    from routers import developer
    app.include_router(developer.router)
    logger.info('✅ Developer router loaded')
except Exception as e:
    logger.error(f'❌ Developer router failed: {e}')

try:
    from routers import settings
    app.include_router(settings.router)
    logger.info('✅ Settings router loaded')
except Exception as e:
    logger.error(f'❌ Settings router failed: {e}')

try:
    from routers import chat
    app.include_router(chat.router)
    logger.info('✅ Chat router loaded')
except Exception as e:
    logger.error(f'❌ Chat router failed: {e}')

try:
    from routers import lists
    app.include_router(lists.router)
    logger.info('✅ Lists router loaded')
except Exception as e:
    logger.warning(f'Lists router not loaded: {e}')

try:
    from routers import calendar
    app.include_router(calendar.router)
    logger.info('✅ Calendar router loaded')
except Exception as e:
    logger.warning(f'Calendar router not loaded: {e}')

try:
    from routers import memory
    app.include_router(memory.router)
    logger.info('✅ Memory router loaded')
except Exception as e:
    logger.warning(f'Memory router not loaded: {e}')

@app.get('/')
async def root():
    return {
        'service': 'Zoe AI Core',
        'version': '6.0-fixed',
        'status': 'operational',
        'timestamp': datetime.now().isoformat()
    }

@app.get('/health')
async def health():
    return {
        'status': 'healthy',
        'routing': 'enabled',
        'timestamp': datetime.now().isoformat()
    }

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
