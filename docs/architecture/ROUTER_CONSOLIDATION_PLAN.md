# 🔄 Router Consolidation Plan

**Date**: October 9, 2025  
**Status**: Implementation Ready  
**Priority**: High  
**Impact**: Reduces 64 routers to ~25 (62% reduction)

---

## 🎯 Objective

Consolidate overlapping and duplicate router files to improve maintainability while preserving all functionality.

---

## 📊 Current State Analysis

### Total Routers: 64 files (27,247 lines of code)

**Problematic Overlaps**:

| Domain | Current Files | Should Be | Reduction |
|--------|--------------|-----------|-----------|
| Calendar | 3 files | 1 file | 67% |
| Lists | 2 files | 1 file | 50% |
| Memories | 4 files | 1 file | 75% |
| Tasks | 3 files | 1 file | 67% |
| Chat | 2 files | 1 file | 50% |

**Current Router Files**:
```
routers/
├── calendar.py              ⚠️
├── enhanced_calendar.py     ⚠️ Duplicate
├── birthday_calendar.py     ⚠️ Duplicate
├── lists.py                 ⚠️
├── lists_redesigned.py      ⚠️ Duplicate
├── memories.py              ⚠️
├── birthday_memories.py     ⚠️ Duplicate
├── test_memories.py         ⚠️ Duplicate
├── public_memories.py       ⚠️ Duplicate
└── ... 55 more files
```

---

## 🏗️ Proposed Structure

### New Organization (25 routers in categorized folders)

```
services/zoe-core/routers/
├── __init__.py
│
├── core/
│   ├── __init__.py
│   ├── auth.py              # Authentication & authorization
│   ├── users.py             # User management
│   └── sessions.py          # Session handling
│
├── features/
│   ├── __init__.py
│   ├── calendar.py          # ✅ Consolidates: calendar, enhanced_calendar, birthday_calendar
│   ├── lists.py             # ✅ Consolidates: lists, lists_redesigned
│   ├── memories.py          # ✅ Consolidates: memories, birthday_memories, test_memories, public_memories
│   ├── journal.py           # Journal entries
│   ├── reminders.py         # Reminders & notifications
│   ├── tasks.py             # ✅ Consolidates: tasks, developer_tasks, dynamic_tasks
│   ├── family.py            # Family groups & sharing
│   └── weather.py           # Weather integration
│
├── intelligence/
│   ├── __init__.py
│   ├── chat.py              # ✅ Consolidates: chat, enhanced_chat_router
│   ├── agent_planner.py     # Agent planning & goals
│   ├── orchestrator.py      # Multi-agent coordination
│   ├── self_awareness.py    # Self-awareness & reflection
│   └── proactive_insights.py # Proactive assistance
│
├── integrations/
│   ├── __init__.py
│   ├── homeassistant.py     # Home Assistant integration
│   ├── n8n.py               # N8N workflows
│   ├── mcp.py               # Model Context Protocol
│   └── vector_search.py     # Vector search capabilities
│
└── system/
    ├── __init__.py
    ├── health.py            # Health checks & monitoring
    ├── settings.py          # System settings
    ├── touch_panel.py       # Touch panel configuration
    └── onboarding.py        # User onboarding
```

---

## 📋 Consolidation Details

### 1. Calendar Consolidation

**Merge**: `calendar.py` + `enhanced_calendar.py` + `birthday_calendar.py`

**Structure**:
```python
# routers/features/calendar.py
from fastapi import APIRouter, Depends
from typing import Optional

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

# ============================================================================
# BASIC CALENDAR ENDPOINTS
# ============================================================================

@router.get("/events")
async def get_events(user_id: str = Depends(validate_session)):
    """Get all calendar events for user"""
    pass

@router.post("/events")
async def create_event(event: EventCreate, user_id: str = Depends(validate_session)):
    """Create new calendar event"""
    pass

# ============================================================================
# ENHANCED CALENDAR FEATURES (from enhanced_calendar.py)
# ============================================================================

@router.get("/events/recurring")
async def get_recurring_events(user_id: str = Depends(validate_session)):
    """Get recurring event patterns"""
    pass

@router.post("/events/cluster")
async def cluster_events(user_id: str = Depends(validate_session)):
    """Smart event clustering"""
    pass

# ============================================================================
# BIRTHDAY CALENDAR (from birthday_calendar.py)
# ============================================================================

@router.get("/birthdays")
async def get_upcoming_birthdays(
    days_ahead: int = 30,
    user_id: str = Depends(validate_session)
):
    """Get upcoming birthdays from people"""
    pass

@router.get("/birthdays/today")
async def get_todays_birthdays(user_id: str = Depends(validate_session)):
    """Get birthdays happening today"""
    pass
```

**Benefits**:
- ✅ All calendar functionality in one file
- ✅ Clear section separation with comments
- ✅ Single import in main.py
- ✅ Easier to maintain and extend

### 2. Lists Consolidation

**Merge**: `lists.py` + `lists_redesigned.py`

**Structure**:
```python
# routers/features/lists.py
from fastapi import APIRouter, Depends
from enum import Enum

router = APIRouter(prefix="/api/lists", tags=["lists"])

class ListType(str, Enum):
    SHOPPING = "shopping"
    PERSONAL = "personal"
    WORK = "work"
    PROJECT = "project"

# ============================================================================
# LIST MANAGEMENT
# ============================================================================

@router.get("/")
async def get_lists(
    list_type: Optional[ListType] = None,
    user_id: str = Depends(validate_session)
):
    """Get all lists, optionally filtered by type"""
    pass

@router.post("/")
async def create_list(
    list_data: ListCreate,
    user_id: str = Depends(validate_session)
):
    """Create new list"""
    pass

# ============================================================================
# LIST ITEMS
# ============================================================================

@router.get("/{list_id}/items")
async def get_list_items(
    list_id: int,
    user_id: str = Depends(validate_session)
):
    """Get items in a list"""
    pass

@router.post("/{list_id}/items")
async def add_list_item(
    list_id: int,
    item: ListItemCreate,
    user_id: str = Depends(validate_session)
):
    """Add item to list"""
    pass

# ============================================================================
# REDESIGNED FEATURES (from lists_redesigned.py)
# ============================================================================

@router.post("/{list_id}/items/batch")
async def add_items_batch(
    list_id: int,
    items: List[ListItemCreate],
    user_id: str = Depends(validate_session)
):
    """Add multiple items at once"""
    pass

@router.get("/{list_id}/analytics")
async def get_list_analytics(
    list_id: int,
    user_id: str = Depends(validate_session)
):
    """Get list completion analytics"""
    pass
```

### 3. Memories Consolidation

**Merge**: `memories.py` + `birthday_memories.py` + `test_memories.py` + `public_memories.py`

**Structure**:
```python
# routers/features/memories.py
from fastapi import APIRouter, Depends
from typing import Optional

router = APIRouter(prefix="/api/memories", tags=["memories"])

# ============================================================================
# CORE MEMORY OPERATIONS
# ============================================================================

@router.get("/")
async def search_memories(
    query: Optional[str] = None,
    entity_type: Optional[str] = None,
    user_id: str = Depends(validate_session)
):
    """Search memories with filters"""
    pass

@router.post("/")
async def create_memory(
    memory: MemoryCreate,
    user_id: str = Depends(validate_session)
):
    """Create new memory"""
    pass

# ============================================================================
# BIRTHDAY MEMORIES (from birthday_memories.py)
# ============================================================================

@router.get("/birthdays")
async def get_birthday_memories(user_id: str = Depends(validate_session)):
    """Get memories related to birthdays"""
    pass

# ============================================================================
# PUBLIC MEMORIES (from public_memories.py)
# ============================================================================

@router.get("/public/{entity_id}")
async def get_public_memories(entity_id: int):
    """Get public memories (no auth required)"""
    pass

# ============================================================================
# TEST UTILITIES (from test_memories.py - DEV ONLY)
# ============================================================================

if os.getenv("ENVIRONMENT") == "development":
    @router.post("/test/seed")
    async def seed_test_memories(user_id: str = Depends(validate_session)):
        """Seed database with test memories (DEV ONLY)"""
        pass
```

---

## 🚀 Implementation Strategy

### Phase 1: Preparation (Day 1)

1. **Create Backup**
   ```bash
   cd /home/pi/zoe
   git checkout -b router-consolidation
   cp -r services/zoe-core/routers services/zoe-core/routers.backup
   ```

2. **Create New Structure**
   ```bash
   cd services/zoe-core/routers
   mkdir -p core features intelligence integrations system
   touch core/__init__.py features/__init__.py intelligence/__init__.py integrations/__init__.py system/__init__.py
   ```

3. **Document Current Endpoints**
   ```bash
   # Generate endpoint inventory
   python3 -c "
   import sys
   sys.path.append('/home/pi/zoe/services/zoe-core')
   from main import app
   for route in app.routes:
       print(f'{route.path} - {route.methods}')
   " > docs/architecture/ENDPOINT_INVENTORY.md
   ```

### Phase 2: Consolidate High-Value Targets (Days 2-3)

**Priority Order**:
1. ✅ Calendar routers (3 → 1) - **High impact**
2. ✅ Memory routers (4 → 1) - **High impact**
3. ✅ List routers (2 → 1) - **Medium impact**
4. ✅ Task routers (3 → 1) - **Medium impact**
5. ✅ Chat routers (2 → 1) - **Low impact** (already mostly consolidated)

### Phase 3: Update Imports (Day 4)

**Update**: `services/zoe-core/main.py`

```python
# OLD (64 imports)
from routers import auth, tasks, chat
from routers import calendar, enhanced_calendar, birthday_calendar
from routers import lists, lists_redesigned
from routers import memories, birthday_memories, test_memories, public_memories
# ... 55 more imports

# NEW (25 imports - organized by category)
from routers.core import auth, users, sessions
from routers.features import calendar, lists, memories, journal, reminders, tasks, family, weather
from routers.intelligence import chat, agent_planner, orchestrator, self_awareness, proactive_insights
from routers.integrations import homeassistant, n8n, mcp, vector_search
from routers.system import health, settings, touch_panel, onboarding

# Include routers (NEW organized approach)
# Core
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(sessions.router)

# Features
app.include_router(calendar.router)
app.include_router(lists.router)
app.include_router(memories.router)
app.include_router(journal.router)
app.include_router(reminders.router)
app.include_router(tasks.router)
app.include_router(family.router)
app.include_router(weather.router)

# Intelligence
app.include_router(chat.router)
app.include_router(agent_planner.router)
app.include_router(orchestrator.router)
app.include_router(self_awareness.router)
app.include_router(proactive_insights.router)

# Integrations
app.include_router(homeassistant.router)
app.include_router(n8n.router)
app.include_router(mcp.router)
app.include_router(vector_search.router)

# System
app.include_router(health.router)
app.include_router(settings.router)
app.include_router(touch_panel.router)
app.include_router(onboarding.router)
```

### Phase 4: Testing & Validation (Day 5)

1. **Run Full Test Suite**
   ```bash
   pytest tests/ -v --cov=routers
   ```

2. **Test All Endpoints**
   ```bash
   python3 tests/integration/test_all_endpoints.py
   ```

3. **Manual Testing**
   - Test each consolidated router
   - Verify backward compatibility
   - Check API documentation at `/docs`

### Phase 5: Cleanup & Documentation (Day 6)

1. **Remove Old Routers**
   ```bash
   cd services/zoe-core/routers
   # Move old routers to archive
   mkdir -p ../../docs/archive/routers_pre_consolidation
   mv calendar.py enhanced_calendar.py birthday_calendar.py ../../docs/archive/routers_pre_consolidation/
   mv lists.py lists_redesigned.py ../../docs/archive/routers_pre_consolidation/
   # ... etc
   ```

2. **Update Documentation**
   - Update README.md with new structure
   - Update API documentation
   - Create migration guide for developers

---

## ✅ Benefits

### Maintainability
- ✅ 62% fewer files to navigate
- ✅ Clear categorization (core, features, intelligence, etc.)
- ✅ Single source of truth per domain
- ✅ Easier to find related functionality

### Performance
- ✅ Reduced import complexity
- ✅ Faster application startup
- ✅ Better code locality (related code together)

### Developer Experience
- ✅ Clear where to add new endpoints
- ✅ Easier code reviews
- ✅ Better IDE navigation
- ✅ Reduced cognitive load

---

## ⚠️ Risks & Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Breaking existing API clients | Low | High | Maintain endpoint paths exactly |
| Import errors during migration | Medium | Medium | Thorough testing, backup branch |
| Merge conflicts | Low | Low | Work in dedicated branch |
| Lost functionality | Low | High | Document all endpoints first |

---

## 📊 Success Metrics

**Before**:
- 64 router files
- 27,247 lines of code
- ~12 imports per domain
- Unclear organization

**After**:
- 25 router files (62% reduction)
- Same LOC, better organized
- 1 import per domain
- Clear categorization

**Validation**:
- ✅ All tests passing
- ✅ All endpoints functional
- ✅ API docs updated
- ✅ Zero breaking changes

---

## 📝 Next Steps

1. Review and approve this plan
2. Create backup branch
3. Start Phase 1 (preparation)
4. Implement Phase 2 (consolidation) incrementally
5. Test thoroughly
6. Deploy to production

---

*Document created: October 9, 2025*  
*Ready for implementation approval*

