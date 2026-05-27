"""
Tests for CP-16 helper functions:
  - GameState.next_actionable_unit()
  - Camera.center_on()
  - persistence.save.list_saves()
  - main._apply_difficulty()
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.engine.combat import load_damage_matrix
from src.engine.hex import Hex
from src.engine.scenario import load_scenario
from src.engine.state import Faction, GameState
from src.engine.tile import Tile, load_terrain
from src.engine.unit import Unit, load_units
from src.engine.victory import VictoryConfig, DestroyHQ
from src.persistence.save import (
    NUM_SLOTS,
    autosave_path,
    list_saves,
    save_autosave,
    save_slot,
)
from src.render.camera import Camera

SCENARIO_PATH = Path(__file__).parent.parent / "data" / "scenarios" / "m1.json"


@pytest.fixture(autouse=True)
def loaded():
    load_terrain()
    load_units()
    load_damage_matrix()


# ---------------------------------------------------------------------------
# Helper: minimal two-faction state
# ---------------------------------------------------------------------------

def _simple_state() -> GameState:
    nato  = Faction(id="NATO",  name="NATO",  color=(30, 80, 200), credits=600, oil=5, is_ai=False)
    brics = Faction(id="BRICS", name="BRICS", color=(200, 30, 30), credits=400, oil=3, is_ai=True)
    tiles: dict[Hex, Tile] = {
        Hex(0, 0): Tile(Hex(0, 0), "hq",   owner_faction="NATO"),
        Hex(5, 5): Tile(Hex(5, 5), "hq",   owner_faction="BRICS"),
        Hex(2, 2): Tile(Hex(2, 2), "city", owner_faction=None),
    }
    state = GameState(factions=[nato, brics], tiles=tiles)
    state.victory_configs["NATO"]  = VictoryConfig(
        win_conditions=[DestroyHQ("BRICS")],
        lose_conditions=[DestroyHQ("NATO")],
    )
    state.victory_configs["BRICS"] = VictoryConfig(
        win_conditions=[DestroyHQ("NATO")],
        lose_conditions=[DestroyHQ("BRICS")],
    )
    return state


# ---------------------------------------------------------------------------
# GameState.next_actionable_unit
# ---------------------------------------------------------------------------

class TestNextActionableUnit:
    def test_returns_none_when_no_units(self):
        state = _simple_state()
        assert state.next_actionable_unit("NATO") is None

    def test_returns_first_unit_when_no_current(self):
        state = _simple_state()
        u1 = Unit("nato_inf_l", "NATO", Hex(1, 0))
        u2 = Unit("nato_inf_l", "NATO", Hex(1, 1))
        state.add_unit(u1)
        state.add_unit(u2)
        result = state.next_actionable_unit("NATO")
        assert result is not None
        assert result.uid == u1.uid

    def test_cycles_to_next_after_current(self):
        state = _simple_state()
        u1 = Unit("nato_inf_l", "NATO", Hex(1, 0))
        u2 = Unit("nato_inf_l", "NATO", Hex(1, 1))
        state.add_unit(u1)
        state.add_unit(u2)
        result = state.next_actionable_unit("NATO", current_uid=u1.uid)
        assert result is not None
        assert result.uid == u2.uid

    def test_wraps_around_to_first(self):
        state = _simple_state()
        u1 = Unit("nato_inf_l", "NATO", Hex(1, 0))
        u2 = Unit("nato_inf_l", "NATO", Hex(1, 1))
        state.add_unit(u1)
        state.add_unit(u2)
        # Starting from u2 should wrap back to u1.
        result = state.next_actionable_unit("NATO", current_uid=u2.uid)
        assert result is not None
        assert result.uid == u1.uid

    def test_skips_exhausted_units(self):
        state = _simple_state()
        u1 = Unit("nato_inf_l", "NATO", Hex(1, 0))
        u2 = Unit("nato_inf_l", "NATO", Hex(1, 1))
        u1.has_moved = True
        u1.has_attacked = True          # u1 can no longer act
        state.add_unit(u1)
        state.add_unit(u2)
        result = state.next_actionable_unit("NATO")
        assert result is not None
        assert result.uid == u2.uid

    def test_returns_none_all_exhausted(self):
        state = _simple_state()
        u = Unit("nato_inf_l", "NATO", Hex(1, 0))
        u.has_moved = True
        u.has_attacked = True
        state.add_unit(u)
        assert state.next_actionable_unit("NATO") is None

    def test_unknown_current_uid_returns_first(self):
        state = _simple_state()
        u = Unit("nato_inf_l", "NATO", Hex(1, 0))
        state.add_unit(u)
        # current_uid=99999 not in list → fall back to first actionable
        result = state.next_actionable_unit("NATO", current_uid=99999)
        assert result is not None
        assert result.uid == u.uid

    def test_only_returns_own_faction(self):
        state = _simple_state()
        enemy = Unit("nato_inf_l", "BRICS", Hex(2, 0))
        state.add_unit(enemy)
        assert state.next_actionable_unit("NATO") is None

    def test_single_unit_wraps_to_itself(self):
        state = _simple_state()
        u = Unit("nato_inf_l", "NATO", Hex(1, 0))
        state.add_unit(u)
        # With only one unit, cycling past it wraps back to itself.
        result = state.next_actionable_unit("NATO", current_uid=u.uid)
        assert result is not None
        assert result.uid == u.uid


# ---------------------------------------------------------------------------
# Camera.center_on
# ---------------------------------------------------------------------------

class TestCameraCenterOn:
    def test_centers_hex_in_viewport(self):
        cam = Camera(screen_w=800, screen_h=600, hex_size=36.0)
        h = Hex(5, 3)
        cam.center_on(h)
        sx, sy = cam.hex_to_screen(h)
        # The hex should now be very close to the screen centre.
        assert abs(sx - 400) < 2
        assert abs(sy - 300) < 2

    def test_different_hex_different_offset(self):
        cam1 = Camera(screen_w=800, screen_h=600, hex_size=36.0)
        cam2 = Camera(screen_w=800, screen_h=600, hex_size=36.0)
        cam1.center_on(Hex(0, 0))
        cam2.center_on(Hex(10, 5))
        assert cam1.offset_x != cam2.offset_x or cam1.offset_y != cam2.offset_y

    def test_origin_centered(self):
        cam = Camera(screen_w=800, screen_h=600, hex_size=36.0)
        cam.center_on(Hex(0, 0))
        sx, sy = cam.hex_to_screen(Hex(0, 0))
        assert abs(sx - 400) < 2
        assert abs(sy - 300) < 2

    def test_center_on_updates_offset_x_and_y(self):
        cam = Camera(screen_w=800, screen_h=600, hex_size=36.0)
        old_ox, old_oy = cam.offset_x, cam.offset_y
        cam.center_on(Hex(3, 2))
        # offsets must have changed from the initial value
        assert (cam.offset_x, cam.offset_y) != (old_ox, old_oy)


# ---------------------------------------------------------------------------
# list_saves
# ---------------------------------------------------------------------------

class TestListSaves:
    def test_returns_one_plus_num_slots(self, tmp_path):
        saves = list_saves("test_m1", saves_dir=tmp_path)
        # 1 autosave + NUM_SLOTS manual slots
        assert len(saves) == 1 + NUM_SLOTS

    def test_all_nonexistent_at_start(self, tmp_path):
        saves = list_saves("test_m1", saves_dir=tmp_path)
        assert all(not s["exists"] for s in saves)

    def test_autosave_label_first(self, tmp_path):
        saves = list_saves("test_m1", saves_dir=tmp_path)
        assert saves[0]["label"] == "Autosave"

    def test_slot_labels(self, tmp_path):
        saves = list_saves("test_m1", saves_dir=tmp_path)
        for i, s in enumerate(saves[1:], start=1):
            assert s["label"] == f"Slot {i}"

    def test_autosave_appears_after_write(self, tmp_path):
        state, _ = load_scenario(SCENARIO_PATH)
        save_autosave(state, "test_m1", saves_dir=tmp_path)
        saves = list_saves("test_m1", saves_dir=tmp_path)
        autosave_info = saves[0]
        assert autosave_info["exists"] is True
        assert autosave_info["turn"] == state.turn_number

    def test_slot_appears_after_write(self, tmp_path):
        state, _ = load_scenario(SCENARIO_PATH)
        save_slot(state, 2, "test_m1", saves_dir=tmp_path)
        saves = list_saves("test_m1", saves_dir=tmp_path)
        slot2 = saves[2]  # autosave=0, slot1=1, slot2=2
        assert slot2["exists"] is True
        assert slot2["turn"] == state.turn_number

    def test_path_matches_expected(self, tmp_path):
        saves = list_saves("test_m1", saves_dir=tmp_path)
        assert saves[0]["path"] == autosave_path("test_m1", tmp_path)


# ---------------------------------------------------------------------------
# _apply_difficulty (imported from main via direct call to inline logic)
# ---------------------------------------------------------------------------

class TestApplyDifficulty:
    """Test the _apply_difficulty helper via main module import."""

    def _call(self, state: GameState, diff: str) -> dict:
        """Helper: call _apply_difficulty and return a copy of scenario_meta."""
        from main import _apply_difficulty
        meta: dict = {"name": "test", "personalities": {}}
        _apply_difficulty(state, meta, diff)
        return meta

    def test_normal_no_change(self):
        state = _simple_state()
        orig_credits = state.faction_by_id("BRICS").credits
        orig_oil     = state.faction_by_id("BRICS").oil
        meta = self._call(state, "normal")
        assert state.faction_by_id("BRICS").credits == orig_credits
        assert state.faction_by_id("BRICS").oil     == orig_oil
        assert "BRICS" not in meta.get("personalities", {})

    def test_hard_increases_brics_credits(self):
        state = _simple_state()
        before = state.faction_by_id("BRICS").credits
        self._call(state, "hard")
        assert state.faction_by_id("BRICS").credits == before + 400

    def test_hard_increases_brics_oil(self):
        state = _simple_state()
        before = state.faction_by_id("BRICS").oil
        self._call(state, "hard")
        assert state.faction_by_id("BRICS").oil == before + 3

    def test_hard_sets_aggressive_personality(self):
        state = _simple_state()
        meta = self._call(state, "hard")
        assert "BRICS" in meta["personalities"]
        assert meta["personalities"]["BRICS"]["name"] == "aggressive"

    def test_hard_personality_has_attack_damage_weight(self):
        state = _simple_state()
        meta = self._call(state, "hard")
        weights = meta["personalities"]["BRICS"].get("weights", {})
        assert "attack_damage" in weights
        assert weights["attack_damage"] > 1.0   # aggressive → elevated

    def test_hard_does_not_change_nato(self):
        state = _simple_state()
        nato_before_credits = state.faction_by_id("NATO").credits
        nato_before_oil     = state.faction_by_id("NATO").oil
        self._call(state, "hard")
        assert state.faction_by_id("NATO").credits == nato_before_credits
        assert state.faction_by_id("NATO").oil     == nato_before_oil

    def test_missing_brics_is_tolerated(self):
        """If state has no BRICS faction, hard mode should not raise."""
        state = _simple_state()
        # Remove BRICS faction objects (keep tiles/units minimal for test)
        state.factions = [f for f in state.factions if f.id != "BRICS"]
        meta: dict = {"personalities": {}}
        from main import _apply_difficulty
        _apply_difficulty(state, meta, "hard")   # must not raise
