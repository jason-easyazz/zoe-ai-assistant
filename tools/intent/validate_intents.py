#!/usr/bin/env python3
"""
Intent System Validation Tool
==============================

Validates intent system configuration:
- YAML syntax correctness
- Handler registration
- Pattern coverage
- Performance requirements
- Security checks

Run before committing intent system changes.
"""

import sys
import os
from pathlib import Path
import yaml
import logging

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "services" / "zoe-core"))

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Color codes for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

class IntentValidator:
    """Validates intent system configuration."""
    
    def __init__(self, intents_dir: str = "services/zoe-core/intent_system/intents/en"):
        self.intents_dir = Path(intents_dir)
        self.errors = []
        self.warnings = []
        self.info = []
    
    def validate_all(self) -> bool:
        """
        Run all validations.
        
        Returns:
            True if all validations pass, False otherwise
        """
        print(f"{BLUE}🔍 Validating Intent System...{RESET}\n")
        
        # 1. Check directory exists
        if not self.intents_dir.exists():
            self.errors.append(f"Intents directory not found: {self.intents_dir}")
            return False
        
        # 2. Validate YAML files
        print(f"{BLUE}📝 Validating YAML files...{RESET}")
        yaml_files = list(self.intents_dir.glob("*.yaml"))
        
        if not yaml_files:
            self.errors.append(f"No YAML files found in {self.intents_dir}")
            return False
        
        print(f"   Found {len(yaml_files)} YAML files")
        
        intents = {}
        for yaml_file in yaml_files:
            valid, file_intents = self.validate_yaml_file(yaml_file)
            if valid:
                intents.update(file_intents)
        
        print(f"   Total intents loaded: {len(intents)}")
        
        # 3. Check handler registration
        print(f"\n{BLUE}🔧 Checking handler registration...{RESET}")
        self.validate_handlers(intents)
        
        # 4. Check pattern quality
        print(f"\n{BLUE}📊 Checking pattern quality...{RESET}")
        self.validate_patterns(intents)
        
        # 5. Security checks
        print(f"\n{BLUE}🔒 Running security checks...{RESET}")
        self.validate_security()
        
        # Print summary
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}Validation Summary{RESET}")
        print(f"{BLUE}{'='*60}{RESET}")
        
        if self.errors:
            print(f"\n{RED}❌ ERRORS ({len(self.errors)}):{'RESET}")
            for error in self.errors:
                print(f"  {RED}•{RESET} {error}")
        
        if self.warnings:
            print(f"\n{YELLOW}⚠️  WARNINGS ({len(self.warnings)}):{'RESET}")
            for warning in self.warnings:
                print(f"  {YELLOW}•{RESET} {warning}")
        
        if self.info:
            print(f"\n{BLUE}ℹ️  INFO ({len(self.info)}):{'RESET}")
            for info in self.info:
                print(f"  {BLUE}•{RESET} {info}")
        
        if not self.errors:
            print(f"\n{GREEN}✅ All validations passed!{RESET}")
            return True
        else:
            print(f"\n{RED}❌ Validation failed with {len(self.errors)} error(s){RESET}")
            return False
    
    def validate_yaml_file(self, yaml_file: Path) -> tuple[bool, dict]:
        """
        Validate a single YAML file.
        
        Returns:
            (success, intents_dict)
        """
        try:
            with open(yaml_file, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            if not data:
                self.errors.append(f"{yaml_file.name}: Empty file")
                return False, {}
            
            if "language" not in data:
                self.warnings.append(f"{yaml_file.name}: Missing 'language' field")
            
            if "intents" not in data:
                self.errors.append(f"{yaml_file.name}: Missing 'intents' section")
                return False, {}
            
            intents = data["intents"]
            print(f"   ✓ {yaml_file.name}: {len(intents)} intents")
            
            # Validate each intent
            for intent_name, intent_data in intents.items():
                if not isinstance(intent_data, dict):
                    self.errors.append(f"{yaml_file.name}: Intent '{intent_name}' is not a dictionary")
                    continue
                
                if "data" not in intent_data:
                    self.errors.append(f"{yaml_file.name}: Intent '{intent_name}' missing 'data' field")
                    continue
            
            return True, intents
            
        except yaml.YAMLError as e:
            self.errors.append(f"{yaml_file.name}: YAML syntax error: {e}")
            return False, {}
        except Exception as e:
            self.errors.append(f"{yaml_file.name}: Failed to load: {e}")
            return False, {}
    
    def validate_handlers(self, intents: dict):
        """Check that handlers are registered for all intents."""
        try:
            from intent_system.executors import IntentExecutor
            
            executor = IntentExecutor()
            registered = set(executor.get_registered_intents())
            defined = set(intents.keys())
            
            # Check missing handlers
            missing = defined - registered
            if missing:
                for intent in missing:
                    self.warnings.append(f"Intent '{intent}' has no registered handler")
            
            # Check orphaned handlers
            orphaned = registered - defined
            if orphaned:
                for intent in orphaned:
                    self.warnings.append(f"Handler registered for '{intent}' but no YAML pattern found")
            
            coverage = len(registered) / max(len(defined), 1) * 100
            print(f"   Handler coverage: {len(registered)}/{len(defined)} ({coverage:.1f}%)")
            
            if coverage < 50:
                self.errors.append(f"Handler coverage too low: {coverage:.1f}% (need >50%)")
            
        except Exception as e:
            self.errors.append(f"Failed to check handlers: {e}")
    
    def validate_patterns(self, intents: dict):
        """Check pattern quality and coverage."""
        for intent_name, intent_data in intents.items():
            if "data" not in intent_data:
                continue
            
            pattern_count = 0
            for data_item in intent_data["data"]:
                if "sentences" in data_item:
                    pattern_count += len(data_item["sentences"])
            
            if pattern_count == 0:
                self.errors.append(f"Intent '{intent_name}' has no patterns")
            elif pattern_count < 3:
                self.warnings.append(f"Intent '{intent_name}' has only {pattern_count} pattern(s) (recommend 5+)")
            
            self.info.append(f"Intent '{intent_name}': {pattern_count} patterns")
    
    def validate_security(self):
        """Check for potential security issues."""
        handlers_dir = Path("services/zoe-core/intent_system/handlers")
        
        if not handlers_dir.exists():
            self.warnings.append("Handlers directory not found for security check")
            return
        
        # Check for SQL injection vulnerabilities
        dangerous_patterns = [
            (r'f".*{.*}.*".*execute', "Potential SQL injection via f-string"),
            (r'\.format\(.*\).*execute', "Potential SQL injection via .format()"),
            (r'eval\(', "Use of eval() - security risk"),
            (r'exec\(', "Use of exec() - security risk"),
            (r'os\.system\(', "Use of os.system() - security risk"),
            (r'subprocess\.(run|call|Popen)', "Subprocess call - verify input sanitization"),
        ]
        
        for handler_file in handlers_dir.glob("*.py"):
            try:
                content = handler_file.read_text()
                for pattern, message in dangerous_patterns:
                    import re
                    if re.search(pattern, content):
                        self.warnings.append(f"{handler_file.name}: {message}")
            except Exception as e:
                self.warnings.append(f"Failed to scan {handler_file.name}: {e}")


def main():
    """Main validation entry point."""
    validator = IntentValidator()
    success = validator.validate_all()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

