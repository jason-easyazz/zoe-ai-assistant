---
name: agent-zero-comparison
description: Detailed product/option comparisons using Agent Zero
version: 1.0.0
author: agent-zero-module
api_only: true
priority: 6
triggers:
  - "compare"
  - "versus"
  - " vs "
  - "which is better"
  - "pros and cons"
  - "difference between"
allowed_endpoints:
  - "POST /api/agent-zero/task"
  - "GET /api/agent-zero/status"
tags:
  - comparison
  - agent-zero
---
# Agent Zero Comparison

## When to Use
User wants to compare two or more options, products, or approaches.

## API Endpoints

### Submit Comparison
POST http://agent-zero-bridge:8101/tools/task
```json
{"task": "Compare X vs Y", "context": "comparison context"}
```

## Behavior
1. Identify the items being compared
2. Delegate to Agent Zero for thorough analysis
3. Present in a structured pros/cons or comparison format
4. End with a recommendation based on user's stated needs
