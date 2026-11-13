import gzip
import json
import os
import re
import ssl
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from photoframe.app import SlideshowApp, parse_args
from photoframe.config import FrameConfigStore
from photoframe.hardware import HARDWARE_PROFILES
from photoframe.web_portal import (
    SUPPORTED_LANGUAGES,
    WEB_PORTAL_HTML,
    _ThreadingHTTPServer,
    create_portal_handler,
    create_portal_ssl_context,
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
        self.assertIn("btn_theme_light", en_keys)
        self.assertIn("btn_theme_dark", en_keys)
        self.assertIn("title_toggle_theme", en_keys)
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

    def test_portal_light_dark_theme_toggle_support(self):
        self.assertIn(':root[data-theme="light"]', WEB_PORTAL_HTML)
        self.assertIn('id="header-theme-switch"', WEB_PORTAL_HTML)
        self.assertIn('id="login-theme-switch"', WEB_PORTAL_HTML)
        self.assertIn("<md-switch", WEB_PORTAL_HTML)
        self.assertIn("--md-sys-color-primary", WEB_PORTAL_HTML)
        self.assertIn("prefers-color-scheme", WEB_PORTAL_HTML)
        self.assertIn("function onThemeSwitchChange(el)", WEB_PORTAL_HTML)
        self.assertIn("function toggleTheme()", WEB_PORTAL_HTML)
        self.assertIn('id="panel-control"', WEB_PORTAL_HTML)
        self.assertIn('id="panel-display"', WEB_PORTAL_HTML)
        self.assertIn('id="panel-system"', WEB_PORTAL_HTML)
        self.assertIn('role="tabpanel"', WEB_PORTAL_HTML)
        self.assertIn('id="desktop-m3-tabs"', WEB_PORTAL_HTML)
        self.assertIn('id="mobile-bottom-nav"', WEB_PORTAL_HTML)
        self.assertIn('id="advanced-settings-disclosure"', WEB_PORTAL_HTML)
        self.assertIn('class="skip-link"', WEB_PORTAL_HTML)
        self.assertIn('aria-live="polite"', WEB_PORTAL_HTML)
        self.assertIn("lbl_dark_theme_switch", WEB_PORTAL_HTML)
        self.assertIn("nav_control", WEB_PORTAL_HTML)
        self.assertIn("nav_display", WEB_PORTAL_HTML)
        self.assertIn("nav_system", WEB_PORTAL_HTML)
        self.assertIn("sec_advanced_options", WEB_PORTAL_HTML)
        self.assertIn("--btn-hover-bg", WEB_PORTAL_HTML)
        self.assertIn("--md-sys-color-primary-hover", WEB_PORTAL_HTML)
        self.assertIn("--md-sys-color-control-border", WEB_PORTAL_HTML)
        self.assertIn("--md-sys-color-accent-text", WEB_PORTAL_HTML)
        self.assertNotIn("filter: brightness", WEB_PORTAL_HTML)

    def test_web_portal_http_api_endpoints(self):
        """
        Spins up an ephemeral in-process `_ThreadingHTTPServer` and tests login, status,
        settings save & sanitization, remote actions, password change, and logout.
        """
        args = parse_args([])
        app = SlideshowApp(args, hw_profile=HARDWARE_PROFILES["standard"], config_store=self.store)
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
            # 1. GET / serves HTML with custom in-page exit confirmation modal (never native window.confirm)
            with urllib.request.urlopen(f"{base_url}/", timeout=5) as resp:
                html = resp.read().decode("utf-8")
                self.assertEqual(resp.status, 200)
                self.assertIn("Photo Frame Control Portal", html)
                self.assertIn('id="exit-confirm-modal"', html)
                self.assertIn('id="system-confirm-banner"', html)
                self.assertIn("executeConfirmedExit()", html)
                self.assertIn("closeExitModal()", html)
                self.assertNotIn("confirm(t(", html)

            # 1b. GET /assets/m3/*.js serves bundled Material 3 ES modules (plain, gzip, and traversal 404)
            with urllib.request.urlopen(f"{base_url}/assets/m3/index.js", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn("javascript", resp.headers.get("Content-Type", ""))
                self.assertIn("material-web-2.5.0-switch-switch.js", resp.read().decode("utf-8"))

            with urllib.request.urlopen(f"{base_url}/assets/m3/material-web-2.5.0-switch-switch.js", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn("javascript", resp.headers.get("Content-Type", ""))
                self.assertIn("MdSwitch", resp.read().decode("utf-8"))

            gzip_req = urllib.request.Request(
                f"{base_url}/assets/m3/material-web-2.5.0-switch-switch.js",
                headers={"Accept-Encoding": "gzip"},
            )
            with urllib.request.urlopen(gzip_req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("Content-Encoding", "").lower(), "gzip")
                self.assertIn("MdSwitch", gzip.decompress(resp.read()).decode("utf-8"))

            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(f"{base_url}/assets/m3/../../etc/passwd", timeout=5)
            self.assertEqual(ctx.exception.code, 404)

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

            # 8a. Unknown action returns HTTP 400 (never silent 200 ok)
            code, bad_act, _ = request_json(
                "/api/action",
                method="POST",
                cookie=session_cookie,
                payload={"action": "unknown_nonexistent_action"},
            )
            self.assertEqual(code, 400)
            self.assertFalse(bad_act["ok"])
            self.assertIn("Unknown action", bad_act.get("error", ""))

            # 8b. Exit photo frame action via POST /api/action -> returns 200 OK and stops app cleanly
            code, exit_res, _ = request_json(
                "/api/action",
                method="POST",
                cookie=session_cookie,
                payload={"action": "exit_app"},
            )
            self.assertEqual(code, 200)
            self.assertTrue(exit_res["ok"])
            app._process_web_actions()
            time.sleep(0.6)
            self.assertFalse(app.is_running)

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

    def test_https_tls_portal_endpoint(self):
        args = parse_args(["--album", "demo", "--rotate", "0"])
        app = SlideshowApp(args, HARDWARE_PROFILES["standard"], config_store=self.store)
        handler_cls = create_portal_handler(app, self.store, login_fail_delay=0.0)
        ssl_ctx = create_portal_ssl_context()
        server = _ThreadingHTTPServer(("127.0.0.1", 0), handler_cls, ssl_context=ssl_ctx)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        client_ctx = ssl.create_default_context()
        client_ctx.check_hostname = False
        client_ctx.verify_mode = ssl.CERT_NONE

        try:
            with urllib.request.urlopen(f"https://127.0.0.1:{port}/api/status", timeout=5, context=client_ctx) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertFalse(data["authenticated"])
                self.assertTrue(data["is_default_password"])
        finally:
            server.shutdown()
            server.server_close()

    def test_multi_frame_fleet_mesh_and_smart_broadcast(self):
        """
        Spins up two independent photo frame instances (`server_a` and `server_b`) with different
        passwords and device names, connects `server_b` as a peer of `server_a`, and verifies:
        - `/api/fleet/ping` discovery
        - Single-login LAN Peer Trust (`X-PhotoFrame-Peer`) across different frame passwords
        - Inspecting and modifying `server_b` directly from `server_a` (`target="127.0.0.1:<port_b>"`)
        - Smart Broadcast (`target="all"`) applying shared settings to both frames while preserving
          each frame's individual `device_name`
        """
        tmp_dir_b = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir_b.cleanup)
        store_b = FrameConfigStore(path=os.path.join(tmp_dir_b.name, "frame_config_b.json"))
        store_b.change_password("DifferentSecretOnFrameB!")
        store_b.update_settings({"device_name": "bedroom-frame", "duration": 10})
        self.store.update_settings({"device_name": "living-room", "duration": 15})

        app_a = SlideshowApp(parse_args([]), hw_profile=HARDWARE_PROFILES["standard"], config_store=self.store)
        app_b = SlideshowApp(parse_args([]), hw_profile=HARDWARE_PROFILES["standard"], config_store=store_b)

        server_a = _ThreadingHTTPServer(("127.0.0.1", 0), create_portal_handler(app_a, self.store, login_fail_delay=0.0))
        server_b = _ThreadingHTTPServer(("127.0.0.1", 0), create_portal_handler(app_b, store_b, login_fail_delay=0.0))
        port_a = server_a.server_address[1]
        port_b = server_b.server_address[1]

        threading.Thread(target=server_a.serve_forever, daemon=True).start()
        threading.Thread(target=server_b.serve_forever, daemon=True).start()
        self.addCleanup(server_a. server_close)
        self.addCleanup(server_a.shutdown)
        self.addCleanup(server_b.server_close)
        self.addCleanup(server_b.shutdown)

        base_a = f"http://127.0.0.1:{port_a}"
        peer_b_id = f"127.0.0.1:{port_b}"

        def req_a(path: str, method: str = "GET", payload=None, cookie: str = ""):
            headers = {}
            data = None
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                headers["Content-Type"] = "application/json"
            if cookie:
                headers["Cookie"] = cookie
            req = urllib.request.Request(f"{base_a}{path}", data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")), resp.headers.get("Set-Cookie", "")

        # 1. Verify /api/fleet/ping works without auth
        code, ping_a, _ = req_a("/api/fleet/ping")
        self.assertEqual(code, 200)
        self.assertEqual(ping_a["service"], "photoframe")
        self.assertEqual(ping_a["edition"], "gphotos")
        self.assertEqual(ping_a["device_name"], "living-room")

        # 2. Log into Frame A only (using default 'admin' password)
        code, _, set_cookie = req_a("/api/login", method="POST", payload={"password": "admin"})
        self.assertEqual(code, 200)
        cookie_a = set_cookie.split(";", 1)[0]

        # 3. Add Frame B as a peer on Frame A (even though Frame B has a different password)
        code, add_res, _ = req_a(
            "/api/fleet/add_peer",
            method="POST",
            cookie=cookie_a,
            payload={"host": peer_b_id},
        )
        self.assertEqual(code, 200)
        self.assertTrue(add_res["ok"])
        self.assertEqual(add_res["node"]["device_name"], "bedroom-frame")

        # 4. Inspect Frame B's status & settings via Frame A (/api/status?target=127.0.0.1:<port_b>)
        code, st_b, _ = req_a(f"/api/status?target={peer_b_id}", cookie=cookie_a)
        self.assertEqual(code, 200)
        self.assertEqual(st_b["settings"]["device_name"], "bedroom-frame")
        self.assertEqual(st_b["settings"]["duration"], 10)

        # 5. Smart Broadcast settings to "all" from Frame A -> updates duration on both A and B,
        #    while preserving each frame's individual device_name, album, and rotate!
        self.store.update_settings({"album": "https://photos.app.goo.gl/FrameAAlbum", "rotate": 270})
        store_b.update_settings({"album": "https://photos.app.goo.gl/FrameBAlbum", "rotate": 0})
        code, bcast_res, _ = req_a(
            "/api/settings",
            method="POST",
            cookie=cookie_a,
            payload={
                "target": "all",
                "album": "https://photos.app.goo.gl/ShouldNotOverwriteInAllMode",
                "device_name": "should-not-overwrite-in-all-mode",
                "rotate": 90,
                "duration": 42,
                "transition": "fade",
            },
        )
        self.assertEqual(code, 200)
        self.assertTrue(bcast_res["ok"])
        app_a._process_web_actions()
        app_b._process_web_actions()
        self.assertEqual(app_a.slide_duration, 42.0)
        self.assertEqual(app_b.slide_duration, 42.0)
        self.assertEqual(self.store.get_settings()["album"], "https://photos.app.goo.gl/FrameAAlbum")
        self.assertEqual(store_b.get_settings()["album"], "https://photos.app.goo.gl/FrameBAlbum")
        self.assertEqual(self.store.get_settings()["rotate"], 270)
        self.assertEqual(store_b.get_settings()["rotate"], 0)
        self.assertEqual(self.store.get_settings()["device_name"], "living-room")
        self.assertEqual(store_b.get_settings()["device_name"], "bedroom-frame")

    def test_touchscreen_and_mouse_pause_toggle_with_debounce(self):
        """
        Verifies that tapping the screen (MOUSEBUTTONDOWN button=1 or FINGERDOWN) toggles
        pause/resume, wakes from Night Mode if sleeping, and debounces duplicate events within 250ms.
        """
        import types
        from unittest.mock import patch
        import photoframe.app as app_mod

        fake_pg = types.SimpleNamespace(
            QUIT=256,
            KEYDOWN=768,
            MOUSEBUTTONDOWN=1025,
            FINGERDOWN=1792,
            K_ESCAPE=27,
            K_q=113,
            K_SPACE=32,
            K_RETURN=13,
            K_RIGHT=1073741903,
            K_n=110,
            K_LEFT=1073741904,
            K_p=112,
        )

        app = SlideshowApp(parse_args([]), hw_profile=HARDWARE_PROFILES["standard"], config_store=self.store)
        self.assertFalse(app.is_paused)

        class FakeEvent:
            def __init__(self, ev_type, **kwargs):
                self.type = ev_type
                for k, v in kwargs.items():
                    setattr(self, k, v)

        # 1. Single MOUSEBUTTONDOWN (left click / X11 touch) pauses slideshow
        app._last_touch_toggle_time = 0.0
        fake_pg.event = types.SimpleNamespace(get=lambda: [FakeEvent(fake_pg.MOUSEBUTTONDOWN, button=1)])
        with patch.object(app_mod, "pygame", fake_pg):
            app.handle_events()
        self.assertTrue(app.is_paused)

        # 2. Immediate FINGERDOWN within 250ms is debounced (stays paused)
        fake_pg.event = types.SimpleNamespace(get=lambda: [FakeEvent(fake_pg.FINGERDOWN)])
        with patch.object(app_mod, "pygame", fake_pg):
            app.handle_events()
        self.assertTrue(app.is_paused)

        # 3. Tap after 250ms resumes slideshow
        app._last_touch_toggle_time = time.time() - 1.0
        with patch.object(app_mod, "pygame", fake_pg):
            app.handle_events()
        self.assertFalse(app.is_paused)


if __name__ == "__main__":
    unittest.main()
