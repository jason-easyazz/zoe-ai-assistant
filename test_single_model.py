#!/usr/bin/env python3
"""Test a single model with multi-turn conversations"""

import requests
import json
import sys
import time
from datetime import datetime

model_name = sys.argv[1] if len(sys.argv) > 1 else "unknown"

TEST_CONVERSATIONS = [
    {
        "name": "Greeting",
        "turns": ["Hey Zoe, how are you?", "What can you help me with?"],
        "hallucination_check": ["Jason", "John", "my day", "I had", "my friend"]
    },
    {
        "name": "Party Planning (Context Memory Test)",
        "turns": [
            "I am planning a birthday party for my friend Sarah next weekend",
            "What should I put on the shopping list?",
            "How much will that cost roughly?",
            "Remind me what we are planning?"
        ],
        "context_check": ["party", "birthday", "Sarah"]
    },
    {
        "name": "Joke Test",
        "turns": [
            "Tell me a joke",
            "That's funny! Tell me another one"
        ],
        "hallucination_check": ["Teneeka", "my friend", "I met"]
    }
]

def test_conversation(conv):
    history = []
    results = {
        "name": conv["name"],
        "turns": [],
        "hallucinations": [],
        "context_lost": False,
        "total_time": 0,
        "status": "success"
    }
    
    for i, msg in enumerate(conv['turns'], 1):
        try:
            start = time.time()
            resp = requests.post(
                'https://localhost/api/chat/',
                json={'message': msg, 'conversation_history': history, 'mode': 'widget_chat'},
                verify=False,
                timeout=30
            )
            elapsed = time.time() - start
            
            if resp.status_code == 200:
                data = resp.json()
                response = data['response']
                time_taken = data.get('response_time', elapsed)
                
                # Check for hallucinations
                if 'hallucination_check' in conv:
                    for bad_word in conv['hallucination_check']:
                        if bad_word.lower() in response.lower():
                            results['hallucinations'].append(bad_word)
                
                # Check for context retention (in last turn)
                if i == len(conv['turns']) and 'context_check' in conv:
                    context_found = sum(1 for word in conv['context_check'] if word.lower() in response.lower())
                    if context_found < len(conv['context_check']) // 2:  # At least half should be present
                        results['context_lost'] = True
                
                # Check for gibberish
                if any(x in response for x in ["ZZZZZ", "MyMyMy", "errorerror", "innernerner"]):
                    results['status'] = "gibberish"
                
                history.append({'role': 'user', 'content': msg})
                history.append({'role': 'assistant', 'content': response})
                
                results['turns'].append({
                    "user": msg,
                    "assistant": response[:200],
                    "time": time_taken
                })
                results['total_time'] += time_taken
            else:
                results['status'] = f"http_error_{resp.status_code}"
                break
        except Exception as e:
            results['status'] = f"exception: {str(e)}"
            break
    
    return results

# Run all conversations
all_results = []
for conv in TEST_CONVERSATIONS:
    result = test_conversation(conv)
    all_results.append(result)
    time.sleep(1)  # Cooldown

# Calculate scores
total_time = sum(r['total_time'] for r in all_results)
total_hallucinations = sum(len(r['hallucinations']) for r in all_results)
context_losses = sum(1 for r in all_results if r['context_lost'])
failures = sum(1 for r in all_results if r['status'] != "success")

# Quality score
quality = 100
if total_hallucinations > 0:
    quality -= 30
if context_losses > 0:
    quality -= 25
if failures > 0:
    quality -= 30
if total_time / len(TEST_CONVERSATIONS) > 10:
    quality -= 10

quality = max(0, quality)

# Output JSON
output = {
    "model": model_name,
    "timestamp": datetime.now().isoformat(),
    "conversations": all_results,
    "summary": {
        "total_time": round(total_time, 2),
        "avg_time_per_conversation": round(total_time / len(TEST_CONVERSATIONS), 2),
        "hallucinations": total_hallucinations,
        "context_losses": context_losses,
        "failures": failures,
        "quality_score": quality
    }
}

print(json.dumps(output, indent=2))

# Also save to file
output_file = f"/home/zoe/assistant/results_{model_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
with open(output_file, 'w') as f:
    json.dump(output, f, indent=2)

# Summary to stderr for visibility
print(f"\n{'='*60}", file=sys.stderr)
print(f"MODEL: {model_name}", file=sys.stderr)
print(f"{'='*60}", file=sys.stderr)
print(f"Quality Score: {quality}/100", file=sys.stderr)
print(f"Avg Time: {total_time / len(TEST_CONVERSATIONS):.2f}s per conversation", file=sys.stderr)
print(f"Hallucinations: {total_hallucinations}", file=sys.stderr)
print(f"Context Lost: {context_losses}/{len(TEST_CONVERSATIONS)}", file=sys.stderr)
print(f"Failures: {failures}/{len(TEST_CONVERSATIONS)}", file=sys.stderr)

if quality >= 80:
    print("✅ RECOMMENDED", file=sys.stderr)
elif quality >= 60:
    print("⚠️  ACCEPTABLE", file=sys.stderr)
else:
    print("❌ NOT RECOMMENDED", file=sys.stderr)

print(f"📊 Saved to: {output_file}", file=sys.stderr)
print("", file=sys.stderr)

