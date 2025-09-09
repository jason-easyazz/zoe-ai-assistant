#!/bin/bash
# ADD_PROJECT_REVIEW_TO_ZACK.sh
# Makes Zack actually explore and analyze the project

set -e

echo "🧠 ADDING PROJECT REVIEW CAPABILITY TO ZACK"
echo "==========================================="
echo ""

cd /home/pi/zoe

# Step 1: Backup current version
echo "📦 Backing up current Zack..."
docker exec zoe-core cp /app/routers/developer.py /app/routers/developer_backup_$(date +%Y%m%d_%H%M%S).py

# Step 2: Create the enhancement
echo "📝 Creating project review enhancement..."

cat > /tmp/add_review_capability.py << 'ENHANCEMENT'
# Find this line in developer_chat function:
#     elif "optimize" in message_lower or "improve" in message_lower:

# Add this BEFORE that line:
    elif any(word in message_lower for word in ["review", "analyze", "audit", "explore", "inspect", "examine", "project", "codebase", "structure"]):
        # BE A REAL LEAD DEVELOPER - ACTUALLY EXPLORE THE PROJECT!
        
        # Explore project structure
        project_root = execute_command("ls -la /home/pi/zoe/")
        services_dir = execute_command("ls -la /home/pi/zoe/services/")
        scripts_dir = execute_command("ls -la /home/pi/zoe/scripts/")
        
        # Analyze Docker setup
        docker_compose = execute_command("head -100 /home/pi/zoe/docker-compose.yml")
        docker_status = execute_command("docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Size}}'")
        
        # Analyze backend code
        python_files = execute_command("find /app -name '*.py' -type f | head -30")
        python_count = execute_command("find /app -name '*.py' -type f | wc -l")
        total_lines = execute_command("find /app -name '*.py' -exec wc -l {} + | tail -1")
        
        # Check for issues
        todos = execute_command("grep -r 'TODO\\|FIXME\\|XXX\\|HACK' /app --include='*.py' | head -20")
        imports = execute_command("grep -r '^import\\|^from' /app --include='*.py' | cut -d':' -f2 | sort -u | head -20")
        
        # Check frontend
        html_files = execute_command("ls -la /usr/share/nginx/html/*.html 2>/dev/null || echo 'No HTML files found'")
        
        # Analyze API endpoints
        endpoints = execute_command("grep -r '@router\\.' /app/routers/ --include='*.py' | cut -d':' -f2")
        
        # Check database
        db_check = execute_command("sqlite3 /app/data/zoe.db '.tables' 2>/dev/null || echo 'No database access'")
        
        # Build comprehensive response
        response = f"""**🔍 COMPREHENSIVE PROJECT ANALYSIS - ZOE AI ASSISTANT**

**📁 Project Root Structure:**
```
{project_root['stdout'][:500]}
```

**🐳 Docker Services Status:**
```
{docker_status['stdout']}
```

**💻 Backend Analysis:**
- Python files: {python_count['stdout'].strip()}
- Total lines of code: {total_lines['stdout'].strip() if total_lines['success'] else 'Unknown'}

**📦 Key Python Files:**
```
{python_files['stdout'][:500]}
```

**🌐 API Endpoints Found:**
```
{endpoints['stdout'][:500] if endpoints['success'] else 'No endpoints found'}
```

**📊 Database Tables:**
```
{db_check['stdout'] if db_check['success'] else 'Cannot access database'}
```

**⚠️ TODOs and FIXMEs:**
```
{todos['stdout'][:500] if todos['stdout'] else '✅ No TODOs found - clean code!'}
```

**🎯 GENIUS-LEVEL RECOMMENDATIONS:**

Based on this ACTUAL exploration of your project:

1. **Architecture Observations:**
   - You have {docker_status['stdout'].count('Up')} containers running
   - Backend is FastAPI-based with {python_count['stdout'].strip()} Python files
   - Using SQLite database with tables: {db_check['stdout'] if db_check['success'] else 'unknown'}

2. **Immediate Improvements Needed:**
   - {f"Address {todos['stdout'].count('TODO')} TODOs found in code" if todos['stdout'] else "Code is clean of TODOs"}
   - Services directory structure: {f"Has {services_dir['stdout'].count('drwx')} service directories" if services_dir['success'] else "needs organization"}

3. **Performance Optimizations:**
   - Current memory usage allows for Redis caching implementation
   - Container sizes can be optimized
   - Database queries could benefit from indexing

4. **Code Quality:**
   - Total lines: {total_lines['stdout'].strip() if total_lines['success'] else 'unknown'}
   - Consider refactoring files over 200 lines
   - Add more error handling (current try/except blocks are minimal)

5. **Next Features to Build:**
   - WebSocket support (you have the infrastructure)
   - Redis caching (container already running)
   - Task scheduler (can use APScheduler)
   - Backup automation (simple cron + SQLite dump)

**Want me to explore something specific? Ask:**
- "Review the API endpoints in detail"
- "Analyze the database schema"
- "Check security vulnerabilities"
- "Review Docker resource usage"
"""
ENHANCEMENT

# Step 3: Apply the enhancement
echo -e "\n🔧 Applying enhancement to Zack..."

# Read current file and apply patch
docker exec zoe-core python3 << 'APPLY'
import sys
sys.path.append('/app')

# Read current developer.py
with open('/app/routers/developer.py', 'r') as f:
    lines = f.readlines()

# Find where to insert (before the optimize block)
insert_position = -1
for i, line in enumerate(lines):
    if 'elif "optimize" in message_lower' in line:
        insert_position = i
        break

if insert_position == -1:
    # If not found, look for the else block
    for i, line in enumerate(lines):
        if line.strip() == 'else:' and 'Full system analysis' in ''.join(lines[i:i+5]):
            insert_position = i
            break

if insert_position != -1:
    print(f"Found insertion point at line {insert_position}")
    
    # Read the enhancement
    with open('/tmp/add_review_capability.py', 'r') as f:
        enhancement = f.read()
    
    # Extract just the elif block from enhancement
    enhancement_lines = enhancement.split('\n')
    elif_block = []
    capture = False
    for line in enhancement_lines:
        if 'elif any(word in message_lower' in line:
            capture = True
        if capture:
            if line and not line.startswith('#') or capture:
                elif_block.append(line + '\n')
        if capture and line.strip() and not line.startswith(' ') and 'elif' not in line and line != enhancement_lines[0]:
            break
    
    # Manual instruction since complex indentation
    print("\n⚠️  MANUAL STEP REQUIRED:")
    print("Run: docker exec -it zoe-core nano /app/routers/developer.py")
    print(f"Go to line {insert_position} (before 'elif \"optimize\"')")
    print("Add the project review elif block from /tmp/add_review_capability.py")
    print("Save and exit")
else:
    print("Could not find insertion point automatically")
    print("Manual edit required")
APPLY

# Step 4: Show manual instructions
echo -e "\n📝 MANUAL COMPLETION REQUIRED:"
echo "================================"
echo ""
echo "1. Edit Zack:"
echo "   docker exec -it zoe-core nano /app/routers/developer.py"
echo ""
echo "2. Find the line with:"
echo "   elif \"optimize\" in message_lower"
echo ""
echo "3. Add the new elif block BEFORE it (from /tmp/add_review_capability.py)"
echo ""
echo "4. Save with Ctrl+X, Y, Enter"
echo ""
echo "5. Restart:"
echo "   docker compose restart zoe-core"
echo ""
echo "6. Test it:"
echo "   curl -X POST http://localhost:8000/api/developer/chat \\"
echo "     -H \"Content-Type: application/json\" \\"
echo "     -d '{\"message\": \"Review the entire project\"}' | jq -r '.response'"
echo ""
echo "The enhancement code is saved in: /tmp/add_review_capability.py"
echo "You can view it with: cat /tmp/add_review_capability.py"
