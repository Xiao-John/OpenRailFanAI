package org.openrailfanai.app

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

class SettingsClient(private val baseUrl: String) {
    fun providers(): JSONObject = request("/api/providers")
    fun models(config: ProviderConfig): JSONObject = request("/api/providers/models", config)
    fun test(config: ProviderConfig): JSONObject = request("/api/providers/test", config)

    private fun request(path: String, config: ProviderConfig? = null): JSONObject {
        val connection = URL(baseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection
        connection.connectTimeout = 8_000
        connection.readTimeout = 30_000
        if (config != null) {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            val body = JSONObject().put("base_url", config.baseUrl)
                .put("model", config.model).put("api", config.api).put("api_key", config.key)
            if (!config.custom) body.put("provider", config.id)
            connection.outputStream.use { it.write(body.toString().toByteArray(StandardCharsets.UTF_8)) }
        }
        try {
            val stream = if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader(StandardCharsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (connection.responseCode !in 200..299) throw IllegalStateException("HTTP ${connection.responseCode}: $text")
            return JSONObject(text)
        } finally { connection.disconnect() }
    }
}
