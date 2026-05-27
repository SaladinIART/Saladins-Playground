"""
Combat resolver — deterministic damage with terrain bonus + HP scaling + counter.

Damage formula
--------------
    final = round(base[atk_class][def_class] * (atk.hp / 10) * (1 - tile_def / 10))

where:
  - base[..][..] comes from data/damage_matrix.json, integer 0..10.
  - atk.hp is the attacker's current HP (so wounded attackers do less).
  - tile_def is the defender tile's defense_bonus (mountain 4, city 2, plain 0…).
  - Negative damage is clamped to 0.
  - A `base` of 0 means the attack is *illegal* — typically because the attacker
    cannot hit that unit class at all (e.g. ground infantry vs flying jet).

Counter rules
-------------
After applying damage to the defender, if the defender is still alive AND the
distance falls within the defender's own [range_min, range_max] AND its base
damage against the attacker is > 0, the defender hits back. The counter uses
the defender's *post-hit* HP for scaling.

Side effects of resolve_attack
------------------------------
Sets attacker.has_attacked = True, mutates HP on both sides, and removes any
unit whose HP fell to 0 from `state.units`. Triggers `state.invalidate_fog()`
exactly once if anything died (dead units no longer provide vision).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.engine.hex import distance
from src.engine.state import GameState
from src.engine.unit import Unit

_DATA_DIR = Path(__file__).parent.parent.parent / "data"

# {attacker_class: {defender_class: base_damage (0..10)}}
_matrix: dict[str, dict[str, int]] = {}


# ---------------------------------------------------------------------------
# Matrix loading
# ---------------------------------------------------------------------------

def load_damage_matrix(path: Optional[Path] = None) -> dict[str, dict[str, int]]:
    """Load (or reload) the damage matrix from JSON. Returns the registry."""
    if path is None:
        path = _DATA_DIR / "damage_matrix.json"
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    _matrix.clear()
    for atk_cls, row in raw["base_damage"].items():
        _matrix[atk_cls] = {def_cls: int(v) for def_cls, v in row.items()}
    return _matrix


def base_damage(attacker_class: str, defender_class: str) -> int:
    """Lookup base damage. 0 = attack ineffective / illegal."""
    if not _matrix:
        load_damage_matrix()
    return _matrix.get(attacker_class, {}).get(defender_class, 0)


# ---------------------------------------------------------------------------
# Damage prediction
# ---------------------------------------------------------------------------

def _terrain_defense(state: GameState, unit: Unit) -> int:
    """Defense bonus of the unit's current tile (0 if off-map)."""
    tile = state.tiles.get(unit.hex)
    return tile.terrain.defense_bonus if tile is not None else 0


def _damage_with_hp(
    state: GameState,
    attacker: Unit,
    defender: Unit,
    atk_hp_override: Optional[int] = None,
) -> int:
    """Internal: damage calc that lets us override the attacker's HP scaling."""
    base = base_damage(attacker.unit_type.unit_class, defender.unit_type.unit_class)
    if base <= 0:
        return 0
    atk_hp = attacker.hp if atk_hp_override is None else atk_hp_override
    if atk_hp <= 0:
        return 0
    terrain_def = _terrain_defense(state, defender)
    raw = base * (atk_hp / 10.0) * (1.0 - terrain_def / 10.0)
    return max(0, round(raw))


def predict_damage(state: GameState, attacker: Unit, defender: Unit) -> int:
    """Predicted damage from attacker to defender at current HP. Deterministic."""
    return _damage_with_hp(state, attacker, defender)


def predict_exchange(
    state: GameState, attacker: Unit, defender: Unit
) -> tuple[int, int]:
    """
    Returns (attacker_damage, counter_damage) for the hypothetical attack.

    The counter uses the defender's *post-hit* HP for scaling, mirroring what
    resolve_attack() will actually do. Used by UI for hover prediction and by
    AI threat eval.

    Self-destruct (kamikaze) attackers explode before the defender can counter,
    so counter_damage is reported as 0.
    """
    atk_dmg = predict_damage(state, attacker, defender)
    if atk_dmg >= defender.hp:
        return atk_dmg, 0  # defender dies, no counter
    if attacker.unit_type.self_destruct:
        return atk_dmg, 0  # attacker explodes — defender never gets to counter
    dist = distance(attacker.hex, defender.hex)
    if not defender.unit_type.in_range(dist):
        return atk_dmg, 0
    if base_damage(defender.unit_type.unit_class, attacker.unit_type.unit_class) <= 0:
        return atk_dmg, 0
    post_hp = defender.hp - atk_dmg
    counter = _damage_with_hp(state, defender, attacker, atk_hp_override=post_hp)
    return atk_dmg, counter


# ---------------------------------------------------------------------------
# Legality + target enumeration
# ---------------------------------------------------------------------------

def can_attack(state: GameState, attacker: Unit, defender: Unit) -> bool:
    """Pure rule check — no fog filter (UI layer enforces visibility)."""
    if not attacker.is_alive() or not defender.is_alive():
        return False
    if attacker.faction == defender.faction:
        return False
    if attacker.has_attacked:
        return False
    if defender.hex not in state.tiles or attacker.hex not in state.tiles:
        return False
    dist = distance(attacker.hex, defender.hex)
    if not attacker.unit_type.in_range(dist):
        return False
    if base_damage(attacker.unit_type.unit_class, defender.unit_type.unit_class) <= 0:
        return False
    return True


def attack_targets(state: GameState, attacker: Unit) -> list[Unit]:
    """All units the attacker may legally hit from its current hex."""
    if attacker.has_attacked or not attacker.is_alive():
        return []
    return [u for u in state.units.values() if can_attack(state, attacker, u)]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

@dataclass
class AttackResult:
    attacker_uid: int
    defender_uid: int
    distance: int
    damage_dealt: int        # attacker → defender
    defender_killed: bool
    counter_damage: int      # defender → attacker (0 if no counter)
    attacker_killed: bool


def resolve_attack(
    state: GameState, attacker: Unit, defender: Unit
) -> AttackResult:
    """
    Apply an attack. Mutates HP, attacker.has_attacked, and removes dead units.
    Raises ValueError if can_attack() rejects the pairing.
    """
    if not can_attack(state, attacker, defender):
        raise ValueError(
            f"Illegal attack: {attacker.unit_type.id} ({attacker.faction}) "
            f"-> {defender.unit_type.id} ({defender.faction})"
        )

    dist = distance(attacker.hex, defender.hex)
    dmg = predict_damage(state, attacker, defender)
    defender.apply_damage(dmg)
    attacker.has_attacked = True

    counter = 0
    if attacker.unit_type.self_destruct:
        # Kamikaze: attacker explodes after the hit; no counter possible.
        attacker.apply_damage(attacker.hp)   # → hp 0
    elif defender.is_alive():
        if defender.unit_type.in_range(dist) and base_damage(
            defender.unit_type.unit_class, attacker.unit_type.unit_class
        ) > 0:
            counter = _damage_with_hp(state, defender, attacker)
            attacker.apply_damage(counter)

    # Batch-remove the dead. Direct dict-pop avoids double fog invalidation.
    dead_uids: list[int] = []
    if not defender.is_alive():
        dead_uids.append(defender.uid)
    if not attacker.is_alive():
        dead_uids.append(attacker.uid)
    for uid in dead_uids:
        state.units.pop(uid, None)
    if dead_uids:
        state.invalidate_fog()

    return AttackResult(
        attacker_uid=attacker.uid,
        defender_uid=defender.uid,
        distance=dist,
        damage_dealt=dmg,
        defender_killed=not defender.is_alive(),
        counter_damage=counter,
        attacker_killed=not attacker.is_alive(),
    )
