"""Fernet encryption at rest. The key is generated once into data/secret.key."""

import logging
import os
import threading

from cryptography.fernet import Fernet, InvalidToken

from app.config import SECRET_KEY_PATH, ensure_data_dirs

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_fernet: Fernet | None = None


def _load_or_create_key() -> bytes:
    ensure_data_dirs()
    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_bytes().strip()
    key = Fernet.generate_key()
    try:
        fd = os.open(SECRET_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return SECRET_KEY_PATH.read_bytes().strip()
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    logger.info("Generated new encryption key at %s", SECRET_KEY_PATH)
    return key


def get_fernet() -> Fernet:
    global _fernet
    with _lock:
        if _fernet is None:
            _fernet = Fernet(_load_or_create_key())
        return _fernet


def encrypt(plaintext: str) -> str:
    return get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str | None) -> str | None:
    """Return the plaintext, or None if the value is missing or cannot be decrypted."""
    if not ciphertext:
        return None
    try:
        return get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.error("Stored value could not be decrypted (secret.key changed?)")
        return None
