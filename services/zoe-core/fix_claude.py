#!/usr/bin/env python3
"""Check and fix Claude API integration"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Check if API key exists
api_key = os.getenv('ANTHROPIC_API_KEY', '').strip()

if api_key and api_key not in ['', 'your-key-here', 'sk-ant-api03-YOUR-KEY-HERE']:
    print(f"✅ API key found: {api_key[:10]}...")
    
    # Test the key
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        # Simple test
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=10,
            messages=[{"role": "user", "content": "Say 'test'"}]
        )
        print("✅ Claude API is working!")
    except Exception as e:
        print(f"❌ Claude API error: {e}")
        print("Check that your API key is valid")
else:
    print("❌ No valid API key found in .env")
    print("Add your key: ANTHROPIC_API_KEY=sk-ant-api03-...")
