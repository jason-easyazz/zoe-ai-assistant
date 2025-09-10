#!/bin/bash
# Safely rename CLAUDE_CURRENT_STATE to ZOE_CURRENT_STATE

echo "🔄 Renaming CLAUDE_CURRENT_STATE to ZOE_CURRENT_STATE..."

# 1. Rename the file
mv ZOE_CURRENT_STATE.md ZOE_CURRENT_STATE.md

# 2. Update .cursorrules
sed -i 's/CLAUDE_CURRENT_STATE/ZOE_CURRENT_STATE/g' .cursorrules

# 3. Update all shell scripts
find . -name "*.sh" -type f -exec sed -i 's/CLAUDE_CURRENT_STATE/ZOE_CURRENT_STATE/g' {} \;

# 4. Create a compatibility symlink just in case
ln -s ZOE_CURRENT_STATE.md ZOE_CURRENT_STATE.md

echo "✅ Renamed! Both names will work via symlink"
