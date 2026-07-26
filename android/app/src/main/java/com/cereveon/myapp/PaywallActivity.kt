package com.cereveon.myapp

import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.isVisible
import androidx.core.view.updatePadding
import com.revenuecat.purchases.CustomerInfo
import com.revenuecat.purchases.Offering
import com.revenuecat.purchases.PackageType
import com.revenuecat.purchases.PurchaseParams
import com.revenuecat.purchases.Purchases
import com.revenuecat.purchases.getOfferingsWith
import com.revenuecat.purchases.logInWith
import com.revenuecat.purchases.purchaseWith

/**
 * Cereveon · Atrium · Paywall (handoff screen #11).
 *
 * Reached from SettingsBottomSheet → "Upgrade · Premium" chevron row.
 *
 * Purchase flow (RevenueCat → "pro" entitlement → Pro)
 * ----------------------------------------------------
 *  1. [prepareBilling] pins the RevenueCat identity to the server player
 *     id (`logIn`) so the purchase is attributable, and pre-fetches the
 *     current offering.
 *  2. Subscribe → [startPurchase] launches the RevenueCat purchase for
 *     the selected plan's package (monthly / annual).  RevenueCat
 *     validates the receipt with Play AND acknowledges it — there is no
 *     manual acknowledge / server-verify round-trip anymore.
 *  3. On success, if the customer's ACTIVE entitlements include
 *     [PRO_ENTITLEMENT] ([grantsPro]): cache the plan locally
 *     ([PREF_PLAYER_PLAN]) for instant UI and finish.  The SERVER plan
 *     flips authoritatively out-of-band via the RevenueCat webhook
 *     (POST /billing/revenuecat/webhook), keyed on the app_user_id we set
 *     in step 1.
 *  4. User cancellation is silent; any other failure keeps the paywall
 *     open (the user is never charged for a purchase that didn't clear).
 *
 * The static plan catalogue ([DEFAULT_PLANS] / [DEFAULT_FEATURES] /
 * [recommendedPlanKey]) is display copy: it decides what the tiles SAY.
 * What gets BILLED is the Play product attached to each RevenueCat
 * package in the RevenueCat dashboard, selected here by
 * [packageTypeFor].
 */
class PaywallActivity : AppCompatActivity() {

    private var selectedPlanKey: String = "yearly"
    private lateinit var monthlyTile: FrameLayout
    private lateinit var yearlyTile: FrameLayout
    private lateinit var monthlyPrice: TextView
    private lateinit var yearlyPrice: TextView
    private lateinit var monthlySub: TextView
    private lateinit var yearlySub: TextView

    private val authRepo: AuthRepository by lazy {
        AuthRepository(EncryptedTokenStorage(this))
    }

    /**
     * The current RevenueCat offering, fetched in [onCreate].  Null until
     * the fetch returns (or if it fails / billing is unconfigured); the
     * Subscribe tap ([startPurchase]) handles a null offering gracefully.
     */
    private var currentOffering: Offering? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_paywall)

        // Theme runs edge-to-edge; without this listener the bottom
        // "Subscribe" / "Maybe later" footer would render
        // under the system gesture / nav bar.
        val footer = findViewById<LinearLayout>(R.id.paywallFooter)
        val footerBasePaddingBottom = footer.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(footer) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.updatePadding(bottom = footerBasePaddingBottom + bars.bottom)
            insets
        }

        renderFeatureBullets(findViewById(R.id.paywallFeatures))

        monthlyTile  = findViewById(R.id.paywallPlanMonthly)
        yearlyTile   = findViewById(R.id.paywallPlanYearly)
        monthlyPrice = findViewById(R.id.paywallPlanMonthlyPrice)
        yearlyPrice  = findViewById(R.id.paywallPlanYearlyPrice)
        monthlySub   = findViewById(R.id.paywallPlanMonthlySub)
        yearlySub    = findViewById(R.id.paywallPlanYearlySub)

        // DEFAULT_PLANS is the single source for the tile copy — the XML
        // values are pre-bind placeholders.  What gets BILLED is the Play
        // product attached to the RevenueCat package these tiles select
        // (see packageTypeFor); keep these labels in lock-step with the
        // prices configured there (pinned by PaywallActivityTest).
        DEFAULT_PLANS.firstOrNull { it.key == "monthly" }?.let {
            monthlyPrice.text = it.price
            monthlySub.text = it.sub
        }
        DEFAULT_PLANS.firstOrNull { it.key == "yearly" }?.let {
            yearlyPrice.text = it.price
            yearlySub.text = it.sub
        }

        monthlyTile.setOnClickListener { selectPlan("monthly") }
        yearlyTile.setOnClickListener { selectPlan("yearly") }
        // Initial state matches the design — yearly active by default.
        selectPlan(selectedPlanKey)

        findViewById<Button>(R.id.btnPaywallBegin).setOnClickListener {
            startPurchase()
        }
        findViewById<TextView>(R.id.btnPaywallMaybeLater).setOnClickListener {
            finish()
        }

        prepareBilling()
    }

    // ── RevenueCat billing ───────────────────────────────────────────

    /**
     * Pin the RevenueCat identity to the server player id and pre-fetch
     * the current offering so the Subscribe tap can launch immediately.
     *
     * `logIn(playerId)` makes RevenueCat's `app_user_id` equal the server
     * player UUID — the key the webhook maps on
     * (POST /billing/revenuecat/webhook).  Without it a purchase would be
     * attributed to an anonymous id the server can't resolve, and the
     * plan would never flip server-side.
     */
    private fun prepareBilling() {
        if (!Purchases.isConfigured) {
            // No RevenueCat key in this build (see CereveonApplication) —
            // the paywall renders but can't sell. Guard the tap in
            // startPurchase() rather than crash here.
            Log.w(TAG, "RevenueCat not configured — paywall is display-only")
            return
        }
        val playerId = (authRepo.authState() as? AuthState.Authenticated)
            ?.playerId
            ?.takeIf { it.isNotBlank() }
        if (playerId != null) {
            Purchases.sharedInstance.logInWith(
                playerId,
                onError = { error -> Log.w(TAG, "RevenueCat logIn failed: $error") },
                onSuccess = { _, _ -> },
            )
        } else {
            // Paywall is only reached from a logged-in Settings sheet, so
            // this is defensive — but an unattributable purchase is worse
            // than none, so log it loudly.
            Log.w(TAG, "no player id available — a purchase would be unattributable")
        }
        Purchases.sharedInstance.getOfferingsWith(
            onError = { error ->
                Log.w(TAG, "offerings fetch failed: $error")
                toastOnUi("Plans are unavailable right now — try again shortly")
            },
            onSuccess = { offerings -> currentOffering = offerings.current },
        )
    }

    private fun startPurchase() {
        if (!Purchases.isConfigured) {
            toastOnUi("In-app purchases aren't available on this build")
            return
        }
        val offering = currentOffering
        if (offering == null) {
            toastOnUi("Plans are still loading — try again in a moment")
            return
        }
        val target = packageTypeFor(selectedPlanKey)
        val pkg = offering.availablePackages.firstOrNull { it.packageType == target }
        if (pkg == null) {
            // The offering exists but carries no package of the selected
            // type — a RevenueCat / Play Console misconfiguration, not a
            // client bug. Fail soft.
            toastOnUi("That plan isn't available right now")
            return
        }
        Purchases.sharedInstance.purchaseWith(
            PurchaseParams.Builder(this, pkg).build(),
            onError = { error, userCancelled ->
                if (!userCancelled) {
                    Log.w(TAG, "purchase failed: $error")
                    toastOnUi("Purchase didn't complete — you haven't been charged")
                }
                // USER_CANCELLED is a deliberate dismissal — no toast noise.
            },
            onSuccess = { _, customerInfo -> onPurchaseComplete(customerInfo) },
        )
    }

    private fun onPurchaseComplete(customerInfo: CustomerInfo) {
        if (grantsPro(customerInfo.entitlements.active.keys)) {
            // RevenueCat has already validated + acknowledged the purchase
            // with Play. Cache the plan for instant UI; the server plan
            // flips authoritatively via the RevenueCat webhook.
            cachePlan("pro")
            toastOnUi("Premium active — welcome aboard")
            finish()
        } else {
            // Purchased, but the "pro" entitlement isn't active yet — rare
            // (e.g. a pending / deferred purchase awaiting settlement).
            // Leave the paywall open; it unlocks when the entitlement lands.
            Log.w(TAG, "purchase succeeded but $PRO_ENTITLEMENT entitlement not active")
            toastOnUi("Purchase received — Premium will unlock once it clears")
        }
    }

    private fun cachePlan(plan: String) {
        getSharedPreferences(MainActivity.PREFS_NAME, MODE_PRIVATE).edit()
            .putString(PREF_PLAYER_PLAN, plan)
            .apply()
    }

    private fun toastOnUi(message: String) {
        runOnUiThread { Toast.makeText(this, message, Toast.LENGTH_SHORT).show() }
    }

    // ── Static UI scaffolding (unchanged from the scaffold pass) ─────

    private fun renderFeatureBullets(container: LinearLayout) {
        container.removeAllViews()
        val inflater = LayoutInflater.from(this)
        for (feature in DEFAULT_FEATURES) {
            val row = inflater.inflate(R.layout.item_paywall_bullet, container, false)
            row.findViewById<TextView>(R.id.paywallBulletText).text = feature.text
            // Optional "Free plan: …" contrast — shown only for metered
            // features so the user sees the limit each Pro benefit lifts.
            val free = row.findViewById<TextView>(R.id.paywallBulletFree)
            free.text = feature.free.orEmpty()
            free.isVisible = feature.free != null
            container.addView(row)
        }
    }

    private fun selectPlan(key: String) {
        selectedPlanKey = key
        val isMonthly = key == "monthly"

        monthlyTile.background = ContextCompat.getDrawable(
            this,
            if (isMonthly) R.drawable.atrium_paywall_plan_active
            else R.drawable.atrium_paywall_plan_dormant,
        )
        yearlyTile.background = ContextCompat.getDrawable(
            this,
            if (isMonthly) R.drawable.atrium_paywall_plan_dormant
            else R.drawable.atrium_paywall_plan_active,
        )
        monthlyPrice.setTextColor(
            ContextCompat.getColor(
                this,
                if (isMonthly) R.color.atrium_accent_cyan else R.color.atrium_ink,
            ),
        )
        yearlyPrice.setTextColor(
            ContextCompat.getColor(
                this,
                if (isMonthly) R.color.atrium_ink else R.color.atrium_accent_cyan,
            ),
        )
    }

    /** One subscription plan tile in the paywall's 2-column grid. */
    data class Plan(
        val key: String,
        val title: String,
        val price: String,
        val sub: String,
        val isRecommended: Boolean,
    )

    /**
     * One line in the paywall's Pro feature list.  [free] is the muted
     * "Free plan: …" contrast shown under [text] for metered benefits
     * (null for benefits with no free-tier limit to surface).
     */
    data class Feature(val text: String, val free: String? = null)

    companion object {
        private const val TAG = "PaywallActivity"

        /**
         * SharedPreferences key (in [MainActivity.PREFS_NAME]) caching
         * the last server-confirmed plan ("free" / "pro").  Written by
         * this activity after a successful purchase; read by the limit /
         * upgrade UI.  A UI cache only — the server re-decides entitlement
         * on every metered call.
         */
        const val PREF_PLAYER_PLAN = "player_plan"

        /**
         * The RevenueCat entitlement identifier that unlocks Pro.  Must
         * match the entitlement configured in the RevenueCat dashboard AND
         * the server webhook's `PRO_ENTITLEMENT`
         * (`llm/seca/billing/router.py`) — a drift here would let a
         * purchase succeed on-device while the server never grants Pro.
         */
        const val PRO_ENTITLEMENT = "pro"

        /**
         * Paywall plan key → RevenueCat package type.  The concrete Play
         * product (`pro_monthly` / `pro_yearly`) is attached to each
         * package in the RevenueCat dashboard, not chosen here.  Unknown
         * keys fall back to MONTHLY — defensive only; the activity's click
         * listeners can only produce catalogue keys (pinned by
         * [PaywallActivityTest]).
         */
        fun packageTypeFor(planKey: String): PackageType = when (planKey) {
            "yearly" -> PackageType.ANNUAL
            "monthly" -> PackageType.MONTHLY
            else -> PackageType.MONTHLY
        }

        /**
         * Whether a customer's ACTIVE entitlement ids grant Pro.  Pure over
         * the id set so the host-JVM test can pin the rule without building
         * a RevenueCat `CustomerInfo`.
         */
        fun grantsPro(activeEntitlementIds: Set<String>): Boolean =
            PRO_ENTITLEMENT in activeEntitlementIds

        /**
         * Canonical plan-tile copy, bound to the tiles in [onCreate].
         * Lifted to the companion so unit tests can verify the shape
         * without launching the activity; the "yearly" entry is marked
         * recommended (drives the initial active-tile selection).
         *
         * LAUNCH PRICING (2026-07): €9.99/month; yearly €71.99 (= €6 a
         * month, ~40% off).  Chosen against the MEASURED unit costs —
         * a fully-coached game ≈ $0.0033 in DeepSeek tokens, so a
         * heavy Pro user costs well under €1/month (≥95% gross margin
         * after ~20% VAT + Play's 15% fee).  These labels are DISPLAY
         * copy: what gets billed is the Play product attached to the
         * selected RevenueCat package — change both together, and let
         * Play's per-country price templates localise the actual charge.
         */
        val DEFAULT_PLANS: List<Plan> = listOf(
            Plan(
                key = "monthly",
                title = "Monthly",
                price = "€9.99",
                sub = "per month",
                isRecommended = false,
            ),
            Plan(
                key = "yearly",
                title = "Yearly",
                price = "€71.99",
                sub = "€6 / month",
                isRecommended = true,
            ),
        )

        /**
         * Pro feature list.  Order matters — the metered benefits (whose
         * Free limit is the reason to upgrade) come first, each carrying
         * the muted "Free plan: …" contrast so the user sees BOTH the
         * plan's features and today's limits.
         *
         * Every line + Free contrast must match the live entitlements
         * table (llm/seca/entitlements/service.py, pinned by
         * PaywallActivityTest):
         *   - coached game    free 1/day    → pro unlimited
         *   - chat turn       free 3/day    → pro 30/day
         *   - import analysis free 3/month  → pro 10/day (imported Lichess
         *     games scored by the coach's engine pass)
         * The last two lines are quality benefits with no separate free
         * cap to surface (the game / chat caps above already bound them).
         */
        val DEFAULT_FEATURES: List<Feature> = listOf(
            Feature("Unlimited adaptive games", "Free plan: 1 a day"),
            Feature("30 coach questions a day", "Free plan: 3 a day"),
            Feature("Your Lichess games, coach-analysed", "Free plan: 3 a month"),
            Feature("Full coach hints in every game"),
            Feature("Coach chat grounded in your games"),
        )

        /**
         * Recommended plan key used by the activity's initial tile
         * selection.  Defaults to "yearly" (matching the design)
         * unless every plan's `isRecommended` is false, in which
         * case we fall back to the first plan.
         */
        fun recommendedPlanKey(plans: List<Plan> = DEFAULT_PLANS): String =
            plans.firstOrNull { it.isRecommended }?.key
                ?: plans.firstOrNull()?.key
                ?: "yearly"
    }
}
