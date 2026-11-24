import json
import os
import tempfile
import time
import unittest
from unittest import mock

from photoframe.collage import PhotoManager


class TestGooglePhotosScraperAndCache(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.cache_path = os.path.join(self.tmp_dir.name, "album_cache.json")
        self.pm = PhotoManager(
            1920,
            1080,
            album_url="https://photos.app.goo.gl/TestAlbum999",
            cache_file=self.cache_path,
            auto_refresh=False,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_normalize_gphotos_url(self):
        self.assertEqual(PhotoManager.normalize_gphotos_url(""), "")
        self.assertEqual(PhotoManager.normalize_gphotos_url("   "), "")
        self.assertEqual(PhotoManager.normalize_gphotos_url("demo"), "")
        self.assertEqual(PhotoManager.normalize_gphotos_url("NONE"), "")

        self.assertEqual(
            PhotoManager.normalize_gphotos_url("AbCdEfGhIjKlMnOp1"),
            "https://photos.app.goo.gl/AbCdEfGhIjKlMnOp1",
        )
        self.assertEqual(
            PhotoManager.normalize_gphotos_url("https://photos.app.goo.gl/XyZ123"),
            "https://photos.app.goo.gl/XyZ123",
        )
        self.assertEqual(
            PhotoManager.normalize_gphotos_url("share/AF1Qip"),
            "https://photos.google.com/share/AF1Qip",
        )

    def test_parse_initial_page_photos_videos_and_pagination_tokens(self):
        """
        Verifies `_parse_initial_page` extracts Google Photos `lh3.googleusercontent.com/pw/`
        base URLs, identifies video items via key `"76647426"`, and extracts `page_token`,
        `album_key`, `auth_key`, and `WIZ_global_data` for `snAcKc` RPC pagination.
        """
        url_photo_1 = "https://lh3.googleusercontent.com/pw/AP1GczPhotoOne_LongIdentifierString_1234567890"
        url_photo_2 = "https://lh3.googleusercontent.com/pw/AP1GczPhotoTwo_LongIdentifierString_0987654321"
        url_video_1 = "https://lh3.googleusercontent.com/pw/AP1GczVideoOne_LongIdentifierString_1122334455"
        album_id = "AF1QipN_SampleSharedAlbumIdentifier_123456789012345"
        next_token = "CjkKMjRfc2FtcGxlX2NvbnRpbnVhdGlvbl90b2tlbl9mb3JfcGFnaW5hdGlvbl9ycGNfY2FsbB"

        ds1_obj = [
            None,
            [
                ["item1", [url_photo_1, 4032, 3024]],
                ["item2", [url_photo_2, 1920, 1080]],
                ["item3", [url_video_1, 1920, 1080], {"76647426": [15000]}],
            ],
            next_token,
            [album_id],
        ]
        sample_html = (
            'window.WIZ_global_data = {"FdrFJe": "12345", "cfb2h": "boq_photosuiserver"};\n'
            f"AF_initDataCallback({{key: 'ds:1', data: {json.dumps(ds1_obj)}, sideChannel: {{}}}});"
        )
        resolved_url = f"https://photos.google.com/share/{album_id}?key=SampleAuthKey_987654321"

        photos, videos, page_token, album_key, auth_key, wiz_data = self.pm._parse_initial_page(
            sample_html, resolved_url
        )
        self.assertEqual(photos, [url_photo_1, url_photo_2, url_video_1])
        self.assertEqual(videos, {url_video_1})
        self.assertEqual(page_token, next_token)
        self.assertEqual(album_key, album_id)
        self.assertEqual(auth_key, "SampleAuthKey_987654321")
        self.assertEqual(wiz_data.get("FdrFJe"), "12345")

    def test_url_cache_roundtrip_with_video_metadata(self):
        """
        Verifies `_save_cached_photos` and `_load_cached_photos` preserve both photo URLs
        (formatted with CDN dimensions) and video base URLs in the RAM JSON cache.
        """
        u1 = "https://lh3.googleusercontent.com/pw/AP1GczCacheTestPhoto1_123456789012345"
        u2 = "https://lh3.googleusercontent.com/pw/AP1GczCacheTestVideo2_123456789012345"
        self.pm._save_cached_photos([u1, u2], [u2])

        pm2 = PhotoManager(
            1920,
            1080,
            album_url="https://photos.app.goo.gl/TestAlbum999",
            cache_file=self.cache_path,
            auto_refresh=False,
        )
        loaded_urls, updated_at, has_videos = pm2._load_cached_photos()
        self.assertTrue(has_videos)
        self.assertGreater(updated_at, 0.0)
        self.assertEqual(len(loaded_urls), 2)
        self.assertTrue(loaded_urls[0].startswith(u1 + "=w"))
        self.assertTrue(pm2.is_video_source(loaded_urls[1]))
        self.assertFalse(pm2.is_video_source(loaded_urls[0]))

    def test_packaged_synthetic_test_media_and_remote_demo_links(self):
        """
        Verifies that packaged synthetic test images & videos in `demo_media/` and
        remote demo links in `CURATED_DEMO_URLS` are properly discovered.
        """
        self.assertEqual(len(PhotoManager.CURATED_DEMO_URLS), 24)
        self.assertTrue(all(u.startswith("https://") for u in PhotoManager.CURATED_DEMO_URLS))

        if os.path.isdir(PhotoManager.DEFAULT_DEMO_DIR):
            pm_local = PhotoManager(
                1920,
                1080,
                album_url="demo",
                local_dir=PhotoManager.DEFAULT_DEMO_DIR,
                auto_refresh=False,
            )
            local_items = pm_local.scan_local_directory(PhotoManager.DEFAULT_DEMO_DIR)
            self.assertGreaterEqual(len(local_items), 12)
            self.assertGreaterEqual(len(pm_local.video_bases), 2)

    def test_broken_unsplash_url_falls_back_to_local_synthetic_test_photo(self):
        """
        Verifies that when an Unsplash link (portrait or landscape) is broken or unreachable,
        `_get_fallback_test_photo_path` and `_download_raw_pil` replace the dead URL in
        `photo_sources` and return a matching-orientation local synthetic test photo from `demo_media/`.
        """
        broken_portrait = "https://images.unsplash.com/photo-broken-404?auto=format&fit=crop&w=720&h=1080&q=82"
        broken_landscape = "https://images.unsplash.com/photo-broken-404?auto=format&fit=crop&w=1080&h=720&q=82"

        pm_demo = PhotoManager(1920, 1080, album_url="demo", auto_refresh=False)
        pm_demo.photo_sources = [broken_portrait, broken_landscape]

        fb_port = pm_demo._get_fallback_test_photo_path(broken_portrait)
        fb_land = pm_demo._get_fallback_test_photo_path(broken_landscape)

        self.assertIsNotNone(fb_port)
        self.assertIsNotNone(fb_land)
        self.assertTrue(os.path.exists(fb_port))
        self.assertTrue(os.path.exists(fb_land))
        self.assertTrue(os.path.basename(fb_port).startswith("p"))
        self.assertTrue(os.path.basename(fb_land).startswith("l"))
        # Broken Unsplash URLs must be replaced in photo_sources with the local fallback paths
        self.assertNotIn(broken_portrait, pm_demo.photo_sources)
        self.assertNotIn(broken_landscape, pm_demo.photo_sources)

    def test_extract_item_metadata_prefers_added_timestamp(self):
        u_new = "https://lh3.googleusercontent.com/pw/AP1GczAddedTsItem_1234567890"
        u_cap = "https://lh3.googleusercontent.com/pw/AP1GczCaptureTsItem_1234567890"
        u_none = "https://lh3.googleusercontent.com/pw/AP1GczNoTsItem_1234567890"
        self.assertEqual(
            PhotoManager._extract_item_metadata(["id1", [u_new, 1000, 800], 1700000000000, None, None, 1795400000000]),
            (u_new, False, 1795400000000),
        )
        self.assertEqual(
            PhotoManager._extract_item_metadata(["id2", [u_cap, 1000, 800], 1712345000000, None, {"76647426": [1]}]),
            (u_cap, True, 1712345000000),
        )
        self.assertEqual(
            PhotoManager._extract_item_metadata(["id3", [u_none, 1000, 800]]),
            (u_none, False, 0),
        )

    def test_quick_refresh_recent_and_expiry_lifecycle(self):
        """
        Verifies quick_refresh_recent collects newest uploaded items (even on oldest-first
        multi-page albums via RPC tail fetch), flushes prefetched queues, cycles newest-first
        when active, persists recent_items in RAM cache, and reverts cleanly on stop or expiry.
        """
        u_old = "https://lh3.googleusercontent.com/pw/AP1GczOldItem_123456789012345"
        u_mid = "https://lh3.googleusercontent.com/pw/AP1GczMidItem_123456789012345"
        u_newest = "https://lh3.googleusercontent.com/pw/AP1GczNewestItem_123456789012345"
        album_id = "AF1QipN_RecentAlbum_123456"

        ds1_obj = [
            None,
            [
                ["i1", [u_old, 1000, 800], 1600000000000, None, None, 1700000000000],
                ["i2", [u_mid, 1000, 800], 1600000000000, None, None, 1750000000000],
            ],
            "PAGE_TOKEN_2",
            [album_id],
        ]
        html_page1 = (
            'window.WIZ_global_data = {"FdrFJe": "111", "cfb2h": "b1", "SNlM0e": "a1"};\n'
            f"AF_initDataCallback({{key: 'ds:1', data: {json.dumps(ds1_obj)}, sideChannel: {{}}}});"
        )
        rpc_inner = json.dumps([
            None,
            [
                ["i3", [u_newest, 1000, 800], 1600000000000, None, {"76647426": [5000]}, 1795499999000],
            ],
            None,
        ])
        rpc_body = ')]}\'\n' + json.dumps([["wrb.fr", "snAcKc", rpc_inner]])

        class _FakeResp:
            def __init__(self, payload: str, url: str = ""):
                self._data = payload.encode("utf-8")
                self._url = url
            def read(self):
                return self._data
            def geturl(self):
                return self._url
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        def fake_urlopen(req, context=None, timeout=15):
            req_url = req.full_url if hasattr(req, "full_url") else str(req)
            if "batchexecute" in req_url:
                return _FakeResp(rpc_body, req_url)
            return _FakeResp(html_page1, f"https://photos.google.com/share/{album_id}?key=AuthK")

        cdn_suffix = f"=w{self.pm.cdn_w}-h{self.pm.cdn_h}"
        self.pm.photo_sources = [f"{u_old}{cdn_suffix}", f"{u_mid}{cdn_suffix}"]
        self.pm.prefetched_surface = object()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            recent = self.pm.quick_refresh_recent(max_items=40)

        self.assertEqual(
            [u.split("=")[0] for u in recent],
            [u_newest, u_mid, u_old],
        )
        self.assertTrue(self.pm.is_video_source(f"{u_newest}{cdn_suffix}"))
        self.assertIn(f"{u_newest}{cdn_suffix}", self.pm.photo_sources)

        # Cache reload preserves recent_items
        pm_reloaded = PhotoManager(
            1920, 1080,
            album_url="https://photos.app.goo.gl/TestAlbum999",
            cache_file=self.cache_path,
            auto_refresh=False,
        )
        pm_reloaded._load_cached_photos()
        self.assertEqual([u.split("=")[0] for u in pm_reloaded._recent_sources], [u_newest, u_mid, u_old])

        # Activation -> pops newest-first, Stop -> reverts to full album & flushes prefetch
        with self.pm._sources_lock:
            self.pm._recent_mode_until = time.time() + 300.0
            self.pm._recent_index = 0
        self.assertEqual(self.pm._pop_next_source_url().split("=")[0], u_newest)
        self.assertEqual(self.pm._pop_next_source_url().split("=")[0], u_mid)
        self.assertTrue(self.pm.get_recent_mode_status()["recent_mode_active"])
        self.assertGreater(self.pm.get_recent_mode_status()["recent_mode_remaining_sec"], 0)

        self.pm.prefetched_surface = object()
        self.assertTrue(self.pm.stop_recent_mode())
        self.assertIsNone(self.pm.prefetched_surface)
        self.assertFalse(self.pm.is_recent_mode_active())

        # Natural expiry clears prefetched slide and reverts
        with self.pm._sources_lock:
            self.pm._recent_mode_until = time.time() - 1.0
        self.pm.prefetched_surface = object()
        self.assertTrue(self.pm.check_recent_mode_expiry())
        self.assertIsNone(self.pm.prefetched_surface)
        self.assertFalse(self.pm.get_recent_mode_status()["recent_mode_active"])
        self.assertTrue(self.pm.consume_recent_expiry_notification())
        self.assertFalse(self.pm.consume_recent_expiry_notification())


if __name__ == "__main__":
    unittest.main()
