package org.openrailfanai.app

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class ProviderConfig(
    val id: String,
    val label: String,
    val baseUrl: String,
    val model: String,
    val api: String = "auto",
    val key: String = "",
    val custom: Boolean = false,
    val maxTokens: Int? = null,
    val contextTokens: Int? = null,
)

/** Reads and updates the WebUI-compatible state.json/secrets.json without replacing other state. */
class MainSettingsRepository private constructor(private val files: File) {
    constructor(context: Context) : this(context.applicationContext.filesDir)
    internal constructor(files: File, isolated: Boolean) : this(files)
    private val lock = Any()

    fun entries(): List<ProviderConfig> = synchronized(lock) {
        val saved = loadOrMigrateLlm()
        val remember = saved.optBoolean("rememberKey")
        val entries = saved.optJSONArray("entries") ?: JSONArray()
        (0 until entries.length()).mapNotNull { index ->
            val item = entries.optJSONObject(index) ?: return@mapNotNull null
            val id = item.optString("id").takeIf(String::isNotBlank) ?: return@mapNotNull null
            val key = item.optString("key").ifBlank { if (remember) secureGet(id) else "" }
            ProviderConfig(id, item.optString("label", id), item.optString("base_url"), item.optString("model"),
                item.optString("api", "auto"), key, item.optBoolean("custom"), item.optInt("max_tokens").takeIf { it > 0 },
                item.optInt("context_tokens").takeIf { it > 0 })
        }
    }

    fun activeId(): String = synchronized(lock) {
        loadOrMigrateLlm().optString("activeId")
    }

    fun rememberKey(): Boolean = synchronized(lock) {
        val data = loadOrMigrateLlm()
        data.optBoolean("rememberKey")
    }

    fun theme(): String = synchronized(lock) { readState().optString(THEME_KEY, "auto") }

    fun save(configs: List<ProviderConfig>, activeId: String, rememberKey: Boolean, theme: String? = null) = synchronized(lock) {
        val root = readState()
        val secrets = readSecrets()
        val activeIds = configs.mapTo(mutableSetOf()) { it.id }
        val savedIds = secrets.keys().asSequence().toList()
        savedIds.filterNot(activeIds::contains).forEach(secrets::remove)
        val entries = JSONArray()
        configs.forEach { config ->
            val item = JSONObject().put("id", config.id).put("label", config.label)
                .put("base_url", config.baseUrl).put("model", config.model).put("api", config.api)
                .put("custom", config.custom).put("max_tokens", config.maxTokens ?: JSONObject.NULL)
                .put("context_tokens", config.contextTokens ?: JSONObject.NULL)
            if (rememberKey) {
                if (config.key.isNotBlank()) securePut(secrets, config.id, config.key)
                else if (!secrets.has(config.id)) item.put("key", "")
            } else {
                secrets.remove(config.id)
                item.put("key", "")
            }
            entries.put(item)
        }
        writeSecrets(secrets)
        val llm = JSONObject().put("entries", entries).put("activeId", activeId).put("rememberKey", rememberKey)
        root.put(LLM_KEY, llm.toString())
        if (!theme.isNullOrBlank()) root.put(THEME_KEY, theme)
        writeState(root)
    }

    fun requestLlmSpec(): JSONObject? = synchronized(lock) {
        val active = activeId()
        val config = entries().firstOrNull { it.id == active } ?: return null
        JSONObject().put("model", config.model).put("api", config.api)
            .put("base_url", config.baseUrl).put("api_key", config.key)
            .also { if (!config.custom) it.put("provider", config.id) }
            .also { if (config.maxTokens != null) it.put("max_tokens", config.maxTokens) }
            .also { if (config.contextTokens != null) it.put("context_tokens", config.contextTokens) }
    }

    private fun readState(): JSONObject = readJson(File(files, "state.json"))
    private fun readSecrets(): JSONObject = readJson(File(files, SECRETS_FILE))
    private fun readJson(file: File): JSONObject = runCatching { if (file.isFile) JSONObject(file.readText(StandardCharsets.UTF_8)) else JSONObject() }
        .onFailure { Log.w("RailFanSettings", "Could not read local settings state") }.getOrDefault(JSONObject())

    private fun writeState(value: JSONObject) = writeAtomically(File(files, "state.json"), value.toString())
    private fun writeSecrets(value: JSONObject) = writeAtomically(File(files, SECRETS_FILE), value.toString())

    /** Convert the WebUI's pre-entry-list object once, encrypting any legacy saved key before replacing it. */
    private fun loadOrMigrateLlm(): JSONObject {
        val root = readState()
        val saved = root.optString(LLM_KEY).takeIf(String::isNotBlank)?.let { runCatching { JSONObject(it) }.getOrNull() } ?: JSONObject()
        if (saved.optJSONArray("entries") != null) return saved
        val provider = saved.optString("provider").ifBlank { if (saved.has("base_url")) "custom" else "" }
        if (provider.isBlank()) return saved
        val id = provider
        val keys = saved.optJSONObject("keys") ?: JSONObject()
        val legacyKey = keys.optString(id).ifBlank { keys.optString(provider) }
        val remember = saved.optBoolean("rememberKey")
        val config = JSONObject().put("id", id).put("label", if (id == "custom") "自定义" else id)
            .put("base_url", saved.optString("base_url")).put("model", saved.optString("model"))
            .put("api", saved.optString("api", "auto")).put("custom", id == "custom")
            .put("max_tokens", JSONObject.NULL).put("context_tokens", JSONObject.NULL)
        val secrets = readSecrets()
        if (legacyKey.isNotBlank()) securePut(secrets, id, legacyKey)
        val migrated = JSONObject().put("entries", JSONArray().put(config)).put("activeId", id).put("rememberKey", remember)
        writeSecrets(secrets)
        root.put(LLM_KEY, migrated.toString())
        writeState(root)
        return migrated
    }
    private fun writeAtomically(file: File, value: String) {
        val temp = File(file.parentFile, file.name + ".tmp")
        FileOutputStream(temp).use { output ->
            output.write(value.toByteArray(StandardCharsets.UTF_8))
            output.fd.sync()
        }
        if (!temp.renameTo(file)) {
            if (file.exists()) file.delete()
            check(temp.renameTo(file)) { "Unable to atomically update ${file.name}" }
        }
    }

    private fun secureGet(id: String): String = runCatching {
        val encoded = readSecrets().optString(id)
        if (encoded.isBlank()) return ""
        val blob = Base64.decode(encoded, Base64.NO_WRAP)
        val cipher = Cipher.getInstance(TRANSFORM)
        cipher.init(Cipher.DECRYPT_MODE, keystoreKey(), GCMParameterSpec(TAG_BITS, blob, 0, IV_BYTES))
        String(cipher.doFinal(blob, IV_BYTES, blob.size - IV_BYTES), StandardCharsets.UTF_8)
    }.onFailure { Log.w("RailFanSettings", "Could not read saved provider credential") }.getOrDefault("")

    private fun securePut(all: JSONObject, id: String, value: String) {
        if (value.isBlank()) { all.remove(id); return }
        val cipher = Cipher.getInstance(TRANSFORM)
        cipher.init(Cipher.ENCRYPT_MODE, keystoreKey())
        val iv = cipher.iv
        val encrypted = cipher.doFinal(value.toByteArray(StandardCharsets.UTF_8))
        all.put(id, Base64.encodeToString(iv + encrypted, Base64.NO_WRAP))
    }

    private fun keystoreKey(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val entry = store.getEntry(KEY_ALIAS, null)
        if (entry is KeyStore.SecretKeyEntry) return entry.secretKey
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setKeySize(256).build())
        return generator.generateKey()
    }

    companion object {
        private const val LLM_KEY = "railfan_llm_v1"
        private const val THEME_KEY = "railfan_theme"
        private const val SECRETS_FILE = "secrets.json"
        private const val KEY_ALIAS = "railfan-byok-v1"
        private const val TRANSFORM = "AES/GCM/NoPadding"
        private const val IV_BYTES = 12
        private const val TAG_BITS = 128
    }
}
