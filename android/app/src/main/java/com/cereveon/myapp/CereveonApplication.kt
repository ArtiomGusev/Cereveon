package com.cereveon.myapp

import android.app.Application
import android.util.Log
import androidx.appcompat.app.AppCompatDelegate
import com.revenuecat.purchases.LogLevel
import com.revenuecat.purchases.Purchases
import com.revenuecat.purchases.PurchasesConfiguration
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * Process-wide singletons + cold-start warm-up.
 *
 * Created once by the Android framework before the first activity
 * runs (because we register it in the application tag of the
 * AndroidManifest).  Used here for one job: pre-warming the
 * Android Keystore-backed [EncryptedTokenStorage].
 *
 * Why pre-warm
 * ------------
 * `EncryptedSharedPreferences.create` (and its prerequisite
 * `MasterKey.Builder().build()`) hits the Android Keystore on
 * first call.  The Keystore is hardware-backed on most modern
 * devices; the first KeyStore operation in a process does a
 * key-material fetch / unwrap that can take 200–800ms — sometimes
 * worse on cold-boot or low-end hardware.
 *
 * The token is read on the main thread by every screen that needs
 * auth (LoginActivity, HomeActivity, MainActivity).  Without
 * pre-warming, the first activity to read it stalls its onCreate
 * for the duration of that key fetch — which is part of why the
 * user reported "the New game button takes ~10 taps before it
 * opens" on cold start.
 *
 * The fix moves that one-shot cost off the main thread to a
 * background coroutine fired here.  Any activity that subsequently
 * constructs an [EncryptedTokenStorage] re-uses the warm
 * MasterKey + EncryptedSharedPreferences instance and the
 * .load() returns immediately.
 *
 * The coroutine is intentionally fire-and-forget; if it loses a
 * race against an activity that reads the token first, the
 * activity-side call still works (just pays the same cost it
 * would have without us).  Failures are silent — auth still
 * works, the keystore just isn't pre-warmed.
 */
class CereveonApplication : Application() {

    /**
     * Application-scoped supervisor scope for fire-and-forget
     * background work.  SupervisorJob so a single failure (e.g.
     * Keystore corruption) doesn't cancel future warm-ups.
     */
    private val appScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onCreate() {
        super.onCreate()
        applyPersistedAppearance()
        configureRevenueCat()
        prewarmEncryptedTokenStorage()
    }

    /**
     * Configure the RevenueCat billing SDK once per process, before any
     * activity can reach the paywall.
     *
     * Configured ANONYMOUSLY here (no app_user_id) so cold start never
     * blocks on the Keystore-backed token read.  [PaywallActivity] pins the
     * RevenueCat identity to the server player id (`logIn`) right before a
     * purchase — the only moment the webhook's `app_user_id` → player
     * mapping needs to be correct.
     *
     * Guarded on a non-blank key: dev / CI builds ship
     * `BuildConfig.REVENUECAT_API_KEY = ""` (see build.gradle.kts) and
     * `Purchases.configure` rejects a blank key, so we skip configuration
     * and the paywall degrades to "billing unavailable" instead of crashing
     * at startup.
     */
    private fun configureRevenueCat() {
        val apiKey = BuildConfig.REVENUECAT_API_KEY
        if (apiKey.isBlank()) {
            Log.i("CereveonApplication", "RevenueCat key absent — billing disabled for this build")
            return
        }
        Purchases.logLevel = if (BuildConfig.DEBUG) LogLevel.DEBUG else LogLevel.WARN
        Purchases.configure(PurchasesConfiguration.Builder(this, apiKey).build())
    }

    /**
     * Apply the persisted Appearance setting BEFORE the first
     * activity inflates.  Default is "system": the palette follows
     * the phone's current colour mode (MODE_NIGHT_FOLLOW_SYSTEM),
     * including live quick-settings flips.  An explicit Dark or
     * Bright choice in Settings › Appearance stays FORCED
     * (MODE_NIGHT_YES / MODE_NIGHT_NO) so it holds regardless of the
     * phone setting.  The pref read is a synchronous
     * SharedPreferences load of a file every launch screen reads
     * moments later anyway.
     */
    private fun applyPersistedAppearance() {
        AppCompatDelegate.setDefaultNightMode(
            SettingsBottomSheet.nightModeFor(
                SettingsBottomSheet.readAppearanceMode(this),
            ),
        )
    }

    /**
     * Touch the token storage so the Keystore unwrap happens off
     * the main thread before any activity needs it.  We call
     * .load() (not .save()) because save() does a real keystore
     * encrypt + apply() write that we don't want on cold-start —
     * load() is sufficient to drive the lazy initialiser.
     */
    private fun prewarmEncryptedTokenStorage() {
        appScope.launch {
            try {
                EncryptedTokenStorage(this@CereveonApplication).load()
                Log.d("CereveonApplication", "EncryptedTokenStorage warm")
            } catch (e: Exception) {
                // Activity-side construction will throw the same
                // way and surface the issue through the existing
                // try/catch in AuthRepository.getToken; nothing to
                // do here besides note the failure.
                Log.w("CereveonApplication", "EncryptedTokenStorage warm-up failed", e)
            }
        }
    }
}
