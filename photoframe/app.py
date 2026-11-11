"""
Native Linux Display Controller (Wayland / X11 / KMSDRM + Rotation + Video + Night Mode)
and CLI entry point.
"""

import argparse
import collections
import datetime
import fcntl
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Tuple

from .collage import PYGAME_AVAILABLE, PhotoManager, pygame
from .config import FrameConfigStore
from .hardware import (
    DEFAULT_RAM_CACHE,
    DEFAULT_RAM_LOG,
    detect_hardware_profile,
    logger,
    setup_ram_logging,
)
from .web_portal import start_web_config_server


class SlideshowApp:
    VALID_TRANSITIONS = (
        "none",
        "fade",
        "fade-black",
        "slide-left",
        "slide-right",
        "slide-up",
        "wipe",
        "random",
    )

    def __init__(
        self,
        args: argparse.Namespace,
        hw_profile: Dict[str, Any],
        config_store: Optional[FrameConfigStore] = None,
    ):
        self.args = args
        self.hw_profile = hw_profile
        self.loop_hz = int(self.hw_profile.get("loop_hz", 10))
        self.is_running = True

        self.config_store = config_store if config_store is not None else FrameConfigStore()
        saved = self.config_store.get_settings()
        if saved:
            for k, v in saved.items():
                if hasattr(self.args, k) and v is not None:
                    setattr(self.args, k, v)
            if "night_mode_enabled" in saved:
                self.args.disable_night_mode = not bool(saved["night_mode_enabled"])

        self.screen_w = args.width
        self.screen_h = args.height
        self.fullscreen = not args.windowed
        self.driver_preference = args.driver
        self.rotation = int(args.rotate) % 360
        if self.rotation not in (0, 90, 180, 270):
            self.rotation = 0

        self.canvas_w = self.screen_h if self.rotation in (90, 270) else self.screen_w
        self.canvas_h = self.screen_w if self.rotation in (90, 270) else self.screen_h

        self.screen: Optional[Any] = None
        self.canvas: Optional[Any] = None
        self.clock = None

        self.slide_duration = float(args.duration)
        self.max_video_seconds = float(args.max_video_seconds)
        raw_trans = str(getattr(args, "transition", "none") or "none").strip().lower()
        self.transition = raw_trans if raw_trans in self.VALID_TRANSITIONS else "none"
        self.last_slide_switch = time.time()
        self.is_paused = False
        self.full_redraw_needed = True

        # Active FFmpeg video playback state (streams directly from /dev/shm)
        self._video_proc: Optional[subprocess.Popen] = None
        self._video_playing = False
        self._video_gen = 0
        self._video_screen_rect: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self._video_corner_stamps: List[Tuple[Any, Tuple[int, int]]] = []
        self._video_fps = int(self.hw_profile.get("video_fps", 18))
        self._latest_video_frame: Optional[bytes] = None
        self._video_eof = False
        self._pending_slide_videos: Deque[Dict[str, Any]] = collections.deque()
        self._base_screen_surf: Optional[Any] = None

        # Night mode sleep schedule (black screen to save power & eliminate glare)
        self.night_mode_enabled = not args.disable_night_mode
        self.night_start_time = self._parse_time_str(args.night_start, 22, 0)
        self.night_end_time = self._parse_time_str(args.night_end, 6, 0)
        self.is_night_mode = False
        self._last_schedule_check_minute = -1
        self._last_scheduled_state = False

        self.photo_manager: Optional[PhotoManager] = None
        self.current_photo_surf: Optional[Any] = None
        self.current_video_list: List[Dict[str, Any]] = []
        self._web_action_queue: Deque[Tuple[str, Any]] = collections.deque()

    @staticmethod
    def _parse_time_str(time_str: str, default_h: int, default_m: int) -> datetime.time:
        if not time_str:
            return datetime.time(default_h, default_m)
        s = time_str.strip().lower()
        try:
            if "am" in s or "pm" in s:
                return datetime.datetime.strptime(s, "%I:%M%p" if ":" in s else "%I%p").time()
            parts = [int(p) for p in s.split(":")]
            return datetime.time(parts[0], parts[1] if len(parts) > 1 else 0)
        except Exception:
            return datetime.time(default_h, default_m)

    def is_within_night_schedule(self, now_dt: Optional[datetime.datetime] = None) -> bool:
        if not self.night_mode_enabled:
            return False
        now_dt = now_dt or datetime.datetime.now()
        cur = now_dt.time()
        if self.night_start_time <= self.night_end_time:
            return self.night_start_time <= cur < self.night_end_time
        return cur >= self.night_start_time or cur < self.night_end_time

    def check_night_mode_schedule(self):
        if not self.night_mode_enabled:
            return
        now_dt = datetime.datetime.now()
        if now_dt.minute == self._last_schedule_check_minute:
            return
        self._last_schedule_check_minute = now_dt.minute
        in_sched = self.is_within_night_schedule(now_dt)
        if in_sched != self._last_scheduled_state:
            self._last_scheduled_state = in_sched
            if in_sched and not self.is_night_mode:
                self.stop_video_playback()
                self.is_night_mode = True
                self.full_redraw_needed = True
                logger.info("🌙 Entering Night Mode (black display).")
            elif not in_sched and self.is_night_mode:
                self.is_night_mode = False
                self.last_slide_switch = time.time()
                self.full_redraw_needed = True
                logger.info("☀️ Exiting Night Mode (resuming slideshow).")

    def _detect_local_desktop_env(self):
        """
        When launched via SSH or systemd without DISPLAY/WAYLAND_DISPLAY set,
        inspects /run/user/<uid> and /tmp/.X11-unix to detect an active local
        Wayland or X11 desktop session on the Linux host.
        """
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
        if os.path.isdir(runtime_dir):
            for wl_sock in ("wayland-0", "wayland-1"):
                if os.path.exists(os.path.join(runtime_dir, wl_sock)):
                    os.environ.setdefault("XDG_RUNTIME_DIR", runtime_dir)
                    os.environ.setdefault("WAYLAND_DISPLAY", wl_sock)
                    break
        if not os.environ.get("DISPLAY") and os.path.exists("/tmp/.X11-unix/X0"):
            os.environ["DISPLAY"] = ":0"
            xauth = os.path.expanduser("~/.Xauthority")
            if os.path.exists(xauth):
                os.environ.setdefault("XAUTHORITY", xauth)

    def init_display(self):
        if not PYGAME_AVAILABLE:
            logger.error("Pygame is not installed! Run: sudo apt-get install python3-pygame")
            sys.exit(1)

        self._detect_local_desktop_env()

        hw_flags = pygame.DOUBLEBUF | pygame.HWSURFACE
        sw_flags = 0
        if self.fullscreen:
            hw_flags |= pygame.FULLSCREEN
            sw_flags |= pygame.FULLSCREEN

        candidates = []
        if self.driver_preference not in ("auto", "default"):
            candidates.append((self.driver_preference, False, hw_flags))
            candidates.append((self.driver_preference, True, sw_flags))
        else:
            if os.environ.get("WAYLAND_DISPLAY"):
                candidates.append(("wayland", False, sw_flags))
            if os.environ.get("DISPLAY"):
                candidates.append(("x11", False, sw_flags))
            candidates.extend([
                ("kmsdrm", False, hw_flags),
                ("kmsdrm", True, sw_flags),
                ("fbcon", True, sw_flags),
                (None, True, sw_flags),
            ])

        last_err = None
        for drv, use_sw_accel, mode_flags in candidates:
            try:
                pygame.display.quit()
            except Exception:
                pass

            if drv:
                os.environ["SDL_VIDEODRIVER"] = drv
            else:
                os.environ.pop("SDL_VIDEODRIVER", None)

            if use_sw_accel:
                os.environ["SDL_FRAMEBUFFER_ACCELERATION"] = "0"
                os.environ["SDL_RENDER_DRIVER"] = "software"
            else:
                os.environ.pop("SDL_FRAMEBUFFER_ACCELERATION", None)
                os.environ.pop("SDL_RENDER_DRIVER", None)

            try:
                pygame.display.init()
                try:
                    info = pygame.display.Info()
                    if self.fullscreen and info.current_w and info.current_h:
                        self.screen_w = info.current_w
                        self.screen_h = info.current_h
                except Exception:
                    pass

                self.screen = pygame.display.set_mode((self.screen_w, self.screen_h), mode_flags)
                logger.info(
                    "Selected video driver '%s' (software_surface=%s)",
                    drv or "default",
                    use_sw_accel,
                )
                break
            except Exception as e:
                last_err = e
                logger.warning(
                    "Video driver '%s' (software_surface=%s) failed: %s",
                    drv or "default",
                    use_sw_accel,
                    e,
                )
                self.screen = None
        else:
            logger.error(
                "All SDL video drivers failed (last error: %s). "
                "Ensure Mesa EGL/DRM packages are installed ('sudo apt install -y libegl1 libgbm1 libgles2 libgl1-mesa-dri'), "
                "your user is in the 'video' and 'render' groups ('sudo usermod -aG video,render $USER'), "
                "and 'dtoverlay=vc4-kms-v3d' is enabled in /boot/firmware/config.txt.",
                last_err,
            )
            raise last_err

        pygame.mouse.set_visible(False)
        self.canvas_w = self.screen_h if self.rotation in (90, 270) else self.screen_w
        self.canvas_h = self.screen_w if self.rotation in (90, 270) else self.screen_h

        pygame.display.set_caption("Google Photos Slideshow")
        self.clock = pygame.time.Clock()

        self.canvas = self.screen if self.rotation == 0 else pygame.Surface((self.canvas_w, self.canvas_h))
        driver_name = pygame.display.get_driver() if hasattr(pygame.display, "get_driver") else "unknown"
        logger.info(
            "Display initialized: %dx%d (Canvas: %dx%d, Rotation: %d°, Driver: %s)",
            self.screen_w, self.screen_h, self.canvas_w, self.canvas_h, self.rotation, driver_name
        )

    # --------------------------------------------------------------------------
    # Hardware-Accelerated FFmpeg Video Streaming (Direct to Rotated Screen)
    # --------------------------------------------------------------------------

    def _map_canvas_rect_to_screen(
        self,
        vx: int,
        vy: int,
        vw: int,
        vh: int,
        centering: Tuple[float, float] = (0.50, 0.50),
        inset: int = 0,
    ) -> Tuple[int, int, int, int, str]:
        """
        Maps a video rectangle `(vx, vy, vw, vh)` on `self.canvas` (`canvas_w x canvas_h`)
        to the rotated physical `self.screen` (`screen_w x screen_h`), returning
        `(sx, sy, sw, sh, ffmpeg_vf_filter)`.
        Uses the same subject-aware `(cx, cy)` centering and tile border inset as
        `PhotoManager._paste_rounded_tile` so the first video frame aligns pixel-for-pixel
        with the static poster thumbnail without jumping.
        """
        fps = self._video_fps
        cx = max(0.0, min(1.0, float(centering[0]))) if centering and len(centering) >= 2 else 0.50
        cy = max(0.0, min(1.0, float(centering[1]))) if centering and len(centering) >= 2 else 0.50
        ins = max(0, int(inset))
        scale_w = vw + 2 * ins
        scale_h = vh + 2 * ins
        crop_x = f"(in_w-{scale_w})*{cx:.4f}+{ins}" if ins > 0 else f"(in_w-out_w)*{cx:.4f}"
        crop_y = f"(in_h-{scale_h})*{cy:.4f}+{ins}" if ins > 0 else f"(in_h-out_h)*{cy:.4f}"
        scale_part = (
            f"fps={fps},scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase:flags=fast_bilinear,"
            f"crop={vw}:{vh}:{crop_x}:{crop_y}"
        )
        if self.rotation == 270:
            # 270 deg CCW == 90 deg CW (FFmpeg transpose=1)
            sx = self.canvas_h - (vy + vh)
            sy = vx
            return sx, sy, vh, vw, f"{scale_part},transpose=1"
        if self.rotation == 90:
            # 90 deg CCW (FFmpeg transpose=2)
            sx = vy
            sy = self.canvas_w - (vx + vw)
            return sx, sy, vh, vw, f"{scale_part},transpose=2"
        if self.rotation == 180:
            sx = self.canvas_w - (vx + vw)
            sy = self.canvas_h - (vy + vh)
            return sx, sy, vw, vh, f"{scale_part},hflip,vflip"
        return vx, vy, vw, vh, scale_part

    def _build_video_corner_stamps(
        self, sx: int, sy: int, sw: int, sh: int, inset: int
    ) -> List[Tuple[Any, Tuple[int, int]]]:
        """
        Extracts the four 8x8 rounded-border corner stamps from `self._base_screen_surf`
        so blitting rectangular video frames at `(sx, sy, sw, sh)` preserves the crisp
        `radius=10` 2px white rounded tile border and outer bokeh corners.
        """
        if (
            inset <= 0
            or not PYGAME_AVAILABLE
            or self._base_screen_surf is None
            or sw < 16
            or sh < 16
        ):
            return []
        stamps: List[Tuple[Any, Tuple[int, int]]] = []
        patch_sz = 8
        r_sq = 6.8 * 6.8
        corners = [
            (sx, sy, 7.0, 7.0),
            (sx + sw - patch_sz, sy, 0.0, 7.0),
            (sx, sy + sh - patch_sz, 7.0, 0.0),
            (sx + sw - patch_sz, sy + sh - patch_sz, 0.0, 0.0),
        ]
        try:
            for cx_pos, cy_pos, arc_x, arc_y in corners:
                stamp = pygame.Surface((patch_sz, patch_sz), pygame.SRCALPHA)
                stamp.blit(self._base_screen_surf, (0, 0), (cx_pos, cy_pos, patch_sz, patch_sz))
                for py in range(patch_sz):
                    dy = py - arc_y
                    for px in range(patch_sz):
                        dx = px - arc_x
                        if (dx * dx + dy * dy) < r_sq:
                            stamp.set_at((px, py), (0, 0, 0, 0))
                stamps.append((stamp, (cx_pos, cy_pos)))
        except Exception:
            return []
        return stamps

    def stop_video_playback(self, clear_queue: bool = True):
        self._video_gen += 1
        self._video_playing = False
        if clear_queue:
            self._pending_slide_videos.clear()
        proc = self._video_proc
        self._video_proc = None
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass
        self._latest_video_frame = None
        self._video_eof = False
        self._video_corner_stamps = []

    def start_slide_videos(self, video_list: Optional[List[Dict[str, Any]]]):
        self.stop_video_playback(clear_queue=True)
        if not video_list or self.is_night_mode:
            return
        self._pending_slide_videos = collections.deque(video_list)
        self._start_next_queued_video()

    def _start_next_queued_video(self) -> bool:
        self.stop_video_playback(clear_queue=False)
        while self._pending_slide_videos and not self.is_night_mode:
            video_info = self._pending_slide_videos.popleft()
            mp4_path = video_info.get("mp4_path")
            if not mp4_path or not os.path.exists(mp4_path):
                continue

            vx, vy, vw, vh = video_info["rect"]
            centering = video_info.get("centering", (0.50, 0.50))
            inset = int(video_info.get("inset", 0))
            tile_idx = video_info.get("tile_index", 1)
            total_tiles = video_info.get("total_tiles", 1)
            play_sec = float(video_info.get("max_seconds", self.max_video_seconds))
            self._video_fps = int(video_info.get("fps", self._video_fps))
            sx, sy, sw, sh, vf_filter = self._map_canvas_rect_to_screen(
                vx, vy, vw, vh, centering=centering, inset=inset
            )
            self._video_screen_rect = (sx, sy, sw, sh)
            self._video_corner_stamps = self._build_video_corner_stamps(sx, sy, sw, sh, inset)
            frame_bytes_len = sw * sh * 3

            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-t", f"{play_sec:.1f}",
                "-i", mp4_path,
                "-an",
                "-vf", vf_filter,
                "-f", "rawvideo",
                "-pix_fmt", "rgb24",
                "pipe:1",
            ]

            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=frame_bytes_len * 2)
            except Exception as e:
                logger.warning("Failed to start ffmpeg video playback: %s", e)
                continue

            gen = self._video_gen
            self._video_proc = proc
            self._video_playing = True
            self._video_eof = False
            logger.info(
                "▶️  Playing video in tile %d/%d (%dx%d at (%d,%d) @ %d FPS, max %.0fs)",
                tile_idx, total_tiles, vw, vh, vx, vy, self._video_fps, play_sec
            )

            def reader_worker(p: subprocess.Popen, nbytes: int, fps: int, expected_gen: int):
                frame_interval = 1.0 / max(1, fps)
                next_frame_t = time.time()
                try:
                    while self._video_playing and self._video_gen == expected_gen and p.poll() is None and p.stdout:
                        if self.is_paused:
                            time.sleep(0.1)
                            next_frame_t = time.time()
                            continue
                        buf = p.stdout.read(nbytes)
                        if not buf or len(buf) < nbytes or self._video_gen != expected_gen or not self._video_playing:
                            break
                        self._latest_video_frame = buf
                        now = time.time()
                        sleep_s = next_frame_t - now
                        if sleep_s > 0:
                            time.sleep(sleep_s)
                        next_frame_t = max(now, next_frame_t) + frame_interval
                except Exception:
                    pass
                finally:
                    if self._video_proc is p and self._video_gen == expected_gen:
                        self._video_eof = True

            threading.Thread(target=reader_worker, args=(proc, frame_bytes_len, self._video_fps, gen), daemon=True).start()
            return True
        return False

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.is_running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    self.is_running = False
                elif self.is_night_mode:
                    self.is_night_mode = False
                    self.full_redraw_needed = True
                elif event.key == pygame.K_n:
                    self.stop_video_playback()
                    self.is_night_mode = not self.is_night_mode
                    self.full_redraw_needed = True
                elif event.key == pygame.K_SPACE:
                    self.is_paused = not self.is_paused
                    logger.info("Slideshow %s", "paused" if self.is_paused else "resumed")
                elif event.key == pygame.K_RIGHT:
                    self.advance_slide()
                elif event.key in (pygame.K_LEFT, pygame.K_p):
                    self.previous_slide()
                elif event.key == pygame.K_r:
                    self.stop_video_playback()
                    self.rotation = (self.rotation + 90) % 360
                    self.canvas_w = self.screen_h if self.rotation in (90, 270) else self.screen_w
                    self.canvas_h = self.screen_w if self.rotation in (90, 270) else self.screen_h
                    if self.screen is not None and PYGAME_AVAILABLE:
                        self.canvas = self.screen if self.rotation == 0 else pygame.Surface((self.canvas_w, self.canvas_h))
                    if self.photo_manager:
                        self.photo_manager.target_w = self.canvas_w
                        self.photo_manager.target_h = self.canvas_h
                    self.full_redraw_needed = True
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if self.is_night_mode:
                    self.is_night_mode = False
                    self.full_redraw_needed = True
                else:
                    self.advance_slide()

    def _compose_screen_surface(self, photo_surf: Any) -> Optional[Any]:
        """
        Renders `photo_surf` (plus the Demo Mode pill when applicable) onto a canvas-sized
        surface and rotates it to match physical screen coordinates (`self.screen_w x self.screen_h`).
        """
        if not PYGAME_AVAILABLE or photo_surf is None:
            return None
        try:
            temp_canvas = pygame.Surface((self.canvas_w, self.canvas_h))
            temp_canvas.blit(photo_surf, (0, 0))
            if self.photo_manager and self.photo_manager.is_demo_mode:
                self._draw_demo_setup_pill(temp_canvas)
            if self.rotation != 0:
                return pygame.transform.rotate(temp_canvas, self.rotation)
            return temp_canvas
        except Exception as e:
            logger.debug("Failed to compose transition surface: %s", e)
            return None

    def _run_slide_transition(
        self,
        prev_surf: Any,
        next_surf: Any,
        prev_screen_surf: Optional[Any] = None,
    ) -> bool:
        """
        Animates the transition between `prev_surf` (or `prev_screen_surf` containing the
        latest rendered video frame) and `next_surf` on `self.screen`.
        When `self.transition == 'none'` (the default) or running headlessly, returns False
        immediately so `render_frame()` performs an instant cut with zero overhead.
        """
        if (
            self.transition == "none"
            or self.screen is None
            or not PYGAME_AVAILABLE
            or self.is_night_mode
            or (prev_surf is None and prev_screen_surf is None)
            or next_surf is None
        ):
            return False

        effect = self.transition
        if effect == "random":
            effect = random.choice(("fade", "fade-black", "slide-left", "slide-right", "slide-up", "wipe"))

        prev_s = prev_screen_surf if prev_screen_surf is not None else self._compose_screen_surface(prev_surf)
        next_s = self._compose_screen_surface(next_surf)
        if prev_s is None or next_s is None:
            return False

        sw, sh = prev_s.get_size()
        steps = 12
        step_delay = 0.028  # ~35 FPS, ~0.34s total transition time

        try:
            if effect == "fade":
                overlay = next_s.copy()
                for i in range(1, steps):
                    t = i / steps
                    u = 3 * t * t - 2 * t * t * t
                    self.screen.blit(prev_s, (0, 0))
                    overlay.set_alpha(int(255 * u))
                    self.screen.blit(overlay, (0, 0))
                    pygame.display.flip()
                    time.sleep(step_delay)
            elif effect == "fade-black":
                black_surf = pygame.Surface((sw, sh))
                black_surf.fill((0, 0, 0))
                for i in range(1, steps):
                    t = i / steps
                    if t < 0.5:
                        alpha = int(255 * (t * 2.0))
                        self.screen.blit(prev_s, (0, 0))
                    else:
                        alpha = int(255 * (2.0 - t * 2.0))
                        self.screen.blit(next_s, (0, 0))
                    black_surf.set_alpha(max(0, min(255, alpha)))
                    self.screen.blit(black_surf, (0, 0))
                    pygame.display.flip()
                    time.sleep(step_delay)
            elif effect == "slide-left":
                for i in range(1, steps):
                    t = i / steps
                    u = 3 * t * t - 2 * t * t * t
                    offset = int(sw * u)
                    self.screen.blit(prev_s, (-offset, 0))
                    self.screen.blit(next_s, (sw - offset, 0))
                    pygame.display.flip()
                    time.sleep(step_delay)
            elif effect == "slide-right":
                for i in range(1, steps):
                    t = i / steps
                    u = 3 * t * t - 2 * t * t * t
                    offset = int(sw * u)
                    self.screen.blit(prev_s, (offset, 0))
                    self.screen.blit(next_s, (-sw + offset, 0))
                    pygame.display.flip()
                    time.sleep(step_delay)
            elif effect == "slide-up":
                for i in range(1, steps):
                    t = i / steps
                    u = 3 * t * t - 2 * t * t * t
                    offset = int(sh * u)
                    self.screen.blit(prev_s, (0, -offset))
                    self.screen.blit(next_s, (0, sh - offset))
                    pygame.display.flip()
                    time.sleep(step_delay)
            elif effect == "wipe":
                for i in range(1, steps):
                    t = i / steps
                    u = 3 * t * t - 2 * t * t * t
                    wipe_w = max(1, int(sw * u))
                    self.screen.blit(prev_s, (0, 0))
                    self.screen.blit(next_s, (0, 0), (0, 0, wipe_w, sh))
                    pygame.display.flip()
                    time.sleep(step_delay)
            return True
        except Exception as e:
            logger.debug("Slide transition error (%s): %s", effect, e)
            return False

    def advance_slide(self):
        self.stop_video_playback()
        self.last_slide_switch = time.time()
        if not self.photo_manager:
            return
        prev_surf = self.current_photo_surf
        prev_screen_surf = self._base_screen_surf.copy() if self._base_screen_surf is not None else None
        next_surf, next_vids = self.photo_manager.advance_slide()
        if next_surf:
            self.current_photo_surf = next_surf
            self.current_video_list = next_vids
            self._run_slide_transition(prev_surf, next_surf, prev_screen_surf=prev_screen_surf)
            self.full_redraw_needed = True
            self.render_frame()
            if next_vids:
                self.start_slide_videos(next_vids)

    def previous_slide(self):
        self.stop_video_playback()
        self.last_slide_switch = time.time()
        if not self.photo_manager:
            return
        prev_surf = self.current_photo_surf
        prev_screen_surf = self._base_screen_surf.copy() if self._base_screen_surf is not None else None
        prev_slide_surf, prev_vids = self.photo_manager.previous_slide()
        if prev_slide_surf:
            self.current_photo_surf = prev_slide_surf
            self.current_video_list = prev_vids
            self._run_slide_transition(prev_surf, prev_slide_surf, prev_screen_surf=prev_screen_surf)
            self.full_redraw_needed = True
            self.render_frame()
            if prev_vids:
                self.start_slide_videos(prev_vids)

    @staticmethod
    def _get_local_ip() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "localhost"

    def _draw_demo_setup_pill(self, target: Any):
        """
        Draws a sleek, non-intrusive translucent corner pill while in Demo Showcase Mode
        showing the local Web Portal URL so a new user knows where to configure their album.
        Automatically disappears once a Google Photos album is configured.
        """
        try:
            if not hasattr(self, "_cached_demo_pill") or self._cached_demo_pill is None:
                pygame.font.init()
                font = pygame.font.SysFont("dejavusans,arial,sans-serif", 20, bold=True)
                ip_str = self._get_local_ip()
                port = int(getattr(self.args, "web_port", 8080) or 8080)
                text = f"Demo Mode (AI-Generated Media)  •  Configure at https://{ip_str}:{port}"
                txt_surf = font.render(text, True, (241, 245, 249))
                tw, th = txt_surf.get_size()
                pad_x, pad_y = 20, 10
                pw, ph = tw + pad_x * 2, th + pad_y * 2
                pill = pygame.Surface((pw, ph), pygame.SRCALPHA)
                pygame.draw.rect(pill, (11, 15, 25, 195), (0, 0, pw, ph), border_radius=ph // 2)
                pygame.draw.rect(pill, (255, 255, 255, 140), (0, 0, pw, ph), width=1, border_radius=ph // 2)
                pill.blit(txt_surf, (pad_x, pad_y))
                self._cached_demo_pill = pill

            pill_surf = self._cached_demo_pill
            pw, ph = pill_surf.get_size()
            tw_canvas, th_canvas = target.get_size()
            target.blit(pill_surf, (tw_canvas - pw - 22, th_canvas - ph - 22))
        except Exception as e:
            logger.debug("Demo pill render skipped: %s", e)

    def render_frame(self):
        if self.screen is None:
            self.full_redraw_needed = False
            return
        if self.full_redraw_needed:
            target = self.canvas if self.canvas else self.screen
            if self.is_night_mode:
                target.fill((0, 0, 0))
            elif self.current_photo_surf:
                target.blit(self.current_photo_surf, (0, 0))
                if self.photo_manager and self.photo_manager.is_demo_mode:
                    self._draw_demo_setup_pill(target)
            else:
                target.fill((15, 23, 42))

            if self.rotation != 0 and self.canvas:
                rotated = pygame.transform.rotate(self.canvas, self.rotation)
                self.screen.blit(rotated, (0, 0))

            try:
                self._base_screen_surf = self.screen.copy()
            except Exception:
                self._base_screen_surf = None

            pygame.display.flip()
            if self._base_screen_surf is not None:
                try:
                    self.screen.blit(self._base_screen_surf, (0, 0))
                except Exception:
                    pass
            self.full_redraw_needed = False
            return

        # Fast-path live video frame blit via coherent `_base_screen_surf` (eliminates
        # hardware DOUBLEBUF page-flipping tearing / stale backbuffer flashes and
        # preserves the final video frame when switching tiles or slides)
        if self._video_playing and not self.is_night_mode and self._latest_video_frame is not None:
            frame_bytes = self._latest_video_frame
            self._latest_video_frame = None
            sx, sy, sw, sh = self._video_screen_rect
            try:
                vid_surf = pygame.image.frombuffer(frame_bytes, (sw, sh), "RGB")
                if self._base_screen_surf is not None:
                    self._base_screen_surf.blit(vid_surf, (sx, sy))
                    for stamp_surf, stamp_pos in self._video_corner_stamps:
                        self._base_screen_surf.blit(stamp_surf, stamp_pos)
                    if self.rotation == 0 and self.photo_manager and self.photo_manager.is_demo_mode:
                        self._draw_demo_setup_pill(self._base_screen_surf)
                    self.screen.blit(self._base_screen_surf, (0, 0))
                else:
                    self.screen.blit(vid_surf, (sx, sy))
                pygame.display.flip()
            except Exception as e:
                logger.debug("Video frame blit error: %s", e)

    def _start_wifi_keepalive_daemon(self):
        """
        Keeps USB Wi-Fi dongles (e.g. Ralink RT5370 / Realtek) and router ARP tables
        awake 24/7 (including during 8-hour Night Mode sleep) by disabling wlan0
        power_save and sending a 1-packet gateway heartbeat every 45 seconds.
        """
        def _get_default_gateway() -> str:
            try:
                with open("/proc/net/route", "r", encoding="utf-8") as f:
                    for line in f.readlines()[1:]:
                        parts = line.strip().split()
                        if len(parts) >= 3 and parts[1] == "00000000":
                            gw_hex = parts[2]
                            octets = [str(int(gw_hex[i:i + 2], 16)) for i in (6, 4, 2, 0)]
                            return ".".join(octets)
            except Exception:
                pass
            return "8.8.8.8"

        def _keepalive_worker():
            if sys.platform.startswith("linux"):
                for iw_bin in ("/usr/sbin/iw", "/sbin/iw", "iw"):
                    if shutil.which(iw_bin):
                        try:
                            subprocess.run(
                                [iw_bin, "dev", "wlan0", "set", "power_save", "off"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                timeout=3,
                            )
                        except Exception:
                            pass
                        break
            logger.info("📡 USB Wi-Fi keepalive watchdog active (45s heartbeat, power_save=off).")
            while self.is_running:
                gw = _get_default_gateway()
                try:
                    subprocess.run(
                        ["ping", "-c", "1", "-W", "2", gw],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=4,
                    )
                except Exception:
                    pass
                for _ in range(45):
                    if not self.is_running:
                        break
                    time.sleep(1.0)

        threading.Thread(target=_keepalive_worker, name="WiFiKeepAliveThread", daemon=True).start()

    def get_status_snapshot(self) -> Dict[str, Any]:
        items_count = 0
        videos_count = 0
        is_demo = True
        if self.photo_manager:
            is_demo = self.photo_manager.is_demo_mode
            with self.photo_manager._sources_lock:
                items_count = len(self.photo_manager.photo_sources)
                videos_count = len(self.photo_manager.video_bases)
        return {
            "is_night_mode": self.is_night_mode,
            "night_mode_enabled": self.night_mode_enabled,
            "is_paused": self.is_paused,
            "video_playing": self._video_playing,
            "is_demo_mode": is_demo,
            "photo_count": items_count,
            "media_count": items_count,
            "video_count": videos_count,
            "landscape_mode": self.args.landscape_mode,
            "transition": self.transition,
            "slide_duration": self.slide_duration,
            "night_start": self.args.night_start,
            "night_end": self.args.night_end,
            "canvas_w": self.canvas_w,
            "canvas_h": self.canvas_h,
            "rotation": self.rotation,
            "profile": self.hw_profile.get("name", "Auto"),
        }

    def get_current_settings_dict(self) -> Dict[str, Any]:
        return {
            "album": self.args.album,
            "language": getattr(self.args, "language", "en"),
            "landscape_mode": self.args.landscape_mode,
            "transition": self.transition,
            "duration": self.slide_duration,
            "videos": self.args.videos,
            "max_video_seconds": self.max_video_seconds,
            "fit": self.args.fit,
            "rotate": self.rotation,
            "refresh_hours": self.args.refresh_hours,
            "shuffle": bool(self.args.shuffle),
            "night_mode_enabled": self.night_mode_enabled,
            "night_start": self.args.night_start,
            "night_end": self.args.night_end,
        }

    def get_ram_log_tail(self, max_lines: int = 35) -> str:
        log_path = getattr(self.args, "log_file", DEFAULT_RAM_LOG)
        if not log_path or log_path == "none" or not os.path.exists(log_path):
            return "(RAM log file not available)"
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                return "".join(lines[-max_lines:]).strip()
        except Exception as e:
            return f"Error reading RAM log: {e}"

    def update_and_persist_settings(self, data: Dict[str, Any]) -> str:
        clean: Dict[str, Any] = {}
        if "album" in data:
            raw_album = str(data["album"]).strip()
            clean["album"] = raw_album if raw_album else "demo"
        if "language" in data and str(data["language"]) in ("en", "zh", "ja", "ko", "es", "de"):
            clean["language"] = str(data["language"])
        if "landscape_mode" in data and str(data["landscape_mode"]) in (
            "collage", "stack", "triple", "bento", "trio", "classic-5",
            "mosaic-7", "sidebar-9", "mondrian-8", "grid-3-2-3", "single"
        ):
            clean["landscape_mode"] = str(data["landscape_mode"])
        if "transition" in data and str(data["transition"]) in self.VALID_TRANSITIONS:
            clean["transition"] = str(data["transition"])
        if "duration" in data:
            try:
                clean["duration"] = max(3.0, min(3600.0, float(data["duration"])))
            except Exception:
                pass
        if "videos" in data and str(data["videos"]) in ("play", "skip", "still"):
            clean["videos"] = str(data["videos"])
        if "max_video_seconds" in data:
            try:
                clean["max_video_seconds"] = max(3.0, min(300.0, float(data["max_video_seconds"])))
            except Exception:
                pass
        if "fit" in data and str(data["fit"]) in ("contain-blur", "contain", "cover"):
            clean["fit"] = str(data["fit"])
        if "rotate" in data:
            try:
                rot = int(data["rotate"]) % 360
                if rot in (0, 90, 180, 270):
                    clean["rotate"] = rot
            except Exception:
                pass
        if "refresh_hours" in data:
            try:
                clean["refresh_hours"] = max(0.0, min(168.0, float(data["refresh_hours"])))
            except Exception:
                pass
        if "shuffle" in data:
            clean["shuffle"] = bool(data["shuffle"])
        if "night_mode_enabled" in data:
            clean["night_mode_enabled"] = bool(data["night_mode_enabled"])
        if "night_start" in data and str(data["night_start"]).strip():
            clean["night_start"] = str(data["night_start"]).strip()
        if "night_end" in data and str(data["night_end"]).strip():
            clean["night_end"] = str(data["night_end"]).strip()

        self.config_store.save_settings(clean)
        self._web_action_queue.append(("apply_settings", clean))
        return "Settings saved and applied live!"

    def queue_web_action(self, action: str, album_arg: str = "") -> str:
        valid = {
            "wake": "Screen woken from Night Mode.",
            "sleep": "Screen placed into Night Mode sleep.",
            "toggle_night": "Toggled Night Mode state.",
            "next": "Advancing to next slide...",
            "prev": "Returning to previous slide...",
            "pause_toggle": "Toggled slide pause.",
            "resync": "Syncing Google Photos album now...",
            "demo": "Switched to built-in Demo Showcase Pack!",
            "test_videos": "Queued video tile test sequence.",
            "restart": "Restarting slideshow service...",
            "reboot": "Rebooting device now...",
        }
        if action not in valid:
            return f"Unknown action: {action}"
        if action == "resync" and album_arg:
            self.config_store.save_settings({"album": album_arg})
        elif action == "demo":
            self.config_store.save_settings({"album": "demo"})
        self._web_action_queue.append((action, album_arg if action == "resync" else None))
        return valid[action]

    def _process_web_actions(self):
        while self._web_action_queue:
            action, payload = self._web_action_queue.popleft()
            logger.info("🌐 Executing web control action: %s", action)
            if action == "wake":
                self.is_night_mode = False
                self.last_slide_switch = time.time()
                self.full_redraw_needed = True
                if self.current_video_list:
                    self.start_slide_videos(self.current_video_list)
            elif action == "sleep":
                self.is_night_mode = True
                self.stop_video_playback()
                self.full_redraw_needed = True
            elif action == "toggle_night":
                self.is_night_mode = not self.is_night_mode
                self.last_slide_switch = time.time()
                self.full_redraw_needed = True
                if self.is_night_mode:
                    self.stop_video_playback()
                elif self.current_video_list:
                    self.start_slide_videos(self.current_video_list)
            elif action == "next":
                self.is_night_mode = False
                self.advance_slide()
            elif action == "prev":
                self.is_night_mode = False
                self.previous_slide()
            elif action == "pause_toggle":
                self.is_paused = not self.is_paused
                self.last_slide_switch = time.time()
            elif action == "demo":
                self.is_night_mode = False
                self.args.album = "demo"
                if self.photo_manager:
                    threading.Thread(
                        target=self.photo_manager.switch_album,
                        args=("demo", lambda: self._web_action_queue.append(("next", None))),
                        name="WebDemoModeSwitch",
                        daemon=True,
                    ).start()
            elif action == "resync":
                if self.photo_manager:
                    target_album = str(payload).strip() if payload else self.args.album
                    self.args.album = target_album
                    threading.Thread(
                        target=self.photo_manager.switch_album,
                        args=(target_album, lambda: self._web_action_queue.append(("next", None))),
                        name="WebManualAlbumSync",
                        daemon=True,
                    ).start()
            elif action == "test_videos":
                self.is_night_mode = False
                if self.photo_manager:
                    self.photo_manager.queue_video_positions_test()
                    self.advance_slide()
            elif action == "apply_settings" and isinstance(payload, dict):
                old_norm = PhotoManager.normalize_gphotos_url(self.args.album)
                old_mode = getattr(self.args, "landscape_mode", "collage")
                old_fit = getattr(self.args, "fit", "contain-blur")
                for k, v in payload.items():
                    setattr(self.args, k, v)
                raw_trans = str(getattr(self.args, "transition", "none") or "none").strip().lower()
                self.transition = raw_trans if raw_trans in self.VALID_TRANSITIONS else "none"
                self.slide_duration = float(self.args.duration)
                self.max_video_seconds = float(self.args.max_video_seconds)
                if "night_mode_enabled" in payload:
                    self.night_mode_enabled = bool(payload["night_mode_enabled"])
                    if not self.night_mode_enabled and self.is_night_mode:
                        self.is_night_mode = False
                        self.full_redraw_needed = True
                self.night_start_time = self._parse_time_str(self.args.night_start, 22, 0)
                self.night_end_time = self._parse_time_str(self.args.night_end, 6, 0)

                new_rot = int(getattr(self.args, "rotate", 0)) % 360
                if new_rot not in (0, 90, 180, 270):
                    new_rot = 0
                rotation_changed = (new_rot != self.rotation)
                if rotation_changed:
                    self.stop_video_playback()
                    self.rotation = new_rot
                    self.canvas_w = self.screen_h if self.rotation in (90, 270) else self.screen_w
                    self.canvas_h = self.screen_w if self.rotation in (90, 270) else self.screen_h
                    if self.screen is not None and PYGAME_AVAILABLE:
                        self.canvas = self.screen if self.rotation == 0 else pygame.Surface((self.canvas_w, self.canvas_h))
                    self._cached_demo_pill = None
                    if self.photo_manager:
                        self.photo_manager.target_w = self.canvas_w
                        self.photo_manager.target_h = self.canvas_h
                        self.photo_manager._slide_history.clear()
                    logger.info(
                        "🌐 Screen rotation updated live to %d° (Canvas: %dx%d)",
                        self.rotation, self.canvas_w, self.canvas_h
                    )

                layout_or_fit_changed = (
                    rotation_changed
                    or getattr(self.args, "landscape_mode", "collage") != old_mode
                    or getattr(self.args, "fit", "contain-blur") != old_fit
                )

                if self.photo_manager:
                    self.photo_manager.landscape_mode = self.args.landscape_mode
                    self.photo_manager.video_mode = self.args.videos
                    self.photo_manager.max_video_seconds = self.max_video_seconds
                    self.photo_manager.photo_fit = self.args.fit
                    self.photo_manager.shuffle = bool(self.args.shuffle)
                    self.photo_manager._resync_interval = float(self.args.refresh_hours) * 3600.0
                    new_norm = PhotoManager.normalize_gphotos_url(payload.get("album", self.args.album))
                    if new_norm != old_norm:
                        logger.info("🌐 Album source changed via Web Portal -> switching slide deck...")
                        threading.Thread(
                            target=self.photo_manager.switch_album,
                            args=(new_norm or "demo", lambda: self._web_action_queue.append(("next", None))),
                            name="WebAlbumChangeSync",
                            daemon=True,
                        ).start()
                    else:
                        with self.photo_manager._prefetch_lock:
                            self.photo_manager.prefetched_surface = None
                            self.photo_manager.prefetched_video_list = []
                        if layout_or_fit_changed:
                            self.advance_slide()
                        else:
                            self.photo_manager.trigger_prefetch_next()
            elif action == "restart":
                self.is_running = False
            elif action == "reboot":
                logger.warning("🌐 Reboot requested via Web Portal! Running sudo reboot...")
                try:
                    subprocess.Popen(["sudo", "reboot"])
                except Exception as e:
                    logger.error("Failed to execute sudo reboot: %s", e)

    def run(self):
        self.init_display()
        self._start_wifi_keepalive_daemon()
        if getattr(self.args, "web_port", 8080) > 0:
            start_web_config_server(self, self.config_store, port=int(self.args.web_port))

        self.photo_manager = PhotoManager(
            target_w=self.canvas_w,
            target_h=self.canvas_h,
            album_url=self.args.album,
            local_dir=self.args.local_dir,
            shuffle=self.args.shuffle,
            photo_fit=self.args.fit,
            landscape_mode=self.args.landscape_mode,
            video_mode=self.args.videos,
            max_video_seconds=self.args.max_video_seconds,
            resync_hours=self.args.refresh_hours,
            cache_file=self.args.cache_file,
            hw_profile=self.hw_profile,
        )
        self.current_photo_surf, self.current_video_list = self.photo_manager.get_initial_slide()
        self.full_redraw_needed = True

        if self.night_mode_enabled and self.is_within_night_schedule():
            self.is_night_mode = True
            self._last_scheduled_state = True
            logger.info("🌙 Starting in Night Mode schedule (%s - %s).", self.args.night_start, self.args.night_end)

        self.render_frame()
        if self.current_video_list and not self.is_night_mode:
            self.start_slide_videos(self.current_video_list)

        while self.is_running:
            self.handle_events()
            self._process_web_actions()
            self.check_night_mode_schedule()

            if self.is_night_mode:
                self.render_frame()
                self.clock.tick(2)
                continue

            if self._video_playing:
                self.render_frame()
                if self._video_eof and not self.is_paused:
                    if not self._start_next_queued_video():
                        self.advance_slide()
                self.clock.tick(self._video_fps)
                continue

            if not self.is_paused and (time.time() - self.last_slide_switch >= self.slide_duration):
                self.advance_slide()

            self.render_frame()
            self.clock.tick(self.loop_hz)

        self.stop_video_playback()
        pygame.quit()
        logger.info("Slideshow terminated cleanly.")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Linux Native Google Photos Slideshow (100% RAM logging & caching, zero disk/flash wear)"
    )
    parser.add_argument(
        "-p", "--profile",
        choices=["auto", "standard", "lowmem"],
        default="auto",
        help=(
            "Hardware optimization profile: "
            "'auto' (detects standard vs low-memory system via CPU arch & RAM), "
            "'standard' (multi-core / 1GB+ RAM, full 1080x1920 CDN fetch), "
            "or 'lowmem' (single-core / <=600MB RAM / low-power Wi-Fi optimization)"
        ),
    )
    parser.add_argument(
        "-a", "--album", "--album-id",
        dest="album",
        default="demo",
        help="Google Photos shared album URL or ID (default: 'demo' built-in showcase pack until configured via Web Portal)"
    )
    parser.add_argument(
        "-d", "--duration",
        type=float,
        default=15.0,
        help="Slide display duration in seconds (default: 15)"
    )
    parser.add_argument(
        "-l", "--landscape-mode",
        choices=[
            "collage",
            "stack",
            "triple",
            "bento",
            "trio",
            "classic-5",
            "mosaic-7",
            "sidebar-9",
            "mondrian-8",
            "grid-3-2-3",
            "single",
        ],
        default="collage",
        help=(
            "How photos are presented on screen: "
            "'collage' (weighted mix of full-screen single portrait/landscape slides + all 9 even-gap grid templates), "
            "'stack' (2-tile stack), "
            "'triple' (3-tile stack), "
            "'bento' (4-tile Bento grid), "
            "'trio' (3-tile: 1 Landscape + 2 Portraits), "
            "'classic-5' (5-tile: 1 Hero + 2 Portraits + 2 Landscapes), "
            "'mosaic-7' (7-tile alternating splits + corner feature), "
            "'sidebar-9' (9-tile stacked left column + right feature), "
            "'mondrian-8' (8-tile symmetrical Mondrian mosaic), "
            "'grid-3-2-3' (8-tile: 3 Portraits + 2 Landscapes + 3 Portraits), "
            "or 'single' (1 photo/video per slide)"
        ),
    )
    parser.add_argument(
        "-t", "--transition",
        choices=list(SlideshowApp.VALID_TRANSITIONS),
        default="none",
        help=(
            "Slide transition effect: "
            "'none' (default instant cut), 'fade' (crossfade), 'fade-black' (fade through black), "
            "'slide-left', 'slide-right', 'slide-up', 'wipe', or 'random'"
        ),
    )
    parser.add_argument(
        "-v", "--videos",
        choices=["play", "skip", "still"],
        default="play",
        help="How album videos are handled: 'play' (stream video in RAM via FFmpeg), 'skip' (ignore videos), or 'still' (show static thumbnail)"
    )
    parser.add_argument(
        "--max-video-seconds",
        type=float,
        default=20.0,
        help="Maximum seconds to play a video clip before advancing (default: 20)"
    )
    parser.add_argument(
        "--fit",
        choices=["contain-blur", "contain", "cover"],
        default="contain-blur",
        help="Single-photo fit mode: 'contain-blur' (bokeh blur backdrop), 'contain' (black bars), or 'cover' (crop to fill)"
    )
    parser.add_argument(
        "--rotate", "--rotation",
        dest="rotate",
        type=int,
        choices=[0, 90, 180, 270],
        default=0,
        help="Screen rotation in degrees: 0, 90, 180, or 270 (default: 0 horizontal)"
    )
    parser.add_argument(
        "--no-shuffle",
        dest="shuffle",
        action="store_false",
        default=True,
        help="Disable random photo shuffling"
    )
    parser.add_argument(
        "--refresh-hours",
        type=float,
        default=12.0,
        help="Interval in hours to re-sync Google Photos album in background (default: 12.0)"
    )
    parser.add_argument(
        "--local-dir",
        default=None,
        help="Optional fallback folder of local photos/videos"
    )
    parser.add_argument(
        "--night-start",
        default="22:00",
        help="Night mode blank screen start time (default: 22:00)"
    )
    parser.add_argument(
        "--night-end",
        default="06:00",
        help="Night mode blank screen end time (default: 06:00)"
    )
    parser.add_argument(
        "--disable-night-mode",
        action="store_true",
        help="Keep display on 24/7 (disable night mode sleep schedule)"
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=8080,
        help="Port for password-protected local web configuration portal (default: 8080, 0 to disable)"
    )
    parser.add_argument(
        "--language",
        default="en",
        choices=["en", "zh", "ja", "ko", "es", "de"],
        help="Default Web Portal UI language: en, zh, ja, ko, es, de (default: en)"
    )
    parser.add_argument(
        "--driver",
        default="auto",
        help="SDL video driver: 'auto' (kmsdrm on console, x11 on desktop), 'kmsdrm', or 'x11'"
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1920,
        help="Display width (default: 1920)"
    )
    parser.add_argument(
        "--height",
        type=int,
        default=1080,
        help="Display height (default: 1080)"
    )
    parser.add_argument(
        "-w", "--windowed", "--no-fullscreen",
        dest="windowed",
        action="store_true",
        help="Run in a window instead of fullscreen"
    )
    parser.add_argument(
        "--log-file",
        default=DEFAULT_RAM_LOG,
        help=f"Log file path in RAM tmpfs (default: {DEFAULT_RAM_LOG}, or 'none')"
    )
    parser.add_argument(
        "--cache-file",
        default=DEFAULT_RAM_CACHE,
        help=f"Album URL cache path in RAM tmpfs (default: {DEFAULT_RAM_CACHE})"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress stdout console output (log only to RAM tmpfs file)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose DEBUG logging"
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()
    profile_key, hw_profile = detect_hardware_profile(args.profile)

    is_NonInteractive = not sys.stdout.isatty() or bool(os.environ.get("INVOCATION_ID"))
    quiet_mode = args.quiet or is_NonInteractive
    setup_ram_logging(
        log_path=args.log_file,
        max_mb=int(hw_profile.get("ram_log_max_mb", 2)),
        debug=args.debug,
        quiet=quiet_mode,
        profile_key=profile_key,
        profile_info=hw_profile,
    )

    lock_file = None
    try:
        lock_file = open("/tmp/slideshow.lock", "a+")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, BlockingIOError, OSError):
        logger.critical("Another slideshow instance is already running (holding /tmp/slideshow.lock). Exiting.")
        sys.exit(1)

    app = SlideshowApp(args, hw_profile=hw_profile)

    def _sig_handler(signum, frame):
        logger.info("Signal %s received; shutting down...", signum)
        app.is_running = False

    def _wake_handler(signum, frame):
        app.is_night_mode = not app.is_night_mode
        app.last_slide_switch = time.time()
        app.full_redraw_needed = True
        if app.is_night_mode:
            app.stop_video_playback()
            logger.info("🌙 Night Mode enabled via SIGUSR1.")
        else:
            logger.info("☀️ Screen woken from Night Mode via SIGUSR1.")
            if app.current_video_list:
                app.start_slide_videos(app.current_video_list)

    def _test_videos_handler(signum, frame):
        app.is_night_mode = False
        if app.photo_manager:
            app.photo_manager.queue_video_positions_test()
            app.stop_video_playback()
            app.last_slide_switch = 0.0

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)
    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, _wake_handler)
    if hasattr(signal, "SIGUSR2"):
        signal.signal(signal.SIGUSR2, _test_videos_handler)

    try:
        app.run()
    finally:
        if lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
                lock_file.close()
            except Exception:
                pass
