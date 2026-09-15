// 顶层构建脚本：只声明插件版本，不在这里配置模块。
//
// 刻意不引入 AndroidX / Kotlin / Compose：
//   - UI 用系统自带的 android.webkit.WebView，不需要任何 Web 组件框架；
//   - 少一层依赖 = 更小的 APK、更快的构建、更少的兼容面。
plugins {
    id("com.android.application") version "8.7.3" apply false
    id("com.chaquo.python") version "17.0.0" apply false
}
