"""
AI personality — per-scenario weight overrides on top of DEFAULT_WEIGHTS.

A ``Personality`` is just a name + a dict of weight overrides; each scenario
JSON can supply its own (see ``from_dict``).  Presets BALANCED / AGGRESSIVE /
DEFENSIVE are provided for tests and the default skirmish opponent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Personality:
    """Named bundle of weight overrides.  Empty dict = pure default behaviour."""
    name: str = "balanced"
    weight_overrides: dict[str, float] = field(default_factory=dict)


BALANCED = Personality(name="balanced")

AGGRESSIVE = Personality(
    name="aggressive",
    weight_overrides={
        "attack_damage":           5.0,
        "attack_kill_bonus":       80.0,
        "approach_enemy_hq":       40.0,
        "retreat_when_low_hp":     0.8,
        "threat_aversion_base":    0.2,
    },
)

DEFENSIVE = Personality(
    name="defensive",
    weight_overrides={
        "capture_progress":        70.0,
        "capture_continue":        40.0,
        "retreat_when_low_hp":     3.5,
        "threat_aversion_base":    1.5,
        "build_when_low_army":     60.0,
        "build_engineer_bonus":    40.0,
    },
)


def from_dict(d: dict[str, Any]) -> Personality:
    """
    Parse scenario JSON like::

        {"name": "balanced", "weights": {"attack_damage": 4.0}}
    """
    return Personality(
        name=d.get("name", "custom"),
        weight_overrides={k: float(v) for k, v in d.get("weights", {}).items()},
    )
