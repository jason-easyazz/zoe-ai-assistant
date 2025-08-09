
        // Global state
        let isListening = false;
        let currentMode = 'orb';
        let backendConnected = false;
        
        // DOM Elements
        const orbMode = document.getElementById('orbMode');
        const mainInterface = document.getElementById('mainInterface');
        const fluidOrb = document.getElementById('fluidOrb');
        const chatInput = document.getElementById('chatInput');
        const voiceBtn = document.getElementById('voiceBtn');
        const statusIndicator = document.getElementById('statusIndicator');
        const chatMessagesOverlay = document.getElementById('chatMessagesOverlay');

        // Initialize
        document.addEventListener('DOMContentLoaded', function() {
            console.log('🚀 Zoe v3.1 Interface Loaded');
            updateTime();
            setInterval(updateTime, 1000);
            updateWeather();
            setInterval(updateWeather, 300000); // Every 5 minutes
            checkBackendConnection();
            setInterval(checkBackendConnection, 30000); // Every 30 seconds
            
            showStatus('Interface loaded successfully', 2000);
            
            // Focus chat input when interface is active
            mainInterface.addEventListener('transitionend', function() {
                if (mainInterface.classList.contains('active')) {
                    setTimeout(() => chatInput.focus(), 100);
                }
            });
            
            // Voice activation in orb mode (spacebar)
            document.addEventListener('keydown', function(e) {
                if (currentMode === 'orb' && e.code === 'Space') {
                    e.preventDefault();
                    toggleVoiceFromOrb();
                }
                // ESC to return to orb
                if (e.key === 'Escape' && currentMode === 'interface') {
                    exitToOrb();
                }
            });
            
            // Chat input enter key
            chatInput.addEventListener('keypress', function(e) {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    openChatWindow();
                }
            });

            // Chat overlay interactions
            setupChatOverlayEvents();
            setupShoppingOverlayEvents();
        });

        // Show status messages
        function showStatus(message, duration = 3000) {
            statusIndicator.textContent = message;
            statusIndicator.classList.add('show');
            setTimeout(() => {
                statusIndicator.classList.remove('show');
            }, duration);
        }

        // Check backend connection
        function checkBackendConnection() {
            fetch('/api/health')
                .then(response => {
                    if (response.ok) {
                        backendConnected = true;
                        console.log('✅ Backend connected');
                    } else {
                        backendConnected = false;
                    }
                })
                .catch(error => {
                    backendConnected = false;
                    console.log('⚠️ Backend not available (this is expected in standalone mode)');
                });
        }

        // Mode switching
        function enterInterface() {
            currentMode = 'interface';
            orbMode.classList.add('hidden');
            mainInterface.classList.add('active');
            showStatus('Interface activated');
        }

        function exitToOrb() {
            currentMode = 'orb';
            mainInterface.classList.remove('active');
            orbMode.classList.remove('hidden');
            showStatus('Returned to orb mode');
        }

        // Navigation - Updated to handle panels
        function switchPanel(panel) {
            const navItems = document.querySelectorAll('.nav-item');
            navItems.forEach(item => item.classList.remove('active'));
            
            // Hide main dashboard and show panels container
            const mainContainer = document.querySelector('.main-container');
            const panelsContainer = document.getElementById('panelsContainer');
            const panels = document.querySelectorAll('.panel');
            
            if (panel === 'dashboard') {
                // Show main dashboard
                mainContainer.style.display = 'flex';
                panelsContainer.classList.remove('active');
                if (event && event.target) event.target.classList.add('active');
                showStatus('Dashboard');
            } else if (panel === 'shopping') {
                openShoppingWindow();
            } else if (panel === 'settings') {
                // Show settings panel
                mainContainer.style.display = 'none';
                panelsContainer.classList.add('active');
                panels.forEach(p => p.classList.remove('active'));
                document.getElementById('settingsPanel').classList.add('active');
                showStatus('Settings opened');
            } else {
                // Show specific panel
                mainContainer.style.display = 'none';
                panelsContainer.classList.add('active');
                panels.forEach(p => p.classList.remove('active'));
                
                const targetPanel = document.getElementById(panel + 'Panel');
                if (targetPanel) {
                    targetPanel.classList.add('active');
                    if (event && event.target) event.target.classList.add('active');
                    showStatus(`${panel.charAt(0).toUpperCase() + panel.slice(1)} opened`);
                    
                    // Special initialization for calendar
                    if (panel === 'calendar') {
                        generateCalendar();
                    }
                }
            }
        }

        // Journal functions
        function selectMood(moodElement) {
            document.querySelectorAll('.mood-option').forEach(option => {
                option.classList.remove('selected');
            });
            moodElement.classList.add('selected');
            showStatus(`Mood selected: ${moodElement.dataset.mood}`);
        }

        function saveJournalEntry() {
            const selectedMood = document.querySelector('.mood-option.selected');
            const entryText = document.getElementById('journalText').value.trim();
            const tags = document.getElementById('journalTags').value.trim();
            
            if (!entryText) {
                showStatus('Please write something in your journal entry');
                return;
            }

            const moodEmoji = {
                'amazing': '😄',
                'good': '😊',
                'okay': '😐',
                'stressed': '😰',
                'sad': '😢'
            };

            const mood = selectedMood ? selectedMood.dataset.mood : 'okay';
            const emoji = moodEmoji[mood] || '💭';

            // Create new entry
            const entryDiv = document.createElement('div');
            entryDiv.className = 'entry-item';
            entryDiv.innerHTML = `
                <div class="entry-header">
                    <span class="entry-date">${new Date().toLocaleDateString()}, ${new Date().toLocaleTimeString()}</span>
                    <span class="entry-mood">${emoji}</span>
                </div>
                <p class="entry-text">${entryText}</p>
                ${tags ? `<div class="entry-tags">${tags}</div>` : ''}
            `;

            // Add to history
            const historyContainer = document.querySelector('.journal-history');
            historyContainer.insertBefore(entryDiv, historyContainer.children[1]);

            // Clear form
            document.getElementById('journalText').value = '';
            document.getElementById('journalTags').value = '';
            document.querySelectorAll('.mood-option').forEach(option => {
                option.classList.remove('selected');
            });

            showStatus('Journal entry saved successfully!');

            // Save to backend if connected
            if (backendConnected) {
                fetch('/api/journal/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        mood: mood,
                        content: entryText,
                        tags: tags.split(',').map(tag => tag.trim()).filter(tag => tag)
                    })
                })
                .catch(error => console.log('Journal API not available'));
            }
        }

        // Task functions
        function addNewTask() {
            const title = document.getElementById('newTaskTitle').value.trim();
            const priority = document.getElementById('taskPriority').value;
            const dueDate = document.getElementById('taskDueDate').value;
            
            if (!title) {
                showStatus('Please enter a task title');
                return;
            }

            const taskDiv = document.createElement('div');
            taskDiv.className = 'task-item-detailed';
            taskDiv.innerHTML = `
                <div class="task-checkbox" onclick="toggleTask(this)"></div>
                <div class="task-content">
                    <div class="task-title">${title}</div>
                    <div class="task-meta">${dueDate ? `Due: ${new Date(dueDate).toLocaleDateString()}` : 'No due date'} • ${priority.charAt(0).toUpperCase() + priority.slice(1)} Priority</div>
                </div>
            `;

            document.getElementById('allTasksList').appendChild(taskDiv);

            // Clear form
            document.getElementById('newTaskTitle').value = '';
            document.getElementById('taskDueDate').value = '';

            showStatus(`Task "${title}" added successfully!`);

            // Save to backend if connected
            if (backendConnected) {
                fetch('/api/tasks/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: title,
                        priority: priority,
                        due_date: dueDate || null
                    })
                })
                .catch(error => console.log('Tasks API not available'));
            }
        }

        function filterTasks(filter) {
            const filterBtns = document.querySelectorAll('.filter-btn');
            filterBtns.forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');

            const tasks = document.querySelectorAll('.task-item-detailed');
            tasks.forEach(task => {
                const isCompleted = task.querySelector('.task-checkbox').classList.contains('checked');
                const isHigh = task.querySelector('.task-meta').textContent.includes('High Priority');

                switch(filter) {
                    case 'all':
                        task.style.display = 'flex';
                        break;
                    case 'pending':
                        task.style.display = isCompleted ? 'none' : 'flex';
                        break;
                    case 'completed':
                        task.style.display = isCompleted ? 'flex' : 'none';
                        break;
                    case 'high':
                        task.style.display = isHigh ? 'flex' : 'none';
                        break;
                }
            });

            showStatus(`Showing ${filter} tasks`);
        }

        // Calendar functions
        let currentDate = new Date();

        function generateCalendar() {
            const grid = document.getElementById('calendarGrid');
            const monthTitle = document.getElementById('currentMonth');
            
            const year = currentDate.getFullYear();
            const month = currentDate.getMonth();
            
            monthTitle.textContent = new Date(year, month).toLocaleDateString('en-US', { 
                month: 'long', 
                year: 'numeric' 
            });
            
            // Clear existing calendar
            grid.innerHTML = '';
            
            // Add day headers
            const dayHeaders = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
            dayHeaders.forEach(day => {
                const dayDiv = document.createElement('div');
                dayDiv.className = 'calendar-day';
                dayDiv.style.fontWeight = 'bold';
                dayDiv.style.background = 'rgba(123, 97, 255, 0.1)';
                dayDiv.textContent = day;
                grid.appendChild(dayDiv);
            });
            
            // Get first day of month and number of days
            const firstDay = new Date(year, month, 1).getDay();
            const daysInMonth = new Date(year, month + 1, 0).getDate();
            const daysInPrevMonth = new Date(year, month, 0).getDate();
            
            // Add previous month's trailing days
            for (let i = firstDay - 1; i >= 0; i--) {
                const dayDiv = document.createElement('div');
                dayDiv.className = 'calendar-day other-month';
                dayDiv.textContent = daysInPrevMonth - i;
                grid.appendChild(dayDiv);
            }
            
            // Add current month's days
            const today = new Date();
            for (let day = 1; day <= daysInMonth; day++) {
                const dayDiv = document.createElement('div');
                dayDiv.className = 'calendar-day';
                dayDiv.textContent = day;
                
                // Mark today
                if (year === today.getFullYear() && month === today.getMonth() && day === today.getDate()) {
                    dayDiv.classList.add('today');
                }
                
                // Mark days with events (sample)
                if ([5, 12, 20, 25].includes(day)) {
                    dayDiv.classList.add('has-event');
                }
                
                dayDiv.addEventListener('click', () => {
                    document.querySelectorAll('.calendar-day').forEach(d => d.classList.remove('selected'));
                    dayDiv.classList.add('selected');
                    showStatus(`Selected ${monthTitle.textContent} ${day}`);
                });
                
                grid.appendChild(dayDiv);
            }
            
            // Add next month's leading days
            const totalCells = grid.children.length;
            const remainingCells = 42 - totalCells; // 6 rows × 7 days
            for (let day = 1; day <= remainingCells; day++) {
                const dayDiv = document.createElement('div');
                dayDiv.className = 'calendar-day other-month';
                dayDiv.textContent = day;
                grid.appendChild(dayDiv);
            }
        }

        function previousMonth() {
            currentDate.setMonth(currentDate.getMonth() - 1);
            generateCalendar();
            showStatus('Previous month');
        }

        function nextMonth() {
            currentDate.setMonth(currentDate.getMonth() + 1);
            generateCalendar();
            showStatus('Next month');
        }

        function addNewEvent() {
            const title = document.getElementById('eventTitle').value.trim();
            const date = document.getElementById('eventDate').value;
            const time = document.getElementById('eventTime').value;
            const description = document.getElementById('eventDescription').value.trim();
            
            if (!title || !date) {
                showStatus('Please enter event title and date');
                return;
            }

            // Clear form
            document.getElementById('eventTitle').value = '';
            document.getElementById('eventDate').value = '';
            document.getElementById('eventTime').value = '';
            document.getElementById('eventDescription').value = '';

            showStatus(`Event "${title}" scheduled for ${new Date(date).toLocaleDateString()}`);

            // Save to backend if connected
            if (backendConnected) {
                fetch('/api/events/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: title,
                        date: date,
                        time: time || null,
                        description: description || null
                    })
                })
                .catch(error => console.log('Events API not available'));
            }
        }

        // Settings functions
        function updatePersonality(type, value) {
            document.getElementById(type + 'Value').textContent = value;
            showStatus(`${type.charAt(0).toUpperCase() + type.slice(1)} level set to ${value}/10`);

            // Save to backend if connected
            if (backendConnected) {
                fetch('/api/settings/personality', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        [type]: parseInt(value)
                    })
                })
                .catch(error => console.log('Settings API not available'));
            }
        }

        function testVoice() {
            showStatus('Generating speech...');
            if (backendConnected) {
                fetch('/api/voice/tts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: 'Hello! This is how I sound when speaking aloud!' })
                })
                .then(res => res.blob())
                .then(blob => {
                    const url = URL.createObjectURL(blob);
                    new Audio(url).play();
                })
                .catch(() => showStatus('TTS failed'));
            } else {
                showStatus('Voice synthesis not available');
            }
        }

        function exportData() {
            showStatus('Preparing data export...');
            setTimeout(() => {
                showStatus('Data export ready! (Demo mode)');
            }, 2000);
        }

        function backupData() {
            showStatus('Creating backup...');
            setTimeout(() => {
                showStatus('Backup completed successfully!');
            }, 1500);
        }

        function clearData() {
            if (confirm('Are you sure you want to clear all data? This cannot be undone.')) {
                showStatus('All data cleared successfully!');
            }
        }

        // System functions
        function restartServices() {
            showStatus('Restarting services...');
            setTimeout(() => {
                showStatus('All services restarted successfully!');
            }, 2000);
        }

        function clearCache() {
            showStatus('Clearing cache...');
            setTimeout(() => {
                showStatus('Cache cleared successfully!');
            }, 1000);
        }

        function updateSystem() {
            showStatus('Checking for updates...');
            setTimeout(() => {
                showStatus('System is up to date!');
            }, 2000);
        }

        function viewLogs() {
            showStatus('Opening system logs...');
            console.log('System logs would be displayed here');
        }

        // Time and Date
        function updateTime() {
            const now = new Date();
            const timeString = now.toLocaleTimeString('en-US', {
                hour: 'numeric',
                minute: '2-digit',
                hour12: true
            });
            const dateString = now.toLocaleDateString('en-US', {
                weekday: 'long',
                month: 'long',
                day: 'numeric'
            });
            
            document.getElementById('currentTime').textContent = timeString;
            document.getElementById('currentDate').textContent = dateString;
        }

        // Weather
        function updateWeather() {
            const iconEl = document.getElementById('weatherIcon');
            const tempEl = document.getElementById('weatherTemp');
            iconEl.textContent = '…';
            tempEl.textContent = 'Loading';
            if (backendConnected) {
                fetch('/api/weather')
                    .then(response => response.json())
                    .then(data => {
                        iconEl.textContent = getWeatherIcon(data.condition || 'sunny');
                        tempEl.textContent = `${data.temperature || 23}°`;
                    })
                    .catch(error => {
                        console.log('Weather API not available, using defaults');
                        tempEl.textContent = 'Error';
                        setDefaultWeather();
                    });
            } else {
                setDefaultWeather();
            }
        }

        function setDefaultWeather() {
            document.getElementById('weatherIcon').textContent = getWeatherIcon('sunny');
            document.getElementById('weatherTemp').textContent = '23°';
        }

        function getWeatherIcon(condition) {
            const weatherIcons = {
                'sunny': '☀️',
                'clear': '☀️',
                'partly-cloudy': '⛅',
                'cloudy': '☁️',
                'rain': '🌧️',
                'thunderstorm': '⛈️',
                'snow': '❄️',
                'fog': '🌫️'
            };
            return weatherIcons[condition.toLowerCase()] || '🌤️';
        }

        // Voice functionality
        function toggleVoice() {
            isListening = !isListening;
            
            if (isListening) {
                startListening();
            } else {
                stopListening();
            }
        }

        function toggleVoiceFromOrb() {
            isListening = !isListening;
            
            if (isListening) {
                fluidOrb.className = 'fluid-orb listening';
                startListening();
                showStatus('Listening...');
            } else {
                fluidOrb.className = 'fluid-orb idle';
                stopListening();
                showStatus('Stopped listening');
            }
        }

        function startListening() {
            voiceBtn.classList.add('active');
            
            if (backendConnected) {
                showStatus('Listening for voice input...');
            } else {
                console.log('Voice recording started (standalone mode)');
                showStatus('Voice recording started (demo mode)');
            }
        }

        function stopListening() {
            voiceBtn.classList.remove('active');
            
            if (backendConnected) {
                fetch('/api/voice/stt', {
                    method: 'POST'
                })
                .then(response => response.json())
                .then(data => {
                    if (data.text) {
                        chatInput.value = data.text;
                        openChatWindow();
                    } else {
                        showStatus('No transcription received', 2000);
                    }
                })
                .catch(error => {
                    console.log('Voice API not available');
                    showStatus('Voice input failed', 2000);
                });
            } else {
                setTimeout(() => {
                    const demoInputs = [
                        "Hello Zoe, how are you today?",
                        "What's the weather like?",
                        "Add a new task for me",
                        "Tell me about my schedule"
                    ];
                    const randomInput = demoInputs[Math.floor(Math.random() * demoInputs.length)];
                    chatInput.value = randomInput;
                    showStatus('Voice input received (demo mode)');
                }, 1000);
            }
        }

        // Chat overlay functions
        function openChatWindow() {
            const chatOverlay = document.getElementById('chatOverlay');
            chatOverlay.classList.add('active');
            
            setTimeout(() => {
                document.getElementById('chatInputOverlay').focus();
            }, 300);
            
            showStatus('Chat opened');
        }

        function closeChatWindow() {
            const chatOverlay = document.getElementById('chatOverlay');
            chatOverlay.classList.remove('active');
            showStatus('Chat closed');
        }

        function sendMessageFromOverlay() {
            const input = document.getElementById('chatInputOverlay');
            const message = input.value.trim();
            if (!message) return;

            addMessageToOverlay(message, 'user');
            input.value = '';

            showTypingIndicatorInOverlay();

            if (backendConnected) {
                const msgEl = addMessageToOverlay('', 'assistant');
                const evtSource = new EventSource(`/api/chat/stream?prompt=${encodeURIComponent(message)}`);
                evtSource.onmessage = (e) => {
                    if (e.data === '[DONE]') {
                        hideTypingIndicatorInOverlay();
                        evtSource.close();
                    } else {
                        msgEl.textContent += e.data;
                    }
                };
                evtSource.onerror = () => {
                    hideTypingIndicatorInOverlay();
                    showStatus('Chat connection error');
                    evtSource.close();
                };
            } else {
                setTimeout(() => {
                    hideTypingIndicatorInOverlay();
                    const demoResponse = generateDemoResponse(message);
                    addMessageToOverlay(demoResponse, 'assistant');
                }, 1500);
            }
        }

        function addMessageToOverlay(content, sender) {
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${sender}`;

            const avatar = document.createElement('div');
            avatar.className = 'message-avatar';
            avatar.textContent = sender === 'user' ? 'Y' : 'Z';

            const messageContent = document.createElement('div');
            messageContent.className = 'message-content';
            messageContent.textContent = content;

            messageDiv.appendChild(avatar);
            messageDiv.appendChild(messageContent);

            chatMessagesOverlay.appendChild(messageDiv);
            chatMessagesOverlay.scrollTop = chatMessagesOverlay.scrollHeight;
            return messageContent;
        }

        function showTypingIndicatorInOverlay() {
            const typingDiv = document.createElement('div');
            typingDiv.className = 'message assistant typing';
            typingDiv.id = 'typingIndicatorOverlay';
            
            const avatar = document.createElement('div');
            avatar.className = 'message-avatar';
            avatar.textContent = 'Z';
            
            const indicator = document.createElement('div');
            indicator.className = 'typing-indicator';
            indicator.innerHTML = '<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>';
            
            typingDiv.appendChild(avatar);
            typingDiv.appendChild(indicator);
            
            chatMessagesOverlay.appendChild(typingDiv);
            chatMessagesOverlay.scrollTop = chatMessagesOverlay.scrollHeight;
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

        // Shopping list functionality
        function openShoppingWindow() {
            const shoppingOverlay = document.getElementById('shoppingOverlay');
            shoppingOverlay.classList.add('active');
            
            setTimeout(() => {
                document.getElementById('shoppingInput').focus();
            }, 300);
            
            showStatus('Shopping list opened');
        }

        function closeShoppingWindow() {
            const shoppingOverlay = document.getElementById('shoppingOverlay');
            shoppingOverlay.classList.remove('active');
            showStatus('Shopping list closed');
        }

        function addShoppingItem() {
            const input = document.getElementById('shoppingInput');
            const itemText = input.value.trim();
            if (!itemText) return;

            const shoppingList = document.getElementById('shoppingList');
            const itemDiv = document.createElement('div');
            itemDiv.className = 'shopping-item';
            
            itemDiv.innerHTML = `
                <div class="shopping-checkbox" onclick="toggleShoppingItem(this)"></div>
                <div class="shopping-text">${itemText}</div>
                <button class="delete-item-btn" onclick="deleteShoppingItem(this)">×</button>
            `;
            
            shoppingList.appendChild(itemDiv);
            input.value = '';
            
            showStatus(`Added "${itemText}" to shopping list`);
            
            if (backendConnected) {
                fetch('/api/shopping/add', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ item: itemText })
                })
                .catch(error => console.log('Shopping API not available'));
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
                    body: JSON.stringify({
                        item: itemText,
                        completed: isCompleted
                    })
                })
                .catch(error => console.log('Shopping update not synced'));
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
                })
                .catch(error => console.log('Shopping delete not synced'));
            }
        }

        // Quick actions
        function quickAction(action) {
            switch(action) {
                case 'journal':
                    openChatWindow();
                    setTimeout(() => {
                        addMessageToOverlay('Let\'s create a journal entry! What\'s on your mind today? You can write about your thoughts, experiences, or anything you\'d like to remember.', 'assistant');
                    }, 500);
                    showStatus('Ready to create journal entry');
                    break;
                    
                case 'event':
                    openChatWindow();
                    setTimeout(() => {
                        document.getElementById('chatInputOverlay').value = 'Schedule an event: ';
                        document.getElementById('chatInputOverlay').focus();
                    }, 300);
                    showStatus('Ready to schedule an event');
                    break;
                    
                case 'task':
                    openChatWindow();
                    setTimeout(() => {
                        document.getElementById('chatInputOverlay').value = 'Create a new task: ';
                        document.getElementById('chatInputOverlay').focus();
                    }, 300);
                    showStatus('Ready to add a new task');
                    break;

                case 'shopping':
                    openShoppingWindow();
                    break;
            }
        }

        // Task management
        function toggleTask(checkbox) {
            const taskItem = checkbox.parentElement;
            const taskText = taskItem.querySelector('.task-text');
            
            checkbox.classList.toggle('checked');
            taskText.classList.toggle('completed');
            
            const isCompleted = checkbox.classList.contains('checked');
            const taskContent = taskText.textContent;
            
            showStatus(`Task ${isCompleted ? 'completed' : 'unchecked'}: ${taskContent}`);
            
            if (backendConnected) {
                const taskId = taskItem.dataset.taskId || Math.random().toString(36).substr(2, 9);
                fetch('/api/tasks/update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        task_id: taskId,
                        completed: isCompleted
                    })
                })
                .catch(error => {
                    console.log('Task update not synced (demo mode)');
                });
            }
        }

        // Demo response generator
        function generateDemoResponse(message) {
            const responses = [
                "That's interesting! I'm running in demo mode right now, but I can see your message clearly.",
                "I'd love to help with that! Once my backend is connected, I'll have access to all my smart features.",
                "Great question! I'm Zoe, your AI companion. Right now I'm showing you the beautiful interface we've built together.",
                "I can see the interface is working perfectly! The orb animations, chat system, and all the UI elements are functioning beautifully.",
                "This new v3.1 interface is pretty amazing, isn't it? Glass morphic design, fluid animations, and touch-optimized for your Pi!",
                "I'm excited to be fully connected soon! Then I can help with tasks, journal entries, weather updates, and so much more."
            ];
            
            const lowerMessage = message.toLowerCase();
            if (lowerMessage.includes('weather')) {
                return "I can see the weather widget is working! Once connected to a weather API, I'll give you live updates.";
            } else if (lowerMessage.includes('task')) {
                return "I'd love to help you manage tasks! You can see the task list on the dashboard, and soon I'll be able to add and update them dynamically.";
            } else if (lowerMessage.includes('hello') || lowerMessage.includes('hi')) {
                return "Hello! Welcome to Zoe v3.1! I'm so excited you can see the new interface. Isn't it beautiful?";
            } else if (lowerMessage.includes('interface') || lowerMessage.includes('design')) {
                return "I love this new interface too! The fluid orb, glass morphic design, and smooth animations make me feel so elegant.";
            }
            
            return responses[Math.floor(Math.random() * responses.length)];
        }

        // Event listeners setup
        function setupChatOverlayEvents() {
            const chatInputOverlay = document.getElementById('chatInputOverlay');
            if (chatInputOverlay) {
                chatInputOverlay.addEventListener('keypress', function(e) {
                    if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        sendMessageFromOverlay();
                    }
                });
            }
            
            document.getElementById('chatOverlay').addEventListener('click', function(e) {
                if (e.target === this) {
                    closeChatWindow();
                }
            });
        }

        function setupShoppingOverlayEvents() {
            const shoppingInput = document.getElementById('shoppingInput');
            if (shoppingInput) {
                shoppingInput.addEventListener('keypress', function(e) {
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        addShoppingItem();
                    }
                });
            }
            
            document.getElementById('shoppingOverlay').addEventListener('click', function(e) {
                if (e.target === this) {
                    closeShoppingWindow();
                }
            });
        }

        // Auto-return to orb mode after inactivity
        let inactivityTimer;
        const INACTIVITY_TIMEOUT = 300000; // 5 minutes

        function resetInactivityTimer() {
            clearTimeout(inactivityTimer);
            if (currentMode === 'interface') {
                inactivityTimer = setTimeout(() => {
                    exitToOrb();
                    showStatus('Returned to orb mode due to inactivity');
                }, INACTIVITY_TIMEOUT);
            }
        }

        // Track user activity
        ['mousedown', 'mousemove', 'keypress', 'scroll', 'touchstart'].forEach(event => {
            document.addEventListener(event, resetInactivityTimer, true);
        });

        // Console welcome message
        console.log(`
🌟 Zoe v3.1 Interface Loaded Successfully!

✨ Features Active:
   • Fluid orb animations with 3 states
   • Glass morphic design system
   • Touch-optimized for Pi deployment
   • Real-time clock and weather
   • Interactive chat interface
   • Task and event management
   • Voice input ready (spacebar in orb mode)
   • Backend integration ready

🎯 Status: ${backendConnected ? 'Connected to backend' : 'Standalone demo mode'}

💡 Try:
   • Click the orb to enter interface
   • Press spacebar for voice input
   • Type messages in chat
   • Toggle tasks and try quick actions
   • Use ESC to return to orb mode

🔗 API Endpoints Ready:
   • /api/chat - Chat with Zoe
   • /api/voice/start & /api/voice/stop - Voice services
   • /api/tasks/today & /api/tasks/update - Task management
   • /api/events/upcoming - Calendar events
   • /api/journal/create - Journal entries
   • /api/weather - Weather data
   • /api/health - Service health check

Built with ❤️ for your Raspberry Pi touchscreen!
        `);
    
        // State management
        const orbMode = document.getElementById('orbMode');
        const mainInterface = document.getElementById('mainInterface');
        const fluidOrb = document.getElementById('fluidOrb');

        // Update time display
        function updateTime() {
            const now = new Date();
            const timeString = now.toLocaleTimeString([], { 
                hour: 'numeric', 
                minute: '2-digit',
                hour12: true 
            });
            const dateString = now.toLocaleDateString([], { 
                weekday: 'long', 
                month: 'long', 
                day: 'numeric' 
            });
            
            document.getElementById('currentTime').textContent = timeString;
            document.getElementById('currentDate').textContent = dateString;
        }

        // Initialize time and update every minute
        updateTime();
        setInterval(updateTime, 60000);

        // Enter main interface
        function enterInterface() {
            orbMode.classList.add('hidden');
            mainInterface.classList.add('active');
        }

        // Exit to orb mode
        function exitToOrb() {
            mainInterface.classList.remove('active');
            orbMode.classList.remove('hidden');
        }

        // Orb state management
        function setOrbState(state) {
            fluidOrb.className = `fluid-orb ${state}`;
        }

        // Calendar functions
        function previousMonth() {
            setOrbState('listening');
            setTimeout(() => setOrbState('idle'), 1000);
            console.log('Previous month');
        }

        function nextMonth() {
            setOrbState('listening');
            setTimeout(() => setOrbState('idle'), 1000);
            console.log('Next month');
        }

        // Add event via AI chat
        function addEventViaChat() {
            const chatOverlay = document.getElementById('chatOverlay');
            chatOverlay.classList.add('active');
            
            setTimeout(() => {
                document.getElementById('chatInputField').focus();
            }, 300);
            
            setOrbState('listening');
            setTimeout(() => setOrbState('idle'), 1000);
        }

        // Close chat window
        function closeChatWindow() {
            const chatOverlay = document.getElementById('chatOverlay');
            chatOverlay.classList.remove('active');
        }

        // Voice input
        function startVoiceInput() {
            setOrbState('listening');
            
            setTimeout(() => {
                setOrbState('speaking');
                addMessage('user', 'Schedule dinner with Sarah tomorrow at 7pm');
                
                setTimeout(() => {
                    addMessage('zoe', 'Perfect! I\'ve scheduled "Dinner with Sarah" for tomorrow (August 8th) at 7:00 PM. Would you like me to add a location?');
                    setOrbState('idle');
                }, 1000);
            }, 2000);
        }

        // Add message to chat
        function addMessage(sender, text) {
            const chatMessages = document.getElementById('chatMessages');
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${sender}-message`;
            
            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            contentDiv.textContent = text;
            
            messageDiv.appendChild(contentDiv);
            chatMessages.appendChild(messageDiv);
            
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }

        // Update day view based on selected date
        function updateDayView(date) {
            const selectedDateEl = document.getElementById('selectedDate');
            const selectedWeekdayEl = document.getElementById('selectedWeekday');
            const dayEventsEl = document.getElementById('dayEvents');
            
            const eventsData = {
                '7': [
                    { time: '10:00 AM', title: 'Team Standup', location: 'Conference Room A' },
                    { time: '2:00 PM', title: 'Product Review', location: 'Zoom Meeting' },
                    { time: '4:30 PM', title: 'Coffee with Maria', location: 'Central Café' }
                ],
                '8': [
                    { time: '9:00 AM', title: 'Doctor Appointment', location: 'Health Center' },
                    { time: '1:00 PM', title: 'Lunch Meeting', location: 'Downtown Bistro' }
                ],
                '12': [
                    { time: '6:00 PM', title: 'Dinner with Sarah', location: 'The Olive Tree' },
                    { time: '8:30 PM', title: 'Movie Night', location: 'Home' }
                ],
                '15': [
                    { time: '3:00 PM', title: 'Project Kickoff', location: 'Office Building 2' }
                ],
                '20': [
                    { time: '10:00 AM', title: 'Team Meeting', location: 'Conference Room B' },
                    { time: '2:00 PM', title: 'Client Call', location: 'Zoom' },
                    { time: '5:00 PM', title: 'Happy Hour', location: 'Local Bar' }
                ]
            };
            
            const dateObj = new Date(2025, 7, parseInt(date));
            const isToday = date === '7';
            
            if (isToday) {
                selectedDateEl.textContent = 'Today, Aug 7';
            } else {
                selectedDateEl.textContent = `Aug ${date}`;
            }
            
            selectedWeekdayEl.textContent = dateObj.toLocaleDateString('en-US', { weekday: 'long' });
            
            const events = eventsData[date] || [];
            
            if (events.length === 0) {
                dayEventsEl.innerHTML = '<div style="text-align: center; color: #666; font-style: italic; padding: 20px;">No events scheduled</div>';
            } else {
                dayEventsEl.innerHTML = events.map(event => `
                    <div class="event-item" onclick="editEvent('${event.title}')">
                        <div class="event-time">${event.time}</div>
                        <div class="event-title">${event.title}</div>
                        <div class="event-location">${event.location}</div>
                    </div>
                `).join('');
            }
            
            const eventCount = events.length;
            const freeHours = Math.max(0, 8 - eventCount);
            
            document.querySelector('.summary-stat:first-child .stat-number').textContent = eventCount;
            document.querySelector('.summary-stat:last-child .stat-number').textContent = freeHours;
        }

        // Edit event function
        function editEvent(title) {
            setOrbState('listening');
            setTimeout(() => setOrbState('idle'), 500);
            console.log('Edit event:', title);
        }

        // Navigation
        document.addEventListener('click', function(e) {
            if (e.target.classList.contains('nav-item')) {
                document.querySelectorAll('.nav-item').forEach(item => {
                    item.classList.remove('active');
                });
                e.target.classList.add('active');
                
                console.log('Navigate to:', e.target.textContent);
            }
        });

        // Voice activation from orb mode
        let isListening = false;
        
        document.addEventListener('keydown', function(e) {
            if (e.code === 'Space' && !mainInterface.classList.contains('active')) {
                e.preventDefault();
                if (!isListening) {
                    isListening = true;
                    setOrbState('listening');
                }
            }
        });

        document.addEventListener('keyup', function(e) {
            if (e.code === 'Space' && isListening) {
                isListening = false;
                setOrbState('speaking');
                setTimeout(() => {
                    setOrbState('idle');
                }, 2000);
            }
        });

        // Auto-return to orb mode after inactivity (optional)
        let inactivityTimer;
        function resetInactivityTimer() {
            clearTimeout(inactivityTimer);
            inactivityTimer = setTimeout(() => {
                if (mainInterface.classList.contains('active')) {
                    exitToOrb();
                }
            }, 300000); // 5 minutes
        }

        document.addEventListener('mousemove', resetInactivityTimer);
        document.addEventListener('keypress', resetInactivityTimer);
        document.addEventListener('click', resetInactivityTimer);

        // Handle chat input and calendar interactions
        document.addEventListener('DOMContentLoaded', function() {
            const chatInput = document.getElementById('chatInputField');
            
            chatInput.addEventListener('keypress', function(e) {
                if (e.key === 'Enter') {
                    const message = this.value.trim();
                    if (message) {
                        addMessage('user', message);
                        this.value = '';
                        
                        setOrbState('speaking');
                        
                        setTimeout(() => {
                            addMessage('zoe', 'Got it! I\'ll add that to your calendar. Anything else you\'d like to schedule?');
                            setOrbState('idle');
                        }, 1000);
                    }
                }
            });
            
            document.getElementById('chatOverlay').addEventListener('click', function(e) {
                if (e.target === this) {
                    closeChatWindow();
                }
            });

            document.querySelectorAll('.calendar-day').forEach(day => {
                day.addEventListener('click', function() {
                    if (this.classList.contains('other-month')) return;
                    
                    setOrbState('listening');
                    setTimeout(() => setOrbState('idle'), 500);
                    
                    document.querySelectorAll('.calendar-day').forEach(d => d.classList.remove('selected'));
                    this.classList.add('selected');
                    updateDayView(this.dataset.date);
                });
            });
        });
    export function initIntegration() {
  console.log('Frontend integration v3.1 active');
}

if (typeof window !== 'undefined') {
  initIntegration();
}
