# Balance ideas (effort: hours to days each)

These are data-driven or design-level balance improvements that don't
require major engine changes.  Each can be picked up independently.

---

## 1. More campaign missions (M6-M10)

**Summary**: Five additional missions beyond the current M1-M5 arc,
covering the remaining faction matchups and a final boss-level showdown.
Suggested arc:

- **M6 -- Guerilla vs BRICS**: Defend a jungle supply route for 15 turns
  with inferior forces.  Victory = hold three supply caches.
- **M7 -- NATO vs BRICS + GUERILLA (3-faction)**: NATO must prevent
  BRICS and Guerilla from allying.  First 10 turns are 2v1; after BRICS
  captures a radio tower, Guerilla flips to neutral.  Victory = destroy
  both HQs before that happens.
- **M8 -- Siege**: Assault a heavily fortified BRICS position with only
  3-turn reinforcement windows.
- **M9 -- Island Hopping**: Coastal map; units embark/disembark via
  bridge tiles.  First faction to hold all 5 islands wins.
- **M10 -- Endgame**: All three factions converge on a single central
  objective.  3-faction battle, NATO player vs 2 AIs.

**Files to touch**:
- `data/scenarios/m6.json` -- `m10.json` (new files, no engine change).
- `main.py` -- extend `SCENARIOS` list.

**Effort**: ~2-3 days per mission (map authoring + balance testing).
3-faction support would need a `GameState.factions` length increase --
verify no code assumes exactly 2 factions.

---

## 2. More skirmish maps

**Summary**: Add 5 more hand-crafted skirmish maps to supplement the 3
existing ones + random procgen:

- **Archipelago** (22x14) -- island clusters, naval chokepoints.
- **City Block** (18x12) -- dense urban grid, rivers = canals.
- **Mountain Pass** (20x16) -- two high ridges with a narrow pass; heavy
  vehicle-hostile.
- **Steppe** (30x10) -- wide open, long vision; favors recon + jets.
- **Industrial Zone** (24x14) -- factory tiles give +10cr/turn bonus to
  owner; lots of oil wells.

**Files to touch**:
- `data/skirmish/map_*.json` (5 new files).
- `main.py` -- extend `SKIRMISH_MAPS` list.

**Effort**: ~1 day per map.  All JSON-driven; no engine change.

---

## 3. Veterancy decay on reload (optional mode)

**Summary**: When a save is reloaded, units above Veteran rank (level 5)
lose 1 XP per reload.  Discourages save-scumming to protect elite units
without invalidating the save system.  Toggle in pre-match config.

**Why**: High-level units are very powerful.  A mild reload penalty keeps
elite units precious without hard-blocking the player.

**Files to touch**:
- `src/persistence/save.py` -- `load_state()` accepts
  `xp_decay_on_reload: bool`; subtracts 1 XP (min 0) from each unit
  above level 5.
- `main.py` -- pass flag based on pre-match config toggle.
- `src/engine/veterancy.py` -- expose `level_for_xp(xp)` helper to
  recalculate level after decay.

**Effort**: ~half a day.

---

## 4. Per-mission difficulty scaling

**Summary**: Instead of a single Normal/Hard/Insane toggle that applies
the same credit buff to all missions, each mission JSON specifies its own
difficulty multipliers tuned during playtesting.

**Why**: M4 (Last Stand) is already hard on Normal; M1 is easy on Hard.
Blanket buffs over-tune easy missions and under-tune hard ones.

**Schema addition**:
```json
"difficulty_overrides": {
  "hard":   {"ai_credits_bonus": 200, "ai_oil_bonus": 2,
              "personality": "aggressive"},
  "insane": {"ai_credits_bonus": 500, "ai_oil_bonus": 4,
              "personality": "predator"}
}
```

**Files to touch**:
- `data/scenarios/m*.json` -- add `difficulty_overrides` block.
- `src/engine/scenario.py` -- expose `meta["difficulty_overrides"]`.
- `main.py` -- `_apply_difficulty` reads mission overrides first, falls
  back to global defaults.

**Effort**: ~1 day (schema + parser).  Actual balance tuning per mission
is ~30 min of playtesting each.

---

## 5. Achievement system

**Summary**: A lightweight achievement tracker stored in a local
`saves/achievements.json` file.  Display unlocked achievements on the
main menu.  Suggested starter set:

- **First Blood** -- win Mission 1 on any difficulty.
- **Ironclad** -- win any mission without losing a unit.
- **Speed Run** -- win any mission in under 20 turns.
- **Decimator** -- destroy 50 enemy units in a single campaign run.
- **Landlord** -- own all neutral cities simultaneously.
- **Ghost** -- complete a mission with Guerilla faction without any unit
  dying.
- **Mythic Commander** -- level any unit to Mythic rank (level 25).
- **Veteran Campaign** -- complete all 5 missions on Hard.

**Files to touch**:
- New `src/persistence/achievements.py` -- `AchievementSet` dataclass;
  `load()` / `save()` / `check_and_unlock(event, state)`; returns list
  of newly-unlocked IDs.
- `main.py` -- call `check_and_unlock` at end-turn and game-over; flash
  newly-unlocked achievement names (like save_flash pattern); main menu
  button "Achievements" shows a grid of locked/unlocked with icons.

**Effort**: ~2-3 days (tracker: 1 day; UI: 1 day; defining 15+ conditions:
1 day).
