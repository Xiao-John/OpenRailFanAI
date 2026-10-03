package org.openrailfanai.app

/** App pages hosted by MainComposeActivity, ordered from root to current. */
internal enum class MainPage {
    CHAT,
    HISTORY,
    SETTINGS,
}

/** Immutable navigation state with a small back stack and no Android dependencies. */
internal class MainPageNavigation private constructor(entries: List<MainPage>) {
    private val entries = entries.toList()
    val current: MainPage get() = entries.last()
    val canGoBack: Boolean get() = entries.size > 1

    fun navigateTo(page: MainPage): MainPageNavigation =
        if (page == current) this else MainPageNavigation(entries + page)

    /** Returns null when the root page has no in-app page to return to. */
    fun pop(): MainPageNavigation? =
        if (canGoBack) MainPageNavigation(entries.dropLast(1)) else null

    fun savedPages(): List<String> = entries.map(MainPage::name)

    companion object {
        fun root(): MainPageNavigation = MainPageNavigation(listOf(MainPage.CHAT))

        fun restore(savedPages: List<String>?): MainPageNavigation {
            val restored = savedPages.orEmpty().mapNotNull { name ->
                MainPage.entries.firstOrNull { it.name == name }
            }
            return if (restored.firstOrNull() == MainPage.CHAT) {
                MainPageNavigation(restored)
            } else {
                root()
            }
        }
    }
}
