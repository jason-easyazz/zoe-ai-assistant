#!/usr/bin/env python3
"""Install and test ollama connection"""
import subprocess
import sys

def install_ollama():
    """Install ollama with pip"""
    try:
        # First upgrade pip
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], check=False)
        
        # Install ollama
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "ollama"],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            print("✅ Ollama installed successfully")
            return True
        else:
            print(f"❌ Installation failed: {result.stderr}")
            # Try with no dependencies
            result2 = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--no-deps", "ollama"],
                capture_output=True,
                text=True
            )
            if result2.returncode == 0:
                print("✅ Ollama installed without dependencies")
                return True
    except Exception as e:
        print(f"Error: {e}")
    return False

def test_ollama():
    """Test ollama connection"""
    try:
        import ollama
        client = ollama.Client(host='http://zoe-ollama:11434')
        models = client.list()
        print(f"✅ Ollama works! Models: {[m.get('name') for m in models.get('models', [])]}")
        return True
    except Exception as e:
        print(f"❌ Ollama test failed: {e}")
        return False

if __name__ == "__main__":
    if install_ollama():
        test_ollama()
