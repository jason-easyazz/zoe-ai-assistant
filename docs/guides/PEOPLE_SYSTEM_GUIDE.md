# Zoe People System - Complete Guide

**Version**: 1.0  
**Status**: ✅ **FULLY OPERATIONAL**  
**Inspired by**: [Monica CRM](https://github.com/monicahq/monica)

---

## 🎯 Overview

The People System is Zoe's Personal CRM (Customer/Contact Relationship Management) - a comprehensive system for managing relationships, tracking interactions, and remembering important details about the people in your life.

### What Can You Do?

- **Manage Contacts**: Add people with names, relationships, birthdays, contact info
- **Track Interactions**: Log conversations, activities, last contact dates
- **Remember Details**: Store notes, preferences, important dates
- **Gift Planning**: Track gift ideas and occasions
- **Relationship Mapping**: Visualize your social network
- **Natural Language**: Use chat to manage everything naturally

---

## 🚀 Quick Start

### Using the Chat Interface

Zoe understands natural language for people management:

```
You: Add Sarah as a friend
Zoe: ✅ Added Sarah to your people as friend!

You: Remember that John loves coffee
Zoe: ✅ Added note about John!

You: Gift idea for Mom: flowers
Zoe: ✅ Added gift idea 'flowers' for Mom!

You: I talked to Alice about the project today
Zoe: ✅ Logged conversation with Alice!

You: Who is Sarah?
Zoe: **Sarah** (friend)
     Last contact: 3 days ago
```

### Using the People Page

1. **Navigate**: Go to `people.html` in the UI
2. **Visual Map**: See your relationships displayed in a beautiful interactive graph
3. **Add Person**: Click the `+` button
4. **View Details**: Click on any person to see their full profile
5. **Edit**: Click the edit button to update information

---

## 💡 Features

### 1. **Person Management**

#### Add People
- **Via Chat**: "Add [Name] as [relationship]"
- **Via UI**: Click `+` button, fill in details
- **Details**: Name, relationship type, birthday, phone, email, address, notes

#### Categories
- **Inner Circle**: Closest relationships (family, best friends)
- **Circle**: Regular friends and close contacts  
- **Acquaintances**: People you know but don't see often
- **Professional**: Work colleagues, business contacts
- **Archive**: People you no longer interact with but want to remember

### 2. **Notes & Memory**

#### Add Notes
```
"Remember that Sarah loves hiking"
"Note about John: prefers tea over coffee"
```

#### Automatic Tracking
- Date-stamped notes
- Conversation logs
- Interaction history

### 3. **Gift Planning**

Track gift ideas for special occasions:
```
"Gift idea for Mom: new scarf"
"Remember to get John a book on Python"
```

Features:
- Track gift ideas
- Link to occasions (birthday, anniversary, etc.)
- Status tracking (idea → purchased → given)

### 4. **Important Dates**

- **Birthdays**: Never forget a birthday again
- **Anniversaries**: Relationship milestones
- **Custom Dates**: Any important date you want to remember

```
"Sarah's birthday is January 15"
"Add anniversary for Alice on June 10"
```

### 5. **Conversation Tracking**

Log what you talked about:
```
"I talked to John about the new project"
"Spoke with Sarah about travel plans"
```

Benefits:
- Remember context from last conversation
- Track discussion topics
- Build deeper relationships

### 6. **Relationship Intelligence**

The system tracks:
- **Last Contact**: How long since you last connected
- **Frequency**: How often you usually talk
- **Connection Strength**: Based on interaction patterns
- **Attention Needed**: Visual alerts when you haven't connected in a while

---

## 🎨 Frontend Interface

### Visual Relationship Map

The people page features an interactive canvas that displays:

1. **You (Center Node)**: Your position in the network
2. **People (Orbiting Nodes)**: Positioned by relationship strength
3. **Connection Lines**: Visual links showing relationships
4. **Color Coding**: 
   - 🌸 Pink: Inner Circle
   - 🌊 Teal: Circle
   - ⚪ Gray: Acquaintances
   - 🔵 Blue: Professional
   - ⚫ Dark Gray: Archive

### Person Detail Panel

Click any person to see:
- **Connection Health**: Visual bar showing relationship strength
- **Contact Info**: Phone, email, address
- **Notes**: All your notes about them
- **Quick Actions**: Schedule, create todo, add note, set goal, etc.
- **Timeline**: History of interactions

---

## 🔧 API Endpoints

### Core Operations

#### Get All People
```http
GET /api/people
Authorization: Bearer {session_token}
```

Response:
```json
{
  "people": [
    {
      "id": 1,
      "name": "Sarah",
      "relationship": "friend",
      "birthday": "1990-01-15",
      "phone": "555-1234",
      "email": "sarah@example.com",
      "notes": "Loves hiking and coffee",
      "metadata": {...}
    }
  ],
  "count": 1
}
```

#### Add Person
```http
POST /api/people
Content-Type: application/json
Authorization: Bearer {session_token}

{
  "name": "John Doe",
  "relationship": "friend",
  "birthday": "1985-06-20",
  "phone": "555-5678",
  "email": "john@example.com",
  "notes": "Met at conference"
}
```

#### Get Person Details
```http
GET /api/people/{person_id}
Authorization: Bearer {session_token}
```

#### Update Person
```http
PUT /api/people/{person_id}
Content-Type: application/json
Authorization: Bearer {session_token}

{
  "name": "John Doe",
  "relationship": "colleague",
  "notes": "Updated notes..."
}
```

#### Delete Person
```http
DELETE /api/people/{person_id}
Authorization: Bearer {session_token}
```

#### Search People
```http
GET /api/people/search?query=Sarah
Authorization: Bearer {session_token}
```

#### Get Enhanced Person Data
```http
GET /api/people/{person_id}/analysis
Authorization: Bearer {session_token}
```

Returns:
- Basic person info
- Timeline events
- Activities
- Conversations
- Gift ideas
- Important dates
- Relationships

### Natural Language Actions

#### Execute Action via Chat
```http
POST /api/people/actions/execute
Content-Type: application/json
Authorization: Bearer {session_token}

{
  "action_type": "add_person|add_note|add_gift|log_conversation|search",
  "data": {...}
}
```

**Action Types**:
- `add_person`: Add new person
- `add_note`: Add note to existing person
- `add_gift`: Add gift idea
- `log_conversation`: Track conversation
- `search`: Search for people

---

## 🤖 Person Expert

The Person Expert is Zoe's specialized intelligence for managing people.

### Capabilities

1. **add_person**: Add new people to your network
2. **update_person**: Modify person details
3. **search_people**: Find people by name, relationship, etc.
4. **get_person_details**: Retrieve full person profile
5. **add_note**: Add notes about someone
6. **add_interaction**: Log interactions
7. **add_gift_idea**: Track gift ideas
8. **add_important_date**: Remember special dates
9. **track_conversation**: Log conversations
10. **set_relationship**: Define relationship types
11. **get_relationship_insights**: Analyze your network

### How It Works

1. **Query Analysis**: Understands natural language
2. **Name Extraction**: Identifies people mentioned
3. **Action Planning**: Determines what you want to do
4. **Execution**: Calls the appropriate API
5. **Response**: Confirms action in natural language

### Integration with Chat

The Person Expert integrates with:
- **Cross-Agent Collaboration**: Works with other experts for complex tasks
- **Enhanced MEM Agent**: Provides intelligent action execution
- **Chat Router**: Seamlessly handles person queries

Example multi-expert task:
```
"Add Sarah as a friend and schedule coffee next Tuesday"
```
- Person Expert: Adds Sarah
- Calendar Expert: Schedules event
- Orchestrator: Coordinates both actions

---

## 📊 Database Schema

### Tables

#### `people`
- `id`: Primary key
- `user_id`: User isolation
- `name`: Person's name
- `relationship`: Relationship type
- `birthday`: Birthday date
- `phone`: Phone number
- `email`: Email address
- `address`: Physical address
- `notes`: Text notes
- `avatar_url`: Profile picture URL
- `tags`: JSON array of tags
- `metadata`: JSON metadata
- `created_at`: Creation timestamp
- `updated_at`: Last update timestamp

#### `person_timeline`
- Events and milestones

#### `person_activities`
- Shared activities

#### `person_conversations`
- Conversation logs

#### `person_gifts`
- Gift ideas and tracking

#### `person_important_dates`
- Important dates beyond birthday

#### `person_shared_goals`
- Goals you have with people

#### `relationships`
- Connections between people

---

## 🔐 Security & Privacy

### User Isolation
- All queries filtered by `user_id`
- No cross-user data leakage
- Session-based authentication required

### Data Protection
- Encrypted connections (HTTPS)
- Database isolation
- No data sharing with third parties

---

## 🎯 Use Cases

### Personal
- Remember friends' birthdays
- Track family interactions
- Plan gifts for loved ones
- Maintain relationships during busy times

### Professional
- Manage business contacts
- Remember client preferences
- Track networking connections
- Follow up on conversations

### Social
- Plan group activities
- Track who introduced you to whom
- Remember conversation topics
- Strengthen weak ties

---

## 🚦 Status & Health

### Current Status
✅ **Backend**: Fully operational  
✅ **Person Expert**: Integrated  
✅ **Cross-Agent**: Configured  
✅ **API Endpoints**: Complete  
✅ **Frontend**: Functional  
✅ **Chat Integration**: Active  

### Testing
```bash
# Run integration tests
cd /home/zoe/assistant
python3 tests/integration/test_people_system.py

# Expected output: ✅ ALL TESTS PASSED
```

---

## 📝 Examples

### Complete Workflow

```
# Day 1: Add contacts
You: Add Sarah as a friend
Zoe: ✅ Added Sarah!

You: Sarah's birthday is January 15
Zoe: ✅ Saved Sarah's birthday!

You: Remember that Sarah loves hiking
Zoe: ✅ Added note about Sarah!

# Day 2: Log interaction
You: I talked to Sarah about planning a hiking trip
Zoe: ✅ Logged conversation with Sarah!

# Day 3: Plan gifts
You: Gift idea for Sarah: new hiking boots
Zoe: ✅ Added gift idea for Sarah!

# Any day: Search
You: Who is Sarah?
Zoe: **Sarah** (friend)
     🎂 Birthday: January 15
     Last contact: 1 day ago
     
     Notes: Loves hiking
     
     Recent conversation: Planning a hiking trip
     Gift ideas: New hiking boots
```

---

## 🔄 Future Enhancements

### Planned Features
- [ ] Automatic birthday reminders
- [ ] Relationship strength visualization
- [ ] Photo attachments
- [ ] Social media integration
- [ ] Family tree visualization
- [ ] Bulk import from contacts
- [ ] Export to vCard/CSV
- [ ] Relationship analytics
- [ ] AI-powered insights
- [ ] Conversation topic analysis

---

## 🤝 Contributing

To enhance the people system:

1. **Backend**: Edit `/services/zoe-core/routers/people.py`
2. **Expert**: Edit `/services/zoe-core/services/person_expert.py`
3. **Frontend**: Edit `/services/zoe-ui/dist/people.html`
4. **Tests**: Add tests to `/tests/integration/test_people_system.py`

---

## 📚 References

- **Inspiration**: [Monica CRM](https://github.com/monicahq/monica) - Open source personal CRM
- **API Docs**: Available at `/docs` when Zoe is running
- **Architecture**: See `/docs/architecture/PERSON_EXPERT_RECOMMENDATION.md`

---

## ✅ Summary

The Zoe People System is a **fully functional Personal CRM** that allows you to:

✅ Manage contacts through chat or UI  
✅ Track interactions and conversations  
✅ Remember important details and dates  
✅ Plan gifts and special occasions  
✅ Visualize your relationship network  
✅ Use natural language for everything  

**Get started**: Just talk to Zoe!

"Add [name] as [relationship]" - and Zoe handles the rest! 🎉

---

**Last Updated**: November 1, 2025  
**Version**: 1.0  
**Status**: Production Ready


