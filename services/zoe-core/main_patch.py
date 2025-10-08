import sys
import os

# Read main.py
main_path = "/app/main.py"
if os.path.exists(main_path):
    with open(main_path, 'r') as f:
        content = f.read()
    
    # Update AI client import
    if "from ai_client import" in content:
        content = content.replace(
            "from ai_client import",
            "from ai_client_enhanced import"
        )
    elif "import ai_client" in content:
        content = content.replace(
            "import ai_client",
            "import ai_client_enhanced as ai_client"
        )
    
    # Ensure developer router is included
    if "routers.developer" not in content:
        # Add import
        content = content.replace(
            "from routers import",
            "from routers import developer,"
        )
        # Add router inclusion
        if "app.include_router(developer.router)" not in content:
            content = content.replace(
                "# Include routers",
                "# Include routers\napp.include_router(developer.router)"
            )
    
    # Write back
    with open(main_path, 'w') as f:
        f.write(content)
    
    print("✅ main.py updated")
else:
    print("⚠️ main.py not found, creating minimal version")
    # Create minimal main.py
    with open(main_path, 'w') as f:
        f.write("""from fastapi import FastAPI
from routers import developer

app = FastAPI(title="Zoe AI")

app.include_router(developer.router)

@app.get("/health")
async def health():
    return {"status": "healthy"}
""")
