package io.github.arknights1234.atkr.core

import java.util.Collections
import java.util.IdentityHashMap
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

data class Box(val left: Int, val top: Int, val right: Int, val bottom: Int) {
    val width get() = right - left
    val height get() = bottom - top
}

class Detection(
    val text: String,
    val score: Double?,
    val box: Box,
    val sourceLines: List<String>? = null,
    val sourceBoxes: List<Box>? = null,
) {
    var translation: String? = null
}

private const val MIN_OCR_SCORE = 0.6
private const val MIN_SINGLE_HANZI_SCORE = 0.98
private const val LINE_GAP_MIN_BREAK = 8.0
private const val LINE_GAP_BREAK_RATIO = 2.5
private const val SAME_ROW_MIN_OVERLAP = 0.6
private const val SAME_ROW_MAX_GAP = 0.6
private const val CELL_NEIGHBOUR_MAX_GAP = 2.0
const val LEFT_EDGE_TOLERANCE = 6
private val NUMERIC_TAIL = Regex("""(?=.*[0-9%％])[0-9.]*[%％]?[)）,，.。]*""")
private const val NUMERIC_TAIL_MIN_PREVIOUS_CHARS = 12
private const val NUMERIC_TAIL_MAX_WIDTH_SHARE = 0.2

/** Turns raw OCR pieces into the texts to translate. Mirrors screen_translator.py. */
class LineGrouper(
    private val lookup: OfficialLookup,
    /** How far neighbours on a line may overlap, as a share of height. Null is the PC's fixed 2 pixels. */
    private val sameLineOverlapShare: Double? = null,
) {

    /** Drop icons misread as text, and ticker fragments repeated inside a fuller line. */
    fun filterNoise(detections: List<Detection>): List<Detection> {
        val kept = detections.filter { item ->
            val score = item.score ?: return@filter true
            val hanzi = item.text.count(::isHanzi)
            !(score < MIN_OCR_SCORE || (hanzi == 1 && score < MIN_SINGLE_HANZI_SCORE))
        }
        return kept.filterNot { item ->
            hasHanzi(item.text) && kept.any { other ->
                other !== item && other.text.length > item.text.length && item.text in other.text &&
                    min(item.box.bottom, other.box.bottom) - max(item.box.top, other.box.top) >
                    0.5 * min(item.box.height, other.box.height)
            }
        }
    }

    /** OCR sometimes cuts one printed line in two; rejoin close neighbours on the same row. */
    private fun joinSameLine(detections: List<Detection>): List<Detection> {
        val rest = detections.filterNot { hasHanzi(it.text) }
        val joined = ArrayList<Detection>()
        for (item in detections.filter { hasHanzi(it.text) }.sortedBy { it.box.left }) {
            val bounds = item.box
            val target = joined.indexOfFirst { other ->
                val box = other.box
                val overlap = min(bounds.bottom, box.bottom) - max(bounds.top, box.top)
                val gap = bounds.left - box.right
                val allowedOverlap = sameLineOverlapShare?.let { (it * min(bounds.height, box.height)).toInt() } ?: 2
                overlap >= SAME_ROW_MIN_OVERLAP * min(bounds.height, box.height) &&
                    bounds.height.toDouble() / box.height in 0.6..1.7 &&
                    gap >= -allowedOverlap && gap <= SAME_ROW_MAX_GAP * max(bounds.height, box.height) &&
                    !separateLabels(other.text, item.text)
            }
            if (target < 0) {
                joined.add(item)
                continue
            }
            val other = joined[target]
            joined[target] = combine(listOf(other, item))
        }
        return joined + rest
    }

    private fun combine(pieces: List<Detection>): Detection {
        val box = Box(
            pieces.minOf { it.box.left }, pieces.minOf { it.box.top },
            pieces.maxOf { it.box.right }, pieces.maxOf { it.box.bottom },
        )
        return Detection(pieces.joinToString("") { it.text }, pieces.mapNotNull { it.score }.minOrNull(), box)
    }

    /** Two neighbours that are each official text, but not together, are distinct labels. */
    private fun separateLabels(left: String, right: String) =
        lookup.translate(normalizeText(left)) != null && lookup.translate(normalizeText(right)) != null &&
            lookup.translate(normalizeText(left + right)) == null

    /** A separate text close beside a line marks a table cell or tag row, not wrapped prose. */
    private fun hasCellNeighbour(bounds: Box, all: List<Detection>): Boolean = all.any { other ->
        val box = other.box
        if (box == bounds) return@any false
        val overlap = min(bounds.bottom, box.bottom) - max(bounds.top, box.top)
        if (overlap < SAME_ROW_MIN_OVERLAP * min(bounds.height, box.height)) return@any false
        val gap = max(box.left - bounds.right, bounds.left - box.right)
        gap > SAME_ROW_MAX_GAP * bounds.height && gap <= CELL_NEIGHBOUR_MAX_GAP * bounds.height
    }

    /** Rejoin a line OCR cut at a wide gap, e.g. before "【", when official text confirms it. */
    private fun joinByOfficialText(detections: List<Detection>): List<Detection> {
        val lines = detections.sortedWith(compareBy({ it.box.top }, { it.box.left }))
        for (item in lines) {
            if (!hasHanzi(item.text)) continue
            val height = item.box.height
            val run = arrayListOf(item)
            while (true) {
                val last = run.last().box
                val next = lines.filter { pair ->
                    pair.box.left - last.right > 0 && pair.box.left - last.right <= CELL_NEIGHBOUR_MAX_GAP * height &&
                        min(pair.box.bottom, last.bottom) - max(pair.box.top, last.top) >= SAME_ROW_MIN_OVERLAP * min(height, pair.box.height)
                }.minByOrNull { it.box.left } ?: break
                run.add(next)
            }
            if (run.size < 2) continue
            val below = lines.filter {
                abs(it.box.left - item.box.left) <= LEFT_EDGE_TOLERANCE && it.box.top - item.box.bottom in 0..(height * 0.7).toInt() &&
                    it.box.top - item.box.bottom <= height * 0.7
            }.minByOrNull { it.box.top }
            val tails = listOfNotNull("", below?.let { normalizeText(it.text) })
            for (stop in run.size downTo 2) {
                val pieces = run.subList(0, stop)
                val text = pieces.joinToString("") { normalizeText(it.text) }
                if (tails.any { lookup.translate(text + it) != null }) {
                    val used = Collections.newSetFromMap(IdentityHashMap<Detection, Boolean>())
                    used.addAll(pieces)
                    return joinByOfficialText(lines.filterNot { it in used } + combine(pieces))
                }
            }
        }
        return lines
    }

    /** A number cut by a line wrap ("+2" / "0"), told apart from a value under a short label. */
    private fun wrapsIntoNumber(previousText: String, previous: Box, tail: Box) =
        previousText.length >= NUMERIC_TAIL_MIN_PREVIOUS_CHARS &&
            previousText.last() in "0123456789.+-" &&
            abs(tail.left - previous.left) <= LEFT_EDGE_TOLERANCE &&
            tail.width <= NUMERIC_TAIL_MAX_WIDTH_SHARE * previous.width

    /** OCR reads a lone wrapped "%" as "0%", turning +35% into +350%. Official text decides. */
    private fun correctMisreadPercent(group: List<Detection>): List<Detection> {
        if (group.last().text != "0%") return group
        val texts = group.dropLast(1).map { normalizeText(it.text) }
        val joined = texts.indices.map { texts.subList(it, texts.size).joinToString("") }
        if (joined.any { lookup.exact(it + "0%") != null }) return group
        if (joined.none { lookup.exact("$it%") != null }) return group
        val last = group.last()
        return group.dropLast(1) + Detection("%", last.score, last.box)
    }

    /** Join vertically wrapped Chinese OCR lines before dictionary/API translation. */
    fun group(detections: List<Detection>): List<Detection> {
        val positioned = joinByOfficialText(joinSameLine(detections)).sortedWith(compareBy({ it.box.top }, { it.box.left }))
        val cells = Collections.newSetFromMap(IdentityHashMap<Detection, Boolean>())
        positioned.filterTo(cells) { hasCellNeighbour(it.box, positioned) }

        val groups = ArrayList<MutableList<Detection>>()
        for (item in positioned) {
            val tail = NUMERIC_TAIL.matches(item.text)
            var target: MutableList<Detection>? = null
            if (item !in cells && (tail || hasHanzi(item.text))) {
                val currentHeight = max(1, item.box.height)
                var bestGap = Int.MAX_VALUE
                for (group in groups) {
                    val previous = group.last()
                    if (tail && !wrapsIntoNumber(previous.text, previous.box, item.box)) continue
                    val previousHeight = max(1, previous.box.height)
                    val gap = item.box.top - previous.box.bottom
                    val sameColumn = abs(item.box.left - previous.box.left) <= max(30, (max(previousHeight, currentHeight) * 1.5).toInt())
                    val similarHeight = currentHeight.toDouble() / previousHeight in 0.6..1.7
                    val close = gap >= -(min(previousHeight, currentHeight) * 0.35).toInt() &&
                        gap <= max(8, (max(previousHeight, currentHeight) * 0.7).toInt())
                    if (previous !in cells && hasHanzi(previous.text) && sameColumn && similarHeight && close && group.size < 8 && abs(gap) < bestGap) {
                        bestGap = abs(gap)
                        target = group
                    }
                }
            }
            if (target == null) groups.add(arrayListOf(item)) else target.add(item)
        }
        return groups.flatMap { splitGroup(it) }.map(::mergeLines)
    }

    private fun mergeLines(lines: List<Detection>): Detection {
        if (lines.size == 1) return lines[0]
        val box = Box(lines.minOf { it.box.left }, lines.minOf { it.box.top }, lines.maxOf { it.box.right }, lines.maxOf { it.box.bottom })
        val parts = lines.map { it.text }
        return Detection(parts.joinToString(""), lines.mapNotNull { it.score }.minOrNull(), box, parts, lines.map { it.box })
    }

    /** Consecutive lines forming an official string come first, then a jump in line spacing splits the rest. */
    private fun splitGroup(original: List<Detection>): List<List<Detection>> {
        if (original.size < 2) return listOf(original)
        val group = correctMisreadPercent(original)
        val texts = group.map { normalizeText(it.text) }
        val segments = ArrayList<List<Detection>>()
        val unmatched = ArrayList<Detection>()
        var start = 0
        while (start < group.size) {
            val end = (group.size downTo start + 1).firstOrNull { stop ->
                lookup.translate(texts.subList(start, stop).joinToString("")) != null
            }
            if (end == null) {
                unmatched.add(group[start])
                start++
                continue
            }
            if (unmatched.isNotEmpty()) {
                segments.addAll(splitBySpacing(unmatched.toList()))
                unmatched.clear()
            }
            segments.add(group.subList(start, end))
            start = end
        }
        if (unmatched.isNotEmpty()) segments.addAll(splitBySpacing(unmatched))
        return segments
    }

    /** Needs three lines or more: two lines give no normal spacing to compare with. */
    private fun splitBySpacing(lines: List<Detection>): List<List<Detection>> {
        if (lines.size < 3) return listOf(lines)
        val gaps = (0 until lines.size - 1).map { lines[it + 1].box.top - lines[it].box.bottom }
        val limit = max(LINE_GAP_MIN_BREAK, gaps.sorted()[gaps.size / 2] * LINE_GAP_BREAK_RATIO)
        val segments = arrayListOf(arrayListOf(lines[0]))
        gaps.forEachIndexed { index, gap ->
            if (gap > limit) segments.add(arrayListOf(lines[index + 1])) else segments.last().add(lines[index + 1])
        }
        return segments
    }
}
