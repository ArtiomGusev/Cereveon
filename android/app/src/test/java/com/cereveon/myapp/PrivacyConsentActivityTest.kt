package com.cereveon.myapp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Host-JVM tests for the pure consent helpers on
 * [PrivacyConsentActivity.Companion].  The gate's UI and the prefs
 * round-trip need instrumentation; these pin the two decisions that
 * matter for correctness — whether a user is (re-)gated, and where the
 * policy link points.
 */
class PrivacyConsentActivityTest {

    @Test
    fun `CONSENT_VERSION is a real positive version`() {
        // 0 is the "never accepted" sentinel in the stored pref, so the
        // required version must be >= 1 or the gate could never trigger.
        assertTrue(PrivacyConsentActivity.CONSENT_VERSION >= 1)
    }

    @Test
    fun `hasAcceptedVersion is true only at or above the current version`() {
        val current = PrivacyConsentActivity.CONSENT_VERSION
        assertTrue(
            "the current version counts as accepted",
            PrivacyConsentActivity.hasAcceptedVersion(current),
        )
        assertTrue(
            "a newer accepted version still counts",
            PrivacyConsentActivity.hasAcceptedVersion(current + 1),
        )
        assertFalse(
            "never-accepted (0) must gate",
            PrivacyConsentActivity.hasAcceptedVersion(0),
        )
        assertFalse(
            "an older accepted version must re-gate after a policy bump",
            PrivacyConsentActivity.hasAcceptedVersion(current - 1),
        )
    }

    @Test
    fun `privacyPolicyUrl appends the privacy path`() {
        assertEquals(
            "https://cereveon.com/privacy",
            PrivacyConsentActivity.privacyPolicyUrl("https://cereveon.com"),
        )
    }

    @Test
    fun `privacyPolicyUrl trims a trailing slash so it never doubles`() {
        // COACH_API_BASE may carry a trailing slash from config; the URL
        // must resolve to one clean /privacy, never //privacy.
        assertEquals(
            "https://cereveon.com/privacy",
            PrivacyConsentActivity.privacyPolicyUrl("https://cereveon.com/"),
        )
    }

    @Test
    fun `privacyPolicyUrl works against a local dev base`() {
        // Debug builds can point COACH_API_BASE at the emulator's host
        // loopback; the policy is served by the same backend at /privacy.
        assertEquals(
            "http://10.0.2.2:8000/privacy",
            PrivacyConsentActivity.privacyPolicyUrl("http://10.0.2.2:8000"),
        )
    }
}
