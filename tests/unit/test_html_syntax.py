#!/usr/bin/env python3
"""
HTML Syntax Validation Test
Checks for common JavaScript syntax errors in HTML files
"""
import os
from pathlib import Path

# Auto-detect project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
import re
from typing import List, Dict, Any

class HTMLSyntaxValidator:
    def __init__(self, html_dir: str = str(PROJECT_ROOT / "services/zoe-ui/dist")):
        self.html_dir = html_dir
        self.errors = []
    
    def check_quotes_in_js(self, content: str, filename: str) -> List[Dict[str, Any]]:
        """Check for unterminated quotes in JavaScript"""
        errors = []
        lines = content.split('\n')
        
        for i, line in enumerate(lines, 1):
            # Look for unterminated single quotes in JavaScript contexts
            if 'input.value = \';' in line:
                errors.append({
                    'file': filename,
                    'line': i,
                    'type': 'unterminated_quote',
                    'message': 'Unterminated single quote in JavaScript',
                    'content': line.strip()
                })
        
        return errors
    
    def check_basic_syntax(self, content: str, filename: str) -> List[Dict[str, Any]]:
        """Check for basic JavaScript syntax errors"""
        errors = []
        
        # Check for common syntax issues
        syntax_patterns = [
            (r'input\.value = \';', 'Unterminated single quote'),
            (r'console\.log\([^)]*$', 'Unclosed console.log'),
            (r'function\s+\w+\s*\([^)]*$', 'Unclosed function definition'),
            (r'if\s*\([^)]*$', 'Unclosed if statement'),
        ]
        
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            for pattern, message in syntax_patterns:
                if re.search(pattern, line):
                    errors.append({
                        'file': filename,
                        'line': i,
                        'type': 'syntax_error',
                        'message': message,
                        'content': line.strip()
                    })
        
        return errors
    
    def validate_file(self, filepath: str) -> List[Dict[str, Any]]:
        """Validate a single HTML file"""
        errors = []
        filename = os.path.basename(filepath)
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Check for quote issues
            errors.extend(self.check_quotes_in_js(content, filename))
            
            # Check for basic syntax issues
            errors.extend(self.check_basic_syntax(content, filename))
            
        except Exception as e:
            errors.append({
                'file': filename,
                'line': 0,
                'type': 'file_error',
                'message': f'Error reading file: {str(e)}',
                'content': ''
            })
        
        return errors
    
    def validate_all_html_files(self) -> Dict[str, Any]:
        """Validate all HTML files in the directory"""
        print("🔍 Validating HTML files for syntax errors...")
        
        html_files = []
        for root, dirs, files in os.walk(self.html_dir):
            for file in files:
                if file.endswith('.html'):
                    html_files.append(os.path.join(root, file))
        
        all_errors = []
        for filepath in html_files:
            errors = self.validate_file(filepath)
            all_errors.extend(errors)
        
        return {
            'total_files': len(html_files),
            'files_with_errors': len(set(error['file'] for error in all_errors)),
            'total_errors': len(all_errors),
            'errors': all_errors,
            'files_checked': [os.path.basename(f) for f in html_files]
        }
    
    def print_results(self, results: Dict[str, Any]):
        """Print validation results"""
        print("\n" + "=" * 60)
        print("📋 HTML SYNTAX VALIDATION RESULTS")
        print("=" * 60)
        
        print(f"📁 Files checked: {results['total_files']}")
        print(f"❌ Files with errors: {results['files_with_errors']}")
        print(f"🚨 Total errors: {results['total_errors']}")
        
        if results['errors']:
            print(f"\n🔍 Error Details:")
            for error in results['errors']:
                print(f"   📄 {error['file']}:{error['line']} - {error['message']}")
                if error['content']:
                    print(f"      💬 {error['content']}")
        else:
            print(f"\n✅ No syntax errors found!")
        
        print("\n" + "=" * 60)

def main():
    """Main validation function"""
    validator = HTMLSyntaxValidator()
    results = validator.validate_all_html_files()
    validator.print_results(results)
    
    return results

if __name__ == "__main__":
    main()

