# File Tagging System for Unused Files

## Overview
The File Tagging System automatically identifies files that haven't been accessed in the last week and prepares them for review before retirement. This helps keep the project clean and organized.

## How It Works

### 1. Automatic Tagging
- **Schedule**: Runs every Sunday at 2 AM via cron job
- **Criteria**: Files not accessed in the last 7 days
- **Exclusions**: Essential files, databases, logs, cache files
- **Output**: Creates `scripts/maintenance/file_tags.json`

### 2. Manual Review
- Review tagged files before retirement
- Essential files are automatically excluded:
  - `README.md`, `requirements.txt`, `main.py`
  - Core service files (`chat.py`, `auth.py`, etc.)
  - Database files, logs, and cache files

### 3. Retirement Process
- Manual confirmation required
- Approved files are deleted from the working tree
- Original tags file updated
- Committed files remain recoverable from git history

## Usage

### Setup (One-time)
```bash
# Make scripts executable and setup cron job
chmod +x scripts/maintenance/setup_file_tagging.sh
./scripts/maintenance/setup_file_tagging.sh
```

### Manual Operations
```bash
# Tag unused files manually
python3 scripts/maintenance/tag_unused_files.py

# Review and retire tagged files
python3 scripts/maintenance/archive_tagged_files.py
```

### Check Status
```bash
# View current tags
cat scripts/maintenance/file_tags.json

# View tagging logs
tail scripts/maintenance/tagging.log
```

## File Categories

### Automatically Excluded
- `.git/`, `.gitignore`, `mcp_test_env/`
- `__pycache__/`, `*.pyc`, `*.log`
- `*.db`, `*.db-shm`, `*.db-wal`
- `node_modules/`, `.env`, `*.key`, `*.pem`

### Essential Files (Never Tagged)
- `README.md`, `requirements.txt`, `Dockerfile`
- `main.py`, `chat.py`, `auth.py`
- `calendar.py`, `lists.py`, `journal.py`

### Tagged for Review
- Old documentation files
- Unused configuration files
- Temporary or test files
- Legacy code files

## Safety Features
- **Manual confirmation** required for retirement
- **Restore capability** from git history for committed files
- **Essential files** automatically protected
- **Logging** of all operations

## Best Practices
1. **Review weekly** tagged files before retirement
2. **Keep essential files** in core directories
3. **Use descriptive names** for better identification
4. **Retire in batches** rather than individual files
5. **Document why files were retired in the commit message**

## Troubleshooting

### Scripts Not Working
```bash
# Check permissions
ls -la scripts/maintenance/tag_unused_files.py

# Make executable
chmod +x scripts/maintenance/*.py
```

### Cron Job Issues
```bash
# Check cron jobs
crontab -l

# Check logs
tail /var/log/syslog | grep CRON
```

### Recovery
```bash
# Find prior versions of a retired file
git log -- path/to/file.py

# Restore a specific committed version
git show <commit>:path/to/file.py > path/to/file.py
```

## Configuration
Edit `tag_unused_files.py` to modify:
- Exclude patterns
- Essential files list
- Time threshold (currently 7 days)
- Retirement behavior
