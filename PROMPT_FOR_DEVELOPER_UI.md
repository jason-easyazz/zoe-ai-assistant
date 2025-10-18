# Prompt for Building Enhanced Developer UI

Copy and paste this entire prompt to Claude to build a beautiful, modern developer interface.

---

# Build a Beautiful Developer Dashboard UI for Zoe AI Assistant

## Project Context

You are building a modern, glass-morphic developer dashboard for **Zoe AI Assistant** - a comprehensive AI-powered personal assistant running on a Raspberry Pi 5. The developer section (called "Zack") provides advanced tools for managing the system, tracking issues, managing Docker containers, and generating automation workflows.

## Current System Status

- **Backend**: Fully implemented and operational (100% test success)
- **APIs**: 30+ endpoints all working and tested
- **Authentication**: Dev mode enabled (no login required for localhost/private networks)
- **Current UI**: Basic HTML dashboard exists but needs major upgrade
- **Location**: `/home/pi/zoe/services/zoe-ui/dist/developer/`

## Your Mission

Create a **stunning, modern, production-quality developer dashboard** that:
1. Looks professional and polished (think Vercel, Railway, or Tailwind UI quality)
2. Works perfectly on desktop and tablet
3. Has smooth animations and transitions
4. Provides real-time updates via API polling
5. Is built with vanilla HTML/CSS/JS (no build step required)

---

## Available APIs (All Working & Tested)

### 1. Developer Chat (Zack) - AI Assistant
```
Base URL: http://localhost:8000/api/developer-chat

GET  /status
  Returns: { status, personality, intelligence_systems, capabilities }

POST /chat
  Body: { message: string, user_id: string, interface: "developer" }
  Returns: { response: string, type: string, ai_enhanced: boolean }

GET  /history/{user_id}?limit=10
  Returns: { user_id, episodes: [], count }
```

**Intelligence Systems Available**:
- Temporal memory (conversation continuity)
- Enhanced MEM agent (semantic search)
- Cross-agent orchestration
- Learning system
- Predictive intelligence
- Preference learner
- Unified learner
- RouteLLM (model selection)

---

### 2. Issues Tracking (GitHub-Style)
```
Base URL: http://localhost:8000/api/issues

GET  /
  Query: status, issue_type, severity, assigned_to, limit, offset
  Returns: { issues: [], count, limit, offset }

POST /
  Body: {
    title: string,
    description?: string,
    issue_type: "bug"|"feature"|"enhancement"|"question",
    severity: "critical"|"high"|"medium"|"low",
    priority?: 1-5,
    reporter?: string,
    labels?: string[],
    affected_files?: string[]
  }
  Returns: { success, issue_id, issue_number, title }

GET  /{issue_id}
  Returns: Full issue details + comments

PATCH /{issue_id}
  Body: { title?, description?, status?, severity?, assigned_to?, ... }
  Returns: { success, issue_id, message }

POST /{issue_id}/comments
  Body: { author: string, comment_text: string }
  Returns: { success, comment_id, issue_id }

GET  /analytics
  Returns: {
    timestamp,
    by_status: { open, in_progress, resolved, closed },
    by_type: { bug, feature, enhancement, question },
    by_severity: { critical, high, medium, low },
    avg_resolution_days,
    top_reporters: []
  }
```

**Issue Types**: bug, feature, enhancement, question  
**Severities**: critical, high, medium, low  
**Statuses**: open, in_progress, resolved, closed, wontfix

---

### 3. Docker Management (Portainer-Like)
```
Base URL: http://localhost:8000/api/docker

GET  /status
  Returns: { available: boolean, service, version }

GET  /containers?all=true
  Returns: { containers: [], count }
  Container: { id, name, image, status, state, created, ports, networks }

GET  /containers/{id}/stats
  Returns: {
    container_id, container_name, timestamp,
    cpu_percent, memory_usage_mb, memory_limit_mb, memory_percent,
    network_rx_mb, network_tx_mb,
    block_read_mb, block_write_mb
  }

GET  /stats
  Returns: { stats: [all running container stats], count }

POST /containers/{id}/start
  Returns: { success, message }

POST /containers/{id}/stop?timeout=10
  Returns: { success, message }

POST /containers/{id}/restart?timeout=10
  Returns: { success, message }

GET  /containers/{id}/logs?tail=100&since=timestamp
  Returns: { container_id, logs: string, lines }

GET  /images
  Returns: { images: [], count }
  Image: { id, tags, created, size_mb }

GET  /networks
  Returns: { networks: [], count }

GET  /volumes
  Returns: { volumes: [], count }

GET  /system/df
  Returns: {
    images: { count, size_mb },
    containers: { count, size_mb },
    volumes: { count, size_mb }
  }
```

**Key Containers**:
- zoe-core (API server)
- zoe-ui (Frontend)
- zoe-ollama (AI models)
- zoe-redis (Cache)
- zoe-whisper (Speech-to-text)
- zoe-tts (Text-to-speech)
- zoe-n8n (Automation)

---

### 4. n8n Workflow Generation
```
Base URL: http://localhost:8000/api/n8n

GET  /status
  Returns: { available, service, version }

GET  /templates
  Returns: { templates: [], count }
  Template: { id, name, description, node_count }

POST /generate
  Body: { description: string, name?: string, activate?: boolean }
  Returns: {
    success, workflow: object, message,
    preview: { name, node_count, nodes: [] }
  }

POST /generate-from-template
  Body: { template_id: string, parameters?: object }
  Returns: { success, workflow, message, preview }

POST /analyze-request?description=string
  Returns: {
    description, template_detected, suggested_nodes: [],
    estimated_complexity, can_generate
  }

GET  /capabilities
  Returns: {
    features: [],
    supported_patterns: [],
    node_types: []
  }
```

**Available Templates**:
1. webhook_to_slack - Webhook → Slack notification
2. schedule_to_api - Scheduled API calls
3. email_to_database - Email processing to DB

---

### 5. System Health & Metrics
```
GET  http://localhost:8000/health
  Returns: { status: "healthy", service, version, features: [] }

GET  http://localhost:8000/api/system/status
  Returns: System status information

GET  http://localhost:8000/api/developer/status
  Returns: { status, mode, ai_enabled, metrics, capabilities }

GET  http://localhost:8000/api/developer/metrics
  Returns: {
    cpu, cpu_percent, cpu_cores: [],
    memory: { percent, used, total },
    disk: { percent, used, total },
    top_memory_processes: [],
    large_directories: []
  }
```

---

## Authentication

**NO AUTHENTICATION REQUIRED** for local development!

- Dev mode is enabled by default
- All requests from localhost/private networks work without headers
- For production (if needed): Include `X-Session-ID` header

---

## Design Guidelines

### Visual Style
- **Glass-morphism design** with frosted glass effects
- **Modern gradients** (purple/blue theme: #7B61FF, #5AE0E0)
- **Smooth animations** (0.2s-0.3s transitions)
- **Card-based layout** with subtle shadows
- **Clean typography** (SF Pro Display, Inter, or system fonts)
- **Status indicators** with color coding:
  - Green (#4ade80): Running/healthy
  - Yellow (#fbbf24): Warning
  - Red (#ef4444): Error/critical
  - Gray (#94a3b8): Stopped/inactive

### Layout Structure
```
┌─────────────────────────────────────────────────────────────┐
│ Header: Logo + Navigation + User Menu                       │
├─────────────────────────────────────────────────────────────┤
│ Stats Row: 4-6 key metrics cards with icons                 │
├─────────────────────────────────────────────────────────────┤
│ Main Content:                                                │
│ ┌─────────────────────┬──────────────────────────────────┐ │
│ │ Left Sidebar        │ Main Dashboard Area              │ │
│ │ - Quick Actions     │ - Container Grid                 │ │
│ │ - Recent Activity   │ - Issues List                    │ │
│ │ - Chat Widget       │ - Workflow Builder               │ │
│ │                     │ - Logs Viewer                    │ │
│ └─────────────────────┴──────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### Components Needed

1. **Dashboard Overview** (dashboard.html)
   - Stats cards: Containers, Issues, Workflows, Health
   - Quick actions buttons
   - Container status grid
   - Recent activity feed
   - System metrics chart (optional)

2. **Chat Interface** (chat.html)
   - Message list with user/assistant bubbles
   - Input box with send button
   - Typing indicator
   - Chat history
   - System capabilities panel

3. **Issues Board** (issues.html)
   - Kanban-style board (Open | In Progress | Resolved)
   - Issue cards with severity badges
   - Create issue modal
   - Issue detail view
   - Analytics dashboard
   - Filters: type, severity, status

4. **Docker Management** (docker.html)
   - Container cards with status
   - Action buttons (start/stop/restart)
   - Stats graphs (CPU, memory)
   - Logs viewer with search
   - Image/network/volume tabs

5. **Workflow Builder** (workflows.html)
   - Template gallery
   - Natural language input
   - Workflow preview
   - Generated workflow display
   - Export/deploy buttons

6. **Navigation**
   - Persistent sidebar or top nav
   - Active state indicators
   - Icons for each section
   - Responsive hamburger menu

---

## Technical Requirements

### Technology Stack
- **HTML5** - Semantic markup
- **CSS3** - Custom styles (no frameworks, but can use CSS variables)
- **Vanilla JavaScript** - ES6+ features OK
- **NO build process** - Must work directly in browser
- **NO external dependencies** - Everything inline or from CDN

### Code Structure
```
/home/pi/zoe/services/zoe-ui/dist/developer/
├── index.html (redirect to dashboard)
├── dashboard.html (main overview)
├── chat.html (Zack chat interface)
├── issues.html (issue tracking)
├── docker.html (container management)
├── workflows.html (n8n generation)
├── assets/
│   ├── styles.css (shared styles)
│   └── scripts.js (shared utilities)
```

### JavaScript Patterns

**API Helper**:
```javascript
const API = {
  BASE: 'http://localhost:8000',
  
  async get(endpoint) {
    const response = await fetch(`${this.BASE}${endpoint}`);
    if (!response.ok) throw new Error(`API Error: ${response.status}`);
    return await response.json();
  },
  
  async post(endpoint, data) {
    const response = await fetch(`${this.BASE}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    if (!response.ok) throw new Error(`API Error: ${response.status}`);
    return await response.json();
  }
};

// Usage
const containers = await API.get('/api/docker/containers');
const chatResponse = await API.post('/api/developer-chat/chat', {
  message: "Hello",
  user_id: "developer",
  interface: "developer"
});
```

**Auto-refresh**:
```javascript
function startAutoRefresh(fetchFunction, interval = 5000) {
  fetchFunction(); // Initial fetch
  return setInterval(fetchFunction, interval);
}

// Usage
const refreshId = startAutoRefresh(fetchContainerStats, 3000);
// Stop with: clearInterval(refreshId)
```

---

## Feature Requirements by Page

### 1. Dashboard (dashboard.html)
Must Have:
- [ ] 4 stat cards: Containers running, Open issues, Active workflows, System health
- [ ] Container status grid (all 7 containers)
- [ ] Quick action buttons (Chat, Create Issue, Generate Workflow, Manage Docker)
- [ ] Recent activity feed (last 10 events)
- [ ] Auto-refresh every 5 seconds

Nice to Have:
- [ ] CPU/Memory usage chart
- [ ] Quick search across all features
- [ ] Favorite actions
- [ ] System uptime

### 2. Chat (chat.html)
Must Have:
- [ ] Chat message history
- [ ] Message input with send button
- [ ] User/assistant message bubbles
- [ ] Typing indicator while waiting
- [ ] System capabilities display
- [ ] Clear conversation button

Nice to Have:
- [ ] Code syntax highlighting in responses
- [ ] Copy to clipboard for code blocks
- [ ] Session history dropdown
- [ ] Suggested prompts

### 3. Issues (issues.html)
Must Have:
- [ ] Kanban board view (Open | In Progress | Resolved)
- [ ] Create new issue button + modal
- [ ] Issue cards with: title, severity badge, type badge, timestamp
- [ ] Issue detail modal/view
- [ ] Add comment functionality
- [ ] Status change buttons
- [ ] Analytics panel (by type, severity, status)

Nice to Have:
- [ ] Search/filter issues
- [ ] Sort by severity/date
- [ ] Bulk actions
- [ ] Issue templates
- [ ] Export to CSV

### 4. Docker (docker.html)
Must Have:
- [ ] Container cards with status indicator
- [ ] Start/Stop/Restart buttons per container
- [ ] Real-time stats (CPU %, Memory %)
- [ ] Logs viewer with tail
- [ ] Tabs: Containers | Images | Networks | Volumes
- [ ] Disk usage breakdown

Nice to Have:
- [ ] Stats history chart
- [ ] Log search/filter
- [ ] Container terminal (if possible)
- [ ] Bulk container actions
- [ ] Export logs

### 5. Workflows (workflows.html)
Must Have:
- [ ] Natural language input box
- [ ] Template gallery (3 templates)
- [ ] Generate button
- [ ] Workflow preview (node list)
- [ ] JSON output display
- [ ] Copy to clipboard button

Nice to Have:
- [ ] Visual workflow diagram
- [ ] Edit workflow parameters
- [ ] Deploy to n8n button
- [ ] Workflow history
- [ ] Template creator

---

## Example Code Snippets

### Glass-morphic Card CSS
```css
.glass-card {
  background: rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
  padding: 1.5rem;
  transition: transform 0.2s, box-shadow 0.2s;
}

.glass-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.15);
}
```

### Gradient Button
```css
.btn-gradient {
  background: linear-gradient(135deg, #7B61FF, #5AE0E0);
  color: white;
  border: none;
  padding: 0.75rem 1.5rem;
  border-radius: 8px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.2s;
}

.btn-gradient:hover {
  opacity: 0.9;
}
```

### Status Badge
```css
.badge {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.25rem 0.75rem;
  border-radius: 9999px;
  font-size: 0.875rem;
  font-weight: 500;
}

.badge-success {
  background: #d1fae5;
  color: #065f46;
}

.badge-warning {
  background: #fef3c7;
  color: #92400e;
}

.badge-error {
  background: #fee2e2;
  color: #991b1b;
}
```

---

## Testing & Validation

After building, test these scenarios:

1. **Dashboard loads** and shows all stats
2. **Chat sends message** and receives response
3. **Create issue** successfully
4. **View issue details** and add comment
5. **Container stats** update in real-time
6. **Restart container** works
7. **Generate workflow** from description
8. **All navigation** links work
9. **Auto-refresh** updates data
10. **Mobile responsive** (test on smaller screen)

---

## Success Criteria

Your UI is successful if:
- [ ] Looks professional and modern (like a SaaS product)
- [ ] All APIs integrate correctly
- [ ] Real-time updates work smoothly
- [ ] No console errors
- [ ] Responsive on desktop and tablet
- [ ] Smooth animations throughout
- [ ] Intuitive navigation
- [ ] Clear visual hierarchy
- [ ] Status indicators are clear
- [ ] Works without authentication

---

## Additional Notes

- **Current UI exists** at `/home/pi/zoe/services/zoe-ui/dist/developer/dashboard.html` (basic version - you can reference it or start fresh)
- **Base API URL**: `http://localhost:8000` (always use this)
- **Dev mode enabled**: No auth headers needed
- **All endpoints tested**: 100% working, 21/21 tests passing
- **Color scheme**: Purple/blue gradients (#7B61FF, #5AE0E0) but feel free to enhance
- **Icons**: Can use emoji or Unicode symbols (no external icon libraries)

---

## Example API Calls for Testing

```javascript
// Test 1: Get system health
const health = await fetch('http://localhost:8000/health').then(r => r.json());

// Test 2: List containers
const containers = await fetch('http://localhost:8000/api/docker/containers').then(r => r.json());

// Test 3: Chat with Zack
const chat = await fetch('http://localhost:8000/api/developer-chat/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    message: "What can you do?",
    user_id: "developer",
    interface: "developer"
  })
}).then(r => r.json());

// Test 4: List issues
const issues = await fetch('http://localhost:8000/api/issues/').then(r => r.json());

// Test 5: Generate workflow
const workflow = await fetch('http://localhost:8000/api/n8n/generate', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    description: "Send Slack notification when webhook is triggered"
  })
}).then(r => r.json());
```

---

## Start Here

1. **Read this entire prompt** to understand the system
2. **Test the APIs** with the example calls above
3. **Create a plan** for the UI structure
4. **Build incrementally**: Dashboard → Chat → Issues → Docker → Workflows
5. **Test each page** as you complete it
6. **Ensure smooth UX** with loading states and error handling

**Goal**: Create a developer dashboard that's so beautiful and functional that it could be featured on a design showcase website. Think Vercel, Railway, or Retool quality. You have all the APIs you need - now make them shine! ✨

