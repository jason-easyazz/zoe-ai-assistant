"""
Code Review System for Zack
Validates code before execution to prevent dangerous operations
"""
import ast
import re
import os
import logging
from typing import Dict, List, Tuple, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

class CodeReviewer:
    """Intelligent code reviewer that validates code safety and quality"""
    
    def __init__(self):
        self.dangerous_patterns = [
            # File system operations
            r'os\.remove\(',
            r'os\.rmdir\(',
            r'shutil\.rmtree\(',
            r'os\.unlink\(',
            r'open\([^,)]*,\s*["\']w["\']',
            r'open\([^,)]*,\s*["\']a["\']',
            
            # System operations
            r'subprocess\.run\([^)]*shell=True',
            r'os\.system\(',
            r'os\.popen\(',
            r'eval\(',
            r'exec\(',
            r'__import__\(',
            
            # Database operations
            r'DROP\s+TABLE',
            r'DELETE\s+FROM',
            r'TRUNCATE\s+TABLE',
            r'ALTER\s+TABLE.*DROP',
            
            # Network operations
            r'requests\.get\([^)]*verify=False',
            r'urllib\.request\.urlopen\(',
            
            # Docker operations
            r'docker.*rm\s+-f',
            r'docker.*prune\s+-f',
            r'docker.*system\s+prune',
        ]
        
        self.cursorrules_path = Path(__file__).parent.parent.parent.resolve() / ".cursorrules"
        self.cursorrules = self._load_cursorrules()
    
    def _load_cursorrules(self) -> List[str]:
        """Load .cursorrules file if it exists"""
        if self.cursorrules_path.exists():
            try:
                with open(self.cursorrules_path, 'r') as f:
                    return f.read().split('\n')
            except Exception as e:
                logger.warning(f"Could not load .cursorrules: {e}")
        return []
    
    def review_code(self, code: str, file_path: str = None) -> Dict:
        """Comprehensive code review"""
        issues = []
        suggestions = []
        safety_score = 100
        
        # Basic safety checks
        safety_issues = self._check_dangerous_operations(code)
        issues.extend(safety_issues)
        safety_score -= len(safety_issues) * 10
        
        # Syntax validation
        syntax_issues = self._check_syntax(code)
        issues.extend(syntax_issues)
        safety_score -= len(syntax_issues) * 15
        
        # Cursorrules validation
        cursorrules_issues = self._check_cursorrules(code, file_path)
        issues.extend(cursorrules_issues)
        safety_score -= len(cursorrules_issues) * 5
        
        # Code quality suggestions
        quality_suggestions = self._suggest_improvements(code)
        suggestions.extend(quality_suggestions)
        
        # Security analysis
        security_issues = self._check_security(code)
        issues.extend(security_issues)
        safety_score -= len(security_issues) * 20
        
        # Determine if code should be blocked
        should_block = any(issue.get('severity') == 'critical' for issue in issues)
        
        return {
            "safe": not should_block and safety_score >= 70,
            "safety_score": max(0, safety_score),
            "issues": issues,
            "suggestions": suggestions,
            "should_block": should_block,
            "file_path": file_path,
            "review_summary": self._generate_summary(issues, suggestions, safety_score)
        }
    
    def _check_dangerous_operations(self, code: str) -> List[Dict]:
        """Check for dangerous operations"""
        issues = []
        
        for pattern in self.dangerous_patterns:
            matches = re.finditer(pattern, code, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                line_num = code[:match.start()].count('\n') + 1
                issues.append({
                    "type": "dangerous_operation",
                    "severity": "critical",
                    "message": f"Dangerous operation detected: {match.group()}",
                    "line": line_num,
                    "pattern": pattern,
                    "suggestion": "Consider using safer alternatives or add proper validation"
                })
        
        return issues
    
    def _check_syntax(self, code: str) -> List[Dict]:
        """Check for syntax errors"""
        issues = []
        
        try:
            ast.parse(code)
        except SyntaxError as e:
            issues.append({
                "type": "syntax_error",
                "severity": "critical",
                "message": f"Syntax error: {e.msg}",
                "line": e.lineno,
                "column": e.offset,
                "suggestion": "Fix syntax error before execution"
            })
        except Exception as e:
            issues.append({
                "type": "parse_error",
                "severity": "critical",
                "message": f"Code parsing failed: {str(e)}",
                "line": 1,
                "suggestion": "Check code structure and syntax"
            })
        
        return issues
    
    def _check_cursorrules(self, code: str, file_path: str = None) -> List[Dict]:
        """Check against .cursorrules"""
        issues = []
        
        if not self.cursorrules:
            return issues
        
        # Check for forbidden patterns from .cursorrules
        for rule in self.cursorrules:
            rule = rule.strip()
            if not rule or rule.startswith('#'):
                continue
            
            # Look for "don't" or "avoid" patterns
            if any(word in rule.lower() for word in ['don\'t', 'avoid', 'never', 'forbidden']):
                # Extract the forbidden pattern
                forbidden_patterns = self._extract_forbidden_patterns(rule)
                for pattern in forbidden_patterns:
                    if re.search(pattern, code, re.IGNORECASE):
                        issues.append({
                            "type": "cursorrules_violation",
                            "severity": "warning",
                            "message": f"Violates .cursorrules: {rule}",
                            "line": 1,
                            "suggestion": f"Follow .cursorrules guideline: {rule}"
                        })
        
        return issues
    
    def _extract_forbidden_patterns(self, rule: str) -> List[str]:
        """Extract regex patterns from .cursorrules"""
        patterns = []
        
        # Simple pattern extraction - look for code snippets in quotes
        code_matches = re.findall(r'["\']([^"\']+)["\']', rule)
        for match in code_matches:
            if len(match) > 3:  # Only consider substantial patterns
                patterns.append(re.escape(match))
        
        return patterns
    
    def _check_security(self, code: str) -> List[Dict]:
        """Check for security issues"""
        issues = []
        
        # Check for hardcoded secrets
        secret_patterns = [
            r'password\s*=\s*["\'][^"\']+["\']',
            r'api_key\s*=\s*["\'][^"\']+["\']',
            r'secret\s*=\s*["\'][^"\']+["\']',
            r'token\s*=\s*["\'][^"\']+["\']',
        ]
        
        for pattern in secret_patterns:
            if re.search(pattern, code, re.IGNORECASE):
                issues.append({
                    "type": "security_issue",
                    "severity": "warning",
                    "message": "Potential hardcoded secret detected",
                    "line": 1,
                    "suggestion": "Use environment variables or secure configuration"
                })
        
        # Check for SQL injection risks
        sql_patterns = [
            r'execute\s*\(\s*["\'].*%s.*["\']',
            r'execute\s*\(\s*f["\'].*{.*}.*["\']',
        ]
        
        for pattern in sql_patterns:
            if re.search(pattern, code, re.IGNORECASE):
                issues.append({
                    "type": "security_issue",
                    "severity": "warning",
                    "message": "Potential SQL injection risk",
                    "line": 1,
                    "suggestion": "Use parameterized queries"
                })
        
        return issues
    
    def _suggest_improvements(self, code: str) -> List[Dict]:
        """Suggest code improvements"""
        suggestions = []
        
        # Check for missing error handling
        if 'try:' not in code and any(op in code for op in ['open(', 'subprocess.', 'requests.']):
            suggestions.append({
                "type": "improvement",
                "message": "Consider adding error handling with try/except blocks",
                "suggestion": "Wrap risky operations in try/except blocks"
            })
        
        # Check for long functions
        lines = code.split('\n')
        if len(lines) > 50:
            suggestions.append({
                "type": "improvement",
                "message": "Function is quite long",
                "suggestion": "Consider breaking into smaller functions"
            })
        
        # Check for missing docstrings
        if 'def ' in code and '"""' not in code and "'''" not in code:
            suggestions.append({
                "type": "improvement",
                "message": "Consider adding docstrings to functions",
                "suggestion": "Add docstrings to document function purpose and parameters"
            })
        
        return suggestions
    
    def _generate_summary(self, issues: List[Dict], suggestions: List[Dict], safety_score: int) -> str:
        """Generate a human-readable summary"""
        critical_issues = [i for i in issues if i.get('severity') == 'critical']
        warnings = [i for i in issues if i.get('severity') == 'warning']
        
        summary_parts = []
        
        if critical_issues:
            summary_parts.append(f"❌ {len(critical_issues)} critical issues found - CODE BLOCKED")
        elif warnings:
            summary_parts.append(f"⚠️ {len(warnings)} warnings found")
        else:
            summary_parts.append("✅ No critical issues found")
        
        if suggestions:
            summary_parts.append(f"💡 {len(suggestions)} improvement suggestions")
        
        summary_parts.append(f"Safety Score: {safety_score}/100")
        
        return " | ".join(summary_parts)

# Global instance
code_reviewer = CodeReviewer()
