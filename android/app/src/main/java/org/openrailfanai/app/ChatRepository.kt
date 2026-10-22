package org.openrailfanai.app

import android.util.Log
import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

/** Owns the HTTP/SSE contract with the existing FastAPI chat endpoint. */
class ChatRepository(private val baseUrl: String, context: Context? = null) {
    private val appContext = context?.applicationContext
    @Volatile private var activeConnection: HttpURLConnection? = null
    @Volatile private var activeWorker: Thread? = null
    @Volatile private var capabilitiesChecked = false
    @Volatile private var ticketFareAvailabilitySupported = false

    fun cancel() { activeWorker?.interrupt(); activeConnection?.disconnect() }

    fun stream(request: ChatStreamRequest, onEvent: (JSONObject) -> Unit): ChatStreamOutcome {
        activeWorker = Thread.currentThread()
        return try { streamRequest(request, onEvent) } finally { activeWorker = null }
    }

    private fun streamRequest(request: ChatStreamRequest, onEvent: (JSONObject) -> Unit): ChatStreamOutcome {
        if (request.displayAction == null && (request.agentFiles != null || AppAgent.fileCommand(request.message))) {
            return runFileAgent(request, onEvent)
        }
        val connection = (URL("$baseUrl/api/chat/stream").openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 15_000
            readTimeout = 0
            doOutput = true
            setRequestProperty("Content-Type", "application/json; charset=utf-8")
            setRequestProperty("Accept", "text/event-stream")
        }
        activeConnection = connection
        val answer = StringBuilder()
        val thinking = StringBuilder()
        var error: String? = null
        var intent: String? = null
        var sources: List<String> = emptyList()
        var results: List<DisplayResult> = emptyList()
        var displayResultsJson = "[]"
        var usage = JSONObject()
        var latency: Double? = null
        var processLogs: List<String> = emptyList()
        var slots: JSONArray? = null
        try {
            val capabilities = if (ensureCapabilities()) listOf("ticket_fare_availability_v1") else emptyList()
            val effectiveRequest = request.copy(clientCapabilities = capabilities)
            connection.outputStream.use { it.write(effectiveRequest.toJson().toString().toByteArray(StandardCharsets.UTF_8)) }
            val code = connection.responseCode
            if (code !in 200..299) throw ChatHttpException(code)
            InputStreamReader(connection.inputStream, StandardCharsets.UTF_8).buffered().use { reader ->
                readSseEvents(reader) { event ->
                    when (event.optString("type")) {
                        "answer" -> answer.append(event.optString("delta"))
                        "think", "thinking" -> thinking.append(event.optString("delta"))
                        "replace" -> { answer.setLength(0); answer.append(event.optString("text")) }
                        "error" -> error = event.optString("message").takeIf(String::isNotBlank)
                        "done" -> {
                            val finalThinking = event.optString("thinking").takeIf(String::isNotBlank)
                            if (finalThinking != null) { thinking.setLength(0); thinking.append(finalThinking) }
                            intent = event.string("intent")
                            slots = event.optJSONArray("slots")
                            sources = stringArray(event.optJSONArray("sources"))
                            results = DisplayResultParser.parseArray(event.optJSONArray("display_results"))
                            displayResultsJson = event.optJSONArray("display_results")?.toString() ?: "[]"
                            usage = event.optJSONObject("usage") ?: JSONObject()
                            latency = if (event.has("latency_ms") && !event.isNull("latency_ms")) event.optDouble("latency_ms") else null
                            processLogs = stringArray(event.optJSONArray("process_logs"))
                            if (event.has("error") && !event.isNull("error")) error = event.optString("error")
                        }
                    }
                    onEvent(event)
                }
            }
            val photoSpotJson = if (intent == "photo_spot" && error == null && !Thread.currentThread().isInterrupted) {
                PhotoSpotRepository(baseUrl).snapshot(photoScope(slots), sources).toString()
            } else null
            return ChatStreamOutcome(answer.toString(), error, intent, sources, results, usage, latency, processLogs, displayResultsJson, photoSpotJson, thinking.toString())
        } catch (e: ChatHttpException) {
            throw e
        } catch (e: Exception) {
            if (Thread.currentThread().isInterrupted) throw InterruptedException("Request cancelled")
            Log.w("RailFanChat", "Chat stream failed (${e.javaClass.simpleName})")
            throw e
        } finally {
            activeConnection = null
            connection.disconnect()
        }
    }

    private fun runFileAgent(request: ChatStreamRequest, onEvent: (JSONObject) -> Unit): ChatStreamOutcome {
        val declared = request.llmSpec?.optJSONArray("input_types") ?: JSONArray().put("text")
        val imageEnabled = (0 until declared.length()).any { declared.optString(it) == "image" }
        var files = request.agentFiles ?: JSONArray()
        fun localOcr() {
            onEvent(JSONObject().put("type", "stage").put("stage", "local_ocr").put("msg", "正在设备上识别图片文字…"))
            files = ImageTextRecognizer.convert(appContext ?: error("设备端 OCR 需要应用上下文"), files)
        }
        val hasImages = (0 until files.length()).any { i -> files.optJSONObject(i)?.let { AppAgent.isImage(it.optString("name"), it.optString("mime")) } == true }
        if (!imageEnabled && hasImages) localOcr()
        val body = request.toJson().put("actions", JSONArray().put("files"))
            .put("files", files).put("input_types", if (imageEnabled) JSONArray().put("text").put("image") else JSONArray().put("text"))
        var payload = agentResponse(body)
        if (payload.optBoolean("needs_local_ocr")) {
            if (!imageEnabled) error("本地识别后仍无法处理附件，请重新导入。")
            localOcr()
            payload = agentResponse(body.put("files", files).put("input_types", JSONArray().put("text")))
            if (payload.optBoolean("needs_local_ocr")) error("无法处理图片，请换清晰图片后重试。")
        }
        if (!payload.optBoolean("handled")) throw IllegalStateException("未识别出文件操作")
        if (Thread.currentThread().isInterrupted) throw InterruptedException("Request cancelled")
        val outputs = payload.optJSONArray("files") ?: JSONArray()
        for (i in 0 until outputs.length()) onEvent(JSONObject().put("type", "agent_file").put("file", outputs.getJSONObject(i)))
        val answer = payload.optString("answer", "文件草稿已生成，请选择保存位置。")
        onEvent(JSONObject().put("type", "answer").put("delta", answer))
        return ChatStreamOutcome(answer, null, "file_tool", emptyList(), emptyList(), payload.optJSONObject("usage") ?: JSONObject(), null, emptyList(), "[]")
    }

    private fun agentResponse(body: JSONObject): JSONObject {
        if (Thread.currentThread().isInterrupted) throw InterruptedException("Request cancelled")
        val connection = (URL("$baseUrl/api/agent/run").openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"; connectTimeout = 15_000; readTimeout = 120_000; doOutput = true
            setRequestProperty("Content-Type", "application/json; charset=utf-8")
        }
        activeConnection = connection
        try {
            connection.outputStream.use { it.write(body.toString().toByteArray(StandardCharsets.UTF_8)) }
            if (connection.responseCode !in 200..299) throw ChatHttpException(connection.responseCode)
            return connection.inputStream.bufferedReader(StandardCharsets.UTF_8).use { JSONObject(it.readText()) }
        } finally { connection.disconnect(); activeConnection = null }
    }

    /** Probe once per backend instance; unknown or failed probes keep legacy delivery safe. */
    private fun ensureCapabilities(): Boolean {
        if (capabilitiesChecked) return ticketFareAvailabilitySupported
        synchronized(this) {
            if (capabilitiesChecked) return ticketFareAvailabilitySupported
            val probe = (URL("$baseUrl/api/sessions").openConnection() as HttpURLConnection).apply {
                connectTimeout = 4_000; readTimeout = 4_000; requestMethod = "GET"
            }
            try {
                if (probe.responseCode in 200..299) {
                    val json = probe.inputStream.bufferedReader(StandardCharsets.UTF_8).use { JSONObject(it.readText()) }
                    val values = json.optJSONArray("supported_client_capabilities")
                    ticketFareAvailabilitySupported = (0 until (values?.length() ?: 0)).any {
                        values?.optString(it) == "ticket_fare_availability_v1"
                    }
                }
            } catch (_: Exception) {
                ticketFareAvailabilitySupported = false
            } finally {
                probe.disconnect()
                capabilitiesChecked = true
            }
            return ticketFareAvailabilitySupported
        }
    }

    private fun readSseEvents(reader: BufferedReader, consume: (JSONObject) -> Unit) {
        val dataLines = mutableListOf<String>()
        fun dispatch() {
            if (dataLines.isEmpty()) return
            val payload = dataLines.joinToString("\n")
            dataLines.clear()
            runCatching { JSONObject(payload) }.onSuccess(consume)
                .onFailure { Log.w("RailFanChat", "Ignored malformed SSE JSON") }
        }
        while (true) {
            val line = reader.readLine() ?: break
            if (line.isEmpty()) {
                dispatch()
            } else if (!line.startsWith(":")) {
                when {
                    line.startsWith("data:") -> dataLines += line.removePrefix("data:").removePrefix(" ")
                    line.startsWith("data ") -> dataLines += line.removePrefix("data ")
                }
            }
        }
        dispatch()
    }

    private fun stringArray(array: JSONArray?): List<String> {
        if (array == null) return emptyList()
        return (0 until array.length()).mapNotNull { index -> array.opt(index)?.takeUnless { it == JSONObject.NULL }?.toString() }
    }
}

class ChatHttpException(val statusCode: Int) : Exception("HTTP $statusCode")

private fun JSONObject.string(key: String): String? =
    opt(key)?.takeUnless { it == JSONObject.NULL }?.toString()?.takeIf(String::isNotEmpty)
