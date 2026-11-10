import random
import unittest
from typing import Optional, Tuple

from photoframe.collage import PIL_AVAILABLE, Image, PhotoManager


class FakeImage:
    """
    Lightweight, zero-dependency image double used when Pillow is not installed on the test host
    or for deterministic synthetic edge-energy & smart-crop tests.
    """

    def __init__(
        self,
        width: int,
        height: int,
        hotspot: Optional[Tuple[float, float]] = None,
        crop_box: Optional[Tuple[int, int, int, int]] = None,
    ):
        self.width = width
        self.height = height
        self.hotspot = hotspot
        self.crop_box = crop_box

    @property
    def size(self) -> Tuple[int, int]:
        return (self.width, self.height)

    def get_edge_bytes(self, thumb_sz: int = 48) -> bytes:
        buf = bytearray(thumb_sz * thumb_sz)
        if self.hotspot is not None:
            hx = int(round(self.hotspot[0] * (thumb_sz - 1)))
            hy = int(round(self.hotspot[1] * (thumb_sz - 1)))
            for dy in range(-3, 4):
                for dx in range(-3, 4):
                    px = max(0, min(thumb_sz - 1, hx + dx))
                    py = max(0, min(thumb_sz - 1, hy + dy))
                    buf[py * thumb_sz + px] = 240
        return bytes(buf)

    def crop_fit(self, size: Tuple[int, int], centering: Tuple[float, float] = (0.5, 0.5)) -> "FakeImage":
        target_w, target_h = size
        target_aspect = target_w / max(1, target_h)
        src_aspect = self.width / max(1, self.height)
        if src_aspect > target_aspect:
            crop_h = self.height
            crop_w = int(round(crop_h * target_aspect))
        else:
            crop_w = self.width
            crop_h = int(round(crop_w / target_aspect))
        left = int(round((self.width - crop_w) * centering[0]))
        top = int(round((self.height - crop_h) * centering[1]))
        return FakeImage(
            target_w,
            target_h,
            hotspot=self.hotspot,
            crop_box=(left, top, left + crop_w, top + crop_h),
        )

    def resize(self, size: Tuple[int, int], *args, **kwargs) -> "FakeImage":
        return FakeImage(size[0], size[1], hotspot=self.hotspot, crop_box=self.crop_box)

    def paste(self, other: "FakeImage", pos: Tuple[int, int], *args, **kwargs):
        pass


class TestCollageGeometryAndSmartCrop(unittest.TestCase):
    EXPECTED_TILE_COUNTS = {
        "stack-2": 2,
        "stack-3": 3,
        "bento-4": 4,
        "grid-trio": 3,
        "grid-classic-5": 5,
        "grid-mosaic-7": 7,
        "grid-sidebar-9": 9,
        "grid-mondrian-8": 8,
        "grid-3-2-3": 8,
    }

    def _assert_valid_grid_cells(self, cells, canvas_w: int, canvas_h: int, style: str):
        pad = 14
        gap = 10
        self.assertEqual(
            len(cells),
            self.EXPECTED_TILE_COUNTS[style],
            f"Style {style} expected {self.EXPECTED_TILE_COUNTS[style]} cells, got {len(cells)}",
        )

        # Bounding box and even-dimension checks
        min_x = min(x for x, y, w, h in cells)
        min_y = min(y for x, y, w, h in cells)
        max_r = max(x + w for x, y, w, h in cells)
        max_b = max(y + h for x, y, w, h in cells)

        self.assertEqual(min_x, pad, f"{style}: left margin should be {pad}px")
        self.assertEqual(min_y, pad, f"{style}: top margin should be {pad}px")
        self.assertLessEqual( abs(max_r - (canvas_w - pad)), 2, f"{style}: right margin mismatch")
        self.assertLessEqual(abs(max_b - (canvas_h - pad)), 2, f"{style}: bottom margin mismatch")

        for idx, (x, y, w, h) in enumerate(cells):
            self.assertGreater(w, 60, f"{style} tile {idx} width too small: {w}")
            self.assertGreater(h, 60, f"{style} tile {idx} height too small: {h}")
            self.assertEqual(w % 2, 0, f"{style} tile {idx} width {w} must be even for FFmpeg")
            self.assertEqual(h % 2, 0, f"{style} tile {idx} height {h} must be even for FFmpeg")

        # Pairwise non-overlap and 10px inner gap verification
        for i in range(len(cells)):
            x1, y1, w1, h1 = cells[i]
            r1, b1 = x1 + w1, y1 + h1
            for j in range(i + 1, len(cells)):
                x2, y2, w2, h2 = cells[j]
                r2, b2 = x2 + w2, y2 + h2
                overlap_x = min(r1, r2) - max(x1, x2)
                overlap_y = min(b1, b2) - max(y1, y2)
                self.assertFalse(
                    overlap_x > 0 and overlap_y > 0,
                    f"{style}: tiles {i} {(x1, y1, w1, h1)} and {j} {(x2, y2, w2, h2)} overlap!",
                )
                # If horizontally adjacent (share vertical overlap) and close, gap must be 10px
                if overlap_y > 0:
                    h_dist = max(x1, x2) - min(r1, r2)
                    if h_dist < 40:
                        self.assertEqual(h_dist, gap, f"{style}: horizontal gap between tiles {i} & {j} is {h_dist} != {gap}")
                # If vertically adjacent (share horizontal overlap) and close, gap must be 10px
                if overlap_x > 0:
                    v_dist = max(y1, y2) - min(b1, b2)
                    if v_dist < 40:
                        self.assertEqual(v_dist, gap, f"{style}: vertical gap between tiles {i} & {j} is {v_dist} != {gap}")

    def test_horizontal_1920x1080_all_templates_geometry_and_natural_aspect_ratios(self):
        """
        Verifies all 9 collage templates in Horizontal (`0°/180°`, `1920x1080`) orientation
        have exact 14px margins, 10px gaps, and natural Portrait (`~3:4`) and Landscape (`~4:3–16:9`)
        tile aspect ratios so photos never suffer extreme cropping.
        """
        random.seed(42)
        pm = PhotoManager(1920, 1080, album_url="demo", auto_refresh=False)
        for style in PhotoManager.COLLAGE_STYLES:
            for trial in range(6):
                cells = pm._get_grid_template_cells(style)
                self._assert_valid_grid_cells(cells, 1920, 1080, style)
                for idx, (x, y, w, h) in enumerate(cells):
                    aspect = w / h
                    if h > w:
                        # Portrait tile on horizontal screen: must be natural 3:4 / 4:5 range
                        self.assertGreaterEqual(
                            aspect, 0.58, f"{style} (trial {trial}) portrait tile {idx} too skinny: {w}x{h} ({aspect:.2f})"
                        )
                        self.assertLessEqual(
                            aspect, 0.92, f"{style} (trial {trial}) portrait tile {idx} too square: {w}x{h} ({aspect:.2f})"
                        )
                    else:
                        # Landscape tile on horizontal screen: full-height hero tiles (h >= 1000) are ~1.03+,
                        # while split-height landscape tiles (h < 1000) are natural 4:3 to 16:9 (1.30 to 2.05)
                        min_land_aspect = 1.02 if h >= 1000 else 1.30
                        self.assertGreaterEqual(
                            aspect, min_land_aspect, f"{style} (trial {trial}) landscape tile {idx} too boxy: {w}x{h} ({aspect:.2f})"
                        )
                        self.assertLessEqual(
                            aspect, 2.05, f"{style} (trial {trial}) landscape tile {idx} too wide: {w}x{h} ({aspect:.2f})"
                        )

    def test_portrait_1080x1920_all_templates_geometry(self):
        """
        Verifies all 9 collage templates in Portrait (`90°/270°`, `1080x1920`) orientation
        maintain exact 14px margins, 10px inner gaps, even dimensions, and zero overlaps.
        """
        random.seed(99)
        pm = PhotoManager(1080, 1920, album_url="demo", auto_refresh=False)
        for style in PhotoManager.COLLAGE_STYLES:
            for _ in range(4):
                cells = pm._get_grid_template_cells(style)
                self._assert_valid_grid_cells(cells, 1080, 1920, style)

    def test_core_content_centering_priors_and_subject_detection(self):
        """
        Verifies `_compute_core_content_centering` uses upper-middle prior `(0.50, 0.38)` for
        portrait photos (protecting heads/faces) and `(0.50, 0.46)` for landscape photos, and
        shifts toward high-contrast subject regions while clamping to safe bounds.
        """
        # 1. Flat image without edges falls back to ergonomic photographic priors
        flat_portrait = FakeImage(1200, 1600, hotspot=None)
        flat_landscape = FakeImage(1600, 1200, hotspot=None)
        self.assertEqual(PhotoManager._compute_core_content_centering(flat_portrait, 780, 1052), (0.50, 0.38))
        self.assertEqual(PhotoManager._compute_core_content_centering(flat_landscape, 1094, 520), (0.50, 0.46))

        # 2. Portrait image with subject near upper-left (0.20, 0.20) shifts crop center up and left
        subj_portrait = FakeImage(1200, 1600, hotspot=(0.20, 0.20))
        cx, cy = PhotoManager._compute_core_content_centering(subj_portrait, 780, 1052)
        self.assertLess(cx, 0.42)
        self.assertLess(cy, 0.32)
        self.assertGreaterEqual(cx, 0.20)
        self.assertGreaterEqual(cy, 0.18)

        # 3. Landscape image with subject near right-bottom (0.85, 0.75) shifts crop center right and down
        subj_landscape = FakeImage(1920, 1080, hotspot=(0.85, 0.75))
        cx2, cy2 = PhotoManager._compute_core_content_centering(subj_landscape, 840, 520)
        self.assertGreater(cx2, 0.58)
        self.assertGreater(cy2, 0.55)
        self.assertLessEqual(cx2, 0.80)
        self.assertLessEqual(cy2, 0.78)

    def test_smart_crop_fit_preserves_aspect_ratio_without_stretching(self):
        """
        Verifies `_smart_crop_fit` crops only the excess axis to match the target tile `(w, h)`
        aspect ratio and biases the crop box toward the detected subject.
        """
        # Portrait 1200x1800 (2:3) fitted into a 3:4 tile (750x1000)
        img_top = FakeImage(1200, 1800, hotspot=(0.50, 0.18))
        img_bot = FakeImage(1200, 1800, hotspot=(0.50, 0.80))
        fitted_top = PhotoManager._smart_crop_fit(img_top, 750, 1000)
        fitted_bot = PhotoManager._smart_crop_fit(img_bot, 750, 1000)

        self.assertEqual((fitted_top.width, fitted_top.height), (750, 1000))
        self.assertEqual((fitted_bot.width, fitted_bot.height), (750, 1000))
        # Crop box width should be full 1200, height should be 1600 (3:4), and top offset should be smaller for img_top
        self.assertIsNotNone(fitted_top.crop_box)
        self.assertEqual(fitted_top.crop_box[2] - fitted_top.crop_box[0], 1200)
        self.assertEqual(fitted_top.crop_box[3] - fitted_top.crop_box[1], 1600)
        self.assertLess(fitted_top.crop_box[1], fitted_bot.crop_box[1])

        if PIL_AVAILABLE and Image is not None:
            real_img = Image.new("RGB", (1600, 1200), (40, 80, 120))
            real_fitted = PhotoManager._smart_crop_fit(real_img, 800, 450)
            self.assertEqual(real_fitted.size, (800, 450))

    def test_compose_single_photo_fit_modes(self):
        """
        Verifies `_compose_single_photo` handles `cover`, `contain`, and `contain-blur`
        correctly for both matching and non-matching orientations.
        """
        pm = PhotoManager(1920, 1080, album_url="demo", photo_fit="cover", auto_refresh=False)
        land_img = FakeImage(1600, 1200)
        canvas, rects = pm._compose_single_photo(land_img)
        self.assertEqual(rects, [(0, 0, 1920, 1080)])

        # Portrait single photo on 1920x1080 horizontal screen in `contain` mode
        pm.photo_fit = "contain"
        port_img = FakeImage(1080, 1440)
        _, rects_port = pm._compose_single_photo(port_img)
        self.assertEqual(len(rects_port), 1)
        rx, ry, rw, rh = rects_port[0]
        self.assertEqual(rh, 1080)
        self.assertEqual(rw, 810)
        self.assertEqual(rx, (1920 - 810) // 2)
        self.assertEqual(ry, 0)


if __name__ == "__main__":
    unittest.main()
