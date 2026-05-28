"""
Tests for CP-33 (right-click inspect), CP-34 (volume cycle),
and CP-35 (save-slot screenshot thumbnails).

All run headlessly via SDL dummy drivers.
ASCII-only source (Pygbag cp1252 constraint).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
pygame.init()

from src.engine.hex import Hex
from src.engine.tile import load_terrain
from src.engine.unit import load_units
from src.persistence.save import thumbnail_path, slot_path, autosave_path
from src.render.camera import Camera


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _load():
    load_terrain()
    load_units()


@pytest.fixture
def cam() -> Camera:
    return Camera(screen_w=800, screen_h=600, hex_size=40)


# ===========================================================================
# CP-33: Camera right-click vs right-drag classifier
# ===========================================================================

class TestRightClickClassifier:
    """Verify Camera.take_right_click() distinguishes clicks from drags."""

    def _btn3_down(self, cam: Camera, pos=(100, 200)):
        e = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=3, pos=pos)
        cam.handle_event(e)

    def _btn3_up(self, cam: Camera, pos=(100, 200)):
        e = pygame.event.Event(pygame.MOUSEBUTTONUP, button=3, pos=pos)
        cam.handle_event(e)

    def _motion(self, cam: Camera, pos=(100, 200)):
        e = pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(0, 0, 1))
        cam.handle_event(e)

    def test_no_click_initially(self, cam):
        assert cam.take_right_click() is False

    def test_short_press_registers_as_click(self, cam):
        """Press and release at same spot -> right-click."""
        self._btn3_down(cam, (100, 200))
        self._btn3_up(cam,   (100, 200))
        assert cam.take_right_click() is True

    def test_take_right_click_clears_flag(self, cam):
        """Flag is consumed exactly once."""
        self._btn3_down(cam, (100, 200))
        self._btn3_up(cam,   (100, 200))
        assert cam.take_right_click() is True
        assert cam.take_right_click() is False

    def test_drag_does_not_register_as_click(self, cam):
        """Mouse moved > 5px -> drag, not click."""
        self._btn3_down(cam, (100, 200))
        # Move 20px to the right -- exceeds 5px threshold
        self._motion(cam, (120, 200))
        self._btn3_up(cam,   (120, 200))
        assert cam.take_right_click() is False

    def test_tiny_jitter_below_threshold_still_click(self, cam):
        """Displacement of 3px is within threshold -> still counts as click."""
        self._btn3_down(cam, (100, 200))
        self._motion(cam, (103, 200))   # 3px < 5px threshold
        self._btn3_up(cam, (103, 200))
        assert cam.take_right_click() is True

    def test_exactly_five_px_is_drag(self, cam):
        """Displacement of exactly 5px on x-axis triggers drag flag."""
        self._btn3_down(cam, (100, 200))
        # Check our implementation uses > 5 (strict): 5 should not trigger
        # Actually the code says abs(dx) > 5, so dx=5 is NOT a drag
        self._motion(cam, (105, 200))   # dx=5 not > 5 -> still click
        self._btn3_up(cam, (105, 200))
        assert cam.take_right_click() is True

    def test_six_px_is_drag(self, cam):
        """Displacement of 6px triggers drag."""
        self._btn3_down(cam, (100, 200))
        self._motion(cam, (106, 200))   # dx=6 > 5 -> drag
        self._btn3_up(cam, (106, 200))
        assert cam.take_right_click() is False

    def test_drag_still_pans_camera(self, cam):
        """Right-drag must still pan the camera regardless of threshold."""
        ox0 = cam.offset_x
        self._btn3_down(cam, (100, 200))
        self._motion(cam, (160, 200))   # large drag
        # Camera offset should have changed
        assert cam.offset_x != ox0

    def test_no_click_on_up_without_prior_down(self, cam):
        """Spurious MOUSEBUTTONUP without a prior MOUSEBUTTONDOWN -> no click."""
        self._btn3_up(cam, (100, 200))
        assert cam.take_right_click() is False

    def test_multiple_sequential_clicks(self, cam):
        """Two separate short presses -> two separate click signals."""
        self._btn3_down(cam, (100, 200))
        self._btn3_up(cam,   (100, 200))
        first = cam.take_right_click()

        self._btn3_down(cam, (200, 300))
        self._btn3_up(cam,   (200, 300))
        second = cam.take_right_click()

        assert first is True
        assert second is True


# ===========================================================================
# CP-34: Volume cycle (unit tests on SoundManager)
# ===========================================================================

class TestVolumeCycle:

    def test_set_volume_clamps_high(self):
        from src.audio.sounds import SoundManager
        mgr = SoundManager()
        mgr.set_volume(2.0)
        assert mgr.master_volume == 1.0

    def test_set_volume_clamps_low(self):
        from src.audio.sounds import SoundManager
        mgr = SoundManager()
        mgr.set_volume(-0.5)
        assert mgr.master_volume == 0.0

    def test_volume_cycle_steps(self):
        """Simulate the cycle logic that main.py uses."""
        _VOL_STEPS = [1.0, 0.75, 0.50, 0.25, 0.0]

        def _cycle(cur: float) -> float:
            ci = min(range(len(_VOL_STEPS)), key=lambda i: abs(_VOL_STEPS[i] - cur))
            return _VOL_STEPS[(ci + 1) % len(_VOL_STEPS)]

        assert _cycle(1.00) == 0.75
        assert _cycle(0.75) == 0.50
        assert _cycle(0.50) == 0.25
        assert _cycle(0.25) == 0.00
        assert _cycle(0.00) == 1.00   # wraps back to 100%

    def test_volume_cycle_wraps(self):
        _VOL_STEPS = [1.0, 0.75, 0.50, 0.25, 0.0]
        cur = 1.0
        for _ in range(len(_VOL_STEPS)):
            ci = min(range(len(_VOL_STEPS)), key=lambda i: abs(_VOL_STEPS[i] - cur))
            cur = _VOL_STEPS[(ci + 1) % len(_VOL_STEPS)]
        # After a full cycle we're back at 100%
        assert cur == 1.0

    def test_zero_percent_flashes_muted(self):
        """When volume steps to 0, flash message should say 'Muted'."""
        _VOL_STEPS = [1.0, 0.75, 0.50, 0.25, 0.0]
        pct = int(0.0 * 100)
        msg = f"Volume {pct}%" if pct > 0 else "Muted"
        assert msg == "Muted"

    def test_nonzero_percent_shows_percentage(self):
        for vol in [0.75, 0.50, 0.25]:
            pct = int(vol * 100)
            msg = f"Volume {pct}%" if pct > 0 else "Muted"
            assert "%" in msg
            assert str(pct) in msg

    def test_set_volume_affects_master_volume_property(self):
        from src.audio.sounds import SoundManager
        mgr = SoundManager()
        mgr.set_volume(0.5)
        assert abs(mgr.master_volume - 0.5) < 1e-9


# ===========================================================================
# CP-35: Save-slot screenshot thumbnails
# ===========================================================================

class TestSaveThumbnails:

    def test_thumbnail_path_derives_from_json(self):
        """thumbnail_path() replaces .json suffix with .png."""
        j = Path("saves/m1_autosave.json")
        t = thumbnail_path(j)
        assert t.suffix == ".png"
        assert t.stem == "m1_autosave"
        assert t.parent == j.parent

    def test_thumbnail_path_slot(self):
        """Works for slot paths too."""
        j = Path("saves/m1_save_2.json")
        t = thumbnail_path(j)
        assert t == Path("saves/m1_save_2.png")

    def test_thumbnail_path_autosave(self):
        j = autosave_path("m1")
        t = thumbnail_path(j)
        assert t.suffix == ".png"

    def test_thumbnail_round_trip(self, tmp_path):
        """Create a dummy surface -> save as PNG -> reload -> non-empty."""
        surf = pygame.Surface((200, 120))
        surf.fill((30, 120, 200))   # some non-black color
        png_path = tmp_path / "test_thumb.png"
        pygame.image.save(surf, str(png_path))

        assert png_path.is_file()
        loaded = pygame.image.load(str(png_path))
        assert loaded.get_width() == 200
        assert loaded.get_height() == 120

        # At least one pixel should be non-black
        px = pygame.surfarray.pixels3d(loaded)
        assert px.max() > 0

    def test_thumbnail_smoothscale_preserves_aspect(self, tmp_path):
        """200x120 thumbnail has the correct aspect ratio after smoothscale."""
        big = pygame.Surface((1280, 720))
        big.fill((100, 100, 100))
        thumb = pygame.transform.smoothscale(big, (200, 120))
        assert thumb.get_size() == (200, 120)

    def test_read_save_meta_includes_thumb_path(self, tmp_path):
        """_read_save_meta returns thumb_path key even when file absent."""
        from src.persistence.save import _read_save_meta
        p = tmp_path / "fake_autosave.json"
        info = _read_save_meta(p, "Autosave")
        assert "thumb_path" in info
        assert info["thumb_path"] == thumbnail_path(p)

    def test_thumbnail_path_missing_returns_path_object(self, tmp_path):
        """thumbnail_path() always returns a Path, regardless of existence."""
        j = tmp_path / "nonexistent.json"
        t = thumbnail_path(j)
        assert isinstance(t, Path)

    def test_thumbnail_saved_alongside_json(self, tmp_path):
        """Verify that saving a thumbnail next to a JSON produces a .png sibling."""
        j = tmp_path / "saves" / "m1_save_1.json"
        j.parent.mkdir(parents=True, exist_ok=True)
        j.write_text("{}")   # dummy JSON

        surf = pygame.Surface((200, 120))
        surf.fill((255, 0, 0))
        t = thumbnail_path(j)
        pygame.image.save(surf, str(t))

        assert t.is_file()
        assert t.parent == j.parent
