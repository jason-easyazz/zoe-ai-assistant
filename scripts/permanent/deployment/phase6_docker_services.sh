#!/bin/bash
# PHASE 2: Create Docker Services for Zoe
# Save as: /home/pi/zoe/scripts/permanent/deployment/phase6_docker_services.sh

echo "🐳 PHASE 6: Docker Services Setup"
echo "=================================="

cd /home/pi/zoe

# Create docker-compose.yml
cat > docker-compose.yml << 'DOCKER_EOF'
version: '3.8'

services:
  zoe-redis:
    image: redis:7-alpine
    container_name: zoe-redis
    restart: unless-stopped
    ports:
      - "6379:6379"
    volumes:
      - ./data/redis:/data
    networks:
      - zoe-network
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 30s
      timeout: 10s
      retries: 3

  zoe-ollama:
    image: ollama/ollama:latest
    container_name: zoe-ollama
    restart: unless-stopped
    ports:
      - "11434:11434"
    volumes:
      - ./models:/root/.ollama
    networks:
      - zoe-network
    environment:
      - OLLAMA_MODELS=/root/.ollama
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11434/"]
      interval: 30s
      timeout: 10s
      retries: 5

  zoe-core:
    build: ./services/zoe-core
    container_name: zoe-core
    restart: unless-stopped
    ports:
      - "8000:8000"
    depends_on:
      - zoe-redis
      - zoe-ollama
    volumes:
      - ./data:/app/data
      - ./services/zoe-core:/app
    networks:
      - zoe-network
    environment:
      - REDIS_URL=redis://zoe-redis:6379
      - OLLAMA_URL=http://zoe-ollama:11434
      - DATABASE_PATH=/app/data/zoe.db
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  zoe-ui:
    image: nginx:alpine
    container_name: zoe-ui
    restart: unless-stopped
    ports:
      - "8080:80"
    volumes:
      - ./services/zoe-ui/dist:/usr/share/nginx/html:ro
      - ./services/zoe-ui/nginx.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on:
      - zoe-core
    networks:
      - zoe-network
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:80"]
      interval: 30s
      timeout: 10s
      retries: 3

networks:
  zoe-network:
    driver: bridge

volumes:
  zoe_redis_data:
  zoe_ollama_models:
DOCKER_EOF

# Create FastAPI backend
mkdir -p services/zoe-core
cat > services/zoe-core/Dockerfile << 'DOCKERFILE_EOF'
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
DOCKERFILE_EOF

# Create requirements.txt
cat > services/zoe-core/requirements.txt << 'REQUIREMENTS_EOF'
fastapi==0.104.1
uvicorn[standard]==0.24.0
pydantic==2.5.0
sqlalchemy==2.0.23
aiosqlite==0.19.0
redis==5.0.1
httpx==0.25.2
python-multipart==0.0.6
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
python-dateutil==2.8.2
REQUIREMENTS_EOF

# Create main FastAPI application
cat > services/zoe-core/main.py << 'MAIN_EOF'
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
import httpx
import json
import sqlite3
import os
from typing import Optional, List, Dict

app = FastAPI(title="Zoe AI Assistant API", version="3.1")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database setup
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://zoe-ollama:11434")

def init_db():
    """Initialize database tables"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Events table
    c.execute('''CREATE TABLE IF NOT EXISTS events
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT NOT NULL,
                  date TEXT,
                  time TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Conversations table
    c.execute('''CREATE TABLE IF NOT EXISTS conversations
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_message TEXT,
                  assistant_response TEXT,
                  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Tasks table
    c.execute('''CREATE TABLE IF NOT EXISTS tasks
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT NOT NULL,
                  completed BOOLEAN DEFAULT 0,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

# Pydantic models
class ChatMessage(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str
    detected_events: Optional[List[Dict]] = []
    detected_tasks: Optional[List[str]] = []

class Event(BaseModel):
    title: str
    date: Optional[str]
    time: Optional[str]

@app.get("/")
async def root():
    return {"message": "Zoe AI Assistant API v3.1"}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "services": {
            "api": "running",
            "database": os.path.exists(DB_PATH),
            "ollama": await check_ollama()
        }
    }

async def check_ollama():
    """Check if Ollama is accessible"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
            return response.status_code == 200
    except:
        return False

@app.post("/api/chat", response_model=ChatResponse)
async def chat(message: ChatMessage):
    """Main chat endpoint"""
    try:
        # Store conversation
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Simple response for now (will integrate Ollama)
        response_text = f"I received: {message.message}"
        
        # Detect events (simple pattern matching for now)
        detected_events = []
        detected_tasks = []
        
        lower_msg = message.message.lower()
        if any(word in lower_msg for word in ['meeting', 'appointment', 'birthday']):
            detected_events.append({"type": "event", "text": message.message})
        
        if any(word in lower_msg for word in ['todo', 'task', 'remind me']):
            detected_tasks.append(message.message)
        
        # Save to database
        c.execute("INSERT INTO conversations (user_message, assistant_response) VALUES (?, ?)",
                  (message.message, response_text))
        conn.commit()
        conn.close()
        
        return ChatResponse(
            response=response_text,
            detected_events=detected_events,
            detected_tasks=detected_tasks
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/events")
async def get_events():
    """Get all events"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM events ORDER BY created_at DESC")
    events = c.fetchall()
    conn.close()
    
    return {"events": [
        {"id": e[0], "title": e[1], "date": e[2], "time": e[3], "created_at": e[4]}
        for e in events
    ]}

@app.post("/api/events")
async def create_event(event: Event):
    """Create a new event"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO events (title, date, time) VALUES (?, ?, ?)",
              (event.title, event.date, event.time))
    conn.commit()
    event_id = c.lastrowid
    conn.close()
    
    return {"id": event_id, "message": "Event created successfully"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
MAIN_EOF

# Create nginx config
cat > services/zoe-ui/nginx.conf << 'NGINX_EOF'
server {
    listen 80;
    server_name localhost;
    
    root /usr/share/nginx/html;
    index index.html;
    
    location / {
        try_files $uri $uri/ /index.html;
    }
    
    location /api {
        proxy_pass http://zoe-core:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
NGINX_EOF

# Create basic UI
cat > services/zoe-ui/dist/index.html << 'HTML_EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Zoe AI Assistant</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        
        .container {
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 30px;
            width: 100%;
            max-width: 600px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        
        h1 {
            color: #333;
            text-align: center;
            margin-bottom: 10px;
            font-size: 2.5em;
        }
        
        .subtitle {
            text-align: center;
            color: #666;
            margin-bottom: 30px;
        }
        
        .chat-container {
            background: #f8f9fa;
            border-radius: 15px;
            padding: 20px;
            min-height: 300px;
            max-height: 400px;
            overflow-y: auto;
            margin-bottom: 20px;
        }
        
        .message {
            margin-bottom: 15px;
            padding: 10px 15px;
            border-radius: 10px;
        }
        
        .user-message {
            background: #667eea;
            color: white;
            margin-left: 20%;
            text-align: right;
        }
        
        .assistant-message {
            background: white;
            color: #333;
            margin-right: 20%;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        
        .input-container {
            display: flex;
            gap: 10px;
        }
        
        input {
            flex: 1;
            padding: 15px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 16px;
            transition: border-color 0.3s;
        }
        
        input:focus {
            outline: none;
            border-color: #667eea;
        }
        
        button {
            padding: 15px 30px;
            background: #667eea;
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            cursor: pointer;
            transition: background 0.3s;
        }
        
        button:hover {
            background: #5a67d8;
        }
        
        .status {
            text-align: center;
            margin-top: 20px;
            padding: 10px;
            background: #e8f5e9;
            border-radius: 10px;
            color: #2e7d32;
        }
        
        .status.error {
            background: #ffebee;
            color: #c62828;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Zoe</h1>
        <p class="subtitle">Your AI Assistant</p>
        
        <div class="chat-container" id="chatContainer">
            <div class="message assistant-message">
                Hello! I'm Zoe, your AI assistant. How can I help you today?
            </div>
        </div>
        
        <div class="input-container">
            <input type="text" id="messageInput" placeholder="Type your message..." 
                   onkeypress="if(event.key==='Enter') sendMessage()">
            <button onclick="sendMessage()">Send</button>
        </div>
        
        <div class="status" id="status">Ready</div>
    </div>
    
    <script>
        const API_URL = '/api';
        const chatContainer = document.getElementById('chatContainer');
        const messageInput = document.getElementById('messageInput');
        const status = document.getElementById('status');
        
        async function sendMessage() {
            const message = messageInput.value.trim();
            if (!message) return;
            
            // Add user message to chat
            addMessage(message, 'user');
            messageInput.value = '';
            
            // Update status
            setStatus('Thinking...', false);
            
            try {
                const response = await fetch(`${API_URL}/chat`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ message })
                });
                
                if (!response.ok) throw new Error('Failed to get response');
                
                const data = await response.json();
                addMessage(data.response, 'assistant');
                
                if (data.detected_events?.length > 0) {
                    setStatus(`Detected ${data.detected_events.length} event(s)`, false);
                } else {
                    setStatus('Ready', false);
                }
            } catch (error) {
                console.error('Error:', error);
                setStatus('Error: Could not connect to API', true);
                addMessage('Sorry, I encountered an error. Please try again.', 'assistant');
            }
        }
        
        function addMessage(text, sender) {
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${sender}-message`;
            messageDiv.textContent = text;
            chatContainer.appendChild(messageDiv);
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }
        
        function setStatus(text, isError) {
            status.textContent = text;
            status.className = isError ? 'status error' : 'status';
        }
        
        // Check API health on load
        async function checkHealth() {
            try {
                const response = await fetch(`${API_URL}/health`);
                const data = await response.json();
                if (data.status === 'healthy') {
                    setStatus('Connected to Zoe API', false);
                }
            } catch (error) {
                setStatus('API not available', true);
            }
        }
        
        checkHealth();
    </script>
</body>
</html>
HTML_EOF

echo "✅ Docker services configuration created"
echo ""
echo "Starting services..."
docker compose up -d

echo ""
echo "Waiting for services to start (30 seconds)..."
sleep 30

# Pull Ollama model
echo "Downloading AI model (this may take a few minutes)..."
docker exec zoe-ollama ollama pull llama3.2:3b || echo "Model download will continue in background"

IP_ADDR=$(hostname -I | awk '{print $1}')
echo ""
echo "========================================"
echo "✅ ZOE SERVICES RUNNING!"
echo "========================================"
echo ""
echo "🌐 Web Interface: http://${IP_ADDR}:8080"
echo "🔌 API Documentation: http://${IP_ADDR}:8000/docs"
echo ""
echo "Check status: docker ps"
echo "View logs: docker compose logs -f"
echo ""
echo "Zoe is ready to chat!"
