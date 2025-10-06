# Zoe File Tagging System

## Overview

The Zoe File Tagging System is a smart file management solution that helps maintain a clean, organized repository while preserving important files during automated cleanups. It uses a tagging system to categorize files and an intelligent cleanup script that respects these tags.

## Components

### 1. Tagging File (`.file-tags`)
Located at `/home/pi/zoe/.file-tags`, this file contains all file tags and their categories.

**Format:**
```
<file_path> | <tag1,tag2,tag3> | <description>
```

### 2. Tagging Utility (`tag-files.sh`)
Script for managing file tags with commands to add, remove, list, and search tags.

### 3. Smart Cleanup Script (`smart-cleanup.sh`)
Automated cleanup script that moves files to backup directories based on their tags.

## Available Tags

| Tag | Description | Cleanup Action |
|-----|-------------|----------------|
| `CORE` | Essential system files | **PRESERVE** - Never move or delete |
| `CONFIG` | Configuration files | **PRESERVE** - Critical for operation |
| `DOCS` | Documentation files | **PRESERVE** - Important for project understanding |
| `SCRIPTS` | Important scripts | **PRESERVE** - Core functionality scripts |
| `DATA` | Data files and databases | **PRESERVE** - Application data |
| `SERVICES` | Service files and APIs | **PRESERVE** - Core service components |
| `UI` | User interface files | **PRESERVE** - Frontend components |
| `SECURITY` | Security-related files | **PRESERVE** - SSL, keys, auth |
| `DEPLOYMENT` | Deployment files | **PRESERVE** - Docker, deployment configs |
| `TEMP` | Temporary files | **CLEANUP** - Move to `backups/temp-files/` |
| `OLD` | Old versions | **BACKUP** - Move to `backups/old-files/` |
| `DEV` | Development files | **BACKUP** - Move to `backups/dev-files/` |

## Usage

### Tagging Files

#### Add a tag to a specific file:
```bash
./scripts/permanent/maintenance/tag-files.sh -t CORE -d "Main application entry point" services/zoe-core/main.py
```

#### Tag multiple files by pattern:
```bash
./scripts/permanent/maintenance/tag-files.sh -t TEMP -d "Temporary files" "*.tmp"
```

#### Remove tags from a file:
```bash
./scripts/permanent/maintenance/tag-files.sh -r services/zoe-core/main.py
```

#### List all tagged files:
```bash
./scripts/permanent/maintenance/tag-files.sh -l
```

#### Search for tagged files:
```bash
./scripts/permanent/maintenance/tag-files.sh -s "*.py"
```

### Running Smart Cleanup

#### Full cleanup:
```bash
./scripts/permanent/maintenance/smart-cleanup.sh
```

This will:
1. Move `TEMP` files to `backups/temp-files/`
2. Move `OLD` files to `backups/old-files/`
3. Move `DEV` files to `backups/dev-files/`
4. Clean Python cache files
5. Analyze file usage and show large file candidates
6. Generate a cleanup summary

## Backup Structure

After cleanup, files are organized in:
```
backups/
├── dev-files/          # Development files (*.sh, dev scripts)
├── old-files/          # Old versions (*.backup, *.old, *.bak)
├── temp-files/         # Temporary files (*.tmp, *.cache, *.log)
├── unused-files/       # Potentially unused files
├── large-dirs/         # Large directories (models, tools, etc.)
├── scripts/            # Moved shell scripts
└── zoe-ui/             # zoe-ui backup files
```

## Best Practices

### 1. Tag Core Files First
Always tag essential files as `CORE` to prevent accidental removal:
```bash
./scripts/permanent/maintenance/tag-files.sh -t CORE -d "Essential service" services/zoe-core/
```

### 2. Use Descriptive Descriptions
Include clear descriptions when tagging:
```bash
./scripts/permanent/maintenance/tag-files.sh -t CONFIG -d "Database configuration settings" config/database.yml
```

### 3. Regular Cleanup
Run smart cleanup regularly to maintain organization:
```bash
# Weekly cleanup
./scripts/permanent/maintenance/smart-cleanup.sh
```

### 4. Review Before Deleting
Always review files in backup directories before permanent deletion:
```bash
ls -la backups/temp-files/
```

## Examples

### Tagging a New Service
```bash
# Tag the main service file
./scripts/permanent/maintenance/tag-files.sh -t CORE -d "New microservice entry point" services/new-service/main.py

# Tag configuration
./scripts/permanent/maintenance/tag-files.sh -t CONFIG -d "Service configuration" services/new-service/config.yml

# Tag development scripts
./scripts/permanent/maintenance/tag-files.sh -t DEV -d "Development setup scripts" services/new-service/setup.sh
```

### Cleaning Up Development Files
```bash
# Tag all shell scripts as development files
./scripts/permanent/maintenance/tag-files.sh -t DEV -d "Development and setup scripts" "*.sh"

# Run cleanup to move them to backup
./scripts/permanent/maintenance/smart-cleanup.sh
```

### Finding Large Files
The smart cleanup script automatically identifies large files and shows which ones are protected:
```bash
./scripts/permanent/maintenance/smart-cleanup.sh
# Output will show:
# 📦 large-file.bin (50MB) - Candidate for backup
# 📦 core-service.py - Protected (tagged: CORE)
```

## Safety Features

1. **Protected Files**: Files tagged as `CORE`, `CONFIG`, `DOCS`, `SCRIPTS`, `DATA`, `SERVICES`, `UI`, `SECURITY`, `DEPLOYMENT` are never moved or deleted
2. **Backup Preservation**: All moved files are preserved in organized backup directories
3. **Dry Run Mode**: The cleanup script shows what will be moved before actually moving files
4. **Pattern Matching**: Supports wildcard patterns for bulk tagging
5. **Recovery**: All moved files can be easily restored from backup directories

## Troubleshooting

### File Not Being Cleaned Up
If a file isn't being moved during cleanup:
1. Check if it's tagged as important: `./scripts/permanent/maintenance/tag-files.sh -s "filename"`
2. Verify the file path in `.file-tags`
3. Check if the file is already in a backup directory

### Accidentally Tagged File
To remove a tag:
```bash
./scripts/permanent/maintenance/tag-files.sh -r path/to/file
```

### Restoring Files
To restore files from backup:
```bash
# Copy specific file back
cp backups/dev-files/path/to/file original/path/

# Or move entire directory back
mv backups/dev-files/service/ services/
```

## Integration with Git

The `.file-tags` file is tracked by git, so your tagging system is version controlled and shared across environments. The backup directories are excluded from git via `.gitignore`.

This ensures that:
- File tags are preserved across deployments
- Backup files don't bloat the repository
- Team members can see which files are important
- Cleanup operations are consistent across environments
