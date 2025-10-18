#!/usr/bin/env python3
"""
Auto-CHANGELOG Generator for Zoe AI Assistant
Parses conventional commits and generates CHANGELOG.md entries

Usage:
    # Generate changelog since last tag
    python3 tools/generators/generate_changelog.py
    
    # Generate changelog for specific version
    python3 tools/generators/generate_changelog.py --version v2.4.0
    
    # Preview without writing
    python3 tools/generators/generate_changelog.py --dry-run
"""

import subprocess
import re
import argparse
from datetime import datetime
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent.parent
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"

# Conventional commit types and their display names
COMMIT_TYPES = {
    'feat': ('✨ Features', []),
    'fix': ('🐛 Bug Fixes', []),
    'db': ('💾 Database Changes', []),
    'perf': ('⚡ Performance Improvements', []),
    'refactor': ('♻️ Code Refactoring', []),
    'docs': ('📚 Documentation', []),
    'test': ('✅ Tests', []),
    'build': ('📦 Build System', []),
    'ci': ('👷 CI/CD', []),
    'chore': ('🔧 Chores', []),
    'style': ('💄 Styling', []),
}

def get_git_tags():
    """Get all git tags sorted by date"""
    result = subprocess.run(
        ['git', 'tag', '-l', '--sort=-creatordate'],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True
    )
    return result.stdout.strip().split('\n') if result.stdout.strip() else []

def get_commits_since_tag(tag=None):
    """Get commits since specified tag (or all if no tag)"""
    if tag:
        cmd = ['git', 'log', f'{tag}..HEAD', '--pretty=format:%H|%s|%an|%ad', '--date=short']
    else:
        cmd = ['git', 'log', '--pretty=format:%H|%s|%an|%ad', '--date=short']
    
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    
    commits = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        parts = line.split('|')
        if len(parts) == 4:
            commits.append({
                'hash': parts[0][:7],
                'message': parts[1],
                'author': parts[2],
                'date': parts[3]
            })
    
    return commits

def parse_conventional_commit(message):
    """Parse a conventional commit message"""
    # Pattern: type(scope): description
    pattern = r'^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|db)(\([a-z0-9-]+\))?: (.+)$'
    match = re.match(pattern, message, re.IGNORECASE)
    
    if match:
        return {
            'type': match.group(1).lower(),
            'scope': match.group(2)[1:-1] if match.group(2) else None,
            'description': match.group(3)
        }
    return None

def categorize_commits(commits):
    """Categorize commits by type"""
    categorized = defaultdict(list)
    
    for commit in commits:
        parsed = parse_conventional_commit(commit['message'])
        if parsed:
            commit_type = parsed['type']
            scope = f"**{parsed['scope']}**: " if parsed['scope'] else ""
            description = parsed['description']
            
            categorized[commit_type].append({
                'text': f"{scope}{description}",
                'hash': commit['hash']
            })
        else:
            # Uncategorized commits
            categorized['other'].append({
                'text': commit['message'],
                'hash': commit['hash']
            })
    
    return categorized

def generate_changelog_entry(version, date, categorized_commits):
    """Generate a changelog entry"""
    lines = []
    
    # Header
    lines.append(f"## [{version}] - {date}")
    lines.append("")
    
    # Add categorized commits
    for commit_type, (display_name, _) in COMMIT_TYPES.items():
        if commit_type in categorized_commits:
            lines.append(f"### {display_name}")
            lines.append("")
            for commit in categorized_commits[commit_type]:
                lines.append(f"- {commit['text']} ({commit['hash']})")
            lines.append("")
    
    # Add other commits if any
    if 'other' in categorized_commits:
        lines.append("### 🔀 Other Changes")
        lines.append("")
        for commit in categorized_commits['other']:
            lines.append(f"- {commit['text']} ({commit['hash']})")
        lines.append("")
    
    return '\n'.join(lines)

def read_existing_changelog():
    """Read existing CHANGELOG.md"""
    if CHANGELOG_PATH.exists():
        return CHANGELOG_PATH.read_text()
    return "# Changelog - Zoe AI Assistant\n\nAll notable changes to this project will be documented in this file.\n\n"

def write_changelog(content):
    """Write updated CHANGELOG.md"""
    CHANGELOG_PATH.write_text(content)

def main():
    parser = argparse.ArgumentParser(description="Generate CHANGELOG from conventional commits")
    parser.add_argument("--version", help="Version number (e.g., v2.4.0)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()
    
    # Get version
    if args.version:
        version = args.version
    else:
        # Auto-increment from last tag
        tags = get_git_tags()
        if tags and tags[0]:
            last_version = tags[0]
            # Simple increment: v2.3.1 -> v2.4.0
            match = re.match(r'v?(\d+)\.(\d+)\.(\d+)', last_version)
            if match:
                major, minor, patch = map(int, match.groups())
                version = f"v{major}.{minor + 1}.0"
            else:
                version = "v2.4.0"
        else:
            version = "v2.4.0"
    
    # Get date
    date = datetime.now().strftime("%Y-%m-%d")
    
    # Get commits since last tag
    tags = get_git_tags()
    last_tag = tags[0] if tags and tags[0] else None
    
    print(f"Generating CHANGELOG for {version}")
    if last_tag:
        print(f"Commits since: {last_tag}")
    else:
        print("No previous tags found, using all commits")
    
    commits = get_commits_since_tag(last_tag)
    print(f"Found {len(commits)} commits")
    
    if not commits:
        print("No new commits to add to CHANGELOG")
        return
    
    # Categorize commits
    categorized = categorize_commits(commits)
    
    # Generate entry
    entry = generate_changelog_entry(version, date, categorized)
    
    if args.dry_run:
        print("\n" + "="*60)
        print("CHANGELOG ENTRY (DRY RUN)")
        print("="*60)
        print(entry)
        return
    
    # Read existing changelog
    existing = read_existing_changelog()
    
    # Insert new entry after header
    lines = existing.split('\n')
    header_end = 0
    for i, line in enumerate(lines):
        if line.startswith('## ['):
            header_end = i
            break
        if i > 10:  # Safety limit
            header_end = i
            break
    
    if header_end == 0:
        # No existing entries, add after header
        new_changelog = existing.rstrip() + "\n\n" + entry
    else:
        # Insert before first entry
        new_changelog = '\n'.join(lines[:header_end]) + '\n\n' + entry + '\n' + '\n'.join(lines[header_end:])
    
    # Write changelog
    write_changelog(new_changelog)
    print(f"\n✓ CHANGELOG updated with {version}")
    print(f"  Added {sum(len(commits) for commits in categorized.values())} commits")
    print(f"\nNext steps:")
    print(f"  1. Review CHANGELOG.md")
    print(f"  2. git add CHANGELOG.md")
    print(f"  3. git commit -m 'chore: Update CHANGELOG for {version}'")
    print(f"  4. git tag -a {version} -m 'Release {version}'")

if __name__ == "__main__":
    main()



