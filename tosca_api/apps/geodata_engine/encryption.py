"""
Encryption utilities for secure field handling
"""
from cryptography.fernet import Fernet
from django.conf import settings
import os
import base64


def get_encryption_key():
    """Get or generate encryption key for field encryption"""
    # Check if key exists in settings
    if hasattr(settings, 'FIELD_ENCRYPTION_KEY'):
        return settings.FIELD_ENCRYPTION_KEY.encode()
    
    # Check environment variable
    key = os.environ.get('FIELD_ENCRYPTION_KEY')
    if key:
        return key.encode()
    
    # Generate new key (for development only)
    # In production, this should be set explicitly
    key = Fernet.generate_key()
    print(f"Generated new encryption key: {key.decode()}")
    print("Set this as FIELD_ENCRYPTION_KEY in your environment")
    return key


def encrypt_value(plain_text):
    """Encrypt a plain text value"""
    if not plain_text:
        return plain_text
    
    key = get_encryption_key()
    fernet = Fernet(key)
    
    # Convert to bytes and encrypt
    encrypted_bytes = fernet.encrypt(plain_text.encode())
    # Base64 encode for database storage
    return base64.urlsafe_b64encode(encrypted_bytes).decode()


def decrypt_value(encrypted_text):
    """Decrypt an encrypted text value"""
    if not encrypted_text:
        return encrypted_text
    
    try:
        key = get_encryption_key()
        fernet = Fernet(key)
        
        # Base64 decode and decrypt
        encrypted_bytes = base64.urlsafe_b64decode(encrypted_text.encode())
        decrypted_bytes = fernet.decrypt(encrypted_bytes)
        return decrypted_bytes.decode()
    except Exception as e:
        # If decryption fails, assume it's plain text (for migration purposes)
        return encrypted_text


class EncryptedCharField:
    """Mixin for models to handle encrypted char fields"""
    
    def encrypt_field(self, field_name, value):
        """Encrypt a field value before saving"""
        if value and not self._is_encrypted(value):
            return encrypt_value(value)
        return value
    
    def decrypt_field(self, field_name, value):
        """Decrypt a field value after loading"""
        if value and self._is_encrypted(value):
            return decrypt_value(value)
        return value
    
    def _is_encrypted(self, value):
        """Check if a value appears to be encrypted (basic heuristic)"""
        try:
            # Try to decode as base64 - encrypted values should be base64 encoded
            base64.urlsafe_b64decode(value.encode())
            return len(value) > 50  # Encrypted values are typically longer
        except:
            return False