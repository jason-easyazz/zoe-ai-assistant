from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .api import dashboard_weather_endpoints, voice_integration_endpoints, streaming_chat_implementation

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard_weather_endpoints.router)
app.include_router(voice_integration_endpoints.router)
app.include_router(streaming_chat_implementation.router)
