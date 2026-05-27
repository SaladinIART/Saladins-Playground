"""
Unit types (immutable) and unit instances (mutable).

UnitType  — stat block loaded from data/units.json. One per unit kind.
Unit      — a live unit on the map. Has position, current HP, action flags.

Pattern mirrors tile.py / TerrainType: a global registry holds all UnitTypes;
Unit instances reference them by type_id.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from typing import Optional

from src.engine.hex import Hex

_DATA_DIR = Path(__file__).parent.parent.parent / "data"

VALID_UNIT_CLASSES = frozenset(
    {"infantry", "engineer", "recon", "vehicle", "artillery", "aa",
     "sniper", "jet", "helicopter", "bomber"}
)
VALID_MOVE_CATEGORIES = frozenset({"foot", "wheeled", "tracked", "towed", "air"})


@dataclass(frozen=True)
class UnitType:
    id: str
    name: str
    faction: str            # "NATO" | "BRICS" | "GUERILLA"
    tier: int               # 1-3 (gated by Research HQ)
    unit_class: str         # infantry, vehicle, artillery, etc.
    move_category: str      # maps to TerrainType.move_cost keys
    hp: int                 # max HP (always 10 in base design)
    atk: int                # attack modifier (0-10)
    def_: int               # defence modifier (0-5), stored as def_ to avoid keyword clash
    move: int               # movement points per turn
    vision: int             # vision radius in hexes
    range_min: int          # min attack range (1 = adjacent; >1 = indirect-fire only)
    range_max: int          # max attack range
    cost_credits: int
    cost_oil: int
    upkeep_oil: int         # oil consumed per turn while alive
    can_capture: bool       # engineer-class flag: can capture buildings
    stealth: bool           # invisible beyond 1 hex to enemies (Guerilla units)
    flying: bool            # immune to ground-only weapons; ignores terrain costs
    amphibious: bool        # can cross river tiles
    color: tuple[int, int, int]  # (R, G, B) programmer-art tint

    def is_indirect(self) -> bool:
        return self.range_min > 1

    def in_range(self, dist: int) -> bool:
        return self.range_min <= dist <= self.range_max


# ---------------------------------------------------------------------------
# Unit instance
# ---------------------------------------------------------------------------

_id_counter: count = count(1)


@dataclass
class Unit:
    type_id: str
    faction: str
    hex: Hex
    hp: int = field(default=10)
    has_moved: bool = field(default=False)
    has_attacked: bool = field(default=False)
    uid: int = field(default_factory=lambda: next(_id_counter))

    @property
    def unit_type(self) -> UnitType:
        return get(self.type_id)

    def is_alive(self) -> bool:
        return self.hp > 0

    def is_exhausted(self) -> bool:
        """True when the unit can take no more actions this turn."""
        return self.has_moved and self.has_attacked

    def reset_turn(self) -> None:
        """Call at the start of this faction's turn."""
        self.has_moved = False
        self.has_attacked = False

    def apply_damage(self, amount: int) -> None:
        self.hp = max(0, self.hp - amount)

    def can_act(self) -> bool:
        return self.is_alive() and not self.is_exhausted()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_registry: dict[str, UnitType] = {}


def load_units(path: Optional[Path] = None) -> dict[str, UnitType]:
    """Load unit type definitions from JSON, populate registry, return it."""
    if path is None:
        path = _DATA_DIR / "units.json"
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    _registry.clear()
    for entry in raw["unit_types"]:
        if "id" not in entry:   # skip comment-only dicts (e.g. {"_comment": "..."})
            continue
        color = tuple(entry["color"])
        ut = UnitType(
            id=entry["id"],
            name=entry["name"],
            faction=entry["faction"],
            tier=int(entry["tier"]),
            unit_class=entry["unit_class"],
            move_category=entry["move_category"],
            hp=int(entry["hp"]),
            atk=int(entry["atk"]),
            def_=int(entry["def"]),
            move=int(entry["move"]),
            vision=int(entry["vision"]),
            range_min=int(entry["range_min"]),
            range_max=int(entry["range_max"]),
            cost_credits=int(entry["cost_credits"]),
            cost_oil=int(entry["cost_oil"]),
            upkeep_oil=int(entry["upkeep_oil"]),
            can_capture=bool(entry["can_capture"]),
            stealth=bool(entry["stealth"]),
            flying=bool(entry["flying"]),
            amphibious=bool(entry["amphibious"]),
            color=(int(color[0]), int(color[1]), int(color[2])),
        )
        _registry[ut.id] = ut
    return _registry


def get(type_id: str) -> UnitType:
    if not _registry:
        load_units()
    return _registry[type_id]


def all_units() -> dict[str, UnitType]:
    if not _registry:
        load_units()
    return dict(_registry)


def units_for_faction(faction: str) -> list[UnitType]:
    return [ut for ut in all_units().values() if ut.faction == faction]


def units_for_tier(faction: str, max_tier: int) -> list[UnitType]:
    """Available build options given a faction and current tech tier."""
    return [
        ut for ut in all_units().values()
        if ut.faction == faction and ut.tier <= max_tier
    ]


def advance_uid_counter(min_uid: int) -> None:
    """Advance the global UID counter so the next auto-assigned UID is >= min_uid.

    Call this after loading a saved game that contains pre-existing units to
    prevent the new-unit counter from issuing UIDs that collide with saved ones.
    """
    global _id_counter
    _id_counter = count(min_uid)
