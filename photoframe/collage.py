"""
Even-Gap Collage Composer, Orientation Matcher & Subject-Aware Smart Cropper
============================================================================
Composes 9 even-gap grid templates across both Horizontal (`0°/180°`, `target_w >= target_h`)
and Portrait (`90°/270°`, `target_h > target_w`) screen orientations over a dark bokeh
background with thin `2px` white borders and live FFmpeg video playback in any tile.
"""

from __future__ import annotations

import collections
import gc
import os
import random
import shutil
import ssl
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    pygame = None
    PYGAME_AVAILABLE = False

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps
    PIL_AVAILABLE = True
except ImportError:
    Image = ImageDraw = ImageEnhance = ImageFilter = ImageOps = None
    PIL_AVAILABLE = False

from .gphotos import GooglePhotosMixin
from .hardware import DEFAULT_RAM_CACHE, HARDWARE_PROFILES, logger


class PhotoManager(GooglePhotosMixin):
    """
    Manages Google Photos shared album scraping, full pagination, video detection,
    RAM prefetching, and orientation-matched multi-photo collage generation.
    """

    COLLAGE_STYLES = (
        "stack-2",
        "stack-3",
        "bento-4",
        "grid-trio",
        "grid-classic-5",
        "grid-mosaic-7",
        "grid-sidebar-9",
        "grid-mondrian-8",
        "grid-3-2-3",
    )

    def __init__(
        self,
        target_w: int,
        target_h: int,
        album_url: str,
        local_dir: Optional[str] = None,
        shuffle: bool = True,
        photo_fit: str = "contain-blur",
        landscape_mode: str = "collage",
        video_mode: str = "play",
        max_video_seconds: float = 20.0,
        resync_hours: float = 12.0,
        cache_file: str = DEFAULT_RAM_CACHE,
        hw_profile: Optional[Dict[str, Any]] = None,
        auto_refresh: bool = True,
    ):
        self.target_w = target_w
        self.target_h = target_h
        self.hw_profile = hw_profile or HARDWARE_PROFILES["pi2"]
        self.cdn_scale = float(self.hw_profile.get("cdn_scale", 1.0))
        cdn_max = max(320, int(round(max(self.target_w, self.target_h) * self.cdn_scale)))
        self.cdn_w = cdn_max
        self.cdn_h = cdn_max
        self.http_timeout = float(self.hw_profile.get("http_timeout", 8.0))
        self.sync_sleep_sec = float(self.hw_profile.get("sync_sleep_sec", 0.05))
        self.video_fps = int(self.hw_profile.get("video_fps", 18))

        self.gphotos_url = self.normalize_gphotos_url(album_url)
        self.local_dir = os.path.abspath(local_dir) if local_dir else None
        self.shuffle = shuffle
        self.photo_fit = photo_fit
        self.landscape_mode = landscape_mode
        self.video_mode = video_mode if shutil.which("ffmpeg") else ("skip" if video_mode == "play" else video_mode)
        self.max_video_seconds = max(3.0, float(max_video_seconds))
        self._resync_interval = max(0.0, resync_hours * 3600.0)
        self.cache_file = cache_file

        self.photo_sources: List[str] = []
        self.video_bases: Set[str] = set()
        self.current_index = 0
        self.ssl_ctx = ssl._create_unverified_context()

        self._sources_lock = threading.Lock()
        self._is_syncing_album = False
        self._album_generation = 0
        self._last_resync_time = time.time()

        p_max = int(self.hw_profile.get("portrait_queue_max", 8))
        l_max = int(self.hw_profile.get("landscape_queue_max", 8))
        h_max = int(self.hw_profile.get("slide_history_max", 3))
        self._portrait_queue: Deque[Tuple[Any, Optional[str]]] = collections.deque(maxlen=p_max)
        self._landscape_queue: Deque[Tuple[Any, Optional[str]]] = collections.deque(maxlen=l_max)
        self._video_url_queue: Deque[str] = collections.deque(maxlen=4)
        self._slide_history: Deque[Tuple[Any, List[Dict[str, Any]]]] = collections.deque(maxlen=h_max)

        self._video_ram_slot = 0
        self._test_styles_queue: Deque[str] = collections.deque()
        self._cached_test_videos: List[Tuple[Any, str]] = []

        self.current_surface: Optional[Any] = None
        self.current_video_list: List[Dict[str, Any]] = []
        self.prefetched_surface: Optional[Any] = None
        self.prefetched_video_list: List[Dict[str, Any]] = []
        self._is_prefetching = False
        self._prefetch_lock = threading.Lock()

        if auto_refresh:
            self.refresh_sources()

    @staticmethod
    def _is_landscape(img: Any) -> bool:
        return img.width >= img.height

    def _download_media_item(self, url: str) -> Optional[Tuple[Any, Optional[str]]]:
        """
        Downloads a media item (photo or video) from `url`, returning `(pil_img, mp4_path_or_None)`.
        Returns `None` if skipped or download failed.
        """
        if self.is_video_source(url):
            if self.video_mode == "skip":
                logger.info("Skipping video item per --videos skip.")
                return None
            if self.video_mode == "play":
                poster = self._download_raw_pil(url)
                mp4_path = self._download_video_to_ram(url)
                if poster and mp4_path:
                    return (poster, mp4_path)
                return None
        img = self._download_raw_pil(url)
        if not img:
            return None
        return (img, None)

    def _collect_oriented_items(
        self,
        need_l: int,
        need_p: int,
        first_item: Optional[Tuple[Any, Optional[str]]] = None,
        max_videos_per_collage: int = 2,
        max_attempts: int = 26,
    ) -> Tuple[List[Tuple[Any, Optional[str]]], List[Tuple[Any, Optional[str]]]]:
        """
        Strictly collects `need_l` Landscape items and `need_p` Portrait items.
        Never mixes landscape items into portrait lists or portrait items into landscape lists.
        """
        l_items: List[Tuple[Any, Optional[str]]] = []
        p_items: List[Tuple[Any, Optional[str]]] = []
        vid_count = 0

        if first_item is not None:
            f_img, f_mp4 = first_item
            if f_mp4 is not None:
                vid_count += 1
            if self._is_landscape(f_img):
                if need_l > 0:
                    l_items.append(first_item)
                else:
                    self._landscape_queue.appendleft(first_item)
            else:
                if need_p > 0:
                    p_items.append(first_item)
                else:
                    self._portrait_queue.appendleft(first_item)

        if len(l_items) < need_l and self._landscape_queue:
            skipped_l: List[Tuple[Any, Optional[str]]] = []
            while len(l_items) < need_l and self._landscape_queue:
                it = self._landscape_queue.popleft()
                if it[1] is not None and vid_count >= max_videos_per_collage:
                    skipped_l.append(it)
                    continue
                if it[1] is not None:
                    vid_count += 1
                l_items.append(it)
            for it in reversed(skipped_l):
                self._landscape_queue.appendleft(it)

        if len(p_items) < need_p and self._portrait_queue:
            skipped_p: List[Tuple[Any, Optional[str]]] = []
            while len(p_items) < need_p and self._portrait_queue:
                it = self._portrait_queue.popleft()
                if it[1] is not None and vid_count >= max_videos_per_collage:
                    skipped_p.append(it)
                    continue
                if it[1] is not None:
                    vid_count += 1
                p_items.append(it)
            for it in reversed(skipped_p):
                self._portrait_queue.appendleft(it)

        attempts = 0
        while (len(l_items) < need_l or len(p_items) < need_p) and attempts < max_attempts:
            attempts += 1
            url = self._pop_next_source_url()
            if not url:
                break
            if self.is_video_source(url) and self.video_mode == "play" and vid_count >= max_videos_per_collage:
                if len(self._video_url_queue) < self._video_url_queue.maxlen:
                    self._video_url_queue.append(url)
                continue

            item = self._download_media_item(url)
            if not item:
                continue
            img, mp4_path = item
            if self._is_landscape(img):
                if len(l_items) < need_l:
                    if mp4_path is not None:
                        vid_count += 1
                    l_items.append(item)
                else:
                    self._landscape_queue.append(item)
            else:
                if len(p_items) < need_p:
                    if mp4_path is not None:
                        vid_count += 1
                    p_items.append(item)
                else:
                    self._portrait_queue.append(item)

        return l_items, p_items

    # --------------------------------------------------------------------------
    # Frame, Even-Gap Grid Collage & Any-Tile Video Slide Composers
    # --------------------------------------------------------------------------

    def _make_bokeh_canvas(self, img: Any, brightness: float = 0.50) -> Any:
        """Fast 64x36 BoxBlur bokeh backdrop (<15ms on Pi Zero / Pi 2)."""
        try:
            thumb_w = 64
            thumb_h = max(1, int(round(thumb_w * (self.target_h / self.target_w))))
            bg_scale = max(thumb_w / img.width, thumb_h / img.height)
            bw = max(thumb_w, int(round(img.width * bg_scale)))
            bh = max(thumb_h, int(round(img.height * bg_scale)))

            bg_thumb = img.resize((bw, bh), Image.Resampling.BILINEAR)
            cx = (bw - thumb_w) // 2
            cy = (bh - thumb_h) // 2
            bg_thumb = bg_thumb.crop((cx, cy, cx + thumb_w, cy + thumb_h))

            if ImageFilter:
                bg_thumb = bg_thumb.filter(ImageFilter.BoxBlur(2))
            if ImageEnhance:
                bg_thumb = ImageEnhance.Brightness(bg_thumb).enhance(brightness)
            else:
                bg_thumb = bg_thumb.point(lambda p: int(p * brightness))

            return bg_thumb.resize((self.target_w, self.target_h), Image.Resampling.BILINEAR)
        except Exception:
            if Image:
                return Image.new("RGB", (self.target_w, self.target_h), (12, 16, 24))
            return None

    @staticmethod
    def _compute_core_content_centering(img: Any, target_w: int, target_h: int) -> Tuple[float, float]:
        """
        Computes a subject-aware `(cx, cy)` centering point in `[0.0, 1.0]` for `ImageOps.fit`
        by combining a fast 48x48 edge/detail energy centroid with an ergonomic photographic
        prior (protecting upper-middle faces/heads in portraits and foreground/horizon detail
        in landscapes). Runs in <0.4ms on Raspberry Pi.
        """
        is_port = img.height > img.width
        prior_x = 0.50
        prior_y = 0.38 if is_port else 0.46
        try:
            thumb_sz = 48
            if hasattr(img, "get_edge_bytes"):
                raw = img.get_edge_bytes(thumb_sz)
            elif ImageFilter and Image and hasattr(img, "convert"):
                gray = img.convert("L").resize((thumb_sz, thumb_sz), Image.Resampling.BILINEAR)
                edges = gray.filter(ImageFilter.FIND_EDGES)
                raw = edges.tobytes()
            else:
                return (prior_x, prior_y)
            sum_w = 0.0
            sum_wx = 0.0
            sum_wy = 0.0
            margin = 3
            span = thumb_sz - 1
            for py in range(margin, thumb_sz - margin):
                row_off = py * thumb_sz
                wy_bias = 1.0 - 0.35 * abs((py / span) - prior_y)
                for px in range(margin, thumb_sz - margin):
                    val = raw[row_off + px]
                    if val > 18:
                        w = float(val) * wy_bias
                        sum_w += w
                        sum_wx += w * (px / span)
                        sum_wy += w * (py / span)
            if sum_w > 100.0:
                det_x = sum_wx / sum_w
                det_y = sum_wy / sum_w
                cx = max(0.20, min(0.80, 0.55 * det_x + 0.45 * prior_x))
                cy = max(0.18, min(0.78, 0.55 * det_y + 0.45 * prior_y))
                return (cx, cy)
        except Exception:
            pass
        return (prior_x, prior_y)

    @classmethod
    def _smart_crop_fit(cls, img: Any, w: int, h: int) -> Any:
        """
        Fits `img` into `(w, h)` without distortion, using subject-aware center cropping
        whenever the aspect ratio of `img` differs from `(w, h)`.
        """
        w = max(2, int(w))
        h = max(2, int(h))
        if img.width == w and img.height == h:
            return img
        centering = cls._compute_core_content_centering(img, w, h)
        if hasattr(img, "crop_fit"):
            return img.crop_fit((w, h), centering=centering)
        if ImageOps and Image and hasattr(ImageOps, "fit"):
            return ImageOps.fit(img, (w, h), method=Image.Resampling.BILINEAR, centering=centering)
        return img.resize((w, h))

    def _compose_single_photo(self, img: Any) -> Tuple[Any, List[Tuple[int, int, int, int]]]:
        """Renders a single photo/video and returns `(canvas, [(x, y, w, h)])`."""
        if self.photo_fit == "cover":
            fitted = self._smart_crop_fit(img, self.target_w, self.target_h)
            return fitted, [(0, 0, self.target_w, self.target_h)]

        img_aspect = img.width / max(1, img.height)
        canvas_aspect = self.target_w / max(1, self.target_h)
        same_orientation = (img.width >= img.height) == (self.target_w >= self.target_h)
        if self.photo_fit == "contain-blur" and same_orientation and (0.82 <= (img_aspect / canvas_aspect) <= 1.22):
            fitted = self._smart_crop_fit(img, self.target_w, self.target_h)
            return fitted, [(0, 0, self.target_w, self.target_h)]

        scale = min(self.target_w / img.width, self.target_h / img.height)
        fit_w = min(self.target_w, max(2, (int(round(img.width * scale)) // 2) * 2))
        fit_h = min(self.target_h, max(2, (int(round(img.height * scale)) // 2) * 2))
        if (img.width, img.height) != (fit_w, fit_h):
            fit_img = img.resize((fit_w, fit_h), Image.Resampling.BILINEAR) if Image else img.resize((fit_w, fit_h))
        else:
            fit_img = img

        if fit_w == self.target_w and fit_h == self.target_h:
            return fit_img, [(0, 0, fit_w, fit_h)]

        is_pil_img = bool(Image and hasattr(img, "im"))
        if self.photo_fit == "contain":
            canvas = Image.new("RGB", (self.target_w, self.target_h), (0, 0, 0)) if is_pil_img else fit_img
        else:
            canvas = (self._make_bokeh_canvas(img, brightness=0.55) if is_pil_img else None) or fit_img

        fx = (self.target_w - fit_w) // 2
        fy = (self.target_h - fit_h) // 2
        if hasattr(canvas, "paste") and (not is_pil_img or hasattr(fit_img, "im")):
            canvas.paste(fit_img, (fx, fy))
        return canvas, [(fx, fy, fit_w, fit_h)]

    @classmethod
    def _paste_rounded_tile(cls, canvas: Any, tile_img: Any, x: int, y: int, w: int, h: int, radius: int = 10, border: bool = True):
        """
        Renders `tile_img` into the fixed grid cell `(x, y, w, h)` using subject-aware
        center-crop (`_smart_crop_fit`) so the photo fills 100% of the tile cleanly with
        rounded corners and a crisp 2px white border while preserving core content.
        """
        w = max(2, int(w))
        h = max(2, int(h))
        fitted = cls._smart_crop_fit(tile_img, w, h)

        if not ImageDraw or radius <= 0 or not Image:
            if hasattr(canvas, "paste"):
                canvas.paste(fitted, (x, y))
            return

        mask = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
        canvas.paste(fitted, (x, y), mask)

        if border:
            canvas_draw = ImageDraw.Draw(canvas)
            canvas_draw.rounded_rectangle(
                (x, y, x + w - 1, y + h - 1),
                radius=radius,
                outline=(255, 255, 255),
                width=2,
            )

    def _get_grid_template_cells(self, style: str) -> List[Tuple[int, int, int, int]]:
        """
        Returns mathematically aligned `(x, y, w, h)` grid cells with a constant 14px outer
        frame and constant 10px inner dividers across the entire `target_w x target_h` canvas.
        Automatically optimizes tile geometry for both Horizontal (`target_w >= target_h`, e.g. 0°/180°
        1920x1080) and Portrait (`target_h > target_w`, e.g. 90°/270° 1080x1920) orientations so every
        Landscape cell (`w >= h`) has a natural 4:3 / 3:2 / 16:9 aspect ratio and every Portrait cell
        (`h > w`) has a natural 3:4 / 4:5 aspect ratio.
        """
        pad = 14
        gap = 10
        x0 = pad
        y0 = pad
        W = ((self.target_w - 2 * pad) // 2) * 2
        H = ((self.target_h - 2 * pad) // 2) * 2
        w_half = (((W - gap) // 2) // 2) * 2
        w_right_half = ((W - w_half - gap) // 2) * 2
        h_half = (((H - gap) // 2) // 2) * 2
        h_bot_half = ((H - h_half - gap) // 2) * 2

        # ======================================================================
        # A. HORIZONTAL SCREEN ORIENTATION (0° / 180°, e.g. 1920x1080, W >= H)
        # ======================================================================
        if self.target_w >= self.target_h:
            if style == "horiz-2p":
                return [
                    (x0, y0, w_half, H),
                    (x0 + w_half + gap, y0, w_right_half, H),
                ]

            if style == "stack-2":
                w_port = ((int(H * 0.74)) // 2) * 2
                w_land = ((W - w_port - gap) // 2) * 2
                if random.choice([True, False]):
                    return [
                        (x0, y0, w_land, H),
                        (x0 + w_land + gap, y0, w_port, H),
                    ]
                return [
                    (x0, y0, w_port, H),
                    (x0 + w_port + gap, y0, w_land, H),
                ]

            if style == "stack-3":
                w_hero = ((int(W * 0.578)) // 2) * 2
                w_side = ((W - w_hero - gap) // 2) * 2
                if random.choice([True, False]):
                    return [
                        (x0, y0, w_hero, H),
                        (x0 + w_hero + gap, y0, w_side, h_half),
                        (x0 + w_hero + gap, y0 + h_half + gap, w_side, h_bot_half),
                    ]
                return [
                    (x0, y0, w_side, h_half),
                    (x0, y0 + h_half + gap, w_side, h_bot_half),
                    (x0 + w_side + gap, y0, w_hero, H),
                ]

            if style in ("bento-4", "collage-bento"):
                w_wide = ((int(W * 0.545)) // 2) * 2
                w_narrow = ((W - w_wide - gap) // 2) * 2
                variant = random.choice(["even", "alt_a", "alt_b"])
                if variant == "alt_a":
                    return [
                        (x0, y0, w_wide, h_half),
                        (x0 + w_wide + gap, y0, w_narrow, h_half),
                        (x0, y0 + h_half + gap, w_narrow, h_bot_half),
                        (x0 + w_narrow + gap, y0 + h_half + gap, w_wide, h_bot_half),
                    ]
                if variant == "alt_b":
                    return [
                        (x0, y0, w_narrow, h_half),
                        (x0 + w_narrow + gap, y0, w_wide, h_half),
                        (x0, y0 + h_half + gap, w_wide, h_bot_half),
                        (x0 + w_wide + gap, y0 + h_half + gap, w_narrow, h_bot_half),
                    ]
                return [
                    (x0, y0, w_half, h_half),
                    (x0 + w_half + gap, y0, w_right_half, h_half),
                    (x0, y0 + h_half + gap, w_half, h_bot_half),
                    (x0 + w_half + gap, y0 + h_half + gap, w_right_half, h_bot_half),
                ]

            if style == "grid-trio":
                w_port = ((int(H * 0.80)) // 2) * 2
                w_land = ((W - w_port - gap) // 2) * 2
                if random.choice([True, False]):
                    return [
                        (x0, y0, w_port, H),
                        (x0 + w_port + gap, y0, w_land, h_half),
                        (x0 + w_port + gap, y0 + h_half + gap, w_land, h_bot_half),
                    ]
                return [
                    (x0, y0, w_land, h_half),
                    (x0, y0 + h_half + gap, w_land, h_bot_half),
                    (x0 + w_land + gap, y0, w_port, H),
                ]

            if style == "grid-classic-5":
                w_p = ((int(H * 0.386)) // 2) * 2
                w_l_mid = ((W - 2 * w_p - 2 * gap) // 2) * 2
                if random.choice([True, False]):
                    return [
                        (x0, y0, w_half, h_half),
                        (x0 + w_half + gap, y0, w_right_half, h_half),
                        (x0, y0 + h_half + gap, w_p, h_bot_half),
                        (x0 + w_p + gap, y0 + h_half + gap, w_l_mid, h_bot_half),
                        (x0 + w_p + gap + w_l_mid + gap, y0 + h_half + gap, w_p, h_bot_half),
                    ]
                return [
                    (x0, y0, w_p, h_half),
                    (x0 + w_p + gap, y0, w_l_mid, h_half),
                    (x0 + w_p + gap + w_l_mid + gap, y0, w_p, h_half),
                    (x0, y0 + h_half + gap, w_half, h_bot_half),
                    (x0 + w_half + gap, y0 + h_half + gap, w_right_half, h_bot_half),
                ]

            if style == "grid-mosaic-7":
                w_left = ((int(W * 0.286)) // 2) * 2
                w_right = ((W - w_left - gap) // 2) * 2
                r_h1 = (((H - 2 * gap) // 3) // 2) * 2
                r_h2 = r_h1
                r_h3 = ((H - r_h1 - r_h2 - 2 * gap) // 2) * 2
                w_p = ((int(h_half * 0.75)) // 2) * 2
                w_l = ((w_right - w_p - gap) // 2) * 2
                x_r = x0 + w_left + gap
                y_r2 = y0 + h_half + gap
                return [
                    (x0, y0, w_left, r_h1),
                    (x0, y0 + r_h1 + gap, w_left, r_h2),
                    (x0, y0 + r_h1 + gap + r_h2 + gap, w_left, r_h3),
                    (x_r, y0, w_p, h_half),
                    (x_r + w_p + gap, y0, w_l, h_half),
                    (x_r, y_r2, w_l, h_bot_half),
                    (x_r + w_l + gap, y_r2, w_p, h_bot_half),
                ]

            if style == "grid-sidebar-9":
                w_col = ((int(W * 0.286)) // 2) * 2
                w_feat = ((W - 2 * w_col - 2 * gap) // 2) * 2
                r_h1 = (((H - 2 * gap) // 3) // 2) * 2
                r_h2 = r_h1
                r_h3 = ((H - r_h1 - r_h2 - 2 * gap) // 2) * 2
                w_fp1 = (((w_feat - gap) // 2) // 2) * 2
                w_fp2 = ((w_feat - w_fp1 - gap) // 2) * 2
                x_c2 = x0 + w_col + gap
                x_c3 = x_c2 + w_col + gap
                return [
                    (x0, y0, w_col, r_h1),
                    (x0, y0 + r_h1 + gap, w_col, r_h2),
                    (x0, y0 + r_h1 + gap + r_h2 + gap, w_col, r_h3),
                    (x_c2, y0, w_col, r_h1),
                    (x_c2, y0 + r_h1 + gap, w_col, r_h2),
                    (x_c2, y0 + r_h1 + gap + r_h2 + gap, w_col, r_h3),
                    (x_c3, y0, w_fp1, h_half),
                    (x_c3 + w_fp1 + gap, y0, w_fp2, h_half),
                    (x_c3, y0 + h_half + gap, w_feat, h_bot_half),
                ]

            if style == "grid-mondrian-8":
                w_p_out = ((int(H * 0.37)) // 2) * 2
                w_p_in = ((int(H * 0.356)) // 2) * 2
                w_hero_l = ((W - w_p_out - 2 * w_p_in - 3 * gap) // 2) * 2
                y_r2 = y0 + h_half + gap
                return [
                    (x0, y0, w_p_out, h_half),
                    (x0 + w_p_out + gap, y0, w_hero_l, h_half),
                    (x0 + w_p_out + gap + w_hero_l + gap, y0, w_p_in, h_half),
                    (x0 + w_p_out + gap + w_hero_l + gap + w_p_in + gap, y0, w_p_in, h_half),
                    (x0, y_r2, w_p_in, h_bot_half),
                    (x0 + w_p_in + gap, y_r2, w_p_in, h_bot_half),
                    (x0 + 2 * (w_p_in + gap), y_r2, w_hero_l, h_bot_half),
                    (x0 + 2 * (w_p_in + gap) + w_hero_l + gap, y_r2, w_p_out, h_bot_half),
                ]

            if style == "grid-3-2-3":
                w_side = ((int(W * 0.29)) // 2) * 2
                w_mid = ((W - 2 * w_side - 2 * gap) // 2) * 2
                r_h1 = (((H - 2 * gap) // 3) // 2) * 2
                r_h2 = r_h1
                r_h3 = ((H - r_h1 - r_h2 - 2 * gap) // 2) * 2
                x_mid = x0 + w_side + gap
                x_right = x_mid + w_mid + gap
                return [
                    (x0, y0, w_side, r_h1),
                    (x0, y0 + r_h1 + gap, w_side, r_h2),
                    (x0, y0 + r_h1 + gap + r_h2 + gap, w_side, r_h3),
                    (x_mid, y0, w_mid, h_half),
                    (x_mid, y0 + h_half + gap, w_mid, h_bot_half),
                    (x_right, y0, w_side, r_h1),
                    (x_right, y0 + r_h1 + gap, w_side, r_h2),
                    (x_right, y0 + r_h1 + gap + r_h2 + gap, w_side, r_h3),
                ]

            return self._get_grid_template_cells("bento-4")

        # ======================================================================
        # B. PORTRAIT SCREEN ORIENTATION (90° / 270°, e.g. 1080x1920, H > W)
        # ======================================================================
        if style == "stack-2":
            h_top = (((H - gap) // 2) // 2) * 2
            h_bot = ((H - h_top - gap) // 2) * 2
            return [
                (x0, y0, W, h_top),
                (x0, y0 + h_top + gap, W, h_bot),
            ]

        if style == "stack-3":
            h1 = (((H - 2 * gap) // 3) // 2) * 2
            h2 = h1
            h3 = ((H - h1 - h2 - 2 * gap) // 2) * 2
            return [
                (x0, y0, W, h1),
                (x0, y0 + h1 + gap, W, h2),
                (x0, y0 + h1 + gap + h2 + gap, W, h3),
            ]

        if style in ("bento-4", "collage-bento"):
            h_hero = ((int(H * 0.38)) // 2) * 2
            h_pair = ((H - 2 * h_hero - 2 * gap) // 2) * 2
            variant = random.choice(["sandwich", "pair_top", "pair_bottom"])
            if variant == "pair_top":
                y1 = y0 + h_pair + gap
                y2 = y1 + h_hero + gap
                return [
                    (x0, y0, w_half, h_pair),
                    (x0 + w_half + gap, y0, w_right_half, h_pair),
                    (x0, y1, W, h_hero),
                    (x0, y2, W, h_hero),
                ]
            if variant == "pair_bottom":
                y1 = y0 + h_hero + gap
                y2 = y1 + h_hero + gap
                return [
                    (x0, y0, W, h_hero),
                    (x0, y1, W, h_hero),
                    (x0, y2, w_half, h_pair),
                    (x0 + w_half + gap, y2, w_right_half, h_pair),
                ]
            y1 = y0 + h_hero + gap
            y2 = y1 + h_pair + gap
            return [
                (x0, y0, W, h_hero),
                (x0, y1, w_half, h_pair),
                (x0 + w_half + gap, y1, w_right_half, h_pair),
                (x0, y2, W, h_hero),
            ]

        if style == "grid-trio":
            h_land = ((int(H * 0.455)) // 2) * 2
            h_port = ((H - h_land - gap) // 2) * 2
            if random.choice([True, False]):
                return [
                    (x0, y0, W, h_land),
                    (x0, y0 + h_land + gap, w_half, h_port),
                    (x0 + w_half + gap, y0 + h_land + gap, w_right_half, h_port),
                ]
            return [
                (x0, y0, w_half, h_port),
                (x0 + w_half + gap, y0, w_right_half, h_port),
                (x0, y0 + h_port + gap, W, h_land),
            ]

        if style == "grid-classic-5":
            h1 = ((int(H * 0.415)) // 2) * 2
            h2 = ((int(H * 0.368)) // 2) * 2
            h3 = ((H - h1 - h2 - 2 * gap) // 2) * 2
            y1 = y0 + h1 + gap
            y2 = y1 + h2 + gap
            return [
                (x0, y0, W, h1),
                (x0, y1, w_half, h2),
                (x0 + w_half + gap, y1, w_right_half, h2),
                (x0, y2, w_half, h3),
                (x0 + w_half + gap, y2, w_right_half, h3),
            ]

        if style == "grid-mosaic-7":
            r1_h = ((int(H * 0.264)) // 2) * 2
            r2_h = r1_h
            r3_h = ((H - r1_h - r2_h - 2 * gap) // 2) * 2
            w_sm = ((int(W * 0.38)) // 2) * 2
            w_lg = ((W - w_sm - gap) // 2) * 2
            w_r3_left = ((int(W * 0.438)) // 2) * 2
            w_r3_right = ((W - w_r3_left - gap) // 2) * 2
            r3_sub1 = (((r3_h - gap) // 2) // 2) * 2
            r3_sub2 = ((r3_h - r3_sub1 - gap) // 2) * 2
            y_r2 = y0 + r1_h + gap
            y_r3 = y_r2 + r2_h + gap
            return [
                (x0, y0, w_sm, r1_h),
                (x0 + w_sm + gap, y0, w_lg, r1_h),
                (x0, y_r2, w_lg, r2_h),
                (x0 + w_lg + gap, y_r2, w_sm, r2_h),
                (x0, y_r3, w_r3_left, r3_sub1),
                (x0, y_r3 + r3_sub1 + gap, w_r3_left, r3_sub2),
                (x0 + w_r3_left + gap, y_r3, w_r3_right, r3_h),
            ]

        if style == "grid-sidebar-9":
            band_h = (((H - 2 * gap) // 3) // 2) * 2
            w_left = ((int(W * 0.365)) // 2) * 2
            w_right = ((W - w_left - gap) // 2) * 2
            cells: List[Tuple[int, int, int, int]] = []
            cur_y = y0
            for b in range(3):
                bh = band_h if b < 2 else ((H - 2 * (band_h + gap)) // 2) * 2
                sh1 = (((bh - gap) // 2) // 2) * 2
                sh2 = ((bh - sh1 - gap) // 2) * 2
                cells.append((x0, cur_y, w_left, sh1))
                cells.append((x0, cur_y + sh1 + gap, w_left, sh2))
                cells.append((x0 + w_left + gap, cur_y, w_right, bh))
                cur_y += bh + gap
            return cells

        if style == "grid-mondrian-8":
            m_top_h = ((int(H * 0.232)) // 2) * 2
            m_bot_h = m_top_h
            m_mid_h = ((H - m_top_h - m_bot_h - 2 * gap) // 2) * 2
            c_side = (((W - 2 * gap) // 3) // 2) * 2
            c_mid = ((W - 2 * c_side - 2 * gap) // 2) * 2
            w_wide = ((W - c_side - gap) // 2) * 2
            y_mid = y0 + m_top_h + gap
            y_bot = y_mid + m_mid_h + gap
            m_sub1 = (((m_mid_h - gap) // 2) // 2) * 2
            m_sub2 = ((m_mid_h - m_sub1 - gap) // 2) * 2
            return [
                (x0, y0, c_side, m_top_h),
                (x0 + c_side + gap, y0, w_wide, m_top_h),
                (x0, y_mid, c_side, m_mid_h),
                (x0 + c_side + gap, y_mid, c_mid, m_sub1),
                (x0 + c_side + gap, y_mid + m_sub1 + gap, c_mid, m_sub2),
                (x0 + c_side + gap + c_mid + gap, y_mid, c_side, m_mid_h),
                (x0, y_bot, w_wide, m_bot_h),
                (x0 + w_wide + gap, y_bot, c_side, m_bot_h),
            ]

        if style == "grid-3-2-3":
            g_top_h = ((int(H * 0.365)) // 2) * 2
            g_bot_h = g_top_h
            g_mid_h = ((H - g_top_h - g_bot_h - 2 * gap) // 2) * 2
            w_third = (((W - 2 * gap) // 3) // 2) * 2
            w_mid_third = ((W - 2 * w_third - 2 * gap) // 2) * 2
            y_gm = y0 + g_top_h + gap
            y_gb = y_gm + g_mid_h + gap
            return [
                (x0, y0, w_third, g_top_h),
                (x0 + w_third + gap, y0, w_mid_third, g_top_h),
                (x0 + w_third + gap + w_mid_third + gap, y0, w_third, g_top_h),
                (x0, y_gm, w_half, g_mid_h),
                (x0 + w_half + gap, y_gm, w_right_half, g_mid_h),
                (x0, y_gb, w_third, g_bot_h),
                (x0 + w_third + gap, y_gb, w_mid_third, g_bot_h),
                (x0 + w_third + gap + w_mid_third + gap, y_gb, w_third, g_bot_h),
            ]

        return self._get_grid_template_cells("stack-2")

    def _compose_grid_collage(
        self,
        style: str,
        first_item: Optional[Tuple[Any, Optional[str]]] = None,
    ) -> Tuple[Optional[Any], List[Dict[str, Any]]]:
        """
        Composes an even-spaced grid collage for `style` over a dark bokeh background
        with thin 2px white tile borders. Strictly aligns photo orientation with tile
        orientation (Landscape photo in Landscape tile, Portrait photo in Portrait tile).
        """
        cells = self._get_grid_template_cells(style)
        need_l = sum(1 for (_, _, w, h) in cells if w >= h)
        need_p = len(cells) - need_l

        l_items, p_items = self._collect_oriented_items(need_l, need_p, first_item=first_item)

        if len(l_items) < need_l or len(p_items) < need_p:
            if self.target_w >= self.target_h:
                fallback_order = [
                    "grid-classic-5",
                    "bento-4",
                    "grid-trio",
                    "stack-3",
                    "stack-2",
                    "horiz-2p",
                ]
            else:
                fallback_order = [
                    "grid-classic-5",
                    "grid-trio",
                    "bento-4",
                    "stack-3",
                    "stack-2",
                ]
            chosen_fallback = None
            for fb_style in fallback_order:
                fb_cells = self._get_grid_template_cells(fb_style)
                fb_l = sum(1 for (_, _, w, h) in fb_cells if w >= h)
                fb_p = len(fb_cells) - fb_l
                if len(l_items) >= fb_l and len(p_items) >= fb_p:
                    chosen_fallback = (fb_style, fb_cells, fb_l, fb_p)
                    break

            if chosen_fallback is None:
                all_collected = l_items + p_items
                if not all_collected:
                    return None, []
                single_item = all_collected[0]
                for it in reversed(l_items[1:] if l_items and single_item == l_items[0] else l_items):
                    self._landscape_queue.appendleft(it)
                for it in reversed(p_items[1:] if p_items and single_item == p_items[0] else p_items):
                    self._portrait_queue.appendleft(it)
                s_img, s_mp4 = single_item
                out, rects = self._compose_single_photo(s_img)
                vids = self._extract_video_list_from_tiles([single_item], rects, inset_border=False)
                return out, vids

            style, cells, need_l, need_p = chosen_fallback
            while len(l_items) > need_l:
                self._landscape_queue.appendleft(l_items.pop())
            while len(p_items) > need_p:
                self._portrait_queue.appendleft(p_items.pop())

        if len(l_items) > 1:
            random.shuffle(l_items)
        if len(p_items) > 1:
            random.shuffle(p_items)

        group: List[Tuple[Any, Optional[str]]] = []
        li = 0
        pi = 0
        for (_, _, w, h) in cells:
            if w >= h:
                group.append(l_items[li])
                li += 1
            else:
                group.append(p_items[pi])
                pi += 1

        canvas = self._make_bokeh_canvas(group[0][0], brightness=0.32)
        for idx, (x, y, w, h) in enumerate(cells):
            img = group[idx][0]
            self._paste_rounded_tile(canvas, img, x, y, w, h, radius=10, border=True)

        vid_slots = [idx + 1 for idx, it in enumerate(group) if it[1] is not None]
        if vid_slots:
            logger.info("Rendering %s layout (%d tiles, video in tile %s).", style, len(cells), vid_slots)
        else:
            logger.info("Rendering %s layout with %d tiles.", style, len(cells))

        vids = self._extract_video_list_from_tiles(group, cells, inset_border=True)
        del group
        gc.collect()
        return canvas, vids

    def _extract_video_list_from_tiles(
        self,
        group: List[Tuple[Any, Optional[str]]],
        tile_rects: List[Tuple[int, int, int, int]],
        inset_border: bool = True,
        max_seconds_override: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Builds the list of video playback descriptors for any collage tiles that hold videos."""
        video_list: List[Dict[str, Any]] = []
        total_tiles = len(group)
        for idx, (pil_img, mp4_path) in enumerate(group):
            if not mp4_path or idx >= len(tile_rects):
                continue
            tx, ty, tw, th = tile_rects[idx]
            inset = 2 if (inset_border and tw > 8 and th > 8) else 0
            if inset > 0:
                vx = tx + inset
                vy = ty + inset
                vw = max(2, ((tw - 2 * inset) // 2) * 2)
                vh = max(2, ((th - 2 * inset) // 2) * 2)
            else:
                vx = tx
                vy = ty
                vw = max(2, (tw // 2) * 2)
                vh = max(2, (th // 2) * 2)
            centering = (
                self._compute_core_content_centering(pil_img, tw, th)
                if pil_img is not None
                else (0.50, 0.50)
            )
            info: Dict[str, Any] = {
                "mp4_path": mp4_path,
                "rect": (vx, vy, vw, vh),
                "centering": centering,
                "inset": inset,
                "fps": self.video_fps,
                "tile_index": idx + 1,
                "total_tiles": total_tiles,
            }
            if max_seconds_override is not None:
                info["max_seconds"] = max_seconds_override
            video_list.append(info)
        return video_list

    def queue_video_positions_test(self):
        """Queues all 9 even-gap collage templates to test video streaming across all layouts."""
        self._test_styles_queue.clear()
        self._test_styles_queue.extend([
            "grid-trio",
            "grid-classic-5",
            "grid-mosaic-7",
            "grid-sidebar-9",
            "grid-mondrian-8",
            "grid-3-2-3",
            "stack-2",
            "stack-3",
            "bento-4",
        ])
        self.prefetched_surface = None
        self.prefetched_video_list = []
        logger.info("🧪 Queued video position test across all 9 even-gap collage templates.")

    def _build_test_video_slide(self, style: str) -> Optional[Tuple[Any, List[Dict[str, Any]]]]:
        """Builds a test collage where every tile is a live video (4s per tile)."""
        cells = self._get_grid_template_cells(style)
        if not self._cached_test_videos:
            with self._sources_lock:
                vid_candidates = list(self.video_bases)
            random.shuffle(vid_candidates)
            cdn_params = f"=w{self.cdn_w}-h{self.cdn_h}"
            for base in vid_candidates:
                if len(self._cached_test_videos) >= 2:
                    break
                target = base if os.path.exists(base) else f"{base}{cdn_params}"
                item = self._download_media_item(target)
                if item and item[1]:
                    self._cached_test_videos.append((item[0], item[1]))

        if not self._cached_test_videos:
            return None

        group: List[Tuple[Any, Optional[str]]] = [
            self._cached_test_videos[i % len(self._cached_test_videos)] for i in range(len(cells))
        ]
        canvas = self._make_bokeh_canvas(group[0][0], brightness=0.32)
        for idx, (x, y, w, h) in enumerate(cells):
            self._paste_rounded_tile(canvas, group[idx][0], x, y, w, h, radius=10, border=True)

        logger.info("🧪 [TEST] Rendering %s with videos in ALL %d tiles.", style, len(cells))
        vids = self._extract_video_list_from_tiles(group, cells, inset_border=True, max_seconds_override=4.0)
        if not self._test_styles_queue:
            self._cached_test_videos.clear()
        return canvas, vids

    def _build_next_slide(self) -> Tuple[Optional[Any], List[Dict[str, Any]]]:
        """Returns `(composed_pil_image, list_of_video_infos)`."""
        while self._test_styles_queue:
            test_style = self._test_styles_queue.popleft()
            res = self._build_test_video_slide(test_style)
            if res:
                return res

        is_horizontal_canvas = self.target_w >= self.target_h

        if not is_horizontal_canvas:
            if self._portrait_queue and (len(self._portrait_queue) >= 3 or random.random() < 0.35):
                p_img, p_mp4 = self._portrait_queue.popleft()
                logger.info("Rendering full-screen portrait %s (%dx%d).", "video" if p_mp4 else "photo", p_img.width, p_img.height)
                out, rects = self._compose_single_photo(p_img)
                vids = self._extract_video_list_from_tiles([(p_img, p_mp4)], rects, inset_border=False)
                return out, vids

        first_item: Optional[Tuple[Any, Optional[str]]] = None
        if self._video_url_queue and self.video_mode == "play":
            vid_url = self._video_url_queue.popleft()
            first_item = self._download_media_item(vid_url)

        if not first_item and is_horizontal_canvas and len(self._portrait_queue) >= 3:
            first_item = self._portrait_queue.popleft()

        if not first_item and self._landscape_queue and random.random() < 0.50:
            first_item = self._landscape_queue.popleft()

        if not first_item:
            for _ in range(6):
                url = self._pop_next_source_url()
                if not url:
                    break
                first_item = self._download_media_item(url)
                if first_item:
                    break

        if not first_item:
            if self._portrait_queue:
                first_item = self._portrait_queue.popleft()
            elif self._landscape_queue:
                first_item = self._landscape_queue.popleft()
            else:
                return None, []

        first_img, first_mp4 = first_item
        is_land = self._is_landscape(first_img)

        if self.landscape_mode == "single":
            logger.info("Rendering single %s (%dx%d).", "video" if first_mp4 else "photo", first_img.width, first_img.height)
            out, rects = self._compose_single_photo(first_img)
            vids = self._extract_video_list_from_tiles([first_item], rects, inset_border=False)
            return out, vids

        if is_horizontal_canvas:
            if is_land:
                single_prob = 0.45 if first_mp4 is not None else 0.24
                if random.random() < single_prob:
                    logger.info("Rendering full-screen single landscape %s (%dx%d).", "video" if first_mp4 else "photo", first_img.width, first_img.height)
                    out, rects = self._compose_single_photo(first_img)
                    vids = self._extract_video_list_from_tiles([first_item], rects, inset_border=False)
                    return out, vids
            else:
                if random.random() < 0.08:
                    logger.info("Rendering full-screen portrait %s (%dx%d).", "video" if first_mp4 else "photo", first_img.width, first_img.height)
                    out, rects = self._compose_single_photo(first_img)
                    vids = self._extract_video_list_from_tiles([first_item], rects, inset_border=False)
                    return out, vids
        else:
            if not is_land:
                if first_mp4 is not None or random.random() < 0.70:
                    logger.info("Rendering full-screen portrait %s (%dx%d).", "video" if first_mp4 else "photo", first_img.width, first_img.height)
                    out, rects = self._compose_single_photo(first_img)
                    vids = self._extract_video_list_from_tiles([first_item], rects, inset_border=False)
                    return out, vids
            elif random.random() < 0.12:
                logger.info("Rendering full-screen single landscape %s (%dx%d).", "video" if first_mp4 else "photo", first_img.width, first_img.height)
                out, rects = self._compose_single_photo(first_img)
                vids = self._extract_video_list_from_tiles([first_item], rects, inset_border=False)
                return out, vids

        mode = self.landscape_mode
        if mode in ("collage", "random", "auto"):
            if not is_land:
                if is_horizontal_canvas:
                    style = random.choices(
                        [
                            "grid-trio",
                            "stack-2",
                            "grid-classic-5",
                            "grid-mosaic-7",
                            "grid-mondrian-8",
                            "grid-sidebar-9",
                        ],
                        weights=[26, 22, 22, 14, 10, 6],
                        k=1,
                    )[0]
                else:
                    style = random.choices(
                        [
                            "grid-trio",
                            "grid-classic-5",
                            "grid-mosaic-7",
                            "grid-mondrian-8",
                            "grid-3-2-3",
                        ],
                        weights=[32, 26, 16, 13, 13],
                        k=1,
                    )[0]
            else:
                style = random.choices(
                    [
                        "bento-4",
                        "grid-trio",
                        "stack-3",
                        "grid-classic-5",
                        "stack-2",
                        "grid-mosaic-7",
                        "grid-sidebar-9",
                        "grid-3-2-3",
                        "grid-mondrian-8",
                    ],
                    weights=[18, 16, 15, 14, 12, 9, 6, 5, 5],
                    k=1,
                )[0]
        elif mode == "stack":
            style = "stack-2"
        elif mode == "triple":
            style = "stack-3"
        elif mode == "bento":
            style = "bento-4"
        elif mode == "trio":
            style = "grid-trio"
        elif mode == "classic-5":
            style = "grid-classic-5"
        elif mode == "mosaic-7":
            style = "grid-mosaic-7"
        elif mode == "sidebar-9":
            style = "grid-sidebar-9"
        elif mode == "mondrian-8":
            style = "grid-mondrian-8"
        elif mode == "grid-3-2-3":
            style = "grid-3-2-3"
        else:
            style = "stack-2"

        return self._compose_grid_collage(style, first_item=first_item)

    def _create_pygame_surface(self, pil_img: Any) -> Optional[Any]:
        if not PYGAME_AVAILABLE or not pil_img:
            return None
        try:
            surf = pygame.image.fromstring(pil_img.tobytes(), pil_img.size, pil_img.mode)
            if pygame.display.get_surface() and threading.current_thread() is threading.main_thread():
                return surf.convert()
            return surf
        except Exception as e:
            logger.error("Surface creation error: %s", e)
            return None

    def trigger_prefetch_next(self):
        if self._is_prefetching or self.prefetched_surface is not None:
            return

        def prefetch_worker():
            with self._prefetch_lock:
                if self.prefetched_surface is not None:
                    return
                self._is_prefetching = True
                try:
                    pil_img, vid_list = self._build_next_slide()
                    if pil_img:
                        surf = self._create_pygame_surface(pil_img)
                        del pil_img
                        if surf:
                            self.prefetched_video_list = vid_list
                            self.prefetched_surface = surf
                    gc.collect()
                finally:
                    self._is_prefetching = False

        threading.Thread(target=prefetch_worker, daemon=True).start()

    def get_initial_slide(self) -> Tuple[Optional[Any], List[Dict[str, Any]]]:
        pil_img, vid_list = self._build_next_slide()
        if pil_img:
            surf = self._create_pygame_surface(pil_img)
            del pil_img
            self.current_surface = surf
            self.current_video_list = vid_list
            self.trigger_prefetch_next()
            return self.current_surface, self.current_video_list
        return None, []

    def advance_slide(self) -> Tuple[Optional[Any], List[Dict[str, Any]]]:
        if self.gphotos_url and self._resync_interval > 0:
            if time.time() - self._last_resync_time > self._resync_interval and not self._is_syncing_album:
                self._start_background_sync(self.gphotos_url)

        new_surf = None
        new_vids: List[Dict[str, Any]] = []
        with self._prefetch_lock:
            if self.prefetched_surface is not None and not self._test_styles_queue:
                new_surf = self.prefetched_surface
                new_vids = self.prefetched_video_list
                self.prefetched_surface = None
                self.prefetched_video_list = []
            else:
                self.prefetched_surface = None
                self.prefetched_video_list = []
                pil_img, new_vids = self._build_next_slide()
                new_surf = self._create_pygame_surface(pil_img)
                del pil_img

        if new_surf:
            if PYGAME_AVAILABLE and pygame.display.get_surface() and threading.current_thread() is threading.main_thread():
                try:
                    new_surf = new_surf.convert()
                except Exception:
                    pass
            if self.current_surface is not None:
                self._slide_history.append((self.current_surface, self.current_video_list))
            self.current_surface = new_surf
            self.current_video_list = new_vids
            gc.collect()
            self.trigger_prefetch_next()
            return self.current_surface, self.current_video_list

        self.trigger_prefetch_next()
        return self.current_surface, self.current_video_list

    def previous_slide(self) -> Tuple[Optional[Any], List[Dict[str, Any]]]:
        if self._slide_history:
            prev_surf, prev_vids = self._slide_history.pop()
            self.current_surface = prev_surf
            self.current_video_list = prev_vids
            return self.current_surface, self.current_video_list
        return self.current_surface, self.current_video_list
