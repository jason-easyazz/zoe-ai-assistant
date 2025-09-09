#!/bin/bash
# CLEANUP_BACKUPS.sh
# Remove all .env files from existing backups

echo "🧹 CLEANING UP BACKUP SECURITY"
echo "=============================="
echo ""

cd /home/pi/zoe

# Count exposed files
echo "🔍 Scanning for exposed .env files in backups..."
exposed_count=$(find backups -type f \( -name ".env" -o -name "*.env" \) 2>/dev/null | wc -l)

if [ "$exposed_count" -gt 0 ]; then
    echo "  Found $exposed_count exposed .env files"
    echo ""
    echo "Files to be removed:"
    find backups -type f \( -name ".env" -o -name "*.env" \) 2>/dev/null | while read file; do
        echo "  - $file"
    done
    
    echo ""
    echo "⚠️  This will permanently delete .env files from backups"
    echo "Press Enter to clean up or Ctrl+C to cancel..."
    read
    
    # Remove .env files
    find backups -type f \( -name ".env" -o -name "*.env" \) -delete 2>/dev/null
    echo "✅ Removed $exposed_count .env files"
    
    # Check tar/zip archives
    echo ""
    echo "🔍 Checking compressed backups..."
    find backups -type f \( -name "*.tar.gz" -o -name "*.tar" -o -name "*.zip" \) | while read archive; do
        echo "  Checking: $(basename $archive)"
        
        # Check if archive contains .env
        if tar -tzf "$archive" 2>/dev/null | grep -q "\.env"; then
            echo "    ⚠️ Contains .env - cleaning..."
            
            # Extract, remove .env, recompress
            temp_dir=$(mktemp -d)
            tar -xzf "$archive" -C "$temp_dir" 2>/dev/null
            find "$temp_dir" -name ".env" -o -name "*.env" | xargs rm -f
            
            # Recompress without .env
            new_archive="${archive}.clean"
            tar -czf "$new_archive" -C "$temp_dir" .
            mv "$new_archive" "$archive"
            rm -rf "$temp_dir"
            
            echo "    ✅ Cleaned $(basename $archive)"
        else
            echo "    ✅ Clean"
        fi
    done
else
    echo "✅ No exposed .env files found in backups!"
fi

echo ""
echo "✅ Backup cleanup complete!"
echo ""
echo "Final security check:"
~/check_key_security.sh
