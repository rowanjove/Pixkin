"""Pure display-placement rules shared by desktop UI components."""

from dataclasses import dataclass
from typing import Any, Iterable, Tuple


@dataclass(frozen=True)
class DisplayRect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width - 1

    @property
    def bottom(self) -> int:
        return self.top + self.height - 1

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom


def pet_position(
    saved: Any,
    *,
    window_size: Tuple[int, int],
    screens: Iterable[DisplayRect],
    primary: DisplayRect,
    right_margin: int = 28,
    bottom_margin: int = 44,
) -> Tuple[int, int]:
    """Restore a visible position, otherwise use the primary display."""
    width, height = window_size
    screen_list = list(screens)
    if isinstance(saved, dict) and "x" in saved and "y" in saved:
        try:
            x, y = int(saved["x"]), int(saved["y"])
        except (TypeError, ValueError):
            pass
        else:
            center_x = x + width // 2
            center_y = y + height // 2
            if any(
                screen.contains(center_x, center_y)
                for screen in screen_list
            ):
                return x, y
    return (
        primary.right - width - right_margin,
        primary.bottom - height - bottom_margin,
    )


def overlay_position(
    *,
    anchor_position: Tuple[int, int],
    anchor_size: Tuple[int, int],
    overlay_size: Tuple[int, int],
    screen: DisplayRect,
    padding: int = 8,
) -> Tuple[int, int]:
    """Place an overlay above its anchor and keep its origin on-screen."""
    anchor_x, anchor_y = anchor_position
    anchor_width, anchor_height = anchor_size
    overlay_width, overlay_height = overlay_size
    x = anchor_x + (anchor_width - overlay_width) // 2
    y = anchor_y - overlay_height - padding
    if y < screen.top + padding:
        y = anchor_y + anchor_height + padding
    x = max(
        screen.left + padding,
        min(x, screen.right - overlay_width + 1 - padding),
    )
    y = max(
        screen.top + padding,
        min(y, screen.bottom - overlay_height + 1 - padding),
    )
    return x, y
