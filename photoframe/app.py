"""
Native Linux Display Controller (Wayland / X11 / KMSDRM + Rotation + Video + Night Mode)
and CLI entry point.
"""

import argparse
import collections
import datetime
import fcntl
import os
import platform
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
from .mdns import (
    MDNS_HOSTNAME,
    get_active_mdns_hostname,
    normalize_mdns_base_name,
    set_mdns_base_name,
)
from .qrcode_gen import encode_qr_matrix
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

    STARTUP_OVERLAY_DURATION_SEC = 60.0
    _LAN_IP_TTL_SEC = 5.0
    _cached_lan_ip: str = "localhost"
    _cached_lan_ip_ts: float = 0.0
    _hw_h264_probed: bool = False
    _hw_h264_codec: Optional[str] = None

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

        set_mdns_base_name(getattr(self.args, "device_name", "") or "photoframe")

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
        self.app_start_time = time.time()
        self._startup_overlay_expired = False
        self.is_paused = False
        self.full_redraw_needed = True

        # Active FFmpeg video playback state (streams directly from /dev/shm)
        self._video_proc: Optional[subprocess.Popen] = None
        self._video_playing = False
        self._video_gen = 0
        self._video_screen_rect: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self._video_corner_stamps: List[Tuple[Any, Tuple[int, int]]] = []
        self._video_fps = int(self.hw_profile.get("video_fps", 24))
        self._video_max_edge = int(self.hw_profile.get("video_max_edge", 1920))
        self._video_decode_size: Optional[Tuple[int, int]] = None
        self._video_raw_surf: Optional[Any] = None
        self._latest_video_frame: Optional[Tuple[int, Any]] = None
        self._last_video_frame_bytes: Optional[Any] = None
        self._last_video_canvas_rect: Optional[Tuple[int, int, int, int]] = None
        self._video_tile_surf: Optional[Any] = None
        self._video_eof = False
        self._pending_slide_videos: Deque[Dict[str, Any]] = collections.deque()
        self._base_screen_surf: Optional[Any] = None
        self._last_pill_screen_rect: Optional[Tuple[int, int, int, int]] = None

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
        max_edge: Optional[int] = None,
    ) -> Tuple[int, int, int, int, str]:
        """
        Maps a video rectangle `(vx, vy, vw, vh)` on `self.canvas` (`canvas_w x canvas_h`)
        to the rotated physical `self.screen` (`screen_w x screen_h`), returning
        `(sx, sy, sw, sh, ffmpeg_vf_filter)`.
        Uses the same subject-aware `(cx, cy)` centering and tile border inset as
        `PhotoManager._paste_rounded_tile` so the first video frame aligns pixel-for-pixel
        with the static poster thumbnail without jumping.
        When `max_edge` (defaults to `self._video_max_edge`) is smaller than `max(vw, vh)`,
        caps FFmpeg's internal decode/transpose/pipe dimensions `(fw, fh)` to avoid
        expensive software upscaling/pipe copies on Raspberry Pi SBCs while `pygame.transform.scale`
        stretches the pre-allocated 32-bit surface cleanly at blit time.
        """
        fps = self._video_fps
        cx = max(0.0, min(1.0, float(centering[0]))) if centering and len(centering) >= 2 else 0.50
        cy = max(0.0, min(1.0, float(centering[1]))) if centering and len(centering) >= 2 else 0.50
        eff_max = self._video_max_edge if max_edge is None else int(max_edge)
        longest = max(vw, vh)
        if eff_max > 0 and longest > eff_max:
            ratio = eff_max / float(longest)
            dw = max(2, int(round(vw * ratio)) & ~1)
            dh = max(2, int(round(vh * ratio)) & ~1)
            ins = max(0, int(round(inset * ratio)))
        else:
            dw, dh = vw, vh
            ins = max(0, int(inset))

        scale_w = dw + 2 * ins
        scale_h = dh + 2 * ins
        crop_x = f"(in_w-{scale_w})*{cx:.4f}+{ins}" if ins > 0 else f"(in_w-out_w)*{cx:.4f}"
        crop_y = f"(in_h-{scale_h})*{cy:.4f}+{ins}" if ins > 0 else f"(in_h-out_h)*{cy:.4f}"
        scale_part = (
            f"fps={fps},scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase:flags=fast_bilinear,"
            f"crop={dw}:{dh}:{crop_x}:{crop_y}"
        )
        if self.rotation == 270:
            # 270 deg CCW == 90 deg CW (FFmpeg transpose=1)
            sx = self.canvas_h - (vy + vh)
            sy = vx
            self._video_decode_size = (dh, dw)
            return sx, sy, vh, vw, f"{scale_part},transpose=1"
        if self.rotation == 90:
            # 90 deg CCW (FFmpeg transpose=2)
            sx = vy
            sy = self.canvas_w - (vx + vw)
            self._video_decode_size = (dh, dw)
            return sx, sy, vh, vw, f"{scale_part},transpose=2"
        if self.rotation == 180:
            sx = self.canvas_w - (vx + vw)
            sy = self.canvas_h - (vy + vh)
            self._video_decode_size = (dw, dh)
            return sx, sy, vw, vh, f"{scale_part},hflip,vflip"
        self._video_decode_size = (dw, dh)
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

    @classmethod
    def _detect_hw_h264_decoder(cls) -> Optional[str]:
        """
        Returns an explicit hardware H.264 decoder only when opted in via
        `PHOTOFRAME_HW_DECODE` (e.g. `PHOTOFRAME_HW_DECODE=1` or `h264_v4l2m2m`).
        On Raspberry Pi (`bcm2835-codec`), V4L2 M2M writes decoded YU12 frames into
        uncached VideoCore CMA memory; following decode with CPU `-vf` filters (`scale`,
        `crop`, `transpose`) + `rawvideo` color conversion stalls on uncached bus reads
        (~9 FPS vs ~28.5 FPS with `-threads 0` multi-core NEON decode into cached CPU RAM).
        """
        env_val = os.environ.get("PHOTOFRAME_HW_DECODE", "").strip()
        if not env_val or env_val.lower() in ("0", "false", "no", "off", "none"):
            return None
        if env_val.lower() not in ("1", "true", "yes", "on", "auto"):
            return env_val
        if cls._hw_h264_probed:
            return cls._hw_h264_codec
        cls._hw_h264_probed = True
        cls._hw_h264_codec = None
        try:
            arch = platform.machine().lower()
            if not arch.startswith(("arm", "aarch64")):
                return None
            if not os.path.exists("/dev/video10") or not shutil.which("ffmpeg"):
                return None
            out = subprocess.check_output(
                ["ffmpeg", "-hide_banner", "-decoders"],
                stderr=subprocess.DEVNULL,
                timeout=3.0,
            ).decode("utf-8", errors="ignore")
            if "h264_v4l2m2m" in out:
                cls._hw_h264_codec = "h264_v4l2m2m"
                logger.info("Detected Raspberry Pi V4L2 M2M hardware H.264 decoder (h264_v4l2m2m).")
        except Exception:
            cls._hw_h264_codec = None
        return cls._hw_h264_codec

    def _select_video_pix_fmt(self, sw: int, sh: int) -> Tuple[str, int, Optional[Any]]:
        """
        Matches FFmpeg's rawvideo output pixel format to `self.screen`'s native 32-bit
        memory layout (`bgr0` / `rgb0`) so SDL blits use pure `memcpy` instead of per-pixel
        24->32 bit conversion on CPU. Falls back cleanly to `rgb24` (3 bytes/pixel).
        """
        if (
            PYGAME_AVAILABLE
            and self.screen is not None
            and sys.byteorder == "little"
            and sw > 0
            and sh > 0
        ):
            try:
                if self.screen.get_bitsize() == 32:
                    masks = tuple(self.screen.get_masks()[:3])
                    fmt = None
                    if masks == (0xFF0000, 0x00FF00, 0x0000FF):
                        fmt = "bgr0"
                    elif masks == (0x0000FF, 0x00FF00, 0xFF0000):
                        fmt = "rgb0"
                    if fmt is not None:
                        tile_surf = pygame.Surface((sw, sh))
                        if tile_surf.get_bitsize() == 32 and tile_surf.get_pitch() == sw * 4:
                            return fmt, 4, tile_surf
            except Exception:
                pass
        return "rgb24", 3, None

    @staticmethod
    def _read_exact_into(stream: Any, buf: bytearray) -> bool:
        """Reads exactly `len(buf)` bytes from `stream` into pre-allocated `buf` with zero heap churn."""
        nbytes = len(buf)
        mv = memoryview(buf)
        got = 0
        while got < nbytes:
            n = stream.readinto(mv[got:])
            if not n:
                return False
            got += n
        return True

    def _frame_bytes_to_surface(self, frame_bytes: Any, sw: int, sh: int) -> Optional[Any]:
        """
        Converts raw `frame_bytes` (`bgr0`/`rgb0` 32-bit or `rgb24` 24-bit) into a `(sw, sh)`
        surface ready for fast blitting. Supports both native `(sw, sh)` buffers and capped
        `(fw, fh)` decode buffers scaled in-place into pre-allocated `self._video_tile_surf`.
        """
        if not frame_bytes or sw <= 0 or sh <= 0:
            return None
        nlen = len(frame_bytes)
        if nlen == sw * sh * 4:
            tile_surf = self._video_tile_surf
            if tile_surf is None or tile_surf.get_size() != (sw, sh) or tile_surf.get_pitch() != sw * 4:
                tile_surf = pygame.Surface((sw, sh))
                if tile_surf.get_bitsize() != 32 or tile_surf.get_pitch() != sw * 4:
                    return pygame.image.frombuffer(frame_bytes, (sw, sh), "BGRA")
                self._video_tile_surf = tile_surf
            v = tile_surf.get_view("0")
            mv = memoryview(v)
            mv[:] = frame_bytes
            del mv, v
            return tile_surf
        if nlen == sw * sh * 3:
            return pygame.image.frombuffer(frame_bytes, (sw, sh), "RGB")

        dec_sz = self._video_decode_size
        if dec_sz is not None:
            fw, fh = dec_sz
            if fw > 0 and fh > 0 and (fw, fh) != (sw, sh):
                if nlen == fw * fh * 4:
                    raw_surf = self._video_raw_surf
                    if raw_surf is None or raw_surf.get_size() != (fw, fh) or raw_surf.get_pitch() != fw * 4:
                        raw_surf = pygame.Surface((fw, fh))
                        if raw_surf.get_bitsize() != 32 or raw_surf.get_pitch() != fw * 4:
                            s = pygame.image.frombuffer(frame_bytes, (fw, fh), "BGRA")
                            return pygame.transform.scale(s, (sw, sh))
                        self._video_raw_surf = raw_surf
                    v = raw_surf.get_view("0")
                    mv = memoryview(v)
                    mv[:] = frame_bytes
                    del mv, v
                    tile_surf = self._video_tile_surf
                    if (
                        tile_surf is None
                        or tile_surf.get_size() != (sw, sh)
                        or tile_surf.get_bitsize() != raw_surf.get_bitsize()
                    ):
                        tile_surf = pygame.Surface((sw, sh))
                        self._video_tile_surf = tile_surf
                    pygame.transform.scale(raw_surf, (sw, sh), tile_surf)
                    return tile_surf
                if nlen == fw * fh * 3:
                    raw24 = pygame.image.frombuffer(frame_bytes, (fw, fh), "RGB")
                    return pygame.transform.scale(raw24, (sw, sh))
        return None

    def _compose_video_tile_surface(self, frame_bytes: Any) -> Optional[Any]:
        """
        Builds a screen-oriented `(sw x sh)` surface copy for the active tile from raw
        `frame_bytes`, with the rounded-border corner stamps re-applied.
        Returns None if the buffer does not match the active tile dimensions.
        """
        sx, sy, sw, sh = self._video_screen_rect
        src_surf = self._frame_bytes_to_surface(frame_bytes, sw, sh)
        if src_surf is None:
            return None
        tile = src_surf.copy()
        for stamp_surf, (px, py) in self._video_corner_stamps:
            tile.blit(stamp_surf, (px - sx, py - sy))
        return tile

    def _freeze_last_video_frame_into_slide(self):
        """
        Pastes the last displayed video frame into `self.current_photo_surf` (canvas
        orientation) so any later full redraw (overlay timeout, web action, next video tile
        on the same slide) keeps showing that frame instead of flashing back to the poster.
        """
        frame_bytes = self._last_video_frame_bytes
        canvas_rect = self._last_video_canvas_rect
        if not frame_bytes or canvas_rect is None or self.current_photo_surf is None or not PYGAME_AVAILABLE:
            return
        try:
            tile = self._compose_video_tile_surface(frame_bytes)
            if tile is None:
                return
            if self.rotation != 0:
                tile = pygame.transform.rotate(tile, -self.rotation)
            vx, vy, vw, vh = canvas_rect
            if tile.get_size() == (vw, vh):
                self.current_photo_surf.blit(tile, (vx, vy))
        except Exception as e:
            logger.debug("Freeze last video frame skipped: %s", e)

    def stop_video_playback(self, clear_queue: bool = True):
        if self._video_playing:
            self._freeze_last_video_frame_into_slide()
            # Keep hardware DOUBLEBUF backbuffer coherent with frontbuffer at the stopped tile
            if (
                PYGAME_AVAILABLE
                and self.screen is not None
                and self._base_screen_surf is not None
                and (self.screen.get_flags() & pygame.DOUBLEBUF)
            ):
                sx, sy, sw, sh = self._video_screen_rect
                if sw > 0 and sh > 0:
                    try:
                        self.screen.blit(self._base_screen_surf, (sx, sy), (sx, sy, sw, sh))
                    except Exception:
                        pass
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
        self._last_video_frame_bytes = None
        self._last_video_canvas_rect = None
        self._video_decode_size = None
        self._video_raw_surf = None
        self._video_tile_surf = None
        self._video_eof = False
        self._video_corner_stamps = []

    def start_slide_videos(self, video_list: Optional[List[Dict[str, Any]]]):
        self.stop_video_playback(clear_queue=True)
        if not video_list or self.is_night_mode:
            return
        self._pending_slide_videos = collections.deque(video_list)
        self._start_next_queued_video()

    @staticmethod
    def _build_ffmpeg_rawvideo_cmd(
        mp4_path: str,
        play_sec: float,
        vf_filter: str,
        pix_fmt: str,
        hw_decoder: Optional[str] = None,
    ) -> List[str]:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
        ]
        if hw_decoder:
            cmd.extend(["-c:v", hw_decoder])
        else:
            cmd.extend(["-threads", "0"])
        cmd.extend([
            "-t", f"{play_sec:.1f}",
            "-i", mp4_path,
            "-an",
            "-vf", vf_filter,
            "-f", "rawvideo",
            "-pix_fmt", pix_fmt,
            "pipe:1",
        ])
        return cmd

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
            fw, fh = self._video_decode_size or (sw, sh)
            self._video_screen_rect = (sx, sy, sw, sh)
            self._last_video_canvas_rect = (vx, vy, vw, vh)
            self._video_corner_stamps = self._build_video_corner_stamps(sx, sy, sw, sh, inset)

            pix_fmt, bpp, tile_surf = self._select_video_pix_fmt(sw, sh)
            self._video_tile_surf = tile_surf
            if (fw, fh) != (sw, sh) and bpp == 4 and PYGAME_AVAILABLE and self.screen is not None:
                try:
                    self._video_raw_surf = pygame.Surface((fw, fh))
                except Exception:
                    self._video_raw_surf = None
            else:
                self._video_raw_surf = None
            frame_bytes_len = fw * fh * bpp

            hw_dec = self._detect_hw_h264_decoder()
            cmd = self._build_ffmpeg_rawvideo_cmd(mp4_path, play_sec, vf_filter, pix_fmt, hw_decoder=hw_dec)

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=frame_bytes_len * 2,
                )
            except Exception as e:
                logger.warning("Failed to start ffmpeg video playback: %s", e)
                continue

            gen = self._video_gen
            self._video_proc = proc
            self._video_playing = True
            self._video_eof = False
            logger.info(
                "▶️  Playing video in tile %d/%d (%dx%d -> %dx%d pipe at (%d,%d) @ %d FPS, fmt=%s, dec=%s, max %.0fs)",
                tile_idx, total_tiles, vw, vh, fw, fh, vx, vy, self._video_fps, pix_fmt, hw_dec or "sw-mt", play_sec
            )

            def reader_worker(
                initial_proc: subprocess.Popen,
                nbytes: int,
                fps: int,
                expected_gen: int,
                used_hw_dec: Optional[str],
                src_mp4: str,
                dur_sec: float,
                vf_str: str,
                pfmt: str,
            ):
                p = initial_proc
                frame_interval = 1.0 / max(1, fps)
                # 4 pre-allocated reusable buffers eliminate ~150 MB/s heap churn & GC pauses:
                # 1 on screen (_last_video_frame_bytes), 1 queued (_latest_video_frame), 2 free.
                buf_pool = [bytearray(nbytes) for _ in range(4)]
                frame_idx = 0
                next_frame_t = time.perf_counter() + frame_interval
                clip_deadline = time.perf_counter() + dur_sec + 1.5
                try:
                    while self._video_playing and self._video_gen == expected_gen and p.stdout:
                        if self.is_paused:
                            time.sleep(0.05)
                            next_frame_t = time.perf_counter() + frame_interval
                            clip_deadline += 0.05
                            continue
                        if time.perf_counter() >= clip_deadline:
                            break

                        latest_pair = self._latest_video_frame
                        busy_a = id(latest_pair[1]) if isinstance(latest_pair, tuple) and len(latest_pair) == 2 else 0
                        busy_b = id(self._last_video_frame_bytes)
                        target_buf = buf_pool[0]
                        for candidate in buf_pool:
                            cid = id(candidate)
                            if cid != busy_a and cid != busy_b:
                                target_buf = candidate
                                break

                        ok = self._read_exact_into(p.stdout, target_buf)
                        if not ok:
                            # If hardware V4L2 M2M failed on frame 0 (e.g. non-H.264 stream or
                            # unsupported profile), seamlessly fall back to multi-core software decode.
                            if frame_idx == 0 and used_hw_dec and self._video_playing and self._video_gen == expected_gen:
                                used_hw_dec = None
                                type(self)._hw_h264_codec = None
                                try:
                                    p.kill()
                                except Exception:
                                    pass
                                sw_cmd = self._build_ffmpeg_rawvideo_cmd(
                                    src_mp4, dur_sec, vf_str, pfmt, hw_decoder=None
                                )
                                p = subprocess.Popen(
                                    sw_cmd,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL,
                                    bufsize=nbytes * 2,
                                )
                                if self._video_gen == expected_gen and self._video_playing:
                                    self._video_proc = p
                                    next_frame_t = time.perf_counter() + frame_interval
                                    clip_deadline = time.perf_counter() + dur_sec + 1.5
                                    continue
                            break

                        if self._video_gen != expected_gen or not self._video_playing:
                            break

                        frame_idx += 1
                        self._latest_video_frame = (expected_gen, target_buf)
                        now = time.perf_counter()
                        sleep_s = next_frame_t - now
                        if sleep_s > 0.001:
                            time.sleep(sleep_s)
                        next_frame_t = max(now, next_frame_t) + frame_interval
                except Exception:
                    pass
                finally:
                    try:
                        if p.poll() is None:
                            p.kill()
                    except Exception:
                        pass
                    if self._video_proc is p and self._video_gen == expected_gen:
                        self._video_eof = True

            threading.Thread(
                target=reader_worker,
                args=(proc, frame_bytes_len, self._video_fps, gen, hw_dec, mp4_path, play_sec, vf_filter, pix_fmt),
                daemon=True,
            ).start()
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

    @classmethod
    def _get_local_ip(cls) -> str:
        now = time.monotonic()
        if (now - cls._cached_lan_ip_ts) < cls._LAN_IP_TTL_SEC and cls._cached_lan_ip:
            return cls._cached_lan_ip
        ip = "localhost"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
        except Exception:
            ip = "localhost"
        cls._cached_lan_ip = ip
        cls._cached_lan_ip_ts = now
        return ip

    @staticmethod
    def _rects_intersect(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        return ax < (bx + bw) and (ax + aw) > bx and ay < (by + bh) and (ay + ah) > by

    def _should_show_portal_overlay(self) -> bool:
        if self.is_night_mode:
            return False
        if self.photo_manager and self.photo_manager.is_demo_mode:
            return True
        return (time.time() - self.app_start_time) < self.STARTUP_OVERLAY_DURATION_SEC

    @staticmethod
    def _create_qr_surface(qr_url: str, target_size_px: int = 84) -> Any:
        matrix = encode_qr_matrix(qr_url)
        n = len(matrix)
        quiet_zone = 2
        total_modules = n + quiet_zone * 2
        out_size = max(total_modules, target_size_px)
        surf = pygame.Surface((out_size, out_size))
        surf.fill((255, 255, 255))
        for r in range(n):
            for c in range(n):
                if matrix[r][c]:
                    x1 = ((c + quiet_zone) * out_size) // total_modules
                    y1 = ((r + quiet_zone) * out_size) // total_modules
                    x2 = ((c + quiet_zone + 1) * out_size) // total_modules
                    y2 = ((r + quiet_zone + 1) * out_size) // total_modules
                    pygame.draw.rect(surf, (0, 0, 0), (x1, y1, max(1, x2 - x1), max(1, y2 - y1)))
        return surf

    def _draw_demo_setup_pill(self, target: Any, apply_rotation: int = 0):
        """
        Draws a sleek, non-intrusive bottom-right translucent card containing the
        friendly mDNS URL (`http://photoframe.local:8080`), fallback IP, and a
        ~1x1 inch (`84x84` px) QR code. Shown for the first 60 seconds of app startup
        and at all times while in Demo Showcase Mode, always on top of photos and videos.
        """
        try:
            is_demo = bool(self.photo_manager and self.photo_manager.is_demo_mode)
            ip_str = self._get_local_ip()
            port = int(getattr(self.args, "web_port", 8080) or 8080)
            mdns_host = get_active_mdns_hostname()
            cache_key = (is_demo, ip_str, port, mdns_host)
            if (
                not hasattr(self, "_cached_demo_pill")
                or self._cached_demo_pill is None
                or getattr(self, "_cached_demo_pill_key", None) != cache_key
            ):
                pygame.font.init()
                title_font = pygame.font.SysFont("dejavusans,arial,sans-serif", 17, bold=True)
                url_font = pygame.font.SysFont("dejavusans,arial,sans-serif", 20, bold=True)
                sub_font = pygame.font.SysFont("dejavusans,arial,sans-serif", 16, bold=False)

                line1 = "Demo Mode (AI-Generated Media)" if is_demo else "Admin Web Control Portal"
                line2 = f"Configure at http://{mdns_host}:{port}"
                line3 = f"Fallback IP: http://{ip_str}:{port}  •  Scan QR →"

                s1 = title_font.render(line1, True, (56, 189, 248))
                s2 = url_font.render(line2, True, (248, 250, 252))
                s3 = sub_font.render(line3, True, (148, 163, 184))

                text_w = max(s1.get_width(), s2.get_width(), s3.get_width())
                qr_size = 84  # ~1x1 inch QR code
                pad_outer = 14
                gap = 18
                pw = pad_outer + text_w + gap + qr_size + pad_outer
                ph = qr_size + pad_outer * 2

                pill = pygame.Surface((pw, ph), pygame.SRCALPHA)
                pygame.draw.rect(pill, (11, 15, 25, 215), (0, 0, pw, ph), border_radius=18)
                pygame.draw.rect(pill, (255, 255, 255, 115), (0, 0, pw, ph), width=1, border_radius=18)

                tx = pad_outer + 4
                pill.blit(s1, (tx, 14))
                pill.blit(s2, (tx, 41))
                pill.blit(s3, (tx, 71))

                qr_surf = self._create_qr_surface(f"http://{ip_str}:{port}", qr_size)
                qx = pw - pad_outer - qr_size
                qy = pad_outer
                pygame.draw.rect(pill, (255, 255, 255, 255), (qx - 2, qy - 2, qr_size + 4, qr_size + 4), border_radius=6)
                pill.blit(qr_surf, (qx, qy))

                self._cached_demo_pill = pill
                self._cached_demo_pill_key = cache_key
                self._cached_rotated_pill_map = {0: pill}

            rot_map = getattr(self, "_cached_rotated_pill_map", None)
            if not isinstance(rot_map, dict) or rot_map.get(0) is not self._cached_demo_pill:
                rot_map = {0: self._cached_demo_pill}
                self._cached_rotated_pill_map = rot_map

            if apply_rotation not in rot_map:
                rot_map[apply_rotation] = pygame.transform.rotate(self._cached_demo_pill, apply_rotation)
            pill_surf = rot_map[apply_rotation]

            pw, ph = pill_surf.get_size()
            tw_canvas, th_canvas = target.get_size()
            margin = 22
            if apply_rotation == 90:
                pos = (tw_canvas - pw - margin, margin)
            elif apply_rotation == 180:
                pos = (margin, margin)
            elif apply_rotation == 270:
                pos = (margin, th_canvas - ph - margin)
            else:
                pos = (tw_canvas - pw - margin, th_canvas - ph - margin)
            target.blit(pill_surf, pos)

            # Record screen-space bounding box of the pill so live video frames only
            # redraw the pill when the video tile actually overlaps it.
            base_pw, base_ph = self._cached_demo_pill.get_size()
            sw_scr, sh_scr = self.screen.get_size() if self.screen is not None else (self.screen_w, self.screen_h)
            if self.rotation == 90:
                self._last_pill_screen_rect = (sw_scr - base_ph - margin, margin, base_ph, base_pw)
            elif self.rotation == 180:
                self._last_pill_screen_rect = (margin, margin, base_pw, base_ph)
            elif self.rotation == 270:
                self._last_pill_screen_rect = (margin, sh_scr - base_pw - margin, base_ph, base_pw)
            else:
                self._last_pill_screen_rect = (sw_scr - base_pw - margin, sh_scr - base_ph - margin, base_pw, base_ph)
        except Exception as e:
            logger.debug("Demo pill render skipped: %s", e)

    def render_frame(self):
        if self.screen is None:
            self.full_redraw_needed = False
            return
        if not self._startup_overlay_expired and (time.time() - self.app_start_time) >= self.STARTUP_OVERLAY_DURATION_SEC:
            self._startup_overlay_expired = True
            if not (self.photo_manager and self.photo_manager.is_demo_mode):
                self.full_redraw_needed = True

        show_overlay = self._should_show_portal_overlay()
        if show_overlay:
            expected_pill_key = (
                bool(self.photo_manager and self.photo_manager.is_demo_mode),
                self._get_local_ip(),
                int(getattr(self.args, "web_port", 8080) or 8080),
                get_active_mdns_hostname(),
            )
            if getattr(self, "_cached_demo_pill_key", None) != expected_pill_key:
                self.full_redraw_needed = True

        if self.full_redraw_needed:
            target = self.canvas if self.canvas else self.screen
            if self.is_night_mode:
                target.fill((0, 0, 0))
            elif self.current_photo_surf:
                target.blit(self.current_photo_surf, (0, 0))
                # Keep the currently playing video frame in place during a full redraw
                # (e.g. overlay timeout) instead of flashing the static poster for a frame.
                if self._video_playing and self._last_video_frame_bytes and self._last_video_canvas_rect:
                    try:
                        tile = self._compose_video_tile_surface(self._last_video_frame_bytes)
                        if tile is not None:
                            if self.rotation != 0:
                                tile = pygame.transform.rotate(tile, -self.rotation)
                            target.blit(tile, self._last_video_canvas_rect[:2])
                    except Exception:
                        pass
                if show_overlay:
                    self._draw_demo_setup_pill(target)
                else:
                    self._last_pill_screen_rect = None
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

        # Fast-path sub-rectangle live video frame blit:
        # Copies ONLY the active video tile `(sx, sy, sw, sh)` to `self.screen` (saving
        # up to 85% RAM copy bandwidth on Raspberry Pi 3 vs. full-screen 1920x1080 blits)
        # and uses dirty-rect `pygame.display.update(tile_rect)` on software surfaces.
        if self._video_playing and not self.is_night_mode and self._latest_video_frame is not None:
            latest = self._latest_video_frame
            self._latest_video_frame = None
            sx, sy, sw, sh = self._video_screen_rect
            if not isinstance(latest, tuple) or len(latest) != 2 or sw <= 0 or sh <= 0:
                return
            frame_gen, frame_bytes = latest
            # Drop frames from a previous clip (race between reader thread and stop/start)
            # or any buffer that doesn't match this tile's screen or capped decode dimensions.
            if frame_gen != self._video_gen:
                return
            try:
                vid_surf = self._frame_bytes_to_surface(frame_bytes, sw, sh)
                if vid_surf is None:
                    return
                self._last_video_frame_bytes = frame_bytes
                tile_rect = (sx, sy, sw, sh)
                overlay_overlaps = show_overlay and (
                    self._last_pill_screen_rect is None
                    or self._rects_intersect(tile_rect, self._last_pill_screen_rect)
                )
                if self._base_screen_surf is not None:
                    self._base_screen_surf.blit(vid_surf, (sx, sy))
                    for stamp_surf, stamp_pos in self._video_corner_stamps:
                        self._base_screen_surf.blit(stamp_surf, stamp_pos)
                    if overlay_overlaps:
                        self._draw_demo_setup_pill(self._base_screen_surf, apply_rotation=self.rotation)
                    self.screen.blit(self._base_screen_surf, (sx, sy), tile_rect)
                else:
                    self.screen.blit(vid_surf, (sx, sy))
                    if overlay_overlaps:
                        self._draw_demo_setup_pill(self.screen, apply_rotation=self.rotation)

                if self.screen.get_flags() & pygame.DOUBLEBUF:
                    pygame.display.flip()
                else:
                    pygame.display.update(tile_rect)
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
        from . import _load_version_info
        ver_str, build_num = _load_version_info()
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
            "app_version": ver_str,
            "app_version_code": build_num,
            "mdns_hostname": get_active_mdns_hostname(),
            "lan_ip": self._get_local_ip(),
            "edition": "gphotos",
            "platform": "linux",
        }

    def get_current_settings_dict(self) -> Dict[str, Any]:
        return {
            "album": self.args.album,
            "device_name": getattr(self.args, "device_name", ""),
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
        if "device_name" in data:
            raw_dev = str(data["device_name"]).strip()
            clean["device_name"] = normalize_mdns_base_name(raw_dev) if raw_dev else ""
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
                if "device_name" in payload:
                    set_mdns_base_name(getattr(self.args, "device_name", "") or "photoframe")
                    self._cached_demo_pill = None
                    self.full_redraw_needed = True
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
                # Poll at >=60 Hz so decoded frames from `reader_worker` are presented
                # within <8ms without beat-frequency drops between two equal-rate clocks.
                # Idle polls (`_latest_video_frame is None`) return immediately in <5us.
                self.clock.tick(max(60, self._video_fps * 2))
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
        "--device-name", "--hostname",
        dest="device_name",
        default="",
        help="Optional custom mDNS base hostname (default: 'photoframe' -> photoframe.local, auto-numbers on LAN conflict)"
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
