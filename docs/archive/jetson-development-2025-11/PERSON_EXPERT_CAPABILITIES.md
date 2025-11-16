# Person Expert Capabilities Audit

## Current Person-Related MCP Tools (3 total)

### 1. `create_person`
**Purpose**: Create a new person in Zoe's memory system

**Parameters**:
- `name` (required): Person's name
- `relationship` (optional): Relationship to user (e.g., 'friend', 'family', 'colleague')
- `notes` (optional): Additional notes about the person

**What it CAN do**:
- Create new people with basic info
- Set initial relationship type
- Add notes about the person

**What it CANNOT do**:
- Update existing person details
- Add detailed attributes (birthday, contact info, preferences, etc.)
- Manage complex relationship types
- Track interaction history

---

### 2. `get_people`
**Purpose**: Get all people from the people service

**Parameters**:
- `user_id` (optional): User ID to filter people

**What it CAN do**:
- List all people for a user
- Basic person info retrieval

**What it CANNOT do**:
- Filter by relationship type
- Search by attributes
- Sort by last interaction

---

### 3. `get_person_analysis`
**Purpose**: Get comprehensive analysis of a person including relationships and timeline

**Parameters**:
- `person_id` (required): Person ID to analyze
- `user_id` (optional): User ID

**What it CAN do**:
- Comprehensive person analysis
- Relationship mapping
- Timeline of interactions

**What it CANNOT do**:
- Update analysis
- Modify relationships

---

## ❌ MISSING Person Management Tools

The Person Expert is currently **LIMITED**. Missing critical operations:

### 1. **Update Person** (Not Available)
- Update name, relationship, notes
- Add/modify contact info
- Update preferences
- Change attributes

### 2. **Delete Person** (Not Available)
- Remove person from system
- Archive relationships
- Clean up associated data

### 3. **Advanced Relationship Management** (Not Available)
- Add multiple relationships
- Update relationship strength
- Track relationship history
- Bi-directional relationships

### 4. **Person Attributes** (Limited)
`create_person` only accepts:
- name
- relationship  
- notes

**Missing attributes**:
- Birthday
- Contact info (phone, email, address)
- Preferences (likes, dislikes)
- Interaction frequency
- Important dates
- Custom fields

### 5. **Search & Filter** (Not Available)
- Search people by name
- Filter by relationship type
- Find people by attributes
- Sort by last interaction

---

## 🎯 ANSWER TO USER'S QUESTION

**Q: "Can the person expert deal with all the details in the person section?"**

**A: NO, currently the Person Expert is LIMITED.**

**What it CAN handle**:
✅ Create new people with basic info (name, relationship, notes)
✅ Get list of all people
✅ Get comprehensive analysis of a specific person

**What it CANNOT handle**:
❌ Update existing person details
❌ Delete people
❌ Manage detailed attributes (birthday, contact info, preferences)
❌ Advanced relationship management
❌ Search/filter people by criteria

---

## 📋 RECOMMENDATIONS

### Option 1: Expose More MCP Tools
Add these tools to the MCP server:
```
• update_person - Update person details
• delete_person - Remove a person
• search_people - Search by name/attributes
• add_person_attribute - Add custom attributes
• update_relationship - Modify relationship details
• get_person_by_name - Find person by name
```

### Option 2: Enhance create_person
Extend `create_person` to accept more fields:
```json
{
  "name": "John Doe",
  "relationship": "friend",
  "notes": "Met at conference",
  "birthday": "1990-05-15",
  "email": "john@example.com",
  "phone": "+1234567890",
  "preferences": {
    "likes": ["Arduino", "coffee"],
    "dislikes": ["mornings"]
  },
  "custom_attributes": {}
}
```

### Option 3: Use search_memories as Workaround
Currently, `search_memories` with `memory_type: "people"` might retrieve person details, but this is:
- Indirect (not explicit person management)
- Read-only (can't update)
- Limited control

---

## 🚀 IMMEDIATE ACTION

The **Person Expert is functional but basic**. For full person management, we need to:

1. **Check if people service has more APIs** that aren't exposed via MCP
2. **Add missing MCP tools** (update, delete, search)
3. **Enhance create_person** to accept more detailed attributes
4. **Document current limitations** for the LLM

For now, the Person Expert can:
- ✅ Create people with basic info
- ✅ List all people
- ✅ Get detailed analysis of a person

But it CANNOT:
- ❌ Update/delete people
- ❌ Manage complex attributes
- ❌ Advanced search/filtering

