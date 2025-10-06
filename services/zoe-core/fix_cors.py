# Add this to main.py to handle proxied requests properly

from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

# Allow all origins since nginx is proxying
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # nginx handles security
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Trust the proxy headers
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"]  # nginx handles host validation
)
