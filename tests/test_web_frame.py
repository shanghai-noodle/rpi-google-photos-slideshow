import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

from photoframe.app import SlideshowApp, parse_args
from photoframe.collage import PhotoManager
from photoframe.config import FrameConfigStore
from photoframe.hardware import HARDWARE_PROFILES
import photoframe.web_frame as web_frame_mod
from photoframe.web_frame import (
    DEFAULT_WEB_FRAME_PORT,
    DISABLED_WEB_FRAME_HTML,
    WEB_FRAME_HTML,
    WebFrameController,
    _MINIMAL_1X1_JPEG,
    compute_web_canvas_dimensions,
    detect_web_client_profile,
    normalize_web_frame_profile,
)
from photoframe.web_portal import _ThreadingHTTPServer, create_portal_handler


class FakeTestImage:
    """Zero-dependency PIL Image test double supporting resize, copy, paste, point, convert, and JPEG save."""

    class Resampling:
        BILINEAR = 2

    def __init__(self, width: int, height: int, mode: str = "RGB"):
        self.width = int(width)
        self.height = int(height)
        self.mode = mode
        self.pasted = []

    @property
    def size(self):
        return (self.width, self.height)

    @classmethod
    def new(cls, mode: str, size, color=None):
        return cls(size[0], size[1], mode=mode)

    @classmethod
    def open(cls, path):
        if "port" in str(path):
            return cls(900, 1400)
        return cls(1600, 1000)

    def copy(self):
        return FakeTestImage(self.width, self.height, self.mode)

    def convert(self, mode: str):
        return FakeTestImage(self.width, self.height, mode)

    def resize(self, size, resample=None):
        return FakeTestImage(size[0], size[1], self.mode)

    def point(self, fn):
        return FakeTestImage(self.width, self.height, self.mode)

    def paste(self, im, box, mask=None):
        self.pasted.append((im, box))

    def crop_fit(self, size, centering=(0.5, 0.5)):
        return FakeTestImage(size[0], size[1], self.mode)

    def save(self, buf, format="JPEG", quality=80, optimize=False, progressive=False):
        buf.write(_MINIMAL_1X1_JPEG)

    def getdraw(self, mode=None):
        return FakeTestImageDraw._Drawer()


class FakeTestImageDraw:
    class _Drawer:
        def rectangle(self, *args, **kwargs):
            pass

        def rounded_rectangle(self, *args, **kwargs):
            pass

        def ellipse(self, *args, **kwargs):
            pass

    @classmethod
    def Draw(cls, img):
        return cls._Drawer()


class TestWebFrameUnit(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.cfg_path = os.path.join(self.tmp_dir.name, "frame_config.json")
        self.store = FrameConfigStore(self.cfg_path)

        self.land_paths = []
        self.port_paths = []
        for i in range(5):
            lp = os.path.join(self.tmp_dir.name, f"land_{i}.jpg")
            with open(lp, "wb") as f:
                f.write(_MINIMAL_1X1_JPEG)
            self.land_paths.append(lp)

            pp = os.path.join(self.tmp_dir.name, f"port_{i}.jpg")
            with open(pp, "wb") as f:
                f.write(_MINIMAL_1X1_JPEG)
            self.port_paths.append(pp)

        args = parse_args([
            "--album", "demo",
            "--local-dir", self.tmp_dir.name,
            "--web-port", "0",
            "--web-frame-port", "0",
            "--log-file", "none",
        ])
        self.app = SlideshowApp(args, hw_profile=HARDWARE_PROFILES["standard"], config_store=self.store)
        self.app.photo_manager = PhotoManager(
            target_w=1920,
            target_h=1080,
            album_url="demo",
            local_dir=self.tmp_dir.name,
            auto_refresh=False,
        )
        self.app.photo_manager.photo_sources = self.land_paths + self.port_paths
        self.app.photo_manager._download_raw_pil = lambda src: (
            FakeTestImage(900, 1400) if "port_" in str(src) else FakeTestImage(1600, 1000)
        )

    def tearDown(self):
        if hasattr(self, "app") and hasattr(self.app, "web_frame") and self.app.web_frame:
            self.app.web_frame.stop_dedicated_server()
        self.tmp_dir.cleanup()

    def test_normalize_and_detect_profiles(self):
        self.assertEqual(normalize_web_frame_profile(None), "auto")
        self.assertEqual(normalize_web_frame_profile(""), "auto")
        self.assertEqual(normalize_web_frame_profile("  LEGACY "), "legacy")
        self.assertEqual(normalize_web_frame_profile("HD"), "hd")
        self.assertEqual(normalize_web_frame_profile("unknown"), "auto")

        self.assertEqual(
            detect_web_client_profile("Mozilla/5.0 (Macintosh)", configured_profile="hd", query_profile="legacy"),
            "legacy",
        )
        self.assertEqual(
            detect_web_client_profile("iPad; CPU OS 9_3_5", configured_profile="legacy", query_profile="hd"),
            "hd",
        )
        self.assertEqual(
            detect_web_client_profile("Mozilla/5.0 (Macintosh)", configured_profile="legacy", query_profile="auto"),
            "legacy",
        )
        self.assertEqual(
            detect_web_client_profile("iPad; CPU OS 9_3_5", configured_profile="hd", query_profile=None),
            "hd",
        )

        ipad2_ua = "Mozilla/5.0 (iPad; CPU OS 9_3_5 like Mac OS X) AppleWebKit/601.1.46 (KHTML, like Gecko) Version/9.0 Mobile/13G36 Safari/601.1"
        self.assertEqual(detect_web_client_profile(ipad2_ua, "auto"), "legacy")
        self.assertEqual(detect_web_client_profile("Mozilla/5.0 (iPhone; CPU iPhone OS 12_5_7 like Mac OS X)", "auto"), "legacy")
        self.assertEqual(detect_web_client_profile("Mozilla/5.0 (Linux; Android 4.4.2; Nexus 7 Build/KOT49H)", "auto"), "legacy")
        self.assertEqual(detect_web_client_profile("Mozilla/5.0 (Linux; Android 7; SM-T580)", "auto"), "legacy")
        self.assertEqual(detect_web_client_profile("Mozilla/5.0 (Linux; U; en-us; KFAPWI Build/JDQ39) Silk/3.68", "auto"), "legacy")
        self.assertEqual(detect_web_client_profile("Mozilla/5.0 (compatible; MSIE 9.0; Windows NT 6.1; Trident/5.0)", "auto"), "legacy")

        self.assertEqual(detect_web_client_profile("Mozilla/5.0 (iPad; CPU OS 16_5 like Mac OS X)", "auto"), "hd")
        self.assertEqual(detect_web_client_profile("Mozilla/5.0 (Linux; Android 13; Pixel 7)", "auto"), "hd")
        self.assertEqual(detect_web_client_profile("", "auto"), "hd")
        self.assertEqual(detect_web_client_profile(None, "auto"), "hd")

    def test_compute_web_canvas_dimensions_edge_cases(self):
        self.assertEqual(compute_web_canvas_dimensions(1920, 1080, "legacy"), (1024, 576))
        self.assertEqual(compute_web_canvas_dimensions(1536, 2048, "legacy"), (768, 1024))
        self.assertEqual(compute_web_canvas_dimensions(1024, 768, "legacy"), (1024, 768))

        self.assertEqual(compute_web_canvas_dimensions(3840, 2160, "hd"), (1920, 1080))
        self.assertEqual(compute_web_canvas_dimensions(2160, 3840, "hd"), (1080, 1920))

        self.assertEqual(compute_web_canvas_dimensions("bad", None, "legacy"), (1024, 768))
        self.assertEqual(compute_web_canvas_dimensions(-100, 0, "hd"), (1024, 768))
        self.assertEqual(compute_web_canvas_dimensions(101, 99, "legacy"), (240, 240))

    def test_slide_composition_styles_and_sessions(self):
        with mock.patch.object(web_frame_mod, "PIL_AVAILABLE", True), \
             mock.patch.object(web_frame_mod, "Image", FakeTestImage), \
             mock.patch.object(web_frame_mod, "ImageDraw", FakeTestImageDraw):
            wf = self.app.web_frame
            wf.max_sessions = 3
            wf.max_raw_cache = 4

            initial_native_idx = self.app.photo_manager.current_index

            for mode in ("single", "collage", "stack", "triple", "bento", "trio", "classic-5", "mosaic-7", "sidebar-9", "mondrian-8", "grid-3-2-3"):
                self.app.args.landscape_mode = mode
                for fit in ("contain-blur", "contain", "cover"):
                    self.app.args.fit = fit
                    jpeg_l, prof_l, tw, th = wf.compose_client_slide_jpeg(
                        client_id="c1",
                        req_w=1024,
                        req_h=768,
                        query_profile="legacy",
                    )
                    self.assertTrue(jpeg_l.startswith(b"\xff\xd8"))
                    self.assertEqual(prof_l, "legacy")
                    self.assertEqual((tw, th), (1024, 768))

                jpeg_p, prof_p, ptw, pth = wf.compose_client_slide_jpeg(
                    client_id="c2",
                    req_w=1080,
                    req_h=1920,
                    query_profile="hd",
                )
                self.assertTrue(jpeg_p.startswith(b"\xff\xd8"))
                self.assertEqual(prof_p, "hd")
                self.assertEqual((ptw, pth), (1080, 1920))

            self.assertEqual(self.app.photo_manager.current_index, initial_native_idx)

            prev_bytes, _, _, _ = wf.compose_client_slide_jpeg(client_id="c1", req_w=1024, req_h=768, direction="prev")
            self.assertTrue(prev_bytes.startswith(b"\xff\xd8"))

            wf._touch_session("c3")
            wf._touch_session("c4")
            self.assertLessEqual(len(wf._sessions), 3)
            for s in wf._sessions.values():
                s.last_seen = time.time() - 200.0
            self.assertEqual(wf.get_active_client_count(), 0)
            wf._touch_session("c5")
            self.assertEqual(wf.get_active_client_count(), 1)

    def test_fallback_when_sources_empty_or_corrupt_or_video_skipped(self):
        with mock.patch.object(web_frame_mod, "PIL_AVAILABLE", True), \
             mock.patch.object(web_frame_mod, "Image", FakeTestImage), \
             mock.patch.object(web_frame_mod, "ImageDraw", FakeTestImageDraw):
            wf = self.app.web_frame
            vid_path = os.path.join(self.tmp_dir.name, "clip.mp4")
            with open(vid_path, "wb") as f:
                f.write(b"0000")
            self.app.photo_manager.video_bases.add(vid_path)
            self.app.photo_manager.photo_sources = [vid_path, self.land_paths[0]]
            self.app.args.videos = "skip"
            wf._raw_pil_cache.clear()
            jpeg_bytes, _, _, _ = wf.compose_client_slide_jpeg(client_id="vid_test", req_w=800, req_h=600)
            self.assertTrue(jpeg_bytes.startswith(b"\xff\xd8"))

            # Only 2 portraits in collage mode -> triggers 2-tile stack fallback
            self.app.photo_manager.photo_sources = [self.port_paths[0], self.port_paths[1]]
            self.app.args.landscape_mode = "classic-5"
            jpeg_two, _, _, _ = wf.compose_client_slide_jpeg(client_id="two_photo", req_w=1024, req_h=768)
            self.assertTrue(jpeg_two.startswith(b"\xff\xd8"))

            # Direct file fallback when _download_raw_pil returns None
            self.app.photo_manager._download_raw_pil = lambda src: None
            wf._raw_pil_cache.clear()
            jpeg_file_fb, _, _, _ = wf.compose_client_slide_jpeg(client_id="file_fb", req_w=1024, req_h=768)
            self.assertTrue(jpeg_file_fb.startswith(b"\xff\xd8"))

            # All sources fail to load -> placeholder JPEG
            self.app.photo_manager.photo_sources = ["/nonexistent/missing_1.jpg"]
            wf._raw_pil_cache.clear()
            jpeg_ph, _, _, _ = wf.compose_client_slide_jpeg(client_id="broken", req_w=640, req_h=480)
            self.assertTrue(jpeg_ph.startswith(b"\xff\xd8"))

            # Empty sources list -> placeholder JPEG
            self.app.photo_manager.photo_sources = []
            jpeg_empty, _, _, _ = wf.compose_client_slide_jpeg(client_id="empty", req_w=640, req_h=480)
            self.assertTrue(jpeg_empty.startswith(b"\xff\xd8"))

            # _encode_pil_to_jpeg edge cases
            self.assertEqual(wf._encode_pil_to_jpeg(None), _MINIMAL_1X1_JPEG)
            bad_img = FakeTestImage(10, 10, mode="RGB")
            bad_img.save = mock.Mock(side_effect=RuntimeError("encode error"))
            self.assertEqual(wf._encode_pil_to_jpeg(bad_img), _MINIMAL_1X1_JPEG)

            # Single-item collage fallback & HD auto collage style branches
            self.app.photo_manager.photo_sources = [self.port_paths[0]]
            self.app.photo_manager._download_raw_pil = lambda src: FakeTestImage(900, 1400)
            self.app.args.landscape_mode = "collage"
            wf._raw_pil_cache.clear()
            with mock.patch("random.random", return_value=0.99):
                # Only 1 photo when pop_more_fn returns None -> hits L1063 and L1083 and L1133-1135
                call_count = {"n": 0}
                def _one_shot_dl(src):
                    call_count["n"] += 1
                    return FakeTestImage(900, 1400) if call_count["n"] == 1 else None
                self.app.photo_manager._download_raw_pil = _one_shot_dl
                wf.compose_client_slide_jpeg(client_id="hd_port", req_w=1920, req_h=1080, query_profile="hd")
                call_count["n"] = 0
                self.app.photo_manager._download_raw_pil = lambda src: (
                    FakeTestImage(1600, 1000) if call_count.setdefault("m", 0) == 0 and not call_count.update(m=1) else None
                )
                wf._raw_pil_cache.clear()
                wf.compose_client_slide_jpeg(client_id="hd_land", req_w=1920, req_h=1080, query_profile="hd")

            # Edge cases for _load_pil_image, _get_source_snapshot, _make_bokeh_for_size, _build_placeholder_pil
            self.assertIsNone(wf._load_pil_image(""))
            self.app.photo_manager._download_raw_pil = mock.Mock(side_effect=RuntimeError("dl err"))
            FakeTestImage.open = classmethod(lambda cls, p: (_ for _ in ()).throw(RuntimeError("open err")))
            self.assertIsNone(wf._load_pil_image(self.land_paths[0]))
            FakeTestImage.open = classmethod(lambda cls, p: cls(900, 1400) if "port" in str(p) else cls(1600, 1000))

            # Resize exception in _load_pil_image returns raw img
            bad_resize_img = FakeTestImage(2000, 1500)
            bad_resize_img.resize = mock.Mock(side_effect=RuntimeError("resize err"))
            self.app.photo_manager._download_raw_pil = lambda src: bad_resize_img
            self.assertIs(wf._load_pil_image("custom_src", max_edge=800), bad_resize_img)

            # _make_bokeh_for_size exception fallback (both with and without PIL)
            self.assertIsNotNone(wf._make_bokeh_for_size(bad_resize_img, 640, 480))
        with mock.patch.object(web_frame_mod, "PIL_AVAILABLE", False), \
             mock.patch.object(web_frame_mod, "Image", None):
            self.assertIsNone(wf._build_placeholder_pil(640, 480))
            self.assertIs(wf._make_bokeh_for_size(bad_resize_img, 640, 480), bad_resize_img)
            plain_img = FakeTestImage(1600, 1000)
            wf._raw_pil_cache.clear()
            self.app.photo_manager._download_raw_pil = lambda src: plain_img
            self.assertIsNotNone(wf._load_pil_image("no_pil_src", max_edge=800))
            self.assertIsNotNone(wf._make_bokeh_for_size(plain_img, 640, 480))
            self.assertIsNotNone(wf._compose_single_for_size(self.app.photo_manager, plain_img, 800, 600, "contain-blur"))
            self.assertIsNotNone(wf._compose_single_for_size(self.app.photo_manager, FakeTestImage(800, 600), 800, 600, "contain"))

        # _get_source_snapshot when photo_manager is None or has no _sources_lock
        saved_pm = self.app.photo_manager
        self.app.photo_manager = None
        self.assertEqual(wf._get_source_snapshot(), [])
        class _UnlockedPM:
            photo_sources = ["a.jpg"]
        self.app.photo_manager = _UnlockedPM()
        self.assertEqual(wf._get_source_snapshot(), ["a.jpg"])
        self.app.photo_manager = saved_pm

        # set_enabled edge cases: port < 0, port change while running, config_store save error, bind error
        wf.set_enabled(False)
        wf.port = -1
        self.assertFalse(wf.start_dedicated_server())
        ok_msg = wf.set_enabled(True, profile="legacy", port=-1)
        self.assertTrue(ok_msg[0])
        self.assertIn("/frame", ok_msg[1])

        with mock.patch.object(self.store, "save_settings", side_effect=RuntimeError("disk full")):
            wf.set_enabled(False, persist=True)

        wf.port = 0
        with mock.patch("http.server.HTTPServer.__init__", side_effect=OSError("port in use")):
            wf.enabled = True
            self.assertFalse(wf.start_dedicated_server())

        # Direct in-thread handle_http_get test for non-matching path
        class _DummyHandler:
            path = "/api/other"
            headers = {"User-Agent": "Mozilla/5.0"}
        self.assertFalse(wf.handle_http_get(_DummyHandler(), is_dedicated_port=True))

    def test_http_endpoints_and_dedicated_server_on_off_lifecycle(self):
        handler_cls = create_portal_handler(self.app, self.store, login_fail_delay=0.0, port=0)
        portal_srv = _ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        portal_port = portal_srv.server_address[1]
        t = threading.Thread(target=portal_srv.serve_forever, daemon=True)
        t.start()

        try:
            self.assertFalse(self.app.web_frame.enabled)
            self.assertFalse(self.app.web_frame.is_running)

            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(f"http://127.0.0.1:{portal_port}/frame", timeout=3)
            self.assertEqual(ctx.exception.code, 403)
            err_html = ctx.exception.read().decode("utf-8")
            self.assertIn("Web Photo Frame is Off", err_html)

            with self.assertRaises(urllib.error.HTTPError) as ctx2:
                urllib.request.urlopen(f"http://127.0.0.1:{portal_port}/api/webframe/state", timeout=3)
            self.assertEqual(ctx2.exception.code, 403)

            with self.assertRaises(urllib.error.HTTPError) as ctx3:
                urllib.request.urlopen(f"http://127.0.0.1:{portal_port}/api/webframe/slide.jpg", timeout=3)
            self.assertEqual(ctx3.exception.code, 403)

            # Turn ON via queue_web_action("web_frame_on") using an ephemeral dedicated port
            self.app.web_frame.port = 0
            msg_on = self.app.queue_web_action("web_frame_on")
            self.assertIn("enabled", msg_on)
            self.assertTrue(self.app.web_frame.enabled)
            self.assertTrue(self.app.web_frame.is_running)
            ded_port = self.app.web_frame.bound_port
            self.assertGreater(ded_port, 0)

            # Calling start_dedicated_server again when already running returns True
            self.assertTrue(self.app.web_frame.start_dedicated_server())

            with urllib.request.urlopen(f"http://127.0.0.1:{portal_port}/frame", timeout=3) as r1:
                self.assertEqual(r1.status, 200)
                html1 = r1.read().decode("utf-8")
                self.assertIn("Web Photo Frame", html1)

            with urllib.request.urlopen(f"http://127.0.0.1:{ded_port}/", timeout=3) as r2:
                self.assertEqual(r2.status, 200)
                html2 = r2.read().decode("utf-8")
                self.assertIn("Web Photo Frame", html2)

            req_state = urllib.request.Request(
                f"http://127.0.0.1:{ded_port}/api/webframe/state?cid=ipad2&w=1024&h=768",
                headers={"User-Agent": "Mozilla/5.0 (iPad; CPU OS 9_3_5 like Mac OS X)"},
            )
            with urllib.request.urlopen(req_state, timeout=3) as r_st:
                st_data = json.loads(r_st.read().decode("utf-8"))
                self.assertTrue(st_data["enabled"])
                self.assertEqual(st_data["effective_profile"], "legacy")
                self.assertEqual(st_data["target_w"], 1024)
                self.assertEqual(st_data["target_h"], 768)

            req_slide = urllib.request.Request(
                f"http://127.0.0.1:{ded_port}/api/webframe/slide.jpg?cid=ipad2&w=1024&h=768",
                headers={"User-Agent": "Mozilla/5.0 (iPad; CPU OS 9_3_5 like Mac OS X)"},
            )
            with urllib.request.urlopen(req_slide, timeout=3) as r_sl:
                self.assertEqual(r_sl.status, 200)
                self.assertEqual(r_sl.headers.get("Content-Type"), "image/jpeg")
                self.assertEqual(r_sl.headers.get("X-WebFrame-Profile"), "legacy")
                jpg_data = r_sl.read()
                self.assertTrue(jpg_data.startswith(b"\xff\xd8"))

            self.app.update_and_persist_settings({
                "web_frame_enabled": True,
                "web_frame_profile": "legacy",
            })
            self.assertEqual(self.app.web_frame.profile, "legacy")

            # Change port live and verify restart
            self.app.web_frame.port = 9999
            self.app.web_frame.set_enabled(True, profile="hd", port=0)
            self.assertTrue(self.app.web_frame.is_running)

            # Turn OFF via queue_web_action("web_frame_toggle")
            msg_off = self.app.queue_web_action("web_frame_toggle")
            self.assertIn("disabled", msg_off)
            self.assertFalse(self.app.web_frame.enabled)
            self.assertFalse(self.app.web_frame.is_running)

        finally:
            portal_srv.shutdown()
            portal_srv.server_close()


if __name__ == "__main__":
    unittest.main()
