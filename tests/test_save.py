"""Tests for src/persistence/save.py — JSON round-trip, autosave, slots."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine.combat import load_damage_matrix
from src.engine.hex import Hex
from src.engine.scenario import load_scenario
from src.engine.state import Faction, GameState
from src.engine.tile import Tile, load_terrain
from src.engine.unit import Unit, load_units
from src.engine.victory import (
    DestroyHQ,
    HoldTiles,
    OwnAllOfTerrain,
    Outcome,
    VictoryConfig,
    condition_to_dict,
    victory_config_to_dict,
)
from src.persistence.save import (
    NUM_SLOTS,
    autosave_path,
    dict_to_state,
    load_state,
    save_autosave,
    save_slot,
    save_state,
    slot_path,
    state_to_dict,
)

SCENARIO_PATH = Path(__file__).parent.parent / "data" / "scenarios" / "m1.json"


@pytest.fixture(autouse=True)
def loaded():
    load_terrain()
    load_units()
    load_damage_matrix()


# ---------------------------------------------------------------------------
# Helper: minimal two-faction state
# ---------------------------------------------------------------------------

def _simple_state() -> GameState:
    nato  = Faction(id="NATO",  name="NATO",  color=(30, 80, 200), credits=600, oil=5, is_ai=False)
    brics = Faction(id="BRICS", name="BRICS", color=(200, 30, 30), credits=400, oil=3, is_ai=True)
    tiles: dict[Hex, Tile] = {
        Hex(0, 0): Tile(Hex(0, 0), "hq",   owner_faction="NATO"),
        Hex(5, 5): Tile(Hex(5, 5), "hq",   owner_faction="BRICS"),
        Hex(2, 2): Tile(Hex(2, 2), "city", owner_faction=None),
    }
    state = GameState(factions=[nato, brics], tiles=tiles)
    state.add_unit(Unit("nato_inf_l",    "NATO",  Hex(1, 0)))
    state.add_unit(Unit("nato_engineer", "BRICS", Hex(4, 5)))
    state.victory_configs["NATO"]  = VictoryConfig(
        win_conditions=[DestroyHQ("BRICS")],
        lose_conditions=[DestroyHQ("NATO")],
    )
    state.victory_configs["BRICS"] = VictoryConfig(
        win_conditions=[DestroyHQ("NATO")],
        lose_conditions=[DestroyHQ("BRICS")],
    )
    return state


# ---------------------------------------------------------------------------
# condition_to_dict / victory_config_to_dict (round-trip of types)
# ---------------------------------------------------------------------------

def test_condition_to_dict_destroy_hq():
    d = condition_to_dict(DestroyHQ("BRICS"))
    assert d == {"type": "destroy_hq", "target_faction": "BRICS"}


def test_condition_to_dict_hold_tiles():
    ht = HoldTiles(target_hexes=[Hex(1, 2), Hex(3, 4)], turns_required=5, consecutive_turns=2)
    d = condition_to_dict(ht)
    assert d["type"] == "hold_tiles"
    assert d["turns_required"] == 5
    assert d["consecutive_turns"] == 2
    assert [1, 2] in d["target_hexes"]


def test_condition_to_dict_own_all_terrain():
    d = condition_to_dict(OwnAllOfTerrain("oil_well"))
    assert d == {"type": "own_all_terrain", "terrain_id": "oil_well"}


def test_victory_config_to_dict_round_trips():
    cfg = VictoryConfig(
        win_conditions=[DestroyHQ("BRICS")],
        win_mode="any",
        lose_conditions=[DestroyHQ("NATO")],
        lose_mode="any",
    )
    d = victory_config_to_dict(cfg)
    assert d["win_mode"] == "any"
    assert len(d["win_conditions"]) == 1
    assert d["win_conditions"][0]["type"] == "destroy_hq"


# ---------------------------------------------------------------------------
# state_to_dict / dict_to_state
# ---------------------------------------------------------------------------

def test_state_to_dict_has_required_keys():
    s = _simple_state()
    d = state_to_dict(s, scenario_slug="test")
    for key in ("version", "scenario_slug", "turn_number", "active_faction_idx",
                "factions", "tiles", "units", "explored", "outcomes", "victory_configs"):
        assert key in d


def test_state_to_dict_factions_count():
    s = _simple_state()
    d = state_to_dict(s)
    assert len(d["factions"]) == 2


def test_state_to_dict_tiles_count():
    s = _simple_state()
    d = state_to_dict(s)
    assert len(d["tiles"]) == 3


def test_state_to_dict_units_count():
    s = _simple_state()
    d = state_to_dict(s)
    assert len(d["units"]) == 2


def test_dict_to_state_factions(tmp_path):
    s = _simple_state()
    p = tmp_path / "test.json"
    save_state(s, p)
    s2, _ = load_state(p)
    assert {f.id for f in s2.factions} == {"NATO", "BRICS"}


def test_dict_to_state_credits(tmp_path):
    s = _simple_state()
    p = tmp_path / "test.json"
    save_state(s, p)
    s2, _ = load_state(p)
    assert s2.faction_by_id("NATO").credits  == 600
    assert s2.faction_by_id("BRICS").credits == 400


def test_dict_to_state_tiles(tmp_path):
    s = _simple_state()
    p = tmp_path / "test.json"
    save_state(s, p)
    s2, _ = load_state(p)
    assert len(s2.tiles) == 3
    assert s2.tiles[Hex(0, 0)].terrain_id == "hq"
    assert s2.tiles[Hex(0, 0)].owner_faction == "NATO"


def test_dict_to_state_units(tmp_path):
    s = _simple_state()
    p = tmp_path / "test.json"
    save_state(s, p)
    s2, _ = load_state(p)
    assert len(s2.units) == 2


def test_dict_to_state_unit_uids_preserved(tmp_path):
    s = _simple_state()
    orig_uids = set(s.units.keys())
    p = tmp_path / "test.json"
    save_state(s, p)
    s2, _ = load_state(p)
    assert set(s2.units.keys()) == orig_uids


def test_dict_to_state_unit_hp(tmp_path):
    s = _simple_state()
    # Wound a unit
    first = next(iter(s.units.values()))
    first.hp = 4
    p = tmp_path / "test.json"
    save_state(s, p)
    s2, _ = load_state(p)
    assert s2.units[first.uid].hp == 4


def test_dict_to_state_unit_flags(tmp_path):
    s = _simple_state()
    first = next(iter(s.units.values()))
    first.has_moved = True
    first.has_attacked = True
    p = tmp_path / "test.json"
    save_state(s, p)
    s2, _ = load_state(p)
    u2 = s2.units[first.uid]
    assert u2.has_moved is True
    assert u2.has_attacked is True


def test_dict_to_state_turn_number(tmp_path):
    s = _simple_state()
    s.turn_number = 7
    p = tmp_path / "test.json"
    save_state(s, p)
    s2, _ = load_state(p)
    assert s2.turn_number == 7


def test_dict_to_state_active_faction_idx(tmp_path):
    s = _simple_state()
    s.active_faction_idx = 1
    p = tmp_path / "test.json"
    save_state(s, p)
    s2, _ = load_state(p)
    assert s2.active_faction_idx == 1


def test_dict_to_state_explored(tmp_path):
    s = _simple_state()
    s.explored["NATO"] = {Hex(0, 0), Hex(1, 0)}
    p = tmp_path / "test.json"
    save_state(s, p)
    s2, _ = load_state(p)
    assert Hex(0, 0) in s2.explored["NATO"]
    assert Hex(1, 0) in s2.explored["NATO"]


def test_dict_to_state_victory_configs(tmp_path):
    s = _simple_state()
    p = tmp_path / "test.json"
    save_state(s, p)
    s2, _ = load_state(p)
    assert "NATO" in s2.victory_configs
    cfg = s2.victory_configs["NATO"]
    assert isinstance(cfg.win_conditions[0], DestroyHQ)
    assert cfg.win_conditions[0].target_faction == "BRICS"


def test_dict_to_state_capture_progress(tmp_path):
    s = _simple_state()
    city = s.tiles[Hex(2, 2)]
    city.capture_progress = 2
    city.capturing_faction = "NATO"
    p = tmp_path / "test.json"
    save_state(s, p)
    s2, _ = load_state(p)
    t2 = s2.tiles[Hex(2, 2)]
    assert t2.capture_progress == 2
    assert t2.capturing_faction == "NATO"


def test_dict_to_state_hold_tiles_counter(tmp_path):
    """HoldTiles.consecutive_turns must survive the round-trip."""
    s = _simple_state()
    ht = HoldTiles(target_hexes=[Hex(0, 0)], turns_required=3, consecutive_turns=2)
    s.victory_configs["NATO"] = VictoryConfig(
        win_conditions=[ht], lose_conditions=[]
    )
    p = tmp_path / "test.json"
    save_state(s, p)
    s2, _ = load_state(p)
    loaded_ht = s2.victory_configs["NATO"].win_conditions[0]
    assert isinstance(loaded_ht, HoldTiles)
    assert loaded_ht.consecutive_turns == 2


def test_dict_to_state_outcomes(tmp_path):
    s = _simple_state()
    s.outcomes["NATO"] = Outcome.WON
    p = tmp_path / "test.json"
    save_state(s, p)
    s2, _ = load_state(p)
    assert s2.outcomes["NATO"] == Outcome.WON


def test_uid_counter_advanced_after_load(tmp_path):
    """New units built after loading must not collide with loaded UIDs."""
    s = _simple_state()
    saved_uids = set(s.units.keys())
    p = tmp_path / "test.json"
    save_state(s, p)
    s2, _ = load_state(p)
    # Build a new unit — its uid must not appear in the saved set.
    new_unit = Unit("nato_inf_l", "NATO", Hex(3, 3))
    assert new_unit.uid not in saved_uids


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def test_autosave_path_naming():
    p = autosave_path("m1", Path("/tmp"))
    assert p.name == "m1_autosave.json"


def test_slot_path_naming():
    p = slot_path("m1", 2, Path("/tmp"))
    assert p.name == "m1_save_2.json"


def test_slot_path_rejects_out_of_range():
    with pytest.raises(ValueError):
        slot_path("m1", 0)
    with pytest.raises(ValueError):
        slot_path("m1", NUM_SLOTS + 1)


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def test_save_autosave_creates_file(tmp_path):
    s = _simple_state()
    p = save_autosave(s, "test", saves_dir=tmp_path)
    assert p.exists()


def test_save_autosave_returns_correct_path(tmp_path):
    s = _simple_state()
    p = save_autosave(s, "test", saves_dir=tmp_path)
    assert p.name == "test_autosave.json"


def test_save_slot_creates_file(tmp_path):
    s = _simple_state()
    p = save_slot(s, 1, "test", saves_dir=tmp_path)
    assert p.exists()


def test_save_slot_2_creates_separate_file(tmp_path):
    s = _simple_state()
    p1 = save_slot(s, 1, "test", saves_dir=tmp_path)
    p2 = save_slot(s, 2, "test", saves_dir=tmp_path)
    assert p1 != p2
    assert p1.exists() and p2.exists()


def test_load_after_save_autosave(tmp_path):
    s = _simple_state()
    p = save_autosave(s, "test", saves_dir=tmp_path)
    s2, meta = load_state(p)
    assert meta["scenario_slug"] == "test"
    assert len(s2.factions) == 2


def test_load_after_save_slot(tmp_path):
    s = _simple_state()
    p = save_slot(s, 3, "test", saves_dir=tmp_path)
    s2, _ = load_state(p)
    assert s2.faction_by_id("NATO").credits == 600


# ---------------------------------------------------------------------------
# Scenario integration: m1 round-trip
# ---------------------------------------------------------------------------

def test_m1_scenario_round_trips(tmp_path):
    """Full round-trip with the real Mission 1 map."""
    state, _ = load_scenario(SCENARIO_PATH)
    p = tmp_path / "m1_rt.json"
    save_state(state, p, scenario_slug="m1")
    s2, meta = load_state(p)

    assert meta["scenario_slug"] == "m1"
    assert len(s2.tiles) == len(state.tiles)
    assert len(s2.units) == len(state.units)
    assert {f.id for f in s2.factions} == {f.id for f in state.factions}


def test_m1_hq_ownership_survives_round_trip(tmp_path):
    state, _ = load_scenario(SCENARIO_PATH)
    p = tmp_path / "rt.json"
    save_state(state, p)
    s2, _ = load_state(p)
    assert s2.hq_of("NATO")  is not None
    assert s2.hq_of("BRICS") is not None


def test_m1_end_turn_after_load(tmp_path):
    """State loaded from file should survive an end_turn call."""
    state, _ = load_scenario(SCENARIO_PATH)
    p = tmp_path / "rt.json"
    save_state(state, p)
    s2, _ = load_state(p)
    s2.end_turn()
    assert s2.active_faction.id == "BRICS"


def test_save_file_is_valid_json(tmp_path):
    s = _simple_state()
    p = tmp_path / "out.json"
    save_state(s, p)
    with p.open(encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["version"] == 1


def test_unknown_version_raises(tmp_path):
    s = _simple_state()
    p = tmp_path / "bad.json"
    save_state(s, p)
    with p.open(encoding="utf-8") as fh:
        data = json.load(fh)
    data["version"] = 99
    with p.open("w", encoding="utf-8") as fh:
        json.dump(data, fh)
    with pytest.raises(ValueError, match="Unsupported save version"):
        load_state(p)
