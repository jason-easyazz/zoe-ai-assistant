"""
Context Window Optimization
Smart selection and compression of context for better LLM performance
"""
import logging
from typing import Dict, List, Tuple
from datetime import datetime, timedelta
import re

logger = logging.getLogger(__name__)


class ContextSelector:
    """Select most relevant context pieces for the prompt"""
    
    def __init__(self):
        self.token_budget = {
            "system_prompt": 0.40,  # 40% of context window
            "memories": 0.30,        # 30% for retrieved memories
            "user_context": 0.20,    # 20% for calendar/journal/etc
            "examples": 0.10         # 10% for few-shot examples
        }
    
    def score_context_piece(self, piece: Dict, query: str, recency_weight: float = 0.3) -> float:
        """
        Score a context piece by relevance
        
        Args:
            piece: Context item (event, memory, journal entry, etc.)
            query: User's query
            recency_weight: How much to weight recency (0-1)
        
        Returns:
            Relevance score (0-1)
        """
        score = 0.0
        
        # Relevance score (keyword matching)
        content = str(piece.get('content', '') or piece.get('title', '') or piece.get('name', '')).lower()
        query_lower = query.lower()
        query_words = set(query_lower.split())
        content_words = set(content.split())
        
        # Jaccard similarity
        if query_words and content_words:
            intersection = query_words.intersection(content_words)
            union = query_words.union(content_words)
            jaccard = len(intersection) / len(union) if union else 0
            score += jaccard * (1 - recency_weight)
        
        # Recency score
        created_at = piece.get('created_at') or piece.get('timestamp') or piece.get('start_date')
        if created_at:
            try:
                if isinstance(created_at, str):
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                else:
                    dt = created_at
                
                days_ago = (datetime.now() - dt).days
                recency_score = max(0, 1 - (days_ago / 30))  # Decay over 30 days
                score += recency_score * recency_weight
            except:
                pass
        
        # Importance boost (if specified)
        importance = piece.get('importance', 5)
        score *= (importance / 10)  # Scale by importance
        
        return min(1.0, score)
    
    def select_best_context(
        self,
        query: str,
        calendar_events: List[Dict],
        journal_entries: List[Dict],
        people: List[Dict],
        projects: List[Dict],
        memories: List[Dict],
        max_items_per_category: int = 5
    ) -> Dict:
        """
        Select most relevant context from all sources
        
        Returns:
            Dict with selected context pieces
        """
        
        selected = {
            "calendar_events": [],
            "journal_entries": [],
            "people": [],
            "projects": [],
            "memories": []
        }
        
        # Score and select calendar events
        scored_events = [
            (event, self.score_context_piece(event, query, recency_weight=0.5))
            for event in calendar_events
        ]
        scored_events.sort(key=lambda x: x[1], reverse=True)
        selected["calendar_events"] = [e[0] for e in scored_events[:max_items_per_category]]
        
        # Score and select journal entries
        scored_journal = [
            (entry, self.score_context_piece(entry, query, recency_weight=0.4))
            for entry in journal_entries
        ]
        scored_journal.sort(key=lambda x: x[1], reverse=True)
        selected["journal_entries"] = [e[0] for e in scored_journal[:max_items_per_category]]
        
        # Score and select people
        scored_people = [
            (person, self.score_context_piece(person, query, recency_weight=0.2))
            for person in people
        ]
        scored_people.sort(key=lambda x: x[1], reverse=True)
        selected["people"] = [p[0] for p in scored_people[:max_items_per_category]]
        
        # Score and select projects
        scored_projects = [
            (project, self.score_context_piece(project, query, recency_weight=0.3))
            for project in projects
        ]
        scored_projects.sort(key=lambda x: x[1], reverse=True)
        selected["projects"] = [p[0] for p in scored_projects[:max_items_per_category]]
        
        # Score and select memories
        scored_memories = [
            (memory, self.score_context_piece(memory, query, recency_weight=0.1))
            for memory in memories
        ]
        scored_memories.sort(key=lambda x: x[1], reverse=True)
        selected["memories"] = [m[0] for m in scored_memories[:max_items_per_category]]
        
        logger.info(f"📊 Context selection: {sum(len(v) for v in selected.values())} items selected")
        
        return selected


class ContextCompressor:
    """Compress long context into concise summaries"""
    
    @staticmethod
    def compress_calendar(events: List[Dict]) -> str:
        """Compress calendar events into summary"""
        if not events:
            return "No events scheduled"
        
        if len(events) == 1:
            event = events[0]
            return f"1 event: {event.get('title')} at {event.get('start_time', 'TBD')}"
        
        # Group by category
        by_category = {}
        for event in events:
            category = event.get('category', 'general')
            by_category.setdefault(category, []).append(event)
        
        summary_parts = [f"{len(events)} events total"]
        
        for category, category_events in list(by_category.items())[:3]:
            summary_parts.append(f"{len(category_events)} {category}")
        
        return ", ".join(summary_parts)
    
    @staticmethod
    def compress_journal(entries: List[Dict]) -> str:
        """Compress journal entries"""
        if not entries:
            return "No journal entries"
        
        if len(entries) == 1:
            entry = entries[0]
            return f"Journal: {entry.get('title', 'Entry')} (Mood: {entry.get('mood', 'neutral')})"
        
        # Most recent mood
        recent_mood = entries[0].get('mood', 'neutral')
        return f"{len(entries)} journal entries, recent mood: {recent_mood}"
    
    @staticmethod
    def compress_people(people: List[Dict]) -> str:
        """Compress people list"""
        if not people:
            return "No people mentioned"
        
        if len(people) <= 3:
            names = [p.get('name') for p in people]
            return f"People: {', '.join(names)}"
        
        return f"{len(people)} people in context"
    
    @staticmethod
    def compress_memories(memories: List[Dict], max_length: int = 200) -> str:
        """Compress memory search results"""
        if not memories:
            return "No relevant memories"
        
        # Take top 3 most relevant
        top_memories = memories[:3]
        
        compressed = []
        for memory in top_memories:
            content = memory.get('content', memory.get('fact', ''))
            # Truncate long memories
            if len(content) > 100:
                content = content[:100] + "..."
            compressed.append(f"• {content}")
        
        result = "\n".join(compressed)
        
        if len(memories) > 3:
            result += f"\n(+{len(memories) - 3} more memories)"
        
        return result


class DynamicContextBudgeter:
    """Dynamically allocate context window based on query complexity"""
    
    @staticmethod
    def analyze_query_complexity(query: str) -> str:
        """Determine if query is simple, moderate, or complex"""
        
        query_lower = query.lower()
        
        # Simple queries (short, direct)
        if len(query.split()) <= 5 and any(word in query_lower for word in ['hi', 'hello', 'thanks', 'yes', 'no']):
            return "simple"
        
        # Complex queries (multi-part, planning, analysis)
        complexity_indicators = ['plan', 'analyze', 'compare', 'help me', 'what if', 'how can i']
        if any(indicator in query_lower for indicator in complexity_indicators):
            return "complex"
        
        # Moderate (default)
        return "moderate"
    
    @staticmethod
    def get_budget_allocation(complexity: str) -> Dict[str, float]:
        """Get token allocation based on complexity"""
        
        budgets = {
            "simple": {
                "system_prompt": 0.50,  # More prompt for simple queries
                "memories": 0.20,
                "user_context": 0.20,
                "examples": 0.10
            },
            "moderate": {
                "system_prompt": 0.40,
                "memories": 0.30,
                "user_context": 0.20,
                "examples": 0.10
            },
            "complex": {
                "system_prompt": 0.30,  # Less prompt, more context
                "memories": 0.35,
                "user_context": 0.25,
                "examples": 0.10
            }
        }
        
        return budgets.get(complexity, budgets["moderate"])


# Global instances
context_selector = ContextSelector()
context_compressor = ContextCompressor()
context_budgeter = DynamicContextBudgeter()


# ================================================================================
# Fresh Project Context (Phase 2: gitingest alternative)
# ================================================================================

def get_fresh_project_context() -> str:
    """
    Load fresh project digest for AI context awareness.
    Generated nightly by fresh_context.sh cron job.
    """
    import os
    
    digest_path = "/app/data/project_digest_trimmed.txt"
    
    if not os.path.exists(digest_path):
        return ""
    
    try:
        with open(digest_path, 'r') as f:
            content = f.read()
        
        # Return first 5000 chars for context
        return content[:5000]
    except Exception as e:
        logger.error(f"Error loading project digest: {e}")
        return ""


def should_include_project_context(query: str) -> bool:
    """
    Determine if query needs project context.
    Only include for developer-related queries to avoid token bloat.
    """
    developer_keywords = [
        "project", "structure", "file", "code", "router", "service",
        "database", "table", "schema", "endpoint", "api", "implement",
        "where is", "how does", "what does", "show me", "list",
        "architecture", "system", "container", "docker"
    ]
    
    query_lower = query.lower()
    return any(keyword in query_lower for keyword in developer_keywords)












