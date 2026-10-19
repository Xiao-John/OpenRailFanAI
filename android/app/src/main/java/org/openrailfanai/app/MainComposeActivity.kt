package org.openrailfanai.app

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.core.tween
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.ClipData
import android.content.ClipboardManager
import android.widget.Toast
import android.os.Bundle
import android.os.IBinder
import androidx.activity.ComponentActivity
import androidx.activity.enableEdgeToEdge
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.border
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.runtime.key
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.flow.collect
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/** Main's native application host; the stable launcher dispatches here for Main builds. */
class MainComposeActivity : ComponentActivity(), BackendService.Listener {
    private var status by mutableStateOf("等待设备内服务…")
    private var input by mutableStateOf("")
    private val stateMachine = ChatStateMachine()
    private var chatState by mutableStateOf(stateMachine.state)
    private var connectedPort by mutableStateOf(0)
    private var pageNavigation by mutableStateOf(MainPageNavigation.root())
    private var settingsKeyError by mutableStateOf(false)
    private lateinit var conversationStore: ConversationStore
    private var conversationId by mutableStateOf("")
    private var conversationRevision by mutableStateOf(0)
    private lateinit var settingsRepository: MainSettingsRepository
    private var pendingExport: String? = null
    private var pendingAgentExport: ByteArray? = null
    private var previousAgentTheme: String? = null
    private var pendingImportConversationId: String? = null
    private val agentFiles = mutableStateMapOf<String, org.json.JSONArray>()
    private val busy: Boolean get() = chatState.busy
    private val io = Executors.newSingleThreadExecutor()
    private var backendService: BackendService? = null
    private var bound = false
    private var repository: ChatRepository? = null
    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            backendService = (binder as BackendService.LocalBinder).service()
            backendService?.addListener(this@MainComposeActivity)
        }
        override fun onServiceDisconnected(name: ComponentName?) { backendService = null }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Compose owns safe-area and IME consumption on every supported Android version.
        enableEdgeToEdge()
        pageNavigation = MainPageNavigation.restore(savedInstanceState?.getStringArrayList(STATE_PAGE_STACK))
        if (intent.getBooleanExtra("open_update_settings", false)) {
            pageNavigation = pageNavigation.navigateTo(MainPage.SETTINGS)
            UpdateTasks.get(this).focus(intent.getStringExtra("update_focus").orEmpty())
            intent.removeExtra("open_update_settings")
        }
        settingsRepository = MainSettingsRepository(this)
        NativeColors.preference = settingsRepository.theme()
        NativeColors.systemDark = (resources.configuration.uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK) == android.content.res.Configuration.UI_MODE_NIGHT_YES
        conversationStore = ConversationStore(this)
        if (conversationStore.current() == null) conversationStore.create()
        conversationId = conversationStore.currentId()
        input = conversationStore.draft(conversationId)
        setContent {
            CompositionLocalProvider(LocalUriHandler provides object : UriHandler {
                override fun openUri(uri: String) {
                    runCatching { AppLinkBrowser.open(this@MainComposeActivity, uri) }
                        .onFailure { Toast.makeText(this@MainComposeActivity, "链接未能打开，请检查浏览器", Toast.LENGTH_SHORT).show() }
                }
            }) {
            BackHandler(enabled = pageNavigation.canGoBack) {
                navigateBackOrDelegateToActivity()
            }
            when (pageNavigation.current) {
                MainPage.SETTINGS -> {
                Column(Modifier.fillMaxSize().background(NativeColors.background).windowInsetsPadding(WindowInsets.safeDrawing).imePadding()) {
                    val port = connectedPort
                    if (port > 0) MainSettingsScreen(
                        repository = settingsRepository,
                        client = SettingsClient("http://127.0.0.1:$port"),
                        onBack = { navigateBackOrDelegateToActivity() },
                        onSaved = { pageNavigation = MainPageNavigation.root() },
                        onCopy = { text ->
                            (getSystemService(CLIPBOARD_SERVICE) as? ClipboardManager)?.setPrimaryClip(ClipData.newPlainText("RailFanAI", text))
                        },
                        onPaste = {
                            val clipboard = getSystemService(CLIPBOARD_SERVICE) as? ClipboardManager
                            if (clipboard?.hasPrimaryClip() == true) clipboard.primaryClip?.getItemAt(0)?.coerceToText(this@MainComposeActivity)?.toString().orEmpty() else ""
                        },
                        keyError = settingsKeyError,
                        onKeyEdited = { settingsKeyError = false },
                        onShare = { text ->
                            startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text), "分享"))
                        },
                        onExport = { text -> exportSettings(text) },
                        onOpenUrl = { url -> startActivity(Intent(Intent.ACTION_VIEW, android.net.Uri.parse(url))) },
                        onOpenSetupUrl = { url -> ProviderGuideBrowser.open(this@MainComposeActivity, url) },
                        onThemeChanged = { NativeColors.preference = it },
                    ) else BasicText("正在连接设备内服务…")
                }
                }
                MainPage.CHAT, MainPage.HISTORY -> Box(Modifier.fillMaxSize()) {
                key(conversationId) { MainChatScreen(
                status = status,
                connected = connectedPort > 0,
                photoBackendUrl = if (connectedPort > 0) "http://127.0.0.1:$connectedPort" else null,
                chatState = chatState,
                messages = conversationStore.all().firstOrNull { it.id == conversationId }?.messages.orEmpty(),
                conversationTokens = conversationStore.all().firstOrNull { it.id == conversationId }?.totalTokens ?: 0L,
                input = input,
                onInputChange = { input = it },
                pendingAttachment = agentFiles[conversationId]?.optJSONObject(0)?.let { file ->
                    val encoded = file.optString("content_base64")
                    val bytes = encoded.length.toLong() * 3 / 4 - encoded.takeLast(2).count { it == '=' }
                    PendingAttachment(file.optString("name"), bytes, AppAgent.isImage(file.optString("name"), file.optString("mime")))
                },
                onImportAttachment = { imagesOnly -> openAgentImport(imagesOnly) },
                onRemoveAttachment = { if (!busy) { agentFiles.remove(conversationId); status = "附件已移除" } },
                onOpenHistory = { pageNavigation = pageNavigation.navigateTo(MainPage.HISTORY) },
                onNewConversation = { openNewConversation() },
                onBack = { navigateBackOrDelegateToActivity() },
                onSubmit = { submitQuery() },
                onStop = { stopQuery() },
                onReadingChange = { reading ->
                    if (stateMachine.state.reading != reading) {
                        stateMachine.setReading(reading)
                        chatState = stateMachine.state
                    }
                },
                onAction = { action, prompt, preserve -> submitQuery(prompt, action, preserve) },
                onRetry = { submitQuery(chatState.query) },
                onSettings = { category ->
                    settingsKeyError = category == "auth"
                    pageNavigation = pageNavigation.navigateTo(MainPage.SETTINGS)
                },
                onCopy = { text ->
                    (getSystemService(CLIPBOARD_SERVICE) as? ClipboardManager)?.setPrimaryClip(ClipData.newPlainText("RailFanAI", text))
                },
                onShare = { text ->
                    val share = Intent(Intent.ACTION_SEND).apply {
                        type = "text/plain"
                        putExtra(Intent.EXTRA_TEXT, text)
                    }
                    startActivity(Intent.createChooser(share, "分享回复"))
                },
                onRegenerate = { query -> submitQuery(query) },
                onRetryQuery = { query -> submitQuery(query) },
                onFollowup = { draft -> input = draft },
                ) }
                AnimatedVisibility(pageNavigation.current == MainPage.HISTORY, enter = fadeIn(tween(160)), exit = fadeOut(tween(140))) {
                    HistoryScreen(
                        store = conversationStore, currentId = conversationId, revision = conversationRevision,
                        onBack = { if (pageNavigation.current == MainPage.HISTORY) navigateBackOrDelegateToActivity() },
                        onCreate = createFromHistory@{
                            if (pageNavigation.current != MainPage.HISTORY) return@createFromHistory
                            openNewConversation()
                        },
                        onSelect = selectFromHistory@{ id ->
                            if (pageNavigation.current != MainPage.HISTORY) return@selectFromHistory
                            if (busy) stopQuery()
                            saveCurrentDraft()
                            conversationStore.select(id)
                            conversationId = id
                            conversationRevision++
                            input = conversationStore.draft(id)
                            stateMachine.reset()
                            chatState = stateMachine.state
                            pageNavigation = MainPageNavigation.root()
                        },
                        onRename = { id, title -> conversationStore.rename(id, title); conversationRevision++ },
                        onDelete = { id ->
                            saveCurrentDraft()
                            conversationStore.delete(id)
                            conversationId = conversationStore.currentId()
                            input = conversationStore.draft(conversationId)
                            conversationRevision++
                        },
                        onSettings = { pageNavigation = pageNavigation.navigateTo(MainPage.SETTINGS) },
                        onHelp = { startActivity(Intent(Intent.ACTION_VIEW, android.net.Uri.parse(HELP_URL))) },
                    )
                }
                }
            }
        }
        }
        val intent = Intent(this, BackendService::class.java)
        startService(intent)
        bound = bindService(intent, connection, Context.BIND_AUTO_CREATE)
    }

    private fun navigateBackOrDelegateToActivity() {
        val previous = pageNavigation.pop()
        if (previous != null) {
            pageNavigation = previous
        } else {
            onBackPressedDispatcher.onBackPressed()
        }
    }

    private fun saveCurrentDraft() {
        if (::conversationStore.isInitialized) conversationStore.saveDraft(conversationId, input)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (intent.getBooleanExtra("open_update_settings", false)) {
            pageNavigation = pageNavigation.navigateTo(MainPage.SETTINGS)
            UpdateTasks.get(this).focus(intent.getStringExtra("update_focus").orEmpty())
            intent.removeExtra("open_update_settings")
        }
    }

    override fun onPause() {
        saveCurrentDraft()
        super.onPause()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        saveCurrentDraft()
        outState.putStringArrayList(STATE_PAGE_STACK, ArrayList(pageNavigation.savedPages()))
        super.onSaveInstanceState(outState)
    }

    override fun onBackendStage(stage: String) { status = "正在启动：$stage" }

    override fun onBackendReady(port: Int, selfCheck: String) {
        status = "正在连接设备内服务…"
        io.execute {
            val probe = runCatching {
                val connection = URL("http://127.0.0.1:$port/health").openConnection() as HttpURLConnection
                connection.connectTimeout = 3000
                connection.readTimeout = 3000
                try {
                    val code = connection.responseCode
                    check(code == 200) { "HTTP $code" }
                } finally { connection.disconnect() }
            }
            runOnUiThread {
                if (probe.isSuccess) {
                    repository = ChatRepository("http://127.0.0.1:$port")
                    connectedPort = port
                    status = "后端已连接（127.0.0.1:$port）"
                } else {
                    connectedPort = 0
                    status = "后端连接失败：${probe.exceptionOrNull()?.message ?: "网络错误"}"
                }
            }
        }
    }

    override fun onBackendFailed(detail: String) { status = "后端启动失败：$detail" }

    private fun openNewConversation() {
        val current = conversationStore.all().firstOrNull { it.id == conversationId }
        if (!busy && current?.messages?.isEmpty() == true) {
            Toast.makeText(this, "已在新对话中", Toast.LENGTH_SHORT).show()
            pageNavigation = MainPageNavigation.root()
            return
        }
        if (busy) stopQuery()
        saveCurrentDraft()
        conversationId = conversationStore.pendingOrCreate().id
        conversationRevision++
        input = conversationStore.draft(conversationId)
        stateMachine.reset()
        chatState = stateMachine.state
        pageNavigation = MainPageNavigation.root()
    }

    private fun submitQuery(retryQuery: String? = null, displayAction: org.json.JSONObject? = null, preserveResults: Boolean = false) {
        val query = (retryQuery ?: input).trim()
        if (query.isEmpty() || busy) return
        if (retryQuery != null && displayAction == null && AppAgent.requiresReimport(conversationStore.all().firstOrNull { it.id == conversationId }?.messages.orEmpty(), query)) {
            status = "该提问使用了附件，请重新导入后重新发送；不会改用当前附件。"
            Toast.makeText(this, "请重新导入原附件，再发送这条问题。", Toast.LENGTH_LONG).show()
            return
        }
        if (displayAction == null) {
            val theme = AppAgent.themeCommand(query)
            if (theme != null) {
                val target = if (theme == "undo") previousAgentTheme else theme
                val answer = if (target == null) "没有可撤销的主题切换。" else {
                    val old = settingsRepository.theme()
                    settingsRepository.setTheme(target)
                    NativeColors.preference = target
                    previousAgentTheme = if (theme == "undo") null else old
                    if (theme == "undo") "已撤销主题切换。" else "已切换为${when (target) { "dark" -> "深色"; "light" -> "浅色"; else -> "跟随系统" }}模式。"
                }
                conversationStore.addUser(conversationId, query)
                conversationStore.addAssistant(conversationId, answer, org.json.JSONObject().put("intent", "app_action"))
                input = ""; saveCurrentDraft(); stateMachine.reset(); chatState = stateMachine.state; conversationRevision++
                return
            }
            if (AppAgent.importCommand(query)) {
                openAgentImport(query.contains("图片"))
                return
            }
            if (query == "移除附件") { agentFiles.remove(conversationId); input = ""; saveCurrentDraft(); status = "附件已移除"; return }
        }
        val client = repository ?: return
        if (query.isEmpty() || busy) return
        if (retryQuery == null) {
            input = ""
            saveCurrentDraft()
        }
        val requestConversationId = conversationId
        val requestAttachments = if (displayAction == null) agentFiles[requestConversationId] else null
        conversationStore.addUser(requestConversationId, query, if (requestAttachments != null) org.json.JSONObject().put("attachment_input", true) else null)
        conversationRevision++
        val fileOperation = displayAction == null && (AppAgent.fileCommand(query) || agentFiles.containsKey(requestConversationId))
        val priorMessages = conversationStore.all().firstOrNull { it.id == requestConversationId }?.messages.orEmpty()
            .filter { it.role == "user" || (it.role == "assistant" && it.meta?.has("error") != true && it.meta?.optBoolean("stopped") != true) }
            .takeLast(20).map {
                val content = if (fileOperation && it.role == "assistant") {
                    listOfNotNull(it.content,
                        it.meta?.optJSONArray("displayResults")?.takeIf { data -> data.length() > 0 }?.let { data -> "[已展示的结构化结果]\n$data" },
                        it.meta?.optJSONArray("sources")?.takeIf { data -> data.length() > 0 }?.let { data -> "[来源]\n$data" },
                    ).joinToString("\n\n")
                } else it.content
                ChatMessage(it.role, content)
            }
        status = "正在查询…"
        val requestId = stateMachine.begin(query, preserveResults = preserveResults)
        chatState = stateMachine.state
        val request = ChatStreamRequest(
            query,
            history = priorMessages.dropLast(1),
            sessionId = requestConversationId,
            displayAction = displayAction,
            llmSpec = settingsRepository.requestLlmSpec(),
            agentFiles = requestAttachments,
        )
        io.execute {
            val response = runCatching {
                client.stream(request) { event ->
                    runOnUiThread {
                        val type = event.optString("type")
                        if (type == "agent_file" && chatState.requestId == requestId && chatState.busy) {
                            event.optJSONObject("file")?.let { file ->
                                runCatching {
                                    val name = file.getString("name")
                                    require(Regex("railfan-draft\\.(csv|md|json|ics)").matches(name))
                                    val bytes = android.util.Base64.decode(file.getString("content_base64"), android.util.Base64.DEFAULT)
                                    require(bytes.size <= 1024 * 1024)
                                    pendingAgentExport = bytes
                                    startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                                        addCategory(Intent.CATEGORY_OPENABLE)
                                        this.type = file.getString("mime")
                                        putExtra(Intent.EXTRA_TITLE, name)
                                    }, REQUEST_AGENT_EXPORT)
                                }.onFailure { status = "无法保存文件：${it.javaClass.simpleName}" }
                            }
                        }
                        if (stateMachine.onEvent(
                                requestId, type, event.optString("delta"),
                                event.optString("text"), event.optString("message"),
                                event.optString("stage"), event.optString("recognized"),
                            )) {
                            chatState = stateMachine.state
                            if (type == "stage") status = "正在查询：${event.optString("stage")}"
                        }
                    }
                }
            }
            runOnUiThread {
                response.onSuccess { outcome ->
                    if (stateMachine.complete(requestId, outcome)) {
                        if (outcome.intent == "file_tool") agentFiles.remove(requestConversationId)
                        chatState = stateMachine.state
                        val meta = org.json.JSONObject()
                            .put("query", query)
                            .put("intent", outcome.intent)
                            .put("sources", org.json.JSONArray(outcome.sources))
                            .put("displayResults", org.json.JSONArray(outcome.displayResultsJson))
                            .put("processLogs", org.json.JSONArray(outcome.processLogs))
                            .put("usage", outcome.usage)
                            .put("latencyMs", outcome.latencyMs)
                        outcome.photoSpotJson?.let { meta.put("photoSpots", org.json.JSONObject(it)) }
                        if (outcome.error != null) meta.put("error", outcome.error).put("errorCategory", mainErrorCategory(outcome.error))
                        conversationStore.addAssistant(requestConversationId, outcome.answer, meta)
                        conversationRevision++
                        status = outcome.error ?: "查询完成"
                    }
                }.onFailure { error ->
                    val message = when (error) {
                        is ChatHttpException -> "请求失败（HTTP ${error.statusCode}）"
                        else -> "连接失败：${error.message ?: error.javaClass.simpleName}"
                    }
                    if (stateMachine.fail(requestId, message)) {
                        chatState = stateMachine.state
                        conversationStore.addAssistant(requestConversationId, "", org.json.JSONObject().put("error", message).put("errorCategory", mainErrorCategory(message)))
                        conversationRevision++
                        status = message
                    }
                }
            }
        }
    }

    private fun stopQuery() {
        val id = chatState.requestId ?: return
        if (stateMachine.stop(id)) {
            chatState = stateMachine.state
            conversationStore.addAssistant(conversationId, chatState.answer, org.json.JSONObject().put("stopped", true))
            conversationRevision++
            status = "已停止生成"
            repository?.cancel()
        }
    }

    private fun openAgentImport(imagesOnly: Boolean) {
        if (busy || pendingImportConversationId != null) return
        pendingImportConversationId = conversationId
        runCatching {
            startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                addCategory(Intent.CATEGORY_OPENABLE)
                type = if (imagesOnly) "image/*" else "*/*"
                putExtra(Intent.EXTRA_MIME_TYPES, if (imagesOnly) arrayOf("image/png", "image/jpeg", "image/webp", "image/gif")
                    else arrayOf("text/*", "application/json", "application/x-tex", "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/rtf"))
            }, REQUEST_AGENT_IMPORT)
        }.onFailure { pendingImportConversationId = null; status = "无法打开文件选择器" }
    }

    private fun exportSettings(text: String) {
        pendingExport = text
        runCatching {
            startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                addCategory(Intent.CATEGORY_OPENABLE)
                type = "text/plain"
                putExtra(Intent.EXTRA_TITLE, "railfanai-settings.txt")
            }, REQUEST_EXPORT)
        }.onFailure { status = "无法打开系统文件保存界面：${it.javaClass.simpleName}" }
    }

    @Deprecated("Deprecated by Android, retained for the SAF result callback")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQUEST_AGENT_IMPORT) {
            val targetConversation = pendingImportConversationId
            pendingImportConversationId = null
            val uri = data?.data
            if (resultCode != RESULT_OK || uri == null || targetConversation == null) return
            io.execute {
                val result = runCatching {
                    val name = contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
                        if (it.moveToFirst()) it.getString(0) else null
                    } ?: "import.txt"
                    require(!name.contains('/') && !name.contains('\\')) { "文件名不能包含路径" }
                    val mime = contentResolver.getType(uri).orEmpty()
                    require(name.substringAfterLast('.', "").lowercase() in AppAgent.documentExtensions + AppAgent.imageExtensions) { "不支持的文件类型" }
                    val bytes = contentResolver.openInputStream(uri)?.use { stream ->
                        val output = java.io.ByteArrayOutputStream()
                        val buffer = ByteArray(8192)
                        while (true) {
                            val n = stream.read(buffer); if (n < 0) break
                            require(output.size() + n <= AppAgent.MAX_INPUT_BYTES) { "文件超过 8 MiB" }
                            output.write(buffer, 0, n)
                        }
                        output.toByteArray()
                    } ?: error("无法读取文件")
                    org.json.JSONObject().put("name", name).put("mime", mime).put("content_base64", android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP))
                }
                runOnUiThread {
                    result.onSuccess {
                        if (conversationStore.all().none { conversation -> conversation.id == targetConversation }) return@onSuccess
                        agentFiles[targetConversation] = org.json.JSONArray().put(it)
                        if (conversationId == targetConversation) {
                            if (input.isBlank() || AppAgent.importCommand(input.trim())) {
                                input = if (AppAgent.isImage(it.optString("name"), it.optString("mime"))) "请阅读已导入图片，提取并整理其中的信息" else "请阅读并整理已导入文件"
                            }
                            saveCurrentDraft()
                        }
                        status = "已导入 ${it.optString("name")}；发送问题后处理，输入“移除附件”可清除。"
                        Toast.makeText(this, "附件已就绪，发送问题后处理。", Toast.LENGTH_SHORT).show()
                    }.onFailure {
                        status = "导入失败：请使用 8 MiB 内的 TXT/MD/TEX/CSV/JSON/DOC/DOCX/RTF 或 PNG/JPEG/WebP/GIF。"
                        Toast.makeText(this, status, Toast.LENGTH_LONG).show()
                    }
                }
            }
            return
        }
        if (requestCode == REQUEST_AGENT_EXPORT) {
            val bytes = pendingAgentExport; pendingAgentExport = null
            val uri = data?.data
            if (resultCode == RESULT_OK && bytes != null && uri != null) {
                runCatching {
                    val stream = contentResolver.openOutputStream(uri) ?: error("无法写入文件")
                    stream.use { it.write(bytes) }
                }.onSuccess { status = "文件已保存" }.onFailure { status = "文件保存失败" }
            } else status = "已取消保存，原文件未改动"
            return
        }
        if (requestCode != REQUEST_EXPORT) return
        val contents = pendingExport
        pendingExport = null
        val uri = data?.data ?: return
        if (resultCode != RESULT_OK || contents == null) return
        runCatching { contentResolver.openOutputStream(uri)?.use { it.write(contents.toByteArray(Charsets.UTF_8)) } }
            .onFailure { status = "导出失败：${it.javaClass.simpleName}" }
    }

    override fun onDestroy() {
        if (bound) {
            backendService?.removeListener(this)
            unbindService(connection)
        }
        repository?.cancel()
        io.shutdownNow()
        super.onDestroy()
    }

    companion object {
        private const val REQUEST_EXPORT = 8741
        private const val REQUEST_AGENT_IMPORT = 8742
        private const val REQUEST_AGENT_EXPORT = 8743
        private const val STATE_PAGE_STACK = "main_page_stack"
        private const val HELP_URL = "https://github.com/OpenRailFanAI/OpenRailFanAI"
    }
}
