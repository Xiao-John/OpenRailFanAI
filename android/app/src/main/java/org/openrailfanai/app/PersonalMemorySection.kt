package org.openrailfanai.app

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.CancellationException

@Composable
internal fun PersonalMemorySection(settings: MainSettingsRepository, baseUrl: String) {
    val context = LocalContext.current
    val store = remember { PersonalMemoryStore(context) }
    val client = remember(baseUrl) { MemorySummaryClient(baseUrl) }
    val scope = rememberCoroutineScope()
    var enabled by remember { mutableStateOf(store.enabled) }
    var revision by remember { mutableStateOf(store.revision) }
    var draft by remember { mutableStateOf("") }
    var message by remember { mutableStateOf("") }
    var summary by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var confirmSummary by remember { mutableStateOf(false) }
    var confirmClear by remember { mutableStateOf(false) }
    var showRecords by remember { mutableStateOf(false) }
    var job by remember { mutableStateOf<Job?>(null) }
    var generation by remember { mutableStateOf(0L) }
    val rows = remember(revision) { runCatching { store.records() }.getOrNull() }
    DisposableEffect(client) { onDispose { client.cancel(); job?.cancel() } }
    fun invalidate() {
        generation++; client.cancel(); job?.cancel(); loading = false; summary = ""; revision = store.revision
    }
    SettingsCard("个人记忆", "默认关闭。开启后，相关记忆会随提问发送给当前模型服务商；只保存你明确选定的内容。") {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
            BasicText("启用跨对话记忆", style = TextStyle(color = NativeColors.ink, fontSize = 16.sp))
            SettingsSwitch(enabled, true) { value ->
                runCatching { store.setEnabled(value); enabled = value; invalidate(); message = if (value) "已开启；可保存消息或使用“记住：内容”。" else "已关闭，后续对话不再附带记忆；已有记忆保留，可单独清空。" }
                    .onFailure { message = "无法保存记忆开关，请重试。" }
            }
        }
        BasicText(if (rows == null) "记忆文件无法读取，原文件未改动。" else "本机已保存 ${rows.length()} 条；不会自动收集全部聊天。", style = TextStyle(color = NativeColors.muted, fontSize = 14.sp))
        if (enabled) {
            Field("添加一条明确记忆", draft, multiline = true) { draft = it }
            SettingsButton("保存这条记忆", Modifier.fillMaxWidth(), enabled = draft.isNotBlank() && !loading) {
                runCatching { store.remember(draft, "用户在设置中明确保存"); draft = ""; invalidate(); message = "已保存到本机。" }
                    .onFailure { message = it.message ?: "保存失败，原记忆保留。" }
            }
        }
        SettingsButton(if (loading) "正在重新读取并总结…" else "查看记忆总结", Modifier.fillMaxWidth(), enabled = enabled && !loading && rows != null && rows.length() > 0) { confirmSummary = true }
        if (loading) SettingsButton("取消总结", Modifier.fillMaxWidth()) { invalidate(); message = "已取消；已经发往服务商的内容无法撤回。" }
        BasicText("开关只控制跨对话记忆，当前对话历史仍正常使用。总结由当前已保存的模型按需生成，不是全部原始记录，也不会替换记忆文件。", style = TextStyle(color = NativeColors.muted, fontSize = 14.sp))
        if (summary.isNotBlank()) {
            BasicText("本次模型总结", style = TextStyle(color = NativeColors.ink, fontSize = 17.sp))
            MarkdownAnswer(summary, Modifier.fillMaxWidth())
        }
        SettingsButton(if (showRecords) "收起原始记录管理" else "管理原始记录", Modifier.fillMaxWidth(), enabled = rows != null) { showRecords = !showRecords }
        if (showRecords && rows != null) for (i in 0 until rows.length()) {
            val row = rows.getJSONObject(i)
            BasicText(row.optString("text"), style = TextStyle(color = NativeColors.ink, fontSize = 15.sp))
            BasicText("${row.optString("source")} · ${row.optString("saved_at")}", style = TextStyle(color = NativeColors.muted, fontSize = 13.sp))
            SettingsButton("删除此条", Modifier.fillMaxWidth()) {
                runCatching { store.remove(row.getString("id")); invalidate(); message = "已删除。" }.onFailure { message = "删除失败，请重试。" }
            }
        }
        SettingsButton("清空全部记忆", Modifier.fillMaxWidth(), enabled = rows == null || rows.length() > 0) { confirmClear = true }
        if (message.isNotBlank()) BasicText(message, style = TextStyle(color = NativeColors.muted, fontSize = 14.sp))
    }
    if (confirmSummary) Dialog(onDismissRequest = { confirmSummary = false }) {
        SettingsCard("由当前模型总结记忆", "本次将重新读取本机全部记忆，并发送给当前已保存的模型服务商；会产生模型用量。仅在你确认后发送。") {
            BasicText("只将记忆作为总结资料，不额外读取聊天或附件；密钥仅用于模型认证。总结可能有遗漏，原始记忆不会被修改。", style = TextStyle(color = NativeColors.muted, fontSize = 14.sp))
            SettingsButton("确认读取并生成总结", Modifier.fillMaxWidth(), primary = true) {
                confirmSummary = false
                val spec = settings.requestLlmSpec()
                if (spec == null) message = "请先保存并选择一个对话模型。" else {
                    summary = ""; message = ""; loading = true
                    val epoch = store.revision
                    val callId = ++generation
                    job = scope.launch {
                        try {
                            // 在执行时读取磁盘快照，不使用设置页打开时的列表或旧总结。
                            val answer = withContext(Dispatchers.IO) { client.summarize(store.summarySnapshot(), spec) }
                            if (generation == callId && store.enabled && store.revision == epoch) summary = answer
                        } catch (cancelled: CancellationException) { throw cancelled }
                        catch (_: Exception) { if (generation == callId && store.revision == epoch) message = "生成失败；原记忆保留。请检查当前模型连接及上下文窗口。" }
                        finally { if (generation == callId) loading = false }
                    }
                }
            }
            SettingsButton("取消", Modifier.fillMaxWidth()) { confirmSummary = false }
        }
    }
    if (confirmClear) Dialog(onDismissRequest = { confirmClear = false }) {
        SettingsCard("清空全部记忆？", "仅删除本机记忆；不会删除历史对话，也不能撤回已经发送给模型服务商的内容。") {
            SettingsButton("确认清空", Modifier.fillMaxWidth(), primary = true) {
                runCatching { store.clear(); invalidate(); message = "记忆已清空。" }.onFailure { message = "清空失败，请重试。" }
                confirmClear = false
            }
            SettingsButton("取消", Modifier.fillMaxWidth()) { confirmClear = false }
        }
    }
}

@Composable
internal fun SaveMessageMemoryDialog(text: String, source: String, onDismiss: () -> Unit) {
    val context = LocalContext.current
    val store = remember { PersonalMemoryStore(context) }
    var value by remember(text) { mutableStateOf(text) }
    var error by remember { mutableStateOf("") }
    Dialog(onDismissRequest = onDismiss) {
        Column(Modifier.fillMaxWidth().heightIn(max = 560.dp).verticalScroll(rememberScrollState())) {
            SettingsCard("记住这条消息", "保存前可以编辑，最多 2000 字。助手回复按历史资料保存，不视作已核实的实时事实。") {
                if (!store.enabled) BasicText("个人记忆已关闭，请先在设置中开启。", style = TextStyle(color = NativeColors.muted, fontSize = 16.sp))
                else {
                    Field("要记住的内容", value, multiline = true) { value = it }
                    BasicText("${value.length} / 2000 字；开启时可能随后续提问发送给模型。", style = TextStyle(color = NativeColors.muted, fontSize = 14.sp))
                    SettingsButton("确认记住", Modifier.fillMaxWidth(), primary = true, enabled = value.isNotBlank() && value.length <= 2000) {
                        runCatching { store.remember(value, source); android.widget.Toast.makeText(context, "已记住，可在设置中管理", android.widget.Toast.LENGTH_SHORT).show(); onDismiss() }
                            .onFailure { error = it.message ?: "未能保存记忆" }
                    }
                }
                if (error.isNotBlank()) BasicText(error, style = TextStyle(color = NativeColors.muted, fontSize = 14.sp))
                SettingsButton("取消", Modifier.fillMaxWidth(), onClick = onDismiss)
            }
        }
    }
}
