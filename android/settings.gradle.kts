pluginManagement {
    repositories {
        // Chaquopy 的 Gradle 插件发布在 Maven Central（17.0.0 起）
        mavenCentral()
        google()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "OpenRailFanAI"
include(":app")
