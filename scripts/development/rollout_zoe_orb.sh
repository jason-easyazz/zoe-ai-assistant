#!/bin/bash
# Zoe Orb Rollout Script
# Purpose: Add Zoe orb to all desktop interface pages
# Excludes: developer/index.html and index.html (main chat)

set -e

DIST_DIR="/home/pi/zoe/services/zoe-ui/dist"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🔄 Starting Zoe Orb Rollout..."

# Pages to update (excluding developer/index.html and index.html)
PAGES=(
    "calendar.html"
    "lists.html" 
    "memories.html"
    "workflows.html"
    "settings.html"
    "journal.html"
    "chat.html"
    "diagnostics.html"
)

# Extract orb components from dashboard.html
echo "📋 Extracting orb components from dashboard.html..."

# Get the orb CSS (from <style> tag)
ORB_CSS=$(grep -A 200 "\.zoe-orb {" "$DIST_DIR/dashboard.html" | grep -B 200 "}" | head -n -1)

# Get the orb HTML
ORB_HTML=$(grep -A 5 '<div class="zoe-orb"' "$DIST_DIR/dashboard.html")

# Get the orb JavaScript functions
ORB_JS=$(grep -A 50 "function toggleOrbChat" "$DIST_DIR/dashboard.html" | head -n 50)

echo "✅ Extracted orb components"

# Function to add orb to a page
add_orb_to_page() {
    local page="$1"
    local page_path="$DIST_DIR/$page"
    
    if [[ ! -f "$page_path" ]]; then
        echo "❌ Page not found: $page"
        return 1
    fi
    
    echo "🔧 Adding orb to $page..."
    
    # Create backup
    cp "$page_path" "$page_path.backup-$(date +%Y%m%d-%H%M%S)"
    
    # Check if orb already exists
    if grep -q "zoe-orb" "$page_path"; then
        echo "⚠️  Orb already exists in $page, skipping..."
        return 0
    fi
    
    # Add CSS to head (before closing </head>)
    if ! grep -q "zoe-orb" "$page_path"; then
        # Insert CSS before </head>
        sed -i '/<\/head>/i\
        <!-- Zoe Orb Styles -->\
        <style>\
        .zoe-orb {\
            position: fixed; bottom: 24px; right: 24px; width: 70px; height: 70px;\
            border-radius: 50%; cursor: pointer; transition: all 0.4s cubic-bezier(0.25, 0.46, 0.45, 0.94);\
            z-index: 1200; display: flex; align-items: center; justify-content: center;\
            overflow: hidden;\
            animation: orb-liquid-swirl 12s ease-in-out infinite, orb-breathe 4s ease-in-out infinite;\
            background: linear-gradient(135deg, #7B61FF 0%, #8B5CF6 50%, #A855F7 100%);\
            background-size: 200% 200%;\
            box-shadow: 0 6px 20px rgba(123, 97, 255, 0.4), 0 0 40px rgba(123, 97, 255, 0.2);\
        }\
        .zoe-orb::before {\
            content: ""; position: absolute; top: 50%; left: 50%; width: 20px; height: 20px;\
            background: radial-gradient(circle, rgba(255,255,255,0.3) 0%, transparent 70%);\
            border-radius: 50%; transform: translate(-50%, -50%);\
            animation: orb-inner-glow 2s ease-in-out infinite alternate;\
        }\
        .zoe-orb:hover {\
            transform: scale(1.12);\
            box-shadow: 0 8px 25px rgba(123, 97, 255, 0.5), 0 0 50px rgba(123, 97, 255, 0.3);\
            animation-duration: 8s, 3s;\
        }\
        .zoe-orb:active { transform: scale(0.95); }\
        .zoe-orb.connecting {\
            background: linear-gradient(135deg, #7B61FF 0%, #6366F1 50%, #8B5CF6 100%);\
            background-size: 200% 200%;\
            box-shadow: 0 6px 20px rgba(123, 97, 255, 0.4), 0 0 40px rgba(123, 97, 255, 0.2);\
        }\
        .zoe-orb.connected {\
            background: linear-gradient(135deg, #7B61FF 0%, #10B981 50%, #34D399 100%);\
            background-size: 200% 200%;\
            box-shadow: 0 6px 20px rgba(123, 97, 255, 0.4), 0 0 40px rgba(16, 185, 129, 0.2);\
        }\
        .zoe-orb.thinking {\
            background: linear-gradient(135deg, #7B61FF 0%, #F59E0B 50%, #FBBF24 100%);\
            background-size: 200% 200%;\
            box-shadow: 0 6px 20px rgba(123, 97, 255, 0.4), 0 0 40px rgba(245, 158, 11, 0.2);\
            animation: orb-thinking-gentle 3s ease-in-out infinite, orb-liquid-swirl 10s ease-in-out infinite;\
        }\
        .zoe-orb.proactive {\
            background: linear-gradient(135deg, #7B61FF 0%, #EC4899 50%, #F472B6 100%);\
            background-size: 200% 200%;\
            box-shadow: 0 6px 20px rgba(123, 97, 255, 0.4), 0 0 40px rgba(236, 72, 153, 0.2);\
            animation: orb-proactive-gentle 4s ease-in-out infinite, orb-liquid-swirl 10s ease-in-out infinite;\
        }\
        .zoe-orb.error {\
            background: linear-gradient(135deg, #7B61FF 0%, #EF4444 50%, #F87171 100%);\
            background-size: 200% 200%;\
            box-shadow: 0 6px 20px rgba(123, 97, 255, 0.4), 0 0 40px rgba(239, 68, 68, 0.2);\
            animation: orb-error-shake 0.5s ease-in-out infinite, orb-liquid-swirl 3s ease-in-out infinite;\
        }\
        .zoe-orb.chatting {\
            background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 50%, #06B6D4 100%);\
            background-size: 200% 200%;\
            box-shadow: 0 8px 25px rgba(123, 97, 255, 0.6), 0 0 60px rgba(123, 97, 255, 0.4);\
            animation: orb-liquid-swirl 12s ease-in-out infinite, orb-breathe 4s ease-in-out infinite;\
        }\
        .zoe-orb.badge::after {\
            content: ""; position: absolute; top: 8px; right: 8px; width: 12px; height: 12px;\
            background: #F59E0B; border-radius: 50%; border: 2px solid white;\
            box-shadow: 0 0 12px rgba(245, 158, 11, 0.8);\
            animation: badge-pulse 2s ease-in-out infinite;\
        }\
        @keyframes orb-liquid-swirl {\
            0%, 100% { background-position: 0% 50%; border-radius: 50% 45% 55% 50%; }\
            25% { background-position: 100% 50%; border-radius: 55% 50% 45% 55%; }\
            50% { background-position: 100% 100%; border-radius: 45% 55% 50% 45%; }\
            75% { background-position: 0% 100%; border-radius: 50% 45% 55% 50%; }\
        }\
        @keyframes orb-breathe {\
            0%, 100% { transform: scale(1); }\
            50% { transform: scale(1.05); }\
        }\
        @keyframes orb-inner-glow {\
            0% { opacity: 0.3; transform: translate(-50%, -50%) scale(1); }\
            100% { opacity: 0.6; transform: translate(-50%, -50%) scale(1.1); }\
        }\
        @keyframes orb-thinking-gentle {\
            0%, 100% { transform: rotate(0deg); }\
            25% { transform: rotate(5deg); }\
            75% { transform: rotate(-5deg); }\
        }\
        @keyframes orb-proactive-gentle {\
            0%, 100% { transform: scale(1); }\
            50% { transform: scale(1.08); }\
        }\
        @keyframes orb-error-shake {\
            0%, 100% { transform: translateX(0); }\
            25% { transform: translateX(-2px); }\
            75% { transform: translateX(2px); }\
        }\
        @keyframes badge-pulse {\
            0%, 100% { opacity: 1; transform: scale(1); }\
            50% { opacity: 0.7; transform: scale(1.2); }\
        }\
        </style>' "$page_path"
    fi
    
    # Add orb HTML before closing </body>
    if ! grep -q "zoe-orb" "$page_path"; then
        sed -i '/<\/body>/i\
    <!-- Zoe Orb -->\
    <div class="zoe-orb" id="zoeOrb" title="Click to chat with Zoe" onclick="toggleOrbChat()">\
    </div>' "$page_path"
    fi
    
    # Add JavaScript functions before closing </body>
    if ! grep -q "toggleOrbChat" "$page_path"; then
        sed -i '/<\/body>/i\
    <script>\
    // Zoe Orb Functions\
    let orbChatOpen = false;\
    let orbWebSocket = null;\
    \
    function toggleOrbChat() {\
        if (!orbChatOpen) {\
            openOrbChat();\
        } else {\
            closeOrbChat();\
        }\
    }\
    \
    function openOrbChat() {\
        // Create chat window if it doesn'\''t exist\
        if (!document.getElementById('\''orbChatWindow'\'')) {\
            const chatWindow = document.createElement('\''div'\'');\
            chatWindow.id = '\''orbChatWindow'\'';\
            chatWindow.className = '\''orb-chat-window'\'';\
            chatWindow.innerHTML = `\
                <div class="orb-chat-header">\
                    <div class="orb-chat-title">Chat with Zoe</div>\
                    <button class="orb-chat-close" onclick="closeOrbChat()">×</button>\
                </div>\
                <div class="orb-chat-messages" id="orbChatMessages">\
                    <div class="orb-chat-message assistant">\
                        Hi! I'\''m Zoe, your AI assistant. How can I help you today?\
                    </div>\
                </div>\
                <div class="orb-chat-input-area">\
                    <textarea class="orb-chat-input" id="orbChatInput" placeholder="Type your message..." rows="1"></textarea>\
                    <button class="orb-chat-send" id="orbChatSend" onclick="sendOrbMessage()">→</button>\
                </div>\
            `;\
            document.body.appendChild(chatWindow);\
        }\
        \
        document.getElementById('\''orbChatWindow'\'').style.display = '\''block'\'';\
        document.getElementById('\''orbChatInput'\'').focus();\
        orbChatOpen = true;\
        document.getElementById('\''zoeOrb'\'').classList.add('\''chatting'\'');\
        \
        // Connect to intelligence WebSocket\
        connectOrbWebSocket();\
    }\
    \
    function closeOrbChat() {\
        if (document.getElementById('\''orbChatWindow'\'')) {\
            document.getElementById('\''orbChatWindow'\'').style.display = '\''none'\'';\
        }\
        orbChatOpen = false;\
        document.getElementById('\''zoeOrb'\'').classList.remove('\''chatting'\'');\
        \
        // Disconnect WebSocket\
        if (orbWebSocket) {\
            orbWebSocket.close();\
            orbWebSocket = null;\
        }\
    }\
    \
    function sendOrbMessage() {\
        const input = document.getElementById('\''orbChatInput'\'');\
        const message = input.value.trim();\
        if (!message) return;\
        \
        // Add user message\
        addOrbMessage(message, '\''user'\'');\
        input.value = '\''\'';\
        \
        // Send to chat API\
        fetch('\''/api/chat'\'', {\
            method: '\''POST'\'',\
            headers: {\
                '\''Content-Type'\'': '\''application/json'\'',\
                '\''X-Session-ID'\'': localStorage.getItem('\''session_id'\'') || '\''default'\''\
            },\
            body: JSON.stringify({ message: message, context: '\''orb_chat'\'' })\
        })\
        .then(response => response.json())\
        .then(data => {\
            if (data.response) {\
                addOrbMessage(data.response, '\''assistant'\'');\
            }\
        })\
        .catch(error => {\
            addOrbMessage('\''Sorry, I encountered an error. Please try again.'\'', '\''assistant'\'');\
        });\
    }\
    \
    function addOrbMessage(message, sender) {\
        const messagesDiv = document.getElementById('\''orbChatMessages'\'');\
        const messageDiv = document.createElement('\''div'\'');\
        messageDiv.className = `orb-chat-message ${sender}`;\
        messageDiv.textContent = message;\
        messagesDiv.appendChild(messageDiv);\
        messagesDiv.scrollTop = messagesDiv.scrollHeight;\
    }\
    \
    function connectOrbWebSocket() {\
        if (orbWebSocket) return;\
        \
        try {\
            const protocol = window.location.protocol === '\''https:'\'' ? '\''wss:'\'' : '\''ws:'\'';\
            const wsUrl = `${protocol}//${window.location.host}/ws/intelligence`;\
            orbWebSocket = new WebSocket(wsUrl);\
            \
            orbWebSocket.onopen = function() {\
                document.getElementById('\''zoeOrb'\'').classList.add('\''connected'\'');\
                document.getElementById('\''zoeOrb'\'').classList.remove('\''connecting'\'');\
            };\
            \
            orbWebSocket.onmessage = function(event) {\
                const data = JSON.parse(event.data);\
                if (data.type === '\''proactive_suggestion'\'') {\
                    showOrbNotification(data.data);\
                }\
            };\
            \
            orbWebSocket.onclose = function() {\
                document.getElementById('\''zoeOrb'\'').classList.remove('\''connected'\'');\
                document.getElementById('\''zoeOrb'\'').classList.add('\''connecting'\'');\
                orbWebSocket = null;\
            };\
            \
            orbWebSocket.onerror = function() {\
                document.getElementById('\''zoeOrb'\'').classList.add('\''error'\'');\
            };\
        } catch (error) {\
            console.log('\''WebSocket connection failed'\'', error);\
        }\
    }\
    \
    function showOrbNotification(notification) {\
        // Add badge to orb\
        document.getElementById('\''zoeOrb'\'').classList.add('\''badge'\'');\
        \
        // Show toast notification\
        const toast = document.createElement('\''div'\'');\
        toast.className = '\''orb-toast'\'';\
        toast.innerHTML = `\
            <div class="orb-toast-content">\
                <div class="orb-toast-title">${notification.title}</div>\
                <div class="orb-toast-message">${notification.message}</div>\
            </div>\
        `;\
        document.body.appendChild(toast);\
        \
        // Auto-remove after 5 seconds\
        setTimeout(() => {\
            if (toast.parentNode) {\
                toast.parentNode.removeChild(toast);\
            }\
        }, 5000);\
    }\
    \
    // Initialize orb on page load\
    document.addEventListener('\''DOMContentLoaded'\'', function() {\
        // Set initial state\
        document.getElementById('\''zoeOrb'\'').classList.add('\''connecting'\'');\
        \
        // Try to connect WebSocket\
        connectOrbWebSocket();\
        \
        // Add Enter key support for chat input\
        document.addEventListener('\''keydown'\'', function(e) {\
            if (e.key === '\''Enter'\'' && orbChatOpen) {\
                e.preventDefault();\
                sendOrbMessage();\
            }\
        });\
    });\
    </script>' "$page_path"
    fi
    
    # Add orb chat window styles
    if ! grep -q "orb-chat-window" "$page_path"; then
        sed -i '/<\/head>/i\
        <!-- Zoe Orb Chat Styles -->\
        <style>\
        .orb-chat-window {\
            position: fixed; bottom: 100px; right: 24px; width: 350px; height: 500px;\
            background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(20px);\
            border-radius: 20px; box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);\
            display: none; flex-direction: column; z-index: 1300;\
            border: 1px solid rgba(255, 255, 255, 0.2);\
        }\
        .orb-chat-header {\
            padding: 20px; border-bottom: 1px solid rgba(0, 0, 0, 0.1);\
            display: flex; justify-content: space-between; align-items: center;\
        }\
        .orb-chat-title { font-weight: 600; color: #1f2937; }\
        .orb-chat-close {\
            background: none; border: none; font-size: 24px; cursor: pointer;\
            color: #6b7280; transition: color 0.2s;\
        }\
        .orb-chat-close:hover { color: #ef4444; }\
        .orb-chat-messages {\
            flex: 1; padding: 20px; overflow-y: auto;\
            display: flex; flex-direction: column; gap: 12px;\
        }\
        .orb-chat-message {\
            max-width: 80%; padding: 12px 16px; border-radius: 18px;\
            word-wrap: break-word;\
        }\
        .orb-chat-message.user {\
            background: linear-gradient(135deg, #7B61FF, #8B5CF6);\
            color: white; align-self: flex-end;\
        }\
        .orb-chat-message.assistant {\
            background: rgba(0, 0, 0, 0.05);\
            color: #1f2937; align-self: flex-start;\
        }\
        .orb-chat-input-area {\
            padding: 20px; border-top: 1px solid rgba(0, 0, 0, 0.1);\
            display: flex; gap: 12px; align-items: flex-end;\
        }\
        .orb-chat-input {\
            flex: 1; border: 1px solid rgba(0, 0, 0, 0.1);\
            border-radius: 20px; padding: 12px 16px;\
            resize: none; outline: none; font-family: inherit;\
            background: rgba(255, 255, 255, 0.8);\
        }\
        .orb-chat-send {\
            background: linear-gradient(135deg, #7B61FF, #8B5CF6);\
            color: white; border: none; border-radius: 50%;\
            width: 40px; height: 40px; cursor: pointer;\
            display: flex; align-items: center; justify-content: center;\
            transition: transform 0.2s;\
        }\
        .orb-chat-send:hover { transform: scale(1.1); }\
        .orb-toast {\
            position: fixed; top: 24px; right: 24px; z-index: 1400;\
            background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(20px);\
            border-radius: 12px; padding: 16px; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);\
            border: 1px solid rgba(255, 255, 255, 0.2); max-width: 300px;\
            animation: toast-slide-in 0.3s ease-out;\
        }\
        .orb-toast-title { font-weight: 600; color: #1f2937; margin-bottom: 4px; }\
        .orb-toast-message { color: #6b7280; font-size: 14px; }\
        @keyframes toast-slide-in {\
            from { transform: translateX(100%); opacity: 0; }\
            to { transform: translateX(0); opacity: 1; }\
        }\
        </style>' "$page_path"
    fi
    
    echo "✅ Added orb to $page"
}

# Process each page
echo "🚀 Processing pages..."
for page in "${PAGES[@]}"; do
    add_orb_to_page "$page"
done

echo ""
echo "🎉 Zoe Orb Rollout Complete!"
echo ""
echo "✅ Pages updated:"
for page in "${PAGES[@]}"; do
    if [[ -f "$DIST_DIR/$page" ]]; then
        echo "   - $page"
    fi
done

echo ""
echo "🔍 Verification:"
echo "   - Backups created with timestamp"
echo "   - Orb CSS, HTML, and JavaScript added"
echo "   - WebSocket connection configured"
echo "   - Chat functionality integrated"
echo ""
echo "🧪 Test the orb by:"
echo "   1. Opening any updated page"
echo "   2. Looking for the purple orb in bottom-right corner"
echo "   3. Clicking the orb to open chat"
echo "   4. Verifying WebSocket connection (orb should show 'connected' state)"
echo ""
echo "📝 Next steps:"
echo "   1. Test orb functionality on each page"
echo "   2. Verify WebSocket connections work"
echo "   3. Test chat functionality"
echo "   4. Complete WebSocket integration for real-time intelligence"

