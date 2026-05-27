"""
Modern Warfare 4X — CP-14 scenario loader.

Controls:
  WASD / arrows  — pan
  Scroll wheel   — zoom (cursor-anchored)
  Right-drag     — pan
  Left-click own HQ — open build menu
  Left-click unit   — select / move / attack
  SPACE          — end turn
  F              — toggle fog of war
  ESC            — cancel selection / close menu / quit
"""
import asyncio
from pathlib import Path
from typing import Any, Iterator, Optional

import pygame

from src.ai.heuristic import Action, describe, take_turn_steps
from src.ai.personality import from_dict as personality_from_dict, Personality
from src.engine.combat import (
    attack_targets,
    load_damage_matrix,
    predict_exchange,
    resolve_attack,
)
from src.engine.fog import can_faction_see_unit
from src.engine.hex import Hex
from src.engine.movement import Movement, compute_movement
from src.engine.scenario import load_scenario
from src.engine.state import Faction, GameState
from src.engine.tile import Tile, load_terrain
from src.engine.unit import Unit, load_units
from src.engine.tech import (
    all_displayable_units,
    can_upgrade_tier,
    next_tier_cost,
)
from src.engine.victory import Outcome, default_victory_config
from src.render.camera import Camera
from src.render.hex_renderer import HexRenderer

# Faction the local player views the world through (game-over modal POV).
# CP-16 will lift this into pre-match config; for now NATO is the human side.
HUMAN_FACTION = "NATO"

# Seconds between visible AI actions so the player can follow what's happening.
AI_ACTION_DELAY = 0.55

WIDTH, HEIGHT = 1280, 720
FPS = 60
BG = (18, 24, 38)

# Default scenario to load.  CP-16 will make this selectable from a menu.
DEFAULT_SCENARIO = Path("data/scenarios/m1.json")

# Module-level mutable: populated by _load_initial_state(), read by the AI
# step launcher to pass the right personality per faction.
_scenario_meta: dict[str, Any] = {"name": "", "description": "", "personalities": {}}


def _load_initial_state(scenario_path: "str | Path" = DEFAULT_SCENARIO) -> GameState:
    """Load a scenario JSON and return the configured GameState.

    Also updates the module-level ``_scenario_meta`` so the AI driver can
    pick up per-faction personalities without needing a direct reference.
    """
    global _scenario_meta
    state, meta = load_scenario(scenario_path)
    _scenario_meta = meta
    return state


def _personality_for(faction_id: str) -> Optional[Personality]:
    """Return the Personality for *faction_id* from the loaded scenario, or None."""
    pd = _scenario_meta.get("personalities", {}).get(faction_id)
    if pd is None:
        return None
    return personality_from_dict(pd)


def _draw_game_over(
    surface: pygame.Surface,
    font_ui: pygame.font.Font,
    font_hud: pygame.font.Font,
    state: "GameState",
    perspective_fid: str,
) -> list[tuple[pygame.Rect, str]]:
    """
    Draw the game-over modal from *perspective_fid*'s POV.
    Returns ``[(rect, action_id), ...]`` for click detection.
    action_id is ``"retry"`` or ``"quit"``.
    """
    outcome = state.outcomes.get(perspective_fid, Outcome.PENDING)
    if outcome == Outcome.PENDING:
        return []

    sw, sh = surface.get_size()

    # Full-screen dim
    dim = pygame.Surface((sw, sh), pygame.SRCALPHA)
    dim.fill((0, 0, 0, 170))
    surface.blit(dim, (0, 0))

    PANEL_W, PANEL_H = 480, 240
    px = (sw - PANEL_W) // 2
    py = (sh - PANEL_H) // 2

    won        = (outcome == Outcome.WON)
    border_col = (90, 220, 140) if won else (220, 90, 90)
    title_txt  = "VICTORY" if won else "DEFEAT"

    # Panel
    pygame.draw.rect(surface, (16, 22, 38), (px, py, PANEL_W, PANEL_H))
    pygame.draw.rect(surface, border_col,   (px, py, PANEL_W, PANEL_H), 3)

    # Title
    big = pygame.font.SysFont("consolas", 56, bold=True)
    t_lbl = big.render(title_txt, True, border_col)
    surface.blit(t_lbl, (px + (PANEL_W - t_lbl.get_width()) // 2, py + 24))

    # Subtitle
    winner_fid = state.winner()
    sub_txt = f"Turn {state.turn_number}"
    if winner_fid:
        sub_txt += f"   |   Winner: {winner_fid}"
    s_lbl = font_ui.render(sub_txt, True, (200, 200, 200))
    surface.blit(s_lbl, (px + (PANEL_W - s_lbl.get_width()) // 2, py + 108))

    # Buttons
    BTN_W, BTN_H = 160, 48
    btn_y = py + PANEL_H - BTN_H - 20
    retry_rect = pygame.Rect(px + 50,                 btn_y, BTN_W, BTN_H)
    quit_rect  = pygame.Rect(px + PANEL_W - 50 - BTN_W, btn_y, BTN_W, BTN_H)
    mx, my = pygame.mouse.get_pos()

    for rect, label in [(retry_rect, "RETRY"), (quit_rect, "QUIT")]:
        hover = rect.collidepoint(mx, my)
        bg = (60, 90, 140) if hover else (38, 58, 100)
        pygame.draw.rect(surface, bg, rect)
        pygame.draw.rect(surface, (150, 190, 240), rect, 2)
        l_lbl = font_hud.render(label, True, (240, 240, 255))
        surface.blit(
            l_lbl,
            (rect.x + (rect.w - l_lbl.get_width()) // 2,
             rect.y + (rect.h - l_lbl.get_height()) // 2),
        )

    return [(retry_rect, "retry"), (quit_rect, "quit")]


def _draw_build_menu(
    surface: pygame.Surface,
    font_ui: pygame.font.Font,
    font_hud: pygame.font.Font,
    state: "GameState",
) -> tuple[pygame.Rect, list[tuple[pygame.Rect, str]]]:
    """
    Draw the build menu panel centred on the screen.

    Returns ``(panel_rect, item_list)`` where item_list is a sequence of
    ``(screen_rect, action_id)`` pairs.  action_id is either a unit type_id
    or the special string ``"upgrade_tier"``.  Tier-locked rows are shown but
    NOT included in item_list (cannot be clicked).
    """
    faction = state.active_faction
    mx, my = pygame.mouse.get_pos()

    PANEL_W = 340
    ITEM_H  = 30
    HDR_H   = 54   # title + resource line
    PAD     = 8
    UPGR_H  = 36   # tier-upgrade row is slightly taller
    FOOT_H  = 22

    display_units = all_displayable_units()
    has_upgrade   = can_upgrade_tier(faction)
    PANEL_H = (HDR_H + PAD
               + len(display_units) * ITEM_H
               + (UPGR_H if has_upgrade else 0)
               + PAD + FOOT_H)

    sw, sh = surface.get_size()
    px = (sw - PANEL_W) // 2
    py = max(10, (sh - PANEL_H) // 2)
    panel_rect = pygame.Rect(px, py, PANEL_W, PANEL_H)

    # ── Panel background ──────────────────────────────────────────────────
    panel = pygame.Surface((PANEL_W, PANEL_H), pygame.SRCALPHA)
    panel.fill((14, 20, 38, 228))
    pygame.draw.rect(panel, (80, 120, 200), (0, 0, PANEL_W, PANEL_H), 2)

    # ── Header ────────────────────────────────────────────────────────────
    tier_colors = {1: (140, 190, 255), 2: (190, 255, 140), 3: (255, 220, 100)}
    t_col  = tier_colors.get(faction.tier, (200, 200, 200))
    t_surf = font_hud.render(
        f"BUILD  [{faction.name}]  Tier {faction.tier}", True, t_col
    )
    panel.blit(t_surf, (PAD, PAD))
    r_surf = font_ui.render(
        f"Credits: {faction.credits}   Oil: {faction.oil}", True, (200, 200, 130)
    )
    panel.blit(r_surf, (PAD, PAD + 26))
    pygame.draw.line(panel, (55, 80, 140), (PAD, HDR_H - 4), (PANEL_W - PAD, HDR_H - 4))

    # ── Unit rows ─────────────────────────────────────────────────────────
    item_list: list[tuple[pygame.Rect, str]] = []
    y = HDR_H + PAD

    for ut in display_units:
        tier_locked = ut.tier > faction.tier
        affordable  = faction.can_afford(ut.cost_credits, ut.cost_oil)
        row_scr     = pygame.Rect(px + 2, py + y, PANEL_W - 4, ITEM_H - 2)
        is_hover    = row_scr.collidepoint(mx, my) and not tier_locked

        if tier_locked:
            bg   = (26, 26, 38, 170)
            nc   = (65, 65, 80)
            cc   = (65, 65, 80)
        elif not affordable:
            bg   = (60, 22, 22, 210) if not is_hover else (75, 30, 30, 230)
            nc   = (180, 105, 105)
            cc   = (200, 115, 115)
        else:
            bg   = (34, 54, 100, 215) if not is_hover else (52, 78, 140, 235)
            nc   = (230, 240, 255)
            cc   = (160, 230, 130)

        row = pygame.Surface((PANEL_W - 4, ITEM_H - 2), pygame.SRCALPHA)
        row.fill(bg)

        # Tier badge
        badge_col = tier_colors.get(ut.tier, (160, 160, 160)) if not tier_locked else (48, 48, 58)
        b_lbl = font_ui.render(f"T{ut.tier}", True, badge_col)
        row.blit(b_lbl, (4, (ITEM_H - 2 - b_lbl.get_height()) // 2))

        # Name
        n_lbl = font_ui.render(ut.name, True, nc)
        row.blit(n_lbl, (30, (ITEM_H - 2 - n_lbl.get_height()) // 2))

        # Cost (right-aligned)
        c_str = f"{ut.cost_credits}cr" + (f" {ut.cost_oil}oil" if ut.cost_oil else "")
        c_lbl = font_ui.render(c_str, True, cc)
        row.blit(c_lbl, (PANEL_W - 4 - c_lbl.get_width() - 6,
                         (ITEM_H - 2 - c_lbl.get_height()) // 2))

        panel.blit(row, (2, y))
        if not tier_locked:
            item_list.append((row_scr, ut.id))
        y += ITEM_H

    # ── Tier-upgrade row ──────────────────────────────────────────────────
    if has_upgrade:
        cost       = next_tier_cost(faction)
        affordable = faction.can_afford(cost, 0)
        row_scr    = pygame.Rect(px + 2, py + y, PANEL_W - 4, UPGR_H - 2)
        is_hover   = row_scr.collidepoint(mx, my)

        if affordable:
            bg = (28, 68, 38, 215) if not is_hover else (42, 96, 54, 235)
            tc = (150, 255, 165)
            bc = (55, 120, 65)
        else:
            bg = (28, 36, 28, 180)
            tc = (88, 126, 88)
            bc = (40, 55, 40)

        row = pygame.Surface((PANEL_W - 4, UPGR_H - 2), pygame.SRCALPHA)
        row.fill(bg)
        pygame.draw.rect(row, bc, (0, 0, PANEL_W - 4, UPGR_H - 2), 1)
        u_lbl = font_ui.render(
            f"Unlock Tier {faction.tier + 1}  —  {cost} cr", True, tc
        )
        row.blit(u_lbl, ((PANEL_W - 4 - u_lbl.get_width()) // 2,
                         (UPGR_H - 2 - u_lbl.get_height()) // 2))
        panel.blit(row, (2, y))
        item_list.append((row_scr, "upgrade_tier"))
        y += UPGR_H

    # ── Footer ────────────────────────────────────────────────────────────
    f_lbl = font_ui.render(
        "Left-click to build  |  ESC to close", True, (95, 108, 145)
    )
    panel.blit(f_lbl, ((PANEL_W - f_lbl.get_width()) // 2, y + 4))

    surface.blit(panel, (px, py))
    return panel_rect, item_list


async def main() -> None:
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Modern Warfare 4X")
    clock = pygame.time.Clock()
    font_ui = pygame.font.SysFont("consolas", 18)
    font_hud = pygame.font.SysFont("consolas", 22, bold=True)

    state = _load_initial_state()
    camera = Camera(WIDTH, HEIGHT, hex_size=36, offset_x=60.0, offset_y=60.0)
    renderer = HexRenderer(camera)

    hovered: Hex | None = None
    selected_unit: Unit | None = None
    movement: Movement | None = None
    path_preview: list[Hex] = []
    attack_target_uids: set[int] = set()   # enemy uids the selection can hit from current hex
    fog_enabled = True                     # F to toggle
    build_hq: Hex | None = None            # HQ hex whose build menu is open
    _bm_panel_rect: pygame.Rect | None = None   # updated each render frame
    _bm_items: list[tuple[pygame.Rect, str]] = []
    _go_buttons: list[tuple[pygame.Rect, str]] = []   # game-over modal click targets
    ai_steps: Optional[Iterator[Action]] = None       # generator for active AI turn
    ai_timer: float = 0.0                             # seconds since last AI action

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                continue

            # ── Game-over modal owns input ───────────────────────────────────
            if state.game_over:
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = pygame.mouse.get_pos()
                    for rect, action in _go_buttons:
                        if rect.collidepoint(mx, my):
                            if action == "retry":
                                state = _load_initial_state()
                                selected_unit = None
                                movement = None
                                path_preview = []
                                attack_target_uids = set()
                                build_hq = None
                                ai_steps = None
                            elif action == "quit":
                                running = False
                            break
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False
                continue

            # ── AI turn locks out player game logic.  Camera still handles
            #    right-drag/scroll for spectating; left-click is swallowed.
            if ai_steps is not None:
                if not (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1):
                    camera.handle_event(event)
                continue

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if build_hq is not None:
                        build_hq = None
                    elif selected_unit is not None:
                        selected_unit = None
                        movement = None
                        path_preview = []
                        attack_target_uids = set()
                    else:
                        running = False
                elif event.key == pygame.K_SPACE:
                    build_hq = None
                    selected_unit = None
                    movement = None
                    path_preview = []
                    attack_target_uids = set()
                    state.end_turn()
                    print(f"End turn → {state.active_faction.id} "
                          f"(turn {state.turn_number}, "
                          f"credits={state.active_faction.credits}, "
                          f"oil={state.active_faction.oil})")
                    if state.active_faction.is_ai and not state.game_over:
                        _pers = _personality_for(state.active_faction.id)
                        ai_steps = take_turn_steps(state, state.active_faction.id, _pers)
                        ai_timer = 0.0
                elif event.key == pygame.K_f:
                    fog_enabled = not fog_enabled
                    print(f"Fog {'on' if fog_enabled else 'off'}")
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mx, my = pygame.mouse.get_pos()
                clicked = camera.screen_to_hex(mx, my)
                active_id = state.active_faction.id
                clicked_unit = state.unit_at(clicked)

                if build_hq is not None:
                    # ── Build menu open ──────────────────────────────────────
                    consumed = False
                    for rect, action_id in _bm_items:
                        if rect.collidepoint(mx, my):
                            if action_id == "upgrade_tier":
                                try:
                                    state.upgrade_tier(active_id)
                                    print(f"Tier unlocked -> {state.active_faction.tier}")
                                except ValueError as e:
                                    print(f"Upgrade failed: {e}")
                            else:
                                try:
                                    u = state.build_unit(action_id, active_id, build_hq)
                                    print(f"Built {u.unit_type.name} at {u.hex}")
                                except ValueError as e:
                                    print(f"Build failed: {e}")
                            build_hq = None
                            consumed = True
                            break
                    if not consumed:
                        if _bm_panel_rect is None or not _bm_panel_rect.collidepoint(mx, my):
                            build_hq = None

                elif selected_unit is not None:
                    # ── Unit selected ────────────────────────────────────────
                    # Clicking own HQ switches to build menu.
                    clicked_tile = state.tiles.get(clicked)
                    if (clicked_tile and clicked_tile.terrain.is_hq
                            and clicked_tile.owner_faction == active_id):
                        selected_unit = None
                        movement = None
                        path_preview = []
                        attack_target_uids = set()
                        build_hq = clicked
                    elif clicked_unit is not None and clicked_unit.uid in attack_target_uids:
                        # Attack
                        result = resolve_attack(state, selected_unit, clicked_unit)
                        print(
                            f"Attack: {selected_unit.unit_type.name} -> "
                            f"{clicked_unit.unit_type.name}  "
                            f"{result.damage_dealt} dmg"
                            + (" (killed)" if result.defender_killed else "")
                            + (f"  counter {result.counter_damage}"
                               if result.counter_damage else "")
                            + (" (attacker killed)" if result.attacker_killed else "")
                        )
                        selected_unit = None
                        movement = None
                        path_preview = []
                        attack_target_uids = set()
                    elif movement is not None and clicked in movement.reachable:
                        # Move, then recompute attack targets from new hex.
                        state.move_unit(selected_unit.uid, clicked)
                        selected_unit.has_moved = True
                        movement = None
                        path_preview = []
                        new_targets = {t.uid for t in attack_targets(state, selected_unit)}
                        if new_targets:
                            attack_target_uids = new_targets
                        else:
                            selected_unit = None
                            attack_target_uids = set()
                    elif (clicked_unit is not None
                          and clicked_unit.faction == active_id
                          and clicked_unit.can_act()):
                        # Reselect a different own unit.
                        selected_unit = clicked_unit
                        movement = (None if clicked_unit.has_moved
                                    else compute_movement(state, clicked_unit))
                        path_preview = []
                        attack_target_uids = {
                            t.uid for t in attack_targets(state, clicked_unit)
                        }
                    else:
                        selected_unit = None
                        movement = None
                        path_preview = []
                        attack_target_uids = set()

                else:
                    # ── Idle ────────────────────────────────────────────────
                    clicked_tile = state.tiles.get(clicked)
                    if (clicked_tile and clicked_tile.terrain.is_hq
                            and clicked_tile.owner_faction == active_id):
                        # Click own HQ -> build menu.
                        build_hq = clicked
                    elif (clicked_unit is not None
                          and clicked_unit.faction == active_id
                          and clicked_unit.can_act()):
                        selected_unit = clicked_unit
                        movement = (None if clicked_unit.has_moved
                                    else compute_movement(state, clicked_unit))
                        path_preview = []
                        attack_target_uids = {
                            t.uid for t in attack_targets(state, clicked_unit)
                        }
                    elif clicked_unit is not None:
                        print(f"Unit at {clicked}: {clicked_unit.unit_type.name} "
                              f"({clicked_unit.faction}) HP={clicked_unit.hp}")
                    else:
                        tile = state.tiles.get(clicked)
                        if tile:
                            print(f"Tile at {clicked}: {tile.terrain.name} "
                                  f"(owner={tile.owner_faction})")
            else:
                camera.handle_event(event)

        camera.handle_keys(pygame.key.get_pressed(), dt)
        hovered = camera.screen_to_hex(*pygame.mouse.get_pos())

        # ── AI step-tick ─────────────────────────────────────────────────────
        # Pull one action per AI_ACTION_DELAY so moves are visible.  When the
        # generator is exhausted, automatically end the AI turn — handing
        # control back to the player (or to the next AI in a multi-AI match).
        if ai_steps is not None:
            ai_timer += dt
            if ai_timer >= AI_ACTION_DELAY:
                ai_timer = 0.0
                try:
                    a = next(ai_steps)
                    print(f"  AI {state.active_faction.id}: {describe(a)}")
                except StopIteration:
                    ai_steps = None
                    if not state.game_over:
                        state.end_turn()
                        print(f"End turn → {state.active_faction.id} "
                              f"(turn {state.turn_number}, "
                              f"credits={state.active_faction.credits}, "
                              f"oil={state.active_faction.oil})")
                        if state.active_faction.is_ai and not state.game_over:
                            _pers = _personality_for(state.active_faction.id)
                            ai_steps = take_turn_steps(state, state.active_faction.id, _pers)

        # Update path preview toward hovered hex.
        if selected_unit is not None and movement is not None:
            if hovered in movement.reachable:
                path_preview = movement.path_to(hovered)
            else:
                path_preview = []

        # Fog: render from the active faction's POV (hot-seat for now;
        # CP-13 AI will automate non-human factions).
        viewer_id = state.active_faction.id
        if fog_enabled:
            visible_set = state.visible_to(viewer_id)
            explored_set = state.explored.get(viewer_id, set())
            def _can_see(u: Unit, _vid=viewer_id) -> bool:
                return can_faction_see_unit(state, _vid, u)
        else:
            visible_set = None
            explored_set = None
            _can_see = None

        # Render
        screen.fill(BG)
        renderer.draw_map(
            screen, state.tiles, hovered_hex=hovered,
            visible=visible_set, explored=explored_set,
        )
        if selected_unit is not None and movement is not None:
            renderer.draw_movement_overlay(
                screen, movement.reachable, path_preview, selected_unit.hex
            )
        if selected_unit is not None and attack_target_uids:
            target_hexes = [
                state.units[uid].hex
                for uid in attack_target_uids
                if uid in state.units
            ]
            renderer.draw_attack_overlay(screen, target_hexes, hovered_hex=hovered)
        renderer.draw_units(screen, list(state.units.values()), can_see=_can_see)

        # Build menu overlay (drawn on top of map, before HUD text)
        if build_hq is not None:
            _bm_panel_rect, _bm_items = _draw_build_menu(
                screen, font_ui, font_hud, state
            )
        else:
            _bm_panel_rect = None
            _bm_items = []

        # Game-over modal (drawn last so it covers everything else)
        if state.game_over:
            _go_buttons = _draw_game_over(
                screen, font_ui, font_hud, state, HUMAN_FACTION
            )
        else:
            _go_buttons = []

        # HUD top-left
        af = state.active_faction
        ai_suffix = "  [AI thinking...]" if ai_steps is not None else ""
        scenario_title = _scenario_meta.get("name", "")
        hud_lines = [
            (scenario_title, (160, 160, 200)) if scenario_title else None,
            (f"Turn {state.turn_number}  —  {af.name}{ai_suffix}", af.color),
            (f"Credits: {af.credits}", (220, 220, 100)),
            (f"Oil: {af.oil}", (220, 160, 80)),
            (f"Tier: {af.tier}", (180, 220, 180)),
            (f"Units: {len(state.units_of(af.id))}", (200, 200, 200)),
        ]
        hud_lines = [line for line in hud_lines if line is not None]
        y = 10
        for txt, col in hud_lines:
            lbl = font_hud.render(txt, True, col)
            screen.blit(lbl, (12, y))
            y += 24

        # Selected unit info panel.
        if selected_unit is not None:
            y += 4
            sel_lines = [
                (f"[ {selected_unit.unit_type.name} ]", (255, 255, 180)),
                (f"HP {selected_unit.hp}/{selected_unit.unit_type.hp}  "
                 f"Move {selected_unit.unit_type.move}  "
                 f"Rng {selected_unit.unit_type.range_min}-{selected_unit.unit_type.range_max}",
                 (200, 200, 200)),
            ]
            # Hover prediction on attack target.
            if hovered is not None:
                hover_unit = state.unit_at(hovered)
                if (hover_unit is not None
                    and hover_unit.uid in attack_target_uids):
                    atk_dmg, counter_dmg = predict_exchange(
                        state, selected_unit, hover_unit
                    )
                    sel_lines.append((
                        f"→ {atk_dmg} dmg  •  ← {counter_dmg} counter",
                        (255, 180, 180),
                    ))
            sel_lines.append(
                ("Click hex/enemy  •  ESC cancel", (140, 140, 110))
            )
            for txt, col in sel_lines:
                lbl = font_ui.render(txt, True, col)
                screen.blit(lbl, (12, y))
                y += 20

        # FPS + hover (bottom-left)
        fps_lbl = font_ui.render(f"FPS {clock.get_fps():.0f}", True, (160, 160, 160))
        screen.blit(fps_lbl, (12, HEIGHT - 44))
        if hovered:
            hov_lbl = font_ui.render(
                f"Hover q={hovered.q}, r={hovered.r}", True, (180, 180, 100)
            )
            screen.blit(hov_lbl, (12, HEIGHT - 22))

        # Help (bottom-right)
        help_txt = "SPACE end turn  •  click HQ to build  •  F fog  •  WASD pan  •  scroll zoom"
        h_lbl = font_ui.render(help_txt, True, (120, 120, 140))
        screen.blit(h_lbl, (WIDTH - h_lbl.get_width() - 12, HEIGHT - 22))

        pygame.display.flip()
        await asyncio.sleep(0)

    pygame.quit()


if __name__ == "__main__":
    asyncio.run(main())
