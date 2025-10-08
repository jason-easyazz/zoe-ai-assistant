import os
import json
from pathlib import Path
from cryptography.fernet import Fernet

class APIKeyManager:
    def __init__(self):
        self.data_dir = Path("/app/data")
        self.data_dir.mkdir(exist_ok=True)
        self.keys_file = self.data_dir / "api_keys.enc"
        self.key_file = self.data_dir / ".key"
        self._init_cipher()
        self.keys = {}
        self._load()
    
    def _init_cipher(self):
        if not self.key_file.exists():
            key = Fernet.generate_key()
            self.key_file.write_bytes(key)
        self.cipher = Fernet(self.key_file.read_bytes())
    
    def _load(self):
        # Try loading from encrypted file
        if self.keys_file.exists():
            try:
                enc_data = self.keys_file.read_text()
                dec_data = self.cipher.decrypt(enc_data.encode())
                self.keys = json.loads(dec_data)
            except:
                pass
        
        # Check environment variables
        for svc in ["openai", "anthropic"]:
            env_key = os.getenv(f"{svc.upper()}_API_KEY", "")
            if env_key and not env_key.startswith("your-"):
                if svc not in self.keys:
                    self.keys[svc] = env_key
                    self._save()
    
    def _save(self):
        data = json.dumps(self.keys)
        enc_data = self.cipher.encrypt(data.encode())
        self.keys_file.write_text(enc_data.decode())
    
    def get_key(self, service):
        return self.keys.get(service.lower())
    
    def set_key(self, service, key):
        self.keys[service.lower()] = key
        self._save()
        return True
    
    def remove_key(self, service):
        if service.lower() in self.keys:
            del self.keys[service.lower()]
            self._save()
            return True
        return False
    
    def list_keys(self):
        return {
            svc: {"configured": svc in self.keys}
            for svc in ["openai", "anthropic", "elevenlabs", "google"]
        }

api_keys = APIKeyManager()
