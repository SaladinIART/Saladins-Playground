"""
Camera: pan + zoom-on-cursor for the hex map.

World pixels = axial_to_pixel(hex, hex_size).
Screen pixels = world + (offset_x, offset_y).
All state lives here; no Pygame surfaces touched.
"""
from __future__ import annotations

import pygame

from src.engine.hex import Hex, axial_to_pixel, pixel_to_axial

PAN_SPEED = 360.0       # pixels per second (WASD)
ZOOM_STEP = 1.15        # factor per scroll tick
HEX_SIZE_MIN = 14.0
HEX_SIZE_MAX = 80.0


class Camera:
    def __init__(
        self,
        screen_w: int,
        screen_h: int,
        hex_size: float = 36.0,
        offset_x: float = 60.0,
        offset_y: float = 60.0,
    ) -> None:
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.hex_size = hex_size
        self.offset_x = offset_x
        self.offset_y = offset_y
        self._drag_origin: tuple[int, int] | None = None
        self._drag_offset_start: tuple[float, float] = (0.0, 0.0)
        # CP-33: track whether the RMB press became a drag (>5 px) so we can
        # distinguish a right-click-inspect from a right-drag-pan.
        self._drag_moved:   bool = False
        self._right_clicked: bool = False   # set for one event-poll cycle

    # ------------------------------------------------------------------
    # Transforms
    # ------------------------------------------------------------------

    def world_to_screen(self, wx: float, wy: float) -> tuple[float, float]:
        return (wx + self.offset_x, wy + self.offset_y)

    def screen_to_world(self, sx: float, sy: float) -> tuple[float, float]:
        return (sx - self.offset_x, sy - self.offset_y)

    def hex_to_screen(self, h: Hex) -> tuple[float, float]:
        wx, wy = axial_to_pixel(h, self.hex_size)
        return self.world_to_screen(wx, wy)

    def screen_to_hex(self, sx: float, sy: float) -> Hex:
        wx, wy = self.screen_to_world(sx, sy)
        return pixel_to_axial(wx, wy, self.hex_size)

    # ------------------------------------------------------------------
    # Zoom (keeps anchor pixel fixed)
    # ------------------------------------------------------------------

    def zoom(self, factor: float, anchor_sx: float, anchor_sy: float) -> None:
        new_size = max(HEX_SIZE_MIN, min(HEX_SIZE_MAX, self.hex_size * factor))
        ratio = new_size / self.hex_size
        self.offset_x = anchor_sx - (anchor_sx - self.offset_x) * ratio
        self.offset_y = anchor_sy - (anchor_sy - self.offset_y) * ratio
        self.hex_size = new_size

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Process a single Pygame event. Returns True if camera changed."""
        if event.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            factor = ZOOM_STEP if event.y > 0 else 1.0 / ZOOM_STEP
            self.zoom(factor, mx, my)
            return True

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            # Use event.pos when available (testable without a real display).
            self._drag_origin = getattr(event, "pos", pygame.mouse.get_pos())
            self._drag_offset_start = (self.offset_x, self.offset_y)
            self._drag_moved = False
            self._right_clicked = False
            return False

        if event.type == pygame.MOUSEBUTTONUP and event.button == 3:
            if self._drag_origin is not None and not self._drag_moved:
                self._right_clicked = True   # short press -> inspect click
            self._drag_origin = None
            return False

        if event.type == pygame.MOUSEMOTION and self._drag_origin is not None:
            mx, my = getattr(event, "pos", pygame.mouse.get_pos())
            dx = mx - self._drag_origin[0]
            dy = my - self._drag_origin[1]
            if abs(dx) > 5 or abs(dy) > 5:
                self._drag_moved = True
            self.offset_x = self._drag_offset_start[0] + dx
            self.offset_y = self._drag_offset_start[1] + dy
            return True

        return False

    def take_right_click(self) -> bool:
        """Return True (and clear the flag) if a right-click-without-drag just fired.

        Call once per event-poll iteration.  The flag is set by ``handle_event``
        and cleared here so callers can react exactly once per click.
        """
        v = self._right_clicked
        self._right_clicked = False
        return v

    def center_on(self, h: Hex) -> None:
        """Pan so that hex *h* is centred in the viewport."""
        wx, wy = axial_to_pixel(h, self.hex_size)
        self.offset_x = self.screen_w / 2 - wx
        self.offset_y = self.screen_h / 2 - wy

    def handle_keys(self, keys: pygame.key.ScancodeWrapper, dt: float) -> bool:
        """WASD pan. Call once per frame with delta-time in seconds."""
        dx = dy = 0.0
        speed = PAN_SPEED * dt
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            dx += speed
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            dx -= speed
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            dy += speed
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            dy -= speed
        if dx or dy:
            self.offset_x += dx
            self.offset_y += dy
            return True
        return False
