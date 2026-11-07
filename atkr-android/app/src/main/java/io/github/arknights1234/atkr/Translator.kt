package io.github.arknights1234.atkr

import android.content.Context
import android.util.Log
import io.github.arknights1234.atkr.core.Detection
import io.github.arknights1234.atkr.core.hasHanzi
import io.github.arknights1234.atkr.core.normalizeText
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** Dictionary first, then the DeepL cache, then DeepL. Mirrors make_translation_job in screen_translator.py. */
class Translator(context: Context, private val dictionary: Dictionary, private val official: (String) -> String?) {
    private val cacheFile = File(context.filesDir, "deepl_translation_cache.json")
    private val cache: JSONObject = runCatching { JSONObject(cacheFile.readText()) }.getOrDefault(JSONObject())
    private var glossaryId: String? = null
    private var glossaryChecked = false
    private val terms by lazy { dictionary.terms() }
    private val longestTerm by lazy { terms.keys.maxOfOrNull { it.length } ?: 0 }

    data class Counts(val dictionary: Int, val cache: Int, val deepl: Int)

    fun translate(detections: List<Detection>, apiKey: String?): Counts {
        val pending = ArrayList<Detection>()
        var dictionaryHits = 0
        var cacheHits = 0
        var glossaryTried = false
        for (item in detections) {
            val text = normalizeText(item.text)
            item.translation = text
            if (!hasHanzi(text)) continue
            val found = official(text)
            if (found != null) {
                item.translation = found
                dictionaryHits++
                continue
            }
            // Only text outside the dictionary needs DeepL, and the cache key depends on the glossary.
            if (apiKey != null && !glossaryChecked && !glossaryTried) {
                glossaryTried = true
                lookUpGlossary(apiKey)
            }
            val cached = cache.optString(cacheKey(text, glossaryId != null), "")
            if (cached.isNotEmpty()) {
                item.translation = cached
                cacheHits++
            } else {
                pending.add(item)
            }
        }
        if (pending.isNotEmpty() && apiKey != null) requestDeepL(pending, apiKey)
        return Counts(dictionaryHits, cacheHits, if (apiKey == null) 0 else pending.size)
    }

    private fun cacheKey(text: String, withGlossary: Boolean) = (if (withGlossary) "\u0001v3:" else "\u0001v2:") + text

    /** Glossaries belong to one DeepL account, so it is found by name on this key's account. */
    private fun lookUpGlossary(apiKey: String) {
        runCatching {
            val glossaries = JSONObject(call(apiKey, "/v2/glossaries", null)).getJSONArray("glossaries")
            var newest: JSONObject? = null
            for (index in 0 until glossaries.length()) {
                val item = glossaries.getJSONObject(index)
                val usable = item.optString("name").startsWith(GLOSSARY_NAME_PREFIX) && item.optBoolean("ready") &&
                    item.optString("source_lang").equals("ZH", true) && item.optString("target_lang").equals("KO", true)
                if (usable && (newest == null || item.optString("creation_time") > newest.optString("creation_time"))) newest = item
            }
            glossaryId = newest?.getString("glossary_id")
            glossaryChecked = true
        }.onFailure { Log.w(TAG, "용어집 확인 실패: $it") }
    }

    private fun requestDeepL(pending: List<Detection>, apiKey: String) {
        val texts = pending.map { normalizeText(it.text) }
        val withGlossary = glossaryId
        val results = runCatching {
            if (withGlossary == null) throw IOException("no glossary")
            val body = requestBody(texts).put("glossary_id", withGlossary)
            parse(call(apiKey, "/v2/translate", body), texts.size) to true
        }.recoverCatching {
            if (withGlossary != null) {
                // A deleted glossary must not stop translation, so the next run looks it up again.
                Log.w(TAG, "용어집 사용 실패, 없이 다시 시도: $it")
                glossaryId = null
                glossaryChecked = false
            }
            val body = requestBody(texts.map(::protectTerms)).put("tag_handling", "xml").put("ignore_tags", JSONArray().put("x"))
            parse(call(apiKey, "/v2/translate", body), texts.size).map(::restoreTerms) to false
        }.getOrElse {
            Log.w(TAG, "DeepL 번역 실패: $it")
            return
        }
        val (translations, usedGlossary) = results
        pending.forEachIndexed { index, item ->
            item.translation = translations[index]
            cache.put(cacheKey(texts[index], usedGlossary), translations[index])
        }
        runCatching { cacheFile.writeText(cache.toString()) }.onFailure { Log.w(TAG, "캐시 저장 실패: $it") }
    }

    private fun requestBody(texts: List<String>) = JSONObject()
        .put("text", JSONArray(texts))
        .put("source_lang", "ZH")
        .put("target_lang", "KO")
        .put("context", DEEPL_CONTEXT)

    private fun parse(response: String, expected: Int): List<String> {
        val translations = JSONObject(response).getJSONArray("translations")
        if (translations.length() != expected) throw IOException("DeepL 응답 개수가 다릅니다")
        return (0 until expected).map { translations.getJSONObject(it).getString("text") }
    }

    private fun call(apiKey: String, path: String, body: JSONObject?): String {
        val host = if (apiKey.endsWith(":fx")) "api-free.deepl.com" else "api.deepl.com"
        val connection = URL("https://$host$path").openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = TIMEOUT_MS
            connection.readTimeout = TIMEOUT_MS
            connection.setRequestProperty("Authorization", "DeepL-Auth-Key $apiKey")
            if (body != null) {
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(body.toString().toByteArray()) }
            }
            if (connection.responseCode !in 200..299) throw IOException("DeepL HTTP ${connection.responseCode}")
            return connection.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
        } finally {
            connection.disconnect()
        }
    }

    /** DeepL XML with official Korean terms in ignored tags, for accounts without a glossary. */
    private fun protectTerms(text: String): String {
        val output = StringBuilder()
        var position = 0
        while (position < text.length) {
            val term = (minOf(longestTerm, text.length - position) downTo 1)
                .firstNotNullOfOrNull { size -> terms[text.substring(position, position + size)]?.let { it to size } }
            if (term != null) {
                output.append("<x>").append(escape(term.first)).append("</x>")
                position += term.second
            } else {
                output.append(escape(text[position].toString()))
                position++
            }
        }
        return output.toString()
    }

    private fun escape(text: String) = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    private fun restoreTerms(text: String) =
        text.replace("<x>", "").replace("</x>", "").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")

    private companion object {
        const val TAG = "Translator"
        const val TIMEOUT_MS = 20000
        const val GLOSSARY_NAME_PREFIX = "arknights_glossary"
        const val DEEPL_CONTEXT = "명일방주(Arknights) 모바일 게임의 UI 문구와 대사. 作战은 작전, 干员은 오퍼레이터, 代理指挥는 대리 지휘."
    }
}
