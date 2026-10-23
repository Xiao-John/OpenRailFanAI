package org.openrailfanai.app

import android.content.Context
import android.util.AtomicFile
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.UUID

/** Private, conversation-scoped storage for imported files and regeneration snapshots. */
internal class AttachmentStore(context: Context) {
    private val root = File(context.applicationContext.filesDir, "attachments-v1")

    @Synchronized
    fun saveAttachment(conversationId: String, name: String, mime: String, bytes: ByteArray): JSONObject {
        val conversation = conversationDirectory(conversationId)
        validateName(name)
        require(name.substringAfterLast('.', "").lowercase() in
            AppAgent.documentExtensions + AppAgent.imageExtensions) { "不支持的文件类型" }
        require(bytes.size <= AppAgent.MAX_INPUT_BYTES) { "文件超过 8 MiB" }

        val digest = sha256(bytes)
        val blob = safeChild(File(conversation, "blobs"), digest)
        val blobDir = blob.parentFile ?: error("附件存储路径无效")
        if (!blobDir.exists() && !blobDir.mkdirs()) error("无法创建附件存储目录")
        val existing = if (blob.exists()) runCatching { readAtomic(blob, AppAgent.MAX_INPUT_BYTES) }.getOrNull() else null
        if (existing == null || existing.size != bytes.size || sha256(existing) != digest) {
            writeAtomic(blob, bytes)
        }
        return JSONObject()
            .put("id", digest)
            .put("name", name)
            .put("mime", mime)
            .put("size", bytes.size)
            .put("sha256", digest)
            .put("version", REF_VERSION)
    }

    @Synchronized
    fun load(conversationId: String, ref: JSONObject): JSONObject {
        val conversation = conversationDirectory(conversationId)
        val checked = try {
            validateRef(ref)
        } catch (error: Exception) {
            throw IllegalArgumentException("附件引用无效", error)
        }
        val blob = safeChild(File(conversation, "blobs"), checked.sha256)
        val bytes = readAtomic(blob, AppAgent.MAX_INPUT_BYTES) ?: error("附件不存在")
        require(bytes.size == checked.size) { "附件大小校验失败" }
        require(sha256(bytes) == checked.sha256) { "附件校验失败" }
        return JSONObject()
            .put("name", checked.name)
            .put("mime", checked.mime)
            .put("content_base64", Base64.encodeToString(bytes, Base64.NO_WRAP))
    }

    @Synchronized
    fun saveRequest(conversationId: String, snapshot: JSONObject): String {
        val conversation = conversationDirectory(conversationId)
        validateSnapshot(snapshot)
        rejectSensitiveKeys(snapshot)
        val requestId = UUID.randomUUID().toString()
        val requests = File(conversation, "requests")
        if (!requests.exists() && !requests.mkdirs()) error("无法创建请求存储目录")
        val file = safeChild(requests, "$requestId.json")
        val manifest = JSONObject()
            .put("version", MANIFEST_VERSION)
            .put("created_at", System.currentTimeMillis())
            .put("snapshot", JSONObject(snapshot.toString()))
        val bytes = manifest.toString().toByteArray(StandardCharsets.UTF_8)
        require(bytes.size <= 8 * 1024 * 1024) { "请求上下文过大，无法保存" }
        writeAtomic(file, bytes)
        return requestId
    }

    @Synchronized
    fun request(conversationId: String, requestId: String): JSONObject {
        val conversation = conversationDirectory(conversationId)
        val uuid = strictUuid(requestId, "请求编号无效")
        val file = safeChild(File(conversation, "requests"), "$uuid.json")
        val bytes = readAtomic(file) ?: error("请求记录不存在")
        val manifest = parseManifest(bytes)
        val snapshot = manifest.getJSONObject("snapshot")
        validateSnapshot(snapshot)
        rejectSensitiveKeys(snapshot)
        return JSONObject(snapshot.toString())
    }

    @Synchronized
    fun deleteConversation(conversationId: String) {
        val conversation = conversationDirectory(conversationId)
        if (conversation.exists() && !conversation.deleteRecursively()) error("无法删除会话附件")
    }

    @Synchronized
    fun cleanup(conversationId: String, requestIds: Set<String>, draftRefs: List<JSONObject>) {
        val conversation = conversationDirectory(conversationId)
        if (!conversation.exists()) return
        val now = System.currentTimeMillis()
        val cutoff = now - GC_AGE_MS
        val requests = File(conversation, "requests")
        val keep = requestIds.mapNotNull { runCatching { strictUuid(it, "") }.getOrNull() }.toSet()
        val referenced = mutableSetOf<String>()
        var safeToCollectBlobs = true

        try {
            for (ref in draftRefs) referenced += validateRef(ref).sha256
        } catch (_: Exception) {
            // An unreadable draft reference may point at any blob, so retain blobs this pass.
            safeToCollectBlobs = false
        }

        val staleManifests = mutableListOf<File>()
        val requestFiles: Array<File>? = if (requests.exists()) requests.listFiles() else emptyArray()
        if (requests.exists() && requestFiles == null) safeToCollectBlobs = false
        val manifests = requestFiles?.filter { it.isFile && it.name.endsWith(".json") }.orEmpty()
        for (file in manifests) {
            val id = file.name.removeSuffix(".json")
            if (runCatching { strictUuid(id, "") }.isFailure) {
                safeToCollectBlobs = false
                continue
            }
            val parsed = runCatching { parseManifest(readAtomic(file) ?: error("请求记录不存在")) }.getOrNull()
            if (parsed == null) {
                // Keep corrupt manifests and conservatively skip all blob collection this pass.
                safeToCollectBlobs = false
                continue
            }
            val snapshot = parsed.optJSONObject("snapshot")
            val refs = snapshot?.optJSONArray("attachments")
            if (snapshot == null || refs == null) {
                safeToCollectBlobs = false
                continue
            }
            val manifestRefs = runCatching {
                (0 until refs.length()).map { index ->
                    validateRef(refs.getJSONObject(index)).sha256
                }
            }.getOrNull()
            if (manifestRefs == null) {
                safeToCollectBlobs = false
                continue
            }
            if (id !in keep && file.lastModified() < cutoff) {
                staleManifests += file
            } else {
                referenced += manifestRefs
            }
        }

        staleManifests.forEach { AtomicFile(it).delete() }
        if (!safeToCollectBlobs) return

        val blobs = File(conversation, "blobs")
        blobs.listFiles()?.forEach { file ->
            if (!file.isFile || !SHA256_REGEX.matches(file.name)) return@forEach
            if (file.name !in referenced && file.lastModified() < cutoff) AtomicFile(file).delete()
        }
    }

    private fun conversationDirectory(id: String): File {
        val uuid = strictUuid(id, "会话编号无效")
        return safeChild(root, uuid)
    }

    private fun safeChild(parent: File, childName: String): File {
        require(childName.isNotBlank() && !childName.contains('/') && !childName.contains('\\')) { "存储路径无效" }
        val file = File(parent, childName)
        val parentCanonical = parent.canonicalFile
        val canonical = file.canonicalFile
        require(canonical.parentFile == parentCanonical) { "存储路径无效" }
        return file
    }

    private fun strictUuid(value: String, error: String): String {
        val parsed = runCatching { UUID.fromString(value) }.getOrNull()
        require(parsed != null && parsed.toString() == value.lowercase() && UUID_REGEX.matches(value)) { error.ifBlank { "编号无效" } }
        return parsed.toString()
    }

    private fun validateName(name: String) {
        require(name.isNotBlank() && name != "." && name != ".." &&
            !name.contains('/') && !name.contains('\\') && name.none { it.isISOControl() }) { "文件名无效" }
    }

    private data class CheckedRef(val sha256: String, val size: Int, val name: String, val mime: String)

    private fun validateRef(ref: JSONObject): CheckedRef {
        val id = ref.getString("id")
        val hash = ref.getString("sha256")
        require(SHA256_REGEX.matches(hash) && id == hash) { "附件引用无效" }
        require(ref.getInt("version") == REF_VERSION) { "附件版本不支持" }
        val size = ref.getLong("size")
        require(size in 0..AppAgent.MAX_INPUT_BYTES.toLong()) { "附件大小无效" }
        val name = ref.getString("name")
        validateName(name)
        require(name.substringAfterLast('.', "").lowercase() in
            AppAgent.documentExtensions + AppAgent.imageExtensions) { "不支持的文件类型" }
        return CheckedRef(hash, size.toInt(), name, ref.optString("mime"))
    }

    private fun validateSnapshot(snapshot: JSONObject) {
        require(snapshot.optInt("version") == 1 && snapshot.opt("message") is String && snapshot.getString("message").isNotBlank()) { "请求记录损坏" }
        val history = snapshot.optJSONArray("history") ?: error("请求记录缺少上下文")
        require(history.length() <= 20) { "请求上下文超出范围" }
        for (index in 0 until history.length()) {
            val item = history.getJSONObject(index)
            require(item.optString("role") in setOf("user", "assistant") && item.opt("content") is String) { "请求上下文损坏" }
        }
        require(snapshot.opt("thinking_enabled") is Boolean && snapshot.optJSONArray("input_types") != null) { "请求输入设置损坏" }
        require(snapshot.optJSONArray("attachments") != null) { "请求记录缺少附件引用" }
        val refs = snapshot.getJSONArray("attachments")
        try {
            for (index in 0 until refs.length()) validateRef(refs.getJSONObject(index))
        } catch (error: Exception) {
            throw IllegalArgumentException("请求记录中的附件引用无效", error)
        }
    }

    private fun rejectSensitiveKeys(value: Any?) {
        when (value) {
            is JSONObject -> {
                val iterator = value.keys()
                while (iterator.hasNext()) {
                    val key = iterator.next()
                    val normalized = key.lowercase().replace(Regex("[^a-z0-9]"), "")
                    require(normalized !in SENSITIVE_KEYS && !normalized.contains("apikey")) { "请求记录不能包含密钥或凭据" }
                    rejectSensitiveKeys(value.opt(key))
                }
            }
            is JSONArray -> for (index in 0 until value.length()) rejectSensitiveKeys(value.opt(index))
        }
    }

    private fun parseManifest(bytes: ByteArray): JSONObject = try {
        val manifest = JSONObject(String(bytes, StandardCharsets.UTF_8))
        require(manifest.getInt("version") == MANIFEST_VERSION) { "请求记录版本不支持" }
        require(manifest.getLong("created_at") > 0L) { "请求记录时间无效" }
        require(manifest.optJSONObject("snapshot") != null) { "请求记录损坏" }
        manifest
    } catch (error: Exception) {
        if (error.message?.any { it in '\u4e00'..'\u9fff' } == true) throw error
        throw IllegalStateException("请求记录损坏", error)
    }

    private fun readAtomic(file: File, maxBytes: Int = 8 * 1024 * 1024): ByteArray? {
        if (!file.exists() && !File(file.path + ".bak").exists()) return null
        return runCatching {
            AtomicFile(file).openRead().use { input ->
                val output = ByteArrayOutputStream()
                val buffer = ByteArray(8192)
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    require(output.size() + count <= maxBytes) { "本地文件大小无效" }
                    output.write(buffer, 0, count)
                }
                output.toByteArray()
            }
        }.getOrElse { throw IllegalStateException("本地文件损坏", it) }
    }

    private fun writeAtomic(file: File, bytes: ByteArray) {
        val parent = file.parentFile ?: error("存储路径无效")
        if (!parent.exists() && !parent.mkdirs()) error("无法创建本地存储目录")
        val atomic = AtomicFile(file)
        var stream: FileOutputStream? = null
        try {
            stream = atomic.startWrite()
            stream.write(bytes)
            stream.flush()
            stream.fd.sync()
            atomic.finishWrite(stream)
        } catch (error: Exception) {
            stream?.let { atomic.failWrite(it) }
            throw IllegalStateException("无法保存本地文件", error)
        }
    }

    private fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }

    private companion object {
        const val REF_VERSION = 1
        const val MANIFEST_VERSION = 1
        const val GC_AGE_MS = 24L * 60L * 60L * 1000L
        val UUID_REGEX = Regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
        val SHA256_REGEX = Regex("^[0-9a-f]{64}$")
        val SENSITIVE_KEYS = setOf(
            "apikey", "authorization", "accesstoken", "refreshtoken", "token",
            "key", "privatekey", "secret", "password", "credential", "credentials", "headers", "cookies",
        )
    }
}
