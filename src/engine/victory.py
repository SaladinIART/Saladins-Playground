"""
Victory / defeat condition engine.

Design
------
Each scenario gives every faction a ``VictoryConfig`` describing the conditions
under which they *win* and the conditions under which they *lose*.  A condition
is a small dataclass that exposes ``evaluate(state, faction_id) -> bool``.

Composition
-----------
Inside a VictoryConfig, win-conditions and lose-conditions each have a *mode*:
  - ``"any"`` (OR) — condition list is satisfied if at least one is true
  - ``"all"`` (AND) — condition list is satisfied only when every one is true

If both win and lose are satisfied the same turn, **loss takes priority** —
prevents a "you destroyed enemy HQ AND your own HQ in the same turn" from
ambiguously resolving in your favour.

State
-----
Most conditions are pure functions of the game state.  A few (``HoldTiles``)
carry a small counter so they can track consecutive turns of ownership; that
counter mutates inside ``evaluate``.  The engine therefore promises one
call per turn (made by ``GameState.end_turn``).

Pickle / scenario JSON
----------------------
All conditions and VictoryConfig are plain dataclasses, fully picklable.
``condition_from_dict`` / ``victory_config_from_dict`` parse scenario JSON
into the corresponding dataclass instances.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from src.engine.hex import Hex

if TYPE_CHECKING:
    from src.engine.state import GameState


class Outcome(str, Enum):
    PENDING = "pending"
    WON = "won"
    LOST = "lost"


# ---------------------------------------------------------------------------
# Condition protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Condition(Protocol):
    """A victory / defeat condition evaluable against a game state."""
    def evaluate(self, state: "GameState", faction_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# Concrete conditions
# ---------------------------------------------------------------------------

@dataclass
class DestroyHQ:
    """
    Satisfied when *target_faction* no longer owns an HQ tile.

    In v0 the HQ is a *tile* (terrain_id="hq"), so "destroyed" really means
    "captured by anyone else" — ownership flipped via the capture mechanic.
    """
    target_faction: str

    def evaluate(self, state: "GameState", faction_id: str) -> bool:
        return state.hq_of(self.target_faction) is None


@dataclass
class HoldTiles:
    """
    Satisfied when *faction_id* has held all *target_hexes* for at least
    *turns_required* consecutive turns.  Tracks state across evaluations.

    Note: ``evaluate`` mutates ``consecutive_turns``; the engine guarantees
    one call per turn.
    """
    target_hexes: list[Hex]
    turns_required: int
    consecutive_turns: int = 0

    def evaluate(self, state: "GameState", faction_id: str) -> bool:
        holds_all = bool(self.target_hexes) and all(
            state.tiles.get(h) is not None
            and state.tiles[h].owner_faction == faction_id
            for h in self.target_hexes
        )
        if holds_all:
            self.consecutive_turns += 1
        else:
            self.consecutive_turns = 0
        return self.consecutive_turns >= self.turns_required


@dataclass
class OwnAllOfTerrain:
    """
    Satisfied when *faction_id* owns every tile of a given ``terrain_id``.
    Returns False if no such tiles exist (avoids accidental empty-set win).
    """
    terrain_id: str

    def evaluate(self, state: "GameState", faction_id: str) -> bool:
        matching = [t for t in state.tiles.values() if t.terrain_id == self.terrain_id]
        if not matching:
            return False
        return all(t.owner_faction == faction_id for t in matching)


@dataclass
class EliminateFaction:
    """
    Satisfied when *target_faction* has no live units and no owned HQ —
    effectively wiped from the map.
    """
    target_faction: str

    def evaluate(self, state: "GameState", faction_id: str) -> bool:
        return (
            len(state.units_of(self.target_faction)) == 0
            and state.hq_of(self.target_faction) is None
        )


@dataclass
class DestroyUnitType:
    """
    Satisfied when no live units of (*type_id*, *owner_faction*) remain.
    Used for "kill the boss" / "protect the convoy" objectives.
    """
    type_id: str
    owner_faction: str

    def evaluate(self, state: "GameState", faction_id: str) -> bool:
        return not any(
            u.is_alive() and u.type_id == self.type_id and u.faction == self.owner_faction
            for u in state.units.values()
        )


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

@dataclass
class VictoryConfig:
    """
    Per-faction victory / defeat configuration.

    A condition list satisfied under its *mode* triggers the outcome.
    ``mode="any"`` (OR) — at least one condition true.
    ``mode="all"`` (AND) — every condition true.
    An empty condition list is *never* satisfied.

    Loss takes priority when both sides resolve true the same turn.
    """
    win_conditions: list[Condition] = field(default_factory=list)
    win_mode: str = "any"           # "any" | "all"
    lose_conditions: list[Condition] = field(default_factory=list)
    lose_mode: str = "any"

    def evaluate(self, state: "GameState", faction_id: str) -> Outcome:
        # IMPORTANT: evaluate both sides first so stateful conditions (HoldTiles)
        # always update — even if the other side already decides the outcome.
        win_results  = [c.evaluate(state, faction_id) for c in self.win_conditions]
        lose_results = [c.evaluate(state, faction_id) for c in self.lose_conditions]

        won  = _satisfied(win_results,  self.win_mode)
        lost = _satisfied(lose_results, self.lose_mode)

        if lost:
            return Outcome.LOST       # loss takes priority
        if won:
            return Outcome.WON
        return Outcome.PENDING


def _satisfied(results: list[bool], mode: str) -> bool:
    if not results:
        return False
    if mode == "any":
        return any(results)
    if mode == "all":
        return all(results)
    raise ValueError(f"Unknown victory mode: {mode!r} (expected 'any' or 'all')")


def default_victory_config(own_faction: str, enemy_factions: list[str]) -> VictoryConfig:
    """
    Default: win by destroying any enemy HQ; lose by losing your own HQ.

    With more than one enemy this is AND for win (must destroy *all* enemy HQs).
    """
    win_conds = [DestroyHQ(target_faction=ef) for ef in enemy_factions]
    return VictoryConfig(
        win_conditions=win_conds,
        win_mode="all" if len(win_conds) > 1 else "any",
        lose_conditions=[DestroyHQ(target_faction=own_faction)],
        lose_mode="any",
    )


# ---------------------------------------------------------------------------
# Scenario JSON parsing
# ---------------------------------------------------------------------------

_CONDITION_REGISTRY: dict[str, type] = {
    "destroy_hq":         DestroyHQ,
    "hold_tiles":         HoldTiles,
    "own_all_terrain":    OwnAllOfTerrain,
    "eliminate_faction":  EliminateFaction,
    "destroy_unit_type":  DestroyUnitType,
}


def condition_from_dict(d: dict[str, Any]) -> Condition:
    """Instantiate a Condition from a scenario JSON dict (key ``type`` selects class)."""
    t = d.get("type")
    if t is None:
        raise ValueError("Condition dict missing 'type' key")
    cls = _CONDITION_REGISTRY.get(t)
    if cls is None:
        raise ValueError(f"Unknown condition type: {t!r}")
    kwargs = {k: v for k, v in d.items() if k != "type"}
    # Convert [q, r] pairs to Hex for HoldTiles
    if t == "hold_tiles" and "target_hexes" in kwargs:
        kwargs["target_hexes"] = [Hex(q, r) for q, r in kwargs["target_hexes"]]
    return cls(**kwargs)


def victory_config_from_dict(d: dict[str, Any]) -> VictoryConfig:
    """Parse a per-faction VictoryConfig block from scenario JSON."""
    return VictoryConfig(
        win_conditions=[condition_from_dict(c) for c in d.get("win_conditions", [])],
        win_mode=d.get("win_mode", "any"),
        lose_conditions=[condition_from_dict(c) for c in d.get("lose_conditions", [])],
        lose_mode=d.get("lose_mode", "any"),
    )
