"""
CP-17 tests — BRICS unit roster + faction filtering + m1 integration.

Covers:
  - All BRICS unit types load and validate correctly.
  - buildable_units() and all_displayable_units() filter by faction.
  - m1 scenario uses proper brics_* unit type IDs.
  - BRICS AI builds BRICS units (not NATO units).
  - BRICS has at least one can_capture engineer per tier gate.
  - Flagship units (swarm_drone, hypersonic) have correct properties.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.engine.combat import load_damage_matrix
from src.engine.hex import Hex
from src.engine.scenario import load_scenario
from src.engine.state import Faction, GameState
from src.engine.tech import all_displayable_units, buildable_units
from src.engine.tile import Tile, load_terrain
from src.engine.unit import Unit, all_units, get as get_unit, load_units

SCENARIO_PATH = Path(__file__).parent.parent / "data" / "scenarios" / "m1.json"

BRICS_IDS = {
    "brics_inf_l", "brics_engineer", "brics_recon", "brics_aa_l",
    "brics_vehicle_l", "brics_inf_m", "brics_vehicle_m",
    "brics_artillery_l", "brics_swarm_drone", "brics_hypersonic",
}

NATO_IDS = {
    "nato_inf_l", "nato_engineer", "nato_recon", "nato_aa_l",
    "nato_vehicle_l", "nato_inf_m", "nato_vehicle_m",
    "nato_artillery_l", "nato_jet_l",
}


@pytest.fixture(autouse=True)
def loaded():
    load_terrain()
    load_units()
    load_damage_matrix()


# ---------------------------------------------------------------------------
# Unit registry
# ---------------------------------------------------------------------------

class TestBricsRosterLoads:
    def test_all_brics_ids_present(self):
        registry = all_units()
        for uid in BRICS_IDS:
            assert uid in registry, f"Missing unit type: {uid}"

    def test_brics_count(self):
        brics_units = [ut for ut in all_units().values() if ut.faction == "BRICS"]
        assert len(brics_units) == 10

    def test_nato_count_unchanged(self):
        nato_units = [ut for ut in all_units().values() if ut.faction == "NATO"]
        assert len(nato_units) == 9

    def test_all_brics_have_valid_unit_class(self):
        from src.engine.unit import VALID_UNIT_CLASSES
        for uid in BRICS_IDS:
            ut = get_unit(uid)
            assert ut.unit_class in VALID_UNIT_CLASSES, f"{uid}.unit_class invalid"

    def test_all_brics_have_valid_move_category(self):
        from src.engine.unit import VALID_MOVE_CATEGORIES
        for uid in BRICS_IDS:
            ut = get_unit(uid)
            assert ut.move_category in VALID_MOVE_CATEGORIES

    def test_all_brics_faction_field(self):
        for uid in BRICS_IDS:
            assert get_unit(uid).faction == "BRICS"

    def test_brics_tiers_span_1_to_3(self):
        tiers = {get_unit(uid).tier for uid in BRICS_IDS}
        assert tiers == {1, 2, 3}

    def test_brics_t1_units(self):
        t1 = [get_unit(uid) for uid in BRICS_IDS if get_unit(uid).tier == 1]
        assert len(t1) == 5

    def test_brics_t2_units(self):
        t2 = [get_unit(uid) for uid in BRICS_IDS if get_unit(uid).tier == 2]
        assert len(t2) == 4

    def test_brics_t3_units(self):
        t3 = [get_unit(uid) for uid in BRICS_IDS if get_unit(uid).tier == 3]
        assert len(t3) == 1

    def test_brics_hp_all_10(self):
        for uid in BRICS_IDS:
            assert get_unit(uid).hp == 10, f"{uid}.hp != 10"

    def test_brics_costs_positive(self):
        for uid in BRICS_IDS:
            ut = get_unit(uid)
            assert ut.cost_credits > 0, f"{uid}.cost_credits == 0"


# ---------------------------------------------------------------------------
# Cheaper-than-NATO invariant
# ---------------------------------------------------------------------------

class TestBricsCheaperThanNato:
    """BRICS equivalent units must cost ≤ NATO equivalents at the same tier."""

    def test_infantry_t1_cheaper(self):
        assert get_unit("brics_inf_l").cost_credits < get_unit("nato_inf_l").cost_credits

    def test_engineer_t1_cheaper(self):
        assert get_unit("brics_engineer").cost_credits <= get_unit("nato_engineer").cost_credits

    def test_recon_t1_cheaper(self):
        assert get_unit("brics_recon").cost_credits < get_unit("nato_recon").cost_credits

    def test_aa_t1_cheaper(self):
        assert get_unit("brics_aa_l").cost_credits < get_unit("nato_aa_l").cost_credits

    def test_vehicle_t1_cheaper(self):
        assert get_unit("brics_vehicle_l").cost_credits < get_unit("nato_vehicle_l").cost_credits

    def test_vehicle_m_cheaper(self):
        assert get_unit("brics_vehicle_m").cost_credits < get_unit("nato_vehicle_m").cost_credits

    def test_artillery_cheaper(self):
        assert get_unit("brics_artillery_l").cost_credits < get_unit("nato_artillery_l").cost_credits


# ---------------------------------------------------------------------------
# Engineers / capture
# ---------------------------------------------------------------------------

class TestBricsEngineers:
    def test_brics_engineer_can_capture(self):
        assert get_unit("brics_engineer").can_capture is True

    def test_brics_inf_m_can_capture(self):
        assert get_unit("brics_inf_m").can_capture is True

    def test_brics_inf_l_cannot_capture(self):
        assert get_unit("brics_inf_l").can_capture is False

    def test_brics_vehicle_cannot_capture(self):
        assert get_unit("brics_vehicle_l").can_capture is False


# ---------------------------------------------------------------------------
# Flagships
# ---------------------------------------------------------------------------

class TestBricsFlagships:
    def test_swarm_drone_is_helicopter(self):
        assert get_unit("brics_swarm_drone").unit_class == "helicopter"

    def test_swarm_drone_is_flying(self):
        assert get_unit("brics_swarm_drone").flying is True

    def test_swarm_drone_move_category_air(self):
        assert get_unit("brics_swarm_drone").move_category == "air"

    def test_swarm_drone_has_range(self):
        ut = get_unit("brics_swarm_drone")
        assert ut.range_max >= 2

    def test_swarm_drone_tier2(self):
        assert get_unit("brics_swarm_drone").tier == 2

    def test_hypersonic_is_artillery(self):
        assert get_unit("brics_hypersonic").unit_class == "artillery"

    def test_hypersonic_tier3(self):
        assert get_unit("brics_hypersonic").tier == 3

    def test_hypersonic_long_range(self):
        ut = get_unit("brics_hypersonic")
        assert ut.range_max >= 6

    def test_hypersonic_indirect_only(self):
        # range_min > 1 means it cannot hit adjacent hexes
        assert get_unit("brics_hypersonic").range_min >= 4

    def test_hypersonic_high_atk(self):
        assert get_unit("brics_hypersonic").atk >= 8


# ---------------------------------------------------------------------------
# tech.buildable_units — faction filtering
# ---------------------------------------------------------------------------

class TestBuildableUnitsFiltering:
    def _nato(self, tier: int = 1) -> Faction:
        return Faction(id="NATO", name="NATO", color=(0, 0, 200), tier=tier)

    def _brics(self, tier: int = 1) -> Faction:
        return Faction(id="BRICS", name="BRICS", color=(200, 0, 0), tier=tier)

    def test_nato_t1_no_brics_units(self):
        units = buildable_units(self._nato(1))
        ids = {ut.id for ut in units}
        assert ids.isdisjoint(BRICS_IDS)

    def test_brics_t1_no_nato_units(self):
        units = buildable_units(self._brics(1))
        ids = {ut.id for ut in units}
        assert ids.isdisjoint(NATO_IDS)

    def test_brics_t1_only_tier1(self):
        units = buildable_units(self._brics(1))
        assert all(ut.tier == 1 for ut in units)

    def test_brics_t2_includes_t1_and_t2(self):
        units = buildable_units(self._brics(2))
        tiers = {ut.tier for ut in units}
        assert 1 in tiers and 2 in tiers

    def test_brics_t2_no_t3(self):
        units = buildable_units(self._brics(2))
        assert all(ut.tier <= 2 for ut in units)

    def test_brics_t3_includes_hypersonic(self):
        units = buildable_units(self._brics(3))
        ids = {ut.id for ut in units}
        assert "brics_hypersonic" in ids

    def test_nato_t2_includes_jet(self):
        units = buildable_units(self._nato(2))
        ids = {ut.id for ut in units}
        assert "nato_jet_l" in ids

    def test_buildable_sorted_by_tier_then_cost(self):
        units = buildable_units(self._brics(3))
        keys = [(ut.tier, ut.cost_credits) for ut in units]
        assert keys == sorted(keys)

    def test_brics_has_engineer_in_buildable_t1(self):
        units = buildable_units(self._brics(1))
        assert any(ut.can_capture for ut in units)


# ---------------------------------------------------------------------------
# tech.all_displayable_units — faction separation
# ---------------------------------------------------------------------------

class TestAllDisplayableUnits:
    def test_nato_only(self):
        units = all_displayable_units("NATO")
        assert all(ut.faction == "NATO" for ut in units)

    def test_brics_only(self):
        units = all_displayable_units("BRICS")
        assert all(ut.faction == "BRICS" for ut in units)

    def test_nato_brics_disjoint(self):
        nato_ids  = {ut.id for ut in all_displayable_units("NATO")}
        brics_ids = {ut.id for ut in all_displayable_units("BRICS")}
        assert nato_ids.isdisjoint(brics_ids)

    def test_brics_displayable_includes_t3(self):
        """Even T3 flagship is included regardless of current tier."""
        units = all_displayable_units("BRICS")
        ids = {ut.id for ut in units}
        assert "brics_hypersonic" in ids

    def test_sorted_by_tier_then_cost(self):
        units = all_displayable_units("BRICS")
        keys = [(ut.tier, ut.cost_credits) for ut in units]
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# m1 scenario integration
# ---------------------------------------------------------------------------

class TestM1UsesBricsTypes:
    @pytest.fixture
    def m1(self):
        return load_scenario(SCENARIO_PATH)

    def test_brics_units_are_brics_typed(self, m1):
        state, _ = m1
        brics_units = state.units_of("BRICS")
        for u in brics_units:
            assert u.type_id.startswith("brics_"), (
                f"BRICS unit has nato type: {u.type_id}"
            )

    def test_brics_has_engineer(self, m1):
        state, _ = m1
        engineers = [u for u in state.units_of("BRICS") if u.unit_type.can_capture]
        assert len(engineers) >= 1

    def test_brics_starting_units_count(self, m1):
        state, _ = m1
        assert len(state.units_of("BRICS")) == 3

    def test_no_nato_types_on_brics_faction(self, m1):
        state, _ = m1
        for u in state.units_of("BRICS"):
            assert u.type_id not in NATO_IDS


# ---------------------------------------------------------------------------
# AI builds BRICS units (not NATO)
# ---------------------------------------------------------------------------

class TestAIBuildsBricsUnits:
    def _brics_state_with_hq(self) -> GameState:
        """Two-faction state with BRICS HQ surrounded by empty plains."""
        nato  = Faction(id="NATO",  name="NATO",  color=(0,0,200),
                        credits=0, oil=0, tier=1, is_ai=False)
        brics = Faction(id="BRICS", name="BRICS", color=(200,0,0),
                        credits=3000, oil=20, tier=2, is_ai=True)
        tiles: dict[Hex, Tile] = {
            Hex(0, 0):  Tile(Hex(0, 0),  "hq",    owner_faction="BRICS"),
            Hex(1, 0):  Tile(Hex(1, 0),  "plain"),
            Hex(1, -1): Tile(Hex(1, -1), "plain"),
            Hex(0, -1): Tile(Hex(0, -1), "plain"),
            Hex(-1, 0): Tile(Hex(-1, 0), "plain"),
            Hex(-1, 1): Tile(Hex(-1, 1), "plain"),
            Hex(0, 1):  Tile(Hex(0, 1),  "plain"),
        }
        return GameState(factions=[nato, brics], tiles=tiles)

    def test_ai_build_action_uses_brics_type(self):
        from src.ai.heuristic import enumerate_actions, BuildAction
        state = self._brics_state_with_hq()
        actions = enumerate_actions(state, "BRICS")
        build_actions = [a for a in actions if isinstance(a, BuildAction)]
        assert build_actions, "AI enumerated no build actions"
        for ba in build_actions:
            assert ba.type_id.startswith("brics_"), (
                f"AI trying to build non-BRICS unit: {ba.type_id}"
            )

    def test_ai_does_not_enumerate_nato_builds_for_brics(self):
        from src.ai.heuristic import enumerate_actions, BuildAction
        state = self._brics_state_with_hq()
        actions = enumerate_actions(state, "BRICS")
        nato_builds = [
            a for a in actions
            if isinstance(a, BuildAction) and a.type_id.startswith("nato_")
        ]
        assert nato_builds == []
