package org.openrailfanai.app

import android.os.Build
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.*
import org.json.JSONObject
import java.io.File

@Composable
internal fun UpdateSettingsSection(settingsClient: SettingsClient, onOpenUrl: (String) -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val currentVersion = remember { SoftwareInstaller.installedVersion(context) }
    val abi = remember { Build.SUPPORTED_ABIS.firstOrNull().orEmpty() }
    var software by remember { mutableStateOf<JSONObject?>(null) }
    var softwareMessage by remember { mutableStateOf("") }
    var softwareBusy by remember { mutableStateOf(false) }
    var softwareJob by remember { mutableStateOf<Job?>(null) }
    var softwareClient by remember { mutableStateOf<UpdateClient?>(null) }
    var apkPath by rememberSaveable { mutableStateOf("") }
    var apkVersion by rememberSaveable { mutableStateOf("") }
    var showNotes by remember { mutableStateOf(false) }
    var local by remember { mutableStateOf<JSONObject?>(null) }
    var dictionary by remember { mutableStateOf<JSONObject?>(null) }
    var dictionaryMessage by remember { mutableStateOf("") }
    var dictionaryBusy by remember { mutableStateOf(false) }
    var dictionaryJob by remember { mutableStateOf<Job?>(null) }
    var dictionaryClient by remember { mutableStateOf<UpdateClient?>(null) }

    DisposableEffect(settingsClient) {
        onDispose { softwareClient?.cancel(); dictionaryClient?.cancel(); softwareJob?.cancel(); dictionaryJob?.cancel() }
    }
    LaunchedEffect(settingsClient) {
        runCatching { withContext(Dispatchers.IO) { settingsClient.updates().dictionaryLocal() } }
            .onSuccess { local = it.optJSONObject("current") }
            .onFailure { if (it is CancellationException) throw it else dictionaryMessage = "本地词典版本暂时无法读取，请刷新。" }
    }
    fun cancelSoftware() {
        softwareClient?.cancel(); softwareJob?.cancel()
        softwareMessage = "已取消。未完成的下载文件已清理。"
    }
    fun softwareOperation(message: String, operation: suspend (UpdateClient) -> Unit) {
        softwareBusy = true; softwareMessage = message
        val client = settingsClient.updates(); softwareClient = client
        softwareJob = scope.launch {
            try { operation(client) }
            catch (error: CancellationException) { throw error }
            catch (error: Exception) { softwareMessage = "操作失败：${error.message ?: "请稍后重试"}" }
            finally { softwareBusy = false; softwareClient = null }
        }
    }
    fun dictionaryOperation(message: String, apply: Boolean = false, operation: suspend (UpdateClient) -> Unit) {
        dictionaryBusy = true; dictionaryMessage = message
        val client = settingsClient.updates(); dictionaryClient = client
        dictionaryJob = scope.launch {
            try { operation(client) }
            catch (error: CancellationException) { throw error }
            catch (error: Exception) { dictionaryMessage = "操作失败：${error.message ?: "请稍后重试"}" }
            finally {
                if (apply) withContext(NonCancellable + Dispatchers.IO) {
                    runCatching { settingsClient.updates().dictionaryLocal() }.onSuccess { local = it.optJSONObject("current") }
                }
                dictionaryBusy = false; dictionaryClient = null
            }
        }
    }
    SettingsCard("软件更新", "当前安装版本 $currentVersion") {
        BasicText(softwareMessage.ifBlank { "检查本项目正式版，下载后由系统完成安装。" }, style = updateStyle())
        SettingsButton(if (softwareBusy) "正在处理…" else "检查软件更新", Modifier.fillMaxWidth(), enabled = !softwareBusy) {
            software = null
            softwareOperation("正在检查正式版…") { client ->
                val response = withContext(Dispatchers.IO) { client.software(currentVersion, abi) }
                require(response.optString("status") == "ok") { "更新检查未返回有效状态" }
                software = response
                softwareMessage = when {
                    response.isNull("update_available") || !response.has("update_available") -> "当前版本无法与正式版比较，请查看发布页面。"
                    response.optBoolean("update_available") -> "发现新版本 ${response.optString("latest_version")}" +
                        if (!response.optBoolean("download_supported")) "，当前架构没有可下载的安装包。" else ""
                    response.optString("latest_version") == currentVersion -> "已是最新正式版。"
                    else -> "未发现更高正式版本。"
                }
            }
        }
        val response = software
        if (response != null) {
            val url = response.optString("release_url")
            if (url.startsWith("https://github.com/Xiao-John/OpenRailFanAI/releases/"))
                SettingsButton("查看发布页面", Modifier.fillMaxWidth()) { onOpenUrl(url) }
            val notes = response.optString("notes")
            if (notes.isNotBlank()) {
                SettingsButton(if (showNotes) "收起更新说明" else "查看更新说明", Modifier.fillMaxWidth()) { showNotes = !showNotes }
                if (showNotes) MarkdownAnswer(notes)
            }
            if (response.optBoolean("update_available") && response.optBoolean("download_supported")) {
                val latest = response.optString("latest_version")
                SettingsButton("下载 $latest", Modifier.fillMaxWidth(), primary = true, enabled = !softwareBusy) {
                    softwareOperation("正在下载并校验安装包，请稍候…") { client ->
                        val file = withContext(Dispatchers.IO) {
                            val hash = response.optJSONObject("asset")?.optString("sha256").orEmpty().removePrefix("sha256:")
                            client.download(currentVersion, latest, abi, File(context.cacheDir, "software-updates"), hash)
                                .also { downloaded ->
                                    try { SoftwareInstaller.verify(context, downloaded, latest) }
                                    catch (error: Exception) { downloaded.delete(); throw error }
                                }
                        }
                        if (apkPath.isNotBlank()) File(apkPath).delete()
                        apkPath = file.absolutePath; apkVersion = latest
                        softwareMessage = "安装包已下载并校验，请点击安装。"
                    }
                }
            }
        }
        if (softwareBusy) SettingsButton("取消", Modifier.fillMaxWidth()) { cancelSoftware() }
        if (apkPath.isNotBlank() && File(apkPath).isFile()) {
            SettingsButton("安装 $apkVersion", Modifier.fillMaxWidth(), primary = true, enabled = !softwareBusy) {
                softwareMessage = runCatching { SoftwareInstaller.launch(context, File(apkPath), apkVersion) }
                    .getOrElse { "无法打开安装器：${it.message ?: "请稍后重试"}" }
            }
        }
    }
    SettingsCard("词典更新", "独立更新 GTFS 站序、时刻、坐标和里程，保留线路与车站档案缓存。") {
        val value = local
        BasicText(if (value?.optBoolean("available") == true) "数据版本：${value.optString("version", "未提供")}" else "本地词典尚未就绪或版本未读取。", style = updateStyle())
        value?.optString("pulled_at")?.takeIf(String::isNotBlank)?.let { BasicText("采样日期：$it", style = updateStyle()) }
        if (dictionaryMessage.isNotBlank()) BasicText(dictionaryMessage, style = updateStyle())
        SettingsButton("刷新本地版本", Modifier.fillMaxWidth(), enabled = !dictionaryBusy) {
            dictionaryOperation("正在读取本地词典…") { client ->
                local = withContext(Dispatchers.IO) { client.dictionaryLocal() }.optJSONObject("current")
                dictionaryMessage = "已读取当前本地版本。"
            }
        }
        SettingsButton(if (dictionaryBusy) "正在处理…" else "检查词典更新", Modifier.fillMaxWidth(), enabled = !dictionaryBusy) {
            dictionary = null
            dictionaryOperation("正在检查词典数据源…") { client ->
                val response = withContext(Dispatchers.IO) { client.dictionary() }
                require(response.optString("status") == "ok" && response.opt("update_available") is Boolean) { "词典检查未返回有效状态" }
                dictionary = response; local = response.optJSONObject("current")
                dictionaryMessage = if (response.optBoolean("update_available")) "发现词典版本 ${response.optString("latest_version")}" else "当前 GTFS 词典已是最新版本。"
            }
        }
        val latest = dictionary?.optString("latest_version").orEmpty()
        if (dictionary?.optBoolean("update_available") == true && latest.isNotBlank()) {
            SettingsButton("更新 GTFS 词典", Modifier.fillMaxWidth(), primary = true, enabled = !dictionaryBusy) {
                dictionaryOperation("正在下载、校验并导入词典…", apply = true) { client ->
                    val response = withContext(Dispatchers.IO) { client.applyDictionary(latest) }
                    require(response.optString("status") in setOf("updated", "unchanged")) { "词典没有返回有效完成状态" }
                    dictionary = null
                    dictionaryMessage = if (response.optString("status") == "updated") "词典更新已提交。" else "词典版本无需更新。"
                }
            }
        }
        if (dictionaryBusy) SettingsButton("取消", Modifier.fillMaxWidth()) {
            dictionaryClient?.cancel(); dictionaryJob?.cancel()
            dictionaryMessage = "已请求取消；若已进入提交阶段，数据可能已更新。请刷新本地版本确认。"
        }
    }
}

private fun updateStyle() = TextStyle(color = NativeColors.muted, fontSize = 14.appSp, lineHeight = 21.appSp)
