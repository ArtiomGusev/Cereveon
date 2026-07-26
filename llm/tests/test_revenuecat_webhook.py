"""Tests for POST /billing/revenuecat/webhook — RevenueCat subscription
source of truth.

RevenueCat validates receipts with the stores and POSTs lifecycle events;
the webhook verifies a shared-secret Authorization header, maps
``event.app_user_id`` (the server player id) to the player, and flips
``Player.plan``.  Pinned here:

1.  Auth: unconfigured secret → 503; wrong/absent header → 401.
2.  Grant: INITIAL_PURCHASE / RENEWAL of the "pro" entitlement → plan=pro.
3.  Revoke: EXPIRATION → plan=free.  CANCELLATION is a NO-OP (auto-renew
    off but still entitled until expiry).
4.  Robustness: unknown player → 200 (no retry); anonymous id → 200;
    TEST event → 200; a non-"pro" entitlement grant → no-op.
5.  Idempotency: a duplicated grant leaves plan=pro.
6.  Enforcement-independent: set_plan runs regardless of
    SECA_ENTITLEMENTS_ENFORCED.

Direct async-handler call style (limiter disabled, fake request), matching
test_billing_verify.py.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import llm.seca.billing.router as billing
from llm.seca.auth.models import Base, Player
from llm.seca.billing.router import RevenueCatEvent, RevenueCatWebhook

_SECRET = "rc-shared-secret-abc123"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def no_limiter(monkeypatch):
    from llm.seca.shared_limiter import limiter

    monkeypatch.setattr(limiter, "enabled", False)


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_AUTH", _SECRET)


def _make_player(db, email: str = "sub@test.com", plan: str = "free") -> Player:
    p = Player(email=email, password_hash="x", plan=plan)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _fake_request() -> StarletteRequest:
    return StarletteRequest({
        "type": "http", "method": "POST", "path": "/billing/revenuecat/webhook",
        "headers": [], "client": ("127.0.0.1", 0),
    })


def _call(db, *, etype, app_user_id, entitlement_ids=("pro",), authorization=_SECRET, environment="PRODUCTION"):
    body = RevenueCatWebhook(
        api_version="1.0",
        event=RevenueCatEvent(
            type=etype,
            app_user_id=app_user_id,
            entitlement_ids=list(entitlement_ids) if entitlement_ids is not None else None,
            environment=environment,
        ),
    )
    return asyncio.run(
        billing.revenuecat_webhook(
            body=body, request=_fake_request(), authorization=authorization, db=db
        )
    )


# ---------------------------------------------------------------------------
# 1. Auth
# ---------------------------------------------------------------------------


class TestAuth:
    def test_unconfigured_secret_503(self, db, no_limiter, monkeypatch):
        monkeypatch.delenv("REVENUECAT_WEBHOOK_AUTH", raising=False)
        player = _make_player(db)
        with pytest.raises(HTTPException) as exc:
            _call(db, etype="INITIAL_PURCHASE", app_user_id=player.id)
        assert exc.value.status_code == 503

    def test_wrong_authorization_401(self, db, no_limiter, configured):
        player = _make_player(db)
        with pytest.raises(HTTPException) as exc:
            _call(db, etype="INITIAL_PURCHASE", app_user_id=player.id, authorization="nope")
        assert exc.value.status_code == 401
        db.refresh(player)
        assert player.plan == "free", "a rejected webhook must not flip the plan"

    def test_absent_authorization_401(self, db, no_limiter, configured):
        player = _make_player(db)
        with pytest.raises(HTTPException) as exc:
            _call(db, etype="INITIAL_PURCHASE", app_user_id=player.id, authorization="")
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# 2 + 3. Grant / revoke
# ---------------------------------------------------------------------------


class TestGrantRevoke:
    @pytest.mark.parametrize("etype", ["INITIAL_PURCHASE", "RENEWAL", "UNCANCELLATION", "PRODUCT_CHANGE"])
    def test_grant_flips_to_pro(self, db, no_limiter, configured, etype):
        player = _make_player(db)
        result = _call(db, etype=etype, app_user_id=player.id)
        assert result["action"] == "granted"
        assert result["plan"] == "pro"
        db.refresh(player)
        assert player.plan == "pro"

    def test_expiration_flips_to_free(self, db, no_limiter, configured):
        player = _make_player(db, plan="pro")
        result = _call(db, etype="EXPIRATION", app_user_id=player.id)
        assert result["action"] == "revoked"
        assert result["plan"] == "free"
        db.refresh(player)
        assert player.plan == "free"

    def test_cancellation_is_noop_still_pro(self, db, no_limiter, configured):
        # Auto-renew off, but access holds until EXPIRATION — plan must NOT drop.
        player = _make_player(db, plan="pro")
        result = _call(db, etype="CANCELLATION", app_user_id=player.id)
        assert result["action"] == "ignored"
        db.refresh(player)
        assert player.plan == "pro"

    def test_billing_issue_keeps_pro(self, db, no_limiter, configured):
        player = _make_player(db, plan="pro")
        _call(db, etype="BILLING_ISSUE", app_user_id=player.id)
        db.refresh(player)
        assert player.plan == "pro", "grace period keeps access"

    def test_grant_independent_of_enforcement_flag(self, db, no_limiter, configured, monkeypatch):
        # The plan column tracks reality even when limits aren't enforced.
        monkeypatch.delenv("SECA_ENTITLEMENTS_ENFORCED", raising=False)
        player = _make_player(db)
        _call(db, etype="INITIAL_PURCHASE", app_user_id=player.id)
        db.refresh(player)
        assert player.plan == "pro"


# ---------------------------------------------------------------------------
# 4. Robustness
# ---------------------------------------------------------------------------


class TestRobustness:
    def test_unknown_player_200_no_crash(self, db, no_limiter, configured):
        result = _call(db, etype="INITIAL_PURCHASE", app_user_id="no-such-player-id")
        assert result["action"] == "player_not_found"

    def test_anonymous_app_user_id_skipped(self, db, no_limiter, configured):
        result = _call(
            db, etype="INITIAL_PURCHASE", app_user_id="$RCAnonymousID:deadbeef"
        )
        assert result["action"] == "skipped_anonymous"

    def test_test_event_ignored(self, db, no_limiter, configured):
        player = _make_player(db)
        result = _call(db, etype="TEST", app_user_id=player.id)
        assert result["action"] == "ignored"
        db.refresh(player)
        assert player.plan == "free"

    def test_non_pro_entitlement_grant_is_noop(self, db, no_limiter, configured):
        player = _make_player(db)
        result = _call(
            db, etype="INITIAL_PURCHASE", app_user_id=player.id, entitlement_ids=["some_other"]
        )
        assert result["action"] == "ignored"
        db.refresh(player)
        assert player.plan == "free"

    def test_missing_entitlement_info_treated_as_pro(self, db, no_limiter, configured):
        # Single-entitlement app: an event without entitlement info still grants.
        player = _make_player(db)
        result = _call(db, etype="RENEWAL", app_user_id=player.id, entitlement_ids=None)
        assert result["action"] == "granted"
        db.refresh(player)
        assert player.plan == "pro"


# ---------------------------------------------------------------------------
# 5. Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_duplicate_grant_stays_pro(self, db, no_limiter, configured):
        player = _make_player(db)
        _call(db, etype="INITIAL_PURCHASE", app_user_id=player.id)
        _call(db, etype="INITIAL_PURCHASE", app_user_id=player.id)
        db.refresh(player)
        assert player.plan == "pro"
