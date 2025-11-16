#!/usr/bin/env python3
"""
Generate VAPID Keys for Web Push Notifications
Uses cryptography library directly to avoid py-vapid compatibility issues
"""

import os
from pathlib import Path

# Auto-detect project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

def generate_vapid_keys():
    """Generate VAPID key pair"""
    print("🔑 Generating VAPID keys...")
    
    # Generate EC private key (SECP256R1/P-256 curve required for VAPID)
    private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    
    # Serialize private key to PEM format
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    
    # Get public key
    public_key = private_key.public_key()
    public_numbers = public_key.public_numbers()
    
    # Convert public key to uncompressed format (required for VAPID)
    # Format: 0x04 || X (32 bytes) || Y (32 bytes)
    x_bytes = public_numbers.x.to_bytes(32, byteorder='big')
    y_bytes = public_numbers.y.to_bytes(32, byteorder='big')
    public_key_bytes = b'\x04' + x_bytes + y_bytes
    
    # Encode to URL-safe base64 (without padding)
    public_key_b64 = base64.urlsafe_b64encode(public_key_bytes).decode('utf-8').rstrip('=')
    
    return private_pem, public_key_b64


def save_keys(private_pem, public_key_b64, output_dir=str(PROJECT_ROOT / "config")):
    """Save keys to files"""
    os.makedirs(output_dir, exist_ok=True)
    
    private_path = os.path.join(output_dir, "vapid_private.pem")
    public_path = os.path.join(output_dir, "vapid_public.pem")
    
    # Save private key
    with open(private_path, 'wb') as f:
        f.write(private_pem)
    print(f"✅ Private key saved: {private_path}")
    
    # Save public key
    with open(public_path, 'w') as f:
        f.write(public_key_b64)
    print(f"✅ Public key saved: {public_path}")
    
    # Set permissions
    os.chmod(private_path, 0o600)  # Private key readable only by owner
    os.chmod(public_path, 0o644)   # Public key readable by all
    
    return private_path, public_path


if __name__ == "__main__":
    print("=" * 60)
    print("Zoe VAPID Key Generator")
    print("=" * 60)
    print()
    
    # Check if keys already exist
    private_path = str(PROJECT_ROOT / "config/vapid_private.pem")
    public_path = str(PROJECT_ROOT / "config/vapid_public.pem")
    
    if os.path.exists(private_path) and os.path.exists(public_path):
        print("⚠️  VAPID keys already exist!")
        print(f"   Private: {private_path}")
        print(f"   Public: {public_path}")
        print()
        response = input("Overwrite existing keys? (yes/no): ").lower()
        if response != 'yes':
            print("Cancelled. Keeping existing keys.")
            exit(0)
        print()
    
    # Generate keys
    try:
        private_pem, public_key_b64 = generate_vapid_keys()
        private_path, public_path = save_keys(private_pem, public_key_b64)
        
        print()
        print("=" * 60)
        print("✅ VAPID Keys Generated Successfully!")
        print("=" * 60)
        print()
        print("📁 Key Files:")
        print(f"   Private: {private_path}")
        print(f"   Public: {public_path}")
        print()
        print("🔑 Public Key (for frontend):")
        print(f"   {public_key_b64}")
        print()
        print("🎯 Next Steps:")
        print("   1. Restart zoe-core: docker restart zoe-core")
        print("   2. Test API: curl http://localhost:8000/api/push/vapid-public-key")
        print("   3. Should return the public key shown above")
        print()
        
    except Exception as e:
        print(f"❌ Error generating keys: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

