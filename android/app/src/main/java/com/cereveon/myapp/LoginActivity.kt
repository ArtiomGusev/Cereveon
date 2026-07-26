package com.cereveon.myapp

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.launch

/**
 * Login screen that authenticates the user against the coach backend.
 *
 * Flow:
 *  1. User enters email + password and taps Sign In or Create Account.
 *  2. [AuthApiClient.login] / [AuthApiClient.register] is called; on success
 *     the JWT is stored in [AuthRepository] (backed by [EncryptedSharedPreferences]).
 *  3. Routing:
 *     - Login success → [HomeActivity] (or [OnboardingActivity] if a prior
 *       registration was abandoned before the calibration step completed).
 *     - Registration success → [OnboardingActivity] for skill calibration
 *       (handoff #2), then on Continue → [HomeActivity].
 *
 * "Sign in with Lichess" (OAuth PKCE — `docs/API_CONTRACTS.md` §16a):
 *  1. Tap → [startLichessSignIn] mints a PKCE verifier + `state`, persists
 *     them in app-private prefs (they must survive process death while the
 *     browser is foregrounded), and opens the system browser at the
 *     Lichess authorize URL ([LichessOAuth.buildAuthorizeUrl]).
 *  2. Lichess redirects to `ai.chesscoach.app://lichess-auth`;
 *     [LichessAuthRedirectActivity] forwards the URI here (onNewIntent
 *     when this instance is still alive, onCreate data after death).
 *  3. [handleLichessRedirect] verifies `state` against the persisted
 *     value, then [performLichessLogin] posts the one-time code + verifier
 *     to the backend, which does the code exchange server-side and
 *     returns the same JWT shape as password login → [launchPostAuth].
 *
 * Token expiry: [AuthRepository.isLoggedIn] checks the `exp` claim; if the
 * stored token is already expired when the app starts, [MainActivity] /
 * [HomeActivity] redirect back here automatically.
 */
class LoginActivity : AppCompatActivity() {

    companion object {
        /**
         * App-private prefs holding the in-flight OAuth attempt.  Plain
         * (not encrypted) SharedPreferences is deliberate: the verifier
         * is single-use, worthless without the matching one-time code
         * Lichess issues to OUR redirect, and cleared the moment the
         * redirect resolves — while surviving process death during the
         * browser round-trip, which is the property that matters.
         */
        private const val LICHESS_OAUTH_PREFS = "lichess_oauth"
        private const val KEY_PENDING_VERIFIER = "pending_code_verifier"
        private const val KEY_PENDING_STATE = "pending_state"
    }

    private lateinit var etEmail: TextInputEditText
    private lateinit var etPassword: TextInputEditText
    private lateinit var btnLogin: Button
    private lateinit var btnRegister: Button
    private lateinit var btnLichess: Button
    private lateinit var tvError: TextView
    private lateinit var progressBar: ProgressBar

    private val authApiClient: AuthApiClient by lazy {
        HttpAuthApiClient(baseUrl = BuildConfig.COACH_API_BASE)
    }

    private val authRepository: AuthRepository by lazy {
        AuthRepository(EncryptedTokenStorage(this))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // If already logged in, go straight to the game — avoid showing the
        // login form when the user re-opens the app with a valid session.
        if (authRepository.isLoggedIn()) {
            routeAfterAuth()
            return
        }

        setContentView(R.layout.activity_login)

        etEmail = findViewById(R.id.etEmail)
        etPassword = findViewById(R.id.etPassword)
        btnLogin = findViewById(R.id.btnLogin)
        btnRegister = findViewById(R.id.btnRegister)
        btnLichess = findViewById(R.id.btnLichess)
        tvError = findViewById(R.id.tvError)
        progressBar = findViewById(R.id.progressBar)

        btnLogin.setOnClickListener {
            val email = etEmail.text?.toString()?.trim().orEmpty()
            val password = etPassword.text?.toString().orEmpty()

            if (email.isEmpty() || password.isEmpty()) {
                showError("Please enter your email and password.")
                return@setOnClickListener
            }

            performLogin(email, password)
        }

        btnRegister.setOnClickListener {
            val email = etEmail.text?.toString()?.trim().orEmpty()
            val password = etPassword.text?.toString().orEmpty()

            if (email.isEmpty() || password.isEmpty()) {
                showError("Please enter an email and password to create an account.")
                return@setOnClickListener
            }

            performRegister(email, password)
        }

        btnLichess.setOnClickListener { startLichessSignIn() }

        // Cold-start delivery of an OAuth redirect (LichessAuthRedirectActivity
        // relaunched us after process death while the browser was up).
        handleLichessRedirect(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        // Warm delivery: the redirect trampoline reached the existing
        // instance via CLEAR_TOP | SINGLE_TOP.
        handleLichessRedirect(intent)
    }

    // ---------------------------------------------------------------------------
    // Login logic
    // ---------------------------------------------------------------------------

    private fun performLogin(email: String, password: String) {
        progressBar.visibility = View.VISIBLE
        btnLogin.isEnabled = false
        tvError.visibility = View.GONE

        lifecycleScope.launch {
            when (val result = authApiClient.login(email, password)) {
                is ApiResult.Success -> {
                    authRepository.saveToken(result.data.accessToken)
                    routeAfterAuth()
                }

                is ApiResult.HttpError -> {
                    val message =
                        if (result.code == 401) {
                            "Invalid email or password."
                        } else {
                            "Server error (${result.code}). Please try again."
                        }
                    showError(message)
                }

                is ApiResult.NetworkError ->
                    showError("Cannot reach the coach server. Check your connection.")

                ApiResult.Timeout ->
                    showError("Connection timed out. Please try again.")
            }
        }
    }

    private fun performRegister(email: String, password: String) {
        progressBar.visibility = View.VISIBLE
        btnLogin.isEnabled = false
        btnRegister.isEnabled = false
        tvError.visibility = View.GONE

        lifecycleScope.launch {
            when (val result = authApiClient.register(email, password)) {
                is ApiResult.Success -> {
                    authRepository.saveToken(result.data.accessToken)
                    routeAfterAuth()
                }

                is ApiResult.HttpError -> {
                    val message = when (result.code) {
                        409 -> "An account with this email already exists."
                        else -> "Registration failed (${result.code}). Please try again."
                    }
                    showError(message)
                }

                is ApiResult.NetworkError ->
                    showError("Cannot reach the coach server. Check your connection.")

                ApiResult.Timeout ->
                    showError("Connection timed out. Please try again.")
            }
        }
    }

    // ---------------------------------------------------------------------------
    // Sign in with Lichess (OAuth authorization-code + PKCE)
    // ---------------------------------------------------------------------------

    /**
     * Mint fresh PKCE material, persist it, and hand the user to the
     * system browser for the Lichess consent screen.
     *
     * The verifier + state are written with [android.content.SharedPreferences.Editor.commit]
     * (not `apply`) because the browser takes over the foreground
     * immediately — an async write racing process death would strand the
     * redirect with no verifier to exchange.
     */
    private fun startLichessSignIn() {
        val verifier = LichessOAuth.generateCodeVerifier()
        val state = LichessOAuth.generateState()
        getSharedPreferences(LICHESS_OAUTH_PREFS, MODE_PRIVATE)
            .edit()
            .putString(KEY_PENDING_VERIFIER, verifier)
            .putString(KEY_PENDING_STATE, state)
            .commit()

        val url = LichessOAuth.buildAuthorizeUrl(
            codeChallenge = LichessOAuth.codeChallengeS256(verifier),
            state = state,
        )
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (_: ActivityNotFoundException) {
            clearPendingLichessAuth()
            showError("No browser available to open Lichess.")
        }
    }

    /**
     * Consume a Lichess OAuth redirect if (and only if) this intent
     * carries one and a sign-in attempt is actually pending.
     *
     * Silent no-ops (by design):
     *  - intents without our `ai.chesscoach.app://lichess-auth` data —
     *    every normal launcher start lands here;
     *  - redirects with no pending verifier — a replayed / stale intent
     *    (e.g. re-delivery on configuration change after the attempt
     *    already resolved) has nothing to exchange, and any hostile app
     *    firing forged redirect intents at us learns nothing.
     */
    private fun handleLichessRedirect(intent: Intent?) {
        val data = intent?.data ?: return
        if (data.scheme != LichessOAuth.REDIRECT_SCHEME || data.host != LichessOAuth.REDIRECT_HOST) {
            return
        }

        val prefs = getSharedPreferences(LICHESS_OAUTH_PREFS, MODE_PRIVATE)
        val pendingVerifier = prefs.getString(KEY_PENDING_VERIFIER, null)
        val pendingState = prefs.getString(KEY_PENDING_STATE, null)
        if (pendingVerifier == null || pendingState == null) {
            return
        }

        // The user backed out on the consent screen (?error=access_denied)
        // or Lichess reported another authorize-stage failure.
        if (data.getQueryParameter("error") != null) {
            clearPendingLichessAuth()
            showError("Lichess sign-in was cancelled.")
            return
        }

        val code = data.getQueryParameter("code")
        val state = data.getQueryParameter("state")
        if (code.isNullOrEmpty() || state != pendingState) {
            // State mismatch = this redirect does not belong to the flow WE
            // started (CSRF / injected intent).  Burn the pending attempt.
            clearPendingLichessAuth()
            showError("Lichess sign-in failed. Please try again.")
            return
        }

        clearPendingLichessAuth()
        performLichessLogin(code, pendingVerifier)
    }

    private fun performLichessLogin(code: String, codeVerifier: String) {
        progressBar.visibility = View.VISIBLE
        btnLogin.isEnabled = false
        btnRegister.isEnabled = false
        btnLichess.isEnabled = false
        tvError.visibility = View.GONE

        lifecycleScope.launch {
            when (val result = authApiClient.loginWithLichess(code, codeVerifier)) {
                is ApiResult.Success -> {
                    authRepository.saveToken(result.data.accessToken)
                    routeAfterAuth()
                }

                is ApiResult.HttpError -> {
                    val message = when (result.code) {
                        // The one-time code was rejected (expired / replayed /
                        // verifier mismatch) — restarting the flow mints a
                        // fresh one.
                        401 -> "Lichess sign-in failed. Please try again."
                        503 -> "Lichess is busy right now. Please try again shortly."
                        else -> "Server error (${result.code}). Please try again."
                    }
                    showError(message)
                }

                is ApiResult.NetworkError ->
                    showError("Cannot reach the coach server. Check your connection.")

                ApiResult.Timeout ->
                    showError("Connection timed out. Please try again.")
            }
        }
    }

    private fun clearPendingLichessAuth() {
        getSharedPreferences(LICHESS_OAUTH_PREFS, MODE_PRIVATE)
            .edit()
            .remove(KEY_PENDING_VERIFIER)
            .remove(KEY_PENDING_STATE)
            .apply()
    }

    private fun showError(message: String) {
        progressBar.visibility = View.GONE
        btnLogin.isEnabled = true
        btnRegister.isEnabled = true
        btnLichess.isEnabled = true
        tvError.text = message
        tvError.visibility = View.VISIBLE
    }

    /**
     * First hop after ANY successful authentication (register, login,
     * Lichess, or an already-valid session on cold start): force the
     * privacy consent gate if this device hasn't accepted the current
     * policy version, otherwise fall through to the normal
     * onboarding-or-home routing.
     *
     * Gating on every authenticated entry — not only after registration —
     * makes consent unskippable: a user who kills the app while the gate
     * is up returns to [LoginActivity] still logged in, and this bounces
     * them straight back to the gate.  [PrivacyConsentActivity] records
     * consent and then routes onward the same way [launchPostAuth] would.
     */
    private fun routeAfterAuth() {
        if (PrivacyConsentActivity.isAccepted(this)) {
            launchPostAuth()
        } else {
            startActivity(
                Intent(this, PrivacyConsentActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
            )
            finish()
        }
    }

    /**
     * Decide where an authenticated user lands.  Newly-registered users that
     * abandoned the calibration flow (or any future case where onboarding is
     * incomplete) get routed through [OnboardingActivity] first; everyone else
     * goes to [HomeActivity], the post-auth landing.
     */
    private fun launchPostAuth() {
        if (OnboardingActivity.isCompleted(this)) {
            launchHome()
        } else {
            launchOnboarding()
        }
    }

    private fun launchHome() {
        startActivity(
            Intent(this, HomeActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
        )
        finish()
    }

    private fun launchOnboarding() {
        // Enter at step 1/3 (Welcome).  Step 2/3 (calibration) and
        // step 3/3 (completion) are reached by the Begin / Continue
        // buttons on each screen; HomeActivity is the post-onboarding
        // landing.
        startActivity(
            Intent(this, OnboardingWelcomeActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
        )
        finish()
    }
}
