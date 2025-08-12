import re

# Read main.py
with open('main.py', 'r') as f:
    content = f.read()

# Add events API endpoints before the end of the file
events_api = '''

# Events API Endpoints for Calendar Integration
@app.get("/api/events")
async def get_events():
    """Get all events from database for calendar"""
    try:
        import aiosqlite
        async with aiosqlite.connect('/app/data/zoe.db') as db:
            cursor = await db.execute("""
                SELECT id, title, start_date, start_time, source, created_at
                FROM events 
                ORDER BY start_date ASC, start_time ASC
            """)
            
            events = []
            async for row in cursor:
                events.append({
                    "id": row[0],
                    "title": row[1],
                    "date": row[2],  # Already in YYYY-MM-DD format
                    "time": row[3] or "",
                    "source": row[4],
                    "created_at": row[5]
                })
            
            return {"events": events, "count": len(events)}
            
    except Exception as e:
        print(f"Error fetching events: {e}")
        return {"events": [], "count": 0}

@app.get("/api/events/today")
async def get_today_events():
    """Get today's events for dashboard"""
    try:
        import aiosqlite
        from datetime import date
        today = date.today()
        
        async with aiosqlite.connect('/app/data/zoe.db') as db:
            cursor = await db.execute("""
                SELECT id, title, start_time
                FROM events 
                WHERE start_date = ?
                ORDER BY start_time ASC
            """, (today,))
            
            events = []
            async for row in cursor:
                events.append({
                    "id": row[0],
                    "title": row[1],
                    "time": row[2] or "All day"
                })
            
            return {"events": events, "date": today.isoformat()}
            
    except Exception as e:
        print(f"Error fetching today's events: {e}")
        return {"events": [], "date": date.today().isoformat()}
'''

# Add before any existing endpoint or at the end
if '@app.get("/api/shopping")' in content:
    content = content.replace('@app.get("/api/shopping")', events_api + '\n@app.get("/api/shopping")')
else:
    content += events_api

with open('main.py', 'w') as f:
    f.write(content)

print("✅ Added events API endpoints for calendar integration")
