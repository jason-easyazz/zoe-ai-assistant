#!/usr/bin/env python3

def fix_chat_return():
    """Simple fix for chat endpoint return statement"""
    
    with open('main.py', 'r') as f:
        lines = f.readlines()
    
    modified = False
    for i, line in enumerate(lines):
        # Look for the return statement with response and timestamp
        if 'return {' in line and i < len(lines) - 3:
            # Check if this looks like our chat endpoint return
            next_few_lines = ''.join(lines[i:i+4])
            if '"response": ai_response' in next_few_lines and '"timestamp": datetime.now()' in next_few_lines:
                print(f"Found return statement at line {i+1}")
                
                # Replace this return block with our enhanced version
                indent = len(line) - len(line.lstrip())
                spaces = ' ' * indent
                
                # New return block
                new_return = [
                    f'{spaces}# Enhanced return with event detection\n',
                    f'{spaces}event_created = None\n',
                    f'{spaces}if entities and entities.get("events"):\n',
                    f'{spaces}    event = entities["events"][0]\n',
                    f'{spaces}    event_created = {{\n',
                    f'{spaces}        "title": event.get("title", "Event"),\n',
                    f'{spaces}        "date": str(event.get("date", "TBD")),\n',
                    f'{spaces}        "time": str(event.get("time")) if event.get("time") else None,\n',
                    f'{spaces}        "category": event.get("category", "general"),\n',
                    f'{spaces}        "priority": event.get("priority", "medium"),\n',
                    f'{spaces}        "notifications": event.get("notifications", []),\n',
                    f'{spaces}        "tasks": event.get("tasks", [])\n',
                    f'{spaces}    }}\n',
                    f'{spaces}\n',
                    f'{spaces}response_data = {{\n',
                    f'{spaces}    "response": ai_response,\n',
                    f'{spaces}    "conversation_id": conversation_id,\n',
                    f'{spaces}    "timestamp": datetime.now().isoformat()\n',
                    f'{spaces}}}\n',
                    f'{spaces}\n',
                    f'{spaces}if event_created:\n',
                    f'{spaces}    response_data["event_created"] = event_created\n',
                    f'{spaces}\n',
                    f'{spaces}return response_data\n'
                ]
                
                # Find the end of the current return block
                j = i
                brace_count = 0
                found_opening = False
                while j < len(lines):
                    for char in lines[j]:
                        if char == '{':
                            found_opening = True
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if found_opening and brace_count == 0:
                                # Found the end of return block
                                end_line = j + 1
                                print(f"Replacing lines {i+1} to {end_line}")
                                
                                # Replace the lines
                                lines[i:end_line] = new_return
                                modified = True
                                break
                    if modified:
                        break
                    j += 1
                break
    
    if modified:
        with open('main.py', 'w') as f:
            f.writelines(lines)
        print("✅ Successfully updated chat endpoint")
        return True
    else:
        print("❌ Could not find the return statement to modify")
        return False

if __name__ == "__main__":
    fix_chat_return()
