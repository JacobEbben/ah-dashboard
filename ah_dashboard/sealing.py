"""Server-side secret and encryption keys.

The server keeps one secret (``TOKEN_SEAL_SECRET``, or a random one generated
once into Flask's instance folder, which is never served). Purpose-specific
Fernet keys (AES + HMAC) are derived from it with HKDF. Currently used to
encrypt the AH tokens stored on disk (see ``ah/auth.py``).
"""

from __future__ import annotations

import base64
import logging
import os
import secrets

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from flask import Flask

log = logging.getLogger(__name__)

SECRET_FILENAME = "token_seal.secret"


def derive_key(secret: str, purpose: bytes, length: int = 32) -> bytes:
    """Raw key bytes for one purpose; different purposes get unrelated keys."""
    if len(secret) < 16:
        raise ValueError("TOKEN_SEAL_SECRET must be at least 16 characters")
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=purpose).derive(secret.encode("utf-8"))


def derive_fernet(secret: str, purpose: bytes) -> Fernet:
    """A Fernet key (AES + HMAC) for one purpose."""
    return Fernet(base64.urlsafe_b64encode(derive_key(secret, purpose)))


def server_secret(app: Flask) -> str:
    """The configured secret, or the generated one from the instance folder."""
    return app.config.get("TOKEN_SEAL_SECRET") or _instance_secret(app)


def _instance_secret(app) -> str:
    """Load the generated secret from the instance folder, creating it once."""
    os.makedirs(app.instance_path, exist_ok=True)
    path = os.path.join(app.instance_path, SECRET_FILENAME)
    try:
        with open(path, encoding="utf-8") as fh:
            secret = fh.read().strip()
        if secret:
            return secret
    except FileNotFoundError:
        pass
    secret = secrets.token_urlsafe(48)
    # O_EXCL: if two workers start at once, only one writes; the other re-reads.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(secret)
    log.warning("TOKEN_SEAL_SECRET not set; generated one in %s. Set TOKEN_SEAL_SECRET "
                "explicitly when running several servers.", path)
    return secret
