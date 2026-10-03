// 顶层构建脚本：只声明插件版本，不在这里配置模块。
//
// Kotlin 与 Compose 仅用于 Main Android 原生界面迁移；正式入口切换留到 CT9。
plugins {
    id("com.android.application") version "8.7.3" apply false
    id("com.chaquo.python") version "17.0.0" apply false
    id("org.jetbrains.kotlin.android") version "2.2.21" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.2.21" apply false
}
