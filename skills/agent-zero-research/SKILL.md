---
name: agent-zero-research
description: Deep research, analysis, and comparison tasks using Agent Zero
version: 1.0.0
author: zoe-team
api_only: true
priority: 5
triggers:
  - "research"
  - "investigate"
  - "analyze"
  - "compare"
  - "deep dive"
  - "look into"
  - "find out about"
  - "what are the best"
  - "pros and cons"
allowed_endpoints:
  - "POST /api/agent-zero/research"
  - "POST /api/agent-zero/task"
  - "GET /api/agent-zero/status"
tags:
  - research
  - analysis
  - agent-zero
---
# Agent Zero Research

## When to Use
User wants deep research, multi-source analysis, comparison studies, or any task that requires browsing the web, reading documents, or synthesizing information from multiple sources.

## How to Handle

1. Identify what the user wants to research or analyze
2. Determine the type: research, comparison, analysis, investigation
3. Delegate to Agent Zero with clear instructions
4. Present findings to the user in a clear, structured format

## API Endpoints

### Submit Research Task
POST http://localhost:8101/tools/research
```json
{
  "query": "Best solar panels for residential use in 2026",
  "depth": "thorough",
  "max_sources": 10
}
```

### Submit General Task
POST http://localhost:8101/tools/task
```json
{
  "task": "Compare Tesla Powerwall vs BYD battery for home storage",
  "context": "User is considering home battery storage"
}
```

### Check Task Status
GET http://localhost:8101/tools/status

## Examples
- "Research the best solar panels" -> research task with depth: thorough
- "Compare Tesla vs BYD" -> comparison task with both items
- "Look into home automation trends" -> research task
- "Analyze my energy usage patterns" -> analysis task

## Important Notes
- Research tasks can take 30-60 seconds to complete
- Always tell the user you're researching and will report back
- Present findings in a clear, structured format
- Cite sources when available
- For comparisons, use a pros/cons or table format
