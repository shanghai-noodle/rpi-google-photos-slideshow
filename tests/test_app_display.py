import datetime
import os
import tempfile
import unittest

from photoframe.app import SlideshowApp, parse_args
from photoframe.collage import PhotoManager
from photoframe.config import FrameConfigStore
from photoframe.hardware import (
    HARDWARE_PROFILES,
    CappedRamFileHandler,
    detect_hardware_profile,
)


class TestAppDisplayRotationAndNightMode(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.cfg_path = os.path.join(self.tmp_dir.name, "frame_config.json")
        self.store = FrameConfigStore(path=self.cfg_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_default_cli_args_have_zero_degree_horizontal_rotation(self):
        """
        Verifies the default setup after initial configuration is 0° (Horizontal 1920x1080),
        collage layout mode, and English UI language.
        """
        args = parse_args([])
        self.assertEqual(args.rotate, 0)
        self.assertEqual(args.width, 1920)
        self.assertEqual(args.height, 1080)
        self.assertEqual(args.landscape_mode, "collage")
        self.assertEqual(args.transition, "none")
        self.assertEqual(args.language, "en")
        self.assertEqual(args.album, "demo")

        app = SlideshowApp(args, hw_profile=HARDWARE_PROFILES["standard"], config_store=self.store)
        self.assertEqual(app.rotation, 0)
        self.assertEqual(app.transition, "none")
        self.assertEqual((app.canvas_w, app.canvas_h), (1920, 1080))

    def test_slide_transition_options_and_live_persistence(self):
        """
        Verifies the default slide transition is 'none' (no transition) and all supported
        transition options ('fade', 'fade-black', 'slide-left', 'slide-right', 'slide-up',
        'wipe', 'random') can be configured via CLI and updated live via Web Portal settings.
        """
        args = parse_args([])
        app = SlideshowApp(args, hw_profile=HARDWARE_PROFILES["standard"], config_store=self.store)
        self.assertEqual(app.transition, "none")
        self.assertEqual(app.get_current_settings_dict()["transition"], "none")

        for mode in SlideshowApp.VALID_TRANSITIONS:
            app.update_and_persist_settings({"transition": mode})
            app._process_web_actions()
            self.assertEqual(app.transition, mode)
            self.assertEqual(app.get_status_snapshot()["transition"], mode)
            self.assertEqual(self.store.get_settings()["transition"], mode)

    def test_live_rotation_switching_updates_canvas_and_photo_manager(self):
        """
        Verifies changing `rotate` via `update_and_persist_settings` immediately updates
        `app.rotation`, `app.canvas_w`, `app.canvas_h`, and `photo_manager.target_w/h`.
        """
        args = parse_args([])
        app = SlideshowApp(args, hw_profile=HARDWARE_PROFILES["standard"], config_store=self.store)
        app.photo_manager = PhotoManager(
            app.canvas_w,
            app.canvas_h,
            album_url="demo",
            auto_refresh=False,
        )

        # Switch to 270° (Portrait 1080x1920)
        app.update_and_persist_settings({"rotate": 270})
        app._process_web_actions()
        self.assertEqual(app.rotation, 270)
        self.assertEqual((app.canvas_w, app.canvas_h), (1080, 1920))
        self.assertEqual((app.photo_manager.target_w, app.photo_manager.target_h), (1080, 1920))

        # Switch back to 0° (Landscape 1920x1080)
        app.update_and_persist_settings({"rotate": 0})
        app._process_web_actions()
        self.assertEqual(app.rotation, 0)
        self.assertEqual((app.canvas_w, app.canvas_h), (1920, 1080))
        self.assertEqual((app.photo_manager.target_w, app.photo_manager.target_h), (1920, 1080))

    def test_ffmpeg_video_rect_mapping_across_all_rotations(self):
        """
        Verifies `_map_canvas_rect_to_screen` accurately maps tile coordinates `(vx, vy, vw, vh)`
        and FFmpeg transpose/flip filters for `0°`, `90°`, `180°`, and `270°`.
        """
        # 0° (1920x1080 canvas)
        args0 = parse_args(["--rotate", "0"])
        app0 = SlideshowApp(args0, hw_profile=HARDWARE_PROFILES["standard"], config_store=self.store)
        sx, sy, sw, sh, vf = app0._map_canvas_rect_to_screen(14, 14, 800, 520)
        self.assertEqual((sx, sy, sw, sh), (14, 14, 800, 520))
        self.assertNotIn("transpose", vf)
        self.assertNotIn("hflip", vf)

        # 180° (1920x1080 canvas inverted)
        args180 = parse_args(["--rotate", "180"])
        app180 = SlideshowApp(args180, hw_profile=HARDWARE_PROFILES["standard"], config_store=self.store)
        sx, sy, sw, sh, vf = app180._map_canvas_rect_to_screen(14, 14, 800, 520)
        self.assertEqual((sx, sy, sw, sh), (1920 - 814, 1080 - 534, 800, 520))
        self.assertIn("hflip,vflip", vf)

        # 270° (1080x1920 portrait canvas on 1920x1080 physical display)
        args270 = parse_args(["--rotate", "270"])
        app270 = SlideshowApp(args270, hw_profile=HARDWARE_PROFILES["standard"], config_store=self.store)
        sx, sy, sw, sh, vf = app270._map_canvas_rect_to_screen(14, 100, 500, 600)
        self.assertEqual((sx, sy, sw, sh), (1920 - 700, 14, 600, 500))
        self.assertIn("transpose=1", vf)

        # 90° (1080x1920 portrait flipped canvas on 1920x1080 physical display)
        args90 = parse_args(["--rotate", "90"])
        app90 = SlideshowApp(args90, hw_profile=HARDWARE_PROFILES["standard"], config_store=self.store)
        sx, sy, sw, sh, vf = app90._map_canvas_rect_to_screen(14, 100, 500, 600)
        self.assertEqual((sx, sy, sw, sh), (100, 1080 - 514, 600, 500))
        self.assertIn("transpose=2", vf)

        # Subject-aware centering + 2px border inset alignment (eliminates poster-to-video crop jump)
        sx_i, sy_i, sw_i, sh_i, vf_i = app0._map_canvas_rect_to_screen(
            16, 16, 496, 396, centering=(0.50, 0.38), inset=2
        )
        self.assertEqual((sx_i, sy_i, sw_i, sh_i), (16, 16, 496, 396))
        self.assertIn("scale=500:400:force_original_aspect_ratio=increase", vf_i)
        self.assertIn("crop=496:396:(in_w-500)*0.5000+2:(in_h-400)*0.3800+2", vf_i)

        # Verify stop_video_playback increments _video_gen to prevent stale reader_worker frames
        gen_before = app0._video_gen
        app0._latest_video_frame = b"123"
        app0._video_eof = True
        app0.stop_video_playback()
        self.assertEqual(app0._video_gen, gen_before + 1)
        self.assertIsNone(app0._latest_video_frame)
        self.assertFalse(app0._video_eof)

    def test_night_mode_schedule_parsing_and_overnight_evaluation(self):
        """
        Verifies `_parse_time_str` (both 24-hour and AM/PM formats) and `is_within_night_schedule`
        across overnight (`22:00` -> `06:00`) and daytime (`09:00` -> `17:00`) windows.
        """
        self.assertEqual(SlideshowApp._parse_time_str("22:30", 0, 0), datetime.time(22, 30))
        self.assertEqual(SlideshowApp._parse_time_str("10:15pm", 0, 0), datetime.time(22, 15))
        self.assertEqual(SlideshowApp._parse_time_str("6am", 0, 0), datetime.time(6, 0))

        args = parse_args(["--night-start", "22:00", "--night-end", "06:00"])
        app = SlideshowApp(args, hw_profile=HARDWARE_PROFILES["standard"], config_store=self.store)

        self.assertTrue(app.is_within_night_schedule(datetime.datetime(2026, 11, 9, 23, 15)))
        self.assertTrue(app.is_within_night_schedule(datetime.datetime(2026, 11, 9, 2, 30)))
        self.assertFalse(app.is_within_night_schedule(datetime.datetime(2026, 11, 9, 6, 0)))
        self.assertFalse(app.is_within_night_schedule(datetime.datetime(2026, 11, 9, 14, 0)))

    def test_hardware_profiles_and_capped_ram_file_handler(self):
        key_std, prof_std = detect_hardware_profile("standard")
        self.assertEqual(key_std, "standard")
        self.assertEqual(prof_std["cdn_scale"], 1.0)

        key_low, prof_low = detect_hardware_profile("lowmem")
        self.assertEqual(key_low, "lowmem")
        self.assertLess(prof_low["cdn_scale"], 1.0)

        log_path = os.path.join(self.tmp_dir.name, "ram_test.log")
        handler = CappedRamFileHandler(log_path, maxBytes=200, backupCount=0, encoding="utf-8")
        import logging

        test_logger = logging.getLogger("test_capped_ram")
        test_logger.setLevel(logging.INFO)
        test_logger.addHandler(handler)
        try:
            for i in range(25):
                test_logger.info("Log line %02d with padding text", i)
            self.assertLessEqual(os.path.getsize(log_path), 260)
            with open(log_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("rotated in memory", content)
        finally:
            test_logger.removeHandler(handler)
            handler.close()

    def test_qr_code_mdns_and_portal_overlay_visibility(self):
        """
        Verifies QR code matrix generation, mDNS A-record packet construction for
        `photoframe.local`, build version tracking, and the 60-second / Demo Mode
        portal overlay visibility rules.
        """
        import threading
        import time
        from photoframe.mdns import MDNS_HOSTNAME, build_mdns_a_response
        from photoframe.qrcode_gen import encode_qr_matrix

        mat = encode_qr_matrix("http://192.168.86.42:8080")
        self.assertEqual(len(mat), 25)  # Version 2 QR code (25x25 modules)
        self.assertEqual(len(mat[0]), 25)
        # Top-left finder pattern outer corner and center must be dark
        self.assertTrue(mat[0][0])
        self.assertTrue(mat[3][3])

        pkt = build_mdns_a_response(0x1234, "192.168.86.42", hostname=MDNS_HOSTNAME)
        self.assertIsNotNone(pkt)
        self.assertIn(b"\nphotoframe\x05local\x00", pkt)
        self.assertTrue(pkt.endswith(b"\xc0\xa8\x56\x2a"))

        args = parse_args(["--album", "demo", "--rotate", "0"])
        app = SlideshowApp(args, hw_profile=HARDWARE_PROFILES["standard"], config_store=self.store)

        class _FakePM:
            def __init__(self, is_demo: bool):
                self.is_demo_mode = is_demo
                self.photo_sources = ["a"]
                self.video_bases = set()
                self._sources_lock = threading.Lock()

        # In Demo Mode: overlay remains visible even after 60 seconds
        app.photo_manager = _FakePM(True)
        app.app_start_time = time.time() - 120.0
        self.assertTrue(app._should_show_portal_overlay())

        # In Custom Album Mode: visible during first 60s, hidden after 60s
        app.photo_manager = _FakePM(False)
        app.app_start_time = time.time() - 15.0
        self.assertTrue(app._should_show_portal_overlay())
        app.app_start_time = time.time() - 65.0
        self.assertFalse(app._should_show_portal_overlay())

        # In Night Mode: always hidden
        app.is_night_mode = True
        app.photo_manager = _FakePM(True)
        self.assertFalse(app._should_show_portal_overlay())

        snap = app.get_status_snapshot()
        self.assertIn("app_version", snap)
        self.assertGreaterEqual(int(snap["app_version_code"]), 3)


if __name__ == "__main__":
    unittest.main()
