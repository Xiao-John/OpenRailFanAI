package org.openrailfanai.app

import android.content.Context
import android.content.Intent
import android.content.ClipData
import android.content.pm.PackageManager
import android.os.Build
import android.net.Uri
import android.provider.Settings
import androidx.core.content.FileProvider
import java.io.File

internal object SoftwareInstaller {
    fun installedVersion(context: Context): String = context.packageManager.getPackageInfo(context.packageName, 0).versionName.orEmpty()
    @Suppress("DEPRECATION")
    fun verify(context: Context, file: File, latest: String) {
        val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
        val archive = context.packageManager.getPackageArchiveInfo(file.absolutePath, flags) ?: error("无法读取安装包")
        val installed = context.packageManager.getPackageInfo(context.packageName, flags)
        require(archive.packageName == context.packageName && archive.versionName == latest) { "安装包身份或版本不匹配" }
        val archiveCode = if (Build.VERSION.SDK_INT >= 28) archive.longVersionCode else archive.versionCode.toLong()
        val installedCode = if (Build.VERSION.SDK_INT >= 28) installed.longVersionCode else installed.versionCode.toLong()
        require(archiveCode > installedCode) { "安装包不是当前应用的更高版本" }
        val installedSigners = if (Build.VERSION.SDK_INT >= 28) installed.signingInfo?.signingCertificateHistory else installed.signatures
        val archiveSigners = if (Build.VERSION.SDK_INT >= 28) archive.signingInfo?.apkContentsSigners else archive.signatures
        require(!archiveSigners.isNullOrEmpty() && !installedSigners.isNullOrEmpty() && archiveSigners.all { candidate -> installedSigners.any { it == candidate } }) { "安装包签名与当前应用不匹配" }
    }
    /** Returns a truthful status; installation itself belongs to Android. */
    fun launch(context: Context, file: File, latest: String): String {
        verify(context, file, latest)
        if (Build.VERSION.SDK_INT >= 26 && !context.packageManager.canRequestPackageInstalls()) {
            context.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}")))
            return "请允许此应用安装更新，返回后再次点击安装。"
        }
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.updates", file)
        context.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION).also { it.clipData = ClipData.newRawUri("软件更新", uri) })
        return "已打开系统安装器，请在系统界面完成安装。"
    }
}
