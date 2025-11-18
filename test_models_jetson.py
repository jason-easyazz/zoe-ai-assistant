#!/usr/bin/env python3
"""
Model Quality Testing Script for Jetson
Tests models with multi-turn conversations to evaluate quality, hallucination, and performance
"""

import requests
import json
import time
from datetime import datetime
from typing import List, Dict

# Test conversation with memory carrying across turns
TEST_CONVERSATIONS = [
    {
        "name": "Birthday Party Planning (4 turns)",
        "turns": [
            "Hey Zoe, I'm planning a birthday party for my friend next weekend",
            "What should I put on the shopping list?",
            "How much do you think that will cost?",
            "Remind me what we're planning again?"
        ],
        "expected_context": ["party", "birthday", "friend", "shopping"]
    },
    {
        "name": "Casual Greeting Test",
        "turns": [
            "Hey Zoe, how are you?",
            "What can you help me with?"
        ],
        "hallucination_check": ["Jason", "John", "shopping trip", "my day was", "I had dinner"]
    },
    {
        "name": "Simple Question Test",
        "turns": [
            "Tell me a joke",
            "That's funny! Can you explain why it's funny?"
        ],
        "hallucination_check": ["Teneeka", "my friend", "I met"]
    }
]

# Available models on Jetson
MODELS_TO_TEST = [
    {
        "path": "/models/smollm2-1.7b-gguf/SmolLM2-1.7B-Instruct-Q4_K_M.gguf",
        "name": "smollm2-1.7b",
        "size_gb": 1.0,
        "description": "Ultra-lightweight (1GB)"
    },
    {
        "path": "/models/llama-3.2-3b-gguf/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "name": "llama3.2-3b",
        "size_gb": 1.9,
        "description": "Fast, balanced (1.9GB)"
    },
    {
        "path": "/models/qwen2.5-7b-gguf/Qwen2.5-7B-Instruct-Q3_K_M.gguf",
        "name": "qwen2.5-7b-q3",
        "size_gb": 3.6,
        "description": "Best tool calling, lighter quant (3.6GB)"
    },
    {
        "path": "/models/qwen2.5-7b-gguf/Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        "name": "qwen2.5-7b-q4",
        "size_gb": 4.4,
        "description": "Best tool calling, higher quality (4.4GB)"
    }
]

API_URL = "https://localhost/api/chat/"
VERIFY_SSL = False  # Self-signed cert


def test_model_via_api(model_name: str, conversation: Dict) -> Dict:
    """Test a model through Zoe's chat API with conversation history"""
    print(f"\n{'='*60}")
    print(f"Testing: {conversation['name']}")
    print(f"{'='*60}\n")
    
    conversation_history = []
    results = {
        "model": model_name,
        "conversation": conversation['name'],
        "turns": [],
        "total_time": 0,
        "avg_time_per_turn": 0,
        "hallucination_detected": False,
        "context_maintained": True,
        "quality_score": 0,
        "errors": []
    }
    
    for i, user_message in enumerate(conversation['turns'], 1):
        print(f"Turn {i}: {user_message}")
        
        try:
            start_time = time.time()
            
            response = requests.post(
                API_URL,
                json={
                    "message": user_message,
                    "conversation_history": conversation_history,
                    "mode": "widget_chat"
                },
                verify=VERIFY_SSL,
                timeout=60
            )
            
            elapsed = time.time() - start_time
            
            if response.status_code == 200:
                data = response.json()
                assistant_response = data.get('response', '')
                response_time = data.get('response_time', elapsed)
                
                print(f"→ Zoe: {assistant_response[:200]}")
                print(f"  Time: {response_time:.2f}s\n")
                
                # Check for hallucinations
                if 'hallucination_check' in conversation:
                    for bad_word in conversation['hallucination_check']:
                        if bad_word.lower() in assistant_response.lower():
                            print(f"⚠️  HALLUCINATION DETECTED: '{bad_word}'")
                            results['hallucination_detected'] = True
                
                # Check for gibberish
                gibberish_indicators = ["ZZZZZ", "MyMyMy", "personpersonperson", "innernernerner"]
                for indicator in gibberish_indicators:
                    if indicator in assistant_response:
                        print(f"⚠️  GIBBERISH DETECTED: {indicator}")
                        results['errors'].append(f"Gibberish: {indicator}")
                
                # Update conversation history
                conversation_history.append({"role": "user", "content": user_message})
                conversation_history.append({"role": "assistant", "content": assistant_response})
                
                results['turns'].append({
                    "user": user_message,
                    "assistant": assistant_response,
                    "time": response_time,
                    "status": "success"
                })
                results['total_time'] += response_time
            else:
                error_msg = f"HTTP {response.status_code}: {response.text[:200]}"
                print(f"❌ Error: {error_msg}\n")
                results['errors'].append(error_msg)
                results['turns'].append({
                    "user": user_message,
                    "error": error_msg,
                    "status": "error"
                })
        
        except Exception as e:
            error_msg = str(e)
            print(f"❌ Exception: {error_msg}\n")
            results['errors'].append(error_msg)
            results['turns'].append({
                "user": user_message,
                "error": error_msg,
                "status": "error"
            })
    
    # Calculate metrics
    if results['turns']:
        results['avg_time_per_turn'] = results['total_time'] / len(results['turns'])
    
    # Quality score (0-100)
    quality = 100
    if results['hallucination_detected']:
        quality -= 40
    if results['errors']:
        quality -= 30
    if results['avg_time_per_turn'] > 5:
        quality -= 10  # Slow response penalty
    
    results['quality_score'] = max(0, quality)
    
    return results


def switch_model(model_path: str, model_name: str) -> bool:
    """Restart llama.cpp container with new model"""
    import subprocess
    
    print(f"\n🔄 Switching to model: {model_name}")
    print(f"   Path: {model_path}")
    
    try:
        # Update docker-compose.yml to use new model
        subprocess.run([
            "docker", "exec", "zoe-llamacpp",
            "pkill", "-9", "llama-server"
        ], check=False)
        
        # Start new model
        subprocess.run([
            "docker", "restart", "zoe-llamacpp"
        ], check=True)
        
        print("⏳ Waiting for model to load (30s)...")
        time.sleep(30)
        
        # Verify it's running
        health_check = subprocess.run([
            "docker", "exec", "zoe-llamacpp", 
            "ps", "aux"
        ], capture_output=True, text=True)
        
        if "llama-server" in health_check.stdout:
            print("✅ Model loaded successfully\n")
            return True
        else:
            print("❌ Model failed to load\n")
            return False
            
    except Exception as e:
        print(f"❌ Error switching model: {e}\n")
        return False


def main():
    print("="*80)
    print("JETSON MODEL QUALITY TESTING SUITE")
    print("Testing conversational AI models with multi-turn memory")
    print("="*80)
    print(f"\nStarted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Models to test: {len(MODELS_TO_TEST)}")
    print(f"Test scenarios: {len(TEST_CONVERSATIONS)}")
    print(f"Total tests: {len(MODELS_TO_TEST) * len(TEST_CONVERSATIONS)}")
    
    all_results = []
    
    for model in MODELS_TO_TEST:
        print(f"\n\n{'#'*80}")
        print(f"MODEL: {model['name']} - {model['description']}")
        print(f"{'#'*80}")
        
        # Note: For now we'll test with the currently loaded model
        # To fully automate, we'd need to modify docker-compose.yml and restart
        # For this initial run, manually switch models between runs
        
        print(f"\n⚠️  Manual Step: Ensure {model['name']} is loaded in zoe-llamacpp")
        print(f"   docker-compose.yml MODEL_PATH should be: {model['path']}")
        input("Press Enter when ready to test this model...")
        
        model_results = []
        
        for conversation in TEST_CONVERSATIONS:
            result = test_model_via_api(model['name'], conversation)
            model_results.append(result)
            all_results.append(result)
            
            # Cool down between tests
            time.sleep(2)
        
        # Model summary
        print(f"\n{'─'*60}")
        print(f"SUMMARY: {model['name']}")
        print(f"{'─'*60}")
        avg_quality = sum(r['quality_score'] for r in model_results) / len(model_results)
        avg_time = sum(r['avg_time_per_turn'] for r in model_results) / len(model_results)
        hallucinations = sum(1 for r in model_results if r['hallucination_detected'])
        errors = sum(len(r['errors']) for r in model_results)
        
        print(f"Average Quality Score: {avg_quality:.1f}/100")
        print(f"Average Response Time: {avg_time:.2f}s")
        print(f"Hallucinations Detected: {hallucinations}/{len(model_results)}")
        print(f"Total Errors: {errors}")
        
        # Rating
        if avg_quality >= 80 and not hallucinations:
            print("✅ RECOMMENDED - Excellent quality, no hallucinations")
        elif avg_quality >= 60:
            print("⚠️  ACCEPTABLE - Decent quality but has issues")
        else:
            print("❌ NOT RECOMMENDED - Poor quality or unreliable")
    
    # Final comparison
    print(f"\n\n{'='*80}")
    print("FINAL COMPARISON")
    print(f"{'='*80}\n")
    
    model_scores = {}
    for result in all_results:
        model = result['model']
        if model not in model_scores:
            model_scores[model] = []
        model_scores[model].append(result['quality_score'])
    
    ranked = sorted(model_scores.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True)
    
    for rank, (model, scores) in enumerate(ranked, 1):
        avg_score = sum(scores) / len(scores)
        print(f"{rank}. {model}: {avg_score:.1f}/100")
    
    print(f"\n🏆 Winner: {ranked[0][0]}")
    print(f"   Recommended for production use")
    
    # Save results
    output_file = f"/home/zoe/assistant/model_test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "results": all_results,
            "summary": {
                "models_tested": list(model_scores.keys()),
                "ranked_models": [{"rank": i+1, "model": m, "score": sum(s)/len(s)} 
                                 for i, (m, s) in enumerate(ranked)]
            }
        }, f, indent=2)
    
    print(f"\n📊 Results saved to: {output_file}")
    print(f"\nCompleted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()

