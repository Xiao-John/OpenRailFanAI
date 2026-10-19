package org.openrailfanai.app

import android.app.Activity
import android.content.Context
import android.os.Bundle
import android.provider.Browser
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import androidx.browser.customtabs.CustomTabColorSchemeParams
import androidx.browser.customtabs.CustomTabsClient
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.ui.graphics.toArgb

/** Browser-owned authentication: no WebView, script injection, credential access or login inference. */
internal object SiliconGuideBrowser {
    fun open(activity: Activity, url: String) {
        val uri = Uri.parse(url)
        require(uri.scheme == "https" && uri.host in setOf("cloud.siliconflow.cn", "account.siliconflow.cn", "www.siliconflow.cn", "siliconflow.cn")) {
            "向导只能打开硅基流动的官方 HTTPS 页面"
        }
        AppLinkBrowser.open(activity, uri.buildUpon().appendQueryParameter("locale", "zh-CN").build().toString(), "zh-CN")
    }
}

internal object ProviderGuideBrowser {
    fun open(activity: Activity, url: String) {
        val uri = Uri.parse(url)
        val allowed = providerSetupGuides().flatMap { guide ->
            listOfNotNull(guide.accountUrl, guide.accessUrl, guide.keyUrl, guide.authUrl)
        }.map { Uri.parse(it).host }.toSet()
        require(uri.scheme == "https" && uri.host in allowed) { "向导只能打开预设的官方 HTTPS 页面" }
        if (uri.host == "cloud.siliconflow.cn") SiliconGuideBrowser.open(activity, url)
        else AppLinkBrowser.open(activity, url, "zh-CN")
    }
}

/** Shared browser entry for public chat sources and Markdown links. */
internal object AppLinkBrowser {
    fun open(context: Context, url: String, language: String? = null) {
        val uri = Uri.parse(url)
        require(uri.scheme in setOf("https", "http") && !uri.host.isNullOrBlank()) { "仅支持网页链接" }
        val browser = CustomTabsClient.getPackageName(context, null)
        if (browser != null) {
            val colors = CustomTabColorSchemeParams.Builder()
                .setToolbarColor(NativeColors.surface.toArgb())
                .setNavigationBarColor(NativeColors.background.toArgb())
                .build()
            val tab = CustomTabsIntent.Builder()
                .setDefaultColorSchemeParams(colors)
                .setColorScheme(if (NativeColors.dark) CustomTabsIntent.COLOR_SCHEME_DARK else CustomTabsIntent.COLOR_SCHEME_LIGHT)
                .setShowTitle(true)
                .setShareState(CustomTabsIntent.SHARE_STATE_OFF)
                .build()
            tab.intent.setPackage(browser)
            if (context !is Activity) tab.intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if (language != null) tab.intent.putExtra(Browser.EXTRA_HEADERS, Bundle().apply { putString("Accept-Language", language) })
            try {
                tab.launchUrl(context, uri)
                return
            } catch (_: ActivityNotFoundException) {
                // The chosen browser may have been removed or disabled after discovery.
            } catch (_: SecurityException) {
                // An unavailable browser component must not trap the configuration flow.
            }
        }
        val fallback = Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE)
        if (context !is Activity) fallback.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (language != null) fallback.putExtra(Browser.EXTRA_HEADERS, Bundle().apply { putString("Accept-Language", language) })
        context.startActivity(fallback)
    }
}
