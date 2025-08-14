# Fix CONFIG issue in enhanced chat
import re

with open('main.py', 'r') as f:
    content = f.read()

# Replace CONFIG["database_path"] with direct path
content = content.replace('CONFIG["database_path"]', '"/app/data/zoe.db"')

with open('main.py', 'w') as f:
    f.write(content)

print("✅ Fixed CONFIG database path references")
