# Personal Facts Skill

Store and recall personal facts and preferences about the user.

Replaces hardcoded regex patterns in chat.py for personal information management.

## Triggers

- my favorite
- my favourite
- my name is
- i live in
- i work as
- i work at
- my birthday is
- i was born
- my email is
- my phone number is
- what's my
- what is my
- do you remember my
- remember that i
- i prefer
- i like
- i don't like
- i hate

## Behavior

1. Detect if the user is storing a fact or recalling one
2. For storage: extract the key-value pair and save via API
3. For recall: look up the fact and present it naturally
4. For preferences: store under user preferences category

## API Endpoints (api_only)

- `GET /api/memories/facts` - List all stored facts for user
- `POST /api/memories/facts` - Store a new fact
- `GET /api/memories/facts/{key}` - Get specific fact
- `DELETE /api/memories/facts/{key}` - Remove a fact
- `GET /api/user-data/preferences` - Get user preferences
- `PUT /api/user-data/preferences` - Update user preferences

## Response Style

Warm and personal. When storing: "Got it, I'll remember that!"
When recalling: present the fact naturally in context.
