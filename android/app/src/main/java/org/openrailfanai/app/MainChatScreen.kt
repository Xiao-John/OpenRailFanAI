package org.openrailfanai.app

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.ExperimentalLayoutApi
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
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
@OptIn(ExperimentalLayoutApi::class)
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
    onShare: (String) -> Unit = {},
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
    conversationTokens: Long? = null,
) {
    val listState = rememberLazyListState()
    val tokenTotal = conversationTokens ?: messages.filter { it.role == "assistant" }.sumOf { replyTokenUsage(it.meta) ?: 0L }
    val persistedResults = messages.filter { it.role == "assistant" }.flatMap {
        DisplayResultParser.parseArray(it.meta?.optJSONArray("displayResults"))
    }
    val unpersistedResults = chatState.results.filterNot { it in persistedResults }
    val stoppedAnswerPersisted = messages.lastOrNull()?.let {
        it.role == "assistant" && it.meta?.optBoolean("stopped") == true && it.content == chatState.answer
    } == true
    val followupSchedule = messages.lastOrNull()?.takeIf { it.role == "assistant" }?.let { message ->
        DisplayResultParser.parseArray(message.meta?.optJSONArray("displayResults"))
            .filterIsInstance<TrainScheduleDisplay>().lastOrNull()
    }
    val readingBaseline = remember(chatState.reading) {
        Triple(messages.size, chatState.answer.length, chatState.results.size)
    }
    val hasNewReadingContent = chatState.reading && (messages.size > readingBaseline.first ||
        chatState.answer.length > readingBaseline.second || chatState.results.size > readingBaseline.third)
    val inputFocusRequester = remember { FocusRequester() }
    val scope = rememberCoroutineScope()
    var scrollingToLatest by remember { mutableStateOf(false) }
    LaunchedEffect(listState) {
        snapshotFlow {
            val layout = listState.layoutInfo
            val last = layout.visibleItemsInfo.lastOrNull()
            val atBottom = if (layout.totalItemsCount > 0) {
                !listState.canScrollForward
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
            diagnostic.atBottom?.let { bottom ->
                if (!scrollingToLatest && (bottom || listState.isScrollInProgress)) onReadingChange(!bottom)
            }
        }
    }
    LaunchedEffect(chatState.answer, chatState.results.size, chatState.phase, chatState.reading, messages.size) {
        if (autoScrollToLatest && !chatState.reading) {
            val itemCount = snapshotFlow { listState.layoutInfo.totalItemsCount }.first { it > 0 }
            if (!chatState.reading) {
                scrollingToLatest = true
                try {
                    listState.scrollToItem(itemCount - 1)
                    listState.scrollBy(listState.layoutInfo.visibleItemsInfo.lastOrNull()?.size?.toFloat() ?: 0f)
                } finally { scrollingToLatest = false }
            }
        }
    }

    val pageModifier = modifier.fillMaxSize().background(NativeColors.background)
    // Insets are consumed here once; the composer ends at the live safe bottom,
    // without adding a fixed gap copied from the mockup's device illustration.
    Column((if (applySystemInsets) pageModifier.windowInsetsPadding(systemInsets).imePadding() else pageModifier)
        .captureLayoutConstraints("main-safe-content", onLayoutDiagnostics)
        .testTag("main-safe-content")) {
      Column(Modifier.fillMaxSize().railEntrance().padding(horizontal = 15.dp).testTag("main-page-content")) {
        Box(
            Modifier.fillMaxWidth().height(56.dp).captureLayoutConstraints("main-topbar", onLayoutDiagnostics).testTag("main-topbar"),
        ) {
            Box(
                Modifier.align(androidx.compose.ui.Alignment.CenterStart).requiredSize(48.dp)
                    .clickable { onSettings(null) }.semantics { contentDescription = "设置"; role = Role.Button }
                    .testTag("main-settings-control"),
                contentAlignment = androidx.compose.ui.Alignment.Center,
            ) {
                RailIcon("settings", Modifier.size(24.dp), NativeColors.ink, "main-settings-icon")
            }
            Box(
                Modifier.align(androidx.compose.ui.Alignment.CenterStart).offset(x = 48.dp).requiredSize(48.dp)
                    .clickable(onClick = onOpenHistory).semantics { contentDescription = "对话历史"; role = Role.Button }
                    .testTag("main-history-control"),
                contentAlignment = androidx.compose.ui.Alignment.Center,
            ) {
                RailIcon("history", Modifier.size(24.dp), NativeColors.ink, "main-history-icon")
            }
            Row(
                Modifier.align(androidx.compose.ui.Alignment.Center),
                horizontalArrangement = Arrangement.spacedBy(5.appDp),
                verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
            ) {
                // Source viewBox 38×40, projected from the complete 438px canvas.
                RailIcon("train-logo", Modifier.width(36.50.appDp).height(38.42.appDp), NativeColors.blue, "main-brand-icon")
                BasicText("RailFanAI", Modifier.testTag("main-brand-label"), style = TextStyle(color = NativeColors.ink, fontSize = 20.appSp,
                    fontWeight = androidx.compose.ui.text.font.FontWeight.Bold))
            }
            Box(
                Modifier.align(androidx.compose.ui.Alignment.CenterEnd).requiredSize(48.dp)
                    .clickable(onClick = onNewConversation).semantics { contentDescription = "新建对话"; role = Role.Button }
                    .testTag("main-new-control"),
                contentAlignment = androidx.compose.ui.Alignment.Center,
            ) {
                RailIcon("plus", Modifier.size(24.dp), NativeColors.ink, "main-new-icon")
            }
        }
        if (!connected && status.isNotBlank()) {
            BasicText(status, style = TextStyle(color = NativeColors.muted, fontSize = 14.appSp))
        }
        Box(Modifier.weight(1f).fillMaxWidth().captureLayoutConstraints("main-list-container", onLayoutDiagnostics).testTag("main-list-container")) {
        if (messages.isEmpty() && !chatState.busy) {
            ChatWelcome(onDraft = { draft -> onFollowup(draft); inputFocusRequester.requestFocus() })
        } else LazyColumn(
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
                                    Modifier.heightIn(min = 50.appDp)
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
                        val metadata = message.meta
                        val storedResults = DisplayResultParser.parseArray(metadata?.optJSONArray("displayResults"))
                        val presentationBody = farePresentationBody(message.content, storedResults)
                        if (presentationBody.isNotBlank()) Row(Modifier.fillMaxWidth(), verticalAlignment = androidx.compose.ui.Alignment.Top) {
                            Box(Modifier.size(50.appDp), contentAlignment = androidx.compose.ui.Alignment.TopStart) {
                                Box(Modifier.size(40.appDp).background(NativeColors.selected, CircleShape).testTag("main-assistant-avatar"),
                                    contentAlignment = androidx.compose.ui.Alignment.Center) {
                                    RailIcon("train-logo", Modifier.width(28.82.appDp).height(30.74.appDp), NativeColors.blue, "main-assistant-avatar-icon")
                                }
                            }
                            MarkdownAnswer(presentationBody, Modifier.weight(1f).testTag("main-answer-$index"), firstTextTag = "main-answer-$index-text")
                        }
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
                                Row(Modifier.fillMaxWidth().captureLayoutConstraints("result-card-host", onLayoutDiagnostics)) {
                                    Box(
                                        Modifier.width(50.appDp).heightIn(min = 32.appDp),
                                        contentAlignment = androidx.compose.ui.Alignment.TopStart,
                                    ) {
                                        if (presentationBody.isBlank() && resultIndex == 0) {
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
                                        val category = (result as? ErrorDisplay)?.value?.message?.let(::mainErrorCategory)
                                            ?: metadata?.optString("error")?.let(::mainErrorCategory)
                                        onSettings(category)
                                    },
                                    datePicker = datePicker,
                                    )
                                }

                        }
                        metadata?.optString("error")?.takeIf { it.isNotBlank() && storedResults.none { result -> result is ErrorDisplay } }?.let { errorMessage ->
                            Row(Modifier.fillMaxWidth()) {
                                Box(Modifier.width(50.appDp)) {
                                    if (message.content.isBlank() && storedResults.isEmpty()) {
                                        Box(Modifier.size(40.appDp).background(NativeColors.selected, CircleShape), contentAlignment = androidx.compose.ui.Alignment.Center) {
                                            RailIcon("train-logo", Modifier.width(28.82.appDp).height(30.74.appDp), NativeColors.blue)
                                        }
                                    }
                                }
                                DisplayResultCard(
                                    ErrorDisplay(ErrorResult("failed", errorMessage, metadata.optString("errorCategory"), null)),
                                    chatState, onAction,
                                    onRetry = { onRetryQuery(metadata.optString("query")) },
                                    onSettings = { onSettings(mainErrorCategory(errorMessage)) },
                                    modifier = Modifier.weight(1f),
                                )
                            }
                        }
                        val replyText = listOf(presentationBody.takeIf(String::isNotBlank),
                            storedResults.joinToString("\n\n") { replyClipboardText(it) }.takeIf(String::isNotBlank),
                            metadata?.optString("error")?.takeIf(String::isNotBlank)).filterNotNull().distinct().joinToString("\n\n")
                        val originalQuery = metadata?.optString("query")?.takeIf(String::isNotBlank)
                            ?: messages.take(index).lastOrNull { it.role == "user" }?.content.orEmpty()
                        FlowRow(Modifier.fillMaxWidth().padding(start = 50.appDp, top = 2.appDp)
                            .captureLayoutConstraints("result-actions", onLayoutDiagnostics).testTag("result-actions"),
                            horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                            ResultAction("复制", "copy", "result-copy") { onCopy(replyText) }
                            ResultAction("重新生成", "refresh", "result-regenerate") { onRegenerate(originalQuery) }
                            ResultAction("分享", "share", "result-share") { onShare(replyText) }
                        }
                        Box(Modifier.fillMaxWidth().padding(start = 50.appDp, bottom = 2.dp), contentAlignment = androidx.compose.ui.Alignment.CenterEnd) {
                            val tokens = replyTokenUsage(metadata)
                            BasicText(tokens?.let { "${formatTokenCount(it)} Token" } ?: "Token 未返回",
                                Modifier.testTag("reply-token-usage-$index"),
                                style = TextStyle(color = NativeColors.muted, fontSize = 16.appSp))
                        }

                    }
                }
            }
            if (chatState.busy) item(key = "live-assistant") {
                Row(Modifier.fillMaxWidth(), verticalAlignment = androidx.compose.ui.Alignment.Top) {
                    Box(Modifier.width(50.appDp)) {
                        Box(Modifier.size(40.appDp).background(NativeColors.selected, CircleShape).testTag("main-live-assistant-avatar"),
                            contentAlignment = androidx.compose.ui.Alignment.Center) {
                            RailIcon("train-logo", Modifier.width(28.82.appDp).height(30.74.appDp), NativeColors.blue)
                        }
                    }
                    Column(Modifier.weight(1f).animateContentSize()) {
                        RailReveal(chatState.answer.isBlank()) {
                            QueryLoadingCard(chatState.recognized, chatState.stage)
                        }
                        if (chatState.answer.isNotBlank()) MarkdownAnswer(chatState.answer)
                    }
                }
            }
            if (chatState.phase == ChatPhase.STOPPED && chatState.answer.isNotBlank() && !stoppedAnswerPersisted) item {
                MarkdownAnswer(chatState.answer)
            }
            itemsIndexed(if (chatState.busy || chatState.phase == ChatPhase.STOPPED) unpersistedResults else emptyList()) { resultIndex, result ->
                Row(Modifier.fillMaxWidth()) {
                    Box(Modifier.width(50.appDp)) {
                        if (!chatState.busy && resultIndex == 0 && chatState.answer.isBlank()) {
                            Box(Modifier.size(40.appDp).background(NativeColors.selected, CircleShape), contentAlignment = androidx.compose.ui.Alignment.Center) {
                                RailIcon("train-logo", Modifier.width(28.82.appDp).height(30.74.appDp), NativeColors.blue)
                            }
                        }
                    }
                DisplayResultCard(
                    result, chatState, onAction, onRetry,
                    onSettings = { onSettings(chatState.error?.let(::mainErrorCategory)) },
                    modifier = Modifier.weight(1f),
                )
                }
            }
        }
        // Float only the button above the list: no opaque full-width band or reserved row.
        RailReveal(chatState.reading, Modifier.align(androidx.compose.ui.Alignment.BottomCenter).padding(bottom = 4.dp)) {
                        Box(
                Modifier.heightIn(min = 44.dp)
                    .clickable {
                        scope.launch {
                            scrollingToLatest = true
                            try {
                                val lastIndex = listState.layoutInfo.totalItemsCount - 1
                                if (lastIndex >= 0) {
                                    listState.scrollToItem(lastIndex)
                                    listState.scrollBy(listState.layoutInfo.visibleItemsInfo.lastOrNull()?.size?.toFloat() ?: 0f)
                                }
                                onReadingChange(false)
                            } finally { scrollingToLatest = false }
                        }
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
                    BasicText(if (hasNewReadingContent) "有新内容 · 回到底部" else "回到底部", Modifier.testTag("return-to-bottom-label"), style = TextStyle(color = NativeColors.blue, fontSize = 16.appSp))
                }
            }
        }
        }
        followupSchedule?.let { schedule ->
            ScheduleFollowupActions(schedule.value, chatState.reading, inputFocusRequester, onFollowup, onLayoutDiagnostics)
        }
        Box(Modifier.fillMaxWidth().padding(top = 4.dp, end = 8.dp), contentAlignment = androidx.compose.ui.Alignment.CenterEnd) {
            BasicText("本对话累计 ${formatTokenCount(tokenTotal)} Token", Modifier.testTag("conversation-token-total"),
                style = TextStyle(color = NativeColors.muted, fontSize = 16.appSp))
        }
        val composerShape = RoundedCornerShape(30.appDp)
        Row(
            Modifier.fillMaxWidth()
                .padding(top = 8.dp).heightIn(min = 56.dp)
                .background(NativeColors.surface, composerShape)
                .border(1.dp, NativeColors.line, composerShape)
                .captureLayoutConstraints("main-fixed-input", onLayoutDiagnostics).testTag("main-fixed-input"),
            verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.appDp),
        ) {
            BasicTextField(
                value = input,
                onValueChange = onInputChange,
                maxLines = 5,
                textStyle = TextStyle(color = NativeColors.ink, fontSize = 16.appSp),
                modifier = Modifier.weight(1f)
                    .heightIn(min = 56.dp, max = 144.dp)
                    .padding(horizontal = 16.dp, vertical = 16.dp)
                    .focusRequester(inputFocusRequester)
                    .semantics { contentDescription = "消息输入框" }.testTag("main-input-field"),
                decorationBox = { inner ->
                    if (input.isEmpty()) BasicText(if (messages.isEmpty()) "输入你的问题…" else "继续追问…", Modifier.testTag("main-input-placeholder"),
                        style = TextStyle(color = NativeColors.muted, fontSize = 16.appSp))
                    inner()
                },
            )
            Box(
                Modifier.padding(end = 4.dp).size(48.dp)
                    .background(if (chatState.busy || (connected && input.isNotBlank())) NativeColors.blue else NativeColors.blue.copy(alpha = .35f), CircleShape)
                    .clickable(enabled = if (chatState.busy) true else connected && input.isNotBlank(), role = Role.Button) {
                        if (chatState.busy) onStop() else onSubmit()
                    }
                    .semantics {
                        contentDescription = if (chatState.busy) "停止生成" else "查询"
                        role = Role.Button
                    }.testTag("main-submit-control"),
                contentAlignment = androidx.compose.ui.Alignment.Center,
            ) {
                RailIcon(if (chatState.busy) "stop" else "send",
                    Modifier.size(22.dp), Color.White,
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

/** Empty conversations expose working examples rather than a blank message list. */
@Composable
private fun ChatWelcome(onDraft: (String) -> Unit) {
    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp).testTag("chat-welcome"),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = androidx.compose.ui.Alignment.CenterHorizontally) {
        RailIcon("train-logo", Modifier.size(56.dp), NativeColors.blue)
        Spacer(Modifier.height(16.dp))
        BasicText("想查哪趟列车？", style = TextStyle(color = NativeColors.ink, fontSize = 24.appSp,
            fontWeight = androidx.compose.ui.text.font.FontWeight.Bold))
        Spacer(Modifier.height(8.dp))
        BasicText("票价、时刻、车组交路，直接问我。", style = TextStyle(color = NativeColors.muted, fontSize = 14.appSp))
        Spacer(Modifier.height(24.dp))
        val examples = listOf(
            Triple("查票价", "search", "明天北京南到上海虹桥的 G1 票价"),
            Triple("查时刻", "calendar", "G1 今天的时刻表"),
            Triple("查交路", "train-logo", "CR400AF-5033 今天的交路"),
            Triple("找机位", "search", "我想找一个拍 CR400AF 的机位"),
        )
        examples.chunked(2).forEach { row ->
            Row(Modifier.fillMaxWidth().padding(bottom = 10.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                row.forEach { (label, icon, query) ->
                    Row(Modifier.weight(1f).heightIn(min = 56.dp)
                        .background(NativeColors.surface, RoundedCornerShape(16.dp))
                        .border(1.dp, NativeColors.line, RoundedCornerShape(16.dp))
                        .clickable(role = Role.Button) { onDraft(query) }.padding(12.dp)
                        .testTag("welcome-$label"),
                        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        RailIcon(icon, Modifier.size(20.dp), NativeColors.blue)
                        BasicText(label, style = TextStyle(color = NativeColors.ink, fontSize = 15.appSp))
                    }
                }
            }
        }
        BasicText("点选示例后可修改，再发送查询", Modifier.padding(top = 6.dp),
            style = TextStyle(color = NativeColors.muted, fontSize = 14.appSp))
    }
}

@Composable
private fun ResultAction(label: String, icon: String, tag: String, onClick: () -> Unit) {
    Box(Modifier.heightIn(min = 44.dp).clickable(role = Role.Button, onClick = onClick)
        .semantics { contentDescription = label; role = Role.Button }.testTag("$tag-action")) {
        Row(Modifier.padding(top = 9.appDp), horizontalArrangement = Arrangement.spacedBy(10.appDp),
            verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
            RailIcon(icon, Modifier.size(18.appDp), NativeColors.muted, "$tag-icon")
            BasicText(label, Modifier.testTag("$tag-label"), style = TextStyle(color = NativeColors.muted, fontSize = 16.appSp))
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
    val future = runCatching { LocalDate.parse(schedule.date).isAfter(LocalDate.now(java.time.ZoneId.of("Asia/Shanghai"))) }.getOrDefault(false)
    val choices = if (reading) listOf("查余票" to "search", "换个日期" to "calendar")
        else if (future) listOf("查票价" to "search", "换个日期" to "calendar")
        else listOf("查票价" to "search", "查担当车组" to "train-logo")
    Box(Modifier.fillMaxWidth().padding(bottom = 7.appDp), contentAlignment = androidx.compose.ui.Alignment.Center) {
        Row(Modifier.captureLayoutConstraints("result-suggestions", onLayoutDiagnostics).testTag("result-suggestions"), horizontalArrangement = Arrangement.spacedBy(if (reading) 12.appDp else 8.appDp)) {
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
                                .padding(horizontal = 16.dp,
                                vertical = if (reading) 6.appDp else 11.appDp),
                        horizontalArrangement = Arrangement.spacedBy(6.appDp),
                        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                    ) {
                        icon?.let { RailIcon(it, Modifier.size(16.appDp), NativeColors.blue) }
                        val choiceStyle = if (reading) {
                            TextStyle(color = NativeColors.ink, fontSize = 16.appSp, lineHeight = 22.appSp)
                        } else {
                            TextStyle(color = NativeColors.ink, fontSize = 16.appSp)
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

private fun scheduleClipboardText(value: ScheduleResult): String = buildString {
    append(value.trainCode.orEmpty()).append(' ').append(listOfNotNull(value.fromStation, value.toStation).joinToString(" → "))
    value.date?.let { append('\n').append(it) }
    value.duration?.let { append(" · ").append(it) }
    value.stops.forEach { stop -> append('\n').append(stop.station.orEmpty()).append("  ").append(stop.arriveTime ?: "--").append("  ").append(stop.startTime ?: "--") }
}

private fun suggestedQuery(choice: String, value: ScheduleResult): String = when (choice) {
    "查余票" -> if (!value.fromStation.isNullOrBlank() && !value.toStation.isNullOrBlank())
        "查一下 ${value.date.orEmpty()} ${value.trainCode.orEmpty()} ${value.fromStation} 到 ${value.toStation} 的余票"
    else "查一下 ${value.date.orEmpty()} ${value.trainCode.orEmpty()} 从出发站到到达站的余票"
    "换个日期" -> "查一下 ${value.trainCode.orEmpty()} 其他日期的时刻表"
    "查票价" -> "查一下 ${value.date.orEmpty()} ${value.trainCode.orEmpty()} ${value.fromStation.orEmpty()} 到 ${value.toStation.orEmpty()} 的票价"
    else -> "查一下 ${value.date.orEmpty()} ${value.trainCode.orEmpty()} 的担当车组"
}

private fun replyClipboardText(result: DisplayResult): String = when (result) {
    is TrainScheduleDisplay -> scheduleClipboardText(result.value)
    is TicketFareDisplay -> fareClipboardText(result.value)
    is TrainBatchDisplay -> result.value.items.joinToString("\n\n") { replyClipboardText(it) }
    is RoutingDisplay -> buildString {
        append(result.value.query.orEmpty()).append(" · ").append(result.value.focusDate.orEmpty())
        append("\n以下为交路记录时间，不是列车到发时间。")
        result.value.records.forEach { record ->
            append("\n").append(record.optString("train_code")).append("  ").append(record.optString("date")).append("  ").append(record.optString("time"))
            val units = record.optJSONArray("units")
            if (units != null) append("  ").append((0 until units.length()).joinToString(" + ") { units.optJSONObject(it)?.optString("emu_no_display").orEmpty() })
            if (record.optBoolean("coupled")) append("（重联）")
        }
    }
    is EmptyDisplay -> "${result.value.query.orEmpty()} ${result.value.date.orEmpty()}：未查到记录，不能据此判断停运。"
    is ErrorDisplay -> result.value.message.orEmpty()
    is UnsupportedDisplay -> "当前客户端暂不支持该结果格式。"
}

private fun formatTokenCount(value: Long): String = java.text.NumberFormat.getIntegerInstance(java.util.Locale.CHINA).format(value)
