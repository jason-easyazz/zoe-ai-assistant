---
name: agent-zero-planning
description: Complex task planning and breakdown using Agent Zero
version: 1.0.0
author: agent-zero-module
api_only: true
priority: 5
triggers:
  - "plan"
  - "break down"
  - "how should i"
  - "help me plan"
  - "create a plan"
  - "strategy for"
  - "steps to"
allowed_endpoints:
  - "POST /api/agent-zero/task"
  - "GET /api/agent-zero/status"
tags:
  - planning
  - agent-zero
---
# Agent Zero Planning

## When to Use
User wants to plan a complex task, project, or event. Agent Zero will decompose the task into actionable steps.

## API Endpoints

### Submit Planning Task
POST http://agent-zero-bridge:8101/tools/task
```json
{"task": "plan description", "context": "relevant context"}
```

## Behavior
1. Understand the scope of what needs planning
2. Delegate to Agent Zero for detailed breakdown
3. Present as numbered steps with estimated effort
4. Offer to save the plan to Zoe's task list
