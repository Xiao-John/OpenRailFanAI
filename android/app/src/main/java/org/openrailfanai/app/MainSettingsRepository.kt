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
    val inputTypes: Set<String> = setOf("text"),
) {
    fun withModel(value: String): ProviderConfig = value.trim().let {
        if (it == model) this else copy(model = it, inputTypes = setOf("text"))
    }
    fun withBaseUrl(value: String): ProviderConfig = value.trim().let {
        if (it == baseUrl) this else copy(baseUrl = it, inputTypes = setOf("text"))
    }
}

/** Reads and updates the WebUI-compatible state.json/secrets.json without replacing other state. */
class MainSettingsRepository private constructor(private val files: File) {
    constructor(context: Context) : this(context.applicationContext.filesDir)
    internal constructor(files: File, isolated: Boolean) : this(files)
    private val lock = sessionLock
    private val sessionNamespace = files.absolutePath

    fun entries(): List<ProviderConfig> = synchronized(lock) {
        val saved = loadOrMigrateLlm()
        val remember = saved.optBoolean("rememberKey")
        val entries = saved.optJSONArray("entries") ?: JSONArray()
        (0 until entries.length()).mapNotNull { index ->
            val item = entries.optJSONObject(index) ?: return@mapNotNull null
            val id = item.optString("id").takeIf(String::isNotBlank) ?: return@mapNotNull null
            val rememberEntry = if (item.has("remember_key")) item.optBoolean("remember_key") else remember
            val key = sessionKeys[sessionNamespace]?.get(id)
                ?: item.optString("key").ifBlank { if (rememberEntry) secureGet(id) else "" }
            ProviderConfig(id, item.optString("label", id), item.optString("base_url"), item.optString("model"),
                item.optString("api", "auto"), key, item.optBoolean("custom"), item.optInt("max_tokens").takeIf { it > 0 },
                item.optInt("context_tokens").takeIf { it > 0 },
                setOf("text") + if (item.optJSONArray("input_types")?.let { types -> (0 until types.length()).any { types.optString(it) == "image" } } == true) setOf("image") else emptySet())
        }
    }

    fun activeId(): String = synchronized(lock) {
        loadOrMigrateLlm().optString("activeId")
    }

    fun rememberKey(): Boolean = rememberKeyForProvider(activeId())

    internal fun rememberKeyForProvider(id: String): Boolean = synchronized(lock) {
        val data = loadOrMigrateLlm()
        val rows = data.optJSONArray("entries") ?: JSONArray()
        val entry = (0 until rows.length()).mapNotNull { rows.optJSONObject(it) }.firstOrNull { it.optString("id") == id }
        if (entry?.has("remember_key") == true) entry.optBoolean("remember_key") else data.optBoolean("rememberKey")
    }

    fun theme(): String = synchronized(lock) { readState().optString(THEME_KEY, "auto") }

    fun setTheme(value: String) = synchronized(lock) {
        require(value in listOf("auto", "light", "dark"))
        writeState(readState().put(THEME_KEY, value))
    }

    fun save(configs: List<ProviderConfig>, activeId: String, rememberKey: Boolean, theme: String? = null) = synchronized(lock) {
        val root = readState()
        val secrets = readSecrets()
        val previous = loadOrMigrateLlm()
        val previousEntries = previous.optJSONArray("entries") ?: JSONArray()
        val persistence = (0 until previousEntries.length()).mapNotNull { previousEntries.optJSONObject(it) }.associate {
            it.optString("id") to if (it.has("remember_key")) it.optBoolean("remember_key") else previous.optBoolean("rememberKey")
        }
        val activeIds = configs.mapTo(mutableSetOf()) { it.id }.also { ids ->
            readSecrets().keys().asSequence().filter { it.startsWith(WIZARD_SECRET_PREFIX) || it == LEGACY_WIZARD_SECRET }.forEach(ids::add)
        }
        // Session credentials remain usable without opting into disk persistence.
        sessionKeys[sessionNamespace] = sessionKeys[sessionNamespace].orEmpty().filterKeys { it.startsWith(WIZARD_SECRET_PREFIX) || it == LEGACY_WIZARD_SECRET } + configs.associate { it.id to it.key }
        val savedIds = secrets.keys().asSequence().toList()
        savedIds.filterNot(activeIds::contains).forEach(secrets::remove)
        val entries = JSONArray()
        configs.forEach { config ->
            val rememberEntry = if (config.id == activeId) rememberKey else persistence[config.id] ?: rememberKey
            val item = JSONObject().put("id", config.id).put("label", config.label)
                .put("base_url", config.baseUrl).put("model", config.model).put("api", config.api)
                .put("custom", config.custom).put("max_tokens", config.maxTokens ?: JSONObject.NULL)
                .put("input_types", JSONArray(config.inputTypes + "text"))
                .put("context_tokens", config.contextTokens ?: JSONObject.NULL).put("remember_key", rememberEntry)
            if (rememberEntry) {
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
        JSONObject().put("model", config.model).put("api", config.api).put("input_types", JSONArray(config.inputTypes + "text"))
            .put("base_url", config.baseUrl).put("api_key", config.key)
            .also { if (!config.custom) it.put("provider", config.id) }
            .also { if (config.maxTokens != null) it.put("max_tokens", config.maxTokens) }
            .also { if (config.contextTokens != null) it.put("context_tokens", config.contextTokens) }
    }

    /** A guide only activates its selected provider and preserves every other account. */
    internal fun saveProviderConfiguration(config: ProviderConfig, remember: Boolean, theme: String) = synchronized(lock) {
        require(providerSetupGuide(config.id) != null && providerPresetHelp(config) != null)
        val saved = loadOrMigrateLlm()
        val root = readState()
        val oldRoot = root.toString()
        val oldSecrets = readSecrets().toString()
        val secrets = JSONObject(oldSecrets)
        val item = JSONObject().put("id", config.id).put("label", config.label)
            .put("base_url", config.baseUrl).put("model", config.model).put("api", config.api)
            .put("custom", config.custom).put("max_tokens", config.maxTokens ?: JSONObject.NULL)
            .put("input_types", JSONArray(config.inputTypes + "text"))
                .put("context_tokens", config.contextTokens ?: JSONObject.NULL).put("remember_key", remember).put("key", "")
        val entries = saved.optJSONArray("entries") ?: JSONArray()
        val updated = JSONArray()
        var found = false
        for (i in 0 until entries.length()) {
            val existing = entries.optJSONObject(i) ?: continue
            if (existing.optString("id") == config.id) { updated.put(item); found = true } else updated.put(existing)
        }
        if (!found) updated.put(item)
        saved.put("entries", updated).put("activeId", config.id)
        if (!saved.has("rememberKey")) saved.put("rememberKey", remember)
        root.put(LLM_KEY, saved.toString()).put(THEME_KEY, theme)
        if (remember) securePut(secrets, config.id, config.key) else secrets.remove(config.id)
        try { writeSecrets(secrets); writeState(root) }
        catch (error: Exception) {
            runCatching { writeSecrets(JSONObject(oldSecrets)); writeState(JSONObject(oldRoot)) }
            throw error
        }
        sessionKeys[sessionNamespace] = sessionKeys[sessionNamespace].orEmpty() + (config.id to config.key)
    }

    internal fun providerSetupProgress(id: String): ProviderSetupProgress? = synchronized(lock) {
        require(providerSetupGuide(id) != null)
        val root = readState()
        val saved = root.optJSONObject(progressField(id))
            ?: (if (id == "siliconflow") root.optJSONObject("railfan_silicon_setup_v1") else null)
            ?: return null
        ProviderSetupProgress(ProviderSetupStep.restore(saved.optString("step")), saved.optBoolean("rememberKey"), saved.optString("model"))
    }

    internal fun providerSetupKey(id: String): String = synchronized(lock) {
        require(providerSetupGuide(id) != null)
        val key = wizardSecret(id)
        sessionKeys[sessionNamespace]?.get(key) ?: secureGet(key)
            .ifBlank { if (id == "siliconflow") sessionKeys[sessionNamespace]?.get(LEGACY_WIZARD_SECRET) ?: secureGet(LEGACY_WIZARD_SECRET) else "" }
    }

    internal fun saveProviderSetup(id: String, progress: ProviderSetupProgress, key: String) = synchronized(lock) {
        require(providerSetupGuide(id) != null)
        val root = readState()
        val secrets = readSecrets()
        val secret = wizardSecret(id)
        val retained = sessionKeys[sessionNamespace].orEmpty().let { if (id == "siliconflow") it - LEGACY_WIZARD_SECRET else it }
        sessionKeys[sessionNamespace] = retained + (secret to key)
        if (progress.rememberKey && key.isNotBlank()) securePut(secrets, secret, key) else secrets.remove(secret)
        if (id == "siliconflow") secrets.remove(LEGACY_WIZARD_SECRET)
        writeSecrets(secrets)
        root.put(progressField(id), JSONObject().put("step", progress.step.name)
            .put("rememberKey", progress.rememberKey).put("model", progress.model))
        if (id == "siliconflow") root.remove("railfan_silicon_setup_v1")
        writeState(root)
    }

    internal fun clearProviderSetup(id: String) = synchronized(lock) {
        require(providerSetupGuide(id) != null)
        val secret = wizardSecret(id)
        val root = readState().also { it.remove(progressField(id)); if (id == "siliconflow") it.remove("railfan_silicon_setup_v1") }
        val secrets = readSecrets().also { it.remove(secret); if (id == "siliconflow") it.remove(LEGACY_WIZARD_SECRET) }
        sessionKeys[sessionNamespace] = sessionKeys[sessionNamespace].orEmpty() - secret - if (id == "siliconflow") LEGACY_WIZARD_SECRET else ""
        writeSecrets(secrets); writeState(root)
    }

    private fun progressField(id: String) = "railfan_provider_setup_v1_$id"
    private fun wizardSecret(id: String) = "$WIZARD_SECRET_PREFIX$id"

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
        if (legacyKey.isNotBlank()) {
            sessionKeys.getOrPut(sessionNamespace) { emptyMap() }.let { current ->
                sessionKeys[sessionNamespace] = current + (id to legacyKey)
            }
            if (remember) securePut(secrets, id, legacyKey) else secrets.remove(id)
        }
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
        private val sessionLock = Any()
        private val sessionKeys = mutableMapOf<String, Map<String, String>>()
        private const val WIZARD_SECRET_PREFIX = "__setup_provider_v1_"
        private const val LEGACY_WIZARD_SECRET = "__setup_siliconflow_v1"
        private const val LLM_KEY = "railfan_llm_v1"
        private const val THEME_KEY = "railfan_theme"
        private const val SECRETS_FILE = "secrets.json"
        private const val KEY_ALIAS = "railfan-byok-v1"
        private const val TRANSFORM = "AES/GCM/NoPadding"
        private const val IV_BYTES = 12
        private const val TAG_BITS = 128
    }
}
