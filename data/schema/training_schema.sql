CREATE TABLE training_examples (
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
            );
CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE response_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT,
                pattern_description TEXT,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                avg_quality_score REAL,
                example_ids TEXT,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE tool_call_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                common_errors TEXT,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE training_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                start_time DATETIME,
                end_time DATETIME,
                examples_count INTEGER,
                adapter_path TEXT,
                validation_score REAL,
                deployed BOOLEAN DEFAULT FALSE,
                notes TEXT
            );
CREATE INDEX idx_examples_timestamp ON training_examples(timestamp);
CREATE INDEX idx_examples_user ON training_examples(user_id);
CREATE INDEX idx_examples_feedback ON training_examples(feedback_type);
