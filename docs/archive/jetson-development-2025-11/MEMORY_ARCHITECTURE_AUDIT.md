# Memory Architecture Audit - Before Consolidation
**Date:** 2025-11-12  
**Status:** 🔍 REVIEW IN PROGRESS

---

## Executive Summary

Currently, Zoe has **TWO SEPARATE** systems for storing facts about people:
1. **`user_profiles` table** - For the logged-in user's personality/profile
2. **`people` table** - For contacts and other people

**Problem:** When user says "My favorite food is pizza", it should store somewhere, but the systems are separate and inconsistent.

**Proposed Solution:** **UNIFIED PEOPLE TABLE** - Store everyone (including self) in ONE table with `relationship="self"` flag.

---

## Current State Analysis

### 1. `user_profiles` Table

**Purpose:** Compatibility matching, personality analysis  
**Schema:**
```sql
CREATE TABLE user_profiles (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    name TEXT,
    bio TEXT,
    location TEXT,
    timezone TEXT,
    birthday DATE,
    avatar_url TEXT,
    age_range TEXT,
    personality_traits JSON,      -- Big 5 + custom
    values_priority JSON,          -- 12 value dimensions
    interests JSON,                -- With intensity/skill level
    life_goals JSON,               -- With timeframes
    communication_styles JSON,     -- Enum array
    social_energy TEXT,            -- Enum
    current_life_phase TEXT,       -- Enum
    daily_routine_type TEXT,       -- Enum
    profile_completeness REAL,     -- 0-1 score
    confidence_score REAL,         -- 0-1 score
    ai_insights JSON,              -- Free-form observations
    observed_patterns JSON,        -- Behavior patterns
    onboarding_completed BOOLEAN,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
)
```

**Current Rows:** 0 (not actively used yet)

**Used By:**
- `/api/user/profile` - GET/POST profile data
- `profile_analyzer.py` - Analyzes compatibility
- `onboarding.py` - Collects initial profile

**Designed For:**
- Zoe-to-Zoe friend matching
- Personality-based recommendations
- Communication style adaptation

---

### 2. `people` Table

**Purpose:** Contact management, relationship tracking  
**Schema:**
```sql
CREATE TABLE people (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,           -- Owner of this record
    name TEXT NOT NULL,
    folder_path TEXT,                -- File system integration
    profile JSON,                    -- Flexible profile data
    facts JSON,                      -- Flexible facts storage
    important_dates JSON,            -- Dates related to person
    preferences JSON,                -- Their preferences
    relationship TEXT,               -- friend, family, coworker
    birthday DATE,
    phone TEXT,
    email TEXT,
    address TEXT,
    avatar_url TEXT,
    notes TEXT,
    tags TEXT,                       -- JSON array
    metadata JSON,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
)
```

**Current Rows:** 6  
**Sample Data:**
```
ID:3  User:72038d8e  Name:Test Person       Rel:None
ID:21 User:jason     Name:Teneeka           Rel:inner
```

**Used By:**
- `/api/people` - Full CRUD API (32 matches in code)
- `person_expert.py` - Person-specific insights
- `birthday_calendar.py` - Birthday tracking
- `memory_system.py` - Person-based memories

**Related Tables:**
- `person_timeline` (0 rows) - Events in person's life
- `person_activities` (0 rows) - Activities done with person
- `person_conversations` (0 rows) - Conversation topics
- `person_gifts` (0 rows) - Gift ideas
- `person_important_dates` (0 rows) - Special dates
- `person_shared_goals` (0 rows) - Goals with person
- `relationships` (1 row) - Graph of person-to-person relationships

---

## Current Data Flow

### When User Says "My favorite food is pizza"

**Current Behavior (93.8% test success):**
1. ✅ **Detected as action** (via action_patterns)
2. ✅ **Routes to tool calling** (qwen model with llama.cpp)
3. ❓ **Storage:** Unclear where it goes
   - Not in `user_profiles` (0 rows)
   - Not in `people` (no "self" entry)
   - Possibly in mem-agent? (checked: 0 results)
   - **LIKELY NOT STORED AT ALL**

**Issue:** Tool calling works, but storage is undefined.

---

### When User Says "Sarah likes sushi"

**Current Behavior:**
1. ✅ Detected as action
2. ✅ Routes to tool calling
3. ✅ Stored in `people` table (ID:17, Name:Sarah)
4. ✅ Can be retrieved via `/api/people/{id}`

**Works well!**

---

## Analysis: Why Current Design is Problematic

### 1. **Duplicate Logic**
```python
# For user (self)
→ user_profiles table → Complex schema → Rarely used

# For others
→ people table → Flexible schema → Actively used
```

### 2. **Inconsistent APIs**
```python
# Get my favorite food
GET /api/user/profile → Parse ai_insights JSON → ???

# Get Sarah's favorite food  
GET /api/people/17 → profile.favorite_food or facts.food
```

### 3. **No "Self" Entry**
- Can't answer "What's my favorite food?" consistently
- Can't use same graph relationships for self
- Can't do "me vs Sarah" comparisons easily

### 4. **Complexity Barrier**
- `user_profiles` has 24 columns, designed for deep compatibility matching
- Most users just want to store "I like pizza"
- Over-engineered for simple facts

---

## Proposed Unified Architecture

### Core Principle
**ONE table for ALL people** - including yourself

### New `people` Table Schema

```sql
CREATE TABLE people (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,           -- WHO owns this record (multi-user support)
    name TEXT NOT NULL,
    relationship TEXT,               -- "self", "friend", "family", "spouse", etc.
    is_self BOOLEAN DEFAULT 0,       -- Quick filtering
    
    -- Contact Info
    phone TEXT,
    email TEXT,
    address TEXT,
    birthday DATE,
    avatar_url TEXT,
    
    -- Flexible Data (JSON)
    profile JSON,                    -- Rich profile (personality if needed)
    facts JSON,                      -- Simple facts: {favorite_food: "pizza"}
    preferences JSON,                -- Preferences and settings
    important_dates JSON,            -- Special dates
    personality_traits JSON,         -- Optional: Big 5 etc (for self or others)
    interests JSON,                  -- Hobbies, passions
    
    -- Notes & Organization
    notes TEXT,
    tags TEXT,                       -- JSON array
    folder_path TEXT,
    metadata JSON,
    
    -- Timestamps
    created_at TIMESTAMP,
    updated_at TIMESTAMP
)
```

### Usage Examples

```sql
-- Store "My favorite food is pizza"
INSERT INTO people (user_id, name, relationship, is_self, facts)
VALUES ('jason', 'Jason', 'self', 1, '{"favorite_food": "pizza"}')

-- Store "Sarah likes sushi"
INSERT INTO people (user_id, name, relationship, facts)
VALUES ('jason', 'Sarah', 'friend', '{"favorite_food": "sushi"}')

-- Query "What's my favorite food?"
SELECT facts->>'favorite_food' FROM people 
WHERE user_id='jason' AND is_self=1

-- Query "What does Sarah like?"
SELECT facts->>'favorite_food' FROM people 
WHERE user_id='jason' AND name='Sarah'

-- Query "Show everyone's favorite foods"
SELECT name, facts->>'favorite_food' FROM people 
WHERE user_id='jason' AND facts->>'favorite_food' IS NOT NULL
```

---

## Migration Strategy

### Option A: Merge Into `people` (RECOMMENDED)

**Steps:**
1. ✅ Audit current usage (this document)
2. Add `is_self` column to `people` table
3. Create "self" entry for each user in `people`
4. Migrate any `user_profiles` data to `people.profile` JSON
5. Update MCP tools to handle `relationship="self"`
6. Update chat router to store personal facts in `people`
7. Deprecate `user_profiles` table (or repurpose for advanced features)

**Pros:**
- ✅ ONE system for all person data
- ✅ Consistent APIs and logic
- ✅ Graph relationships work for everyone
- ✅ Already has flexible JSON storage
- ✅ Actively used (6 rows, 32 code references)

**Cons:**
- Need to update `/api/user/profile` endpoints
- Need to update onboarding flow
- Loses specialized compatibility matching schema (can keep in JSON if needed)

### Option B: Keep Separate (NOT RECOMMENDED)

**Would require:**
- Clear routing: personal facts → `user_profiles`, others → `people`
- Duplicate logic in all tools
- Complex queries for "everyone including me"
- Continued confusion about where to store what

---

## Files That Need Updates

### Core Services
- `/api/user/profile` → Redirect to `/api/people?is_self=true`
- `routers/people.py` → Add is_self filtering
- `routers/onboarding.py` → Create self entry in people
- `profile_analyzer.py` → Read from people.profile JSON

### MCP Tools
- `tools_additions.py` → Add "store_self_fact" tool
- `http_mcp_server.py` → Handle relationship="self"

### Chat Router
- `routers/chat.py` → Route personal facts to people table

### Queries to Update
- 32 places reference `people` table
- 6 places reference `user_profiles` table

---

## Risks & Mitigations

### Risk 1: Data Loss
**Mitigation:** 
- Backup database before migration
- `user_profiles` table has 0 rows currently (no data to lose)
- Test migration on copy first

### Risk 2: Breaking Existing Code
**Mitigation:**
- Keep `user_profiles` table initially (deprecated)
- Add compatibility shim in `/api/user/profile`
- Phased rollout

### Risk 3: Performance
**Mitigation:**
- Add index on `is_self` column
- JSON queries in SQLite are fast for small datasets
- Current `people` table only has 6 rows

---

## Recommendation

✅ **PROCEED WITH OPTION A: UNIFIED PEOPLE TABLE**

**Reasons:**
1. Simpler architecture - ONE place for all person data
2. Already has flexible JSON storage for custom fields
3. Low risk - `user_profiles` has no data yet
4. Consistent with user's insight: "information about others goes into one location"
5. Easier to maintain long-term

**Next Steps:**
1. Get user approval
2. Create migration script
3. Update schema
4. Update MCP tools
5. Update chat router
6. Test with natural language suite
7. Document new architecture

---

## Questions for User

1. ✅ Should we keep `user_profiles` table for advanced compatibility features, or remove entirely?
   - **Recommendation:** Keep table but deprecate, use `people.profile` JSON for most data

2. ✅ Should "self" be named with user's actual name (e.g., "Jason") or generic (e.g., "Me", "You")?
   - **Recommendation:** Use actual name for consistency

3. ✅ Should we migrate immediately or test in parallel first?
   - **Recommendation:** Test in parallel, then full migration

---

## Current Test Results

**Natural Language Suite:** 30/32 (93.8%)

**Failing Tests:**
- "Put eggs on my list" (75% in Shopping-Direct category)
- "I need to see the dentist next week" (75% in Calendar-Natural)

**Working Categories:**
- ✅ Shopping Natural: 10/10 (100%)
- ✅ Shopping Conversational: 4/4 (100%) - NOW WORKING with new patterns!
- ✅ Memory Store: 3/3 (100%) - "My favorite food is pizza" etc.
- ✅ Calendar Direct: 4/4 (100%)
- ✅ Multi-System: 3/3 (100%)

**Key Insight:** Memory storage patterns are triggering actions successfully (100%), but unclear where data is actually stored.

---

## Status: AWAITING USER DECISION

Should we proceed with unified `people` table migration?






