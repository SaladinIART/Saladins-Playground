"""
Tech tier system — constants and helpers for build menus, tier upgrades, and
unit spawning adjacent to an HQ.

Tier flow (v0 — instant, no build queue)
-----------------------------------------
  Tier 1  available from turn 1.
  Tier 2  unlock by paying TIER_UPGRADE_COSTS[2] credits (instant).
  Tier 3  unlock by paying TIER_UPGRADE_COSTS[3] credits (instant).

TODO CP-11 note: production queue (multi-turn Research HQ) is deferred to a later
  polish pass; "instant" was the deliberate v0 simplification.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.engine.hex import Hex
    from src.engine.state import Faction, GameState
    from src.engine.unit import UnitType

# Credits required to unlock each tier (oil cost = 0).
TIER_UPGRADE_COSTS: dict[int, int] = {
    2: 1000,
    3: 2500,
}

MAX_TIER = 3


# ---------------------------------------------------------------------------
# Build-menu helpers
# ---------------------------------------------------------------------------

def buildable_units(faction: "Faction") -> list["UnitType"]:
    """
    Unit types the faction may currently build.

    Filters by:
    - ``unit_type.faction == faction.id``  (own faction's units only)
    - ``unit_type.tier   <= faction.tier`` (current tech level)

    Sorted by (tier, cost_credits) for consistent menu ordering.
    """
    from src.engine.unit import all_units
    available = [
        ut for ut in all_units().values()
        if ut.faction == faction.id and ut.tier <= faction.tier
    ]
    return sorted(available, key=lambda ut: (ut.tier, ut.cost_credits))


def all_displayable_units(faction_id: str) -> list["UnitType"]:
    """
    All unit types belonging to *faction_id*, sorted by (tier, cost_credits).

    Used to populate the full build menu (including tier-locked rows).
    Pass ``faction.id`` from the active faction.
    """
    from src.engine.unit import all_units
    owned = [ut for ut in all_units().values() if ut.faction == faction_id]
    return sorted(owned, key=lambda ut: (ut.tier, ut.cost_credits))


# ---------------------------------------------------------------------------
# Tier upgrade helpers
# ---------------------------------------------------------------------------

def can_upgrade_tier(faction: "Faction") -> bool:
    """True when the faction's tier can still be raised."""
    return faction.tier < MAX_TIER


def next_tier_cost(faction: "Faction") -> int:
    """Credits needed to unlock faction.tier + 1.  Returns 0 at max tier."""
    return TIER_UPGRADE_COSTS.get(faction.tier + 1, 0)


# ---------------------------------------------------------------------------
# Spawn-hex finder
# ---------------------------------------------------------------------------

def find_spawn_hex(
    state: "GameState",
    hq_hex: "Hex",
    unit_type: "UnitType",
) -> "Hex | None":
    """
    Return the first empty, passable neighbor of *hq_hex* suitable for
    spawning *unit_type*.  Flying units (air move_category) ignore terrain
    passability.  Returns None if no valid hex is available.
    """
    from src.engine.hex import neighbours

    for neighbor in neighbours(hq_hex):
        if neighbor not in state.tiles:
            continue
        if unit_type.move_category != "air":
            terrain = state.tiles[neighbor].terrain
            if terrain.get_move_cost(unit_type.move_category) is None:
                continue  # impassable for this move category
        if state.unit_at(neighbor) is None:
            return neighbor
    return None
