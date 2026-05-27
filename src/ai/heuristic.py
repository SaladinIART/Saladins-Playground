"""
Heuristic utility AI.

Loop
----
Each AI turn:
  1. ``enumerate_actions(state, faction_id)`` — every legal action this turn
  2. Score each action via ``score_action``
  3. Pick highest-scoring action; if score <= 0 stop (nothing worth doing)
  4. Execute it (mutates state)
  5. Repeat until no profitable action or MAX_ACTIONS reached

``take_turn`` runs the whole loop synchronously; ``take_turn_steps`` is a
generator yielding one action at a time so the UI can pace them visibly
with a per-frame timer.

Action types
------------
- ``AttackAction``        — attack from current hex
- ``MoveAttackAction``    — move, optionally attack from destination
- ``BuildAction``         — purchase a unit at the faction HQ
- ``UpgradeTierAction``   — pay to unlock the next tech tier

Scoring (default weights)
-------------------------
- attack damage / kill bonus / counter penalty / suicide penalty
- move base, capture-tile bonus, approach-enemy-HQ pull, threat aversion
- build base, low-army bonus, engineer scarcity bonus, tier-cost discount
- tier-upgrade base

Personality overrides any of these weights (see ``personality.py``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional, Union

from src.ai.personality import BALANCED, Personality
from src.ai.threat import threat_to_unit_at
from src.engine.combat import (
    attack_targets,
    can_attack,
    predict_exchange,
    resolve_attack,
)
from src.engine.hex import Hex, distance
from src.engine.movement import compute_movement
from src.engine.state import GameState
from src.engine.tech import (
    buildable_units,
    can_upgrade_tier,
    find_spawn_hex,
    next_tier_cost,
)
from src.engine.unit import Unit, get as get_unit_type

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

MAX_ACTIONS_PER_TURN = 50   # safety cap; real turns rarely exceed ~10

DEFAULT_WEIGHTS: dict[str, float] = {
    # Combat
    "attack_damage":             3.0,    # per HP dealt
    "attack_kill_bonus":         50.0,   # finishing a unit
    "attack_counter_penalty":    2.0,    # per HP we take back
    "suicide_penalty":           80.0,   # counter kills us

    # Movement
    "move_base":                 1.0,    # small reward for any reachable hex
    "approach_enemy_hq":         20.0,   # divided by (distance + 1)
    "capture_progress":          40.0,   # engineer landing on capturable tile
    "capture_continue":          20.0,   # bonus if we were already capturing
    "retreat_when_low_hp":       1.5,    # multiplier on dest-threat when wounded
    "threat_aversion_base":      0.4,    # mild threat penalty at full HP
    "low_hp_threshold_frac":     0.5,    # below this fraction of max HP = "wounded"

    # Building
    "build_base":                10.0,
    "build_when_low_army":       35.0,
    "low_army_threshold":        4.0,
    "build_tier_value":          8.0,    # multiplier on unit tier
    "build_engineer_bonus":      30.0,   # if fewer than 2 engineers
    "build_cost_discount":       3.0,    # multiplier on (credits / cost) cap 2

    # Tech
    "upgrade_tier_base":         45.0,
}


# ---------------------------------------------------------------------------
# Action dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AttackAction:
    attacker_uid: int
    defender_uid: int


@dataclass(frozen=True)
class MoveAttackAction:
    unit_uid: int
    dest: Hex
    target_uid: Optional[int] = None   # None = move only


@dataclass(frozen=True)
class BuildAction:
    hq_hex: Hex
    type_id: str


@dataclass(frozen=True)
class UpgradeTierAction:
    faction_id: str


Action = Union[AttackAction, MoveAttackAction, BuildAction, UpgradeTierAction]


def describe(action: Action) -> str:
    """Human-readable description for logging."""
    if isinstance(action, AttackAction):
        return f"attack uid={action.attacker_uid} -> uid={action.defender_uid}"
    if isinstance(action, MoveAttackAction):
        if action.target_uid is None:
            return f"move uid={action.unit_uid} -> {action.dest}"
        return f"move uid={action.unit_uid} -> {action.dest} & attack uid={action.target_uid}"
    if isinstance(action, BuildAction):
        return f"build {action.type_id} at HQ {action.hq_hex}"
    if isinstance(action, UpgradeTierAction):
        return f"upgrade tier for {action.faction_id}"
    return repr(action)


# ---------------------------------------------------------------------------
# Weight resolution
# ---------------------------------------------------------------------------

def effective_weights(personality: Optional[Personality] = None) -> dict[str, float]:
    """DEFAULT_WEIGHTS overlaid with the personality's overrides."""
    out = dict(DEFAULT_WEIGHTS)
    if personality is not None:
        out.update(personality.weight_overrides)
    return out


# ---------------------------------------------------------------------------
# Action enumeration
# ---------------------------------------------------------------------------

def enumerate_actions(state: GameState, faction_id: str) -> list[Action]:
    """Every legal action this AI could take right now."""
    actions: list[Action] = []
    for unit in state.units_of(faction_id):
        if not unit.can_act():
            continue
        actions.extend(_unit_actions(state, unit))

    hq = state.hq_of(faction_id)
    if hq is not None:
        faction = state.faction_by_id(faction_id)
        for ut in buildable_units(faction):
            if not faction.can_afford(ut.cost_credits, ut.cost_oil):
                continue
            if find_spawn_hex(state, hq.hex, ut) is None:
                continue
            actions.append(BuildAction(hq_hex=hq.hex, type_id=ut.id))
        if can_upgrade_tier(faction) and faction.can_afford(next_tier_cost(faction), 0):
            actions.append(UpgradeTierAction(faction_id=faction_id))

    return actions


def _unit_actions(state: GameState, unit: Unit) -> list[Action]:
    """Attack-in-place + move + move-then-attack actions for one unit."""
    actions: list[Action] = []

    # Attack from current hex.
    if not unit.has_attacked:
        for target in attack_targets(state, unit):
            actions.append(AttackAction(unit.uid, target.uid))

    # Move (optionally followed by attack from new hex).
    if not unit.has_moved:
        movement = compute_movement(state, unit)
        orig_hex = unit.hex
        for dest in movement.reachable:
            if dest == orig_hex:
                continue
            # Move-only option.
            actions.append(MoveAttackAction(unit.uid, dest, None))
            if not unit.has_attacked:
                # Simulate move: temporarily relocate to enumerate post-move attacks.
                unit.hex = dest
                try:
                    for target in attack_targets(state, unit):
                        actions.append(MoveAttackAction(unit.uid, dest, target.uid))
                finally:
                    unit.hex = orig_hex

    return actions


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_action(
    state: GameState,
    faction_id: str,
    action: Action,
    weights: dict[str, float],
) -> float:
    if isinstance(action, AttackAction):
        attacker = state.units.get(action.attacker_uid)
        defender = state.units.get(action.defender_uid)
        if attacker is None or defender is None:
            return -1.0
        return _score_attack(state, attacker, defender, weights)

    if isinstance(action, MoveAttackAction):
        unit = state.units.get(action.unit_uid)
        if unit is None:
            return -1.0
        move_part = _score_move(state, unit, action.dest, weights)
        if action.target_uid is None:
            return move_part
        target = state.units.get(action.target_uid)
        if target is None:
            return move_part
        # Score the attack from the prospective destination.
        orig = unit.hex
        unit.hex = action.dest
        try:
            atk_part = _score_attack(state, unit, target, weights)
        finally:
            unit.hex = orig
        return move_part + atk_part

    if isinstance(action, BuildAction):
        faction = state.faction_by_id(faction_id)
        return _score_build(state, faction, action.type_id, weights)

    if isinstance(action, UpgradeTierAction):
        return weights["upgrade_tier_base"]

    return 0.0


def _score_attack(state, attacker, defender, weights) -> float:
    atk_dmg, counter_dmg = predict_exchange(state, attacker, defender)
    score = atk_dmg * weights["attack_damage"]
    if atk_dmg >= defender.hp:
        score += weights["attack_kill_bonus"]
    score -= counter_dmg * weights["attack_counter_penalty"]
    if counter_dmg >= attacker.hp:
        score -= weights["suicide_penalty"]
    return score


def _score_move(state, unit, dest: Hex, weights) -> float:
    score = weights["move_base"]

    # Capture-intent bonus
    if unit.unit_type.can_capture:
        tile = state.tiles.get(dest)
        if tile is not None and tile.terrain.capturable and tile.owner_faction != unit.faction:
            score += weights["capture_progress"]
            if tile.capture_progress > 0 and tile.capturing_faction == unit.faction:
                score += weights["capture_continue"]

    # Pull toward enemy HQs (closer = higher score)
    enemy_hqs = [
        t.hex for t in state.tiles.values()
        if t.terrain.is_hq and t.owner_faction is not None and t.owner_faction != unit.faction
    ]
    if enemy_hqs and not unit.unit_type.can_capture:
        # Combat units pull toward enemy HQ; engineers prioritise capture instead.
        nearest = min(distance(dest, h) for h in enemy_hqs)
        score += weights["approach_enemy_hq"] / (nearest + 1.0)

    # Threat aversion (more if wounded)
    threat = threat_to_unit_at(state, unit, dest)
    wounded = unit.hp <= unit.unit_type.hp * weights["low_hp_threshold_frac"]
    score -= threat * (weights["retreat_when_low_hp"] if wounded else weights["threat_aversion_base"])

    return score


def _score_build(state, faction, type_id: str, weights) -> float:
    ut = get_unit_type(type_id)

    score = weights["build_base"]

    army_size = len(state.units_of(faction.id))
    if army_size < weights["low_army_threshold"]:
        score += weights["build_when_low_army"]

    # Cost discount: more credits relative to cost = mild bonus (capped).
    ratio = faction.credits / max(1, ut.cost_credits)
    score += min(ratio - 1.0, 2.0) * weights["build_cost_discount"]

    # Tier value
    score += ut.tier * weights["build_tier_value"]

    # Engineers scarcity bonus
    if ut.can_capture:
        engineers = sum(
            1 for u in state.units_of(faction.id) if u.unit_type.can_capture
        )
        if engineers < 2:
            score += weights["build_engineer_bonus"]

    return score


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def execute_action(state: GameState, action: Action) -> None:
    """Mutate the state to apply *action*.  Silently skips stale references."""
    if isinstance(action, AttackAction):
        a = state.units.get(action.attacker_uid)
        d = state.units.get(action.defender_uid)
        if a is None or d is None or not a.is_alive() or not d.is_alive():
            return
        if not can_attack(state, a, d):
            return
        resolve_attack(state, a, d)
        return

    if isinstance(action, MoveAttackAction):
        u = state.units.get(action.unit_uid)
        if u is None or not u.is_alive() or u.has_moved:
            return
        movement = compute_movement(state, u)
        if action.dest not in movement.reachable or state.unit_at(action.dest) is not None:
            return
        state.move_unit(u.uid, action.dest)
        u.has_moved = True
        if action.target_uid is not None and not u.has_attacked:
            t = state.units.get(action.target_uid)
            if t is not None and t.is_alive() and can_attack(state, u, t):
                resolve_attack(state, u, t)
        return

    if isinstance(action, BuildAction):
        try:
            state.build_unit(action.type_id, state.active_faction.id, action.hq_hex)
        except ValueError:
            pass
        return

    if isinstance(action, UpgradeTierAction):
        try:
            state.upgrade_tier(action.faction_id)
        except ValueError:
            pass
        return


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------

def _pick_best(
    state: GameState,
    faction_id: str,
    weights: dict[str, float],
) -> Optional[Action]:
    """Score every legal action, return the highest if any has score > 0."""
    actions = enumerate_actions(state, faction_id)
    if not actions:
        return None
    best: Optional[Action] = None
    best_score = 0.0
    for a in actions:
        s = score_action(state, faction_id, a, weights)
        if s > best_score:
            best_score = s
            best = a
    return best


def take_turn(
    state: GameState,
    faction_id: str,
    personality: Optional[Personality] = None,
) -> list[Action]:
    """
    Run the AI synchronously to completion.  Returns the list of executed actions.
    Does NOT call ``state.end_turn``; caller is responsible for advancing.
    """
    weights = effective_weights(personality)
    executed: list[Action] = []
    for _ in range(MAX_ACTIONS_PER_TURN):
        if state.game_over:
            break
        action = _pick_best(state, faction_id, weights)
        if action is None:
            break
        execute_action(state, action)
        executed.append(action)
    return executed


def take_turn_steps(
    state: GameState,
    faction_id: str,
    personality: Optional[Personality] = None,
) -> Iterator[Action]:
    """
    Generator variant: yields one ``Action`` at a time after executing it.
    Used by main.py to pace AI moves with a per-frame timer so the player
    can follow what is happening.  Does NOT end the turn.
    """
    weights = effective_weights(personality)
    for _ in range(MAX_ACTIONS_PER_TURN):
        if state.game_over:
            return
        action = _pick_best(state, faction_id, weights)
        if action is None:
            return
        execute_action(state, action)
        yield action
