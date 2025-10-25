-- Training Database Schema
-- Used for ML training data collection and model fine-tuning

-- Main training examples table
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
    helpfulness_score REAL,
    accuracy_score REAL,
    
    -- Training metadata
    model_version TEXT,
    routing_type TEXT,
    model_used TEXT,
    training_weight REAL DEFAULT 1.0,
    weight REAL DEFAULT 1.0,
    is_processed BOOLEAN DEFAULT 0,
    processed_at DATETIME,
    
    -- User context
    user_id TEXT DEFAULT 'default',
    session_id TEXT,
    
    -- Additional metadata
    metadata_json TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Training runs log
CREATE TABLE IF NOT EXISTS training_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    model_version TEXT,
    examples_count INTEGER,
    training_duration_seconds INTEGER,
    success BOOLEAN,
    error_message TEXT,
    metrics_json TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- User feedback tracking
CREATE TABLE IF NOT EXISTS user_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    interaction_id TEXT NOT NULL,
    user_id TEXT DEFAULT 'default',
    feedback_type TEXT NOT NULL, -- 'positive', 'negative', 'correction', 'rating'
    rating INTEGER, -- 1-5 scale
    feedback_text TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Training statistics
CREATE TABLE IF NOT EXISTS training_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE DEFAULT CURRENT_DATE,
    total_examples INTEGER DEFAULT 0,
    positive_feedback INTEGER DEFAULT 0,
    negative_feedback INTEGER DEFAULT 0,
    corrections INTEGER DEFAULT 0,
    avg_quality_score REAL,
    avg_warmth_score REAL,
    avg_helpfulness_score REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_training_examples_timestamp ON training_examples(timestamp);
CREATE INDEX IF NOT EXISTS idx_training_examples_user_id ON training_examples(user_id);
CREATE INDEX IF NOT EXISTS idx_training_examples_feedback ON training_examples(feedback_type);
CREATE INDEX IF NOT EXISTS idx_training_examples_processed ON training_examples(is_processed);
CREATE INDEX IF NOT EXISTS idx_user_feedback_interaction ON user_feedback(interaction_id);
CREATE INDEX IF NOT EXISTS idx_user_feedback_user_id ON user_feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_training_stats_date ON training_stats(date);

-- Triggers for automatic updates
CREATE TRIGGER IF NOT EXISTS update_training_stats
    AFTER INSERT ON training_examples
    BEGIN
        INSERT OR REPLACE INTO training_stats (date, total_examples, updated_at)
        VALUES (DATE('now'), 
                (SELECT COUNT(*) FROM training_examples WHERE DATE(timestamp) = DATE('now')),
                CURRENT_TIMESTAMP);
    END;

CREATE TRIGGER IF NOT EXISTS update_feedback_stats
    AFTER INSERT ON user_feedback
    BEGIN
        UPDATE training_stats 
        SET positive_feedback = (SELECT COUNT(*) FROM user_feedback 
                                WHERE DATE(timestamp) = DATE('now') AND feedback_type = 'positive'),
            negative_feedback = (SELECT COUNT(*) FROM user_feedback 
                               WHERE DATE(timestamp) = DATE('now') AND feedback_type = 'negative'),
            corrections = (SELECT COUNT(*) FROM user_feedback 
                          WHERE DATE(timestamp) = DATE('now') AND feedback_type = 'correction'),
            updated_at = CURRENT_TIMESTAMP
        WHERE date = DATE('now');
    END;