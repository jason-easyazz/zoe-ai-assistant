#!/usr/bin/env python3
"""
Add Enhanced Zoe Features Tasks to Developer Task Database
Based on Codex's feedback and Akiflow-inspired capabilities
"""

import sqlite3
import json
import hashlib
from datetime import datetime
from pathlib import Path

# Database path
DB_PATH = "/home/zoe/assistant/data/developer_tasks.db"

def init_database():
    """Initialize the database if it doesn't exist"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create dynamic_tasks table if it doesn't exist
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dynamic_tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            requirements TEXT NOT NULL,  -- JSON array
            constraints TEXT,  -- JSON array
            acceptance_criteria TEXT,  -- JSON array
            priority TEXT DEFAULT 'medium',
            assigned_to TEXT DEFAULT 'zack',
            status TEXT DEFAULT 'pending',
            context_snapshot TEXT,  -- System state when created (for reference)
            last_analysis TEXT,  -- Last execution analysis
            execution_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_executed_at TIMESTAMP,
            completed_at TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def add_task(title, objective, requirements, constraints, acceptance_criteria, priority="medium", assigned_to="zack"):
    """Add a single task to the database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Generate task ID
    task_id = hashlib.md5(f"{title}{datetime.now()}".encode()).hexdigest()[:8]
    
    # Check if task already exists
    cursor.execute("SELECT id FROM dynamic_tasks WHERE title = ?", (title,))
    if cursor.fetchone():
        print(f"Task '{title}' already exists, skipping...")
        conn.close()
        return False
    
    # Insert task
    cursor.execute('''
        INSERT INTO dynamic_tasks 
        (id, title, objective, requirements, constraints, acceptance_criteria, 
         priority, assigned_to, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        task_id,
        title,
        objective,
        json.dumps(requirements),
        json.dumps(constraints),
        json.dumps(acceptance_criteria),
        priority,
        assigned_to,
        'pending'
    ))
    
    conn.commit()
    conn.close()
    print(f"✅ Added task: {title}")
    return True

def main():
    """Add all enhanced tasks to the database"""
    print("🚀 Adding Enhanced Zoe Features Tasks to Developer Database")
    print("=" * 60)
    
    # Initialize database
    init_database()
    
    # Time-Intelligence Layer Tasks
    print("\n📅 Adding Time-Intelligence Layer Tasks...")
    time_tasks = [
        {
            "title": "Add Time Estimation to Tasks",
            "objective": "Enhance existing task system with duration estimates and smart scheduling capabilities",
            "requirements": [
                "Add time estimation field to task creation form",
                "Implement time-based task sorting and filtering",
                "Create time tracking interface for active tasks",
                "Add estimated vs actual time comparison analytics"
            ],
            "constraints": [
                "Don't break existing task functionality",
                "Maintain backward compatibility with current tasks",
                "Keep UI responsive on Pi 5 hardware"
            ],
            "acceptance_criteria": [
                "Users can add time estimates when creating tasks",
                "Tasks can be sorted by estimated duration",
                "Time tracking works for in-progress tasks",
                "Analytics show time estimation accuracy"
            ],
            "priority": "high"
        },
        {
            "title": "Implement Smart Scheduling Engine",
            "objective": "Create AI-based optimal time slot assignment for tasks based on user patterns and energy levels",
            "requirements": [
                "Analyze user task completion patterns",
                "Implement energy-based scheduling algorithm",
                "Add calendar integration for time blocking",
                "Create scheduling conflict detection and resolution"
            ],
            "constraints": [
                "Respect existing task priorities",
                "Work offline without external calendar APIs",
                "Learn from user behavior patterns locally"
            ],
            "acceptance_criteria": [
                "System suggests optimal times for different task types",
                "Energy-based scheduling adapts to user patterns",
                "Calendar blocking prevents double-booking",
                "Scheduling conflicts are automatically resolved"
            ],
            "priority": "high"
        },
        {
            "title": "Add Calendar Blocking for Tasks",
            "objective": "Reserve dedicated time blocks for bucket list items and important tasks",
            "requirements": [
                "Create calendar view for task scheduling",
                "Implement drag-and-drop task scheduling",
                "Add time block conflict detection",
                "Create recurring task scheduling"
            ],
            "constraints": [
                "Integrate with existing task system",
                "Maintain privacy - no external calendar sync",
                "Keep interface simple and intuitive"
            ],
            "acceptance_criteria": [
                "Users can drag tasks to calendar slots",
                "Time blocks are visually distinct",
                "Conflicts are highlighted and resolved",
                "Recurring tasks can be scheduled"
            ],
            "priority": "medium"
        },
        {
            "title": "Build Energy-Based Scheduler",
            "objective": "Learn and adapt to user energy patterns to schedule hard tasks at optimal times",
            "requirements": [
                "Track user energy levels throughout day",
                "Analyze task completion success by time of day",
                "Implement machine learning for pattern recognition",
                "Create energy-based task recommendations"
            ],
            "constraints": [
                "Privacy-first: all learning happens locally",
                "Respect user's explicit preferences",
                "Don't override user's manual scheduling"
            ],
            "acceptance_criteria": [
                "System learns user energy patterns over time",
                "Hard tasks are suggested during high-energy periods",
                "Energy recommendations improve task completion rates",
                "User can override energy-based suggestions"
            ],
            "priority": "medium"
        }
    ]
    
    # Context-Aware Task Suggestions
    print("\n🧠 Adding Context-Aware Task Suggestions...")
    context_tasks = [
        {
            "title": "Implement Vector Search for Memory",
            "objective": "Create RAG system for semantic task and memory retrieval using vector embeddings",
            "requirements": [
                "Implement vector database for task and memory storage",
                "Create embedding generation for task content",
                "Build semantic search interface",
                "Add similarity scoring and ranking"
            ],
            "constraints": [
                "Use lightweight embedding models for Pi 5",
                "Keep all data local and private",
                "Maintain fast search performance"
            ],
            "acceptance_criteria": [
                "Users can find similar past tasks semantically",
                "Search works across all task types and memories",
                "Results are ranked by relevance",
                "Search performance is under 2 seconds"
            ],
            "priority": "high"
        },
        {
            "title": "Add Ambient Context Awareness",
            "objective": "Use time of day, location, and weather to suggest relevant tasks",
            "requirements": [
                "Integrate time-based task suggestions",
                "Add weather API integration (privacy-first)",
                "Implement location-aware suggestions",
                "Create context-based task filtering"
            ],
            "constraints": [
                "All location data stays local",
                "Weather data is optional and cached",
                "Don't require GPS or external services"
            ],
            "acceptance_criteria": [
                "Tasks are suggested based on time of day",
                "Weather affects outdoor task suggestions",
                "Context awareness improves task relevance",
                "Users can disable context features"
            ],
            "priority": "medium"
        },
        {
            "title": "Create Relationship-Aware Task Reminders",
            "objective": "Generate proactive reminders based on relationships and past interactions",
            "requirements": [
                "Track relationship interaction patterns",
                "Implement reminder generation algorithm",
                "Add relationship-based task suggestions",
                "Create reminder scheduling system"
            ],
            "constraints": [
                "Respect privacy - no external data sharing",
                "Learn only from user's explicit interactions",
                "Allow users to disable relationship tracking"
            ],
            "acceptance_criteria": [
                "System suggests 'contact Mom' after 2 weeks",
                "Relationship reminders are contextually relevant",
                "Users can customize reminder frequencies",
                "Privacy controls are clear and easy to use"
            ],
            "priority": "low"
        },
        {
            "title": "Build Project Momentum Detection",
            "objective": "Detect stalled projects and suggest next steps to maintain momentum",
            "requirements": [
                "Track project activity and progress",
                "Implement stagnation detection algorithm",
                "Create momentum-based suggestions",
                "Add project health scoring"
            ],
            "constraints": [
                "Don't overwhelm users with suggestions",
                "Respect user's project priorities",
                "Keep momentum scoring simple and clear"
            ],
            "acceptance_criteria": [
                "Stalled projects are identified automatically",
                "Next steps are suggested for stalled projects",
                "Project momentum is visualized clearly",
                "Users can dismiss momentum suggestions"
            ],
            "priority": "medium"
        }
    ]
    
    # Advanced Voice Capabilities
    print("\n🎤 Adding Advanced Voice Capabilities...")
    voice_tasks = [
        {
            "title": "Create Streaming STT Pipeline",
            "objective": "Implement real-time voice transcription for conversational task capture",
            "requirements": [
                "Integrate streaming Whisper API",
                "Create real-time transcription interface",
                "Add voice command processing",
                "Implement conversation context management"
            ],
            "constraints": [
                "Maintain low latency for real-time feel",
                "Work with existing Whisper setup",
                "Handle background noise gracefully"
            ],
            "acceptance_criteria": [
                "Voice is transcribed in real-time",
                "Voice commands create tasks automatically",
                "Conversation context is maintained",
                "Transcription accuracy is above 90%"
            ],
            "priority": "high"
        },
        {
            "title": "Add Voice Notes to Tasks",
            "objective": "Allow users to attach audio memos to tasks with automatic transcription",
            "requirements": [
                "Implement voice recording interface",
                "Add audio file storage and management",
                "Create transcription service integration",
                "Build voice note playback interface"
            ],
            "constraints": [
                "Audio files must be stored locally",
                "Transcription should be fast and accurate",
                "Keep audio file sizes reasonable"
            ],
            "acceptance_criteria": [
                "Users can record voice notes for any task",
                "Voice notes are automatically transcribed",
                "Both audio and text are accessible",
                "Voice notes sync with task updates"
            ],
            "priority": "medium"
        },
        {
            "title": "Implement Multi-Speaker Support",
            "objective": "Enable different family members to add tasks to shared lists with voice recognition",
            "requirements": [
                "Add user identification system",
                "Implement speaker recognition",
                "Create family member profiles",
                "Add voice-based task assignment"
            ],
            "constraints": [
                "Speaker recognition must be privacy-first",
                "Family member data stays local",
                "Voice recognition should be accurate"
            ],
            "acceptance_criteria": [
                "System recognizes different family members",
                "Tasks are assigned to correct speakers",
                "Family members can share task lists",
                "Voice commands work for all users"
            ],
            "priority": "low"
        },
        {
            "title": "Add Wake Phrase Training",
            "objective": "Enable custom wake words beyond 'Hey Zoe' for personalized voice activation",
            "requirements": [
                "Implement wake word detection system",
                "Create custom wake word training interface",
                "Add wake word validation",
                "Integrate with existing voice pipeline"
            ],
            "constraints": [
                "Wake word detection must be local",
                "Training should be simple and quick",
                "Don't interfere with existing voice commands"
            ],
            "acceptance_criteria": [
                "Users can train custom wake phrases",
                "Custom wake words work reliably",
                "Training process is intuitive",
                "Multiple wake phrases are supported"
            ],
            "priority": "low"
        }
    ]
    
    # Service Health & Monitoring
    print("\n📊 Adding Service Health & Monitoring Tasks...")
    health_tasks = [
        {
            "title": "Build Unified Health Dashboard",
            "objective": "Create single view of all Docker services with real-time status monitoring",
            "requirements": [
                "Integrate Docker service monitoring",
                "Create real-time status dashboard",
                "Add service performance metrics",
                "Implement health check automation"
            ],
            "constraints": [
                "Dashboard must be lightweight for Pi 5",
                "Real-time updates without overwhelming system",
                "Work with existing Docker setup"
            ],
            "acceptance_criteria": [
                "All services show real-time status",
                "Performance metrics are clearly displayed",
                "Health checks run automatically",
                "Dashboard updates in real-time"
            ],
            "priority": "critical"
        },
        {
            "title": "Implement Alert System",
            "objective": "Create proactive notifications when services degrade or fail",
            "requirements": [
                "Add service degradation detection",
                "Implement notification system",
                "Create alert escalation rules",
                "Add alert history and management"
            ],
            "constraints": [
                "Alerts must be actionable and not spam",
                "Notification system should be reliable",
                "Alert rules should be configurable"
            ],
            "acceptance_criteria": [
                "Service failures trigger immediate alerts",
                "Degradation warnings are sent proactively",
                "Alert history is maintained",
                "Users can configure alert preferences"
            ],
            "priority": "critical"
        },
        {
            "title": "Add Performance Trending",
            "objective": "Track response times and resource usage over time with visual analytics",
            "requirements": [
                "Implement performance data collection",
                "Create time-series data storage",
                "Build performance visualization",
                "Add trend analysis and reporting"
            ],
            "constraints": [
                "Data collection must be lightweight",
                "Storage should be efficient",
                "Visualizations should be clear"
            ],
            "acceptance_criteria": [
                "Performance metrics are tracked over time",
                "Trends are visualized clearly",
                "Reports can be generated",
                "Performance data is actionable"
            ],
            "priority": "high"
        },
        {
            "title": "Create Auto-Recovery System",
            "objective": "Automatically restart failed services with exponential backoff and smart retry logic",
            "requirements": [
                "Implement service failure detection",
                "Add automatic restart logic",
                "Create exponential backoff algorithm",
                "Add recovery success tracking"
            ],
            "constraints": [
                "Recovery should not cause infinite loops",
                "Backoff should prevent system overload",
                "Recovery attempts should be logged"
            ],
            "acceptance_criteria": [
                "Failed services restart automatically",
                "Backoff prevents system overload",
                "Recovery success is tracked",
                "Manual override is available"
            ],
            "priority": "critical"
        }
    ]
    
    # Enhanced Automation Workflows
    print("\n⚙️ Adding Enhanced Automation Workflow Tasks...")
    automation_tasks = [
        {
            "title": "Create Task Template System",
            "objective": "Build reusable task patterns for common scenarios like party planning or trip preparation",
            "requirements": [
                "Design template creation interface",
                "Implement template storage and retrieval",
                "Add template customization options",
                "Create template sharing system"
            ],
            "constraints": [
                "Templates should be flexible and customizable",
                "Template creation should be intuitive",
                "Don't overcomplicate the interface"
            ],
            "acceptance_criteria": [
                "Users can create custom task templates",
                "Templates can be applied to new projects",
                "Templates are easily customizable",
                "Template library is searchable"
            ],
            "priority": "medium"
        },
        {
            "title": "Implement Conditional Automation",
            "objective": "Create smart automation rules that trigger based on conditions like weather or time",
            "requirements": [
                "Design rule creation interface",
                "Implement condition evaluation engine",
                "Add action execution system",
                "Create rule testing and validation"
            ],
            "constraints": [
                "Rules should be simple to create",
                "Condition evaluation should be reliable",
                "Actions should be safe and reversible"
            ],
            "acceptance_criteria": [
                "Users can create conditional rules",
                "Rules trigger based on specified conditions",
                "Rule execution is logged and trackable",
                "Rules can be tested before activation"
            ],
            "priority": "medium"
        },
        {
            "title": "Add Cross-List Dependencies",
            "objective": "Enable tasks across different lists to depend on each other and auto-trigger",
            "requirements": [
                "Design dependency relationship system",
                "Implement dependency tracking",
                "Add automatic task triggering",
                "Create dependency visualization"
            ],
            "constraints": [
                "Dependencies should not create circular references",
                "Dependency resolution should be efficient",
                "Users should understand dependency chains"
            ],
            "acceptance_criteria": [
                "Tasks can depend on tasks in other lists",
                "Dependencies trigger automatically",
                "Dependency chains are visualized",
                "Circular dependencies are prevented"
            ],
            "priority": "low"
        },
        {
            "title": "Build Email-to-Task Parser",
            "objective": "Parse emails for actionable items and create tasks automatically (privacy-first, local only)",
            "requirements": [
                "Implement email parsing engine",
                "Add action item detection",
                "Create task generation from emails",
                "Add email privacy controls"
            ],
            "constraints": [
                "All email processing must be local",
                "No external email service integration",
                "Privacy controls must be comprehensive"
            ],
            "acceptance_criteria": [
                "Emails are parsed for actionable items",
                "Tasks are created automatically from emails",
                "Privacy controls are clear and effective",
                "Email parsing accuracy is high"
            ],
            "priority": "low"
        }
    ]
    
    # Privacy-First Backup & Sync
    print("\n🔒 Adding Privacy-First Backup & Sync Tasks...")
    privacy_tasks = [
        {
            "title": "Implement Encrypted Snapshots",
            "objective": "Create automatic encrypted backups to external drive with secure key management",
            "requirements": [
                "Implement encryption for backup files",
                "Add automatic backup scheduling",
                "Create secure key management",
                "Add backup verification system"
            ],
            "constraints": [
                "Encryption must be strong and secure",
                "Backup process should not impact performance",
                "Key management should be user-friendly"
            ],
            "acceptance_criteria": [
                "Backups are encrypted automatically",
                "Backup process runs on schedule",
                "Keys are managed securely",
                "Backup integrity is verified"
            ],
            "priority": "high"
        },
        {
            "title": "Add Selective Sync System",
            "objective": "Allow users to choose which lists to share with family members with granular controls",
            "requirements": [
                "Design sync permission system",
                "Implement selective data sharing",
                "Add family member management",
                "Create sync conflict resolution"
            ],
            "constraints": [
                "Sync should be secure and private",
                "Conflict resolution should be intuitive",
                "Permission system should be flexible"
            ],
            "acceptance_criteria": [
                "Users can choose what to sync",
                "Family members see only shared data",
                "Sync conflicts are resolved automatically",
                "Permission changes take effect immediately"
            ],
            "priority": "medium"
        },
        {
            "title": "Create Git-based Task History",
            "objective": "Implement version control for task changes with full history and rollback capabilities",
            "requirements": [
                "Integrate Git for task versioning",
                "Add change tracking and history",
                "Implement rollback functionality",
                "Create history visualization"
            ],
            "constraints": [
                "Git integration should be lightweight",
                "History should not consume too much space",
                "Rollback should be safe and tested"
            ],
            "acceptance_criteria": [
                "All task changes are versioned",
                "Users can view task history",
                "Rollback to previous versions works",
                "History is searchable and clear"
            ],
            "priority": "low"
        },
        {
            "title": "Build Offline PWA",
            "objective": "Create Progressive Web App that works fully offline with local data storage",
            "requirements": [
                "Implement service worker for offline functionality",
                "Add local data storage and sync",
                "Create offline-first UI",
                "Add sync when connection returns"
            ],
            "constraints": [
                "PWA should work on all modern browsers",
                "Offline functionality should be comprehensive",
                "Sync should be reliable and efficient"
            ],
            "acceptance_criteria": [
                "App works completely offline",
                "Data syncs when connection returns",
                "Offline indicators are clear",
                "PWA installs on mobile devices"
            ],
            "priority": "medium"
        }
    ]
    
    # Focus & Productivity Analytics
    print("\n📈 Adding Focus & Productivity Analytics Tasks...")
    analytics_tasks = [
        {
            "title": "Implement Time Tracking",
            "objective": "Add automatic time tracking when tasks are active with detailed analytics",
            "requirements": [
                "Add automatic time tracking for active tasks",
                "Create time tracking analytics dashboard",
                "Implement time categorization",
                "Add productivity insights"
            ],
            "constraints": [
                "Time tracking should be automatic",
                "Analytics should be privacy-preserving",
                "UI should not be cluttered"
            ],
            "acceptance_criteria": [
                "Time is tracked automatically for active tasks",
                "Analytics show time usage patterns",
                "Time data is categorized by task type",
                "Productivity insights are actionable"
            ],
            "priority": "medium"
        },
        {
            "title": "Add Distraction Scoring",
            "objective": "Measure focus quality during task work and provide distraction insights",
            "requirements": [
                "Implement focus measurement algorithm",
                "Add distraction detection",
                "Create focus quality scoring",
                "Build distraction insights dashboard"
            ],
            "constraints": [
                "Focus measurement should be non-intrusive",
                "Scoring should be accurate and fair",
                "Insights should be helpful, not judgmental"
            ],
            "acceptance_criteria": [
                "Focus quality is measured during tasks",
                "Distractions are identified and scored",
                "Focus insights are provided to users",
                "Scoring improves over time"
            ],
            "priority": "low"
        },
        {
            "title": "Create Productivity Pattern Analysis",
            "objective": "Identify best times for different task types and optimal work patterns",
            "requirements": [
                "Analyze task completion patterns",
                "Identify optimal work times",
                "Create productivity recommendations",
                "Build pattern visualization"
            ],
            "constraints": [
                "Analysis should be privacy-preserving",
                "Patterns should be statistically significant",
                "Recommendations should be actionable"
            ],
            "acceptance_criteria": [
                "Optimal work times are identified",
                "Productivity patterns are visualized",
                "Recommendations are personalized",
                "Patterns improve over time"
            ],
            "priority": "medium"
        },
        {
            "title": "Implement Burnout Prevention",
            "objective": "Detect overwork patterns and suggest breaks to maintain healthy productivity",
            "requirements": [
                "Track work intensity and duration",
                "Implement burnout detection algorithm",
                "Create break suggestion system",
                "Add wellness recommendations"
            ],
            "constraints": [
                "Burnout detection should be accurate",
                "Suggestions should be helpful, not annoying",
                "Wellness recommendations should be evidence-based"
            ],
            "acceptance_criteria": [
                "Overwork patterns are detected",
                "Break suggestions are timely and helpful",
                "Wellness recommendations are provided",
                "Burnout prevention is effective"
            ],
            "priority": "high"
        }
    ]
    
    # Add all tasks to database
    all_tasks = [
        ("Time-Intelligence", time_tasks),
        ("Context-Aware", context_tasks),
        ("Voice Capabilities", voice_tasks),
        ("Service Health", health_tasks),
        ("Automation Workflows", automation_tasks),
        ("Privacy & Backup", privacy_tasks),
        ("Productivity Analytics", analytics_tasks)
    ]
    
    total_added = 0
    for category, tasks in all_tasks:
        print(f"\n📋 Processing {category} Tasks...")
        for task in tasks:
            if add_task(**task):
                total_added += 1
    
    print(f"\n🎉 Successfully added {total_added} enhanced tasks to the developer database!")
    print(f"📊 Total tasks in database: {total_added}")
    print("\n✨ Enhanced Zoe Features are ready for development!")

if __name__ == "__main__":
    main()
