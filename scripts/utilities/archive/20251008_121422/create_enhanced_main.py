#!/usr/bin/env python3
"""
Create Enhanced Main.py
======================

Create a properly integrated main.py with enhancement systems.
"""

# Read the current main.py from container
import subprocess
import re

# Get the current main.py content
result = subprocess.run(['docker', 'exec', 'zoe-core-test', 'cat', '/app/main.py'], 
                       capture_output=True, text=True)
content = result.stdout

print("📝 Creating enhanced main.py...")

# Find the router imports section and add our imports
import_pattern = r'(from routers import.*?)\n'
matches = list(re.finditer(import_pattern, content, re.MULTILINE))

if matches:
    # Find the last import line
    last_match = matches[-1]
    insert_pos = last_match.end()
    
    # Add our enhancement imports
    enhancement_imports = """
# Import enhancement routers
try:
    from routers import temporal_memory, cross_agent_collaboration, user_satisfaction
    ENHANCEMENT_ROUTERS_AVAILABLE = True
    print("✅ Enhancement routers loaded successfully")
except ImportError as e:
    print(f"⚠️ Enhancement routers not available: {e}")
    ENHANCEMENT_ROUTERS_AVAILABLE = False
"""
    
    content = content[:insert_pos] + enhancement_imports + content[insert_pos:]

# Find the router includes section and add our includes
include_pattern = r'(app\.include_router\([^)]+\.router\))'
matches = list(re.finditer(include_pattern, content))

if matches:
    # Find the last include
    last_match = matches[-1]
    insert_pos = last_match.end()
    
    # Add our enhancement includes
    enhancement_includes = """

# Include enhancement routers if available
if ENHANCEMENT_ROUTERS_AVAILABLE:
    try:
        app.include_router(temporal_memory.router)
        app.include_router(cross_agent_collaboration.router)
        app.include_router(user_satisfaction.router)
        print("✅ Enhancement routers included successfully")
    except Exception as e:
        print(f"⚠️ Failed to include enhancement routers: {e}")
"""
    
    content = content[:insert_pos] + enhancement_includes + content[insert_pos:]

# Find the features list in health check and add our features
features_pattern = r'("features":\s*\[)(.*?)(\])'
match = re.search(features_pattern, content, re.DOTALL)

if match:
    features_start = match.group(1)
    features_content = match.group(2)
    features_end = match.group(3)
    
    # Add our features
    enhancement_features = ''',
            "temporal_memory",  # New temporal & episodic memory
            "cross_agent_collaboration",  # New orchestration system
            "user_satisfaction_tracking",  # New satisfaction measurement
            "context_summarization_cache"  # New context caching'''
    
    new_features = features_start + features_content + enhancement_features + features_end
    content = re.sub(features_pattern, new_features, content, flags=re.DOTALL)

# Write the enhanced main.py
with open('/app/main_enhanced.py', 'w') as f:
    f.write(content)

print("✅ Enhanced main.py created successfully")
print("📁 Saved as: /app/main_enhanced.py")


