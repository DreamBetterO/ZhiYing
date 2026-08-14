from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from video_study.frames import select_keyframe_candidates


class KeyframeSelectionTests(unittest.TestCase):
    def test_content_rich_late_scene_beats_early_title_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = []
            for index in range(9):
                image = Image.new("RGB", (640, 360), "white")
                draw = ImageDraw.Draw(image)
                if index < 3:
                    draw.text((250, 170), "TITLE", fill="black")
                elif index < 6:
                    for row in range(4):
                        draw.text((240, 120 + row * 30), f"MENU {row}", fill="black")
                else:
                    for x in range(80, 560):
                        shade = int(255 * (x - 80) / 480)
                        draw.line((x, 70, x, 290), fill=(shade, 80, 255 - shade))
                    draw.text((180, 170), "KEY KNOWLEDGE", fill="black")
                path = root / f"candidate_{index:05d}.jpg"
                image.save(path, quality=90)
                paths.append(path)

            chosen = select_keyframe_candidates(paths, {
                "scene_change_threshold": 0.02,
                "min_content_entropy": 2.0,
                "max_keyframes": 3,
            })

        self.assertEqual(len(chosen), 1)
        self.assertGreaterEqual(chosen[0][1], 6)

    def test_long_video_selection_skips_setup_and_spreads_across_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = []
            for index in range(48):
                image = Image.new("RGB", (640, 360), "white")
                draw = ImageDraw.Draw(image)
                if index < 6:
                    for x in range(640):
                        draw.line((x, 0, x, 359), fill=((x * 17) % 255, (x * 31) % 255, (x * 47) % 255))
                else:
                    draw.rectangle((100, 60, 540, 300), outline="black", width=2)
                    for row in range(1 + index % 5):
                        draw.text((140, 100 + row * 28), f"SECTION {index // 6} POINT {row}", fill="black")
                path = root / f"candidate_{index:05d}.jpg"
                image.save(path, quality=90)
                paths.append(path)

            chosen = select_keyframe_candidates(paths, {
                "scene_change_threshold": 0.005,
                "min_content_entropy": 0.5,
                "max_keyframes": 4,
                "_min_candidate_index": 6,
                "_min_candidate_gap": 8,
            })

        indices = [row[1] for row in chosen]
        self.assertEqual(len(indices), 4)
        self.assertGreaterEqual(min(indices), 6)
        self.assertLess(indices[0], 18)
        self.assertGreater(indices[-1], 36)
        self.assertTrue(all(right - left >= 8 for left, right in zip(indices, indices[1:])))


if __name__ == "__main__":
    unittest.main()
