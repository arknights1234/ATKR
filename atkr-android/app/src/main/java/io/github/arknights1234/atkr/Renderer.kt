package io.github.arknights1234.atkr

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import io.github.arknights1234.atkr.core.Box
import io.github.arknights1234.atkr.core.Detection
import io.github.arknights1234.atkr.core.LEFT_EDGE_TOLERANCE
import io.github.arknights1234.atkr.core.estimateTextWidth
import io.github.arknights1234.atkr.core.hasHanzi
import io.github.arknights1234.atkr.core.wrapToWidth
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 * Erases the original text and draws the translation, following render_translated_image on the PC.
 * The PC's OpenCV inpainting is replaced by filling the text inward from the surrounding pixels.
 * Its pixel sizes were tuned for a 900-pixel-high window, so they scale with the image height here.
 */
class Renderer(context: Context) {
    private val typeface = Typeface.createFromAsset(context.assets, "NotoSansCJKkr-Medium.otf")

    private class Item(val detection: Detection, val box: Box, val luma: Double)

    /** Draws on [bitmap], which must be mutable. */
    fun render(bitmap: Bitmap, detections: List<Detection>) {
        val width = bitmap.width
        val height = bitmap.height
        val scale = height / DESIGN_HEIGHT
        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
        val mask = BooleanArray(pixels.size)
        val regions = ArrayList<Box>()
        val items = ArrayList<Item>()
        val margin = (5 * scale).roundToInt()
        for (detection in detections) {
            val original = detection.text.trim()
            val translated = detection.translation?.trim().orEmpty()
            if (translated.isEmpty() || translated == original || !hasHanzi(original)) continue
            val box = Box(max(0, detection.box.left), max(0, detection.box.top), min(width, detection.box.right), min(height, detection.box.bottom))
            if (box.width <= 0 || box.height <= 0) continue
            // A single oversized region is decoration, such as the operator's big name.
            if (detection.sourceLines == null && box.width > 300 * scale && box.height > 90 * scale) continue
            val patch = Box(max(0, box.left - margin), max(0, box.top - margin), min(width, box.right + margin), min(height, box.bottom + margin))
            regions.add(patch)
            val background = ringMedian(pixels, width, patch, box)
            markText(pixels, mask, width, box, background)
            val luma = if (background == null) 50.0 else background[0] * 0.299 + background[1] * 0.587 + background[2] * 0.114
            items.add(Item(detection, box, luma))
        }
        if (items.isEmpty()) return
        for (region in mergeRegions(regions)) fillMasked(pixels, mask, width, region)
        bitmap.setPixels(pixels, 0, width, 0, 0, width, height)

        val canvas = Canvas(bitmap)
        val allBoxes = items.map { it.box }
        val textBoxes = detections.map { it.box }
        val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { typeface = this@Renderer.typeface }
        val stroke = Paint(fill).apply {
            style = Paint.Style.STROKE
            strokeWidth = STROKE_WIDTH * scale
            strokeJoin = Paint.Join.ROUND
        }
        for (item in items) draw(canvas, pixels, width, height, scale, item, allBoxes, textBoxes, fill, stroke)
    }

    private fun draw(
        canvas: Canvas, pixels: IntArray, width: Int, height: Int, scale: Float, item: Item,
        allBoxes: List<Box>, textBoxes: List<Box>, fill: Paint, stroke: Paint,
    ) {
        val box = item.box
        val text = item.detection.translation!!.trim()
        val minFont = MIN_FONT * scale
        val boxWidth = max(20 * scale, box.width.toFloat())
        val boxHeight = max(12 * scale, box.height.toFloat())
        val sourceBoxes = item.detection.sourceBoxes
        val sourceHeights = sourceBoxes?.map { max(1, it.height) }?.sorted().orEmpty()
        val lineHeight = if (sourceHeights.isEmpty()) boxHeight else sourceHeights[sourceHeights.size / 2].toFloat()
        var fontSize = max(minFont, min(MAX_FONT * scale, lineHeight * 0.84f))
        val singleLine = sourceBoxes == null
        var maxWidth = boxWidth * 1.1f
        var leftAligned = false
        if (singleLine) {
            leftAligned = sharesLeftEdge(box, allBoxes)
            maxWidth = max(maxWidth, horizontalRoom(pixels, width, box, textBoxes, leftAligned, scale))
            // A one-line label reads best as one line: use free space, then shrink.
            val fullWidth = estimateTextWidth(text, fontSize)
            if (fullWidth > maxWidth) {
                val fitted = fontSize * maxWidth / fullWidth
                if (fitted >= minFont) fontSize = fitted
            }
        }
        var lines = wrapToWidth(text, fontSize, maxWidth)
        repeat(3) {
            val fitted = (boxHeight + fontSize * 0.25f) / (lines.size * 1.15f)
            if (fitted < fontSize) {
                fontSize = max(minFont, fitted)
                lines = wrapToWidth(text, fontSize, maxWidth)
            }
        }
        val step = fontSize * 1.2f
        val blockHeight = lines.size * step
        var y = box.top.toFloat()
        if (singleLine) y = max(0f, min(box.top + (boxHeight - blockHeight) / 2, height - blockHeight))
        val dark = item.luma < 145
        fill.color = if (dark) Color.rgb(245, 245, 245) else Color.rgb(30, 30, 30)
        stroke.color = if (dark) Color.rgb(25, 25, 25) else Color.rgb(245, 245, 245)
        fill.textSize = fontSize
        stroke.textSize = fontSize
        val centered = singleLine && !leftAligned
        val blockWidth = lines.maxOf { fill.measureText(it) }
        val blockLeft = if (centered) max(0f, box.left + (boxWidth - blockWidth) / 2) else box.left.toFloat()
        lines.forEachIndexed { index, line ->
            val x = if (centered) blockLeft + (blockWidth - fill.measureText(line)) / 2 else blockLeft
            val baseline = y + index * step + fontSize * 0.95f
            canvas.drawText(line, x, baseline, stroke)
            canvas.drawText(line, x, baseline, fill)
        }
    }

    /** Median colour of the patch outside the text box, or null when the box fills the patch. */
    private fun ringMedian(pixels: IntArray, width: Int, patch: Box, box: Box): IntArray? {
        val red = IntArray(256)
        val green = IntArray(256)
        val blue = IntArray(256)
        var count = 0
        for (y in patch.top until patch.bottom) {
            for (x in patch.left until patch.right) {
                if (x >= box.left && x < box.right && y >= box.top && y < box.bottom) continue
                val color = pixels[y * width + x]
                red[Color.red(color)]++
                green[Color.green(color)]++
                blue[Color.blue(color)]++
                count++
            }
        }
        if (count == 0) return null
        return intArrayOf(median(red, count), median(green, count), median(blue, count))
    }

    private fun median(histogram: IntArray, count: Int): Int {
        var seen = 0
        for (value in histogram.indices) {
            seen += histogram[value]
            if (seen * 2 >= count) return value
        }
        return 255
    }

    private fun markText(pixels: IntArray, mask: BooleanArray, width: Int, box: Box, background: IntArray?) {
        if (background == null) return
        val found = ArrayList<Int>()
        for (y in box.top until box.bottom) {
            for (x in box.left until box.right) {
                val index = y * width + x
                val color = pixels[index]
                val dr = Color.red(color) - background[0]
                val dg = Color.green(color) - background[1]
                val db = Color.blue(color) - background[2]
                if (dr * dr + dg * dg + db * db > TEXT_CONTRAST * TEXT_CONTRAST) found.add(index)
            }
        }
        val height = mask.size / width
        // One pixel wider, to take the soft edges of the glyphs too.
        for (index in found) {
            val x = index % width
            val y = index / width
            for (dy in -1..1) {
                for (dx in -1..1) {
                    val nx = x + dx
                    val ny = y + dy
                    if (nx in 0 until width && ny in 0 until height) mask[ny * width + nx] = true
                }
            }
        }
    }

    /** Fills masked pixels layer by layer from the known pixels around them. */
    private fun fillMasked(pixels: IntArray, mask: BooleanArray, width: Int, region: Box) {
        val height = mask.size / width
        var remaining = ArrayList<Int>()
        for (y in region.top until region.bottom) {
            for (x in region.left until region.right) {
                if (mask[y * width + x]) remaining.add(y * width + x)
            }
        }
        while (remaining.isNotEmpty()) {
            val next = ArrayList<Int>()
            val solved = ArrayList<Pair<Int, Int>>()
            for (index in remaining) {
                val x = index % width
                val y = index / width
                var red = 0
                var green = 0
                var blue = 0
                var known = 0
                for (dy in -1..1) {
                    for (dx in -1..1) {
                        val nx = x + dx
                        val ny = y + dy
                        if (nx !in 0 until width || ny !in 0 until height) continue
                        val neighbour = ny * width + nx
                        if (mask[neighbour]) continue
                        val color = pixels[neighbour]
                        red += Color.red(color)
                        green += Color.green(color)
                        blue += Color.blue(color)
                        known++
                    }
                }
                if (known == 0) next.add(index) else solved.add(index to Color.rgb(red / known, green / known, blue / known))
            }
            if (solved.isEmpty()) return
            for ((index, color) in solved) {
                pixels[index] = color
                mask[index] = false
            }
            remaining = next
        }
    }

    private fun mergeRegions(regions: List<Box>): List<Box> {
        val merged = ArrayList<Box>()
        for (start in regions.sortedWith(compareBy({ it.top }, { it.left }))) {
            var region = start
            var index = 0
            while (index < merged.size) {
                val other = merged[index]
                if (region.left <= other.right + 2 && region.right + 2 >= other.left && region.top <= other.bottom + 2 && region.bottom + 2 >= other.top) {
                    region = Box(min(region.left, other.left), min(region.top, other.top), max(region.right, other.right), max(region.bottom, other.bottom))
                    merged.removeAt(index)
                    index = 0
                } else {
                    index++
                }
            }
            merged.add(region)
        }
        return merged
    }

    /** True when a text just above or below starts at the same x, as in a left-aligned block. */
    private fun sharesLeftEdge(box: Box, others: List<Box>): Boolean = others.any { other ->
        if (other == box || abs(other.left - box.left) > LEFT_EDGE_TOLERANCE) return@any false
        val gap = max(other.top - box.bottom, box.top - other.bottom)
        gap >= 0 && gap <= LEFT_EDGE_MAX_GAP_RATIO * min(box.height, other.height)
    }

    /** Width a one-line text may use: up to other text or to where the background changes. */
    private fun horizontalRoom(pixels: IntArray, width: Int, box: Box, textBoxes: List<Box>, leftAligned: Boolean, scale: Float): Float {
        var leftLimit = 0
        var rightLimit = width
        for (other in textBoxes) {
            if (other == box) continue
            val overlap = min(box.bottom, other.bottom) - max(box.top, other.top)
            if (overlap <= ROOM_MIN_ROW_OVERLAP * min(box.height, other.height)) continue
            if (other.left >= box.right) rightLimit = min(rightLimit, other.left)
            else if (other.right <= box.left) leftLimit = max(leftLimit, other.right)
        }
        val background = boxMedian(pixels, width, box)
        val roomMargin = (ROOM_MARGIN * scale).roundToInt()

        fun freeColumns(columns: IntProgression): Int {
            var count = 0
            for (column in columns) {
                var changed = 0
                for (y in box.top until box.bottom) {
                    val color = pixels[y * width + column]
                    val dr = Color.red(color) - background[0]
                    val dg = Color.green(color) - background[1]
                    val db = Color.blue(color) - background[2]
                    if (sqrt((dr * dr + dg * dg + db * db).toDouble()) > ROOM_CONTRAST) changed++
                }
                if (changed > ROOM_CHANGED_SHARE * box.height) return max(0, count - roomMargin)
                count++
            }
            return max(0, count - roomMargin)
        }

        val right = freeColumns(box.right until rightLimit)
        if (leftAligned) return (box.width + right).toFloat()
        val left = freeColumns(box.left - 1 downTo leftLimit)
        return (box.width + 2 * min(left, right)).toFloat()
    }

    private fun boxMedian(pixels: IntArray, width: Int, box: Box): IntArray {
        val red = IntArray(256)
        val green = IntArray(256)
        val blue = IntArray(256)
        for (y in box.top until box.bottom) {
            for (x in box.left until box.right) {
                val color = pixels[y * width + x]
                red[Color.red(color)]++
                green[Color.green(color)]++
                blue[Color.blue(color)]++
            }
        }
        val count = box.width * box.height
        return intArrayOf(median(red, count), median(green, count), median(blue, count))
    }

    private companion object {
        const val DESIGN_HEIGHT = 900f
        const val MIN_FONT = 12f
        const val MAX_FONT = 32f
        const val STROKE_WIDTH = 2f
        const val TEXT_CONTRAST = 38
        const val LEFT_EDGE_MAX_GAP_RATIO = 2.0
        const val ROOM_MIN_ROW_OVERLAP = 0.3
        const val ROOM_CONTRAST = 38.0
        const val ROOM_CHANGED_SHARE = 0.25
        const val ROOM_MARGIN = 6
    }
}
