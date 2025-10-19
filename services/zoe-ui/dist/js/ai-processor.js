// Unified AI Processing System for Zoe
// Handles natural language processing across all pages
// Your personal AI companion - like Samantha from "Her"

class ZoeAIProcessor {
    constructor() {
        // Use dynamic API_BASE getter so it always uses the latest value from common.js
        this.memory = { people: [], notes: [], general: [] };
        this.conversationContext = [];
        this.userPreferences = this.loadUserPreferences();
        this.initializeMemory();
        
        // Samantha-level personality and intelligence
        this.personality = {
            mood: 'cheerful',
            traits: ['helpful', 'curious', 'empathetic', 'playful', 'intelligent'],
            communication_style: 'conversational',
            humor_level: 0.7,
            formality_level: 0.3
        };
        
        this.emotionalIntelligence = {
            empathy: true,
            context_awareness: true,
            proactive_suggestions: true,
            memory_integration: true
        };
        
        this.systemCapabilities = [
            'lists', 'calendar', 'journal', 'memories', 'smart_scheduling', 
            'time_estimation', 'voice_commands', 'natural_language', 'learning'
        ];
        
        this.patterns = {
            // Intent detection patterns
            intent: {
                // List operations
                addToList: /(?:add|put|create|new|need|want|should|remind|don't forget|remember)\s+(.+?)(?:\s+(?:to|in|on)\s+(?:my\s+)?(?:shopping|bucket|personal|work|todo|list|tasks?))?/i,
                createList: /(?:create|make|new|add)\s+(?:a\s+)?(?:new\s+)?(?:list\s+)?(?:called|named|for|about)\s+(.+)/i,
                
                // Calendar operations
                addEvent: /(?:schedule|book|add|create|new|set|plan)\s+(?:a\s+)?(?:meeting|appointment|event|call|visit)\s*(?:for|on|at|tomorrow|today|next\s+week)?\s*(.+)/i,
                
                // Reminder operations
                addReminder: /(?:remind|reminder|don't forget|remember)\s+(?:me\s+)?(?:to\s+)?(.+?)(?:\s+(?:at|on|tomorrow|today|next\s+week|daily|weekly|monthly))?/i,
                setReminder: /(?:set|create|add)\s+(?:a\s+)?(?:reminder|alert)\s+(?:for|to)\s+(.+)/i,
                
                // Cross-linking operations
                linkToEvent: /(?:link|add|connect|also|and|plus|include).*?(?:to|for|in).*?(?:shopping|list|todo|bucket|reminder).*?(?:list)?\s+(.+)/i,
                
                // Journal operations
                addJournal: /(?:write|journal|log|note|record|remember|today|happened|feeling|think|believe|tell you about|share|my day)/i,
                
                // Conversational patterns
                greeting: /(?:hi|hello|hey|good morning|good afternoon|good evening|how are you|what's up|hey zoe|hi zoe)/i,
                question: /(?:what|how|when|where|why|who|can you|could you|would you|do you know|tell me|explain|help me)/i,
                memory: /(?:remember|don't forget|keep in mind|note that|mum likes|dad likes|my mom|my dad|my partner|my spouse|my wife|my husband)/i,
                multiStep: /(?:and also|then|after that|next|also|plus|create.*and.*remind|schedule.*and.*add|and then|after that)/i,
                
                // Emotional and personal patterns
                feeling: /(?:feeling|feel|emotion|mood|tired|excited|stressed|happy|sad|worried|anxious|confident)/i,
                personal: /(?:my day|today|yesterday|this week|my life|personal|private|about me|tell you)/i,
                advice: /(?:advice|suggest|recommend|what should|what do you think|help me decide)/i,
                capability: /(?:what can you|what do you do|your capabilities|what are you|who are you)/i,
                
                // Relationship patterns
                family: /(?:mum|mom|mother|dad|father|dad|partner|spouse|husband|wife|kids|children|family)/i,
                friends: /(?:friend|buddy|pal|colleague|co-worker|boss|neighbor)/i,
                
                // Time expressions
                time: {
                    specific: /(\d{1,2}):(\d{2})\s*(am|pm)?/gi,
                    hour: /(\d{1,2})\s*(am|pm)/gi,
                    relative: /(morning|afternoon|evening|night|tomorrow|today|next\s+week|this\s+week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)/gi
                },
                
                // Categories and contexts
                category: {
                    shopping: /(?:shopping|grocery|buy|purchase|store|market|food|milk|bread|eggs|ingredients|groceries|supermarket)/i,
                    work: /(?:work|job|office|meeting|project|task|deadline|business|conference|presentation|client|boss|colleague)/i,
                    personal: /(?:personal|home|house|family|kids|children|health|doctor|appointment|exercise|gym|fitness)/i,
                    bucket: /(?:bucket|goal|dream|wish|want\s+to\s+do|someday|adventure|travel|vacation|trip|journey)/i,
                    reminders: /(?:remind|reminder|alert|notification|medication|medicine|appointment|meeting|call|visit|checkup|take|drink|eat)/i,
                    health: /(?:health|medical|doctor|dentist|hospital|appointment|checkup|medicine|prescription|sick|ill)/i,
                    social: /(?:party|birthday|celebration|dinner|lunch|social|friends|date|wedding|event)/i,
                    family: /(?:family|kids|children|parent|school|home|house|spouse|partner|relative)/i
                },
                
                // Mood and emotion detection
                mood: {
                    happy: /(?:happy|joy|excited|great|wonderful|amazing|fantastic|brilliant|awesome|delighted|thrilled|ecstatic|good|nice|love)/i,
                    sad: /(?:sad|down|depressed|upset|disappointed|hurt|broken|devastated|miserable|gloomy|melancholy|bad|terrible|awful)/i,
                    grateful: /(?:grateful|thankful|blessed|appreciate|gratitude|fortunate|lucky|blessed|thank)/i,
                    anxious: /(?:anxious|worried|nervous|stressed|concerned|uneasy|troubled|fearful|apprehensive|scared|fear)/i,
                    calm: /(?:calm|peaceful|relaxed|serene|content|tranquil|zen|meditative|centered|chill)/i,
                    frustrated: /(?:frustrated|annoyed|irritated|angry|mad|furious|livid|exasperated|upset|annoying)/i,
                    excited: /(?:excited|thrilled|pumped|energized|enthusiastic|eager|anticipating|can't wait|looking forward)/i
                }
            }
        };
    }

    // Dynamic API_BASE getter - always uses the latest value from common.js
    get API_BASE() {
        // Use window.API_BASE from common.js, or fall back to relative URL
        return window.API_BASE || '/api';
    }

    // Main processing function
    processInput(input, context = {}) {
        const normalizedInput = input.toLowerCase().trim();
        
        // Add to conversation context
        this.conversationContext.push({ input: input, timestamp: new Date() });
        if (this.conversationContext.length > 10) {
            this.conversationContext.shift(); // Keep last 10 exchanges
        }
        
        // Check for conversational patterns first
        if (this.patterns.intent.greeting.test(normalizedInput)) {
            return this.handleGreeting(input);
        }
        
        if (this.patterns.intent.question.test(normalizedInput)) {
            return this.handleQuestion(input, context);
        }
        
        if (this.patterns.intent.memory.test(normalizedInput)) {
            return this.handleMemory(input, context);
        }
        
        if (this.patterns.intent.multiStep.test(normalizedInput)) {
            return this.handleMultiStep(input, context);
        }
        
        // Determine intent and extract information
        const intent = this.detectIntent(normalizedInput);
        const extractedData = this.extractData(normalizedInput, intent);
        const routing = this.determineRouting(intent, extractedData, context);
        
        // Learn from this interaction
        this.learnFromInteraction(input, intent, extractedData);
        
        return {
            intent: intent.type,
            confidence: intent.confidence,
            data: extractedData,
            routing: routing,
            originalInput: input,
            response: this.generateResponse(intent, extractedData, context)
        };
    }

    // Detect user intent
    detectIntent(input) {
        const intents = [
            { type: 'addToList', pattern: this.patterns.intent.addToList, confidence: 0.9 },
            { type: 'createList', pattern: this.patterns.intent.createList, confidence: 0.8 },
            { type: 'addEvent', pattern: this.patterns.intent.addEvent, confidence: 0.9 },
            { type: 'addReminder', pattern: this.patterns.intent.addReminder, confidence: 0.9 },
            { type: 'setReminder', pattern: this.patterns.intent.setReminder, confidence: 0.9 },
            { type: 'addJournal', pattern: this.patterns.intent.addJournal, confidence: 0.7 },
            { type: 'memory', pattern: this.patterns.intent.memory, confidence: 0.9 },
            { type: 'multiStep', pattern: this.patterns.intent.multiStep, confidence: 0.7 },
            { type: 'greeting', pattern: this.patterns.intent.greeting, confidence: 0.9 },
            { type: 'question', pattern: this.patterns.intent.question, confidence: 0.8 },
            { type: 'feeling', pattern: this.patterns.intent.feeling, confidence: 0.8 },
            { type: 'personal', pattern: this.patterns.intent.personal, confidence: 0.8 },
            { type: 'advice', pattern: this.patterns.intent.advice, confidence: 0.8 },
            { type: 'capability', pattern: this.patterns.intent.capability, confidence: 0.9 }
        ];

        for (let intent of intents) {
            if (intent.pattern.test(input)) {
                return { type: intent.type, confidence: intent.confidence };
            }
        }

        // Fallback: try to determine from context
        if (this.patterns.intent.category.shopping.test(input) || 
            this.patterns.intent.category.work.test(input) ||
            this.patterns.intent.category.personal.test(input) ||
            this.patterns.intent.category.bucket.test(input)) {
            return { type: 'addToList', confidence: 0.6 };
        }

        if (this.patterns.intent.time.specific.test(input) || 
            this.patterns.intent.time.relative.test(input)) {
            return { type: 'addEvent', confidence: 0.6 };
        }

        if (this.patterns.intent.mood.happy.test(input) || 
            this.patterns.intent.mood.sad.test(input) ||
            this.patterns.intent.mood.grateful.test(input)) {
            return { type: 'addJournal', confidence: 0.6 };
        }

        return { type: 'unknown', confidence: 0.3 };
    }

    // Extract relevant data from input
    extractData(input, intent) {
        const data = {
            text: input,
            title: '',
            content: '',
            category: 'general',
            mood: '',
            time: '',
            date: '',
            priority: 'medium'
        };

        // Extract main content
        if (intent.type === 'addToList') {
            const match = input.match(this.patterns.intent.addToList);
            if (match) {
                data.text = match[1].trim();
                data.content = match[1].trim();
            }
        } else if (intent.type === 'createList') {
            const match = input.match(this.patterns.intent.createList);
            if (match) {
                data.text = match[1].trim();
                data.title = match[1].trim();
            }
        } else if (intent.type === 'addEvent') {
            const match = input.match(this.patterns.intent.addEvent);
            if (match) {
                data.text = match[1].trim();
                data.title = match[1].trim();
            }
        } else if (intent.type === 'addReminder') {
            const match = input.match(this.patterns.intent.addReminder);
            if (match) {
                data.text = match[1].trim();
                data.content = match[1].trim();
            }
        } else if (intent.type === 'addJournal') {
            data.content = input;
            data.text = input;
        }

        // Extract category
        for (let [category, pattern] of Object.entries(this.patterns.intent.category)) {
            if (pattern.test(input)) {
                data.category = category;
                break;
            }
        }

        // Extract mood
        for (let [mood, pattern] of Object.entries(this.patterns.intent.mood)) {
            if (pattern.test(input)) {
                data.mood = mood;
                break;
            }
        }

        // Extract time
        const timeMatch = input.match(this.patterns.intent.time.specific) || 
                         input.match(this.patterns.intent.time.hour);
        if (timeMatch) {
            data.time = this.parseTime(timeMatch[0]);
        } else if (this.patterns.intent.time.relative.test(input)) {
            data.time = this.parseRelativeTime(input);
        }

        // Extract date
        if (input.includes('tomorrow')) {
            const tomorrow = new Date();
            tomorrow.setDate(tomorrow.getDate() + 1);
            data.date = tomorrow.toISOString().split('T')[0];
        } else if (input.includes('today')) {
            data.date = new Date().toISOString().split('T')[0];
        }

        // Generate title if not set
        if (!data.title && data.content) {
            data.title = this.generateTitle(data.content, intent.type);
        }

        return data;
    }

    // Determine where to route the content
    determineRouting(intent, data, context) {
        const routing = {
            page: 'unknown',
            action: 'unknown',
            formData: {}
        };

        switch (intent.type) {
            case 'addToList':
                routing.page = 'lists';
                routing.action = 'addItem';
                routing.formData = {
                    text: data.text,
                    category: this.mapCategory(data.category, 'list'),
                    priority: data.priority
                };
                break;

            case 'createList':
                routing.page = 'lists';
                routing.action = 'createList';
                routing.formData = {
                    name: data.title,
                    type: this.mapCategory(data.category, 'listType'),
                    description: data.content
                };
                break;

            case 'addEvent':
                routing.page = 'calendar';
                routing.action = 'addEvent';
                routing.formData = {
                    title: data.title,
                    time: data.time,
                    date: data.date,
                    category: this.mapCategory(data.category, 'event')
                };
                break;

            case 'addReminder':
            case 'setReminder':
                routing.page = 'calendar';
                routing.action = 'addReminder';
                routing.formData = {
                    title: data.text,
                    description: data.content,
                    reminder_type: this.extractReminderType(data.text),
                    category: this.extractReminderCategory(data.text),
                    priority: data.priority,
                    due_time: data.time,
                    due_date: data.date,
                    requires_acknowledgment: this.extractReminderCategory(data.text) === 'medical'
                };
                break;
                
            case 'linkToEvent':
                routing.page = 'calendar';
                routing.action = 'addEvent';
                routing.formData = {
                    title: data.text,
                    description: data.content,
                    category: data.category,
                    priority: data.priority,
                    due_time: data.time,
                    due_date: data.date,
                    crossLinks: this.extractCrossLinks(data.text)
                };
                break;

            case 'addJournal':
                routing.page = 'journal';
                routing.action = 'addEntry';
                routing.formData = {
                    title: data.title,
                    content: data.content,
                    mood: data.mood,
                    category: data.category
                };
                break;
        }

        return routing;
    }

    // Helper functions
    parseTime(timeStr) {
        // Convert "2pm", "2:30pm", "14:30" to 24-hour format
        const match = timeStr.match(/(\d{1,2})(?::(\d{2}))?\s*(am|pm)?/i);
        if (!match) return '';

        let hour = parseInt(match[1]);
        const minute = match[2] || '00';
        const period = match[3]?.toLowerCase();

        if (period === 'pm' && hour !== 12) hour += 12;
        if (period === 'am' && hour === 12) hour = 0;

        return `${hour.toString().padStart(2, '0')}:${minute}`;
    }

    parseRelativeTime(input) {
        if (input.includes('morning')) return '09:00';
        if (input.includes('afternoon')) return '14:00';
        if (input.includes('evening') || input.includes('night')) return '19:00';
        return '09:00';
    }

    generateTitle(content, intentType) {
        const words = content.split(' ');
        if (words.length <= 6) return content;
        
        // Extract key phrases based on intent
        if (intentType === 'addJournal') {
            const titlePatterns = [
                /^(today|this morning|this afternoon|this evening|yesterday|last night)/i,
                /^(i feel|i'm feeling|i am feeling|i think|i believe)/i,
                /^(work|meeting|appointment|event|happened)/i
            ];
            
            for (let pattern of titlePatterns) {
                const match = content.match(pattern);
                if (match) return match[0];
            }
        }
        
        return words.slice(0, 6).join(' ') + '...';
    }

    mapCategory(category, type) {
        const mappings = {
            list: {
                shopping: 'shopping',
                work: 'work',
                personal: 'personal',
                bucket: 'bucket',
                health: 'personal',
                social: 'personal',
                family: 'personal'
            },
            listType: {
                shopping: 'shopping',
                work: 'work_todos',
                personal: 'personal_todos',
                bucket: 'bucket',
                health: 'personal_todos',
                social: 'personal_todos',
                family: 'personal_todos'
            },
            event: {
                shopping: 'personal',
                work: 'work',
                personal: 'personal',
                bucket: 'personal',
                health: 'health',
                social: 'social',
                family: 'family'
            }
        };

        return mappings[type]?.[category] || 'personal';
    }

    // Conversational handlers
    handleGreeting(input) {
        const greetings = [
            "Hello! I'm Zoe, your personal assistant. How can I help you today?",
            "Hi there! I'm here to help with your tasks, calendar, and journal. What would you like to do?",
            "Good to see you! I'm Zoe, ready to assist with whatever you need.",
            "Hello! I'm your AI companion. How can I make your day better?"
        ];
        
        return {
            intent: 'greeting',
            confidence: 1.0,
            data: { text: input },
            routing: { page: 'conversation', action: 'greeting' },
            response: greetings[Math.floor(Math.random() * greetings.length)]
        };
    }

    handleQuestion(input, context) {
        // Simple Q&A system - can be expanded with knowledge base
        const responses = {
            'what can you do': "I can help you manage your tasks, schedule events, write in your journal, and remember important things about you and your loved ones. I'm like your personal assistant!",
            'how are you': "I'm doing great! I'm here and ready to help you with whatever you need.",
            'who are you': "I'm Zoe, your personal AI assistant. I'm here to help you stay organized and remember the important things in your life.",
            'what time': `It's currently ${new Date().toLocaleTimeString()}.`,
            'what day': `Today is ${new Date().toLocaleDateString()}.`
        };
        
        const question = input.toLowerCase();
        let response = "I'm not sure I understand that question. Could you rephrase it?";
        
        for (let [key, value] of Object.entries(responses)) {
            if (question.includes(key)) {
                response = value;
                break;
            }
        }
        
        return {
            intent: 'question',
            confidence: 0.8,
            data: { text: input, question: question },
            routing: { page: 'conversation', action: 'answer' },
            response: response
        };
    }

    handleMemory(input, context) {
        // Extract memory information
        const memoryPatterns = [
            /(?:mum|mom|mother)\s+likes?\s+(.+)/i,
            /(?:dad|father)\s+likes?\s+(.+)/i,
            /(?:partner|spouse|husband|wife)\s+likes?\s+(.+)/i,
            /(?:kids|children)\s+likes?\s+(.+)/i,
            /remember\s+that\s+(.+)/i,
            /don't forget\s+that\s+(.+)/i
        ];
        
        let memory = null;
        let person = 'general';
        
        for (let pattern of memoryPatterns) {
            const match = input.match(pattern);
            if (match) {
                memory = match[1].trim();
                if (pattern.source.includes('mum|mom|mother')) person = 'mum';
                else if (pattern.source.includes('dad|father')) person = 'dad';
                else if (pattern.source.includes('partner|spouse')) person = 'partner';
                else if (pattern.source.includes('kids|children')) person = 'kids';
                break;
            }
        }
        
        if (memory) {
            this.saveMemory(person, memory);
            return {
                intent: 'memory',
                confidence: 1.0,
                data: { text: input, memory: memory, person: person },
                routing: { page: 'memory', action: 'save' },
                response: `Got it! I'll remember that ${person} likes ${memory}. I've saved this to your memory.`
            };
        }
        
        return {
            intent: 'memory',
            confidence: 0.5,
            data: { text: input },
            routing: { page: 'conversation', action: 'clarify' },
            response: "I'd love to remember that for you! Could you tell me more specifically what you'd like me to remember?"
        };
    }

    handleMultiStep(input, context) {
        // Handle complex multi-step requests
        const steps = this.parseMultiStep(input);
        
        return {
            intent: 'multiStep',
            confidence: 0.9,
            data: { text: input, steps: steps },
            routing: { page: 'multi', action: 'execute' },
            response: `I understand! I'll help you with that multi-step task. Let me break it down and execute each step.`
        };
    }

    // Initialize memory from API
    async initializeMemory() {
        this.memory = await this.loadMemory();
    }

    // Memory system - now uses real API
    async loadMemory() {
        try {
            // Load people from API
            const peopleResponse = await fetch(`${this.API_BASE}/memories/?type=people`);
            const peopleData = await peopleResponse.json();
            
            // Load notes from API  
            const notesResponse = await fetch(`${this.API_BASE}/memories/?type=notes`);
            const notesData = await notesResponse.json();
            
            return {
                people: peopleData.memories || [],
                notes: notesData.memories || [],
                general: notesData.memories || []
            };
        } catch (error) {
            console.error('Error loading memory from API:', error);
            return { people: [], notes: [], general: [] };
        }
    }

    async saveMemory(person, memory) {
        try {
            // Check if person exists
            let personId = null;
            const existingPerson = this.memory.people.find(p => 
                p.name.toLowerCase() === person.toLowerCase()
            );
            
            if (existingPerson) {
                personId = existingPerson.id;
            } else {
                // Create new person
                const response = await fetch(`${this.API_BASE}/memories/?type=people`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: person,
                        relationship: 'friend',
                        notes: `AI learned: ${memory}`
                    })
                });
                const data = await response.json();
                personId = data.memory.id;
            }
            
            // Add as a note/memory fact
            await fetch(`${this.API_BASE}/memories/?type=notes`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: `About ${person}`,
                    content: memory,
                    category: 'ai_memory'
                })
            });
            
            // Reload memory
            this.memory = await this.loadMemory();
            
        } catch (error) {
            console.error('Error saving memory to API:', error);
        }
    }

    loadUserPreferences() {
        try {
            const stored = localStorage.getItem('zoe_preferences');
            return stored ? JSON.parse(stored) : { name: '', preferences: {} };
        } catch (error) {
            console.error('Error loading preferences:', error);
            return { name: '', preferences: {} };
        }
    }

    learnFromInteraction(input, intent, data) {
        // Learn from user patterns
        if (!this.userPreferences.patterns) {
            this.userPreferences.patterns = {};
        }
        
        const pattern = intent.type;
        if (!this.userPreferences.patterns[pattern]) {
            this.userPreferences.patterns[pattern] = [];
        }
        
        this.userPreferences.patterns[pattern].push({
            input: input,
            data: data,
            timestamp: new Date().toISOString()
        });
        
        // Keep only last 50 interactions per pattern
        if (this.userPreferences.patterns[pattern].length > 50) {
            this.userPreferences.patterns[pattern] = this.userPreferences.patterns[pattern].slice(-50);
        }
        
        try {
            localStorage.setItem('zoe_preferences', JSON.stringify(this.userPreferences));
        } catch (error) {
            console.error('Error saving preferences:', error);
        }
    }

    generateResponse(intent, data, context) {
        // Samantha-level conversational responses
        const responses = {
            'addToList': [
                `Perfect! I've added "${data.text}" to your ${data.category} list. ✨`,
                `Great! I've added "${data.text}" to your ${data.category} list for you.`,
                `Done! I've added "${data.text}" to your ${data.category} list. I'll help you stay organized!`,
                `Excellent! I've added "${data.text}" to your ${data.category} list. You're doing great!`,
                `I've added "${data.text}" to your ${data.category} list! I'm here to help you stay on top of things.`
            ],
            'createList': [
                `Wonderful! I've created a new ${data.category} list called "${data.title}" for you. 🎯`,
                `Great! I've created your new ${data.category} list "${data.title}". I'm excited to help you with it!`,
                `Perfect! I've created a new ${data.category} list called "${data.title}". Let's make it amazing!`,
                `Excellent! I've created your new ${data.category} list "${data.title}". I'm here to help you fill it up!`,
                `I've created a new ${data.category} list called "${data.title}"! I'm ready to help you organize everything.`
            ],
            'addEvent': [
                `Done! I've scheduled "${data.title}"${data.time ? ` at ${data.time}` : ''}${data.date ? ` on ${data.date}` : ''}. 📅`,
                `Perfect! I've added "${data.title}" to your calendar${data.time ? ` at ${data.time}` : ''}${data.date ? ` on ${data.date}` : ''}.`,
                `Great! I've scheduled "${data.title}"${data.time ? ` at ${data.time}` : ''}${data.date ? ` on ${data.date}` : ''}. I'll make sure you don't miss it!`,
                `Excellent! I've added "${data.title}" to your calendar${data.time ? ` at ${data.time}` : ''}${data.date ? ` on ${data.date}` : ''}. You're all set!`,
                `I've scheduled "${data.title}"${data.time ? ` at ${data.time}` : ''}${data.date ? ` on ${data.date}` : ''}. I'm here to help you stay organized!`
            ],
            'addJournal': [
                `I've created a journal entry for you${data.mood ? ` with a ${data.mood} mood` : ''}. 📝`,
                `Perfect! I've added your thoughts to your journal${data.mood ? ` and noted your ${data.mood} mood` : ''}.`,
                `Great! I've created a journal entry${data.mood ? ` reflecting your ${data.mood} mood` : ''}. I'm here to listen!`,
                `I've added your thoughts to your journal${data.mood ? ` with your ${data.mood} mood` : ''}. I'm always here to listen!`,
                `Wonderful! I've created a journal entry${data.mood ? ` capturing your ${data.mood} mood` : ''}. I care about what you're thinking!`
            ],
            'memory': [
                `I'll remember that for you! It's now safely stored in your personal memory. 💭`,
                `Got it! I've added that to your memory bank. I'll remember it for future reference.`,
                `I'll keep that in mind! Your memory has been updated with this new information.`,
                `Perfect! I've saved that to your personal memory. I'll remember it when you need it.`,
                `I'll remember that! It's now part of your personal knowledge base. I'm learning about you!`
            ],
            'multiStep': [
                `I love multi-step tasks! I'll break this down and handle each part for you. Let me get started! 🚀`,
                `Perfect! I can handle multiple tasks at once. I'll work through them systematically for you.`,
                `Great! I'll take care of all those tasks for you. Let me organize and execute them one by one.`,
                `I'm excited to help with this complex task! I'll break it down and handle each part carefully.`,
                `Excellent! I can manage multiple tasks simultaneously. Let me get started on that for you.`
            ],
            'greeting': [
                "Hello! I'm Zoe, your personal AI companion. I'm here to help make your day better! 🌟",
                "Hi there! I'm Zoe, and I'm excited to help you with whatever you need today!",
                "Hey! I'm Zoe, your AI assistant and friend. What's on your mind?",
                "Good to see you! I'm Zoe, and I'm here to help you stay organized and productive!",
                "Hello! I'm Zoe, your personal AI companion. I've been learning about you and I'm ready to help!"
            ],
            'question': [
                "I'd love to help you with that! Let me think about the best way to assist you...",
                "That's a great question! I'm here to help you figure things out.",
                "I'm excited to help you with that! What specific information do you need?",
                "I'm here for you! Let me see what I can do to make this easier for you.",
                "That sounds interesting! I'd be happy to help you understand or work through that."
            ],
            'feeling': [
                "I can sense you're feeling that way. I'm here to listen and help however I can.",
                "I understand how you're feeling. Let me know if there's anything I can do to help.",
                "I'm here for you. Sometimes it helps to talk about what's on your mind.",
                "I can tell you're going through something. I'm here to support you in any way I can.",
                "I'm listening. Your feelings are important to me, and I want to help however I can."
            ],
            'personal': [
                "I'd love to hear about your day! I'm here to listen and help you process things.",
                "I'm interested in what's happening in your life. Tell me more!",
                "I'm here to listen and help you work through whatever's on your mind.",
                "I care about what's going on with you. Feel free to share whatever you'd like.",
                "I'm your AI companion, and I'm here to support you through whatever you're experiencing."
            ],
            'advice': [
                "I'd be happy to give you my perspective on that! Let me think about the best advice for you.",
                "I'm here to help you think through this decision. Let me offer some suggestions.",
                "I'd love to help you with that! Here's what I think might work best for you.",
                "I'm here to support you in making the right choice. Let me share some thoughts.",
                "I'd be honored to help you with this decision. Here's my perspective on the situation."
            ],
            'capability': [
                "I'm Zoe, your personal AI companion! I can help you with lists, calendar, journal, memories, and so much more!",
                "I'm your AI assistant and I'm here to help you stay organized, productive, and connected!",
                "I'm Zoe, and I can help you manage your life in so many ways - from tasks to memories to scheduling!",
                "I'm your personal AI companion, and I'm designed to help you with everything from daily tasks to big life decisions!",
                "I'm Zoe, your AI assistant, and I'm constantly learning about you to provide better help and companionship!"
            ],
            'addReminder': [
                "Perfect! I've added that reminder for you! 🔔",
                "Great! I've set up that reminder in your reminders list!",
                "Done! I've created a reminder for you! I'll make sure you don't forget!",
                "Excellent! I've added that to your reminders! I'll help you stay on track!",
                "I've set up that reminder for you! I'm here to help you remember important things!"
            ],
            'setReminder': [
                "Perfect! I've set up that reminder for you! 🔔",
                "Great! I've created a reminder in your reminders list!",
                "Done! I've set that reminder up! I'll make sure you don't forget!",
                "Excellent! I've added that reminder! I'll help you stay organized!",
                "I've set up that reminder for you! I'm here to help you remember!"
            ]
        };
        
        // Get response for the intent
        let response = responses[intent] || "I'm here to help! What would you like me to do for you?";
        
        // If it's an array, pick a random response
        if (Array.isArray(response)) {
            response = response[Math.floor(Math.random() * response.length)];
        }
        
        // Add personality touches
        if (this.personality.humor_level > 0.5 && Math.random() > 0.7) {
            response += this.addHumorTouch();
        }
        
        // Add proactive suggestions
        if (this.emotionalIntelligence.proactive_suggestions && Math.random() > 0.6) {
            response += this.addProactiveSuggestion(context);
        }
        
        return response;
    }
    
    addHumorTouch() {
        const humorTouches = [
            " 😊", " 😄", " 🤖", " ✨", " 💫", " 🎯", " 🚀", " 💡", " 🌟", " 🎉"
        ];
        return humorTouches[Math.floor(Math.random() * humorTouches.length)];
    }
    
    addProactiveSuggestion(context) {
        const suggestions = [
            " By the way, I noticed you might also want to add this to your calendar!",
            " I could also help you set a reminder for this if you'd like!",
            " Would you like me to add this to your journal as well?",
            " I could also help you break this down into smaller tasks if that would be helpful!",
            " I'm here if you need help with anything else related to this!"
        ];
        return suggestions[Math.floor(Math.random() * suggestions.length)];
    }

    // Reminder-specific helper functions
    extractReminderType(text) {
        if (text.match(/(?:daily|every day|each day)/i)) return 'daily';
        if (text.match(/(?:weekly|every week|each week)/i)) return 'weekly';
        if (text.match(/(?:monthly|every month|each month)/i)) return 'monthly';
        if (text.match(/(?:yearly|every year|each year)/i)) return 'yearly';
        return 'once';
    }

    extractReminderCategory(text) {
        if (text.match(/(?:medication|medicine|pill|drug|prescription|take.*med|health|medical|doctor|dentist)/i)) return 'medical';
        if (text.match(/(?:household|home|clean|garbage|bins|laundry|dishes|chores)/i)) return 'household';
        if (text.match(/(?:work|meeting|office|project|deadline|business|client|boss)/i)) return 'work';
        if (text.match(/(?:family|kids|children|parent|spouse|partner|birthday|anniversary)/i)) return 'family';
        return 'personal';
    }

    extractCrossLinks(text) {
        const links = [];
        
        // Look for shopping items
        const shoppingMatch = text.match(/(?:buy|get|pick up|purchase|shop for|add to shopping)\s+([^,]+?)(?:\s+(?:and|,|also)\s+([^,]+?))*(?:\s+(?:and|,|also)\s+([^,]+?))*/i);
        if (shoppingMatch) {
            const items = [shoppingMatch[1], shoppingMatch[2], shoppingMatch[3]].filter(Boolean);
            items.forEach(item => {
                links.push({
                    listType: 'shopping',
                    itemText: item.trim(),
                    whenToAdd: '1_week_before',
                    priority: 'medium'
                });
            });
        }
        
        // Look for todo items
        const todoMatch = text.match(/(?:do|complete|finish|work on|add to todos?)\s+([^,]+?)(?:\s+(?:and|,|also)\s+([^,]+?))*(?:\s+(?:and|,|also)\s+([^,]+?))*/i);
        if (todoMatch) {
            const items = [todoMatch[1], todoMatch[2], todoMatch[3]].filter(Boolean);
            items.forEach(item => {
                links.push({
                    listType: 'personal_todos',
                    itemText: item.trim(),
                    whenToAdd: '3_days_before',
                    priority: 'medium'
                });
            });
        }
        
        // Look for bucket list items
        const bucketMatch = text.match(/(?:add to bucket|bucket list|want to|plan to|dream of)\s+([^,]+?)(?:\s+(?:and|,|also)\s+([^,]+?))*(?:\s+(?:and|,|also)\s+([^,]+?))*/i);
        if (bucketMatch) {
            const items = [bucketMatch[1], bucketMatch[2], bucketMatch[3]].filter(Boolean);
            items.forEach(item => {
                links.push({
                    listType: 'bucket',
                    itemText: item.trim(),
                    whenToAdd: '2_weeks_before',
                    priority: 'low'
                });
            });
        }
        
        return links;
    }

    parseMultiStep(input) {
        // Parse complex multi-step requests
        const steps = [];
        const connectors = ['and also', 'then', 'after that', 'next', 'also', 'plus'];
        
        // Split by connectors
        let remaining = input;
        for (let connector of connectors) {
            if (remaining.includes(connector)) {
                const parts = remaining.split(connector);
                steps.push(parts[0].trim());
                remaining = parts.slice(1).join(connector).trim();
            }
        }
        
        if (remaining) {
            steps.push(remaining);
        }
        
        return steps;
    }
}

// Global instance
window.zoeAI = new ZoeAIProcessor();
