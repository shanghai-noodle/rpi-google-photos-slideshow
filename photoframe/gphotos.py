"""
Google Photos Shared Album Scraper, RPC Paginator & Local/Demo Media Loader
===========================================================================
Handles Google Photos shared link normalization, `AF_initDataCallback` + `batchexecute`
full-album pagination, RAM cache persistence (`/dev/shm/gphotos_cache.json`), and
built-in Demo Showcase (`demo_media/`) discovery.
"""

from __future__ import annotations

import glob
import io
import json
import os
import random
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, List, Optional, Set, Tuple

try:
    from PIL import Image, ImageOps
    PIL_AVAILABLE = True
except ImportError:
    Image = ImageOps = None
    PIL_AVAILABLE = False

from .hardware import RAM_DIR, logger

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GooglePhotosMixin:
    """Mixin providing Google Photos scraping, RAM caching, and media download methods."""

    VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")
    DEFAULT_DEMO_DIR = os.path.join(PROJECT_ROOT, "demo_media")
    CURATED_DEMO_URLS = (
        # 12 Portrait Showcase Photos
        "https://images.unsplash.com/photo-1432405972618-c60b0225b8f9?auto=format&fit=crop&w=720&h=1080&q=82",
        "https://images.unsplash.com/photo-1531366936337-7c912a4589a7?auto=format&fit=crop&w=720&h=1080&q=82",
        "https://images.unsplash.com/photo-1441974231531-c6227db76b6e?auto=format&fit=crop&w=720&h=1080&q=82",
        "https://images.unsplash.com/photo-1493976040374-85c8e12f0c0e?auto=format&fit=crop&w=720&h=1080&q=82",
        "https://images.unsplash.com/photo-1570077188670-e3a8d69ac5ff?auto=format&fit=crop&w=720&h=1080&q=82",
        "https://images.unsplash.com/photo-1506744038136-46273834b3fb?auto=format&fit=crop&w=720&h=1080&q=82",
        "https://images.unsplash.com/photo-1552053831-71594a27632d?auto=format&fit=crop&w=720&h=1080&q=82",
        "https://images.unsplash.com/photo-1514888286974-6c03e2ca1dba?auto=format&fit=crop&w=720&h=1080&q=82",
        "https://images.unsplash.com/photo-1540573133985-87b6da6d54a9?auto=format&fit=crop&w=720&h=1080&q=82",
        "https://images.unsplash.com/photo-1509042239860-f550ce710b93?auto=format&fit=crop&w=720&h=1080&q=82",
        "https://images.unsplash.com/photo-1528207776546-365bb710ee93?auto=format&fit=crop&w=720&h=1080&q=82",
        "https://images.unsplash.com/photo-1513558161293-cdaf765ed2fd?auto=format&fit=crop&w=720&h=1080&q=82",
        # 12 Landscape Showcase Photos
        "https://images.unsplash.com/photo-1464822759023-fed622ff2c3b?auto=format&fit=crop&w=1080&h=720&q=82",
        "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?auto=format&fit=crop&w=1080&h=720&q=82",
        "https://images.unsplash.com/photo-1499002238440-d264edd596ec?auto=format&fit=crop&w=1080&h=720&q=82",
        "https://images.unsplash.com/photo-1514890547357-a9ee288728e0?auto=format&fit=crop&w=1080&h=720&q=82",
        "https://images.unsplash.com/photo-1533105079780-92b9be482077?auto=format&fit=crop&w=1080&h=720&q=82",
        "https://images.unsplash.com/photo-1507608616759-54f48f0af0ee?auto=format&fit=crop&w=1080&h=720&q=82",
        "https://images.unsplash.com/photo-1612536057832-2ff7ead58194?auto=format&fit=crop&w=1080&h=720&q=82",
        "https://images.unsplash.com/photo-1573865526739-10659fec78a5?auto=format&fit=crop&w=1080&h=720&q=82",
        "https://images.unsplash.com/photo-1544551763-46a013bb70d5?auto=format&fit=crop&w=1080&h=720&q=82",
        "https://images.unsplash.com/photo-1569718212165-3a8278d5f624?auto=format&fit=crop&w=1080&h=720&q=82",
        "https://images.unsplash.com/photo-1555507036-ab1f4038808a?auto=format&fit=crop&w=1080&h=720&q=82",
        "https://images.unsplash.com/photo-1579871494447-9811cf80d66c?auto=format&fit=crop&w=1080&h=720&q=82",
    )

    @staticmethod
    def normalize_gphotos_url(album_input: str) -> str:
        if not album_input:
            return ""
        s = album_input.strip()
        if not s or s.lower() in ("demo", "none", "default"):
            return ""
        if s.startswith("http://") or s.startswith("https://"):
            return s
        if s.startswith("share/"):
            return f"https://photos.google.com/{s}"
        return f"https://photos.app.goo.gl/{s}"

    @property
    def is_demo_mode(self) -> bool:
        return not bool(self.gphotos_url)

    @staticmethod
    def _extract_item_url_and_is_video(it: Any) -> Tuple[Optional[str], bool]:
        """Extracts `(base_url, is_video)` from a Google Photos RPC item array."""
        if not isinstance(it, list) or len(it) <= 1 or not isinstance(it[1], list) or not it[1]:
            return None, False
        u = it[1][0]
        if not isinstance(u, str) or not u.startswith("http"):
            return None, False
        base = u.split("=")[0]
        is_vid = any(isinstance(el, dict) and "76647426" in el for el in it)
        return base, is_vid

    def is_video_source(self, source: str) -> bool:
        if not source:
            return False
        low = source.lower()
        if any(low.endswith(ext) for ext in self.VIDEO_EXTENSIONS):
            return True
        base = source.split("=")[0]
        with self._sources_lock:
            return base in self.video_bases

    def _load_cached_photos(self) -> Tuple[List[str], float, bool]:
        """Returns `(formatted_urls, updated_at, has_video_metadata)`."""
        candidates = [self.cache_file]
        seed_cache = os.path.join(PROJECT_ROOT, ".gphotos_cache.json")
        if seed_cache not in candidates:
            candidates.append(seed_cache)

        for path in candidates:
            if not path or not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cached_album = data.get("album_url")
                if cached_album != self.gphotos_url:
                    continue
                raw_urls = data.get("urls", [])
                has_videos_key = "videos" in data
                raw_videos = data.get("videos", []) if has_videos_key else []
                updated_at = float(data.get("updated_at", 0.0))
                if raw_urls:
                    with self._sources_lock:
                        self.video_bases = set(
                            v.split("=")[0] for v in raw_videos if isinstance(v, str) and v.startswith("http")
                        )
                    cdn_params = f"=w{self.cdn_w}-h{self.cdn_h}"
                    formatted = [
                        f"{u.split('=')[0]}{cdn_params}"
                        for u in raw_urls
                        if isinstance(u, str) and u.startswith("http")
                    ]
                    logger.info(
                        "Loaded %d items (%d videos) from cache (%s, CDN size: %dx%d)",
                        len(formatted), len(self.video_bases), path, self.cdn_w, self.cdn_h
                    )
                    if path != self.cache_file:
                        self._save_cached_photos(raw_urls, list(self.video_bases))
                    return formatted, updated_at, has_videos_key
            except Exception as e:
                logger.warning("Failed to read photo cache (%s): %s", path, e)
        return [], 0.0, False

    def _save_cached_photos(self, base_urls: List[str], video_urls: Optional[List[str]] = None):
        if not base_urls or not self.cache_file:
            return
        try:
            clean_urls = list(dict.fromkeys([
                u.split("=")[0] for u in base_urls if isinstance(u, str) and u.startswith("http")
            ]))
            if video_urls is None:
                with self._sources_lock:
                    clean_videos = sorted(list(self.video_bases))
            else:
                clean_videos = sorted(list(set(
                    v.split("=")[0] for v in video_urls if isinstance(v, str) and v.startswith("http")
                )))
            payload = {
                "album_url": self.gphotos_url,
                "urls": clean_urls,
                "videos": clean_videos,
                "count": len(clean_urls),
                "video_count": len(clean_videos),
                "updated_at": time.time(),
            }
            tmp_path = f"{self.cache_file}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp_path, self.cache_file)
            logger.info(
                "Cached %d Google Photos URLs (%d videos) in RAM (%s)",
                len(clean_urls), len(clean_videos), self.cache_file
            )
        except Exception as e:
            logger.warning("Failed to save RAM photo cache: %s", e)

    def _parse_initial_page(self, html: str, final_url: str = "") -> Tuple[List[str], Set[str], Optional[str], Optional[str], Optional[str], dict]:
        wiz_m = re.search(r'window\.WIZ_global_data\s*=\s*(\{.*?\});', html)
        wiz_data = {}
        if wiz_m:
            try:
                wiz_data = json.loads(wiz_m.group(1))
            except Exception:
                pass

        cb_m = re.search(
            r"AF_initDataCallback\([^)]*?key:\s*'ds:1'[^)]*?data:\s*(?:function\(\)\{return\s+)?(\[.*?\])\s*(?:\}\(\))?\s*,\s*sideChannel:",
            html,
            re.DOTALL,
        )

        photos: List[str] = []
        videos: Set[str] = set()
        page_token = None
        album_key = None

        if cb_m:
            try:
                ds1 = json.loads(cb_m.group(1))
                items = ds1[1] if len(ds1) > 1 and isinstance(ds1[1], list) else []
                page_token = ds1[2] if len(ds1) > 2 and isinstance(ds1[2], str) else None
                album_info = ds1[3] if len(ds1) > 3 and isinstance(ds1[3], list) else []
                album_key = album_info[0] if album_info else None
                for it in items:
                    base, is_vid = self._extract_item_url_and_is_video(it)
                    if base:
                        photos.append(base)
                        if is_vid:
                            videos.add(base)
            except Exception as e:
                logger.debug("Failed parsing structured ds:1 callback: %s", e)

        if not photos:
            pattern = r'(https:\/\/[a-z0-9\-_.]+\.googleusercontent\.com\/(?:pw\/|drive-viewer\/)?[a-zA-Z0-9\-_]{20,})'
            matches = re.findall(pattern, html)
            unique_urls = list(dict.fromkeys(matches))
            photos = [
                u for u in unique_urls
                if "/a/" not in u and "photo.jpg" not in u and "default-user" not in u
            ]

        auth_key = None
        if isinstance(final_url, str) and final_url:
            auth_m = re.search(r"[?&]key=([^&#]+)", final_url)
            if auth_m:
                auth_key = auth_m.group(1)
        if not auth_key:
            auth_req_m = re.search(r'request:\s*\[\"[^\"]+\",\s*\"([^\"]+)\"\]', html)
            if auth_req_m:
                auth_key = auth_req_m.group(1)

        return photos, videos, page_token, album_key, auth_key, wiz_data

    def fetch_google_photos(self, url: str) -> List[str]:
        if not url:
            return []

        cached, updated_at, has_video_metadata = self._load_cached_photos()
        if cached:
            if self.shuffle and len(cached) > 1:
                random.shuffle(cached)
            with self._sources_lock:
                self.photo_sources = list(cached)
                self.current_index = 0
            now = time.time()
            if not has_video_metadata or (self._resync_interval > 0 and (now - updated_at > self._resync_interval)):
                logger.info("Starting background album sync (video metadata indexed: %s)...", has_video_metadata)
                self._start_background_sync(url)
            return cached

        logger.info("Discovering Google Photos in album: %s", url)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=15) as res:
                final_url = getattr(res, "geturl", lambda: "")() or ""
                html = res.read().decode("utf-8", errors="ignore")

            page1_photos, page1_videos, next_token, album_key, auth_key, wiz_data = self._parse_initial_page(html, final_url)
            cdn_params = f"=w{self.cdn_w}-h{self.cdn_h}"
            result_urls = [f"{base.split('=')[0]}{cdn_params}" for base in page1_photos]
            shuffled_page1 = list(result_urls)
            if self.shuffle and len(shuffled_page1) > 1:
                random.shuffle(shuffled_page1)

            with self._sources_lock:
                self.video_bases = set(page1_videos)
                self.photo_sources = shuffled_page1
                self.current_index = 0

            logger.info("Found %d items (%d videos) on page 1.", len(result_urls), len(page1_videos))

            if next_token and album_key:
                self._start_background_sync(
                    url,
                    initial_token=next_token,
                    album_key=album_key,
                    auth_key=auth_key,
                    wiz_data=wiz_data,
                    initial_photos=result_urls,
                    initial_videos=page1_videos,
                )
            else:
                self._save_cached_photos(result_urls, list(page1_videos))

            return shuffled_page1
        except Exception as e:
            logger.error("Failed to fetch Google Photos album (%s): %s", url, e)
            return []

    def _start_background_sync(
        self,
        url: str,
        initial_token: Optional[str] = None,
        album_key: Optional[str] = None,
        auth_key: Optional[str] = None,
        wiz_data: Optional[dict] = None,
        initial_photos: Optional[List[str]] = None,
        initial_videos: Optional[Set[str]] = None,
        force: bool = False,
    ):
        if self._is_syncing_album and not force:
            return

        gen = self._album_generation

        def worker():
            try:
                self._is_syncing_album = True
                self._sync_album_in_background(
                    url,
                    initial_token=initial_token,
                    album_key=album_key,
                    auth_key=auth_key,
                    wiz_data=wiz_data,
                    initial_photos=initial_photos,
                    initial_videos=initial_videos,
                    expected_gen=gen,
                )
            finally:
                if self._album_generation == gen:
                    self._is_syncing_album = False
                    self._last_resync_time = time.time()

        threading.Thread(target=worker, name="GPhotosSyncThread", daemon=True).start()

    def _sync_album_in_background(
        self,
        url: str,
        initial_token: Optional[str] = None,
        album_key: Optional[str] = None,
        auth_key: Optional[str] = None,
        wiz_data: Optional[dict] = None,
        initial_photos: Optional[List[str]] = None,
        initial_videos: Optional[Set[str]] = None,
        expected_gen: Optional[int] = None,
    ):
        if expected_gen is not None and self._album_generation != expected_gen:
            return
        logger.info("Starting background Google Photos full album pagination...")
        discovered_urls: List[str] = list(initial_photos or [])
        discovered_videos: Set[str] = set(initial_videos or set())
        token = initial_token
        a_key = album_key
        auth = auth_key
        wiz = wiz_data or {}

        if not token or not a_key:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
                with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=15) as res:
                    final_url = res.geturl()
                    html = res.read().decode("utf-8", errors="ignore")
                if expected_gen is not None and self._album_generation != expected_gen:
                    return
                p1, v1, token, a_key, auth, wiz = self._parse_initial_page(html, final_url)
                cdn_params = f"=w{self.cdn_w}-h{self.cdn_h}"
                discovered_urls = [f"{u.split('=')[0]}{cdn_params}" for u in p1]
                discovered_videos.update(v1)
                with self._sources_lock:
                    self.video_bases.update(v1)
            except Exception as e:
                logger.error("Failed to fetch album root during background sync: %s", e)
                return

        f_sid = wiz.get("FdrFJe", "")
        bl = wiz.get("cfb2h", "")
        at = wiz.get("SNlM0e", "")
        cdn_params = f"=w{self.cdn_w}-h{self.cdn_h}"

        with self._sources_lock:
            existing_bases = set(u.split("=")[0] for u in self.photo_sources)
            if not discovered_urls and self.photo_sources:
                discovered_urls = list(self.photo_sources)

        seen_bases = set(u.split("=")[0] for u in discovered_urls) | existing_bases
        page_num = 1
        max_pages = 100

        while token and page_num < max_pages:
            if expected_gen is not None and self._album_generation != expected_gen:
                logger.info("Aborting background sync for superseded album generation.")
                return
            page_num += 1
            try:
                rpc_payload = json.dumps([a_key, token, None, auth])
                envelope = json.dumps([[["snAcKc", rpc_payload, None, "generic"]]])
                post_data = urllib.parse.urlencode({"f.req": envelope, "at": at}).encode("utf-8")

                query_params = {
                    "rpcids": "snAcKc",
                    "_reqid": str(int(time.time() * 1000) % 1000000),
                    "bl": bl,
                    "soc-app": "1",
                    "soc-platform": "1",
                    "soc-device": "1",
                    "rt": "c",
                }
                if f_sid:
                    query_params["f.sid"] = f_sid

                post_url = "https://photos.google.com/_/PhotosUi/data/batchexecute?" + urllib.parse.urlencode(query_params)
                req = urllib.request.Request(
                    post_url,
                    data=post_data,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                        "User-Agent": "curl/7.68.0",
                        "Referer": "https://photos.google.com/",
                    },
                )

                with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=15) as res:
                    body = res.read().decode("utf-8", errors="ignore")

                if expected_gen is not None and self._album_generation != expected_gen:
                    return

                new_token = None
                batch_urls = []
                batch_videos = set()
                page_item_count = 0
                for line in body.splitlines():
                    line = line.strip()
                    if line.startswith('[["wrb.fr"'):
                        parsed_line = json.loads(line)
                        inner_payload = json.loads(parsed_line[0][2])
                        items = inner_payload[1] if len(inner_payload) > 1 and isinstance(inner_payload[1], list) else []
                        new_token = inner_payload[2] if len(inner_payload) > 2 and isinstance(inner_payload[2], str) else None
                        for it in items:
                            base, is_vid = self._extract_item_url_and_is_video(it)
                            if base:
                                page_item_count += 1
                                if is_vid:
                                    batch_videos.add(base)
                                if base not in seen_bases:
                                    seen_bases.add(base)
                                    batch_urls.append(f"{base}{cdn_params}")
                        break

                if batch_videos:
                    discovered_videos.update(batch_videos)
                    with self._sources_lock:
                        self.video_bases.update(batch_videos)

                if batch_urls:
                    with self._sources_lock:
                        if self.shuffle:
                            for b_url in batch_urls:
                                insert_pos = (
                                    random.randint(self.current_index + 1, len(self.photo_sources))
                                    if len(self.photo_sources) > self.current_index + 1
                                    else len(self.photo_sources)
                                )
                                self.photo_sources.insert(insert_pos, b_url)
                        else:
                            self.photo_sources.extend(batch_urls)
                    discovered_urls.extend(batch_urls)

                if not new_token or page_item_count == 0:
                    break
                token = new_token
                time.sleep(self.sync_sleep_sec)
            except Exception as e:
                logger.warning("Error fetching album page %d: %s", page_num, e)
                break

        if expected_gen is not None and self._album_generation != expected_gen:
            return

        with self._sources_lock:
            all_urls = list(self.photo_sources) if len(self.photo_sources) >= len(discovered_urls) else discovered_urls
            all_vids = list(self.video_bases | discovered_videos)

        logger.info(
            "Google Photos album sync complete: %d total items (%d videos).",
            len(all_urls), len(all_vids)
        )
        self._save_cached_photos(all_urls, all_vids)

    def scan_local_directory(self, directory: Optional[str] = None) -> List[str]:
        target_dir = directory or self.local_dir
        if not target_dir or not os.path.exists(target_dir):
            return []
        valid_exts = ("*.jpg", "*.jpeg", "*.png", "*.webp", "*.bmp", "*.mp4", "*.mov")
        files = []
        for ext in valid_exts:
            files.extend(glob.glob(os.path.join(target_dir, ext)))
            files.extend(glob.glob(os.path.join(target_dir, ext.upper())))
        cleaned = []
        local_vids = set()
        for fpath in sorted(list(set(files))):
            low = fpath.lower()
            if any(low.endswith(f"{vext}.jpg") for vext in self.VIDEO_EXTENSIONS):
                continue
            cleaned.append(fpath)
            if any(low.endswith(vext) for vext in self.VIDEO_EXTENSIONS):
                local_vids.add(fpath)
        if local_vids:
            with self._sources_lock:
                self.video_bases.update(local_vids)
        return cleaned

    def refresh_sources(self):
        sources: List[str] = []
        if self.gphotos_url:
            sources = self.fetch_google_photos(self.gphotos_url)
        if not sources:
            with self._sources_lock:
                self.video_bases = set()
            if self.local_dir and os.path.exists(self.local_dir):
                sources = self.scan_local_directory(self.local_dir)
            if not sources and os.path.exists(self.DEFAULT_DEMO_DIR):
                sources = self.scan_local_directory(self.DEFAULT_DEMO_DIR)
            if not sources:
                sources = list(self.CURATED_DEMO_URLS)
            if self.shuffle and len(sources) > 1:
                random.shuffle(sources)
            with self._sources_lock:
                self.photo_sources = sources
                self.current_index = 0
        mode_str = "DEMO SHOWCASE MODE" if self.is_demo_mode else "Google Photos Album"
        logger.info(
            "Initialized slideshow [%s] with %d item(s) (%d known videos, video mode: %s).",
            mode_str, len(self.photo_sources), len(self.video_bases), self.video_mode
        )

    def switch_album(self, new_album_url: str, on_ready_callback: Optional[Any] = None):
        """
        Atomically switches to a new Google Photos shared album (or Demo Showcase Mode
        when `new_album_url` is empty or 'demo'), flushes all prefetched queues and stale
        caches, and invokes `on_ready_callback` as soon as the new slide deck is ready.
        """
        normalized = self.normalize_gphotos_url(new_album_url)

        self._album_generation += 1
        self._is_syncing_album = False
        self.gphotos_url = normalized

        if self.cache_file and os.path.exists(self.cache_file):
            try:
                os.remove(self.cache_file)
            except Exception:
                pass

        with self._sources_lock:
            self.photo_sources = []
            self.video_bases = set()
            self.current_index = 0

        self._portrait_queue.clear()
        self._landscape_queue.clear()
        self._video_url_queue.clear()
        self._slide_history.clear()
        with self._prefetch_lock:
            self.prefetched_surface = None
            self.prefetched_video_list = []

        if self.is_demo_mode:
            logger.info("✨ Switching slideshow to built-in Demo Showcase Mode...")
        else:
            logger.info("🔄 Switching Google Photos album to: %s", self.gphotos_url)
        self.refresh_sources()

        self._portrait_queue.clear()
        self._landscape_queue.clear()
        with self._prefetch_lock:
            self.prefetched_surface = None
            self.prefetched_video_list = []

        if callable(on_ready_callback) and self.photo_sources:
            on_ready_callback()

    def _pop_next_source_url(self) -> Optional[str]:
        with self._sources_lock:
            if not self.photo_sources:
                return None
            url = self.photo_sources[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.photo_sources)
            return url

    def _get_fallback_test_photo_path(self, failed_source: str = "") -> Optional[str]:
        """
        Selects a local synthetic test photo from `demo_media/` matching the orientation
        of `failed_source` (portrait vs landscape) when a remote demo/Unsplash URL fails,
        and replaces the broken URL in `self.photo_sources` so it won't stall future slides.
        """
        if not os.path.isdir(self.DEFAULT_DEMO_DIR):
            return None
        try:
            all_jpgs = sorted(
                os.path.join(self.DEFAULT_DEMO_DIR, f)
                for f in os.listdir(self.DEFAULT_DEMO_DIR)
                if f.lower().endswith((".jpg", ".jpeg", ".png")) and not f.lower().endswith(".mp4.jpg")
            )
        except Exception:
            return None
        if not all_jpgs:
            return None

        portraits = [p for p in all_jpgs if os.path.basename(p).lower().startswith("p")]
        landscapes = [p for p in all_jpgs if os.path.basename(p).lower().startswith("l")]

        low_src = (failed_source or "").lower()
        want_portrait = ("h=1080" in low_src and "w=720" in low_src) or ("portrait" in low_src)
        want_landscape = ("w=1080" in low_src and "h=720" in low_src) or ("landscape" in low_src)

        if want_portrait and portraits:
            chosen = random.choice(portraits)
        elif want_landscape and landscapes:
            chosen = random.choice(landscapes)
        else:
            chosen = random.choice(all_jpgs)

        if failed_source and ("unsplash.com" in low_src or self.is_demo_mode):
            with self._sources_lock:
                self.photo_sources = [chosen if s == failed_source else s for s in self.photo_sources]

        return chosen

    def _download_raw_pil(self, source: str) -> Optional[Any]:
        """Downloads and EXIF-transposes a raw PIL Image in RAM without cropping, with automatic local test photo fallback."""
        if not PIL_AVAILABLE or not Image:
            return None
        try:
            if os.path.exists(source):
                if any(source.lower().endswith(ext) for ext in self.VIDEO_EXTENSIONS):
                    poster_path = f"{source}.jpg"
                    if os.path.exists(poster_path):
                        img = Image.open(poster_path)
                    else:
                        res = subprocess.run(
                            ["ffmpeg", "-i", source, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL,
                            timeout=5,
                            check=True,
                        )
                        img = Image.open(io.BytesIO(res.stdout))
                else:
                    img = Image.open(source)
            else:
                req = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0 (X11; Linux)"})
                with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=self.http_timeout) as res:
                    data = res.read()
                img = Image.open(io.BytesIO(data))

            if ImageOps:
                try:
                    img = ImageOps.exif_transpose(img)
                except Exception:
                    pass
            return img.convert("RGB")
        except Exception as e:
            logger.warning("Failed to download photo (%s...): %s", source[:60], e)
            if not os.path.exists(source):
                fallback_path = self._get_fallback_test_photo_path(source)
                if fallback_path and os.path.exists(fallback_path):
                    logger.info("Using local synthetic test photo fallback: %s", os.path.basename(fallback_path))
                    try:
                        return Image.open(fallback_path).convert("RGB")
                    except Exception:
                        pass
            return None

    def _download_video_to_ram(self, source: str) -> Optional[str]:
        """
        Downloads a Google Photos transcoded video (=m18 MP4) into /dev/shm (RAM tmpfs)
        so FFmpeg can play it with zero network jitter and zero SD card writes.
        """
        if os.path.exists(source):
            return source

        base = source.split("=")[0]
        stream_url = f"{base}=m18"
        slot = self._video_ram_slot
        self._video_ram_slot = (self._video_ram_slot + 1) % 6
        ram_mp4_path = os.path.join(RAM_DIR, f"slide_vid_{slot}.mp4")

        try:
            req = urllib.request.Request(stream_url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux)"})
            max_bytes = 20 * 1024 * 1024
            total = 0
            tmp_path = f"{ram_mp4_path}.part"
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=max(12.0, self.http_timeout * 1.5)) as res:
                ctype = (res.headers.get("Content-Type") or "").lower()
                if "video" not in ctype and "octet-stream" not in ctype:
                    logger.warning("URL did not return video stream (%s)", ctype)
                    return None
                with open(tmp_path, "wb") as f:
                    while True:
                        chunk = res.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            break
                        f.write(chunk)
            if total < 4096:
                return None
            os.replace(tmp_path, ram_mp4_path)
            logger.info("Prefetched video into RAM: %s (%.2f MB)", ram_mp4_path, total / (1024 * 1024))
            return ram_mp4_path
        except Exception as e:
            logger.warning("Failed to prefetch video stream (%s...): %s", base[:55], e)
            return None
