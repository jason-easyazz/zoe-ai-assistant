#!/bin/bash
# FIX_ZACK_ERRORS.sh - Fix the specific errors found

echo "🔧 FIXING ZACK ERRORS"
echo "===================="

cd /home/pi/zoe

# Fix 1: Remove the exception handler from developer.py
echo "📝 Fixing developer router exception handler..."
docker exec zoe-core bash -c "
sed -i '/@router.exception_handler/,/\"status\": \"error\"/d' /app/routers/developer.py
"

# Fix 2: Fix duplicate imports in main.py
echo "📝 Fixing duplicate imports in main.py..."
docker exec zoe-core python3 -c "
with open('/app/main.py', 'r') as f:
    content = f.read()

# Remove duplicate developer imports
content = content.replace('developer, developer', 'developer')
content = content.replace('from routers import developer, simple_creator', 'from routers import simple_creator')
content = content.replace('from routers import developer, chat', 'from routers import chat')  
content = content.replace('from routers import developer, settings', 'from routers import settings')

# Ensure developer is imported once at the top
lines = content.split('\n')
new_lines = []
developer_imported = False
developer_included = False

for line in lines:
    if 'from routers import' in line and 'developer' not in line and not developer_imported:
        # Add developer to first router import
        line = line.replace('from routers import', 'from routers import developer,')
        developer_imported = True
    elif 'from routers import developer' in line and developer_imported:
        # Skip duplicate import
        continue
    elif 'app.include_router(developer.router)' in line:
        if not developer_included:
            developer_included = True
            new_lines.append(line)
        continue
    new_lines.append(line)

content = '\n'.join(new_lines)

# If developer wasn't included yet, add it
if not developer_included and 'app.include_router' in content:
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if 'app.include_router' in line and 'developer' not in line:
            lines.insert(i, 'app.include_router(developer.router)')
            break
    content = '\n'.join(lines)

with open('/app/main.py', 'w') as f:
    f.write(content)
print('✅ Fixed imports')
"

# Fix 3: Create api_keys table (optional but removes warning)
echo "📝 Creating api_keys table..."
docker exec zoe-core sqlite3 /app/data/zoe.db << 'SQL'
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service TEXT UNIQUE NOT NULL,
    encrypted_key TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
SQL

# Fix 4: Restart service
echo "🔄 Restarting service..."
docker compose restart zoe-core
sleep 8

# Test
echo -e "\n🧪 Testing fixed Zack..."
echo "1️⃣ Status check:"
curl -s http://localhost:8000/api/developer/status | jq '.'

echo -e "\n2️⃣ Code generation test:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Build a health check endpoint"}' | jq '.code' | head -10

echo -e "\n✅ Errors fixed!"
