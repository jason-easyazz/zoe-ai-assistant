#!/usr/bin/env python3
"""
Intelligent Architecture Validator

Ensures chat router uses intelligent systems, not hardcoded logic.

This prevents:
- Hardcoded regex patterns for command detection
- If/else chains for intent recognition
- Canned responses instead of LLM generation
- Bypassing MEM Agent, Agent Planner, Expert Orchestrator
"""

import re
from pathlib import Path

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

def check_chat_router_intelligence():
    """Validate chat router uses intelligent systems"""
    chat_path = PROJECT_ROOT / "services/zoe-core/routers/chat.py"
    
    if not chat_path.exists():
        return False, "chat.py not found"
    
    content = chat_path.read_text()
    violations = []
    
    # Check for hardcoded regex patterns (anti-pattern)
    regex_patterns = re.findall(r're\.search\(.*?\)', content)
    if len(regex_patterns) > 2:  # Some regex is OK, but not for command detection
        violations.append(f"Found {len(regex_patterns)} regex patterns - may indicate hardcoded command detection")
    
    # Check for large if/else chains (anti-pattern)
    if_statements = re.findall(r'if.*in message.*:', content)
    if len(if_statements) > 5:
        violations.append(f"Found {len(if_statements)} message string checks - use LLM intent detection instead")
    
    # Check for canned responses (anti-pattern)
    canned_responses = re.findall(r'return\s+"[^"]{50,}"', content)
    if len(canned_responses) > 3:
        violations.append(f"Found {len(canned_responses)} hardcoded responses - use LLM generation instead")
    
    # Check that intelligent systems are imported (required)
    required_imports = [
        ("mem_agent_client", "MemAgentClient - for semantic memory"),
        ("enhanced_mem_agent", "EnhancedMemAgentClient - for action execution"),
        ("route_llm", "RouteLLM - for intelligent routing")
    ]
    
    missing_systems = []
    for module, description in required_imports:
        if module not in content and "import" in content:  # Only check if file has imports
            missing_systems.append(description)
    
    if missing_systems:
        violations.append(f"Missing intelligent systems: {', '.join(missing_systems)}")
    
    # Check for agent/orchestrator usage
    uses_orchestrator = "orchestrat" in content.lower() or "expert" in content.lower()
    uses_mem_agent = "mem_agent" in content.lower()
    
    intelligence_score = 0
    if uses_orchestrator:
        intelligence_score += 1
    if uses_mem_agent:
        intelligence_score += 1
    if not violations:
        intelligence_score += 1
    
    return len(violations) == 0, violations, intelligence_score

def main():
    """Run validation"""
    print(f"\n{Colors.BLUE}{'='*70}{Colors.RESET}")
    print(f"{Colors.BLUE}🧠 INTELLIGENT ARCHITECTURE VALIDATION{Colors.RESET}")
    print(f"{Colors.BLUE}{'='*70}{Colors.RESET}\n")
    
    print("Checking: Chat router uses intelligent systems (not hardcoded logic)...")
    
    passed, violations, score = check_chat_router_intelligence()
    
    if passed:
        print(f"{Colors.GREEN}✅ PASS: Chat router uses intelligent systems{Colors.RESET}")
        print(f"{Colors.GREEN}   Intelligence Score: {score}/3{Colors.RESET}")
    else:
        print(f"{Colors.RED}❌ FAIL: Chat router has hardcoded logic{Colors.RESET}")
        print(f"{Colors.RED}   Intelligence Score: {score}/3{Colors.RESET}")
        print(f"\n{Colors.RED}Violations found:{Colors.RESET}")
        for v in violations:
            print(f"{Colors.RED}  • {v}{Colors.RESET}")
        print(f"\n{Colors.YELLOW}Fix: Use MemAgent, EnhancedMemAgent, ExpertOrchestrator, RouteLLM{Colors.RESET}")
    
    print(f"\n{Colors.BLUE}{'='*70}{Colors.RESET}")
    
    if passed:
        print(f"{Colors.GREEN}✅ INTELLIGENT ARCHITECTURE VALIDATED{Colors.RESET}")
    else:
        print(f"{Colors.RED}❌ REVERT TO INTELLIGENT APPROACH{Colors.RESET}")
        print(f"\n{Colors.YELLOW}The chat router should ORCHESTRATE intelligent systems, not replace them!{Colors.RESET}")
    
    print(f"{Colors.BLUE}{'='*70}{Colors.RESET}\n")
    
    return 0 if passed else 1

if __name__ == "__main__":
    import sys
    sys.exit(main())

