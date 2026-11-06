package io.github.arknights1234.atkr

import android.Manifest
import android.app.Activity
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
