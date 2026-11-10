import datetime
import os
import tempfile
import unittest

from photoframe.app import PiSlideshowApp, parse_args
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

        app = PiSlideshowApp(args, hw_profile=HARDWARE_PROFILES["pi2"], config_store=self.store)
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
        app = PiSlideshowApp(args, hw_profile=HARDWARE_PROFILES["pi2"], config_store=self.store)
        self.assertEqual(app.transition, "none")
        self.assertEqual(app.get_current_settings_dict()["transition"], "none")

        for mode in PiSlideshowApp.VALID_TRANSITIONS:
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
        app = PiSlideshowApp(args, hw_profile=HARDWARE_PROFILES["pi2"], config_store=self.store)
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
        app0 = PiSlideshowApp(args0, hw_profile=HARDWARE_PROFILES["pi2"], config_store=self.store)
        sx, sy, sw, sh, vf = app0._map_canvas_rect_to_screen(14, 14, 800, 520)
        self.assertEqual((sx, sy, sw, sh), (14, 14, 800, 520))
        self.assertNotIn("transpose", vf)
        self.assertNotIn("hflip", vf)

        # 180° (1920x1080 canvas inverted)
        args180 = parse_args(["--rotate", "180"])
        app180 = PiSlideshowApp(args180, hw_profile=HARDWARE_PROFILES["pi2"], config_store=self.store)
        sx, sy, sw, sh, vf = app180._map_canvas_rect_to_screen(14, 14, 800, 520)
        self.assertEqual((sx, sy, sw, sh), (1920 - 814, 1080 - 534, 800, 520))
        self.assertIn("hflip,vflip", vf)

        # 270° (1080x1920 portrait canvas on 1920x1080 physical display)
        args270 = parse_args(["--rotate", "270"])
        app270 = PiSlideshowApp(args270, hw_profile=HARDWARE_PROFILES["pi2"], config_store=self.store)
        sx, sy, sw, sh, vf = app270._map_canvas_rect_to_screen(14, 100, 500, 600)
        self.assertEqual((sx, sy, sw, sh), (1920 - 700, 14, 600, 500))
        self.assertIn("transpose=1", vf)

        # 90° (1080x1920 portrait flipped canvas on 1920x1080 physical display)
        args90 = parse_args(["--rotate", "90"])
        app90 = PiSlideshowApp(args90, hw_profile=HARDWARE_PROFILES["pi2"], config_store=self.store)
        sx, sy, sw, sh, vf = app90._map_canvas_rect_to_screen(14, 100, 500, 600)
        self.assertEqual((sx, sy, sw, sh), (100, 1080 - 514, 600, 500))
        self.assertIn("transpose=2", vf)

    def test_night_mode_schedule_parsing_and_overnight_evaluation(self):
        """
        Verifies `_parse_time_str` (both 24-hour and AM/PM formats) and `is_within_night_schedule`
        across overnight (`22:00` -> `06:00`) and daytime (`09:00` -> `17:00`) windows.
        """
        self.assertEqual(PiSlideshowApp._parse_time_str("22:30", 0, 0), datetime.time(22, 30))
        self.assertEqual(PiSlideshowApp._parse_time_str("10:15pm", 0, 0), datetime.time(22, 15))
        self.assertEqual(PiSlideshowApp._parse_time_str("6am", 0, 0), datetime.time(6, 0))

        args = parse_args(["--night-start", "22:00", "--night-end", "06:00"])
        app = PiSlideshowApp(args, hw_profile=HARDWARE_PROFILES["pi2"], config_store=self.store)

        self.assertTrue(app.is_within_night_schedule(datetime.datetime(2026, 11, 9, 23, 15)))
        self.assertTrue(app.is_within_night_schedule(datetime.datetime(2026, 11, 9, 2, 30)))
        self.assertFalse(app.is_within_night_schedule(datetime.datetime(2026, 11, 9, 6, 0)))
        self.assertFalse(app.is_within_night_schedule(datetime.datetime(2026, 11, 9, 14, 0)))

    def test_hardware_profiles_and_capped_ram_file_handler(self):
        key_pi2, prof_pi2 = detect_hardware_profile("pi2")
        self.assertEqual(key_pi2, "pi2")
        self.assertEqual(prof_pi2["cdn_scale"], 1.0)

        key_zero, prof_zero = detect_hardware_profile("pizero")
        self.assertEqual(key_zero, "pizero")
        self.assertLess(prof_zero["cdn_scale"], 1.0)

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


if __name__ == "__main__":
    unittest.main()
