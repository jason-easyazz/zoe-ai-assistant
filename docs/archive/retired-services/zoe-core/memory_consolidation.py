"""
Memory Consolidation System
Creates daily/weekly/monthly summaries for long-term understanding
"""
import sqlite3
import json
from datetime import datetime, date, timedelta
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


class MemoryConsolidator:
    """Consolidate memories into higher-level summaries"""
    
    def __init__(self, db_path: str = "/app/data/zoe.db"):
        self.db_path = db_path
        self._init_consolidation_tables()
    
    def _init_consolidation_tables(self):
        """Create tables for consolidated memories"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                summary_type TEXT NOT NULL,
                summary_date DATE NOT NULL,
                summary_text TEXT NOT NULL,
                insights TEXT,
                entities_mentioned TEXT,
                importance INTEGER DEFAULT 7,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, summary_type, summary_date)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pattern_insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                pattern_type TEXT,
                pattern_description TEXT,
                frequency INTEGER DEFAULT 1,
                last_seen DATE,
                examples TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
        logger.info("✅ Consolidation tables initialized")
    
    async def create_daily_summary(self, user_id: str, target_date: date = None) -> str:
        """
        Create summary of the day's interactions and activities
        Run at 2am each night
        """
        if target_date is None:
            target_date = date.today() - timedelta(days=1)  # Yesterday
        
        logger.info(f"📝 Creating daily summary for {user_id} on {target_date}")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Gather all activity from that day
            summary_data = {
                "calendar_events": [],
                "chat_interactions": [],
                "journal_entries": [],
                "list_changes": [],
                "new_memories": []
            }
            
            # Get calendar events
            try:
                cursor.execute("""
                    SELECT title, start_time, category
                    FROM events
                    WHERE user_id = ? AND date(start_date) = ?
                    ORDER BY start_time
                """, (user_id, target_date.isoformat()))
                summary_data["calendar_events"] = [
                    {"title": r[0], "time": r[1], "category": r[2]}
                    for r in cursor.fetchall()
                ]
            except sqlite3.OperationalError:
                pass
            
            # Get journal entries
            try:
                cursor.execute("""
                    SELECT title, mood, content
                    FROM journal_entries
                    WHERE user_id = ? AND date(created_at) = ?
                """, (user_id, target_date.isoformat()))
                summary_data["journal_entries"] = [
                    {"title": r[0], "mood": r[1], "content": r[2][:200]}
                    for r in cursor.fetchall()
                ]
            except sqlite3.OperationalError:
                pass
            
            # Get training interactions (chat history)
            try:
                cursor.execute("""
                    SELECT user_input, zoe_output, feedback_type
                    FROM training_examples
                    WHERE user_id = ? AND date(timestamp) = ?
                    ORDER BY timestamp
                    LIMIT 20
                """, (user_id, target_date.isoformat()))
                summary_data["chat_interactions"] = [
                    {"user": r[0], "zoe": r[1][:100], "feedback": r[2]}
                    for r in cursor.fetchall()
                ]
            except sqlite3.OperationalError:
                pass
            
            # Create natural language summary
            summary_text = self._generate_summary_text(summary_data, target_date)
            
            # Extract insights
            insights = self._extract_insights(summary_data)
            
            # Store summary
            cursor.execute("""
                INSERT OR REPLACE INTO memory_summaries
                (user_id, summary_type, summary_date, summary_text, insights, importance)
                VALUES (?, 'daily', ?, ?, ?, 7)
            """, (user_id, target_date.isoformat(), summary_text, json.dumps(insights)))
            
            conn.commit()
            
            logger.info(f"✅ Daily summary created: {len(summary_text)} chars")
            return summary_text
            
        finally:
            conn.close()
    
    def _generate_summary_text(self, data: Dict, target_date: date) -> str:
        """Generate natural language summary"""
        
        parts = []
        
        # Date header
        parts.append(f"Summary for {target_date.strftime('%A, %B %d, %Y')}:\n")
        
        # Calendar events
        if data["calendar_events"]:
            parts.append(f"\n📅 Events ({len(data['calendar_events'])}):")
            for event in data["calendar_events"][:5]:
                parts.append(f"  • {event['time']} - {event['title']}")
        
        # Journal mood
        if data["journal_entries"]:
            entry = data["journal_entries"][0]
            parts.append(f"\n📖 Journal: {entry['title']} (Mood: {entry['mood']})")
        
        # Chat activity
        if data["chat_interactions"]:
            interactions = len(data["chat_interactions"])
            corrections = sum(1 for i in data["chat_interactions"] if i.get('feedback') == 'correction')
            parts.append(f"\n💬 Chat: {interactions} interactions, {corrections} corrections")
        
        # Key topics discussed
        topics = self._extract_topics(data["chat_interactions"])
        if topics:
            parts.append(f"\n🏷️ Topics: {', '.join(topics[:5])}")
        
        return "\n".join(parts)
    
    def _extract_topics(self, interactions: List[Dict]) -> List[str]:
        """Extract main topics from interactions"""
        # Simple keyword extraction
        topics = set()
        
        keywords = ["shopping", "calendar", "schedule", "garden", "project", "sarah", "arduino"]
        
        for interaction in interactions:
            user_input = interaction.get('user', '').lower()
            for keyword in keywords:
                if keyword in user_input:
                    topics.add(keyword)
        
        return list(topics)
    
    def _extract_insights(self, data: Dict) -> List[str]:
        """Extract patterns and insights"""
        insights = []
        
        # Busy day detection
        if len(data["calendar_events"]) >= 5:
            insights.append("Busy day with many scheduled events")
        
        # Mood tracking
        if data["journal_entries"]:
            mood = data["journal_entries"][0].get("mood")
            if mood:
                insights.append(f"Mood was {mood}")
        
        # Chat patterns
        if len(data["chat_interactions"]) > 20:
            insights.append("High chat activity - very engaged with Zoe")
        
        return insights
    
    async def create_weekly_summary(self, user_id: str) -> str:
        """Create summary of the week's patterns"""
        
        logger.info(f"📊 Creating weekly summary for {user_id}")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Get this week's daily summaries
            week_start = date.today() - timedelta(days=7)
            
            cursor.execute("""
                SELECT summary_date, summary_text, insights
                FROM memory_summaries
                WHERE user_id = ? 
                AND summary_type = 'daily'
                AND summary_date >= ?
                ORDER BY summary_date
            """, (user_id, week_start.isoformat()))
            
            daily_summaries = cursor.fetchall()
            
            if not daily_summaries:
                return "No activity this week"
            
            # Aggregate insights
            all_insights = []
            for _, _, insights_json in daily_summaries:
                if insights_json:
                    all_insights.extend(json.loads(insights_json))
            
            # Create weekly summary
            summary = f"Week of {week_start.strftime('%B %d, %Y')}:\n\n"
            summary += f"📊 {len(daily_summaries)} days of activity\n"
            
            if all_insights:
                summary += f"\n💡 Insights:\n"
                # Count insight frequencies
                insight_counts = {}
                for insight in all_insights:
                    insight_counts[insight] = insight_counts.get(insight, 0) + 1
                
                for insight, count in sorted(insight_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
                    summary += f"  • {insight} ({count}x this week)\n"
            
            # Store weekly summary
            cursor.execute("""
                INSERT OR REPLACE INTO memory_summaries
                (user_id, summary_type, summary_date, summary_text, importance)
                VALUES (?, 'weekly', ?, ?, 8)
            """, (user_id, week_start.isoformat(), summary))
            
            conn.commit()
            
            logger.info(f"✅ Weekly summary created")
            return summary
            
        finally:
            conn.close()
    
    async def get_consolidated_context(self, user_id: str, days_back: int = 7) -> str:
        """Get consolidated memories for context (instead of all raw data)"""
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cutoff_date = (date.today() - timedelta(days=days_back)).isoformat()
            
            cursor.execute("""
                SELECT summary_type, summary_text
                FROM memory_summaries
                WHERE user_id = ? AND summary_date >= ?
                ORDER BY summary_date DESC, 
                         CASE summary_type 
                             WHEN 'daily' THEN 1 
                             WHEN 'weekly' THEN 2 
                         END
                LIMIT 10
            """, (user_id, cutoff_date))
            
            summaries = cursor.fetchall()
            
            if not summaries:
                return ""
            
            context = "# Recent Activity Summary:\n\n"
            for summary_type, summary_text in summaries:
                context += f"{summary_text}\n\n"
            
            return context
            
        finally:
            conn.close()


# Global instance
memory_consolidator = MemoryConsolidator()












