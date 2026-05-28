"""
Floating text layer for CP-29 (damage numbers) and CP-30 (level-up flash).

A FloaterLayer holds a list of short-lived text labels that drift upward and
fade out over their lifetime.  Every frame:

  1. Call ``tick(dt)`` to advance lifetimes and prune dead floaters.
  2. Call ``draw(surface, font)`` to render all live floaters onto *surface*.

Push new floaters with:

  layer.push(text, color, screen_pos)               -- screen coords
  layer.push_at_hex(text, color, hex, camera)        -- world hex -> screen

All parameters are intentionally simple so the layer can be created once and
shared across the whole playing screen without any engine dependency.

ASCII-only source (Pygbag cp1252 constraint).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import pygame

from src.engine.hex import Hex
from src.render.camera import Camera

# Default durations
DAMAGE_DURATION  = 0.85   # seconds for -N / +N labels
LEVELUP_DURATION = 1.30   # seconds for LEVEL UP! banner


@dataclass
class _Floater:
    text:         str
    color:        tuple   # (R, G, B)
    screen_x:     float
    screen_y:     float
    lifetime:     float   # seconds remaining
    max_lifetime: float   # total duration (for alpha/offset interpolation)


class FloaterLayer:
    """Manages and renders all active floating text labels."""

    def __init__(self) -> None:
        self._floaters: List[_Floater] = []

    # ------------------------------------------------------------------
    # Push API
    # ------------------------------------------------------------------

    def push(
        self,
        text: str,
        color: tuple,
        screen_pos: tuple,
        duration: float = DAMAGE_DURATION,
    ) -> None:
        """Add a floater at *screen_pos* = (x, y)."""
        x, y = screen_pos
        self._floaters.append(
            _Floater(text, color, float(x), float(y), duration, duration)
        )

    def push_at_hex(
        self,
        text: str,
        color: tuple,
        h: Hex,
        camera: Camera,
        duration: float = DAMAGE_DURATION,
        y_offset: int = 0,
    ) -> None:
        """Add a floater centred on *h* (world hex), converting via *camera*."""
        cx, cy = camera.hex_to_screen(h)
        self.push(text, color, (cx, cy + y_offset), duration)

    # ------------------------------------------------------------------
    # Per-frame API
    # ------------------------------------------------------------------

    def tick(self, dt: float) -> None:
        """Advance all floaters by *dt* seconds; prune expired ones."""
        for f in self._floaters:
            f.lifetime -= dt
        self._floaters = [f for f in self._floaters if f.lifetime > 0.0]

    def draw(self, surface: pygame.Surface, font: pygame.font.Font) -> None:
        """Render all live floaters onto *surface*."""
        for f in self._floaters:
            # t goes 0 (just born) -> 1 (about to die)
            t = 1.0 - (f.lifetime / f.max_lifetime)
            # Alpha: full at birth, fades to 0 in the last 40% of lifetime
            fade_start = 0.60
            if t < fade_start:
                alpha = 255
            else:
                alpha = int(255 * (1.0 - (t - fade_start) / (1.0 - fade_start)))
            alpha = max(0, min(255, alpha))

            # Drift: rise up to 36px over the full lifetime
            y_drift = int(t * 36)

            lbl = font.render(f.text, True, f.color)
            lbl.set_alpha(alpha)
            sx = int(f.screen_x - lbl.get_width() / 2)
            sy = int(f.screen_y - y_drift - lbl.get_height() / 2)
            surface.blit(lbl, (sx, sy))

    def clear(self) -> None:
        """Remove all floaters (call on screen-state reset)."""
        self._floaters.clear()

    @property
    def count(self) -> int:
        """Number of live floaters (useful for testing)."""
        return len(self._floaters)
