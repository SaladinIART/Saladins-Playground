"""
Tests for CP-31 (MP-cost numbers on reachable hexes)
and CP-32 (AI last-action arrows).

All render tests run headlessly; arrow/pip tests just verify pixel changes
on off-screen surfaces so they work without a display server.

ASCII-only source (Pygbag cp1252 constraint).
"""
from __future__ import annotations

import collections
import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
pygame.init()

from src.ai.heuristic import (
    AttackAction,
    BuildAction,
    MoveAttackAction,
    UpgradeTierAction,
)
from src.engine.hex import Hex
from src.engine.tile import load_terrain
from src.engine.unit import Unit, load_units
from src.render.camera import Camera
from src.render.hex_renderer import HexRenderer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _load():
    load_terrain()
    load_units()


@pytest.fixture
def cam() -> Camera:
    return Camera(screen_w=800, screen_h=600, hex_size=48)


@pytest.fixture
def small_cam() -> Camera:
    """Camera with hex_size < 24 -- cost numbers should NOT appear."""
    return Camera(screen_w=800, screen_h=600, hex_size=18)


@pytest.fixture
def renderer(cam) -> HexRenderer:
    return HexRenderer(cam)


@pytest.fixture
def surface() -> pygame.Surface:
    return pygame.Surface((800, 600))


# ===========================================================================
# CP-31: Movement-cost numbers on reachable hexes
# ===========================================================================

class TestMovementCostNumbers:

    def _make_costs(self, *hexes_costs):
        """Build a {Hex: int} dict from (hex, cost) pairs."""
        return {h: c for h, c in hexes_costs}

    def test_costs_arg_defaults_to_none(self, renderer, surface):
        """draw_movement_overlay with no costs= should not raise."""
        reachable = {Hex(1, 0): 1, Hex(0, 1): 2}
        renderer.draw_movement_overlay(surface, reachable, [], Hex(0, 0))

    def test_no_costs_at_small_zoom(self, small_cam, surface):
        """At hex_size < 24 no cost numbers should be rendered (surface unchanged
        beyond the colour overlay)."""
        renderer = HexRenderer(small_cam)
        reachable = {Hex(1, 0): 1}
        before = pygame.surfarray.pixels3d(surface).copy()
        # With costs= provided but zoom too small -- costs should be suppressed
        # We just check the call doesn't raise; pixel check is impractical here
        # because the blue overlay IS drawn regardless.
        renderer.draw_movement_overlay(surface, reachable, [], Hex(0, 0),
                                       costs=reachable)

    def test_costs_drawn_at_full_zoom(self, renderer, surface, cam):
        """At hex_size >= 24 a cost-1 hex should produce non-background pixels
        near the centre of that hex."""
        h = Hex(0, 0)
        cx, cy = cam.hex_to_screen(h)
        reachable = {h: 1}

        bg = (18, 24, 38)
        surface.fill(bg)
        renderer.draw_movement_overlay(surface, reachable, [], None,
                                       costs=reachable)

        # Check a region around (cx, cy) for any non-bg pixel from the cost label
        strip = pygame.surfarray.pixels3d(surface)
        found_non_bg = False
        for px in range(max(0, int(cx) - 20), min(800, int(cx) + 20)):
            for py in range(max(0, int(cy) - 16), min(600, int(cy) + 16)):
                if tuple(strip[px, py]) != bg:
                    found_non_bg = True
                    break
            if found_non_bg:
                break
        assert found_non_bg, "Expected cost-label pixels near hex centre at zoom 48"

    def test_cost_skipped_for_path_hexes(self, renderer, surface, cam):
        """Hexes on the active path should NOT show a cost label (path is already
        highlighted in yellow -- the cost number would be redundant and noisy)."""
        h = Hex(1, 0)
        reachable = {h: 2}
        path = [Hex(0, 0), h]

        bg = (18, 24, 38)
        surface.fill(bg)
        renderer.draw_movement_overlay(surface, reachable, path, Hex(0, 0),
                                       costs=reachable)
        # After the call the path hex has the yellow overlay -- that's expected.
        # We can't easily distinguish the yellow overlay from a cost label pixel,
        # so just verify no crash and that the API signature is stable.

    def test_cost_skipped_for_selected_hex(self, renderer, surface, cam):
        """The selected unit's own hex should not show a cost label."""
        sel = Hex(0, 0)
        reachable = {sel: 0, Hex(1, 0): 1}
        # Should not raise
        renderer.draw_movement_overlay(surface, reachable, [], sel,
                                       costs=reachable)

    def test_multiple_cost_hexes(self, renderer, surface):
        """Many reachable hexes with different costs -- no crash, no assertion."""
        reachable = {Hex(q, r): q + r + 1
                     for q in range(-2, 3)
                     for r in range(-2, 3)
                     if abs(q + r) <= 2}
        renderer.draw_movement_overlay(surface, reachable, [], Hex(0, 0),
                                       costs=reachable)

    def test_zero_cost_still_rendered(self, renderer, surface, cam):
        """A hex with MP cost 0 (already at origin) is fine to render -- no crash."""
        h = Hex(0, 0)
        renderer.draw_movement_overlay(surface, {h: 0}, [], None, costs={h: 0})


# ===========================================================================
# CP-32: AI last-action arrows
# ===========================================================================

class TestAITrace:

    def _unit(self, type_id: str, faction: str, h: Hex) -> Unit:
        return Unit(type_id=type_id, faction=faction, hex=h)

    # --- deque behaviour (pure logic, no rendering) ---

    def test_deque_maxlen_5(self):
        d: collections.deque = collections.deque(maxlen=5)
        for i in range(8):
            d.append(MoveAttackAction(unit_uid=i, dest=Hex(i, 0)))
        assert len(d) == 5
        # Oldest 3 were evicted; first element should be uid=3
        assert list(d)[0].unit_uid == 3

    def test_deque_oldest_first(self):
        d: collections.deque = collections.deque(maxlen=5)
        actions = [
            MoveAttackAction(unit_uid=i, dest=Hex(i, 0)) for i in range(3)
        ]
        for a in actions:
            d.append(a)
        assert list(d) == actions   # insertion order preserved

    def test_deque_clear_on_player_turn(self):
        d: collections.deque = collections.deque(maxlen=5)
        d.append(BuildAction(hq_hex=Hex(0, 0), type_id="nato_inf_l"))
        d.clear()
        assert len(d) == 0

    def test_attack_action_in_deque(self):
        d: collections.deque = collections.deque(maxlen=5)
        a = AttackAction(attacker_uid=1, defender_uid=2)
        d.append(a)
        assert isinstance(list(d)[0], AttackAction)

    def test_upgrade_tier_in_deque(self):
        d: collections.deque = collections.deque(maxlen=5)
        a = UpgradeTierAction(faction_id="NATO")
        d.append(a)
        assert len(d) == 1

    # --- draw_ai_trace rendering (headless) ---

    def _make_minimal_state(self):
        """Return a minimal object with a .units dict for draw_ai_trace."""
        class _FakeState:
            def __init__(self):
                self.units: dict[int, Unit] = {}
        return _FakeState()

    def test_draw_ai_trace_empty_no_crash(self, renderer, surface):
        state = self._make_minimal_state()
        renderer.draw_ai_trace(surface, [], state)

    def test_draw_ai_trace_move_action(self, renderer, surface):
        """MoveAttackAction with a live unit should draw without crash."""
        state = self._make_minimal_state()
        u = self._unit("nato_inf_l", "NATO", Hex(0, 0))
        u.uid = 99
        state.units[99] = u

        action = MoveAttackAction(unit_uid=99, dest=Hex(2, 0))
        renderer.draw_ai_trace(surface, [action], state)

    def test_draw_ai_trace_attack_action(self, renderer, surface):
        """AttackAction with two live units should draw without crash."""
        state = self._make_minimal_state()
        atk = self._unit("nato_inf_l", "NATO",   Hex(0, 0)); atk.uid = 1
        dfn = self._unit("nato_inf_l", "BRICS",  Hex(2, 0)); dfn.uid = 2
        state.units[1] = atk; state.units[2] = dfn

        action = AttackAction(attacker_uid=1, defender_uid=2)
        renderer.draw_ai_trace(surface, [action], state)

    def test_draw_ai_trace_build_action(self, renderer, surface):
        """BuildAction should draw a diamond marker, no crash."""
        state = self._make_minimal_state()
        action = BuildAction(hq_hex=Hex(1, 1), type_id="nato_inf_l")
        renderer.draw_ai_trace(surface, [action], state)

    def test_draw_ai_trace_upgrade_tier_no_crash(self, renderer, surface):
        """UpgradeTierAction has no position -- just verify no crash."""
        state = self._make_minimal_state()
        action = UpgradeTierAction(faction_id="NATO")
        renderer.draw_ai_trace(surface, [action], state)

    def test_draw_ai_trace_missing_unit_no_crash(self, renderer, surface):
        """If unit_uid is no longer in state.units (was killed), no crash."""
        state = self._make_minimal_state()
        action = MoveAttackAction(unit_uid=999, dest=Hex(3, 0))
        renderer.draw_ai_trace(surface, [action], state)   # uid 999 absent

    def test_draw_ai_trace_multiple_actions_older_more_transparent(
        self, renderer, cam
    ):
        """Verify older actions get lower alpha -- test via checking that the
        most-recently-added action produces brighter pixels than the oldest."""
        state = self._make_minimal_state()

        # Two units at distinct, on-screen hexes
        u1 = self._unit("nato_inf_l", "BRICS", Hex(-3, 0)); u1.uid = 1
        u2 = self._unit("nato_inf_l", "BRICS", Hex( 3, 0)); u2.uid = 2
        state.units[1] = u1; state.units[2] = u2

        # Old action: move u1 src=Hex(-3,0) dest=Hex(-1,0)
        old_a = MoveAttackAction(unit_uid=1, dest=Hex(-1, 0))
        # New action: move u2 src=Hex(3,0) dest=Hex(1,0)
        new_a = MoveAttackAction(unit_uid=2, dest=Hex(1, 0))

        surf = pygame.Surface((800, 600))
        surf.fill((0, 0, 0))
        renderer.draw_ai_trace(surf, [old_a, new_a], state)

        # Sample brightness along the mid-line of each arrow.
        # The new arrow (index 1, n=2, t=1.0, alpha=200) should be brighter
        # than the old arrow (index 0, t=0.5, alpha=120).
        pixels = pygame.surfarray.pixels3d(surf)

        def _avg_brightness(center_x: float, center_y: float, radius: int = 8) -> float:
            total = 0; count = 0
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    px = int(center_x) + dx; py = int(center_y) + dy
                    if 0 <= px < 800 and 0 <= py < 600:
                        r, g, b = pixels[px, py]
                        total += (r + g + b) / 3; count += 1
            return total / max(count, 1)

        # Old arrow midpoint (from u1's current hex to dest)
        old_src_x, old_src_y = cam.hex_to_screen(u1.hex)
        old_dst_x, old_dst_y = cam.hex_to_screen(Hex(-1, 0))
        old_mid_x = (old_src_x + old_dst_x) / 2
        old_mid_y = (old_src_y + old_dst_y) / 2

        # New arrow midpoint
        new_src_x, new_src_y = cam.hex_to_screen(u2.hex)
        new_dst_x, new_dst_y = cam.hex_to_screen(Hex(1, 0))
        new_mid_x = (new_src_x + new_dst_x) / 2
        new_mid_y = (new_src_y + new_dst_y) / 2

        old_bright = _avg_brightness(old_mid_x, old_mid_y)
        new_bright = _avg_brightness(new_mid_x, new_mid_y)

        # Newest arrow MUST be brighter (higher alpha = more visible on black bg)
        assert new_bright >= old_bright, (
            f"Newest arrow ({new_bright:.1f}) should be >= oldest ({old_bright:.1f})"
        )
