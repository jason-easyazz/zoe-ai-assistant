#!/usr/bin/env python3

def fix_chat_return():
    """Fix chat endpoint to return event_created"""
    
    with open('main.py', 'r') as f:
        lines = f.readlines()
    
    # Find the chat endpoint and look for the return statement
    in_chat_function = False
    for i, line in enumerate(lines):
        if '@app.post("/api/chat")' in line:
            in_chat_function = True
            continue
        
        if in_chat_function and 'return {' in line:
            # Found the return statement
            print(f"Found return statement at line {i+1}: {line.strip()}")
            
            # Get the indentation
            indent = len(line) - len(line.lstrip())
            spaces = ' ' * indent
            
            # Insert event detection before the return
            new_lines = [
                f'{spaces}# Check for events and add to response\n',
                f'{spaces}event_created = None\n',
                f'{spaces}if entities and entities.get("events"):\n',
                f'{spaces}    event = entities["events"][0]\n',
                f'{spaces}    event_created = {{\n',
                f'{spaces}        "title": event.get("title", "Event"),\n',
                f'{spaces}        "date": event["date"].strftime("%d/%m/%Y") if hasattr(event["date"], "strftime") else str(event["date"]),\n',
                f'{spaces}        "category": event.get("category", "general"),\n',
                f'{spaces}        "priority": event.get("priority", "medium")\n',
                f'{spaces}    }}\n',
                f'{spaces}    print(f"✅ Event created for response: {{event_created}}")\n',
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
            
            # Replace the return statement and any following lines that are part of it
            j = i
            while j < len(lines) and not lines[j].strip().startswith('return'):
                j += 1
            
            if j < len(lines):
                # Find the end of the return statement
                brace_count = 0
                end_j = j
                for k in range(j, len(lines)):
                    for char in lines[k]:
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                end_j = k + 1
                                break
                    if brace_count == 0:
                        break
                
                print(f"Replacing lines {j+1} to {end_j}")
                lines[j:end_j] = new_lines
            
            break
    
    with open('main.py', 'w') as f:
        f.writelines(lines)
    
    print("✅ Fixed chat endpoint return statement")

fix_chat_return()
