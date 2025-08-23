#!/bin/bash
# Remove all message background colors

echo "🎨 REMOVING MESSAGE COLORS"
echo "========================="

cd /home/pi/zoe

# Find and update the developer CSS
cat > services/zoe-ui/dist/developer/css/clean_style.css << 'EOF'
/* Clean message styling - no backgrounds */
.message {
    margin: 10px 0;
    display: flex;
    align-items: flex-start;
}

.message.claude {
    justify-content: flex-start;
}

.message.user {
    justify-content: flex-end;
}

.message-icon {
    font-size: 24px;
    margin: 0 10px;
}

.message-content {
    background: none !important;
    padding: 10px 15px;
    border-radius: 8px;
    max-width: 80%;
    border: 1px solid #e5e7eb;
    color: #1f2937;
}

.message.claude .message-content {
    background: #ffffff !important;
    border-left: 3px solid #3b82f6;
}

.message.user .message-content {
    background: #f8fafc !important;
    border-right: 3px solid #10b981;
}

/* Remove any purple/gradient backgrounds */
.glass-morphic,
.message-bubble {
    background: transparent !important;
}

/* Ensure text is readable */
.message-content h4,
.message-content h5,
.message-content div,
.message-content p {
    color: #1f2937 !important;
}
EOF

# Add the CSS to the HTML page
sed -i '/<\/head>/i <link rel="stylesheet" href="css/clean_style.css">' services/zoe-ui/dist/developer/index.html

# Also update inline styles in developer.js to remove backgrounds
sed -i 's/background:.*gradient.*;//g' services/zoe-ui/dist/developer/js/developer.js
sed -i 's/background:.*purple.*;//g' services/zoe-ui/dist/developer/js/developer.js
sed -i 's/background:.*#9333ea.*;//g' services/zoe-ui/dist/developer/js/developer.js

echo "✅ Removed all message colors!"
echo ""
echo "Messages now have:"
echo "• Clean white background"
echo "• Simple border"
echo "• No purple or gradients"
echo "• Better readability"
echo ""
echo "Clear cache and refresh: Ctrl+Shift+Delete then refresh"

