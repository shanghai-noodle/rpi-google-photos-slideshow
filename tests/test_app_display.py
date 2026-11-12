import datetime
import os
import tempfile
import unittest

try:
    from PIL import Image
except ImportError:
    Image = None

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
        from photoframe.mdns import (
            MDNS_HOSTNAME,
            build_mdns_a_response,
            build_mdns_query,
            format_mdns_hostname,
            normalize_mdns_base_name,
            parse_mdns_a_records,
            select_non_conflicting_hostname,
            set_mdns_base_name,
        )
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

        # Verify A-record parser & query builder
        parsed_records = parse_mdns_a_records(pkt)
        self.assertEqual(parsed_records, [("photoframe.local", "192.168.86.42")])
        q_pkt = build_mdns_query(0x0001, "photoframe-2.local")
        self.assertIn(b"\x0cphotoframe-2\x05local\x00", q_pkt)
        self.assertEqual(parse_mdns_a_records(q_pkt), [])

        # Verify base name normalization and auto-numbering format
        self.assertEqual(normalize_mdns_base_name(""), "photoframe")
        self.assertEqual(normalize_mdns_base_name("Living Room.local"), "living-room")
        self.assertEqual(format_mdns_hostname("photoframe", 1), "photoframe.local")
        self.assertEqual(format_mdns_hostname("photoframe", 2), "photoframe-2.local")
        self.assertEqual(format_mdns_hostname("Living Room", 3), "living-room-3.local")

        # Verify automatic conflict resolution across multiple photo frames on the same LAN
        class _FakeMdnsSocket:
            def __init__(self, taken_map):
                self.taken_map = taken_map
                self.last_queried = None
                self.replied = False

            def sendto(self, data, addr):
                # Extract queried name from packet
                from photoframe.mdns import _read_dns_name
                qname, _ = _read_dns_name(data, 12)
                self.last_queried = qname.lower().strip(".")
                self.replied = False

            def settimeout(self, t):
                pass

            def recvfrom(self, bufsize):
                import socket as _sock
                if not self.replied and self.last_queried in self.taken_map:
                    self.replied = True
                    owner_ip = self.taken_map[self.last_queried]
                    return build_mdns_a_response(0, owner_ip, hostname=self.last_queried), (owner_ip, 5353)
                raise _sock.timeout()

        fake_sock = _FakeMdnsSocket({
            "photoframe.local": "192.168.86.53",
            "photoframe-2.local": "192.168.86.52",
        })
        chosen_host, chosen_idx = select_non_conflicting_hostname(
            fake_sock, "photoframe", "192.168.86.250", start_index=1, probe_duration_sec=0.05
        )
        self.assertEqual(chosen_host, "photoframe-3.local")
        self.assertEqual(chosen_idx, 3)

        # If our own IP owns photoframe.local, it is NOT a conflict
        own_host, own_idx = select_non_conflicting_hostname(
            fake_sock, "photoframe", "192.168.86.53", start_index=1, probe_duration_sec=0.05
        )
        self.assertEqual(own_host, "photoframe.local")
        self.assertEqual(own_idx, 1)
        set_mdns_base_name("photoframe")

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

        # When Pause action is triggered: Admin Portal control panel is shown, and hidden on Resume
        app.queue_web_action("pause_toggle")
        app._process_web_actions()
        self.assertTrue(app.is_paused)
        self.assertTrue(app.full_redraw_needed)
        self.assertTrue(app._should_show_portal_overlay())

        app.full_redraw_needed = False
        app.queue_web_action("pause_toggle")
        app._process_web_actions()
        self.assertFalse(app.is_paused)
        self.assertTrue(app.full_redraw_needed)
        self.assertFalse(app._should_show_portal_overlay())

        # Explicit pause & resume actions also show/hide even during initial 60s window
        app.app_start_time = time.time() - 10.0
        app._startup_overlay_expired = False
        app.queue_web_action("pause")
        app._process_web_actions()
        self.assertTrue(app._should_show_portal_overlay())
        app.queue_web_action("resume")
        app._process_web_actions()
        self.assertFalse(app._should_show_portal_overlay())

        # In Night Mode: always hidden
        app.is_night_mode = True
        app.photo_manager = _FakePM(True)
        self.assertFalse(app._should_show_portal_overlay())

        snap = app.get_status_snapshot()
        self.assertIn("app_version", snap)
        self.assertGreaterEqual(int(snap["app_version_code"]), 3)

    def test_video_frames_never_leak_between_clips_and_freeze_on_stop(self):
        """
        Regression test for video start/stop flashes:
          1. A frame tagged with a previous clip's generation must never be drawn.
          2. Stopping a video freezes its last frame into the slide surface so later full
             redraws do not flash the static poster.
          3. A full redraw while a video plays keeps the live frame in the tile.
        """
        try:
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
            import pygame
            pygame.display.init()
        except Exception:
            self.skipTest("pygame not available")

        args = parse_args(["--album", "demo", "--rotate", "0", "--width", "64", "--height", "36", "--web-port", "0"])
        app = SlideshowApp(args, hw_profile=HARDWARE_PROFILES["standard"], config_store=self.store)
        app.screen = pygame.display.set_mode((64, 36))
        app.canvas = app.screen
        app.current_photo_surf = pygame.Surface((64, 36))
        app.current_photo_surf.fill((0, 0, 255))  # blue "poster"
        app.app_start_time = 0.0  # overlay expired
        app._startup_overlay_expired = True
        app.full_redraw_needed = True
        app.render_frame()

        # Simulate an active clip occupying tile (8, 4, 16, 10)
        app._video_playing = True
        app._video_screen_rect = (8, 4, 16, 10)
        app._last_video_canvas_rect = (8, 4, 16, 10)
        app._video_corner_stamps = []
        red = bytes([255, 0, 0]) * (16 * 10)
        green = bytes([0, 255, 0]) * (16 * 10)

        # 1. Stale frame from an older generation is dropped
        app._latest_video_frame = (app._video_gen - 1, green)
        app.render_frame()
        self.assertEqual(tuple(app.screen.get_at((12, 8)))[:3], (0, 0, 255))

        # Current-generation frame is drawn
        app._latest_video_frame = (app._video_gen, red)
        app.render_frame()
        self.assertEqual(tuple(app.screen.get_at((12, 8)))[:3], (255, 0, 0))

        # 3. Full redraw while playing keeps the live frame (no poster flash)
        app.full_redraw_needed = True
        app.render_frame()
        self.assertEqual(tuple(app.screen.get_at((12, 8)))[:3], (255, 0, 0))

        # 2. Stop freezes the last frame into the slide surface
        app.stop_video_playback()
        self.assertEqual(tuple(app.current_photo_surf.get_at((12, 8)))[:3], (255, 0, 0))
        self.assertEqual(tuple(app.current_photo_surf.get_at((2, 2)))[:3], (0, 0, 255))
        app.full_redraw_needed = True
        app.render_frame()
        self.assertEqual(tuple(app.screen.get_at((12, 8)))[:3], (255, 0, 0))
        pygame.display.quit()

    def test_smooth_video_fastpath_native_32bit_dirty_rect_and_deferred_prefetch(self):
        """
        Verifies the Linux/Raspberry Pi smooth video playback optimizations:
          1. Native 32-bit `bgr0`/`rgb0` direct surface blitting matches exact RGB colors.
          2. Zero-allocation `_read_exact_into` reassembles fragmented pipe reads cleanly.
          3. Hardware V4L2 M2M (`h264_v4l2m2m`) vs multi-core `-threads 0` FFmpeg CLI flags.
          4. Higher smooth FPS defaults (`24 FPS` standard, `30 FPS` on >=1.8GB RAM, `15 FPS` lowmem).
          5. Slide prefetch is deferred while a video slide is active (preventing GIL/GC stutter).
        """
        import io

        # 4. Verify raised FPS profiles
        _, std_prof = detect_hardware_profile("standard")
        _, low_prof = detect_hardware_profile("lowmem")
        self.assertEqual(std_prof["video_fps"], 24)
        self.assertEqual(low_prof["video_fps"], 15)

        mem_1gb = os.path.join(self.tmp_dir.name, "meminfo_1gb")
        with open(mem_1gb, "w", encoding="utf-8") as f:
            f.write("MemTotal:         948000 kB\n")
        _, pi3_prof = detect_hardware_profile("auto", machine_override="armv7l", meminfo_path=mem_1gb)
        self.assertEqual(pi3_prof["video_fps"], 20)
        self.assertEqual(pi3_prof["video_max_edge"], 640)

        mem_4gb = os.path.join(self.tmp_dir.name, "meminfo_4gb")
        with open(mem_4gb, "w", encoding="utf-8") as f:
            f.write("MemTotal:        4096000 kB\n")
        _, hi_prof = detect_hardware_profile("auto", machine_override="aarch64", meminfo_path=mem_4gb)
        self.assertEqual(hi_prof["video_fps"], 30)
        self.assertEqual(hi_prof["video_max_edge"], 960)

        # Verify capped-resolution FFmpeg decode sizing (e.g. 1080x1920 rotated 270 -> 640x360 pipe)
        args_pi = parse_args(["--rotate", "270"])
        app_pi = SlideshowApp(args_pi, hw_profile=pi3_prof, config_store=self.store)
        sx_p, sy_p, sw_p, sh_p, vf_p = app_pi._map_canvas_rect_to_screen(0, 0, 1080, 1920)
        self.assertEqual((sx_p, sy_p, sw_p, sh_p), (0, 0, 1920, 1080))
        self.assertEqual(app_pi._video_decode_size, (640, 360))
        self.assertIn("scale=360:640:force_original_aspect_ratio=increase", vf_p)
        self.assertIn("transpose=1", vf_p)

        # 2. Verify zero-alloc `_read_exact_into` across partial chunks & EOF
        class _ChunkedReader(io.RawIOBase):
            def __init__(self, payload: bytes, step: int = 5):
                self._data = payload
                self._pos = 0
                self._step = step

            def readinto(self, b):
                if self._pos >= len(self._data):
                    return 0
                end = min(len(self._data), self._pos + min(len(b), self._step))
                n = end - self._pos
                b[:n] = self._data[self._pos:end]
                self._pos = end
                return n

        target = bytearray(17)
        self.assertTrue(SlideshowApp._read_exact_into(_ChunkedReader(b"abcdefghijklmnopq", step=4), target))
        self.assertEqual(bytes(target), b"abcdefghijklmnopq")
        self.assertFalse(SlideshowApp._read_exact_into(_ChunkedReader(b"short", step=3), bytearray(10)))

        # 3. Verify FFmpeg command construction for HW V4L2 M2M vs multi-threaded SW
        hw_cmd = SlideshowApp._build_ffmpeg_rawvideo_cmd("/tmp/v.mp4", 15.0, "fps=24", "bgr0", hw_decoder="h264_v4l2m2m")
        self.assertIn("h264_v4l2m2m", hw_cmd)
        self.assertNotIn("-threads", hw_cmd)
        sw_cmd = SlideshowApp._build_ffmpeg_rawvideo_cmd("/tmp/v.mp4", 15.0, "fps=24", "bgr0", hw_decoder=None)
        self.assertIn("-threads", sw_cmd)
        self.assertIn("0", sw_cmd)

        # 1. Verify native 32-bit `bgr0` frame blitting & freeze accuracy
        try:
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
            import pygame
            pygame.display.init()
        except Exception:
            return

        args = parse_args(["--album", "demo", "--rotate", "0", "--width", "64", "--height", "36", "--web-port", "0"])
        app = SlideshowApp(args, hw_profile=std_prof, config_store=self.store)
        app.screen = pygame.display.set_mode((64, 36))
        app.canvas = app.screen
        app.current_photo_surf = pygame.Surface((64, 36))
        app.current_photo_surf.fill((10, 20, 30))
        app.app_start_time = 0.0
        app._startup_overlay_expired = True
        app.full_redraw_needed = True
        app.render_frame()

        fmt, bpp, tile_surf = app._select_video_pix_fmt(12, 8)
        app._video_playing = True
        app._video_screen_rect = (10, 6, 12, 8)
        app._last_video_canvas_rect = (10, 6, 12, 8)
        app._video_tile_surf = tile_surf
        if fmt == "bgr0" and bpp == 4:
            # B=40, G=180, R=250, X=0 -> RGB(250, 180, 40)
            frame_buf = bytearray([40, 180, 250, 0] * (12 * 8))
            app._latest_video_frame = (app._video_gen, frame_buf)
            app.render_frame()
            self.assertEqual(tuple(app.screen.get_at((14, 10)))[:3], (250, 180, 40))
            self.assertEqual(tuple(app.screen.get_at((2, 2)))[:3], (10, 20, 30))
            app.stop_video_playback()
            self.assertEqual(tuple(app.current_photo_surf.get_at((14, 10)))[:3], (250, 180, 40))

            # Also verify capped-resolution decode buffer (6x4 -> scaled in-place to 12x8 tile)
            app._video_playing = True
            app._video_screen_rect = (10, 6, 12, 8)
            app._last_video_canvas_rect = (10, 6, 12, 8)
            app._video_decode_size = (6, 4)
            app._video_tile_surf = pygame.Surface((12, 8))
            capped_buf = bytearray([90, 210, 30, 0] * (6 * 4))  # RGB(30, 210, 90)
            app._latest_video_frame = (app._video_gen, capped_buf)
            app.render_frame()
            self.assertEqual(tuple(app.screen.get_at((14, 10)))[:3], (30, 210, 90))
            app.stop_video_playback()
            self.assertEqual(tuple(app.current_photo_surf.get_at((14, 10)))[:3], (30, 210, 90))

        # 5. Verify prefetch is deferred when a slide contains playable videos
        pm = PhotoManager(64, 36, album_url="demo", video_mode="play", auto_refresh=False)
        prefetch_calls = []
        pm.trigger_prefetch_next = lambda: prefetch_calls.append(1)
        pm._build_next_slide = lambda: (Image.new("RGB", (64, 36), (0, 0, 0)), [{"mp4_path": "/tmp/fake.mp4", "rect": (0, 0, 64, 36)}])
        pm.advance_slide()
        self.assertEqual(len(prefetch_calls), 0)
        pm._build_next_slide = lambda: (Image.new("RGB", (64, 36), (0, 0, 0)), [])
        pm.advance_slide()
        self.assertEqual(len(prefetch_calls), 1)
        pygame.display.quit()


if __name__ == "__main__":
    unittest.main()
