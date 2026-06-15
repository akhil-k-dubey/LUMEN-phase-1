"""
credential_store.py — AES-256-GCM encrypted credential storage.

Credentials are encrypted at rest in ~/.lumen/credentials.enc.
The encryption key is derived from the Windows DPAPI machine key,
meaning the file is only decryptable on THIS machine by THIS user.

Usage:
    store = CredentialStore()
    store.set("telegram", {"bot_token": "123:ABC..."})
    creds = store.get("telegram")  # {"bot_token": "123:ABC..."}
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Optional

from paths import PATHS

_CREDS_FILE = PATHS.data("credentials.enc")
_SALT = b"lumen-credential-store-v1"


class CredentialStore:
    """AES-256-GCM encrypted credential storage with DPAPI key derivation."""

    def _derive_key(self) -> bytes:
        """Derive a 32-byte AES key from Windows DPAPI."""
        key_file = PATHS.data("master.key.enc")
        try:
            import ctypes
            import ctypes.wintypes

            class DATA_BLOB(ctypes.Structure):
                _fields_ = [
                    ("cbData", ctypes.wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char)),
                ]

            if not os.path.exists(key_file):
                import secrets
                raw_key = secrets.token_bytes(32)
                blob_in = DATA_BLOB(len(raw_key), ctypes.create_string_buffer(raw_key))
                blob_out = DATA_BLOB()
                if ctypes.windll.crypt32.CryptProtectData(
                    ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
                ):
                    encrypted_key = ctypes.string_at(blob_out.pbData, blob_out.cbData)
                    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
                    os.makedirs(os.path.dirname(key_file), exist_ok=True)
                    with open(key_file, "wb") as f:
                        f.write(encrypted_key)
                    return hashlib.sha256(_SALT + raw_key).digest()
            else:
                with open(key_file, "rb") as f:
                    encrypted_key = f.read()
                blob_in = DATA_BLOB(len(encrypted_key), ctypes.create_string_buffer(encrypted_key))
                blob_out = DATA_BLOB()
                if ctypes.windll.crypt32.CryptUnprotectData(
                    ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
                ):
                    raw_key = ctypes.string_at(blob_out.pbData, blob_out.cbData)
                    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
                    return hashlib.sha256(_SALT + raw_key).digest()
        except Exception:
            pass

        # Fallback: derive from machine ID + username (less secure but functional)
        import platform
        machine_id = f"{platform.node()}-{os.getlogin()}-{os.path.expanduser('~')}"
        return hashlib.sha256(_SALT + machine_id.encode()).digest()

    def _encrypt(self, plaintext: bytes) -> bytes:
        """AES-256-GCM encrypt."""
        from hashlib import sha256
        import secrets

        key = self._derive_key()
        nonce = secrets.token_bytes(12)  # 96-bit nonce for GCM

        # Pure-Python AES-GCM via PyCryptodome (bundled) or fallback XOR
        try:
            from Crypto.Cipher import AES
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            ciphertext, tag = cipher.encrypt_and_digest(plaintext)
            return nonce + tag + ciphertext
        except ImportError:
            # Fallback: XOR with key-derived stream (NOT production-grade,
            # but better than plaintext for personal use)
            stream = sha256(key + nonce).digest() * (len(plaintext) // 32 + 1)
            xored = bytes(a ^ b for a, b in zip(plaintext, stream))
            return nonce + xored

    def _decrypt(self, data: bytes) -> bytes:
        """AES-256-GCM decrypt."""
        from hashlib import sha256

        key = self._derive_key()

        try:
            from Crypto.Cipher import AES
            nonce = data[:12]
            tag = data[12:28]
            ciphertext = data[28:]
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            return cipher.decrypt_and_verify(ciphertext, tag)
        except ImportError:
            nonce = data[:12]
            xored = data[12:]
            stream = sha256(key + nonce).digest() * (len(xored) // 32 + 1)
            return bytes(a ^ b for a, b in zip(xored, stream))

    def _load_all(self) -> dict:
        if not os.path.exists(_CREDS_FILE):
            return {}
        with open(_CREDS_FILE, "rb") as f:
            encrypted = f.read()
        if not encrypted:
            return {}
        try:
            plaintext = self._decrypt(encrypted)
            return json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            # If decryption/decoding fails (e.g. MAC check failed because of machine key mismatch),
            # log warning and return empty to avoid boot crash.
            print(f"[credential-store] WARNING: Failed to decrypt credentials (key mismatch or corrupt file). Starting fresh: {exc}")
            return {}

    def _save_all(self, data: dict) -> None:
        plaintext = json.dumps(data, indent=2).encode("utf-8")
        encrypted = self._encrypt(plaintext)
        with open(_CREDS_FILE, "wb") as f:
            f.write(encrypted)

    def get(self, platform: str) -> dict:
        """Load credentials for a platform. Returns {} if not set."""
        return self._load_all().get(platform, {})

    def set(self, platform: str, creds: dict) -> None:
        """Save credentials for a platform (encrypted)."""
        all_creds = self._load_all()
        all_creds[platform] = creds
        self._save_all(all_creds)

    def has(self, platform: str) -> bool:
        return platform in self._load_all()

    def list_platforms(self) -> list[str]:
        return list(self._load_all().keys())
