---
name: personal-facts
description: Store and recall personal facts and preferences about the user
version: 1.0.0
author: zoe-team
api_only: true
priority: 5
tags:
  - memory
  - preferences
  - personal
triggers:
  - "my favorite"
  - "my favourite"
  - "my name is"
  - "i live in"
  - "i work as"
  - "i work at"
  - "my birthday is"
  - "i was born"
  - "my email is"
  - "my phone number is"
  - "what's my"
  - "what is my"
  - "do you remember my"
allowed_endpoints:
  - "GET /api/memories/facts"
  - "POST /api/memories/facts"
  - "GET /api/memories/facts/{key}"
  - "DELETE /api/memories/facts/{key}"
  - "GET /api/user-data/preferences"
  - "PUT /api/user-data/preferences"
---
# Personal Facts Skill

Store and recall personal facts and preferences about the user.

## Behavior

1. Detect if the user is storing a fact or recalling one
2. For storage: extract the key-value pair and save via API
3. For recall: look up the fact and present it naturally
4. For preferences: store under user preferences category

## Response Style

Warm and personal. When storing: "Got it, I'll remember that!"
When recalling: present the fact naturally in context.
