package org.openrailfanai.app

import androidx.compose.foundation.border
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
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
) {
    val scope = rememberCoroutineScope()
    var configs by remember { mutableStateOf(repository.entries()) }
    var activeId by remember { mutableStateOf(repository.activeId()) }
    var rememberKey by remember { mutableStateOf(repository.rememberKey()) }
    var presets by remember { mutableStateOf<List<ProviderConfig>>(emptyList()) }
    var selectedId by remember { mutableStateOf(activeId) }
    var models by remember { mutableStateOf<List<String>>(emptyList()) }
    var status by remember { mutableStateOf("") }
    var theme by remember { mutableStateOf(repository.theme()) }

    LaunchedEffect(Unit) {
        runCatching { withContext(Dispatchers.IO) { client.providers() } }
            .onSuccess { response ->
                val rows = response.optJSONArray("providers") ?: JSONArray()
                presets = (0 until rows.length()).mapNotNull { index -> rows.optJSONObject(index)?.let { row ->
                    ProviderConfig(
                        row.optString("id"), row.optString("label", row.optString("id")),
                        row.optString("base_url"), row.optString("model"), row.optString("api", "auto"),
                    ).takeIf { it.id.isNotBlank() }
                } }
            }
            .onFailure { status = "读取提供方失败：${it.message ?: it.javaClass.simpleName}" }
    }

    val selected = configs.firstOrNull { it.id == selectedId }
    Column(Modifier.fillMaxWidth().background(NativeColors.background).verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            BasicText("返回", Modifier.clickable(onClick = onBack).heightIn(min = 44.dp).padding(vertical = 12.dp), style = TextStyle(color = SettingsBlue))
            BasicText("设置", Modifier.padding(vertical = 12.dp), style = TextStyle(color = SettingsInk, fontSize = 18.sp, fontWeight = FontWeight.Bold))
            BasicText("保存", Modifier.clickable {
                repository.save(configs, activeId, rememberKey, theme)
                status = "设置已保存"
            }.heightIn(min = 44.dp).padding(vertical = 12.dp), style = TextStyle(color = SettingsBlue))
        }
        SettingsCard("云端模型", "选择提供方、填写 API Key，再选一个对话模型。") {
            if (configs.isEmpty()) BasicText("还没有添加提供方。", style = TextStyle(color = SettingsMuted))
            configs.forEach { config ->
                Row(Modifier.fillMaxWidth().clickable { selectedId = config.id }.padding(vertical = 8.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                    BasicText(config.label, style = TextStyle(color = SettingsInk, fontWeight = FontWeight.Medium))
                    BasicText(if (activeId == config.id) "当前使用" else "设为当前", Modifier.clickable {
                        activeId = config.id; selectedId = config.id
                    }.padding(horizontal = 10.dp, vertical = 8.dp), style = TextStyle(color = SettingsBlue))
                }
            }
            if (selected != null) {
                if (selected.custom) Field("名称", selected.label) { value -> configs = updateConfig(configs, selectedId) { it.copy(label = value) } }
                Field("接口地址", selected.baseUrl) { value -> configs = updateConfig(configs, selectedId) { it.copy(baseUrl = value) } }
                Field("API Key", selected.key, password = true) { value -> onKeyEdited(); configs = updateConfig(configs, selectedId) { it.copy(key = value) } }
                if (keyError) BasicText("密钥无效，请检查后重试", style = TextStyle(color = Color(0xFFF0524F), fontSize = 13.sp))
                Field("模型", selected.model) { value -> configs = updateConfig(configs, selectedId) { it.copy(model = value) } }
                Field("最大输出（可选）", selected.maxTokens?.toString().orEmpty()) { value -> configs = updateConfig(configs, selectedId) { it.copy(maxTokens = value.toIntOrNull()) } }
                Field("上下文窗口（可选）", selected.contextTokens?.toString().orEmpty()) { value -> configs = updateConfig(configs, selectedId) { it.copy(contextTokens = value.toIntOrNull()) } }
                SettingsButton("API 方言：${selected.api}") {
                    val next = when (selected.api) { "auto" -> "chat_completions"; "chat_completions" -> "responses"; else -> "auto" }
                    configs = updateConfig(configs, selectedId) { it.copy(api = next) }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SettingsButton("探测模型") {
                        val config = configs.first { it.id == selectedId }
                        scope.launch {
                            status = "正在探测可用模型…"
                            runCatching { withContext(Dispatchers.IO) { client.models(config) } }
                                .onSuccess { response ->
                                    val array = response.optJSONArray("chat_models") ?: response.optJSONArray("models") ?: JSONArray()
                                    models = (0 until array.length()).mapNotNull { array.optString(it).takeIf(String::isNotBlank) }
                                    status = if (models.isEmpty()) "没有发现模型，可手工填写模型名。" else "探测到 ${models.size} 个模型。"
                                }.onFailure { status = "探测失败：${it.message ?: it.javaClass.simpleName}" }
                        }
                    }
                    SettingsButton("测试连接") {
                        val config = configs.first { it.id == selectedId }
                        scope.launch {
                            status = "正在测试提供方…"
                            runCatching { withContext(Dispatchers.IO) { client.test(config) } }
                                .onSuccess { status = if (it.optBoolean("ok", true)) "连接成功" else "连接失败：${it.optString("error")}" }
                                .onFailure { status = "连接失败：${it.message ?: it.javaClass.simpleName}" }
                        }
                    }
                    SettingsButton("删除") { configs = configs.filterNot { it.id == selectedId }; if (activeId == selectedId) activeId = configs.firstOrNull()?.id.orEmpty(); selectedId = activeId }
                }
                models.forEach { model ->
                    BasicText(model, Modifier.fillMaxWidth().clickable { configs = updateConfig(configs, selectedId) { it.copy(model = model) } }.padding(vertical = 8.dp), style = TextStyle(color = SettingsMuted))
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SettingsButton("添加提供方") {
                    presets.firstOrNull { preset -> configs.none { it.id == preset.id } }?.let { preset ->
                        configs = configs + preset; selectedId = preset.id
                    } ?: run { status = "没有可添加的内置提供方。" }
                }
                SettingsButton("添加自定义提供方") {
                    val config = ProviderConfig("custom-${System.currentTimeMillis().toString(36)}", "自定义提供方", "", "", custom = true)
                    configs = configs + config; selectedId = config.id
                }
            }
            BasicText("记住 API Key（加密保存在本机）${if (rememberKey) "：已开启" else "：关闭"}",
                Modifier.fillMaxWidth().clickable { rememberKey = !rememberKey }.heightIn(min = 44.dp).padding(vertical = 12.dp), style = TextStyle(color = SettingsInk))
            BasicText("配置仅保存在当前设备。请妥善保管 API Key。", style = TextStyle(color = SettingsMuted, fontSize = 13.sp))
        }
        SettingsCard("外观", "") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("auto" to "跟随系统", "light" to "浅色", "dark" to "深色").forEach { (value, label) ->
                    SettingsButton(if (theme == value) "$label ✓" else label) { theme = value; onThemeChanged(value) }
                }
            }
        }
        SettingsCard("系统操作", "使用 Android 系统剪贴板、分享面板、文件保存和浏览器。") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SettingsButton("复制帮助链接") { onCopy("https://github.com/Xiao-John/OpenRailFanAI") }
                SettingsButton("粘贴 API Key") {
                    val pasted = onPaste().trim().lineSequence().firstOrNull().orEmpty()
                    if (pasted.isNotBlank() && selectedId.isNotBlank()) configs = updateConfig(configs, selectedId) { it.copy(key = pasted) }
                    status = if (pasted.isBlank()) "读不到剪贴板，请手动粘贴 API Key。" else "已从剪贴板填入 API Key。"
                }
                SettingsButton("分享应用") { onShare("RailFanAI · 让铁路出行更简单") }
                SettingsButton("导出设置") { onExport(configs.joinToString("\n") { "${it.label}: ${it.baseUrl} / ${it.model}" }) }
            }
            SettingsButton("查看帮助") { onOpenUrl("https://github.com/Xiao-John/OpenRailFanAI") }
        }
        if (status.isNotBlank()) BasicText(status, style = TextStyle(color = SettingsMuted).copy(fontSize = 13.sp))
    }
}

@Composable
private fun SettingsCard(title: String, subtitle: String, content: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().background(NativeColors.surface, RoundedCornerShape(14.dp)).border(1.dp, SettingsBorder, RoundedCornerShape(14.dp)).padding(14.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
        BasicText(title, style = TextStyle(color = SettingsInk, fontSize = 17.sp, fontWeight = FontWeight.Bold))
        if (subtitle.isNotBlank()) BasicText(subtitle, style = TextStyle(color = SettingsMuted, fontSize = 13.sp))
        content()
    }
}

@Composable
private fun Field(label: String, value: String, password: Boolean = false, onChange: (String) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        BasicText(label, style = TextStyle(color = SettingsInk, fontSize = 14.sp))
        BasicTextField(value, onChange, Modifier.fillMaxWidth().background(NativeColors.surface, RoundedCornerShape(9.dp)).border(1.dp, SettingsBorder, RoundedCornerShape(9.dp)).padding(11.dp)
            .semantics { contentDescription = if (password) "${label}输入框" else "$label 输入框" },
            textStyle = TextStyle(color = SettingsInk, fontSize = 15.sp),
            visualTransformation = if (password) PasswordVisualTransformation() else androidx.compose.ui.text.input.VisualTransformation.None,
            decorationBox = { inner ->
                if (value.isEmpty()) BasicText(if (password) "sk-…（只保存在本机）" else "填写$label", style = TextStyle(color = SettingsMuted))
                inner()
            },
        )
    }
}

@Composable
private fun SettingsButton(text: String, onClick: () -> Unit) {
    BasicText(text, Modifier.border(1.dp, SettingsBlue, RoundedCornerShape(10.dp)).clickable(onClick = onClick)
        .heightIn(min = 44.dp).padding(horizontal = 10.dp, vertical = 11.dp), style = TextStyle(color = SettingsBlue, fontSize = 13.sp))
}

private fun updateConfig(configs: List<ProviderConfig>, id: String, transform: (ProviderConfig) -> ProviderConfig): List<ProviderConfig> =
    configs.map { if (it.id == id) transform(it) else it }
