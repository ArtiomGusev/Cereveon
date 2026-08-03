"""
Backend tests for the optional game_id field on GameFinishRequest.

Background
----------
/game/start returns a uuid game_id and writes a row into the `games`
table.  /game/finish historically wrote ONLY a separate game_events
row, leaving the games row in NULL purgatory forever.  The client
now persists the game_id from /game/start and forwards it on
/game/finish, which lets the events router call repo.finish_game()
to mark the games row complete.

This is what closes the Resume loop: the Android Resume tap reuses
the same game_id across the original session and the resumed one,
so a finished game gets exactly one games row marked complete
regardless of how many times the user backgrounded mid-game.

Pinned invariants
-----------------
 1. RESUME_OPTIONAL_GAME_ID         omitting game_id is still accepted
                                    (older clients keep working).
 2. RESUME_GAME_ID_BLANK_NORMALISED a whitespace-only game_id is
                                    treated as None — no spurious
                                    repo.finish_game call.
 3. RESUME_GAME_ID_MAX_LEN          rejects > 64 chars defensively.
 4. RESUME_GAME_ID_NO_CONTROL_CHARS rejects control chars (CRLF, NUL).
 5. RESUME_FINISH_CALLS_REPO        when game_id is provided, the
                                    games row's result + finished_at
                                    columns are populated.
 6. RESUME_FINISH_NO_REPO_WHEN_NULL no repo.finish_game call happens
                                    when game_id is omitted.
 7. RESUME_REPO_FAILURE_NONFATAL    repo.finish_game raising must NOT
                                    fail the finish endpoint — the
                                    GameEvent + skill update are the
                                    load-bearing writes.
 8. RESUME_APP_GAME_ID_FORWARDED    req.game_id is threaded into
                                    store_game(app_game_id=...) so the
                                    game_events row links to the live
                                    game's chat thread; None when omitted.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from starlette.requests import Request as StarletteRequest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from llm.seca.events.router import GameFinishRequest, finish_game
from llm.seca.shared_limiter import limiter


_VALID_PGN = (
    '[Event "Test"]\n'
    '[Site "?"]\n'
    '[Date "2026.01.01"]\n'
    '[Round "1"]\n'
    '[White "Tester"]\n'
    '[Black "Bot"]\n'
    '[Result "1-0"]\n'
    "\n"
    "1. e4 e5 2. Nf3 Nc6 1-0"
)
_DEFAULT_REQ = {
    "pgn": _VALID_PGN,
    "result": "win",
    "accuracy": 0.8,
    "weaknesses": {},
}


def _fake_request() -> StarletteRequest:
    # The finish handler reads ``request.app.state.engine_pool`` for the
    # server-side PGN accuracy recompute.  Stub an app with empty state so the
    # resolver falls back to client-supplied accuracy (no engine pool ->
    # ACC_FALLBACK path), mirroring _make_game_finish_mocks in
    # test_api_contract_validation.py.  Without the "app" scope key, starlette
    # raises ``KeyError: 'app'`` on ``request.app`` before the resolver's
    # getattr default can apply.
    return StarletteRequest({
        "type": "http", "method": "POST", "path": "/game/finish",
        "headers": [], "client": ("127.0.0.1", 0),
        "app": SimpleNamespace(state=SimpleNamespace()),
    })


def _make_player_and_db():
    """Mirrors _make_game_finish_mocks in test_api_contract_validation.py
    — minimal player + auto-mock db that satisfies every db.* chain
    finish_game's downstream code reaches for."""
    player = SimpleNamespace(id="player-abc", rating=1500.0, confidence=0.5)

    def _fake_refresh(obj):
        if obj is player:
            # Simulate the post-skill-update rating bump so the
            # response's new_rating field is realistic; not load-bearing
            # for these tests, just keeps the surrounding code happy.
            player.rating = 1510.0
            player.confidence = 0.55

    db = MagicMock()
    db.refresh.side_effect = _fake_refresh
    # The recent-games query in the coach-pipeline branch does a
    # .query(...).filter(...).order_by(...).limit(...).all() chain;
    # default MagicMock returns are MagicMocks themselves, but .all()
    # needs to return an iterable for the recent_weaknesses loop.
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = (
        []
    )
    # The idempotent-replay guard (events.router._replayed_finish_response,
    # added by the 2026-07-14 P2 #1 double-finish fix) runs a
    # .query(GameEvent).filter(...).order_by(...).first() BEFORE the finish
    # body whenever a game_id is present.  Left at its default the auto-
    # MagicMock returns a truthy fake "prior event", so the guard short-
    # circuits to the 202 pending replay and the finish body (repo.finish_game
    # / store_game) never runs — every game_id-carrying assertion below then
    # sees 0 calls.  Force the lookup to None so these tests exercise a FRESH
    # finish.
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    return player, db


def _call_finish(req_kwargs, repo_finish_side_effect=None):
    """Invoke the finish_game handler with the resume-link repo call
    intercepted so we can assert it was (or wasn't) called.

    The repo function is imported inside finish_game via
    `from llm.seca.storage.repo import finish_game as _repo_finish_game`
    — patch the source module rather than a name in events.router."""
    player, db = _make_player_and_db()
    req = GameFinishRequest(**req_kwargs)
    request = _fake_request()
    repo_finish_mock = MagicMock(side_effect=repo_finish_side_effect)
    fake_event = SimpleNamespace(id=99)

    prev = limiter.enabled
    limiter.enabled = False
    try:
        with (
            patch("llm.seca.events.router.EventStorage") as MockStorage,
            patch("llm.seca.events.router.SkillUpdater"),
            patch("llm.seca.storage.repo.finish_game", repo_finish_mock),
        ):
            MockStorage.return_value.store_game.return_value = fake_event
            # storage.get_recent_games is also called in the historical
            # analysis branch; default MagicMock return makes it iterate
            # over a MagicMock which fails — return an empty list.
            MockStorage.return_value.get_recent_games.return_value = []
            from fastapi import BackgroundTasks as _BackgroundTasks  # noqa: PLC0415

            result = finish_game(
                req=req,
                request=request,
                background_tasks=_BackgroundTasks(),
                player=player,
                db=db,
            )
    finally:
        limiter.enabled = prev

    return result, repo_finish_mock


# ---------------------------------------------------------------------------
# 1.  Schema validation
# ---------------------------------------------------------------------------


class TestGameFinishRequestGameIdValidation:
    """game_id is optional and bounded; older clients keep working."""

    def test_omitted_game_id_defaults_to_none(self):
        """RESUME_OPTIONAL_GAME_ID."""
        req = GameFinishRequest(**_DEFAULT_REQ)
        assert req.game_id is None

    def test_explicit_uuid_accepted(self):
        req = GameFinishRequest(**_DEFAULT_REQ, game_id="6d4b2c8e-1f3a-4d5e-9b7c-0a1b2c3d4e5f")
        assert req.game_id == "6d4b2c8e-1f3a-4d5e-9b7c-0a1b2c3d4e5f"

    def test_auto_player_key_accepted(self):
        # repo.get_or_create_auto_game uses "auto-{player_id}" — must
        # round-trip through the validator unchanged.
        req = GameFinishRequest(**_DEFAULT_REQ, game_id="auto-player-abc-123")
        assert req.game_id == "auto-player-abc-123"

    def test_blank_game_id_normalised_to_none(self):
        """RESUME_GAME_ID_BLANK_NORMALISED — whitespace-only / "" must
        become None so the endpoint doesn't fire a spurious
        repo.finish_game call."""
        for blank in ("", "   ", "\t"):
            req = GameFinishRequest(**_DEFAULT_REQ, game_id=blank)
            assert req.game_id is None, f"expected None, got {req.game_id!r}"

    def test_game_id_too_long_rejected(self):
        """RESUME_GAME_ID_MAX_LEN."""
        with pytest.raises(ValidationError, match="game_id must be ≤ 64"):
            GameFinishRequest(**_DEFAULT_REQ, game_id="x" * 65)

    @pytest.mark.parametrize("bad", ["abc\ndef", "abc\rdef", "abc\x00def", "abc\x7fdef"])
    def test_control_chars_rejected(self, bad):
        """RESUME_GAME_ID_NO_CONTROL_CHARS — log-injection defence."""
        with pytest.raises(ValidationError, match="control characters"):
            GameFinishRequest(**_DEFAULT_REQ, game_id=bad)


# ---------------------------------------------------------------------------
# 2.  Endpoint behaviour
# ---------------------------------------------------------------------------


class TestFinishGameRepoCall:
    """The finish_game endpoint calls repo.finish_game iff game_id is
    provided, and tolerates repo failures."""

    def test_repo_called_when_game_id_provided(self):
        """RESUME_FINISH_CALLS_REPO."""
        _, repo_mock = _call_finish({**_DEFAULT_REQ, "game_id": "test-game-id-123"})
        # player.id is threaded through so the repo scopes the finish to the
        # authenticated owner (IDOR fix) — see repo.finish_game.
        repo_mock.assert_called_once_with("test-game-id-123", "win", "player-abc")

    def test_repo_not_called_when_game_id_omitted(self):
        """RESUME_FINISH_NO_REPO_WHEN_NULL."""
        _, repo_mock = _call_finish(_DEFAULT_REQ)
        repo_mock.assert_not_called()

    def test_repo_not_called_for_blank_game_id(self):
        # Blank game_id is normalised to None by the validator, so the
        # endpoint sees req.game_id is None and skips the call.
        _, repo_mock = _call_finish({**_DEFAULT_REQ, "game_id": "   "})
        repo_mock.assert_not_called()

    def test_repo_failure_does_not_fail_finish(self):
        """RESUME_REPO_FAILURE_NONFATAL — a repo crash mustn't block
        the GameEvent + skill update path that's already happened."""
        result, repo_mock = _call_finish(
            {**_DEFAULT_REQ, "game_id": "stale-id"},
            repo_finish_side_effect=RuntimeError("games row vanished"),
        )
        repo_mock.assert_called_once()
        # Endpoint still returned a successful response — the finish
        # path ran end-to-end despite the repo crash.  Spot-check
        # fields that downstream UI consumes (rating, confidence,
        # coach_content) so a future response refactor can't quietly
        # turn this into a no-op-shaped success.
        assert isinstance(result, dict)
        for key in ("confidence", "coach_action", "coach_content"):
            assert key in result, f"finish response missing {key!r}, got keys: {sorted(result.keys())}"

    def test_repo_called_with_loss_result(self):
        # Result string passes through verbatim so the games row
        # records the same status the events row does.
        _, repo_mock = _call_finish({**_DEFAULT_REQ, "result": "loss", "game_id": "abc"})
        repo_mock.assert_called_once_with("abc", "loss", "player-abc")

    def test_repo_called_with_draw_result(self):
        _, repo_mock = _call_finish({**_DEFAULT_REQ, "result": "draw", "game_id": "abc"})
        repo_mock.assert_called_once_with("abc", "draw", "player-abc")


# ---------------------------------------------------------------------------
# 3.  app_game_id forwarding (game-history -> chat link)
# ---------------------------------------------------------------------------


class TestFinishGameForwardsAppGameId:
    """RESUME_APP_GAME_ID_FORWARDED — finish_game threads req.game_id into
    EventStorage.store_game(app_game_id=...) so the persisted game_events row
    carries the live games.id, letting the game-history UI load that game's
    coaching chat via GET /chat/history?game_id=..."""

    def _finish_capturing_store(self, req_kwargs):
        """Run finish_game with EventStorage mocked; return the store_game
        mock so the caller can inspect how it was invoked."""
        player, db = _make_player_and_db()
        req = GameFinishRequest(**req_kwargs)
        request = _fake_request()
        fake_event = SimpleNamespace(id=99)

        prev = limiter.enabled
        limiter.enabled = False
        try:
            with (
                patch("llm.seca.events.router.EventStorage") as MockStorage,
                patch("llm.seca.events.router.SkillUpdater"),
                patch("llm.seca.storage.repo.finish_game", MagicMock()),
            ):
                MockStorage.return_value.store_game.return_value = fake_event
                MockStorage.return_value.get_recent_games.return_value = []
                from fastapi import BackgroundTasks as _BackgroundTasks  # noqa: PLC0415

                finish_game(
                    req=req,
                    request=request,
                    background_tasks=_BackgroundTasks(),
                    player=player,
                    db=db,
                )
                return MockStorage.return_value.store_game
        finally:
            limiter.enabled = prev

    def test_app_game_id_forwarded_from_game_id(self):
        """A provided game_id is passed straight through to store_game."""
        store_mock = self._finish_capturing_store({**_DEFAULT_REQ, "game_id": "live-game-7"})
        store_mock.assert_called_once()
        assert store_mock.call_args.kwargs.get("app_game_id") == "live-game-7"

    def test_app_game_id_none_when_game_id_omitted(self):
        """No game_id -> store_game gets app_game_id=None (no chat link)."""
        store_mock = self._finish_capturing_store(_DEFAULT_REQ)
        store_mock.assert_called_once()
        assert store_mock.call_args.kwargs.get("app_game_id") is None

    def test_app_game_id_none_for_blank_game_id(self):
        """Blank game_id is normalised to None by the validator, so the
        store link is None too — consistent with the repo-call skip."""
        store_mock = self._finish_capturing_store({**_DEFAULT_REQ, "game_id": "   "})
        store_mock.assert_called_once()
        assert store_mock.call_args.kwargs.get("app_game_id") is None
