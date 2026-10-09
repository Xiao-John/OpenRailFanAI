package org.openrailfanai.app

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

internal data class PhotoIssueTarget(val url: String, val occurrenceId: String?)

@Composable
internal fun PhotoSpotResults(snapshot: JSONObject, backendUrl: String?, modifier: Modifier = Modifier,
    loadDetails: suspend (PhotoIssueTarget) -> List<JSONObject> = { target ->
        withContext(Dispatchers.IO) {
            require(!backendUrl.isNullOrBlank()) { "设备内服务尚未连接" }
            PhotoSpotRepository(backendUrl).details(target.url, target.occurrenceId)
        }
    }) {
    var selected by remember { mutableStateOf<PhotoIssueTarget?>(null) }
    val search = snapshot.optJSONObject("search")
    val items = photoObjects(search?.optJSONArray("items"))
    val documents = photoObjects(snapshot.optJSONArray("documents"))
    Column(modifier, verticalArrangement = Arrangement.spacedBy(12.appDp)) {
        items.forEach { item ->
            key(item.optString("occurrence_id")) {
                PhotoSpotCard(item) { selected = PhotoIssueTarget(item.optString("url"), item.optString("occurrence_id")) }
            }
        }
        if (items.isNotEmpty()) {
            BasicText("共 ${search?.optInt("total")} 处 · 显示 ${search?.optInt("shown")} 处" +
                if (search?.optBoolean("truncated") == true) "，其余结果未展示" else "",
                style = photoCaption())
        }
        documents.filter { doc ->
            // Reviewed document issues are already reachable from their displayed occurrence cards.
            doc.optString("status") != "reviewed" || items.none { it.optString("url") == doc.optString("url") }
        }.forEach { doc ->
            PhotoSourceCard(doc) { selected = PhotoIssueTarget(doc.optString("url"), null) }
        }
        if (search?.optBoolean("available", true) == false) PhotoNotice("旧词典未提供标注，继续阅读原攻略。")
        snapshot.optString("search_error").takeIf(String::isNotBlank)?.let { PhotoNotice(it) }
    }
    selected?.let { target -> PhotoIssueDialog(target, loadDetails) { selected = null } }
}

@Composable
internal fun PhotoSpotCard(item: JSONObject, onPending: () -> Unit) {
    val claims = photoObjects(item.optJSONArray("claims"))
    val markers = photoObjects(item.optJSONArray("issue_markers"))
    Column(Modifier.fillMaxWidth().testTag("photo-spot-card")
        .background(NativeColors.surface, RoundedCornerShape(16.appDp))
        .border(1.appDp, NativeColors.line, RoundedCornerShape(16.appDp)).padding(14.appDp),
        verticalArrangement = Arrangement.spacedBy(12.appDp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.appDp)) {
            BasicText(item.optString("name_raw").ifBlank { "机位线索" }, Modifier.weight(1f),
                style = TextStyle(color = NativeColors.ink, fontSize = 20.appSp, fontWeight = FontWeight.Bold))
            BasicText("机位线索", Modifier.background(NativeColors.selected, RoundedCornerShape(6.appDp)).padding(8.appDp, 5.appDp), style = photoCaption())
        }
        val unconditional = claims.filter { it.isNull("group_id") || it.optString("group_id").isBlank() }
        val core = unconditional.flatMap(::photoClaimRows).filter { it.first in setOf("城市", "机位类型") }.distinct()
        if (core.isNotEmpty()) Row(Modifier.fillMaxWidth().background(NativeColors.panel, RoundedCornerShape(10.appDp)).padding(12.appDp), horizontalArrangement = Arrangement.spacedBy(10.appDp)) {
            core.forEach { (label, value) -> Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(5.appDp)) {
                BasicText(label, style = photoCaption())
                BasicText(value, style = TextStyle(color = NativeColors.ink, fontSize = 16.appSp, fontWeight = FontWeight.SemiBold))
            } }
        }
        // Keep separate condition groups; never make a new combined claim.
        claims.groupBy { it.optString("group_id").takeUnless { id -> id.isBlank() || id == "null" } }.forEach { (group, values) ->
            val rows = values.flatMap(::photoClaimRows).filterNot {
                (group == null && it in core) || (it.first == "参照位置" && it.second == item.optString("name_raw"))
            }
            if (rows.isNotEmpty()) Column(Modifier.fillMaxWidth().background(NativeColors.panel, RoundedCornerShape(10.appDp)).padding(12.appDp), verticalArrangement = Arrangement.spacedBy(8.appDp)) {
                if (group != null) BasicText("原文条件组", style = photoCaption())
                rows.distinct().forEach { (label, value) ->
                    Column(verticalArrangement = Arrangement.spacedBy(3.appDp)) {
                        BasicText(label, style = photoCaption())
                        BasicText(value, style = TextStyle(color = NativeColors.ink, fontSize = 16.appSp, fontWeight = FontWeight.SemiBold))
                    }
                }
                values.mapNotNull { (it.opt("valid_time_raw") as? String)?.takeIf(String::isNotBlank) }.distinct().forEach {
                    BasicText("原文时效：$it", style = photoCaption())
                }
            }
        }
        if (markers.isNotEmpty()) Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) { PhotoPendingButton(onPending) }
        PhotoSourceRow(item.optString("url"), "机位名称保留原文称呼；开放及交通信息保留原文时效。")
    }
}

@Composable
private fun PhotoSourceCard(doc: JSONObject, onPending: () -> Unit) {
    val status = doc.optString("status")
    Column(Modifier.fillMaxWidth().testTag("photo-source-card")
        .background(NativeColors.surface, RoundedCornerShape(16.appDp))
        .border(1.appDp, NativeColors.line, RoundedCornerShape(16.appDp)).padding(14.appDp),
        verticalArrangement = Arrangement.spacedBy(12.appDp)) {
        BasicText("攻略来源", style = TextStyle(color = NativeColors.ink, fontSize = 18.appSp, fontWeight = FontWeight.Bold))
        when (status) {
            "unreviewed" -> PhotoNotice("尚未复核，保留原攻略阅读。")
            "stale" -> PhotoNotice("原文已变化，旧标注暂不作为已确认信息。")
        }
        if (photoObjects(doc.optJSONArray("issue_markers")).isNotEmpty()) Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) { PhotoPendingButton(onPending) }
        PhotoSourceRow(doc.optString("url"), if (status == "reviewed") "已复核，仍可能有待确认字段。" else "此来源不作为已确认的结构化事实。")
    }
}

@Composable
private fun PhotoPendingButton(onClick: () -> Unit) {
    Row(Modifier.heightIn(min = 44.dp).background(if (NativeColors.dark) Color(0xFF48371C) else Color(0xFFFFF1CA), RoundedCornerShape(9.appDp))
        .clickable(role = Role.Button, onClick = onClick).padding(horizontal = 12.appDp).testTag("photo-pending"),
        horizontalArrangement = Arrangement.spacedBy(8.appDp), verticalAlignment = Alignment.CenterVertically) {
        val color = if (NativeColors.dark) Color(0xFFF1C979) else Color(0xFF805809)
        RailIcon("info", Modifier.size(18.appDp), color)
        BasicText("待确认", style = TextStyle(color = color, fontSize = 14.appSp))
    }
}

@Composable
private fun PhotoSourceRow(url: String, note: String) {
    var expanded by remember(url) { mutableStateOf(false) }
    val uri = LocalUriHandler.current
    Column {
        Row(Modifier.fillMaxWidth().heightIn(min = 44.dp).background(NativeColors.panel, RoundedCornerShape(9.appDp))
            .clickable(role = Role.Button) { expanded = !expanded }.padding(horizontal = 10.appDp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.appDp)) {
            RailIcon("link", Modifier.size(18.appDp), NativeColors.muted)
            BasicText("来源与查询详情", Modifier.weight(1f), style = photoCaption())
            RailIcon(if (expanded) "chevron-up" else "chevron-down", Modifier.size(16.appDp), NativeColors.muted)
        }
        if (expanded) Column(Modifier.padding(top = 10.appDp), verticalArrangement = Arrangement.spacedBy(6.appDp)) {
            BasicText(note, style = photoCaption())
            MarkdownDocument.safeLink(url)?.let { safe ->
                BasicText(url, Modifier.fillMaxWidth().heightIn(min = 44.dp).clickable(role = Role.Button) { uri.openUri(safe) },
                    style = TextStyle(color = NativeColors.blue, fontSize = 14.appSp))
            }
        }
    }
}

@Composable
private fun PhotoIssueDialog(target: PhotoIssueTarget, loader: suspend (PhotoIssueTarget) -> List<JSONObject>, onClose: () -> Unit) {
    var details by remember(target) { mutableStateOf<List<JSONObject>?>(null) }
    var error by remember(target) { mutableStateOf<String?>(null) }
    var revision by remember(target) { mutableStateOf(0) }
    LaunchedEffect(target, revision) {
        details = null; error = null
        try { details = loader(target) }
        catch (e: kotlinx.coroutines.CancellationException) { throw e }
        catch (e: Exception) { error = if (e is PhotoIssueUpdated) "说明已更新，请重新查看来源。" else "说明暂未加载，请重试。" }
    }
    Dialog(onDismissRequest = onClose) {
        Column(Modifier.fillMaxWidth().background(NativeColors.surface, RoundedCornerShape(16.appDp))
            .border(1.appDp, NativeColors.line, RoundedCornerShape(16.appDp)).padding(18.appDp).testTag("photo-issue-dialog")) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                BasicText("待确认说明", Modifier.weight(1f), style = TextStyle(color = NativeColors.ink, fontSize = 20.appSp, fontWeight = FontWeight.Bold))
                Box(Modifier.size(44.dp).clickable(role = Role.Button, onClickLabel = "关闭说明", onClick = onClose).testTag("photo-issue-close"), contentAlignment = Alignment.Center) {
                    RailIcon("close", Modifier.size(20.appDp), NativeColors.muted)
                }
            }
            Column(Modifier.heightIn(max = 460.appDp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.appDp)) {
                if (details == null && error == null) PhotoNotice("正在加载说明…")
                error?.let {
                    PhotoNotice(it)
                    PhotoTextButton("重新加载") { revision++ }
                }
                details?.forEach { issue ->
                    Column(verticalArrangement = Arrangement.spacedBy(8.appDp)) {
                        BasicText(if (issue.isNull("occurrence_id") || issue.optString("occurrence_id").isBlank()) "整篇攻略" else "本机位",
                            style = photoCaption())
                        BasicText(issue.optString("title").ifBlank { "待确认" }, style = TextStyle(color = NativeColors.ink, fontSize = 16.appSp, fontWeight = FontWeight.SemiBold))
                        BasicText(issue.optString("note"), style = TextStyle(color = NativeColors.ink, fontSize = 16.appSp))
                        if (issue.optString("source_status") == "stale") PhotoNotice("原文已变化，此说明不代表当前已确认信息。")
                        PhotoEvidence(issue)
                    }
                }
            }
        }
    }
}

@Composable
private fun PhotoEvidence(issue: JSONObject) {
    var open by remember(issue.optString("issue_id")) { mutableStateOf(false) }
    PhotoTextButton(if (open) "收起原文依据" else "查看原文依据") { open = !open }
    if (open) Column(verticalArrangement = Arrangement.spacedBy(8.appDp)) {
        val quotes = photoObjects(issue.optJSONArray("evidence")).mapNotNull { (it.opt("quote") as? String)?.takeIf(String::isNotBlank) }
        if (quotes.isEmpty()) BasicText("暂无可展示的原文依据。", style = photoCaption())
        quotes.forEach { BasicText(it, Modifier.background(NativeColors.panel, RoundedCornerShape(8.appDp)).padding(10.appDp), style = photoCaption()) }
        val uri = LocalUriHandler.current
        MarkdownDocument.safeLink(issue.optString("url"))?.let { safe -> PhotoTextButton("查看来源") { uri.openUri(safe) } }
    }
}

@Composable
private fun PhotoTextButton(label: String, action: () -> Unit) {
    BasicText(label, Modifier.fillMaxWidth().heightIn(min = 44.dp).clickable(role = Role.Button, onClick = action).padding(vertical = 10.appDp),
        style = TextStyle(color = NativeColors.blue, fontSize = 14.appSp))
}
@Composable
private fun PhotoNotice(text: String) = BasicText(text, Modifier.fillMaxWidth().background(NativeColors.panel, RoundedCornerShape(10.appDp)).padding(12.appDp), style = photoCaption())
private fun photoCaption() = TextStyle(color = NativeColors.muted, fontSize = 14.appSp)
