"""Tests for the heuristic AI: threat eval, personality, enumeration, scoring, turns."""
from __future__ import annotations

import pytest

from src.ai.heuristic import (
    AttackAction,
    BuildAction,
    DEFAULT_WEIGHTS,
    MoveAttackAction,
    UpgradeTierAction,
    describe,
    effective_weights,
    enumerate_actions,
    execute_action,
    score_action,
    take_turn,
    take_turn_steps,
)
from src.ai.personality import (
    AGGRESSIVE,
    BALANCED,
    DEFENSIVE,
    Personality,
    from_dict as personality_from_dict,
)
from src.ai.threat import threat_to_unit, threat_to_unit_at
from src.engine.combat import load_damage_matrix
from src.engine.hex import Hex
from src.engine.state import Faction, GameState
from src.engine.tile import Tile, load_terrain
from src.engine.unit import Unit, load_units


@pytest.fixture(autouse=True)
def loaded():
    load_terrain()
    load_units()
    load_damage_matrix()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state(*, ai_credits: int = 2000, ai_oil: int = 10) -> GameState:
    """
    Standard 2-faction state: NATO (human) HQ at (0,0), BRICS (AI) HQ at (8,0),
    with plain tiles filling 0..8 along r=0 and the six BRICS-HQ neighbours.
    """
    nato  = Faction(id="NATO",  name="NATO",  color=(0,0,200), credits=500, oil=5, is_ai=False)
    brics = Faction(id="BRICS", name="BRICS", color=(200,0,0), credits=ai_credits, oil=ai_oil, is_ai=True)

    tiles: dict[Hex, Tile] = {}
    for q in range(-2, 12):
        for r in range(-2, 4):
            tiles[Hex(q, r)] = Tile(Hex(q, r), "plain")
    tiles[Hex(0, 0)] = Tile(Hex(0, 0), "hq",   owner_faction="NATO")
    tiles[Hex(8, 0)] = Tile(Hex(8, 0), "hq",   owner_faction="BRICS")
    tiles[Hex(4, 0)] = Tile(Hex(4, 0), "city")   # neutral mid-map city
    return GameState(factions=[nato, brics], tiles=tiles)


# ---------------------------------------------------------------------------
# Threat eval
# ---------------------------------------------------------------------------

def test_threat_zero_with_no_enemies():
    s = _state()
    u = Unit("nato_inf_l", "BRICS", Hex(5, 0))
    s.add_unit(u)
    assert threat_to_unit(s, u) == 0


def test_threat_counts_in_range_enemy():
    s = _state()
    target  = Unit("nato_inf_l", "BRICS", Hex(5, 0))
    attacker = Unit("nato_inf_l", "NATO", Hex(4, 0))   # adjacent → range 1
    s.add_unit(target)
    s.add_unit(attacker)
    threat = threat_to_unit(s, target)
    assert threat > 0


def test_threat_ignores_out_of_range_enemy():
    s = _state()
    target  = Unit("nato_inf_l", "BRICS", Hex(5, 0))
    far     = Unit("nato_inf_l", "NATO", Hex(0, 0))   # far away, range 1 unit
    s.add_unit(target)
    s.add_unit(far)
    assert threat_to_unit(s, target) == 0


def test_threat_to_unit_at_uses_prospective_hex():
    """Mutation restores: passing an out-of-range hex should compute zero
    without permanently moving the unit."""
    s = _state()
    target = Unit("nato_inf_l", "BRICS", Hex(5, 0))
    near   = Unit("nato_inf_l", "NATO", Hex(4, 0))
    s.add_unit(target)
    s.add_unit(near)
    safe_threat = threat_to_unit_at(s, target, Hex(10, 0))
    assert safe_threat == 0
    assert target.hex == Hex(5, 0)   # restored


# ---------------------------------------------------------------------------
# Personality
# ---------------------------------------------------------------------------

def test_default_weights_unchanged_by_balanced():
    w = effective_weights(BALANCED)
    assert w["attack_damage"] == DEFAULT_WEIGHTS["attack_damage"]


def test_aggressive_personality_amps_attack():
    w = effective_weights(AGGRESSIVE)
    assert w["attack_damage"] > DEFAULT_WEIGHTS["attack_damage"]
    assert w["attack_kill_bonus"] > DEFAULT_WEIGHTS["attack_kill_bonus"]


def test_defensive_personality_amps_capture_and_caution():
    w = effective_weights(DEFENSIVE)
    assert w["capture_progress"] > DEFAULT_WEIGHTS["capture_progress"]
    assert w["retreat_when_low_hp"] > DEFAULT_WEIGHTS["retreat_when_low_hp"]


def test_personality_from_dict_parses():
    p = personality_from_dict({"name": "custom", "weights": {"attack_damage": 99.0}})
    assert p.name == "custom"
    assert p.weight_overrides["attack_damage"] == 99.0


def test_personality_from_dict_defaults_empty():
    p = personality_from_dict({})
    assert p.weight_overrides == {}


# ---------------------------------------------------------------------------
# Action enumeration
# ---------------------------------------------------------------------------

def test_enumerate_includes_attack_in_place():
    s = _state()
    attacker = Unit("nato_inf_l", "BRICS", Hex(4, 0))
    defender = Unit("nato_inf_l", "NATO", Hex(5, 0))
    s.add_unit(attacker)
    s.add_unit(defender)
    s.active_faction_idx = 1  # BRICS active
    actions = enumerate_actions(s, "BRICS")
    attacks = [a for a in actions if isinstance(a, AttackAction)]
    assert any(a.defender_uid == defender.uid for a in attacks)


def test_enumerate_includes_move_then_attack():
    """Unit two hexes away can move adjacent then attack."""
    s = _state()
    attacker = Unit("nato_inf_l", "BRICS", Hex(3, 0))
    defender = Unit("nato_inf_l", "NATO", Hex(5, 0))
    s.add_unit(attacker)
    s.add_unit(defender)
    s.active_faction_idx = 1
    actions = enumerate_actions(s, "BRICS")
    move_attacks = [
        a for a in actions
        if isinstance(a, MoveAttackAction) and a.target_uid == defender.uid
    ]
    assert len(move_attacks) >= 1


def test_enumerate_skips_exhausted_units():
    s = _state()
    u = Unit("nato_inf_l", "BRICS", Hex(5, 0))
    u.has_moved = True
    u.has_attacked = True
    s.add_unit(u)
    actions = enumerate_actions(s, "BRICS")
    # Should still have HQ build actions, but no unit actions.
    unit_actions = [
        a for a in actions
        if isinstance(a, (AttackAction, MoveAttackAction))
    ]
    assert unit_actions == []


def test_enumerate_includes_build_when_affordable():
    s = _state(ai_credits=2000)
    actions = enumerate_actions(s, "BRICS")
    builds = [a for a in actions if isinstance(a, BuildAction)]
    assert len(builds) > 0


def test_enumerate_skips_build_when_broke():
    s = _state(ai_credits=0, ai_oil=0)
    actions = enumerate_actions(s, "BRICS")
    builds = [a for a in actions if isinstance(a, BuildAction)]
    assert builds == []


def test_enumerate_includes_upgrade_when_affordable():
    s = _state(ai_credits=5000)
    actions = enumerate_actions(s, "BRICS")
    upgrades = [a for a in actions if isinstance(a, UpgradeTierAction)]
    assert len(upgrades) == 1


def test_enumerate_skips_upgrade_at_max_tier():
    s = _state(ai_credits=99999)
    s.faction_by_id("BRICS").tier = 3
    actions = enumerate_actions(s, "BRICS")
    assert not any(isinstance(a, UpgradeTierAction) for a in actions)


def test_unit_hex_restored_after_enumeration():
    """Move-then-attack simulation must not leave units mispositioned."""
    s = _state()
    u = Unit("nato_inf_l", "BRICS", Hex(3, 0))
    s.add_unit(u)
    enumerate_actions(s, "BRICS")
    assert u.hex == Hex(3, 0)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def test_killing_attack_outscores_non_killing():
    s = _state()
    a = Unit("nato_inf_l", "BRICS", Hex(4, 0))
    fresh = Unit("nato_inf_l", "NATO", Hex(5, 0))   # full HP
    wounded = Unit("nato_inf_l", "NATO", Hex(3, 0))  # 1 HP, killable
    wounded.hp = 1
    s.add_unit(a); s.add_unit(fresh); s.add_unit(wounded)

    w = DEFAULT_WEIGHTS
    kill_score    = score_action(s, "BRICS", AttackAction(a.uid, wounded.uid), w)
    nonkill_score = score_action(s, "BRICS", AttackAction(a.uid, fresh.uid),   w)
    assert kill_score > nonkill_score


def test_suicide_attack_penalised():
    """1-HP attacker vs heavy vehicle: counter will kill, score should be negative."""
    s = _state()
    weak = Unit("nato_inf_l", "BRICS", Hex(4, 0))
    weak.hp = 1
    tank = Unit("nato_vehicle_m", "NATO", Hex(5, 0))
    s.add_unit(weak); s.add_unit(tank)
    score = score_action(s, "BRICS", AttackAction(weak.uid, tank.uid), DEFAULT_WEIGHTS)
    assert score < 0


def test_engineer_prefers_capturable_tile():
    """Engineer's move score for capturable tile beats move score for plain tile."""
    from src.ai.heuristic import _score_move
    s = _state()
    eng = Unit("nato_engineer", "BRICS", Hex(3, 0))
    s.add_unit(eng)
    score_city   = _score_move(s, eng, Hex(4, 0), DEFAULT_WEIGHTS)   # neutral city
    score_plain  = _score_move(s, eng, Hex(2, 0), DEFAULT_WEIGHTS)
    assert score_city > score_plain


def test_build_engineer_scarcity_bonus():
    """Building an engineer scores higher when faction has zero engineers."""
    from src.ai.heuristic import _score_build
    s = _state(ai_credits=2000)
    brics = s.faction_by_id("BRICS")
    eng_score    = _score_build(s, brics, "nato_engineer", DEFAULT_WEIGHTS)
    inf_score    = _score_build(s, brics, "nato_inf_l",    DEFAULT_WEIGHTS)
    assert eng_score > inf_score


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def test_execute_attack_resolves():
    s = _state()
    a = Unit("nato_inf_l", "BRICS", Hex(4, 0))
    d = Unit("nato_inf_l", "NATO", Hex(5, 0))
    s.add_unit(a); s.add_unit(d)
    s.active_faction_idx = 1
    initial_hp = d.hp
    execute_action(s, AttackAction(a.uid, d.uid))
    assert d.hp < initial_hp
    assert a.has_attacked


def test_execute_move_marks_unit_moved():
    s = _state()
    u = Unit("nato_inf_l", "BRICS", Hex(5, 0))
    s.add_unit(u)
    s.active_faction_idx = 1
    execute_action(s, MoveAttackAction(u.uid, Hex(6, 0), None))
    assert u.hex == Hex(6, 0)
    assert u.has_moved


def test_execute_stale_attack_silently_ignored():
    """Executing an action whose unit was already removed should not raise."""
    s = _state()
    execute_action(s, AttackAction(attacker_uid=999, defender_uid=998))   # both missing


def test_execute_build_purchases_unit():
    s = _state(ai_credits=2000)
    s.active_faction_idx = 1
    before = len(s.units_of("BRICS"))
    execute_action(s, BuildAction(hq_hex=Hex(8, 0), type_id="nato_inf_l"))
    assert len(s.units_of("BRICS")) == before + 1


def test_execute_upgrade_tier_raises_tier():
    s = _state(ai_credits=2000)
    s.active_faction_idx = 1
    execute_action(s, UpgradeTierAction("BRICS"))
    assert s.faction_by_id("BRICS").tier == 2


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def test_take_turn_no_units_returns_empty():
    """AI with no units, no HQ, no money should do nothing."""
    nato = Faction(id="NATO", name="NATO", color=(0,0,200), is_ai=False)
    brics = Faction(id="BRICS", name="BRICS", color=(200,0,0), credits=0, oil=0, is_ai=True)
    s = GameState(factions=[nato, brics], tiles={Hex(0, 0): Tile(Hex(0, 0), "plain")})
    s.active_faction_idx = 1
    assert take_turn(s, "BRICS") == []


def test_take_turn_attacks_when_can_kill():
    s = _state()
    a = Unit("nato_vehicle_m", "BRICS", Hex(4, 0))
    d = Unit("nato_inf_l", "NATO", Hex(5, 0))
    d.hp = 2  # killable
    s.add_unit(a); s.add_unit(d)
    s.active_faction_idx = 1
    actions = take_turn(s, "BRICS")
    # Killing the infantry should be part of the turn (either attack-in-place
    # or move-then-attack).
    killed = d.uid not in s.units
    assert killed


def test_take_turn_steps_yields_one_at_a_time():
    """Generator must yield separate actions per next() call."""
    s = _state(ai_credits=2000)
    s.active_faction_idx = 1
    gen = take_turn_steps(s, "BRICS")
    actions = list(gen)
    assert len(actions) >= 1   # at least one build/upgrade is profitable


def test_take_turn_stops_at_max_actions():
    """Safety cap prevents infinite loops."""
    # Force a state that could keep producing actions, then confirm cap respected.
    s = _state(ai_credits=10_000_000, ai_oil=10_000_000)
    s.active_faction_idx = 1
    actions = take_turn(s, "BRICS")
    from src.ai.heuristic import MAX_ACTIONS_PER_TURN
    assert len(actions) <= MAX_ACTIONS_PER_TURN


def test_take_turn_does_not_call_end_turn():
    """Caller is responsible for state.end_turn() — verify AI never advances it."""
    s = _state(ai_credits=2000)
    s.active_faction_idx = 1
    turn_before = s.turn_number
    active_before = s.active_faction_idx
    take_turn(s, "BRICS")
    assert s.turn_number == turn_before
    assert s.active_faction_idx == active_before


# ---------------------------------------------------------------------------
# describe()
# ---------------------------------------------------------------------------

def test_describe_each_action_type():
    assert "attack" in describe(AttackAction(1, 2)).lower()
    assert "move"   in describe(MoveAttackAction(1, Hex(0, 0))).lower()
    assert "build"  in describe(BuildAction(Hex(0, 0), "nato_inf_l")).lower()
    assert "tier"   in describe(UpgradeTierAction("BRICS")).lower()
