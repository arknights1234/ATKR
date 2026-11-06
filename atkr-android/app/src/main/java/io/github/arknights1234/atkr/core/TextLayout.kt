package io.github.arknights1234.atkr.core

/** Inexpensive CJK and Korean width estimate, the same as the PC's. */
fun estimateTextWidth(text: String, size: Float): Float {
    var width = 0f
    for (char in text) {
        width += when {
            char.isWhitespace() -> size * 0.36f
            isWideCharacter(char) -> size * 1.02f
            else -> size * 0.62f
        }
    }
    return width
}

private fun isWideCharacter(char: Char): Boolean = when (char.code) {
    in 0x1100..0x115F, in 0x2E80..0xA4CF, in 0xAC00..0xD7A3, in 0xF900..0xFAFF,
    in 0xFE30..0xFE6F, in 0xFF00..0xFF60, in 0xFFE0..0xFFE6,
    in 0x2010..0x2027, in 0x2030..0x2033, 0x00D7, 0x00B0, 0x00B1, 0x00B7 -> true
    else -> false
}

/** Wrap at spaces, and inside a word only when it is wider than the line. */
fun wrapToWidth(text: String, size: Float, maxWidth: Float): List<String> {
    val output = ArrayList<String>()
    val space = estimateTextWidth(" ", size)
    for (paragraph in text.lines()) {
        var current = ""
        var currentWidth = 0f
        for (word in paragraph.split(Regex("\\s+")).filter { it.isNotEmpty() }) {
            val wordWidth = estimateTextWidth(word, size)
            if (current.isNotEmpty() && currentWidth + space + wordWidth <= maxWidth) {
                current += " $word"
                currentWidth += space + wordWidth
                continue
            }
            if (current.isNotEmpty()) output.add(current)
            current = ""
            currentWidth = 0f
            if (wordWidth <= maxWidth) {
                current = word
                currentWidth = wordWidth
                continue
            }
            for (char in word) {
                val charWidth = estimateTextWidth(char.toString(), size)
                if (current.isNotEmpty() && currentWidth + charWidth > maxWidth) {
                    output.add(current)
                    current = ""
                    currentWidth = 0f
                }
                current += char
                currentWidth += charWidth
            }
        }
        if (current.isNotEmpty()) output.add(current)
    }
    return output
}
