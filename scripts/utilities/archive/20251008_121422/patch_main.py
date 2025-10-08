#!/usr/bin/env python3
"""
Patch main.py to add enhancement routers
"""

# Read the current main.py
with open('/app/main.py', 'r') as f:
    content = f.read()

# Add imports after the existing router imports
import_location = content.find('from routers import vector_search, notifications')
if import_location != -1:
    # Find the end of that line
    end_of_line = content.find('\n', import_location)
    
    # Insert our imports
    new_imports = '\n# Import enhancement routers\nfrom routers import temporal_memory, cross_agent_collaboration, user_satisfaction'
    content = content[:end_of_line] + new_imports + content[end_of_line:]

# Add router includes after the existing includes
include_location = content.find('app.include_router(tool_registry.router)')
if include_location != -1:
    # Find the end of that line
    end_of_line = content.find('\n', include_location)
    
    # Insert our includes
    new_includes = '''
# Include enhancement routers
app.include_router(temporal_memory.router)
app.include_router(cross_agent_collaboration.router)
app.include_router(user_satisfaction.router)'''
    content = content[:end_of_line] + new_includes + content[end_of_line:]

# Add features to health check
features_location = content.find('"multi_expert_model",')
if features_location != -1:
    # Find the end of the features array
    array_end = content.find(']', features_location)
    
    # Insert our features before the closing bracket
    new_features = ''',
            "temporal_memory",  # New temporal & episodic memory
            "cross_agent_collaboration",  # New orchestration system
            "user_satisfaction_tracking",  # New satisfaction measurement
            "context_summarization_cache"  # New context caching'''
    content = content[:array_end] + new_features + content[array_end:]

# Write the patched main.py
with open('/app/main_patched.py', 'w') as f:
    f.write(content)

print("✅ main.py patched successfully")


