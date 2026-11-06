package io.github.arknights1234.atkr.core

import java.text.Normalizer

/** Read access to the runtime dictionary, so this logic also runs on the PC for comparison with Python. */
interface DictionaryStore {
    fun exact(source: String): String?
    fun looseTemplate(key: String): String?
    fun templates(key: String): List<Pair<String, String>>
    fun startingWith(prefix: String, limit: Int): List<Pair<String, String>>
}

private val MARKUP = Regex("<[^>]*>")
val PLACEHOLDER = Regex("""\{[^{}]*\}|%[sd]""")
private val NUMBER = Regex("""[0-9]+(?:\.[0-9]+)?%?""")
private const val EDGE_SYMBOLS = " \t\r\n.,:;!?…·•>＜<|-_~*"
private val TEMPLATE_VALUE = """([+-]?[0-9]+(?:\.[0-9]+)?%?)"""
private val LABEL_VALUE = Regex("""(.+[：:])\s*([0-9OoIl/.]+)""")
private val SIMILAR_PREFIX = Regex("""[㐀-鿿0-9A-Za-z]{4,8}""")
private val QUOTE_PAIRS = listOf("“" to "”", "‘" to "’", "「" to "」", "『" to "』", "《" to "》", "（" to "）", "(" to ")")
private const val SIMILAR_MIN_LENGTH = 15
private const val SIMILAR_MIN_RATIO = 0.9
private const val SIMILAR_MAX_CANDIDATES = 300

fun isHanzi(char: Char) = char in '㐀'..'鿿'

fun hasHanzi(text: String) = text.any(::isHanzi)

/** Remove inline presentation tags such as <@ro.get> and </>. */
fun normalizeText(text: String) = MARKUP.replace(text, "").trim()

fun narrowForms(text: String) = text.replace('％', '%').replace('（', '(').replace('）', ')')

private fun nfkc(text: String) = Normalizer.normalize(text, Normalizer.Form.NFKC)

/** Compare form: no markup, line breaks or spacing, and full-width characters narrowed. */
fun foldText(text: String): String =
    nfkc(normalizeText(text)).replace("\\n", "").filterNot { it.isWhitespace() }

fun maskNumbers(text: String) = NUMBER.replace(PLACEHOLDER.replace(text, "#"), "#")

private fun String.trimEdges() = trim { it in EDGE_SYMBOLS }

/** Key that survives OCR differences: spacing, full-width forms, edge symbols, numbers. */
fun looseKey(text: String) = maskNumbers(foldText(text)).trimEdges()

fun fillNumbers(template: String, source: String): String? {
    val numbers = NUMBER.findAll(nfkc(source)).map { it.value }.toList()
    if (template.count { it == '#' } != numbers.size) return null
    val values = numbers.iterator()
    return Regex("#").replace(template) { values.next() }
}

/** The ratio from Python's difflib.SequenceMatcher with autojunk off. */
fun similarityRatio(a: String, b: String): Double {
    if (a.isEmpty() && b.isEmpty()) return 1.0
    val positions = HashMap<Char, MutableList<Int>>()
    b.forEachIndexed { index, char -> positions.getOrPut(char) { ArrayList() }.add(index) }

    fun longest(aLow: Int, aHigh: Int, bLow: Int, bHigh: Int): Triple<Int, Int, Int> {
        var bestI = aLow
        var bestJ = bLow
        var bestSize = 0
        var lengths = HashMap<Int, Int>()
        for (i in aLow until aHigh) {
            val next = HashMap<Int, Int>()
            for (j in positions[a[i]].orEmpty()) {
                if (j < bLow) continue
                if (j >= bHigh) break
                val size = (lengths[j - 1] ?: 0) + 1
                next[j] = size
                if (size > bestSize) {
                    bestI = i - size + 1
                    bestJ = j - size + 1
                    bestSize = size
                }
            }
            lengths = next
        }
        return Triple(bestI, bestJ, bestSize)
    }

    var matches = 0
    val queue = ArrayDeque<IntArray>()
    queue.addLast(intArrayOf(0, a.length, 0, b.length))
    while (queue.isNotEmpty()) {
        val (aLow, aHigh, bLow, bHigh) = queue.removeLast()
        val (i, j, size) = longest(aLow, aHigh, bLow, bHigh)
        if (size == 0) continue
        matches += size
        if (aLow < i && bLow < j) queue.addLast(intArrayOf(aLow, i, bLow, j))
        if (i + size < aHigh && j + size < bHigh) queue.addLast(intArrayOf(i + size, aHigh, j + size, bHigh))
    }
    return 2.0 * matches / (a.length + b.length)
}

/** Official Korean for a screen text, trying stricter matches first. Mirrors screen_translator.py. */
class OfficialLookup(private val store: DictionaryStore) {
    fun exact(source: String): String? = store.exact(source)?.replace("\\n", "\n")

    fun translate(source: String): String? {
        exact(source)?.let { return it }
        val narrow = narrowForms(source)
        if (narrow != source) exact(narrow)?.let { return it }
        for ((opening, closing) in QUOTE_PAIRS) {
            val opens = source.split(opening).size
            val closes = source.split(closing).size
            // OCR easily misses a faint quote mark at the very start or end of a text.
            val repaired = when {
                opens > closes -> exact(source + closing)
                closes > opens -> exact(opening + source)
                else -> null
            }
            if (repaired != null) return repaired
        }
        val key = looseKey(source)
        if (key != source && '#' !in key) exact(key)?.let { return it }
        template(source, key)?.let { return it }
        store.looseTemplate(key)?.let { template -> fillNumbers(template, source)?.let { return it } }
        LABEL_VALUE.matchEntire(source)?.let { match ->
            translate(match.groupValues[1])?.let { return "$it ${match.groupValues[2]}" }
        }
        return similar(source)
    }

    private fun template(source: String, key: String): String? {
        val screen = foldText(source).trimEdges()
        val results = store.templates(key).mapNotNull { (original, translation) -> fillTemplate(original, translation, screen) }.toSet()
        return results.singleOrNull()
    }

    private fun fillTemplate(source: String, translation: String, screen: String): String? {
        val folded = foldText(source).trimEdges()
        val slots = ArrayList<String>()
        val pattern = StringBuilder()
        var position = 0
        for (match in PLACEHOLDER.findAll(folded)) {
            pattern.append(Regex.escape(folded.substring(position, match.range.first))).append(TEMPLATE_VALUE)
            slots.add(match.value)
            position = match.range.last + 1
        }
        pattern.append(Regex.escape(folded.substring(position)))
        val found = Regex(pattern.toString()).matchEntire(screen) ?: return null
        val values = HashMap<String, String>()
        slots.forEachIndexed { index, slot ->
            val name = slotName(slot)
            var value = found.groupValues[index + 1]
            if ('%' in slot && !value.endsWith("%")) value += "%"
            if (values.getOrPut(name) { value } != value) return null
        }
        val filled = PLACEHOLDER.replace(translation) { values[slotName(it.value)] ?: it.value }.replace("\\n", "\n")
        return if (PLACEHOLDER.containsMatchIn(filled)) null else filled
    }

    private fun slotName(slot: String) = slot.trim('{', '}').substringBefore(':').trim().lowercase()

    /**
     * Official text for a long OCR line with small misreads. Candidates must start the same and hold
     * the same digits in the same order, so a different level of the same skill never matches.
     */
    private fun similar(source: String): String? {
        if (source.length < SIMILAR_MIN_LENGTH) return null
        val prefix = SIMILAR_PREFIX.matchAt(source, 0)?.value ?: return null
        val target = foldText(source)
        val digits = target.filter { it in '0'..'9' }
        var bestRatio = 0.0
        val best = HashSet<String>()
        for ((candidate, translation) in store.startingWith(prefix, SIMILAR_MAX_CANDIDATES)) {
            val folded = foldText(candidate)
            if (folded.filter { it in '0'..'9' } != digits) continue
            val ratio = similarityRatio(target, folded)
            if (ratio > bestRatio) {
                bestRatio = ratio
                best.clear()
                best.add(translation)
            } else if (ratio == bestRatio) {
                best.add(translation)
            }
        }
        return if (bestRatio >= SIMILAR_MIN_RATIO && best.size == 1) best.first() else null
    }
}
