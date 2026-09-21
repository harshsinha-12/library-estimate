"""Optional client-side AES-GCM envelope for object-store evidence."""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.app.storage.objects import ObjectStore

MAGIC = b"LSENC1\x00"


class EncryptedObjectStore:
    def __init__(self, inner: ObjectStore, key_base64: str) -> None:
        try:
            key = base64.b64decode(key_base64, validate=True)
        except ValueError as error:
            raise ValueError("LIBRARY_DATA_ENCRYPTION_KEY must be base64") from error
        if len(key) != 32:
            raise ValueError("LIBRARY_DATA_ENCRYPTION_KEY must contain 32 bytes")
        self.inner = inner
        self.cipher = AESGCM(key)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        nonce = os.urandom(12)
        sealed = MAGIC + nonce + self.cipher.encrypt(nonce, data, key.encode("utf-8"))
        self.inner.put(key, sealed, "application/octet-stream")

    def get(self, key: str) -> bytes:
        sealed = self.inner.get(key)
        if not sealed.startswith(MAGIC):
            raise ValueError("object is not encrypted with the configured key")
        nonce = sealed[len(MAGIC):len(MAGIC) + 12]
        body = sealed[len(MAGIC) + 12:]
        return self.cipher.decrypt(nonce, body, key.encode("utf-8"))

    def exists(self, key: str) -> bool:
        return self.inner.exists(key)

    def delete(self, key: str) -> None:
        self.inner.delete(key)

    def list_prefix(self, prefix: str) -> list[str]:
        return self.inner.list_prefix(prefix)

    def ping(self) -> None:
        self.inner.ping()
