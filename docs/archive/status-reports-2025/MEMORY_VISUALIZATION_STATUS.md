# Memory Visualization Status & Solution

## User Question
"Are memories that it does store visualised somewhere? The user details are meant to be in their profile in the people crm"

---

## Current Situation

### ✅ Memories ARE Being Stored
Self-facts are successfully stored in the `self_facts` table:
```sql
SELECT * FROM self_facts WHERE user_id='jason'
→ favorite_color: purple
→ vehicle: ny friends named sarah (test data)
```

### ❌ NOT Visualized in People CRM
The People CRM (`/people.html`) looks for data in the `people` table with `is_self=1`, but:
- The `people.is_self.facts` field is empty: `{}`
- The `self_facts` table is separate and not displayed

---

## Root Cause

**Two Disconnected Systems:**

1. **New System:** `self_facts` table
   - Used by: Chat auto-extraction, MCP tools
   - Data: ✅ Working, facts stored correctly
   - UI: ❌ Not visualized anywhere

2. **Old System:** `people` table with `is_self=1`
   - Used by: People CRM UI
   - Data: ❌ Empty `facts` field
   - UI: ✅ Has visualization in `/people.html`

---

## Solution Implemented

### 1. Modified `/api/people/self` Endpoint
**File:** `/home/zoe/assistant/services/zoe-core/routers/people.py`

**Changes:**
- Now queries BOTH `people.is_self` AND `self_facts` tables
- Merges results into a single response
- Adds `self_facts` array for visibility
- Backward compatible with existing UI

**New Response Format:**
```json
{
  "self": {
    "id": 26,
    "user_id": "jason",
    "name": "User_jason",
    "is_self": 1,
    "facts": {
      "favorite_color": "purple",
      "vehicle": "ny friends named sarah"
    },
    "self_facts": [
      {
        "key": "favorite_color",
        "value": "purple",
        "confidence": 0.9,
        "source": "user_stated",
        "updated_at": "2025-12-08 04:42:10"
      }
    ]
  },
  "facts_count": 2,
  "source": "merged from people.is_self and self_facts table"
}
```

### 2. UI Integration Needed (Next Step)

The People CRM UI needs to be updated to display `self_facts`:

**File to modify:** `/home/zoe/assistant/services/zoe-ui/dist/people.html`

**Changes needed:**
1. Add a "My Profile" or "About Me" section
2. Display `self_facts` array from `/api/people/self`
3. Show each fact with key/value/confidence/updated_at
4. Add ability to edit/delete facts

---

## Current Status

### ✅ Backend Complete
- `/api/people/self` endpoint now returns merged data
- Self-facts from `self_facts` table included
- Backward compatible with existing code

### ⏳ Frontend Pending
- People CRM UI doesn't yet display the self_facts
- Need to add UI component to show memories

---

## Quick Test

```bash
# Test the merged endpoint
curl "http://localhost:8000/api/people/self" -H "X-Session-ID: test"

# Should return:
# - facts: merged dict of all facts
# - self_facts: array with detailed info
# - facts_count: number of stored facts
```

---

## Recommendation for User

**Short term:** Use the API endpoint to view memories:
```bash
curl "http://localhost:8000/api/people/self" -H "X-Session-ID: your-session"
```

**Long term:** Update People CRM UI to display the `self_facts` array in a dedicated "About Me" section.

---

## Files Modified

1. `/home/zoe/assistant/services/zoe-core/routers/people.py`
   - Modified `get_self()` endpoint (lines 236-268)
   - Now merges `people.is_self` + `self_facts` table

---

**Status:** ✅ Backend integration complete  
**Next Step:** Frontend UI to display self_facts  
**Workaround:** Use API endpoint directly to view memories






