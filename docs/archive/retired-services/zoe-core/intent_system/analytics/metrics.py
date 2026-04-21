"""
Intent System Metrics Collection
=================================

Tracks performance metrics for intent classification and execution:
- Tier distribution (how many queries use which tier)
- Latency per tier and intent
- Success rates per intent
- Most common intents
- Failed classifications
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class IntentMetric:
    """Single intent execution metric."""
    timestamp: datetime
    user_id: str
    intent_name: str
    tier: int
    confidence: float
    latency_ms: float
    success: bool
    input_text: str
    source: str  # chat, voice, touch, api


class MetricsCollector:
    """
    Collects and analyzes intent system metrics.
    
    Stores metrics in SQLite for analytics and monitoring.
    """
    
    def __init__(self, db_path: str = "/app/data/zoe.db"):
        """
        Initialize metrics collector.
        
        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self._init_tables()
    
    def _init_tables(self):
        """Create metrics tables if they don't exist."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS intent_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    user_id TEXT NOT NULL,
                    intent_name TEXT NOT NULL,
                    tier INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    latency_ms REAL NOT NULL,
                    success BOOLEAN NOT NULL,
                    input_text TEXT,
                    source TEXT DEFAULT 'chat'
                )
            """)
            
            # Index for fast queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_intent_metrics_timestamp 
                ON intent_metrics(timestamp)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_intent_metrics_intent 
                ON intent_metrics(intent_name)
            """)
            
            conn.commit()
            conn.close()
            
            logger.info("Intent metrics tables initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize metrics tables: {e}")
    
    def record_execution(
        self,
        user_id: str,
        intent_name: str,
        tier: int,
        confidence: float,
        latency_ms: float,
        success: bool,
        input_text: str,
        source: str = "chat"
    ):
        """
        Record an intent execution.
        
        Args:
            user_id: User identifier
            intent_name: Name of the executed intent
            tier: Classification tier used (0-3)
            confidence: Classification confidence (0.0-1.0)
            latency_ms: Total execution time in milliseconds
            success: Whether execution succeeded
            input_text: Original user input
            source: Input source (chat, voice, touch, api)
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO intent_metrics 
                (user_id, intent_name, tier, confidence, latency_ms, success, input_text, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, intent_name, tier, confidence, latency_ms, success, input_text, source))
            
            conn.commit()
            conn.close()
            
            logger.debug(f"Recorded metric: {intent_name} (tier {tier}, {latency_ms:.2f}ms)")
            
        except Exception as e:
            logger.error(f"Failed to record metric: {e}")
    
    def get_tier_distribution(self, hours: int = 24) -> Dict[int, int]:
        """
        Get distribution of queries across tiers.
        
        Args:
            hours: Time window in hours
            
        Returns:
            Dict mapping tier number to query count
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            since = datetime.now() - timedelta(hours=hours)
            
            cursor.execute("""
                SELECT tier, COUNT(*) as count
                FROM intent_metrics
                WHERE timestamp >= ?
                GROUP BY tier
                ORDER BY tier
            """, (since,))
            
            results = {tier: count for tier, count in cursor.fetchall()}
            conn.close()
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to get tier distribution: {e}")
            return {}
    
    def get_tier_percentage(self, tier: int, hours: int = 24) -> float:
        """
        Get percentage of queries using a specific tier.
        
        Args:
            tier: Tier number (0-3)
            hours: Time window in hours
            
        Returns:
            Percentage (0-100)
        """
        distribution = self.get_tier_distribution(hours)
        total = sum(distribution.values())
        
        if total == 0:
            return 0.0
        
        tier_count = distribution.get(tier, 0)
        return (tier_count / total) * 100
    
    def get_avg_latency(self, tier: Optional[int] = None, hours: int = 24) -> float:
        """
        Get average latency for a tier or overall.
        
        Args:
            tier: Tier number (0-3) or None for all tiers
            hours: Time window in hours
            
        Returns:
            Average latency in milliseconds
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            since = datetime.now() - timedelta(hours=hours)
            
            if tier is not None:
                cursor.execute("""
                    SELECT AVG(latency_ms)
                    FROM intent_metrics
                    WHERE timestamp >= ? AND tier = ?
                """, (since, tier))
            else:
                cursor.execute("""
                    SELECT AVG(latency_ms)
                    FROM intent_metrics
                    WHERE timestamp >= ?
                """, (since,))
            
            result = cursor.fetchone()[0]
            conn.close()
            
            return result or 0.0
            
        except Exception as e:
            logger.error(f"Failed to get average latency: {e}")
            return 0.0
    
    def get_success_rate(self, hours: int = 24) -> float:
        """
        Get overall success rate.
        
        Args:
            hours: Time window in hours
            
        Returns:
            Success rate percentage (0-100)
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            since = datetime.now() - timedelta(hours=hours)
            
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful
                FROM intent_metrics
                WHERE timestamp >= ?
            """, (since,))
            
            total, successful = cursor.fetchone()
            conn.close()
            
            if total == 0:
                return 0.0
            
            return (successful / total) * 100
            
        except Exception as e:
            logger.error(f"Failed to get success rate: {e}")
            return 0.0
    
    def get_top_intents(self, limit: int = 20, hours: int = 24) -> List[Tuple[str, int]]:
        """
        Get most commonly used intents.
        
        Args:
            limit: Maximum number of intents to return
            hours: Time window in hours
            
        Returns:
            List of (intent_name, count) tuples
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            since = datetime.now() - timedelta(hours=hours)
            
            cursor.execute("""
                SELECT intent_name, COUNT(*) as count
                FROM intent_metrics
                WHERE timestamp >= ?
                GROUP BY intent_name
                ORDER BY count DESC
                LIMIT ?
            """, (since, limit))
            
            results = cursor.fetchall()
            conn.close()
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to get top intents: {e}")
            return []
    
    def get_failed_queries(self, limit: int = 50, hours: int = 24) -> List[Tuple[str, str, str]]:
        """
        Get recent failed queries for debugging.
        
        Args:
            limit: Maximum number of failures to return
            hours: Time window in hours
            
        Returns:
            List of (timestamp, input_text, intent_name) tuples
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            since = datetime.now() - timedelta(hours=hours)
            
            cursor.execute("""
                SELECT timestamp, input_text, intent_name
                FROM intent_metrics
                WHERE timestamp >= ? AND success = 0
                ORDER BY timestamp DESC
                LIMIT ?
            """, (since, limit))
            
            results = cursor.fetchall()
            conn.close()
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to get failed queries: {e}")
            return []
    
    def get_performance_summary(self, hours: int = 24) -> Dict:
        """
        Get comprehensive performance summary.
        
        Args:
            hours: Time window in hours
            
        Returns:
            Dictionary with all key metrics
        """
        tier_dist = self.get_tier_distribution(hours)
        total_queries = sum(tier_dist.values())
        
        return {
            "time_window_hours": hours,
            "total_queries": total_queries,
            "tier_distribution": {
                "tier_0_pct": self.get_tier_percentage(0, hours),
                "tier_1_pct": self.get_tier_percentage(1, hours),
                "tier_2_pct": self.get_tier_percentage(2, hours),
                "tier_3_pct": self.get_tier_percentage(3, hours),
                "tier_0_count": tier_dist.get(0, 0),
                "tier_1_count": tier_dist.get(1, 0),
                "tier_2_count": tier_dist.get(2, 0),
                "tier_3_count": tier_dist.get(3, 0),
            },
            "latency": {
                "overall_avg_ms": self.get_avg_latency(None, hours),
                "tier_0_avg_ms": self.get_avg_latency(0, hours),
                "tier_1_avg_ms": self.get_avg_latency(1, hours),
                "tier_2_avg_ms": self.get_avg_latency(2, hours),
                "tier_3_avg_ms": self.get_avg_latency(3, hours),
            },
            "success_rate_pct": self.get_success_rate(hours),
            "top_intents": self.get_top_intents(10, hours),
        }


# Global singleton
_metrics_collector: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """Get global MetricsCollector singleton."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector

