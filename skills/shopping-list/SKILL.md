---
name: shopping-list
description: Manage shopping lists -- add items, view lists, remove items, and clear completed
version: 1.0.0
author: zoe-team
api_only: true
priority: 5
tags:
  - lists
  - shopping
triggers:
  - "add to shopping list"
  - "add to my shopping list"
  - "i need to buy"
  - "put on the list"
  - "shopping list"
  - "show my shopping list"
  - "what's on my list"
  - "remove from shopping list"
  - "clear shopping list"
  - "mark as bought"
allowed_endpoints:
  - "GET /api/lists/shopping"
  - "POST /api/lists/shopping/items"
  - "DELETE /api/lists/shopping/items/{id}"
  - "PUT /api/lists/shopping/items/{id}"
  - "DELETE /api/lists/shopping"
---
# Shopping List Skill

Manage shopping lists -- add items, view lists, remove items, and clear completed.

## Behavior

1. Parse the user's intent (add, view, remove, clear)
2. Extract item names and optional quantities
3. Call the appropriate API endpoint
4. Confirm the action with a natural response

## Response Style

Brief and confirming. Use checkmarks for completed items.
Group items by category when displaying the full list.
