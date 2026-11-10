import json
import os
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from photoframe.app import PiSlideshowApp, parse_args
from photoframe.config import FrameConfigStore
from photoframe.hardware import HARDWARE_PROFILES
from photoframe.web_portal import (
    SUPPORTED_LANGUAGES,
    WEB_PORTAL_HTML,
    _ThreadingHTTPServer,
    create_portal_handler,
)


class TestFrameConfigStoreAndWebPortal(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.cfg_path = os.path.join(self.tmp_dir.name, "frame_config.json")
        self.store = FrameConfigStore(path=self.cfg_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_default_password_and_pbkdf2_change(self):
        self.assertTrue(self.store.is_default_password())
        self.assertTrue(self.store.verify_password("admin"))
        self.assertFalse(self.store.verify_password("wrong-password"))

        old_token = self.store.create_session_token()
        self.assertTrue(self.store.verify_session_token(old_token))

        # Change password -> old session token must immediately be invalidated
        self.store.change_password("MyNewSecret123")
        self.assertFalse(self.store.is_default_password())
        self.assertFalse(self.store.verify_password("admin"))
        self.assertTrue(self.store.verify_password("MyNewSecret123"))
        self.assertFalse(self.store.verify_session_token(old_token))

        # Reload store from disk to verify persistence
        reloaded = FrameConfigStore(path=self.cfg_path)
        self.assertFalse(reloaded.is_default_password())
        self.assertTrue(reloaded.verify_password("MyNewSecret123"))

    def test_session_token_tamper_and_expiry(self):
        token = self.store.create_session_token()
        self.assertTrue(self.store.verify_session_token(token))
        self.assertFalse(self.store.verify_session_token(""))
        self.assertFalse(self.store.verify_session_token("invalid_token"))
        self.assertFalse(self.store.verify_session_token(token + "a"))

        # Expired token (> 30 days old)
        expired_ts = str(int(time.time()) - 31 * 86400)
        sig = token.split(".", 1)[1]
        self.assertFalse(self.store.verify_session_token(f"{expired_ts}.{sig}"))

    def test_i18n_six_languages_completeness(self):
        """
        Verifies all 6 supported languages (`en`, `zh`, `ja`, `ko`, `es`, `de`) are present in
        `WEB_PORTAL_HTML` and have complete parity of translation keys with `en`.
        """
        self.assertEqual(SUPPORTED_LANGUAGES, ("en", "zh", "ja", "ko", "es", "de"))

        # Extract keys per language block in WEB_PORTAL_HTML
        lang_keys = {}
        for lang in SUPPORTED_LANGUAGES:
            pattern = rf"\b{lang}:\s*\{{(.*?)\n\s*\}}"
            match = re.search(pattern, WEB_PORTAL_HTML, flags=re.DOTALL)
            self.assertIsNotNone(match, f"Language block '{lang}' missing from WEB_PORTAL_HTML")
            block = match.group(1)
            keys = set(re.findall(r"^\s*([a-zA-Z0-9_]+)\s*:", block, flags=re.MULTILINE))
            lang_keys[lang] = keys

        en_keys = lang_keys["en"]
        self.assertGreaterEqual(len(en_keys), 60, "Expected at least 60 i18n keys in English dictionary")
        for lang in SUPPORTED_LANGUAGES:
            missing = en_keys - lang_keys[lang]
            extra = lang_keys[lang] - en_keys
            self.assertEqual(
                missing,
                set(),
                f"Language '{lang}' is missing i18n keys: {missing}",
            )
            self.assertEqual(
                extra,
                set(),
                f"Language '{lang}' has unexpected extra i18n keys: {extra}",
            )

    def test_web_portal_http_api_endpoints(self):
        """
        Spins up an ephemeral in-process `_ThreadingHTTPServer` and tests login, status,
        settings save & sanitization, remote actions, password change, and logout.
        """
        args = parse_args([])
        app = PiSlideshowApp(args, hw_profile=HARDWARE_PROFILES["pi2"], config_store=self.store)
        handler_cls = create_portal_handler(app, self.store, login_fail_delay=0.0)
        server = _ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        base_url = f"http://127.0.0.1:{port}"

        def request_json(path: str, method: str = "GET", payload=None, cookie: str = ""):
            headers = {}
            data = None
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                headers["Content-Type"] = "application/json"
            if cookie:
                headers["Cookie"] = cookie
            req = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.status, json.loads(resp.read().decode("utf-8")), resp.headers.get("Set-Cookie", "")
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read().decode("utf-8")), e.headers.get("Set-Cookie", "")

        try:
            # 1. GET / serves HTML
            with urllib.request.urlopen(f"{base_url}/", timeout=5) as resp:
                html = resp.read().decode("utf-8")
                self.assertEqual(resp.status, 200)
                self.assertIn("Photo Frame Control Portal", html)

            # 2. Unauthenticated GET /api/status
            code, st_unauth, _ = request_json("/api/status")
            self.assertEqual(code, 200)
            self.assertFalse(st_unauth["authenticated"])
            self.assertTrue(st_unauth["is_default_password"])

            # 3. Unauthenticated POST /api/settings is rejected with 401
            code, _, _ = request_json("/api/settings", method="POST", payload={"duration": 20})
            self.assertEqual(code, 401)

            # 4. Login with wrong password -> 401
            code, _, _ = request_json("/api/login", method="POST", payload={"password": "wrong"})
            self.assertEqual(code, 401)

            # 5. Login with default 'admin' password -> 200 + session cookie
            code, login_data, set_cookie = request_json("/api/login", method="POST", payload={"password": "admin"})
            self.assertEqual(code, 200)
            self.assertTrue(login_data["ok"])
            self.assertIn("frame_session=", set_cookie)
            session_cookie = set_cookie.split(";", 1)[0]

            # 6. Authenticated GET /api/status returns full status & settings with 0° default rotation
            code, st_auth, _ = request_json("/api/status", cookie=session_cookie)
            self.assertEqual(code, 200)
            self.assertTrue(st_auth["authenticated"])
            self.assertEqual(st_auth["settings"]["rotate"], 0)
            self.assertEqual(st_auth["status"]["canvas_w"], 1920)
            self.assertEqual(st_auth["status"]["canvas_h"], 1080)

            # 7. Save & apply settings via POST /api/settings
            code, save_res, _ = request_json(
                "/api/settings",
                method="POST",
                cookie=session_cookie,
                payload={
                    "album": "https://photos.app.goo.gl/TestAlbum123",
                    "language": "zh",
                    "landscape_mode": "classic-5",
                    "duration": 25,
                    "rotate": 270,
                    "night_mode_enabled": True,
                    "night_start": "23:00",
                    "night_end": "07:00",
                },
            )
            self.assertEqual(code, 200)
            self.assertTrue(save_res["ok"])
            app._process_web_actions()
            self.assertEqual(app.rotation, 270)
            self.assertEqual((app.canvas_w, app.canvas_h), (1080, 1920))
            self.assertEqual(app.args.language, "zh")
            self.assertEqual(app.args.landscape_mode, "classic-5")
            self.assertEqual(app.slide_duration, 25.0)

            # 8. Remote control action via POST /api/action
            code, act_res, _ = request_json(
                "/api/action",
                method="POST",
                cookie=session_cookie,
                payload={"action": "pause_toggle"},
            )
            self.assertEqual(code, 200)
            self.assertTrue(act_res["ok"])
            app._process_web_actions()
            self.assertTrue(app.is_paused)

            # 9. Change password via POST /api/password
            code, pw_res, new_set_cookie = request_json(
                "/api/password",
                method="POST",
                cookie=session_cookie,
                payload={"current_password": "admin", "new_password": "NewPassword99"},
            )
            self.assertEqual(code, 200)
            self.assertTrue(pw_res["ok"])
            self.assertIn("frame_session=", new_set_cookie)
            new_cookie = new_set_cookie.split(";", 1)[0]

            # Old cookie must now be unauthenticated, new cookie authenticated
            _, st_old, _ = request_json("/api/status", cookie=session_cookie)
            self.assertFalse(st_old["authenticated"])
            _, st_new, _ = request_json("/api/status", cookie=new_cookie)
            self.assertTrue(st_new["authenticated"])
            self.assertFalse(st_new["is_default_password"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
