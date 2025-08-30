#!/bin/bash
# REGISTER_ROUTERS.sh - Register all created routers in main.py

echo "🔧 REGISTERING ALL ROUTERS"
echo "========================="

cd /home/pi/zoe

# Step 1: Check what routers exist
echo "📋 Found routers:"
docker exec zoe-core ls -1 /app/routers/*.py | grep -v __pycache__

# Step 2: Update main.py to include all routers
echo -e "\n📝 Updating main.py to register all routers..."
docker exec zoe-core python3 << 'PYTHON'
import os
import re

# Get all router files
router_dir = "/app/routers"
router_files = []
for file in os.listdir(router_dir):
    if file.endswith(".py") and not file.startswith("__"):
        router_name = file.replace(".py", "")
        router_files.append(router_name)

print(f"Found routers: {router_files}")

# Read main.py
with open("/app/main.py", "r") as f:
    content = f.read()

# Build the import statement
router_imports = ", ".join(router_files)
new_import = f"from routers import {router_imports}"

# Replace or add import
if "from routers import" in content:
    # Replace existing import
    content = re.sub(r"from routers import .*", new_import, content)
else:
    # Add import after FastAPI import
    content = content.replace(
        "from fastapi import FastAPI",
        f"from fastapi import FastAPI\n{new_import}"
    )

# Ensure all routers are registered
for router in router_files:
    include_line = f"app.include_router({router}.router)"
    if include_line not in content:
        # Find where to add it
        if "app.include_router" in content:
            # Add after last include_router
            lines = content.split("\n")
            for i in range(len(lines)-1, -1, -1):
                if "app.include_router" in lines[i]:
                    lines.insert(i+1, include_line)
                    break
            content = "\n".join(lines)
        else:
            # Add after app creation
            content = content.replace(
                'app = FastAPI(title="Zoe AI")',
                f'app = FastAPI(title="Zoe AI")\n\n{include_line}'
            )

# Write back
with open("/app/main.py", "w") as f:
    f.write(content)

print("✅ All routers registered")

# Show the registrations
print("\n📋 Router registrations in main.py:")
with open("/app/main.py", "r") as f:
    for line in f:
        if "include_router" in line:
            print(f"  {line.strip()}")
PYTHON

# Step 3: Restart service
echo -e "\n🔄 Restarting service..."
docker compose restart zoe-core
sleep 8

# Step 4: Test all endpoints
echo -e "\n🧪 Testing endpoints..."

echo "1️⃣ Developer status:"
curl -s http://localhost:8000/api/developer/status | jq '.status' || echo "Failed"

echo -e "\n2️⃣ Backup create:"
curl -s -X POST http://localhost:8000/api/backup/create | jq '.' || echo "Not registered"

echo -e "\n3️⃣ Backup list:"
curl -s http://localhost:8000/api/backup/list | jq '.' || echo "Not registered"

echo -e "\n4️⃣ All registered routes:"
curl -s http://localhost:8000/openapi.json | jq '.paths | keys[]' | head -20

echo -e "\n✅ Router registration complete!"
