"""
JSON save / load for GameState.

File format (version 1)
-----------------------
::

    {
      "version": 1,
      "scenario_slug": "m1",
      "turn_number": 5,
      "active_faction_idx": 0,
      "factions": [
        {"id": "NATO", "name": "NATO", "color": [30,80,200],
         "credits": 900, "oil": 8, "tier": 1, "defeated": false, "is_ai": false}
      ],
      "tiles": [
        {"hex": [2, 3], "terrain_id": "hq", "owner": "NATO",
         "capture_progress": 0, "capturing_faction": null}
      ],
      "units": [
        {"uid": 1, "type_id": "nato_inf_l", "faction": "NATO",
         "hex": [1, 3], "hp": 10, "has_moved": false, "has_attacked": false}
      ],
      "explored": {"NATO": [[0,0], ...], "BRICS": [[18,11], ...]},
      "outcomes": {"NATO": "pending", "BRICS": "pending"},
      "victory_configs": {
        "NATO": {
          "win_conditions": [{"type": "destroy_hq", "target_faction": "BRICS"}],
          "win_mode": "any",
          "lose_conditions": [{"type": "destroy_hq", "target_faction": "NATO"}],
          "lose_mode": "any"
        }
      }
    }

``explored`` is the persistent fog-of-war memory (every hex each faction has
ever seen).  ``_visible_cache`` is *not* saved — it is transient and recomputed
lazily on first access after load.

``victory_configs`` round-trips fully, including stateful ``HoldTiles``
``consecutive_turns`` counters, so long games survive save/reload mid-hold.

UID safety
----------
Unit UIDs are assigned by a module-level counter in ``unit.py``.  After loading
a save, ``advance_uid_counter(max_uid + 1)`` is called so freshly built units
cannot collide with restored ones.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from src.engine.combat import load_damage_matrix
from src.engine.hex import Hex
from src.engine.state import Faction, GameState
from src.engine.tile import Tile, load_terrain
from src.engine.unit import Unit, advance_uid_counter, load_units
from src.engine.victory import Outcome, victory_config_from_dict, victory_config_to_dict

SAVE_FORMAT_VERSION = 1
SAVE_DIR = Path("saves")
NUM_SLOTS = 3


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def autosave_path(scenario_slug: str, saves_dir: Path = SAVE_DIR) -> Path:
    """Return the autosave path for *scenario_slug*."""
    return saves_dir / f"{scenario_slug}_autosave.json"


def slot_path(scenario_slug: str, slot: int, saves_dir: Path = SAVE_DIR) -> Path:
    """Return the manual-slot path for *scenario_slug* and *slot* (1-based)."""
    if not 1 <= slot <= NUM_SLOTS:
        raise ValueError(f"Slot must be 1-{NUM_SLOTS}, got {slot}")
    return saves_dir / f"{scenario_slug}_save_{slot}.json"


def thumbnail_path(json_path: Path) -> Path:
    """Return the PNG thumbnail path that accompanies *json_path* (CP-35).

    E.g. ``saves/m1_autosave.json`` -> ``saves/m1_autosave.png``.
    """
    return json_path.with_suffix(".png")


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _faction_to_dict(f: Faction) -> dict[str, Any]:
    return {
        "id":       f.id,
        "name":     f.name,
        "color":    list(f.color),
        "credits":  f.credits,
        "oil":      f.oil,
        "tier":     f.tier,
        "defeated": f.defeated,
        "is_ai":    f.is_ai,
    }


def _tile_to_dict(t: Tile) -> dict[str, Any]:
    return {
        "hex":               [t.hex.q, t.hex.r],
        "terrain_id":        t.terrain_id,
        "owner":             t.owner_faction,
        "capture_progress":  t.capture_progress,
        "capturing_faction": t.capturing_faction,
    }


def _unit_to_dict(u: Unit) -> dict[str, Any]:
    return {
        "uid":         u.uid,
        "type_id":     u.type_id,
        "faction":     u.faction,
        "hex":         [u.hex.q, u.hex.r],
        "hp":          u.hp,
        "has_moved":   u.has_moved,
        "has_attacked": u.has_attacked,
        "stance":      u.stance,
        "level":       u.level,
        "xp":          u.xp,
    }


# ---------------------------------------------------------------------------
# State → dict
# ---------------------------------------------------------------------------

def state_to_dict(state: GameState, scenario_slug: str = "") -> dict[str, Any]:
    """Serialise *state* to a plain-Python dict suitable for ``json.dump``."""
    return {
        "version":            SAVE_FORMAT_VERSION,
        "scenario_slug":      scenario_slug,
        "turn_number":        state.turn_number,
        "active_faction_idx": state.active_faction_idx,
        "factions": [_faction_to_dict(f) for f in state.factions],
        "tiles":    [_tile_to_dict(t)    for t in state.tiles.values()],
        "units":    [_unit_to_dict(u)    for u in state.units.values()],
        "explored": {
            fid: [[h.q, h.r] for h in hexes]
            for fid, hexes in state.explored.items()
        },
        "outcomes": {fid: o.value for fid, o in state.outcomes.items()},
        "victory_configs": {
            fid: victory_config_to_dict(cfg)
            for fid, cfg in state.victory_configs.items()
        },
    }


# ---------------------------------------------------------------------------
# dict → State
# ---------------------------------------------------------------------------

def dict_to_state(data: dict[str, Any]) -> GameState:
    """
    Reconstruct a ``GameState`` from a previously serialised dict.

    Calls ``load_terrain`` / ``load_units`` / ``load_damage_matrix`` on entry
    (idempotent; each is a no-op if already loaded).
    """
    load_terrain()
    load_units()
    load_damage_matrix()

    version = data.get("version", 1)
    if version != SAVE_FORMAT_VERSION:
        raise ValueError(
            f"Unsupported save version {version!r} "
            f"(expected {SAVE_FORMAT_VERSION})"
        )

    # ── Factions ──────────────────────────────────────────────────────────
    factions = [
        Faction(
            id=fd["id"],
            name=fd["name"],
            color=tuple(fd["color"]),
            credits=int(fd["credits"]),
            oil=int(fd["oil"]),
            tier=int(fd["tier"]),
            defeated=bool(fd["defeated"]),
            is_ai=bool(fd["is_ai"]),
        )
        for fd in data["factions"]
    ]

    # ── Tiles ─────────────────────────────────────────────────────────────
    tiles: dict[Hex, Tile] = {}
    for td in data["tiles"]:
        q, r = td["hex"]
        h = Hex(q, r)
        tiles[h] = Tile(
            hex=h,
            terrain_id=td["terrain_id"],
            owner_faction=td.get("owner"),
            capture_progress=int(td.get("capture_progress", 0)),
            capturing_faction=td.get("capturing_faction"),
        )

    # ── GameState shell ───────────────────────────────────────────────────
    state = GameState(
        factions=factions,
        tiles=tiles,
        active_faction_idx=int(data.get("active_faction_idx", 0)),
        turn_number=int(data.get("turn_number", 1)),
    )

    # ── Units ─────────────────────────────────────────────────────────────
    max_uid = 0
    for ud in data.get("units", []):
        q, r = ud["hex"]
        unit = Unit(
            type_id=ud["type_id"],
            faction=ud["faction"],
            hex=Hex(q, r),
            hp=int(ud["hp"]),
            has_moved=bool(ud["has_moved"]),
            has_attacked=bool(ud["has_attacked"]),
            uid=int(ud["uid"]),   # explicit uid — bypasses auto-counter
            stance=str(ud.get("stance", "attack")),    # back-compat default
            level=int(ud.get("level", 1)),             # back-compat: rookie
            xp=int(ud.get("xp", 0)),
        )
        state.units[unit.uid] = unit   # bypass add_unit to avoid fog invalidation
        max_uid = max(max_uid, unit.uid)

    if max_uid > 0:
        advance_uid_counter(max_uid + 1)

    # ── Fog: explored sets ────────────────────────────────────────────────
    state.explored = {
        fid: {Hex(q, r) for q, r in pairs}
        for fid, pairs in data.get("explored", {}).items()
    }
    # _visible_cache stays empty — recomputed lazily on first access.

    # ── Victory ───────────────────────────────────────────────────────────
    state.outcomes = {
        fid: Outcome(v)
        for fid, v in data.get("outcomes", {}).items()
    }
    state.victory_configs = {
        fid: victory_config_from_dict(cfg_dict)
        for fid, cfg_dict in data.get("victory_configs", {}).items()
    }

    return state


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def save_state(
    state: GameState,
    path: "str | Path",
    scenario_slug: str = "",
) -> None:
    """Serialise *state* to JSON at *path*.  Creates parent directories."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    d = state_to_dict(state, scenario_slug=scenario_slug)
    with p.open("w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2)


def load_state(path: "str | Path") -> tuple[GameState, dict[str, Any]]:
    """
    Load a save file and return ``(state, save_meta)``.

    ``save_meta`` contains at minimum ``"scenario_slug"`` and ``"version"``.
    """
    p = Path(path)
    with p.open(encoding="utf-8") as fh:
        data = json.load(fh)
    state = dict_to_state(data)
    meta = {
        "scenario_slug": data.get("scenario_slug", ""),
        "version":       data.get("version", 1),
    }
    return state, meta


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def save_autosave(
    state: GameState,
    scenario_slug: str,
    saves_dir: Path = SAVE_DIR,
) -> Path:
    """Write autosave; return the path written."""
    p = autosave_path(scenario_slug, saves_dir)
    save_state(state, p, scenario_slug=scenario_slug)
    return p


def save_slot(
    state: GameState,
    slot: int,
    scenario_slug: str,
    saves_dir: Path = SAVE_DIR,
) -> Path:
    """Write to manual save slot (1-based); return the path written."""
    p = slot_path(scenario_slug, slot, saves_dir)
    save_state(state, p, scenario_slug=scenario_slug)
    return p


# ---------------------------------------------------------------------------
# Save discovery (used by the load-game menu in main.py)
# ---------------------------------------------------------------------------

def _read_save_meta(path: Path, label: str) -> dict[str, Any]:
    """Read the minimal header of one save file for display in a load menu."""
    thumb = thumbnail_path(path)
    if not path.exists():
        return {
            "path": path, "label": label, "turn": None, "exists": False,
            "thumb_path": thumb,
        }
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return {
            "path":       path,
            "label":      label,
            "turn":       data.get("turn_number"),
            "exists":     True,
            "thumb_path": thumb,
        }
    except Exception:
        return {
            "path": path, "label": label, "turn": None, "exists": True,
            "thumb_path": thumb,
        }


def list_saves(
    scenario_slug: str,
    saves_dir: Path = SAVE_DIR,
) -> list[dict[str, Any]]:
    """Return a list of save-file metadata dicts for *scenario_slug*.

    Order: autosave first, then slots 1..NUM_SLOTS.  Each dict has::

        {"path": Path, "label": str, "turn": int|None, "exists": bool}
    """
    results: list[dict[str, Any]] = []
    results.append(_read_save_meta(autosave_path(scenario_slug, saves_dir), "Autosave"))
    for slot in range(1, NUM_SLOTS + 1):
        results.append(
            _read_save_meta(slot_path(scenario_slug, slot, saves_dir), f"Slot {slot}")
        )
    return results
