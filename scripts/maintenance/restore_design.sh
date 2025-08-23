#!/bin/bash
# Restore the full design, only fix message backgrounds

echo "🎨 RESTORING FULL DESIGN"
echo "======================="

cd /home/pi/zoe

# Restore the original developer.css with full design
cat > services/zoe-ui/dist/developer/css/developer.css << 'EOF'
/* Developer Dashboard - Full Design */
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    min-height: 100vh;
    color: #1f2937;
}

.container {
    max-width: 1400px;
    margin: 0 auto;
    padding: 20px;
}

.header {
    background: rgba(255, 255, 255, 0.95);
    backdrop-filter: blur(10px);
    border-radius: 16px;
    padding: 25px;
    margin-bottom: 20px;
    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
}

.header h1 {
    color: #1e40af;
    font-size: 28px;
    margin-bottom: 5px;
}

.status-bar {
    display: flex;
    gap: 20px;
    align-items: center;
    color: #6b7280;
}

.main-content {
    display: grid;
    grid-template-columns: 350px 1fr;
    gap: 20px;
}

.sidebar {
    display: flex;
    flex-direction: column;
    gap: 20px;
}

.status-panel,
.quick-actions,
.recent-tasks {
    background: rgba(255, 255, 255, 0.95);
    backdrop-filter: blur(10px);
    border-radius: 16px;
    padding: 20px;
    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
}

.chat-panel {
    background: rgba(255, 255, 255, 0.95);
    backdrop-filter: blur(10px);
    border-radius: 16px;
    padding: 20px;
    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
}

.system-status {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 15px;
    margin-top: 15px;
}

.status-item {
    text-align: center;
    padding: 10px;
    background: #f3f4f6;
    border-radius: 8px;
}

.status-icon {
    font-size: 24px;
    margin-bottom: 5px;
}

.quick-actions button {
    width: 100%;
    padding: 12px;
    margin: 5px 0;
    background: #3b82f6;
    color: white;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    font-size: 14px;
    transition: background 0.3s;
}

.quick-actions button:hover {
    background: #2563eb;
}

/* MESSAGES - Clean backgrounds only */
#messages {
    height: 450px;
    overflow-y: auto;
    padding: 15px;
    margin-bottom: 20px;
    background: #f9fafb;
    border-radius: 12px;
}

.message {
    margin: 12px 0;
    display: flex;
    align-items: flex-start;
}

.message-icon {
    font-size: 24px;
    margin: 0 10px;
}

/* Clean white message bubbles */
.message-content {
    background: #ffffff !important;
    border: 1px solid #e5e7eb;
    padding: 12px 16px;
    border-radius: 12px;
    max-width: 85%;
    line-height: 1.6;
    color: #1f2937;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}

.message.claude {
    justify-content: flex-start;
}

.message.claude .message-content {
    border-left: 3px solid #3b82f6;
    margin-left: 0;
}

.message.user {
    justify-content: flex-end;
}

.message.user .message-content {
    background: #f0f9ff !important;
    border-right: 3px solid #10b981;
}

.message.user .message-icon {
    order: 2;
    margin-left: 10px;
    margin-right: 0;
}

/* Input area */
.input-area {
    display: flex;
    gap: 10px;
}

#messageInput {
    flex: 1;
    padding: 14px;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    font-size: 15px;
    background: white;
}

#messageInput:focus {
    outline: none;
    border-color: #3b82f6;
}

.send-button {
    padding: 14px 28px;
    background: #3b82f6;
    color: white;
    border: none;
    border-radius: 12px;
    cursor: pointer;
    font-size: 15px;
    font-weight: 500;
    transition: background 0.3s;
}

.send-button:hover {
    background: #2563eb;
}

/* Ensure text is readable */
.message-content h3,
.message-content h4 {
    color: #1e40af;
    margin: 10px 0 5px 0;
}

.message-content h5 {
    color: #2563eb;
    margin: 8px 0 5px 0;
}

.message-content p,
.message-content div,
.message-content li {
    color: #374151;
}

.message-content strong,
.message-content b {
    color: #111827;
    font-weight: 600;
}
EOF

# Remove any style overrides that broke the design
sed -i '/<style>\.message-content{background:#fff/d' services/zoe-ui/dist/developer/index.html

echo "✅ Full design restored!"
echo ""
echo "The page now has:"
echo "• Beautiful gradient background"
echo "• Glass-morphic panels"
echo "• Clean white message bubbles (no purple)"
echo "• All original styling"
echo ""
echo "Refresh to see the restored design!"
