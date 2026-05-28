# Medium ideas (effort: days, not hours)

These are quality-of-life and content additions that would each touch 2-5
files and require a dedicated planning session before implementation.  They
are preserved here so they don't get re-derived six months from now.

---

## 1. Replay log

**Summary**: At the end of every game write a full turn-by-turn action log
to `saves/<slug>_replay.json`.  On load, a "Watch replay" button in the
load menu replays the match deterministically from the initial state.

**Why**: Useful for learning AI patterns and sharing interesting games.

**Files to touch**:
- `src/persistence/save.py` -- `ReplayLog` dataclass, append-on-action
- `main.py` -- hook into every `resolve_attack`, `move_unit`, `build_unit`
  call to append a `{"type": ..., "args": ...}` record
- `main.py` -- `replay_mode` screen-state, step-through at AI_ACTION_DELAY

**Effort**: ~2 days.

---

## 2. Undo (single step)

**Summary**: Ctrl+Z undoes the last player action (move or attack) within
the current turn.  Implemented by snapshotting `state_to_dict` before each
action and restoring on undo.

**Why**: Removes the "fat-finger" frustration that turns into a reload.

**Files to touch**:
- `main.py` -- `_undo_snapshot: dict | None`; push before action, pop on
  Ctrl+Z; invalidate on end-turn.
- Nothing in engine needed -- the JSON snapshot already captures full state.

**Effort**: ~1 day.  Risk: snapshot is a full deepcopy; large maps may lag.

---

## 3. Tutorial overlay

**Summary**: First-time-play walkthrough: a sequence of text bubbles that
highlight specific hexes/buttons and pause game input until dismissed.

**Why**: The current STRATEGY.md is good for reading but players need
in-game guidance on their first mission.

**Files to touch**:
- New `src/render/tutorial.py` -- `TutorialStep(text, highlight_rect,
  next_trigger)` list; `TutorialOverlay.draw(surface, font)`.
- `main.py` -- `tutorial_step_idx` state; advance on left-click or E.
- `data/scenarios/m1.json` -- `"tutorial": true` flag to enable.

**Effort**: ~2 days.

---

## 4. Build queue (1-turn delay)

**Summary**: Purchasing a unit at HQ queues it for delivery next turn
instead of spawning instantly.  Gives defenders a 1-turn window to contest
the HQ.  A small "incoming" indicator appears on the HQ tile.

**Why**: Instant spawning is a-historical; enemies can be denied the HQ
spawn point by occupying adjacent hexes.

**Files to touch**:
- `src/engine/state.py` -- `Faction.build_queue: list[str]`; drain at
  `_on_turn_start`.
- `src/render/hex_renderer.py` -- draw a small icon over HQ when queue > 0.
- `src/engine/tech.py` -- `build_unit` writes to queue instead of spawning.

**Effort**: ~2 days.

---

## 5. Doctrine cards (one-shot per match)

**Summary**: Each faction gets 3 unique one-shot abilities selectable in the
pre-match config panel.  Examples: NATO "Precision Airstrike" (remove one
enemy unit anywhere on the map), BRICS "Mass Mobilisation" (+5 free T1
units next turn), Guerilla "Sabotage" (disable a random enemy city for 3
turns).

**Why**: Adds asymmetric depth and replay value without touching the core
combat model.

**Files to touch**:
- New `src/engine/doctrine.py` -- `Doctrine` dataclass, `use_doctrine(state,
  faction, doctrine)` resolver.
- `data/doctrines.json` -- definitions.
- `main.py` -- doctrine button in action panel; pre-match doctrine picker.

**Effort**: ~3 days.

---

## 6. Asymmetric income (faction-flavoured economy)

**Summary**:
- Guerilla earns +N credits per enemy unit killed (insurgency bounty).
- BRICS earns +10% from any city tile that started the game as BRICS-owned.
- NATO has the highest flat base income per tile.

**Why**: Encourages distinct play styles even in skirmish.

**Files to touch**:
- `src/engine/economy.py` (new) -- split income logic out of `state.py`.
- `src/engine/state.py` -- call economy module at turn-start.
- Guerilla kill-bounty hook in `src/engine/combat.py`.

**Effort**: ~1-2 days.

---

## 7. In-game map editor

**Summary**: A separate screen (Ctrl+E from main menu) that lets the player
paint terrain, place units, set HQs, and export the result as a scenario
JSON.

**Why**: The biggest barrier to user-generated content is hand-editing JSON.

**Files to touch**:
- New `src/editor/` package -- `EditorState`, `EditorRenderer`,
  `editor_toolbar.py`.
- `main.py` -- `"editor"` screen state.
- `data/scenarios/` -- autosave editor output here.

**Effort**: ~5 days.  High complexity; deferred until core is stable.

---

## 8. Sound expansion

**Summary**: Distinct SFX per faction (BRICS attack sounds harsher; Guerilla
attack uses suppressed-fire thud).  Ambient battlefield loop during play.
Unit-selected voice line stubs (one word per faction).

**Why**: Audio is the cheapest way to reinforce faction identity.

**Files to touch**:
- `tools/gen_sounds.py` -- add 6 faction-specific attack WAVs.
- `src/audio/sounds.py` -- `play_sfx(name, faction=None)` fallback chain.
- `main.py` -- pass `faction=attacker.faction` to `play_sfx("attack", ...)`.

**Effort**: ~1-2 days.

---

## 9. Ironman mode

**Summary**: One autosave slot, no manual saves, no undo.  Toggle in the
pre-match config.  A skull icon on the HUD reminds the player.

**Why**: High-stakes runs dramatically change how players approach resource
management.

**Files to touch**:
- `main.py` -- `ironman: bool` flag; disable F5 and undo; delete autosave
  on game-over.
- `src/persistence/save.py` -- embed `"ironman": true` in save JSON so the
  flag survives a reload.

**Effort**: ~half a day.
