# Shopping List Skill

Manage shopping lists -- add items, view lists, remove items, and clear completed.

Replaces hardcoded regex patterns in chat.py for shopping list operations.

## Triggers

- add to shopping list
- add to my shopping list
- i need to buy
- put on the list
- shopping list
- show my shopping list
- what's on my list
- remove from shopping list
- clear shopping list
- mark as bought

## Behavior

1. Parse the user's intent (add, view, remove, clear)
2. Extract item names and optional quantities
3. Call the appropriate API endpoint
4. Confirm the action with a natural response

## API Endpoints (api_only)

- `GET /api/lists/shopping` - Get current shopping list
- `POST /api/lists/shopping/items` - Add item(s) to list
- `DELETE /api/lists/shopping/items/{id}` - Remove item from list
- `PUT /api/lists/shopping/items/{id}` - Update item (mark bought, change quantity)
- `DELETE /api/lists/shopping` - Clear entire list

## Response Style

Brief and confirming. Use checkmarks for completed items.
Group items by category when displaying the full list.
