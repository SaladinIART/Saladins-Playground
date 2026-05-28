# Code health ideas (effort: hours each)

These are technical debt and tooling improvements.  None of them change
game behaviour -- they make the codebase easier to maintain and extend.
Pick them up between feature sessions.

---

## 1. mypy + ruff pre-commit hook

**Summary**: Add `mypy` (strict mode, src/ only) and `ruff` (lint +
format) as pre-commit hooks.  Current code is untyped in several AI and
render modules; adding stubs incrementally is easier than doing it all
at once.

**Why**: Catches `Optional` misuse, missing return annotations, and
import cycles before CI does.

**Files to touch**:
- `.pre-commit-config.yaml` (new) -- ruff + mypy hooks.
- `pyproject.toml` -- `[tool.mypy]` + `[tool.ruff]` config sections.
- `src/**/*.py` -- add missing `-> None` return annotations; replace
  bare `dict` with `dict[str, Any]` where already known.

**Effort**: ~2-3 hours to wire the hooks; ongoing annotation work per
module (~30 min each) done incrementally.

---

## 2. AI profiling pass

**Summary**: Profile `take_turn` for a 50-unit game on CPython and on
Pygbag WASM.  The heuristic enumerates O(units x moves x targets) per
turn -- large games may hit the 0.55s-per-action budget.

**Key suspects**:
- `threat_to_unit_at`: mutates + restores `unit.hex` for each candidate
  hex -- many dict lookups.
- `compute_movement` (Dijkstra): runs once per unit per `enumerate_actions`
  call even for units that won't move.

**Suggested fixes**:
- Memoize `compute_movement` per unit per turn (invalidate on any unit
  move in the same faction turn).
- Pre-sort `enumerate_actions` by unit priority (high-HP first) and early-
  exit when `MAX_ACTIONS_PER_TURN` is reached.

**Files to touch**:
- `src/ai/heuristic.py` -- movement cache dict, early-exit guard.
- `tools/profile_ai.py` (new) -- synthetic 50-unit game, `cProfile` run,
  prints top-20 hot spots.

**Effort**: ~half a day to profile; 1-2 days to fix the top bottlenecks.

---

## 3. Fog dirty-flag optimisation

**Summary**: `state._visible_cache` is invalidated (cleared) on every
`add_unit`, `remove_unit`, and `move_unit` call.  On large maps this
forces full recomputation from scratch each time any unit moves.

**Fix**: Track a `_fog_dirty: set[str]` of faction IDs whose cache needs
rebuilding.  Only the factions whose units moved (or whose enemies moved
within vision range) get their cache invalidated.

**Why**: Currently the active player's fog is recomputed after every AI
unit move, even when the AI unit is on the other side of the map.

**Files to touch**:
- `src/engine/state.py` -- `_fog_dirty: set[str]`; `move_unit` adds the
  moving unit's faction AND any faction that can see the from/to hexes
  (check via existing `explored` sets as a fast approximation).
- `src/engine/fog.py` -- `compute_visibility` unchanged; `state.visible_to`
  checks `_fog_dirty` before computing.

**Effort**: ~1 day.  Risk: the approximation (explored as proxy for visible)
may over-dirty; measure cache-miss rate before and after.

---

## 4. Remove leftover Tank Game references

**Summary**: The repo still has stale strings from the original project:
`README.md` mentions "Tank Game" in the description; `pyproject.toml`
has `name = "tank-game"`; the archive file is `archive/tankv1.py`.
None of these are bugs but they confuse new contributors.

**Suggested rename**: `Arclight` or `Hexfront` or let the user pick.
Update consistently: repo description on GitHub, `pyproject.toml`, the
`<title>` in `index.html`, and `main.py`'s Pygame window title.

**Files to touch**:
- `pyproject.toml` -- `name`, `description`.
- `main.py` -- `pygame.display.set_caption(...)`.
- `README.md` -- title + description.
- `index.html` -- `<title>` tag.
- `.github/workflows/deploy.yml` -- artifact name if set.

**Effort**: ~1 hour (pure search + replace).

---

## 5. Split main.py

**Summary**: `main.py` is currently ~1400 lines and growing.  Split it
into focused modules:

- `src/ui/screens/` package -- one file per screen state
  (`main_menu.py`, `pre_match.py`, `load_menu.py`, `mission_select.py`,
  `skirmish_config.py`, `playing.py`, `game_over.py`).
- `src/ui/hud.py` -- all HUD drawing helpers (`_draw_hud`, `_draw_unit_panel`,
  `_draw_build_menu`, `_draw_end_turn_button`).
- `main.py` shrinks to `~150 lines`: Pygame init, screen-state router,
  and the async `main()` loop.

**Why**: Every CP that touches the game loop touches `main.py`.  Merge
conflicts become inevitable with more contributors.

**Effort**: ~2-3 days.  Purely mechanical; no logic changes.  High risk
of import-cycle issues -- map dependencies before starting.

---

## 6. Sprite regen flag in gen_sprites.py

**Summary**: `tools/gen_sprites.py` regenerates all sprites unconditionally
on every run.  If the Kenney asset swap (CP-24) has landed, this clobbers
real art with procedural placeholders.

**Fix**: Add a `--force` flag; default behaviour skips a sprite if its
PNG already exists (i.e., a real asset has been placed).  Also add a
`--list` flag to print which sprites are procedural vs. real.

**Files to touch**:
- `tools/gen_sprites.py` -- `argparse` with `--force` / `--list`;
  wrap each `pygame.image.save` call with `if --force or not path.exists()`.

**Effort**: ~1 hour.
