#!/usr/bin/env python3
"""
Fix memories.py router - replace Query(None) with proper authentication
"""
import re

filepath = '/home/zoe/assistant/services/zoe-core/routers/memories.py'

with open(filepath, 'r') as f:
    content = f.read()

print("Fixing memories.py authentication...")

# Pattern: user_id: str = Query(None, description=...),
# Replace with proper session authentication and remove the user_id parameter entirely
# since these already have session: AuthenticatedSession

# Find all functions with both user_id Query(None) AND session: AuthenticatedSession
# In these cases, we should REMOVE the user_id Query parameter

pattern = r'(\s+)user_id:\s*str\s*=\s*Query\(None[^)]*\),\n(\s+)(session:\s*(?:Optional\[)?AuthenticatedSession(?:\])?\s*=\s*Depends\([^)]+\))'

def replacement(match):
    indent = match.group(1)
    session_line = match.group(3)
    # Just return the session line, removing user_id completely
    return f'{indent}{session_line}'

content = re.sub(pattern, replacement, content)

# Also handle the case where session comes AFTER user_id parameter  
# And handle cases where session is Optional
fixes = [
    # Remove user_id when session exists
    (r'user_id:\s*str\s*=\s*Query\(None[^)]*\),\s*\n\s*session:\s*Optional\[AuthenticatedSession\]\s*=\s*Depends\([^)]+\)', 
     'session: AuthenticatedSession = Depends(validate_session)'),
]

for pattern, repl in fixes:
    content = re.sub(pattern, repl, content)

# Fix the specific case in get_memories where session is Optional and set to None
content = content.replace(
    'session: Optional[AuthenticatedSession] = Depends(lambda: None)',
    'session: AuthenticatedSession = Depends(validate_session)'
)

# Now ensure all these functions extract user_id = session.user_id
# by adding it at the start of each function that uses session

with open(filepath, 'w') as f:
    f.write(content)

print("✅ memories.py fixed")
print("⚠️  Manual verification needed for complex cases")

