"""Tests for src/engine/scenario.py — loading m1.json into a GameState."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.engine.combat import load_damage_matrix
from src.engine.hex import Hex
from src.engine.scenario import load_scenario
from src.engine.tile import load_terrain
from src.engine.unit import load_units
from src.engine.victory import Outcome


SCENARIO_PATH = Path(__file__).parent.parent / "data" / "scenarios" / "m1.json"


@pytest.fixture(autouse=True)
def loaded():
    load_terrain()
    load_units()
    load_damage_matrix()


# ---------------------------------------------------------------------------
# Fixture: loaded state + meta from m1.json
# ---------------------------------------------------------------------------

@pytest.fixture()
def m1():
    state, meta = load_scenario(SCENARIO_PATH)
    return state, meta


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

def test_meta_name(m1):
    _, meta = m1
    assert "Mission 1" in meta["name"]


def test_meta_has_brics_personality(m1):
    _, meta = m1
    assert "BRICS" in meta["personalities"]


# ---------------------------------------------------------------------------
# Factions
# ---------------------------------------------------------------------------

def test_factions_loaded(m1):
    state, _ = m1
    ids = {f.id for f in state.factions}
    assert "NATO" in ids
    assert "BRICS" in ids


def test_nato_is_human(m1):
    state, _ = m1
    nato = state.faction_by_id("NATO")
    assert nato.is_ai is False


def test_brics_is_ai(m1):
    state, _ = m1
    brics = state.faction_by_id("BRICS")
    assert brics.is_ai is True


def test_starting_credits(m1):
    state, _ = m1
    assert state.faction_by_id("NATO").credits == 600
    assert state.faction_by_id("BRICS").credits == 600


def test_starting_oil(m1):
    state, _ = m1
    assert state.faction_by_id("NATO").oil >= 0
    assert state.faction_by_id("BRICS").oil >= 0


def test_starting_tier_is_one(m1):
    state, _ = m1
    assert state.faction_by_id("NATO").tier == 1
    assert state.faction_by_id("BRICS").tier == 1


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------

def test_nato_hq_exists(m1):
    state, _ = m1
    hq = state.hq_of("NATO")
    assert hq is not None
    assert hq.terrain.is_hq
    assert hq.owner_faction == "NATO"


def test_brics_hq_exists(m1):
    state, _ = m1
    hq = state.hq_of("BRICS")
    assert hq is not None
    assert hq.terrain.is_hq
    assert hq.owner_faction == "BRICS"


def test_neutral_city_exists(m1):
    state, _ = m1
    # At least one capturable city with no owner
    neutral_cities = [
        t for t in state.tiles.values()
        if t.terrain.capturable and t.terrain_id == "city" and t.owner_faction is None
    ]
    assert len(neutral_cities) >= 1


def test_neutral_oil_well_exists(m1):
    state, _ = m1
    neutral_oils = [
        t for t in state.tiles.values()
        if t.terrain_id == "oil_well" and t.owner_faction is None
    ]
    assert len(neutral_oils) >= 1


def test_map_has_river_and_bridge(m1):
    state, _ = m1
    river_tiles = [t for t in state.tiles.values() if t.terrain_id == "river"]
    bridge_tiles = [t for t in state.tiles.values() if t.terrain_id == "bridge"]
    assert len(river_tiles) >= 2
    assert len(bridge_tiles) >= 1


def test_map_has_forest(m1):
    state, _ = m1
    forest = [t for t in state.tiles.values() if t.terrain_id == "forest"]
    assert len(forest) >= 2


def test_map_fills_grid(m1):
    state, _ = m1
    # Default terrain fills width × height = 21 × 15 = 315 minimum
    assert len(state.tiles) == 21 * 15


def test_plain_default_for_unlisted(m1):
    # A hex not listed in tiles should be "plain"
    state, _ = m1
    # Hex (0, 0) is not listed as anything special
    t = state.tiles.get(Hex(0, 0))
    assert t is not None
    assert t.terrain_id == "plain"


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

def test_nato_has_three_starting_units(m1):
    state, _ = m1
    assert len(state.units_of("NATO")) == 3


def test_brics_has_three_starting_units(m1):
    state, _ = m1
    assert len(state.units_of("BRICS")) == 3


def test_nato_has_engineer(m1):
    state, _ = m1
    engineers = [u for u in state.units_of("NATO") if u.unit_type.can_capture]
    assert len(engineers) >= 1


def test_brics_has_engineer(m1):
    state, _ = m1
    engineers = [u for u in state.units_of("BRICS") if u.unit_type.can_capture]
    assert len(engineers) >= 1


def test_starting_units_not_exhausted(m1):
    """Units placed by scenario loader should be able to act on turn 1."""
    state, _ = m1
    for u in state.units_of("NATO"):
        assert u.can_act(), f"{u.unit_type.name} should be able to act turn 1"


def test_units_in_bounds(m1):
    state, _ = m1
    for u in state.units.values():
        assert u.hex in state.tiles, f"Unit at {u.hex} is outside the map"


def test_no_two_units_on_same_hex(m1):
    state, _ = m1
    occupied: set[Hex] = set()
    for u in state.units.values():
        assert u.hex not in occupied, f"Two units share hex {u.hex}"
        occupied.add(u.hex)


# ---------------------------------------------------------------------------
# Victory configuration
# ---------------------------------------------------------------------------

def test_victory_configs_installed(m1):
    state, _ = m1
    assert "NATO" in state.victory_configs
    assert "BRICS" in state.victory_configs


def test_nato_wins_by_destroying_brics_hq(m1):
    state, _ = m1
    # Remove BRICS HQ ownership → NATO should win
    brics_hq = state.hq_of("BRICS")
    state.tiles[brics_hq.hex].owner_faction = None
    cfg = state.victory_configs["NATO"]
    assert cfg.evaluate(state, "NATO") == Outcome.WON


def test_nato_loses_by_losing_own_hq(m1):
    state, _ = m1
    nato_hq = state.hq_of("NATO")
    state.tiles[nato_hq.hex].owner_faction = None
    cfg = state.victory_configs["NATO"]
    assert cfg.evaluate(state, "NATO") == Outcome.LOST


def test_all_outcomes_pending_at_start(m1):
    state, _ = m1
    state.evaluate_victory()
    for fid in ("NATO", "BRICS"):
        assert state.outcomes.get(fid, Outcome.PENDING) == Outcome.PENDING


# ---------------------------------------------------------------------------
# Comment-only entries (robustness)
# ---------------------------------------------------------------------------

def test_comment_tiles_are_skipped():
    """The loader should silently ignore dicts that have no 'hex' key."""
    raw = {
        "factions": [
            {"id": "A", "name": "A", "color": [1, 2, 3], "credits": 0, "oil": 0, "is_ai": False}
        ],
        "map": {
            "width": 5,
            "height": 5,
            "default_terrain": "plain",
            "tiles": [
                {"comment": "just a note"},
                {"hex": [2, 2], "terrain": "city"}
            ]
        },
        "units": [{"comment": "skip me"}],
        "victory": {}
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(raw, fh)
        tmp = fh.name

    state, meta = load_scenario(tmp)
    assert state.tiles[Hex(2, 2)].terrain_id == "city"
    assert len(state.units) == 0      # comment unit was skipped


def test_load_from_m1_path_does_not_raise():
    """Smoke test: loading the actual m1.json must not throw."""
    load_scenario(SCENARIO_PATH)


# ---------------------------------------------------------------------------
# End-to-end: scenario survives an end_turn cycle
# ---------------------------------------------------------------------------

def test_end_turn_does_not_crash(m1):
    state, _ = m1
    state.end_turn()      # NATO → BRICS turn, income + captures process
    assert state.active_faction.id == "BRICS"


def test_income_accrues_on_turn_start(m1):
    state, _ = m1
    credits_before = state.faction_by_id("BRICS").credits
    state.end_turn()      # ends NATO turn, starts BRICS turn → BRICS gets income
    assert state.faction_by_id("BRICS").credits > credits_before
