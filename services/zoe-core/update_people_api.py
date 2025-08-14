# Update people API to use database
import re

# Find and replace the people endpoint
with open('main.py', 'r') as f:
    content = f.read()

# Replace the simple people endpoint with database version
new_people_endpoint = '''@app.get("/api/people")
async def get_people_working():
    """Get all people - database version"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute("""
                SELECT p.id, p.name, p.relationship, p.avatar_emoji, p.mention_count,
                       p.last_mentioned, COUNT(pa.id) as attribute_count
                FROM people p
                LEFT JOIN person_attributes pa ON p.id = pa.person_id
                WHERE p.user_id = 'default'
                GROUP BY p.id
                ORDER BY p.mention_count DESC, p.last_mentioned DESC
            """)
            
            people = []
            rows = await cursor.fetchall()
            for row in rows:
                people.append({
                    "id": row[0],
                    "name": row[1],
                    "relationship": row[2],
                    "avatar_emoji": row[3] or "👤",
                    "mention_count": row[4] or 0,
                    "last_mentioned": row[5],
                    "attribute_count": row[6] or 0
                })
            
            return people
            
    except Exception as e:
        print(f"People endpoint error: {e}")
        return []'''

# Replace the endpoint
pattern = r'@app\.get\("/api/people"\)\s*async def get_people_working\(\):[^@]*?return \[\]'
content = re.sub(pattern, new_people_endpoint, content, flags=re.DOTALL)

with open('main.py', 'w') as f:
    f.write(content)

print("✅ People API updated with database functionality")
