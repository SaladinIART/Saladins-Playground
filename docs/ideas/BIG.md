# Big ideas (effort: weeks, not days)

These are architectural expansions that would touch the core engine and
likely require a dedicated design phase before any code is written.
They are preserved here so the ideas don't get lost between sessions.

---

## 1. Multiplayer hot-seat (local 2-player)

**Summary**: Two human players share one keyboard, taking turns.  No
networking required -- just a flag in `GameState` that marks both factions
as `is_ai=False`.  The active player sees their faction's fog of war; a
"Pass screen" blacks out the display for 3 seconds so the other player
can sit down before the new turn reveals.

**Why**: Immediately doubles replay value with zero AI tuning.  The pass
screen solves the fog-of-war peeking problem elegantly.

**Files to touch**:
- `src/engine/state.py` -- `is_ai=False` on both factions (already
  supported; just needs a pre-match toggle).
- `main.py` -- `hotset_pass` screen state (3-second black screen +
  "Player 2, take the keyboard!" message + countdown); skip if only one
  human faction.
- `main.py` / `_draw_pre_match()` -- "2P Hot-seat" toggle in pre-match
  config; disable AI opponent dropdowns when toggled.

**Effort**: ~1 week.  The engine is already faction-agnostic; the work is
the pass screen, UI toggles, and fog-switch correctness.

---

## 2. Per-faction tech tree

**Summary**: Replace the flat T1/T2/T3 tier system with a branching tech
tree unique per faction.  NATO might branch between "Precision" (jet
upgrades) and "Armour" (tank upgrades); BRICS between "Mass Production"
(cost cuts) and "Doctrine" (special unit unlocks); Guerilla between
"Shadow" (stealth expand) and "Explosives" (kamikaze + IED).

**Why**: Makes faction choice feel strategically meaningful through the
mid-game, not just through different unit stat sheets.

**Files to touch**:
- New `src/engine/techtree.py` -- `TechNode(id, faction, prereqs, cost,
  unlocks_units, unlocks_upgrades)`; `TechTree` per faction; `unlock(node)`
  marks it researched.
- New `data/techtrees/*.json` -- one JSON per faction.
- `main.py` -- tech-tree panel (Ctrl+T); replaces tier upgrade button.
- `src/engine/state.py` -- `Faction.researched: set[str]` persisted in
  save JSON.

**Effort**: ~2 weeks.  Schema design is the hardest part; renderer is
straightforward (node graph with arrows).

---

## 3. Weather + day/night cycle

**Summary**: Each scenario can enable a weather track: Clear -> Overcast
-> Rain -> Storm -> Clear (4-turn loop, or random roll each turn).
Day/Night cycles every 6 turns.  Effects: rain halves wheeled move speed
and reduces vision -1; storm grounds all air units; night reduces vision
-2 globally but gives Guerilla a +1 stealth range bonus.

**Why**: Adds tactical variety to every mission replay without new content.
Forces players to plan around turn-count windows.

**Files to touch**:
- New `src/engine/weather.py` -- `Weather` enum (CLEAR/OVERCAST/RAIN/
  STORM), `TimeOfDay` enum (DAY/NIGHT); `WeatherState(weather, tod,
  turn_counter)`; `next_turn(ws)` advances both cycles.
- `src/engine/fog.py` -- pass `weather_vision_mod` into radius calc.
- `src/engine/movement.py` -- pass `move_mod` into cost table.
- `main.py` -- weather icon in HUD top-right; `weather_state` persisted
  in save.

**Effort**: ~1 week (engine: 2 days, renderer + integration: 3 days,
balance tuning: 2 days).

---

## 4. Squad system

**Summary**: Replace 1-unit-per-hex with squad stacks of up to 3 units
(same class only) that move and attack as one.  A squad's combined HP is
capped at 30.  Squads split automatically when one member reaches 0 HP.
Visually: up to 3 smaller icons on a hex.

**Why**: Closer to real combined-arms play.  Lets players consolidate
weak survivors without wasting a hex slot.

**Files to touch**:
- `src/engine/state.py` -- `tiles_to_squads` mapping, `Squad` wrapper
  around `list[Unit]`; `GameState.units` becomes `squads`.
- `src/engine/combat.py` -- damage distributed to squad members by HP
  weight; leader is first member.
- `src/render/hex_renderer.py` -- draw up to 3 mini-icons offset inside
  hex.
- Almost every AI and movement function needs squad-awareness.

**Effort**: ~3 weeks.  Invasive refactor; touches every layer.  Not
recommended until a full playtest pass confirms the 1-unit model is
genuinely limiting rather than just different.

---

## 5. Stronger AI (MCTS or neural network)

**Summary**: Replace the heuristic scorer with a Monte Carlo Tree Search
(MCTS) that simulates N turns ahead, or with a lightweight neural network
policy trained on self-play.  MCTS is more tractable in Python given the
existing `state_to_dict` / `dict_to_state` round-trip (clone state cheaply
for rollouts).

**Why**: The heuristic AI is readable but brittle; it stalls on multi-step
captures and can't reason about sacrifice plays.

**Files to touch**:
- New `src/ai/mcts.py` -- `MCTSNode`, `rollout(state, depth)`, `select /
  expand / backprop`.
- `src/ai/heuristic.py` -- keep as a fast rollout policy (leaf eval) for
  MCTS.
- `main.py` -- async MCTS budget (ms cap per frame so it doesn't freeze).
- Optional: `tools/selfplay.py` -- train a tiny MLP on game outcomes.

**Effort**: ~3 weeks for MCTS; 6-10 weeks for NN path.  Python MCTS is
slow (~500 sims/s in CPython); 10-50ms budget per frame = 5-25 sims --
viable for depth 2-3.  Pygbag WASM is ~3x slower; budget accordingly.

---

## 6. Large-map performance

**Summary**: Maps above 40x30 hexes start dropping below 60fps because
`draw_map` iterates every tile each frame.  Fix: dirty-rect rendering
(only redraw tiles whose state changed), or a static terrain surface
baked at load time and composited over.

**Why**: Procedurally generated skirmish maps will trend large.  Also
needed before any multiplayer path.

**Files to touch**:
- `src/render/hex_renderer.py` -- `bake_terrain_surface(state, camera)`
  returns a pre-rendered pygame.Surface; `draw_map` blits from it when
  camera is static.
- `src/engine/state.py` -- `dirty_tiles: set[Hex]`; set on capture,
  ownership change, unit move.
- `src/render/camera.py` -- expose `dirty` flag when zoom/pan changes.

**Effort**: ~1 week.  The bake approach is simpler (one Surface per zoom
level); dirty-rect is finer-grained but complex with alpha overlays.

---

## 7. More terrain types

**Summary**: Add 4-6 new terrain variants to increase map diversity:
**Swamp** (move cost 3, blocks wheeled), **Desert** (vision +1, no
defense bonus), **Urban Ruins** (defense +3, blocks LOS, blocks vehicles),
**Coastal/Beach** (unit can embark to ship, vision +2 over water),
**Bunker** (defense +4, infantry only, capture 5 turns).

**Why**: Current 10 terrain types map to the same ~4 tactical roles.
Ruins and bunkers in particular add chokepoint variety.

**Files to touch**:
- `data/terrain.json` -- add entries with new ids + modifiers.
- `src/engine/tile.py` -- no code changes if JSON-driven (schema already
  handles all fields).
- `tools/gen_sprites.py` -- add procedural sprite icons for each new type.
- `data/scenarios/` -- update existing maps or author new ones that
  showcase the new tiles.

**Effort**: ~4 days per batch of 3 new types (content work, not engine
work).  Engine is already fully data-driven.
