# Billing Go-Live — Wiring the Paywall to Your Bank Account

This is the operator checklist for turning the **already-built** paywall
into real subscription revenue. No app or server code needs to change to
go live — everything below is Google Play Console configuration plus a
handful of server environment variables.

> **Money path.** A user taps *Subscribe* → Google Play charges their card
> → Google collects the money → Google pays out (monthly, minus its ~15 %
> subscription service fee) to the **bank account attached to your Play
> payments profile**. "Wiring the paywall to a bank account" therefore
> means: finish the Play Console commerce setup so Google can charge users
> and remit payouts to you. Nothing routes money through our own servers —
> our server only *verifies* purchases and flips the plan.

---

## A. What the code already does (do not rebuild)

| Piece | Where | Status |
|-------|-------|--------|
| Paywall UI + Play Billing purchase flow | `android/.../PaywallActivity.kt` | ✅ complete (Play Billing lib `billing-ktx:7.1.1`) |
| Products offered | `PaywallActivity.PLAY_PRODUCT_IDS` → `pro_monthly`, `pro_yearly` | ✅ |
| Display pricing | `PaywallActivity.DEFAULT_PLANS` → €9.99 / mo, €71.99 / yr | ✅ |
| Client → server verify | `android/.../BillingApiClient.kt` → `POST /billing/google/verify` | ✅ |
| Server verify + plan flip | `llm/seca/billing/router.py` (`purchases.subscriptionsv2.get`) | ✅ code ready, needs credentials |
| Free/Pro enforcement | `llm/seca/entitlements/service.py::LIMITS` | ✅ (gated by `SECA_ENTITLEMENTS_ENFORCED`) |

**Trust posture already baked in:** the server is the sole entitlement
authority. A local purchase result is never trusted; the client acknowledges
the purchase *only after* the server returns `plan == "pro"`. An
unacknowledged purchase is auto-refunded by Play, so a dead/unconfigured
server can never silently keep a user's money (`PaywallActivity.kt` step 4).

---

## B. Your steps in Play Console (the "outside settings")

Do these in order. Steps **1–2** are the literal bank-account wiring;
**3–5** make purchases actually work and get verified.

### 1. Payments profile + bank account  ← the bank wiring
- Play Console → **Setup → Payments profile** (a.k.a. *Payments & subscriptions → Payment settings*).
- Create / link a Google **payments (merchant) profile** for the developer account.
- Add your **business/legal details**, **tax information**, and a **bank account** for payouts, then complete Google's verification (micro-deposit or instant, depending on country/bank).
- Until this profile is active and verified, paid products cannot be sold at all.

### 2. Confirm the app is a paid-capable, signed release
- Upload a **signed release AAB** (`applicationId = com.cereveon.myapp`) to at least the **Internal testing** track. Products are only purchasable from an uploaded build signed with the release/upload key.
  - Signing is wired via `MYAPP_UPLOAD_*` Gradle properties (`android/app/build.gradle.kts`); release builds run R8 (`isMinifyEnabled = true`).
- Add **license testers** (Play Console → *Setup → License testing*) so you can test purchases without being charged.

### 3. Create the two subscription products
Play Console → **Monetize → Products → Subscriptions**. Create exactly these
IDs (they must match the code and the server allow-list verbatim):

| Product ID | Base plan | Price | Must match |
|------------|-----------|-------|------------|
| `pro_monthly` | monthly, auto-renewing | **€9.99 / month** | `PaywallActivity.PLAY_PRODUCT_IDS` + `DEFAULT_PLANS` |
| `pro_yearly`  | yearly, auto-renewing  | **€71.99 / year**  | same |

- Both grant the same server plan (`"pro"`) — the billing period is a Play-side pricing concern only (`router.py::KNOWN_PRODUCTS`).
- Let Play's per-country price templates localise from the EUR anchor.
- If you change an ID or price, change it in **both** the Console **and**
  `PaywallActivity` (pinned by `PaywallActivityTest`) — they must not drift.

### 4. Create the verification service account
The server verifies purchase tokens against the Google Play Developer API,
which needs a service account:
- In the **linked Google Cloud project**, create a service account and enable the **Google Play Android Developer API**.
- Download its **JSON key**.
- In Play Console → **Users & permissions**, invite the service account's email and grant it access to this app with at least **"View financial data, orders, and cancellation survey responses"** (and manage-orders if you later add refunds/RTDN).
- Note the SA **email** and **private key** — these become server env below.

### 5. Roll the tester → production track
Once an internal-track test purchase verifies end-to-end (section D), promote
the release through Closed → Open/Production per your launch plan.

---

## C. Server environment (my "local edits" prepared these)

Set these in the production env file (`/opt/chesscoach/.env.prod` on the
Hetzner host — see `docs/DEPLOYMENT.md`). The template
(`.env.prod.example`) now documents all four:

```bash
GOOGLE_PLAY_PACKAGE_NAME=com.cereveon.myapp
GOOGLE_PLAY_SA_EMAIL=<service-account>@<project>.iam.gserviceaccount.com
GOOGLE_PLAY_SA_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n
SECA_ENTITLEMENTS_ENFORCED=true
```

- `GOOGLE_PLAY_PACKAGE_NAME` **must** be `com.cereveon.myapp` (the renamed
  applicationId, PR #433). The old `ai.chesscoach.app` would make every
  verify hit the wrong package and 402.
- `GOOGLE_PLAY_SA_PRIVATE_KEY` may keep the literal `\n` escapes from the
  JSON key file — the server normalises them (`router.py::_sa_credentials`).
- With **any** `GOOGLE_PLAY_*` var missing, `/billing/google/verify`
  answers **503** ("not configured") — safe, never fake-success.
- `SECA_ENTITLEMENTS_ENFORCED=true` is what makes free-tier limits bite; the
  paywall is pointless without it.
- After editing `.env.prod`, **recreate** the API container (a bare
  `compose restart` does not reload `env_file`).

---

## D. Verify end-to-end before going wide

1. Sign in on a device as a **license tester**, open the paywall, buy `pro_monthly`.
2. Expect: *"Premium active — welcome aboard"* toast, and the paywall closes.
3. Server side, confirm `POST /billing/google/verify` returned **200** with
   `{"plan":"pro", ...}` (grep the API logs for `billing: player ... activated plan pro`).
4. Confirm the free limits now lift for that account (e.g. a 2nd coached
   game in a day no longer degrades / 402s).
5. Negative check: a fresh account still hits the free caps, proving
   `SECA_ENTITLEMENTS_ENFORCED` is on.

Verdict codes to expect from the endpoint:
`200` activated · `402` Google says token not entitled · `502` verification
upstream unavailable (retry) · `503` server missing `GOOGLE_PLAY_*`.

---

## E. Known follow-ups (not blockers for taking payment)

- **Auto-downgrade on cancel/expiry (RTDN).** The verify endpoint *activates*
  Pro but does not yet subscribe to Real-Time Developer Notifications, so an
  expired/refunded subscriber is not automatically downgraded server-side
  (`router.py` module docstring — "Expiry-driven automatic downgrade is the
  RTDN follow-up, not this endpoint"). `CANCELED` correctly stays entitled
  until the paid period ends; only post-expiry downgrade is manual/pending
  until RTDN lands. Track this before scaling paid volume.
- **iOS parity.** This runbook is Android/Play only; iOS StoreKit billing is
  a separate, not-yet-built surface.

---

## Quick reference — files touched to prepare this

| File | Change |
|------|--------|
| `.env.prod.example` | Added `GOOGLE_PLAY_*` + `SECA_ENTITLEMENTS_ENFORCED` to the prod template |
| `.env.example` | Fixed stale `GOOGLE_PLAY_PACKAGE_NAME` → `com.cereveon.myapp`; refreshed entitlements guidance |
| `android/app/proguard-rules.pro` | R8 keep-rules for JSON API models re-pointed `ai.chesscoach.app` → `com.cereveon.myapp` (PR #433 missed this; would break the release build's models) |
| `docs/BILLING_GO_LIVE.md` | This runbook |
