package io.github.arknights1234.atkr

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ContentUris
import android.content.Intent
import android.database.ContentObserver
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.PixelFormat
import android.net.Uri
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.provider.MediaStore
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.ImageView
import android.widget.TextView
import io.github.arknights1234.atkr.core.LineGrouper
import io.github.arknights1234.atkr.core.OfficialLookup
import java.util.Locale

/** Waits for a new device screenshot and draws its translation over the current app. */
class ScreenshotService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private val thread = HandlerThread("screenshots").apply { start() }
    private val worker = Handler(thread.looper)
    private val images = MediaStore.Images.Media.EXTERNAL_CONTENT_URI
    private var engines: Engines? = null
    private var result: View? = null
    private var progress: View? = null
    private var lastId = -1L
    private var busy = false

    private class Engines(
        val ocr: PaddleOcr, val renderer: Renderer, val dictionary: Dictionary,
        val grouper: LineGrouper, val translator: Translator, val generation: Int,
    )

    private val observer = object : ContentObserver(worker) {
        override fun onChange(selfChange: Boolean, uri: Uri?) {
            checkNewest()
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        running = true
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "번역 패널", NotificationManager.IMPORTANCE_LOW),
        )
        startForeground(NOTIFICATION_ID, notification("스크린샷을 찍으면 번역합니다"))
        worker.post {
            // Screenshots taken before starting are not translated.
            lastId = newestScreenshot()?.first ?: -1L
            contentResolver.registerContentObserver(images, true, observer)
            // Loading now spares the first translation the wait. A failure is reported when one runs.
            runCatching { loadEngines() }.onFailure { Log.w(TAG, "엔진 미리 불러오기 실패: $it") }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) stopSelf()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        running = false
        contentResolver.unregisterContentObserver(observer)
        removeView(result)
        removeView(progress)
        result = null
        progress = null
        // The worker may still be translating, so the engines close on its thread, after that job.
        worker.post {
            engines?.ocr?.close()
            engines?.dictionary?.close()
        }
        thread.quitSafely()
        super.onDestroy()
    }

    private fun newestScreenshot(): Pair<Long, String>? {
        val columns = arrayOf(MediaStore.Images.Media._ID, MediaStore.Images.Media.DISPLAY_NAME)
        val selection = "${MediaStore.Images.Media.RELATIVE_PATH} LIKE ? AND ${MediaStore.Images.Media.IS_PENDING}=0"
        contentResolver.query(images, columns, selection, arrayOf("%Screenshots%"), "${MediaStore.Images.Media._ID} DESC")?.use { cursor ->
            if (cursor.moveToFirst()) return cursor.getLong(0) to cursor.getString(1)
        }
        return null
    }

    /** Runs on the worker thread; the media store reports one screenshot several times. */
    private fun checkNewest() {
        val (id, name) = newestScreenshot() ?: return
        if (id <= lastId) return
        lastId = id
        main.post {
            if (busy) return@post
            // A screenshot taken over our result shows the result, so it only closes it.
            if (result != null) {
                removeView(result)
                result = null
                return@post
            }
            busy = true
            showProgress()
            worker.post {
                // A failure on this thread would otherwise close the app with no message.
                runCatching { translate(id, name) }.onFailure { error ->
                    main.post { finish("오류: $error") }
                }
            }
        }
    }

    private fun decode(uri: Uri): Bitmap? {
        val options = BitmapFactory.Options().apply {
            inMutable = true
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        repeat(DECODE_ATTEMPTS) { attempt ->
            val bitmap = runCatching {
                contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, options) }
            }.getOrNull()
            if (bitmap != null) return bitmap
            if (attempt < DECODE_ATTEMPTS - 1) Thread.sleep(DECODE_RETRY_MS)
        }
        return null
    }

    /** Reuses what is loaded, reopening only the dictionary after an update. */
    private fun loadEngines(): Engines {
        val current = engines
        if (current != null && current.generation == Dictionary.generation) return current
        current?.dictionary?.close()
        val generation = Dictionary.generation
        val dictionary = Dictionary.open(this)
        val lookup = OfficialLookup(dictionary)
        return Engines(
            current?.ocr ?: PaddleOcr(this), current?.renderer ?: Renderer(this), dictionary,
            LineGrouper(lookup, SAME_LINE_OVERLAP_SHARE), Translator(this, dictionary, lookup::translate), generation,
        ).also { engines = it }
    }

    private fun translate(id: Long, name: String) {
        val started = SystemClock.elapsedRealtime()
        val bitmap = decode(ContentUris.withAppendedId(images, id))
        if (bitmap == null) {
            main.post { finish("이미지를 읽지 못했습니다: $name") }
            return
        }
        val parts = loadEngines()
        val ready = SystemClock.elapsedRealtime()
        val ocr = parts.ocr.recognize(bitmap)
        val read = SystemClock.elapsedRealtime()
        val grouped = parts.grouper.group(parts.grouper.filterNoise(ocr.lines))
        val counts = parts.translator.translate(grouped, sessionKey ?: SecureKey.load(this))
        val translated = SystemClock.elapsedRealtime()
        parts.renderer.render(bitmap, grouped)
        val done = SystemClock.elapsedRealtime()
        val status = String.format(
            Locale.US, "%.1f초 (준비 %.1f, OCR %.1f, 번역 %.1f, 그리기 %.1f) · 사전 %d, 캐시 %d, DeepL %d",
            (done - started) / 1000.0, (ready - started) / 1000.0, (read - ready) / 1000.0,
            (translated - read) / 1000.0, (done - translated) / 1000.0, counts.dictionary, counts.cache, counts.deepl,
        )
        main.post { show(bitmap, status) }
    }

    private fun show(bitmap: Bitmap, status: String) {
        val view = ImageView(this).apply {
            setImageBitmap(bitmap)
            scaleType = ImageView.ScaleType.FIT_XY
            setOnClickListener {
                removeView(this)
                result = null
                setImageDrawable(null)
                bitmap.recycle()
            }
        }
        getSystemService(WindowManager::class.java).addView(view, overlayParams(touchable = true))
        result = view
        finish(status)
    }

    private fun overlayParams(touchable: Boolean) = WindowManager.LayoutParams(
        WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or
            (if (touchable) 0 else WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE),
        PixelFormat.TRANSLUCENT,
    ).apply {
        // Screenshots include the camera cutout, so the overlay has to cover it too.
        layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
    }

    private fun showProgress() {
        val view = TextView(this).apply {
            text = "  번역 중…  "
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.argb(215, 30, 144, 255))
            textSize = 14f
            gravity = Gravity.CENTER
        }
        val params = overlayParams(touchable = false).apply {
            width = WindowManager.LayoutParams.WRAP_CONTENT
            height = WindowManager.LayoutParams.WRAP_CONTENT
            gravity = Gravity.TOP or Gravity.START
        }
        getSystemService(WindowManager::class.java).addView(view, params)
        progress = view
    }

    private fun finish(status: String) {
        removeView(progress)
        progress = null
        busy = false
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification(status))
    }

    private fun removeView(view: View?) {
        if (view != null) getSystemService(WindowManager::class.java).removeView(view)
    }

    private fun notification(status: String): Notification {
        val stop = PendingIntent.getService(
            this, 1, Intent(this, ScreenshotService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(status)
            .setStyle(Notification.BigTextStyle().bigText(status))
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "종료", stop).build())
            .build()
    }

    companion object {
        private const val ACTION_STOP = "io.github.arknights1234.atkr.STOP"
        private const val CHANNEL = "panel"
        private const val NOTIFICATION_ID = 1
        private const val TAG = "ScreenshotService"
        // Waits up to 1.2 seconds for the file, checking often so a ready file is not left waiting.
        private const val DECODE_ATTEMPTS = 25
        private const val DECODE_RETRY_MS = 50L
        private const val SAME_LINE_OVERLAP_SHARE = 0.25

        /** A key the user chose not to save; it lasts until the app process ends. */
        @Volatile
        var sessionKey: String? = null

        @Volatile
        var running = false
    }
}
