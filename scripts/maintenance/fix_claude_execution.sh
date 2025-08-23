#!/bin/bash
# Fix Claude execution in developer dashboard

echo "🔧 FIXING CLAUDE COMMAND EXECUTION"
echo "=================================="

# Test the execute endpoint directly
echo "Testing execute endpoint..."
curl -X POST http://localhost:8000/api/developer/execute \
  -H "Content-Type: application/json" \
  -d '{"command": "docker ps --format \"table {{.Names}}\\t{{.Status}}\"", "working_dir": "/app"}' | jq '.'

# Check developer.js has the right function
echo ""
echo "Checking developer.js..."
if grep -q "executeCommand" services/zoe-ui/dist/developer/js/developer.js; then
    echo "✅ executeCommand function exists"
else
    echo "❌ executeCommand function missing - adding it..."
    
    # Add the function to developer.js
    cat >> services/zoe-ui/dist/developer/js/developer.js << 'EOF'

// Execute system commands
async function executeCommand(command) {
    const response = await fetch(`${API_BASE}/developer/execute`, {
        method: 'POST',
        headers: {'Content-Type': application/json'},
        body: JSON.stringify({ command: command, working_dir: "/app" })
    });
    const result = await response.json();
    return result.success ? result.stdout : result.stderr;
}

// Make it global for Claude
window.executeCommand = executeCommand;
EOF
fi

echo ""
echo "✅ Done! Refresh the developer dashboard"
echo "Tell Claude: 'Actually run: docker ps'"
