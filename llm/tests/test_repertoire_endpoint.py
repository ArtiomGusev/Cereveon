"""
Backend tests for GET /repertoire — opening repertoire list backing
the AtriumOpenings screen.

Pinned invariants
-----------------
 1. REP_DEFAULTS_ON_EMPTY:        a fresh player (nothing saved) gets
                                   the 4-entry canonical default list.
 2. REP_DEFAULTS_NOT_PERSISTED:   GET is side-effect-free — the
                                   defaults are NOT inserted on read.
 3. REP_SAVED_OVERRIDES_DEFAULTS: when a player has stored rows, the
                                   GET returns those, NOT the defaults.
 4. REP_ORDER_BY_ORDINAL:         stored rows come back in ordinal ASC.
 5. REP_FILTERS_BY_PLAYER:        one player's openings don't leak to
                                   another.
 6. REP_RESPONSE_SHAPE:           {"openings": [...]} envelope, each
                                   entry has the documented field set.
 7. REP_DEFAULT_HAS_EXACTLY_ONE_ACTIVE: the canonical defaults always
                                   have exactly one is_active=True
                                   entry (matches the OpeningsActivity
                                   companion test).
 8. REP_DEFAULT_MIRRORS_ANDROID:  the default ECOs match the Android
                                   client's DEFAULT_REPERTOIRE 1-for-1
                                   so first-vs-second-visit don't
                                   show different defaults.
"""

from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Bind the project SQLAlchemy engine to a per-test SQLite file.

    Post-2026-05-09 the repertoire / games / moves / explanations
    tables are SQLAlchemy models on the auth-side engine; this fixture
    swaps the engine to a tmp file for the test's lifetime.  See
    ``_storage_test_helpers.bind_temp_database`` for the mechanics.
    """
    from llm.tests._storage_test_helpers import bind_temp_database

    return bind_temp_database(tmp_path, monkeypatch)


def _ensure_player(player_id: str = "player-rep") -> str:
    from llm.seca.storage.repo import ensure_player
    ensure_player(player_id)
    return player_id


def _player_namespace(id="player-rep"):
    from types import SimpleNamespace
    return SimpleNamespace(id=id)


def _fake_request():
    from starlette.requests import Request
    return Request({
        "type": "http", "method": "GET", "path": "/repertoire",
        "headers": [], "client": ("127.0.0.1", 0),
    })


def _disable_limiter():
    """Return the endpoints' limiter with its rate-limit window cleared.

    Imported from the router module so it is exactly the object the
    ``@limiter.limit`` decorators are bound to.  Under slowapi 0.1.10 the
    in-process FixedWindow storage accumulates across the full CI suite, so
    toggling ``enabled`` alone is not enough for a helper that fires many
    calls in one test (``TestRepertoireDrillResult.test_clamped_to_unit_interval``
    drills mastery 50+ times against the shared 30/minute budget, tripping a
    429 once the process-wide window fills).  ``reset()`` clears the window so
    each functional call starts from zero; the rate limit itself is exercised
    by ``test_security_game_finish_rate_limit``, not here.
    """
    from llm.seca.repertoire.router import limiter

    try:
        limiter.reset()
    except Exception:  # pragma: no cover - storage backends without reset()
        pass
    return limiter


def _call_get_repertoire(player):
    from llm.seca.repertoire.router import get_repertoire
    limiter = _disable_limiter()
    prev = limiter.enabled
    limiter.enabled = False
    try:
        return get_repertoire(request=_fake_request(), player=player)
    finally:
        limiter.enabled = prev


# ---------------------------------------------------------------------------
# 1.  Default-on-empty behaviour
# ---------------------------------------------------------------------------


class TestRepertoireDefaults:
    def test_fresh_player_gets_canonical_defaults(self, temp_db):
        """REP_DEFAULTS_ON_EMPTY."""
        _ensure_player()
        result = _call_get_repertoire(_player_namespace())
        assert "openings" in result
        assert len(result["openings"]) == 4
        ecos = [o["eco"] for o in result["openings"]]
        assert ecos == ["C84", "B22", "D02", "A04"]

    def test_defaults_not_persisted_on_read(self, temp_db):
        """REP_DEFAULTS_NOT_PERSISTED — calling GET twice with no
        storage between must not have populated the table."""
        _ensure_player()
        from llm.seca.storage.repo import list_repertoire

        _call_get_repertoire(_player_namespace())
        # Repo-level read should still see zero rows for this player.
        assert list_repertoire("player-rep") == []

        _call_get_repertoire(_player_namespace())
        assert list_repertoire("player-rep") == []

    def test_default_has_exactly_one_active(self, temp_db):
        """REP_DEFAULT_HAS_EXACTLY_ONE_ACTIVE — matches the Android
        client's OpeningsActivityTest invariant."""
        _ensure_player()
        result = _call_get_repertoire(_player_namespace())
        actives = [o for o in result["openings"] if o["is_active"]]
        assert len(actives) == 1, f"expected 1 active line, got {len(actives)}"

    def test_default_mirrors_android_companion(self, temp_db):
        """REP_DEFAULT_MIRRORS_ANDROID — drift here would show users
        different ECOs on first vs subsequent visits."""
        from llm.seca.repertoire.router import DEFAULT_REPERTOIRE
        _ensure_player()
        result = _call_get_repertoire(_player_namespace())
        assert result["openings"] == DEFAULT_REPERTOIRE


# ---------------------------------------------------------------------------
# 2.  Saved-overrides-defaults + ordering + isolation
# ---------------------------------------------------------------------------


class TestRepertoireStorage:
    def _insert(self, db_file, **fields):
        """Direct ORM insert — bypasses the upsert helper so these
        tests can exercise the read path against precisely-shaped
        state.  Uses the same SQLAlchemy session the repo helpers
        do, so the row is visible through ``list_repertoire`` etc.
        without committing twice over the same DB file.
        """
        defaults = {
            "player_id": "player-rep",
            "eco": "C84",
            "name": "Ruy Lopez",
            "line": "1.e4 e5",
            "mastery": 0.5,
            "is_active": 0,
            "ordinal": 0,
        }
        defaults.update(fields)

        from llm.seca.auth.router import SessionLocal
        from llm.seca.storage.models import Repertoire

        sess = SessionLocal()
        try:
            sess.add(Repertoire(**defaults))
            sess.commit()
        finally:
            sess.close()

    def test_saved_overrides_defaults(self, temp_db):
        """REP_SAVED_OVERRIDES_DEFAULTS — once the player has even one
        saved row, the defaults are NOT mixed in."""
        _ensure_player()
        self._insert(temp_db, eco="X99", name="My Custom Line", ordinal=0)
        result = _call_get_repertoire(_player_namespace())
        assert len(result["openings"]) == 1
        assert result["openings"][0]["eco"] == "X99"

    def test_order_by_ordinal_ascending(self, temp_db):
        """REP_ORDER_BY_ORDINAL — display order is stable."""
        _ensure_player()
        self._insert(temp_db, eco="A1", ordinal=2)
        self._insert(temp_db, eco="A2", ordinal=0)
        self._insert(temp_db, eco="A3", ordinal=1)

        ecos = [o["eco"] for o in _call_get_repertoire(_player_namespace())["openings"]]
        assert ecos == ["A2", "A3", "A1"]

    def test_filters_by_player(self, temp_db):
        """REP_FILTERS_BY_PLAYER — player A's openings must not leak
        to player B."""
        _ensure_player("player-a")
        _ensure_player("player-b")
        self._insert(temp_db, player_id="player-a", eco="A1")

        # B has no rows — falls through to defaults
        result_b = _call_get_repertoire(_player_namespace("player-b"))
        ecos = [o["eco"] for o in result_b["openings"]]
        assert ecos == ["C84", "B22", "D02", "A04"]  # canonical defaults

        # A sees only their own
        result_a = _call_get_repertoire(_player_namespace("player-a"))
        assert [o["eco"] for o in result_a["openings"]] == ["A1"]


# ---------------------------------------------------------------------------
# 3.  Response shape
# ---------------------------------------------------------------------------


class TestRepertoireResponseShape:
    def test_envelope_is_openings(self, temp_db):
        """REP_RESPONSE_SHAPE — top-level key is 'openings' so the
        client can add metadata (count / pagination cursor / etc.)
        later without a breaking change."""
        _ensure_player()
        result = _call_get_repertoire(_player_namespace())
        assert isinstance(result, dict)
        assert list(result.keys()) == ["openings"]

    def test_each_entry_has_documented_fields(self, temp_db):
        _ensure_player()
        result = _call_get_repertoire(_player_namespace())
        required = {"eco", "name", "line", "mastery", "is_active", "ordinal"}
        for entry in result["openings"]:
            missing = required - set(entry.keys())
            assert not missing, f"entry missing fields: {missing} in {entry}"


# ---------------------------------------------------------------------------
# 4.  Editing endpoints — POST add / DELETE / POST set-active
# ---------------------------------------------------------------------------


def _call_add(player, **kwargs):
    from llm.seca.repertoire.router import add_or_update_opening, RepertoireEntryRequest
    limiter = _disable_limiter()
    prev = limiter.enabled
    limiter.enabled = False
    try:
        return add_or_update_opening(
            req=RepertoireEntryRequest(**kwargs),
            request=_fake_request(),
            player=player,
        )
    finally:
        limiter.enabled = prev


def _call_delete(player, eco):
    from llm.seca.repertoire.router import delete_opening_endpoint
    limiter = _disable_limiter()
    prev = limiter.enabled
    limiter.enabled = False
    try:
        return delete_opening_endpoint(
            eco=eco,
            request=_fake_request(),
            player=player,
        )
    finally:
        limiter.enabled = prev


def _call_set_active(player, eco):
    from llm.seca.repertoire.router import set_active_opening_endpoint
    limiter = _disable_limiter()
    prev = limiter.enabled
    limiter.enabled = False
    try:
        return set_active_opening_endpoint(
            eco=eco,
            request=_fake_request(),
            player=player,
        )
    finally:
        limiter.enabled = prev


class TestRepertoireAdd:
    """POST /repertoire — add or upsert one opening."""

    def test_add_seeds_defaults_then_appends(self, temp_db):
        _ensure_player()
        result = _call_add(
            _player_namespace(),
            eco="X99", name="My Custom", line="1.a3", mastery=0.0,
        )
        ecos = [o["eco"] for o in result["openings"]]
        # Defaults (4) + new entry (1).  Ordering: defaults first by
        # ordinal, custom appended at the end.
        assert ecos == ["C84", "B22", "D02", "A04", "X99"]

    def test_upsert_existing_eco_updates_in_place(self, temp_db):
        _ensure_player()
        # Seed defaults via a first call, then update C84's name.
        _call_add(_player_namespace(), eco="X99", name="x", line="1.a3")
        result = _call_add(
            _player_namespace(),
            eco="C84", name="Renamed Ruy", line="1.e4 e5", mastery=0.9,
        )
        # Same number of rows — no duplicate.
        ecos = [o["eco"] for o in result["openings"]]
        assert len([e for e in ecos if e == "C84"]) == 1
        c84 = next(o for o in result["openings"] if o["eco"] == "C84")
        assert c84["name"] == "Renamed Ruy"
        assert c84["mastery"] == 0.9

    def test_invalid_eco_rejected(self, temp_db):
        _ensure_player()
        for bad in ("", "   ", "longerthan8", "abc\nde", "abc\x00"):
            with pytest.raises(HTTPException) as exc:
                _call_add(_player_namespace(), eco=bad, name="x", line="1.a3")
            assert exc.value.status_code == 400, f"expected 400 for eco={bad!r}"

    def test_invalid_mastery_rejected(self, temp_db):
        _ensure_player()
        from pydantic import ValidationError
        for bad in (-0.1, 1.5, 100.0):
            with pytest.raises(ValidationError, match="mastery must be in"):
                _call_add(_player_namespace(), eco="X1", name="x", line="1.a3", mastery=bad)


class TestRepertoireDelete:
    """DELETE /repertoire/{eco}."""

    def test_delete_seeds_defaults_then_removes(self, temp_db):
        _ensure_player()
        result = _call_delete(_player_namespace(), eco="C84")
        ecos = [o["eco"] for o in result["openings"]]
        assert "C84" not in ecos
        # Other defaults survived.
        assert ecos == ["B22", "D02", "A04"]

    def test_delete_unknown_returns_404(self, temp_db):
        _ensure_player()
        with pytest.raises(HTTPException) as exc:
            _call_delete(_player_namespace(), eco="ZZ9")
        assert exc.value.status_code == 404

    def test_delete_invalid_eco_rejected(self, temp_db):
        _ensure_player()
        with pytest.raises(HTTPException) as exc:
            _call_delete(_player_namespace(), eco="abc\ndef")
        assert exc.value.status_code == 400


class TestRepertoireSetActive:
    """POST /repertoire/{eco}/active — atomic exactly-one-active swap."""

    def test_promote_existing_default_line(self, temp_db):
        _ensure_player()
        # Defaults have C84 active; promote B22 instead.
        result = _call_set_active(_player_namespace(), eco="B22")
        actives = [o["eco"] for o in result["openings"] if o["is_active"]]
        assert actives == ["B22"], f"expected only B22 active, got {actives}"

    def test_unknown_eco_returns_404(self, temp_db):
        _ensure_player()
        with pytest.raises(HTTPException) as exc:
            _call_set_active(_player_namespace(), eco="ZZ9")
        assert exc.value.status_code == 404

    def test_promote_after_add(self, temp_db):
        """User adds a new line, then promotes it — the prior
        active line (C84) must be demoted."""
        _ensure_player()
        _call_add(_player_namespace(), eco="X99", name="custom", line="1.a3")
        result = _call_set_active(_player_namespace(), eco="X99")
        actives = [o["eco"] for o in result["openings"] if o["is_active"]]
        assert actives == ["X99"]


def _call_drill(player, eco, outcome):
    from llm.seca.repertoire.router import drill_result_endpoint, DrillResultRequest
    limiter = _disable_limiter()
    prev = limiter.enabled
    limiter.enabled = False
    try:
        return drill_result_endpoint(
            eco=eco,
            req=DrillResultRequest(outcome=outcome),
            request=_fake_request(),
            player=player,
        )
    finally:
        limiter.enabled = prev


class TestRepertoireDrillResult:
    """POST /repertoire/{eco}/drill-result — EMA mastery update."""

    def test_perfect_outcome_pushes_mastery_up(self, temp_db):
        """Outcome 1.0 against a 0.78 default → 0.78 + 0.2*0.22 ≈ 0.824."""
        _ensure_player()
        result = _call_drill(_player_namespace(), eco="C84", outcome=1.0)
        c84 = next(o for o in result["openings"] if o["eco"] == "C84")
        assert c84["mastery"] == pytest.approx(0.824, abs=1e-9)

    def test_bad_outcome_pushes_mastery_down(self, temp_db):
        """Outcome 0.2 against a 0.78 default → 0.78 + 0.2*(-0.58) = 0.664."""
        _ensure_player()
        result = _call_drill(_player_namespace(), eco="C84", outcome=0.2)
        c84 = next(o for o in result["openings"] if o["eco"] == "C84")
        assert c84["mastery"] == pytest.approx(0.664, abs=1e-9)

    def test_clamped_to_unit_interval(self, temp_db):
        """Successive perfect outcomes can never push mastery > 1.0
        (defensive — the EMA can't escape [0,1] in theory but float
        noise could)."""
        _ensure_player()
        for _ in range(50):
            _call_drill(_player_namespace(), eco="C84", outcome=1.0)
        result = _call_drill(_player_namespace(), eco="C84", outcome=1.0)
        c84 = next(o for o in result["openings"] if o["eco"] == "C84")
        assert 0.0 <= c84["mastery"] <= 1.0

    def test_seeds_defaults_for_fresh_player(self, temp_db):
        """A user who drills before ever editing should still see
        their drill recorded — endpoint seeds defaults first."""
        _ensure_player()
        # A fresh player has nothing in the table; the endpoint must
        # materialise the defaults before applying the drill.
        result = _call_drill(_player_namespace(), eco="C84", outcome=1.0)
        ecos = [o["eco"] for o in result["openings"]]
        # All 4 defaults are now present (seeded), with C84's mastery bumped.
        assert ecos == ["C84", "B22", "D02", "A04"]

    def test_unknown_eco_returns_404(self, temp_db):
        """The seed step materialises ONLY the canonical defaults;
        an unknown ECO still has no row to update."""
        _ensure_player()
        with pytest.raises(HTTPException) as exc:
            _call_drill(_player_namespace(), eco="ZZ9", outcome=1.0)
        assert exc.value.status_code == 404

    def test_invalid_outcome_rejected(self, temp_db):
        """Outcome must be in [0,1]; out-of-range fails validation."""
        from pydantic import ValidationError
        _ensure_player()
        for bad in (-0.1, 1.5, 100.0):
            with pytest.raises(ValidationError, match="outcome must be in"):
                _call_drill(_player_namespace(), eco="C84", outcome=bad)

    def test_idempotent_with_outcome_equal_to_current(self, temp_db):
        """When outcome exactly matches current mastery, EMA leaves
        the value unchanged (defensive — guards against float drift
        compounding into a noticeable shift over many drills)."""
        _ensure_player()
        # First seed defaults so we know mastery=0.78 for C84.
        _call_drill(_player_namespace(), eco="C84", outcome=0.78)
        result = _call_drill(_player_namespace(), eco="C84", outcome=0.78)
        c84 = next(o for o in result["openings"] if o["eco"] == "C84")
        assert c84["mastery"] == pytest.approx(0.78, abs=1e-9)


class TestRepertoireSeedingIdempotence:
    """seed_default_repertoire is the central reason editing
    endpoints work on a fresh player.  These pin its behaviour."""

    def test_seed_only_runs_when_empty(self, temp_db):
        from llm.seca.storage.repo import seed_default_repertoire, list_repertoire
        from llm.seca.repertoire.router import DEFAULT_REPERTOIRE

        _ensure_player()
        assert seed_default_repertoire("player-rep", DEFAULT_REPERTOIRE) == 4
        assert len(list_repertoire("player-rep")) == 4
        # Second call no-ops.
        assert seed_default_repertoire("player-rep", DEFAULT_REPERTOIRE) == 0
        assert len(list_repertoire("player-rep")) == 4
