"""
Modern Warfare 4X — CP-16 main menu + pre-match config + UI polish.

Controls (in-game)
------------------
  WASD / arrows  — pan camera
  Scroll wheel   — zoom (cursor-anchored)
  Right-drag     — pan
  Left-click own HQ  — open build menu
  Left-click unit    — select / move / attack
  E / SPACE      — end turn
  Tab            — cycle to next unit that can act
  F5             — manual save (cycles slots 1-3)
  F              — toggle fog of war
  ESC            — cancel selection / back to menu
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Iterator, Optional

import pygame

from src.ai.heuristic import Action, describe, take_turn_steps
from src.ai.personality import from_dict as personality_from_dict, Personality
from src.persistence.save import (
    NUM_SLOTS,
    list_saves,
    load_state,
    save_autosave,
    save_slot,
)
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HUMAN_FACTION = "NATO"           # player faction; CP-17 will make this configurable
AI_ACTION_DELAY = 0.55           # seconds per visible AI action

WIDTH, HEIGHT = 1280, 720
FPS = 60
BG = (18, 24, 38)

DEFAULT_SCENARIO = Path("data/scenarios/m1.json")

# ---------------------------------------------------------------------------
# Module-level mutable state (shared between helpers + main loop)
# ---------------------------------------------------------------------------

_SCENARIO_SLUG: str = DEFAULT_SCENARIO.stem

_scenario_meta: dict[str, Any] = {
    "name": "", "description": "", "personalities": {}
}

_current_save_slot: int = 1   # cycles 1..NUM_SLOTS on each F5 press


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_initial_state(
    scenario_path: "str | Path" = DEFAULT_SCENARIO,
) -> GameState:
    """Parse scenario JSON → GameState; refresh _scenario_meta side-channel."""
    global _scenario_meta
    state, meta = load_scenario(scenario_path)
    _scenario_meta = meta
    return state


def _personality_for(faction_id: str) -> Optional[Personality]:
    pd = _scenario_meta.get("personalities", {}).get(faction_id)
    return personality_from_dict(pd) if pd else None


def _apply_difficulty(
    state: GameState, scenario_meta: dict[str, Any], difficulty: str
) -> None:
    """Mutate *state* and *scenario_meta* in-place for the chosen difficulty.

    Normal — no changes.
    Hard   — AI faction (BRICS) gets +400 cr / +3 oil and an aggressive personality.
    """
    if difficulty != "hard":
        return
    try:
        ai_faction = state.faction_by_id("BRICS")
        ai_faction.credits += 400
        ai_faction.oil += 3
    except KeyError:
        pass
    # Override the AI personality to aggressive for Hard.
    scenario_meta.setdefault("personalities", {})["BRICS"] = {
        "name": "aggressive",
        "weights": {
            "attack_damage":        5.0,
            "attack_kill_bonus":    80.0,
            "approach_enemy_hq":    40.0,
            "retreat_when_low_hp":  0.8,
            "threat_aversion_base": 0.2,
        },
    }


def _do_autosave(state: GameState) -> None:
    """Write autosave silently; print path or error to stdout."""
    try:
        p = save_autosave(state, _SCENARIO_SLUG)
        print(f"[autosave] {p}")
    except Exception as exc:          # pragma: no cover
        print(f"[autosave FAILED] {exc}")


# ---------------------------------------------------------------------------
# Drawing helpers — menus
# ---------------------------------------------------------------------------

def _draw_main_menu(
    surface: pygame.Surface,
    font_big: pygame.font.Font,
    font_ui: pygame.font.Font,
) -> list[tuple[pygame.Rect, str]]:
    """Draw the title screen.  Returns [(rect, action_id)] click targets."""
    sw, sh = surface.get_size()
    mx, my = pygame.mouse.get_pos()

    # Full-screen gradient-ish background
    surface.fill(BG)
    # Subtle horizontal rule
    pygame.draw.line(surface, (35, 50, 80), (0, sh // 2 - 10), (sw, sh // 2 - 10))

    PANEL_W, PANEL_H = 440, 360
    px = (sw - PANEL_W) // 2
    py = (sh - PANEL_H) // 2

    panel = pygame.Surface((PANEL_W, PANEL_H), pygame.SRCALPHA)
    panel.fill((14, 20, 38, 220))
    pygame.draw.rect(panel, (60, 100, 180), (0, 0, PANEL_W, PANEL_H), 2)

    # Title
    t1 = font_big.render("MODERN WARFARE 4X", True, (180, 210, 255))
    panel.blit(t1, ((PANEL_W - t1.get_width()) // 2, 20))
    t2 = font_ui.render("Turn-based Tactical Hex Strategy", True, (110, 140, 180))
    panel.blit(t2, ((PANEL_W - t2.get_width()) // 2, 80))
    pygame.draw.line(panel, (50, 80, 140), (20, 110), (PANEL_W - 20, 110))

    # Buttons
    BTN_W, BTN_H = 260, 52
    buttons_spec = [
        ("CAMPAIGN",   "campaign",  (50, 100, 200), (100, 160, 255)),
        ("LOAD GAME",  "load",      (40,  80, 160), ( 90, 140, 220)),
        ("QUIT",       "quit",      (60,  30,  40), (200,  80,  80)),
    ]
    click_targets: list[tuple[pygame.Rect, str]] = []
    btn_x = (PANEL_W - BTN_W) // 2
    btn_y = 130
    for label, action, col_base, col_hover in buttons_spec:
        scr_rect = pygame.Rect(px + btn_x, py + btn_y, BTN_W, BTN_H)
        hover = scr_rect.collidepoint(mx, my)
        bg = col_hover if hover else col_base
        row = pygame.Surface((BTN_W, BTN_H), pygame.SRCALPHA)
        row.fill((*bg, 220))
        pygame.draw.rect(row, col_hover, (0, 0, BTN_W, BTN_H), 2)
        lbl = font_big.render(label, True, (240, 245, 255))
        row.blit(lbl, ((BTN_W - lbl.get_width()) // 2, (BTN_H - lbl.get_height()) // 2))
        panel.blit(row, (btn_x, btn_y))
        click_targets.append((scr_rect, action))
        btn_y += BTN_H + 12

    surface.blit(panel, (px, py))
    return click_targets


def _draw_pre_match(
    surface: pygame.Surface,
    font_big: pygame.font.Font,
    font_ui: pygame.font.Font,
    font_hud: pygame.font.Font,
    pre_match: dict[str, str],
    scenario_name: str,
) -> list[tuple[pygame.Rect, str]]:
    """Draw the pre-match config panel.  Returns click targets."""
    sw, sh = surface.get_size()
    mx, my = pygame.mouse.get_pos()

    PANEL_W, PANEL_H = 520, 440
    px = (sw - PANEL_W) // 2
    py = (sh - PANEL_H) // 2

    panel = pygame.Surface((PANEL_W, PANEL_H), pygame.SRCALPHA)
    panel.fill((14, 20, 38, 228))
    pygame.draw.rect(panel, (60, 100, 180), (0, 0, PANEL_W, PANEL_H), 2)

    # Header
    t1 = font_big.render("CAMPAIGN", True, (160, 200, 255))
    panel.blit(t1, (20, 18))
    t2 = font_ui.render(scenario_name, True, (120, 160, 200))
    panel.blit(t2, (24, 68))
    pygame.draw.line(panel, (50, 80, 140), (16, 98), (PANEL_W - 16, 98))

    click_targets: list[tuple[pygame.Rect, str]] = []
    PAD = 24

    # ── Faction ──────────────────────────────────────────────────────────
    f_lbl = font_hud.render("FACTION", True, (150, 180, 220))
    panel.blit(f_lbl, (PAD, 112))
    factions_spec = [
        ("NATO",       "NATO",    True),
        ("BRICS",      "BRICS",   False),
        ("GUERILLA",   "GRL",     False),
    ]
    fx = PAD
    for full_name, faction_id, selectable in factions_spec:
        is_selected = (pre_match["faction"] == faction_id)
        btn_w = 130 if full_name == "GUERILLA" else 110
        scr_rect = pygame.Rect(px + fx, py + 140, btn_w, 36)
        if selectable:
            hover = scr_rect.collidepoint(mx, my)
            if is_selected:
                bg = (40, 90, 180); border = (130, 180, 255)
            elif hover:
                bg = (30, 60, 110); border = ( 90, 130, 200)
            else:
                bg = (20, 40,  80); border = ( 60,  90, 150)
            tc = (230, 240, 255)
        else:
            bg = (22, 26, 36); border = (44, 52, 68); tc = (55, 65, 80)
        row = pygame.Surface((btn_w, 36), pygame.SRCALPHA)
        row.fill((*bg, 215))
        pygame.draw.rect(row, border, (0, 0, btn_w, 36), 1)
        nl = font_ui.render(faction_id, True, tc)
        row.blit(nl, ((btn_w - nl.get_width()) // 2, (36 - nl.get_height()) // 2))
        panel.blit(row, (fx, 140))
        if selectable:
            click_targets.append((scr_rect, f"faction_{faction_id}"))
        fx += btn_w + 10

    # ── Difficulty ────────────────────────────────────────────────────────
    d_lbl = font_hud.render("DIFFICULTY", True, (150, 180, 220))
    panel.blit(d_lbl, (PAD, 206))
    for diff_id, label in [("normal", "NORMAL"), ("hard", "HARD")]:
        is_sel = (pre_match["difficulty"] == diff_id)
        btn_w  = 140
        bx     = PAD if diff_id == "normal" else PAD + 160
        scr_rect = pygame.Rect(px + bx, py + 238, btn_w, 38)
        hover    = scr_rect.collidepoint(mx, my)
        if is_sel:
            bg = (35, 110, 55) if diff_id == "normal" else (120, 50, 30)
            border = (80, 200, 120) if diff_id == "normal" else (220, 100, 60)
        elif hover:
            bg = (24, 72, 36) if diff_id == "normal" else (80, 36, 22)
            border = (60, 140, 80) if diff_id == "normal" else (160, 70, 44)
        else:
            bg = (20, 40, 28); border = (44, 60, 50)
        tc = (220, 255, 220) if diff_id == "normal" else (255, 200, 160)
        row = pygame.Surface((btn_w, 38), pygame.SRCALPHA)
        row.fill((*bg, 215))
        pygame.draw.rect(row, border, (0, 0, btn_w, 38), 1)
        nl = font_hud.render(label, True, tc)
        row.blit(nl, ((btn_w - nl.get_width()) // 2, (38 - nl.get_height()) // 2))
        panel.blit(row, (bx, 238))
        click_targets.append((scr_rect, f"diff_{diff_id}"))

    pygame.draw.line(panel, (50, 80, 140), (16, 300), (PANEL_W - 16, 300))

    # Difficulty description
    desc = ("Standard start — good for learning.",
            "AI gets +400cr, +3oil and plays aggressively.")
    dc = (160, 220, 160) if pre_match["difficulty"] == "normal" else (255, 190, 140)
    dl = font_ui.render(desc[0 if pre_match["difficulty"] == "normal" else 1], True, dc)
    panel.blit(dl, ((PANEL_W - dl.get_width()) // 2, 310))

    # ── Buttons ───────────────────────────────────────────────────────────
    BTN_W, BTN_H = 160, 48
    for bx, label, action, col_b, col_h in [
        (PAD,              "START",  "start",  (40, 100, 40), ( 80, 200,  80)),
        (PANEL_W - PAD - BTN_W, "BACK", "back", (60, 40, 40), (200, 100, 100)),
    ]:
        scr_rect = pygame.Rect(px + bx, py + PANEL_H - BTN_H - 20, BTN_W, BTN_H)
        hover = scr_rect.collidepoint(mx, my)
        bg    = col_h if hover else col_b
        row   = pygame.Surface((BTN_W, BTN_H), pygame.SRCALPHA)
        row.fill((*bg, 230))
        pygame.draw.rect(row, col_h, (0, 0, BTN_W, BTN_H), 2)
        nl = font_big.render(label, True, (240, 250, 255))
        row.blit(nl, ((BTN_W - nl.get_width()) // 2, (BTN_H - nl.get_height()) // 2))
        panel.blit(row, (bx, PANEL_H - BTN_H - 20))
        click_targets.append((scr_rect, action))

    surface.blit(panel, (px, py))
    return click_targets


def _draw_load_menu(
    surface: pygame.Surface,
    font_big: pygame.font.Font,
    font_ui: pygame.font.Font,
    saves_info: list[dict[str, Any]],
) -> list[tuple[pygame.Rect, str]]:
    """Draw the load-game panel.  Returns [(rect, path_str_or_'back')] targets."""
    sw, sh = surface.get_size()
    mx, my = pygame.mouse.get_pos()

    PANEL_W, PANEL_H = 480, 60 + len(saves_info) * 56 + 80
    px = (sw - PANEL_W) // 2
    py = (sh - PANEL_H) // 2

    panel = pygame.Surface((PANEL_W, PANEL_H), pygame.SRCALPHA)
    panel.fill((14, 20, 38, 228))
    pygame.draw.rect(panel, (60, 100, 180), (0, 0, PANEL_W, PANEL_H), 2)

    t1 = font_big.render("LOAD GAME", True, (160, 200, 255))
    panel.blit(t1, (20, 16))
    pygame.draw.line(panel, (50, 80, 140), (16, 56), (PANEL_W - 16, 56))

    click_targets: list[tuple[pygame.Rect, str]] = []
    BTN_W, BTN_H = PANEL_W - 40, 44
    y = 68

    for info in saves_info:
        exists   = info["exists"]
        turn     = info["turn"]
        label    = info["label"]
        turn_txt = f"Turn {turn}" if turn is not None else "(empty)"
        scr_rect = pygame.Rect(px + 20, py + y, BTN_W, BTN_H)
        hover    = scr_rect.collidepoint(mx, my) and exists
        if exists:
            bg = (36, 68, 120) if hover else (24, 44, 80)
            border = (90, 150, 230) if hover else (60, 100, 160)
            tc = (230, 240, 255)
        else:
            bg = (22, 26, 36); border = (38, 44, 58); tc = (55, 65, 80)
        row = pygame.Surface((BTN_W, BTN_H), pygame.SRCALPHA)
        row.fill((*bg, 220))
        pygame.draw.rect(row, border, (0, 0, BTN_W, BTN_H), 1)
        nl = font_ui.render(f"{label:<12}{turn_txt}", True, tc)
        row.blit(nl, (12, (BTN_H - nl.get_height()) // 2))
        panel.blit(row, (20, y))
        if exists:
            click_targets.append((scr_rect, str(info["path"])))
        y += BTN_H + 10

    # Back button
    BB_W, BB_H = 140, 44
    back_rect = pygame.Rect(px + (PANEL_W - BB_W) // 2, py + PANEL_H - BB_H - 14, BB_W, BB_H)
    hover = back_rect.collidepoint(mx, my)
    bg = (60, 40, 40) if not hover else (90, 50, 50)
    row = pygame.Surface((BB_W, BB_H), pygame.SRCALPHA)
    row.fill((*bg, 230))
    pygame.draw.rect(row, (180, 80, 80), (0, 0, BB_W, BB_H), 2)
    bl = font_big.render("BACK", True, (255, 200, 200))
    row.blit(bl, ((BB_W - bl.get_width()) // 2, (BB_H - bl.get_height()) // 2))
    panel.blit(row, ((PANEL_W - BB_W) // 2, PANEL_H - BB_H - 14))
    click_targets.append((back_rect, "back"))

    surface.blit(panel, (px, py))
    return click_targets


# ---------------------------------------------------------------------------
# Drawing helpers — in-game UI
# ---------------------------------------------------------------------------

def _draw_end_turn_button(
    surface: pygame.Surface,
    font_hud: pygame.font.Font,
) -> pygame.Rect:
    """Draw the END TURN button; return its Rect for click detection."""
    BTN_W, BTN_H = 196, 44
    mx, my = pygame.mouse.get_pos()
    btn = pygame.Rect(WIDTH - BTN_W - 14, HEIGHT - BTN_H - 36, BTN_W, BTN_H)
    hover = btn.collidepoint(mx, my)
    bg = (50, 100, 180) if hover else (30, 60, 120)
    pygame.draw.rect(surface, bg, btn)
    pygame.draw.rect(surface, (90, 160, 255), btn, 2)
    lbl = font_hud.render("END TURN [E]", True, (220, 240, 255))
    surface.blit(lbl, (
        btn.x + (BTN_W - lbl.get_width())  // 2,
        btn.y + (BTN_H - lbl.get_height()) // 2,
    ))
    return btn


def _draw_game_over(
    surface: pygame.Surface,
    font_ui: pygame.font.Font,
    font_hud: pygame.font.Font,
    state: GameState,
    perspective_fid: str,
) -> list[tuple[pygame.Rect, str]]:
    """
    Draw the game-over modal from *perspective_fid*'s POV.
    Returns ``[(rect, action_id), ...]``.
    action_id is ``"retry"`` (restart from pre-match) or ``"menu"`` (main menu).
    """
    outcome = state.outcomes.get(perspective_fid, Outcome.PENDING)
    if outcome == Outcome.PENDING:
        return []

    sw, sh = surface.get_size()
    dim = pygame.Surface((sw, sh), pygame.SRCALPHA)
    dim.fill((0, 0, 0, 170))
    surface.blit(dim, (0, 0))

    PANEL_W, PANEL_H = 480, 240
    px = (sw - PANEL_W) // 2
    py = (sh - PANEL_H) // 2

    won        = (outcome == Outcome.WON)
    border_col = (90, 220, 140) if won else (220, 90, 90)
    title_txt  = "VICTORY" if won else "DEFEAT"

    pygame.draw.rect(surface, (16, 22, 38), (px, py, PANEL_W, PANEL_H))
    pygame.draw.rect(surface, border_col,   (px, py, PANEL_W, PANEL_H), 3)

    big = pygame.font.SysFont("consolas", 56, bold=True)
    t_lbl = big.render(title_txt, True, border_col)
    surface.blit(t_lbl, (px + (PANEL_W - t_lbl.get_width()) // 2, py + 24))

    winner_fid = state.winner()
    sub_txt = f"Turn {state.turn_number}"
    if winner_fid:
        sub_txt += f"   |   Winner: {winner_fid}"
    s_lbl = font_ui.render(sub_txt, True, (200, 200, 200))
    surface.blit(s_lbl, (px + (PANEL_W - s_lbl.get_width()) // 2, py + 108))

    BTN_W, BTN_H = 160, 48
    btn_y  = py + PANEL_H - BTN_H - 20
    mx, my = pygame.mouse.get_pos()

    retry_rect = pygame.Rect(px + 50,                    btn_y, BTN_W, BTN_H)
    menu_rect  = pygame.Rect(px + PANEL_W - 50 - BTN_W,  btn_y, BTN_W, BTN_H)
    for rect, label in [(retry_rect, "RETRY"), (menu_rect, "MENU")]:
        hover = rect.collidepoint(mx, my)
        bg = (60, 90, 140) if hover else (38, 58, 100)
        pygame.draw.rect(surface, bg, rect)
        pygame.draw.rect(surface, (150, 190, 240), rect, 2)
        l_lbl = font_hud.render(label, True, (240, 240, 255))
        surface.blit(
            l_lbl,
            (rect.x + (rect.w - l_lbl.get_width())  // 2,
             rect.y + (rect.h - l_lbl.get_height()) // 2),
        )

    return [(retry_rect, "retry"), (menu_rect, "menu")]


def _draw_build_menu(
    surface: pygame.Surface,
    font_ui: pygame.font.Font,
    font_hud: pygame.font.Font,
    state: GameState,
) -> tuple[pygame.Rect, list[tuple[pygame.Rect, str]]]:
    """
    Draw the build menu panel.
    Returns ``(panel_rect, item_list)`` where item_list contains
    ``(screen_rect, action_id)`` pairs (action_id = type_id or ``"upgrade_tier"``).
    """
    faction = state.active_faction
    mx, my  = pygame.mouse.get_pos()

    PANEL_W = 340
    ITEM_H  = 30
    HDR_H   = 54
    PAD     = 8
    UPGR_H  = 36
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

    panel = pygame.Surface((PANEL_W, PANEL_H), pygame.SRCALPHA)
    panel.fill((14, 20, 38, 228))
    pygame.draw.rect(panel, (80, 120, 200), (0, 0, PANEL_W, PANEL_H), 2)

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

    item_list: list[tuple[pygame.Rect, str]] = []
    y = HDR_H + PAD

    for ut in display_units:
        tier_locked = ut.tier > faction.tier
        affordable  = faction.can_afford(ut.cost_credits, ut.cost_oil)
        row_scr     = pygame.Rect(px + 2, py + y, PANEL_W - 4, ITEM_H - 2)
        is_hover    = row_scr.collidepoint(mx, my) and not tier_locked

        if tier_locked:
            bg = (26, 26, 38, 170); nc = (65, 65, 80); cc = (65, 65, 80)
        elif not affordable:
            bg = (75, 30, 30, 230) if is_hover else (60, 22, 22, 210)
            nc = (180, 105, 105); cc = (200, 115, 115)
        else:
            bg = (52, 78, 140, 235) if is_hover else (34, 54, 100, 215)
            nc = (230, 240, 255); cc = (160, 230, 130)

        row = pygame.Surface((PANEL_W - 4, ITEM_H - 2), pygame.SRCALPHA)
        row.fill(bg)

        badge_col = tier_colors.get(ut.tier, (160, 160, 160)) if not tier_locked else (48, 48, 58)
        b_lbl = font_ui.render(f"T{ut.tier}", True, badge_col)
        row.blit(b_lbl, (4, (ITEM_H - 2 - b_lbl.get_height()) // 2))

        n_lbl = font_ui.render(ut.name, True, nc)
        row.blit(n_lbl, (30, (ITEM_H - 2 - n_lbl.get_height()) // 2))

        c_str = f"{ut.cost_credits}cr" + (f" {ut.cost_oil}oil" if ut.cost_oil else "")
        c_lbl = font_ui.render(c_str, True, cc)
        row.blit(c_lbl, (PANEL_W - 4 - c_lbl.get_width() - 6,
                         (ITEM_H - 2 - c_lbl.get_height()) // 2))

        panel.blit(row, (2, y))
        if not tier_locked:
            item_list.append((row_scr, ut.id))
        y += ITEM_H

    if has_upgrade:
        cost       = next_tier_cost(faction)
        affordable = faction.can_afford(cost, 0)
        row_scr    = pygame.Rect(px + 2, py + y, PANEL_W - 4, UPGR_H - 2)
        is_hover   = row_scr.collidepoint(mx, my)
        if affordable:
            bg = (42, 96, 54, 235) if is_hover else (28, 68, 38, 215)
            tc = (150, 255, 165); bc = (55, 120, 65)
        else:
            bg = (28, 36, 28, 180); tc = (88, 126, 88); bc = (40, 55, 40)
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

    f_lbl = font_ui.render(
        "Left-click to build  |  ESC to close", True, (95, 108, 145)
    )
    panel.blit(f_lbl, ((PANEL_W - f_lbl.get_width()) // 2, y + 4))

    surface.blit(panel, (px, py))
    return panel_rect, item_list


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def main() -> None:  # noqa: C901  (complexity expected in a game loop)
    pygame.init()
    pygame.font.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Modern Warfare 4X")
    clock    = pygame.time.Clock()
    font_big = pygame.font.SysFont("consolas", 40, bold=True)
    font_hud = pygame.font.SysFont("consolas", 22, bold=True)
    font_ui  = pygame.font.SysFont("consolas", 18)

    # ── Persistent objects ────────────────────────────────────────────────
    camera   = Camera(WIDTH, HEIGHT, hex_size=36, offset_x=60.0, offset_y=60.0)
    renderer = HexRenderer(camera)

    # ── Screen state machine ─────────────────────────────────────────────
    # "main_menu" | "pre_match" | "load_menu" | "playing"
    screen_state: str = "main_menu"
    pre_match: dict[str, str] = {"difficulty": "normal", "faction": "NATO"}

    # Menu click targets (rebuilt each render frame)
    menu_btns:   list[tuple[pygame.Rect, str]] = []
    pm_btns:     list[tuple[pygame.Rect, str]] = []
    load_btns:   list[tuple[pygame.Rect, str]] = []

    # ── Playing state ─────────────────────────────────────────────────────
    state:              Optional[GameState]           = None
    hovered:            Optional[Hex]                 = None
    selected_unit:      Optional[Unit]                = None
    movement:           Optional[Movement]            = None
    path_preview:       list[Hex]                     = []
    attack_target_uids: set[int]                      = set()
    fog_enabled:        bool                          = True
    build_hq:           Optional[Hex]                 = None
    bm_panel_rect:      Optional[pygame.Rect]         = None
    bm_items:           list[tuple[pygame.Rect, str]] = []
    go_buttons:         list[tuple[pygame.Rect, str]] = []
    et_btn_rect:        Optional[pygame.Rect]         = None
    ai_steps:           Optional[Iterator[Action]]    = None
    ai_timer:           float                         = 0.0
    save_flash:         float                         = 0.0   # countdown for save msg
    save_flash_msg:     str                           = ""

    def _reset_playing() -> None:
        nonlocal hovered, selected_unit, movement, path_preview
        nonlocal attack_target_uids, build_hq, bm_panel_rect, bm_items
        nonlocal go_buttons, et_btn_rect, ai_steps, ai_timer, save_flash
        hovered = selected_unit = movement = build_hq = None
        path_preview = []; attack_target_uids = set()
        bm_panel_rect = None; bm_items = []; go_buttons = []
        et_btn_rect = None; ai_steps = None; ai_timer = 0.0; save_flash = 0.0

    def _start_playing(new_state: GameState) -> None:
        nonlocal state, screen_state
        state = new_state
        _reset_playing()
        camera.offset_x = 60.0
        camera.offset_y = 60.0
        screen_state = "playing"
        # If it's the AI's turn from the start of a loaded save, kick it off.
        if state.active_faction.is_ai and not state.game_over:
            _kick_ai()

    def _kick_ai() -> None:
        nonlocal ai_steps, ai_timer
        assert state is not None
        _pers = _personality_for(state.active_faction.id)
        ai_steps = take_turn_steps(state, state.active_faction.id, _pers)
        ai_timer = 0.0

    def _end_turn_action() -> None:
        """Shared logic for SPACE / E / end-turn button."""
        nonlocal ai_steps, ai_timer
        assert state is not None
        nonlocal selected_unit, movement, path_preview, attack_target_uids, build_hq
        build_hq = selected_unit = movement = None
        path_preview = []; attack_target_uids = set()
        state.end_turn()
        _do_autosave(state)
        print(f"End turn → {state.active_faction.id} "
              f"(turn {state.turn_number}, "
              f"credits={state.active_faction.credits}, "
              f"oil={state.active_faction.oil})")
        if state.active_faction.is_ai and not state.game_over:
            _kick_ai()

    # ── Main loop ─────────────────────────────────────────────────────────
    global _current_save_slot
    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0

        # ── Events ────────────────────────────────────────────────────────
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                continue

            # ── MAIN MENU ──────────────────────────────────────────────
            if screen_state == "main_menu":
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = pygame.mouse.get_pos()
                    for rect, action in menu_btns:
                        if rect.collidepoint(mx, my):
                            if action == "campaign":
                                screen_state = "pre_match"
                            elif action == "load":
                                screen_state = "load_menu"
                            elif action == "quit":
                                running = False
                            break
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False

            # ── PRE-MATCH CONFIG ───────────────────────────────────────
            elif screen_state == "pre_match":
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = pygame.mouse.get_pos()
                    for rect, action in pm_btns:
                        if rect.collidepoint(mx, my):
                            if action == "start":
                                new_state = _load_initial_state()
                                _apply_difficulty(new_state, _scenario_meta,
                                                  pre_match["difficulty"])
                                _start_playing(new_state)
                            elif action == "back":
                                screen_state = "main_menu"
                            elif action.startswith("diff_"):
                                pre_match["difficulty"] = action[5:]
                            elif action.startswith("faction_"):
                                pre_match["faction"] = action[8:]
                            break
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    screen_state = "main_menu"

            # ── LOAD MENU ──────────────────────────────────────────────
            elif screen_state == "load_menu":
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = pygame.mouse.get_pos()
                    for rect, action in load_btns:
                        if rect.collidepoint(mx, my):
                            if action == "back":
                                screen_state = "main_menu"
                            else:
                                try:
                                    loaded, _ = load_state(Path(action))
                                    _start_playing(loaded)
                                except Exception as exc:
                                    print(f"[load failed] {exc}")
                            break
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    screen_state = "main_menu"

            # ── PLAYING ────────────────────────────────────────────────
            elif screen_state == "playing" and state is not None:

                # Game-over modal owns all input.
                if state.game_over:
                    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        mx, my = pygame.mouse.get_pos()
                        for rect, action in go_buttons:
                            if rect.collidepoint(mx, my):
                                if action == "retry":
                                    screen_state = "pre_match"
                                elif action == "menu":
                                    screen_state = "main_menu"
                                    state = None
                                break
                    elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        screen_state = "main_menu"
                        state = None
                    continue

                # AI turn: camera still responds, but no player game-logic.
                if ai_steps is not None:
                    if not (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1):
                        camera.handle_event(event)
                    continue

                # ── Keyboard ──────────────────────────────────────────
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
                            screen_state = "main_menu"   # ESC → menu

                    elif event.key in (pygame.K_SPACE, pygame.K_e):
                        _end_turn_action()

                    elif event.key == pygame.K_TAB:
                        # Cycle to next unit that can act; pan camera to it.
                        cur_uid = selected_unit.uid if selected_unit else None
                        nxt = state.next_actionable_unit(HUMAN_FACTION, cur_uid)
                        if nxt is not None:
                            selected_unit = nxt
                            movement = (compute_movement(state, nxt)
                                        if not nxt.has_moved else None)
                            path_preview = []
                            attack_target_uids = {
                                t.uid for t in attack_targets(state, nxt)
                            }
                            camera.center_on(nxt.hex)

                    elif event.key == pygame.K_F5:
                        try:
                            assert state is not None
                            p = save_slot(state, _current_save_slot, _SCENARIO_SLUG)
                            save_flash = 2.0
                            save_flash_msg = f"Saved slot {_current_save_slot}"
                            print(f"[save] slot {_current_save_slot} → {p}")
                        except Exception as exc:
                            print(f"[save FAILED] {exc}")
                        _current_save_slot = (_current_save_slot % NUM_SLOTS) + 1

                    elif event.key == pygame.K_f:
                        fog_enabled = not fog_enabled
                        print(f"Fog {'on' if fog_enabled else 'off'}")

                # ── Mouse ─────────────────────────────────────────────
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = pygame.mouse.get_pos()

                    # End-turn button (highest priority)
                    if et_btn_rect is not None and et_btn_rect.collidepoint(mx, my):
                        _end_turn_action()
                        continue

                    clicked = camera.screen_to_hex(mx, my)
                    active_id    = state.active_faction.id
                    clicked_unit = state.unit_at(clicked)

                    if build_hq is not None:
                        consumed = False
                        for rect, action_id in bm_items:
                            if rect.collidepoint(mx, my):
                                if action_id == "upgrade_tier":
                                    try:
                                        state.upgrade_tier(active_id)
                                        print(f"Tier unlocked → {state.active_faction.tier}")
                                    except ValueError as e:
                                        print(f"Upgrade failed: {e}")
                                else:
                                    try:
                                        u = state.build_unit(action_id, active_id, build_hq)
                                        print(f"Built {u.unit_type.name} at {u.hex}")
                                    except ValueError as e:
                                        print(f"Build failed: {e}")
                                build_hq = None; consumed = True; break
                        if not consumed:
                            if bm_panel_rect is None or not bm_panel_rect.collidepoint(mx, my):
                                build_hq = None

                    elif selected_unit is not None:
                        clicked_tile = state.tiles.get(clicked)
                        if (clicked_tile and clicked_tile.terrain.is_hq
                                and clicked_tile.owner_faction == active_id):
                            selected_unit = movement = None
                            path_preview = []; attack_target_uids = set()
                            build_hq = clicked
                        elif clicked_unit is not None and clicked_unit.uid in attack_target_uids:
                            result = resolve_attack(state, selected_unit, clicked_unit)
                            print(
                                f"Attack: {selected_unit.unit_type.name} → "
                                f"{clicked_unit.unit_type.name}  "
                                f"{result.damage_dealt} dmg"
                                + (" (killed)" if result.defender_killed else "")
                                + (f"  counter {result.counter_damage}"
                                   if result.counter_damage else "")
                                + (" (attacker killed)" if result.attacker_killed else "")
                            )
                            selected_unit = movement = None
                            path_preview = []; attack_target_uids = set()
                        elif movement is not None and clicked in movement.reachable:
                            state.move_unit(selected_unit.uid, clicked)
                            selected_unit.has_moved = True
                            movement = None; path_preview = []
                            new_targets = {t.uid for t in attack_targets(state, selected_unit)}
                            if new_targets:
                                attack_target_uids = new_targets
                            else:
                                selected_unit = None; attack_target_uids = set()
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
                        else:
                            selected_unit = movement = None
                            path_preview = []; attack_target_uids = set()

                    else:
                        clicked_tile = state.tiles.get(clicked)
                        if (clicked_tile and clicked_tile.terrain.is_hq
                                and clicked_tile.owner_faction == active_id):
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

        # ── Per-frame updates (playing only) ──────────────────────────────
        if screen_state == "playing" and state is not None:
            camera.handle_keys(pygame.key.get_pressed(), dt)
            hovered = camera.screen_to_hex(*pygame.mouse.get_pos())

            # AI step-tick
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
                            _do_autosave(state)
                            print(f"End turn → {state.active_faction.id} "
                                  f"(turn {state.turn_number}, "
                                  f"credits={state.active_faction.credits}, "
                                  f"oil={state.active_faction.oil})")
                            if state.active_faction.is_ai and not state.game_over:
                                _kick_ai()

            # Path preview
            if selected_unit is not None and movement is not None:
                path_preview = (movement.path_to(hovered)
                                if hovered in movement.reachable else [])

            # Save flash countdown
            if save_flash > 0:
                save_flash = max(0.0, save_flash - dt)

        # ── Render ────────────────────────────────────────────────────────
        screen.fill(BG)

        if screen_state == "main_menu":
            menu_btns = _draw_main_menu(screen, font_big, font_ui)

        elif screen_state == "pre_match":
            pm_btns = _draw_pre_match(
                screen, font_big, font_ui, font_hud,
                pre_match, _scenario_meta.get("name", ""),
            )

        elif screen_state == "load_menu":
            saves_info = list_saves(_SCENARIO_SLUG)
            load_btns  = _draw_load_menu(screen, font_big, font_ui, saves_info)

        elif screen_state == "playing" and state is not None:
            # Fog
            viewer_id = state.active_faction.id
            if fog_enabled:
                visible_set  = state.visible_to(viewer_id)
                explored_set = state.explored.get(viewer_id, set())
                def _can_see(u: Unit, _v=viewer_id) -> bool:
                    return can_faction_see_unit(state, _v, u)
            else:
                visible_set = explored_set = _can_see = None

            # Map + overlays
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
                    for uid in attack_target_uids if uid in state.units
                ]
                renderer.draw_attack_overlay(screen, target_hexes, hovered_hex=hovered)
            renderer.draw_units(screen, list(state.units.values()), can_see=_can_see)

            # Build menu
            if build_hq is not None:
                bm_panel_rect, bm_items = _draw_build_menu(
                    screen, font_ui, font_hud, state
                )
            else:
                bm_panel_rect = None; bm_items = []

            # End-turn button (only when player is active and game not over)
            if (not state.game_over
                    and not state.active_faction.is_ai
                    and ai_steps is None
                    and build_hq is None):
                et_btn_rect = _draw_end_turn_button(screen, font_hud)
            else:
                et_btn_rect = None

            # Game-over modal
            if state.game_over:
                go_buttons = _draw_game_over(
                    screen, font_ui, font_hud, state, HUMAN_FACTION
                )
            else:
                go_buttons = []

            # ── HUD top-left ──────────────────────────────────────────
            af = state.active_faction
            ai_suffix      = "  [AI thinking...]" if ai_steps is not None else ""
            scenario_title = _scenario_meta.get("name", "")
            hud_lines: list[tuple[str, tuple[int, int, int]]] = []
            if scenario_title:
                hud_lines.append((scenario_title, (160, 160, 200)))
            hud_lines += [
                (f"Turn {state.turn_number}  —  {af.name}{ai_suffix}", af.color),
                (f"Credits: {af.credits}", (220, 220, 100)),
                (f"Oil: {af.oil}", (220, 160, 80)),
                (f"Tier: {af.tier}", (180, 220, 180)),
                (f"Units: {len(state.units_of(af.id))}", (200, 200, 200)),
            ]
            y = 10
            for txt, col in hud_lines:
                lbl = font_hud.render(txt, True, col)
                screen.blit(lbl, (12, y)); y += 24

            # Selected unit panel
            if selected_unit is not None:
                y += 4
                sel_lines: list[tuple[str, tuple[int, int, int]]] = [
                    (f"[ {selected_unit.unit_type.name} ]", (255, 255, 180)),
                    (f"HP {selected_unit.hp}/{selected_unit.unit_type.hp}  "
                     f"Move {selected_unit.unit_type.move}  "
                     f"Rng {selected_unit.unit_type.range_min}"
                     f"-{selected_unit.unit_type.range_max}",
                     (200, 200, 200)),
                ]
                if hovered is not None:
                    hu = state.unit_at(hovered)
                    if hu is not None and hu.uid in attack_target_uids:
                        atk_dmg, counter_dmg = predict_exchange(
                            state, selected_unit, hu
                        )
                        sel_lines.append((
                            f"→ {atk_dmg} dmg  •  ← {counter_dmg} counter",
                            (255, 180, 180),
                        ))
                sel_lines.append(("Tab=next  •  ESC=cancel", (140, 140, 110)))
                for txt, col in sel_lines:
                    lbl = font_ui.render(txt, True, col)
                    screen.blit(lbl, (12, y)); y += 20

            # Save flash
            if save_flash > 0:
                fl = font_ui.render(save_flash_msg, True, (120, 220, 120))
                screen.blit(fl, (WIDTH - fl.get_width() - 14, HEIGHT - 60))

            # FPS + hover (bottom-left)
            fps_lbl = font_ui.render(
                f"FPS {clock.get_fps():.0f}", True, (160, 160, 160)
            )
            screen.blit(fps_lbl, (12, HEIGHT - 44))
            if hovered:
                hov_lbl = font_ui.render(
                    f"q={hovered.q}, r={hovered.r}", True, (180, 180, 100)
                )
                screen.blit(hov_lbl, (12, HEIGHT - 22))

            # Help (bottom-right)
            help_txt = ("E end turn  •  Tab next unit  •  F5 save  "
                        "•  HQ click build  •  F fog  •  WASD pan  •  scroll zoom")
            h_lbl = font_ui.render(help_txt, True, (100, 110, 130))
            screen.blit(h_lbl, (WIDTH - h_lbl.get_width() - 12, HEIGHT - 22))

        pygame.display.flip()
        await asyncio.sleep(0)

    pygame.quit()


if __name__ == "__main__":
    asyncio.run(main())
