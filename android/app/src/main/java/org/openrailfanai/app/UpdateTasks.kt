package org.openrailfanai.app

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.SystemClock
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.*
import org.json.JSONObject
import java.io.File

/** Application-owned state: settings screens only observe it; navigation never cancels work. */
internal class UpdateTasks private constructor(context: Context) {
    private val app = context.applicationContext
    private val preferences = app.getSharedPreferences("update_tasks", Context.MODE_PRIVATE)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val jobs = mutableMapOf<String, Job>()
    private val clients = mutableMapOf<String, UpdateClient>()
    private val foreground = mutableSetOf<String>()
    private val deadlines = mutableMapOf<String, Job>()
    private val pendingIds = mutableMapOf<String, String>()
    var software by mutableStateOf<JSONObject?>(null)
    var softwareMessage by mutableStateOf(preferences.getString("software_message", "").orEmpty())
    var softwareBusy by mutableStateOf(false)
    var softwareProgress by mutableStateOf<JSONObject?>(null)
    var softwareStartedAt by mutableStateOf(0L)
    var apkPath by mutableStateOf(preferences.getString("apk_path", "").orEmpty())
    var apkVersion by mutableStateOf(preferences.getString("apk_version", "").orEmpty())
    var local by mutableStateOf<JSONObject?>(null)
    var dictionary by mutableStateOf<JSONObject?>(null)
    var dictionaryMessage by mutableStateOf(preferences.getString("dictionary_message", "").orEmpty())
    var dictionaryBusy by mutableStateOf(false)
    var dictionaryProgress by mutableStateOf<JSONObject?>(null)
    var dictionaryStartedAt by mutableStateOf(0L)
    var focusKind by mutableStateOf("software")
        private set
    var focusRequest by mutableStateOf(0L)
        private set
    fun focus(kind: String) { focusKind = if (kind == "dictionary") kind else "software"; focusRequest++ }
    val hasForegroundWork get() = foreground.isNotEmpty()

    init {
        if (preferences.getBoolean("software_running", false)) softwareMessage = "上次下载已中断，请重新下载。"
        if (preferences.getBoolean("dictionary_running", false)) dictionaryMessage = "上次词典更新已中断，请刷新本地版本后确认。"
        if (apkPath.isNotBlank() && (!File(apkPath).isFile || apkVersion == SoftwareInstaller.installedVersion(app))) { apkPath = ""; apkVersion = "" }
        File(app.cacheDir, "software-updates").listFiles()?.filter { it.extension == "part" }?.forEach { it.delete() }
        persist()
    }
    private fun persist() {
        preferences.edit().putString("software_message", softwareMessage).putString("dictionary_message", dictionaryMessage)
            .putString("apk_path", apkPath).putString("apk_version", apkVersion)
            .putBoolean("software_running", "software" in foreground)
            .putBoolean("dictionary_running", "dictionary" in foreground).commit()
    }
    fun reportSoftwareMessage(message: String) { softwareMessage = message; persist() }
    private fun reserve(kind: String, message: String): Boolean {
        if (if (kind == "software") softwareBusy else dictionaryBusy) return false
        if (kind == "software") {
            softwareBusy = true; softwareMessage = message; softwareProgress = null; softwareStartedAt = SystemClock.elapsedRealtime()
        } else {
            dictionaryBusy = true; dictionaryMessage = message; dictionaryProgress = null; dictionaryStartedAt = SystemClock.elapsedRealtime()
        }
        return true
    }
    private fun operation(kind: String, base: String, refresh: Boolean = false, block: suspend (UpdateClient) -> Unit) {
        if (jobs[kind]?.isActive == true) return
        val client = UpdateClient(base); clients[kind] = client
        if (kind in foreground) deadlines[kind] = scope.launch {
            delay(30 * 60 * 1000L)
            cancel(kind, "更新超时，请返回设置确认状态后重试。")
        }
        jobs[kind] = scope.launch {
            try { block(client) }
            catch (_: CancellationException) { /* cancel() supplies the reason */ }
            catch (error: Exception) {
                if (kind == "software") softwareMessage = "操作失败：${error.message ?: "请稍后重试"}"
                else dictionaryMessage = "操作失败：${error.message ?: "请稍后重试"}"
            } finally {
                if (refresh) {
                    dictionaryProgress = JSONObject().put("stage", "refresh")
                    withContext(NonCancellable + Dispatchers.IO) {
                        val value = runCatching { UpdateClient(base).dictionaryLocal() }.getOrNull()
                        withContext(Dispatchers.Main) { if (value != null) local = value.optJSONObject("current") }
                    }
                }
                deadlines.remove(kind)?.cancel()
                clients.remove(kind); jobs.remove(kind); foreground.remove(kind); pendingIds.remove(kind)
                if (kind == "software") softwareBusy = false else dictionaryBusy = false
                persist()
            }
        }
    }
    fun refreshLocal(base: String) {
        if (!reserve("dictionary", "正在读取本地词典…")) return
        operation("dictionary", base) { client ->
            local = withContext(Dispatchers.IO) { client.dictionaryLocal() }.optJSONObject("current")
            dictionaryMessage = "已读取本地词典信息。"
        }
    }
    fun checkDictionary(base: String) {
        if (!reserve("dictionary", "正在检查时刻词典数据源…")) return
        dictionary = null
        operation("dictionary", base) { client ->
            val response = withContext(Dispatchers.IO) { client.dictionary() }
            require(response.optString("status") == "ok" && response.opt("update_available") is Boolean) { "词典检查未返回有效状态" }
            dictionary = response; local = response.optJSONObject("current")
            dictionaryMessage = if (response.optBoolean("update_available")) "发现时刻词典版本 ${response.optString("latest_version")}" else "当前时刻词典已是最新版本。"
        }
    }
    fun checkSoftware(base: String, current: String, abi: String) {
        if (!reserve("software", "正在检查正式版…")) return
        software = null
        operation("software", base) { client ->
            val response = withContext(Dispatchers.IO) { client.software(current, abi) }
            require(response.optString("status") == "ok") { "更新检查未返回有效状态" }
            software = response
            softwareMessage = when {
                response.isNull("update_available") || !response.has("update_available") -> "当前版本无法与正式版比较，请查看发布页面。"
                response.optBoolean("update_available") -> "发现新版本 ${response.optString("latest_version")}" + if (!response.optBoolean("download_supported")) "，当前架构没有可下载的安装包。" else ""
                response.optString("latest_version") == current -> "已是最新正式版。"
                else -> "未发现更高正式版本。"
            }
        }
    }
    fun startDownload(base: String, current: String, abi: String, latest: String, hash: String) = start("software", base, latest,
        Intent().putExtra("current", current).putExtra("abi", abi).putExtra("hash", hash))
    fun startDictionary(base: String, latest: String) = start("dictionary", base, latest, Intent())
    private fun start(kind: String, base: String, latest: String, intent: Intent) {
        if (!reserve(kind, "正在准备后台更新…")) return
        foreground.add(kind)
        val requestId = java.util.UUID.randomUUID().toString()
        pendingIds[kind] = requestId
        persist()
        try {
            intent.setClass(app, UpdateTaskService::class.java).putExtra("kind", kind).putExtra("base", base).putExtra("latest", latest).putExtra("request_id", requestId)
            if (Build.VERSION.SDK_INT >= 26) app.startForegroundService(intent) else app.startService(intent)
        } catch (error: Exception) {
            foreground.remove(kind); pendingIds.remove(kind)
            if (kind == "software") { softwareBusy = false; softwareMessage = "无法启动后台更新：${error.message}" }
            else { dictionaryBusy = false; dictionaryMessage = "无法启动后台更新：${error.message}" }
            persist()
        }
    }
    fun execute(intent: Intent) {
        val kind = intent.getStringExtra("kind") ?: return
        if (kind !in foreground || intent.getStringExtra("request_id") != pendingIds[kind] || jobs[kind]?.isActive == true) return
        val base = intent.getStringExtra("base") ?: return
        val latest = intent.getStringExtra("latest").orEmpty()
        operation(kind, base, refresh = kind == "dictionary") { client ->
            if (kind == "software") {
                var file: File? = null
                try {
                    file = withContext(Dispatchers.IO) {
                        client.download(intent.getStringExtra("current").orEmpty(), latest, intent.getStringExtra("abi").orEmpty(),
                            File(app.cacheDir, "software-updates"), intent.getStringExtra("hash").orEmpty()) { event ->
                            scope.launch { if (clients[kind] === client) softwareProgress = event }
                        }
                    }
                    softwareProgress = JSONObject().put("stage", "signature")
                    withContext(Dispatchers.IO) { SoftwareInstaller.verify(app, requireNotNull(file), latest) }
                    currentCoroutineContext().ensureActive()
                    if (apkPath.isNotBlank()) File(apkPath).delete()
                    apkPath = file.absolutePath; apkVersion = latest
                    softwareMessage = "安装包已下载并校验，请点击安装。"
                } catch (error: Throwable) { file?.delete(); throw error }
            } else {
                val response = withContext(Dispatchers.IO) { client.applyDictionaryStream(latest) { event -> scope.launch { if (clients[kind] === client) dictionaryProgress = event } } }
                require(response.optString("status") in setOf("updated", "unchanged")) { "词典没有返回有效完成状态" }
                dictionary = null
                dictionaryMessage = if (response.optString("status") == "updated") "时刻词典已更新，机位攻略库保持原版本。" else "时刻词典版本无需更新。"
            }
        }
    }
    fun cancel(kind: String, reason: String? = null) {
        clients[kind]?.cancel(); jobs[kind]?.cancel()
        if (kind == "software") softwareMessage = reason ?: "已取消。未完成的下载文件已清理。"
        else dictionaryMessage = reason ?: "已请求取消；若已开始写入，数据可能已经更新。请刷新版本确认。"
        if (jobs[kind] == null) {
            foreground.remove(kind); pendingIds.remove(kind)
            if (kind == "software") softwareBusy = false else dictionaryBusy = false
        }
        persist()
    }
    fun stopForegroundTasks(reason: String) { foreground.toList().forEach { cancel(it, reason) } }
    fun foregroundKinds(): List<String> = foreground.toList()
    companion object {
        @Volatile private var instance: UpdateTasks? = null
        fun get(context: Context): UpdateTasks = instance ?: synchronized(this) { instance ?: UpdateTasks(context).also { instance = it } }
    }
}
