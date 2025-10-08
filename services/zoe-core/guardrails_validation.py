"""
Guardrails Validation System for Zoe AI Assistant
Content and code safety validation with PII detection and dangerous code blocking
"""
import re
import json
import time
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import hashlib

logger = logging.getLogger(__name__)

class GuardrailsValidator:
    """Lightweight validation system for content and code safety"""
    
    def __init__(self):
        self.pii_patterns = {
            "email": r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            "phone": r'(\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}',
            "ssn": r'\b\d{3}-?\d{2}-?\d{4}\b',
            "credit_card": r'\b(?:\d{4}[-\s]?){3}\d{4}\b',
            "ip_address": r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
            "mac_address": r'\b([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})\b',
            "url": r'https?://(?:[-\w.])+(?:[:\d]+)?(?:/(?:[\w/_.])*(?:\?(?:[\w&=%.])*)?(?:#(?:[\w.])*)?)?',
            "api_key": r'(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*["\']?[a-zA-Z0-9+/=]{20,}["\']?',
            "jwt": r'eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*'
        }
        
        self.dangerous_code_patterns = {
            "file_operations": [
                r'os\.remove\(',
                r'os\.unlink\(',
                r'shutil\.rmtree\(',
                r'os\.system\(',
                r'subprocess\.call\(',
                r'eval\(',
                r'exec\(',
                r'__import__\(',
                r'open\([^)]*[\'"]w[\'"]',
                r'open\([^)]*[\'"]a[\'"]'
            ],
            "network_operations": [
                r'requests\.get\(',
                r'requests\.post\(',
                r'urllib\.request\.urlopen\(',
                r'socket\.connect\(',
                r'http\.client\.HTTPConnection\('
            ],
            "system_operations": [
                r'subprocess\.run\(',
                r'subprocess\.Popen\(',
                r'os\.popen\(',
                r'os\.chmod\(',
                r'os\.chown\(',
                r'os\.kill\(',
                r'os\.killpg\('
            ],
            "database_operations": [
                r'DROP\s+TABLE',
                r'DELETE\s+FROM',
                r'TRUNCATE\s+TABLE',
                r'ALTER\s+TABLE',
                r'DROP\s+DATABASE'
            ]
        }
        
        self.sensitive_keywords = [
            "password", "secret", "key", "token", "credential", "auth",
            "private", "confidential", "internal", "admin", "root"
        ]
        
        self.validation_cache = {}
        self.cache_ttl = 300  # 5 minutes
        
    def validate_content(self, content: str, content_type: str = "text") -> Dict[str, Any]:
        """Validate content for safety and PII"""
        start_time = time.time()
        
        # Check cache first
        cache_key = hashlib.md5(f"{content}_{content_type}".encode()).hexdigest()
        if cache_key in self.validation_cache:
            cached_result = self.validation_cache[cache_key]
            if time.time() - cached_result["timestamp"] < self.cache_ttl:
                return cached_result["result"]
        
        result = {
            "is_safe": True,
            "warnings": [],
            "errors": [],
            "pii_detected": [],
            "dangerous_patterns": [],
            "sensitive_keywords": [],
            "validation_time_ms": 0
        }
        
        try:
            # PII Detection
            pii_detected = self._detect_pii(content)
            if pii_detected:
                result["pii_detected"] = pii_detected
                result["warnings"].append("PII detected in content")
                result["is_safe"] = False
            
            # Sensitive Keywords Detection
            sensitive_found = self._detect_sensitive_keywords(content)
            if sensitive_found:
                result["sensitive_keywords"] = sensitive_found
                result["warnings"].append("Sensitive keywords detected")
            
            # Dangerous Code Pattern Detection (if content is code)
            if content_type == "code":
                dangerous_patterns = self._detect_dangerous_code(content)
                if dangerous_patterns:
                    result["dangerous_patterns"] = dangerous_patterns
                    result["errors"].append("Dangerous code patterns detected")
                    result["is_safe"] = False
            
            # Content Length Check
            if len(content) > 100000:  # 100KB limit
                result["warnings"].append("Content exceeds recommended length")
            
            # Profanity/Inappropriate Content (basic check)
            inappropriate = self._detect_inappropriate_content(content)
            if inappropriate:
                result["warnings"].append("Potentially inappropriate content detected")
            
        except Exception as e:
            logger.error(f"Validation error: {e}")
            result["errors"].append(f"Validation failed: {str(e)}")
            result["is_safe"] = False
        
        # Calculate validation time
        result["validation_time_ms"] = round((time.time() - start_time) * 1000, 2)
        
        # Cache result
        self.validation_cache[cache_key] = {
            "result": result,
            "timestamp": time.time()
        }
        
        return result
    
    def _detect_pii(self, content: str) -> List[Dict[str, Any]]:
        """Detect personally identifiable information"""
        pii_detected = []
        
        for pii_type, pattern in self.pii_patterns.items():
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                pii_detected.append({
                    "type": pii_type,
                    "count": len(matches),
                    "matches": matches[:5]  # Limit to first 5 matches
                })
        
        return pii_detected
    
    def _detect_sensitive_keywords(self, content: str) -> List[str]:
        """Detect sensitive keywords that might indicate confidential information"""
        content_lower = content.lower()
        sensitive_found = []
        
        for keyword in self.sensitive_keywords:
            if keyword in content_lower:
                sensitive_found.append(keyword)
        
        return sensitive_found
    
    def _detect_dangerous_code(self, code: str) -> List[Dict[str, Any]]:
        """Detect dangerous code patterns"""
        dangerous_found = []
        
        for category, patterns in self.dangerous_code_patterns.items():
            for pattern in patterns:
                matches = re.findall(pattern, code, re.IGNORECASE)
                if matches:
                    dangerous_found.append({
                        "category": category,
                        "pattern": pattern,
                        "count": len(matches),
                        "matches": matches[:3]  # Limit to first 3 matches
                    })
        
        return dangerous_found
    
    def _detect_inappropriate_content(self, content: str) -> bool:
        """Basic inappropriate content detection"""
        inappropriate_words = [
            "hate", "violence", "harassment", "discrimination",
            "illegal", "harmful", "dangerous"
        ]
        
        content_lower = content.lower()
        for word in inappropriate_words:
            if word in content_lower:
                return True
        
        return False
    
    def validate_code_execution(self, code: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Validate code before execution"""
        result = {
            "can_execute": True,
            "warnings": [],
            "errors": [],
            "suggestions": [],
            "risk_level": "low"
        }
        
        try:
            # Check for dangerous patterns
            dangerous_patterns = self._detect_dangerous_code(code)
            if dangerous_patterns:
                result["can_execute"] = False
                result["errors"].append("Code contains dangerous operations")
                result["risk_level"] = "high"
                
                # Provide suggestions for safer alternatives
                for pattern_info in dangerous_patterns:
                    if pattern_info["category"] == "file_operations":
                        result["suggestions"].append("Consider using read-only operations or file existence checks")
                    elif pattern_info["category"] == "system_operations":
                        result["suggestions"].append("Avoid system-level operations in user code")
                    elif pattern_info["category"] == "database_operations":
                        result["suggestions"].append("Use parameterized queries and avoid DDL operations")
            
            # Check for PII in code
            pii_detected = self._detect_pii(code)
            if pii_detected:
                result["warnings"].append("Code contains PII - consider removing sensitive data")
                result["risk_level"] = "medium"
            
            # Check code length and complexity
            if len(code) > 10000:
                result["warnings"].append("Code is very long - consider breaking into smaller functions")
                result["risk_level"] = "medium"
            
            # Check for imports
            imports = re.findall(r'^import\s+(\w+)', code, re.MULTILINE)
            dangerous_imports = ["os", "subprocess", "sys", "shutil"]
            dangerous_imports_found = [imp for imp in imports if imp in dangerous_imports]
            
            if dangerous_imports_found:
                result["warnings"].append(f"Dangerous imports detected: {dangerous_imports_found}")
                result["risk_level"] = "medium"
            
        except Exception as e:
            logger.error(f"Code validation error: {e}")
            result["can_execute"] = False
            result["errors"].append(f"Validation failed: {str(e)}")
        
        return result
    
    def generate_safe_prompt(self, original_prompt: str, validation_result: Dict[str, Any]) -> str:
        """Generate a safer version of the prompt based on validation results"""
        if validation_result["is_safe"]:
            return original_prompt
        
        safe_prompt = original_prompt
        
        # Remove PII
        for pii_info in validation_result.get("pii_detected", []):
            for match in pii_info["matches"]:
                safe_prompt = safe_prompt.replace(match, f"[{pii_info['type'].upper()}_REDACTED]")
        
        # Add safety instructions
        safety_instructions = [
            "Please ensure your response does not contain:",
            "- Personally identifiable information (PII)",
            "- Dangerous code operations",
            "- Sensitive or confidential data"
        ]
        
        if validation_result.get("dangerous_patterns"):
            safety_instructions.append("- System-level operations")
        
        safe_prompt = f"{safe_prompt}\n\nSafety Guidelines:\n" + "\n".join(safety_instructions)
        
        return safe_prompt
    
    def get_validation_stats(self) -> Dict[str, Any]:
        """Get validation statistics"""
        return {
            "cache_size": len(self.validation_cache),
            "pii_patterns_count": len(self.pii_patterns),
            "dangerous_patterns_count": sum(len(patterns) for patterns in self.dangerous_code_patterns.values()),
            "sensitive_keywords_count": len(self.sensitive_keywords),
            "cache_ttl_seconds": self.cache_ttl
        }
    
    def clear_cache(self) -> bool:
        """Clear validation cache"""
        try:
            self.validation_cache.clear()
            return True
        except Exception as e:
            logger.error(f"Failed to clear cache: {e}")
            return False
    
    def update_patterns(self, pattern_type: str, patterns: Dict[str, Any]) -> bool:
        """Update validation patterns"""
        try:
            if pattern_type == "pii":
                self.pii_patterns.update(patterns)
            elif pattern_type == "dangerous_code":
                self.dangerous_code_patterns.update(patterns)
            elif pattern_type == "sensitive_keywords":
                self.sensitive_keywords.extend(patterns)
            else:
                return False
            
            return True
        except Exception as e:
            logger.error(f"Failed to update patterns: {e}")
            return False

# Global instance
guardrails_validator = GuardrailsValidator()
