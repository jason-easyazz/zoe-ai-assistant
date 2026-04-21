"""
Encryption utility for sensitive data storage
Uses Fernet (symmetric encryption) from cryptography library
"""
import os
import base64
import logging
from pathlib import Path
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

class EncryptionManager:
    """Handles encryption/decryption of sensitive data"""
    
    def __init__(self):
        self._cipher = None
        self._load_or_create_key()
    
    def _load_or_create_key(self):
        """Load or create encryption key"""
        key_file = Path("/app/data/.encryption_key")
        
        if key_file.exists():
            with open(key_file, 'rb') as f:
                key = f.read()
        else:
            # Generate new key
            key = Fernet.generate_key()
            
            # Save securely
            key_file.parent.mkdir(exist_ok=True)
            with open(key_file, 'wb') as f:
                f.write(key)
            
            # Set restrictive permissions (owner read/write only)
            os.chmod(key_file, 0o600)
            logger.info("Generated new encryption key")
        
        self._cipher = Fernet(key)
    
    def encrypt(self, data: str) -> str:
        """Encrypt string data and return base64-encoded ciphertext"""
        if not data:
            return ""
        
        encrypted = self._cipher.encrypt(data.encode('utf-8'))
        return base64.b64encode(encrypted).decode('utf-8')
    
    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt base64-encoded ciphertext and return original string"""
        if not encrypted_data:
            return ""
        
        try:
            encrypted = base64.b64decode(encrypted_data.encode('utf-8'))
            decrypted = self._cipher.decrypt(encrypted)
            return decrypted.decode('utf-8')
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise ValueError("Failed to decrypt data - key may have changed")

# Global instance
_encryption_manager = None

def get_encryption_manager() -> EncryptionManager:
    """Get or create the encryption manager singleton"""
    global _encryption_manager
    if _encryption_manager is None:
        _encryption_manager = EncryptionManager()
    return _encryption_manager

