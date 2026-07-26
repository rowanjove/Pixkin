import unittest

from core.services.display_layout import (
    DisplayRect,
    overlay_position,
    pet_position,
)


class DisplayLayoutTests(unittest.TestCase):
    def setUp(self):
        self.left_screen = DisplayRect(-1920, 0, 1920, 1080)
        self.primary = DisplayRect(0, 0, 2560, 1440)

    def test_saved_position_on_negative_coordinate_monitor_is_restored(self):
        position = pet_position(
            {"x": -900, "y": 240},
            window_size=(180, 180),
            screens=[self.left_screen, self.primary],
            primary=self.primary,
        )

        self.assertEqual(position, (-900, 240))

    def test_offscreen_or_invalid_position_falls_back_to_primary(self):
        expected = (
            self.primary.right - 180 - 28,
            self.primary.bottom - 180 - 44,
        )
        for saved in (
            {"x": 9000, "y": 9000},
            {"x": "invalid", "y": 0},
            None,
        ):
            with self.subTest(saved=saved):
                self.assertEqual(
                    pet_position(
                        saved,
                        window_size=(180, 180),
                        screens=[self.left_screen, self.primary],
                        primary=self.primary,
                    ),
                    expected,
                )

    def test_overlay_is_clamped_inside_negative_coordinate_monitor(self):
        x, y = overlay_position(
            anchor_position=(-1900, 20),
            anchor_size=(180, 180),
            overlay_size=(420, 560),
            screen=self.left_screen,
        )

        self.assertGreaterEqual(x, self.left_screen.left + 8)
        self.assertLessEqual(
            x + 420,
            self.left_screen.right + 1 - 8,
        )
        self.assertGreaterEqual(y, self.left_screen.top + 8)
        self.assertLessEqual(
            y + 560,
            self.left_screen.bottom + 1 - 8,
        )

    def test_overlay_moves_below_anchor_when_top_space_is_insufficient(self):
        position = overlay_position(
            anchor_position=(1000, 10),
            anchor_size=(180, 180),
            overlay_size=(420, 300),
            screen=self.primary,
        )

        self.assertEqual(position[1], 198)


if __name__ == "__main__":
    unittest.main()
