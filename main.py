"""
Saladin's Playground -- turn-based modern-warfare 4X.

Controls (in-game)
------------------
  WASD / arrows  -- pan camera
  Scroll wheel   -- zoom (cursor-anchored)
  Right-drag     -- pan
  Left-click own HQ  -- open build menu
  Left-click unit    -- select / move / attack
  E / SPACE      -- end turn (confirms if any unit can still act)
  Tab            -- cycle to next unit that can act
  H              -- hunker down: +2 DEF, no attack (defend stance)
  R              -- retreat: move selected unit toward own HQ
  J              -- engineer heal +3 HP for adjacent friendly
  F5             -- manual save (cycles slots 1-3)
  F              -- toggle fog of war
  F11            -- toggle fullscreen
  M              -- mute / unmute audio
  ESC            -- cancel selection / back to menu

End-turn confirm modal: Y/Enter = end, N/Esc = cancel
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Iterator, Optional

import pygame

import collections

from src.audio.sounds import SoundManager
from src.ai.heuristic import Action, describe, take_turn_steps
from src.ai.personality import from_dict as personality_from_dict, Personality
from src.persistence.save import (
    NUM_SLOTS,
    list_saves,
    load_state,
    save_autosave,
    save_slot,
    thumbnail_path,
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
from src.engine.procgen import generate_map
from src.engine.scenario import load_scenario
from src.engine.skirmish import build_skirmish_state, load_skirmish_map
from src.engine.stance_actions import (
    actionable_units,
    adjacent_friendly_patients,
    medic_heal,
    retreat as do_retreat,
    set_defend,
)
from src.engine.state import Faction, GameState
from src.engine.tile import Tile, load_terrain
from src.engine.unit import STANCE_DEFEND, Unit, load_units
from src.engine.tech import (
    all_displayable_units,
    can_upgrade_tier,
    next_tier_cost,
)
from src.engine.victory import Outcome, default_victory_config
from src.render.camera import Camera
from src.render.floaters import FloaterLayer, DAMAGE_DURATION, LEVELUP_DURATION
from src.render.hex_renderer import HexRenderer
from src.render.tooltip import (
    draw_tooltip,
    terrain_tooltip_lines,
    unit_tooltip_lines,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HUMAN_FACTION = "NATO"           # player faction; CP-17 will make this configurable
AI_ACTION_DELAY = 0.55           # seconds per visible AI action

WIDTH, HEIGHT = 1280, 720          # initial window size; user can resize/maximize
MIN_WIDTH, MIN_HEIGHT = 960, 600    # below this UI starts wrapping ugly
FPS = 60
BG = (18, 24, 38)

DEFAULT_SCENARIO = Path("data/scenarios/m1.json")

# Campaign mission registry -- order matches in-game menu.
SCENARIOS: list[dict] = [
    {
        "slug": "m1",
        "path": Path("data/scenarios/m1.json"),
        "name": "Mission 1: First Contact",
        "desc": "NATO vs BRICS. Destroy the BRICS field HQ.",
    },
    {
        "slug": "m2",
        "path": Path("data/scenarios/m2.json"),
        "name": "Mission 2: Shadow War",
        "desc": "NATO vs Guerilla. Hold both urban zones or destroy their HQ.",
    },
    {
        "slug": "m3",
        "path": Path("data/scenarios/m3.json"),
        "name": "Mission 3: Iron Fist",
        "desc": "NATO vs BRICS. Capture every oil well to win.",
    },
    {
        "slug": "m4",
        "path": Path("data/scenarios/m4.json"),
        "name": "Mission 4: Last Stand",
        "desc": "NATO defends. Hold your HQ for 20 turns or destroy theirs.",
    },
    {
        "slug": "m5",
        "path": Path("data/scenarios/m5.json"),
        "name": "Mission 5: Decapitation",
        "desc": "NATO vs BRICS. Destroy the Iskander-M missile system.",
    },
]

# Skirmish map registry
SKIRMISH_MAPS: list[dict] = [
    {
        "slug": "plains",
        "path": Path("data/skirmish/map_plains.json"),
        "name": "Plains",
        "desc": "Open terrain -- fast, aggressive games.",
    },
    {
        "slug": "valley",
        "path": Path("data/skirmish/map_valley.json"),
        "name": "Valley",
        "desc": "River valley with two bridges -- control the crossings.",
    },
    {
        "slug": "frontier",
        "path": Path("data/skirmish/map_frontier.json"),
        "name": "Frontier",
        "desc": "Mountain ridge divides the map -- two passes decide the battle.",
    },
    {
        "slug": "random",
        "path": None,       # None -> procedurally generated
        "name": "Random",
        "desc": "Procedurally generated map. Different every game.",
    },
]

_FACTIONS_ALL = ["NATO", "BRICS", "GUERILLA"]

# ---------------------------------------------------------------------------
# Module-level mutable state (shared between helpers + main loop)
# ---------------------------------------------------------------------------

_selected_scenario: dict = SCENARIOS[0]   # updated when player picks a mission
_SCENARIO_SLUG: str = _selected_scenario["slug"]

_scenario_meta: dict[str, Any] = {
    "name":          SCENARIOS[0]["name"],
    "description":   SCENARIOS[0]["desc"],
    "personalities": {},
}

_current_save_slot: int = 1   # cycles 1..NUM_SLOTS on each F5 press

# Skirmish configuration (mutated by the skirmish-config screen)
_skirmish_cfg: dict = {
    "map_idx":       0,                 # index into SKIRMISH_MAPS
    "player":        "NATO",
    "ai_factions":   ["BRICS"],         # list of 1 or 2 AI faction IDs
    "victory_types": ["destroy_hq"],    # toggled on the config screen
    "difficulty":    "normal",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_initial_state(
    scenario_path: "str | Path | None" = None,
) -> GameState:
    """Parse scenario JSON -> GameState; refresh _scenario_meta side-channel."""
    global _scenario_meta
    if scenario_path is None:
        scenario_path = _selected_scenario["path"]
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

    ``normal`` -- no changes (scenario defaults).
    ``hard``   -- each AI faction gets +400 cr, +3 oil, aggressive personality.
    ``insane`` -- each AI faction gets +900 cr, +6 oil, predator personality
                 (heavier kill bias, stronger HQ pull, lower threat aversion).
    Works for any faction name (NATO, BRICS, GUERILLA, ?).
    """
    if difficulty == "normal":
        return

    if difficulty == "hard":
        bumps = {"credits": 400, "oil": 3}
        personality = {
            "name": "aggressive",
            "weights": {
                "attack_damage":        5.0,
                "attack_kill_bonus":    80.0,
                "approach_enemy_hq":    40.0,
                "retreat_when_low_hp":  0.8,
                "threat_aversion_base": 0.2,
            },
        }
    elif difficulty == "insane":
        bumps = {"credits": 900, "oil": 6}
        personality = {
            "name": "predator",
            "weights": {
                "attack_damage":           6.0,
                "attack_kill_bonus":       120.0,
                "approach_enemy_hq":       60.0,
                "approach_capture_target": 55.0,
                "retreat_when_low_hp":     0.4,
                "threat_aversion_base":    0.1,
                "build_when_low_army":     55.0,
                "build_engineer_bonus":    50.0,
            },
        }
    else:
        return  # unknown difficulty -- no-op

    for faction in state.factions:
        if faction.is_ai:
            faction.credits += bumps["credits"]
            faction.oil     += bumps["oil"]
            scenario_meta.setdefault("personalities", {})[faction.id] = personality


def _do_autosave(state: GameState, screen_surf: "pygame.Surface | None" = None) -> None:
    """Write autosave silently; print path or error to stdout.

    *screen_surf*, if provided, is scaled to 200x120 and saved as a PNG
    thumbnail alongside the JSON (CP-35).
    """
    try:
        p = save_autosave(state, _SCENARIO_SLUG)
        print(f"[autosave] {p}")
        if screen_surf is not None:
            try:
                _thumb = pygame.transform.smoothscale(screen_surf, (200, 120))
                pygame.image.save(_thumb, str(thumbnail_path(p)))
            except Exception:
                pass
    except Exception as exc:          # pragma: no cover
        print(f"[autosave FAILED] {exc}")


def _start_skirmish() -> tuple[GameState, str]:
    """Build a skirmish GameState from *_skirmish_cfg*.

    Returns ``(state, slug)`` where slug is used for autosave filenames.
    """
    global _SCENARIO_SLUG, _scenario_meta
    cfg = _skirmish_cfg
    map_entry = SKIRMISH_MAPS[cfg["map_idx"]]

    if map_entry["path"] is None:           # random / procgen
        result   = generate_map()
        tiles    = result["tiles"]
        hq_pos   = result["hq_positions"]
        slug     = f"skirmish_random_{result['seed']}"
    else:
        tiles, hq_pos, _ = load_skirmish_map(map_entry["path"])
        slug = f"skirmish_{map_entry['slug']}"

    state = build_skirmish_state(
        tiles=tiles,
        hq_positions=hq_pos,
        player_faction=cfg["player"],
        ai_factions=list(cfg["ai_factions"]),
        victory_types=list(cfg["victory_types"]),
    )

    # Wire up scenario_meta so HUD title and difficulty apply correctly
    _SCENARIO_SLUG = slug
    _scenario_meta = {
        "name":          f"Skirmish -- {map_entry['name']}",
        "description":   map_entry["desc"],
        "personalities": {},
    }

    _apply_difficulty(state, _scenario_meta, cfg["difficulty"])
    return state, slug


# ---------------------------------------------------------------------------
# Drawing helpers -- menus
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

    PANEL_W, PANEL_H = 440, 420
    px = (sw - PANEL_W) // 2
    py = (sh - PANEL_H) // 2

    panel = pygame.Surface((PANEL_W, PANEL_H), pygame.SRCALPHA)
    panel.fill((14, 20, 38, 220))
    pygame.draw.rect(panel, (60, 100, 180), (0, 0, PANEL_W, PANEL_H), 2)

    # Title
    t1 = font_big.render("SALADIN'S PLAYGROUND", True, (180, 210, 255))
    panel.blit(t1, ((PANEL_W - t1.get_width()) // 2, 20))
    t2 = font_ui.render("Turn-based Tactical Hex Strategy", True, (110, 140, 180))
    panel.blit(t2, ((PANEL_W - t2.get_width()) // 2, 80))
    pygame.draw.line(panel, (50, 80, 140), (20, 110), (PANEL_W - 20, 110))

    # Buttons
    BTN_W, BTN_H = 260, 52
    buttons_spec = [
        ("CAMPAIGN",   "campaign",  (50, 100, 200), (100, 160, 255)),
        ("SKIRMISH",   "skirmish",  (30,  80, 100), ( 70, 160, 200)),
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

    # ? Faction ?
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

    # ? Difficulty ?
    d_lbl = font_hud.render("DIFFICULTY", True, (150, 180, 220))
    panel.blit(d_lbl, (PAD, 206))
    DIFF_SPEC = [
        ("normal", "NORMAL", (35, 110, 55), (80, 200, 120), (220, 255, 220)),
        ("hard",   "HARD",   (120, 50, 30), (220, 100, 60), (255, 200, 160)),
        ("insane", "INSANE", (130, 30, 130), (220, 100, 220), (255, 180, 255)),
    ]
    DBTN_W   = 110
    DBTN_GAP = 8
    base_x   = PAD
    for di, (diff_id, label, sel_bg, sel_bd, sel_tc) in enumerate(DIFF_SPEC):
        is_sel = (pre_match["difficulty"] == diff_id)
        bx       = base_x + di * (DBTN_W + DBTN_GAP)
        scr_rect = pygame.Rect(px + bx, py + 238, DBTN_W, 38)
        hover    = scr_rect.collidepoint(mx, my)
        if is_sel:
            bg, border = sel_bg, sel_bd
        elif hover:
            bg = tuple(c // 2 for c in sel_bg)
            border = tuple(c // 2 for c in sel_bd)
        else:
            bg = (20, 28, 36); border = (44, 60, 60)
        tc = sel_tc if (is_sel or hover) else (130, 140, 150)
        row = pygame.Surface((DBTN_W, 38), pygame.SRCALPHA)
        row.fill((*bg, 215))
        pygame.draw.rect(row, border, (0, 0, DBTN_W, 38), 1)
        nl = font_hud.render(label, True, tc)
        row.blit(nl, ((DBTN_W - nl.get_width()) // 2, (38 - nl.get_height()) // 2))
        panel.blit(row, (bx, 238))
        click_targets.append((scr_rect, f"diff_{diff_id}"))

    pygame.draw.line(panel, (50, 80, 140), (16, 300), (PANEL_W - 16, 300))

    # Difficulty description
    descs = {
        "normal": ("Standard start -- good for learning.",  (160, 220, 160)),
        "hard":   ("AI gets +400cr, +3oil and plays aggressively.", (255, 190, 140)),
        "insane": ("AI gets +900cr, +6oil and hunts you down.",     (255, 170, 240)),
    }
    desc_text, dc = descs.get(pre_match["difficulty"], descs["normal"])
    dl = font_ui.render(desc_text, True, dc)
    panel.blit(dl, ((PANEL_W - dl.get_width()) // 2, 310))

    # ? Buttons ?
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

    # CP-35: rows are taller when thumbnails are present
    _THUMB_W, _THUMB_H = 80, 48   # displayed size inside each row
    PANEL_W, PANEL_H = 520, 60 + len(saves_info) * 56 + 80
    px = (sw - PANEL_W) // 2
    py = (sh - PANEL_H) // 2

    panel = pygame.Surface((PANEL_W, PANEL_H), pygame.SRCALPHA)
    panel.fill((14, 20, 38, 228))
    pygame.draw.rect(panel, (60, 100, 180), (0, 0, PANEL_W, PANEL_H), 2)

    t1 = font_big.render("LOAD GAME", True, (160, 200, 255))
    panel.blit(t1, (20, 16))
    pygame.draw.line(panel, (50, 80, 140), (16, 56), (PANEL_W - 16, 56))

    click_targets: list[tuple[pygame.Rect, str]] = []
    BTN_W, BTN_H = PANEL_W - 40, 48
    y = 68

    for info in saves_info:
        exists    = info["exists"]
        turn      = info["turn"]
        label     = info["label"]
        turn_txt  = f"Turn {turn}" if turn is not None else "(empty)"
        thumb_p   = info.get("thumb_path")
        scr_rect  = pygame.Rect(px + 20, py + y, BTN_W, BTN_H)
        hover     = scr_rect.collidepoint(mx, my) and exists
        if exists:
            bg = (36, 68, 120) if hover else (24, 44, 80)
            border = (90, 150, 230) if hover else (60, 100, 160)
            tc = (230, 240, 255)
        else:
            bg = (22, 26, 36); border = (38, 44, 58); tc = (55, 65, 80)
        row = pygame.Surface((BTN_W, BTN_H), pygame.SRCALPHA)
        row.fill((*bg, 220))
        pygame.draw.rect(row, border, (0, 0, BTN_W, BTN_H), 1)

        # CP-35: thumbnail on the right side of the row
        txt_x = 12
        if thumb_p is not None and thumb_p.is_file():
            try:
                _t = pygame.image.load(str(thumb_p))
                _t = pygame.transform.smoothscale(_t, (_THUMB_W, _THUMB_H))
                row.blit(_t, (BTN_W - _THUMB_W - 4, (BTN_H - _THUMB_H) // 2))
            except Exception:
                pass   # bad/missing PNG -- fall back to text-only
        else:
            # Placeholder block when no thumbnail
            _ph_x = BTN_W - _THUMB_W - 4
            _ph_y = (BTN_H - _THUMB_H) // 2
            pygame.draw.rect(row, (30, 38, 55), (_ph_x, _ph_y, _THUMB_W, _THUMB_H))
            pygame.draw.rect(row, border,       (_ph_x, _ph_y, _THUMB_W, _THUMB_H), 1)

        nl = font_ui.render(f"{label:<12}{turn_txt}", True, tc)
        row.blit(nl, (txt_x, (BTN_H - nl.get_height()) // 2))
        panel.blit(row, (20, y))
        if exists:
            click_targets.append((scr_rect, str(info["path"])))
        y += BTN_H + 8

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


def _draw_mission_select(
    surface: pygame.Surface,
    font_big: pygame.font.Font,
    font_ui: pygame.font.Font,
    font_hud: pygame.font.Font,
) -> list[tuple[pygame.Rect, str]]:
    """Draw the mission selection panel.  Returns [(rect, action_id)] click targets.

    action_id is ``"mission_<index>"`` for each row, or ``"back"``.
    """
    sw, sh = surface.get_size()
    mx, my = pygame.mouse.get_pos()

    ROW_H   = 64
    PANEL_W = 640
    PANEL_H = 66 + len(SCENARIOS) * ROW_H + 68
    px = (sw - PANEL_W) // 2
    py = max(10, (sh - PANEL_H) // 2)

    panel = pygame.Surface((PANEL_W, PANEL_H), pygame.SRCALPHA)
    panel.fill((14, 20, 38, 228))
    pygame.draw.rect(panel, (60, 100, 180), (0, 0, PANEL_W, PANEL_H), 2)

    t1 = font_big.render("SELECT MISSION", True, (160, 200, 255))
    panel.blit(t1, (20, 18))
    pygame.draw.line(panel, (50, 80, 140), (16, 56), (PANEL_W - 16, 56))

    click_targets: list[tuple[pygame.Rect, str]] = []
    y = 64

    for i, sc in enumerate(SCENARIOS):
        scr_rect = pygame.Rect(px + 16, py + y, PANEL_W - 32, ROW_H - 4)
        hover = scr_rect.collidepoint(mx, my)
        bg     = (38, 68, 130, 235) if hover else (24, 44, 80, 215)
        border = (90, 150, 230)     if hover else (50, 90, 160)

        row = pygame.Surface((PANEL_W - 32, ROW_H - 4), pygame.SRCALPHA)
        row.fill(bg)
        pygame.draw.rect(row, border, (0, 0, PANEL_W - 32, ROW_H - 4), 1)

        n_lbl = font_hud.render(sc["name"], True, (230, 240, 255) if hover else (180, 210, 255))
        row.blit(n_lbl, (10, 6))
        d_lbl = font_ui.render(sc["desc"], True, (160, 185, 225) if hover else (110, 140, 185))
        row.blit(d_lbl, (12, 32))

        panel.blit(row, (16, y))
        click_targets.append((scr_rect, f"mission_{i}"))
        y += ROW_H

    # Back button
    BB_W, BB_H = 140, 44
    back_rect = pygame.Rect(px + (PANEL_W - BB_W) // 2, py + PANEL_H - BB_H - 14, BB_W, BB_H)
    hover = back_rect.collidepoint(mx, my)
    bg_b  = (90, 50, 50) if hover else (60, 40, 40)
    row_b = pygame.Surface((BB_W, BB_H), pygame.SRCALPHA)
    row_b.fill((*bg_b, 230))
    pygame.draw.rect(row_b, (180, 80, 80), (0, 0, BB_W, BB_H), 2)
    bl = font_big.render("BACK", True, (255, 200, 200))
    row_b.blit(bl, ((BB_W - bl.get_width()) // 2, (BB_H - bl.get_height()) // 2))
    panel.blit(row_b, ((PANEL_W - BB_W) // 2, PANEL_H - BB_H - 14))
    click_targets.append((back_rect, "back"))

    surface.blit(panel, (px, py))
    return click_targets


def _draw_skirmish_config(
    surface: pygame.Surface,
    font_big: pygame.font.Font,
    font_ui: pygame.font.Font,
    font_hud: pygame.font.Font,
    cfg: dict,
) -> list[tuple[pygame.Rect, str]]:
    """Draw the skirmish configuration screen.

    Returns [(rect, action_id)] click targets.

    action_id values
    ----------------
    ``"map_<N>"``          -- select map index N
    ``"player_<FID>"``     -- set player faction
    ``"ai_toggle_<FID>"``  -- toggle AI faction in/out (2nd AI slot)
    ``"vtype_<name>"``     -- toggle a victory type flag
    ``"diff_normal"`` / ``"diff_hard"``
    ``"start"`` / ``"back"``
    """
    sw, sh = surface.get_size()
    mx, my = pygame.mouse.get_pos()

    PANEL_W = 660
    PANEL_H = 560
    px = (sw - PANEL_W) // 2
    py = max(10, (sh - PANEL_H) // 2)

    panel = pygame.Surface((PANEL_W, PANEL_H), pygame.SRCALPHA)
    panel.fill((14, 20, 38, 228))
    pygame.draw.rect(panel, (60, 130, 160), (0, 0, PANEL_W, PANEL_H), 2)

    t1 = font_big.render("SKIRMISH SETUP", True, (140, 220, 255))
    panel.blit(t1, (20, 16))
    pygame.draw.line(panel, (50, 100, 140), (16, 54), (PANEL_W - 16, 54))

    click_targets: list[tuple[pygame.Rect, str]] = []
    PAD = 20

    # ? Map selection ?
    section_y = 62
    lbl = font_hud.render("MAP", True, (140, 200, 230))
    panel.blit(lbl, (PAD, section_y))

    MAP_BTN_W = (PANEL_W - 2 * PAD - 3 * 8) // 4
    MAP_BTN_H = 50
    for i, sm in enumerate(SKIRMISH_MAPS):
        bx = PAD + i * (MAP_BTN_W + 8)
        by = section_y + 24
        scr_rect = pygame.Rect(px + bx, py + by, MAP_BTN_W, MAP_BTN_H)
        selected = (cfg["map_idx"] == i)
        hover    = scr_rect.collidepoint(mx, my)
        if selected:
            bg = (30, 110, 140); border = (80, 200, 240)
        elif hover:
            bg = (20,  70, 100); border = (60, 150, 190)
        else:
            bg = (18,  40,  60); border = (40, 80, 110)
        row = pygame.Surface((MAP_BTN_W, MAP_BTN_H), pygame.SRCALPHA)
        row.fill((*bg, 220))
        pygame.draw.rect(row, border, (0, 0, MAP_BTN_W, MAP_BTN_H), 1)
        n_lbl = font_hud.render(sm["name"], True, (230, 245, 255) if selected else (160, 200, 230))
        row.blit(n_lbl, ((MAP_BTN_W - n_lbl.get_width()) // 2, 6))
        d_lbl = font_ui.render(sm["desc"][:18], True, (140, 170, 200))
        row.blit(d_lbl, ((MAP_BTN_W - d_lbl.get_width()) // 2, 28))
        panel.blit(row, (bx, by))
        click_targets.append((scr_rect, f"map_{i}"))

    pygame.draw.line(panel, (40, 70, 100), (16, section_y + 84), (PANEL_W - 16, section_y + 84))

    # ? Player faction ?
    section_y += 92
    lbl = font_hud.render("YOUR FACTION", True, (140, 200, 230))
    panel.blit(lbl, (PAD, section_y))

    FACTION_COLORS_MAP = {
        "NATO":     ((30, 80, 200),   (100, 150, 255)),
        "BRICS":    ((200, 30, 30),   (255, 100, 100)),
        "GUERILLA": ((100, 110, 40),  (180, 200, 80)),
    }
    F_BTN_W = 140; F_BTN_H = 38
    for i, fid in enumerate(_FACTIONS_ALL):
        bx = PAD + i * (F_BTN_W + 10)
        by = section_y + 24
        scr_rect = pygame.Rect(px + bx, py + by, F_BTN_W, F_BTN_H)
        selected = (cfg["player"] == fid)
        hover    = scr_rect.collidepoint(mx, my)
        col_base, col_sel = FACTION_COLORS_MAP[fid]
        bg     = col_sel if selected else (col_base[0] // 3, col_base[1] // 3, col_base[2] // 3)
        border = col_sel if (selected or hover) else tuple(c // 2 for c in col_sel)
        row = pygame.Surface((F_BTN_W, F_BTN_H), pygame.SRCALPHA)
        row.fill((*bg, 220))
        pygame.draw.rect(row, border, (0, 0, F_BTN_W, F_BTN_H), 2)
        n_lbl = font_hud.render(fid, True, (240, 245, 255) if selected else (160, 180, 220))
        row.blit(n_lbl, ((F_BTN_W - n_lbl.get_width()) // 2, (F_BTN_H - n_lbl.get_height()) // 2))
        panel.blit(row, (bx, by))
        click_targets.append((scr_rect, f"player_{fid}"))

    pygame.draw.line(panel, (40, 70, 100), (16, section_y + 72), (PANEL_W - 16, section_y + 72))

    # ? AI opponents ?
    section_y += 80
    lbl = font_hud.render("AI OPPONENTS  (1 or 2)", True, (140, 200, 230))
    panel.blit(lbl, (PAD, section_y))

    ai_factions = cfg["ai_factions"]
    for i, fid in enumerate(_FACTIONS_ALL):
        if fid == cfg["player"]:
            continue   # can't pick own faction as AI
        bx = PAD + i * (F_BTN_W + 10)
        by = section_y + 24
        scr_rect = pygame.Rect(px + bx, py + by, F_BTN_W, F_BTN_H)
        checked  = fid in ai_factions
        hover    = scr_rect.collidepoint(mx, my)
        col_base, col_sel = FACTION_COLORS_MAP[fid]
        bg     = col_sel if checked else (col_base[0] // 3, col_base[1] // 3, col_base[2] // 3)
        border = col_sel if (checked or hover) else tuple(c // 2 for c in col_sel)
        row = pygame.Surface((F_BTN_W, F_BTN_H), pygame.SRCALPHA)
        row.fill((*bg, 220))
        pygame.draw.rect(row, border, (0, 0, F_BTN_W, F_BTN_H), 2)
        n_lbl = font_hud.render(fid, True, (240, 245, 255) if checked else (130, 150, 190))
        row.blit(n_lbl, ((F_BTN_W - n_lbl.get_width()) // 2, (F_BTN_H - n_lbl.get_height()) // 2))
        panel.blit(row, (bx, by))
        click_targets.append((scr_rect, f"ai_toggle_{fid}"))

    pygame.draw.line(panel, (40, 70, 100), (16, section_y + 72), (PANEL_W - 16, section_y + 72))

    # ? Victory conditions ?
    section_y += 80
    lbl = font_hud.render("VICTORY CONDITIONS", True, (140, 200, 230))
    panel.blit(lbl, (PAD, section_y))

    VTYPE_SPEC = [
        ("destroy_hq",  "Destroy HQ"),
        ("hold_cities", "Hold Cities"),
        ("capture_oil", "Capture Oil"),
    ]
    V_BTN_W = 170; V_BTN_H = 36
    for i, (vtype, vlabel) in enumerate(VTYPE_SPEC):
        bx = PAD + i * (V_BTN_W + 12)
        by = section_y + 24
        scr_rect = pygame.Rect(px + bx, py + by, V_BTN_W, V_BTN_H)
        checked  = vtype in cfg["victory_types"]
        hover    = scr_rect.collidepoint(mx, my)
        if checked:
            bg = (50, 100, 50); border = (100, 200, 110)
        elif hover:
            bg = (30,  55, 30); border = ( 60, 120,  70)
        else:
            bg = (22,  36, 22); border = ( 40,  56,  40)
        row = pygame.Surface((V_BTN_W, V_BTN_H), pygame.SRCALPHA)
        row.fill((*bg, 220))
        pygame.draw.rect(row, border, (0, 0, V_BTN_W, V_BTN_H), 1)
        prefix = "? " if checked else "  "
        n_lbl  = font_ui.render(prefix + vlabel, True,
                                (200, 255, 200) if checked else (120, 160, 130))
        row.blit(n_lbl, (8, (V_BTN_H - n_lbl.get_height()) // 2))
        panel.blit(row, (bx, by))
        click_targets.append((scr_rect, f"vtype_{vtype}"))

    pygame.draw.line(panel, (40, 70, 100), (16, section_y + 72), (PANEL_W - 16, section_y + 72))

    # ? Difficulty ?
    section_y += 80
    lbl = font_hud.render("DIFFICULTY", True, (140, 200, 230))
    panel.blit(lbl, (PAD, section_y))

    SK_DIFF = [
        ("normal", "NORMAL", (35, 110, 55), (80, 200, 120), (220, 255, 220)),
        ("hard",   "HARD",   (120, 50, 30), (220, 100, 60), (255, 200, 160)),
        ("insane", "INSANE", (130, 30, 130), (220, 100, 220), (255, 180, 255)),
    ]
    SKDBTN_W = 130
    for di, (diff_id, dlabel, sel_bg, sel_bd, sel_tc) in enumerate(SK_DIFF):
        bx = PAD + di * (SKDBTN_W + 10)
        by = section_y + 24
        scr_rect = pygame.Rect(px + bx, py + by, SKDBTN_W, 36)
        is_sel   = (cfg["difficulty"] == diff_id)
        hover    = scr_rect.collidepoint(mx, my)
        if is_sel:
            bg, bc = sel_bg, sel_bd
        elif hover:
            bg = tuple(c // 2 for c in sel_bg)
            bc = tuple(c // 2 for c in sel_bd)
        else:
            bg = (20, 28, 36); bc = (44, 60, 60)
        tc = sel_tc if (is_sel or hover) else (130, 140, 150)
        row = pygame.Surface((SKDBTN_W, 36), pygame.SRCALPHA)
        row.fill((*bg, 215))
        pygame.draw.rect(row, bc, (0, 0, SKDBTN_W, 36), 1)
        dl = font_hud.render(dlabel, True, tc)
        row.blit(dl, ((SKDBTN_W - dl.get_width()) // 2, (36 - dl.get_height()) // 2))
        panel.blit(row, (bx, by))
        click_targets.append((scr_rect, f"diff_{diff_id}"))

    # ? START / BACK ?
    BTN_W2, BTN_H2 = 160, 48
    bottom_y = PANEL_H - BTN_H2 - 16
    for bx, label, action, col_b, col_h in [
        (PAD,                          "START", "start", (40, 100, 40), (80, 200, 80)),
        (PANEL_W - PAD - BTN_W2,       "BACK",  "back",  (60, 40, 40), (200, 100, 100)),
    ]:
        scr_rect = pygame.Rect(px + bx, py + bottom_y, BTN_W2, BTN_H2)
        hover    = scr_rect.collidepoint(mx, my)
        bg       = col_h if hover else col_b
        row      = pygame.Surface((BTN_W2, BTN_H2), pygame.SRCALPHA)
        row.fill((*bg, 230))
        pygame.draw.rect(row, col_h, (0, 0, BTN_W2, BTN_H2), 2)
        n_lbl = font_big.render(label, True, (240, 250, 255))
        row.blit(n_lbl, ((BTN_W2 - n_lbl.get_width()) // 2, (BTN_H2 - n_lbl.get_height()) // 2))
        panel.blit(row, (bx, bottom_y))
        click_targets.append((scr_rect, action))

    surface.blit(panel, (px, py))
    return click_targets


# ---------------------------------------------------------------------------
# Drawing helpers -- in-game UI
# ---------------------------------------------------------------------------

def _draw_end_turn_button(
    surface: pygame.Surface,
    font_hud: pygame.font.Font,
) -> pygame.Rect:
    """Draw the END TURN button; return its Rect for click detection."""
    BTN_W, BTN_H = 196, 44
    mx, my = pygame.mouse.get_pos()
    sw, sh = surface.get_size()
    btn = pygame.Rect(sw - BTN_W - 14, sh - BTN_H - 36, BTN_W, BTN_H)
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

    display_units = all_displayable_units(faction.id)
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
            f"Unlock Tier {faction.tier + 1}  --  {cost} cr", True, tc
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
# Action buttons (Defend / Retreat) -- shown under the selected-unit panel
# ---------------------------------------------------------------------------

def _draw_action_buttons(
    surface: pygame.Surface,
    font: pygame.font.Font,
    unit: Unit,
    y: int,
) -> list[tuple[pygame.Rect, str]]:
    """Render Defend + Retreat buttons; return (rect, action_id) for clicks."""
    BTN_W, BTN_H, GAP = 110, 28, 6
    x = 12
    out: list[tuple[pygame.Rect, str]] = []
    mx, my = pygame.mouse.get_pos()

    # Defend (or Hunkered indicator)
    is_def = unit.stance == STANCE_DEFEND
    def_label = "Hunkered" if is_def else "[H] Hunker"
    def_rect = pygame.Rect(x, y, BTN_W, BTN_H)
    hover = def_rect.collidepoint(mx, my) and not is_def
    if is_def:
        bg, fg, border = (60, 110, 60), (220, 255, 220), (140, 230, 140)
    else:
        bg = (60, 90, 50) if hover else (40, 60, 35)
        fg = (220, 240, 200)
        border = (160, 220, 140) if hover else (110, 160, 90)
    pygame.draw.rect(surface, bg, def_rect)
    pygame.draw.rect(surface, border, def_rect, 2)
    lbl = font.render(def_label, True, fg)
    surface.blit(lbl, (def_rect.x + (BTN_W - lbl.get_width()) // 2,
                       def_rect.y + (BTN_H - lbl.get_height()) // 2))
    if not is_def:
        out.append((def_rect, "defend"))

    # Retreat
    x += BTN_W + GAP
    ret_rect = pygame.Rect(x, y, BTN_W, BTN_H)
    can_retreat = not unit.has_moved
    hover = ret_rect.collidepoint(mx, my) and can_retreat
    if not can_retreat:
        bg, fg, border = (40, 35, 35), (110, 110, 110), (80, 70, 70)
    else:
        bg = (110, 70, 30) if hover else (70, 45, 20)
        fg = (255, 230, 200)
        border = (230, 170, 80) if hover else (170, 110, 50)
    pygame.draw.rect(surface, bg, ret_rect)
    pygame.draw.rect(surface, border, ret_rect, 2)
    lbl = font.render("[R] Retreat", True, fg)
    surface.blit(lbl, (ret_rect.x + (BTN_W - lbl.get_width()) // 2,
                       ret_rect.y + (BTN_H - lbl.get_height()) // 2))
    if can_retreat:
        out.append((ret_rect, "retreat"))

    return out


# ---------------------------------------------------------------------------
# End-turn confirmation modal (units still actionable)
# ---------------------------------------------------------------------------

def _draw_end_turn_confirm(
    surface: pygame.Surface,
    font_ui: pygame.font.Font,
    font_hud: pygame.font.Font,
    n_actionable: int,
) -> list[tuple[pygame.Rect, str]]:
    """Render the 'units left to act' modal; return button rects."""
    sw, sh = surface.get_size()
    # Dim background
    dim = pygame.Surface((sw, sh), pygame.SRCALPHA)
    dim.fill((0, 0, 0, 160))
    surface.blit(dim, (0, 0))

    # Panel
    PW, PH = 480, 200
    px, py = (sw - PW) // 2, (sh - PH) // 2
    panel = pygame.Surface((PW, PH), pygame.SRCALPHA)
    panel.fill((28, 36, 56, 245))
    pygame.draw.rect(panel, (230, 200, 120), panel.get_rect(), 2)

    title = font_hud.render("END TURN?", True, (255, 240, 200))
    panel.blit(title, ((PW - title.get_width()) // 2, 22))

    msg = font_ui.render(
        f"You still have {n_actionable} unit{'s' if n_actionable != 1 else ''}"
        f" that can act.",
        True, (220, 220, 220),
    )
    panel.blit(msg, ((PW - msg.get_width()) // 2, 64))

    sub = font_ui.render(
        "Press Y / Enter to end turn, N / Esc to cancel.",
        True, (160, 160, 180),
    )
    panel.blit(sub, ((PW - sub.get_width()) // 2, 92))

    # Buttons
    BTN_W, BTN_H = 180, 40
    mx, my = pygame.mouse.get_pos()

    end_rect = pygame.Rect(px + 30, py + 140, BTN_W, BTN_H)
    keep_rect = pygame.Rect(px + PW - BTN_W - 30, py + 140, BTN_W, BTN_H)

    # Render to main surface (after blitting panel)
    surface.blit(panel, (px, py))

    out: list[tuple[pygame.Rect, str]] = []
    for label, rect, action, base, hover_col in [
        ("End Turn",  end_rect,  "confirm", (90, 50, 50), (140, 70, 70)),
        ("Keep Playing", keep_rect, "cancel", (50, 80, 110), (80, 120, 160)),
    ]:
        hover = rect.collidepoint(mx, my)
        pygame.draw.rect(surface, hover_col if hover else base, rect)
        pygame.draw.rect(surface, (220, 220, 220), rect, 2)
        lbl = font_ui.render(label, True, (240, 240, 240))
        surface.blit(lbl, (
            rect.x + (BTN_W - lbl.get_width()) // 2,
            rect.y + (BTN_H - lbl.get_height()) // 2,
        ))
        out.append((rect, action))
    return out


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def main() -> None:  # noqa: C901  (complexity expected in a game loop)
    pygame.init()
    pygame.font.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
    pygame.display.set_caption("Saladin's Playground")
    is_fullscreen = False
    windowed_size = (WIDTH, HEIGHT)
    clock    = pygame.time.Clock()
    font_big = pygame.font.SysFont("consolas", 40, bold=True)
    font_hud = pygame.font.SysFont("consolas", 22, bold=True)
    font_ui  = pygame.font.SysFont("consolas", 18)

    # ? Persistent objects ?
    camera   = Camera(WIDTH, HEIGHT, hex_size=36, offset_x=60.0, offset_y=60.0)
    renderer = HexRenderer(camera)
    audio    = SoundManager()

    # Globals that get rebound inside the loop (mission / slug selection).
    global _current_save_slot, _selected_scenario, _SCENARIO_SLUG, _skirmish_cfg

    # ? Screen state machine ?
    # "main_menu" | "mission_select" | "pre_match" | "skirmish_config"
    # | "load_menu" | "playing"
    screen_state: str = "main_menu"
    pre_match: dict[str, str] = {"difficulty": "normal", "faction": "NATO"}

    # Menu click targets (rebuilt each render frame)
    menu_btns:      list[tuple[pygame.Rect, str]] = []
    mission_btns:   list[tuple[pygame.Rect, str]] = []
    pm_btns:        list[tuple[pygame.Rect, str]] = []
    load_btns:      list[tuple[pygame.Rect, str]] = []
    skirmish_btns:  list[tuple[pygame.Rect, str]] = []

    # ? Playing state ?
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
    floater_layer:      FloaterLayer                  = FloaterLayer()
    # CP-32: recent AI actions shown as faded arrows after the AI turn ends.
    recent_ai_actions:  collections.deque             = collections.deque(maxlen=5)
    # CP-33: hex pinned by right-click inspect; cleared on left-click.
    pinned_tooltip_hex: Optional[Hex]                 = None
    # CP-34: volume cycle flash (same pattern as save_flash).
    vol_flash:          float                         = 0.0
    vol_flash_msg:      str                           = ""
    confirm_end_turn:   bool                          = False  # show "units left to act" modal
    confirm_buttons:    list[tuple[pygame.Rect, str]] = []
    action_buttons:     list[tuple[pygame.Rect, str]] = []  # Defend/Retreat panel buttons

    def _reset_playing() -> None:
        nonlocal hovered, selected_unit, movement, path_preview
        nonlocal attack_target_uids, build_hq, bm_panel_rect, bm_items
        nonlocal go_buttons, et_btn_rect, ai_steps, ai_timer, save_flash
        hovered = selected_unit = movement = build_hq = None
        path_preview = []; attack_target_uids = set()
        bm_panel_rect = None; bm_items = []; go_buttons = []
        et_btn_rect = None; ai_steps = None; ai_timer = 0.0; save_flash = 0.0
        floater_layer.clear()
        recent_ai_actions.clear()
        nonlocal pinned_tooltip_hex
        pinned_tooltip_hex = None

    def _start_playing(new_state: GameState) -> None:
        nonlocal state, screen_state
        state = new_state
        _reset_playing()
        camera.offset_x = 60.0
        camera.offset_y = 60.0
        screen_state = "playing"
        # Start faction music for whoever goes first.
        audio.play_music(state.active_faction.id)
        # If it's the AI's turn from the start of a loaded save, kick it off.
        if state.active_faction.is_ai and not state.game_over:
            _kick_ai()

    def _kick_ai() -> None:
        nonlocal ai_steps, ai_timer
        assert state is not None
        _pers = _personality_for(state.active_faction.id)
        ai_steps = take_turn_steps(state, state.active_faction.id, _pers)
        ai_timer = 0.0

    def _end_turn_action(force: bool = False) -> None:
        """Shared logic for SPACE / E / end-turn button.

        If *force* is False (default) and the human still has units that can
        act, open the confirmation modal instead of advancing immediately."""
        nonlocal ai_steps, ai_timer, confirm_end_turn
        assert state is not None
        nonlocal selected_unit, movement, path_preview, attack_target_uids, build_hq
        if (not force
                and not state.active_faction.is_ai
                and not state.game_over
                and actionable_units(state, state.active_faction.id)):
            confirm_end_turn = True
            return
        confirm_end_turn = False
        build_hq = selected_unit = movement = None
        path_preview = []; attack_target_uids = set()
        audio.play_sfx("end_turn")
        state.end_turn()
        _do_autosave(state, screen)
        # Check win/lose BEFORE playing music for the new faction.
        if state.game_over:
            outcome = state.outcomes.get(HUMAN_FACTION)
            from src.engine.victory import Outcome as _Outcome
            if outcome == _Outcome.WON:
                audio.play_sfx("win")
                audio.stop_music()
            elif outcome == _Outcome.LOST:
                audio.play_sfx("lose")
                audio.stop_music()
        else:
            audio.play_music(state.active_faction.id)
        print(f"End turn -> {state.active_faction.id} "
              f"(turn {state.turn_number}, "
              f"credits={state.active_faction.credits}, "
              f"oil={state.active_faction.oil})")
        if state.active_faction.is_ai and not state.game_over:
            _kick_ai()
        else:
            # Player's turn starts -- clear the AI action trace (CP-32)
            recent_ai_actions.clear()

    # --- Unit orders (Defend / Retreat) ---
    def _order_defend() -> None:
        """Toggle defend stance on the selected own unit."""
        nonlocal selected_unit, movement, path_preview, attack_target_uids
        assert state is not None
        if selected_unit is None or selected_unit.faction != state.active_faction.id:
            return
        if state.active_faction.is_ai:
            return
        if selected_unit.stance == STANCE_DEFEND:
            # Already defending -- can't undo (action already spent).
            return
        if not selected_unit.is_alive():
            return
        set_defend(state, selected_unit)
        movement = None; path_preview = []; attack_target_uids = set()
        print(f"[order] {selected_unit.unit_type.name} hunkering down (+2 DEF)")

    def _order_medic() -> None:
        """Engineer-class active heal: heal the most wounded adjacent friendly."""
        nonlocal selected_unit, movement, path_preview, attack_target_uids
        assert state is not None
        if selected_unit is None or selected_unit.faction != state.active_faction.id:
            return
        if state.active_faction.is_ai:
            return
        patients = adjacent_friendly_patients(state, selected_unit)
        if not patients:
            print(f"[medic] {selected_unit.unit_type.name}: no adjacent wounded ally")
            return
        # Heal the one with lowest HP first.
        patient = min(patients, key=lambda u: u.hp)
        _patient_hex    = patient.hex
        _pre_patient_lvl = patient.level
        healed = medic_heal(state, selected_unit, patient)
        audio.play_sfx("capture")  # cheerful sfx; lacks a dedicated heal sound
        print(f"[medic] {selected_unit.unit_type.name} healed "
              f"{patient.unit_type.name} for +{healed} HP")

        # Floating heal number (CP-29)
        if healed > 0:
            floater_layer.push_at_hex(
                f"+{healed}", (80, 230, 100), _patient_hex, camera,
            )
        # Level-up from capture XP (CP-30)
        if patient.level > _pre_patient_lvl:
            floater_layer.push_at_hex(
                "LEVEL UP!", (255, 220, 50),
                _patient_hex, camera,
                duration=LEVELUP_DURATION, y_offset=-16,
            )
            audio.play_sfx("levelup")

        movement = None; path_preview = []; attack_target_uids = set()

    def _order_retreat() -> None:
        nonlocal selected_unit, movement, path_preview, attack_target_uids
        assert state is not None
        if selected_unit is None or selected_unit.faction != state.active_faction.id:
            return
        if state.active_faction.is_ai or selected_unit.has_moved:
            return
        dest = do_retreat(state, selected_unit)
        if dest is not None:
            audio.play_sfx("move")
            print(f"[order] {selected_unit.unit_type.name} retreating -> {dest}")
        else:
            print(f"[order] {selected_unit.unit_type.name} cannot retreat (no path).")
        movement = None; path_preview = []; attack_target_uids = set()

    # ? Main loop ?
    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0

        # ? Events ?
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                continue

            # Focus change -- pause music on tab-out to prevent glitches.
            if event.type == pygame.ACTIVEEVENT:
                if hasattr(event, "gain"):
                    audio.handle_focus_change(bool(event.gain))
                continue

            # Window resize -- update screen + camera viewport.
            if event.type == pygame.VIDEORESIZE:
                new_w = max(MIN_WIDTH,  event.w)
                new_h = max(MIN_HEIGHT, event.h)
                screen = pygame.display.set_mode(
                    (new_w, new_h),
                    (pygame.FULLSCREEN if is_fullscreen else pygame.RESIZABLE),
                )
                camera.screen_w = new_w
                camera.screen_h = new_h
                if not is_fullscreen:
                    windowed_size = (new_w, new_h)
                continue

            # F11 -- toggle fullscreen at native desktop resolution.
            if event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
                is_fullscreen = not is_fullscreen
                if is_fullscreen:
                    info = pygame.display.Info()
                    target = (info.current_w, info.current_h)
                    screen = pygame.display.set_mode(target, pygame.FULLSCREEN)
                else:
                    screen = pygame.display.set_mode(
                        windowed_size, pygame.RESIZABLE
                    )
                camera.screen_w, camera.screen_h = screen.get_size()
                continue

            # ? MAIN MENU ?
            if screen_state == "main_menu":
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = pygame.mouse.get_pos()
                    for rect, action in menu_btns:
                        if rect.collidepoint(mx, my):
                            if action == "campaign":
                                screen_state = "mission_select"
                            elif action == "skirmish":
                                screen_state = "skirmish_config"
                            elif action == "load":
                                screen_state = "load_menu"
                            elif action == "quit":
                                running = False
                            break
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False

            # ? MISSION SELECT ?
            elif screen_state == "mission_select":
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = pygame.mouse.get_pos()
                    for rect, action in mission_btns:
                        if rect.collidepoint(mx, my):
                            if action == "back":
                                screen_state = "main_menu"
                            elif action.startswith("mission_"):
                                idx = int(action[8:])
                                _selected_scenario = SCENARIOS[idx]
                                _SCENARIO_SLUG = _selected_scenario["slug"]
                                _scenario_meta["name"] = _selected_scenario["name"]
                                screen_state = "pre_match"
                            break
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    screen_state = "main_menu"

            # ? PRE-MATCH CONFIG ?
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
                                screen_state = "mission_select"
                            elif action.startswith("diff_"):
                                pre_match["difficulty"] = action[5:]
                            elif action.startswith("faction_"):
                                pre_match["faction"] = action[8:]
                            break
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    screen_state = "mission_select"

            # ? LOAD MENU ?
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

            # ? SKIRMISH CONFIG ?
            elif screen_state == "skirmish_config":
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = pygame.mouse.get_pos()
                    for rect, action in skirmish_btns:
                        if rect.collidepoint(mx, my):
                            if action == "back":
                                screen_state = "main_menu"
                            elif action == "start":
                                if _skirmish_cfg["ai_factions"]:  # must have ?1 AI
                                    sk_state, _slug = _start_skirmish()
                                    _start_playing(sk_state)
                            elif action.startswith("map_"):
                                _skirmish_cfg["map_idx"] = int(action[4:])
                            elif action.startswith("player_"):
                                new_player = action[7:]
                                _skirmish_cfg["player"] = new_player
                                # Remove new player from AI list if present
                                _skirmish_cfg["ai_factions"] = [
                                    f for f in _skirmish_cfg["ai_factions"]
                                    if f != new_player
                                ]
                                # Ensure at least one AI opponent remains
                                if not _skirmish_cfg["ai_factions"]:
                                    others = [f for f in _FACTIONS_ALL if f != new_player]
                                    _skirmish_cfg["ai_factions"] = [others[0]]
                            elif action.startswith("ai_toggle_"):
                                fid = action[10:]
                                if fid == _skirmish_cfg["player"]:
                                    pass  # can't toggle own faction
                                elif fid in _skirmish_cfg["ai_factions"]:
                                    # Only remove if another AI remains
                                    if len(_skirmish_cfg["ai_factions"]) > 1:
                                        _skirmish_cfg["ai_factions"].remove(fid)
                                else:
                                    # Add if we don't already have 2 AIs
                                    if len(_skirmish_cfg["ai_factions"]) < 2:
                                        _skirmish_cfg["ai_factions"].append(fid)
                            elif action.startswith("vtype_"):
                                vtype = action[6:]
                                vtypes = _skirmish_cfg["victory_types"]
                                if vtype in vtypes:
                                    if len(vtypes) > 1:  # keep at least 1
                                        vtypes.remove(vtype)
                                else:
                                    vtypes.append(vtype)
                            elif action.startswith("diff_"):
                                _skirmish_cfg["difficulty"] = action[5:]
                            break
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    screen_state = "main_menu"

            # ? PLAYING ?
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

                # ? Keyboard ?
                if event.type == pygame.KEYDOWN:
                    # Confirm-modal hotkeys take priority over everything.
                    if confirm_end_turn:
                        if event.key in (pygame.K_y, pygame.K_RETURN):
                            _end_turn_action(force=True)
                        elif event.key in (pygame.K_n, pygame.K_ESCAPE):
                            confirm_end_turn = False
                        continue

                    if event.key == pygame.K_ESCAPE:
                        if build_hq is not None:
                            build_hq = None
                        elif selected_unit is not None:
                            selected_unit = None
                            movement = None
                            path_preview = []
                            attack_target_uids = set()
                        else:
                            screen_state = "main_menu"   # ESC -> menu

                    elif event.key in (pygame.K_SPACE, pygame.K_e):
                        _end_turn_action()

                    elif event.key == pygame.K_h:
                        _order_defend()       # H = hunker down (defend stance)

                    elif event.key == pygame.K_r:
                        _order_retreat()      # R = retreat toward HQ

                    elif event.key == pygame.K_j:
                        _order_medic()        # J = engineer heal adjacent friendly

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
                            # CP-35: save a 200x120 thumbnail alongside the JSON
                            try:
                                _thumb = pygame.transform.smoothscale(screen, (200, 120))
                                pygame.image.save(_thumb, str(thumbnail_path(p)))
                            except Exception:
                                pass   # thumbnail failure must never break the save
                            save_flash = 2.0
                            save_flash_msg = f"Saved slot {_current_save_slot}"
                            print(f"[save] slot {_current_save_slot} -> {p}")
                        except Exception as exc:
                            print(f"[save FAILED] {exc}")
                        _current_save_slot = (_current_save_slot % NUM_SLOTS) + 1

                    elif event.key == pygame.K_f:
                        fog_enabled = not fog_enabled
                        print(f"Fog {'on' if fog_enabled else 'off'}")

                    elif event.key == pygame.K_m:
                        # CP-34: cycle volume 100->75->50->25->0->100...
                        _VOL_STEPS = [1.0, 0.75, 0.50, 0.25, 0.0]
                        _cur = audio.master_volume
                        _ci  = min(
                            range(len(_VOL_STEPS)),
                            key=lambda i: abs(_VOL_STEPS[i] - _cur),
                        )
                        _nv = _VOL_STEPS[(_ci + 1) % len(_VOL_STEPS)]
                        audio.set_volume(_nv)
                        # Force-mute the pygame music channel at 0%
                        if _nv == 0.0 and not audio.is_muted:
                            audio.toggle_mute()
                        elif _nv > 0.0 and audio.is_muted:
                            audio.toggle_mute()
                        pct = int(_nv * 100)
                        vol_flash = 1.2
                        vol_flash_msg = f"Volume {pct}%" if pct > 0 else "Muted"
                        print(f"Volume -> {pct}%")

                # ? Mouse ?
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = pygame.mouse.get_pos()
                    pinned_tooltip_hex = None   # CP-33: any left-click clears pin

                    # Confirm modal takes priority over everything.
                    if confirm_end_turn:
                        for rect, act in confirm_buttons:
                            if rect.collidepoint(mx, my):
                                if act == "confirm":
                                    _end_turn_action(force=True)
                                else:
                                    confirm_end_turn = False
                                break
                        continue

                    # Defend / Retreat action buttons (under selected-unit panel)
                    consumed = False
                    for rect, act in action_buttons:
                        if rect.collidepoint(mx, my):
                            if act == "defend":
                                _order_defend()
                            elif act == "retreat":
                                _order_retreat()
                            consumed = True
                            break
                    if consumed:
                        continue

                    # End-turn button (highest priority among map clicks)
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
                                        print(f"Tier unlocked -> {state.active_faction.tier}")
                                    except ValueError as e:
                                        print(f"Upgrade failed: {e}")
                                else:
                                    try:
                                        u = state.build_unit(action_id, active_id, build_hq)
                                        audio.play_sfx("build")
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
                            # Snapshot pre-attack state for floater/levelup detection
                            _atk_hex      = selected_unit.hex
                            _def_hex      = clicked_unit.hex
                            _pre_atk_lvl  = selected_unit.level
                            _pre_def_lvl  = clicked_unit.level

                            result = resolve_attack(state, selected_unit, clicked_unit)
                            audio.play_sfx("attack")
                            print(
                                f"Attack: {selected_unit.unit_type.name} -> "
                                f"{clicked_unit.unit_type.name}  "
                                f"{result.damage_dealt} dmg"
                                + (" (killed)" if result.defender_killed else "")
                                + (f"  counter {result.counter_damage}"
                                   if result.counter_damage else "")
                                + (" (attacker killed)" if result.attacker_killed else "")
                            )

                            # Floating damage numbers (CP-29)
                            if result.damage_dealt:
                                floater_layer.push_at_hex(
                                    f"-{result.damage_dealt}",
                                    (255, 90, 90), _def_hex, camera,
                                )
                            if result.counter_damage:
                                floater_layer.push_at_hex(
                                    f"-{result.counter_damage}",
                                    (255, 165, 60), _atk_hex, camera,
                                )

                            # Level-up flash + SFX (CP-30) -- check both combatants
                            if (not result.attacker_killed
                                    and selected_unit.level > _pre_atk_lvl):
                                floater_layer.push_at_hex(
                                    "LEVEL UP!", (255, 220, 50),
                                    _atk_hex, camera,
                                    duration=LEVELUP_DURATION, y_offset=-16,
                                )
                                audio.play_sfx("levelup")
                            if (not result.defender_killed
                                    and clicked_unit.level > _pre_def_lvl):
                                floater_layer.push_at_hex(
                                    "LEVEL UP!", (255, 220, 50),
                                    _def_hex, camera,
                                    duration=LEVELUP_DURATION, y_offset=-16,
                                )
                                audio.play_sfx("levelup")

                            selected_unit = movement = None
                            path_preview = []; attack_target_uids = set()
                        elif movement is not None and clicked in movement.reachable:
                            state.move_unit(selected_unit.uid, clicked)
                            audio.play_sfx("move")
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
                    # CP-33: right-click inspect -- pin tooltip to the clicked hex.
                    if camera.take_right_click() and state is not None:
                        _rc_h = camera.screen_to_hex(*pygame.mouse.get_pos())
                        pinned_tooltip_hex = _rc_h

        # ? Per-frame updates (playing only) ?
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
                        recent_ai_actions.append(a)   # CP-32: record for trace
                    except StopIteration:
                        ai_steps = None
                        if not state.game_over:
                            audio.play_sfx("end_turn")
                            state.end_turn()
                            _do_autosave(state, screen)
                            if state.game_over:
                                from src.engine.victory import Outcome as _Outcome
                                o = state.outcomes.get(HUMAN_FACTION)
                                if o == _Outcome.WON:
                                    audio.play_sfx("win"); audio.stop_music()
                                elif o == _Outcome.LOST:
                                    audio.play_sfx("lose"); audio.stop_music()
                            else:
                                audio.play_music(state.active_faction.id)
                            print(f"End turn -> {state.active_faction.id} "
                                  f"(turn {state.turn_number}, "
                                  f"credits={state.active_faction.credits}, "
                                  f"oil={state.active_faction.oil})")
                            if state.active_faction.is_ai and not state.game_over:
                                _kick_ai()
                            else:
                                recent_ai_actions.clear()   # CP-32: player's turn

            # Path preview
            if selected_unit is not None and movement is not None:
                path_preview = (movement.path_to(hovered)
                                if hovered in movement.reachable else [])

            # Floater tick
            floater_layer.tick(dt)

            # Save flash countdown
            if save_flash > 0:
                save_flash = max(0.0, save_flash - dt)
            # Volume flash countdown (CP-34)
            if vol_flash > 0:
                vol_flash = max(0.0, vol_flash - dt)

        # ? Render ?
        screen.fill(BG)

        if screen_state == "main_menu":
            menu_btns = _draw_main_menu(screen, font_big, font_ui)

        elif screen_state == "mission_select":
            mission_btns = _draw_mission_select(screen, font_big, font_ui, font_hud)

        elif screen_state == "pre_match":
            pm_btns = _draw_pre_match(
                screen, font_big, font_ui, font_hud,
                pre_match, _scenario_meta.get("name", ""),
            )

        elif screen_state == "skirmish_config":
            skirmish_btns = _draw_skirmish_config(
                screen, font_big, font_ui, font_hud, _skirmish_cfg
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
                    screen, movement.reachable, path_preview, selected_unit.hex,
                    costs=movement.reachable,   # CP-31: show MP cost on each hex
                )
            if selected_unit is not None and attack_target_uids:
                target_hexes = [
                    state.units[uid].hex
                    for uid in attack_target_uids if uid in state.units
                ]
                renderer.draw_attack_overlay(screen, target_hexes, hovered_hex=hovered)
            renderer.draw_units(screen, list(state.units.values()), can_see=_can_see)

            # AI action trace arrows -- only visible after an AI turn (CP-32)
            if recent_ai_actions:
                renderer.draw_ai_trace(screen, list(recent_ai_actions), state)

            # Floating damage / heal / level-up labels (CP-29 / CP-30)
            floater_layer.draw(screen, font_ui)

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

            # End-turn confirmation modal (units still actionable)
            if confirm_end_turn and not state.game_over:
                n_actionable = len(actionable_units(state, state.active_faction.id))
                if n_actionable == 0:
                    # User picked off the last unit between key press + this frame.
                    confirm_end_turn = False
                    confirm_buttons = []
                else:
                    confirm_buttons = _draw_end_turn_confirm(
                        screen, font_ui, font_hud, n_actionable
                    )
            else:
                confirm_buttons = []

            # ? HUD top-left ?
            af = state.active_faction
            ai_suffix      = "  [AI thinking...]" if ai_steps is not None else ""
            scenario_title = _scenario_meta.get("name", "")
            hud_lines: list[tuple[str, tuple[int, int, int]]] = []
            if scenario_title:
                hud_lines.append((scenario_title, (160, 160, 200)))
            hud_lines += [
                (f"Turn {state.turn_number}  --  {af.name}{ai_suffix}", af.color),
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
            action_buttons = []
            if selected_unit is not None:
                y += 4
                stance_line = (
                    "Stance: HUNKERED (+2 DEF)" if selected_unit.stance == STANCE_DEFEND
                    else "Stance: Attack (normal)"
                )
                stance_col = ((140, 230, 140) if selected_unit.stance == STANCE_DEFEND
                              else (200, 200, 200))
                sel_lines: list[tuple[str, tuple[int, int, int]]] = [
                    (f"[ {selected_unit.unit_type.name} ]", (255, 255, 180)),
                    (f"HP {selected_unit.hp}/{selected_unit.unit_type.hp}  "
                     f"Move {selected_unit.unit_type.move}  "
                     f"Rng {selected_unit.unit_type.range_min}"
                     f"-{selected_unit.unit_type.range_max}",
                     (200, 200, 200)),
                    (stance_line, stance_col),
                ]
                if hovered is not None:
                    hu = state.unit_at(hovered)
                    if hu is not None and hu.uid in attack_target_uids:
                        atk_dmg, counter_dmg = predict_exchange(
                            state, selected_unit, hu
                        )
                        sel_lines.append((
                            f"-> {atk_dmg} dmg  *  <- {counter_dmg} counter",
                            (255, 180, 180),
                        ))
                # Engagement / ZoC indicator
                if movement is not None and movement.engaged:
                    sel_lines.append(
                        ("ENGAGED -- movement limited to 1 hex", (240, 180, 110))
                    )
                medic_avail = (
                    selected_unit.unit_type.can_capture
                    and selected_unit.faction == state.active_faction.id
                    and not selected_unit.has_attacked
                    and adjacent_friendly_patients(state, selected_unit)
                )
                medic_hint = "  *  J heal" if medic_avail else ""
                sel_lines.append(
                    (f"H hunker  *  R retreat{medic_hint}  *  Tab next  *  ESC cancel",
                     (140, 140, 110))
                )
                for txt, col in sel_lines:
                    lbl = font_ui.render(txt, True, col)
                    screen.blit(lbl, (12, y)); y += 20

                # Action buttons (only for own units that can still act).
                if (selected_unit.faction == state.active_faction.id
                        and not state.active_faction.is_ai
                        and selected_unit.can_act()):
                    action_buttons = _draw_action_buttons(
                        screen, font_ui, selected_unit, y + 4
                    )
                    y += 36

            # Actionable-units reminder (subtle, top-right).
            if (not state.active_faction.is_ai
                    and ai_steps is None
                    and not state.game_over):
                remaining = len(actionable_units(state, state.active_faction.id))
                if remaining > 0:
                    rem_col = (230, 200, 120) if remaining > 1 else (200, 200, 180)
                    rem_lbl = font_hud.render(
                        f"{remaining} unit{'s' if remaining != 1 else ''} can act",
                        True, rem_col,
                    )
                    screen.blit(rem_lbl, (screen.get_width() - rem_lbl.get_width() - 14, 10))

            sw, sh = screen.get_size()

            # Save flash
            if save_flash > 0:
                fl = font_ui.render(save_flash_msg, True, (120, 220, 120))
                screen.blit(fl, (sw - fl.get_width() - 14, sh - 60))

            # Volume flash (CP-34) -- stacked just above save flash
            if vol_flash > 0:
                vl = font_ui.render(vol_flash_msg, True, (180, 200, 255))
                screen.blit(vl, (sw - vl.get_width() - 14, sh - 80))

            # FPS + hover (bottom-left)
            fps_lbl = font_ui.render(
                f"FPS {clock.get_fps():.0f}", True, (160, 160, 160)
            )
            screen.blit(fps_lbl, (12, sh - 44))
            if hovered:
                hov_lbl = font_ui.render(
                    f"q={hovered.q}, r={hovered.r}", True, (180, 180, 100)
                )
                screen.blit(hov_lbl, (12, sh - 22))

            # Help (bottom-right) -- CP-34: show volume % instead of [MUTED]
            _vol_pct = int(audio.master_volume * 100)
            _vol_ind = (f"  [Vol:{_vol_pct}%]" if _vol_pct < 100 else "")
            if audio.is_muted:
                _vol_ind = "  [MUTED]"
            help_txt = ("E end  *  Tab next  *  H hunker  *  R retreat  "
                        f"*  F5 save  *  HQ build  *  F fog  *  M vol{_vol_ind}"
                        "  *  F11 fullscreen  *  WASD pan")
            h_lbl = font_ui.render(help_txt, True, (100, 110, 130))
            screen.blit(h_lbl, (sw - h_lbl.get_width() - 12, sh - 22))

            # Tooltip -- hover OR pinned right-click inspect (CP-33).
            # Pinned tooltip takes priority; clears on left-click.
            _tooltip_hex = (
                pinned_tooltip_hex if pinned_tooltip_hex is not None else hovered
            )
            if (
                _tooltip_hex is not None
                and build_hq is None
                and not state.game_over
            ):
                tile = state.tiles.get(_tooltip_hex)
                if tile is not None:
                    hu = state.unit_at(_tooltip_hex)
                    show_unit = (
                        hu is not None
                        and (_can_see is None or _can_see(hu))
                    )
                    if show_unit:
                        lines = unit_tooltip_lines(hu)
                    else:
                        _explored_ok = (
                            explored_set is None or _tooltip_hex in explored_set
                        )
                        lines = terrain_tooltip_lines(tile) if _explored_ok else []
                    if lines:
                        # Pinned tooltip: draw at current mouse pos with a subtle
                        # "pinned" indicator in the title line.
                        if pinned_tooltip_hex is not None and lines:
                            lines = list(lines)
                            lines[0] = (lines[0][0] + "  [pinned]", lines[0][1])
                        draw_tooltip(
                            screen, lines,
                            pygame.mouse.get_pos(),
                            font_ui, (sw, sh),
                        )

        pygame.display.flip()
        await asyncio.sleep(0)

    audio.quit()
    pygame.quit()


if __name__ == "__main__":
    asyncio.run(main())
