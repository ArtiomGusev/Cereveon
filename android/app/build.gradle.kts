import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    // ``org.jetbrains.kotlin.android`` was made obsolete by AGP 9.0 —
    // the Android Gradle Plugin now bundles Kotlin support natively
    // (see https://kotl.in/gradle/agp-built-in-kotlin).  Applying it
    // alongside AGP 9 triggers an extension-already-registered error.
    alias(libs.plugins.kotlin.serialization)
}

// Release signing — read from Gradle properties (MYAPP_UPLOAD_*).
// Local dev: set real values in ~/.gradle/gradle.properties (never committed).
// CI: the workflow appends real values to android/gradle.properties before build.
// Empty placeholder values in android/gradle.properties resolve to null here,
// so the signingConfig block is skipped and Gradle produces an unsigned APK.
val releaseKeystoreFile: String? =
    (findProperty("MYAPP_UPLOAD_STORE_FILE") as String?).takeIf { !it.isNullOrBlank() }
val releaseKeyAlias: String? =
    (findProperty("MYAPP_UPLOAD_KEY_ALIAS") as String?).takeIf { !it.isNullOrBlank() }
val releaseKeyPassword: String? =
    (findProperty("MYAPP_UPLOAD_KEY_PASSWORD") as String?).takeIf { !it.isNullOrBlank() }
val releaseStorePassword: String? =
    (findProperty("MYAPP_UPLOAD_STORE_PASSWORD") as String?).takeIf { !it.isNullOrBlank() }
val hasReleaseSigningConfig: Boolean = listOf(
    releaseKeystoreFile, releaseKeyAlias, releaseKeyPassword, releaseStorePassword,
).all { it != null }

android {
    namespace = "com.cereveon.myapp"
    compileSdk = 36

    buildFeatures {
        buildConfig = true
    }

    defaultConfig {
        applicationId = "com.cereveon.myapp"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // Coach backend — points at the production server by default.
        // Override COACH_API_BASE / COACH_API_KEY env vars to redirect to a
        // local dev server (e.g. http://10.0.2.2:8000 for the Android emulator).
        buildConfigField("String", "COACH_API_BASE", "\"https://cereveon.com\"")
        buildConfigField("String", "COACH_API_KEY", "\"dev-key\"")

        // RevenueCat public SDK key (goog_…) — the app's billing source of
        // truth.  Publishable by design (safe in the decompiled APK), so it
        // is a plain configuration value like COACH_API_BASE, injected from
        // the REVENUECAT_API_KEY env var by the build-type blocks below.
        // Defaults to blank: an unset key leaves the SDK unconfigured and the
        // paywall shows "billing unavailable" rather than crashing — dev / CI
        // builds don't need a real key.
        buildConfigField("String", "REVENUECAT_API_KEY", "\"\"")

        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    testOptions {
        unitTests {
            isReturnDefaultValues = true
            // Force IPv4 so HttpURLConnection and MockWebServer use the same
            // loopback address on all platforms (avoids Windows IPv6/keep-alive races).
            all { it.jvmArgs("-Djava.net.preferIPv4Stack=true") }
        }
    }

    if (hasReleaseSigningConfig) {
        signingConfigs {
            create("release") {
                storeFile = file(releaseKeystoreFile!!)
                keyAlias = releaseKeyAlias!!
                keyPassword = releaseKeyPassword!!
                storePassword = releaseStorePassword!!
            }
        }
    }

    buildTypes {
        release {
            if (hasReleaseSigningConfig) {
                signingConfig = signingConfigs.getByName("release")
            }
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            // COACH_API_BASE is a plain configuration value (not a secret) — it is
            // visible in the decompiled APK regardless of obfuscation. Pass it as a
            // GitHub Actions variable (vars.COACH_API_BASE), not a secret.
            // COACH_API_KEY ends up in the APK binary and is therefore semi-public;
            // treat it as a rate-limit shield only, not real authentication. Real
            // per-user auth uses JWT tokens issued by /auth/login. Pass it as a
            // GitHub Actions secret (secrets.COACH_API_KEY).
            //
            // Falls back to dev defaults when env vars are absent or blank.
            // vars.COACH_API_BASE expands to "" (not null) when unset in GitHub Actions,
            // so treat blank the same as absent to avoid a spurious HTTPS guard failure.
            val rawProdApiBase: String? = System.getenv("COACH_API_BASE")?.takeIf { it.isNotBlank() }
            val prodApiBase: String = rawProdApiBase ?: "https://cereveon.com"
            val prodApiKey: String = System.getenv("COACH_API_KEY")?.takeIf { it.isNotBlank() } ?: "dev-key"
            // Hard-fail if COACH_API_BASE is explicitly provided but uses plain HTTP.
            if (rawProdApiBase != null && !prodApiBase.startsWith("https://")) {
                error(
                    "Release build requires COACH_API_BASE to start with https://. " +
                    "Got: $prodApiBase — set a valid TLS endpoint."
                )
            }
            buildConfigField("String", "COACH_API_BASE", "\"$prodApiBase\"")
            buildConfigField("String", "COACH_API_KEY", "\"$prodApiKey\"")
            // Public RevenueCat key (see defaultConfig note). Blank when the
            // env var is unset → SDK stays unconfigured, paywall degrades to
            // "billing unavailable". Pass as a GitHub Actions variable.
            val prodRevenueCatKey: String = System.getenv("REVENUECAT_API_KEY")?.takeIf { it.isNotBlank() } ?: ""
            buildConfigField("String", "REVENUECAT_API_KEY", "\"$prodRevenueCatKey\"")
        }
        debug {
            // Allow developers to point at a remote server (e.g. Hetzner) without
            // modifying source code — export COACH_API_BASE / COACH_API_KEY in the
            // shell, then re-sync Gradle (Step 3.4).
            val debugApiBase: String = System.getenv("COACH_API_BASE") ?: "https://cereveon.com"
            val debugApiKey: String = System.getenv("COACH_API_KEY") ?: "dev-key"
            buildConfigField("String", "COACH_API_BASE", "\"$debugApiBase\"")
            buildConfigField("String", "COACH_API_KEY", "\"$debugApiKey\"")
            val debugRevenueCatKey: String = System.getenv("REVENUECAT_API_KEY") ?: ""
            buildConfigField("String", "REVENUECAT_API_KEY", "\"$debugRevenueCatKey\"")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    // ``kotlinOptions { jvmTarget = "17" }`` was deprecated in Kotlin 2.0 and
    // removed in 2.3 (Android toolchain bump 2026-05-13).  Migration target
    // per https://kotl.in/u1r8ln is the ``compilerOptions`` DSL keyed on the
    // typed ``JvmTarget`` enum rather than a raw string.
    kotlin {
        compilerOptions {
            jvmTarget.set(JvmTarget.JVM_17)
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.19.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("com.google.android.material:material:1.14.0")

    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.activity:activity-ktx:1.12.2")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    implementation(libs.kotlinx.serialization.json)
    // RevenueCat — the app's billing SDK (PaywallActivity purchase flow).
    // RevenueCat validates receipts with Play and acknowledges the purchase;
    // a successful purchase activates the "pro" entitlement, and RevenueCat's
    // webhook (POST /billing/revenuecat/webhook) flips the server-side plan.
    // Brings its own Play Billing client transitively, so we no longer depend
    // on com.android.billingclient directly (avoids a duplicate/conflicting
    // billing-client version).
    implementation("com.revenuecat.purchases:purchases:10.15.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.11.0")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    // Real org.json implementation — overrides the Android stub (android.jar) so that
    // production clients that use JSONObject can be exercised in host JVM unit tests.
    testImplementation("org.json:json:20260522")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
    // FragmentScenario for the instrumented drill-walk test.  Pinned to the
    // RESOLVED transitive androidx.fragment version (1.5.4, via appcompat /
    // material) so it lifts nothing app-wide.  Pre-1.6 fragment has no
    // -manifest split artifact, so this is debugImplementation by design
    // (the documented pattern: it contributes only its test activity to the
    // debug manifest; release builds are untouched).
    debugImplementation("androidx.fragment:fragment-testing:1.5.4")
    // fragment-testing 1.5.4 transitively drags androidx.test:core/monitor
    // 1.4.0 into the DEBUG app APK; the test APK's AndroidJUnitRunner (via
    // ext:junit 1.3.0) needs monitor >= 1.5 (FileTestStorage) and the build
    // dedupes shared classes toward the app APK — the instrumentation
    // process then dies at bind with NoClassDefFoundError before any test
    // runs.  Force the app-side test core up to the same generation the
    // test stack uses.  Debug-only; release untouched.
    debugImplementation("androidx.test:core:1.7.0")
}
