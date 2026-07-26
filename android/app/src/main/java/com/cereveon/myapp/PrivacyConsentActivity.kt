package com.cereveon.myapp

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.checkbox.MaterialCheckBox

/**
 * Cereveon · Atrium · Privacy consent gate.
 *
 * Shown once, right after sign-up — and to any authenticated user who
 * has not yet accepted the current policy version — BEFORE onboarding.
 * The user opens the hosted Privacy Policy and ticks "I agree"; only
 * then does Continue enable and route onward.
 *
 * Non-bypassable: [LoginActivity] routes EVERY post-auth entry (register,
 * login, Lichess, already-logged-in cold start) through
 * [PrivacyConsentActivity.isAccepted].  The gate is launched as the task
 * root (CLEAR_TASK), so Back exits the app and the next launch re-shows
 * it until consent is recorded — killing the app can't skip it.
 *
 * Consent is stored as an accepted policy VERSION ([CONSENT_VERSION]) in
 * the shared app prefs, so a material policy change can re-prompt by
 * bumping the constant.  It is a local acknowledgement record (the policy
 * informs; it is not a processing-consent contract) — consistent with how
 * [OnboardingActivity.isCompleted] tracks the calibration step.
 */
class PrivacyConsentActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_privacy_consent)

        findViewById<TextView>(R.id.privacyPolicyLink).setOnClickListener {
            openPrivacyPolicy()
        }

        val continueButton = findViewById<Button>(R.id.btnPrivacyConsentContinue)
        findViewById<MaterialCheckBox>(R.id.privacyConsentCheckbox)
            .setOnCheckedChangeListener { _, isChecked ->
                continueButton.isEnabled = isChecked
            }

        continueButton.setOnClickListener {
            markAccepted(this)
            routeOnward()
        }
    }

    /** Open the hosted policy in the system browser (same pattern as the
     *  Lichess sign-in hand-off).  Failing soft keeps the gate usable on
     *  a device with no browser — the user can still tick + continue. */
    private fun openPrivacyPolicy() {
        val url = privacyPolicyUrl(BuildConfig.COACH_API_BASE)
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (_: ActivityNotFoundException) {
            Toast.makeText(this, "No browser available to open the policy", Toast.LENGTH_SHORT).show()
        }
    }

    /**
     * Continue past the gate to the same destination [LoginActivity]
     * would have chosen: onboarding for a fresh account, Home once
     * calibration is complete.  CLEAR_TASK so the gate leaves no Back
     * entry behind it.
     */
    private fun routeOnward() {
        val next = if (OnboardingActivity.isCompleted(this)) {
            HomeActivity::class.java
        } else {
            OnboardingWelcomeActivity::class.java
        }
        startActivity(
            Intent(this, next)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
        )
        finish()
    }

    companion object {
        const val PREFS_NAME = MainActivity.PREFS_NAME

        /**
         * Shared-prefs key holding the highest Privacy Policy version the
         * user has accepted (0 = never).  Renaming it would silently
         * re-prompt every existing install.
         */
        const val PREF_PRIVACY_CONSENT_VERSION = "privacy_consent_version"

        /**
         * The current Privacy Policy version.  Bump when the policy
         * materially changes to re-gate existing users (their stored
         * version then falls below this and [isAccepted] returns false).
         */
        const val CONSENT_VERSION = 1

        /** Whether this device has recorded consent to the current policy. */
        fun isAccepted(ctx: Context): Boolean =
            hasAcceptedVersion(
                ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                    .getInt(PREF_PRIVACY_CONSENT_VERSION, 0),
            )

        /** Record consent to the current policy version. */
        fun markAccepted(ctx: Context) {
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit()
                .putInt(PREF_PRIVACY_CONSENT_VERSION, CONSENT_VERSION)
                .apply()
        }

        /**
         * Whether a stored accepted-version satisfies the current
         * requirement.  Pure over the int so the host-JVM test pins the
         * version-gating rule without a Context.
         */
        fun hasAcceptedVersion(storedVersion: Int): Boolean =
            storedVersion >= CONSENT_VERSION

        /**
         * URL of the hosted Privacy Policy for a given API base.  The
         * policy is served by the coach backend at `/privacy`
         * (`llm/seca/legal/router.py`); we trim a trailing slash on the
         * base so `https://host/` and `https://host` both yield one clean
         * `/privacy`.  Pure so the host-JVM test pins the path.
         */
        fun privacyPolicyUrl(apiBase: String): String =
            apiBase.trimEnd('/') + "/privacy"
    }
}
