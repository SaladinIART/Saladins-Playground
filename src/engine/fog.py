"""
Fog of war: per-faction visibility computation.

A hex is *visible* to a faction this frame iff at least one of that faction's
alive units has it within effective vision radius AND no terrain blocks LOS
along the straight hex-line between observer and target.

A hex is *explored* if it has ever been visible — terrain is drawn but live
enemy unit state is hidden. Explored memory is stored per-faction on GameState
and accumulates across turns. Visible state is recomputed (or served cached)
on every query.

Effective vision:
  effective_vision(unit) = max(1, unit.vision + observer_tile.vision_modifier)

LOS:
  - The observer's own hex is always visible.
  - Adjacent hexes (distance 1) are always visible (ignore LOS).
  - For distance ≥ 2, walk hex_line(observer, target); if any intermediate hex
    (not endpoints) blocks LOS, the target is hidden.

Stealth (CP-18 hook):
  can_faction_see_unit() returns False for enemy stealth units unless an own
  unit is within 1 hex of them.
"""
from __future__ import annotations

from src.engine.hex import Hex, distance, hex_line, hexes_within
from src.engine.state import GameState
from src.engine.unit import Unit

# Maximum hex distance at which a stealth-flagged enemy can still be seen.
# 0 would mean only by being on the same hex (impossible since 1u/hex);
# 1 means adjacent observers reveal a stealth unit.
STEALTH_DETECTION_RADIUS = 1


def effective_vision(state: GameState, unit: Unit) -> int:
    """Vision range adjusted by the unit's current tile vision_modifier. Min 1."""
    base = unit.unit_type.vision
    tile = state.tiles.get(unit.hex)
    mod = tile.terrain.vision_modifier if tile is not None else 0
    return max(1, base + mod)


def _line_blocked(state: GameState, observer: Hex, target: Hex) -> bool:
    """True if any hex strictly between observer and target blocks LOS."""
    if observer == target:
        return False
    line = hex_line(observer, target)
    # Skip endpoints; only intermediate terrain blocks.
    for h in line[1:-1]:
        tile = state.tiles.get(h)
        if tile is None:
            continue
        if tile.terrain.blocks_los:
            return True
    return False


def hexes_visible_from(state: GameState, unit: Unit) -> set[Hex]:
    """Set of hexes this unit can see right now."""
    radius = effective_vision(state, unit)
    seen: set[Hex] = set()
    for h in hexes_within(unit.hex, radius):
        if h not in state.tiles:
            continue
        # Own hex + adjacency: always visible.
        if distance(unit.hex, h) <= 1:
            seen.add(h)
            continue
        if not _line_blocked(state, unit.hex, h):
            seen.add(h)
    return seen


def compute_visible(state: GameState, faction_id: str) -> set[Hex]:
    """Union of all alive faction units' fields of view."""
    visible: set[Hex] = set()
    for u in state.units_of(faction_id):
        visible |= hexes_visible_from(state, u)
    return visible


def can_faction_see_unit(state: GameState, faction_id: str, unit: Unit) -> bool:
    """
    Is *unit* visible to *faction_id* right now?

    - Own faction: always visible (we know where our units are).
    - Dead units: never.
    - Enemy in fog: never.
    - Stealth enemy: only when an own unit is within 1 hex of it.
    """
    if not unit.is_alive():
        return False
    if unit.faction == faction_id:
        return True
    visible = state.visible_to(faction_id)
    if unit.hex not in visible:
        return False
    if unit.unit_type.stealth:
        for own in state.units_of(faction_id):
            if distance(own.hex, unit.hex) <= STEALTH_DETECTION_RADIUS:
                return True
        return False
    return True
