#!/bin/bash
# FIX_DASHBOARD_METRICS.sh
# Location: scripts/maintenance/fix_dashboard_metrics.sh
# Purpose: Fix the dashboard UI to properly display system metrics

set -e

echo "🎨 FIXING DASHBOARD METRICS DISPLAY"
echo "===================================="
echo ""
echo "The backend is working (returns real data)"
echo "But the dashboard UI isn't displaying it"
echo ""
echo "Press Enter to fix the dashboard display..."
read

cd /home/pi/zoe

# Step 1: Check what the metrics endpoint returns
echo "📊 Verifying backend metrics endpoint..."
echo "Response from /api/developer/metrics:"
curl -s http://localhost:8000/api/developer/metrics | jq '.'
echo ""

# Step 2: Create the JavaScript to properly fetch and display metrics
echo "📝 Creating dashboard metrics updater..."
cat > services/zoe-ui/dist/developer/metrics.js << 'JSEOF'
// Dashboard Metrics Updater
// Fetches real metrics from backend and updates display

async function updateSystemMetrics() {
    try {
        // Fetch metrics from the backend
        const response = await fetch('/api/developer/metrics');
        const data = await response.json();
        
        // Update CPU Usage
        const cpuElement = document.querySelector('.metric-card:nth-child(1) .metric-value');
        if (cpuElement && data.cpu_percent !== undefined) {
            cpuElement.textContent = `${data.cpu_percent.toFixed(1)}%`;
            const cpuDetail = document.querySelector('.metric-card:nth-child(1) .metric-detail');
            if (cpuDetail) {
                cpuDetail.textContent = '4 cores available';
            }
        }
        
        // Update Memory Usage
        const memElement = document.querySelector('.metric-card:nth-child(2) .metric-value');
        if (memElement && data.memory_percent !== undefined) {
            memElement.textContent = `${data.memory_percent.toFixed(1)}%`;
            const memDetail = document.querySelector('.metric-card:nth-child(2) .metric-detail');
            if (memDetail && data.memory_used_gb !== undefined && data.memory_total_gb !== undefined) {
                memDetail.textContent = `${data.memory_used_gb}GB / ${data.memory_total_gb}GB`;
            }
        }
        
        // Update Disk Usage
        const diskElement = document.querySelector('.metric-card:nth-child(3) .metric-value');
        if (diskElement && data.disk_percent !== undefined) {
            diskElement.textContent = `${data.disk_percent.toFixed(1)}%`;
            const diskDetail = document.querySelector('.metric-card:nth-child(3) .metric-detail');
            if (diskDetail && data.disk_used_gb !== undefined && data.disk_total_gb !== undefined) {
                diskDetail.textContent = `${data.disk_used_gb}GB / ${data.disk_total_gb}GB`;
            }
        }
        
        // Update container count if displayed
        const containerElement = document.querySelector('.containers-count');
        if (containerElement && data.containers_running !== undefined) {
            containerElement.textContent = `${data.containers_running} containers running`;
        }
        
        // Add color coding based on usage levels
        [cpuElement, memElement, diskElement].forEach(elem => {
            if (elem && elem.textContent !== '--') {
                const value = parseFloat(elem.textContent);
                elem.style.color = value > 80 ? '#ef4444' : value > 60 ? '#f59e0b' : '#10b981';
            }
        });
        
    } catch (error) {
        console.error('Failed to fetch metrics:', error);
        // If fetch fails, try alternative method
        tryAlternativeMetricsFetch();
    }
}

// Alternative method if direct fetch fails
async function tryAlternativeMetricsFetch() {
    try {
        // Try fetching through chat endpoint
        const response = await fetch('/api/developer/chat', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({message: 'show system health'})
        });
        const data = await response.json();
        
        // Parse the response to extract metrics
        if (data.response) {
            const text = data.response;
            
            // Extract memory percentage
            const memMatch = text.match(/(\d+\.?\d*)%\s*used/);
            if (memMatch) {
                const memElement = document.querySelector('.metric-card:nth-child(2) .metric-value');
                if (memElement) memElement.textContent = `${memMatch[1]}%`;
            }
            
            // Extract disk percentage
            const diskMatch = text.match(/(\d+)%.*\/$/m);
            if (diskMatch) {
                const diskElement = document.querySelector('.metric-card:nth-child(3) .metric-value');
                if (diskElement) diskElement.textContent = `${diskMatch[1]}%`;
            }
        }
    } catch (error) {
        console.error('Alternative fetch also failed:', error);
    }
}

// Update metrics every 5 seconds
setInterval(updateSystemMetrics, 5000);

// Initial update on page load
document.addEventListener('DOMContentLoaded', () => {
    updateSystemMetrics();
    
    // Also ensure the metrics are visible
    const metricValues = document.querySelectorAll('.metric-value');
    metricValues.forEach(elem => {
        if (elem.textContent === '--' || !elem.textContent) {
            elem.textContent = 'Loading...';
            elem.style.fontSize = '24px';
            elem.style.fontWeight = 'bold';
        }
    });
});

// Export for use in other scripts
window.updateSystemMetrics = updateSystemMetrics;
JSEOF

# Step 3: Check current dashboard HTML structure
echo "🔍 Checking dashboard HTML structure..."
if grep -q "metrics.js" services/zoe-ui/dist/developer/index.html 2>/dev/null; then
    echo "metrics.js already included in dashboard"
else
    echo "Adding metrics.js to dashboard..."
    # Add script tag before closing body tag
    sed -i 's|</body>|<script src="metrics.js"></script>\n</body>|' services/zoe-ui/dist/developer/index.html
fi

# Step 4: Create inline fix if the dashboard structure is different
echo "📝 Creating inline metrics fix..."
cat > services/zoe-ui/dist/developer/dashboard-fix.html << 'HTMLEOF'
<script>
// Inline metrics updater - works regardless of dashboard structure
(function() {
    async function fixMetrics() {
        try {
            const response = await fetch('/api/developer/metrics');
            const data = await response.json();
            
            // Find all elements that might be metric displays
            const possibleMetricElements = [
                // Try different selectors
                document.querySelectorAll('.metric-value'),
                document.querySelectorAll('[class*="metric"]'),
                document.querySelectorAll('[class*="usage"]'),
                document.querySelectorAll('[id*="cpu"], [id*="memory"], [id*="disk"]')
            ];
            
            // Find CPU display
            possibleMetricElements.forEach(elements => {
                elements.forEach(elem => {
                    const text = elem.parentElement?.textContent || '';
                    if (text.toLowerCase().includes('cpu') && data.cpu_percent !== undefined) {
                        if (elem.textContent === '--' || elem.textContent === '') {
                            elem.textContent = `${data.cpu_percent.toFixed(1)}%`;
                            elem.style.fontSize = '24px';
                            elem.style.fontWeight = 'bold';
                        }
                    }
                    if (text.toLowerCase().includes('memory') && data.memory_percent !== undefined) {
                        if (elem.textContent === '--' || elem.textContent === '') {
                            elem.textContent = `${data.memory_percent.toFixed(1)}%`;
                            elem.style.fontSize = '24px';
                            elem.style.fontWeight = 'bold';
                        }
                    }
                    if (text.toLowerCase().includes('disk') && data.disk_percent !== undefined) {
                        if (elem.textContent === '--' || elem.textContent === '') {
                            elem.textContent = `${data.disk_percent.toFixed(1)}%`;
                            elem.style.fontSize = '24px';
                            elem.style.fontWeight = 'bold';
                        }
                    }
                });
            });
            
        } catch (error) {
            console.error('Metrics fetch failed:', error);
        }
    }
    
    // Run immediately and every 5 seconds
    fixMetrics();
    setInterval(fixMetrics, 5000);
})();
</script>
HTMLEOF

# Step 5: Apply the fix directly to the dashboard
echo "🔧 Applying metrics fix to dashboard..."
if ! grep -q "fixMetrics" services/zoe-ui/dist/developer/index.html 2>/dev/null; then
    # Add the inline script to the dashboard
    cat services/zoe-ui/dist/developer/dashboard-fix.html >> services/zoe-ui/dist/developer/index.html
fi

# Step 6: Restart UI service to ensure changes take effect
echo "🔄 Restarting UI service..."
docker compose restart zoe-ui
sleep 5

# Step 7: Test the metrics endpoint one more time
echo ""
echo "✅ Testing metrics endpoint:"
curl -s http://localhost:8000/api/developer/metrics | jq '.'

echo ""
echo "✨ DASHBOARD METRICS FIX COMPLETE!"
echo "==================================="
echo ""
echo "The dashboard should now display:"
echo "  • CPU Usage: Real percentage (e.g., 1.5%)"
echo "  • Memory Usage: Real percentage (e.g., 21.6%)"
echo "  • Disk Usage: Real percentage (e.g., 31.2%)"
echo ""
echo "📱 Please refresh the dashboard:"
echo "   http://192.168.1.60:8080/developer/"
echo ""
echo "If metrics still show '--', try:"
echo "  1. Hard refresh: Ctrl+Shift+R"
echo "  2. Clear browser cache"
echo "  3. Open browser console (F12) to check for errors"
echo ""
echo "The metrics should update every 5 seconds automatically!"
