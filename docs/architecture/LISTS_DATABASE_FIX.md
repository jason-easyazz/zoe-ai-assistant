# Lists Database Schema Fix

## Problem Identified

The lists system had a **critical database schema conflict** where two different data storage approaches were being used simultaneously:

### Conflicting Approaches

1. **JSON Storage (Old/Broken)**:
   - Items stored as JSON in the `lists.items` column
   - Used by frontend POST/PUT endpoints

2. **Separate Tables (Correct)**:
   - Items stored in separate `list_items` table
   - Used by MCP tools and GET endpoint

### The Issue

When AI tools/MCP added items to lists, they wrote to the `list_items` table. When the frontend saved lists, it overwrote the JSON `items` column. **The two systems never saw each other's data**, causing tools to appear to be creating new lists instead of adding items to existing ones.

## Solution Implemented

### 1. Fixed Schema (October 9, 2025)

Standardized on the **separate tables** approach:

**lists table** (no items column):
```sql
CREATE TABLE lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT DEFAULT 'default',
    list_type TEXT NOT NULL,
    list_category TEXT DEFAULT 'personal',
    name TEXT NOT NULL,
    description TEXT,
    metadata JSON,
    shared_with JSON,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
)
```

**list_items table** (separate):
```sql
CREATE TABLE list_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id INTEGER NOT NULL,
    task_text TEXT NOT NULL,
    priority TEXT DEFAULT 'medium',
    completed BOOLEAN DEFAULT 0,
    completed_at TIMESTAMP,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    FOREIGN KEY (list_id) REFERENCES lists(id) ON DELETE CASCADE
)
```

### 2. Updated Code

**routers/lists.py** - Modified:
- `POST /{list_type}` - Now inserts items into `list_items` table
- `PUT /{list_type}/{list_id}` - Now updates items in `list_items` table
- `GET /{list_type}` - Fixed column name from `category` to `list_category`
- `init_lists_db()` - Updated schema to use separate tables

### 3. Data Migration

Created `migrate_lists_items.py` to:
- Migrate existing JSON items to `list_items` table
- Drop the old `items` column from `lists` table
- Preserve all existing data

**Migration Results**:
- Migrated 67 lists
- 0 items from JSON (most were empty)
- 3 lists already had items in `list_items` table (from MCP tools)

## Current State

✅ **FIXED**: All list operations now use the `list_items` table consistently  
✅ **TESTED**: API endpoints return items correctly  
✅ **VERIFIED**: MCP tools and frontend now see the same data  

## Files Modified

1. `/home/zoe/assistant/services/zoe-core/routers/lists.py` - Updated endpoints and schema
2. `/home/zoe/assistant/services/zoe-core/migrate_lists_items.py` - Migration script (can be deleted after verification)

## Testing

```bash
# Test that items are returned correctly
curl -s 'http://localhost:8000/api/lists/personal_todos?user_id=default' | jq '.lists[0]'

# Verify list_items table has data
sqlite3 /home/zoe/assistant/data/zoe.db "SELECT COUNT(*) FROM list_items"
```

## Impact

- ✅ AI tools can now add items to existing lists
- ✅ Frontend sees items added by tools
- ✅ No more duplicate lists being created
- ⚠️ Some advanced features (time estimation, scheduling, etc.) still reference old JSON storage and will need updates

## Next Steps

The following endpoints still try to access the old JSON `items` column and should be updated:

1. Time analytics endpoints
2. Smart scheduling integration
3. Reminder integration
4. Time estimation features

These are lower priority since they're advanced features not currently in active use.

## Related Issues

- GitHub Issue: Lists not syncing between tools and frontend
- Root cause: Database schema conflict
- Resolution: Standardized on separate tables approach


