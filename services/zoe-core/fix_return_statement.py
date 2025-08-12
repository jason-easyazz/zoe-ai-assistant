#!/usr/bin/env python3

def fix_return_statement():
    """Fix the return statement to include event_created field"""
    
    with open('main.py', 'r') as f:
        lines = f.readlines()
    
    # We know the return statement is around line 253, let's find it precisely
    for i, line in enumerate(lines):
        if 'response_data = {' in line and i > 240:  # Look after line 240
            print(f"Found response_data at line {i+1}")
            
            # Look for the return statement after this
            for j in range(i, min(i+10, len(lines))):
                if 'return response_data' in lines[j] or 'return {' in lines[j]:
                    print(f"Found return at line {j+1}")
                    
                    # Insert event_created logic before the return
                    indent = len(lines[j]) - len(lines[j].lstrip())
                    spaces = ' ' * indent
                    
                    # New lines to insert
                    new_lines = [
                        f'{spaces}# Add event_created to response if event was detected\n',
                        f'{spaces}if detected_event and detected_event.get("created"):\n',
                        f'{spaces}    response_data["event_created"] = {{\n',
                        f'{spaces}        "title": detected_event["title"],\n',
                        f'{spaces}        "date": detected_event["date"],\n',
                        f'{spaces}        "category": detected_event.get("category", "general"),\n',
                        f'{spaces}        "priority": detected_event.get("priority", "medium")\n',
                        f'{spaces}    }}\n',
                        f'{spaces}    print(f"✅ Added event_created to response: {{response_data[\'event_created\']}}")\n',
                        f'{spaces}\n'
                    ]
                    
                    # Insert the new lines before the return
                    lines[j:j] = new_lines
                    
                    with open('main.py', 'w') as f:
                        f.writelines(lines)
                    
                    print("✅ Successfully added event_created logic to return statement")
                    return True
            break
    
    print("❌ Could not find return statement to modify")
    return False

fix_return_statement()
