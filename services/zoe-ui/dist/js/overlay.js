// Overlay interactions and shopping list management for Zoe v3.1 interface
let backendConnected = true;

function showStatus(message) {
    console.log(message);
}

function openChatWindow() {
    const overlay = document.getElementById('chatOverlay');
    if (overlay) {
        overlay.classList.add('active');
        setTimeout(() => {
            const input = document.getElementById('chatInputOverlay');
            if (input) input.focus();
        }, 300);
        showStatus('Chat window opened');
    }
}

function closeChatWindow() {
    const overlay = document.getElementById('chatOverlay');
    if (overlay) {
        overlay.classList.remove('active');
        showStatus('Chat window closed');
    }
}

function addMessageToOverlay(content, sender) {
    const container = document.getElementById('chatMessagesOverlay');
    if (!container) return;

    const messageDiv = document.createElement('div');
    messageDiv.style.cssText = `
        margin-bottom: 12px;
        display: flex;
        gap: 10px;
        ${sender === 'user' ? 'flex-direction: row-reverse;' : ''}
    `;

    const avatar = document.createElement('div');
    avatar.style.cssText = `
        width: 32px;
        height: 32px;
        border-radius: 50%;
        background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        color: white;
        font-size: 12px;
        font-weight: 500;
        flex-shrink: 0;
    `;
    avatar.textContent = sender === 'user' ? 'Y' : 'Z';

    const messageContent = document.createElement('div');
    messageContent.style.cssText = `
        ${sender === 'user' ? 'background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%); color: white;' : 'background: rgba(255, 255, 255, 0.8);'}
        padding: 12px 16px;
        border-radius: 18px;
        max-width: 70%;
        font-size: 14px;
        line-height: 1.4;
    `;
    messageContent.textContent = content;

    messageDiv.appendChild(avatar);
    messageDiv.appendChild(messageContent);

    container.appendChild(messageDiv);
    container.scrollTop = container.scrollHeight;
}

function sendMessageFromOverlay() {
    const input = document.getElementById('chatInputOverlay');
    const message = input.value.trim();
    if (!message) return;

    addMessageToOverlay(message, 'user');
    input.value = '';
    showTypingIndicatorInOverlay();

    fetch('http://localhost:8000/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message })
    })
    .then(response => response.json())
    .then(data => {
        hideTypingIndicatorInOverlay();
        addMessageToOverlay(data.response || 'I received your message!', 'assistant');
    })
    .catch(error => {
        console.error('Chat error:', error);
        hideTypingIndicatorInOverlay();
        addMessageToOverlay('Sorry, I encountered an error. Please try again.', 'assistant');
    });
}

function showTypingIndicatorInOverlay() {
    const container = document.getElementById('chatMessagesOverlay');
    if (!container || document.getElementById('typingIndicatorOverlay')) return;

    const typingDiv = document.createElement('div');
    typingDiv.id = 'typingIndicatorOverlay';
    typingDiv.style.cssText = `
        margin-bottom: 12px;
        display: flex;
        gap: 10px;
    `;

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = 'Z';
    avatar.style.cssText = `
        width: 32px;
        height: 32px;
        border-radius: 50%;
        background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        color: white;
        font-size: 12px;
        font-weight: 500;
    `;

    const indicator = document.createElement('div');
    indicator.className = 'typing-indicator';
    indicator.innerHTML = '<span></span><span></span><span></span>';

    typingDiv.appendChild(avatar);
    typingDiv.appendChild(indicator);
    container.appendChild(typingDiv);
    container.scrollTop = container.scrollHeight;
}

function hideTypingIndicatorInOverlay() {
    const typingIndicator = document.getElementById('typingIndicatorOverlay');
    if (typingIndicator) {
        typingIndicator.remove();
    }
}

function toggleVoiceInOverlay() {
    isListening = !isListening;
    if (isListening) {
        startListening();
    } else {
        stopListening();
    }
}

function openShoppingWindow() {
    const overlay = document.getElementById('shoppingOverlay');
    if (overlay) {
        overlay.classList.add('active');
        setTimeout(() => {
            const input = document.getElementById('shoppingInput');
            if (input) input.focus();
        }, 300);
        showStatus('Shopping list opened');
    }
}

function closeShoppingWindow() {
    const overlay = document.getElementById('shoppingOverlay');
    if (overlay) {
        overlay.classList.remove('active');
        showStatus('Shopping list closed');
    }
}

function addShoppingItem() {
    const input = document.getElementById('shoppingInput');
    const itemText = input.value.trim();
    if (!itemText) return;

    const list = document.getElementById('shoppingList');
    const itemDiv = document.createElement('div');
    itemDiv.className = 'shopping-item';
    itemDiv.innerHTML = `
        <div class="shopping-checkbox" onclick="toggleShoppingItem(this)"></div>
        <div class="shopping-text">${itemText}</div>
        <button class="shopping-delete" onclick="deleteShoppingItem(this)">×</button>
    `;
    list.appendChild(itemDiv);
    input.value = '';
    showStatus(`Added "${itemText}" to shopping list`);

    if (backendConnected) {
        fetch('/api/shopping/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item: itemText })
        }).catch(() => console.log('Shopping API not available'));
    }
}

function toggleShoppingItem(checkbox) {
    const item = checkbox.parentElement;
    const text = item.querySelector('.shopping-text');
    checkbox.classList.toggle('checked');
    text.classList.toggle('completed');
    const isCompleted = checkbox.classList.contains('checked');
    const itemText = text.textContent;
    showStatus(`${isCompleted ? 'Checked off' : 'Unchecked'}: ${itemText}`);

    if (backendConnected) {
        fetch('/api/shopping/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item: itemText, completed: isCompleted })
        }).catch(() => console.log('Shopping update not synced'));
    }
}

function deleteShoppingItem(button) {
    const item = button.parentElement;
    const itemText = item.querySelector('.shopping-text').textContent;
    item.remove();
    showStatus(`Deleted "${itemText}" from shopping list`);

    if (backendConnected) {
        fetch('/api/shopping/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item: itemText })
        }).catch(() => console.log('Shopping delete not synced'));
    }
}

function switchPanel(panel) {
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => item.classList.remove('active'));

    if (panel !== 'settings' && typeof event !== 'undefined' && event.target) {
        event.target.classList.add('active');
    }

    if (panel === 'shopping') {
        openShoppingWindow();
    } else if (panel === 'settings') {
        showStatus('Opening settings...');
        console.log('Opening settings panel');
    } else {
        showStatus(`Switched to ${panel}`);
        console.log(`Navigating to ${panel} panel`);
    }
}

function quickAction(action) {
    switch(action) {
        case 'journal':
            openChatWindow();
            setTimeout(() => {
                addMessageToOverlay("Let's create a journal entry! What's on your mind today? You can write about your thoughts, experiences, or anything you'd like to remember.", 'assistant');
            }, 500);
            showStatus('Ready to create journal entry');
            break;
        case 'event':
            openChatWindow();
            setTimeout(() => {
                const input = document.getElementById('chatInputOverlay');
                if (input) {
                    input.value = 'Schedule an event: ';
                    input.focus();
                }
            }, 300);
            showStatus('Ready to schedule an event');
            break;
        case 'task':
            openChatWindow();
            setTimeout(() => {
                const input = document.getElementById('chatInputOverlay');
                if (input) {
                    input.value = 'Create a new task: ';
                    input.focus();
                }
            }, 300);
            showStatus('Ready to add a new task');
            break;
        case 'shopping':
            openShoppingWindow();
            break;
    }
}

document.addEventListener('DOMContentLoaded', function() {
    const chatInputOverlay = document.getElementById('chatInputOverlay');
    if (chatInputOverlay) {
        chatInputOverlay.addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessageFromOverlay();
            }
        });
    }

    const shoppingInput = document.getElementById('shoppingInput');
    if (shoppingInput) {
        shoppingInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                addShoppingItem();
            }
        });
    }

    const chatOverlay = document.getElementById('chatOverlay');
    if (chatOverlay) {
        chatOverlay.addEventListener('click', function(e) {
            if (e.target === this) {
                closeChatWindow();
            }
        });
    }

    const shoppingOverlay = document.getElementById('shoppingOverlay');
    if (shoppingOverlay) {
        shoppingOverlay.addEventListener('click', function(e) {
            if (e.target === this) {
                closeShoppingWindow();
            }
        });
    }
});
