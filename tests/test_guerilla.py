"""
CP-18 tests — Guerilla faction roster + stealth + kamikaze (self_destruct).

Covers:
  - All 10 Guerilla unit types load and validate correctly.
  - Stealth flag is set on guerilla_scout and guerilla_drone_recon.
  - self_destruct is set on guerilla_kamikaze.
  - can_faction_see_unit() correctly hides stealth at distance > 1 and reveals
    them when an enemy is within STEALTH_DETECTION_RADIUS.
  - predict_exchange returns 0 counter for kamikaze (attacker dies before counter).
  - resolve_attack: kamikaze kills attacker on attack; counter not applied.
  - AI doesn't enumerate attacks against stealth-invisible enemies but does
    enumerate them when within stealth detection radius.
  - AI scoring of kamikaze: no suicide penalty applied.
  - load_units skips comment-only dicts (regression: Guerilla section has them).
"""
from __future__ import annotations

import pytest

from src.ai.heuristic import (
    AttackAction,
    MoveAttackAction,
    _ai_can_target,
    _score_attack,
    effective_weights,
    enumerate_actions,
)
from src.engine.combat import (
    load_damage_matrix,
    predict_exchange,
    resolve_attack,
)
from src.engine.fog import (
    STEALTH_DETECTION_RADIUS,
    can_faction_see_unit,
)
from src.engine.hex import Hex
from src.engine.state import Faction, GameState
from src.engine.tile import Tile, load_terrain
from src.engine.unit import Unit, all_units, get as get_unit, load_units

GUERILLA_IDS = {
    "guerilla_irregular", "guerilla_engineer", "guerilla_scout",
    "guerilla_technical", "guerilla_mortar",
    "guerilla_militia", "guerilla_atgm", "guerilla_manpads",
    "guerilla_drone_recon", "guerilla_kamikaze",
}


@pytest.fixture(autouse=True)
def loaded():
    load_terrain()
    load_units()
    load_damage_matrix()


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def _two_faction_state(
    nato_credits: int = 0,
    grl_credits: int = 0,
    tiles: dict[Hex, Tile] | None = None,
) -> GameState:
    """NATO (human) + GUERILLA (AI). Empty plain map by default."""
    nato = Faction(
        id="NATO", name="NATO", color=(30, 80, 200),
        credits=nato_credits, oil=20, tier=2, is_ai=False,
    )
    grl = Faction(
        id="GUERILLA", name="Guerilla", color=(130, 140, 70),
        credits=grl_credits, oil=20, tier=2, is_ai=True,
    )
    if tiles is None:
        tiles = {
            Hex(q, r): Tile(Hex(q, r), "plain")
            for q in range(-3, 6) for r in range(-3, 6)
        }
    return GameState(factions=[nato, grl], tiles=tiles)


# ---------------------------------------------------------------------------
# Guerilla roster
# ---------------------------------------------------------------------------

class TestGuerillaRoster:
    def test_all_ids_present(self):
        registry = all_units()
        for uid in GUERILLA_IDS:
            assert uid in registry, f"Missing unit type: {uid}"

    def test_count_is_ten(self):
        grl = [ut for ut in all_units().values() if ut.faction == "GUERILLA"]
        assert len(grl) == 10

    def test_all_have_correct_faction(self):
        for uid in GUERILLA_IDS:
            assert get_unit(uid).faction == "GUERILLA"

    def test_tiers_span_1_and_2(self):
        tiers = {get_unit(uid).tier for uid in GUERILLA_IDS}
        assert tiers == {1, 2}      # CP-18 doesn't add T3 Guerilla yet

    def test_t1_count(self):
        t1 = [u for u in GUERILLA_IDS if get_unit(u).tier == 1]
        assert len(t1) == 5

    def test_t2_count(self):
        t2 = [u for u in GUERILLA_IDS if get_unit(u).tier == 2]
        assert len(t2) == 5

    def test_hp_all_10(self):
        for uid in GUERILLA_IDS:
            assert get_unit(uid).hp == 10

    def test_engineer_can_capture(self):
        assert get_unit("guerilla_engineer").can_capture is True

    def test_militia_can_capture(self):
        assert get_unit("guerilla_militia").can_capture is True

    def test_irregular_cannot_capture(self):
        assert get_unit("guerilla_irregular").can_capture is False

    def test_irregular_is_cheapest_infantry(self):
        # Guerilla infantry should be cheapest of any faction's T1 infantry.
        assert get_unit("guerilla_irregular").cost_credits <= get_unit("brics_inf_l").cost_credits
        assert get_unit("guerilla_irregular").cost_credits <= get_unit("nato_inf_l").cost_credits


# ---------------------------------------------------------------------------
# Stealth flag presence
# ---------------------------------------------------------------------------

class TestStealthFlag:
    def test_scout_is_stealth(self):
        assert get_unit("guerilla_scout").stealth is True

    def test_drone_recon_is_stealth(self):
        assert get_unit("guerilla_drone_recon").stealth is True

    def test_drone_recon_is_flying(self):
        assert get_unit("guerilla_drone_recon").flying is True

    def test_irregular_is_not_stealth(self):
        assert get_unit("guerilla_irregular").stealth is False

    def test_kamikaze_is_not_stealth(self):
        # Kamikaze drones don't sneak — they're flagged for everyone to see.
        assert get_unit("guerilla_kamikaze").stealth is False


# ---------------------------------------------------------------------------
# Self-destruct flag
# ---------------------------------------------------------------------------

class TestSelfDestructFlag:
    def test_kamikaze_self_destruct(self):
        assert get_unit("guerilla_kamikaze").self_destruct is True

    def test_kamikaze_high_atk(self):
        assert get_unit("guerilla_kamikaze").atk >= 9

    def test_kamikaze_flying(self):
        assert get_unit("guerilla_kamikaze").flying is True

    def test_no_other_units_self_destruct(self):
        for ut in all_units().values():
            if ut.id == "guerilla_kamikaze":
                continue
            assert ut.self_destruct is False, f"{ut.id} should not self_destruct"


# ---------------------------------------------------------------------------
# Stealth + can_faction_see_unit
# ---------------------------------------------------------------------------

class TestStealthVisibility:
    def _state_with_scout_and_observer(self, observer_hex: Hex) -> tuple[GameState, Unit]:
        """NATO scout-observer + GUERILLA stealth scout at (3,0)."""
        state = _two_faction_state()
        scout = Unit("guerilla_scout", "GUERILLA", Hex(3, 0))
        observer = Unit("nato_recon", "NATO", observer_hex)
        state.add_unit(scout)
        state.add_unit(observer)
        return state, scout

    def test_stealth_hidden_at_distance_2(self):
        state, scout = self._state_with_scout_and_observer(Hex(1, 0))
        # distance Hex(1,0)→Hex(3,0) = 2; outside STEALTH_DETECTION_RADIUS=1
        assert can_faction_see_unit(state, "NATO", scout) is False

    def test_stealth_visible_when_adjacent(self):
        state, scout = self._state_with_scout_and_observer(Hex(2, 0))
        # distance Hex(2,0)→Hex(3,0) = 1
        assert can_faction_see_unit(state, "NATO", scout) is True

    def test_stealth_hidden_at_far_distance(self):
        state, scout = self._state_with_scout_and_observer(Hex(-3, 0))
        # distance 6 — observer must be within 1 to see stealth
        assert can_faction_see_unit(state, "NATO", scout) is False

    def test_non_stealth_visible_through_normal_fog(self):
        """Sanity: non-stealth enemy visible when in vision radius even at distance 2."""
        state = _two_faction_state()
        target = Unit("guerilla_irregular", "GUERILLA", Hex(3, 0))
        observer = Unit("nato_recon", "NATO", Hex(1, 0))   # recon has vision 4
        state.add_unit(target)
        state.add_unit(observer)
        assert can_faction_see_unit(state, "NATO", target) is True

    def test_own_stealth_always_visible_to_self(self):
        state = _two_faction_state()
        scout = Unit("guerilla_scout", "GUERILLA", Hex(3, 0))
        state.add_unit(scout)
        # No observer at all — but own faction always sees own units
        assert can_faction_see_unit(state, "GUERILLA", scout) is True

    def test_stealth_constant_is_1(self):
        # Locking the design decision down.
        assert STEALTH_DETECTION_RADIUS == 1


# ---------------------------------------------------------------------------
# Kamikaze combat resolution
# ---------------------------------------------------------------------------

class TestKamikazeCombat:
    def _kamikaze_scenario(self) -> tuple[GameState, Unit, Unit]:
        state = _two_faction_state()
        kami = Unit("guerilla_kamikaze", "GUERILLA", Hex(0, 0))
        target = Unit("nato_vehicle_m", "NATO", Hex(1, 0))   # in range 1
        state.add_unit(kami)
        state.add_unit(target)
        return state, kami, target

    def test_predict_exchange_no_counter(self):
        state, kami, target = self._kamikaze_scenario()
        atk_dmg, counter = predict_exchange(state, kami, target)
        assert atk_dmg > 0
        assert counter == 0   # attacker explodes before defender counters

    def test_resolve_attack_kills_attacker(self):
        state, kami, target = self._kamikaze_scenario()
        kami_uid = kami.uid
        result = resolve_attack(state, kami, target)
        assert result.attacker_killed is True
        assert kami_uid not in state.units    # removed from state

    def test_resolve_attack_no_counter_damage(self):
        state, kami, target = self._kamikaze_scenario()
        result = resolve_attack(state, kami, target)
        assert result.counter_damage == 0

    def test_resolve_attack_damages_target(self):
        state, kami, target = self._kamikaze_scenario()
        target_hp_before = target.hp
        result = resolve_attack(state, kami, target)
        # damage actually applied; either target alive with hp reduced or killed
        if not result.defender_killed:
            assert target.hp < target_hp_before
        else:
            assert target.uid not in state.units

    def test_non_kamikaze_does_not_self_destruct(self):
        """Sanity: regular attacker survives if defender can't counter."""
        state = _two_faction_state()
        atk = Unit("guerilla_irregular", "GUERILLA", Hex(0, 0))
        # Use artillery that can't counter at range 1 (range_min=2 for mortar)
        defender = Unit("guerilla_mortar", "NATO", Hex(1, 0))
        state.add_unit(atk)
        state.add_unit(defender)
        result = resolve_attack(state, atk, defender)
        assert result.attacker_killed is False
        assert atk.uid in state.units


# ---------------------------------------------------------------------------
# AI + stealth interaction
# ---------------------------------------------------------------------------

class TestAIStealthHandling:
    def test_ai_cannot_target_invisible_stealth(self):
        """AI inf adjacent to stealth scout: can target (within detection radius)."""
        state = _two_faction_state()
        # Guerilla scout at (3,0); NATO infantry at (5,0) — distance 2
        scout = Unit("guerilla_scout", "GUERILLA", Hex(3, 0))
        nato_inf = Unit("nato_inf_l", "NATO", Hex(5, 0))
        state.add_unit(scout)
        state.add_unit(nato_inf)
        assert _ai_can_target(state, "NATO", scout) is False

    def test_ai_can_target_stealth_when_adjacent(self):
        state = _two_faction_state()
        scout = Unit("guerilla_scout", "GUERILLA", Hex(3, 0))
        nato_inf = Unit("nato_inf_l", "NATO", Hex(2, 0))   # adjacent
        state.add_unit(scout)
        state.add_unit(nato_inf)
        assert _ai_can_target(state, "NATO", scout) is True

    def test_ai_always_targets_non_stealth(self):
        state = _two_faction_state()
        target = Unit("guerilla_irregular", "GUERILLA", Hex(3, 0))
        nato_inf = Unit("nato_inf_l", "NATO", Hex(5, 0))   # distance 2
        state.add_unit(target)
        state.add_unit(nato_inf)
        # No NATO unit nearby, but AI is fog-blind for non-stealth
        assert _ai_can_target(state, "NATO", target) is True

    def test_ai_enumerate_skips_invisible_stealth(self):
        """AI's enumerate_actions doesn't list attacks against far stealth units."""
        state = _two_faction_state()
        scout = Unit("guerilla_scout", "GUERILLA", Hex(3, 0))
        # Place NATO recon adjacent to scout with range 1
        nato_recon = Unit("nato_recon", "NATO", Hex(0, 0))
        state.add_unit(scout)
        state.add_unit(nato_recon)
        # NATO is the actor; scout is at distance 3 — won't be in attack range
        # of a stationary inf, but we test that no move-then-attack action exists
        # whose target is the stealth scout from a hex that isn't adjacent.
        actions = enumerate_actions(state, "NATO")
        bad = [
            a for a in actions
            if isinstance(a, MoveAttackAction)
            and a.target_uid == scout.uid
            and a.dest != Hex(3, 0)              # not actually adjacent post-move
            # check: from dest, scout must be visible (within STEALTH_DETECTION_RADIUS)
            # — but the AI uses its own units (post-move) to determine visibility
        ]
        # The recon's only adjacency to scout is via (2,0) — moving there
        # makes the recon adjacent so targeting becomes legal.
        # Any MoveAttack at a hex where no own unit is within 1 of scout is a bug.
        for a in bad:
            adj_to_scout = any(
                # after the move, the actor sits at a.dest; other own units stay put
                (own_uid == nato_recon.uid and abs(a.dest.q - 3) + abs(a.dest.r) <= 1)
                for own_uid in (nato_recon.uid,)
            )
            # Actually the simpler invariant is: a.dest must be within
            # STEALTH_DETECTION_RADIUS of scout.hex.
            from src.engine.hex import distance as _d
            assert _d(a.dest, Hex(3, 0)) <= STEALTH_DETECTION_RADIUS, (
                f"AI enumerated attack on stealth scout from non-adjacent {a.dest}"
            )

    def test_ai_enumerate_does_target_non_stealth(self):
        state = _two_faction_state()
        target = Unit("guerilla_irregular", "GUERILLA", Hex(3, 0))
        nato_recon = Unit("nato_recon", "NATO", Hex(2, 0))   # adjacent
        state.add_unit(target)
        state.add_unit(nato_recon)
        actions = enumerate_actions(state, "NATO")
        attack_actions = [
            a for a in actions
            if isinstance(a, AttackAction) and a.defender_uid == target.uid
        ]
        assert attack_actions, "AI should target visible non-stealth enemy"


# ---------------------------------------------------------------------------
# AI scoring — kamikaze does not get suicide penalty
# ---------------------------------------------------------------------------

class TestAIKamikazeScoring:
    def test_kamikaze_scores_positive_against_target(self):
        state = _two_faction_state()
        kami = Unit("guerilla_kamikaze", "GUERILLA", Hex(0, 0))
        target = Unit("nato_vehicle_m", "NATO", Hex(1, 0))
        state.add_unit(kami)
        state.add_unit(target)
        weights = effective_weights()
        score = _score_attack(state, kami, target, weights)
        assert score > 0, "kamikaze attack should score positive (no suicide penalty)"

    def test_kamikaze_score_excludes_suicide_penalty(self):
        """A non-kamikaze attacker with same atk would lose suicide penalty;
        kamikaze should NOT have that penalty even though it always 'dies'."""
        state = _two_faction_state()
        kami = Unit("guerilla_kamikaze", "GUERILLA", Hex(0, 0))
        # Use a tough target that won't die in one hit, ensuring counter would normally kill kami.
        target = Unit("nato_vehicle_m", "NATO", Hex(1, 0))
        state.add_unit(kami)
        state.add_unit(target)
        weights = effective_weights()
        score = _score_attack(state, kami, target, weights)
        # If suicide penalty were applied, score would be ≤ 0 in many configs.
        # We assert the score is at least attack_damage * 1 (positive).
        atk_dmg, _ = predict_exchange(state, kami, target)
        expected_min = atk_dmg * weights["attack_damage"]
        # Allow for kill bonus too; score must be ≥ atk_dmg-only contribution.
        assert score >= expected_min - 0.01
