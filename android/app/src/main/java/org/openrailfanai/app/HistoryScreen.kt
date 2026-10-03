package org.openrailfanai.app

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.PlatformTextStyle
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.LineHeightStyle
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

private val HistoryInk get() = NativeColors.ink
private val HistoryMuted get() = NativeColors.muted
private val HistoryLine get() = NativeColors.line
private val HistoryBlue get() = NativeColors.blue

private val historyLatinRun = Regex("[A-Za-z0-9]+(?:[-._/][A-Za-z0-9]+)*")

private fun historyTitleText(title: String): AnnotatedString = buildAnnotatedString {
    var previousEnd = 0
    historyLatinRun.findAll(title).forEach { match ->
        append(title.substring(previousEnd, match.range.first))
        withStyle(SpanStyle(fontSize = (19.5).appSp, letterSpacing = (.2).appSp)) { append(match.value) }
        previousEnd = match.range.last + 1
    }
    append(title.substring(previousEnd))
}

@Composable
fun HistoryScreen(
    store: ConversationStore,
    currentId: String,
    revision: Int,
    onBack: () -> Unit,
    onCreate: () -> Unit,
    onSelect: (String) -> Unit,
    onRename: (String, String) -> Unit,
    onDelete: (String) -> Unit,
    onSettings: () -> Unit,
    onHelp: () -> Unit,
    modifier: Modifier = Modifier,
    applySystemInsets: Boolean = true,
    systemInsets: WindowInsets = WindowInsets.safeDrawing,
) {
    var query by remember { mutableStateOf("") }
    var actionFor by remember { mutableStateOf<StoredConversation?>(null) }
    var renameFor by remember { mutableStateOf<StoredConversation?>(null) }
    var deleteFor by remember { mutableStateOf<StoredConversation?>(null) }
    var renameValue by remember(renameFor) { mutableStateOf(renameFor?.title.orEmpty()) }
    val historyScrollState = rememberScrollState()
    val conversations = remember(query, revision) { store.search(query) }
    val today = LocalDate.now()
    val groups = listOf(
        "今天" to conversations.filter { localDate(it.updatedAt) == today },
        "昨天" to conversations.filter { localDate(it.updatedAt) == today.minusDays(1) },
        "更早" to conversations.filter { localDate(it.updatedAt).isBefore(today.minusDays(1)) },
    )

    val pageModifier = if (applySystemInsets) modifier.fillMaxSize().background(NativeColors.background)
        .windowInsetsPadding(systemInsets).imePadding()
        else modifier.fillMaxSize().background(NativeColors.background)
    Box(modifier.fillMaxSize()) {
      Column(pageModifier.testTag("history-content").padding(horizontal = 15.dp)) {
       Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(historyScrollState).testTag("history-scroll-container"), verticalArrangement = Arrangement.spacedBy((8).appDp)) {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy((5).appDp)) {
        Row(Modifier.fillMaxWidth().heightIn(min = (42).appDp).testTag("history-header"), horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(44.dp).clickable(onClick = onBack).testTag("history-back-control"), contentAlignment = Alignment.CenterStart) {
                RailIcon("chevron-left", Modifier.padding(start = 2.appDp).size(20.appDp), HistoryInk, "history-back-icon")
            }
            BasicText("对话历史", Modifier.testTag("history-title"), style = TextStyle(color = HistoryInk, fontSize = (18).appSp, fontWeight = FontWeight.Bold))
            Box(Modifier.size(44.dp).clickable(onClick = onBack).testTag("history-close-control"), contentAlignment = Alignment.CenterEnd) {
                RailIcon("close", Modifier.offset(x = (-1.15).appDp, y = (-2.15).appDp)
                    .width(26.appDp).height(27.appDp), HistoryInk, "history-close-icon")
            }
        }
        Box(Modifier.fillMaxWidth().padding(horizontal = (1).appDp).heightIn(min = (46).appDp).background(NativeColors.surface, RoundedCornerShape((11).appDp))
            .border((1).appDp, HistoryLine, RoundedCornerShape((11).appDp)).testTag("history-search-box"), contentAlignment = Alignment.CenterStart) {
            RailIcon("search", Modifier.offset(x = (-.67).appDp, y = (-2).appDp)
                .padding(start = 15.appDp).width(24.appDp).height(25.appDp), HistoryMuted, "history-search-icon")
            BasicTextField(query, { query = it }, Modifier.fillMaxWidth().padding(start = (52).appDp, end = (12).appDp, top = (10).appDp, bottom = (10).appDp).testTag("history-search"),
                textStyle = TextStyle(color = HistoryInk, fontSize = (15).appSp),
                decorationBox = { inner -> if (query.isEmpty()) BasicText("搜索对话…", Modifier.testTag("history-search-placeholder"), style = TextStyle(color = HistoryMuted)); inner() })
        }
        }
        Box(Modifier.fillMaxWidth().padding(horizontal = (1).appDp)) {
            HistoryButton("新建对话", primary = true, icon = "plus", testTag = "history-create", labelTestTag = "history-create-label", onClick = onCreate)
        }
        Column(Modifier.fillMaxWidth().padding(top = (8).appDp), verticalArrangement = Arrangement.spacedBy((8).appDp)) {
            groups.forEachIndexed { groupIndex, (label, items) ->
                if (items.isNotEmpty()) {
                    if (groupIndex > 0 && label == "昨天") Spacer(Modifier.heightIn(min = (10).appDp))
                    Column(Modifier.fillMaxWidth().testTag(if (label == "今天") "history-today-group" else "history-yesterday-group")) {
                        NativeLineText(label, (18).appSp, Modifier.padding(start = (4).appDp, top = (0).appDp, bottom = (10).appDp), style = TextStyle(
                            color = HistoryInk,
                            fontSize = (16).appSp,
                            lineHeight = (18).appSp,
                            lineHeightStyle = LineHeightStyle(alignment = LineHeightStyle.Alignment.Center, trim = LineHeightStyle.Trim.None, mode = LineHeightStyle.Mode.Fixed),
                            platformStyle = PlatformTextStyle(includeFontPadding = false),
                        ), testTag = "history-group-${label}")
                        items.forEachIndexed { itemIndex, conversation ->
                            val subtitle = store.subtitle(conversation).takeIf(String::isNotBlank)
                            val rowTopPadding = if (subtitle == null) (3).appDp else (15).appDp
                            val rowBottomPadding = when {
                                subtitle == null -> (3).appDp
                                itemIndex == items.lastIndex -> (8).appDp
                                else -> (15).appDp
                            }
                            Row(Modifier.fillMaxWidth()
                                .background(if (conversation.id == currentId) NativeColors.selected else Color.Transparent, RoundedCornerShape((10).appDp))
                                .drawBehind {
                                    val stateWidth = (5).appDp.toPx()
                                    val stateHeight = minOf((60).appDp.toPx(), (size.height - (10).appDp.toPx()).coerceAtLeast(0f))
                                    drawRoundRect(
                                        color = if (conversation.id == currentId) HistoryBlue else Color(0xFFE7ECF4),
                                        topLeft = Offset(0f, (size.height - stateHeight) / 2f),
                                        size = Size(stateWidth, stateHeight),
                                        cornerRadius = CornerRadius(stateWidth / 2f),
                                    )
                                }
                                .padding(start = (28).appDp, end = (6).appDp)
                                .testTag("history-row-${conversation.id}"),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically) {
                                Column(Modifier.weight(1f).padding(top = rowTopPadding, bottom = rowBottomPadding).clickable { onSelect(conversation.id) }.testTag("history-select-${conversation.id}"), verticalArrangement = Arrangement.spacedBy((4).appDp)) {
                                    NativeLineText(historyTitleText(conversation.title), (24).appSp, style = TextStyle(
                                        color = HistoryInk,
                                        fontSize = (18).appSp,
                                        letterSpacing = (1.15).appSp,
                                        lineHeight = (24).appSp,
                                        lineHeightStyle = LineHeightStyle(alignment = LineHeightStyle.Alignment.Center, trim = LineHeightStyle.Trim.None, mode = LineHeightStyle.Mode.Fixed),
                                        fontWeight = FontWeight.SemiBold,
                                        platformStyle = PlatformTextStyle(includeFontPadding = false),
                                    ), testTag = "history-title-${conversation.id}")
                                    subtitle?.let { NativeLineText(it, (20).appSp, style = TextStyle(
                                        color = HistoryMuted,
                                        fontSize = (15).appSp,
                                        letterSpacing = (1.95).appSp,
                                        lineHeight = (20).appSp,
                                        lineHeightStyle = LineHeightStyle(alignment = LineHeightStyle.Alignment.Center, trim = LineHeightStyle.Trim.None, mode = LineHeightStyle.Mode.Fixed),
                                        platformStyle = PlatformTextStyle(includeFontPadding = false),
                                    ), testTag = "history-subtitle-${conversation.id}") }
                                }
                                Box(Modifier.size(44.dp).clickable { actionFor = conversation }
                                    .semantics { contentDescription = "更多操作" }
                                    .testTag("history-more-${conversation.id}"), contentAlignment = Alignment.Center) {
                                    HistoryMoreIcon(Modifier.width((20).appDp).height((16).appDp).testTag("history-more-icon-${conversation.id}"))
                                }
                            }
                        }
                    }
                }
            }
            if (conversations.isEmpty()) BasicText("没有匹配的对话", Modifier.padding(vertical = (20).appDp), style = TextStyle(color = HistoryMuted))
        }
       }
        Row(
            Modifier.fillMaxWidth().height(44.dp).testTag("history-fixed-bottom-links"),
            horizontalArrangement = Arrangement.spacedBy((56).appDp, Alignment.CenterHorizontally),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                Modifier.widthIn(min = 44.dp).height(44.dp).clickable(onClick = onSettings)
                    .padding(horizontal = (8).appDp).testTag("history-settings-hit-target"),
                contentAlignment = Alignment.BottomCenter
            ) {
                Row(Modifier.padding(bottom = (6).appDp), verticalAlignment = Alignment.CenterVertically) {
                    RailIcon("settings", Modifier.size(24.appDp), HistoryMuted, "history-settings-icon")
                    BasicText("模型与设置", Modifier.padding(start = (8).appDp).testTag("history-settings-label"), style = TextStyle(color = HistoryMuted))
                }
            }
            Box(Modifier.width((1).appDp).height(44.dp).testTag("history-footer-divider"), contentAlignment = Alignment.BottomCenter) {
                Box(Modifier.padding(bottom = (6).appDp)) {
                    Box(Modifier.width((1).appDp).height((20).appDp).background(HistoryLine))
                }
            }
            Box(
                Modifier.widthIn(min = 44.dp).height(44.dp).clickable(onClick = onHelp)
                    .padding(start = (2).appDp, end = (14).appDp).testTag("history-help-hit-target"),
                contentAlignment = Alignment.BottomCenter
            ) {
                Row(Modifier.padding(bottom = (6).appDp), verticalAlignment = Alignment.CenterVertically) {
                    RailIcon("help", Modifier.size(24.appDp), HistoryMuted, "history-help-icon")
                    BasicText("使用帮助", Modifier.padding(start = (8).appDp).testTag("history-help-label"), style = TextStyle(color = HistoryMuted))
                }
            }
        }
      }
      actionFor?.let { conversation ->
          Box(Modifier.fillMaxSize(), contentAlignment = Alignment.BottomCenter) {
              Box(Modifier.fillMaxSize().clickable { actionFor = null })
              Column(Modifier.fillMaxWidth().windowInsetsPadding(systemInsets).padding(start = 15.dp, end = 15.dp, bottom = (50).appDp), verticalArrangement = Arrangement.spacedBy((8).appDp)) {
                  Column(Modifier.fillMaxWidth().background(NativeColors.surface, RoundedCornerShape((16).appDp))
                      .testTag("history-actions-panel").padding(start = (16).appDp, end = (16).appDp, top = (8).appDp, bottom = (3).appDp)) {
                      BasicText("对话操作", Modifier.fillMaxWidth().heightIn(min = (33).appDp).testTag("history-actions-title"), style = TextStyle(color = HistoryMuted, fontSize = (14).appSp, textAlign = androidx.compose.ui.text.style.TextAlign.Center))
                      HistoryMenuItem("重命名", "edit", "history-rename-menu") { actionFor = null; renameFor = conversation }
                      Box(Modifier.fillMaxWidth().height((1).appDp).background(HistoryLine))
                      HistoryMenuItem("删除对话", "trash", "history-delete-menu", danger = true) { actionFor = null; deleteFor = conversation }
                  }
                  Box(Modifier.fillMaxWidth().heightIn(min = (55).appDp).background(NativeColors.surface, RoundedCornerShape((16).appDp))
                      .clickable { actionFor = null }.testTag("history-actions-cancel"), contentAlignment = Alignment.Center) {
                      BasicText("取消", Modifier.testTag("history-actions-cancel-label"), style = TextStyle(color = HistoryInk, fontSize = (16).appSp))
                  }
              }
          }
      }
    }

    renameFor?.let { conversation ->
        Dialog(onDismissRequest = { renameFor = null }) {
            Column(Modifier.fillMaxWidth().background(NativeColors.surface, RoundedCornerShape((16).appDp)).padding((16).appDp), verticalArrangement = Arrangement.spacedBy((12).appDp)) {
                BasicText("重命名对话", style = TextStyle(color = HistoryInk, fontWeight = FontWeight.Bold))
                BasicTextField(renameValue, { renameValue = it }, Modifier.fillMaxWidth().border((1).appDp, HistoryLine, RoundedCornerShape((8).appDp)).padding((10).appDp).testTag("history-rename"), textStyle = TextStyle(color = HistoryInk))
                Row(horizontalArrangement = Arrangement.spacedBy((8).appDp)) {
                    HistoryButton("保存") { onRename(conversation.id, renameValue); renameFor = null }
                    HistoryButton("取消") { renameFor = null }
                }
            }
        }
    }
    deleteFor?.let { conversation ->
        Dialog(onDismissRequest = { deleteFor = null }) {
            Column(Modifier.fillMaxWidth().background(NativeColors.surface, RoundedCornerShape((16).appDp)).padding((16).appDp), verticalArrangement = Arrangement.spacedBy((12).appDp)) {
                BasicText("删除对话", style = TextStyle(color = HistoryInk, fontWeight = FontWeight.Bold))
                BasicText("确定删除「${conversation.title}」？该操作不可恢复。", style = TextStyle(color = HistoryMuted))
                Row(horizontalArrangement = Arrangement.spacedBy((8).appDp)) {
                    HistoryButton("删除对话", danger = true, testTag = "history-delete-confirm") { onDelete(conversation.id); deleteFor = null }
                    HistoryButton("取消") { deleteFor = null }
                }
            }
        }
    }
}

@Composable
private fun HistoryMoreIcon(modifier: Modifier = Modifier) {
    androidx.compose.foundation.Canvas(modifier) {
        val diameter = (4).appDp.toPx()
        val centerY = size.height / 2f
        val gap = (4).appDp.toPx()
        val centerX = size.width / 2f
        for (offset in -1..1) {
            drawCircle(HistoryMuted, diameter / 2f, Offset(centerX + offset * (diameter + gap), centerY))
        }
    }
}

@Composable
private fun HistoryButton(text: String, primary: Boolean = false, danger: Boolean = false, icon: String? = null, testTag: String? = null, labelTestTag: String? = null, onClick: () -> Unit) {
    val color = when { danger -> NativeColors.danger; primary -> Color.White; else -> HistoryBlue }
    val border = if (primary) HistoryBlue else HistoryLine
    val buttonModifier = Modifier.fillMaxWidth().border((1).appDp, border, RoundedCornerShape((10).appDp))
        .background(if (primary) HistoryBlue else NativeColors.surface, RoundedCornerShape((10).appDp)).clickable(onClick = onClick)
        .heightIn(min = if (primary) (50).appDp else 44.dp).then(if (testTag == null) Modifier else Modifier.testTag(testTag))
    if (icon != null) {
        Row(buttonModifier.padding(horizontal = (12).appDp), horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically) {
            RailIcon(icon, Modifier.size(21.appDp), color)
            BasicText(text, Modifier.padding(start = (8).appDp).then(if (labelTestTag == null) Modifier else Modifier.testTag(labelTestTag)), style = TextStyle(color = color, fontSize = 18.appSp, fontWeight = FontWeight.Medium))
        }
    } else BasicText(text, buttonModifier.padding(horizontal = (12).appDp, vertical = (11).appDp), style = TextStyle(color = color, fontSize = (14).appSp, fontWeight = if (primary) FontWeight.Medium else FontWeight.Normal))
}

@Composable
private fun HistoryMenuItem(text: String, icon: String, tag: String, danger: Boolean = false, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = (52).appDp).clickable(onClick = onClick).testTag(tag)
        .padding(horizontal = (9).appDp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(26.appDp), contentAlignment = Alignment.Center) {
            // Preserve the independent row hit target; align only the complete
            // source glyph inside its existing icon slot.
            RailIcon(icon, Modifier.offset(x = 1.1.appDp, y = 1.7.appDp)
                .size((if (danger) 27 else 24).appDp),
                if (danger) NativeColors.danger else HistoryInk, "icon-$icon")
        }
        BasicText(text, Modifier.padding(start = (17).appDp).testTag("$tag-label"), style = TextStyle(color = if (danger) NativeColors.danger else HistoryInk, fontSize = (15).appSp))
    }
}

private fun localDate(millis: Long): LocalDate = Instant.ofEpochMilli(millis).atZone(ZoneId.systemDefault()).toLocalDate()
