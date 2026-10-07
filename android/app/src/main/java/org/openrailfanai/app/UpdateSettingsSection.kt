package org.openrailfanai.app

import android.os.Build
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.background
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.animation.core.*
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import kotlinx.coroutines.*
import org.json.JSONObject
import java.io.File

@Composable
internal fun UpdateSettingsSection(settingsClient: SettingsClient, onOpenUrl: (String) -> Unit) {
    val context = LocalContext.current
    val currentVersion = remember { SoftwareInstaller.installedVersion(context) }
    val abi = remember { Build.SUPPORTED_ABIS.firstOrNull().orEmpty() }
    val tasks = remember { UpdateTasks.get(context) }
    val base = settingsClient.updateBaseUrl
    val softwareAnchor = remember { androidx.compose.foundation.relocation.BringIntoViewRequester() }
    val dictionaryAnchor = remember { androidx.compose.foundation.relocation.BringIntoViewRequester() }
    val density = androidx.compose.ui.platform.LocalDensity.current.density
    LaunchedEffect(tasks.focusRequest) {
        if (tasks.focusRequest > 0) {
            val anchor = if (tasks.focusKind == "dictionary") dictionaryAnchor else softwareAnchor
            anchor.bringIntoView(androidx.compose.ui.geometry.Rect(0f, 0f, 1f, 64f * density))
        }
    }
    var showNotes by remember { mutableStateOf(false) }
    var elapsed by remember { mutableStateOf(0L) }
    LaunchedEffect(tasks.softwareBusy, tasks.dictionaryBusy) {
        while (tasks.softwareBusy || tasks.dictionaryBusy) { elapsed = android.os.SystemClock.elapsedRealtime(); delay(1000) }
    }
    LaunchedEffect(base) { if (!tasks.dictionaryBusy) tasks.refreshLocal(base) }
    var notificationsEnabled by remember { mutableStateOf(androidx.core.app.NotificationManagerCompat.from(context).areNotificationsEnabled()) }
    val permission = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestPermission()) {
        notificationsEnabled = androidx.core.app.NotificationManagerCompat.from(context).areNotificationsEnabled()
    }
    fun notifications() {
        val preferences = context.getSharedPreferences("update_tasks", android.content.Context.MODE_PRIVATE)
        if (Build.VERSION.SDK_INT >= 33 && !preferences.getBoolean("notifications_requested", false) &&
            androidx.core.content.ContextCompat.checkSelfPermission(context, android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            preferences.edit().putBoolean("notifications_requested", true).apply()
            permission.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }
    }
    with(tasks) {
        SettingsCard("软件更新", "当前安装版本 $currentVersion", Modifier.bringIntoViewRequester(softwareAnchor)) {
            BasicText(softwareMessage.ifBlank { "检查本项目正式版，下载后由系统完成安装。" }, style = updateStyle())
            SettingsButton(if (softwareBusy) "正在处理…" else "检查软件更新", Modifier.fillMaxWidth(), enabled = !softwareBusy) {
                tasks.checkSoftware(base, currentVersion, abi)
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
                        notifications()
                        tasks.startDownload(base, currentVersion, abi, latest,
                            response.optJSONObject("asset")?.optString("sha256").orEmpty().removePrefix("sha256:"))
                    }
                }
            }
            if (softwareBusy) {
                UpdateProgressPanel(softwareProgress, ((elapsed - softwareStartedAt).coerceAtLeast(0) / 1000), software = true)
                BasicText(if (notificationsEnabled) "更新会在后台继续，可离开此页；通知栏可查看进度。"
                    else "更新会在后台继续；系统通知未开启，可返回此页查看进度。", style = updateStyle())
                SettingsButton("取消", Modifier.fillMaxWidth()) { tasks.cancel("software") }
            }
            if (apkPath.isNotBlank() && File(apkPath).isFile()) {
                SettingsButton("安装 $apkVersion", Modifier.fillMaxWidth(), primary = true, enabled = !softwareBusy) {
                    tasks.reportSoftwareMessage(runCatching { SoftwareInstaller.launch(context, File(apkPath), apkVersion) }
                        .getOrElse { "无法打开安装器：${it.message ?: "请稍后重试"}" })
                }
            }
        }
        SettingsCard("词典更新", "在线更新时刻词典；机位攻略库随软件更新接收。", Modifier.bringIntoViewRequester(dictionaryAnchor)) {
            val value = local
            BasicText("时刻词典", style = updateStyle().copy(color = NativeColors.ink))
            BasicText(if (value?.optBoolean("available") == true) "时刻数据版本：${value.optString("version").takeUnless { it.isBlank() || it == "null" } ?: "未提供"}" else "暂未读取到本地时刻版本，请刷新确认。", style = updateStyle())
            value?.optString("pulled_at")?.takeUnless { it.isBlank() || it == "null" }?.let { BasicText("时刻数据更新时间：$it", style = updateStyle()) }
            PhotoDictionarySummary(value)
            if (dictionaryMessage.isNotBlank()) BasicText(dictionaryMessage, style = updateStyle())
            if (dictionaryBusy) {
                UpdateProgressPanel(dictionaryProgress, ((elapsed - dictionaryStartedAt).coerceAtLeast(0) / 1000))
                BasicText(if (notificationsEnabled) "更新会在后台继续，可离开此页；通知栏可查看进度。"
                    else "更新会在后台继续；系统通知未开启，可返回此页查看进度。", style = updateStyle())
            }
            SettingsButton("刷新本地版本", Modifier.fillMaxWidth(), enabled = !dictionaryBusy) {
                tasks.refreshLocal(base)
            }
            SettingsButton(if (dictionaryBusy) "正在处理…" else "检查时刻词典更新", Modifier.fillMaxWidth(), enabled = !dictionaryBusy) {
                tasks.checkDictionary(base)
            }
            val latest = dictionary?.optString("latest_version").orEmpty()
            if (dictionary?.optBoolean("update_available") == true && latest.isNotBlank()) {
                SettingsButton("更新时刻词典", Modifier.fillMaxWidth(), primary = true, enabled = !dictionaryBusy) {
                    notifications()
                    tasks.startDictionary(base, latest)
                }
            }
            if (dictionaryBusy) SettingsButton("取消", Modifier.fillMaxWidth()) {
                tasks.cancel("dictionary")
            }
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
private fun UpdateProgressPanel(event: JSONObject?, elapsed: Long, software: Boolean = false) {
    val stage = event?.optString("stage").orEmpty()
    val done = event?.optLong("completed", -1) ?: -1
    val total = event?.optLong("total", -1) ?: -1
    val bytes = stage == "download" && event?.optString("unit") == "bytes"
    val measured = bytes && done >= 0 && total > 0 && done <= total
    val title = when (stage) {
        "check" -> if (software) "正在核对软件版本" else "正在核对时刻词典版本"
        "signature" -> "正在校验安装包签名"
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

internal fun formatUpdateBytes(bytes: Long): String = when {
    bytes >= 1024 * 1024 -> String.format(java.util.Locale.ROOT, "%.1f MB", bytes / (1024.0 * 1024))
    bytes >= 1024 -> String.format(java.util.Locale.ROOT, "%.1f KB", bytes / 1024.0)
    else -> "$bytes B"
}
