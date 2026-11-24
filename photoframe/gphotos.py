"""
Google Photos Shared Album Scraper, RPC Paginator & Local/Demo Media Loader
===========================================================================
Handles Google Photos shared link normalization, `AF_initDataCallback` + `batchexecute`
full-album pagination, RAM cache persistence (`/dev/shm/gphotos_cache.json`), and
built-in Demo Showcase (`demo_media/`) discovery.
"""

from __future__ import annotations

import glob
import html as html_lib
import io
import json
import math
import os
import random
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

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
    RECENT_MAX_ITEMS = 40
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
    def _extract_item_metadata(it: Any) -> Tuple[Optional[str], bool, int]:
        """
        Extracts `(base_url, is_video, added_ts_ms)` from a Google Photos RPC item array.
        Prefers `it[5]` (added-to-album / upload epoch ms), falling back to `it[2]` (capture ms).
        """
        if not isinstance(it, list) or len(it) <= 1 or not isinstance(it[1], list) or not it[1]:
            return None, False, 0
        u = it[1][0]
        if not isinstance(u, str) or not u.startswith("http"):
            return None, False, 0
        base = u.split("=")[0]
        is_vid = any(isinstance(el, dict) and "76647426" in el for el in it)
        added_ts = 0
        for idx in (5, 2):
            if len(it) > idx:
                val = it[idx]
                if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
                    added_ts = int(val)
                    break
        return base, is_vid, added_ts

    @classmethod
    def _extract_item_url_and_is_video(cls, it: Any) -> Tuple[Optional[str], bool]:
        """Extracts `(base_url, is_video)` from a Google Photos RPC item array."""
        base, is_vid, _ = cls._extract_item_metadata(it)
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

    @staticmethod
    def extract_gphotos_album_title(page_html: str) -> str:
        if not page_html:
            return ""
        og_m = re.search(
            r'<meta\s+[^>]*property=["\']og:title["\'][^>]*content=["\'](.*?)["\']',
            page_html,
            re.IGNORECASE | re.DOTALL,
        ) or re.search(
            r'<meta\s+[^>]*content=["\'](.*?)["\'][^>]*property=["\']og:title["\']',
            page_html,
            re.IGNORECASE | re.DOTALL,
        )
        raw = ""
        if og_m:
            raw = html_lib.unescape(og_m.group(1)).strip()
            raw = re.sub(r"\s*·\s*.*$", "", raw).strip()
        if not raw:
            t_m = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.IGNORECASE | re.DOTALL)
            if t_m:
                raw = html_lib.unescape(t_m.group(1)).strip()
                raw = re.sub(r"\s*-\s*Google\s+Photos\s*$", "", raw, flags=re.IGNORECASE).strip()
        low = raw.lower()
        if low in ("google photos", "shared album", "") or "sign in" in low or "google accounts" in low:
            return ""
        return raw

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
                raw_recent = data.get("recent_items", [])
                updated_at = float(data.get("updated_at", 0.0))
                cached_title = str(data.get("album_title") or "").strip()
                if cached_title:
                    self.album_title = cached_title
                if raw_urls:
                    ts_map: Dict[str, int] = {}
                    if isinstance(raw_recent, list):
                        for entry in raw_recent:
                            if (
                                isinstance(entry, (list, tuple))
                                and len(entry) >= 2
                                and isinstance(entry[0], str)
                                and entry[0].startswith("http")
                            ):
                                try:
                                    ts_map[entry[0].split("=")[0]] = int(entry[1])
                                except (TypeError, ValueError):
                                    pass
                    with self._sources_lock:
                        self.video_bases = set(
                            v.split("=")[0] for v in raw_videos if isinstance(v, str) and v.startswith("http")
                        )
                        if ts_map:
                            self._item_added_ts.update(ts_map)
                    cdn_params = f"=w{self.cdn_w}-h{self.cdn_h}"
                    formatted = [
                        f"{u.split('=')[0]}{cdn_params}"
                        for u in raw_urls
                        if isinstance(u, str) and u.startswith("http")
                    ]
                    if ts_map:
                        self._rebuild_recent_sources(ordered_fallback=formatted)
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
            with self._sources_lock:
                recent_pairs = sorted(
                    ((u, int(ts)) for u, ts in self._item_added_ts.items() if u.startswith("http") and ts > 0),
                    key=lambda kv: kv[1],
                    reverse=True,
                )[:60]
            payload = {
                "album_url": self.gphotos_url,
                "album_title": getattr(self, "album_title", "") or "",
                "urls": clean_urls,
                "videos": clean_videos,
                "recent_items": [[u, ts] for u, ts in recent_pairs],
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

    def _parse_initial_page_with_timestamps(
        self, html: str, final_url: str = ""
    ) -> Tuple[List[str], Set[str], Optional[str], Optional[str], Optional[str], dict, Dict[str, int]]:
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
        timestamps: Dict[str, int] = {}
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
                    base, is_vid, added_ts = self._extract_item_metadata(it)
                    if base:
                        photos.append(base)
                        if is_vid:
                            videos.add(base)
                        if added_ts > timestamps.get(base, 0):
                            timestamps[base] = added_ts
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

        extracted_title = self.extract_gphotos_album_title(html)
        if extracted_title:
            self.album_title = extracted_title
        return photos, videos, page_token, album_key, auth_key, wiz_data, timestamps

    def _parse_initial_page(self, html: str, final_url: str = "") -> Tuple[List[str], Set[str], Optional[str], Optional[str], Optional[str], dict]:
        photos, videos, page_token, album_key, auth_key, wiz_data, _ = self._parse_initial_page_with_timestamps(
            html, final_url
        )
        return photos, videos, page_token, album_key, auth_key, wiz_data

    @classmethod
    def _parse_rpc_page_items(cls, body: str) -> Tuple[Optional[str], List[Tuple[str, bool, int]]]:
        """Parses a Google Photos `batchexecute` `snAcKc` response into `(next_token, [(base_url, is_vid, added_ts)])`."""
        new_token = None
        parsed_items: List[Tuple[str, bool, int]] = []
        for line in (body or "").splitlines():
            line = line.strip()
            if line.startswith('[["wrb.fr"'):
                parsed_line = json.loads(line)
                inner_payload = json.loads(parsed_line[0][2])
                items = inner_payload[1] if len(inner_payload) > 1 and isinstance(inner_payload[1], list) else []
                new_token = inner_payload[2] if len(inner_payload) > 2 and isinstance(inner_payload[2], str) else None
                for it in items:
                    base, is_vid, added_ts = cls._extract_item_metadata(it)
                    if base:
                        parsed_items.append((base, is_vid, added_ts))
                break
        return new_token, parsed_items

    def _fetch_rpc_page(
        self,
        album_key: str,
        token: str,
        auth_key: Optional[str],
        wiz_data: Optional[dict],
    ) -> Tuple[Optional[str], List[Tuple[str, bool, int]]]:
        wiz = wiz_data or {}
        f_sid = wiz.get("FdrFJe", "")
        bl = wiz.get("cfb2h", "")
        at = wiz.get("SNlM0e", "")
        rpc_payload = json.dumps([album_key, token, None, auth_key])
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
        return self._parse_rpc_page_items(body)

    def _rebuild_recent_sources(
        self,
        ts_map: Optional[Dict[str, int]] = None,
        ordered_fallback: Optional[List[str]] = None,
        max_items: Optional[int] = None,
    ) -> List[str]:
        limit = max(1, int(max_items or self.RECENT_MAX_ITEMS))
        cdn_params = f"=w{self.cdn_w}-h{self.cdn_h}"
        with self._sources_lock:
            if ts_map:
                for base_u, ts in ts_map.items():
                    if base_u and ts > self._item_added_ts.get(base_u, 0):
                        self._item_added_ts[base_u] = int(ts)

            candidates: List[str] = []
            seen: Set[str] = set()
            for raw_u in list(self._item_added_ts.keys()) + list(ordered_fallback or []) + list(self.photo_sources):
                if not isinstance(raw_u, str) or not raw_u:
                    continue
                key = raw_u.split("=")[0] if raw_u.startswith("http") else raw_u
                if key not in seen:
                    seen.add(key)
                    candidates.append(raw_u)

            has_ts = any(self._item_added_ts.get(u.split("=")[0] if u.startswith("http") else u, 0) > 0 for u in candidates)
            if has_ts:
                indexed = list(enumerate(candidates))
                indexed.sort(
                    key=lambda pair: (
                        self._item_added_ts.get(pair[1].split("=")[0] if pair[1].startswith("http") else pair[1], 0),
                        -pair[0],
                    ),
                    reverse=True,
                )
                ordered_raw = [u for _, u in indexed]
            else:
                ordered_raw = candidates

            recent_formatted: List[str] = []
            for u in ordered_raw[:limit]:
                if u.startswith("http") and "lh3.googleusercontent.com" in u:
                    recent_formatted.append(f"{u.split('=')[0]}{cdn_params}")
                else:
                    recent_formatted.append(u)
            self._recent_sources = recent_formatted
            return list(recent_formatted)

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
                has_recent = bool(self._recent_sources)
            if not has_recent:
                self._rebuild_recent_sources(ordered_fallback=cached)
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

            page1_photos, page1_videos, next_token, album_key, auth_key, wiz_data, page1_ts = (
                self._parse_initial_page_with_timestamps(html, final_url)
            )
            cdn_params = f"=w{self.cdn_w}-h{self.cdn_h}"
            result_urls = [f"{base.split('=')[0]}{cdn_params}" for base in page1_photos]
            shuffled_page1 = list(result_urls)
            if self.shuffle and len(shuffled_page1) > 1:
                random.shuffle(shuffled_page1)

            with self._sources_lock:
                self.video_bases = set(page1_videos)
                self.photo_sources = shuffled_page1
                self.current_index = 0
            self._rebuild_recent_sources(ts_map=page1_ts, ordered_fallback=result_urls)

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
            elif result_urls:
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
        discovered_ts: Dict[str, int] = {}
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
                p1, v1, token, a_key, auth, wiz, ts1 = self._parse_initial_page_with_timestamps(html, final_url)
                cdn_params = f"=w{self.cdn_w}-h{self.cdn_h}"
                discovered_urls = [f"{u.split('=')[0]}{cdn_params}" for u in p1]
                discovered_videos.update(v1)
                discovered_ts.update(ts1)
                with self._sources_lock:
                    self.video_bases.update(v1)
            except Exception as e:
                logger.error("Failed to fetch album root during background sync: %s", e)
                return

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
                new_token, rpc_items = self._fetch_rpc_page(a_key, token, auth, wiz)
                if expected_gen is not None and self._album_generation != expected_gen:
                    return

                batch_urls = []
                batch_videos = set()
                for base, is_vid, added_ts in rpc_items:
                    if is_vid:
                        batch_videos.add(base)
                    if added_ts > discovered_ts.get(base, 0):
                        discovered_ts[base] = added_ts
                    if base not in seen_bases:
                        seen_bases.add(base)
                        batch_urls.append(f"{base}{cdn_params}")

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

                if not new_token or not rpc_items:
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

        self._rebuild_recent_sources(ts_map=discovered_ts, ordered_fallback=discovered_urls)

        logger.info(
            "Google Photos album sync complete: %d total items (%d videos).",
            len(all_urls), len(all_vids)
        )
        if all_urls:
            self._save_cached_photos(all_urls, all_vids)
            if callable(getattr(self, "on_album_connected", None)):
                try:
                    self.on_album_connected(self.gphotos_url, getattr(self, "album_title", ""), "")
                except Exception as e:
                    logger.debug("on_album_connected callback error: %s", e)

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
            if sources and callable(getattr(self, "on_album_connected", None)):
                try:
                    self.on_album_connected(self.gphotos_url, getattr(self, "album_title", ""), "")
                except Exception as e:
                    logger.debug("on_album_connected callback error: %s", e)
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
        self.album_title = ""

        if self.cache_file and os.path.exists(self.cache_file):
            try:
                os.remove(self.cache_file)
            except Exception:
                pass

        with self._sources_lock:
            self.photo_sources = []
            self.video_bases = set()
            self.current_index = 0
            self._item_added_ts = {}
            self._recent_sources = []
            self._recent_index = 0
            self._recent_mode_until = 0.0
            self._recent_refreshing = False
            self._recent_expiry_notify_pending = False
            self._recent_activation_seq += 1

        self.flush_slide_queues()
        self._slide_history.clear()

        if self.is_demo_mode:
            logger.info("✨ Switching slideshow to built-in Demo Showcase Mode...")
        else:
            logger.info("🔄 Switching Google Photos album to: %s", self.gphotos_url)
        self.refresh_sources()

        self.flush_slide_queues()

        if callable(on_ready_callback) and self.photo_sources:
            on_ready_callback()

    def flush_slide_queues(self):
        """Clears prefetched surfaces and oriented queues so pool changes take effect immediately."""
        if hasattr(self, "_portrait_queue"):
            self._portrait_queue.clear()
        if hasattr(self, "_landscape_queue"):
            self._landscape_queue.clear()
        if hasattr(self, "_video_url_queue"):
            self._video_url_queue.clear()
        with self._prefetch_lock:
            self.prefetched_surface = None
            self.prefetched_video_list = []

    def check_recent_mode_expiry(self) -> bool:
        """
        Checks if a running Recent Photos session has expired. If so, resets state,
        flushes stale prefetched slides, and returns `True`.
        """
        expired = False
        with self._sources_lock:
            if self._recent_mode_until > 0.0 and time.time() >= self._recent_mode_until:
                self._recent_mode_until = 0.0
                self._recent_refreshing = False
                self._recent_index = 0
                self._recent_expiry_notify_pending = True
                expired = True
        if expired:
            logger.info("🕒 Stream Recent Photos timer expired — reverting to full album.")
            self.flush_slide_queues()
        return expired

    def consume_recent_expiry_notification(self) -> bool:
        """
        Checks expiry and atomically consumes any pending natural-expiry notification flag,
        preventing missed web_frame pool notifications when advance_slide expires first.
        """
        self.check_recent_mode_expiry()
        with self._sources_lock:
            if getattr(self, "_recent_expiry_notify_pending", False):
                self._recent_expiry_notify_pending = False
                return True
            return False

    def is_recent_mode_active(self) -> bool:
        self.check_recent_mode_expiry()
        with self._sources_lock:
            return self._recent_mode_until > time.time()

    def get_recent_mode_status(self) -> Dict[str, Any]:
        self.check_recent_mode_expiry()
        now = time.time()
        with self._sources_lock:
            until = self._recent_mode_until
            active = until > now
            remaining = int(math.ceil(max(0.0, until - now))) if active else 0
            count = len(self._recent_sources)
            refreshing = bool(self._recent_refreshing)
        return {
            "recent_mode_active": active,
            "recent_mode_remaining_sec": remaining,
            "recent_mode_count": count,
            "recent_mode_refreshing": refreshing,
        }

    def get_active_sources_snapshot(self) -> List[str]:
        self.check_recent_mode_expiry()
        now = time.time()
        with self._sources_lock:
            if self._recent_mode_until > now and self._recent_sources:
                return list(self._recent_sources)
            return list(self.photo_sources)

    def quick_refresh_recent(self, max_items: int = 40) -> List[str]:
        """
        Fetches the newest uploaded/added items without waiting for a full resync,
        updates `self._recent_sources` (newest-first, up to `max_items`), and merges
        newly discovered media into `self.photo_sources`.
        """
        limit = max(1, int(max_items or self.RECENT_MAX_ITEMS))
        if self.is_demo_mode or not self.gphotos_url:
            with self._sources_lock:
                cur_sources = list(self.photo_sources)
            if cur_sources and any(isinstance(s, str) and os.path.exists(s) for s in cur_sources):
                cur_sources = sorted(
                    cur_sources,
                    key=lambda p: os.path.getmtime(p) if isinstance(p, str) and os.path.exists(p) else 0.0,
                    reverse=True,
                )
            top = cur_sources[:limit]
            with self._sources_lock:
                self._recent_sources = list(top)
                self._recent_index = 0
            return list(top)

        try:
            req = urllib.request.Request(self.gphotos_url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=15) as res:
                final_url = getattr(res, "geturl", lambda: "")() or ""
                html = res.read().decode("utf-8", errors="ignore")

            p1, v1, next_token, album_key, auth_key, wiz_data, ts1 = self._parse_initial_page_with_timestamps(
                html, final_url
            )
            discovered_bases = list(p1)
            discovered_videos = set(v1)
            discovered_ts = dict(ts1)

            # Oldest-first album safeguard: if Page 1 ascends chronologically and has a next_token,
            # follow RPC pagination (up to 20 pages) to reach the newest end of the album.
            nonzero_p1 = [ts1.get(b, 0) for b in p1 if ts1.get(b, 0) > 0]
            if next_token and album_key and len(nonzero_p1) >= 2 and nonzero_p1[0] < nonzero_p1[-1]:
                token: Optional[str] = next_token
                seen_bases = set(discovered_bases)
                for _ in range(20):
                    if not token:
                        break
                    try:
                        new_token, rpc_items = self._fetch_rpc_page(album_key, token, auth_key, wiz_data)
                        if not rpc_items:
                            break
                        for base, is_vid, added_ts in rpc_items:
                            if is_vid:
                                discovered_videos.add(base)
                            if added_ts > discovered_ts.get(base, 0):
                                discovered_ts[base] = added_ts
                            if base not in seen_bases:
                                seen_bases.add(base)
                                discovered_bases.append(base)
                        token = new_token
                        time.sleep(0.05)
                    except Exception as rpc_err:
                        logger.debug("Quick-refresh RPC tail scan stopped: %s", rpc_err)
                        break

            cdn_params = f"=w{self.cdn_w}-h{self.cdn_h}"
            formatted_discovered = [f"{b.split('=')[0]}{cdn_params}" for b in discovered_bases]
            if formatted_discovered:
                with self._sources_lock:
                    self.video_bases.update(discovered_videos)
                    existing_bases = set(u.split("=")[0] for u in self.photo_sources)
                    new_urls = [u for u in formatted_discovered if u.split("=")[0] not in existing_bases]
                    if new_urls:
                        for new_u in reversed(new_urls):
                            self.photo_sources.insert(self.current_index, new_u)
                    all_urls = list(self.photo_sources)
                    all_vids = list(self.video_bases)
                recent = self._rebuild_recent_sources(
                    ts_map=discovered_ts, ordered_fallback=formatted_discovered, max_items=limit
                )
                self._save_cached_photos(all_urls, all_vids)
                logger.info("⚡ Quick-refresh indexed %d recent Google Photos item(s).", len(recent))
                return recent
        except Exception as e:
            logger.warning("Quick-refresh recent photos failed (using cached list): %s", e)

        return self._rebuild_recent_sources(max_items=limit)

    def activate_recent_mode(
        self,
        duration_sec: float = 300.0,
        on_ready_callback: Optional[Callable[[], None]] = None,
    ) -> int:
        """
        Activates Recent Photos priority playback for `duration_sec` seconds and starts
        a fast background quick-refresh so brand-new uploads play immediately.
        """
        dur = max(30.0, float(duration_sec))
        with self._sources_lock:
            self._recent_activation_seq += 1
            seq = self._recent_activation_seq
            gen = self._album_generation
            self._recent_mode_until = time.time() + dur
            self._recent_refreshing = True
            self._recent_expiry_notify_pending = False
            self._recent_index = 0

        self.flush_slide_queues()

        def worker():
            try:
                self.quick_refresh_recent(max_items=self.RECENT_MAX_ITEMS)
                ready = False
                with self._sources_lock:
                    if (
                        self._recent_activation_seq == seq
                        and self._album_generation == gen
                        and self._recent_mode_until > 0.0
                    ):
                        self._recent_mode_until = time.time() + dur
                        self._recent_index = 0
                        self._recent_refreshing = False
                        ready = bool(self._recent_sources or self.photo_sources)
                if ready:
                    self.flush_slide_queues()
                    if callable(on_ready_callback):
                        on_ready_callback()
            finally:
                with self._sources_lock:
                    if self._recent_activation_seq == seq:
                        self._recent_refreshing = False

        threading.Thread(target=worker, name="QuickRefreshRecentThread", daemon=True).start()
        return seq

    def stop_recent_mode(self) -> bool:
        """Stops Recent Photos mode early and flushes slide queues back to full album."""
        was_active = False
        with self._sources_lock:
            was_active = self._recent_mode_until > 0.0 or self._recent_refreshing
            self._recent_activation_seq += 1
            self._recent_mode_until = 0.0
            self._recent_refreshing = False
            self._recent_expiry_notify_pending = False
            self._recent_index = 0
        self.flush_slide_queues()
        if was_active:
            logger.info("🖼️ Stopped Stream Recent Photos early — resuming full album.")
        return was_active

    def _pop_next_source_url(self) -> Optional[str]:
        self.check_recent_mode_expiry()
        now = time.time()
        with self._sources_lock:
            if self._recent_mode_until > now and self._recent_sources:
                url = self._recent_sources[self._recent_index % len(self._recent_sources)]
                self._recent_index = (self._recent_index + 1) % len(self._recent_sources)
                return url
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
