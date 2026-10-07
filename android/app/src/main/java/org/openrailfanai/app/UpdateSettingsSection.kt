package org.openrailfanai.app

import android.os.Build
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.background
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.animation.core.*
import androidx.compose.ui.Alignment
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
    var dictionaryProgress by remember { mutableStateOf<JSONObject?>(null) }
    var dictionaryStartedAt by remember { mutableStateOf(0L) }
    var dictionaryElapsed by remember { mutableStateOf(0L) }
    LaunchedEffect(dictionaryBusy) {
        while (dictionaryBusy) {
            dictionaryElapsed = (android.os.SystemClock.elapsedRealtime() - dictionaryStartedAt) / 1000
            delay(1000)
        }
    }
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
        dictionaryProgress = null
        dictionaryStartedAt = android.os.SystemClock.elapsedRealtime(); dictionaryElapsed = 0
        dictionaryBusy = true; dictionaryMessage = message
        val client = settingsClient.updates(); dictionaryClient = client
        dictionaryJob = scope.launch {
            try { operation(client) }
            catch (error: CancellationException) { throw error }
            catch (error: Exception) { dictionaryMessage = "操作失败：${error.message ?: "请稍后重试"}" }
            finally {
                if (apply) {
                    dictionaryProgress = JSONObject().put("stage", "refresh")
                    withContext(NonCancellable + Dispatchers.IO) {
                        runCatching { settingsClient.updates().dictionaryLocal() }.onSuccess { local = it.optJSONObject("current") }
                    }
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
    SettingsCard("词典更新", "在线更新时刻词典；机位攻略库随软件更新接收。") {
        val value = local
        BasicText("时刻词典", style = updateStyle().copy(color = NativeColors.ink))
        BasicText(if (value?.optBoolean("available") == true) "时刻数据版本：${value.optString("version").takeUnless { it.isBlank() || it == "null" } ?: "未提供"}" else "暂未读取到本地时刻版本，请刷新确认。", style = updateStyle())
        value?.optString("pulled_at")?.takeUnless { it.isBlank() || it == "null" }?.let { BasicText("时刻数据更新时间：$it", style = updateStyle()) }
        PhotoDictionarySummary(value)
        if (dictionaryMessage.isNotBlank()) BasicText(dictionaryMessage, style = updateStyle())
        if (dictionaryBusy) DictionaryProgressPanel(dictionaryProgress, dictionaryElapsed)
        SettingsButton("刷新本地版本", Modifier.fillMaxWidth(), enabled = !dictionaryBusy) {
            dictionaryOperation("正在读取本地词典…") { client ->
                local = withContext(Dispatchers.IO) { client.dictionaryLocal() }.optJSONObject("current")
                dictionaryMessage = "已读取本地词典信息。"
            }
        }
        SettingsButton(if (dictionaryBusy) "正在处理…" else "检查时刻词典更新", Modifier.fillMaxWidth(), enabled = !dictionaryBusy) {
            dictionary = null
            dictionaryOperation("正在检查时刻词典数据源…") { client ->
                val response = withContext(Dispatchers.IO) { client.dictionary() }
                require(response.optString("status") == "ok" && response.opt("update_available") is Boolean) { "词典检查未返回有效状态" }
                dictionary = response; local = response.optJSONObject("current")
                dictionaryMessage = if (response.optBoolean("update_available")) "发现时刻词典版本 ${response.optString("latest_version")}" else "当前时刻词典已是最新版本。"
            }
        }
        val latest = dictionary?.optString("latest_version").orEmpty()
        if (dictionary?.optBoolean("update_available") == true && latest.isNotBlank()) {
            SettingsButton("更新时刻词典", Modifier.fillMaxWidth(), primary = true, enabled = !dictionaryBusy) {
                dictionaryOperation("正在下载、校验并导入时刻词典…", apply = true) { client ->
                    val response = withContext(Dispatchers.IO) { client.applyDictionaryStream(latest) { dictionaryProgress = it } }
                    require(response.optString("status") in setOf("updated", "unchanged")) { "词典没有返回有效完成状态" }
                    dictionary = null
                    dictionaryMessage = if (response.optString("status") == "updated") "时刻词典已更新，机位攻略库保持原版本。" else "时刻词典版本无需更新。"
                }
            }
        }
        if (dictionaryBusy) SettingsButton("取消", Modifier.fillMaxWidth()) {
            dictionaryClient?.cancel(); dictionaryJob?.cancel()
            dictionaryMessage = "已请求取消；若已开始写入，数据可能已经更新。请刷新版本确认。"
        }
    }
}

private fun updateStyle() = TextStyle(color = NativeColors.muted, fontSize = 14.appSp, lineHeight = 21.appSp)

@Composable
private fun PhotoDictionarySummary(local: JSONObject?) {
    val photo = local?.optJSONObject("photo_spots")
    Column(Modifier.fillMaxWidth().background(NativeColors.panel, RoundedCornerShape(10.appDp))
        .padding(12.appDp), verticalArrangement = Arrangement.spacedBy(6.appDp)) {
        BasicText("机位攻略库", style = updateStyle().copy(color = NativeColors.ink))
        when {
            local == null -> BasicText("尚未读取本地机位库，请刷新确认。", style = updateStyle())
            photo == null -> BasicText(if (local.optBoolean("available")) "当前服务未提供机位库信息。" else "暂未读取到本地机位库，请刷新确认。", style = updateStyle())
            !photo.optBoolean("available") -> BasicText("本机尚未收录机位攻略，随软件更新接收。", style = updateStyle())
            else -> {
                val documents = photo.optLong("documents", -1)
                val scopes = photo.optLong("scopes", -1)
                if (documents >= 0 && scopes >= 0)
                    BasicText("已收录 $documents 篇攻略 · $scopes 个收录范围", style = updateStyle())
                val version = photo.optString("version").takeUnless { it.isBlank() || it == "null" }
                BasicText("机位数据版本：${version ?: "未提供"}", style = updateStyle())
                BasicText("篇数不代表机位数量；数据版本不代表重新采集时间。", style = updateStyle().copy(fontSize = 12.appSp))
            }
        }
        BasicText("机位攻略随软件内置词典分发，在线时刻词典更新不会刷新机位库。", style = updateStyle().copy(fontSize = 12.appSp))
    }
}


@Composable
private fun DictionaryProgressPanel(event: JSONObject?, elapsed: Long) {
    val stage = event?.optString("stage").orEmpty()
    val done = event?.optLong("completed", -1) ?: -1
    val total = event?.optLong("total", -1) ?: -1
    val bytes = stage == "download" && event?.optString("unit") == "bytes"
    val measured = bytes && done >= 0 && total > 0 && done <= total
    val title = when (stage) {
        "check" -> "正在核对时刻词典版本"
        "download" -> "正在下载更新包"
        "verify" -> "正在校验更新包"
        "import" -> "正在导入时刻词典数据"
        "validate" -> "正在检查数据完整性"
        "commit" -> "正在保存时刻词典数据"
        "refresh" -> "正在确认本地词典版本"
        else -> "正在等待更新服务"
    }
    Column(Modifier.fillMaxWidth().background(NativeColors.panel, RoundedCornerShape(10.appDp))
        .padding(12.appDp), verticalArrangement = Arrangement.spacedBy(8.appDp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            BasicText(title, Modifier.weight(1f), style = updateStyle().copy(color = NativeColors.ink))
            BasicText("${elapsed}秒", style = updateStyle())
        }
        BoxWithConstraints(Modifier.fillMaxWidth().height(5.appDp).background(NativeColors.line, RoundedCornerShape(3.appDp))) {
            if (measured) Box(Modifier.fillMaxWidth((done.toFloat() / total).coerceIn(0f, 1f)).fillMaxHeight()
                .background(NativeColors.blue, RoundedCornerShape(3.appDp)))
            else {
                val transition = rememberInfiniteTransition(label = "dictionary-working")
                val position by transition.animateFloat(0f, 1f, infiniteRepeatable(tween(1200), RepeatMode.Reverse), label = "dictionary-working-position")
                Box(Modifier.offset(x = maxWidth * (position * 0.7f)).width(maxWidth * 0.3f).fillMaxHeight()
                    .background(NativeColors.blue, RoundedCornerShape(3.appDp)))
            }
        }
        val detail = when {
            measured -> "${formatUpdateBytes(done)} / ${formatUpdateBytes(total)} · 下载 ${(done * 100 / total).coerceIn(0, 100)}%"
            bytes && done >= 0 -> "已下载 ${formatUpdateBytes(done)}，总大小未知"
            stage == "import" -> {
                val table = when (event?.optString("table")) { "g_stop" -> "车站"; "g_trip" -> "车次"; "g_stop_time" -> "站序与时刻"; else -> "数据" }
                "$table · 已处理 ${done.coerceAtLeast(0)} 条记录"
            }
            stage == "commit" -> "正在保存数据；即使请求取消，保存仍可能完成。"
            else -> "本阶段耗时取决于网络和设备性能。"
        }
        BasicText(detail, style = updateStyle())
        BasicText("百分比仅表示下载进度；下载后还需校验并保存数据。", style = updateStyle().copy(fontSize = 12.appSp))
    }
}

private fun formatUpdateBytes(bytes: Long): String = when {
    bytes >= 1024 * 1024 -> String.format(java.util.Locale.ROOT, "%.1f MB", bytes / (1024.0 * 1024))
    bytes >= 1024 -> String.format(java.util.Locale.ROOT, "%.1f KB", bytes / 1024.0)
    else -> "$bytes B"
}
