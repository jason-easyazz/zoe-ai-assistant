#!/bin/bash
# Kill ALL purple backgrounds

echo "🔪 KILLING PURPLE BACKGROUNDS"
echo "============================"

cd /home/pi/zoe

# Find and replace ALL gradient backgrounds in the HTML
cat > services/zoe-ui/dist/developer/style_override.css << 'EOF'
/* OVERRIDE ALL BACKGROUNDS */
.message-content,
.message.claude .message-content,
.message.user .message-content,
.glass-morphic,
.chat-bubble,
.message-bubble {
    background: #ffffff !important;
    background-image: none !important;
    border: 1px solid #e5e7eb !important;
    box-shadow: none !important;
}

.message.claude .message-content {
    border-left: 3px solid #3b82f6 !important;
}

.message.user .message-content {
    border-right: 3px solid #10b981 !important;
}

/* Remove gradient from body if needed */
body .message-content {
    background: white !important;
}
EOF

# Inject the override CSS directly into the HTML with !important
sed -i '/<\/head>/i <style>.message-content{background:#fff!important;background-image:none!important;}.glass-morphic{background:#fff!important;}</style>' services/zoe-ui/dist/developer/index.html

# Also check if there's inline styles in the HTML
sed -i 's/background:.*linear-gradient[^;]*;//g' services/zoe-ui/dist/developer/index.html
sed -i 's/background:.*purple[^;]*;//g' services/zoe-ui/dist/developer/index.html
sed -i 's/background:.*#9333ea[^;]*;//g' services/zoe-ui/dist/developer/index.html
sed -i 's/background:.*#667eea[^;]*;//g' services/zoe-ui/dist/developer/index.html

echo "✅ Purple backgrounds killed!"
echo ""
echo "Force refresh with: Ctrl+F5"
echo "Or open in new incognito window"
