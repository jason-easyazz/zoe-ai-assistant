#!/usr/bin/env python3
"""
Validate commit message follows Conventional Commits format
Used by commit-msg git hook
"""
import sys
import re

def validate_commit_message(commit_msg_file):
    """Validate commit message format"""
    
    with open(commit_msg_file, 'r') as f:
        message = f.read().strip()
    
    # Allow merge commits
    if message.startswith("Merge"):
        return True
    
    # Conventional Commits pattern
    # type(scope): description
    # OR
    # type: description
    pattern = r'^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|db|revert)(\([a-z0-9\-]+\))?: .{10,}'
    
    if not re.match(pattern, message, re.IGNORECASE):
        print("\n" + "="*70)
        print("❌ COMMIT MESSAGE FORMAT ERROR")
        print("="*70)
        print("\nYour commit message does not follow Conventional Commits format.\n")
        print("Required format:")
        print("  type(scope): description")
        print("\nValid types:")
        print("  feat, fix, docs, style, refactor, perf, test, build, ci, chore, db, revert")
        print("\nExamples:")
        print("  feat(chat): add voice command support")
        print("  fix(auth): correct login validation")
        print("  docs: update API documentation")
        print("  db: add indexes for faster queries")
        print("\nYour message:")
        print(f"  {message[:100]}...")
        print("\nDescription must be at least 10 characters.")
        print("\nSee: docs/guides/CHANGE_MANAGEMENT.md for details")
        print("="*70 + "\n")
        return False
    
    return True

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: validate_commit_message.py <commit-msg-file>")
        sys.exit(1)
    
    success = validate_commit_message(sys.argv[1])
    sys.exit(0 if success else 1)

