"""
Persistent Configuration Store & Password Security
==================================================
Stores user settings and salted PBKDF2-HMAC-SHA256 password hashes in `.frame_config.json`.
Writes occur ONLY when settings or passwords are explicitly updated via the Web Portal,
preserving zero SD card wear during normal 24/7 playback.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from typing import Any, Dict

from .hardware import logger

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(PROJECT_ROOT, ".frame_config.json")


class FrameConfigStore:
    """
    Thread-safe configuration & password store backed by `.frame_config.json`.
    Uses salted PBKDF2-HMAC-SHA256 (100,000 iterations) and deterministic HMAC session
    tokens derived from `password_salt:password_hash` so browser sessions survive restarts.
    """

    DEFAULT_PASSWORD = "admin"

    def __init__(self, path: str = DEFAULT_CONFIG_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.session_secret = os.urandom(32)
        self.data: Dict[str, Any] = {}
        self.load()

    @staticmethod
    def _hash_password(password: str, salt_hex: str) -> str:
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return dk.hex()

    def load(self):
        with self._lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        self.data = loaded
                except Exception as e:
                    logger.warning("Failed to load config file (%s): %s", self.path, e)

            if "password_salt" not in self.data or "password_hash" not in self.data:
                salt_hex = os.urandom(16).hex()
                self.data["password_salt"] = salt_hex
                self.data["password_hash"] = self._hash_password(self.DEFAULT_PASSWORD, salt_hex)
                self.data["is_default_password"] = True
                self._save_unlocked()

    def _save_unlocked(self):
        try:
            tmp_path = f"{self.path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp_path, self.path)
        except Exception as e:
            logger.error("Failed to save config file (%s): %s", self.path, e)

    def verify_password(self, candidate: str) -> bool:
        with self._lock:
            salt_hex = self.data.get("password_salt", "")
            expected_hash = self.data.get("password_hash", "")
        if not salt_hex or not expected_hash:
            return False
        actual_hash = self._hash_password(candidate or "", salt_hex)
        return hmac.compare_digest(actual_hash, expected_hash)

    def change_password(self, new_password: str):
        with self._lock:
            salt_hex = os.urandom(16).hex()
            self.data["password_salt"] = salt_hex
            self.data["password_hash"] = self._hash_password(new_password, salt_hex)
            self.data["is_default_password"] = False
            self.session_secret = os.urandom(32)
            self._save_unlocked()

    def is_default_password(self) -> bool:
        with self._lock:
            return bool(self.data.get("is_default_password", False))

    def get_settings(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.data.get("settings", {}))

    def update_settings(self, updates: Dict[str, Any]):
        with self._lock:
            current = dict(self.data.get("settings", {}))
            current.update(updates)
            self.data["settings"] = current
            self._save_unlocked()

    def save_settings(self, updates: Dict[str, Any]):
        self.update_settings(updates)

    def _get_signing_key(self) -> bytes:
        with self._lock:
            pw_hash = str(self.data.get("password_hash", "default"))
            pw_salt = str(self.data.get("password_salt", "salt"))
        return f"{pw_salt}:{pw_hash}".encode("utf-8")

    def create_session_token(self) -> str:
        ts = str(int(time.time()))
        sig = hmac.new(self._get_signing_key(), ts.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{ts}.{sig}"

    def verify_session_token(self, token: str) -> bool:
        if not token or "." not in token:
            return False
        ts_str, sig = token.split(".", 1)
        try:
            ts = int(ts_str)
        except ValueError:
            return False
        if abs(time.time() - ts) > 30 * 86400:
            return False
        expected = hmac.new(self._get_signing_key(), ts_str.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
