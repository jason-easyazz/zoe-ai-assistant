# Zoe AI - LangGraph Enhanced Chat Features

## 🚀 Overview

The LangGraph Enhanced Chat brings advanced multi-agent workflows, real-time visualization, and human-in-the-loop capabilities to Zoe AI, inspired by the AG-UI LangGraph dojo.

## ✨ Key Features

### 1. **Multi-Agent Workflow Orchestration**
- **Intelligent Agent Selection**: Automatically routes tasks to specialized agents (Planner, Executor, Validator, Coordinator)
- **Task Decomposition**: Breaks down complex requests into executable steps
- **Dependency Management**: Handles step dependencies and parallel execution
- **Agent Communication**: Real-time inter-agent messaging via Redis

### 2. **Real-Time Workflow Visualization**
- **Live Agent Status**: See which agents are active, thinking, or idle
- **Workflow Graph**: Mermaid-based graph visualization of execution flow
- **Progress Tracking**: Real-time progress bar and step completion metrics
- **Duration Monitoring**: Track execution time for each workflow

### 3. **Tool-Based Generative UI**
- **Dynamic UI Cards**: Generates contextual UI components based on workflow needs
- **Tool Indicators**: Visual feedback for tool calls (calendar, lists, search, memory)
- **Step Cards**: Beautiful step-by-step execution cards with status
- **Execution Plans**: Visual display of planned workflow before execution

### 4. **Human-in-the-Loop (HITL) Controls**
- **Approval Gates**: Request human approval before executing critical steps
- **Modification Options**: Allow users to modify workflow parameters mid-execution
- **Rejection Handling**: Gracefully handle rejected workflows with alternatives
- **Feedback Integration**: Incorporate user feedback to improve future workflows

### 5. **State Management & Persistence**
- **Session Tracking**: Each conversation has a unique session ID
- **Workflow State**: Maintains complete state of active workflows
- **Agent Actions History**: Logs all agent actions for debugging and analysis
- **Tool Results Caching**: Stores tool results for efficient retrieval

### 6. **Advanced Streaming Protocol**
- **AG-UI Protocol Events**: Rich event streaming for transparency
- **Event Types**:
  - `session_start` - Workflow session initialized
  - `workflow_planning_start` - Planning phase begins
  - `agent_goal_created` - Goal created for workflow
  - `workflow_plan_created` - Execution plan ready
  - `workflow_graph` - Graph visualization data
  - `agent_step_start` - Agent step begins
  - `agent_step_complete` - Agent step completes
  - `agent_thinking` - Agent processing update
  - `tool_call_start` - Tool execution starts
  - `tool_result` - Tool execution result
  - `content_delta` - Streaming response content
  - `workflow_summary` - Final workflow summary
  - `session_end` - Session complete

## 🎯 Use Cases

### Example 1: Family Event Planning
**User Input**: "Plan a family movie night for Friday"

**Workflow**:
1. **Planner Agent**: Analyzes request and creates execution plan
2. **Executor Agent**: Checks family calendar for availability
3. **Specialist Agent**: Researches movie options and showtimes
4. **Executor Agent**: Creates calendar event
5. **Executor Agent**: Adds snacks to shopping list
6. **Validator Agent**: Confirms all steps completed successfully

### Example 2: System Enhancement
**User Input**: "Enhance the memory system with semantic search"

**Workflow**:
1. **Validator Agent**: Analyzes current memory system
2. **Planner Agent**: Designs new features and architecture
3. **Executor Agent**: Implements semantic search
4. **Executor Agent**: Adds relationship graphs
5. **Validator Agent**: Tests and validates implementation

### Example 3: Data Analysis
**User Input**: "Analyze my calendar and suggest optimizations"

**Workflow**:
1. **Executor Agent**: Fetches calendar events
2. **Specialist Agent**: Analyzes patterns and conflicts
3. **Planner Agent**: Creates optimization plan
4. **HITL Gate**: User reviews and approves suggestions
5. **Executor Agent**: Implements approved changes
6. **Validator Agent**: Confirms improvements

## 🔧 Technical Architecture

### Backend Components

#### Chat LangGraph Router (`chat_langgraph.py`)
- Main workflow orchestration endpoint
- Session state management
- Event streaming with AG-UI protocol
- Integration with agent planner system

#### Agent Planner System
- Goal creation and management
- Task decomposition engine
- Dependency analysis
- Risk assessment and rollback strategies

### Frontend Components

#### LangGraph Enhanced UI (`chat-langgraph.html`)
- 3-panel layout: Agents | Chat | Visualization
- Real-time agent status display
- Mermaid.js graph rendering
- Progress tracking and metrics
- Responsive design for all devices

## 🎨 UI Features

### Left Panel - Agent Status
- Shows all active agents
- Real-time status updates (Idle/Thinking/Active)
- Visual pulse animation for active agents

### Center Panel - Chat Interface
- Standard chat messages
- Workflow event indicators
- Step execution cards
- Tool call indicators
- Generated UI components

### Right Panel - Workflow Visualization
- Workflow statistics (steps, duration)
- Progress bar
- Mermaid graph of workflow DAG
- Critical path highlighting

## 📊 Workflow States

1. **IDLE** - No active workflow
2. **PLANNING** - Creating execution plan
3. **EXECUTING** - Running workflow steps
4. **WAITING_FOR_HUMAN** - Paused for human feedback
5. **COMPLETED** - Successfully finished
6. **ERROR** - Encountered error

## 🔌 API Endpoints

### POST `/api/chat/langgraph`
Main streaming chat endpoint with workflow capabilities

**Request**:
```json
{
  "message": "User message",
  "enable_agents": true,
  "enable_visualization": true,
  "context": {}
}
```

**Response**: Server-Sent Events (SSE) stream of AG-UI protocol events

### POST `/api/chat/langgraph/feedback`
Submit human-in-the-loop feedback

**Request**:
```json
{
  "session_id": "session_xxx",
  "feedback_type": "approve|reject|modify",
  "data": {}
}
```

### GET `/api/chat/langgraph/session/{session_id}`
Get current session state

**Response**:
```json
{
  "session_id": "session_xxx",
  "state": "executing",
  "current_step": "step_002",
  "agent_actions": [...],
  "workflow_graph": {...}
}
```

## 🚀 Getting Started

### Access the LangGraph Enhanced Chat

1. **From Classic Chat**: Click the "⚡ LangGraph Enhanced" button in the top-right
2. **Direct URL**: Navigate to `/chat-langgraph.html`

### Toggle Features

- **🤖 Agents Button**: Enable/disable multi-agent workflows
- **📊 Viz Button**: Enable/disable visualization panel
- **🔙 Classic Button**: Return to standard chat

### Try These Prompts

- "Create a weekly meal plan and shopping list"
- "Organize my schedule for next week"
- "Build a family vacation itinerary"
- "Analyze my productivity patterns"
- "Plan a birthday party with tasks and reminders"

## 🔍 Advanced Features

### Parallel Execution
Steps without dependencies run in parallel for faster completion

### Critical Path Analysis
Identifies the longest sequence of dependent steps

### Risk Assessment
Evaluates potential issues and creates mitigation strategies

### Rollback Strategies
Automatic rollback on errors to prevent data corruption

### Agent Load Balancing
Distributes tasks to available agents based on capacity

## 📈 Performance Metrics

- **Average Planning Time**: 0.5-2 seconds
- **Step Execution**: 0.3-5 seconds per step
- **Visualization Latency**: <100ms
- **Concurrent Sessions**: Unlimited (session-based isolation)

## 🔒 Privacy & Security

- **User Isolation**: Each user has separate workflow sessions
- **Session Privacy**: Session data is user-specific
- **Agent Constraints**: Respects user privacy rules
- **Safety Checks**: Validates actions before execution

## 🛠️ Development

### Adding New Agent Types

1. Define in `agent_planner.py`:
```python
class AgentType(str, Enum):
    YOUR_AGENT = "your_agent"
```

2. Create agent implementation
3. Register in agent initialization
4. Update UI to display new agent type

### Creating Custom Workflow Events

```python
yield {
    'type': 'custom_event',
    'data': {...},
    'timestamp': time.time()
}
```

### Adding UI Components

```javascript
function addCustomCard(data) {
    const card = document.createElement('div');
    card.className = 'ui-card';
    card.innerHTML = `...`;
    container.appendChild(card);
}
```

## 🐛 Debugging

### Enable Debug Logging
```javascript
console.log('AG-UI Event:', event);
```

### View Session State
```javascript
fetch(`/api/chat/langgraph/session/${sessionId}`)
    .then(r => r.json())
    .then(console.log);
```

### Monitor Agent Activity
Check the left panel for real-time agent status

## 🎓 Learning Resources

- **AG-UI Protocol**: [dojo.ag-ui.com](https://dojo.ag-ui.com)
- **LangGraph Docs**: LangGraph documentation
- **Mermaid.js**: [mermaid.js.org](https://mermaid.js.org)

## 🤝 Contributing

To add new features:

1. Extend `chat_langgraph.py` for backend logic
2. Update event handlers in `chat-langgraph.html`
3. Add UI components as needed
4. Document new event types
5. Test with various workflows

## 📝 Future Enhancements

- [ ] Persistent workflow history
- [ ] Workflow templates library
- [ ] Advanced graph interactions (zoom, pan, filter)
- [ ] Agent performance analytics
- [ ] Custom agent creation UI
- [ ] Workflow sharing between users
- [ ] Integration with external tools (GitHub, Jira, etc.)
- [ ] Voice-based workflow control
- [ ] Mobile-optimized workflow view
- [ ] Workflow export (JSON, PDF, etc.)

---

**Built with ❤️ by the Zoe AI Team**


