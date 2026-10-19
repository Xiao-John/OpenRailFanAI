package org.openrailfanai.app

import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class MainSettingsRepositoryTest {
    @Test fun providerGuidesKeepDraftsSeparateAndNeverPersistSessionOnlyKeys() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val dir = File(context.cacheDir, "wizard-isolation-${System.nanoTime()}").apply { mkdirs() }
        try {
            val repo = MainSettingsRepository(dir, isolated = true)
            repo.saveProviderSetup("deepseek", ProviderSetupProgress(ProviderSetupStep.KEY, false, "model-a"), "draft-a-secret")
            repo.saveProviderSetup("gemini", ProviderSetupProgress(ProviderSetupStep.CREDIT, false, "model-b"), "draft-b-secret")
            val restored = MainSettingsRepository(dir, isolated = true)
            assertEquals("draft-a-secret", restored.providerSetupKey("deepseek"))
            assertEquals("draft-b-secret", restored.providerSetupKey("gemini"))
            assertEquals(ProviderSetupStep.KEY, restored.providerSetupProgress("deepseek")?.step)
            assertEquals(ProviderSetupStep.CREDIT, restored.providerSetupProgress("gemini")?.step)
            assertFalse(File(dir, "state.json").readText().contains("secret"))
            assertFalse(File(dir, "secrets.json").readText().contains("draft-"))
            repo.clearProviderSetup("deepseek")
            assertEquals("", restored.providerSetupKey("deepseek"))
            assertNull(restored.providerSetupProgress("deepseek"))
            assertEquals("draft-b-secret", restored.providerSetupKey("gemini"))
        } finally { dir.deleteRecursively() }
    }

    @Test fun guideSavePreservesOtherProviderAndEncryptedDraft() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val dir = File(context.cacheDir, "wizard-save-${System.nanoTime()}").apply { mkdirs() }
        try {
            val repo = MainSettingsRepository(dir, isolated = true)
            val original = ProviderConfig("deepseek", "DeepSeek", "https://api.deepseek.com", "fixture-a", key = "original-secret")
            repo.save(listOf(original), original.id, true)
            repo.saveProviderSetup("siliconflow", ProviderSetupProgress(ProviderSetupStep.KEY, true, "draft-model"), "draft-encrypted-secret")
            val next = ProviderConfig("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "fixture-b", key = "new-session-secret")
            repo.saveProviderConfiguration(next, false, "auto")
            repo.save(repo.entries(), next.id, false)
            assertEquals("original-secret", repo.entries().first { it.id == original.id }.key)
            assertTrue(repo.rememberKeyForProvider(original.id))
            assertFalse(repo.rememberKeyForProvider(next.id))
            assertEquals("new-session-secret", repo.requestLlmSpec()?.getString("api_key"))
            assertEquals("draft-encrypted-secret", repo.providerSetupKey("siliconflow"))
            val persisted = File(dir, "state.json").readText() + File(dir, "secrets.json").readText()
            listOf("original-secret", "new-session-secret", "draft-encrypted-secret").forEach { assertFalse(persisted.contains(it)) }
        } finally { dir.deleteRecursively() }
    }

    @Test fun legacySiliconProgressMigratesWithoutAffectingAnotherGuide() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val dir = File(context.cacheDir, "wizard-migration-${System.nanoTime()}").apply { mkdirs() }
        try {
            File(dir, "state.json").writeText("""{"railfan_silicon_setup_v1":{"step":"KEY","rememberKey":false,"model":"old-model"}}""")
            val repo = MainSettingsRepository(dir, isolated = true)
            assertEquals("old-model", repo.providerSetupProgress("siliconflow")?.model)
            assertNull(repo.providerSetupProgress("deepseek"))
            repo.saveProviderSetup("siliconflow", repo.providerSetupProgress("siliconflow")!!, "")
            assertFalse(JSONObject(File(dir, "state.json").readText()).has("railfan_silicon_setup_v1"))
            assertEquals(ProviderSetupStep.KEY, repo.providerSetupProgress("siliconflow")?.step)
        } finally { dir.deleteRecursively() }
    }
    @Test fun savePreservesOldStateAndUsesExistingKeystoreContract() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val dir = File(context.cacheDir, "settings-fixture-${System.nanoTime()}").apply { mkdirs() }
        try {
            File(dir, "state.json").writeText("""{"railfan_conversations_v1":"[{\"id\":\"old-session\"}]"}""")
            val repo = MainSettingsRepository(dir, isolated = true)
            val secret = "fixture-secret-never-log"
            val provider = ProviderConfig("fixture-provider", "Fixture", "https://example.invalid/v1", "test-model", key = secret)
            repo.save(listOf(provider), provider.id, rememberKey = true, theme = "light")

            val state = JSONObject(File(dir, "state.json").readText())
            assertEquals("[{\"id\":\"old-session\"}]", state.getString("railfan_conversations_v1"))
            assertEquals("light", state.getString("railfan_theme"))
            assertFalse(state.toString().contains(secret))
            assertEquals(secret, repo.entries().single().key)
            assertEquals(provider.id, repo.activeId())
        } finally {
            dir.deleteRecursively()
        }
    }

    @Test fun unsavedKeyRemainsAvailableAcrossRepositoriesWithoutDiskPersistence() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val dir = File(context.cacheDir, "settings-session-${System.nanoTime()}").apply { mkdirs() }
        try {
            val provider = ProviderConfig("session-provider", "会话提供商", "https://example.invalid/v1", "test-model", key = "session-secret")
            val repo = MainSettingsRepository(dir, isolated = true)
            repo.save(listOf(provider), provider.id, rememberKey = false)
            val newRepository = MainSettingsRepository(dir, isolated = true)
            assertEquals("session-secret", newRepository.entries().single().key)
            assertEquals("session-secret", newRepository.requestLlmSpec()?.getString("api_key"))
            assertFalse(File(dir, "state.json").readText().contains("session-secret"))
            assertFalse(File(dir, "secrets.json").readText().contains("session-secret"))
            assertFalse(newRepository.rememberKey())
            repo.save(emptyList(), "", rememberKey = false)
            assertEquals(emptyList<ProviderConfig>(), newRepository.entries())
        } finally { dir.deleteRecursively() }
    }

    @Test fun migratesLegacyProviderAndEncryptsItsSavedKey() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val dir = File(context.cacheDir, "settings-legacy-${System.nanoTime()}").apply { mkdirs() }
        try {
            val legacy = JSONObject().put("provider", "custom").put("base_url", "https://example.invalid/v1")
                .put("model", "legacy-model").put("api", "chat_completions").put("rememberKey", true)
                .put("keys", JSONObject().put("custom", "legacy-secret"))
            File(dir, "state.json").writeText(JSONObject().put("railfan_llm_v1", legacy.toString()).toString())
            val repo = MainSettingsRepository(dir, isolated = true)
            val migrated = repo.entries().single()
            assertEquals("custom", migrated.id)
            assertEquals("legacy-secret", migrated.key)
            assertEquals("legacy-model", migrated.model)
            val persisted = JSONObject(File(dir, "state.json").readText())
            assertFalse(persisted.toString().contains("legacy-secret"))
            assertFalse(repo.requestLlmSpec()?.has("provider") == true)
        } finally { dir.deleteRecursively() }
    }
}
