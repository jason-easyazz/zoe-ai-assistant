#!/bin/bash
# FIX IMPORTS AND DEPENDENCIES
# Resolves the import hell issue once and for all

set -e
cd /home/pi/zoe

echo "🔧 FIXING IMPORT AND DEPENDENCY ISSUES"
echo "======================================"
echo ""

# Step 1: Audit current Python files
echo "1️⃣ Auditing Python files..."
echo "Current Python files in zoe-core:"
docker exec zoe-core find /app -name "*.py" -type f | head -20

# Step 2: Check for problematic imports
echo ""
echo "2️⃣ Checking for import issues..."
docker exec zoe-core python3 << 'CHECK'
import sys
import os

print("Python path:")
for p in sys.path:
    print(f"  - {p}")

print("\nChecking imports:")
errors = []

# Test critical imports
modules_to_test = [
    "fastapi",
    "pydantic", 
    "httpx",
    "sqlite3",
    "json",
    "datetime"
]

for module in modules_to_test:
    try:
        __import__(module)
        print(f"✅ {module}")
    except ImportError as e:
        print(f"❌ {module}: {e}")
        errors.append(module)

# Check app modules
print("\nApp modules:")
sys.path.append('/app')

app_modules = [
    "main",
    "ai_client",
    "routers.developer",
    "routers.chat"
]

for module in app_modules:
    try:
        __import__(module)
        print(f"✅ {module}")
    except ImportError as e:
        print(f"❌ {module}: {e}")
        errors.append(module)

if errors:
    print(f"\n⚠️  Found {len(errors)} import errors")
else:
    print("\n✅ All imports working!")
CHECK

# Step 3: Fix router initialization
echo ""
echo "3️⃣ Fixing router initialization..."
cat > services/zoe-core/routers/__init__.py << 'EOF'
"""Router initialization - ensures clean imports"""
# This file ensures the routers module is properly initialized
# Prevents import errors when loading routers
EOF

# Step 4: Create dependency validator
echo ""
echo "4️⃣ Creating dependency validator..."
cat > services/zoe-core/validate_deps.py << 'EOF'
"""Dependency validation utility"""
import sys
import importlib
import json

def check_dependencies():
    """Check all required dependencies are available"""
    
    results = {
        "python_version": sys.version,
        "path": sys.path,
        "modules": {},
        "errors": []
    }
    
    # Required modules
    required = {
        "fastapi": "0.104.1",
        "uvicorn": "0.24.0",
        "pydantic": "2.5.0",
        "httpx": "0.25.2",
        "python-multipart": "0.0.6"
    }
    
    for module, version in required.items():
        try:
            mod = importlib.import_module(module)
            actual_version = getattr(mod, "__version__", "unknown")
            results["modules"][module] = {
                "required": version,
                "actual": actual_version,
                "status": "ok"
            }
        except ImportError as e:
            results["modules"][module] = {
                "required": version,
                "actual": None,
                "status": "missing",
                "error": str(e)
            }
            results["errors"].append(f"{module}: {e}")
    
    return results

if __name__ == "__main__":
    results = check_dependencies()
    print(json.dumps(results, indent=2))
    
    if results["errors"]:
        print(f"\n❌ {len(results['errors'])} dependencies missing!")
        sys.exit(1)
    else:
        print("\n✅ All dependencies satisfied!")
        sys.exit(0)
EOF

# Step 5: Run dependency check
echo ""
echo "5️⃣ Running dependency validation..."
docker exec zoe-core python3 /app/validate_deps.py || {
    echo "⚠️  Some dependencies missing. Installing..."
    docker exec zoe-core pip install fastapi uvicorn pydantic httpx python
