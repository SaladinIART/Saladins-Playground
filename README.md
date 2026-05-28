# Saladin's Playground

A turn-based modern-warfare 4X built in Python + pygame-ce. Plays in a desktop
window or in the browser via [Pygbag](https://pygame-web.github.io).

> Hex map. Three asymmetric factions (NATO / BRICS / Guerilla).
> 5 hand-crafted missions + skirmish mode + procedurally-generated maps.
> Capture-based economy, fog of war, stealth, kamikaze drones, XCOM-style
> hunker-down stance, retreat orders, three difficulty tiers (Normal / Hard
> / Insane).

## Quick start

```bash
pip install -r requirements.txt
python main.py           # desktop
python -m pygbag .       # in-browser at http://localhost:8000
```

On Windows you can also just double-click **`Saladin's Playground.bat`**.

## Controls (in game)

| Key | Action |
|---|---|
| Left-click hex | Select unit / move / attack |
| Left-click own HQ | Open build menu |
| **E / Space** | End turn (asks first if you still have units that can act) |
| **Tab** | Cycle to next unit that can still act |
| **H** | Hunker down: +2 DEF, no attack this turn (XCOM-style) |
| **R** | Retreat: move selected unit toward your own HQ |
| **J** | Engineer medic: heal +3 HP to an adjacent friendly |
| **F5** | Save (cycles 3 slots) |
| **F** | Toggle fog of war (debug) |
| **F11** | Toggle fullscreen |
| **M** | Mute / unmute |
| **Esc** | Cancel selection / back to menu |
| Right-drag / WASD | Pan camera |
| Scroll wheel | Zoom (cursor-anchored) |

Hover any unit or terrain tile for a stat card with combat matchups and
tactical tips.

## Documentation

- [Player manual](docs/MANUAL.md) — gameplay rules, stances, capture, victory
- [Factions](docs/FACTIONS.md) — design philosophy + flagship units
- [Unit reference](docs/UNITS.md) — every unit, stats + cost (auto-generated)
- [Terrain reference](docs/TERRAIN.md) — defence / move cost / income
- [Missions](docs/MISSIONS.md) — campaign walkthrough hints
- [Tips & strategy](docs/STRATEGY.md) — opening moves, combined arms, AI quirks
- [Modding](docs/MODDING.md) — JSON schemas for units, terrain, scenarios
- [Architecture](docs/ARCHITECTURE.md) — code layout for contributors
- [Deployment](docs/DEPLOY.md) — itch.io + GitHub Pages build steps
- [Changelog](docs/CHANGELOG.md) — what landed in each checkpoint
- [Future ideas](docs/ideas/) — medium, big, balance, and code-health
  ideas preserved for future sessions

## Status

783 tests green. Phase D polish complete (CP-28 through CP-36).
The repo retains its original name (`Tank-Game`) for now; the *game* is
**Saladin's Playground**.

## License

Sandbox / personal project. Free to fork, mod, and learn from.
