package org.openrailfanai.app

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

internal data class FuckCrStatus(val enabled: Boolean, val canEnable: Boolean, val ready: Boolean, val mode: String) {
    companion object {
        fun parse(json: JSONObject): FuckCrStatus {
            require(json.optInt("schema_version") == 1 && json.optString("feature") == "fuck_cr")
            listOf("enabled", "can_enable", "new_rules_ready").forEach { require(json.get(it) is Boolean) }
            return FuckCrStatus(json.getBoolean("enabled"), json.getBoolean("can_enable"), json.getBoolean("new_rules_ready"), json.getString("mode"))
                .also { require(!it.enabled || it.ready); require(!it.canEnable || it.ready) }
        }
    }
}

internal class FuckCrUnsupported : Exception()
internal data class FuckCrReply(val status: FuckCrStatus, val rejected: Boolean)

internal class FuckCrClient(private val baseUrl: String) {
    fun status(): FuckCrStatus = request(null).status
    fun activate(): FuckCrReply = request(true)
    private fun request(enabled: Boolean?): FuckCrReply {
        val path = if (enabled == null) "/api/fuck-cr/status" else "/api/fuck-cr/activation"
        val connection = URL(baseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection
        connection.connectTimeout = 8_000; connection.readTimeout = 15_000
        try {
            if (enabled != null) {
                connection.requestMethod = "POST"; connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.outputStream.use { it.write(JSONObject().put("enabled", enabled).toString().toByteArray(Charsets.UTF_8)) }
            }
            val code = connection.responseCode
            if (code == 404) throw FuckCrUnsupported()
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val body = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            require(code in 200..299 || code == 409) { "兼容状态请求失败" }
            val json = JSONObject(body)
            val status = if (code == 409) json.getJSONObject("detail").getJSONObject("status")
                else if (enabled != null) json.getJSONObject("status") else json
            return FuckCrReply(FuckCrStatus.parse(status), code == 409)
        } finally { connection.disconnect() }
    }
}

@Composable
internal fun FuckCrSettingsSection(baseUrl: String) {
    val client = remember(baseUrl) { FuckCrClient(baseUrl) }
    var confirmed by remember(baseUrl) { mutableStateOf<FuckCrStatus?>(null) }
    var busy by remember(baseUrl) { mutableStateOf(false) }
    var unsupported by remember(baseUrl) { mutableStateOf(false) }
    var feedback by remember(baseUrl) { mutableStateOf("") }
    var expanded by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    suspend fun refresh() {
        try {
            confirmed = withContext(Dispatchers.IO) { client.status() }
            unsupported = false; feedback = ""
        } catch (e: CancellationException) { throw e }
        catch (_: FuckCrUnsupported) { unsupported = true; confirmed = null; feedback = "当前服务暂不支持此入口。" }
        catch (_: Exception) { feedback = if (confirmed == null) "暂时无法取得兼容状态，请刷新。" else "状态刷新失败，保留上次确认结果。" }
    }
    LaunchedEffect(baseUrl) { busy = true; try { refresh() } finally { busy = false } }
    SettingsCard("车次规则兼容", "字母升官，代码加班。") {
        val status = confirmed
        BasicText(when {
            unsupported -> "当前服务不支持"
            status?.enabled == true -> "新版兼容已启用"
            status != null && !status.canEnable -> "适配准备中"
            status?.canEnable == true -> "新版适配已就绪"
            else -> "正在确认兼容状态"
        }, Modifier.testTag("fuck-cr-status"), style = TextStyle(color = NativeColors.muted, fontSize = 14.sp))
        SettingsButton(if (busy && status?.canEnable == true) "正在确认…" else "一键FUCK_CR", Modifier.fillMaxWidth().testTag("fuck-cr-activate"),
            primary = true, enabled = !busy && !unsupported && status?.canEnable == true && !status.enabled) {
            if (busy || confirmed?.canEnable != true || confirmed?.enabled == true) return@SettingsButton
            busy = true
            scope.launch {
                try {
                    val result = withContext(Dispatchers.IO) { client.activate() }
                    confirmed = result.status
                    feedback = when {
                        result.rejected -> "新版车次兼容规则尚未就绪。"
                        result.status.enabled -> "新版兼容已由服务确认启用。"
                        else -> "服务尚未确认启用，请刷新状态。"
                    }
                } catch (e: CancellationException) { throw e }
                catch (_: Exception) { refresh(); if (feedback.isBlank()) feedback = "启用结果已重新核对，请以上方状态为准。" }
                finally { busy = false }
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            SettingsButton(if (expanded) "收起说明" else "这是什么？", Modifier.testTag("fuck-cr-explanation")) { expanded = !expanded }
            SettingsButton("刷新状态", enabled = !busy) { busy = true; scope.launch { try { refresh() } finally { busy = false } } }
        }
        RailReveal(expanded) {
            BasicText("开关已经到岗，兼容规则还在赶末班车。\n\n这是应对车次规则变化的兼容入口。新版未就绪时仍按现行规则查询；按钮不会提前报喜。\n\n发布日期不是开关，表达对改革的热情也不能代替适配。", style = TextStyle(color = NativeColors.muted, fontSize = 14.sp, lineHeight = 21.sp))
        }
        if (feedback.isNotBlank()) BasicText(feedback, Modifier.testTag("fuck-cr-feedback"), style = TextStyle(color = NativeColors.muted, fontSize = 14.sp))
    }
}
