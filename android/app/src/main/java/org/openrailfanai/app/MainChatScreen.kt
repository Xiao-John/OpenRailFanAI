package org.openrailfanai.app

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.requiredSize
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.getValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.layout
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.dp
import org.json.JSONObject
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import java.time.LocalDate

/** The production Main chat page. Instrumentation fixtures call this same screen and its real children. */
@Composable
internal fun MainChatScreen(
    status: String,
    connected: Boolean,
    chatState: ChatUiState,
    messages: List<StoredMessage>,
    input: String,
    onInputChange: (String) -> Unit,
    onOpenHistory: () -> Unit,
    onNewConversation: () -> Unit,
    onSubmit: () -> Unit,
    onStop: () -> Unit,
    onReadingChange: (Boolean) -> Unit,
    onAction: (JSONObject, String, Boolean) -> Unit,
    onRetry: () -> Unit,
    onSettings: (String?) -> Unit,
    onCopy: (String) -> Unit = {},
    onRegenerate: (String) -> Unit = {},
    onRetryQuery: (String) -> Unit = { onRetry() },
    onFollowup: (String) -> Unit = {},
    datePicker: ((LocalDate, (LocalDate) -> Unit) -> Unit)? = null,
    modifier: Modifier = Modifier,
    applySystemInsets: Boolean = true,
    systemInsets: WindowInsets = WindowInsets.safeDrawing,
    onScrollDiagnostics: ((Int, Int, Int, Int, List<Int>) -> Unit)? = null,
    onLayoutDiagnostics: ((String, IntArray, IntArray) -> Unit)? = null,
    autoScrollToLatest: Boolean = true,
    onBack: () -> Unit = {},
) {
    val listState = rememberLazyListState()
    val persistedResults = messages.filter { it.role == "assistant" }.flatMap {
        DisplayResultParser.parseArray(it.meta?.optJSONArray("displayResults"))
    }
    val unpersistedResults = chatState.results.filterNot { it in persistedResults }
    val stoppedAnswerPersisted = messages.lastOrNull()?.let {
        it.role == "assistant" && it.meta?.optBoolean("stopped") == true && it.content == chatState.answer
    } == true
    val followupSchedule = messages.asReversed().firstNotNullOfOrNull { message ->
        DisplayResultParser.parseArray(message.meta?.optJSONArray("displayResults"))
            .filterIsInstance<TrainScheduleDisplay>().lastOrNull()
    }
    val inputFocusRequester = remember { FocusRequester() }
    val scope = rememberCoroutineScope()
    LaunchedEffect(listState) {
        snapshotFlow {
            val layout = listState.layoutInfo
            val last = layout.visibleItemsInfo.lastOrNull()
            val atBottom = if (layout.totalItemsCount > 0) {
                last != null && last.index == layout.totalItemsCount - 1 && last.offset + last.size <= layout.viewportEndOffset
            } else null
            ScrollDiagnostics(
                firstVisibleIndex = listState.firstVisibleItemIndex,
                firstVisibleOffset = listState.firstVisibleItemScrollOffset,
                viewportStart = layout.viewportStartOffset,
                viewportEnd = layout.viewportEndOffset,
                visibleIndices = layout.visibleItemsInfo.map { it.index },
                atBottom = atBottom,
            )
        }.collect { diagnostic ->
            onScrollDiagnostics?.invoke(diagnostic.firstVisibleIndex, diagnostic.firstVisibleOffset,
                diagnostic.viewportStart, diagnostic.viewportEnd, diagnostic.visibleIndices)
            diagnostic.atBottom?.let { onReadingChange(!it) }
        }
    }
    LaunchedEffect(chatState.answer, chatState.results.size, chatState.phase, chatState.reading, messages.size) {
        if (autoScrollToLatest && !chatState.reading) {
            val itemCount = snapshotFlow { listState.layoutInfo.totalItemsCount }.first { it > 0 }
            if (!chatState.reading) listState.animateScrollToItem(itemCount - 1)
        }
    }

    val pageModifier = modifier.fillMaxSize().background(NativeColors.background)
    // Insets are consumed here once; the composer ends at the live safe bottom,
    // without adding a fixed gap copied from the mockup's device illustration.
    Column((if (applySystemInsets) pageModifier.windowInsetsPadding(systemInsets).imePadding() else pageModifier)
        .captureLayoutConstraints("main-safe-content", onLayoutDiagnostics)
        .testTag("main-safe-content")) {
      Column(Modifier.fillMaxSize().padding(horizontal = 15.dp).testTag("main-page-content")) {
        Box(
            Modifier.fillMaxWidth().height(44.dp).captureLayoutConstraints("main-topbar", onLayoutDiagnostics).testTag("main-topbar"),
        ) {
            Box(
                Modifier.align(androidx.compose.ui.Alignment.CenterStart).requiredSize(44.dp)
                    .clickable(onClick = onBack).semantics { contentDescription = "返回"; role = Role.Button }
                    .testTag("main-back-control"),
                contentAlignment = androidx.compose.ui.Alignment.Center,
            ) {
                RailIcon("chevron-left", Modifier.offset(x = (-12.5).appDp, y = (-2.25).appDp).size(20.appDp), NativeColors.ink, "main-back-icon")
            }
            Box(
                Modifier.align(androidx.compose.ui.Alignment.CenterStart).offset(x = 44.dp).requiredSize(44.dp)
                    .clickable(onClick = onOpenHistory).semantics { contentDescription = "对话历史"; role = Role.Button }
                    .testTag("main-history-control"),
                contentAlignment = androidx.compose.ui.Alignment.Center,
            ) {
                RailIcon("history", Modifier.offset(x = (-9.5).appDp).size(24.appDp), NativeColors.ink, "main-history-icon")
            }
            Row(
                Modifier.align(androidx.compose.ui.Alignment.TopCenter),
                horizontalArrangement = Arrangement.spacedBy(5.appDp),
                verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
            ) {
                // Source viewBox 38×40, projected from the complete 438px canvas.
                RailIcon("train-logo", Modifier.width(36.50.appDp).height(38.42.appDp), NativeColors.blue, "main-brand-icon")
                BasicText("RailFanAI", Modifier.testTag("main-brand-label"), style = TextStyle(color = NativeColors.ink, fontSize = 20.appSp,
                    fontWeight = androidx.compose.ui.text.font.FontWeight.Bold))
            }
            Box(
                Modifier.align(androidx.compose.ui.Alignment.CenterEnd).requiredSize(44.dp)
                    .clickable(onClick = onNewConversation).semantics { contentDescription = "新建对话"; role = Role.Button }
                    .testTag("main-new-control"),
                contentAlignment = androidx.compose.ui.Alignment.Center,
            ) {
                RailIcon("plus", Modifier.offset(x = (4.3).appDp, y = (-2.15).appDp).size(24.appDp), NativeColors.ink, "main-new-icon")
            }
        }
        if (!connected && status.isNotBlank()) {
            BasicText(status, style = TextStyle(color = NativeColors.muted, fontSize = 13.appSp))
        }
        Box(Modifier.weight(1f).fillMaxWidth().captureLayoutConstraints("main-list-container", onLayoutDiagnostics).testTag("main-list-container")) {
        LazyColumn(
            state = listState,
            modifier = Modifier.fillMaxSize().captureLayoutConstraints("main-message-list", onLayoutDiagnostics).testTag("main-message-list"),
            verticalArrangement = Arrangement.spacedBy(14.appDp),
        ) {
            messages.forEachIndexed { index, message ->
                item(key = "message-$index-${message.role}") {
                    if (message.role == "user") {
                        val promptStyle = TextStyle(color = Color.White, fontSize = 17.5.appSp)
                        val promptHorizontalPadding = 16.5.appDp
                        Box(Modifier.fillMaxWidth().padding(top = 12.5.appDp)
                            .captureLayoutConstraints("main-user-prompt-row", onLayoutDiagnostics).testTag("main-user-prompt-row")) {
                            Row(Modifier.fillMaxWidth().padding(end = 42.appDp), horizontalArrangement = Arrangement.End) {
                                Box(
                                    Modifier.widthIn(min = 249.appDp).heightIn(min = 50.appDp)
                                        .captureLayoutConstraints("main-user-prompt-frame", onLayoutDiagnostics)
                                        .background(NativeColors.blue, RoundedCornerShape(16.appDp)).testTag("main-user-prompt-frame"),
                                    contentAlignment = androidx.compose.ui.Alignment.CenterStart,
                                ) {
                                    BasicText(message.content, Modifier.padding(horizontal = promptHorizontalPadding, vertical = 12.appDp)
                                        .captureLayoutConstraints("main-user-prompt-text", onLayoutDiagnostics).testTag("main-user-prompt"),
                                        style = promptStyle)
                                }
                            }
                            Box(Modifier.align(androidx.compose.ui.Alignment.CenterEnd)
                                .size(36.appDp).background(NativeColors.selected, CircleShape).testTag("main-user-avatar"),
                                contentAlignment = androidx.compose.ui.Alignment.Center) {
                                RailIcon("person", Modifier.size(22.appDp), NativeColors.blue, "main-user-avatar-icon")
                            }
                        }
                    } else {
                        if (message.content.isNotBlank()) Row(Modifier.fillMaxWidth(), verticalAlignment = androidx.compose.ui.Alignment.Top) {
                            Box(Modifier.size(50.appDp), contentAlignment = androidx.compose.ui.Alignment.TopStart) {
                                Box(Modifier.size(40.appDp).background(NativeColors.selected, CircleShape).testTag("main-assistant-avatar"),
                                    contentAlignment = androidx.compose.ui.Alignment.Center) {
                                    RailIcon("train-logo", Modifier.width(28.82.appDp).height(30.74.appDp), NativeColors.blue, "main-assistant-avatar-icon")
                                }
                            }
                            BasicText(message.content, Modifier.weight(1f).testTag("main-answer-$index"),
                                style = TextStyle(color = NativeColors.ink, fontSize = 16.appSp))
                        }
                        val metadata = message.meta
                        val storedResults = DisplayResultParser.parseArray(metadata?.optJSONArray("displayResults"))
                        val storedRequest = ChatUiState(
                            query = metadata?.optString("query").orEmpty(), phase = ChatPhase.COMPLETED,
                            results = storedResults, intent = metadata?.optString("intent"),
                            sources = metadata?.optJSONArray("sources")?.let { values ->
                                (0 until values.length()).map { values.optString(it) }
                            }.orEmpty(),
                            usageJson = metadata?.optJSONObject("usage")?.toString() ?: "{}",
                            latencyMs = metadata?.takeIf { it.has("latencyMs") && !it.isNull("latencyMs") }?.optDouble("latencyMs"),
                            processLogs = metadata?.optJSONArray("processLogs")?.let { values ->
                                (0 until values.length()).map { values.optString(it) }
                            }.orEmpty(),
                        )
                        storedResults.forEachIndexed { resultIndex, result ->
                                val edgeToEdgeResult = result is TrainBatchDisplay || result is EmptyDisplay || result is ErrorDisplay
                                Row(Modifier.fillMaxWidth().captureLayoutConstraints("result-card-host", onLayoutDiagnostics)) {
                                    if (edgeToEdgeResult) Spacer(Modifier.width(0.appDp)) else Box(
                                        Modifier.width(50.appDp).heightIn(min = 32.appDp),
                                        contentAlignment = androidx.compose.ui.Alignment.TopStart,
                                    ) {
                                        if (message.content.isBlank() && resultIndex == 0) {
                                            Box(Modifier.size(40.appDp).background(NativeColors.selected, CircleShape).testTag("main-assistant-avatar"),
                                                contentAlignment = androidx.compose.ui.Alignment.Center) {
                                                RailIcon("train-logo", Modifier.width(28.82.appDp).height(30.74.appDp), NativeColors.blue, "main-assistant-avatar-icon")
                                            }
                                        }
                                    }
                                    DisplayResultCard(
                                    result, storedRequest, onAction,
                                    onRetry = { onRetryQuery(metadata?.optString("query").orEmpty()) },
                                    modifier = Modifier.weight(1f).captureLayoutConstraints("result-card", onLayoutDiagnostics),
                                    onSettings = {
                                        val category = (result as? ErrorDisplay)?.value?.category
                                            ?: metadata?.optString("errorCategory")
                                        onSettings(category)
                                    },
                                    datePicker = datePicker,
                                    )
                                }
                                (result as? TrainScheduleDisplay)?.let { schedule ->
                                    Row(Modifier.padding(start = 54.appDp, top = 2.appDp).captureLayoutConstraints("result-actions", onLayoutDiagnostics).testTag("result-actions"), horizontalArrangement = Arrangement.spacedBy(40.appDp)) {
                                        ResultAction("复制", "copy", "result-copy", onClick = { onCopy(scheduleClipboardText(schedule.value)) })
                                        ResultAction("重新生成", "refresh", "result-regenerate", onClick = { onRegenerate(metadata?.optString("query").orEmpty()) })
                                    }
                                }
                        }
                        metadata?.optString("error")?.takeIf(String::isNotBlank)?.let { errorMessage ->
                            Row(Modifier.fillMaxWidth()) {
                                Spacer(Modifier.width(0.appDp))
                                DisplayResultCard(
                                    ErrorDisplay(ErrorResult("failed", errorMessage, metadata.optString("errorCategory"), null)),
                                    chatState, onAction,
                                    onRetry = { onRetryQuery(metadata.optString("query")) },
                                    onSettings = { onSettings(metadata.optString("errorCategory")) },
                                    modifier = Modifier.weight(1f),
                                )
                            }
                        }
                    }
                }
            }
            if (chatState.busy) item {
                Row(Modifier.fillMaxWidth().padding(horizontal = 16.appDp)) {
                    QueryLoadingCard(chatState.recognized, chatState.stage, Modifier.weight(1f))
                }
            }
            if (chatState.answer.isNotBlank() && chatState.busy) item {
                BasicText(chatState.answer, style = TextStyle(color = NativeColors.ink, fontSize = 16.appSp))
            }
            if (chatState.phase == ChatPhase.STOPPED && chatState.answer.isNotBlank() && !stoppedAnswerPersisted) item {
                BasicText(chatState.answer, style = TextStyle(color = NativeColors.ink, fontSize = 16.appSp))
            }
            itemsIndexed(if (chatState.busy || chatState.phase == ChatPhase.STOPPED) unpersistedResults else emptyList()) { _, result ->
                DisplayResultCard(
                    result, chatState, onAction, onRetry,
                    onSettings = { onSettings(chatState.error?.let(::classifyMainError)) },
                )
            }
        }
        }
        // Keep the reading control outside the scroll viewport so it cannot
        // cover visible result actions; its independent 44dp target is retained.
        if (chatState.reading) {
                        Box(
                Modifier.align(androidx.compose.ui.Alignment.CenterHorizontally)
                    .heightIn(min = 44.dp)
                    .clickable {
                        onReadingChange(false)
                        scope.launch { listState.animateScrollToItem((listState.layoutInfo.totalItemsCount - 1).coerceAtLeast(0)) }
                    }
                    .semantics {
                        contentDescription = "回到底部"
                        role = Role.Button
                    }
                    .testTag("return-to-bottom"),
                contentAlignment = androidx.compose.ui.Alignment.BottomCenter,
            ) {
                            Row(
                    Modifier
                        .testTag("return-to-bottom-visual")
                        .background(NativeColors.surface, RoundedCornerShape(24.appDp))
                        .border(1.appDp, NativeColors.line, RoundedCornerShape(24.appDp))
                        .padding(start = 22.appDp, end = 34.appDp, top = 10.appDp, bottom = 10.appDp),
                    verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.appDp),
                ) {
                    RailIcon("down", Modifier.size(18.appDp), NativeColors.blue, "return-to-bottom-icon")
                    BasicText("有新内容 · 回到底部", Modifier.testTag("return-to-bottom-label"), style = TextStyle(color = NativeColors.blue, fontSize = 14.appSp))
                }
            }
        }
        followupSchedule?.let { schedule ->
            ScheduleFollowupActions(schedule.value, chatState.reading, inputFocusRequester, onFollowup, onLayoutDiagnostics)
        }
        val separateInputPresentation = chatState.busy || chatState.reading
        val loadingInputInset = when {
            chatState.busy -> 16.appDp
            chatState.reading -> 17.appDp
            else -> 0.appDp
        }
        val composerShape = RoundedCornerShape(30.appDp)
        Row(
            Modifier.fillMaxWidth()
                .padding(horizontal = loadingInputInset)
                .padding(top = 8.appDp).heightIn(min = maxOf(44.dp, if (chatState.busy) 51.appDp else 56.appDp))
                .then(if (separateInputPresentation) Modifier else Modifier
                    .background(NativeColors.surface, composerShape)
                    .border(1.appDp, NativeColors.line, composerShape))
                .captureLayoutConstraints("main-fixed-input", onLayoutDiagnostics).testTag("main-fixed-input"),
            verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.appDp),
        ) {
            BasicTextField(
                value = input,
                onValueChange = onInputChange,
                textStyle = TextStyle(color = NativeColors.ink, fontSize = 16.appSp),
                modifier = Modifier.weight(1f)
                    .heightIn(min = maxOf(44.dp, if (chatState.busy) 51.appDp else 56.appDp))
                    .then(if (separateInputPresentation) Modifier
                        .background(NativeColors.surface, composerShape)
                        .border(1.appDp, NativeColors.line, composerShape) else Modifier)
                    .padding(horizontal = 16.appDp, vertical = 15.appDp)
                    .focusRequester(inputFocusRequester)
                    .semantics { contentDescription = "消息输入框" }.testTag("main-input-field"),
                decorationBox = { inner ->
                    if (input.isEmpty()) BasicText("继续追问…", Modifier.testTag("main-input-placeholder"),
                        style = TextStyle(color = NativeColors.muted, fontSize = 16.appSp))
                    inner()
                },
            )
            Box(
                Modifier.then(if (separateInputPresentation) Modifier else Modifier.padding(end = 3.appDp))
                    .size(if (chatState.busy) maxOf(44.dp, 50.appDp) else 44.dp)
                    .background(NativeColors.blue, CircleShape)
                    .clickable(enabled = if (chatState.busy) true else connected, role = Role.Button) {
                        if (chatState.busy) onStop() else onSubmit()
                    }
                    .semantics {
                        contentDescription = if (chatState.busy) "停止生成" else "查询"
                        role = Role.Button
                    }.testTag("main-submit-control"),
                contentAlignment = androidx.compose.ui.Alignment.Center,
            ) {
                RailIcon(if (chatState.busy) "stop" else if (chatState.reading) "up" else "send",
                    Modifier.then(if (!chatState.busy && !chatState.reading) Modifier.offset(x = 2.25.appDp, y = (-1.9).appDp) else Modifier)
                        .size(22.appDp), Color.White,
                    if (chatState.busy) "main-stop-icon" else "main-send-icon")
            }
        }
        if (chatState.busy) Box(Modifier.fillMaxWidth().padding(bottom = 4.appDp), contentAlignment = androidx.compose.ui.Alignment.Center) {
            BasicText("可停止生成，已返回内容会保留", Modifier.testTag("query-preserve-note"),
                style = TextStyle(color = NativeColors.muted, fontSize = 13.25.appSp, letterSpacing = .25.appSp))
        }
      }
    }
}

@Composable
private fun ResultAction(label: String, icon: String, tag: String, onClick: () -> Unit) {
    Box(Modifier.heightIn(min = 44.dp).clickable(role = Role.Button, onClick = onClick)
        .semantics { contentDescription = label; role = Role.Button }.testTag("$tag-action")) {
        Row(Modifier.padding(top = 9.appDp), horizontalArrangement = Arrangement.spacedBy(10.appDp),
            verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
            RailIcon(icon, Modifier.size(16.appDp), NativeColors.muted, "$tag-icon")
            BasicText(label, Modifier.testTag("$tag-label"), style = TextStyle(color = NativeColors.muted, fontSize = 14.appSp))
        }
    }
}

@Composable
private fun ScheduleFollowupActions(
    schedule: ScheduleResult,
    reading: Boolean,
    inputFocusRequester: FocusRequester,
    onFollowup: (String) -> Unit,
    onLayoutDiagnostics: ((String, IntArray, IntArray) -> Unit)?,
) {
    val choices = if (reading) listOf("查余票" to "search", "换个日期" to "calendar")
        else listOf("查票价" to null, "查担当车组" to null)
    Box(Modifier.fillMaxWidth().padding(bottom = 7.appDp), contentAlignment = androidx.compose.ui.Alignment.Center) {
        Row(Modifier.offset(x = 5.appDp).captureLayoutConstraints("result-suggestions", onLayoutDiagnostics).testTag("result-suggestions"), horizontalArrangement = Arrangement.spacedBy(if (reading) 12.appDp else 8.appDp)) {
            choices.forEachIndexed { choiceIndex, (choice, icon) ->
                Box(
                    Modifier.heightIn(min = 44.dp).clickable(role = Role.Button) {
                        onFollowup(suggestedQuery(choice, schedule))
                        inputFocusRequester.requestFocus()
                    }.semantics { contentDescription = choice; role = Role.Button }
                        .testTag("result-followup-${choiceIndex + 1}"),
                    contentAlignment = androidx.compose.ui.Alignment.Center,
                ) {
                    Row(
                        Modifier.testTag("result-followup-${choiceIndex + 1}-visual")
                            .then(if (reading) Modifier else Modifier.heightIn(min = 44.appDp))
                            .border(1.appDp, NativeColors.line, CircleShape)
                                .padding(horizontal = if (reading) 14.5.appDp else 34.5.appDp,
                                vertical = if (reading) 6.appDp else 11.appDp),
                        horizontalArrangement = Arrangement.spacedBy(6.appDp),
                        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                    ) {
                        icon?.let { RailIcon(it, Modifier.size(16.appDp), NativeColors.blue) }
                        val choiceStyle = if (reading) {
                            TextStyle(color = NativeColors.ink, fontSize = 14.appSp, lineHeight = 20.appSp)
                        } else {
                            TextStyle(color = NativeColors.ink, fontSize = 14.appSp)
                        }
                        BasicText(choice, Modifier.testTag("result-followup-${choiceIndex + 1}-label"), style = choiceStyle)
                    }
                }
            }
        }
    }
}

private fun Modifier.captureLayoutConstraints(
    tag: String,
    observer: ((String, IntArray, IntArray) -> Unit)?,
): Modifier = if (observer == null) this else layout { measurable, constraints ->
    val placeable = measurable.measure(constraints)
    observer(tag, intArrayOf(constraints.minWidth, constraints.minHeight, constraints.maxWidth, constraints.maxHeight),
        intArrayOf(placeable.width, placeable.height))
    layout(placeable.width, placeable.height) { placeable.place(0, 0) }
}

private data class ScrollDiagnostics(
    val firstVisibleIndex: Int,
    val firstVisibleOffset: Int,
    val viewportStart: Int,
    val viewportEnd: Int,
    val visibleIndices: List<Int>,
    val atBottom: Boolean?,
)

private fun classifyMainError(message: String): String = when {
    Regex("HTTP\\s*401|鉴权|API.?Key|密钥", RegexOption.IGNORE_CASE).containsMatchIn(message) -> "auth"
    Regex("连接|网络|超时|DNS", RegexOption.IGNORE_CASE).containsMatchIn(message) -> "network"
    else -> "service"
}

private fun scheduleClipboardText(value: ScheduleResult): String = buildString {
    append(value.trainCode.orEmpty()).append(' ').append(listOfNotNull(value.fromStation, value.toStation).joinToString(" → "))
    value.date?.let { append('\n').append(it) }
    value.duration?.let { append(" · ").append(it) }
    value.stops.forEach { stop -> append('\n').append(stop.station.orEmpty()).append("  ").append(stop.arriveTime ?: "--").append("  ").append(stop.startTime ?: "--") }
}

private fun suggestedQuery(choice: String, value: ScheduleResult): String = when (choice) {
    "查余票" -> "查一下 ${value.trainCode.orEmpty()} 的余票"
    "换个日期" -> "查一下 ${value.trainCode.orEmpty()} 其他日期的时刻表"
    "查票价" -> "查一下 ${value.fromStation.orEmpty()} 到 ${value.toStation.orEmpty()} 的票价"
    else -> "查一下 ${value.trainCode.orEmpty()} 的担当车组"
}
