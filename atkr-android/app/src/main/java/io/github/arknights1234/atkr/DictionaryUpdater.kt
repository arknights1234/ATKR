package io.github.arknights1234.atkr

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** Downloads the dictionary committed to the public repository, the same file the PC app uses. */
object DictionaryUpdater {
    private const val REPOSITORY = "arknights1234/ATKR"
    private const val BRANCH = "main"
    private const val REMOTE_PATH = "data/runtime_dictionary.sqlite3"
    private const val USER_AGENT = "ArknightsScreenTranslator"

    data class Outcome(val tag: String, val updated: Boolean, val lookups: Int, val terms: Int)

    private class Latest(val tag: String, val blobSha: String, val url: String)

    private fun open(url: String, timeoutMs: Int, api: Boolean): HttpURLConnection {
        val connection = URL(url).openConnection() as HttpURLConnection
        connection.connectTimeout = timeoutMs
        connection.readTimeout = timeoutMs
        connection.setRequestProperty("User-Agent", USER_AGENT)
        if (api) {
            connection.setRequestProperty("Accept", "application/vnd.github+json")
            connection.setRequestProperty("X-GitHub-Api-Version", "2022-11-28")
        }
        return connection
    }

    private fun getText(url: String): String {
        val connection = open(url, 25000, api = true)
        try {
            when (connection.responseCode) {
                in 200..299 -> return connection.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
                404 -> throw IOException("공개 저장소에서 사전 파일을 찾지 못했습니다")
                else -> throw IOException("GitHub API 오류 (HTTP ${connection.responseCode})")
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun latest(): Latest {
        val base = "https://api.github.com/repos/$REPOSITORY"
        val commits = JSONArray(getText("$base/commits?sha=$BRANCH&path=$REMOTE_PATH&per_page=1"))
        if (commits.length() == 0) throw IOException("저장소에 사전 파일 커밋이 없습니다")
        val commit = commits.getJSONObject(0)
        val sha = commit.getString("sha")
        val date = commit.getJSONObject("commit").getJSONObject("committer").getString("date").take(10).replace("-", "")
        val blob = JSONObject(getText("$base/contents/$REMOTE_PATH?ref=$sha")).getString("sha").lowercase()
        return Latest("$date-${sha.take(7)}", blob, "https://raw.githubusercontent.com/$REPOSITORY/$sha/$REMOTE_PATH")
    }

    /** Hash a file the way Git does, which is what GitHub reports for it. */
    private fun gitBlobSha(file: File): String {
        val digest = MessageDigest.getInstance("SHA-1")
        digest.update("blob ${file.length()}\u0000".toByteArray())
        file.inputStream().use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    class Release(val tag: String, val version: String, val url: String)

    /** Newest published release of the app, like latest_release in dictionary_updater.py. */
    fun latestRelease(): Release {
        val release = JSONObject(getText("https://api.github.com/repos/$REPOSITORY/releases/latest"))
        val tag = release.optString("tag_name")
        if (tag.isEmpty()) throw IOException("GitHub 응답에서 릴리스 정보를 읽지 못했습니다")
        val url = release.optString("html_url").ifEmpty { RELEASES_URL }
        return Release(tag, tag.trimStart('v', 'V'), url)
    }

    /** True when candidate (e.g. 1.3.1) is later than current; unreadable parts count as 0. */
    fun versionNewer(candidate: String, current: String): Boolean {
        fun parts(version: String) = version.trim().split('.', '-', '+').take(3).map { it.toIntOrNull() ?: 0 }
        val a = parts(candidate)
        val b = parts(current)
        for (index in 0 until maxOf(a.size, b.size)) {
            val x = a.getOrElse(index) { 0 }
            val y = b.getOrElse(index) { 0 }
            if (x != y) return x > y
        }
        return false
    }

    /** (newest tag, whether it differs from the dictionary in use), without downloading it. */
    fun updateAvailable(context: Context): Pair<String, Boolean> {
        val target = latest()
        return target.tag to (gitBlobSha(Dictionary.activeFile(context)) != target.blobSha)
    }

    const val RELEASES_URL = "https://github.com/$REPOSITORY/releases/latest"

    /** Runs on a background thread; throws with a message for the user when it fails. */
    fun update(context: Context): Outcome {
        val target = latest()
        val active = Dictionary.activeFile(context)
        if (gitBlobSha(active) == target.blobSha) {
            val (lookups, terms) = Dictionary.validate(active)
            return Outcome(target.tag, false, lookups, terms)
        }
        val safeTag = target.tag.replace(Regex("[^A-Za-z0-9._-]+"), "_")
        val partial = File(context.cacheDir, "dictionary_download.part")
        try {
            val connection = open(target.url, 60000, api = false)
            try {
                if (connection.responseCode !in 200..299) throw IOException("사전 다운로드 실패 (HTTP ${connection.responseCode})")
                connection.inputStream.use { input -> partial.outputStream().use { input.copyTo(it) } }
            } finally {
                connection.disconnect()
            }
            if (gitBlobSha(partial) != target.blobSha) throw IOException("GitHub에 등록된 해시와 받은 사전 파일이 일치하지 않습니다")
            val (lookups, terms) = Dictionary.validate(partial)
            val installed = File(context.filesDir, "dictionary_update_$safeTag.sqlite3")
            partial.copyTo(installed, overwrite = true)
            Dictionary.activate(context, installed, target.tag)
            return Outcome(target.tag, true, lookups, terms)
        } finally {
            partial.delete()
        }
    }
}
