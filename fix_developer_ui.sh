#!/bin/bash
# Fix the developer UI by adding missing elements

echo "📝 Patching developer/index.html..."

# Add the missing currentTime element to the page
sed -i '/<div class="header-right">/a\
            <div class="current-time" id="currentTime">--:--</div>' services/zoe-ui/dist/developer/index.html 2>/dev/null

# If sed didn't work, try a different approach
if ! grep -q 'id="currentTime"' services/zoe-ui/dist/developer/index.html; then
    echo "Using Python to fix..."
    python3 << 'PYTHON'
import re

# Read the HTML file
with open('services/zoe-ui/dist/developer/index.html', 'r') as f:
    content = f.read()

# Check if currentTime element exists
if 'id="currentTime"' not in content:
    # Find the header-right div and add the time element
    if '<div class="header-right">' in content:
        content = content.replace(
            '<div class="header-right">',
            '<div class="header-right">\n            <div class="current-time" id="currentTime">--:--</div>'
        )
    else:
        # If no header-right, add it before </body>
        content = content.replace(
            '</body>',
            '<div id="currentTime" style="position: fixed; top: 10px; right: 10px; color: #666;">--:--</div>\n</body>'
        )
    
    # Write back
    with open('services/zoe-ui/dist/developer/index.html', 'w') as f:
        f.write(content)
    print("✅ Fixed missing currentTime element")
else:
    print("✅ currentTime element already exists")
PYTHON
fi

# Also fix the updateTime function to be more defensive
echo "🛡️ Making JavaScript more defensive..."
cat >> services/zoe-ui/dist/developer/index.html << 'JSFIX'
<script>
// Override updateTime to be defensive
window.updateTime = function() {
    const timeEl = document.getElementById('currentTime');
    if (timeEl) {
        const now = new Date();
        timeEl.textContent = now.toLocaleTimeString([], { 
            hour: 'numeric', 
            minute: '2-digit', 
            hour12: true 
        });
    }
};
</script>
JSFIX

echo "✅ Added defensive JavaScript"
