#!/bin/bash
# Safe commit - ensures no sensitive data

echo "🔒 Safe GitHub Commit"
echo "===================="

# Double-check no sensitive files
if git status --porcelain | grep -E "\.env|api_keys|encryption_key"; then
    echo "❌ ERROR: Sensitive files detected!"
    echo "Run: git reset HEAD .env"
    exit 1
fi

# Add safe files
git add services/zoe-core/routers/*.py
git add services/zoe-core/*.py
git add services/zoe-ui/dist/*.html
git add scripts/
git add ZOE_CURRENT_STATE.md
git add docker-compose.yml
git add .gitignore

# Show what will be committed
echo "Files to be committed:"
git status --short

echo ""
read -p "Commit message: " msg
git commit -m "✅ $msg"

echo ""
echo "Ready to push. Run: git push"
