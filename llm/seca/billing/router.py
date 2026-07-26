"""Google Play billing verification — the Pro-activation surface.

``POST /billing/google/verify``: the Android client completes a Play
Billing purchase, then posts the ``purchase_token`` + ``product_id``
here.  The server verifies the token against the Google Play Developer
API (``purchases.subscriptionsv2.get``) using service-account
credentials from env and, only on an entitled verdict, flips the
player's plan through ``entitlements.set_plan``.  The client's claim is
never trusted — a forged or replayed token flips nothing.

Verification seam
-----------------
``_verify_google_purchase(purchase_token, product_id)`` is the single
injectable seam: tests monkeypatch it with a fake verdict, so CI makes
no external calls; production resolves credentials from env at call
time.  When the three ``GOOGLE_PLAY_*`` vars are unset the seam raises
``BillingNotConfiguredError`` → HTTP 503 — shipping this router without
credentials is safe and LOUD, never fake-success.

Entitled states
---------------
``SUBSCRIPTION_STATE_ACTIVE`` and ``_IN_GRACE_PERIOD`` are obviously
entitled.  ``_CANCELED`` is too: in subscriptionsv2 it means auto-renew
was turned off but the paid period has not ended — access runs until
expiry, at which point the state becomes ``_EXPIRED`` (the terminal
loss).  Expiry-driven automatic downgrade is the RTDN follow-up, not
this endpoint.
"""

# Slowapi reads ``request: Request`` from each rate-limited handler's
# signature even when the handler body doesn't reference it.  Pylint
# flags every such parameter as unused; disabling the rule file-wide
# rather than per-handler keeps the diff stable as new endpoints land.
# pylint: disable=unused-argument

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, field_validator

from llm.seca.auth.models import Player
from llm.seca.auth.router import get_current_player, get_db
from llm.seca.entitlements import service as entitlements
from llm.seca.shared_limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

#: Play products this server recognises → the plan they grant.  Must
#: stay in lock-step with the Android paywall's product catalogue
#: (PaywallActivity PLAY_PRODUCT_IDS: monthly → pro_monthly, yearly →
#: pro_yearly) and the ``upgrade.product`` hint in the chat 402 body
#: (API_CONTRACTS.md §5).  Both products grant the same "pro" plan —
#: the billing period is a Play-side pricing concern, not an
#: entitlement distinction.
KNOWN_PRODUCTS: dict[str, str] = {"pro_monthly": "pro", "pro_yearly": "pro"}

#: subscriptionsv2 states that still carry entitlement — see the module
#: docstring for the CANCELED rationale.
_ENTITLED_STATES = frozenset(
    {
        "SUBSCRIPTION_STATE_ACTIVE",
        "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
        "SUBSCRIPTION_STATE_CANCELED",
    }
)

_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_ANDROID_PUBLISHER_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
_HTTP_TIMEOUT_SECONDS = 15


class BillingNotConfiguredError(RuntimeError):
    """GOOGLE_PLAY_* service-account env vars are absent."""


class BillingUpstreamError(RuntimeError):
    """Google's OAuth or Play API answered abnormally (network, 5xx, parse)."""


@dataclass(frozen=True)
class PurchaseVerdict:
    """Outcome of one token verification."""

    entitled: bool
    state: str


class VerifyGooglePurchaseRequest(BaseModel):
    """Body of POST /billing/google/verify."""

    purchase_token: str
    product_id: str

    @field_validator("purchase_token")
    @classmethod
    def validate_purchase_token(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("purchase_token must not be empty")
        # Play tokens are opaque base64-ish strings well under this cap;
        # the bound is a defensive ceiling, not a format claim.
        if len(v) > 600:
            raise ValueError("purchase_token too long (max 600 chars)")
        if any(c < "\x20" for c in v):
            raise ValueError("purchase_token contains control characters")
        return v

    @field_validator("product_id")
    @classmethod
    def validate_product_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("product_id must not be empty")
        if len(v) > 64:
            raise ValueError("product_id too long (max 64 chars)")
        return v


def _sa_credentials() -> tuple[str, str, str]:
    """(package_name, client_email, private_key_pem) from env, or raise.

    ``GOOGLE_PLAY_SA_PRIVATE_KEY`` commonly arrives with literal ``\\n``
    sequences when pasted into a .env file; normalise them back to real
    newlines so the PEM parses.
    """
    package = os.getenv("GOOGLE_PLAY_PACKAGE_NAME", "").strip()
    email = os.getenv("GOOGLE_PLAY_SA_EMAIL", "").strip()
    key = os.getenv("GOOGLE_PLAY_SA_PRIVATE_KEY", "").strip()
    if not package or not email or not key:
        raise BillingNotConfiguredError(
            "GOOGLE_PLAY_PACKAGE_NAME / GOOGLE_PLAY_SA_EMAIL / "
            "GOOGLE_PLAY_SA_PRIVATE_KEY must all be set for purchase verification"
        )
    return package, email, key.replace("\\n", "\n")


def _google_access_token(email: str, private_key_pem: str) -> str:
    """Service-account JWT-bearer grant → short-lived OAuth access token.

    Signed RS256 via python-jose (its pure-python ``rsa`` backend — no
    ``cryptography`` wheel dependency), exchanged at Google's token
    endpoint.  Raises ``BillingUpstreamError`` on any transport or
    protocol failure.
    """
    from jose import jwt as _jose_jwt  # noqa: PLC0415  # billing-only dependency path

    now = int(time.time())
    assertion = _jose_jwt.encode(
        {
            "iss": email,
            "scope": _ANDROID_PUBLISHER_SCOPE,
            "aud": _OAUTH_TOKEN_URL,
            "iat": now,
            "exp": now + 600,
        },
        private_key_pem,
        algorithm="RS256",
    )
    try:
        resp = httpx.post(
            _OAUTH_TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        token = resp.json().get("access_token", "")
    except httpx.HTTPError as exc:
        raise BillingUpstreamError(f"OAuth token exchange failed: {exc}") from exc
    except ValueError as exc:  # json decode
        raise BillingUpstreamError("OAuth token exchange returned non-JSON") from exc
    if not token:
        raise BillingUpstreamError("OAuth token exchange returned no access_token")
    return token


def _verify_google_purchase(purchase_token: str, product_id: str) -> PurchaseVerdict:
    """The injectable verification seam — ``purchases.subscriptionsv2.get``.

    Returns a ``PurchaseVerdict`` for answerable outcomes (including
    Google explicitly rejecting the token: 400/404/410 → not entitled);
    raises ``BillingNotConfiguredError`` / ``BillingUpstreamError`` for
    states where no verdict exists.  ``product_id`` is accepted for
    parity with future per-product checks; entitlement is decided by
    the subscription state Google reports for the token.
    """
    package, email, key = _sa_credentials()
    access_token = _google_access_token(email, key)
    url = (
        "https://androidpublisher.googleapis.com/androidpublisher/v3/"
        f"applications/{package}/purchases/subscriptionsv2/tokens/{purchase_token}"
    )
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise BillingUpstreamError(f"Play API request failed: {exc}") from exc
    if resp.status_code in (400, 404, 410):
        # Google affirmatively rejected the token — a verdict, not an outage.
        return PurchaseVerdict(entitled=False, state=f"rejected_http_{resp.status_code}")
    if resp.status_code != 200:
        raise BillingUpstreamError(f"Play API answered HTTP {resp.status_code}")
    try:
        state = resp.json().get("subscriptionState", "SUBSCRIPTION_STATE_UNSPECIFIED")
    except ValueError as exc:
        raise BillingUpstreamError("Play API returned non-JSON") from exc
    return PurchaseVerdict(entitled=state in _ENTITLED_STATES, state=state)


@router.post("/google/verify")
@limiter.limit("10/minute")
async def verify_google_purchase(
    req: VerifyGooglePurchaseRequest,
    request: Request,
    player=Depends(get_current_player),
    db=Depends(get_db),
):
    """Verify a Play purchase token and activate the purchased plan.

    Owner-scoped by construction: the plan flip targets the
    authenticated ``player`` only — the body carries no player
    identity.  ``entitlements.set_plan`` re-raises after rollback on
    persistence failure, so a 200 is only ever returned for a landed
    flip (no fake success), and any DB failure surfaces as a 500 with
    the plan unchanged.
    """
    plan = KNOWN_PRODUCTS.get(req.product_id)
    if plan is None:
        raise HTTPException(status_code=400, detail="unknown product_id")

    try:
        verdict = await asyncio.to_thread(
            _verify_google_purchase, req.purchase_token, req.product_id
        )
    except BillingNotConfiguredError as exc:
        logger.warning("billing verify called but not configured: %s", exc)
        raise HTTPException(
            status_code=503, detail="purchase verification not configured"
        ) from exc
    except BillingUpstreamError as exc:
        logger.warning("billing verify upstream failure: %s", exc)
        raise HTTPException(
            status_code=502, detail="purchase verification temporarily unavailable"
        ) from exc

    if not verdict.entitled:
        # A real verdict from Google that this token carries no
        # entitlement — expired, refunded, revoked, or never real.
        raise HTTPException(
            status_code=402, detail=f"purchase not active ({verdict.state})"
        )

    entitlements.set_plan(db, player, plan)
    logger.info(
        "billing: player %s activated plan %s via %s (state %s)",
        player.id,
        plan,
        req.product_id,
        verdict.state,
    )
    return {"plan": plan, "product_id": req.product_id, "state": verdict.state}


# ---------------------------------------------------------------------------
# RevenueCat webhook — the production subscription source of truth
# ---------------------------------------------------------------------------
#
# The app purchases through the RevenueCat SDK (not raw Play Billing), and
# RevenueCat validates receipts with the stores.  It then POSTs lifecycle
# events here, which flip ``Player.plan`` so the server-side entitlements
# enforcement stays authoritative — a modified client cannot fake pro, and
# downgrade-on-expiry is handled automatically (no RTDN wiring needed).
#
# This SUPERSEDES ``POST /billing/google/verify`` above (direct
# purchase-token verification): with RevenueCat the Play service-account
# key lives in the RevenueCat dashboard, not this server's env.  The
# verify endpoint is kept for back-compat / non-RevenueCat clients but is
# no longer the live path.

#: The RevenueCat entitlement identifier that grants the pro plan.  Must
#: match the entitlement configured in the RevenueCat dashboard.
PRO_ENTITLEMENT = "pro"

#: RevenueCat event types that mean the pro entitlement is now ACTIVE.
_RC_GRANT_EVENTS = frozenset(
    {
        "INITIAL_PURCHASE",
        "RENEWAL",
        "UNCANCELLATION",
        "PRODUCT_CHANGE",
        "NON_RENEWING_PURCHASE",
        "SUBSCRIPTION_EXTENDED",
    }
)
#: Event types that mean it ENDED.  ``CANCELLATION`` is deliberately NOT
#: here — it only turns off auto-renew; access holds until ``EXPIRATION``.
#: ``BILLING_ISSUE`` (grace period) and ``SUBSCRIPTION_PAUSED`` also keep
#: the plan and self-correct via a later EXPIRATION if never resolved.
_RC_REVOKE_EVENTS = frozenset({"EXPIRATION"})


class RevenueCatEvent(BaseModel):
    """The ``event`` object of a RevenueCat webhook (fields we use)."""

    type: str = ""
    app_user_id: str = ""
    entitlement_ids: list[str] | None = None
    entitlement_id: str | None = None  # legacy singular form
    environment: str = ""
    store: str = ""


class RevenueCatWebhook(BaseModel):
    """RevenueCat webhook envelope."""

    event: RevenueCatEvent
    api_version: str = ""


def _event_mentions_pro(ev: RevenueCatEvent) -> bool:
    """Whether the event concerns the pro entitlement.

    Uses ``entitlement_ids`` (or the legacy singular ``entitlement_id``).
    When the event carries no entitlement info (some types omit it),
    treat it as relevant — this app has a single entitlement.
    """
    ids = ev.entitlement_ids
    if ids is None:
        ids = [] if ev.entitlement_id is None else [ev.entitlement_id]
    return not ids or PRO_ENTITLEMENT in ids


@router.post("/revenuecat/webhook")
@limiter.limit("120/minute")
async def revenuecat_webhook(
    body: RevenueCatWebhook,
    request: Request,
    authorization: str = Header(default=""),
    db=Depends(get_db),
):
    """RevenueCat webhook — authoritative subscription-state updates.

    RevenueCat POSTs purchase / renewal / expiry / cancellation events.
    We verify the shared-secret ``Authorization`` header (configured in
    the RevenueCat dashboard AND in ``REVENUECAT_WEBHOOK_AUTH``), map
    ``event.app_user_id`` — which the app sets to the server player id —
    to the player, and flip ``Player.plan`` via ``entitlements.set_plan``.

    Auth posture: ``REVENUECAT_WEBHOOK_AUTH`` unset → 503 (fail closed);
    header mismatch/absent → 401.  set_plan is independent of
    ``SECA_ENTITLEMENTS_ENFORCED`` — the plan column always tracks
    reality so enforcement (whenever on) sees the right tier.

    Returns 200 for any authenticated, well-formed event — including
    no-op event types and unmatched players — so RevenueCat does not
    retry a permanent condition.  Only a DB write failure returns 500,
    which RevenueCat will retry.
    """
    secret = os.getenv("REVENUECAT_WEBHOOK_AUTH", "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="revenuecat webhook not configured")
    # Constant-time compare so a mismatch can't be timed byte-by-byte.
    if not hmac.compare_digest(authorization, secret):
        raise HTTPException(status_code=401, detail="invalid webhook authorization")

    ev = body.event
    etype = ev.type.upper()

    if etype in _RC_GRANT_EVENTS and _event_mentions_pro(ev):
        target = entitlements.PLAN_PRO
    elif etype in _RC_REVOKE_EVENTS and _event_mentions_pro(ev):
        target = entitlements.PLAN_FREE
    else:
        logger.info("revenuecat webhook: no-op (type=%s)", etype)
        return {"status": "ok", "action": "ignored", "type": etype}

    uid = ev.app_user_id.strip()
    if not uid or uid.startswith("$RCAnonymousID:"):
        # The SDK fired an event before it was configured with the player
        # id.  Nothing to map — the identified alias event that follows
        # will carry the real id.
        logger.warning("revenuecat webhook: unmappable app_user_id (type=%s)", etype)
        return {"status": "ok", "action": "skipped_anonymous"}

    player = db.query(Player).filter(Player.id == uid).one_or_none()
    if player is None:
        logger.warning("revenuecat webhook: no player for app_user_id=%s (type=%s)", uid[:12], etype)
        return {"status": "ok", "action": "player_not_found"}

    try:
        entitlements.set_plan(db, player, target)
    except Exception as exc:  # noqa: BLE001
        # Persisting failed — return 500 so RevenueCat retries the event.
        logger.warning("revenuecat webhook: set_plan failed (%s); will be retried", exc)
        raise HTTPException(status_code=500, detail="could not persist plan") from exc

    logger.info(
        "revenuecat webhook: player %s -> %s (type=%s, env=%s)",
        uid[:12],
        target,
        etype,
        ev.environment,
    )
    return {
        "status": "ok",
        "action": "granted" if target == entitlements.PLAN_PRO else "revoked",
        "plan": target,
    }
