"""Tests for tech.py + GameState.build_unit / GameState.upgrade_tier."""
from __future__ import annotations

import pytest

from src.engine.hex import Hex
from src.engine.state import Faction, GameState
from src.engine.tech import (
    MAX_TIER,
    TIER_UPGRADE_COSTS,
    all_displayable_units,
    buildable_units,
    can_upgrade_tier,
    find_spawn_hex,
    next_tier_cost,
)
from src.engine.tile import Tile, load_terrain
from src.engine.unit import Unit, get as get_unit, load_units


@pytest.fixture(autouse=True)
def loaded():
    load_terrain()
    load_units()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(credits: int = 2000, oil: int = 10, tier: int = 1) -> GameState:
    """
    Minimal 2-faction state with an HQ at (0,0) surrounded by plain tiles.
    NATO is faction[0] (active), BRICS is faction[1].
    """
    nato  = Faction(id="NATO",  name="NATO",  color=(0,0,200),   credits=credits, oil=oil,  tier=tier, is_ai=False)
    brics = Faction(id="BRICS", name="BRICS", color=(200,0,0),   credits=500,     oil=5,    tier=1)

    # HQ tile + all 6 neighbours as plain tiles so spawn logic has options.
    tiles: dict[Hex, Tile] = {
        Hex(0, 0): Tile(Hex(0, 0),  "hq",    owner_faction="NATO"),
        Hex(1, 0): Tile(Hex(1, 0),  "plain"),
        Hex(1,-1): Tile(Hex(1, -1), "plain"),
        Hex(0,-1): Tile(Hex(0, -1), "plain"),
        Hex(-1,0): Tile(Hex(-1, 0), "plain"),
        Hex(-1,1): Tile(Hex(-1, 1), "plain"),
        Hex(0, 1): Tile(Hex(0,  1), "plain"),
    }
    return GameState(factions=[nato, brics], tiles=tiles)


# ---------------------------------------------------------------------------
# buildable_units
# ---------------------------------------------------------------------------

def test_buildable_units_tier1_shows_only_t1():
    faction = Faction(id="NATO", name="NATO", color=(0,0,0), tier=1)
    units = buildable_units(faction)
    assert all(ut.tier == 1 for ut in units)
    assert len(units) >= 1


def test_buildable_units_tier2_includes_t1_and_t2():
    faction = Faction(id="NATO", name="NATO", color=(0,0,0), tier=2)
    units = buildable_units(faction)
    tiers = {ut.tier for ut in units}
    assert 1 in tiers
    assert 2 in tiers
    assert 3 not in tiers


def test_buildable_units_sorted_by_tier_then_cost():
    faction = Faction(id="NATO", name="NATO", color=(0,0,0), tier=2)
    units = buildable_units(faction)
    keys = [(ut.tier, ut.cost_credits) for ut in units]
    assert keys == sorted(keys)


def test_all_displayable_units_includes_locked():
    """all_displayable_units returns every unit for a faction regardless of tier."""
    all_u = all_displayable_units("NATO")
    tiers_present = {ut.tier for ut in all_u}
    assert 1 in tiers_present
    assert 2 in tiers_present          # T2 units exist in NATO roster
    assert all(ut.faction == "NATO" for ut in all_u)


def test_all_displayable_units_brics():
    """all_displayable_units for BRICS returns only BRICS units."""
    brics_u = all_displayable_units("BRICS")
    assert len(brics_u) >= 1
    assert all(ut.faction == "BRICS" for ut in brics_u)


def test_all_displayable_units_factions_are_separate():
    """NATO and BRICS unit lists must not overlap."""
    nato_ids  = {ut.id for ut in all_displayable_units("NATO")}
    brics_ids = {ut.id for ut in all_displayable_units("BRICS")}
    assert nato_ids.isdisjoint(brics_ids)


# ---------------------------------------------------------------------------
# Tier-upgrade helpers
# ---------------------------------------------------------------------------

def test_can_upgrade_tier_below_max():
    f1 = Faction(id="X", name="X", color=(0,0,0), tier=1)
    f2 = Faction(id="X", name="X", color=(0,0,0), tier=2)
    assert can_upgrade_tier(f1)
    assert can_upgrade_tier(f2)


def test_cannot_upgrade_tier_at_max():
    f = Faction(id="X", name="X", color=(0,0,0), tier=MAX_TIER)
    assert not can_upgrade_tier(f)


def test_next_tier_cost_values():
    f1 = Faction(id="X", name="X", color=(0,0,0), tier=1)
    f2 = Faction(id="X", name="X", color=(0,0,0), tier=2)
    f3 = Faction(id="X", name="X", color=(0,0,0), tier=3)
    assert next_tier_cost(f1) == TIER_UPGRADE_COSTS[2]
    assert next_tier_cost(f2) == TIER_UPGRADE_COSTS[3]
    assert next_tier_cost(f3) == 0   # already max


# ---------------------------------------------------------------------------
# find_spawn_hex
# ---------------------------------------------------------------------------

def test_find_spawn_hex_returns_empty_neighbour():
    s = _make_state()
    ut = get_unit("nato_inf_l")
    result = find_spawn_hex(s, Hex(0, 0), ut)
    assert result is not None
    assert s.unit_at(result) is None


def test_find_spawn_hex_skips_occupied():
    s = _make_state()
    ut = get_unit("nato_inf_l")
    # Fill every neighbour except Hex(0,1).
    for h in [Hex(1,0), Hex(1,-1), Hex(0,-1), Hex(-1,0), Hex(-1,1)]:
        s.add_unit(Unit("nato_inf_l", "NATO", h))
    result = find_spawn_hex(s, Hex(0, 0), ut)
    assert result == Hex(0, 1)


def test_find_spawn_hex_none_if_all_occupied():
    s = _make_state()
    ut = get_unit("nato_inf_l")
    for h in [Hex(1,0), Hex(1,-1), Hex(0,-1), Hex(-1,0), Hex(-1,1), Hex(0,1)]:
        s.add_unit(Unit("nato_inf_l", "NATO", h))
    assert find_spawn_hex(s, Hex(0, 0), ut) is None


def test_find_spawn_hex_skips_impassable_for_tracked():
    """Tracked unit cannot spawn on a mountain neighbour."""
    s = _make_state()
    # Replace all neighbours with mountains (impassable for tracked).
    for h in [Hex(1,0), Hex(1,-1), Hex(0,-1), Hex(-1,0), Hex(-1,1), Hex(0,1)]:
        s.tiles[h] = Tile(h, "mountain")
    ut = get_unit("nato_vehicle_l")   # tracked
    assert find_spawn_hex(s, Hex(0, 0), ut) is None


def test_find_spawn_hex_flying_ignores_impassable():
    """Flying unit (air) can spawn even if all neighbours are mountains."""
    s = _make_state()
    for h in [Hex(1,0), Hex(1,-1), Hex(0,-1), Hex(-1,0), Hex(-1,1), Hex(0,1)]:
        s.tiles[h] = Tile(h, "mountain")
    ut = get_unit("nato_jet_l")   # air
    result = find_spawn_hex(s, Hex(0, 0), ut)
    assert result is not None


# ---------------------------------------------------------------------------
# GameState.build_unit
# ---------------------------------------------------------------------------

def test_build_unit_spawns_adjacent_and_deducts_cost():
    s = _make_state(credits=500, oil=0)
    nato = s.faction_by_id("NATO")
    credits_before = nato.credits
    unit = s.build_unit("nato_inf_l", "NATO", Hex(0, 0))
    assert unit.is_alive()
    assert unit.hex in [Hex(1,0), Hex(1,-1), Hex(0,-1), Hex(-1,0), Hex(-1,1), Hex(0,1)]
    assert nato.credits == credits_before - get_unit("nato_inf_l").cost_credits


def test_build_unit_new_unit_is_exhausted():
    s = _make_state()
    u = s.build_unit("nato_inf_l", "NATO", Hex(0, 0))
    assert u.has_moved
    assert u.has_attacked
    assert not u.can_act()


def test_build_unit_unit_in_state():
    s = _make_state()
    u = s.build_unit("nato_inf_l", "NATO", Hex(0, 0))
    assert u.uid in s.units


def test_build_unit_fails_tier_locked():
    s = _make_state(tier=1, credits=3000)
    with pytest.raises(ValueError, match="tier"):
        s.build_unit("nato_vehicle_m", "NATO", Hex(0, 0))   # T2 unit


def test_build_unit_fails_cannot_afford():
    s = _make_state(credits=50, oil=0)
    with pytest.raises(ValueError, match="afford"):
        s.build_unit("nato_inf_l", "NATO", Hex(0, 0))   # costs 300cr


def test_build_unit_fails_no_space():
    s = _make_state()
    for h in [Hex(1,0), Hex(1,-1), Hex(0,-1), Hex(-1,0), Hex(-1,1), Hex(0,1)]:
        s.add_unit(Unit("nato_inf_l", "NATO", h))
    with pytest.raises(ValueError, match="No empty"):
        s.build_unit("nato_inf_l", "NATO", Hex(0, 0))


# ---------------------------------------------------------------------------
# GameState.upgrade_tier
# ---------------------------------------------------------------------------

def test_upgrade_tier_succeeds():
    s = _make_state(credits=TIER_UPGRADE_COSTS[2])
    nato = s.faction_by_id("NATO")
    s.upgrade_tier("NATO")
    assert nato.tier == 2
    assert nato.credits == 0


def test_upgrade_tier_twice():
    s = _make_state(credits=TIER_UPGRADE_COSTS[2] + TIER_UPGRADE_COSTS[3])
    s.upgrade_tier("NATO")
    s.upgrade_tier("NATO")
    assert s.faction_by_id("NATO").tier == 3


def test_upgrade_tier_fails_cannot_afford():
    s = _make_state(credits=0)
    with pytest.raises(ValueError, match="afford"):
        s.upgrade_tier("NATO")


def test_upgrade_tier_fails_at_max():
    s = _make_state(tier=MAX_TIER, credits=99999)
    with pytest.raises(ValueError, match="max tier"):
        s.upgrade_tier("NATO")
