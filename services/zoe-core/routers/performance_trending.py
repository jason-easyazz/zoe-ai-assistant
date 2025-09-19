from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import sqlite3
import json
import time
import psutil
import asyncio
from datetime import datetime, timedelta
import logging
import threading
from collections import defaultdict, deque
import statistics

router = APIRouter(prefix="/api/performance", tags=["performance-trending"])

logger = logging.getLogger(__name__)

class PerformanceMetric(BaseModel):
    timestamp: str
    metric_name: str
    value: float
    unit: str
    tags: Dict[str, str] = {}

class PerformanceTrend(BaseModel):
    metric_name: str
    current_value: float
    trend_direction: str  # up, down, stable
    trend_percentage: float
    time_range: str
    data_points: List[Dict[str, Any]]

class PerformanceReport(BaseModel):
    report_id: str
    generated_at: str
    time_range: str
    summary: Dict[str, Any]
    trends: List[PerformanceTrend]
    recommendations: List[str]

class PerformanceCollector:
    def __init__(self):
        self.db_path = "/app/data/performance.db"
        self.collection_interval = 30  # seconds
        self.is_collecting = False
        self.collection_thread = None
        self.metrics_buffer = deque(maxlen=1000)
        
        # Initialize database
        self.init_database()
        
        # Start background collection
        self.start_collection()
    
    def init_database(self):
        """Initialize performance database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Create metrics table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS performance_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    value REAL NOT NULL,
                    unit TEXT NOT NULL,
                    tags TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indexes for efficient querying
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_metric_name_timestamp 
                ON performance_metrics(metric_name, timestamp)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp 
                ON performance_metrics(timestamp)
            """)
            
            conn.commit()
            conn.close()
            
            logger.info("✅ Performance database initialized")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize performance database: {e}")
    
    def start_collection(self):
        """Start background performance collection"""
        if not self.is_collecting:
            self.is_collecting = True
            self.collection_thread = threading.Thread(target=self._collect_metrics, daemon=True)
            self.collection_thread.start()
            logger.info("✅ Performance collection started")
    
    def stop_collection(self):
        """Stop background performance collection"""
        self.is_collecting = False
        if self.collection_thread:
            self.collection_thread.join(timeout=5)
        logger.info("✅ Performance collection stopped")
    
    def _collect_metrics(self):
        """Background metric collection loop"""
        while self.is_collecting:
            try:
                # Collect system metrics
                metrics = self._gather_system_metrics()
                
                # Store metrics
                for metric in metrics:
                    self._store_metric(metric)
                    self.metrics_buffer.append(metric)
                
                # Wait for next collection
                time.sleep(self.collection_interval)
                
            except Exception as e:
                logger.error(f"❌ Error in metric collection: {e}")
                time.sleep(self.collection_interval)
    
    def _gather_system_metrics(self) -> List[PerformanceMetric]:
        """Gather current system performance metrics"""
        metrics = []
        timestamp = datetime.now().isoformat()
        
        try:
            # CPU metrics
            cpu_percent = psutil.cpu_percent(interval=1)
            metrics.append(PerformanceMetric(
                timestamp=timestamp,
                metric_name="cpu_usage",
                value=cpu_percent,
                unit="percent",
                tags={"type": "system"}
            ))
            
            # Memory metrics
            memory = psutil.virtual_memory()
            metrics.append(PerformanceMetric(
                timestamp=timestamp,
                metric_name="memory_usage",
                value=memory.percent,
                unit="percent",
                tags={"type": "system"}
            ))
            
            metrics.append(PerformanceMetric(
                timestamp=timestamp,
                metric_name="memory_available",
                value=memory.available / (1024**3),  # GB
                unit="GB",
                tags={"type": "system"}
            ))
            
            # Disk metrics
            disk = psutil.disk_usage('/')
            metrics.append(PerformanceMetric(
                timestamp=timestamp,
                metric_name="disk_usage",
                value=disk.percent,
                unit="percent",
                tags={"type": "system"}
            ))
            
            metrics.append(PerformanceMetric(
                timestamp=timestamp,
                metric_name="disk_free",
                value=disk.free / (1024**3),  # GB
                unit="GB",
                tags={"type": "system"}
            ))
            
            # Network metrics
            network = psutil.net_io_counters()
            metrics.append(PerformanceMetric(
                timestamp=timestamp,
                metric_name="network_bytes_sent",
                value=network.bytes_sent,
                unit="bytes",
                tags={"type": "network"}
            ))
            
            metrics.append(PerformanceMetric(
                timestamp=timestamp,
                metric_name="network_bytes_recv",
                value=network.bytes_recv,
                unit="bytes",
                tags={"type": "network"}
            ))
            
            # Process metrics for Zoe services
            zoe_processes = self._get_zoe_process_metrics()
            metrics.extend(zoe_processes)
            
        except Exception as e:
            logger.error(f"❌ Error gathering system metrics: {e}")
        
        return metrics
    
    def _get_zoe_process_metrics(self) -> List[PerformanceMetric]:
        """Get metrics for Zoe-specific processes"""
        metrics = []
        timestamp = datetime.now().isoformat()
        
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
                try:
                    if 'zoe' in proc.info['name'].lower() or 'python' in proc.info['name'].lower():
                        metrics.append(PerformanceMetric(
                            timestamp=timestamp,
                            metric_name="process_cpu",
                            value=proc.info['cpu_percent'],
                            unit="percent",
                            tags={"process": proc.info['name'], "pid": str(proc.info['pid'])}
                        ))
                        
                        metrics.append(PerformanceMetric(
                            timestamp=timestamp,
                            metric_name="process_memory",
                            value=proc.info['memory_percent'],
                            unit="percent",
                            tags={"process": proc.info['name'], "pid": str(proc.info['pid'])}
                        ))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                    
        except Exception as e:
            logger.error(f"❌ Error getting process metrics: {e}")
        
        return metrics
    
    def _store_metric(self, metric: PerformanceMetric):
        """Store a single metric in the database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO performance_metrics 
                (timestamp, metric_name, value, unit, tags)
                VALUES (?, ?, ?, ?, ?)
            """, (
                metric.timestamp,
                metric.metric_name,
                metric.value,
                metric.unit,
                json.dumps(metric.tags)
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"❌ Error storing metric: {e}")
    
    def get_metrics(self, metric_name: str = None, hours: int = 24) -> List[PerformanceMetric]:
        """Get metrics for a specific time range"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Calculate time range
            start_time = (datetime.now() - timedelta(hours=hours)).isoformat()
            
            if metric_name:
                cursor.execute("""
                    SELECT timestamp, metric_name, value, unit, tags
                    FROM performance_metrics
                    WHERE metric_name = ? AND timestamp >= ?
                    ORDER BY timestamp DESC
                """, (metric_name, start_time))
            else:
                cursor.execute("""
                    SELECT timestamp, metric_name, value, unit, tags
                    FROM performance_metrics
                    WHERE timestamp >= ?
                    ORDER BY timestamp DESC
                """, (start_time,))
            
            metrics = []
            for row in cursor.fetchall():
                metrics.append(PerformanceMetric(
                    timestamp=row['timestamp'],
                    metric_name=row['metric_name'],
                    value=row['value'],
                    unit=row['unit'],
                    tags=json.loads(row['tags']) if row['tags'] else {}
                ))
            
            conn.close()
            return metrics
            
        except Exception as e:
            logger.error(f"❌ Error getting metrics: {e}")
            return []
    
    def calculate_trend(self, metric_name: str, hours: int = 24) -> PerformanceTrend:
        """Calculate trend for a specific metric"""
        try:
            metrics = self.get_metrics(metric_name, hours)
            
            if len(metrics) < 2:
                return PerformanceTrend(
                    metric_name=metric_name,
                    current_value=0.0,
                    trend_direction="stable",
                    trend_percentage=0.0,
                    time_range=f"{hours}h",
                    data_points=[]
                )
            
            # Sort by timestamp (oldest first)
            metrics.sort(key=lambda x: x.timestamp)
            
            # Calculate trend
            values = [m.value for m in metrics]
            current_value = values[-1]
            previous_value = values[0]
            
            if previous_value == 0:
                trend_percentage = 0.0
            else:
                trend_percentage = ((current_value - previous_value) / previous_value) * 100
            
            if trend_percentage > 5:
                trend_direction = "up"
            elif trend_percentage < -5:
                trend_direction = "down"
            else:
                trend_direction = "stable"
            
            # Prepare data points for visualization
            data_points = []
            for metric in metrics[-50:]:  # Last 50 points
                data_points.append({
                    "timestamp": metric.timestamp,
                    "value": metric.value,
                    "unit": metric.unit
                })
            
            return PerformanceTrend(
                metric_name=metric_name,
                current_value=current_value,
                trend_direction=trend_direction,
                trend_percentage=round(trend_percentage, 2),
                time_range=f"{hours}h",
                data_points=data_points
            )
            
        except Exception as e:
            logger.error(f"❌ Error calculating trend: {e}")
            return PerformanceTrend(
                metric_name=metric_name,
                current_value=0.0,
                trend_direction="stable",
                trend_percentage=0.0,
                time_range=f"{hours}h",
                data_points=[]
            )
    
    def generate_report(self, hours: int = 24) -> PerformanceReport:
        """Generate a comprehensive performance report"""
        try:
            # Get all metrics
            metrics = self.get_metrics(hours=hours)
            
            # Group by metric name
            metric_groups = defaultdict(list)
            for metric in metrics:
                metric_groups[metric.metric_name].append(metric)
            
            # Calculate trends for each metric
            trends = []
            for metric_name in metric_groups.keys():
                trend = self.calculate_trend(metric_name, hours)
                trends.append(trend)
            
            # Generate summary
            summary = {
                "total_metrics": len(metrics),
                "unique_metrics": len(metric_groups),
                "time_range": f"{hours} hours",
                "collection_status": "active" if self.is_collecting else "inactive"
            }
            
            # Generate recommendations
            recommendations = self._generate_recommendations(trends)
            
            return PerformanceReport(
                report_id=f"report_{int(time.time())}",
                generated_at=datetime.now().isoformat(),
                time_range=f"{hours}h",
                summary=summary,
                trends=trends,
                recommendations=recommendations
            )
            
        except Exception as e:
            logger.error(f"❌ Error generating report: {e}")
            return PerformanceReport(
                report_id="error",
                generated_at=datetime.now().isoformat(),
                time_range=f"{hours}h",
                summary={},
                trends=[],
                recommendations=["Error generating report"]
            )
    
    def _generate_recommendations(self, trends: List[PerformanceTrend]) -> List[str]:
        """Generate performance recommendations based on trends"""
        recommendations = []
        
        for trend in trends:
            if trend.metric_name == "cpu_usage" and trend.current_value > 80:
                recommendations.append("High CPU usage detected. Consider optimizing processes or adding more resources.")
            
            elif trend.metric_name == "memory_usage" and trend.current_value > 90:
                recommendations.append("High memory usage detected. Consider freeing up memory or adding more RAM.")
            
            elif trend.metric_name == "disk_usage" and trend.current_value > 85:
                recommendations.append("Disk usage is high. Consider cleaning up old files or expanding storage.")
            
            elif trend.trend_direction == "up" and trend.trend_percentage > 20:
                recommendations.append(f"{trend.metric_name} is trending upward significantly. Monitor closely.")
        
        if not recommendations:
            recommendations.append("System performance is within normal ranges.")
        
        return recommendations

# Initialize performance collector
performance_collector = PerformanceCollector()

@router.get("/metrics")
async def get_performance_metrics(
    metric_name: Optional[str] = None,
    hours: int = 24
):
    """Get performance metrics for a specific time range"""
    try:
        metrics = performance_collector.get_metrics(metric_name, hours)
        
        return {
            "metrics": [metric.dict() for metric in metrics],
            "total_count": len(metrics),
            "time_range": f"{hours} hours",
            "metric_name": metric_name
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get metrics: {str(e)}")

@router.get("/trends/{metric_name}")
async def get_metric_trend(
    metric_name: str,
    hours: int = 24
):
    """Get trend analysis for a specific metric"""
    try:
        trend = performance_collector.calculate_trend(metric_name, hours)
        return trend.dict()
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to calculate trend: {str(e)}")

@router.get("/trends")
async def get_all_trends(hours: int = 24):
    """Get trend analysis for all metrics"""
    try:
        metrics = performance_collector.get_metrics(hours=hours)
        metric_names = list(set(m.metric_name for m in metrics))
        
        trends = []
        for metric_name in metric_names:
            trend = performance_collector.calculate_trend(metric_name, hours)
            trends.append(trend.dict())
        
        return {
            "trends": trends,
            "time_range": f"{hours} hours",
            "total_metrics": len(metric_names)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get trends: {str(e)}")

@router.get("/report")
async def get_performance_report(hours: int = 24):
    """Generate a comprehensive performance report"""
    try:
        report = performance_collector.generate_report(hours)
        return report.dict()
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate report: {str(e)}")

@router.get("/status")
async def get_collection_status():
    """Get performance collection status"""
    return {
        "is_collecting": performance_collector.is_collecting,
        "collection_interval": performance_collector.collection_interval,
        "buffer_size": len(performance_collector.metrics_buffer),
        "last_activity": datetime.now().isoformat()
    }

@router.post("/start-collection")
async def start_collection():
    """Start performance data collection"""
    try:
        performance_collector.start_collection()
        return {"message": "Performance collection started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start collection: {str(e)}")

@router.post("/stop-collection")
async def stop_collection():
    """Stop performance data collection"""
    try:
        performance_collector.stop_collection()
        return {"message": "Performance collection stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stop collection: {str(e)}")

@router.get("/dashboard")
async def get_performance_dashboard():
    """Get performance dashboard data"""
    try:
        # Get recent trends for key metrics
        key_metrics = ["cpu_usage", "memory_usage", "disk_usage", "network_bytes_sent"]
        
        dashboard_data = {
            "timestamp": datetime.now().isoformat(),
            "key_metrics": {},
            "alerts": [],
            "summary": {}
        }
        
        for metric in key_metrics:
            trend = performance_collector.calculate_trend(metric, 1)  # Last hour
            dashboard_data["key_metrics"][metric] = {
                "current_value": trend.current_value,
                "trend_direction": trend.trend_direction,
                "trend_percentage": trend.trend_percentage,
                "unit": trend.data_points[0]["unit"] if trend.data_points else "unknown"
            }
            
            # Check for alerts
            if metric == "cpu_usage" and trend.current_value > 80:
                dashboard_data["alerts"].append(f"High CPU usage: {trend.current_value}%")
            elif metric == "memory_usage" and trend.current_value > 90:
                dashboard_data["alerts"].append(f"High memory usage: {trend.current_value}%")
            elif metric == "disk_usage" and trend.current_value > 85:
                dashboard_data["alerts"].append(f"High disk usage: {trend.current_value}%")
        
        # Generate summary
        dashboard_data["summary"] = {
            "total_alerts": len(dashboard_data["alerts"]),
            "system_health": "warning" if dashboard_data["alerts"] else "good",
            "collection_active": performance_collector.is_collecting
        }
        
        return dashboard_data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get dashboard: {str(e)}")

@router.delete("/metrics")
async def clear_old_metrics(days: int = 7):
    """Clear metrics older than specified days"""
    try:
        conn = sqlite3.connect(performance_collector.db_path)
        cursor = conn.cursor()
        
        cutoff_time = (datetime.now() - timedelta(days=days)).isoformat()
        
        cursor.execute("""
            DELETE FROM performance_metrics 
            WHERE timestamp < ?
        """, (cutoff_time,))
        
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        return {
            "message": f"Cleared {deleted_count} metrics older than {days} days",
            "deleted_count": deleted_count
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear metrics: {str(e)}")




