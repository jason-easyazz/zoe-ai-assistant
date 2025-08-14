# Enhanced endpoints for people profiles and projects
import aiosqlite
import logging

logger = logging.getLogger(__name__)

async def get_people_data(user_id: str = "default", db_path: str = "/app/data/zoe.db"):
    """Get all people in user's network"""
    try:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("""
                SELECT p.id, p.name, p.relationship, p.avatar_emoji, p.mention_count,
                       p.last_mentioned, COUNT(pa.id) as attribute_count
                FROM people p
                LEFT JOIN person_attributes pa ON p.id = pa.person_id
                WHERE p.user_id = ?
                GROUP BY p.id
                ORDER BY p.mention_count DESC, p.last_mentioned DESC
            """, (user_id,))
            
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
        logger.error(f"Get people error: {e}")
        return []

async def get_projects_data(user_id: str = "default", db_path: str = "/app/data/zoe.db"):
    """Get all user projects"""
    try:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("""
                SELECT id, name, description, category, status, icon_emoji, 
                       item_count, last_activity, created_at
                FROM projects WHERE user_id = ?
                ORDER BY last_activity DESC
            """, (user_id,))
            
            projects = []
            rows = await cursor.fetchall()
            for row in rows:
                projects.append({
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "category": row[3],
                    "status": row[4],
                    "icon_emoji": row[5] or "📁",
                    "item_count": row[6] or 0,
                    "last_activity": row[7],
                    "created_at": row[8]
                })
            
            return projects
            
    except Exception as e:
        logger.error(f"Get projects error: {e}")
        return []

async def search_memories_data(query: str = "", user_id: str = "default", db_path: str = "/app/data/zoe.db"):
    """Search through stored memories"""
    try:
        async with aiosqlite.connect(db_path) as db:
            if query:
                cursor = await db.execute("""
                    SELECT m.id, m.content, m.memory_type, m.created_at,
                           p.name as person_name, pr.name as project_name
                    FROM memories m
                    LEFT JOIN people p ON m.person_id = p.id
                    LEFT JOIN projects pr ON m.project_id = pr.id
                    WHERE m.content LIKE ? AND m.user_id = ?
                    ORDER BY m.created_at DESC LIMIT 20
                """, (f"%{query}%", user_id))
            else:
                cursor = await db.execute("""
                    SELECT m.id, m.content, m.memory_type, m.created_at,
                           p.name as person_name, pr.name as project_name
                    FROM memories m
                    LEFT JOIN people p ON m.person_id = p.id
                    LEFT JOIN projects pr ON m.project_id = pr.id
                    WHERE m.user_id = ?
                    ORDER BY m.created_at DESC LIMIT 20
                """, (user_id,))
            
            memories = []
            rows = await cursor.fetchall()
            for row in rows:
                memories.append({
                    "id": row[0],
                    "content": row[1],
                    "type": row[2],
                    "created_at": row[3],
                    "person": row[4],
                    "project": row[5]
                })
            
            return memories
            
    except Exception as e:
        logger.error(f"Search memories error: {e}")
        return []
