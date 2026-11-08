package io.github.arknights1234.atkr

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Switch
import android.widget.TextView

class MainActivity : Activity() {
    private lateinit var keyInput: EditText
    private lateinit var saveKey: Switch
    private lateinit var start: Button
    private lateinit var stop: Button
    private lateinit var status: TextView
    private lateinit var permissions: LinearLayout
    private lateinit var permissionStatus: TextView
    private lateinit var grantOverlay: Button
    private lateinit var dictionaryStatus: TextView
    private lateinit var updateButton: Button
    private lateinit var glossaryStatus: TextView
    private lateinit var glossaryButton: Button
    private lateinit var appStatus: TextView
    private lateinit var releaseButton: Button
    private var releaseUrl = DictionaryUpdater.RELEASES_URL

    private val runtimePermissions = arrayOf(Manifest.permission.POST_NOTIFICATIONS, Manifest.permission.READ_MEDIA_IMAGES)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        keyInput = findViewById(R.id.api_key)
        saveKey = findViewById(R.id.save_key)
        start = findViewById(R.id.start)
        stop = findViewById(R.id.stop)
        status = findViewById(R.id.status)
        permissions = findViewById(R.id.permissions)
        permissionStatus = findViewById(R.id.permission_status)
        grantOverlay = findViewById(R.id.grant_overlay)

        val savedKey = SecureKey.load(this)
        keyInput.setText(savedKey ?: ScreenshotService.sessionKey.orEmpty())
        saveKey.isChecked = savedKey != null || ScreenshotService.sessionKey == null
        findViewById<Switch>(R.id.show_key).setOnCheckedChangeListener { _, shown ->
            keyInput.inputType = InputType.TYPE_CLASS_TEXT or
                if (shown) InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD else InputType.TYPE_TEXT_VARIATION_PASSWORD
            keyInput.setSelection(keyInput.text.length)
        }
        grantOverlay.setOnClickListener {
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
        }
        start.setOnClickListener {
            applyKey()
            startForegroundService(Intent(this, ScreenshotService::class.java))
            finish()
        }
        stop.setOnClickListener {
            stopService(Intent(this, ScreenshotService::class.java))
            ScreenshotService.running = false
            refresh()
        }
        dictionaryStatus = findViewById(R.id.dictionary_status)
        updateButton = findViewById(R.id.update_dictionary)
        updateButton.setOnClickListener { updateDictionary() }
        dictionaryStatus.text = "현재 버전: ${Dictionary.versionLabel(this)}"
        glossaryStatus = findViewById(R.id.glossary_status)
        glossaryButton = findViewById(R.id.upload_glossary)
        glossaryButton.setOnClickListener { confirmGlossaryUpload() }
        appStatus = findViewById(R.id.app_status)
        releaseButton = findViewById(R.id.open_release)
        appStatus.text = "현재 ${BuildConfig.VERSION_NAME} · 새 버전 확인 중"
        releaseButton.setOnClickListener { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(releaseUrl))) }
        checkUpdates()
    }

    /** Asks GitHub once per screen whether a newer app release or dictionary exists. Nothing is downloaded. */
    private fun checkUpdates() {
        val appContext = applicationContext
        Thread {
            val release = runCatching { DictionaryUpdater.latestRelease() }.getOrNull()
            val dictionary = runCatching { DictionaryUpdater.updateAvailable(appContext) }.getOrNull()
            runOnUiThread {
                if (isDestroyed) return@runOnUiThread
                val current = BuildConfig.VERSION_NAME
                appStatus.text = when {
                    release == null -> "현재 $current · 새 버전 확인 실패"
                    DictionaryUpdater.versionNewer(release.version, current) -> {
                        releaseUrl = release.url
                        highlight(releaseButton, "새 버전 받기")
                        "현재 $current · 새 버전 ${release.tag}이 있습니다"
                    }
                    else -> "현재 $current · 최신 버전"
                }
                // A download the user already started reports its own result.
                if (dictionary != null && dictionary.second && updateButton.isEnabled) {
                    dictionaryStatus.text = "현재 버전: ${Dictionary.versionLabel(this)} · 새 사전 ${dictionary.first} 있음"
                    highlight(updateButton, "사전 업데이트")
                }
            }
        }.start()
    }

    private fun highlight(button: Button, text: String) {
        button.text = text
        button.setBackgroundResource(R.drawable.button_primary)
        button.setTextColor(getColorStateList(R.color.button_primary_text))
    }

    private fun confirmGlossaryUpload() {
        val apiKey = keyInput.text.toString().trim()
        if (apiKey.isEmpty()) {
            glossaryStatus.text = "DeepL API 키를 먼저 입력하세요."
            return
        }
        AlertDialog.Builder(this)
            .setTitle("DeepL 용어집 등록")
            .setMessage(
                "이 API 키의 DeepL 계정에 명일방주 용어집(중국어→한국어)을 등록합니다.\n" +
                    "이름이 ${GlossaryUploader.NAME_PREFIX}로 시작하는 이전 용어집은 새 용어집으로 바꿉니다.\n" +
                    "용어집 등록은 번역 사용량에 포함되지 않습니다."
            )
            .setPositiveButton("등록") { _, _ -> uploadGlossary(apiKey) }
            .setNegativeButton("취소", null)
            .show()
    }

    private fun uploadGlossary(apiKey: String) {
        glossaryButton.isEnabled = false
        glossaryStatus.text = "DeepL 계정에 용어집을 등록하는 중…"
        val appContext = applicationContext
        Thread {
            val message = runCatching { GlossaryUploader.upload(appContext, apiKey) }.fold(
                onSuccess = { if (ScreenshotService.running) "$it 번역 중지 후 다시 시작하면 적용됩니다." else it },
                onFailure = { "용어집 등록 실패: ${it.message}" },
            )
            runOnUiThread {
                if (!isDestroyed) {
                    glossaryButton.isEnabled = true
                    glossaryStatus.text = message
                }
            }
        }.start()
    }

    private fun updateDictionary() {
        updateButton.isEnabled = false
        dictionaryStatus.text = "최신 사전 확인 중… (받을 때 약 65MB)"
        val appContext = applicationContext
        Thread {
            val message = runCatching { DictionaryUpdater.update(appContext) }.fold(
                onSuccess = { outcome ->
                    val state = if (outcome.updated) "업데이트 완료" else "이미 최신"
                    "$state: ${outcome.tag} (${"%,d".format(outcome.lookups)}개)"
                },
                onFailure = { "업데이트 실패: ${it.message}" },
            )
            runOnUiThread {
                if (!isDestroyed) {
                    updateButton.isEnabled = true
                    updateButton.text = "업데이트 확인"
                    updateButton.setBackgroundResource(R.drawable.button_secondary)
                    updateButton.setTextColor(getColorStateList(R.color.button_secondary_text))
                    dictionaryStatus.text = message
                }
            }
        }.start()
    }

    /** Like the PC app: saved encrypted when the toggle is on; otherwise forgotten when the app closes. */
    private fun applyKey() {
        val typed = keyInput.text.toString().trim()
        if (saveKey.isChecked && typed.isNotEmpty()) {
            SecureKey.save(this, typed)
            ScreenshotService.sessionKey = null
        } else {
            SecureKey.clear(this)
            ScreenshotService.sessionKey = typed.ifEmpty { null }
        }
    }

    override fun onResume() {
        super.onResume()
        val missing = runtimePermissions.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) requestPermissions(missing.toTypedArray(), 1)
        refresh()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        refresh()
    }

    private fun refresh() {
        val needed = buildList {
            if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) add("알림")
            if (checkSelfPermission(Manifest.permission.READ_MEDIA_IMAGES) != PackageManager.PERMISSION_GRANTED) add("사진 읽기")
            if (!Settings.canDrawOverlays(this@MainActivity)) add("다른 앱 위에 표시")
        }
        val running = ScreenshotService.running
        permissions.visibility = if (needed.isEmpty()) View.GONE else View.VISIBLE
        permissionStatus.text = "필요한 권한: ${needed.joinToString(", ")}"
        grantOverlay.visibility = if (Settings.canDrawOverlays(this)) View.GONE else View.VISIBLE
        start.isEnabled = needed.isEmpty() && !running
        stop.isEnabled = running
        status.text = when {
            running -> "번역이 켜져 있습니다. 게임에서 스크린샷을 찍으세요."
            needed.isEmpty() -> "번역을 시작할 수 있습니다."
            else -> "권한을 허용하면 시작할 수 있습니다."
        }
    }
}
