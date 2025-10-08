# 🌊 Streaming Chat Implementation Guide

## Backend: ✅ COMPLETE
The backend now supports Server-Sent Events (SSE) streaming!

## Frontend: Update Required

### Step 1: Update `chat.html` sendMessage function

Replace the current `sendMessage()` function with:

```javascript
async function sendMessage() {
    const input = document.getElementById('chatInput');
    const message = input.value.trim();
    if (!message) return;

    // Add user message to chat
    addMessage(message, 'user');
    input.value = '';
    adjustTextareaHeight(input);

    // Show typing indicator
    showTypingIndicator();

    try {
        const session = window.zoeAuth?.getCurrentSession();
        
        // Use EventSource for streaming
        const url = new URL('https://192.168.1.60/api/chat');
        url.searchParams.set('user_id', session?.user_id || 'default');
        url.searchParams.set('stream', 'true');  // Enable streaming
        
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Session-ID': session?.session_id || ''
            },
            body: JSON.stringify({ 
                message: message,
                context: { mode: 'main_chat' },
                session_id: sessionId
            })
        });

        hideTypingIndicator();

        if (!response.ok) {
            addMessage('Sorry, I encountered an error. Please try again.', 'zoe');
            return;
        }

        // Handle streaming response
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let currentMessage = '';
        let messageDiv = null;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value);
            const lines = chunk.split('\n');

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        
                        if (data.type === 'metadata') {
                            // Create message div on first token
                            messageDiv = addStreamingMessage();
                        } else if (data.type === 'token') {
                            // Append token to message
                            currentMessage += data.content;
                            updateStreamingMessage(messageDiv, currentMessage);
                        } else if (data.type === 'done') {
                            // Streaming complete
                            break;
                        }
                    } catch (e) {
                        console.error('Parse error:', e);
                    }
                }
            }
        }

    } catch (error) {
        hideTypingIndicator();
        console.error('Chat error:', error);
        addMessage('Sorry, I encountered an error. Please check your connection.', 'zoe');
    }
}

// Helper function to create streaming message container
function addStreamingMessage() {
    const messagesContainer = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message zoe-message';
    messageDiv.innerHTML = '<span class="cursor">▊</span>';  // Blinking cursor
    messagesContainer.appendChild(messageDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    return messageDiv;
}

// Helper function to update streaming message
function updateStreamingMessage(messageDiv, content) {
    if (!messageDiv) return;
    messageDiv.innerHTML = content.replace(/\n/g, '<br>') + '<span class="cursor">▊</span>';
    const messagesContainer = document.getElementById('chatMessages');
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}
```

### Step 2: Add CSS for blinking cursor

Add to your CSS:

```css
.cursor {
    animation: blink 1s infinite;
    margin-left: 2px;
}

@keyframes blink {
    0%, 49% { opacity: 1; }
    50%, 100% { opacity: 0; }
}
```

### Step 3: Test!

1. Open https://zoe.local/chat.html
2. Type a message
3. Watch tokens appear instantly as they generate!

## Expected Behavior

**Before:** Wait 8 seconds, then see full response
**After:** See first words in <1 second, complete in 8 seconds

**Perceived Latency:** < 1 second (feels instant!)

## Benefits

✅ Instant feedback - user sees response immediately
✅ Better UX - feels like a real conversation
✅ Same 8s total time, but feels 8x faster
✅ No hardware changes needed
✅ ChatGPT-style experience

## Backend API

**Endpoint:** `/api/chat?stream=true`
**Response:** Server-Sent Events (SSE)
**Format:**
```
data: {"type": "metadata", "routing": "conversation", "memories": 0}
data: {"type": "token", "content": "Hello"}
data: {"type": "token", "content": " there"}
data: {"type": "token", "content": "!"}
data: {"type": "done"}
```

## Notes

- Streaming is optional - set `stream=false` for old behavior
- All advanced features still work (RouteLLM, mem-agent, etc.)
- Compatible with all browsers that support Fetch API
- Falls back gracefully on errors

---

**Status:** Backend ✅ Complete | Frontend ⏳ Needs update in Claude
