---
name: agent-zero-research
description: "Delegate deep research, multi-source analysis, and comparison tasks to Agent Zero. Use when the user asks to research a topic, compare options, investigate something, or wants a 'deep dive' with cited sources."
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
  - "evaluate"
  - "review options"
  - "summarize findings"
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

The user wants deep research, multi-source analysis, comparison studies, or any task requiring web browsing, document reading, or synthesizing information. Triggers include "research", "compare", "deep dive", "pros and cons", or "look into".

## Workflow

1. **Identify the task type** — research (open-ended query), comparison (two+ options), or analysis (structured evaluation)
2. **Inform the user** — tell them research is underway and may take 30–60 seconds
3. **Submit to Agent Zero** — use the appropriate endpoint (see below)
4. **Poll for completion** — check `GET /api/agent-zero/status` until the task finishes
5. **Present findings** — format results clearly: use tables for comparisons, bullet points for research summaries, and always cite sources when available

## API Endpoints

### Submit a research task
```
POST /api/agent-zero/research
{
  "query": "Best solar panels for residential use in 2026",
  "depth": "thorough",
  "max_sources": 10
}
```

### Submit a general task (comparisons, analysis)
```
POST /api/agent-zero/task
{
  "task": "Compare Tesla Powerwall vs BYD battery for home storage",
  "context": "User is considering home battery storage"
}
```

### Check task status
```
GET /api/agent-zero/status
```
Returns the current state (`pending`, `running`, `completed`, `failed`) and results when complete.

## Example

**User:** "Compare Tesla Powerwall vs BYD battery for my home"

**Steps:**
- Identify: comparison task with two items
- Respond: "Let me research that for you — this may take about a minute."
- Submit: `POST /api/agent-zero/task` with task and context
- Poll: `GET /api/agent-zero/status` until `completed`
- Present: table with capacity, price, warranty, pros/cons for each option, with source links

## Error Handling

- **Task timeout** (>120s): Inform the user the research is taking longer than expected and offer to retry or narrow the scope
- **Task failed**: Check the error from the status endpoint and relay a user-friendly message (e.g., "Agent Zero couldn't complete the research — want to try a more specific query?")
- **No results**: Suggest the user rephrase or narrow the research topic

## Formatting Guidelines

- **Research results**: Bullet-point summaries with source citations
- **Comparisons**: Side-by-side table with key attributes
- **Analysis**: Numbered findings with supporting evidence
