package org.openrailfanai.app

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.nio.charset.StandardCharsets
import java.util.UUID

data class StoredMessage(val role: String, val content: String, val meta: JSONObject?)
data class StoredConversation(
    val id: String,
    val title: String,
    val titleAuto: Boolean,
    val createdAt: Long,
    val updatedAt: Long,
    val messages: List<StoredMessage>,
    val totalTokens: Long = 0L,
)

/** Uses the existing WebUI session fields and retains all unknown JSON fields for old clients. */
class ConversationStore private constructor(private val directory: File) {
    constructor(context: Context) : this(context.applicationContext.filesDir)
    internal constructor(directory: File, isolated: Boolean) : this(directory)

    @Synchronized fun all(): List<StoredConversation> = readState().optString(CONVERSATIONS_KEY).toJSONArray().mapConversations()
    @Synchronized fun currentId(): String = readState().optString(CURRENT_KEY)
    @Synchronized fun current(): StoredConversation? = all().firstOrNull { it.id == currentId() }

    @Synchronized fun create(): StoredConversation {
        val now = System.currentTimeMillis()
        val raw = JSONObject().put("id", UUID.randomUUID().toString()).put("title", "新对话").put("titleAuto", true)
            .put("createdAt", now).put("updatedAt", now).put("messages", JSONArray())
        val state = readState()
        val existing = state.optString(CONVERSATIONS_KEY).toJSONArray()
        val list = JSONArray().put(raw)
        for (index in 0 until existing.length()) list.put(existing.opt(index))
        state.put(CONVERSATIONS_KEY, list.toString()).put(CURRENT_KEY, raw.getString("id"))
        persist(state)
        return raw.toConversation()
    }

    @Synchronized fun pendingOrCreate(): StoredConversation {
        val pending = all().firstOrNull { it.messages.isEmpty() }
        if (pending != null) {
            select(pending.id)
            return pending
        }
        return create()
    }

    @Synchronized fun draft(id: String): String {
        val list = readState().optString(CONVERSATIONS_KEY).toJSONArray()
        return (0 until list.length()).firstNotNullOfOrNull { index ->
            list.optJSONObject(index)?.takeIf { it.optString("id") == id }?.optString("draft", "")
        }.orEmpty()
    }

    @Synchronized fun saveDraft(id: String, text: String) {
        if (draft(id) != text) mutate(id) { it.put("draft", text) }
    }

    @Synchronized fun select(id: String) {
        if (all().none { it.id == id }) return
        persist(readState().put(CURRENT_KEY, id))
    }

    @Synchronized fun rename(id: String, title: String) {
        mutate(id) { conversation ->
            val clean = title.trim()
            conversation.put("title", clean.ifBlank { "新对话" }).put("titleAuto", clean.isBlank())
                .put("updatedAt", System.currentTimeMillis())
        }
    }

    @Synchronized fun delete(id: String) {
        val state = readState()
        val list = state.optString(CONVERSATIONS_KEY).toJSONArray()
        val next = JSONArray()
        for (index in 0 until list.length()) {
            val item = list.optJSONObject(index) ?: continue
            if (item.optString("id") != id) next.put(item)
        }
        state.put(CONVERSATIONS_KEY, next.toString())
        if (state.optString(CURRENT_KEY) == id) {
            state.put(CURRENT_KEY, next.optJSONObject(0)?.optString("id") ?: "")
        }
        persist(state)
        if (state.optString(CURRENT_KEY).isBlank()) create()
    }

    @Synchronized fun addUser(id: String, text: String) {
        mutate(id) { conversation ->
            conversation.put("totalTokens", conversation.recordedTokenTotal())
            val messages = conversation.optJSONArray("messages") ?: JSONArray()
            messages.put(JSONObject().put("role", "user").put("content", text))
            conversation.put("messages", trimMessages(messages)).put("updatedAt", System.currentTimeMillis())
            if (conversation.optBoolean("titleAuto", true)) {
                val title = text.replace(Regex("\\s+"), " ").trim().let { if (it.length > 18) it.take(18) + "…" else it }
                if (title.isNotBlank()) conversation.put("title", title)
            }
        }
    }

    @Synchronized fun addAssistant(id: String, content: String, meta: JSONObject) {
        mutate(id) { conversation ->
            val messages = conversation.optJSONArray("messages") ?: JSONArray()
            val previousTokens = conversation.recordedTokenTotal()
            conversation.put("totalTokens", previousTokens + (replyTokenUsage(meta) ?: 0L))
            messages.put(JSONObject().put("role", "assistant").put("content", content).put("meta", meta))
            conversation.put("messages", trimMessages(messages)).put("updatedAt", System.currentTimeMillis())
        }
    }

    fun search(query: String): List<StoredConversation> {
        val normalized = query.trim().lowercase()
        return all().filter { item -> normalized.isBlank() || item.title.lowercase().contains(normalized) || item.messages.any { it.content.lowercase().contains(normalized) } }
            .sortedByDescending { it.updatedAt }
    }

    fun subtitle(conversation: StoredConversation): String {
        val result = conversation.messages.asReversed().firstNotNullOfOrNull { message ->
            message.meta?.optJSONArray("displayResults")?.optJSONObject(0)
        }
        if (result != null) when (result.optString("kind")) {
            "emu_routing" -> return "${result.optJSONArray("records")?.length() ?: 0} 条交路记录"
            "train_schedule" -> {
                val from = result.optString("from_station"); val to = result.optString("to_station")
                if (from.isNotBlank() && to.isNotBlank()) return "$from → $to"
                return listOf(result.optString("train_code"), result.optString("date")).filter(String::isNotBlank).joinToString(" · ")
            }
        }
        val text = conversation.messages.lastOrNull { it.role == "user" }?.content.orEmpty()
        if (text == conversation.title || text.take(18) + "…" == conversation.title) return ""
        return if (text.length > 24) text.take(24) + "…" else text
    }

    private fun mutate(id: String, action: (JSONObject) -> Unit) {
        val state = readState()
        val list = state.optString(CONVERSATIONS_KEY).toJSONArray()
        var found = false
        for (index in 0 until list.length()) {
            val item = list.optJSONObject(index) ?: continue
            if (item.optString("id") == id) { action(item); found = true; break }
        }
        if (found) persist(state.put(CONVERSATIONS_KEY, list.toString()))
    }

    private fun trimMessages(messages: JSONArray): JSONArray {
        val first = (messages.length() - MAX_MESSAGES).coerceAtLeast(0)
        return JSONArray().also { result -> for (index in first until messages.length()) result.put(messages.opt(index)) }
    }

    private fun readState(): JSONObject = runCatching { val file = File(directory, STATE_FILE); if (file.isFile) JSONObject(file.readText(StandardCharsets.UTF_8)) else JSONObject() }.getOrDefault(JSONObject())

    private fun persist(state: JSONObject) {
        val file = File(directory, STATE_FILE)
        val backup = File(directory, BACKUP_FILE)
        if (file.isFile && !backup.exists()) file.copyTo(backup)
        val temp = File(directory, "$STATE_FILE.tmp")
        FileOutputStream(temp).use { out -> out.write(state.toString().toByteArray(StandardCharsets.UTF_8)); out.fd.sync() }
        if (!temp.renameTo(file)) {
            if (file.exists()) file.delete()
            check(temp.renameTo(file)) { "Unable to persist session state" }
        }
    }

    private fun String.toJSONArray(): JSONArray = runCatching { JSONArray(this) }.getOrDefault(JSONArray())

    private fun JSONArray.mapConversations(): List<StoredConversation> = (0 until length()).mapNotNull { optJSONObject(it)?.toConversation() }

    private fun JSONObject.recordedTokenTotal(): Long {
        if (has("totalTokens")) return optLong("totalTokens").coerceAtLeast(0L)
        val messages = optJSONArray("messages") ?: return 0L
        return (0 until messages.length()).sumOf { index ->
            messages.optJSONObject(index)?.takeIf { it.optString("role") == "assistant" }
                ?.optJSONObject("meta")?.let { replyTokenUsage(it) } ?: 0L
        }
    }

    private fun JSONObject.toConversation(): StoredConversation {
        val messages = optJSONArray("messages") ?: JSONArray()
        return StoredConversation(
            id = optString("id"), title = optString("title", "新对话"), titleAuto = optBoolean("titleAuto", true),
            createdAt = optLong("createdAt"), updatedAt = optLong("updatedAt"),
            totalTokens = recordedTokenTotal(),
            messages = (0 until messages.length()).mapNotNull { index -> messages.optJSONObject(index)?.let { message ->
                StoredMessage(message.optString("role"), message.optString("content"), message.optJSONObject("meta"))
            } },
        )
    }

    companion object {
        private const val STATE_FILE = "state.json"
        private const val BACKUP_FILE = "state.json.pre-compose-sessions.bak"
        private const val CONVERSATIONS_KEY = "railfan_conversations_v1"
        private const val CURRENT_KEY = "railfan_current_conv_v1"
        private const val MAX_MESSAGES = 120
    }
}

/** Server-reported usage only; missing values are not estimated from text. */
internal fun replyTokenUsage(meta: JSONObject?): Long? {
    val usage = meta?.optJSONObject("usage") ?: return null
    if (usage.has("total_tokens") && !usage.isNull("total_tokens")) {
        return usage.optLong("total_tokens").coerceAtLeast(0L)
    }
    if (usage.has("prompt_tokens") && usage.has("completion_tokens")) {
        return usage.optLong("prompt_tokens").coerceAtLeast(0L) + usage.optLong("completion_tokens").coerceAtLeast(0L)
    }
    return null
}
