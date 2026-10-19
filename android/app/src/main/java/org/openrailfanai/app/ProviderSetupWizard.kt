package org.openrailfanai.app

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray

/** Web completion is user-confirmed; every provider follows its own official guide. */
@Composable
internal fun ProviderSetupWizard(
    repository: MainSettingsRepository,
    client: SettingsClient,
    official: ProviderConfig?,
    guide: ProviderSetupGuide,
    defaultRemember: Boolean,
    onPaste: () -> String,
    onCopy: (String) -> Unit,
    onOpenUrl: (String) -> Unit,
    onDismiss: () -> Unit,
    onReloadCatalog: () -> Unit,
    onConnectAndSave: (ProviderConfig, Boolean) -> Unit,
    onFinished: () -> Unit,
) {
    val restored = remember { repository.providerSetupProgress(guide.id) }
    var step by remember(guide.id) { mutableStateOf(restored?.step?.takeIf { it in guide.steps } ?: ProviderSetupStep.SERVICE) }
    var rememberKey by remember { mutableStateOf(restored?.rememberKey ?: defaultRemember) }
    var apiKey by remember { mutableStateOf(repository.providerSetupKey(guide.id).ifBlank { official?.key.orEmpty() }) }
    var model by remember { mutableStateOf(restored?.model?.takeIf(String::isNotBlank) ?: official?.model.orEmpty()) }
    var feedback by remember { mutableStateOf("") }
    var models by remember { mutableStateOf<List<String>>(emptyList()) }
    var busy by remember { mutableStateOf(false) }
    var operation by remember { mutableStateOf<Job?>(null) }
    val scope = rememberCoroutineScope()
    val scroll = rememberScrollState()

    fun persist(target: ProviderSetupStep): Boolean = try {
        repository.saveProviderSetup(guide.id, ProviderSetupProgress(target, rememberKey, model), apiKey)
        true
    } catch (_: Exception) {
        feedback = "进度未能保存，请重试。"
        false
    }
    fun move(target: ProviderSetupStep) {
        if (target in guide.steps && persist(target)) { step = target; feedback = ""; models = emptyList() }
    }
    fun pause() {
        operation?.cancel(); busy = false
        if (step == ProviderSetupStep.FINISH) onFinished() else if (persist(step)) onDismiss()
    }
    LaunchedEffect(step) { scroll.scrollTo(0) }
    // Progress contains no key; credentials use the existing optional encrypted/session storage.
    LaunchedEffect(Unit) { persist(step) }

    fun openWebsite(url: String) {
        if (!persist(step)) return
        try { onOpenUrl(url) }
        catch (_: Exception) { feedback = "官网未能打开，请检查浏览器后重试。" }
    }

    fun connect() {
        if (official == null || providerPresetHelp(official) == null) {
            feedback = "官方连接预设尚未加载，请重新加载服务商目录。"; return
        }
        if (apiKey.isBlank()) { feedback = "请先粘贴${guide.name}的 API Key。"; return }
        if (busy) return
        feedback = "正在验证连接…"; busy = true
        val requested = official.copy(key = apiKey.trim()).withModel(model.ifBlank { official.model })
        val requestedRemember = rememberKey
        operation = scope.launch {
            try {
                val ready = withContext(Dispatchers.IO) {
                    var candidate = requested
                    if (candidate.model.isBlank()) {
                        val catalog = client.models(candidate)
                        if (catalog.has("ok") && !catalog.optBoolean("ok")) error(providerFailureCopy(catalog.optString("error")))
                        val array = catalog.optJSONArray("chat_models") ?: catalog.optJSONArray("models") ?: JSONArray()
                        val available = (0 until array.length()).mapNotNull { array.optString(it).takeIf(String::isNotBlank) }
                        val chosen = pickPresetModel(candidate, available)
                            ?: if (available.isNotEmpty()) throw PresetModelSelectionRequired(available)
                            else error("账户暂未返回可用对话模型，请检查模型权限。")
                        candidate = candidate.withModel(chosen)
                    }
                    val result = client.test(candidate)
                    if (!result.optBoolean("ok")) error(providerFailureCopy(result.optString("error")))
                    candidate
                }
                coroutineContext.ensureActive()
                model = ready.model
                try {
                    onConnectAndSave(ready, requestedRemember)
                } catch (_: Exception) {
                    feedback = "连接成功，但配置未保存，请重试。"
                    return@launch
                }
                // The usable configuration is already saved; a cleanup failure must not undo it.
                runCatching { repository.clearProviderSetup(guide.id) }
                step = ProviderSetupStep.FINISH
                feedback = "连接成功，配置已保存。"
            } catch (e: CancellationException) {
                throw e
            } catch (e: PresetModelSelectionRequired) {
                models = e.available
                feedback = "默认模型暂不可用，请从账户列表中选择。"
            } catch (e: Exception) {
                feedback = providerFailureCopy(e.message.orEmpty())
            } finally { busy = false }
        }
    }

    fun loadModels() {
        val candidate = official?.copy(key = apiKey.trim())
        if (candidate == null || providerPresetHelp(candidate) == null || apiKey.isBlank()) {
            feedback = "请先粘贴密钥，并确认官方预设已加载。"; return
        }
        if (busy) return
        busy = true; feedback = "正在读取账户模型…"
        operation = scope.launch {
            try {
                val available = withContext(Dispatchers.IO) {
                    val response = client.models(candidate)
                    if (response.has("ok") && !response.optBoolean("ok")) error(providerFailureCopy(response.optString("error")))
                    val array = response.optJSONArray("chat_models") ?: response.optJSONArray("models") ?: JSONArray()
                    (0 until array.length()).mapNotNull { array.optString(it).takeIf(String::isNotBlank) }.distinct()
                }
                coroutineContext.ensureActive()
                models = available
                feedback = if (models.isEmpty()) "未返回可用对话模型，请检查账户权限。" else "请选择一个模型，再点击连接。"
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { feedback = providerFailureCopy(e.message.orEmpty()) }
            finally { busy = false }
        }
    }

    Dialog(onDismissRequest = ::pause) {
        Column(Modifier.fillMaxWidth().heightIn(max = 680.dp)
            .background(NativeColors.surface, RoundedCornerShape(20.dp))
            .border(1.dp, NativeColors.line, RoundedCornerShape(20.dp)).padding(18.dp).testTag("provider-setup-wizard"),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                BasicText("配置向导", Modifier.weight(1f), style = TextStyle(color = NativeColors.ink, fontSize = 20.sp, fontWeight = FontWeight.Bold))
                SettingsButton(if (step == ProviderSetupStep.FINISH) "完成" else "稍后继续") { pause() }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                guide.steps.forEach { item ->
                    Box(Modifier.weight(1f).height(4.dp).background(if (item.ordinal <= step.ordinal) NativeColors.blue else NativeColors.panel, RoundedCornerShape(2.dp)))
                }
            }
            BasicText("第 ${guide.steps.indexOf(step) + 1} 步，共 ${guide.steps.size} 步 · ${guide.name}", style = wizardCaption())
            Column(Modifier.weight(1f, fill = false).verticalScroll(scroll), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                BasicText(if (step == ProviderSetupStep.SERVICE) "使用${guide.name}" else step.title, style = TextStyle(color = NativeColors.ink, fontSize = 22.sp, fontWeight = FontWeight.Bold))
                when (step) {
                    ProviderSetupStep.SERVICE -> {
                        WizardInstructions(providerPresetHelp(guide.id)?.description.orEmpty(), "向导会带你登录官网、确认服务可用并创建密钥。", "已有密钥可直接跳到连接。")
                        if (guide.note.isNotBlank()) BasicText(guide.note, style = wizardCaption())
                        if (official == null) {
                            BasicText("连接预设正在等待服务商目录。你仍可先完成官网步骤。", style = wizardCaption())
                            SettingsButton("重新加载目录", Modifier.fillMaxWidth()) { onReloadCatalog() }
                        }
                    }
                    ProviderSetupStep.ACCOUNT -> {
                        WizardInstructions("打开${guide.name}官方账户页面，按官网提供的方式注册或登录。", "完成后切回 RailFanAI，点击“我已登录”。")
                        WizardWebsite("前往注册 / 登录", guide.accountUrl, ::openWebsite)
                    }
                    ProviderSetupStep.AUTH -> {
                        WizardInstructions(guide.authInstructions.orEmpty())
                        guide.authUrl?.let { WizardWebsite("打开认证页面", it, ::openWebsite) }
                    }
                    ProviderSetupStep.CREDIT -> {
                        WizardInstructions(guide.accessInstructions, guide.creditInstructions)
                        WizardWebsite(guide.accessLabel, guide.accessUrl, ::openWebsite)
                    }
                    ProviderSetupStep.KEY -> {
                        WizardInstructions(guide.keyInstructions, "若官网要求填写密钥名称，可使用 RailFanAI。", "复制完整密钥，再切回本应用。")
                        SettingsButton("复制建议名称", Modifier.fillMaxWidth(), enabled = !busy) { onCopy("RailFanAI"); feedback = "名称已复制。" }
                        WizardWebsite(guide.keyLabel, guide.keyUrl, ::openWebsite)
                        BasicText("API Key 用于连接模型，与登录密码不同。", style = wizardCaption())
                    }
                    ProviderSetupStep.CONNECT -> {
                        BasicText("地址与接口格式使用官方预设。连接会发送一条简短测试消息，按服务商标准计费。", style = wizardCaption())
                        Field("服务密钥", apiKey, password = true, enabled = !busy, trailingAction = "粘贴", onTrailingAction = {
                            val pasted = onPaste().trim()
                            if (pasted.isNotBlank()) { apiKey = pasted; feedback = "密钥已填入。" }
                            else feedback = "剪贴板没有可用内容，请手动输入。"
                        }) { apiKey = it.trim(); feedback = "" }
                        BasicText(if (model.ifBlank { official?.model.orEmpty() }.isBlank()) "模型：连接时自动选择账户可用的预设模型。" else "模型：${model.ifBlank { official?.model.orEmpty() }}", style = wizardCaption())
                        SettingsButton("选择 / 更换模型", Modifier.fillMaxWidth(), enabled = !busy) { loadModels() }
                        if (models.isNotEmpty()) Column(Modifier.heightIn(max = 180.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            models.forEach { choice -> SettingsButton(choice, Modifier.fillMaxWidth()) { model = choice; models = emptyList(); feedback = "已选择模型，点击连接继续。" } }
                        }
                        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                BasicText("记住密钥", style = TextStyle(color = NativeColors.ink, fontSize = 16.sp))
                                BasicText(if (rememberKey) "加密保存在当前设备。" else "本次运行可用，关闭后需重新填写。", style = wizardCaption())
                            }
                            Spacer(Modifier.width(8.dp))
                            SettingsSwitch(rememberKey, !busy) { rememberKey = it }
                        }
                        if (feedback.contains("余额") || feedback.contains("额度不足")) SettingsButton("返回额度步骤", Modifier.fillMaxWidth(), enabled = !busy) { move(ProviderSetupStep.CREDIT) }
                        SettingsButton("重新打开密钥页面", Modifier.fillMaxWidth(), enabled = !busy) { openWebsite(guide.keyUrl) }
                        if (official == null) SettingsButton("重新加载目录", Modifier.fillMaxWidth()) { onReloadCatalog() }
                    }
                    ProviderSetupStep.FINISH -> {
                        WizardInstructions("${guide.name}连接已通过真实验证，配置已保存。", "使用模型：$model", "回到对话即可开始使用。")
                    }
                }
                if (feedback.isNotBlank()) BasicText(feedback, Modifier.fillMaxWidth().background(NativeColors.panel, RoundedCornerShape(10.dp)).padding(12.dp).testTag("provider-setup-feedback"), style = wizardCaption())
                if (step in setOf(ProviderSetupStep.ACCOUNT, ProviderSetupStep.AUTH, ProviderSetupStep.CREDIT, ProviderSetupStep.KEY))
                    BasicText("打开官网或返回应用不会自动完成此步骤，请确认后继续。", style = wizardCaption())
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                if (step != ProviderSetupStep.SERVICE && step != ProviderSetupStep.FINISH) SettingsButton("上一步", Modifier.weight(1f), enabled = !busy) { move(guide.steps[(guide.steps.indexOf(step) - 1).coerceAtLeast(0)]) }
                val label = when (step) {
                    ProviderSetupStep.SERVICE -> "使用${guide.name}"
                    ProviderSetupStep.ACCOUNT -> "我已登录"
                    ProviderSetupStep.AUTH -> "认证已完成"
                    ProviderSetupStep.CREDIT -> "账户已准备好"
                    ProviderSetupStep.KEY -> "已复制，继续"
                    ProviderSetupStep.CONNECT -> if (busy) "正在连接…" else "连接并保存"
                    ProviderSetupStep.FINISH -> "回到对话"
                }
                SettingsButton(label, Modifier.weight(2f).testTag("provider-setup-next"), primary = true, enabled = !busy) {
                    when (step) {
                        ProviderSetupStep.CONNECT -> connect()
                        ProviderSetupStep.FINISH -> onFinished()
                        else -> move(guide.steps.getOrElse(guide.steps.indexOf(step) + 1) { ProviderSetupStep.FINISH })
                    }
                }
            }
            if (step == ProviderSetupStep.SERVICE || step == ProviderSetupStep.ACCOUNT) SettingsButton("已有密钥，直接连接", Modifier.fillMaxWidth(), enabled = !busy) { move(ProviderSetupStep.CONNECT) }
        }
    }
}

@Composable
private fun WizardInstructions(vararg steps: String) {
    Column(Modifier.fillMaxWidth().background(NativeColors.panel, RoundedCornerShape(12.dp)).padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        steps.forEachIndexed { index, text -> BasicText("${index + 1}. $text", style = TextStyle(color = NativeColors.ink, fontSize = 16.sp)) }
    }
}
@Composable
private fun WizardWebsite(label: String, url: String, onOpenUrl: (String) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        SettingsButton(label, Modifier.fillMaxWidth()) { onOpenUrl(url) }
        BasicText("官网 · ${java.net.URI(url).host}", style = wizardCaption())
        BasicText("关闭官网窗口后，回到向导继续。", style = wizardCaption())
    }
}
private fun wizardCaption() = TextStyle(color = NativeColors.muted, fontSize = 14.sp)
