"""Chat Session Management for Developer Enhanced Router"""
from typing import Dict, List, Optional, Any
from datetime import datetime
import json
import uuid

class ChatSession:
    """Manages a conversation session with requirement extraction"""
    
    def __init__(self, session_id: str = None):
        self.session_id = session_id or str(uuid.uuid4())
        self.messages = []
        self.extracted_requirements = []
        self.extracted_constraints = []
        self.extracted_criteria = []
        self.created_at = datetime.now()
        self.task_ready = False
        
    def add_message(self, role: str, content: str):
        """Add a message to the session"""
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        
    def extract_requirements(self, message: str, response: str):
        """Extract requirements from conversation"""
        # Keywords that indicate requirements
        req_keywords = ['need', 'should', 'must', 'require', 'want', 'implement', 
                       'add', 'create', 'build', 'integrate']
        constraint_keywords = ['not break', 'maintain', 'preserve', 'keep', 'avoid', 
                              'without breaking', 'backward compatible']
        
        # Extract from user message
        for keyword in req_keywords:
            if keyword in message.lower():
                sentences = message.split('.')
                for sent in sentences:
                    if keyword in sent.lower():
                        clean_sent = sent.strip()
                        if clean_sent and len(clean_sent) > 10 and clean_sent not in self.extracted_requirements:
                            self.extracted_requirements.append(clean_sent)
        
        # Extract structured requirements from AI response
        if any(phrase in response.lower() for phrase in ["we'll need to", "here's what", "steps:", "will need"]):
            lines = response.split('\n')
            for line in lines:
                line = line.strip()
                if line and any(line.startswith(p) for p in ['1.', '2.', '3.', '4.', '5.', '-', '*', '•']):
                    # Clean the line
                    import re
                    clean_line = re.sub(r'^[\d\.\-\*\•\s]+', '', line)
                    if clean_line and len(clean_line) > 10 and clean_line not in self.extracted_requirements:
                        self.extracted_requirements.append(clean_line)
        
        # Extract constraints
        for keyword in constraint_keywords:
            if keyword in message.lower() or keyword in response.lower():
                context = message if keyword in message.lower() else response
                sentences = context.split('.')
                for sent in sentences:
                    if keyword in sent.lower():
                        clean_sent = sent.strip()
                        if clean_sent and clean_sent not in self.extracted_constraints:
                            self.extracted_constraints.append(clean_sent)
        
        # Check if we have enough for a task
        self.task_ready = len(self.extracted_requirements) >= 2
    
    def can_create_task(self) -> bool:
        """Check if we have enough info to create a task"""
        return self.task_ready and len(self.extracted_requirements) >= 2
    
    def to_task_data(self, title: str = None) -> Dict[str, Any]:
        """Convert session to task data for the Dynamic Task System"""
        if not title:
            # Generate title from first requirement
            title = self.extracted_requirements[0][:50] if self.extracted_requirements else "New Task"
            if len(title) == 50:
                title += "..."
        
        # Generate objective
        objective = "Implement: " + "; ".join(self.extracted_requirements[:3])
        
        # Generate acceptance criteria if not set
        if not self.extracted_criteria:
            self.extracted_criteria = [
                f"Verify: {req}" for req in self.extracted_requirements[:3]
            ]
            self.extracted_criteria.extend([
                "All tests pass",
                "No existing functionality broken",
                "Code follows project patterns"
            ])
        
        return {
            "title": title,
            "objective": objective,
            "requirements": self.extracted_requirements,
            "constraints": self.extracted_constraints,
            "acceptance_criteria": self.extracted_criteria,
            "chat_context": self.messages[-10:],  # Last 10 messages
            "session_id": self.session_id,
            "priority": "medium",
            "assigned_to": "zack"
        }
    
    def get_summary(self) -> Dict[str, Any]:
        """Get session summary"""
        return {
            "session_id": self.session_id,
            "message_count": len(self.messages),
            "requirements_count": len(self.extracted_requirements),
            "constraints_count": len(self.extracted_constraints),
            "can_create_task": self.can_create_task(),
            "created_at": self.created_at.isoformat(),
            "last_message": self.messages[-1]["content"][:100] if self.messages else None
        }

# Global session storage
chat_sessions: Dict[str, ChatSession] = {}

def get_or_create_session(session_id: Optional[str] = None) -> ChatSession:
    """Get existing session or create new one"""
    if not session_id:
        session_id = str(uuid.uuid4())
    
    if session_id not in chat_sessions:
        chat_sessions[session_id] = ChatSession(session_id)
    
    return chat_sessions[session_id]

def create_task_from_session(session_id: str, title: Optional[str] = None) -> Dict[str, Any]:
    """Convert a chat session into a task"""
    if session_id not in chat_sessions:
        raise ValueError(f"Session {session_id} not found")
    
    session = chat_sessions[session_id]
    if not session.can_create_task():
        raise ValueError("Session doesn't have enough requirements to create a task")
    
    # Get task data
    task_data = session.to_task_data(title)
    
    # Generate task ID
    task_id = str(uuid.uuid4())[:8]
    
    # Add task metadata
    task_data["id"] = task_id
    task_data["created_from_chat"] = True
    task_data["created_at"] = datetime.now().isoformat()
    
    return task_data

def analyze_message_for_implementation(message: str) -> bool:
    """Check if message is discussing implementation"""
    impl_keywords = [
        'implement', 'add', 'create', 'build', 'integrate', 
        'feature', 'redis', 'cache', 'authentication', 'database', 
        'api', 'endpoint', 'frontend', 'backend', 'docker',
        'need to', 'want to', 'should we', 'how to'
    ]
    
    message_lower = message.lower()
    return any(kw in message_lower for kw in impl_keywords)

def suggest_task_creation(session: ChatSession) -> str:
    """Generate suggestion to create task if appropriate"""
    if session.can_create_task() and len(session.messages) >= 4:
        return f"""

💡 **I have enough information to create a task for this.**
- Requirements identified: {len(session.extracted_requirements)}
- Constraints identified: {len(session.extracted_constraints)}

Would you like me to create a task from this discussion? Reply 'yes' or 'create task', or continue discussing to add more details."""
    return ""
