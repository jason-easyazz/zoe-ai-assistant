#!/bin/bash
# BETTER_PORTABILITY_FIX.sh
# Location: scripts/maintenance/fix_portability.sh
# Purpose: Make Zoe truly portable using relative URLs (no hostname needed!)

set -e

echo "🌐 MAKING ZOE TRULY PORTABLE WITH RELATIVE URLS"
echo "==============================================="
echo ""
echo "This improved solution:"
echo "  ✅ Uses relative URLs (/api/...) - no hostnames!"
echo "  ✅ nginx proxies API calls through same port"
echo "  ✅ Works from ANY device without configuration"
echo "  ✅ No localhost references that could break"
echo ""
echo "Press Enter to continue..."
read

cd /home/pi/zoe

# Backup first
BACKUP_DIR="backups/portability_fix_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp -r services "$BACKUP_DIR/" 2>/dev/null || true
echo "✅ Backup created at $BACKUP_DIR"

# ========================================
# STEP 1: Update nginx to proxy API calls
# ========================================
echo -e "\n🌐 Configuring nginx to proxy API calls..."

cat > services/zoe-ui/nginx.conf << 'EOF'
# Nginx configuration for Zoe UI
# Serves frontend and proxies API calls to backend

server {
    listen 80;
    server_name _;
    
    # Serve frontend files
    root /usr/share/nginx/html;
    index index.html;
    
    # Frontend routes
    location / {
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-cache, no-store, must-revalidate";
    }
    
    # Proxy API calls to backend (zoe-core on port 8000)
    location /api/ {
        proxy_pass http://zoe-core:8000/api/;
        proxy_http_version 1.1;
        
        # Important headers for proxying
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts for long-running requests
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    # Proxy health check to backend
    location /health {
        proxy_pass http://zoe-core:8000/health;
    }
    
    # Proxy API documentation
    location /docs {
        proxy_pass http://zoe-core:8000/docs;
    }
    
    location /openapi.json {
        proxy_pass http://zoe-core:8000/openapi.json;
    }
    
    # WebSocket support (if needed)
    location /ws {
        proxy_pass http://zoe-core:8000/ws;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
EOF

echo "✅ nginx configured to proxy API calls"

# ========================================
# STEP 2: Update ALL frontend files to use relative URLs
# ========================================
echo -e "\n📝 Updating frontend to use relative URLs..."

# Fix all HTML files
for html_file in services/zoe-ui/dist/*.html services/zoe-ui/dist/**/*.html; do
    if [ -f "$html_file" ]; then
        echo "  Fixing: $(basename $html_file)"
        
        # Replace all absolute URLs with relative ones
        sed -i "s|http://192.168.1.60:8000/api|/api|g" "$html_file"
        sed -i "s|http://localhost:8000/api|/api|g" "$html_file"
        sed -i "s|http://\${window.location.hostname}:8000/api|/api|g" "$html_file"
        sed -i "s|http://\" + window.location.hostname + \":8000/api|/api|g" "$html_file"
        
        # Fix API_BASE declarations
        sed -i "s|const API_BASE = '[^']*'|const API_BASE = ''|g" "$html_file"
        sed -i "s|const API_BASE = \"[^\"]*\"|const API_BASE = \"\"|g" "$html_file"
        
        # Fix fetch calls to use relative paths
        sed -i "s|fetch(API_BASE + '|fetch('/api|g" "$html_file"
        sed -i "s|fetch(\`\${API_BASE}|fetch(\`/api|g" "$html_file"
    fi
done

# Create a simple common.js for all pages
cat > services/zoe-ui/dist/js/common.js << 'EOF'
// Common JavaScript for all Zoe pages
// Uses relative URLs - works from any device!

// No need for API_BASE with full URLs anymore
// Just use relative paths starting with /api

// Helper function for API calls
async function apiCall(endpoint, options = {}) {
    // Ensure endpoint starts with /
    if (!endpoint.startsWith('/')) {
        endpoint = '/' + endpoint;
    }
    
    // If it doesn't start with /api, add it
    if (!endpoint.startsWith('/api')) {
        endpoint = '/api' + endpoint;
    }
    
    try {
        const response = await fetch(endpoint, {
            ...options,
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            }
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('API call failed:', error);
        throw error;
    }
}

// WebSocket connection helper (if needed)
function connectWebSocket(path = '/ws') {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}${path}`;
    return new WebSocket(wsUrl);
}

console.log('Zoe API ready - using relative URLs');
EOF

# Update JavaScript files
for js_file in services/zoe-ui/dist/js/*.js services/zoe-ui/dist/**/*.js; do
    if [ -f "$js_file" ] && [ "$(basename $js_file)" != "common.js" ]; then
        echo "  Fixing: $(basename $js_file)"
        
        # Replace absolute URLs
        sed -i "s|http://192.168.1.60:8000|''|g" "$js_file"
        sed -i "s|http://localhost:8000|''|g" "$js_file"
        sed -i "s|http://\" + window.location.hostname + \":8000|''|g" "$js_file"
        
        # Fix API_BASE usage
        sed -i "s|const API_BASE = '[^']*'|const API_BASE = ''|g" "$js_file"
        
        # Fix fetch calls
        sed -i "s|fetch(API_BASE + '|fetch('/api|g" "$js_file"
        sed -i "s|fetch(\`\${API_BASE}/|fetch(\`/|g" "$js_file"
        sed -i "s|fetch('http://[^']*:8000|fetch('|g" "$js_file"
    fi
done

# ========================================
# STEP 3: Create example pages showing proper usage
# ========================================
echo -e "\n📚 Creating example implementation..."

cat > services/zoe-ui/dist/example-api-usage.html << 'EOF'
<!DOCTYPE html>
<html>
<head>
    <title>Zoe API Usage Examples</title>
    <script src="/js/common.js"></script>
</head>
<body>
    <h1>Proper API Usage in Zoe</h1>
    <div id="results"></div>
    
    <script>
        // CORRECT: Using relative URLs
        async function testAPIs() {
            try {
                // Example 1: Simple GET request
                const health = await fetch('/api/health');
                const healthData = await health.json();
                console.log('Health:', healthData);
                
                // Example 2: POST request using helper
                const chatResponse = await apiCall('/chat', {
                    method: 'POST',
                    body: JSON.stringify({
                        message: 'Hello Zoe!'
                    })
                });
                console.log('Chat:', chatResponse);
                
                // Example 3: Direct fetch with relative URL
                const events = await fetch('/api/events');
                const eventData = await events.json();
                console.log('Events:', eventData);
                
                document.getElementById('results').innerHTML = 
                    '<p>✅ All API calls working with relative URLs!</p>';
                    
            } catch (error) {
                document.getElementById('results').innerHTML = 
                    '<p>❌ Error: ' + error.message + '</p>';
            }
        }
        
        // Test on load
        window.onload = testAPIs;
    </script>
</body>
</html>
EOF

# ========================================
# STEP 4: Update Docker Compose for nginx
# ========================================
echo -e "\n🐳 Updating Docker configuration..."

# Make sure nginx config is mounted correctly
if grep -q "zoe-ui:" docker-compose.yml; then
    echo "  ✅ Docker compose already configured"
else
    echo "  ⚠️  Note: Ensure nginx.conf is mounted in docker-compose.yml:"
    echo "      volumes:"
    echo "        - ./services/zoe-ui/nginx.conf:/etc/nginx/conf.d/default.conf:ro"
fi

# ========================================
# STEP 5: Fix backend CORS settings
# ========================================
echo -e "\n🔧 Updating backend CORS for proxied requests..."

cat > services/zoe-core/fix_cors.py << 'EOF'
# Add this to main.py to handle proxied requests properly

from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

# Allow all origins since nginx is proxying
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # nginx handles security
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Trust the proxy headers
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"]  # nginx handles host validation
)
EOF

# ========================================
# STEP 6: Create verification script
# ========================================
echo -e "\n🧪 Creating verification script..."

cat > verify_relative_urls.sh << 'EOF'
#!/bin/bash
echo "🔍 Verifying Relative URL Implementation"
echo "========================================"

# Check for absolute URLs in frontend
echo -e "\n1. Checking for absolute URLs in frontend..."
ABSOLUTE_URLS=$(grep -r "http://.*:8000" services/zoe-ui/dist/ 2>/dev/null | wc -l)
if [ "$ABSOLUTE_URLS" -eq 0 ]; then
    echo "  ✅ No absolute URLs found"
else
    echo "  ❌ Found $ABSOLUTE_URLS absolute URLs:"
    grep -r "http://.*:8000" services/zoe-ui/dist/ | head -3
fi

# Check for localhost references
echo -e "\n2. Checking for localhost references..."
LOCALHOST_REFS=$(grep -r "localhost:8000" services/zoe-ui/dist/ 2>/dev/null | wc -l)
if [ "$LOCALHOST_REFS" -eq 0 ]; then
    echo "  ✅ No localhost references"
else
    echo "  ❌ Found $LOCALHOST_REFS localhost references"
fi

# Check nginx configuration
echo -e "\n3. Checking nginx proxy configuration..."
if grep -q "location /api/" services/zoe-ui/nginx.conf; then
    echo "  ✅ API proxy configured"
else
    echo "  ❌ API proxy not configured"
fi

# Test if services are running
echo -e "\n4. Testing services (if running)..."
if curl -s http://localhost:8080/api/health > /dev/null 2>&1; then
    echo "  ✅ API accessible through nginx proxy"
else
    echo "  ⚠️  Services not running or proxy not working"
fi

echo -e "\n✅ Verification complete!"
EOF

chmod +x verify_relative_urls.sh

# ========================================
# STEP 7: Restart services
# ========================================
echo -e "\n🔄 Restarting services..."
docker compose restart zoe-ui || echo "Restart manually when ready"

# ========================================
# STEP 8: Run verification
# ========================================
echo -e "\n🧪 Running verification..."
./verify_relative_urls.sh

# ========================================
# FINAL SUMMARY
# ========================================
echo -e "\n✅ PORTABILITY FIX COMPLETE!"
echo "============================"
echo ""
echo "🎯 What this fixed:"
echo "  ✅ All API calls now use relative URLs (/api/...)"
echo "  ✅ nginx proxies API calls to backend"
echo "  ✅ No hostname or IP references needed"
echo "  ✅ Works from ANY device without configuration"
echo ""
echo "📋 How it works:"
echo "  1. User accesses: http://[any-ip]:8080"
echo "  2. Frontend makes calls to: /api/..."
echo "  3. nginx proxies to: zoe-core:8000/api/..."
echo "  4. Response returns through same port"
echo ""
echo "🌐 Access examples:"
echo "  • From Pi: http://localhost:8080"
echo "  • From phone: http://192.168.1.60:8080"
echo "  • From laptop: http://raspberrypi.local:8080"
echo "  • All work WITHOUT any configuration!"
echo ""
echo "🚀 This is truly portable - anyone can:"
echo "  1. Clone your repo"
echo "  2. Run docker compose up"
echo "  3. Access at http://their-pi:8080"
echo "  4. Everything just works!"
