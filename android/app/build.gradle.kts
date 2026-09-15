import java.util.Properties

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

// Chaquopy 需要**与 App 同主次版本**的本机 Python 来生成部分产物。
// 优先用仓库自带的 venv（Python 3.12），其次读 local.properties / 环境变量。
// 注意：变量名不能叫 buildPython —— 会与 Chaquopy DSL 自身的同名属性（List<String>?）冲突。
val buildPythonPath: String = run {
    System.getenv("CHAQUOPY_BUILD_PYTHON")?.takeIf { it.isNotBlank() }?.let { return@run it }
    val props = Properties().apply {
        val f = rootProject.file("local.properties")
        if (f.exists()) f.inputStream().use { load(it) }
    }
    props.getProperty("chaquopy.buildPython")?.let { return@run it }
    val candidates = listOf(
        rootProject.projectDir.parentFile.resolve("backend/.venv/bin/python3.12"),
        rootProject.projectDir.parentFile.resolve("backend/.venv/bin/python3"),
    )
    candidates.firstOrNull { it.exists() }?.absolutePath ?: "python3"
}

val repoRoot: File = rootProject.projectDir.parentFile

/**
 * 是否把本地数据字典（backend/data/dict.db，约 14MB）打进 APK。
 *
 * 默认**不打**：它会让 APK 从 ~25MB 涨到 ~40MB，而"尽可能轻量化"是本版本的明确目标。
 * 不带字典时，依赖字典的工具（rail.mileage / 车站档案 / 离线时刻）会如实报告不可用，
 * 其余能力（12306 实时查询、交路、站序等）不受影响。
 * 需要完整功能时：bash scripts/android/build.sh -PincludeDict=true assembleRelease
 */
val includeDict: Boolean = (project.findProperty("includeDict") as String?)?.toBoolean() ?: false

/**
 * release 签名配置：android/keystore.properties 存在时才启用
 * （由 scripts/android/make-keystore.sh 生成，含口令，已 gitignore）。
 *
 * 不把口令写进构建脚本：仓库要公开，且 CI 上会通过环境变量/密钥管理注入。
 * 没有该文件时 release 产物是未签名包（可构建、不可安装），这是刻意的：
 * 宁可能构建出未签名包并给出提示，也不要静默用调试密钥签正式包
 * —— 那样发出去的包装不上、也无法升级。
 */
val keystorePropsFile: File = rootProject.file("keystore.properties")
val keystoreProps = Properties().apply {
    if (keystorePropsFile.exists()) keystorePropsFile.inputStream().use { load(it) }
}
val hasReleaseKey: Boolean = keystoreProps.getProperty("storeFile") != null

android {
    namespace = "org.openrailfanai.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "org.openrailfanai.app"
        // WebView 需要能被 Play 更新以获得现代 JS/ES module 支持；24 起覆盖绝大多数在用机型
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        // 只打 arm64：原生库（CPython 运行时）体积直接减半。
        // 需要覆盖 32 位老机时在此追加 "armeabi-v7a"。
        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    signingConfigs {
        if (hasReleaseKey) {
            create("release") {
                storeFile = file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            // 不混淆：Python 侧逻辑不走 Java 反射，收益极小，反而让排障变难
            isMinifyEnabled = false
            isShrinkResources = false
            signingConfig = signingConfigs.findByName("release")
        }
        debug {
            applicationIdSuffix = ".debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        jniLibs { useLegacyPackaging = false }   // 原生库保持未压缩对齐
        resources { excludes += setOf("META-INF/*.kotlin_module") }
    }

    // 前端静态资源与（可选的）本地字典都以 Android assets 形式随包分发，
    // 由 MainActivity 在首次启动时解包到应用私有目录（Python 的 StaticFiles 需要真实文件）。
    // 注意用 srcDir(...) 方法而不是给 srcDirs 属性赋值 —— 后者是只读 Set<File>。
    sourceSets.getByName("main") {
        assets.srcDir(layout.buildDirectory.dir("staged-assets").get().asFile)
    }
}

// ---- 把仓库里的前端与后端源码"暂存"到构建目录（避免把 backend/.venv、tests 也打进包）----
val stageBackendPython by tasks.registering(Copy::class) {
    description = "把 backend/app 复制到构建目录，供 Chaquopy 打进 APK"
    from(repoRoot.resolve("backend/app")) { into("app") }
    // 排除字节码缓存：它们是本机产物，进包只会变胖且可能与目标 Python 版本不符
    exclude("**/__pycache__/**", "**/*.pyc")
    into(layout.buildDirectory.dir("python-staging"))
}

val stageWebApp by tasks.registering(Copy::class) {
    description = "把仓库里的 frontend/ 复制为 assets/webapp"
    from(repoRoot.resolve("frontend")) {
        exclude("tests/**")            // 前端测试脚本不必随包分发
    }
    into(layout.buildDirectory.dir("staged-assets/webapp"))
}

val stageDict by tasks.registering {
    description = "可选：把 backend/data/dict.db 打进 assets（-PincludeDict=true）"
    val src = repoRoot.resolve("backend/data/dict.db")
    val dstDir = layout.buildDirectory.dir("staged-assets/dict").get().asFile
    inputs.property("includeDict", includeDict)
    outputs.dir(dstDir)
    doLast {
        dstDir.mkdirs()
        val dst = File(dstDir, "dict.db")
        if (includeDict) {
            if (!src.isFile()) {
                throw GradleException(
                    "includeDict=true 但找不到 ${src.absolutePath}；" +
                        "请先运行 scripts/mirror_dict.py 生成，或不带该开关构建。"
                )
            }
            src.copyTo(dst, overwrite = true)
            logger.lifecycle("已打包本地字典：${dst.length() / 1024 / 1024} MB")
        } else {
            // 明确删除：避免上一次带字典构建的残留被这次打包进去
            if (dst.exists()) dst.delete()
            logger.lifecycle("未打包本地字典（如需完整功能：-PincludeDict=true）")
        }
    }
}

// preBuild 是所有构建路径的公共前置，但**不够**：Gradle 的有效性校验要求
// "消费某个任务输出的任务"自己声明依赖，否则报 implicit dependency 直接构建失败。
tasks.named("preBuild") {
    dependsOn(stageBackendPython, stageWebApp, stageDict)
}
tasks.matching { it.name.matches(Regex("merge.*PythonSources")) }.configureEach {
    dependsOn(stageBackendPython)
}
tasks.matching { it.name.matches(Regex("merge.*Assets")) }.configureEach {
    dependsOn(stageWebApp, stageDict)
}

// ---- Python 运行时与依赖（Chaquopy）----
chaquopy {
    sourceSets {
        getByName("main") {
            // Android 侧的启动器与兼容层
            srcDir("src/main/python")
            // 真实后端（暂存副本）：app 包
            srcDir(layout.buildDirectory.dir("python-staging").get().asFile.absolutePath)
        }
    }
    defaultConfig {
        version = "3.12"                 // 与 backend/.venv 一致，便于本地跑同一套测试
        buildPython = listOf(buildPythonPath)

        pip {
            // **关键**：--no-deps。
            //   - pydantic 必须是 v1（Android 无 pydantic-core 轮子），而
            //     mcp-server-12306 声明依赖 pydantic-settings(要求 pydantic>=2)，
            //     正常依赖解析必然失败；
            //   - openai 声明依赖 jiter(Rust, Android 无轮子)，同样装不上。
            // 因此闭包由 android/requirements.txt 显式锁定（全部纯 Python），
            // 缺失的两个包由 backend/app/_compat.py 提供替身。
            options("--no-deps")
            install("-r", "../requirements.txt")
        }
    }
}
