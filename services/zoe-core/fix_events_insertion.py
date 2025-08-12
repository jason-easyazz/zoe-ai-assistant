import re
with open('main.py', 'r') as f:
    content = f.read()
content = re.sub(r'await db\.execute\(\"\"\"\s*INSERT INTO events \(title, start_date, source, integration_id, created_at\)\s*VALUES \(\?, \?, \?, \?, \?\)\s*\"\"\", \(\s*event\[\"title\"\],\s*event\.get\(\"date\", datetime\.now\(\)\.date\(\)\),\s*\"chat_detection\",\s*f\"conv_\{conversation_id\}\",\s*datetime\.now\(\),\s*\)\)\s*', '', content, flags=re.DOTALL)
with open('main.py', 'w') as f:
    f.write(content)
print("✅ Fixed duplicate database insertions")
