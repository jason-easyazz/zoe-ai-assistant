#!/usr/bin/env python3
import re

# Read the HTML file
with open('services/zoe-ui/dist/developer/index.html', 'r') as f:
    content = f.read()

# Fix all localhost:8000 references to use relative /api paths
replacements = [
    (r'http://localhost:8000/api', '/api'),
    (r'http://localhost:8000/', '/'),
    (r'localhost:8000/api', '/api'),
    (r'localhost:8000/', '/'),
    (r"fetch\('http://localhost:8000", "fetch('"),
    (r'const API_BASE = .*', 'const API_BASE = "";'),
]

for old, new in replacements:
    content = re.sub(old, new, content)

# Also fix any WebSocket URLs
content = re.sub(r'ws://localhost:8000', 'ws://' + 'window.location.host', content)

# Write back
with open('services/zoe-ui/dist/developer/index.html', 'w') as f:
    f.write(content)

print("✅ Fixed API URLs")
