package io.github.arknights1234.atkr

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import io.github.arknights1234.atkr.core.DictionaryStore
import java.io.File

/** The runtime dictionary: the copy inside the APK, or a newer one downloaded from GitHub. */
class Dictionary private constructor(private val database: SQLiteDatabase) : DictionaryStore, AutoCloseable {

    private fun rows(sql: String, vararg args: String): List<Pair<String, String>> =
        database.rawQuery(sql, args).use { cursor ->
            val found = ArrayList<Pair<String, String>>(cursor.count)
            while (cursor.moveToNext()) {
                found.add(cursor.getString(0) to if (cursor.columnCount > 1) cursor.getString(1) else "")
            }
            found
        }

    override fun exact(source: String) = rows("SELECT translation_ko FROM lookup WHERE source_zh=?", source).firstOrNull()?.first

    override fun looseTemplate(key: String) = rows("SELECT template_ko FROM lookup_loose WHERE key=?", key).firstOrNull()?.first

    override fun templates(key: String) =
        rows("SELECT t.source_zh,l.translation_ko FROM lookup_templates t JOIN lookup l ON l.source_zh=t.source_zh WHERE t.key=?", key)

    override fun startingWith(prefix: String, limit: Int) =
        rows("SELECT source_zh,translation_ko FROM lookup WHERE source_zh>=? AND source_zh<? LIMIT $limit", prefix, prefix + "􏿿")

    fun terms(): Map<String, String> = rows("SELECT source_zh,translation_ko FROM terms").toMap()

    override fun close() = database.close()

    companion object {
        private const val ASSET = "runtime_dictionary.sqlite3"
        private const val PREFS = "dictionary"
        private const val FIELD_FILE = "file"
        private const val FIELD_TAG = "tag"
        private const val FIELD_APP_VERSION = "app_version"

        /** Goes up when a new dictionary becomes active, so a running translator reopens it. */
        @Volatile
        var generation = 0
            private set

        fun open(context: Context): Dictionary = Dictionary(openFile(activeFile(context)))

        private fun openFile(file: File) =
            SQLiteDatabase.openDatabase(file.path, null, SQLiteDatabase.OPEN_READONLY or SQLiteDatabase.NO_LOCALIZED_COLLATORS)

        private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

        /** SQLite needs a real file, so the copy inside the APK is unpacked on first use. */
        @Synchronized
        fun activeFile(context: Context): File {
            val settings = prefs(context)
            val downloaded = settings.getString(FIELD_FILE, null)?.let { File(context.filesDir, it) }
            // A new APK brings its own dictionary, which is at least as new as an older download.
            if (downloaded != null && downloaded.exists() && settings.getInt(FIELD_APP_VERSION, 0) == BuildConfig.VERSION_CODE) {
                return downloaded
            }
            settings.edit().clear().apply()
            val bundled = File(context.filesDir, "dictionary_v${BuildConfig.VERSION_CODE}.sqlite3")
            if (!bundled.exists()) {
                removeDictionaries(context, keep = null)
                val partial = File(context.filesDir, "$ASSET.part")
                context.assets.open(ASSET).use { input -> partial.outputStream().use { input.copyTo(it) } }
                check(partial.renameTo(bundled)) { "사전 파일을 저장하지 못했습니다" }
            }
            return bundled
        }

        fun versionLabel(context: Context): String = prefs(context).getString(FIELD_TAG, null) ?: "앱 포함본"

        @Synchronized
        fun activate(context: Context, file: File, tag: String) {
            prefs(context).edit()
                .putString(FIELD_FILE, file.name)
                .putString(FIELD_TAG, tag)
                .putInt(FIELD_APP_VERSION, BuildConfig.VERSION_CODE)
                .apply()
            // Linux keeps a deleted file readable for whoever still has it open.
            removeDictionaries(context, keep = file)
            generation++
        }

        /** Entry counts, or an exception if the file is not a sound dictionary. */
        fun validate(file: File): Pair<Int, Int> {
            openFile(file).use { database ->
                database.rawQuery("PRAGMA integrity_check", null).use { cursor ->
                    check(cursor.moveToFirst() && cursor.getString(0) == "ok") { "사전 파일이 손상되었습니다" }
                }
                val tables = database.rawQuery("SELECT name FROM sqlite_master WHERE type='table'", null).use { cursor ->
                    buildSet { while (cursor.moveToNext()) add(cursor.getString(0)) }
                }
                check(tables.containsAll(listOf("lookup", "terms", "metadata"))) { "올바른 사전 형식이 아닙니다" }
                fun count(table: String) = database.rawQuery("SELECT COUNT(*) FROM $table", null).use {
                    it.moveToFirst()
                    it.getInt(0)
                }
                return count("lookup") to count("terms")
            }
        }

        private fun removeDictionaries(context: Context, keep: File?) {
            context.filesDir.listFiles { file -> file.name.startsWith("dictionary_") && file != keep }?.forEach { it.delete() }
        }
    }
}
