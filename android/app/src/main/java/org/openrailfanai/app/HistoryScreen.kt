package org.openrailfanai.app

import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.interaction.MutableInteractionSource
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
import androidx.activity.compose.BackHandler
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.boundsInRoot
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.geometry.Rect
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
    var actionAnchor by remember { mutableStateOf(Rect.Zero) }
    val rowAnchors = remember { mutableMapOf<String, Rect>() }
    var safeOrigin by remember { mutableStateOf(Offset.Zero) }
    val density = LocalDensity.current
    BackHandler(enabled = actionFor != null) { actionFor = null }
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

    val safeModifier = if (applySystemInsets) Modifier.windowInsetsPadding(systemInsets).imePadding() else Modifier
    Box(modifier.fillMaxSize().testTag("history-drawer")) {
    BoxWithConstraints(Modifier.fillMaxSize().then(safeModifier).testTag("history-safe-content").onGloballyPositioned { safeOrigin = it.boundsInRoot().topLeft }) {
        val drawerWidth = (maxWidth * .86f).coerceAtMost(420.dp)
        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = .22f))
            .clickable(onClick = onBack).semantics { contentDescription = "关闭对话侧栏" }
            .testTag("history-drawer-scrim"))
        Column(Modifier.width(drawerWidth).fillMaxHeight().railEntrance(fromLeft = true)
            .clip(RoundedCornerShape(topEnd = 24.dp, bottomEnd = 24.dp))
            .background(NativeColors.surface).pointerInput(Unit) { detectTapGestures {} }.testTag("history-content")
            .padding(horizontal = 16.dp)) {
            Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).testTag("history-header"),
                verticalAlignment = Alignment.CenterVertically) {
                BasicText("对话历史", Modifier.weight(1f).testTag("history-title"),
                    style = TextStyle(color = HistoryInk, fontSize = 20.appSp, fontWeight = FontWeight.SemiBold))
                Box(Modifier.size(48.dp).clickable(onClick = onBack).testTag("history-back-control"), contentAlignment = Alignment.Center) {
                    RailIcon("close", Modifier.size(22.dp).testTag("history-close-control"), HistoryMuted, "history-close-icon")
                }
            }
            Row(Modifier.fillMaxWidth().heightIn(min = 48.dp)
                .background(NativeColors.background, RoundedCornerShape(24.dp)).testTag("history-search-box")
                .padding(horizontal = 14.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                RailIcon("search", Modifier.size(20.dp), HistoryMuted, "history-search-icon")
                BasicTextField(query, { query = it }, Modifier.weight(1f).padding(vertical = 12.dp).testTag("history-search"),
                    singleLine = true, textStyle = TextStyle(color = HistoryInk, fontSize = 15.appSp),
                    decorationBox = { inner ->
                        if (query.isEmpty()) BasicText("搜索对话…", Modifier.testTag("history-search-placeholder"), style = TextStyle(color = HistoryMuted, fontSize = 15.appSp))
                        inner()
                    })
            }
            Spacer(Modifier.height(12.dp))
            HistoryButton("新建对话", primary = true, icon = "plus", testTag = "history-create", labelTestTag = "history-create-label", onClick = onCreate)
            Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(historyScrollState)
                .testTag("history-scroll-container").padding(top = 20.dp, bottom = 16.dp),
                verticalArrangement = Arrangement.spacedBy(18.dp)) {
                groups.forEach { (label, items) ->
                    if (items.isNotEmpty()) {
                        Column(Modifier.fillMaxWidth().testTag(when (label) {
                            "今天" -> "history-today-group"
                            "昨天" -> "history-yesterday-group"
                            else -> "history-earlier-group"
                        }), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            BasicText(label, Modifier.padding(start = 12.dp, bottom = 6.dp).testTag("history-group-${label}"),
                                style = TextStyle(color = HistoryMuted, fontSize = 14.appSp))
                            items.forEach { conversation ->
                                val selected = conversation.id == currentId
                                Row(Modifier.fillMaxWidth().background(if (selected) NativeColors.selected else Color.Transparent, RoundedCornerShape(12.dp))
                                    .testTag("history-row-${conversation.id}"), verticalAlignment = Alignment.CenterVertically) {
                                    Box(Modifier.weight(1f).heightIn(min = 48.dp).clickable { onSelect(conversation.id) }
                                        .padding(horizontal = 12.dp, vertical = 8.dp).testTag("history-select-${conversation.id}"),
                                        contentAlignment = Alignment.CenterStart) {
                                        BasicText(conversation.title, Modifier.testTag("history-title-${conversation.id}"),
                                            style = TextStyle(color = if (selected) HistoryBlue else HistoryInk, fontSize = 16.appSp,
                                                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal), maxLines = 1,
                                            overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)

                                    }
                                    Box(Modifier.size(48.dp).onGloballyPositioned { rowAnchors[conversation.id] = it.boundsInRoot() }
                                        .clickable { actionAnchor = rowAnchors[conversation.id] ?: Rect.Zero; actionFor = conversation }
                                        .semantics { contentDescription = "${conversation.title}，更多操作" }.testTag("history-more-${conversation.id}"),
                                        contentAlignment = Alignment.Center) {
                                        HistoryMoreIcon(Modifier.size(20.dp).testTag("history-more-icon-${conversation.id}"))
                                    }
                                }
                            }
                        }
                    }
                }
                if (conversations.isEmpty()) BasicText(if (query.isBlank()) "还没有对话，开始聊聊吧" else "没有匹配的对话",
                    Modifier.padding(12.dp), style = TextStyle(color = HistoryMuted, fontSize = 14.appSp))
            }
            Box(Modifier.fillMaxWidth().height(1.dp).background(HistoryLine).testTag("history-footer-divider"))
            Row(Modifier.fillMaxWidth().heightIn(min = 60.dp).testTag("history-fixed-bottom-links"), verticalAlignment = Alignment.CenterVertically) {
                Row(Modifier.weight(1f).heightIn(min = 48.dp).clickable(onClick = onSettings)
                    .testTag("history-settings-hit-target"), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    RailIcon("settings", Modifier.size(20.dp), HistoryMuted, "history-settings-icon")
                    BasicText("模型与设置", Modifier.testTag("history-settings-label"), style = TextStyle(color = HistoryInk, fontSize = 14.appSp))
                }
                Row(Modifier.heightIn(min = 48.dp).clickable(onClick = onHelp).testTag("history-help-hit-target")
                    .padding(start = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    RailIcon("help", Modifier.size(20.dp), HistoryMuted, "history-help-icon")
                    BasicText("帮助", Modifier.testTag("history-help-label"), style = TextStyle(color = HistoryMuted, fontSize = 14.appSp))
                }
            }
        }
    actionFor?.let { conversation ->
        val menuWidth = 224.dp.coerceAtMost(drawerWidth - 32.dp)
        val menuHeight = 113.dp
        val anchorLeft = with(density) { (actionAnchor.right - safeOrigin.x).toDp() }
        val anchorTop = with(density) { (actionAnchor.top - safeOrigin.y).toDp() }
        val anchorBottom = with(density) { (actionAnchor.bottom - safeOrigin.y).toDp() }
        val menuX = (anchorLeft - menuWidth).coerceIn(16.dp, (drawerWidth - menuWidth - 16.dp).coerceAtLeast(16.dp))
        val preferredY = if (anchorTop >= menuHeight + 8.dp) anchorTop - menuHeight - 8.dp else anchorBottom + 8.dp
        val menuY = preferredY.coerceIn(8.dp, (maxHeight - menuHeight - 8.dp).coerceAtLeast(8.dp))
        Box(Modifier.fillMaxSize().clickable(
            interactionSource = remember { MutableInteractionSource() }, indication = null,
            onClick = { actionFor = null }).testTag("history-actions-dismiss"))
        Column(Modifier.offset(menuX, menuY).width(menuWidth).railEntrance()
            .shadow(8.dp, RoundedCornerShape(16.dp))
            .clip(RoundedCornerShape(16.dp)).background(NativeColors.surface)
            .border(1.dp, HistoryLine, RoundedCornerShape(16.dp))
            .pointerInput(Unit) { detectTapGestures {} }
            .testTag("history-actions-panel").padding(vertical = 8.dp)) {
            HistoryMenuItem("重命名", "edit", "history-rename-menu") { actionFor = null; renameFor = conversation }
            Box(Modifier.fillMaxWidth().height(1.dp).background(HistoryLine))
            HistoryMenuItem("删除对话", "trash", "history-delete-menu", danger = true) { actionFor = null; deleteFor = conversation }
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
                    Box(Modifier.weight(1f)) { HistoryButton("保存") { onRename(conversation.id, renameValue); renameFor = null } }
                    Box(Modifier.weight(1f)) { HistoryButton("取消") { renameFor = null } }
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
                    Box(Modifier.weight(1f)) { HistoryButton("删除对话", danger = true, testTag = "history-delete-confirm") { onDelete(conversation.id); deleteFor = null } }
                    Box(Modifier.weight(1f)) { HistoryButton("取消") { deleteFor = null } }
                }
            }
        }
    }
}

@Composable
private fun HistoryMoreIcon(modifier: Modifier = Modifier) {
    RailIcon("more", modifier, HistoryMuted)
}

@Composable
private fun HistoryButton(text: String, primary: Boolean = false, danger: Boolean = false, icon: String? = null, testTag: String? = null, labelTestTag: String? = null, onClick: () -> Unit) {
    val color = when { danger -> NativeColors.danger; primary -> Color.White; else -> HistoryBlue }
    val border = if (primary) HistoryBlue else HistoryLine
    val buttonModifier = Modifier.fillMaxWidth().border((1).appDp, border, RoundedCornerShape((10).appDp))
        .background(if (primary) HistoryBlue else NativeColors.surface, RoundedCornerShape((10).appDp)).clickable(onClick = onClick)
        .heightIn(min = if (primary) (50).appDp else 48.dp).then(if (testTag == null) Modifier else Modifier.testTag(testTag))
    if (icon != null) {
        Row(buttonModifier.padding(horizontal = (12).appDp), horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically) {
            RailIcon(icon, Modifier.size(21.appDp), color)
            BasicText(text, Modifier.padding(start = (8).appDp).then(if (labelTestTag == null) Modifier else Modifier.testTag(labelTestTag)), style = TextStyle(color = color, fontSize = 18.appSp, fontWeight = FontWeight.Medium))
        }
    } else Box(buttonModifier.padding(horizontal = 12.dp, vertical = 11.dp), contentAlignment = Alignment.Center) {
        BasicText(text, Modifier.then(if (labelTestTag == null) Modifier else Modifier.testTag(labelTestTag)),
            style = TextStyle(color = color, fontSize = 16.appSp, fontWeight = if (primary) FontWeight.Medium else FontWeight.Normal))
    }
}

@Composable
private fun HistoryMenuItem(text: String, icon: String, tag: String, danger: Boolean = false, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable(onClick = onClick).testTag(tag)
        .padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
        RailIcon(icon, Modifier.size(22.dp), if (danger) NativeColors.danger else HistoryInk, "icon-$icon")
        BasicText(text, Modifier.padding(start = 12.dp).testTag("$tag-label"),
            style = TextStyle(color = if (danger) NativeColors.danger else HistoryInk, fontSize = 16.appSp))
    }
}

private fun localDate(millis: Long): LocalDate = Instant.ofEpochMilli(millis).atZone(ZoneId.systemDefault()).toLocalDate()
