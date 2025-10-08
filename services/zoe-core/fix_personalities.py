#!/usr/bin/env python3
"""Direct fix for personality names"""
import os
import glob

def fix_all_personalities():
    # Find ALL Python files
    all_files = glob.glob("/app/**/*.py", recursive=True)
    
    replacements = {
        # Fix Zoe personality
        "Zoe": "Zoe",
        "Zoe, your friendly AI assistant": "Zoe, your friendly AI assistant",
        "I assist you": "I assist you",
        "My name is Zoe": "My name is Zoe",
        
        # Fix Zack personality  
        "My name is Zack": "My name is Zack",
        "I'm Zack": "I'm Zack",
        "Zack here, and I": "Zack here, and I",
        "technical AI assistant for the Zoe system": "technical AI assistant for the Zoe system",
        
        # Generic fixes
        "You are Zack": "You are Zack",
        "You are Zoe": "You are Zoe",
    }
    
    fixed_files = []
    
    for filepath in all_files:
        if filepath.endswith(".py"):
            try:
                with open(filepath, "r") as f:
                    content = f.read()
                
                original = content
                for old, new in replacements.items():
                    content = content.replace(old, new)
                
                if content != original:
                    with open(filepath, "w") as f:
                        f.write(content)
                    fixed_files.append(filepath)
                    print(f"✅ Fixed: {filepath}")
            except:
                pass
    
    return fixed_files

if __name__ == "__main__":
    print("🔧 Fixing all personality references...")
    fixed = fix_all_personalities()
    if fixed:
        print(f"\n✅ Fixed {len(fixed)} files")
    else:
        print("\n⚠️ No files needed fixing")
