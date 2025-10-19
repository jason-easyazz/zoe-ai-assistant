#!/usr/bin/env python3
"""Test the final failing scenarios"""
import requests
import time

def test(name, turns, user, keywords):
    print(f"\n{'='*70}\n{name}\n{'='*70}")
    responses = []
    for i, msg in enumerate(turns, 1):
        print(f"\n👤 Turn {i}: {msg}")
        r = requests.post("http://localhost:8000/api/chat",
                         json={"message": msg, "user_id": user}, timeout=60)
        resp = r.json().get('response', '')
        print(f"🤖 Zoe: {resp}")
        responses.append(resp)
        time.sleep(1)
    
    all_resp = " ".join(responses).lower()
    missing = [k for k in keywords if k.lower() not in all_resp]
    
    if missing:
        print(f"\n❌ FAIL - Missing: {missing}")
        return False
    else:
        print(f"\n✅ PASS - All keywords found!")
        return True

print("╔" + "="*68 + "╗")
print("║  🎯 FINAL 4 FAILURES - PUSH TO 100%  ║")
print("╚" + "="*68 + "╝\n")

results = []

# Based on 92% run, likely failures:
results.append(test("1. Job Title Recall", 
    ["I'm a software engineer", "What's my job?"],
    "final_1", ["software", "engineer"]))

results.append(test("2. Context Chain",
    ["I have a presentation Friday", "Add practice time and buy supplies"],
    "final_2", ["practice", "supplies"]))

results.append(test("3. Show Everything",
    ["Show me all my tasks and events"],
    "final_3", ["task", "event"]))

results.append(test("4. Elliptical Speech",
    ["Do I have meetings today?", "Tomorrow?"],
    "final_4", ["tomorrow"]))

print(f"\n{'='*70}")
print(f"RESULTS: {sum(results)}/4 passed ({sum(results)/4*100:.0f}%)")
print("="*70)

