#!/bin/bash

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║        🔍 VERIFICATION: All References Updated                   ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# Check for old references in key files
echo "📝 Checking core documentation for old references..."
echo ""

FILES=("README.md" "CHANGELOG.md" "CLEANUP_PLAN.md" "PROJECT_STATUS.md")
OLD_REFS=("ZOES_CURRENT_STATE" "SYSTEM_STATUS" "CLEANUP_COMPLETE_SUMMARY")

for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        found=0
        for ref in "${OLD_REFS[@]}"; do
            if grep -q "$ref" "$file" 2>/dev/null; then
                echo "⚠️  $file still references $ref"
                found=1
            fi
        done
        if [ $found -eq 0 ]; then
            echo "✅ $file - no old references"
        fi
    fi
done

echo ""
echo "📁 Verifying new structure..."
echo ""

# Check new structure exists
[ -f "PROJECT_STATUS.md" ] && echo "✅ PROJECT_STATUS.md exists" || echo "❌ PROJECT_STATUS.md missing"
[ -f "CLEANUP_SUMMARY.md" ] && echo "✅ CLEANUP_SUMMARY.md exists" || echo "❌ CLEANUP_SUMMARY.md missing"
[ -f "DOCUMENTATION_STRUCTURE.md" ] && echo "✅ DOCUMENTATION_STRUCTURE.md exists" || echo "❌ DOCUMENTATION_STRUCTURE.md missing"
[ -d "docs/archive" ] && echo "✅ docs/archive/ exists" || echo "❌ docs/archive/ missing"
[ -f "docs/README.md" ] && echo "✅ docs/README.md exists" || echo "❌ docs/README.md missing"

echo ""
echo "🛠️  Verifying tools..."
echo ""

[ -f "comprehensive_audit.py" ] && echo "✅ comprehensive_audit.py" || echo "❌ Missing"
[ -f "comprehensive_cleanup.py" ] && echo "✅ comprehensive_cleanup.py" || echo "❌ Missing"
[ -f "audit_references.py" ] && echo "✅ audit_references.py" || echo "❌ Missing"  
[ -f "fix_references.py" ] && echo "✅ fix_references.py" || echo "❌ Missing"

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║        ✅ VERIFICATION COMPLETE                                   ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
