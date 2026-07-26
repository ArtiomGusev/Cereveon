package com.cereveon.myapp

import com.revenuecat.purchases.PackageType
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-Kotlin tests for the static helpers + canonical defaults on
 * [PaywallActivity.Companion].  Run on the host JVM without
 * instrumentation since the helpers don't touch the Android framework.
 *
 * Invariants pinned
 * -----------------
 *  1. DEFAULT_PLANS contains exactly one recommended entry.
 *  2. DEFAULT_PLANS keys match the activity's hard-coded selection
 *     keys ("monthly" / "yearly") so the tap → selectPlan() path
 *     can never miss.
 *  3. DEFAULT_FEATURES has 5 entries; the metered ones carry a
 *     "Free plan: …" contrast, and Lichess analysis is advertised.
 *  4. recommendedPlanKey returns "yearly" by default so the activity's
 *     initial active-tile state matches the design.
 *  5. recommendedPlanKey falls back to the first plan when no entry
 *     is marked recommended (defensive — a misconfigured rollout
 *     mustn't render the activity with no active tile).
 *  6. recommendedPlanKey falls back to "yearly" string literal when
 *     the list is empty (extreme edge — keeps the call infallible).
 */
class PaywallActivityTest {

    @Test
    fun `DEFAULT_PLANS has exactly one recommended entry`() {
        val recommended = PaywallActivity.DEFAULT_PLANS.filter { it.isRecommended }
        assertEquals(
            "exactly one plan must be marked recommended so the initial " +
                "active tile is unambiguous",
            1, recommended.size,
        )
    }

    @Test
    fun `DEFAULT_PLANS keys match the activity's tap selection keys`() {
        val keys = PaywallActivity.DEFAULT_PLANS.map { it.key }.toSet()
        // selectPlan("monthly") and selectPlan("yearly") are the only
        // values the click listeners pass; if these diverge the activity
        // silently does nothing on tap.
        assertEquals(setOf("monthly", "yearly"), keys)
    }

    @Test
    fun `DEFAULT_PLANS recommended is yearly`() {
        val recommended = PaywallActivity.DEFAULT_PLANS.first { it.isRecommended }
        assertEquals("yearly", recommended.key)
        assertEquals("Yearly", recommended.title)
    }

    @Test
    fun `DEFAULT_PLANS carry the launch pricing`() {
        // Launch pricing (2026-07), chosen against the MEASURED unit
        // economics: a fully-coached game ≈ $0.0033 in DeepSeek tokens,
        // so €9.99/mo carries a ≥95% gross margin after VAT + Play fee.
        // These are DISPLAY labels bound to the tiles in onCreate; the
        // Play Console products behind PLAY_PRODUCT_IDS do the billing —
        // when the Console price changes, change this together with it.
        val monthly = PaywallActivity.DEFAULT_PLANS.first { it.key == "monthly" }
        val yearly = PaywallActivity.DEFAULT_PLANS.first { it.key == "yearly" }
        assertEquals("€9.99", monthly.price)
        assertEquals("per month", monthly.sub)
        assertEquals("€71.99", yearly.price)
        assertEquals("€6 / month", yearly.sub)
    }

    @Test
    fun `DEFAULT_PLANS entries have non-blank prices and subs`() {
        for (plan in PaywallActivity.DEFAULT_PLANS) {
            assertNotNull(plan.price)
            assertNotNull(plan.sub)
            assertTrue("plan ${plan.key} price must be non-blank", plan.price.isNotBlank())
            assertTrue("plan ${plan.key} sub must be non-blank",   plan.sub.isNotBlank())
        }
    }

    @Test
    fun `DEFAULT_FEATURES lists the plan's five bullets`() {
        assertEquals(5, PaywallActivity.DEFAULT_FEATURES.size)
        for (feature in PaywallActivity.DEFAULT_FEATURES) {
            assertTrue("feature text must be non-blank", feature.text.isNotBlank())
            feature.free?.let {
                assertTrue("free contrast, when present, must be non-blank", it.isNotBlank())
            }
        }
    }

    @Test
    fun `DEFAULT_FEATURES surface the free limits and Lichess analysis`() {
        // The paywall must show BOTH the Pro features AND today's free
        // limits, matching the entitlements table (games 1/day, chat
        // 3/day, import-analysis 3/month).  Drift here misstates the plan
        // to a paying user.
        val features = PaywallActivity.DEFAULT_FEATURES
        val freeLines = features.mapNotNull { it.free }
        assertTrue("games free limit (1 a day) shown", freeLines.any { it.contains("1 a day") })
        assertTrue("chat free limit (3 a day) shown", freeLines.any { it.contains("3 a day") })
        assertTrue(
            "Lichess-analysis free limit (3 a month) shown",
            freeLines.any { it.contains("3 a month") },
        )
        assertTrue(
            "coach analysis of Lichess games must be advertised",
            features.any { it.text.contains("Lichess", ignoreCase = true) },
        )
    }

    @Test
    fun `recommendedPlanKey returns yearly by default`() {
        assertEquals("yearly", PaywallActivity.recommendedPlanKey())
    }

    @Test
    fun `recommendedPlanKey falls back to first plan when none recommended`() {
        // Defensive fallback — a misconfigured rollout (no recommended
        // flag set anywhere) shouldn't strand the activity with no
        // active tile.  First plan in the list wins.
        val plans = listOf(
            PaywallActivity.Plan("a", "A", "$1", "x", isRecommended = false),
            PaywallActivity.Plan("b", "B", "$2", "y", isRecommended = false),
        )
        assertEquals("a", PaywallActivity.recommendedPlanKey(plans))
    }

    @Test
    fun `recommendedPlanKey falls back to yearly literal for empty list`() {
        // Extreme edge — a backend that returns an empty plan catalog
        // (network timeout, A/B test misfire) shouldn't crash the
        // initial selectPlan() call.
        assertEquals("yearly", PaywallActivity.recommendedPlanKey(emptyList()))
    }

    // ── RevenueCat wiring ────────────────────────────────────────────

    @Test
    fun `packageTypeFor maps plan keys to RevenueCat package types`() {
        // The tap → selectPlan() keys must resolve to the standard
        // subscription package types RevenueCat exposes on the offering;
        // startPurchase() picks the offering package whose packageType
        // equals this.
        assertEquals(PackageType.MONTHLY, PaywallActivity.packageTypeFor("monthly"))
        assertEquals(PackageType.ANNUAL, PaywallActivity.packageTypeFor("yearly"))
    }

    @Test
    fun `packageTypeFor falls back to MONTHLY for unknown keys`() {
        // Defensive only — the click listeners can only emit catalogue
        // keys, but an unknown key must still resolve to a real package
        // type (never UNKNOWN) so the purchase can proceed.
        assertEquals(PackageType.MONTHLY, PaywallActivity.packageTypeFor("lifetime"))
        assertEquals(PackageType.MONTHLY, PaywallActivity.packageTypeFor(""))
    }

    @Test
    fun `packageTypeFor resolves every plan catalogue key to a real type`() {
        // Every selectable tile must map to a concrete RevenueCat package
        // type; a key that resolved to UNKNOWN would strand the purchase.
        for (plan in PaywallActivity.DEFAULT_PLANS) {
            val type = PaywallActivity.packageTypeFor(plan.key)
            assertTrue(
                "plan ${plan.key} must resolve to MONTHLY or ANNUAL, got $type",
                type == PackageType.MONTHLY || type == PackageType.ANNUAL,
            )
        }
    }

    @Test
    fun `PRO_ENTITLEMENT matches the server and dashboard entitlement id`() {
        // Lock-step with PRO_ENTITLEMENT in llm/seca/billing/router.py and
        // the entitlement configured in the RevenueCat dashboard — a drift
        // lets a purchase succeed on-device while the server never grants
        // Pro (the webhook's _event_mentions_pro would ignore it).
        assertEquals("pro", PaywallActivity.PRO_ENTITLEMENT)
    }

    @Test
    fun `grantsPro is true only when pro is among the active entitlements`() {
        // The activity activates Pro from customerInfo.entitlements.active
        // — only an ACTIVE "pro" entitlement counts.
        assertTrue(PaywallActivity.grantsPro(setOf("pro")))
        assertTrue(
            "extra active entitlements alongside pro still grant Pro",
            PaywallActivity.grantsPro(setOf("pro", "some_other")),
        )
        assertFalse(
            "a different entitlement must not activate Pro",
            PaywallActivity.grantsPro(setOf("some_other")),
        )
        assertFalse(
            "no active entitlements must not activate Pro",
            PaywallActivity.grantsPro(emptySet()),
        )
    }

    @Test
    fun `PREF_PLAYER_PLAN key is stable`() {
        // Written by PaywallActivity after a verified purchase; read by
        // the limit/upgrade UI (client-reaction follow-up).  Renaming it
        // would silently orphan cached Pro state on existing installs.
        assertEquals("player_plan", PaywallActivity.PREF_PLAYER_PLAN)
    }
}
