"""
Encryption utilities for secure field handling
"""
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
import logging

logger = logging.getLogger(__name__)


def get_encryption_key() -> bytes:
    """
    Return the Fernet encryption key as bytes.

    Reads from settings.FIELD_ENCRYPTION_KEY which is populated by the
    FIELD_ENCRYPTION_KEY environment variable (set in .env.dev / .env.prod).

    Raises ImproperlyConfigured if the key is missing — never silently
    generates a throwaway key, which would make every decrypt silently fail.
    """
    key = getattr(settings, 'FIELD_ENCRYPTION_KEY', None)
    if not key:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEY is not set. "
            "Generate one with: "
            "uv run python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" "
            "and add it to your .env file."
        )
    if isinstance(key, str):
        return key.encode()
    return key


def encrypt_value(plain_text: str) -> str:
    """
    Encrypt a plain text value using Fernet (AES-128-CBC + HMAC-SHA256).
    Returns the raw Fernet token as a string — no extra base64 wrapping.
    """
    if not plain_text:
        return plain_text

    fernet = Fernet(get_encryption_key())
    return fernet.encrypt(plain_text.encode()).decode()


def decrypt_value(encrypted_text: str) -> str:
    """
    Decrypt a Fernet-encrypted value.
    Raises ValueError on decryption failure — callers must handle this explicitly.
    Never silently returns ciphertext as if it were plain text.
    """
    if not encrypted_text:
        return encrypted_text

    try:
        fernet = Fernet(get_encryption_key())
        return fernet.decrypt(encrypted_text.encode()).decode()
    except Exception as exc:
        logger.error('decrypt_value failed — wrong key or corrupt data: %s', exc)
        raise ValueError(f'Failed to decrypt value: {exc}') from exc


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
    
    def _is_encrypted(self, value: str) -> bool:
        """
        Fernet tokens always start with 'gAAAAA' (version byte 0x80, base64url-encoded).
        This is a reliable marker — no length heuristic needed.
        """
        return isinstance(value, str) and value.startswith('gAAAAA')