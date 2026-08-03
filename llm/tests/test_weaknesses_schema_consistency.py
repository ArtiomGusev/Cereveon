"""
Bidirectional schema pin for the post-game weaknesses dict.

Context
-------
PR #171 (2026-05-16) closed a silent contract drift between two
parts of the SECA pipeline:

  PRODUCER: ``compute_accuracy_from_pgn`` writes the player's weakness
            vector into ``event.weaknesses_json`` after the game ends.
            Pre-PR-#171 it used SEVERITY keys:
                {"blunders": 0.2, "mistakes": 0.1, "inaccuracies": 0.0}

  CONSUMER: ``aggregate_from_weakness_dicts`` reads the vector back to
            compute ``MistakeStats.dominant_category`` for the
            curriculum recommender and the PostGameCoachController's
            Rule 5.  It expects PHASE keys:
                {"opening": 0.12, "middlegame": 0.05, "endgame": 0.00}
            ... mapped through ``_PHASE_TO_CATEGORIES`` to category
            scores.  Severity keys silently produced empty
            ``phase_rates`` -> empty ``category_scores`` ->
            ``dominant_category=None`` for every player.

Effect: the multi-game probe on 2026-05-16 showed dominant_category
stuck at None after 5 hand-shaped games covering tactics-heavy /
endgame-heavy / draw / win profiles.  Curriculum stuck on the
fallback "mistakes" topic and PostGameCoachController fell through
every rule to "No trigger" because Rule 5 needs recent_weaknesses
populated.

This file pins the producer-consumer contract in BOTH directions so
neither side can drift without immediately breaking CI.

Pinned invariants
-----------------
 1. PRODUCER_KEYS_ARE_PHASE_SUBSET
        ``compute_accuracy_from_pgn`` -> ``AccuracyAnalysis.weaknesses``
        keys are a subset of {"opening", "middlegame", "endgame"}
        across a representative spread of game shapes.

 2. PRODUCER_VALUES_ARE_RATES_IN_UNIT_INTERVAL
        Every value in the dict is in [0.0, 1.0] (rate over total
        player moves).

 3. CONSUMER_ROUNDTRIP_PRODUCES_DOMINANT_CATEGORY
        Producer output -> JSON -> ``aggregate_from_weakness_dicts``
        produces a non-None ``dominant_category`` for a game that
        had at least one player mistake or blunder.

 4. PHASE_CLASSIFIER_MATCHES_HISTORICAL_PRODUCER
        ``pgn_accuracy._phase`` agrees with
        ``analysis.analyzer.GameWeaknessAnalyzer._phase`` on the
        piece-count thresholds.  Both producers must agree or
        downstream aggregation drifts again.

 5. EMPTY_GAME_DOES_NOT_LEAK_KEYS
        A clean game with no player mistakes (or zero moves
        analysed) emits an empty dict, not a dict of zero-rate keys.
        Keeps event.weaknesses_json compact.
"""

from __future__ import annotations

import json

import chess

from llm.seca.analysis.analyzer import GameWeaknessAnalyzer
from llm.seca.analysis.pgn_accuracy import (
    AccuracyAnalysis,
    _phase as _pgn_accuracy_phase,
    _summarise,
)
from llm.seca.analytics.mistake_stats import aggregate_from_weakness_dicts


# ---------------------------------------------------------------------------
# 1 · Producer keys are phase-subset
# ---------------------------------------------------------------------------


def _analysis_from_losses(
    losses_cp: list[int],
    phase_losses_cp: dict[str, list[int]],
) -> AccuracyAnalysis:
    """Call ``_summarise`` directly with hand-shaped loss buckets so
    the test doesn't depend on a live Stockfish pool."""
    return _summarise(
        losses_cp,
        phase_losses_cp,
        player_color=chess.WHITE,
        # The eval series only populate AccuracyAnalysis's telemetry fields
        # (player_pov_eval_*, white_pov_eval_per_position_cp) — they do NOT
        # feed the weakness-rate math these tests pin, so empty series keep
        # the helper focused on the hand-shaped loss buckets it builds.
        player_pov_eval_before_cp=[],
        player_pov_eval_after_cp=[],
        position_evals_cp=[],
    )


_PHASE_LABELS: tuple[str, ...] = ("opening", "middlegame", "endgame")


def test_PRODUCER_KEYS_ARE_PHASE_SUBSET_tactics_heavy():
    """A game with multiple opening-phase blunders emits at least the
    "opening" key — and no severity-shaped keys."""
    analysis = _analysis_from_losses(
        losses_cp=[400, 350, 200, 50, 30],
        phase_losses_cp={
            "opening": [400, 350, 200],  # 3 significant errors in opening
            "middlegame": [50, 30],       # only inaccuracy + ok, no mistakes
            "endgame": [],
        },
    )
    assert set(analysis.weaknesses.keys()) <= set(_PHASE_LABELS), (
        f"weakness keys must be phase-subset; got {set(analysis.weaknesses.keys())}"
    )
    assert "opening" in analysis.weaknesses
    # Severity-shaped keys must not appear.
    for forbidden in ("blunders", "mistakes", "inaccuracies"):
        assert forbidden not in analysis.weaknesses, (
            f"Severity-shaped key {forbidden!r} reappeared in weaknesses — "
            f"PR #171's producer-consumer contract is broken: {analysis.weaknesses}"
        )


def test_PRODUCER_KEYS_ARE_PHASE_SUBSET_endgame_heavy():
    """Endgame-only mistakes emit only the "endgame" key."""
    analysis = _analysis_from_losses(
        losses_cp=[20, 30, 400, 350],
        phase_losses_cp={
            "opening": [20],
            "middlegame": [30],
            "endgame": [400, 350],
        },
    )
    assert set(analysis.weaknesses.keys()) <= set(_PHASE_LABELS)
    assert "endgame" in analysis.weaknesses


def test_PRODUCER_KEYS_ARE_PHASE_SUBSET_mixed():
    """Mistakes spread across all three phases emit all three keys."""
    analysis = _analysis_from_losses(
        losses_cp=[200, 300, 400],
        phase_losses_cp={
            "opening": [200],
            "middlegame": [300],
            "endgame": [400],
        },
    )
    assert set(analysis.weaknesses.keys()) == set(_PHASE_LABELS), (
        f"all three phases should fire on a mixed game; got "
        f"{set(analysis.weaknesses.keys())}"
    )


# ---------------------------------------------------------------------------
# 2 · Values are rates in [0, 1]
# ---------------------------------------------------------------------------


def test_PRODUCER_VALUES_ARE_RATES_IN_UNIT_INTERVAL():
    """Worst-case game (every move is a blunder) produces a rate of
    1.0 in its dominant phase, not >1.  Best-case (no mistakes)
    produces no keys at all."""
    analysis = _analysis_from_losses(
        losses_cp=[400, 400, 400, 400],
        phase_losses_cp={
            "opening": [400, 400, 400, 400],
            "middlegame": [],
            "endgame": [],
        },
    )
    for phase, rate in analysis.weaknesses.items():
        assert 0.0 <= rate <= 1.0, (
            f"rate for phase {phase!r} = {rate}; must be in [0, 1]"
        )
    # Worst case in this test is opening = 4 player blunders out of 4
    # player moves -> rate 1.0.
    assert analysis.weaknesses.get("opening") == 1.0


# ---------------------------------------------------------------------------
# 3 · Round-trip through the consumer produces a dominant_category
# ---------------------------------------------------------------------------


def test_CONSUMER_ROUNDTRIP_PRODUCES_DOMINANT_CATEGORY():
    """The whole point of PR #171: the producer's output, serialised
    to JSON the way ``EventStorage.store_game`` does, can be read
    back by ``aggregate_from_weakness_dicts`` and produce a non-None
    dominant_category.  Pre-PR-#171 this round-trip silently
    produced ``dominant_category=None`` because the consumer's
    phase-keyed lookup found nothing."""
    analysis = _analysis_from_losses(
        losses_cp=[200, 200, 350, 100],
        phase_losses_cp={
            "opening": [200, 200],
            "middlegame": [350, 100],
            "endgame": [],
        },
    )
    weaknesses_json = json.dumps(analysis.weaknesses)
    weakness_dict = json.loads(weaknesses_json)

    stats = aggregate_from_weakness_dicts([weakness_dict])
    assert stats.dominant_category is not None, (
        f"dominant_category should be derivable from a single game "
        f"with player mistakes; got None.  weaknesses={weakness_dict}, "
        f"phase_rates={stats.phase_rates}, "
        f"category_scores={stats.category_scores}.  The producer-consumer "
        f"contract drifted again — see PR #171."
    )
    # The category space comes from MistakeCategory.ALL, not phase labels.
    assert stats.dominant_category in {
        "opening_preparation",
        "tactical_vision",
        "positional_play",
        "endgame_technique",
    }


# ---------------------------------------------------------------------------
# 4 · Phase classifier matches the historical producer
# ---------------------------------------------------------------------------


def test_PHASE_CLASSIFIER_MATCHES_HISTORICAL_PRODUCER():
    """``pgn_accuracy._phase`` and ``GameWeaknessAnalyzer._phase`` must
    agree on every board state.  Drift between the two means a game
    classified as ``opening`` by one producer and ``middlegame`` by
    the other — silently breaks the contract.

    We construct boards at three piece counts (full, 16-piece
    midgame, 4-piece endgame) and assert agreement.  The
    GameWeaknessAnalyzer subclass needs a stockfish path; we never
    call ``.analyze()``, only the private ``._phase`` method.
    """
    historical = GameWeaknessAnalyzer.__new__(GameWeaknessAnalyzer)  # type: ignore[call-arg]

    # 32 pieces — starting position: opening.
    full = chess.Board()
    assert _pgn_accuracy_phase(full) == historical._phase(full) == "opening"

    # ~12 pieces — middlegame threshold.  ``GameWeaknessAnalyzer._phase``
    # returns "middlegame" for ``pieces > 12``, "endgame" otherwise.
    midgame_fen = "4k3/p7/8/4p3/8/8/4P3/4K3 w - - 0 1"  # 4 pieces
    midgame = chess.Board(midgame_fen)
    assert _pgn_accuracy_phase(midgame) == historical._phase(midgame)

    # ~16 pieces — somewhere in the middlegame band.
    mid_band = chess.Board(
        "r3k2r/pp3ppp/8/8/8/8/PP3PPP/R3K2R w KQkq - 0 1"  # 14 pieces
    )
    assert _pgn_accuracy_phase(mid_band) == historical._phase(mid_band)


# ---------------------------------------------------------------------------
# 5 · Empty game does not leak zero-rate keys
# ---------------------------------------------------------------------------


def test_EMPTY_GAME_DOES_NOT_LEAK_KEYS():
    """A game with zero player moves analysed produces an empty
    weaknesses dict, not a dict of zero-rate phase keys.  Keeps
    event.weaknesses_json compact and lets the consumer skip the
    record cleanly."""
    analysis = _analysis_from_losses(
        losses_cp=[],
        phase_losses_cp={"opening": [], "middlegame": [], "endgame": []},
    )
    assert analysis.weaknesses == {}


def test_clean_game_does_not_leak_keys():
    """A game with only inaccuracies and clean moves emits no
    weakness keys — only mistakes and blunders count toward the
    phase-rate numerator."""
    analysis = _analysis_from_losses(
        losses_cp=[30, 40, 10, 70, 80],  # all below 150 cp = inaccuracy/ok
        phase_losses_cp={
            "opening": [30, 40],
            "middlegame": [10, 70, 80],
            "endgame": [],
        },
    )
    assert analysis.weaknesses == {}, (
        f"clean game should emit empty weaknesses dict; got {analysis.weaknesses}"
    )
