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
