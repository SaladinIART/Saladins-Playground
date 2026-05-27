"""
Scenario loader: reads a JSON file and returns a configured GameState.

JSON format (all fields optional except where noted)::

    {
      "name": "Mission 1",
      "description": "...",
      "map": {
        "width": 21,                    # default 20
        "height": 15,                   # default 15
        "default_terrain": "plain",     # terrain_id for unlisted hexes
        "tiles": [
          {"hex": [q, r], "terrain": "forest"},
          {"hex": [q, r], "terrain": "hq", "owner": "NATO"},
          {"comment": "..."}            # comment-only dicts are skipped
        ]
      },
      "factions": [
        {
          "id": "NATO", "name": "NATO", "color": [30,80,200],
          "credits": 600, "oil": 5, "tier": 1, "is_ai": false
        },
        {
          "id": "BRICS", ..., "is_ai": true,
          "personality": {"name": "balanced", "weights": {}}
        }
      ],
      "units": [
        {"type_id": "nato_inf_l", "faction": "NATO", "hex": [q, r]},
        {"comment": "..."}            # comment-only dicts are skipped
      ],
      "victory": {
        "NATO":  {"win_conditions": [...], "lose_conditions": [...]},
        "BRICS": {"win_conditions": [...], "lose_conditions": [...]}
      }
    }

Returns ``(state, meta)`` where ``meta`` is::

    {
      "name":         str,
      "description":  str,
      "personalities": {faction_id: personality_dict, ...}
    }

The caller is responsible for wiring ``meta["personalities"]`` into the AI
driver (``take_turn_steps(state, fid, personality_from_dict(p))``) so that
the engine core stays AI-free.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.engine.combat import load_damage_matrix
from src.engine.hex import Hex
from src.engine.state import Faction, GameState
from src.engine.tile import Tile, load_terrain
from src.engine.unit import Unit, load_units
from src.engine.victory import victory_config_from_dict


def load_scenario(path: "str | Path") -> tuple[GameState, dict[str, Any]]:
    """
    Parse a scenario JSON file and return ``(state, meta)``.

    Loads terrain / unit / damage-matrix data files on first call (idempotent —
    subsequent calls are no-ops thanks to the loaders' internal guards).

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    json.JSONDecodeError
        If the file is not valid JSON.
    KeyError / ValueError
        If required fields are missing or malformed.
    """
    load_terrain()
    load_units()
    load_damage_matrix()

    p = Path(path)
    with p.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)

    # ── Meta ──────────────────────────────────────────────────────────────
    meta: dict[str, Any] = {
        "name":          data.get("name", "Unnamed scenario"),
        "description":   data.get("description", ""),
        "personalities": {},
    }

    # ── Factions ──────────────────────────────────────────────────────────
    factions: list[Faction] = []
    for fd in data.get("factions", []):
        if "id" not in fd:
            continue          # skip comment/malformed entries
        factions.append(Faction(
            id=fd["id"],
            name=fd.get("name", fd["id"]),
            color=tuple(fd.get("color", [128, 128, 128])),
            credits=int(fd.get("credits", 0)),
            oil=int(fd.get("oil", 0)),
            tier=int(fd.get("tier", 1)),
            is_ai=bool(fd.get("is_ai", False)),
        ))
        if "personality" in fd:
            meta["personalities"][fd["id"]] = fd["personality"]

    if not factions:
        raise ValueError("Scenario must define at least one faction.")

    # ── Tiles ─────────────────────────────────────────────────────────────
    map_data        = data.get("map", {})
    width           = int(map_data.get("width",  20))
    height          = int(map_data.get("height", 15))
    default_terrain = map_data.get("default_terrain", "plain")

    tiles: dict[Hex, Tile] = {}

    # Fill the grid with the default terrain.
    for q in range(width):
        for r in range(height):
            h = Hex(q, r)
            tiles[h] = Tile(hex=h, terrain_id=default_terrain)

    # Override with explicitly listed tiles (comment-only dicts are skipped).
    for td in map_data.get("tiles", []):
        if "hex" not in td:
            continue
        q, r = td["hex"]
        h = Hex(q, r)
        tiles[h] = Tile(
            hex=h,
            terrain_id=td["terrain"],
            owner_faction=td.get("owner"),
        )

    # ── GameState ─────────────────────────────────────────────────────────
    state = GameState(factions=factions, tiles=tiles)

    # ── Starting units ────────────────────────────────────────────────────
    for ud in data.get("units", []):
        if "type_id" not in ud:
            continue          # skip comment/malformed entries
        q, r = ud["hex"]
        unit = Unit(
            type_id=ud["type_id"],
            faction=ud["faction"],
            hex=Hex(q, r),
        )
        state.add_unit(unit)

    # ── Victory configs ───────────────────────────────────────────────────
    for fid, vcfg_dict in data.get("victory", {}).items():
        state.victory_configs[fid] = victory_config_from_dict(vcfg_dict)

    return state, meta
