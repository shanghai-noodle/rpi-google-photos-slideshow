"""
Persistent Configuration Store & Password Security
==================================================
Stores user settings and salted PBKDF2-HMAC-SHA256 password hashes in `.frame_config.json`.
Writes occur ONLY when settings or passwords are explicitly updated via the Web Portal,
preserving zero SD card wear during normal 24/7 playback.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

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

    # ------------------------------------------------------------------
    # Encrypted Connected Album / Folder History Vault
    # ------------------------------------------------------------------

    def _get_vault_key_Unlocked(self) -> bytes:
        secret_hex = self.data.get("vault_secret", "")
        if not isinstance(secret_hex, str) or len(secret_hex) < 32:
            secret_hex = os.urandom(32).hex()
            self.data["vault_secret"] = secret_hex
            self._save_unlocked()
        try:
            raw_secret = bytes.fromhex(secret_hex)
        except ValueError:
            raw_secret = secret_hex.encode("utf-8")
        return hashlib.sha256(b"photoframe-album-vault-v1:" + raw_secret).digest()

    @staticmethod
    def _stream_xor(data_bytes: bytes, key: bytes, nonce: bytes) -> bytes:
        out = bytearray(len(data_bytes))
        offset = 0
        counter = 0
        while offset < len(data_bytes):
            counter_bytes = counter.to_bytes(4, "big")
            keystream = hmac.new(key, nonce + counter_bytes, hashlib.sha256).digest()
            chunk_len = min(32, len(data_bytes) - offset)
            for i in range(chunk_len):
                out[offset + i] = data_bytes[offset + i] ^ keystream[i]
            offset += chunk_len
            counter += 1
        return bytes(out)

    @classmethod
    def _encrypt_vault_payload(cls, plaintext: str, key: bytes) -> str:
        enc_key = hmac.new(key, b"enc", hashlib.sha256).digest()
        mac_key = hmac.new(key, b"mac", hashlib.sha256).digest()
        nonce = os.urandom(16)
        pt_bytes = plaintext.encode("utf-8")
        ct_bytes = cls._stream_xor(pt_bytes, enc_key, nonce)
        tag = hmac.new(mac_key, nonce + ct_bytes, hashlib.sha256).hexdigest()
        ct_b64 = base64.urlsafe_b64encode(ct_bytes).decode("ascii").rstrip("=")
        return f"v1.{nonce.hex()}.{ct_b64}.{tag}"

    @classmethod
    def _decrypt_vault_payload(cls, token: str, key: bytes) -> Optional[str]:
        if not isinstance(token, str) or not token.startswith("v1."):
            return None
        parts = token.split(".")
        if len(parts) != 4:
            return None
        _, nonce_hex, ct_b64, tag_hex = parts
        try:
            nonce = bytes.fromhex(nonce_hex)
            pad = "=" * ((4 - len(ct_b64) % 4) % 4)
            ct_bytes = base64.urlsafe_b64decode((ct_b64 + pad).encode("ascii"))
        except Exception:
            return None
        enc_key = hmac.new(key, b"enc", hashlib.sha256).digest()
        mac_key = hmac.new(key, b"mac", hashlib.sha256).digest()
        expected_tag = hmac.new(mac_key, nonce + ct_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(tag_hex, expected_tag):
            return None
        try:
            pt_bytes = cls._stream_xor(ct_bytes, enc_key, nonce)
            return pt_bytes.decode("utf-8")
        except Exception:
            return None

    def get_album_history(self, edition: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            key = self._get_vault_key_Unlocked()
            enc_list = self.data.get("encrypted_album_history", [])
            if not isinstance(enc_list, list):
                return []
            items: List[Dict[str, Any]] = []
            for token in enc_list:
                raw_json = self._decrypt_vault_payload(str(token), key)
                if not raw_json:
                    continue
                try:
                    entry = json.loads(raw_json)
                    if isinstance(entry, dict) and entry.get("album"):
                        if edition and entry.get("edition") and entry.get("edition") != edition:
                            continue
                        items.append(entry)
                except Exception:
                    continue
            items.sort(key=lambda x: float(x.get("connected_at", 0)), reverse=True)
            return items

    def record_connected_album(
        self,
        album_url: str,
        name: str = "",
        pwd: str = "",
        edition: str = "gphotos",
        max_entries: int = 25,
    ) -> Optional[Dict[str, Any]]:
        clean_url = (album_url or "").strip()
        if not clean_url or clean_url.lower() in ("demo", "none", "default"):
            return None
        clean_name = (name or "").strip() or clean_url
        clean_pwd = (pwd or "").strip()
        clean_ed = (edition or "gphotos").strip()

        with self._lock:
            key = self._get_vault_key_Unlocked()
            enc_list = self.data.get("encrypted_album_history", [])
            existing: List[Dict[str, Any]] = []
            if isinstance(enc_list, list):
                for token in enc_list:
                    raw_json = self._decrypt_vault_payload(str(token), key)
                    if not raw_json:
                        continue
                    try:
                        obj = json.loads(raw_json)
                        if isinstance(obj, dict) and obj.get("album"):
                            existing.append(obj)
                    except Exception:
                        continue

            entry_id = hashlib.sha256(f"{clean_ed}:{clean_url}".encode("utf-8")).hexdigest()[:16]
            filtered: List[Dict[str, Any]] = []
            prev_name = ""
            prev_pwd = ""
            for it in existing:
                if it.get("id") == entry_id or (it.get("album") == clean_url and it.get("edition", clean_ed) == clean_ed):
                    if not prev_name and it.get("name") and it.get("name") != clean_url:
                        prev_name = str(it["name"])
                    if not prev_pwd and it.get("pwd"):
                        prev_pwd = str(it["pwd"])
                    continue
                filtered.append(it)

            final_name = clean_name if (clean_name and clean_name != clean_url) else (prev_name or clean_url)
            final_pwd = clean_pwd or prev_pwd
            new_entry = {
                "id": entry_id,
                "album": clean_url,
                "pwd": final_pwd,
                "name": final_name,
                "edition": clean_ed,
                "connected_at": int(time.time()),
            }
            updated = [new_entry] + filtered[: max(1, max_entries - 1)]
            self.data["encrypted_album_history"] = [
                self._encrypt_vault_payload(json.dumps(item, ensure_ascii=False), key)
                for item in updated
            ]
            self._save_unlocked()
            return new_entry

    def remove_album_history_entry(self, target_id_or_url: str) -> bool:
        target = (target_id_or_url or "").strip()
        if not target:
            return False
        with self._lock:
            key = self._get_vault_key_Unlocked()
            enc_list = self.data.get("encrypted_album_history", [])
            if not isinstance(enc_list, list):
                return False
            kept: List[Dict[str, Any]] = []
            removed = False
            for token in enc_list:
                raw_json = self._decrypt_vault_payload(str(token), key)
                if not raw_json:
                    continue
                try:
                    obj = json.loads(raw_json)
                    if isinstance(obj, dict) and obj.get("album"):
                        if obj.get("id") == target or obj.get("album") == target:
                            removed = True
                            continue
                        kept.append(obj)
                except Exception:
                    continue
            if removed:
                self.data["encrypted_album_history"] = [
                    self._encrypt_vault_payload(json.dumps(item, ensure_ascii=False), key)
                    for item in kept
                ]
                self._save_unlocked()
            return removed
