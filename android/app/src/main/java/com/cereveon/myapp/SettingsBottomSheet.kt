package com.cereveon.myapp

import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatDelegate
import androidx.appcompat.widget.SwitchCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.slider.Slider
import kotlin.math.roundToInt
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Cereveon · Atrium · Settings (handoff screen #10).
 *
 * Sections (each separated by an Atrium hairline rule):
 *   1.  Coach voice  — radio (formal / conversational / terse)
 *   2.  Board style  — radio (flat / engraved / wireframe)
 *   3.  Appearance   — radio (system / dark / bright)
 *   4.  Sound        — switch
 *   5.  Notifications — switch
 *   6.  Profile      — chevron row: Skill rating (opens edit dialog)
 *   7.  Premium      — chevron row: Upgrade
 *   8.  Account      — chevron rows: Change password, Sign out
 *
 * Persistence: [PREFS_NAME] SharedPreferences (the same store
 * MainActivity uses for the rating cache and curriculum chip).
 *
 * **Consumer wiring status:**
 *   - Coach voice — persisted and read by `chat_pipeline.generate_chat_reply`
 *     via the `coach_voice` field on `/coach/chat`; CoachApiClient
 *     forwards [readCoachVoice] on every request.
 *   - Board style — persisted and read by [MainActivity.onCreate] /
 *     [MainActivity.onResume]; assigns [ChessBoardView.boardStyle] which
 *     branches the per-square render in `onDraw`.
 *   - Appearance — persisted and applied immediately via
 *     [androidx.appcompat.app.AppCompatDelegate]; re-applied at every
 *     cold start by [CereveonApplication.onCreate] through
 *     [readAppearanceMode] + [nightModeFor].  Default "system" follows
 *     the phone's colour mode; explicit Dark/Bright stay forced.
 *   - Sound / notifications persist, but no audio system or
 *     notification channel exists yet to consume them.
 *
 * The settings UI is the right place to put these toggles ahead of the
 * features that read them, so users see one consistent surface.  The
 * downstream readers can opt-in via [readCoachVoice], [readBoardStyle],
 * [readSoundEnabled], [readNotificationsEnabled].
 */
class SettingsBottomSheet : BottomSheetDialogFragment() {

    /**
     * Optional callbacks the host activity can wire to handle
     * Account-section taps.  All default to no-ops; the hosts set them
     * to forward to the shared [AccountFlows] change-password dialog,
     * logout flow, and confirmation-gated account-deletion flow
     * (GDPR Art. 17 — the row itself never deletes anything).
     */
    var onChangePasswordTapped: (() -> Unit)? = null
    var onSignOutTapped: (() -> Unit)? = null
    var onDeleteAccountTapped: (() -> Unit)? = null

    /**
     * "Download my data" (GDPR Art. 15/20, contract §42).  The hosts
     * forward to [DataExportFlows.startDownload] — the sheet never
     * fetches or writes anything itself.
     */
    var onDownloadDataTapped: (() -> Unit)? = null

    /**
     * Optional callback the host activity wires to surface the
     * Lichess Connect bottom sheet when the "Lichess" row in the
     * Integrations section is tapped.  Defaults to no-op; the host
     * is responsible for showing [LichessConnectBottomSheet] (or any
     * future replacement surface).
     */
    var onConnectLichessTapped: (() -> Unit)? = null

    private val voiceDots = mutableMapOf<String, View>()
    private val boardDots = mutableMapOf<String, View>()
    private val appearanceDots = mutableMapOf<String, View>()

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_settings, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val prefs = requireContext().getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

        // ── Coach voice radio ────────────────────────────────────────
        voiceDots["formal"]         = view.findViewById(R.id.voiceFormalDot)
        voiceDots["conversational"] = view.findViewById(R.id.voiceConversationalDot)
        voiceDots["terse"]          = view.findViewById(R.id.voiceTerseDot)
        applyRadioState(voiceDots, prefs.getString(PREF_COACH_VOICE, DEFAULT_COACH_VOICE)!!)

        bindRow(view, R.id.voiceFormal,         voiceDots, PREF_COACH_VOICE)
        bindRow(view, R.id.voiceConversational, voiceDots, PREF_COACH_VOICE)
        bindRow(view, R.id.voiceTerse,          voiceDots, PREF_COACH_VOICE)

        // ── Board style radio ────────────────────────────────────────
        boardDots["flat"]      = view.findViewById(R.id.boardFlatDot)
        boardDots["engraved"]  = view.findViewById(R.id.boardEngravedDot)
        boardDots["wireframe"] = view.findViewById(R.id.boardWireframeDot)
        applyRadioState(boardDots, prefs.getString(PREF_BOARD_STYLE, DEFAULT_BOARD_STYLE)!!)

        bindRow(view, R.id.boardFlat,      boardDots, PREF_BOARD_STYLE)
        bindRow(view, R.id.boardEngraved,  boardDots, PREF_BOARD_STYLE)
        bindRow(view, R.id.boardWireframe, boardDots, PREF_BOARD_STYLE)

        // ── Appearance radio ─────────────────────────────────────────
        // System (default, follows the phone's colour mode) / Dark /
        // Bright.  Unlike the other radio groups this one changes the
        // live UI, so selection routes through applyAppearanceMode
        // rather than bindRow: persist, dismiss FIRST (the mode change
        // recreates the host activity, and a framework-restored sheet
        // would come back with null Account callbacks — the hosts wire
        // them at show-time, not at restore-time), then flip the mode.
        val currentAppearance = readAppearanceMode(requireContext())
        appearanceDots[APPEARANCE_SYSTEM] = view.findViewById(R.id.appearanceSystemDot)
        appearanceDots[APPEARANCE_DARK]   = view.findViewById(R.id.appearanceDarkDot)
        appearanceDots[APPEARANCE_BRIGHT] = view.findViewById(R.id.appearanceBrightDot)
        applyRadioState(appearanceDots, currentAppearance)

        bindAppearanceRow(view, R.id.rowAppearanceSystem)
        bindAppearanceRow(view, R.id.rowAppearanceDark)
        bindAppearanceRow(view, R.id.rowAppearanceBright)

        // ── Sound switch ─────────────────────────────────────────────
        val sound = view.findViewById<SwitchCompat>(R.id.switchSound)
        sound.isChecked = prefs.getBoolean(PREF_SOUND_ENABLED, true)
        sound.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean(PREF_SOUND_ENABLED, checked).apply()
        }
        view.findViewById<View>(R.id.rowSound).setOnClickListener { sound.toggle() }

        // ── Notifications switch ─────────────────────────────────────
        val notif = view.findViewById<SwitchCompat>(R.id.switchNotifications)
        notif.isChecked = prefs.getBoolean(PREF_NOTIFICATIONS_ENABLED, true)
        notif.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean(PREF_NOTIFICATIONS_ENABLED, checked).apply()
        }
        view.findViewById<View>(R.id.rowNotifications).setOnClickListener { notif.toggle() }

        // ── Profile · Skill rating chevron row ──────────────────────
        // Retired when the user-visible Elo display was hidden from the
        // UI; the layout views are still in the tree with
        // ``visibility="gone"`` (see bottom_sheet_settings.xml).  The
        // showEditRatingDialog / persistRating / firePatchAuthMe helpers
        // below are now unreachable from the UI but remain in place
        // until the next phase replaces the calibration affordance.

        // ── Integrations · Lichess chevron row ──────────────────────
        view.findViewById<View>(R.id.rowConnectLichess).setOnClickListener {
            // Dismiss so the Lichess sheet slides over a settled host
            // background, not over the Settings sheet's fading scrim
            // (matches the rowUpgrade dismiss-then-launch idiom below).
            dismiss()
            onConnectLichessTapped?.invoke()
        }
        // The row's value defaults to "Not linked" in the layout; fetch the
        // live GET /lichess/status so it reflects the REAL link state.
        // Without this the row was a static label that stayed "Not linked"
        // even for a linked account (the authoritative Connect sheet reads
        // the same endpoint and shows "Linked" — the two disagreed).
        refreshLichessRow(view.findViewById(R.id.rowConnectLichessValue))

        // ── Premium chevron row ──────────────────────────────────────
        view.findViewById<View>(R.id.rowUpgrade).setOnClickListener {
            // Dismiss the sheet first so the paywall slides in over a
            // settled MainActivity background, not over a half-faded
            // bottom-sheet scrim.
            dismiss()
            startActivity(Intent(requireContext(), PaywallActivity::class.java))
        }

        // ── Account chevron rows ─────────────────────────────────────
        view.findViewById<View>(R.id.rowChangePassword).setOnClickListener {
            dismiss()
            onChangePasswordTapped?.invoke()
        }
        view.findViewById<View>(R.id.rowSignOut).setOnClickListener {
            dismiss()
            onSignOutTapped?.invoke()
        }
        // Delete account (GDPR Art. 17): the row only forwards to the
        // host's AccountFlows.confirmAndDeleteAccount — the "Are you
        // sure" gate and the DELETE /auth/me call both live there, so
        // this sheet can never delete anything on its own.
        view.findViewById<View>(R.id.rowDeleteAccount).setOnClickListener {
            dismiss()
            onDeleteAccountTapped?.invoke()
        }
        // Download my data (GDPR Art. 15/20): forwards to the host's
        // DataExportFlows.startDownload — fetch + SAF save both live
        // there, so this sheet never touches the network or the disk.
        view.findViewById<View>(R.id.rowDownloadData).setOnClickListener {
            dismiss()
            onDownloadDataTapped?.invoke()
        }
        // Privacy Policy: opens the hosted /privacy page (the same URL the
        // sign-up consent gate links) in the browser.  Self-contained — no
        // host state to thread, so it opens directly rather than via a
        // callback, and the sheet stays up so the user returns to Settings.
        view.findViewById<View>(R.id.rowPrivacyPolicy).setOnClickListener {
            val url = PrivacyConsentActivity.privacyPolicyUrl(BuildConfig.COACH_API_BASE)
            try {
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
            } catch (_: ActivityNotFoundException) {
                Toast.makeText(
                    requireContext(),
                    "No browser available to open the policy",
                    Toast.LENGTH_SHORT,
                ).show()
            }
        }
    }

    /**
     * Populate the Integrations · Lichess row's trailing value from the
     * live GET /lichess/status, so it reflects the real link state rather
     * than the layout's static "Not linked" default.  Reads the same
     * endpoint the authoritative [LichessConnectBottomSheet] uses.
     *
     * Best-effort: on a missing token or any transport / HTTP error the
     * default text stands — a passive settings row must not surface
     * network noise.  Guarded by [isAdded] because the sheet can be
     * dismissed while the request is in flight.
     */
    private fun refreshLichessRow(valueView: TextView) {
        val authRepo = AuthRepository(EncryptedTokenStorage(requireContext()))
        val token = authRepo.getToken() ?: return
        val client = HttpLichessApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            tokenSink = { authRepo.saveToken(it) },
        )
        viewLifecycleOwner.lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) { client.status(token) }
            if (!isAdded) return@launch
            val status = (result as? ApiResult.Success)?.data ?: return@launch
            valueView.text = when {
                status.linked && !status.externalUsername.isNullOrBlank() ->
                    getString(R.string.lichess_settings_linked_as, status.externalUsername)
                status.linked -> getString(R.string.lichess_settings_linked)
                else -> getString(R.string.lichess_settings_not_linked)
            }
        }
    }

    /**
     * Show the rating-edit AlertDialog, prefilled with the user's
     * current calibrated rating.  On Save:
     *   - Persist the new value to PREF_PLAYER_RATING_ESTIMATE +
     *     mirror into PREF_RATING (the drawer header reads it)
     *   - Update the row's trailing label
     *   - Fire PATCH /auth/me on lifecycleScope (best-effort; the
     *     same recovery path MainActivity already runs at cold-start
     *     reconciles if this fails offline)
     *
     * Cancel is a no-op.  No "are you sure" — the slider already
     * shows the new value before tap, and an accidental change is
     * one tap away from a corrective edit.
     */
    private fun showEditRatingDialog(
        prefs: android.content.SharedPreferences,
        rowLabel: TextView,
    ) {
        val ctx = requireContext()
        val view = LayoutInflater.from(ctx).inflate(R.layout.dialog_edit_rating, null, false)
        val slider = view.findViewById<Slider>(R.id.dialogRatingSlider)
        val valueLabel = view.findViewById<TextView>(R.id.dialogRatingValue)

        val current = prefs.getFloat(
            OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE,
            prefs.getFloat(MainActivity.PREF_RATING, OnboardingActivity.DEFAULT_RATING),
        ).coerceIn(SLIDER_MIN, SLIDER_MAX)

        slider.value = current
        valueLabel.text = OnboardingActivity.formatRating(current)
        slider.addOnChangeListener { _, value, _ ->
            valueLabel.text = OnboardingActivity.formatRating(value)
        }

        AlertDialog.Builder(ctx)
            .setTitle("Adjust your rating")
            .setView(view)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Save") { _, _ ->
                val newRating = slider.value
                persistRating(prefs, newRating)
                rowLabel.text = formatRatingLabel(prefs)
                firePatchAuthMe(newRating)
            }
            .show()
    }

    private fun persistRating(prefs: android.content.SharedPreferences, rating: Float) {
        prefs.edit()
            .putFloat(OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE, rating)
            // Mirror into the drawer-header key so MainActivity picks
            // it up immediately without waiting for the next /auth/me
            // round-trip.
            .putFloat(MainActivity.PREF_RATING, rating)
            .apply()
    }

    private fun firePatchAuthMe(rating: Float) {
        val ctx = requireContext()
        val authRepo = AuthRepository(EncryptedTokenStorage(ctx))
        val token = authRepo.getToken() ?: return
        val client: AuthApiClient = HttpAuthApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )
        // Fire on the fragment's lifecycleScope; if the user dismisses
        // the sheet before the call returns, the launch is cancelled
        // cleanly.  Confidence omitted: the user only adjusted the
        // rating, not the certainty around it.
        lifecycleScope.launch {
            when (val r = client.updateMe(token, rating = rating, confidence = null)) {
                is ApiResult.Success -> {
                    Toast.makeText(
                        ctx, "Rating saved · ${rating.roundToInt()}", Toast.LENGTH_SHORT,
                    ).show()
                    // PR #175: clear the onboarding-time PREF after a
                    // successful PATCH.  Without this the value would
                    // linger and (pre-PR-#175 cold-start reconcile) get
                    // re-PATCHed on every cold-start, clobbering
                    // game-driven rating updates.  The cold-start
                    // reconcile path is retired in PR #175; we still
                    // clear the PREF for hygiene so a future regression
                    // can't reintroduce the same shape.
                    ctx.getSharedPreferences(MainActivity.PREFS_NAME, Context.MODE_PRIVATE)
                        .edit()
                        .remove(OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE)
                        .remove(OnboardingActivity.PREF_PLAYER_CONFIDENCE)
                        .apply()
                }
                is ApiResult.HttpError -> {
                    Log.w("SETTINGS", "PATCH /auth/me HTTP ${r.code}")
                    if (r.code != 401) {
                        // 401 → MainActivity's session-expired flow handles it
                        Toast.makeText(
                            ctx,
                            "Saved locally · server sync will retry",
                            Toast.LENGTH_SHORT,
                        ).show()
                    }
                }
                is ApiResult.NetworkError -> {
                    Log.w("SETTINGS", "PATCH /auth/me network error", r.cause)
                    Toast.makeText(
                        ctx, "Saved locally · server sync will retry",
                        Toast.LENGTH_SHORT,
                    ).show()
                }
                ApiResult.Timeout -> {
                    Log.w("SETTINGS", "PATCH /auth/me timed out")
                    Toast.makeText(
                        ctx, "Saved locally · server sync will retry",
                        Toast.LENGTH_SHORT,
                    ).show()
                }
            }
        }
    }

    /**
     * Wire a radio-row click: write [prefKey] = row.tag and update
     * the visual selection so only the tapped dot is filled.
     */
    private fun bindRow(
        root: View,
        rowId: Int,
        dots: Map<String, View>,
        prefKey: String,
    ) {
        val row = root.findViewById<LinearLayout>(rowId)
        val value = row.tag as String
        row.setOnClickListener {
            requireContext().getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit().putString(prefKey, value).apply()
            applyRadioState(dots, value)
        }
    }

    /**
     * Wire an Appearance radio-row click.  Unlike [bindRow] the
     * selection has an immediate visible effect (the palette), so on a
     * CHANGED selection: persist, dismiss FIRST, then flip the night
     * mode (the flip recreates the hosts; see the Appearance block in
     * onViewCreated for why dismissal must come first).  Re-tapping
     * the already-selected row is a no-op beyond the dot state — no
     * pointless activity recreate.
     */
    private fun bindAppearanceRow(root: View, rowId: Int) {
        val row = root.findViewById<LinearLayout>(rowId)
        val value = row.tag as String
        row.setOnClickListener {
            val ctx = requireContext()
            val previous = readAppearanceMode(ctx)
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit().putString(PREF_APPEARANCE_MODE, value).apply()
            applyRadioState(appearanceDots, value)
            if (value != previous) {
                dismiss()
                AppCompatDelegate.setDefaultNightMode(nightModeFor(value))
            }
        }
    }

    /** Set the dot drawable for each entry in [dots]: filled if its key matches [selected]. */
    private fun applyRadioState(dots: Map<String, View>, selected: String) {
        val ctx = requireContext()
        val filled = ContextCompat.getDrawable(ctx, R.drawable.atrium_radio_selected)
        val hollow = ContextCompat.getDrawable(ctx, R.drawable.atrium_radio_unselected)
        dots.forEach { (key, dot) ->
            dot.background = if (key == selected) filled else hollow
        }
    }

    companion object {
        // Same SharedPreferences store MainActivity uses for rating
        // cache + curriculum chip.  One prefs file keeps the app's
        // user-state surface coherent.
        const val PREFS_NAME = "chesscoach_prefs"

        const val PREF_COACH_VOICE = "setting_coach_voice"
        const val DEFAULT_COACH_VOICE = "conversational"

        const val PREF_BOARD_STYLE = "setting_board_style"
        const val DEFAULT_BOARD_STYLE = "flat"

        const val PREF_SOUND_ENABLED = "setting_sound_enabled"
        const val PREF_NOTIFICATIONS_ENABLED = "setting_notifications_enabled"

        // Appearance mode: "system" (default — the palette follows the
        // phone's current colour mode), "dark" (always Atrium dark) or
        // "bright" (always warm paper).  Explicit choices map to FORCED
        // AppCompatDelegate modes; "system" maps to FOLLOW_SYSTEM.
        const val PREF_APPEARANCE_MODE = "setting_appearance_mode"
        const val APPEARANCE_SYSTEM = "system"
        const val APPEARANCE_DARK = "dark"
        const val APPEARANCE_BRIGHT = "bright"
        const val DEFAULT_APPEARANCE_MODE = APPEARANCE_SYSTEM

        // Legacy boolean from the original Bright-mode switch (one
        // release, 2026-07-16).  Read only as a migration fallback by
        // readAppearanceMode: true → "bright".  false is NOT migrated
        // to "dark" — it was the untouched default, so those installs
        // get the new "system" default.  Never written anymore.
        const val PREF_BRIGHT_MODE = "setting_bright_mode"

        // Slider bounds for the rating-edit dialog.  Match the
        // OnboardingActivity slider so a re-edit feels like the
        // same affordance the user saw at calibration.
        const val SLIDER_MIN = 800f
        const val SLIDER_MAX = 2600f

        /**
         * Format the current rating for the row's trailing label.
         * Prefers the local PREF_PLAYER_RATING_ESTIMATE (set by
         * Onboarding + this dialog), falls back to PREF_RATING
         * (the drawer-header cache, possibly synced from /auth/me),
         * and finally to the slider's neutral midpoint default
         * so the row never reads "—" once a user is logged in.
         */
        fun formatRatingLabel(prefs: android.content.SharedPreferences): String {
            val rating = prefs.getFloat(
                OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE,
                prefs.getFloat(MainActivity.PREF_RATING, OnboardingActivity.DEFAULT_RATING),
            )
            return OnboardingActivity.formatRating(rating)
        }

        // ── Reader helpers — call these from downstream features ──

        fun readCoachVoice(ctx: Context): String =
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getString(PREF_COACH_VOICE, DEFAULT_COACH_VOICE)!!

        fun readBoardStyle(ctx: Context): String =
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getString(PREF_BOARD_STYLE, DEFAULT_BOARD_STYLE)!!

        fun readSoundEnabled(ctx: Context): Boolean =
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getBoolean(PREF_SOUND_ENABLED, true)

        fun readNotificationsEnabled(ctx: Context): Boolean =
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getBoolean(PREF_NOTIFICATIONS_ENABLED, true)

        /**
         * The persisted appearance mode, with legacy migration: an
         * absent [PREF_APPEARANCE_MODE] falls back to the retired
         * Bright-mode switch value ([PREF_BRIGHT_MODE] true →
         * [APPEARANCE_BRIGHT]) and finally to [DEFAULT_APPEARANCE_MODE]
         * ("system").  Unknown persisted strings also resolve to the
         * default so a bad write can never wedge the palette.
         */
        fun readAppearanceMode(ctx: Context): String {
            val prefs = ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            val stored = prefs.getString(PREF_APPEARANCE_MODE, null)
                ?: if (prefs.getBoolean(PREF_BRIGHT_MODE, false)) {
                    APPEARANCE_BRIGHT
                } else {
                    DEFAULT_APPEARANCE_MODE
                }
            return if (
                stored == APPEARANCE_SYSTEM ||
                stored == APPEARANCE_DARK ||
                stored == APPEARANCE_BRIGHT
            ) {
                stored
            } else {
                DEFAULT_APPEARANCE_MODE
            }
        }

        /**
         * Map an appearance mode onto an AppCompat night mode.
         * "system" follows the phone's colour mode (the default since
         * 2026-07-16); the explicit choices stay FORCED so a user who
         * picked Dark or Bright keeps it regardless of the phone
         * setting.  [CereveonApplication.onCreate] applies this at
         * process start; the settings radio applies it live.
         */
        fun nightModeFor(mode: String): Int = when (mode) {
            APPEARANCE_BRIGHT -> AppCompatDelegate.MODE_NIGHT_NO
            APPEARANCE_DARK -> AppCompatDelegate.MODE_NIGHT_YES
            else -> AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM
        }
    }
}
