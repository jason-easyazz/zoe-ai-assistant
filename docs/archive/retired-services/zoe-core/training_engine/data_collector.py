"""
Training Data Collector
Logs interactions, feedback, and corrections for overnight training
"""
import sqlite3
import json
import uuid
from datetime import datetime, date
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class TrainingDataCollector:
    """Collects training data from user interactions"""
    
    def __init__(self, db_path: str = "/app/data/training.db"):
        self.db_path = db_path
        self.daily_buffer = []
        self.min_examples = 20  # Minimum to trigger training
        
        # Initialize database
        self._init_database()
        
        logger.info("✅ Training data collector initialized")
    
    def _init_database(self):
        """Initialize training database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Main training examples table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS training_examples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interaction_id TEXT UNIQUE NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                user_input TEXT NOT NULL,
                zoe_output TEXT NOT NULL,
                corrected_output TEXT,
                context_json TEXT,
                
                -- Feedback tracking
                feedback_type TEXT,
                feedback_timestamp DATETIME,
                
                -- Quality metrics
                quality_score REAL,
                warmth_score REAL,
                intelligence_score REAL,
                tool_usage_score REAL,
                
                -- Training metadata
                weight REAL DEFAULT 1.0,
                user_id TEXT,
                used_in_training BOOLEAN DEFAULT FALSE,
                training_date DATE,
                
                -- Context tracking
                routing_type TEXT,
                model_used TEXT,
                memories_used TEXT,
                tool_calls_attempted TEXT
            )
        """)
        
        # Response patterns table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS response_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT,
                pattern_description TEXT,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                avg_quality_score REAL,
                example_ids TEXT,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Tool call performance tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tool_call_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                common_errors TEXT,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Training runs history
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS training_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                start_time DATETIME,
                end_time DATETIME,
                examples_count INTEGER,
                adapter_path TEXT,
                validation_score REAL,
                deployed BOOLEAN DEFAULT FALSE,
                notes TEXT
            )
        """)
        
        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_examples_timestamp ON training_examples(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_examples_user ON training_examples(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_examples_feedback ON training_examples(feedback_type)")
        
        conn.commit()
        conn.close()
        
        logger.info("✅ Training database initialized")
    
    async def log_interaction(self, data: Dict) -> str:
        """Log interaction for potential training"""
        
        interaction_id = str(uuid.uuid4())
        
        training_example = {
            "interaction_id": interaction_id,
            "timestamp": datetime.now().isoformat(),
            "user_input": data["message"],
            "zoe_output": data["response"],
            "context": data.get("context", {}),
            "routing_type": data.get("routing_type"),
            "model_used": data.get("model_used"),
            "user_id": data["user_id"],
            "weight": 1.0
        }
        
        # Add to in-memory buffer
        self.daily_buffer.append(training_example)
        
        # Save to database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO training_examples 
                (interaction_id, user_input, zoe_output, context_json, 
                 routing_type, model_used, user_id, weight)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                interaction_id,
                training_example["user_input"],
                training_example["zoe_output"],
                json.dumps(training_example["context"]),
                training_example["routing_type"],
                training_example["model_used"],
                training_example["user_id"],
                training_example["weight"]
            ))
            
            conn.commit()
            logger.debug(f"📝 Logged interaction {interaction_id}")
        except Exception as e:
            logger.error(f"Failed to log interaction: {e}")
        finally:
            conn.close()
        
        return interaction_id
    
    async def record_correction(self, interaction_id: str, corrected_response: str):
        """High-priority training data - user corrected Zoe"""
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                UPDATE training_examples 
                SET corrected_output = ?,
                    feedback_type = 'correction',
                    feedback_timestamp = datetime('now'),
                    weight = 3.0
                WHERE interaction_id = ?
            """, (corrected_response, interaction_id))
            
            conn.commit()
            logger.info(f"✅ Recorded correction for {interaction_id}")
        except Exception as e:
            logger.error(f"Failed to record correction: {e}")
        finally:
            conn.close()
    
    async def record_positive_feedback(self, interaction_id: str):
        """User liked this response - reinforce it"""
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                UPDATE training_examples 
                SET feedback_type = 'positive',
                    feedback_timestamp = datetime('now'),
                    weight = 1.5
                WHERE interaction_id = ?
            """, (interaction_id,))
            
            conn.commit()
            logger.info(f"👍 Recorded positive feedback for {interaction_id}")
        except Exception as e:
            logger.error(f"Failed to record positive feedback: {e}")
        finally:
            conn.close()
    
    async def record_negative_feedback(self, interaction_id: str):
        """User disliked this response - learn to avoid"""
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                UPDATE training_examples 
                SET feedback_type = 'negative',
                    feedback_timestamp = datetime('now'),
                    weight = 0.5
                WHERE interaction_id = ?
            """, (interaction_id,))
            
            conn.commit()
            logger.info(f"👎 Recorded negative feedback for {interaction_id}")
        except Exception as e:
            logger.error(f"Failed to record negative feedback: {e}")
        finally:
            conn.close()
    
    async def update_interaction_quality(self, interaction_id: str, quality_scores: Dict):
        """Store quality analysis with interaction"""
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                UPDATE training_examples 
                SET quality_score = ?,
                    warmth_score = ?,
                    intelligence_score = ?,
                    tool_usage_score = ?
                WHERE interaction_id = ?
            """, (
                quality_scores.get("quality", 0),
                quality_scores.get("warmth", 0),
                quality_scores.get("intelligence", 0),
                quality_scores.get("tool_usage", 0),
                interaction_id
            ))
            
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to update quality scores: {e}")
        finally:
            conn.close()
    
    async def log_tool_call_failure(self, data: Dict):
        """Log tool calling failures for training improvement"""
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            tool_name = data["tool_name"]
            
            # Update tool call performance
            cursor.execute("""
                INSERT INTO tool_call_performance (tool_name, failure_count, common_errors, last_updated)
                VALUES (?, 1, ?, datetime('now'))
                ON CONFLICT(tool_name) DO UPDATE SET
                    failure_count = failure_count + 1,
                    common_errors = json_insert(common_errors, '$[#]', ?),
                    last_updated = datetime('now')
            """, (tool_name, json.dumps([data["error"]]), data["error"]))
            
            conn.commit()
            logger.warning(f"⚠️ Tool call failure logged: {tool_name}")
        except Exception as e:
            logger.error(f"Failed to log tool call failure: {e}")
        finally:
            conn.close()
    
    async def log_action_pattern(self, user_id: str, tool_name: str, params: Dict, success: bool = True):
        """Log successful action patterns for learning"""
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Update tool call performance
            if success:
                cursor.execute("""
                    INSERT INTO tool_call_performance (tool_name, success_count, last_updated)
                    VALUES (?, 1, datetime('now'))
                    ON CONFLICT(tool_name) DO UPDATE SET
                        success_count = success_count + 1,
                        last_updated = datetime('now')
                """, (tool_name,))
            else:
                cursor.execute("""
                    INSERT INTO tool_call_performance (tool_name, failure_count, last_updated)
                    VALUES (?, 1, datetime('now'))
                    ON CONFLICT(tool_name) DO UPDATE SET
                        failure_count = failure_count + 1,
                        last_updated = datetime('now')
                """, (tool_name,))
            
            # Store action pattern for future reference
            pattern_key = f"{tool_name}:{json.dumps(params, sort_keys=True)}"
            cursor.execute("""
                INSERT INTO response_patterns (pattern_type, pattern_description, success_count, last_updated)
                VALUES (?, ?, 1, datetime('now'))
                ON CONFLICT(pattern_type, pattern_description) DO UPDATE SET
                    success_count = success_count + 1,
                    last_updated = datetime('now')
            """, (f"action_pattern_{user_id}", pattern_key))
            
            conn.commit()
            conn.close()
            
            # Also log to action_logs in zoe.db for suggestion engine
            try:
                action_conn = sqlite3.connect("/app/data/zoe.db")
                action_cursor = action_conn.cursor()
                action_cursor.execute("""
                    INSERT INTO action_logs (user_id, tool_name, tool_params, success, context)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, tool_name, json.dumps(params), success, json.dumps({})))
                action_conn.commit()
                action_conn.close()
            except Exception as e:
                logger.warning(f"Failed to log to action_logs: {e}")
            
            logger.debug(f"📚 Logged action pattern: {tool_name} (success={success})")
        except Exception as e:
            logger.error(f"Failed to log action pattern: {e}")
        finally:
            if conn:
                conn.close()
    
    def get_todays_training_data(self) -> List[Dict]:
        """Get all training examples from today"""
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT user_input, zoe_output, corrected_output, weight, context_json
                FROM training_examples
                WHERE date(timestamp) = date('now')
                AND weight > 0.5
                ORDER BY weight DESC
            """)
            
            examples = []
            for row in cursor.fetchall():
                # Use corrected output if available
                output = row[2] if row[2] else row[1]
                
                examples.append({
                    "input": row[0],
                    "output": output,
                    "weight": row[3],
                    "context": json.loads(row[4]) if row[4] else {}
                })
            
            logger.info(f"📊 Retrieved {len(examples)} training examples for today")
            return examples
            
        except Exception as e:
            logger.error(f"Failed to get today's training data: {e}")
            return []
        finally:
            conn.close()
    
    async def get_stats(self, user_id: str) -> Dict:
        """Get training statistics"""
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Today's count
            cursor.execute("""
                SELECT COUNT(*) FROM training_examples
                WHERE date(timestamp) = date('now') AND user_id = ?
            """, (user_id,))
            today_count = cursor.fetchone()[0]
            
            # This week's corrections
            cursor.execute("""
                SELECT COUNT(*) FROM training_examples
                WHERE feedback_type = 'correction'
                AND date(timestamp) >= date('now', '-7 days')
                AND user_id = ?
            """, (user_id,))
            corrections = cursor.fetchone()[0]
            
            # Latest training run
            cursor.execute("""
                SELECT date, validation_score, deployed
                FROM training_runs
                ORDER BY date DESC
                LIMIT 1
            """)
            latest_run = cursor.fetchone()
            
            return {
                "today_count": today_count,
                "corrections": corrections,
                "next_training": "Tonight at 2am" if today_count >= self.min_examples else f"Need {self.min_examples - today_count} more examples",
                "adapter_score": latest_run[1] if latest_run else 0,
                "adapter_deployed": latest_run[2] if latest_run else False
            }
            
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {}
        finally:
            conn.close()


# Global instance
training_collector = TrainingDataCollector()












