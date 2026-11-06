package io.github.arknights1234.atkr

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Rect
import android.os.SystemClock
import io.github.arknights1234.atkr.core.Box
import io.github.arknights1234.atkr.core.Detection
import java.nio.FloatBuffer
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * PP-OCRv5 mobile detection and recognition on ONNX Runtime.
 *
 * Mirrors the Python pipeline checked on the PC: text regions are axis-aligned because game UI text
 * is horizontal, which avoids needing OpenCV on the phone.
 */
class PaddleOcr(context: Context) : AutoCloseable {
    data class Result(val lines: List<Detection>, val detectMs: Long, val readMs: Long)

    private val env = OrtEnvironment.getEnvironment()
    private val options = OrtSession.SessionOptions()

    /** Which engine ended up running, for the test report. */
    val provider: String = runCatching {
        // XNNPACK has its own thread pool, so ONNX Runtime's is kept to one thread.
        options.addXnnpack(mapOf("intra_op_num_threads" to THREADS.toString()))
        options.setIntraOpNumThreads(1)
        "XNNPACK"
    }.getOrElse {
        options.setIntraOpNumThreads(THREADS)
        "CPU"
    }
    // Reading short lines side by side beat giving one line several threads: 2.2 times faster on the PC.
    private val readerOptions = OrtSession.SessionOptions().apply { setIntraOpNumThreads(1) }
    private val readers = Executors.newFixedThreadPool(READ_WORKERS)
    private val detector = env.createSession(context.assets.open("det.onnx").use { it.readBytes() }, options)
    private val reader = env.createSession(context.assets.open("rec.onnx").use { it.readBytes() }, readerOptions)
    private val characters: List<String> =
        listOf("") + context.assets.open("dict.txt").use { it.readBytes().toString(Charsets.UTF_8) }.split("\n")

    fun recognize(bitmap: Bitmap): Result {
        val started = SystemClock.elapsedRealtime()
        val boxes = detect(bitmap)
        val detected = SystemClock.elapsedRealtime()
        val jobs = boxes.map { box ->
            Callable {
                val (text, score) = read(bitmap, box)
                if (text.isBlank() || score < MIN_SCORE) null
                else Detection(text, score.toDouble(), Box(box.left, box.top, box.right, box.bottom))
            }
        }
        val lines = readers.invokeAll(jobs).mapNotNull { it.get() }
        return Result(lines, detected - started, SystemClock.elapsedRealtime() - detected)
    }

    private fun detect(bitmap: Bitmap): List<Rect> {
        val scale = DETECT_LONG_SIDE.toFloat() / max(bitmap.width, bitmap.height)
        val width = max(32, (bitmap.width * scale / 32).roundToInt() * 32)
        val height = max(32, (bitmap.height * scale / 32).roundToInt() * 32)
        val input = pixels(bitmap, width, height, DETECT_MEAN, DETECT_STD)
        val probabilities = FloatArray(width * height)
        OnnxTensor.createTensor(env, input, longArrayOf(1, 3, height.toLong(), width.toLong())).use { tensor ->
            detector.run(mapOf(detector.inputNames.first() to tensor)).use { output ->
                (output[0] as OnnxTensor).floatBuffer.get(probabilities)
            }
        }
        return regions(probabilities, width, height).map { region ->
            Rect(
                region.left * bitmap.width / width,
                region.top * bitmap.height / height,
                min(bitmap.width, ceil(region.right * bitmap.width.toFloat() / width).toInt()),
                min(bitmap.height, ceil(region.bottom * bitmap.height.toFloat() / height).toInt()),
            )
        }.sortedWith(compareBy({ it.top }, { it.left }))
    }

    /** Connected regions of likely text, kept if confident enough and grown back to the glyph edges. */
    private fun regions(map: FloatArray, width: Int, height: Int): List<Rect> {
        val seen = BooleanArray(map.size)
        val stack = IntArray(map.size)
        val found = ArrayList<Rect>()
        for (start in map.indices) {
            if (seen[start] || map[start] <= BINARY_THRESHOLD) continue
            var size = 0
            stack[size++] = start
            seen[start] = true
            var left = width
            var top = height
            var right = 0
            var bottom = 0
            var sum = 0f
            var count = 0
            while (size > 0) {
                val index = stack[--size]
                val x = index % width
                val y = index / width
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)
                sum += map[index]
                count++
                if (x > 0) size = visit(index - 1, map, seen, stack, size)
                if (x < width - 1) size = visit(index + 1, map, seen, stack, size)
                if (y > 0) size = visit(index - width, map, seen, stack, size)
                if (y < height - 1) size = visit(index + width, map, seen, stack, size)
            }
            val regionWidth = right - left + 1
            val regionHeight = bottom - top + 1
            if (regionWidth < 3 || regionHeight < 3 || sum / count < BOX_THRESHOLD) continue
            val pad = (regionWidth * regionHeight * UNCLIP_RATIO / (2 * (regionWidth + regionHeight))).toInt()
            found.add(Rect(max(0, left - pad), max(0, top - pad), min(width, left + regionWidth + pad), min(height, top + regionHeight + pad)))
        }
        return found
    }

    private fun visit(index: Int, map: FloatArray, seen: BooleanArray, stack: IntArray, size: Int): Int {
        if (seen[index] || map[index] <= BINARY_THRESHOLD) return size
        seen[index] = true
        stack[size] = index
        return size + 1
    }

    private fun read(bitmap: Bitmap, box: Rect): Pair<String, Float> {
        if (box.width() <= 0 || box.height() <= 0) return "" to 0f
        val crop = Bitmap.createBitmap(bitmap, box.left, box.top, box.width(), box.height())
        val width = min(MAX_READ_WIDTH, max(16, ceil(READ_HEIGHT.toFloat() * box.width() / box.height()).toInt()))
        val input = pixels(crop, width, READ_HEIGHT, READ_MEAN, READ_STD)
        crop.recycle()
        OnnxTensor.createTensor(env, input, longArrayOf(1, 3, READ_HEIGHT.toLong(), width.toLong())).use { tensor ->
            reader.run(mapOf(reader.inputNames.first() to tensor)).use { output ->
                val result = output[0] as OnnxTensor
                val shape = result.info.shape
                val logits = FloatArray((shape[1] * shape[2]).toInt())
                result.floatBuffer.get(logits)
                return decode(logits, shape[1].toInt(), shape[2].toInt())
            }
        }
    }

    /** Greedy CTC: drop blanks (index 0) and repeats. */
    private fun decode(logits: FloatArray, steps: Int, classes: Int): Pair<String, Float> {
        val text = StringBuilder()
        var total = 0f
        var kept = 0
        var last = 0
        for (step in 0 until steps) {
            var best = 0
            var bestScore = -1f
            for (index in 0 until classes) {
                val score = logits[step * classes + index]
                if (score > bestScore) {
                    bestScore = score
                    best = index
                }
            }
            if (best != 0 && best != last && best < characters.size) {
                text.append(characters[best])
                total += bestScore
                kept++
            }
            last = best
        }
        return text.toString() to if (kept == 0) 0f else total / kept
    }

    /** CHW floats in BGR order, the order these models were trained on. */
    private fun pixels(source: Bitmap, width: Int, height: Int, mean: FloatArray, std: FloatArray): FloatBuffer {
        val scaled = Bitmap.createScaledBitmap(source, width, height, true)
        val colors = IntArray(width * height)
        scaled.getPixels(colors, 0, width, 0, 0, width, height)
        if (scaled !== source) scaled.recycle()
        val buffer = FloatBuffer.allocate(3 * width * height)
        val count = width * height
        for (i in 0 until count) buffer.put(i, ((colors[i] and 0xFF) / 255f - mean[0]) / std[0])
        for (i in 0 until count) buffer.put(count + i, (((colors[i] shr 8) and 0xFF) / 255f - mean[1]) / std[1])
        for (i in 0 until count) buffer.put(2 * count + i, (((colors[i] shr 16) and 0xFF) / 255f - mean[2]) / std[2])
        buffer.rewind()
        return buffer
    }

    override fun close() {
        readers.shutdown()
        detector.close()
        reader.close()
        options.close()
        readerOptions.close()
    }

    private companion object {
        const val THREADS = 4
        const val READ_WORKERS = 4
        const val DETECT_LONG_SIDE = 736
        const val BINARY_THRESHOLD = 0.3f
        const val BOX_THRESHOLD = 0.6f
        const val UNCLIP_RATIO = 1.5f
        const val READ_HEIGHT = 48
        const val MAX_READ_WIDTH = 3200
        const val MIN_SCORE = 0.6f
        val DETECT_MEAN = floatArrayOf(0.485f, 0.456f, 0.406f)
        val DETECT_STD = floatArrayOf(0.229f, 0.224f, 0.225f)
        val READ_MEAN = floatArrayOf(0.5f, 0.5f, 0.5f)
        val READ_STD = floatArrayOf(0.5f, 0.5f, 0.5f)
    }
}
