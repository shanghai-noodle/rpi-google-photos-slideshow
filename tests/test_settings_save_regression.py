"""
Regression tests for the "Network error while saving settings" bug.

Root cause: ``photoframe/app.py`` called ``re.match`` inside ``_is_valid_timezone`` without
``import re``. The Web Portal always sends the browser timezone on "Save & Apply Settings",
so every save raised ``NameError`` inside the HTTP handler thread, the connection was closed
without a reply, and the browser showed a generic "Network error". The pre-existing portal
test did not include ``timezone`` in its payload, so it never exercised the broken line.

These tests make sure that cannot happen again:

1. A static check that no module references an undefined global name (missing import).
2. The exact payload the browser sends is saved successfully over HTTP.
3. An unexpected exception in a settings/action handler returns a JSON 500 instead of
   silently dropping the connection.
"""

import builtins
import glob
import json
import os
import symtable
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from photoframe.app import SlideshowApp, parse_args
from photoframe.config import FrameConfigStore
from photoframe.hardware import HARDWARE_PROFILES
from photoframe.web_portal import _ThreadingHTTPServer, create_portal_handler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_MODULE_DUNDERS = {
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__path__", "__debug__", "__class__",
}


def _source_files():
    files = sorted(glob.glob(os.path.join(ROOT, "photoframe", "*.py")))
    files.append(os.path.join(ROOT, "slideshow.py"))
    return [f for f in files if os.path.isfile(f)]


def _undefined_global_names(path):
    """Return ``[(scope, lineno, name)]`` for names read as globals but never defined."""
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    top = symtable.symtable(src, path, "exec")

    defined = set(_MODULE_DUNDERS) | set(dir(builtins))
    for sym in top.get_symbols():
        if sym.is_assigned() or sym.is_imported() or sym.is_namespace():
            defined.add(sym.get_name())

    # Names created at runtime through ``global x`` + assignment inside a function.
    def collect_declared_globals(tab):
        for sym in tab.get_symbols():
            if sym.is_declared_global() and sym.is_assigned():
                defined.add(sym.get_name())
        for child in tab.get_children():
            collect_declared_globals(child)

    collect_declared_globals(top)

    missing = []

    def walk(tab):
        for sym in tab.get_symbols():
            name = sym.get_name()
            if (
                sym.is_referenced()
                and sym.is_global()
                and not sym.is_assigned()
                and not sym.is_imported()
                and name not in defined
            ):
                missing.append((tab.get_name(), tab.get_lineno(), name))
        for child in tab.get_children():
            walk(child)

    walk(top)
    return missing


class TestNoUndefinedNames(unittest.TestCase):
    def test_no_module_reads_an_undefined_global_name(self):
        """Catches a missing ``import`` (e.g. ``re``) in any module, in any code path."""
        problems = []
        for path in _source_files():
            for scope, lineno, name in _undefined_global_names(path):
                rel = os.path.relpath(path, ROOT)
                problems.append(f"{rel}: '{name}' is used in scope '{scope}' (line {lineno}) but never imported/defined")
        self.assertEqual(problems, [], "Undefined global names found:\n" + "\n".join(problems))


class TestSaveSettingsRegression(unittest.TestCase):
    # Mirrors the JSON built by `saveSettings()` in the Web Portal for the "self" frame.
    BROWSER_PAYLOAD = {
        "target": "self",
        "language": "en",
        "landscape_mode": "collage",
        "duration": 15,
        "transition": "fade",
        "videos": "play",
        "max_video_seconds": 20,
        "fit": "contain-blur",
        "refresh_hours": 12,
        "shuffle": True,
        "night_mode_enabled": True,
        "night_start": "22:00",
        "night_end": "06:00",
        "timezone": "America/Los_Angeles",
        "web_frame_profile": "auto",
        "album": "https://photos.app.goo.gl/TestAlbum12345",
        "device_name": "",
        "rotate": 0,
        "update_url": "",
    }

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.store = FrameConfigStore(path=os.path.join(self.tmp_dir.name, "frame_config.json"))
        self.app = SlideshowApp(
            parse_args([]), hw_profile=HARDWARE_PROFILES["standard"], config_store=self.store
        )
        # Never touch the host timezone (the real implementation may run `sudo timedatectl`).
        patcher = mock.patch.object(SlideshowApp, "_apply_timezone", autospec=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        handler_cls = create_portal_handler(self.app, self.store, login_fail_delay=0.0)
        self.server = _ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self._shutdown)

        code, _, set_cookie = self._request("/api/login", "POST", {"password": "admin"})
        self.assertEqual(code, 200)
        self.cookie = set_cookie.split(";", 1)[0]

    def _shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp_dir.cleanup()

    def _request(self, path, method="GET", payload=None, cookie=""):
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        req = urllib.request.Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")), resp.headers.get("Set-Cookie", "")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8")), e.headers.get("Set-Cookie", "")

    def test_is_valid_timezone(self):
        valid = SlideshowApp._is_valid_timezone
        self.assertFalse(valid(""))
        self.assertFalse(valid("../../etc/passwd"))
        self.assertFalse(valid("Not A/Zone!"))
        if os.path.isdir("/usr/share/zoneinfo"):
            self.assertTrue(valid("America/Los_Angeles"))
            self.assertTrue(valid("UTC"))
            self.assertFalse(valid("Mars/Olympus_Mons"))

    def test_browser_save_payload_with_timezone_is_accepted(self):
        code, res, _ = self._request("/api/settings", "POST", self.BROWSER_PAYLOAD, self.cookie)
        self.assertEqual(code, 200, res)
        self.assertTrue(res["ok"], res)
        saved = self.store.get_settings()
        self.assertEqual(saved.get("landscape_mode"), "collage")
        self.assertIn("TestAlbum12345", saved.get("album", ""))
        if os.path.isfile("/usr/share/zoneinfo/America/Los_Angeles"):
            self.assertEqual(saved.get("timezone"), "America/Los_Angeles")

    def test_invalid_timezone_is_ignored_not_fatal(self):
        payload = dict(self.BROWSER_PAYLOAD, timezone="Not/A_Real_Zone")
        code, res, _ = self._request("/api/settings", "POST", payload, self.cookie)
        self.assertEqual(code, 200, res)
        self.assertTrue(res["ok"], res)
        self.assertNotIn("timezone", self.store.get_settings())

    def test_unexpected_settings_error_returns_json_500(self):
        with mock.patch.object(self.app, "update_and_persist_settings", side_effect=RuntimeError("boom")):
            with self.assertLogs("Slideshow", level="ERROR"):
                code, res, _ = self._request("/api/settings", "POST", self.BROWSER_PAYLOAD, self.cookie)
        self.assertEqual(code, 500)
        self.assertFalse(res["ok"])
        self.assertIn("boom", res["error"])

    def test_unexpected_action_error_returns_json_500(self):
        with mock.patch.object(self.app, "queue_web_action", side_effect=RuntimeError("kaboom")):
            with self.assertLogs("Slideshow", level="ERROR"):
                code, res, _ = self._request(
                    "/api/action", "POST", {"target": "self", "action": "next"}, self.cookie
                )
        self.assertEqual(code, 500)
        self.assertFalse(res["ok"])
        self.assertIn("kaboom", res["error"])


if __name__ == "__main__":
    unittest.main()
