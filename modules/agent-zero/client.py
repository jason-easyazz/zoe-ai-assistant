"""
Agent Zero Client
=================

Client for communicating with Agent Zero's HTTP API.

Implements the full Agent Zero API protocol for autonomous agent capabilities.
"""

import asyncio
import logging
import httpx
from typing import Dict, Any, Optional
import json
import re
import os

logger = logging.getLogger(__name__)


class AgentZeroClient:
    """
    Client for Agent Zero HTTP API.
    
    Handles communication with the Agent Zero container via /api/message endpoint.
    """
    
    def __init__(self, base_url: str = "http://zoe-agent0:80", api_key: Optional[str] = None):
        """
        Initialize Agent Zero client.
        
        Args:
            base_url: Base URL for Agent Zero service
            api_key: Optional API key for authentication (MCP server token)
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("AGENT_ZERO_API_KEY")
        self.contexts = {}  # user_id -> context_id mapping
        logger.info(f"🤖 Agent Zero client initialized: {self.base_url}")
    
    def get_or_create_context(self, user_id: str) -> Optional[str]:
        """
        Get or create context ID for a user.
        
        Args:
            user_id: User identifier
            
        Returns:
            Context ID for this user (None for first call, Agent Zero will create one)
        """
        return self.contexts.get(user_id)
    
    def _extract_sources(self, text: str) -> list[str]:
        """
        Extract URLs from text as sources.
        
        Args:
            text: Text to extract URLs from
            
        Returns:
            List of URLs found in text
        """
        url_pattern = r'https?://[^\s\)\]<>"]+'
        urls = re.findall(url_pattern, text)
        return urls[:10]  # Limit to 10 sources
    
    async def is_available(self) -> bool:
        """
        Check if Agent Zero is available.
        
        Returns:
            True if Agent Zero is responding, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/")
                return response.status_code == 200
        except Exception as e:
            logger.debug(f"Agent Zero availability check failed: {e}")
            return False
    
    async def send_message(
        self, 
        message: str, 
        user_id: str,
        lifetime_hours: int = 24
    ) -> Dict[str, Any]:
        """
        Send a message to Agent Zero and get response.
        
        Args:
            message: Message/prompt to send
            user_id: User identifier for context
            lifetime_hours: Context lifetime (default: 24 hours)
            
        Returns:
            Response from Agent Zero with context_id and response text
            
        Raises:
            HTTPException: If request fails
        """
        try:
            headers = {
                "Content-Type": "application/json"
            }
            
            # Add API key if configured
            if self.api_key:
                headers["X-API-KEY"] = self.api_key
            
            payload = {
                "message": message,
                "lifetime_hours": lifetime_hours
            }
            
            # Include context_id if we have one for this user
            context_id = self.get_or_create_context(user_id)
            if context_id:
                payload["context_id"] = context_id
            
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/message",
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()
                result = response.json()
                
                # Store context_id for future requests from this user
                if "context_id" in result:
                    self.contexts[user_id] = result["context_id"]
                
                return result
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.error("Agent Zero API key missing or invalid")
                raise Exception("Unauthorized: Check AGENT_ZERO_API_KEY")
            elif e.response.status_code == 404:
                logger.error(f"Agent Zero endpoint not found: {e.request.url}")
                raise Exception("Agent Zero API endpoint not found")
            else:
                logger.error(f"Agent Zero HTTP error: {e}")
                raise
        except httpx.TimeoutException:
            logger.error("Agent Zero request timeout (> 120s)")
            raise Exception("Agent Zero timeout - task may be too complex")
        except Exception as e:
            logger.error(f"Agent Zero request failed: {e}")
            raise
    
    async def research(self, query: str, depth: str = "thorough", user_id: str = "system") -> Dict[str, Any]:
        """
        Execute a research task via Agent Zero.
        
        Args:
            query: Research question
            depth: Research depth (quick, thorough, comprehensive)
            user_id: User identifier for context management
            
        Returns:
            Research results with summary, details, and sources
        """
        logger.info(f"🔍 Research request: {query} (depth: {depth})")
        
        try:
            # Construct research prompt based on depth
            depth_instructions = {
                "quick": "Provide a brief overview with 2-3 key points and 1-2 sources.",
                "thorough": "Provide a comprehensive analysis with detailed findings and 3-5 authoritative sources.",
                "comprehensive": "Provide an exhaustive research report with in-depth analysis, multiple perspectives, and 5-10 sources."
            }
            
            instruction = depth_instructions.get(depth, depth_instructions["thorough"])
            
            prompt = f"""Research the following topic:

{query}

Instructions: {instruction}

Please structure your response as:
1. Summary: Main findings in 2-3 sentences
2. Key Points: Bullet points of important information
3. Sources: List of URLs used for research

Focus on factual, up-to-date information from authoritative sources."""

            # Send to Agent Zero
            result = await self.send_message(prompt, user_id)
            
            # Extract response text
            response_text = result.get("response", "")
            
            # Parse response into structured format
            sources = self._extract_sources(response_text)
            
            # Try to extract summary (first paragraph or first 200 chars)
            lines = response_text.split('\n')
            summary_lines = []
            for line in lines[:5]:  # First 5 lines
                if line.strip():
                    summary_lines.append(line.strip())
                    if len(' '.join(summary_lines)) > 200:
                        break
            
            summary = ' '.join(summary_lines) if summary_lines else response_text[:200] + "..."
            
            return {
                "summary": summary,
                "details": response_text,
                "sources": sources,
                "context_id": result.get("context_id"),
                "status": "success"
            }
        except Exception as e:
            logger.error(f"Research failed: {e}")
            return {
                "summary": f"Research failed: {str(e)}",
                "details": "Agent Zero encountered an error during research.",
                "sources": [],
                "status": "error",
                "error": str(e)
            }
    
    async def plan(self, task: str, user_id: str = "system") -> Dict[str, Any]:
        """
        Create a multi-step plan for a task.
        
        Args:
            task: Task description
            user_id: User identifier for context management
            
        Returns:
            Plan with steps, estimated time, and complexity
        """
        logger.info(f"📋 Planning request: {task}")
        
        try:
            prompt = f"""Create a detailed step-by-step plan for the following task:

{task}

Please provide:
1. Numbered steps with clear actions
2. Estimated time for completion
3. Complexity assessment (simple/medium/complex)
4. Prerequisites or dependencies
5. Potential challenges to watch out for

Structure your response clearly with sections for each of these elements."""

            # Send to Agent Zero
            result = await self.send_message(prompt, user_id)
            response_text = result.get("response", "")
            
            # Parse steps from response (look for numbered list)
            steps = []
            for line in response_text.split('\n'):
                # Match lines like "1. Step one" or "1) Step one"
                if re.match(r'^\s*\d+[\.)]\s+', line):
                    step = re.sub(r'^\s*\d+[\.)]\s+', '', line).strip()
                    if step:
                        steps.append(step)
            
            # Extract estimated time (look for patterns like "2-3 weeks", "3 hours")
            time_pattern = r'(\d+[-–]\d+\s+(?:hour|day|week|month)s?|\d+\s+(?:hour|day|week|month)s?)'
            time_match = re.search(time_pattern, response_text, re.IGNORECASE)
            estimated_time = time_match.group(0) if time_match else "Time estimate not provided"
            
            # Extract complexity
            complexity = "medium"  # default
            if re.search(r'\bsimple\b|\beasy\b|\bstraightforward\b', response_text, re.IGNORECASE):
                complexity = "simple"
            elif re.search(r'\bcomplex\b|\bdifficult\b|\bchallenging\b', response_text, re.IGNORECASE):
                complexity = "complex"
            
            return {
                "steps": steps if steps else ["See detailed plan in response"],
                "details": response_text,
                "estimated_time": estimated_time,
                "complexity": complexity,
                "context_id": result.get("context_id"),
                "status": "success"
            }
        except Exception as e:
            logger.error(f"Planning failed: {e}")
            return {
                "steps": [],
                "details": f"Planning failed: {str(e)}",
                "estimated_time": "Unknown",
                "complexity": "unknown",
                "status": "error",
                "error": str(e)
            }
    
    async def analyze(self, target: str, user_id: str = "system", context: Optional[str] = None) -> Dict[str, Any]:
        """
        Analyze a target (file, system, configuration).
        
        Args:
            target: Target to analyze (description or file path)
            user_id: User identifier for context management
            context: Optional additional context or file content
            
        Returns:
            Analysis results with findings and recommendations
        """
        logger.info(f"🔬 Analysis request: {target}")
        
        try:
            prompt = f"""Analyze the following:

Target: {target}
"""
            
            if context:
                prompt += f"\nContent/Context:\n{context}\n"
            
            prompt += """
Please provide:
1. Overview: What you're analyzing
2. Key Findings: Important observations (use ✅ for good, ⚠️ for warnings, ❌ for issues)
3. Detailed Analysis: In-depth examination
4. Recommendations: Actionable suggestions for improvement

Be specific and practical in your analysis."""

            # Send to Agent Zero
            result = await self.send_message(prompt, user_id)
            response_text = result.get("response", "")
            
            # Parse findings (lines with ✅, ⚠️, ❌)
            findings = []
            for line in response_text.split('\n'):
                if any(marker in line for marker in ['✅', '⚠️', '❌', '•', '-', '*']):
                    finding = line.strip().lstrip('•-*').strip()
                    if finding:
                        findings.append(finding)
            
            # Parse recommendations (numbered or bulleted lists after "recommend")
            recommendations = []
            in_recommendations = False
            for line in response_text.split('\n'):
                if re.search(r'\brecommend', line, re.IGNORECASE):
                    in_recommendations = True
                    continue
                if in_recommendations:
                    if re.match(r'^\s*[\d\-\*•]\s*', line):
                        rec = re.sub(r'^\s*[\d\-\*•)\.]+\s*', '', line).strip()
                        if rec:
                            recommendations.append(rec)
                    elif line.strip() and not re.match(r'^\s*[\d\-\*•]\s*', line):
                        # End of recommendations section
                        break
            
            # Extract summary (first paragraph)
            paragraphs = [p.strip() for p in response_text.split('\n\n') if p.strip()]
            summary = paragraphs[0] if paragraphs else response_text[:200] + "..."
            
            return {
                "analysis": summary,
                "findings": findings if findings else ["See detailed analysis"],
                "recommendations": recommendations if recommendations else ["See detailed analysis"],
                "details": response_text,
                "context_id": result.get("context_id"),
                "status": "success"
            }
        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            return {
                "analysis": f"Analysis failed: {str(e)}",
                "findings": [],
                "recommendations": [],
                "details": "",
                "status": "error",
                "error": str(e)
            }
    
    async def compare(self, item_a: str, item_b: str, user_id: str = "system", criteria: Optional[str] = None) -> Dict[str, Any]:
        """
        Compare two items.
        
        Args:
            item_a: First item to compare
            item_b: Second item to compare
            user_id: User identifier for context management
            criteria: Optional specific criteria to compare
            
        Returns:
            Comparison results with pros/cons and recommendation
        """
        logger.info(f"⚖️ Comparison request: {item_a} vs {item_b}")
        
        try:
            prompt = f"""Compare and contrast the following:

Item A: {item_a}
Item B: {item_b}
"""
            
            if criteria:
                prompt += f"\nCompare specifically on: {criteria}\n"
            
            prompt += """
Please provide:
1. Overview: Brief introduction to both items
2. Pros of Item A: Advantages and strengths
3. Cons of Item A: Disadvantages and weaknesses
4. Pros of Item B: Advantages and strengths
5. Cons of Item B: Disadvantages and weaknesses
6. Key Differences: Main distinctions between them
7. Recommendation: Which to choose and in what scenarios

Be objective and consider multiple use cases."""

            # Send to Agent Zero
            result = await self.send_message(prompt, user_id)
            response_text = result.get("response", "")
            
            # Helper to extract list items from a section
            def extract_section_items(text, section_header):
                items = []
                lines = text.split('\n')
                in_section = False
                
                for line in lines:
                    if re.search(section_header, line, re.IGNORECASE):
                        in_section = True
                        continue
                    if in_section:
                        # Stop at next section or empty lines
                        if line.strip() and re.match(r'^#+\s+|^\d+\.\s+[A-Z]', line):
                            break
                        # Extract list item
                        if re.match(r'^\s*[-\*•]\s+', line):
                            item = re.sub(r'^\s*[-\*•]\s+', '', line).strip()
                            if item:
                                items.append(item)
                return items
            
            # Extract pros and cons
            item_a_pros = extract_section_items(response_text, r'pros?\s+of\s+(item\s+)?a')
            item_a_cons = extract_section_items(response_text, r'cons?\s+of\s+(item\s+)?a')
            item_b_pros = extract_section_items(response_text, r'pros?\s+of\s+(item\s+)?b')
            item_b_cons = extract_section_items(response_text, r'cons?\s+of\s+(item\s+)?b')
            
            # Extract recommendation (usually last paragraph or section)
            recommendation = ""
            if "recommend" in response_text.lower():
                rec_match = re.search(r'recommend.*?:\s*(.+?)(?:\n\n|\n#|\Z)', response_text, re.IGNORECASE | re.DOTALL)
                if rec_match:
                    recommendation = rec_match.group(1).strip()
            
            if not recommendation:
                # Get last paragraph as recommendation
                paragraphs = [p.strip() for p in response_text.split('\n\n') if p.strip()]
                recommendation = paragraphs[-1] if paragraphs else "See detailed comparison for guidance"
            
            return {
                "comparison": response_text[:300] + "..." if len(response_text) > 300 else response_text,
                "item_a_pros": item_a_pros if item_a_pros else [f"See detailed comparison for {item_a} advantages"],
                "item_a_cons": item_a_cons if item_a_cons else [f"See detailed comparison for {item_a} disadvantages"],
                "item_b_pros": item_b_pros if item_b_pros else [f"See detailed comparison for {item_b} advantages"],
                "item_b_cons": item_b_cons if item_b_cons else [f"See detailed comparison for {item_b} disadvantages"],
                "recommendation": recommendation,
                "details": response_text,
                "context_id": result.get("context_id"),
                "status": "success"
            }
        except Exception as e:
            logger.error(f"Comparison failed: {e}")
            return {
                "comparison": f"Comparison failed: {str(e)}",
                "item_a_pros": [],
                "item_a_cons": [],
                "item_b_pros": [],
                "item_b_cons": [],
                "recommendation": "Unable to complete comparison",
                "details": "",
                "status": "error",
                "error": str(e)
            }
    
    def clear_context(self, user_id: str) -> None:
        """
        Clear stored context for a user.
        
        Args:
            user_id: User identifier
        """
        if user_id in self.contexts:
            del self.contexts[user_id]
            logger.info(f"🗑️ Cleared context for user: {user_id}")
    
    def clear_all_contexts(self) -> None:
        """Clear all stored contexts."""
        self.contexts = {}
        logger.info("🗑️ Cleared all contexts")
