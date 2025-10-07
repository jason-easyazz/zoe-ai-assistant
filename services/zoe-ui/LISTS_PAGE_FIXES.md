# Lists Page Fixes - October 7, 2025

## Issues Identified

### 1. **500 Error on DELETE Requests**
**Problem:** When trying to delete lists, the server returned 500 errors
```
sqlite3.OperationalError: no such column: list_type
```

**Root Cause:** The database schema was outdated and missing required columns that the lists router expected.

**Solution:** Created and ran a migration script to update the database schema:
- Added `list_type` column (migrated from category values)
- Added `list_category` column (renamed from category)
- Added `items` column as JSON (migrated from list_items table)
- Added `metadata` column as JSON
- Added `shared_with` column as JSON

**File:** `services/zoe-core/migrate_lists_schema.py`

### 2. **WebSocket Connection Error**
**Problem:** Browser console showed error:
```
WebSocket connection to 'wss://zoe.local/ws/intelligence' failed
```

**Root Cause:** Browser was caching old JavaScript code that included WebSocket connection attempts

**Solution:** 
1. Added cache-busting version parameters to script tags in lists.html:
   - `js/auth.js?v=20251007`
   - `js/common.js?v=20251007`

2. Updated nginx.conf to add proper cache headers for JavaScript and CSS files:
   ```nginx
   location ~* \.(js|css)$ {
       add_header Cache-Control "no-cache, must-revalidate";
       add_header Pragma "no-cache";
       expires -1;
   }
   ```

## Files Modified

1. **services/zoe-core/migrate_lists_schema.py** (NEW)
   - Database migration script to update lists table schema
   
2. **services/zoe-ui/dist/lists.html**
   - Added version parameters to script tags for cache-busting
   
3. **services/zoe-ui/nginx.conf**
   - Added cache control headers for .js and .css files

## Testing the Fixes

### 1. Test Database Schema
```bash
docker exec zoe-core-test python3 -c "import sqlite3; conn = sqlite3.connect('/app/data/zoe.db'); cursor = conn.cursor(); cursor.execute('PRAGMA table_info(lists)'); print([col[1] for col in cursor.fetchall()])"
```

Expected output should include: `list_type`, `list_category`, `items`, `metadata`, `shared_with`

### 2. Test DELETE Endpoint
1. Navigate to https://zoe.local/lists.html
2. Create a new list using the + button
3. Try to delete the list using the × button
4. Should delete successfully without 500 errors

### 3. Test Cache-Busting
1. Hard refresh the browser (Ctrl+Shift+R or Cmd+Shift+R)
2. Check browser console - should not see WebSocket errors
3. Check Network tab - JavaScript files should have version parameters

## Current Database Schema

```
lists table:
  - id (INTEGER)
  - user_id (TEXT)
  - name (TEXT)
  - category (TEXT) - legacy, kept for compatibility
  - description (TEXT)
  - created_at (TIMESTAMP)
  - updated_at (TIMESTAMP)
  - list_type (TEXT) - NEW
  - list_category (TEXT) - NEW
  - items (JSON) - NEW
  - metadata (JSON) - NEW
  - shared_with (JSON) - NEW
```

## Next Steps / Recommendations

1. **Session-Based Authentication:** Update the lists router to use session-based authentication instead of query parameters for better security:
   ```python
   from routers.sessions import get_session_from_header
   
   async def delete_list(
       list_type: str,
       list_id: int,
       session: Session = Depends(get_session_from_header)
   ):
       user_id = session.user_id
       # ... rest of the code
   ```

2. **Remove Legacy Column:** After verifying everything works, the old `category` column can be dropped in a future migration

3. **Add Startup Hook:** Add database initialization to the main app startup:
   ```python
   @app.on_event("startup")
   async def startup_event():
       from routers.lists import init_lists_db
       init_lists_db()
   ```

## Verification Commands

```bash
# Check nginx is using updated config
docker exec zoe-ui cat /etc/nginx/conf.d/default.conf | grep "Cache-Control"

# Check lists.html has version parameters
docker exec zoe-ui grep "js/auth.js" /usr/share/nginx/html/lists.html

# View recent logs for errors
docker logs zoe-core-test 2>&1 | tail -50

# Test API endpoint directly
curl -X GET "https://zoe.local/api/lists/shopping" -k -H "X-Session-ID: <your-session-id>"
```

## Summary

✅ Database schema migrated successfully  
✅ Cache-busting implemented for JavaScript files  
✅ Nginx cache headers configured  
✅ WebSocket error resolved (old cached code)  
✅ DELETE endpoint should now work correctly  

The lists page should now function properly without 500 errors or WebSocket connection issues.

