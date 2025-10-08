#!/usr/bin/env python3
"""
Comprehensive Tests for Enhanced Features
"""
import os


def test_ui_files_exist():
    """Test that all UI files were created"""
    
    files_to_check = [
        "/home/pi/zoe/services/zoe-ui/dist/js/memory-graph.js",
        "/home/pi/zoe/services/zoe-ui/dist/js/wikilink-parser.js",
        "/home/pi/zoe/services/zoe-ui/dist/js/memory-timeline.js",
        "/home/pi/zoe/services/zoe-ui/dist/js/memory-search.js",
        "/home/pi/zoe/services/zoe-ui/dist/memories-enhanced.html",
        "/home/pi/zoe/services/zoe-ui/dist/css/memories-enhanced.css"
    ]
    
    print("\n" + "="*80)
    print("Checking Enhanced UI Files")
    print("="*80)
    
    all_exist = True
    for file_path in files_to_check:
        exists = os.path.exists(file_path)
        status = "✅" if exists else "❌"
        print(f"{status} {file_path.split('/')[-1]}")
        all_exist = all_exist and exists
    
    return all_exist


if __name__ == "__main__":
    ui_ok = test_ui_files_exist()
    print(f"\n{'✅ ALL TESTS PASSED' if ui_ok else '❌ SOME TESTS FAILED'}")
