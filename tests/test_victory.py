"""Tests for victory.py: conditions, composition, scenario parsing, integration."""
from __future__ import annotations

import pickle

import pytest

from src.engine.hex import Hex
from src.engine.state import Faction, GameState
from src.engine.tile import Tile, load_terrain
from src.engine.unit import Unit, load_units
from src.engine.victory import (
    DestroyHQ,
    DestroyUnitType,
    EliminateFaction,
    HoldTiles,
    Outcome,
    OwnAllOfTerrain,
    VictoryConfig,
    condition_from_dict,
    default_victory_config,
    victory_config_from_dict,
)


@pytest.fixture(autouse=True)
def loaded():
    load_terrain()
    load_units()


def _state_with_hqs() -> GameState:
    """NATO HQ at (0,0), BRICS HQ at (5,0), one neutral city in between."""
    nato  = Faction(id="NATO",  name="NATO",  color=(0,0,200))
    brics = Faction(id="BRICS", name="BRICS", color=(200,0,0))
    tiles = {
        Hex(0, 0): Tile(Hex(0, 0), "hq",   owner_faction="NATO"),
        Hex(1, 0): Tile(Hex(1, 0), "city"),
        Hex(2, 0): Tile(Hex(2, 0), "city"),
        Hex(5, 0): Tile(Hex(5, 0), "hq",   owner_faction="BRICS"),
    }
    return GameState(factions=[nato, brics], tiles=tiles)


# ---------------------------------------------------------------------------
# DestroyHQ
# ---------------------------------------------------------------------------

def test_destroy_hq_false_when_target_holds_hq():
    s = _state_with_hqs()
    assert not DestroyHQ(target_faction="BRICS").evaluate(s, "NATO")


def test_destroy_hq_true_when_target_loses_hq():
    s = _state_with_hqs()
    s.set_tile_owner(Hex(5, 0), "NATO")  # NATO captures BRICS HQ
    assert DestroyHQ(target_faction="BRICS").evaluate(s, "NATO")


def test_destroy_hq_true_when_target_hq_neutralised():
    s = _state_with_hqs()
    s.set_tile_owner(Hex(5, 0), None)  # owner removed
    assert DestroyHQ(target_faction="BRICS").evaluate(s, "NATO")


# ---------------------------------------------------------------------------
# HoldTiles
# ---------------------------------------------------------------------------

def test_hold_tiles_increments_when_held():
    s = _state_with_hqs()
    s.set_tile_owner(Hex(1, 0), "NATO")
    s.set_tile_owner(Hex(2, 0), "NATO")
    c = HoldTiles(target_hexes=[Hex(1, 0), Hex(2, 0)], turns_required=3)
    assert not c.evaluate(s, "NATO"); assert c.consecutive_turns == 1
    assert not c.evaluate(s, "NATO"); assert c.consecutive_turns == 2
    assert     c.evaluate(s, "NATO"); assert c.consecutive_turns == 3


def test_hold_tiles_resets_when_one_tile_lost():
    s = _state_with_hqs()
    s.set_tile_owner(Hex(1, 0), "NATO")
    s.set_tile_owner(Hex(2, 0), "NATO")
    c = HoldTiles(target_hexes=[Hex(1, 0), Hex(2, 0)], turns_required=3)
    c.evaluate(s, "NATO")              # 1
    c.evaluate(s, "NATO")              # 2
    s.set_tile_owner(Hex(2, 0), "BRICS")
    assert not c.evaluate(s, "NATO")
    assert c.consecutive_turns == 0


def test_hold_tiles_empty_target_never_satisfied():
    s = _state_with_hqs()
    c = HoldTiles(target_hexes=[], turns_required=1)
    assert not c.evaluate(s, "NATO")


def test_hold_tiles_missing_tile_resets_counter():
    """Target hex that isn't in state.tiles should reset progress."""
    s = _state_with_hqs()
    c = HoldTiles(target_hexes=[Hex(99, 99)], turns_required=1)  # nonexistent
    assert not c.evaluate(s, "NATO")
    assert c.consecutive_turns == 0


# ---------------------------------------------------------------------------
# OwnAllOfTerrain
# ---------------------------------------------------------------------------

def test_own_all_terrain_true_when_full_ownership():
    s = _state_with_hqs()
    s.set_tile_owner(Hex(1, 0), "NATO")
    s.set_tile_owner(Hex(2, 0), "NATO")
    assert OwnAllOfTerrain(terrain_id="city").evaluate(s, "NATO")


def test_own_all_terrain_false_with_one_missing():
    s = _state_with_hqs()
    s.set_tile_owner(Hex(1, 0), "NATO")
    # Hex(2,0) remains neutral
    assert not OwnAllOfTerrain(terrain_id="city").evaluate(s, "NATO")


def test_own_all_terrain_false_when_terrain_absent():
    """Empty match set should not count as a win."""
    s = _state_with_hqs()
    assert not OwnAllOfTerrain(terrain_id="airfield").evaluate(s, "NATO")


# ---------------------------------------------------------------------------
# EliminateFaction
# ---------------------------------------------------------------------------

def test_eliminate_faction_true_when_no_units_no_hq():
    s = _state_with_hqs()
    s.set_tile_owner(Hex(5, 0), "NATO")  # BRICS loses HQ
    assert EliminateFaction(target_faction="BRICS").evaluate(s, "NATO")


def test_eliminate_faction_false_with_one_unit():
    s = _state_with_hqs()
    s.set_tile_owner(Hex(5, 0), "NATO")  # BRICS loses HQ
    s.add_unit(Unit("nato_inf_l", "BRICS", Hex(3, 0)))
    assert not EliminateFaction(target_faction="BRICS").evaluate(s, "NATO")


# ---------------------------------------------------------------------------
# DestroyUnitType
# ---------------------------------------------------------------------------

def test_destroy_unit_type_false_when_alive():
    s = _state_with_hqs()
    s.add_unit(Unit("nato_jet_l", "BRICS", Hex(3, 0)))
    c = DestroyUnitType(type_id="nato_jet_l", owner_faction="BRICS")
    assert not c.evaluate(s, "NATO")


def test_destroy_unit_type_true_when_absent():
    s = _state_with_hqs()
    c = DestroyUnitType(type_id="nato_jet_l", owner_faction="BRICS")
    assert c.evaluate(s, "NATO")


# ---------------------------------------------------------------------------
# VictoryConfig composition
# ---------------------------------------------------------------------------

def test_victory_config_any_mode_one_wins():
    s = _state_with_hqs()
    s.set_tile_owner(Hex(5, 0), "NATO")
    cfg = VictoryConfig(
        win_conditions=[
            DestroyHQ(target_faction="BRICS"),
            OwnAllOfTerrain(terrain_id="airfield"),  # false
        ],
        win_mode="any",
    )
    assert cfg.evaluate(s, "NATO") == Outcome.WON


def test_victory_config_all_mode_requires_every():
    s = _state_with_hqs()
    s.set_tile_owner(Hex(5, 0), "NATO")
    # One met, one not
    cfg = VictoryConfig(
        win_conditions=[
            DestroyHQ(target_faction="BRICS"),
            OwnAllOfTerrain(terrain_id="airfield"),  # false
        ],
        win_mode="all",
    )
    assert cfg.evaluate(s, "NATO") == Outcome.PENDING


def test_victory_config_loss_takes_priority():
    """Even if win condition is true, loss condition pre-empts."""
    s = _state_with_hqs()
    # Both HQs neutralised: NATO win cond AND lose cond both true
    s.set_tile_owner(Hex(0, 0), None)   # NATO HQ neutral
    s.set_tile_owner(Hex(5, 0), None)   # BRICS HQ neutral
    cfg = VictoryConfig(
        win_conditions=[DestroyHQ(target_faction="BRICS")],
        lose_conditions=[DestroyHQ(target_faction="NATO")],
    )
    assert cfg.evaluate(s, "NATO") == Outcome.LOST


def test_victory_config_empty_conditions_returns_pending():
    s = _state_with_hqs()
    cfg = VictoryConfig()
    assert cfg.evaluate(s, "NATO") == Outcome.PENDING


def test_victory_config_unknown_mode_raises():
    s = _state_with_hqs()
    cfg = VictoryConfig(
        win_conditions=[DestroyHQ(target_faction="BRICS")],
        win_mode="weird",
    )
    with pytest.raises(ValueError, match="Unknown victory mode"):
        cfg.evaluate(s, "NATO")


def test_victory_config_evaluates_both_sides_for_stateful_conditions():
    """HoldTiles counter must still advance even when win is already decided."""
    s = _state_with_hqs()
    s.set_tile_owner(Hex(1, 0), "NATO")
    hold = HoldTiles(target_hexes=[Hex(1, 0)], turns_required=5)
    cfg = VictoryConfig(
        win_conditions=[DestroyHQ(target_faction="BRICS")],
        lose_conditions=[hold],  # weird lose condition for test purposes
    )
    # Win condition not met yet; lose condition just counts
    cfg.evaluate(s, "NATO")
    assert hold.consecutive_turns == 1


# ---------------------------------------------------------------------------
# default_victory_config
# ---------------------------------------------------------------------------

def test_default_victory_config_one_enemy():
    cfg = default_victory_config(own_faction="NATO", enemy_factions=["BRICS"])
    assert len(cfg.win_conditions) == 1
    assert cfg.win_mode == "any"
    assert isinstance(cfg.win_conditions[0], DestroyHQ)
    assert cfg.win_conditions[0].target_faction == "BRICS"
    assert isinstance(cfg.lose_conditions[0], DestroyHQ)


def test_default_victory_config_multi_enemy_uses_all():
    cfg = default_victory_config(own_faction="NATO", enemy_factions=["BRICS", "GUERILLA"])
    assert cfg.win_mode == "all"   # must destroy ALL enemy HQs
    assert len(cfg.win_conditions) == 2


# ---------------------------------------------------------------------------
# Scenario JSON parsing
# ---------------------------------------------------------------------------

def test_condition_from_dict_destroy_hq():
    c = condition_from_dict({"type": "destroy_hq", "target_faction": "BRICS"})
    assert isinstance(c, DestroyHQ)
    assert c.target_faction == "BRICS"


def test_condition_from_dict_hold_tiles_converts_hexes():
    c = condition_from_dict({
        "type": "hold_tiles",
        "target_hexes": [[1, 0], [2, 0]],
        "turns_required": 5,
    })
    assert isinstance(c, HoldTiles)
    assert c.target_hexes == [Hex(1, 0), Hex(2, 0)]
    assert c.turns_required == 5


def test_condition_from_dict_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown condition type"):
        condition_from_dict({"type": "nuke_everyone"})


def test_condition_from_dict_missing_type_raises():
    with pytest.raises(ValueError, match="missing 'type'"):
        condition_from_dict({"target_faction": "BRICS"})


def test_victory_config_from_dict():
    cfg = victory_config_from_dict({
        "win_conditions":  [{"type": "destroy_hq", "target_faction": "BRICS"}],
        "win_mode":        "any",
        "lose_conditions": [{"type": "destroy_hq", "target_faction": "NATO"}],
        "lose_mode":       "any",
    })
    assert len(cfg.win_conditions) == 1
    assert isinstance(cfg.win_conditions[0], DestroyHQ)
    assert isinstance(cfg.lose_conditions[0], DestroyHQ)


# ---------------------------------------------------------------------------
# GameState integration
# ---------------------------------------------------------------------------

def test_game_over_false_with_no_outcomes():
    s = _state_with_hqs()
    assert not s.game_over


def test_evaluate_victory_populates_outcomes():
    s = _state_with_hqs()
    s.victory_configs["NATO"]  = default_victory_config("NATO",  ["BRICS"])
    s.victory_configs["BRICS"] = default_victory_config("BRICS", ["NATO"])
    s.evaluate_victory()
    assert s.outcomes["NATO"]  == Outcome.PENDING
    assert s.outcomes["BRICS"] == Outcome.PENDING
    assert not s.game_over


def test_evaluate_victory_marks_defeated_on_loss():
    s = _state_with_hqs()
    s.victory_configs["NATO"]  = default_victory_config("NATO",  ["BRICS"])
    s.victory_configs["BRICS"] = default_victory_config("BRICS", ["NATO"])
    # BRICS captures NATO HQ
    s.set_tile_owner(Hex(0, 0), "BRICS")
    s.evaluate_victory()
    assert s.outcomes["NATO"]  == Outcome.LOST
    assert s.outcomes["BRICS"] == Outcome.WON
    assert s.factions[0].defeated  # NATO marked defeated


def test_end_turn_triggers_victory_eval():
    s = _state_with_hqs()
    s.victory_configs["NATO"]  = default_victory_config("NATO",  ["BRICS"])
    s.victory_configs["BRICS"] = default_victory_config("BRICS", ["NATO"])
    # Pre-mutate: NATO captures BRICS HQ
    s.set_tile_owner(Hex(5, 0), "NATO")
    s.end_turn()
    assert s.game_over
    assert s.winner() == "NATO"


def test_winner_returns_winning_faction():
    s = _state_with_hqs()
    s.outcomes["NATO"]  = Outcome.WON
    s.outcomes["BRICS"] = Outcome.LOST
    assert s.winner() == "NATO"


def test_winner_returns_none_when_pending():
    s = _state_with_hqs()
    s.outcomes["NATO"]  = Outcome.PENDING
    s.outcomes["BRICS"] = Outcome.PENDING
    assert s.winner() is None


# ---------------------------------------------------------------------------
# Pickle compatibility
# ---------------------------------------------------------------------------

def test_state_with_victory_pickles():
    s = _state_with_hqs()
    s.victory_configs["NATO"] = VictoryConfig(
        win_conditions=[DestroyHQ(target_faction="BRICS"),
                        HoldTiles(target_hexes=[Hex(1, 0)], turns_required=3)],
    )
    s.evaluate_victory()
    blob = pickle.dumps(s)
    s2 = pickle.loads(blob)
    assert s2.outcomes["NATO"] == s.outcomes["NATO"]
    assert s2.victory_configs["NATO"].win_conditions[0].target_faction == "BRICS"
    # HoldTiles counter survives pickle
    assert s2.victory_configs["NATO"].win_conditions[1].consecutive_turns == \
           s.victory_configs["NATO"].win_conditions[1].consecutive_turns
