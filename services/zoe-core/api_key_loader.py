"""Load saved API keys for AI router"""
import os
import json
import base64
from pathlib import Path
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

def load_api_keys():
    """Load encrypted API keys and make them available to AI router"""
    try:
        # Check encrypted file
        enc_file = Path("/app/data/api_keys.enc")
        if not enc_file.exists():
            print("No encrypted keys file found")
            return {}
        
        # Generate decryption key
        salt = b"zoe_api_key_salt_2024"
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000
        )
        key = base64.urlsafe_b64encode(kdf.derive(b"zoe_secure_key_2024"))
        cipher = Fernet(key)
        
        # Decrypt and load keys
        with open(enc_file, 'rb') as f:
            decrypted = cipher.decrypt(f.read())
            keys = json.loads(decrypted)
        
        # Set environment variables for AI router
        if 'anthropic' in keys:
            os.environ['ANTHROPIC_API_KEY'] = keys['anthropic']
            print("✅ Loaded Anthropic API key")
        
        if 'openai' in keys:
            os.environ['OPENAI_API_KEY'] = keys['openai']
            print("✅ Loaded OpenAI API key")
            
        return keys
        
    except Exception as e:
        print(f"Error loading API keys: {e}")
        return {}

# Load keys on module import
loaded_keys = load_api_keys()
