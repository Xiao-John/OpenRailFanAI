package org.openrailfanai.app

import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.LocalIndication
import androidx.compose.foundation.Canvas
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.gestures.draggable
import androidx.compose.foundation.gestures.rememberDraggableState
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.ui.draw.clip
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray

private val SettingsInk get() = NativeColors.ink
private val SettingsMuted get() = NativeColors.muted
private val SettingsBorder get() = NativeColors.line
private val SettingsBlue get() = NativeColors.blue

@Composable
fun MainSettingsScreen(
    repository: MainSettingsRepository,
    client: SettingsClient,
    onBack: () -> Unit,
    onCopy: (String) -> Unit,
    onPaste: () -> String,
    keyError: Boolean,
    onKeyEdited: () -> Unit,
    onShare: (String) -> Unit,
    onExport: (String) -> Unit,
    onOpenUrl: (String) -> Unit,
    onThemeChanged: (String) -> Unit,
    onSaved: () -> Unit = onBack,
) {
    val scope = rememberCoroutineScope()
    var configs by remember { mutableStateOf(repository.entries()) }
    var activeId by remember { mutableStateOf(repository.activeId()) }
    var rememberKey by remember { mutableStateOf(repository.rememberKey()) }
    var presets by remember { mutableStateOf<List<ProviderConfig>>(emptyList()) }
    var selectedId by remember { mutableStateOf(activeId.ifBlank { configs.firstOrNull()?.id.orEmpty() }) }
    var models by remember { mutableStateOf<List<String>>(emptyList()) }
    var feedback by remember { mutableStateOf("") }
    var feedbackError by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf("") }
    var catalogLoading by remember { mutableStateOf(false) }
    var catalogError by remember { mutableStateOf("") }
    var showProviders by remember { mutableStateOf(false) }
    var showModels by remember { mutableStateOf(false) }
    var showAdvanced by remember { mutableStateOf(false) }
    var showEditor by remember { mutableStateOf(false) }
    var theme by remember { mutableStateOf(repository.theme()) }

    fun loadCatalog() {
        scope.launch {
            catalogLoading = true
            catalogError = ""
            runCatching { withContext(Dispatchers.IO) { client.providers() } }
                .onSuccess { response ->
                    val rows = response.optJSONArray("providers") ?: JSONArray()
                    presets = (0 until rows.length()).mapNotNull { index -> rows.optJSONObject(index)?.let { row ->
                        ProviderConfig(row.optString("id"), row.optString("label", row.optString("id")),
                            row.optString("base_url"), row.optString("model"), row.optString("api", "auto"))
                            .takeIf { it.id.isNotBlank() }
                    } }
                }
                .onFailure { catalogError = "提供商列表暂时无法加载，请重试或添加自定义提供商。" }
            catalogLoading = false
        }
    }
    LaunchedEffect(Unit) { loadCatalog() }
    fun selectProvider(config: ProviderConfig) {
        showEditor = true
        if (configs.none { it.id == config.id }) configs = configs + config
        selectedId = config.id
        activeId = config.id
        models = emptyList()
        feedback = ""
        showProviders = false
    }
    fun edit(transform: (ProviderConfig) -> ProviderConfig) {
        configs = updateConfig(configs, selectedId, transform)
        feedback = ""
    }
    val selected = configs.firstOrNull { it.id == selectedId }

    Column(Modifier.fillMaxWidth().railEntrance().background(NativeColors.background)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            SettingsButton("返回", Modifier.width(64.dp), enabled = busy.isEmpty(), onClick = onBack)
            BasicText("设置", Modifier.weight(1f), style = TextStyle(color = SettingsInk, fontSize = 20.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center))
            SettingsButton("保存", Modifier.width(64.dp), primary = true, enabled = busy.isEmpty()) {
                val current = configs.firstOrNull { it.id == activeId }
                if (configs.isNotEmpty() && (current == null || current.model.isBlank() || current.baseUrl.isBlank() || current.key.isBlank())) {
                    feedback = "请填写接口地址、API Key 和模型后保存。"
                    feedbackError = true
                } else {
                    runCatching { repository.save(configs, activeId, rememberKey, theme) }
                        .onSuccess { onSaved() }
                        .onFailure { feedback = "设置保存失败，请重试。"; feedbackError = true }
                }
            }
        }
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            SettingsCard("云端模型", "选择提供商，填写密钥与模型，即可开始对话。") {
                configs.forEach { provider ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 64.dp)
                        .border(1.dp, SettingsBorder, RoundedCornerShape(16.dp)).testTag("provider-card-${provider.id}")
                        .clickable(enabled = busy.isEmpty(), role = Role.Button) {
                            activeId = provider.id; selectedId = provider.id; feedback = ""; models = emptyList(); showEditor = false; showAdvanced = false; onKeyEdited()
                        }.padding(start = 16.dp, end = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        BasicText(provider.label, Modifier.weight(1f), style = TextStyle(color = SettingsInk, fontSize = 17.sp, fontWeight = FontWeight.Medium))
                        if (provider.id == activeId) ProviderActiveDot()
                        Spacer(Modifier.width(12.dp))
                        SettingsButton("编辑", Modifier.widthIn(min = 64.dp).testTag("provider-edit-${provider.id}"), enabled = busy.isEmpty()) {
                            selectedId = provider.id; showEditor = true
                        }
                    }
                }
                SettingsButton("＋ 添加模型提供商", Modifier.fillMaxWidth(), enabled = busy.isEmpty()) { showProviders = true }
                RailReveal(selected != null && showEditor, spacing = 12.dp) {
                if (selected != null) {
                    if (configs.size > 1) BasicText("已添加 ${configs.size} 个提供商；当前使用 ${configs.firstOrNull { it.id == activeId }?.label.orEmpty()}", style = TextStyle(color = SettingsMuted, fontSize = 14.sp))
                    if (selected.custom) Field("名称", selected.label, enabled = busy.isEmpty()) { value -> edit { it.copy(label = value) } }
                    Field("接口地址", selected.baseUrl, enabled = busy.isEmpty()) { value -> edit { it.copy(baseUrl = value.trim()) } }
                    Field("API Key", selected.key, password = true, enabled = busy.isEmpty(), trailingAction = "粘贴", onTrailingAction = {
                        val pasted = onPaste().trim().lineSequence().firstOrNull().orEmpty()
                        if (pasted.isNotBlank()) { onKeyEdited(); edit { it.copy(key = pasted) } }
                        feedback = if (pasted.isBlank()) "剪贴板没有可用内容，请手动输入密钥。" else "已填入密钥。"
                        feedbackError = pasted.isBlank()
                    }) { value -> onKeyEdited(); edit { it.copy(key = value.trim()) } }
                    if (keyError) BasicText("密钥无效，请检查后重试", style = TextStyle(color = Color(0xFFF0524F), fontSize = 14.sp))
                    Field("模型", selected.model, enabled = busy.isEmpty()) { value -> edit { it.copy(model = value.trim()) } }
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        SettingsAction(if (busy == "models") "正在获取…" else "获取模型", Modifier.weight(1f), enabled = busy.isEmpty()) {
                            busy = "models"; feedback = "正在获取模型列表…"; feedbackError = false
                            scope.launch {
                                runCatching { withContext(Dispatchers.IO) { client.models(selected) } }
                                    .onSuccess { response ->
                                        if (response.has("ok") && !response.optBoolean("ok")) {
                                            feedback = "获取失败，请检查接口地址和密钥后重试。"; feedbackError = true
                                        } else {
                                            val array = response.optJSONArray("chat_models") ?: response.optJSONArray("models") ?: JSONArray()
                                            models = (0 until array.length()).mapNotNull { array.optString(it).takeIf(String::isNotBlank) }
                                            feedback = if (models.isEmpty()) "没有发现模型，请手动填写模型名称。" else "已获取 ${models.size} 个模型，请选择。"
                                            showModels = models.isNotEmpty()
                                        }
                                    }.onFailure { feedback = "获取失败，请检查接口地址和密钥后重试。"; feedbackError = true }
                                busy = ""
                            }
                        }
                        SettingsButton(if (busy == "test") "正在测试…" else "测试连接", Modifier.weight(1f), primary = true, enabled = busy.isEmpty()) {
                            if (selected.key.isBlank() || selected.model.isBlank() || selected.baseUrl.isBlank()) {
                                feedback = "请先填写接口地址、API Key 和模型。"; feedbackError = true
                            } else {
                                busy = "test"; feedback = "正在连接 ${selected.label}，请稍候…"; feedbackError = false
                                scope.launch {
                                    runCatching { withContext(Dispatchers.IO) { client.test(selected) } }
                                        .onSuccess { response ->
                                            val ok = response.optBoolean("ok", false)
                                            feedback = if (ok) "连接成功，可以使用当前模型。" else "连接失败，请检查密钥、模型和接口地址。"
                                            feedbackError = !ok
                                        }.onFailure { feedback = "连接失败，请检查网络、密钥及接口地址后重试。"; feedbackError = true }
                                    busy = ""
                                }
                            }
                        }
                    }

                }
                }
                if (feedback.isNotBlank()) BasicText(feedback, Modifier.fillMaxWidth().background(if (feedbackError) Color(0xFFFFEAEA) else SettingsBlue.copy(alpha = 0.08f), RoundedCornerShape(10.dp)).padding(12.dp),
                    style = TextStyle(color = if (feedbackError) Color(0xFFB3261E) else SettingsBlue, fontSize = 14.sp))
                Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).toggleable(value = rememberKey, enabled = busy.isEmpty(), role = Role.Switch) { rememberKey = it }.padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        BasicText("记住 API Key", style = TextStyle(color = SettingsInk, fontSize = 16.sp))
                        BasicText(if (rememberKey) "密钥加密保存在当前设备。" else "本次运行仍可使用，关闭应用后需重新填写。", style = TextStyle(color = SettingsMuted, fontSize = 14.sp))
                    }
                    Spacer(Modifier.width(12.dp))
                    SettingsSwitch(rememberKey, busy.isEmpty()) { rememberKey = it }
                }
                if (selected != null) {
                    SettingsButton(if (showAdvanced) "收起高级设置" else "高级设置", Modifier.fillMaxWidth(), enabled = busy.isEmpty()) { showAdvanced = !showAdvanced }
                    RailReveal(showAdvanced, spacing = 12.dp) {
                        Field("最大输出（可选）", selected.maxTokens?.toString().orEmpty(), enabled = busy.isEmpty()) { value -> edit { it.copy(maxTokens = value.toIntOrNull()) } }
                        Field("上下文窗口（可选）", selected.contextTokens?.toString().orEmpty(), enabled = busy.isEmpty()) { value -> edit { it.copy(contextTokens = value.toIntOrNull()) } }
                        SettingsDropdown("API 方言", selected.api,
                            listOf("auto" to "自动识别", "chat_completions" to "Chat Completions", "responses" to "Responses"),
                            enabled = busy.isEmpty()) { value -> edit { it.copy(api = value) } }
                        SettingsButton("删除此提供商", Modifier.fillMaxWidth(), enabled = busy.isEmpty()) {
                            configs = configs.filterNot { it.id == selectedId }
                            activeId = configs.firstOrNull()?.id.orEmpty(); selectedId = activeId; models = emptyList(); feedback = ""
                        }
                    }
                }
            }
            UpdateSettingsSection(client, onOpenUrl)
            SettingsCard("外观", "") {
                SettingsDropdown("主题", theme,
                    listOf("auto" to "跟随系统", "light" to "浅色", "dark" to "深色")) { value ->
                    theme = value; onThemeChanged(value)
                }
            }
            SettingsCard("系统操作", "") {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    SettingsButton("分享应用", Modifier.weight(1f)) { onShare("RailFanAI · 让铁路出行更简单") }
                    SettingsButton("导出设置", Modifier.weight(1f)) { onExport(configs.joinToString("\n") { "${it.label}: ${it.baseUrl} / ${it.model}" }) }
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    SettingsButton("复制帮助链接", Modifier.weight(1f)) { onCopy("https://github.com/Xiao-John/OpenRailFanAI") }
                    SettingsButton("查看帮助", Modifier.weight(1f)) { onOpenUrl("https://github.com/Xiao-John/OpenRailFanAI") }
                }
            }
            Spacer(Modifier.height(8.dp))
        }
    }
    if (showProviders) Dialog(onDismissRequest = { showProviders = false }) {
        SettingsCard("选择提供商", "选择后填写密钥与模型；已有配置会保留。") {
            val providerRows = (presets + configs).distinctBy { it.id }
            Column(Modifier.heightIn(max = 360.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                providerRows.forEach { preset ->
                    val isActive = preset.id == activeId
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp)
                        .background(if (isActive) SettingsBlue.copy(alpha = 0.08f) else Color.Transparent, RoundedCornerShape(10.dp))
                        .clickable(role = Role.Button) { selectProvider(configs.firstOrNull { it.id == preset.id } ?: preset) }
                        .padding(horizontal = 12.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                        BasicText(preset.label, Modifier.weight(1f), style = TextStyle(color = SettingsInk, fontSize = 15.sp, fontWeight = if (isActive) FontWeight.SemiBold else FontWeight.Normal))
                        if (isActive) ProviderActiveDot()
                    }
                }
                if (catalogLoading) BasicText("正在加载提供商…", Modifier.padding(8.dp), style = TextStyle(color = SettingsMuted, fontSize = 14.sp))
                if (catalogError.isNotBlank()) {
                    BasicText(catalogError, Modifier.padding(8.dp), style = TextStyle(color = SettingsMuted, fontSize = 14.sp))
                    SettingsAction("重新加载", enabled = !catalogLoading) { loadCatalog() }
                }
            }
            if (providerRows.size > 4) BasicText("列表可上下滑动查看更多", Modifier.fillMaxWidth().padding(top = 2.dp), style = TextStyle(color = SettingsMuted, fontSize = 14.sp, textAlign = TextAlign.Center))
            SettingsButton("＋ 添加自定义提供商", Modifier.fillMaxWidth(), primary = true) {
                selectProvider(ProviderConfig("custom-${System.currentTimeMillis().toString(36)}", "自定义提供商", "", "", custom = true))
            }
            SettingsButton("取消", Modifier.fillMaxWidth()) { showProviders = false }
        }
    }
    if (showModels) Dialog(onDismissRequest = { showModels = false }) {
        SettingsCard("选择模型", "") {
            Column(Modifier.heightIn(max = 360.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                models.forEach { model -> SettingsButton(model, Modifier.fillMaxWidth()) { edit { it.copy(model = model) }; showModels = false } }
            }
            SettingsButton("取消", Modifier.fillMaxWidth()) { showModels = false }
        }
    }
}

@Composable
internal fun SettingsCard(title: String, subtitle: String, content: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().railEntrance().background(NativeColors.surface, RoundedCornerShape(20.dp)).border(1.dp, SettingsBorder, RoundedCornerShape(20.dp)).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        BasicText(title, style = TextStyle(color = SettingsInk, fontSize = 18.sp, fontWeight = FontWeight.Bold))
        if (subtitle.isNotBlank()) BasicText(subtitle, style = TextStyle(color = SettingsMuted, fontSize = 14.sp, lineHeight = 21.sp))
        content()
    }
}

@Composable
private fun Field(label: String, value: String, password: Boolean = false, enabled: Boolean = true, trailingAction: String? = null, onTrailingAction: (() -> Unit)? = null, onChange: (String) -> Unit) {
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            BasicText(label, Modifier.weight(1f), style = TextStyle(color = SettingsInk, fontSize = 14.sp, fontWeight = FontWeight.Medium))
            if (trailingAction != null && onTrailingAction != null) SettingsAction(trailingAction, enabled = enabled, onClick = onTrailingAction)
        }
        BasicTextField(value, onChange, Modifier.fillMaxWidth().heightIn(min = 52.dp).background(NativeColors.background, RoundedCornerShape(12.dp)).border(1.dp, SettingsBorder, RoundedCornerShape(12.dp)).padding(horizontal = 14.dp, vertical = 14.dp)
            .semantics { contentDescription = if (password) "${label}输入框" else "$label 输入框" },
            enabled = enabled, singleLine = true, textStyle = TextStyle(color = SettingsInk, fontSize = 15.sp),
            visualTransformation = if (password) PasswordVisualTransformation() else androidx.compose.ui.text.input.VisualTransformation.None,
            decorationBox = { inner -> Box { if (value.isEmpty()) BasicText(if (password) "填写 API Key" else "填写$label", style = TextStyle(color = SettingsMuted, fontSize = 15.sp)); inner() } },
        )
    }
}

@Composable
private fun SettingsAction(text: String, modifier: Modifier = Modifier, enabled: Boolean = true, onClick: () -> Unit) {
    Box(modifier.heightIn(min = 44.dp).clickable(enabled = enabled, role = Role.Button, onClick = onClick)
        .padding(horizontal = 12.dp, vertical = 8.dp), contentAlignment = Alignment.Center) {
        BasicText(text, style = TextStyle(color = if (enabled) SettingsBlue else SettingsMuted, fontSize = 16.sp, fontWeight = FontWeight.Medium, textAlign = TextAlign.Center))
    }
}

@Composable
internal fun SettingsButton(text: String, modifier: Modifier = Modifier, primary: Boolean = false, enabled: Boolean = true, onClick: () -> Unit) {
    val interactions = remember { MutableInteractionSource() }
    val pressed by interactions.collectIsPressedAsState()
    val targetBackground = when {
        primary -> SettingsBlue.copy(alpha = if (enabled) 1f else .4f)
        pressed && enabled -> SettingsBlue.copy(alpha = .08f)
        else -> NativeColors.surface
    }
    val background by animateColorAsState(targetBackground, tween(100), label = "button-feedback")
    Box(modifier.heightIn(min = 48.dp).background(background, RoundedCornerShape(12.dp))
        .border(1.dp, if (primary) Color.Transparent else SettingsBorder, RoundedCornerShape(12.dp))
        .clickable(interactionSource = interactions, indication = LocalIndication.current, enabled = enabled, role = Role.Button, onClick = onClick).padding(horizontal = 12.dp, vertical = 12.dp), contentAlignment = Alignment.Center) {
        val foreground = if (primary) Color.White else if (enabled) SettingsInk else SettingsMuted
        if (text.startsWith("＋ ")) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                RailIcon("plus", Modifier.size(18.dp), foreground)
                BasicText(text.removePrefix("＋ "), style = TextStyle(color = foreground, fontSize = 16.sp, fontWeight = FontWeight.Medium))
            }
        } else BasicText(text, style = TextStyle(color = foreground, fontSize = 16.sp, fontWeight = FontWeight.Medium, textAlign = TextAlign.Center))
    }
}

private fun updateConfig(configs: List<ProviderConfig>, id: String, transform: (ProviderConfig) -> ProviderConfig): List<ProviderConfig> =
    configs.map { if (it.id == id) transform(it) else it }

@Composable
private fun ProviderActiveDot() {
    Canvas(Modifier.size(10.dp).semantics { contentDescription = "当前提供商" }) {
        drawCircle(Color(0xFF22C55E))
    }
}

/** 父行提供开关语义和点击；滑块接受拖动，禁用状态同时阻止两种操作。 */
@Composable
private fun SettingsSwitch(checked: Boolean, enabled: Boolean, onChange: (Boolean) -> Unit) {
    val progress by animateFloatAsState(if (checked) 1f else 0f, tween(180, easing = FastOutSlowInEasing), label = "remember-key-switch")
    val trackColor by animateColorAsState(if (checked) SettingsBlue else SettingsBorder, tween(180), label = "switch-track")
    var drag by remember { mutableFloatStateOf(0f) }
    val dragThreshold = with(LocalDensity.current) { 6.dp.toPx() }
    Canvas(Modifier.size(52.dp, 44.dp).testTag("remember-key-switch")
        .draggable(rememberDraggableState { drag += it }, Orientation.Horizontal, enabled = enabled,
            onDragStarted = { drag = 0f }, onDragStopped = {
                if (drag > dragThreshold) onChange(true) else if (drag < -dragThreshold) onChange(false)
            })) {
        val trackHeight = 30.dp.toPx()
        val top = (size.height-trackHeight)/2
        drawRoundRect(trackColor.copy(alpha = if (enabled) 1f else .4f),
            topLeft = Offset(0f,top), size = androidx.compose.ui.geometry.Size(size.width,trackHeight), cornerRadius = CornerRadius(trackHeight/2))
        val radius = 11.dp.toPx()
        drawCircle(Color.White.copy(alpha = if (enabled) 1f else .7f),radius,
            Offset(15.dp.toPx() + progress * (size.width - 30.dp.toPx()),size.height/2))
    }
}

@Composable
private fun SettingsDropdown(label: String, value: String, options: List<Pair<String, String>>,
    enabled: Boolean = true, onSelect: (String) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    val dropdownShape = RoundedCornerShape(12.dp)
    Column(Modifier.fillMaxWidth().clip(dropdownShape).border(1.dp, SettingsBorder, dropdownShape)) {
        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable(enabled = enabled, role = Role.Button) { expanded = !expanded }
            .padding(horizontal = 14.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            BasicText("$label：${options.firstOrNull { it.first == value }?.second ?: value}", Modifier.weight(1f),
                style = TextStyle(color = SettingsInk, fontSize = 16.sp))
            RailIcon(if (expanded) "chevron-up" else "chevron-down", Modifier.size(20.dp), SettingsMuted)
        }
        RailReveal(expanded) { options.forEach { (id, text) ->
            Row(Modifier.fillMaxWidth().heightIn(min = 48.dp)
                .background(if (id == value) SettingsBlue.copy(alpha = .06f) else Color.Transparent)
                .clickable(enabled = enabled, role = Role.Button) { onSelect(id); expanded = false }
                .padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                BasicText(text, Modifier.weight(1f), style = TextStyle(color = SettingsInk, fontSize = 16.sp))
                if (id == value) ProviderActiveDot()
            }
        }
        }
    }
}
