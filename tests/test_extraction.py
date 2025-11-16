#!/usr/bin/env python3
"""
Test the item extraction logic directly
"""

import re

def _extract_item_name(query: str) -> str:
    """Extract item name from query - handles many natural language variants"""
    query_lower = query.lower()
    
    # Pattern 1: "add X to..." / "put X on..." / "add X to it"
    # ✅ FIX: Use regex to match "add" or "put" followed by space OR punctuation
    add_put_pattern = r'\b(add|put)[,\s]+'
    match = re.search(add_put_pattern, query_lower)
    if match:
        idx = match.end()
        rest = query_lower[idx:]
        print(f"   Found pattern at {match.group()}")
        print(f"   Rest before lstrip: '{rest}'")
        # ✅ FIX: Remove leading punctuation (commas, periods, spaces)
        rest = rest.lstrip(" ,.;:")
        print(f"   Rest after lstrip: '{rest}'")
        # Handle "add X to it" - extract X
        if " to it" in rest or " on it" in rest:
            item = rest.replace(" to it", "").replace(" on it", "").strip()
            print(f"   'to it' pattern, item: '{item}'")
            return item.title() if item else "Item"
        # Extract until "to", "on", "list", "shopping"
        for stop in [" to ", " on ", " list", " shopping"]:
            if stop in rest:
                item = rest[:rest.index(stop)].strip()
                print(f"   Found stop '{stop}', item: '{item}'")
                return item.title() if item else "Item"
        print(f"   No stop found, returning rest: '{rest}'")
        return rest.strip().title()
    
    return "Item"

# Test cases
test_queries = [
    "Can you add, Dog treats to my shopping list",
    "Add milk to my list",
    "add eggs and bacon",
    "Put coffee on the list"
]

print("="*80)
print("Testing Item Extraction Logic")
print("="*80)

for query in test_queries:
    print(f"\n📝 Query: {query}")
    result = _extract_item_name(query)
    print(f"✅ Result: '{result}'")
    print()

