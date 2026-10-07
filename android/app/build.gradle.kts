import java.time.OffsetDateTime
import java.util.Properties

plugins {
    id("com.android.application")
    id("com.chaquo.python")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
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
 * 是否把完整本地数据字典（backend/data/dict.db，包含时刻和机位攻略）打进 APK。
 *
 * Main 正式版默认打包；LM 与非 release 任务默认不打包。
 * 可通过 -PincludeDict=true/false 显式覆盖。实际压缩增量以构建产物为准。
 * 正式版使用只读 SQLite 备份纳入已提交的 WAL 数据；安装启动后独立解压，
 * 不直接覆盖运行时词典，由后端按版本合并。
 * 示例：bash scripts/android/build.sh -PincludeDict=true assembleRelease
 */
// 版本号的**唯一来源**：仓库根的 VERSION 文件（前端与后端也读它）。
// 为什么必须统一：同一个 "0.1.1" 曾经对应过好几个内容不同的包，用户无法判断
// 自己装的是哪一版；Android 自身的"应用信息"里也只能看到这个版本号。
val appVersion: String = repoRoot.resolve("VERSION").readText().trim().ifEmpty { "0.0.0" }

// versionCode 由语义化版本导出，保证单调递增（Android 用它判断"是不是升级"）。
// 0.1.2 → 0*10000 + 1*100 + 2 = 102
val appVersionCode: Int = run {
    val seg = appVersion.substringBefore("-").split(".").map { it.trim().toIntOrNull() ?: 0 }
    (seg.getOrElse(0) { 0 } * 10000) + (seg.getOrElse(1) { 0 } * 100) + seg.getOrElse(2) { 0 }
}

val includeDict: Boolean = (project.findProperty("includeDict") as String?)?.toBoolean()
    ?: (project.findProperty("lmLabel") == null && gradle.startParameter.taskNames.any { it.contains("release", ignoreCase = true) })

/**
 * 本地模型版（LM 轨）的独立版本标识：`-PlmLabel=lm1`。
 *
 * 为什么要独立：LM 轨是**内部封测/实验**通道，每次改动都要出一个能彼此区分的新包，
 * 但不能占用正式版本线 —— 改仓库根的 VERSION 会让正式构建跟着跳号；若沿用同一个号，
 * dist/ 里就会出现"同名不同内容"的包（build.sh 注释里明说要消灭的东西）。
 * 因此 LM 轨自带前缀 + 自增数字（lm1 / lm2 / …），与 VERSION 完全解耦。
 *
 * versionCode 取 100000 + 数字，而不是 1/2/3：必须**单调递增且高于正式线**，
 * 否则测试者手机上已有的 debug 包（0.1.6 → 106）会让 Android 拒绝安装更低版本的包。
 */
val lmLabel: String? = (project.findProperty("lmLabel") as String?)?.trim()?.takeIf { it.isNotEmpty() }
val effectiveVersionCode: Int = lmLabel
    ?.let { 100000 + (Regex("(\\d+)$").find(it)?.groupValues?.get(1)?.toIntOrNull() ?: 0) }
    ?: appVersionCode
val effectiveVersionName: String = lmLabel ?: appVersion

/**
 * 内部调试轨（`build.sh --internal`）：把 release 包做成 `debuggable`。
 *
 * 为什么需要：内部调试轨的全部意义是"看得见、改得动"。`debuggable` 才能让
 * `adb shell run-as` 进应用私有目录（llama-server.log、state.json、npu-java-report.txt 都在那儿），
 * 也才能挂 `am profile` / `dumpsys` 这类系统级观测。
 *
 * 为什么只在 release 上开关而不是直接出 debug 包：debug 构建带 `.debug` 包名后缀
 * （见 buildTypes），那会让它变成**另一个应用**，既不能覆盖升级、也不能沿用现有数据。
 * 内部调试轨要的恰恰是"同包名、能直接覆盖已装的 lmN"。
 */
val internalDebug: Boolean =
    (project.findProperty("internalDebug") as String?)?.toBoolean() ?: false

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

    buildFeatures {
        compose = true
    }

    defaultConfig {
        applicationId = "org.openrailfanai.app"
        // WebView 需要能被 Play 更新以获得现代 JS/ES module 支持；24 起覆盖绝大多数在用机型
        minSdk = 24
        targetSdk = 35
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        // 每次出包递增：用户报障时需要能区分版本（LM 轨用自己的 100000+ 计数，见上）
        versionCode = effectiveVersionCode
        versionName = effectiveVersionName
        // Main opens the accepted native UI; LM retains its existing WebView entry.
        resValue("bool", "main_native_ui", (lmLabel == null).toString())

        // 内部调试轨：**独立包名**（见 internalDebug 的注释）。
        // 独立包名让两条轨并存、互不影响，随时能退回 lmN —— 也就不会出现
        // "lm1000 的 versionCode 把回退路堵死"那个不可逆后果。权限注入见文件末尾。
        if (internalDebug) {
            applicationIdSuffix = ".internal"
        }

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
            // 只有内部调试轨打开（见 internalDebug 的注释）。正式包恒为 false。
            isDebuggable = internalDebug
        }
        debug {
            applicationIdSuffix = ".debug"
        }
    }

    compileOptions {
        // Native Main uses java.time for history/date actions on minSdk 24.
        isCoreLibraryDesugaringEnabled = lmLabel == null
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        // 正常版本保持未压缩对齐（体积小、装得快）。
        // **LM 轨必须置 true**：设为 false 时原生库只在 APK 内被 mmap、**不落地成真实文件**，
        // 而设备端推理二进制必须是一个真实且可执行的文件才能被 exec ——
        // Android 10+ 的 W^X 只允许 `nativeLibraryDir`（只读）里的文件被执行。
        // 这一处与 build-llama.sh 把产物命名成 `lib*.so` 是**一对**，改一个必须改另一个。
        jniLibs { useLegacyPackaging = (lmLabel != null) }
        resources { excludes += setOf("META-INF/*.kotlin_module") }
    }

    // 前端静态资源与（可选的）本地字典都以 Android assets 形式随包分发，
    // 由 MainActivity 在首次启动时解包到应用私有目录（Python 的 StaticFiles 需要真实文件）。
    // 注意用 srcDir(...) 方法而不是给 srcDirs 属性赋值 —— 后者是只读 Set<File>。
    sourceSets.getByName("main") {
        assets.srcDir(layout.buildDirectory.dir("staged-assets").get().asFile)
        // 设备端推理二进制只在 LM 轨打进包：正常版本不该为它多背几 MB，
        // 更不该因此被要求把 extractNativeLibs 打开。
        if (lmLabel != null) {
            jniLibs.srcDir("src/lm/jniLibs")
        }
        // 注：`libcdsprpc.so` 的 `<uses-native-library>` 声明放在 **src/main/AndroidManifest.xml**，
        // **不**在这里按 LM 轨分流。原因：`AndroidSourceSet.manifest.srcFile()` 是**替换**
        // 而不是追加 —— 曾经以为它会合并，于是产出了一个没有 label、没有 icon、
        // 连 LAUNCHER 入口都没有的包（lm33：装完桌面没图标、安装器只显示包名）。
        // 那条声明带 `required="false"`，对非骁龙设备是惰性的，放 main 里没有行为代价。
    }
}

// Main software updates must not add installer permissions or providers to the LM track.
if (lmLabel != null) {
    val updateOverlay = layout.buildDirectory.file("generated/manifest/lm-update-exclusions.xml").get().asFile
    updateOverlay.parentFile.mkdirs()
    updateOverlay.writeText("""<manifest xmlns:android="http://schemas.android.com/apk/res/android" xmlns:tools="http://schemas.android.com/tools"><uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" tools:node="remove"/><uses-permission android:name="android.permission.FOREGROUND_SERVICE" tools:node="remove"/><uses-permission android:name="android.permission.FOREGROUND_SERVICE_DATA_SYNC" tools:node="remove"/><uses-permission android:name="android.permission.POST_NOTIFICATIONS" tools:node="remove"/><uses-permission android:name="android.permission.WAKE_LOCK" tools:node="remove"/><application><provider android:name="androidx.core.content.FileProvider" tools:node="remove"/><service android:name="org.openrailfanai.app.UpdateTaskService" tools:node="remove"/></application></manifest>""")
    android.sourceSets.getByName("release").manifest.srcFile(updateOverlay)
    android.sourceSets.getByName("debug").manifest.srcFile(updateOverlay)
}

dependencies {
    if (lmLabel == null) coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.0.3")
    // 固定在 Compose 1.9 系列，兼容当前 compileSdk 35 / AGP 8.7.3。
    val composeBom = platform("androidx.compose:compose-bom:2025.08.00")
    implementation(composeBom)
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("org.commonmark:commonmark:0.24.0")
    implementation("org.commonmark:commonmark-ext-gfm-tables:0.24.0")
    implementation("org.commonmark:commonmark-ext-gfm-strikethrough:0.24.0")
    androidTestImplementation(composeBom)
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    testImplementation("junit:junit:4.13.2")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}

// ---- 把仓库里的前端与后端源码"暂存"到构建目录（避免把 backend/.venv、tests 也打进包）----
val stageBackendPython by tasks.registering(Sync::class) {
    description = "把 backend/app 复制到构建目录，供 Chaquopy 打进 APK"
    from(repoRoot.resolve("backend/app")) {
        into("app")
        if (lmLabel == null) exclude("local_inference.py", "api/local_model.py")
    }
    // 排除字节码缓存：它们是本机产物，进包只会变胖且可能与目标 Python 版本不符
    exclude("**/__pycache__/**", "**/*.pyc")
    inputs.property("lmVariant", lmLabel != null)
    into(layout.buildDirectory.dir("python-staging"))
}

val stageWebApp by tasks.registering(Sync::class) {
    description = "把仓库里的 frontend/ 复制为 assets/webapp"
    from(repoRoot.resolve("frontend")) {
        exclude("tests/**")            // 前端测试脚本不必随包分发
    }
    into(layout.buildDirectory.dir("staged-assets/webapp"))
    // 构建标记依赖 VERSION 与当前提交，**必须声明成输入**：否则 Gradle 判定本任务
    // UP-TO-DATE（它的输入只有 frontend/ 的内容），build.json 不会被重写，标记就会说谎。
    // 实测踩过：把 VERSION 从 0.1.2 改成 0.1.3、工作区干净、重新构建，标记里仍然是
    // "0.1.2 / 0683b89-dirty" —— 一个专门用来消除"我装的是哪一版"疑惑的机制，自己先撒了谎。
    inputs.file(repoRoot.resolve("VERSION"))
    inputs.property("gitHead", gitShortHead())
    // 版本标签也要声明成输入：LM 轨只改 -PlmLabel（VERSION 文件没动），
    // 不声明的话 Gradle 会判 UP-TO-DATE，build.json 里仍写着上一版的标签 ——
    // 正是上面那段注释里"标记自己先撒了谎"的同一个坑，只是换了触发方式。
    inputs.property("versionLabel", effectiveVersionName)
    if (lmLabel != null) inputs.dir(repoRoot.resolve("android/lm-webapp"))
    // 写入构建标记，让"我装的到底是哪一次的包"能直接在界面（设置 → 关于）上看到。
    // 起因：每次修完只能反复叮嘱"需要重新下载"，而静态资源的 URL 跨安装**完全不变**
    // （端口是刻意固定的，为了保住本机状态），光看界面分不出新旧，
    // 用户无从判断重装到底生效了没有。
    doLast {
        val dst = layout.buildDirectory.dir("staged-assets/webapp").get().asFile
        if (lmLabel != null) {
            repoRoot.resolve("android/lm-webapp").copyRecursively(dst, overwrite = true)
        }
        File(dst, "build.json").writeText(
            """{"version":"$effectiveVersionName","commit":"${gitShortHead()}","builtAt":"${buildTimeIso()}"}"""
        )
    }
}

fun gitShortHead(): String = try {
    fun run(vararg args: String): String {
        val proc = ProcessBuilder(*args).directory(repoRoot).redirectErrorStream(true).start()
        return proc.inputStream.bufferedReader().readText().trim()
    }
    val head = run("git", "rev-parse", "--short", "HEAD").ifEmpty { "unknown" }
    // 工作区有未提交改动时标出来：否则"5ffd432"会让人以为这个包就是那次提交的产物，
    // 而实际上它可能带着尚未提交的改动（本次修复正好就是脏树构建）。
    val dirty = run("git", "status", "--porcelain").isNotEmpty()
    if (dirty) "$head-dirty" else head
} catch (e: Exception) {
    "unknown"                              // 没装 git 也不该让构建失败
}

// 注意：这里不能写 `java.time.…` —— 脚本里 `java` 是 Gradle 的 JavaPluginExtension，
// 会把包名遮住（报 Unresolved reference: time）。所以用上面的 import。
fun buildTimeIso(): String = OffsetDateTime.now().withNano(0).toString()

val stageDict by tasks.registering {
    description = "可选：把 backend/data/dict.db 打进 assets（-PincludeDict=true）"
    val src = repoRoot.resolve("backend/data/dict.db")
    val dstDir = layout.buildDirectory.dir("staged-assets/dict").get().asFile
    inputs.property("includeDict", includeDict)
    inputs.property("mainDictionary", lmLabel == null)
    inputs.file(repoRoot.resolve("scripts/android/stage-dictionary.py"))
    if (includeDict) inputs.file(src)
    outputs.dir(dstDir)
    // Committed writes may only change the source WAL, not dict.db itself.
    // Always take a fresh read-only snapshot when including a dictionary.
    outputs.upToDateWhen { !includeDict }
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
            // Read-only SQLite backup includes committed WAL data without modifying the live dictionary.
            if (lmLabel == null) {
                project.exec {
                    val args = mutableListOf(buildPythonPath,
                        repoRoot.resolve("scripts/android/stage-dictionary.py").absolutePath,
                        src.absolutePath, dst.absolutePath)
                    if (gradle.startParameter.taskNames.any { it.contains("release", ignoreCase = true) })
                        args.add("--require-photo-spots")
                    commandLine(args)
                }
            } else {
                project.exec {
                    commandLine(System.getenv("CHAQUOPY_BUILD_PYTHON") ?: "python3", "-c",
                        "import sqlite3,sys,os; src=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); tmp=sys.argv[2]+'.tmp'; os.path.exists(tmp) and os.unlink(tmp); out=sqlite3.connect(tmp); src.backup(out); out.close(); src.close(); os.replace(tmp,sys.argv[2])",
                        src.absolutePath, dst.absolutePath)
                }
            }
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

// ---------------------------------------------------------------- 内部调试轨的 manifest 注入
//
// 为什么不用更"正统"的写法 —— 三条路都实测排除过，别再回头试：
//   1) `sourceSets.main.manifest.srcFile("src/lm/AndroidManifest.xml")`
//      → 是**替换**不是追加（`AndroidSourceFile` 只有 srcFile()/getSrcFile()，
//        没有追加 API，用 javap 在 AGP 8.7.3 的 jar 上确认过）。实测产出了
//        没有 label / 没有 icon / 没有 LAUNCHER 入口的包（lm33）。
//   2) `manifestPlaceholders` + `tools:node="${...}"`
//      → 占位符**不作用于 tools: 属性**，报
//        `No enum constant com.android.manifmerger.NodeOperationType.${ALL_FILES_PERMISSION_NODE}`。
//   3) product flavor
//      → 会让 `assembleRelease` 这个任务名消失（变成 assemble<Flavor>Release），
//        等于改动正式轨的构建入口 —— 而"别碰正式包"是硬约束。
//
// 所以走**构建后注入**：源 manifest **一个字都不改**，
// 正式轨与封测轨的 merged manifest 因此**从构造上**就不可能被碰到（不是"应该不会"）。
// 代价是多一个自定义步骤 —— 用下面的断言把它钉住：
// 注入失败 / 重复注入 / 注错构建类型，都会让构建**当场失败**，而不是悄悄出错包。
//
// 要这份权限的原因：内部调试轨是独立包名（`.internal`），因而看不到另一个包
// `/Android/data/org.openrailfanai.app/files/models/` 里已经下好的模型 ——
// Android 11+ 禁止应用读取**其它包**的 Android/data 目录。
// 有 MANAGE_EXTERNAL_STORAGE 才能按路径直接读那份文件（**原地使用，不拷贝不重下**）。
if (internalDebug) {
    tasks.matching { it.name == "processReleaseMainManifest" }.configureEach {
        doLast {
            val manifest = outputs.files.files.firstOrNull { it.name == "AndroidManifest.xml" }
                ?: throw GradleException("找不到 merged manifest，权限注入无法进行")
            val text = manifest.readText()
            val perm = "android.permission.MANAGE_EXTERNAL_STORAGE"
            if (text.contains(perm)) {
                throw GradleException("$perm 已存在 —— 注入逻辑重复执行了，先查清楚再说")
            }
            check(text.contains("</manifest>")) { "merged manifest 结构异常，拒绝注入" }
            manifest.writeText(text.replace("</manifest>",
                "    <uses-permission android:name=\"$perm\" />\n</manifest>"))
            logger.lifecycle("内部调试轨：已注入 $perm（正式/封测轨不受影响）")
        }
    }
}
