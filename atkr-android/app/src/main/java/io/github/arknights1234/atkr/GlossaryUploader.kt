package io.github.arknights1234.atkr

import android.content.Context
import java.io.IOException
import java.io.Reader
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

/** Puts the bundled glossary on the key's DeepL account, like upload_glossary in translator_app.py. */
object GlossaryUploader {
    const val NAME_PREFIX = "arknights_glossary"
    const val ASSET = "glossary_arknights_glossary_v3.csv"
    private const val LOOKUP_TIMEOUT_MS = 8000
    private const val UPLOAD_TIMEOUT_MS = 60000

    /** Message for the screen; throws IOException when the upload fails. */
    fun upload(context: Context, apiKey: String): String {
        val name = ASSET.removePrefix("glossary_").removeSuffix(".csv")
        val entries = context.assets.open(ASSET).reader(Charsets.UTF_8).use { readGlossary(it) }
        if (entries.isEmpty()) throw IOException("용어집 파일이 비어 있습니다")
        val existing = JSONObject(call(apiKey, "GET", "/v2/glossaries", null, LOOKUP_TIMEOUT_MS)).getJSONArray("glossaries")
            .let { list -> (0 until list.length()).map { list.getJSONObject(it) } }
            .filter {
                it.optString("name").startsWith(NAME_PREFIX) &&
                    it.optString("source_lang").equals("ZH", true) && it.optString("target_lang").equals("KO", true)
            }
        if (existing.any { it.optString("name") == name && it.optBoolean("ready") && it.optInt("entry_count") == entries.size }) {
            return "이미 등록되어 있습니다: $name (용어 ${"%,d".format(entries.size)}개)"
        }
        val body = JSONObject()
            .put("name", name)
            .put("source_lang", "zh")
            .put("target_lang", "ko")
            .put("entries", entries.joinToString("\n") { "${it.first}\t${it.second}" })
            .put("entries_format", "tsv")
        val created = JSONObject(call(apiKey, "POST", "/v2/glossaries", body, UPLOAD_TIMEOUT_MS))
        // Only after the new glossary exists, so a failed upload leaves the old one in use.
        val failed = existing.count { runCatching { call(apiKey, "DELETE", "/v2/glossaries/${it.getString("glossary_id")}", null, LOOKUP_TIMEOUT_MS) }.isFailure }
        val count = created.optInt("entry_count", entries.size)
        return "용어집 등록 완료: $name (용어 ${"%,d".format(count)}개)" +
            if (failed > 0) ". 이전 용어집 ${failed}개는 지우지 못했지만, 새 용어집이 사용됩니다." else ""
    }

    /** (source, target) pairs from DeepL's CSV format: source, target, source language, target language. */
    fun readGlossary(reader: Reader): List<Pair<String, String>> {
        val entries = LinkedHashMap<String, String>()
        for (row in parseCsv(reader.readText().removePrefix("﻿"))) {
            if (row.size < 2) continue
            // TSV upload cannot carry tabs or line breaks inside a term.
            val source = collapseSpaces(row[0])
            val target = collapseSpaces(row[1])
            if (source.isNotEmpty() && target.isNotEmpty()) entries.putIfAbsent(source, target)
        }
        return entries.toList()
    }

    /** Python's " ".join(text.split()), which also treats no-break and ideographic spaces as spaces. */
    private val SPACES = Regex("[\\s\\u001C-\\u001F\\u0085\\u00A0\\u1680\\u2000-\\u200A\\u2028\\u2029\\u202F\\u205F\\u3000]+")

    private fun collapseSpaces(text: String) = text.split(SPACES).filter { it.isNotEmpty() }.joinToString(" ")

    /** RFC 4180 CSV: quoted fields may hold commas, line breaks and doubled quotes. */
    fun parseCsv(text: String): List<List<String>> {
        val rows = ArrayList<List<String>>()
        var row = ArrayList<String>()
        val field = StringBuilder()
        var quoted = false
        var index = 0
        while (index < text.length) {
            val char = text[index]
            when {
                quoted && char == '"' && index + 1 < text.length && text[index + 1] == '"' -> { field.append('"'); index++ }
                char == '"' -> quoted = !quoted
                !quoted && char == ',' -> { row.add(field.toString()); field.clear() }
                !quoted && (char == '\n' || char == '\r') -> {
                    if (char == '\r' && index + 1 < text.length && text[index + 1] == '\n') index++
                    row.add(field.toString()); field.clear()
                    if (row.any { it.isNotEmpty() }) rows.add(row)
                    row = ArrayList()
                }
                else -> field.append(char)
            }
            index++
        }
        row.add(field.toString())
        if (row.any { it.isNotEmpty() }) rows.add(row)
        return rows
    }

    private fun call(apiKey: String, method: String, path: String, body: JSONObject?, timeoutMs: Int): String {
        val host = if (apiKey.endsWith(":fx")) "api-free.deepl.com" else "api.deepl.com"
        val connection = URL("https://$host$path").openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = timeoutMs
            connection.readTimeout = timeoutMs
            connection.requestMethod = method
            connection.setRequestProperty("Authorization", "DeepL-Auth-Key $apiKey")
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val code = connection.responseCode
            if (code !in 200..299) {
                val detail = connection.errorStream?.use { it.readBytes().toString(Charsets.UTF_8) }.orEmpty()
                throw IOException("DeepL API 오류 (HTTP $code) $detail".trim())
            }
            return connection.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
        } finally {
            connection.disconnect()
        }
    }
}
