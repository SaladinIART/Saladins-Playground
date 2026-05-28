"""
Hex map renderer (programmer-art v0).

Draws terrain-coloured hex polygons with:
  - Border outline
  - Hover highlight
  - Ownership tint on capturable tiles
  - Viewport culling (skip off-screen hexes)
  - Terrain initial letter overlay
  - Fog of war (visible / explored / unseen tri-state)
"""
from __future__ import annotations

import math
from typing import Callable, Optional

import pygame

from src.engine.hex import Hex
from src.engine.tile import Tile, CAPTURE_TURNS
from src.engine.unit import Unit
from src.engine.veterancy import rank_of as _rank_of
from src.render.camera import Camera
from src.render.sprites import get_terrain_sprite, get_unit_sprite

# Precompute unit-circle corners for a pointy-top hex (no trig each frame).
_UNIT_CORNERS: tuple[tuple[float, float], ...] = tuple(
    (math.cos(math.radians(60 * i - 90)), math.sin(math.radians(60 * i - 90)))
    for i in range(6)
)

BORDER_COLOR = (0, 0, 0)
HOVER_BRIGHTEN = 60       # RGB additive boost on hover
OWNERSHIP_ALPHA = 80      # Alpha of faction-colour overlay (0-255)

# Movement overlay colours (RGBA)
_REACH_FILL   = (60,  140, 255, 70)
_REACH_BORDER = (100, 180, 255, 180)
_PATH_FILL    = (255, 240,  60, 90)
_PATH_BORDER  = (255, 255, 100, 220)
_SEL_FILL     = (255, 255, 255, 50)
_SEL_BORDER   = (255, 255, 255, 230)

# Attack overlay (red ring on targetable enemy hexes)
_ATK_FILL          = (255,  80,  80,  60)
_ATK_FILL_HOVER    = (255, 100, 100, 130)
_ATK_BORDER        = (255, 120, 120, 200)
_ATK_BORDER_HOVER  = (255, 200, 200, 255)

# Fog of war
_FOG_EXPLORED_FACTOR = 0.40         # multiplier for explored-but-not-visible tiles
_FOG_UNSEEN_COLOR    = (28, 32, 42) # dark fill for never-seen hexes
_FOG_UNSEEN_BORDER   = (12, 14, 18)

# Rank pip colours: 1=bronze, 2=silver, 3=gold, 4=cyan, 5=magenta
_PIP_COLORS: dict[int, tuple[int, int, int]] = {
    1: (180, 110,  30),   # bronze
    2: (200, 200, 215),   # silver
    3: (255, 210,   0),   # gold
    4: ( 60, 220, 225),   # cyan
    5: (220,  60, 225),   # magenta
}

FACTION_COLORS: dict[str, tuple[int, int, int]] = {
    "NATO": (30, 80, 200),
    "BRICS": (200, 30, 30),
    "GUERILLA": (30, 160, 60),
}

TERRAIN_LETTERS: dict[str, str] = {
    "plain": "",
    "forest": "F",
    "mountain": "M",
    "road": "",
    "river": "~",
    "bridge": "=",
    "city": "C",
    "oil_well": "O",
    "airfield": "A",
    "hq": "HQ",
}

UNIT_CLASS_LETTERS: dict[str, str] = {
    "infantry": "I",
    "engineer": "E",
    "recon": "R",
    "vehicle": "V",
    "artillery": "Y",
    "aa": "AA",
    "sniper": "S",
    "jet": "J",
    "helicopter": "H",
    "bomber": "B",
}

_MARGIN = 80  # px — draw hexes this far outside screen edges (avoids pop-in)


def _brighten(color: tuple[int, int, int], amount: int) -> tuple[int, int, int]:
    return tuple(min(255, c + amount) for c in color)  # type: ignore[return-value]


def _blend(base: tuple[int, int, int], tint: tuple[int, int, int], alpha: int) -> tuple[int, int, int]:
    a = alpha / 255.0
    return tuple(int(b * (1 - a) + t * a) for b, t in zip(base, tint))  # type: ignore[return-value]


class HexRenderer:
    def __init__(self, camera: Camera) -> None:
        self.camera = camera
        self._fonts: dict[int, pygame.font.Font] = {}

    def _get_font(self, size: int) -> pygame.font.Font:
        if size not in self._fonts:
            self._fonts[size] = pygame.font.SysFont("consolas", size)
        return self._fonts[size]

    def _hex_polygon(self, h: Hex) -> list[tuple[float, float]]:
        cx, cy = self.camera.hex_to_screen(h)
        s = self.camera.hex_size
        return [(cx + ux * s, cy + uy * s) for ux, uy in _UNIT_CORNERS]

    def _on_screen(self, cx: float, cy: float) -> bool:
        m = _MARGIN + self.camera.hex_size
        return (
            -m <= cx <= self.camera.screen_w + m
            and -m <= cy <= self.camera.screen_h + m
        )

    def draw_map(
        self,
        surface: pygame.Surface,
        tiles: dict[Hex, Tile],
        hovered_hex: Optional[Hex] = None,
        visible: Optional[set] = None,
        explored: Optional[set] = None,
    ) -> None:
        """
        Render the map. If *visible* is None, fog is disabled (debug view).
        Otherwise each tile renders in one of three states:
          - currently visible  → full colour
          - explored only      → dimmed by _FOG_EXPLORED_FACTOR
          - unseen             → flat dark fill, no terrain letter
        """
        label_size = max(8, int(self.camera.hex_size * 0.38))
        font = self._get_font(label_size)
        fog_active = visible is not None

        for h, tile in tiles.items():
            cx, cy = self.camera.hex_to_screen(h)
            if not self._on_screen(cx, cy):
                continue

            in_visible = (not fog_active) or h in visible
            in_explored = in_visible or (explored is not None and h in explored)

            poly = self._hex_polygon(h)

            if not in_explored:
                # Never seen — draw a dark placeholder so the map shape is legible.
                pygame.draw.polygon(surface, _FOG_UNSEEN_COLOR, poly)
                pygame.draw.polygon(surface, _FOG_UNSEEN_BORDER, poly, 1)
                continue

            terrain = tile.terrain
            color: tuple[int, int, int] = terrain.color  # type: ignore[assignment]

            # Ownership tint on capturable tiles.
            if tile.owner_faction and terrain.capturable:
                fc = FACTION_COLORS.get(tile.owner_faction, (200, 200, 200))
                color = _blend(color, fc, OWNERSHIP_ALPHA)

            # Explored-only: dim the colour.
            if not in_visible:
                color = tuple(int(c * _FOG_EXPLORED_FACTOR) for c in color)  # type: ignore[assignment]

            # Hover highlight (only on revealed hexes).
            if h == hovered_hex:
                color = _brighten(color, HOVER_BRIGHTEN)

            pygame.draw.polygon(surface, color, poly)
            pygame.draw.polygon(surface, BORDER_COLOR, poly, 1)

            # Terrain sprite icon — overlaid on top of the polygon.
            # Only drawn at meaningful zoom levels; falls back to the letter.
            sprite_size = max(16, int(self.camera.hex_size * 1.35))
            terrain_spr = get_terrain_sprite(terrain.id, sprite_size) if self.camera.hex_size >= 20 else None

            if terrain_spr is not None:
                # Dim the sprite for explored-only hexes to match the polygon dim.
                if not in_visible:
                    terrain_spr = terrain_spr.copy()
                    terrain_spr.set_alpha(int(255 * _FOG_EXPLORED_FACTOR))
                sx = int(cx - sprite_size / 2)
                sy = int(cy - sprite_size / 2)
                surface.blit(terrain_spr, (sx, sy))
            else:
                # Fallback: terrain letter label.
                letter = TERRAIN_LETTERS.get(terrain.id, "")
                if letter and self.camera.hex_size >= 24:
                    lbl_color = (0, 0, 0) if in_visible else (70, 75, 85)
                    lbl = font.render(letter, True, lbl_color)
                    lx = cx - lbl.get_width() / 2
                    ly = cy - lbl.get_height() / 2
                    surface.blit(lbl, (lx, ly))

            # Capture progress indicator (e.g. "2/3") — only on visible tiles.
            if in_visible and tile.capture_progress > 0 and self.camera.hex_size >= 18:
                cap_str = f"{tile.capture_progress}/{CAPTURE_TURNS}"
                cap_color = FACTION_COLORS.get(
                    tile.capturing_faction, (255, 255, 180)
                )
                cap_size = max(7, int(self.camera.hex_size * 0.30))
                cap_lbl = self._get_font(cap_size).render(cap_str, True, cap_color)
                clx = cx - cap_lbl.get_width() / 2
                cly = cy + self.camera.hex_size * 0.32
                surface.blit(cap_lbl, (clx, cly))

    def draw_units(
        self,
        surface: pygame.Surface,
        units: list[Unit],
        can_see: Optional[Callable[[Unit], bool]] = None,
    ) -> None:
        """
        Draw all live units. Filled circle in faction colour + class letter.
        If *can_see* is given, units for which it returns False are skipped.
        """
        radius = max(6, int(self.camera.hex_size * 0.42))
        font = self._get_font(max(10, int(self.camera.hex_size * 0.42)))
        for u in units:
            if not u.is_alive():
                continue
            if can_see is not None and not can_see(u):
                continue
            cx, cy = self.camera.hex_to_screen(u.hex)
            if not self._on_screen(cx, cy):
                continue
            faction_color = FACTION_COLORS.get(u.faction, (180, 180, 180))
            pygame.draw.circle(surface, faction_color, (int(cx), int(cy)), radius)
            pygame.draw.circle(surface, (0, 0, 0), (int(cx), int(cy)), radius, 2)

            # Unit icon sprite — drawn on top of the faction circle.
            icon_size   = max(10, int(radius * 1.55))
            unit_icon   = get_unit_sprite(u.unit_type.unit_class, icon_size)
            if unit_icon is not None:
                surface.blit(unit_icon, (cx - icon_size / 2, cy - icon_size / 2))
            else:
                # Fallback: class letter.
                letter = UNIT_CLASS_LETTERS.get(u.unit_type.unit_class, "?")
                lbl = font.render(letter, True, (255, 255, 255))
                surface.blit(lbl, (cx - lbl.get_width() / 2, cy - lbl.get_height() / 2))

            # HP bar (only if damaged)
            if u.hp < u.unit_type.hp:
                bar_w = int(radius * 2)
                bar_h = max(2, int(self.camera.hex_size * 0.08))
                bx = int(cx - radius)
                by = int(cy + radius + 2)
                pygame.draw.rect(surface, (60, 60, 60), (bx, by, bar_w, bar_h))
                fill_w = int(bar_w * (u.hp / u.unit_type.hp))
                hp_color = (60, 200, 60) if u.hp > 5 else (220, 180, 40) if u.hp > 2 else (220, 60, 60)
                pygame.draw.rect(surface, hp_color, (bx, by, fill_w, bar_h))

            # Rank pips -- drawn above the unit circle, one per rank > 0.
            # Colours escalate: bronze / silver / gold / cyan / magenta.
            rank = _rank_of(u.level)
            if rank > 0 and self.camera.hex_size >= 16:
                pip_r   = max(2, int(self.camera.hex_size * 0.065))
                pip_gap = 1                              # px between pips
                pip_dia = pip_r * 2
                total_w = rank * pip_dia + (rank - 1) * pip_gap
                pip_y   = int(cy - radius - pip_r - 2)
                pip_x0  = int(cx - total_w / 2) + pip_r
                col     = _PIP_COLORS.get(rank, (255, 255, 255))
                dark    = tuple(c // 3 for c in col)
                for p in range(rank):
                    px = pip_x0 + p * (pip_dia + pip_gap)
                    pygame.draw.circle(surface, col,  (px, pip_y), pip_r)
                    pygame.draw.circle(surface, dark, (px, pip_y), pip_r, 1)

    def draw_movement_overlay(
        self,
        surface: pygame.Surface,
        reachable: dict,
        path: list,
        selected_hex: Optional["Hex"],
        costs: Optional[dict] = None,
    ) -> None:
        """
        Draw a semi-transparent overlay showing:
          - All reachable hexes in blue.
          - The current hover path in yellow.
          - The selected unit's hex with a white ring.
          - Optional MP-cost numbers centred on each reachable hex (CP-31).

        *costs* is a ``dict[Hex, int]`` mapping destination -> MP spent.
        Numbers are only drawn when ``hex_size >= 24`` to avoid clutter at
        small zoom levels.  Pass the same ``movement.reachable`` dict that
        the caller already has; no extra computation needed.
        """
        overlay = pygame.Surface(
            (self.camera.screen_w, self.camera.screen_h), pygame.SRCALPHA
        )
        path_set = set(path)

        # 1. Reachable hexes (blue) — skip ones that are on the path (drawn brighter below).
        for h in reachable:
            if h in path_set:
                continue
            cx, cy = self.camera.hex_to_screen(h)
            if not self._on_screen(cx, cy):
                continue
            poly = self._hex_polygon(h)
            pygame.draw.polygon(overlay, _REACH_FILL, poly)
            pygame.draw.polygon(overlay, _REACH_BORDER, poly, 2)

        # 2. Path hexes (yellow) — skip the start hex (drawn as selected below).
        for h in path[1:]:
            cx, cy = self.camera.hex_to_screen(h)
            if not self._on_screen(cx, cy):
                continue
            poly = self._hex_polygon(h)
            pygame.draw.polygon(overlay, _PATH_FILL, poly)
            pygame.draw.polygon(overlay, _PATH_BORDER, poly, 2)

        # 3. Selected unit hex (white ring).
        if selected_hex is not None:
            cx, cy = self.camera.hex_to_screen(selected_hex)
            if self._on_screen(cx, cy):
                poly = self._hex_polygon(selected_hex)
                pygame.draw.polygon(overlay, _SEL_FILL, poly)
                pygame.draw.polygon(overlay, _SEL_BORDER, poly, 2)

        surface.blit(overlay, (0, 0))

        # 4. MP-cost numbers -- drawn directly on *surface* (not the SRCALPHA
        #    overlay) so alpha-blending doesn't muddy the digits.
        #    Only shown at meaningful zoom; skip hexes on the current path
        #    (the yellow highlight already makes them obvious).
        if costs is not None and self.camera.hex_size >= 24:
            cost_font_size = max(9, int(self.camera.hex_size * 0.30))
            cost_font = self._get_font(cost_font_size)
            cost_surf = pygame.Surface(
                (self.camera.screen_w, self.camera.screen_h), pygame.SRCALPHA
            )
            for h, mp in costs.items():
                if h in path_set or h == selected_hex:
                    continue
                cx, cy = self.camera.hex_to_screen(h)
                if not self._on_screen(cx, cy):
                    continue
                lbl = cost_font.render(str(mp), True, (200, 230, 255))
                lbl.set_alpha(200)
                cost_surf.blit(
                    lbl,
                    (int(cx - lbl.get_width() / 2),
                     int(cy - lbl.get_height() / 2)),
                )
            surface.blit(cost_surf, (0, 0))

    def draw_attack_overlay(
        self,
        surface: pygame.Surface,
        target_hexes,         # iterable[Hex] — enemies the selected unit can hit
        hovered_hex: Optional["Hex"] = None,
    ) -> None:
        """Red overlay on hexes the selected attacker can hit. Hover = brighter."""
        overlay = pygame.Surface(
            (self.camera.screen_w, self.camera.screen_h), pygame.SRCALPHA
        )
        for h in target_hexes:
            cx, cy = self.camera.hex_to_screen(h)
            if not self._on_screen(cx, cy):
                continue
            poly = self._hex_polygon(h)
            is_hover = (h == hovered_hex)
            fill = _ATK_FILL_HOVER if is_hover else _ATK_FILL
            border = _ATK_BORDER_HOVER if is_hover else _ATK_BORDER
            width = 3 if is_hover else 2
            pygame.draw.polygon(overlay, fill, poly)
            pygame.draw.polygon(overlay, border, poly, width)
        surface.blit(overlay, (0, 0))

    def draw_ai_trace(
        self,
        surface: pygame.Surface,
        actions: list,          # list[Action] -- oldest first, newest last
        state: "object",        # GameState -- used to look up unit positions
    ) -> None:
        """Draw faded arrows showing the last N AI actions (CP-32).

        Each action type is handled differently:
          - MoveAttackAction  -> arrow from unit's *current* position to dest hex;
                                 a red dot on target_uid's hex when attack included
          - AttackAction      -> red dot pulsing between attacker and defender
          - BuildAction       -> a small cyan diamond on the HQ hex
          - UpgradeTierAction -> ignored (no meaningful position)

        Oldest actions are most transparent; newest are most opaque.
        Arrows are always shown regardless of fog (the player just saw them).
        """
        n = len(actions)
        if n == 0:
            return

        overlay = pygame.Surface(
            (self.camera.screen_w, self.camera.screen_h), pygame.SRCALPHA
        )

        for idx, action in enumerate(actions):
            # Newest = index n-1 -> alpha 200; oldest = index 0 -> alpha 40
            t = (idx + 1) / n   # 1/n .. 1.0
            alpha = int(40 + 160 * t)
            arrow_col  = (160, 220, 255, alpha)   # blue-white
            attack_col = (255,  80,  80, alpha)   # red

            # Delayed import to avoid circular dep at module level
            from src.ai.heuristic import (
                AttackAction,
                MoveAttackAction,
                BuildAction,
                UpgradeTierAction,
            )

            if isinstance(action, MoveAttackAction):
                # Unit may have moved; look up its current hex from state
                unit = getattr(state, "units", {}).get(action.unit_uid)
                src_h = unit.hex if unit is not None else None
                dst_h = action.dest
                if src_h is not None and src_h != dst_h:
                    sx, sy = self.camera.hex_to_screen(src_h)
                    dx, dy = self.camera.hex_to_screen(dst_h)
                    if self._on_screen(sx, sy) or self._on_screen(dx, dy):
                        self._draw_arrow(overlay, (sx, sy), (dx, dy), arrow_col, 3)
                # Red dot on target if it was an attack move
                if action.target_uid is not None:
                    tgt = getattr(state, "units", {}).get(action.target_uid)
                    if tgt is not None:
                        tx, ty = self.camera.hex_to_screen(tgt.hex)
                        if self._on_screen(tx, ty):
                            r = max(4, int(self.camera.hex_size * 0.18))
                            pygame.draw.circle(overlay, attack_col, (int(tx), int(ty)), r)

            elif isinstance(action, AttackAction):
                atk = getattr(state, "units", {}).get(action.attacker_uid)
                dfn = getattr(state, "units", {}).get(action.defender_uid)
                if atk is not None and dfn is not None:
                    sx, sy = self.camera.hex_to_screen(atk.hex)
                    dx, dy = self.camera.hex_to_screen(dfn.hex)
                    if self._on_screen(sx, sy) or self._on_screen(dx, dy):
                        self._draw_arrow(overlay, (sx, sy), (dx, dy), attack_col, 3)

            elif isinstance(action, BuildAction):
                bx, by = self.camera.hex_to_screen(action.hq_hex)
                if self._on_screen(bx, by):
                    r = max(5, int(self.camera.hex_size * 0.22))
                    # Cyan diamond (4-point star via two rotated rect approximation)
                    diamond = [
                        (int(bx),     int(by) - r),
                        (int(bx) + r, int(by)),
                        (int(bx),     int(by) + r),
                        (int(bx) - r, int(by)),
                    ]
                    pygame.draw.polygon(overlay, (80, 220, 220, alpha), diamond)

        surface.blit(overlay, (0, 0))

    @staticmethod
    def _draw_arrow(
        surface: pygame.Surface,
        start: tuple,
        end: tuple,
        color: tuple,
        width: int = 2,
    ) -> None:
        """Draw a line with a small arrowhead at *end*."""
        sx, sy = start; ex, ey = end
        pygame.draw.line(surface, color, (int(sx), int(sy)), (int(ex), int(ey)), width)

        # Arrowhead: two short lines fanning back from the tip
        dx = ex - sx; dy = ey - sy
        length = math.hypot(dx, dy)
        if length < 1:
            return
        ux = dx / length; uy = dy / length   # unit vector toward tip
        # Perpendicular
        px = -uy; py = ux
        head = 10   # px
        fan  = 0.40 # perpendicular spread
        for sign in (1, -1):
            hx = ex - ux * head + sign * px * head * fan
            hy = ey - uy * head + sign * py * head * fan
            pygame.draw.line(surface, color, (int(ex), int(ey)),
                             (int(hx), int(hy)), width)
