plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "io.github.arknights1234.atkr"
    compileSdk = 34

    defaultConfig {
        applicationId = "io.github.arknights1234.atkr"
        // READ_MEDIA_IMAGES starts at Android 13, the version this test runs on.
        minSdk = 33
        targetSdk = 33
        versionCode = 9
        versionName = "1.0.1"
        ndk {
            abiFilters += "arm64-v8a"
        }
    }

    androidResources {
        noCompress += "onnx"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        buildConfig = true
    }

    applicationVariants.all {
        outputs.all {
            (this as com.android.build.gradle.internal.api.BaseVariantOutputImpl).outputFileName = "ArknightsScreenTranslator.apk"
        }
    }
}

// The dictionary and font are the PC app's files, so there is one copy of each in the repository.
val sharedAssets = layout.buildDirectory.dir("generated/sharedAssets")
val copySharedAssets = tasks.register<Copy>("copySharedAssets") {
    from(rootDir.resolve("../data/runtime_dictionary.sqlite3"))
    from(rootDir.resolve("../font/NotoSansCJKkr-Medium.otf"))
    from(rootDir.resolve("../font/LICENSE.txt")) { rename { "font_license.txt" } }
    into(sharedAssets)
}
android.sourceSets.getByName("main").assets.srcDir(sharedAssets)
tasks.named("preBuild") { dependsOn(copySharedAssets) }

dependencies {
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.20.0")
    testImplementation("junit:junit:4.13.2")
    // Test sources stay local because their expected results hold game text; see .gitignore.
    testImplementation("org.json:json:20240303")
    testImplementation("org.xerial:sqlite-jdbc:3.46.1.3")
}
