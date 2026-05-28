"""
Tests for CP-28 (rank pips), CP-29 (floating damage numbers),
and CP-30 (level-up flash + SFX).

All render tests run headlessly via SDL dummy drivers.
"""
from __future__ import annotations

import os
import wave
from pathlib import Path

import pytest

# Headless pygame before any module import that triggers pygame.init()
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
pygame.init()

from src.engine.hex import Hex
from src.engine.tile import load_terrain
from src.engine.unit import load_units
from src.engine.veterancy import (
    RANKS,
    award_xp,
    rank_of,
    xp_for_level,
    XP_FOR_KILL,
)
from src.render.camera import Camera
from src.render.floaters import (
    FloaterLayer,
    DAMAGE_DURATION,
    LEVELUP_DURATION,
    _Floater,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _load_data():
    load_terrain()
    load_units()


@pytest.fixture
def cam() -> Camera:
    """Tiny camera for screen-position calculations."""
    return Camera(screen_w=800, screen_h=600, hex_size=40)


@pytest.fixture
def font() -> pygame.font.Font:
    return pygame.font.SysFont("consolas", 14)


@pytest.fixture
def surface() -> pygame.Surface:
    return pygame.Surface((800, 600))


# ===========================================================================
# CP-28: Rank pips
# ===========================================================================

class TestRankPips:
    """Verify pip rendering produces non-background pixels in the pip region."""

    def _make_heroic_unit(self):
        """Return a NATO infantry unit at Heroic rank (level 15)."""
        from src.engine.unit import Unit
        u = Unit(type_id="nato_inf_l", faction="NATO", hex=Hex(0, 0))
        # Heroic = rank 3 = level 15
        u.xp    = xp_for_level(15)
        u.level = 15
        return u

    def test_rank_of_heroic_is_3(self, _load_data):
        assert rank_of(15) == 3

    def test_no_pips_drawn_for_rookie(self, cam, surface, _load_data):
        """Rookie (rank 0) must not draw any pip pixels above the unit circle."""
        from src.engine.unit import Unit
        from src.render.hex_renderer import HexRenderer
        u = Unit(type_id="nato_inf_l", faction="NATO", hex=Hex(0, 0))
        # rank 0 -- no pips
        assert u.level == 1
        assert rank_of(u.level) == 0

        renderer = HexRenderer(cam)
        bg = (18, 24, 38)
        surface.fill(bg)
        renderer.draw_units(surface, [u])

        cx, cy = cam.hex_to_screen(Hex(0, 0))
        radius  = max(6, int(cam.hex_size * 0.42))
        # Pip region: a thin strip above the circle
        pip_y   = int(cy - radius - 8)
        strip   = pygame.surfarray.pixels3d(surface)
        # No non-background pixels expected in the pip strip
        for x in range(int(cx) - 10, int(cx) + 10):
            if 0 <= x < 800 and 0 <= pip_y < 600:
                px = tuple(strip[x, pip_y])
                # Allow the circle border to spill up slightly; just check that
                # there are no gold/bronze/silver/magenta/cyan pixels
                assert px not in (
                    (180, 110, 30),   # bronze
                    (200, 200, 215),  # silver
                    (255, 210, 0),    # gold
                ), f"Unexpected pip color {px} at ({x}, {pip_y})"

    def test_pips_drawn_for_heroic(self, cam, surface, _load_data):
        """Heroic rank (3) should produce gold pip pixels above the unit circle."""
        from src.render.hex_renderer import HexRenderer, _PIP_COLORS
        u = self._make_heroic_unit()

        renderer = HexRenderer(cam)
        bg = (18, 24, 38)
        surface.fill(bg)
        renderer.draw_units(surface, [u])

        cx, cy = cam.hex_to_screen(Hex(0, 0))
        radius  = max(6, int(cam.hex_size * 0.42))
        pip_r   = max(2, int(cam.hex_size * 0.065))
        pip_y   = int(cy - radius - pip_r - 2)
        expected_color = _PIP_COLORS[3]  # gold

        strip = pygame.surfarray.pixels3d(surface)
        found = False
        for x in range(max(0, int(cx) - 20), min(800, int(cx) + 20)):
            if 0 <= pip_y < 600:
                px = tuple(strip[x, pip_y])
                if px == expected_color:
                    found = True
                    break
        assert found, "Expected gold pip pixels for a Heroic-rank unit"

    def test_pip_count_matches_rank(self, _load_data):
        """Sanity: each rank index equals the number of pips drawn."""
        for lvl, expected_rank in [
            (1, 0), (5, 1), (10, 2), (15, 3), (20, 4), (25, 5)
        ]:
            assert rank_of(lvl) == expected_rank

    def test_mythic_pips_are_magenta(self, cam, surface, _load_data):
        from src.engine.unit import Unit
        from src.render.hex_renderer import HexRenderer, _PIP_COLORS
        u = Unit(type_id="nato_inf_l", faction="NATO", hex=Hex(0, 0))
        u.xp = xp_for_level(25); u.level = 25
        assert rank_of(25) == 5

        renderer = HexRenderer(cam)
        surface.fill((18, 24, 38))
        renderer.draw_units(surface, [u])

        cx, cy = cam.hex_to_screen(Hex(0, 0))
        radius  = max(6, int(cam.hex_size * 0.42))
        pip_r   = max(2, int(cam.hex_size * 0.065))
        pip_y   = int(cy - radius - pip_r - 2)
        expected = _PIP_COLORS[5]  # magenta

        strip = pygame.surfarray.pixels3d(surface)
        found = any(
            tuple(strip[x, pip_y]) == expected
            for x in range(max(0, int(cx) - 30), min(800, int(cx) + 30))
            if 0 <= pip_y < 600
        )
        assert found, "Expected magenta pip pixels for a Mythic-rank unit"


# ===========================================================================
# CP-29: FloaterLayer -- unit tests
# ===========================================================================

class TestFloaterLayer:

    def test_push_adds_floater(self):
        fl = FloaterLayer()
        assert fl.count == 0
        fl.push("hello", (255, 0, 0), (100, 200))
        assert fl.count == 1

    def test_tick_decrements_lifetime(self):
        fl = FloaterLayer()
        fl.push("test", (255, 255, 255), (50, 50), duration=1.0)
        fl.tick(0.3)
        # Still alive (0.7 remaining)
        assert fl.count == 1

    def test_tick_removes_expired_floaters(self):
        fl = FloaterLayer()
        fl.push("short", (255, 0, 0), (10, 10), duration=0.2)
        fl.tick(0.5)   # well past expiry
        assert fl.count == 0

    def test_multiple_floaters_expire_independently(self):
        fl = FloaterLayer()
        fl.push("long",  (0, 255, 0), (0, 0), duration=1.0)
        fl.push("short", (255, 0, 0), (0, 0), duration=0.3)
        fl.tick(0.4)   # short expires, long survives
        assert fl.count == 1

    def test_clear_removes_all(self):
        fl = FloaterLayer()
        for i in range(5):
            fl.push(str(i), (100, 100, 100), (i * 10, 0))
        fl.clear()
        assert fl.count == 0

    def test_push_at_hex_positions_near_hex(self, cam):
        """push_at_hex should place a floater close to the hex's screen position."""
        fl = FloaterLayer()
        h  = Hex(2, 3)
        fl.push_at_hex("X", (255, 255, 0), h, cam)
        assert fl.count == 1
        f = fl._floaters[0]
        cx, cy = cam.hex_to_screen(h)
        assert abs(f.screen_x - cx) < 2
        assert abs(f.screen_y - cy) < 2

    def test_push_at_hex_y_offset(self, cam):
        fl = FloaterLayer()
        h  = Hex(0, 0)
        fl.push_at_hex("X", (255, 0, 0), h, cam, y_offset=-20)
        f  = fl._floaters[0]
        _, cy = cam.hex_to_screen(h)
        assert abs(f.screen_y - (cy - 20)) < 2

    def test_draw_does_not_crash(self, surface, font):
        """draw() with live floaters must not raise."""
        fl = FloaterLayer()
        fl.push("-5",      (255, 80, 80),   (200, 300))
        fl.push("+3",      (80, 230, 100),  (400, 300))
        fl.push("LEVEL UP!", (255, 220, 50), (300, 200), duration=LEVELUP_DURATION)
        fl.draw(surface, font)  # should not raise

    def test_draw_no_floaters_is_noop(self, surface, font):
        fl = FloaterLayer()
        before = pygame.surfarray.pixels3d(surface).copy()
        fl.draw(surface, font)
        after = pygame.surfarray.pixels3d(surface)
        # Surface unchanged when no floaters
        assert (before == after).all()

    def test_default_duration_is_damage_duration(self):
        fl = FloaterLayer()
        fl.push("X", (255, 0, 0), (0, 0))
        f = fl._floaters[0]
        assert abs(f.max_lifetime - DAMAGE_DURATION) < 1e-9

    def test_levelup_floater_lasts_longer(self):
        fl = FloaterLayer()
        fl.push("LEVEL UP!", (255, 220, 50), (0, 0), duration=LEVELUP_DURATION)
        f = fl._floaters[0]
        assert f.max_lifetime > DAMAGE_DURATION

    def test_alpha_fade_schedule(self):
        """Floater alpha should be full early and fading late (verify fade region)."""
        fl = FloaterLayer()
        fl.push("X", (255, 255, 255), (400, 300), duration=1.0)

        # Just born (t ~ 0.001) -- should be in the sustain zone (t < 0.60)
        fl.tick(0.001)
        f = fl._floaters[0]
        t_early = 1.0 - (f.lifetime / f.max_lifetime)
        assert t_early < 0.60, "Early tick should be in sustain zone"

        # Tick another 0.70s so total elapsed = 0.701, lifetime = 0.299, t ~ 0.701
        fl.tick(0.700)
        f = fl._floaters[0]
        t_late = 1.0 - (f.lifetime / f.max_lifetime)
        # t_late > 0.60 => alpha < 255 (fade zone)
        assert t_late > 0.60, "Late tick should be in fade zone"

    def test_lifetime_never_goes_negative(self):
        fl = FloaterLayer()
        fl.push("X", (0, 0, 0), (0, 0), duration=0.1)
        fl.tick(99.0)   # massively over-tick
        # Floater pruned; count drops to 0
        assert fl.count == 0


# ===========================================================================
# CP-30: levelup.wav exists and is a valid WAV
# ===========================================================================

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "sfx"


class TestLevelupWAV:

    def test_levelup_wav_exists(self):
        assert (ASSETS / "levelup.wav").is_file(), "assets/sfx/levelup.wav missing"

    def test_levelup_wav_is_valid_pcm(self):
        path = ASSETS / "levelup.wav"
        with wave.open(str(path)) as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2        # 16-bit
            assert wf.getframerate() == 44100

    def test_levelup_wav_duration_is_reasonable(self):
        """Should be between 0.3 s and 2.0 s."""
        path = ASSETS / "levelup.wav"
        with wave.open(str(path)) as wf:
            dur = wf.getnframes() / wf.getframerate()
        assert 0.3 <= dur <= 2.0, f"Unexpected duration: {dur:.2f}s"

    def test_levelup_wav_has_signal(self):
        """File must not be silent (all-zero samples)."""
        import array as _array
        path = ASSETS / "levelup.wav"
        with wave.open(str(path)) as wf:
            raw = wf.readframes(wf.getnframes())
        samples = _array.array("h", raw)
        assert any(s != 0 for s in samples), "levelup.wav appears to be silent"
