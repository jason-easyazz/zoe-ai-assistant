CREATE TABLE people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                folder_path TEXT,
                profile JSON,
                facts JSON,
                important_dates JSON,
                preferences JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                folder_path TEXT,
                description TEXT,
                status TEXT DEFAULT 'active',
                metadata JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person1_id INTEGER,
                person2_id INTEGER,
                relationship_type TEXT,
                metadata JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (person1_id) REFERENCES people(id),
                FOREIGN KEY (person2_id) REFERENCES people(id)
            );
CREATE TABLE memory_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT, -- 'person', 'project', 'general'
                entity_id INTEGER,
                fact TEXT NOT NULL,
                category TEXT,
                importance INTEGER DEFAULT 5,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            , embedding_vector BLOB, entity_context TEXT, relationship_path TEXT, embedding_hash TEXT);
CREATE TABLE entity_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                entity_name TEXT NOT NULL,
                embedding_vector BLOB NOT NULL,
                context_summary TEXT,
                embedding_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(entity_type, entity_id)
            );
CREATE TABLE relationship_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                relationship_id INTEGER,
                relationship_type TEXT,
                entity1_name TEXT,
                entity2_name TEXT,
                embedding_vector BLOB,
                strength_score REAL DEFAULT 0.5,
                context_summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE search_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_hash TEXT UNIQUE NOT NULL,
                query_text TEXT NOT NULL,
                results_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            );
CREATE INDEX idx_memory_facts_embedding ON memory_facts(embedding_hash);
CREATE INDEX idx_entity_embeddings_type_id ON entity_embeddings(entity_type, entity_id);
CREATE INDEX idx_search_cache_hash ON search_cache(query_hash);
CREATE INDEX idx_search_cache_expires ON search_cache(expires_at);
CREATE TABLE conversation_episodes (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                start_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'active',
                context_type TEXT NOT NULL DEFAULT 'general',
                summary TEXT,
                message_count INTEGER DEFAULT 0,
                timeout_minutes INTEGER DEFAULT 30,
                topics TEXT,
                participants TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE memory_temporal_metadata (
                fact_id INTEGER PRIMARY KEY,
                episode_id TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                decay_factor REAL DEFAULT 1.0,
                access_count INTEGER DEFAULT 0,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                temporal_context TEXT,
                FOREIGN KEY (fact_id) REFERENCES memory_facts(id),
                FOREIGN KEY (episode_id) REFERENCES conversation_episodes(id)
            );
CREATE TABLE episode_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id TEXT NOT NULL,
                summary_type TEXT NOT NULL,  -- "auto", "manual", "llm_generated"
                content TEXT NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                model_used TEXT,
                confidence_score REAL,
                FOREIGN KEY (episode_id) REFERENCES conversation_episodes(id)
            );
CREATE TABLE conversation_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id TEXT NOT NULL,
                user_message TEXT NOT NULL,
                assistant_response TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (episode_id) REFERENCES conversation_episodes(id)
            );
CREATE INDEX idx_episodes_user_status ON conversation_episodes(user_id, status);
CREATE INDEX idx_episodes_start_time ON conversation_episodes(start_time);
CREATE INDEX idx_temporal_episode ON memory_temporal_metadata(episode_id);
CREATE INDEX idx_temporal_timestamp ON memory_temporal_metadata(timestamp);
CREATE INDEX idx_people_name ON people(name);
CREATE INDEX idx_people_updated ON people(updated_at DESC);
CREATE INDEX idx_projects_name ON projects(name);
CREATE INDEX idx_projects_status ON projects(status);
CREATE INDEX idx_projects_updated ON projects(updated_at DESC);
CREATE INDEX idx_relationships_p1 ON relationships(person1_id);
CREATE INDEX idx_relationships_p2 ON relationships(person2_id);
CREATE INDEX idx_relationships_type ON relationships(relationship_type);
CREATE INDEX idx_facts_entity ON memory_facts(entity_type, entity_id);
CREATE INDEX idx_facts_category ON memory_facts(category);
CREATE INDEX idx_facts_importance ON memory_facts(importance DESC);
CREATE INDEX idx_facts_created ON memory_facts(created_at DESC);
CREATE VIRTUAL TABLE memory_facts_fts 
                    USING fts5(fact, content='memory_facts', content_rowid='id')
/* memory_facts_fts(fact) */;
CREATE TABLE IF NOT EXISTS 'memory_facts_fts_data'(id INTEGER PRIMARY KEY, block BLOB);
CREATE TABLE IF NOT EXISTS 'memory_facts_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS 'memory_facts_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);
CREATE TABLE IF NOT EXISTS 'memory_facts_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;
