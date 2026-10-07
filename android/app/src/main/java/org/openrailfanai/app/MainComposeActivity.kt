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
import androidx.compose.runtime.key
import androidx.compose.runtime.getValue
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
                        onThemeChanged = { NativeColors.preference = it },
                    ) else BasicText("正在连接设备内服务…")
                }
                }
                MainPage.CHAT, MainPage.HISTORY -> Box(Modifier.fillMaxSize()) {
                key(conversationId) { MainChatScreen(
                status = status,
                connected = connectedPort > 0,
                chatState = chatState,
                messages = conversationStore.all().firstOrNull { it.id == conversationId }?.messages.orEmpty(),
                conversationTokens = conversationStore.all().firstOrNull { it.id == conversationId }?.totalTokens ?: 0L,
                input = input,
                onInputChange = { input = it },
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
        val client = repository ?: return
        if (query.isEmpty() || busy) return
        if (retryQuery == null) {
            input = ""
            saveCurrentDraft()
        }
        val requestConversationId = conversationId
        conversationStore.addUser(requestConversationId, query)
        conversationRevision++
        val priorMessages = conversationStore.all().firstOrNull { it.id == requestConversationId }?.messages.orEmpty()
            .filter { it.role == "user" || (it.role == "assistant" && it.meta?.has("error") != true && it.meta?.optBoolean("stopped") != true) }
            .takeLast(20).map { ChatMessage(it.role, it.content) }
        status = "正在查询…"
        val requestId = stateMachine.begin(query, preserveResults = preserveResults)
        chatState = stateMachine.state
        val request = ChatStreamRequest(
            query,
            history = priorMessages.dropLast(1),
            sessionId = requestConversationId,
            displayAction = displayAction,
            llmSpec = settingsRepository.requestLlmSpec(),
        )
        io.execute {
            val response = runCatching {
                client.stream(request) { event ->
                    runOnUiThread {
                        val type = event.optString("type")
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
                        chatState = stateMachine.state
                        val meta = org.json.JSONObject()
                            .put("query", query)
                            .put("intent", outcome.intent)
                            .put("sources", org.json.JSONArray(outcome.sources))
                            .put("displayResults", org.json.JSONArray(outcome.displayResultsJson))
                            .put("processLogs", org.json.JSONArray(outcome.processLogs))
                            .put("usage", outcome.usage)
                            .put("latencyMs", outcome.latencyMs)
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
        private const val STATE_PAGE_STACK = "main_page_stack"
        private const val HELP_URL = "https://github.com/OpenRailFanAI/OpenRailFanAI"
    }
}
