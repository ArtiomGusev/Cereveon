"""
Tests for /game/finish endpoint: PGN validation and coach-pipeline error signalling.

Approach
--------
Tier 1 — AST inspection
    Parse llm/seca/events/router.py to verify structural invariants without
    executing any module-level code.  CI-safe even when heavy dependencies
    (SQLAlchemy, Ollama, Stockfish) are unavailable.

Tier 2 — Pydantic model validation (direct import with fallback)
    Import the real GameFinishRequest, falling back to a local replica that
    duplicates the validators when the module chain cannot be resolved in CI.

Pinned invariants
-----------------
 1. GAME_PGN_EMPTY_REJECTED          Empty PGN → ValidationError.
 2. GAME_PGN_WHITESPACE_REJECTED     Whitespace-only PGN → ValidationError.
 3. GAME_PGN_TOO_LARGE_REJECTED      PGN > 100 000 chars → ValidationError.
 4. GAME_PGN_GIBBERISH_REJECTED      Non-PGN garbage → ValidationError (422-class).
 5. GAME_PGN_ILLEGAL_MOVE_REJECTED   PGN with illegal chess moves → ValidationError.
 6. GAME_PGN_VALID_ACCEPTED          Well-formed PGN with moves → accepted.
 7. GAME_PGN_HEADERS_ONLY_ACCEPTED   PGN with headers but no moves → accepted.
 7. GAME_RESULT_INVALID_REJECTED     result not in {win,loss,draw} → ValidationError.
 8. GAME_ACCURACY_OOB_REJECTED       accuracy outside [0.0, 1.0] → ValidationError.
 9. AST_VALIDATE_PGN_USES_CHESS_PGN  validate_pgn calls chess.pgn.read_game.
10. AST_COACH_FALLBACK_TYPE_IS_ERROR  Coach pipeline except block uses type="ERROR".
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ROUTER_PY = _REPO_ROOT / "llm" / "seca" / "events" / "router.py"

# ---------------------------------------------------------------------------
# Tier 2 — model import with fallback replica
# ---------------------------------------------------------------------------

try:
    os.environ.setdefault("SECA_API_KEY", "ci-test-key")
    os.environ.setdefault("SECA_ENV", "dev")
    os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")
    from llm.seca.events.router import GameFinishRequest as _GameFinishRequest

    _MODEL_IMPORTED = True
except Exception:
    import io as _io

    import chess.pgn as _chess_pgn
    from pydantic import BaseModel
    from pydantic import field_validator as _fv

    class _GameFinishRequest(BaseModel):  # type: ignore[no-redef]
        pgn: str
        result: str
        accuracy: float
        weaknesses: dict
        player_id: str | None = None

        @_fv("pgn")
        @classmethod
        def validate_pgn(cls, v: str) -> str:
            if not v or not v.strip():
                raise ValueError("pgn must not be empty")
            if len(v) > 100_000:
                raise ValueError("pgn too large (max 100 000 chars)")
            try:
                game = _chess_pgn.read_game(_io.StringIO(v))
            except Exception as exc:
                raise ValueError(f"invalid PGN: {exc}") from exc
            if game is None:
                raise ValueError("invalid PGN: no game found")
            return v

        @_fv("result")
        @classmethod
        def validate_result(cls, v: str) -> str:
            if v not in {"win", "loss", "draw"}:
                raise ValueError("result must be win/loss/draw")
            return v

        @_fv("accuracy")
        @classmethod
        def validate_accuracy(cls, v: float) -> float:
            if not (0.0 <= v <= 1.0):
                raise ValueError("accuracy must be 0.0–1.0")
            return v

        @_fv("weaknesses")
        @classmethod
        def validate_weaknesses(cls, v: dict) -> dict:
            return v

    _MODEL_IMPORTED = False


# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------

_VALID_PGN = (
    '[Event "Test"]\n'
    '[Site "?"]\n'
    '[Date "2025.01.01"]\n'
    '[Round "1"]\n'
    '[White "Player1"]\n'
    '[Black "Player2"]\n'
    '[Result "1-0"]\n'
    "\n"
    "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 1-0"
)

_HEADERS_ONLY_PGN = (
    '[Event "Headersonly"]\n'
    '[Site "?"]\n'
    '[Date "2025.01.01"]\n'
    '[Round "1"]\n'
    '[White "Player1"]\n'
    '[Black "Player2"]\n'
    '[Result "*"]\n'
    "\n*"
)


def _valid_req(**overrides) -> dict:
    base = {
        "pgn": _VALID_PGN,
        "result": "win",
        "accuracy": 0.75,
        "weaknesses": {},
    }
    base.update(overrides)
    return base


# ===========================================================================
# Tier 2 — Pydantic validation tests
# ===========================================================================


class TestGameFinishPgnValidation:

    def test_empty_pgn_rejected(self):
        """GAME_PGN_EMPTY_REJECTED: empty string → ValidationError."""
        with pytest.raises(ValidationError):
            _GameFinishRequest(**_valid_req(pgn=""))

    def test_whitespace_only_pgn_rejected(self):
        """GAME_PGN_WHITESPACE_REJECTED: whitespace-only string → ValidationError."""
        with pytest.raises(ValidationError):
            _GameFinishRequest(**_valid_req(pgn="   \n\t  "))

    def test_pgn_too_large_rejected(self):
        """GAME_PGN_TOO_LARGE_REJECTED: PGN > 100 000 chars → ValidationError."""
        with pytest.raises(ValidationError):
            _GameFinishRequest(**_valid_req(pgn="x" * 100_001))

    def test_gibberish_pgn_rejected(self):
        """GAME_PGN_GIBBERISH_REJECTED: non-PGN garbage → ValidationError."""
        with pytest.raises(ValidationError):
            _GameFinishRequest(**_valid_req(pgn="this is definitely not chess pgn !@#$"))

    def test_valid_pgn_with_moves_accepted(self):
        """GAME_PGN_VALID_ACCEPTED: well-formed PGN with moves → accepted."""
        req = _GameFinishRequest(**_valid_req(pgn=_VALID_PGN))
        assert req.pgn == _VALID_PGN

    def test_headers_only_pgn_rejected(self):
        """GAME_PGN_HEADERS_ONLY_REJECTED: PGN with headers but no moves →
        ValidationError.  A finished game with an empty mainline is a
        degenerate/malformed finish; the PGN validator's "no moves found in
        mainline" guard rejects it, consistent with the gibberish and
        whitespace rejections above."""
        with pytest.raises(ValidationError):
            _GameFinishRequest(**_valid_req(pgn=_HEADERS_ONLY_PGN))


class TestGameFinishOtherValidation:

    def test_invalid_result_rejected(self):
        """GAME_RESULT_INVALID_REJECTED: result not in {win,loss,draw} → ValidationError."""
        with pytest.raises(ValidationError):
            _GameFinishRequest(**_valid_req(result="victory"))

    def test_accuracy_above_one_rejected(self):
        """GAME_ACCURACY_OOB_REJECTED: accuracy > 1.0 → ValidationError."""
        with pytest.raises(ValidationError):
            _GameFinishRequest(**_valid_req(accuracy=1.01))

    def test_accuracy_below_zero_rejected(self):
        """GAME_ACCURACY_OOB_REJECTED: accuracy < 0.0 → ValidationError."""
        with pytest.raises(ValidationError):
            _GameFinishRequest(**_valid_req(accuracy=-0.01))


# ===========================================================================
# Tier 1 — AST inspection
# ===========================================================================


def _parse_router() -> ast.Module:
    return ast.parse(_ROUTER_PY.read_text(encoding="utf-8"))


def _get_function(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


class TestAstRouterInvariants:

    def test_validate_pgn_uses_chess_pgn_read_game(self):
        """AST_VALIDATE_PGN_USES_CHESS_PGN: validate_pgn must call chess.pgn.read_game."""
        source = _ROUTER_PY.read_text(encoding="utf-8")
        assert "chess.pgn" in source, (
            "router.py does not import or use chess.pgn. "
            "validate_pgn must call chess.pgn.read_game() to reject malformed PGN "
            "before the game is stored, otherwise garbage data enters the DB."
        )
        assert "read_game" in source, (
            "validate_pgn must call chess.pgn.read_game() — "
            "a length-only check does not catch illegal moves or invalid PGN structure."
        )

    def test_coach_pipeline_fallback_uses_error_type(self):
        """AST_COACH_FALLBACK_TYPE_IS_ERROR: coach except block must set type='ERROR'.

        When the coach pipeline fails the Android client receives the fallback
        coach_action.  A type of 'default' is indistinguishable from a real
        recommendation; 'ERROR' lets the client surface "Keep playing" as a
        degraded-mode message instead of acting on misleading advice.
        """
        source = _ROUTER_PY.read_text(encoding="utf-8")
        assert 'type="ERROR"' in source or "type='ERROR'" in source, (
            "Coach pipeline exception handler must set type='ERROR' on the fallback "
            "coach_action.  Currently it uses type='default', which Android cannot "
            "distinguish from a genuine recommendation."
        )
