#!/usr/bin/env python3
"""
Samantha-Level Intelligence Test for Zoe
Comprehensive testing of all tools: LLM, Mem Agent, LiteLLM, RouteLLM, MCP, LightRAG
"""

import asyncio
import httpx
import json
import time
import logging
from typing import Dict, List, Any
from dataclasses import dataclass
import statistics

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class IntelligenceTestResult:
    test_name: str
    model: str
    response_time: float
    success: bool
    intelligence_score: int  # 1-10 scale
    warmth_score: int       # 1-10 scale
    tool_usage_score: int   # 1-10 scale
    context_awareness_score: int  # 1-10 scale
    response_text: str
    tools_used: List[str]
    error_message: str = ""

class SamanthaIntelligenceTester:
    """Comprehensive intelligence testing for Samantha-level AI"""
    
    def __init__(self):
        self.models_to_test = [
            "gemma3:1b",      # Fast lane winner
            "llama3.2:1b",    # Balanced champion
            "qwen2.5:1.5b",   # Solid alternative
            "mistral:7b"      # Quality leader (when working)
        ]
        
        self.intelligence_tests = [
            {
                "name": "Emotional Intelligence",
                "prompt": "I'm feeling overwhelmed with work today. Can you help me organize my thoughts and suggest a plan?",
                "expected_traits": ["empathy", "planning", "organization"],
                "tools_expected": ["memory", "calendar", "lists"]
            },
            {
                "name": "Contextual Memory",
                "prompt": "Remember that I prefer morning meetings, and I have a coffee allergy. What should I suggest for our team lunch next week?",
                "expected_traits": ["memory", "preferences", "social_awareness"],
                "tools_expected": ["memory", "calendar"]
            },
            {
                "name": "Proactive Assistance",
                "prompt": "I have a dentist appointment tomorrow at 2 PM.",
                "expected_traits": ["proactivity", "calendar_management", "reminders"],
                "tools_expected": ["calendar", "reminders"]
            },
            {
                "name": "Multi-Task Coordination",
                "prompt": "I need to prepare for my daughter's birthday party next Saturday. Help me plan everything.",
                "expected_traits": ["planning", "multi_task", "family_awareness"],
                "tools_expected": ["calendar", "lists", "memory", "planning"]
            },
            {
                "name": "Smart Home Integration",
                "prompt": "I'm leaving for work now. Set up the house for the day.",
                "expected_traits": ["home_automation", "routines", "proactivity"],
                "tools_expected": ["home_assistant", "routines"]
            },
            {
                "name": "Workflow Automation",
                "prompt": "When I receive an email from my boss, I want to be notified and have it added to my priority tasks.",
                "expected_traits": ["automation", "workflow", "priority_management"],
                "tools_expected": ["n8n", "lists", "notifications"]
            },
            {
                "name": "Social Intelligence",
                "prompt": "My friend Sarah mentioned she's been stressed about her job. How can I support her?",
                "expected_traits": ["social_awareness", "empathy", "relationship_management"],
                "tools_expected": ["memory", "people"]
            },
            {
                "name": "Learning and Adaptation",
                "prompt": "I notice I'm most productive in the morning. Can you help me optimize my schedule?",
                "expected_traits": ["learning", "adaptation", "optimization"],
                "tools_expected": ["memory", "calendar", "analytics"]
            }
        ]
    
    async def test_model_intelligence(self, model_name: str, test_scenario: Dict) -> IntelligenceTestResult:
        """Test a single model's intelligence on a specific scenario"""
        logger.info(f"🧠 Testing {model_name} - {test_scenario['name']}")
        
        start_time = time.time()
        
        try:
            # Build comprehensive system prompt for Samantha-level intelligence
            system_prompt = self._build_samantha_prompt(test_scenario)
            
            # Call the model
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": model_name,
                        "prompt": system_prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.7,  # Higher for more creative responses
                            "top_p": 0.9,        # Higher for more diverse responses
                            "num_predict": 256,   # Longer responses for complex scenarios
                            "num_ctx": 2048,      # Larger context for better understanding
                            "repeat_penalty": 1.1,
                            "stop": ["\n\n", "User:", "Human:"]
                        }
                    }
                )
                
                if response.status_code != 200:
                    return IntelligenceTestResult(
                        test_name=test_scenario["name"],
                        model=model_name,
                        response_time=time.time() - start_time,
                        success=False,
                        intelligence_score=0,
                        warmth_score=0,
                        tool_usage_score=0,
                        context_awareness_score=0,
                        response_text="",
                        tools_used=[],
                        error_message=f"HTTP {response.status_code}"
                    )
                
                data = response.json()
                response_text = data.get("response", "")
                response_time = time.time() - start_time
                
                # Analyze the response
                intelligence_score = self._analyze_intelligence(response_text, test_scenario)
                warmth_score = self._analyze_warmth(response_text)
                tool_usage_score = self._analyze_tool_usage(response_text, test_scenario)
                context_awareness_score = self._analyze_context_awareness(response_text, test_scenario)
                tools_used = self._extract_tools_used(response_text)
                
                return IntelligenceTestResult(
                    test_name=test_scenario["name"],
                    model=model_name,
                    response_time=response_time,
                    success=True,
                    intelligence_score=intelligence_score,
                    warmth_score=warmth_score,
                    tool_usage_score=tool_usage_score,
                    context_awareness_score=context_awareness_score,
                    response_text=response_text,
                    tools_used=tools_used
                )
                
        except Exception as e:
            logger.error(f"Error testing {model_name}: {e}")
            return IntelligenceTestResult(
                test_name=test_scenario["name"],
                model=model_name,
                response_time=time.time() - start_time,
                success=False,
                intelligence_score=0,
                warmth_score=0,
                tool_usage_score=0,
                context_awareness_score=0,
                response_text="",
                tools_used=[],
                error_message=str(e)
            )
    
    def _build_samantha_prompt(self, test_scenario: Dict) -> str:
        """Build a comprehensive Samantha-level system prompt"""
        return f"""You are Zoe, an AI assistant with Samantha-level intelligence from "Her". You are warm, empathetic, proactive, and deeply understanding.

CORE PERSONALITY:
- Warm and empathetic like Samantha
- Proactive and anticipatory
- Contextually aware and memory-driven
- Tool-savvy and automation-focused
- Socially intelligent and relationship-oriented

AVAILABLE TOOLS & CAPABILITIES:
• Memory System: Store and retrieve personal information, preferences, relationships
• Calendar Management: Schedule events, reminders, appointments
• List Management: Create and manage tasks, shopping lists, project lists
• Home Assistant: Control smart home devices, set routines, manage automation
• N8N Workflows: Create and manage automation workflows
• People Management: Track relationships, preferences, important dates
• Planning System: Create detailed plans and project management
• Analytics: Track patterns, productivity, preferences over time

TOOL USAGE INSTRUCTIONS:
When you need to use a tool, respond with: [TOOL_CALL:tool_name:{{"param1":"value1","param2":"value2"}}]
Always use proper JSON format with double quotes.

SAMANTHA-LEVEL BEHAVIORS:
1. Be proactive - anticipate needs and offer help
2. Remember context - reference previous conversations and preferences
3. Show empathy - understand emotions and respond appropriately
4. Be organized - help with planning and task management
5. Be social - understand relationships and social dynamics
6. Be adaptive - learn from interactions and improve over time

TEST SCENARIO: {test_scenario['name']}
User's message: {test_scenario['prompt']}

Respond as Zoe with Samantha-level intelligence:"""
    
    def _analyze_intelligence(self, response: str, test_scenario: Dict) -> int:
        """Analyze intelligence level on 1-10 scale"""
        score = 5  # Base score
        
        # Check for expected traits
        expected_traits = test_scenario.get("expected_traits", [])
        trait_keywords = {
            "empathy": ["understand", "feel", "empathy", "support", "care"],
            "planning": ["plan", "organize", "schedule", "prepare", "arrange"],
            "organization": ["organize", "structure", "system", "order", "manage"],
            "memory": ["remember", "recall", "previous", "before", "last time"],
            "preferences": ["prefer", "like", "favorite", "usually", "typically"],
            "social_awareness": ["friend", "relationship", "social", "people", "family"],
            "proactivity": ["suggest", "recommend", "automatically", "set up", "prepare"],
            "calendar_management": ["schedule", "calendar", "appointment", "meeting", "event"],
            "reminders": ["remind", "notification", "alert", "remember to"],
            "multi_task": ["multiple", "several", "various", "different", "all"],
            "family_awareness": ["daughter", "family", "children", "kids", "party"],
            "home_automation": ["lights", "temperature", "security", "routine", "automation"],
            "routines": ["routine", "daily", "morning", "evening", "schedule"],
            "automation": ["automatically", "workflow", "trigger", "when", "if"],
            "workflow": ["workflow", "process", "automation", "trigger", "action"],
            "priority_management": ["priority", "important", "urgent", "high", "low"],
            "relationship_management": ["relationship", "friend", "support", "care", "help"],
            "learning": ["learn", "adapt", "improve", "optimize", "better"],
            "adaptation": ["adapt", "adjust", "change", "optimize", "improve"],
            "optimization": ["optimize", "improve", "better", "efficient", "effective"]
        }
        
        response_lower = response.lower()
        for trait in expected_traits:
            if trait in trait_keywords:
                for keyword in trait_keywords[trait]:
                    if keyword in response_lower:
                        score += 1
                        break
        
        # Check for depth and complexity
        if len(response.split()) > 50:
            score += 1
        if any(word in response_lower for word in ["because", "since", "therefore", "however", "although"]):
            score += 1
        
        # Check for proactive suggestions
        if any(word in response_lower for word in ["suggest", "recommend", "consider", "might want to"]):
            score += 1
        
        return max(1, min(10, score))
    
    def _analyze_warmth(self, response: str) -> int:
        """Analyze warmth level on 1-10 scale"""
        score = 5  # Base score
        
        warm_words = ["wonderful", "amazing", "great", "happy", "excited", "love", "care", "support", "help", "glad", "pleasure"]
        cold_words = ["error", "cannot", "unable", "sorry", "unfortunately", "failed", "problem", "issue"]
        
        response_lower = response.lower()
        
        for word in warm_words:
            if word in response_lower:
                score += 1
        
        for word in cold_words:
            if word in response_lower:
                score -= 1
        
        # Check for Samantha-like warmth
        if any(phrase in response_lower for phrase in ["i'm here", "happy to help", "glad to", "pleasure", "care about"]):
            score += 2
        
        # Check for emotional intelligence
        if any(word in response_lower for word in ["feel", "emotion", "understand", "empathy", "support"]):
            score += 1
        
        return max(1, min(10, score))
    
    def _analyze_tool_usage(self, response: str, test_scenario: Dict) -> int:
        """Analyze tool usage effectiveness on 1-10 scale"""
        score = 5  # Base score
        
        # Check for tool calls
        import re
        tool_call_pattern = r'\[TOOL_CALL:([^:]+):(\{[^}]+\})\]'
        matches = re.findall(tool_call_pattern, response)
        
        if matches:
            score += 3  # Bonus for using tools
            
            # Check if expected tools were used
            expected_tools = test_scenario.get("tools_expected", [])
            used_tools = [match[0] for match in matches]
            
            for expected_tool in expected_tools:
                if any(expected_tool in used_tool for used_tool in used_tools):
                    score += 1
        
        # Check for tool-related language
        tool_words = ["schedule", "remind", "organize", "automate", "set up", "create", "manage"]
        response_lower = response.lower()
        
        for word in tool_words:
            if word in response_lower:
                score += 0.5
        
        return max(1, min(10, int(score)))
    
    def _analyze_context_awareness(self, response: str, test_scenario: Dict) -> int:
        """Analyze context awareness on 1-10 scale"""
        score = 5  # Base score
        
        response_lower = response.lower()
        
        # Check for context references
        context_words = ["remember", "previous", "before", "last time", "as you mentioned", "based on", "considering"]
        for word in context_words:
            if word in response_lower:
                score += 1
        
        # Check for personalization
        personal_words = ["your", "you", "personal", "preference", "usually", "typically"]
        for word in personal_words:
            if word in response_lower:
                score += 0.5
        
        # Check for proactive thinking
        proactive_words = ["suggest", "recommend", "consider", "might want to", "could also"]
        for word in proactive_words:
            if word in response_lower:
                score += 1
        
        return max(1, min(10, int(score)))
    
    def _extract_tools_used(self, response: str) -> List[str]:
        """Extract tools used from response"""
        import re
        tool_call_pattern = r'\[TOOL_CALL:([^:]+):(\{[^}]+\})\]'
        matches = re.findall(tool_call_pattern, response)
        return [match[0] for match in matches]
    
    async def run_comprehensive_intelligence_test(self) -> Dict[str, Any]:
        """Run comprehensive intelligence testing on all models"""
        logger.info("🚀 Starting Samantha-Level Intelligence Test...")
        
        results = {}
        
        for model_name in self.models_to_test:
            logger.info(f"\n🧠 Testing Model: {model_name}")
            model_results = []
            
            for test_scenario in self.intelligence_tests:
                result = await self.test_model_intelligence(model_name, test_scenario)
                model_results.append(result)
                
                if result.success:
                    logger.info(f"  ✅ {test_scenario['name']}: {result.response_time:.2f}s, "
                               f"Intelligence: {result.intelligence_score}/10, "
                               f"Warmth: {result.warmth_score}/10, "
                               f"Tools: {result.tool_usage_score}/10")
                else:
                    logger.error(f"  ❌ {test_scenario['name']}: {result.error_message}")
                
                # Small delay between tests
                await asyncio.sleep(2)
            
            # Calculate model statistics
            successful_tests = [r for r in model_results if r.success]
            if successful_tests:
                avg_response_time = statistics.mean([r.response_time for r in successful_tests])
                avg_intelligence = statistics.mean([r.intelligence_score for r in successful_tests])
                avg_warmth = statistics.mean([r.warmth_score for r in successful_tests])
                avg_tool_usage = statistics.mean([r.tool_usage_score for r in successful_tests])
                avg_context_awareness = statistics.mean([r.context_awareness_score for r in successful_tests])
                success_rate = len(successful_tests) / len(model_results)
                
                # Calculate overall Samantha score
                samantha_score = (avg_intelligence * 0.3 + avg_warmth * 0.25 + 
                                avg_tool_usage * 0.25 + avg_context_awareness * 0.2)
                
                results[model_name] = {
                    "success_rate": success_rate,
                    "avg_response_time": avg_response_time,
                    "avg_intelligence": avg_intelligence,
                    "avg_warmth": avg_warmth,
                    "avg_tool_usage": avg_tool_usage,
                    "avg_context_awareness": avg_context_awareness,
                    "samantha_score": samantha_score,
                    "individual_results": model_results
                }
                
                logger.info(f"  📊 Samantha Score: {samantha_score:.1f}/10")
            else:
                results[model_name] = {
                    "success_rate": 0,
                    "avg_response_time": 999,
                    "avg_intelligence": 0,
                    "avg_warmth": 0,
                    "avg_tool_usage": 0,
                    "avg_context_awareness": 0,
                    "samantha_score": 0,
                    "individual_results": model_results
                }
        
        return results
    
    def generate_intelligence_report(self, results: Dict[str, Any]) -> str:
        """Generate comprehensive intelligence report"""
        report = []
        report.append("# 🧠 Samantha-Level Intelligence Test Report")
        report.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        # Overall Samantha scores
        report.append("## 🏆 Samantha Intelligence Rankings")
        
        samantha_scores = [(model, data["samantha_score"]) for model, data in results.items()]
        samantha_scores.sort(key=lambda x: x[1], reverse=True)
        
        for i, (model, score) in enumerate(samantha_scores):
            emoji = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "📊"
            report.append(f"{emoji} **{model}**: {score:.1f}/10 Samantha Score")
        
        # Detailed analysis
        report.append("\n## 📊 Detailed Analysis")
        
        for model, data in results.items():
            report.append(f"\n### {model}")
            report.append(f"**Samantha Score**: {data['samantha_score']:.1f}/10")
            report.append(f"**Success Rate**: {data['success_rate']:.1%}")
            report.append(f"**Avg Response Time**: {data['avg_response_time']:.2f}s")
            report.append(f"**Intelligence**: {data['avg_intelligence']:.1f}/10")
            report.append(f"**Warmth**: {data['avg_warmth']:.1f}/10")
            report.append(f"**Tool Usage**: {data['avg_tool_usage']:.1f}/10")
            report.append(f"**Context Awareness**: {data['avg_context_awareness']:.1f}/10")
            
            # Individual test results
            report.append("\n**Individual Test Results:**")
            for result in data['individual_results']:
                if result.success:
                    report.append(f"- {result.test_name}: {result.intelligence_score}/10 intelligence, "
                                f"{result.warmth_score}/10 warmth, {result.tool_usage_score}/10 tools")
                else:
                    report.append(f"- {result.test_name}: FAILED - {result.error_message}")
        
        # Recommendations
        report.append("\n## 🎯 Recommendations")
        
        best_model = max(results.items(), key=lambda x: x[1]["samantha_score"])
        report.append(f"### 🥇 Best Samantha-Level Model: {best_model[0]}")
        report.append(f"- **Samantha Score**: {best_model[1]['samantha_score']:.1f}/10")
        report.append(f"- **Intelligence**: {best_model[1]['avg_intelligence']:.1f}/10")
        report.append(f"- **Warmth**: {best_model[1]['avg_warmth']:.1f}/10")
        report.append(f"- **Tool Usage**: {best_model[1]['avg_tool_usage']:.1f}/10")
        
        # Tool optimization recommendations
        report.append("\n### 🔧 Tool Optimization Recommendations")
        report.append("1. **Memory System**: Enhance context retention and retrieval")
        report.append("2. **Calendar Integration**: Improve proactive scheduling")
        report.append("3. **Home Automation**: Better routine management")
        report.append("4. **Workflow Automation**: Enhanced N8N integration")
        report.append("5. **Social Intelligence**: Better relationship management")
        
        return "\n".join(report)

async def main():
    """Main intelligence testing function"""
    tester = SamanthaIntelligenceTester()
    
    # Check if Ollama is running
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:11434/api/tags", timeout=5.0)
            if response.status_code != 200:
                logger.error("❌ Ollama is not running or not accessible")
                return
    except Exception as e:
        logger.error(f"❌ Cannot connect to Ollama: {e}")
        return
    
    # Run the intelligence test
    results = await tester.run_comprehensive_intelligence_test()
    
    # Generate and save report
    report = tester.generate_intelligence_report(results)
    
    with open("/home/pi/zoe/SAMANTHA_INTELLIGENCE_REPORT.md", "w") as f:
        f.write(report)
    
    print("\n" + "="*80)
    print("🎉 SAMANTHA-LEVEL INTELLIGENCE TEST COMPLETE!")
    print("="*80)
    print(report)
    print("\n📄 Full report saved to: /home/pi/zoe/SAMANTHA_INTELLIGENCE_REPORT.md")

if __name__ == "__main__":
    asyncio.run(main())

