"""
Threat evaluation utilities for the heuristic AI.

Given a friendly unit and a hex it might occupy, returns the sum of damage
all in-range enemies could plausibly inflict — used by the move-scorer to
penalise walking into danger.

By default the eval is *fog-blind* (all enemies counted): the v0 AI plays
slightly cheaty so it makes coherent moves on a small test map.  Pass
``viewer_fid`` to restrict to enemies visible to that faction (proper fog).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from src.engine.combat import predict_damage
from src.engine.fog import can_faction_see_unit
from src.engine.hex import distance

if TYPE_CHECKING:
    from src.engine.hex import Hex
    from src.engine.state import GameState
    from src.engine.unit import Unit


def threat_to_unit_at(
    state: "GameState",
    unit: "Unit",
    hex_pos: "Hex",
    *,
    viewer_fid: Optional[str] = None,
) -> int:
    """
    Sum of ``predict_damage`` from every enemy that could reach *unit* at
    ``hex_pos``.  Briefly mutates ``unit.hex`` so terrain bonus + range
    calculations resolve against the prospective position; restored on exit.

    If ``viewer_fid`` is provided, enemies invisible to that faction are
    excluded (fog enforcement).  Omit for fog-blind eval (cheaty AI).
    """
    orig = unit.hex
    unit.hex = hex_pos
    try:
        total = 0
        for enemy in state.enemy_units_of(unit.faction):
            if viewer_fid is not None and not can_faction_see_unit(state, viewer_fid, enemy):
                continue
            dist = distance(enemy.hex, unit.hex)
            if not enemy.unit_type.in_range(dist):
                continue
            total += predict_damage(state, enemy, unit)
        return total
    finally:
        unit.hex = orig


def threat_to_unit(
    state: "GameState",
    unit: "Unit",
    *,
    viewer_fid: Optional[str] = None,
) -> int:
    """Threat at the unit's current hex.  Shorthand for ``threat_to_unit_at(..., unit.hex)``."""
    return threat_to_unit_at(state, unit, unit.hex, viewer_fid=viewer_fid)
