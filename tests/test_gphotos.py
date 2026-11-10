import json
import os
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
