package org.openrailfanai.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MainPageNavigationTest {
    @Test
    fun chatHistoryConversationReturnsToHistory() {
        val selectedConversation = MainPageNavigation.root()
            .navigateTo(MainPage.HISTORY)
            .navigateTo(MainPage.CHAT)

        assertEquals(MainPage.CHAT, selectedConversation.current)
        assertEquals(MainPage.HISTORY, selectedConversation.pop()?.current)
    }

    @Test
    fun creatingConversationFromHistoryAlsoReturnsToHistory() {
        val createdConversation = MainPageNavigation.root()
            .navigateTo(MainPage.HISTORY)
            .navigateTo(MainPage.CHAT)

        assertEquals(MainPage.HISTORY, createdConversation.pop()?.current)
    }

    @Test
    fun historySettingsBackReturnsToHistoryThenChat() {
        val settings = MainPageNavigation.root()
            .navigateTo(MainPage.HISTORY)
            .navigateTo(MainPage.SETTINGS)

        val history = settings.pop()
        assertEquals(MainPage.HISTORY, history?.current)
        assertEquals(MainPage.CHAT, history?.pop()?.current)
    }

    @Test
    fun settingsOpenedFromChatReturnsToChat() {
        val settings = MainPageNavigation.root().navigateTo(MainPage.SETTINGS)

        assertEquals(MainPage.CHAT, settings.pop()?.current)
    }

    @Test
    fun rootChatHasNoInAppBackDestination() {
        val root = MainPageNavigation.root()

        assertEquals(MainPage.CHAT, root.current)
        assertFalse(root.canGoBack)
        assertNull(root.pop())
    }

    @Test
    fun navigatingToCurrentPageDoesNotAddDuplicateEntry() {
        val chat = MainPageNavigation.root().navigateTo(MainPage.CHAT)

        assertFalse(chat.canGoBack)
        assertTrue(chat.pop() == null)
    }

    @Test
    fun restoredStackPreservesPageAndBackDestination() {
        val settings = MainPageNavigation.root()
            .navigateTo(MainPage.HISTORY)
            .navigateTo(MainPage.SETTINGS)

        val restored = MainPageNavigation.restore(settings.savedPages())

        assertEquals(MainPage.SETTINGS, restored.current)
        assertEquals(MainPage.HISTORY, restored.pop()?.current)
    }

    @Test
    fun invalidSavedStackFallsBackToRoot() {
        val restored = MainPageNavigation.restore(listOf("SETTINGS", "HISTORY"))

        assertEquals(MainPage.CHAT, restored.current)
        assertFalse(restored.canGoBack)
    }
}
