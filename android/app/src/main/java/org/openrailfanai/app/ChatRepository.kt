package org.openrailfanai.app

import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

/** Owns the HTTP/SSE contract with the existing FastAPI chat endpoint. */
class ChatRepository(private val baseUrl: String) {
    @Volatile private var activeConnection: HttpURLConnection? = null
    @Volatile private var capabilitiesChecked = false
    @Volatile private var ticketFareAvailabilitySupported = false

    fun cancel() { activeConnection?.disconnect() }

    fun stream(request: ChatStreamRequest, onEvent: (JSONObject) -> Unit): ChatStreamOutcome {
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
        var error: String? = null
        var intent: String? = null
        var sources: List<String> = emptyList()
        var results: List<DisplayResult> = emptyList()
        var displayResultsJson = "[]"
        var usage = JSONObject()
        var latency: Double? = null
        var processLogs: List<String> = emptyList()
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
                        "replace" -> { answer.setLength(0); answer.append(event.optString("text")) }
                        "error" -> error = event.optString("message").takeIf(String::isNotBlank)
                        "done" -> {
                            intent = event.string("intent")
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
            return ChatStreamOutcome(answer.toString(), error, intent, sources, results, usage, latency, processLogs, displayResultsJson)
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
