#!/usr/bin/env python3
"""
Intelligent Model Manager for Zoe
Continuously monitors performance and adapts model selection based on quality metrics
"""

import asyncio
import json
import time
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
import statistics
import sqlite3
import os

logger = logging.getLogger(__name__)

@dataclass
class PerformanceMetrics:
    """Real-time performance metrics for a model"""
    model_name: str
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    avg_response_time: float = 0.0
    avg_quality_score: float = 0.0
    avg_warmth_score: float = 0.0
    avg_intelligence_score: float = 0.0
    avg_tool_usage_score: float = 0.0
    last_updated: datetime = None
    recent_response_times: List[float] = None
    recent_quality_scores: List[float] = None
    
    def __post_init__(self):
        if self.recent_response_times is None:
            self.recent_response_times = []
        if self.recent_quality_scores is None:
            self.recent_quality_scores = []
        if self.last_updated is None:
            self.last_updated = datetime.now()

@dataclass
class ModelRanking:
    """Dynamic model ranking based on performance"""
    model_name: str
    overall_score: float
    reliability_score: float
    speed_score: float
    quality_score: float
    last_ranked: datetime
    rank_position: int = 0

class IntelligentModelManager:
    """Self-adapting model management system"""
    
    def __init__(self, db_path: str = "/app/data/zoe.db"):
        self.db_path = db_path
        self.metrics: Dict[str, PerformanceMetrics] = {}
        self.rankings: List[ModelRanking] = []
        self.adaptation_threshold = 0.1  # 10% performance change triggers adaptation
        self.min_calls_for_ranking = 5   # Minimum calls before ranking
        self.quality_weights = {
            'response_time': 0.25,
            'quality_score': 0.30,
            'warmth_score': 0.20,
            'intelligence_score': 0.15,
            'tool_usage_score': 0.10
        }
        
        # Initialize database
        self._init_database()
        
        # Load existing metrics
        self._load_metrics()
        
        # Start background adaptation
        asyncio.create_task(self._background_adaptation())
    
    def _init_database(self):
        """Initialize performance tracking database"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS model_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_name TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    response_time REAL,
                    quality_score REAL,
                    warmth_score REAL,
                    intelligence_score REAL,
                    tool_usage_score REAL,
                    success BOOLEAN,
                    query_type TEXT,
                    user_id TEXT
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS model_rankings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    model_name TEXT NOT NULL,
                    overall_score REAL,
                    reliability_score REAL,
                    speed_score REAL,
                    quality_score REAL,
                    rank_position INTEGER
                )
            ''')
    
    def _load_metrics(self):
        """Load existing performance metrics from database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT model_name, 
                       COUNT(*) as total_calls,
                       SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful_calls,
                       AVG(response_time) as avg_response_time,
                       AVG(quality_score) as avg_quality_score,
                       AVG(warmth_score) as avg_warmth_score,
                       AVG(intelligence_score) as avg_intelligence_score,
                       AVG(tool_usage_score) as avg_tool_usage_score
                FROM model_performance 
                WHERE timestamp > datetime('now', '-7 days')
                GROUP BY model_name
            ''')
            
            for row in cursor.fetchall():
                model_name = row[0]
                self.metrics[model_name] = PerformanceMetrics(
                    model_name=model_name,
                    total_calls=row[1],
                    successful_calls=row[2],
                    failed_calls=row[1] - row[2],
                    avg_response_time=row[3] or 0.0,
                    avg_quality_score=row[4] or 0.0,
                    avg_warmth_score=row[5] or 0.0,
                    avg_intelligence_score=row[6] or 0.0,
                    avg_tool_usage_score=row[7] or 0.0,
                    last_updated=datetime.now()
                )
    
    async def record_performance(self, 
                               model_name: str, 
                               response_time: float, 
                               success: bool,
                               quality_scores: Dict[str, float] = None,
                               query_type: str = "conversation",
                               user_id: str = "default"):
        """Record performance metrics for a model"""
        
        # Update in-memory metrics
        if model_name not in self.metrics:
            self.metrics[model_name] = PerformanceMetrics(model_name=model_name)
        
        metrics = self.metrics[model_name]
        metrics.total_calls += 1
        metrics.last_updated = datetime.now()
        
        if success:
            metrics.successful_calls += 1
        else:
            metrics.failed_calls += 1
        
        # Update response time
        metrics.recent_response_times.append(response_time)
        if len(metrics.recent_response_times) > 100:  # Keep last 100
            metrics.recent_response_times = metrics.recent_response_times[-100:]
        
        # Update quality scores
        if quality_scores:
            for score_type, score in quality_scores.items():
                if hasattr(metrics, f'avg_{score_type}_score'):
                    current_avg = getattr(metrics, f'avg_{score_type}_score')
                    total_calls = metrics.total_calls
                    new_avg = ((current_avg * (total_calls - 1)) + score) / total_calls
                    setattr(metrics, f'avg_{score_type}_score', new_avg)
                    
                    # Update recent scores
                    recent_scores = getattr(metrics, f'recent_{score_type}_scores', [])
                    recent_scores.append(score)
                    if len(recent_scores) > 50:  # Keep last 50
                        recent_scores = recent_scores[-50:]
                    setattr(metrics, f'recent_{score_type}_scores', recent_scores)
        
        # Update average response time
        if metrics.recent_response_times:
            metrics.avg_response_time = statistics.mean(metrics.recent_response_times)
        
        # Store in database
        await self._store_performance_record(
            model_name, response_time, success, quality_scores, query_type, user_id
        )
        
        # Check if adaptation is needed
        await self._check_adaptation_needed(model_name)
    
    async def _store_performance_record(self, model_name: str, response_time: float, 
                                      success: bool, quality_scores: Dict[str, float],
                                      query_type: str, user_id: str):
        """Store performance record in database"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT INTO model_performance 
                (model_name, response_time, quality_score, warmth_score, 
                 intelligence_score, tool_usage_score, success, query_type, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                model_name, response_time,
                quality_scores.get('quality', 0.0) if quality_scores else 0.0,
                quality_scores.get('warmth', 0.0) if quality_scores else 0.0,
                quality_scores.get('intelligence', 0.0) if quality_scores else 0.0,
                quality_scores.get('tool_usage', 0.0) if quality_scores else 0.0,
                success, query_type, user_id
            ))
    
    async def _check_adaptation_needed(self, model_name: str):
        """Check if model rankings need to be updated"""
        if model_name not in self.metrics:
            return
        
        metrics = self.metrics[model_name]
        
        # Only adapt if we have enough data
        if metrics.total_calls < self.min_calls_for_ranking:
            return
        
        # Check if performance has changed significantly
        if self.rankings:
            current_ranking = next((r for r in self.rankings if r.model_name == model_name), None)
            if current_ranking:
                new_score = self._calculate_overall_score(metrics)
                score_change = abs(new_score - current_ranking.overall_score)
                
                if score_change < self.adaptation_threshold:
                    return  # No significant change
        
        # Trigger adaptation
        await self._adapt_model_rankings()
    
    def _calculate_overall_score(self, metrics: PerformanceMetrics) -> float:
        """Calculate overall performance score for a model"""
        if metrics.total_calls == 0:
            return 0.0
        
        # Reliability score (0-1)
        reliability = metrics.successful_calls / metrics.total_calls
        
        # Speed score (0-1, inverted - faster is better)
        speed = max(0, 1 - (metrics.avg_response_time / 60.0))  # 60s = 0 score
        
        # Quality score (0-1)
        quality = (metrics.avg_quality_score + metrics.avg_warmth_score + 
                  metrics.avg_intelligence_score + metrics.avg_tool_usage_score) / 40.0  # Normalize to 0-1
        quality = min(1.0, quality)
        
        # Weighted overall score
        overall_score = (
            reliability * 0.4 +
            speed * self.quality_weights['response_time'] +
            quality * (self.quality_weights['quality_score'] + 
                      self.quality_weights['warmth_score'] + 
                      self.quality_weights['intelligence_score'] + 
                      self.quality_weights['tool_usage_score'])
        )
        
        return overall_score
    
    async def _adapt_model_rankings(self):
        """Adapt model rankings based on current performance"""
        logger.info("🔄 Adapting model rankings based on performance data...")
        
        new_rankings = []
        
        for model_name, metrics in self.metrics.items():
            if metrics.total_calls >= self.min_calls_for_ranking:
                overall_score = self._calculate_overall_score(metrics)
                reliability = metrics.successful_calls / metrics.total_calls if metrics.total_calls > 0 else 0
                speed = max(0, 1 - (metrics.avg_response_time / 60.0))
                quality = (metrics.avg_quality_score + metrics.avg_warmth_score + 
                          metrics.avg_intelligence_score + metrics.avg_tool_usage_score) / 40.0
                quality = min(1.0, quality)
                
                ranking = ModelRanking(
                    model_name=model_name,
                    overall_score=overall_score,
                    reliability_score=reliability,
                    speed_score=speed,
                    quality_score=quality,
                    last_ranked=datetime.now()
                )
                new_rankings.append(ranking)
        
        # Sort by overall score
        new_rankings.sort(key=lambda x: x.overall_score, reverse=True)
        
        # Assign rank positions
        for i, ranking in enumerate(new_rankings):
            ranking.rank_position = i + 1
        
        self.rankings = new_rankings
        
        # Store in database
        await self._store_rankings()
        
        logger.info(f"✅ Model rankings updated. Top 3: {[r.model_name for r in new_rankings[:3]]}")
    
    async def _store_rankings(self):
        """Store current rankings in database"""
        with sqlite3.connect(self.db_path) as conn:
            # Clear old rankings
            conn.execute('DELETE FROM model_rankings WHERE timestamp < datetime("now", "-1 day")')
            
            # Insert new rankings
            for ranking in self.rankings:
                conn.execute('''
                    INSERT INTO model_rankings 
                    (model_name, overall_score, reliability_score, speed_score, quality_score, rank_position)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    ranking.model_name, ranking.overall_score, ranking.reliability_score,
                    ranking.speed_score, ranking.quality_score, ranking.rank_position
                ))
    
    async def _background_adaptation(self):
        """Background task for continuous adaptation"""
        while True:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes
                await self._adapt_model_rankings()
            except Exception as e:
                logger.error(f"Background adaptation error: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error
    
    def get_best_model(self, query_type: str = "conversation", 
                      max_response_time: float = 30.0) -> str:
        """Get the best model for a specific query type"""
        
        if not self.rankings:
            # Fallback to default if no rankings available
            return "gemma3:1b"
        
        # Filter models by query type requirements
        suitable_models = []
        
        for ranking in self.rankings:
            model_name = ranking.model_name
            if model_name in self.metrics:
                metrics = self.metrics[model_name]
                
                # Check response time requirement
                if metrics.avg_response_time <= max_response_time:
                    # Check minimum reliability
                    reliability = metrics.successful_calls / metrics.total_calls if metrics.total_calls > 0 else 0
                    if reliability >= 0.5:  # At least 50% success rate
                        suitable_models.append(ranking)
        
        if not suitable_models:
            # Fallback to most reliable model
            return max(self.rankings, key=lambda x: x.reliability_score).model_name
        
        # Return best suitable model
        return suitable_models[0].model_name
    
    def get_model_recommendations(self, query_type: str = "conversation") -> List[Dict]:
        """Get model recommendations for a query type"""
        recommendations = []
        
        for ranking in self.rankings[:5]:  # Top 5 models
            model_name = ranking.model_name
            if model_name in self.metrics:
                metrics = self.metrics[model_name]
                recommendations.append({
                    "model_name": model_name,
                    "overall_score": ranking.overall_score,
                    "reliability": metrics.successful_calls / metrics.total_calls if metrics.total_calls > 0 else 0,
                    "avg_response_time": metrics.avg_response_time,
                    "quality_score": metrics.avg_quality_score,
                    "warmth_score": metrics.avg_warmth_score,
                    "total_calls": metrics.total_calls,
                    "rank": ranking.rank_position
                })
        
        return recommendations
    
    def get_performance_summary(self) -> Dict:
        """Get overall performance summary"""
        if not self.rankings:
            return {"status": "No data available"}
        
        total_calls = sum(m.total_calls for m in self.metrics.values())
        total_successful = sum(m.successful_calls for m in self.metrics.values())
        
        return {
            "total_calls": total_calls,
            "total_successful": total_successful,
            "overall_success_rate": total_successful / total_calls if total_calls > 0 else 0,
            "top_model": self.rankings[0].model_name if self.rankings else "None",
            "top_score": self.rankings[0].overall_score if self.rankings else 0,
            "models_tracked": len(self.metrics),
            "last_adaptation": self.rankings[0].last_ranked.isoformat() if self.rankings else None
        }

# Global instance
intelligent_manager = IntelligentModelManager()

