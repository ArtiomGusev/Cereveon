# Add project specific ProGuard rules here.
# You can control the set of applied configuration files using the
# proguardFiles setting in build.gradle.
#
# For more details, see
#   http://developer.android.com/guide/developing/tools/proguard.html

# Preserve stack trace information for crash reporting.
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile

# API model classes are serialized/deserialized via org.json (JSONObject field
# access by string name). Keep their public members so R8 does not rename them.
-keepclassmembers class com.cereveon.myapp.**ApiModels** { public *; }
-keepclassmembers class com.cereveon.myapp.**Models** { public *; }
-keep class com.cereveon.myapp.CoachApiModels** { *; }
-keep class com.cereveon.myapp.GameApiModels** { *; }
-keep class com.cereveon.myapp.AuthApiModels** { *; }
-keep class com.cereveon.myapp.EngineEvalApiModels** { *; }
-keep class com.cereveon.myapp.LiveMoveApiModels** { *; }

# Kotlin coroutines
-keepclassmembernames class kotlinx.** {
    volatile <fields>;
}
-keepnames class kotlinx.coroutines.** { *; }

# AndroidX Security Crypto / EncryptedSharedPreferences
-keep class androidx.security.crypto.** { *; }

# Kotlin metadata (needed for reflection used by coroutines)
-keepattributes *Annotation*, Signature, InnerClasses, EnclosingMethod

# javax.annotation classes are referenced by Google Tink (pulled in transitively
# by androidx.security:security-crypto) but are not present at runtime on Android.
# R8 generates missing_rules.txt with these; suppress to keep the build clean.
-dontwarn javax.annotation.Nullable
-dontwarn javax.annotation.concurrent.GuardedBy

# Strip verbose/debug logging from release builds: R8 removes calls whose
# return value is unused.  Drops Log.d / Log.v lines that echo game IDs and
# internal state IDs, while keeping Log.i / Log.w / Log.e for crash and error
# diagnostics.
-assumenosideeffects class android.util.Log {
    public static int v(...);
    public static int d(...);
}