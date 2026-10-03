package org.openrailfanai.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.ComponentName;
import android.content.ServiceConnection;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import android.content.pm.PackageInfo;
import android.content.res.AssetManager;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.os.Process;
import android.os.IBinder;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import android.util.Log;
import android.view.View;
import android.view.WindowInsets;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * 唯一的 Activity：把设备内的 Python 后端跑起来，再用**系统自带**的 WebView 加载它。
 *
 * 一体化：无远程服务端，也不引入任何 Web 组件框架（Capacitor/Cordova/RN），
 * UI 就是平台自带的 android.webkit.WebView。
 *
 * 设计上的一条硬要求：**任何失败都必须显示在屏幕上**。
 * 上一版在 WebView 加载失败时只会呈现一片白屏（自定义 WebViewClient 会吞掉系统错误页），
 * 而真机排障时用户拿不到 logcat —— 所以这里把启动过程做成可见日志，
 * 并把 WebView 的网络/HTTP 错误、以及前端 JS 的运行期错误都显示出来。
 */
public class MainActivity extends Activity implements BackendService.Listener {

    private static final String TAG = "RailFanAI";

    private static final String ASSET_WEBAPP = "webapp";
    private static final String ASSET_DICT = "dict";

    private WebView web;
    private TextView logView;
    private TextView hintView;
    private final StringBuilder bootLog = new StringBuilder();
    private volatile boolean pageLoaded = false;
    private BackendService backendService;
    private boolean backendBound;
    private final ServiceConnection backendConnection = new ServiceConnection() {
        @Override public void onServiceConnected(ComponentName name, IBinder binder) {
            backendService = ((BackendService.LocalBinder) binder).service();
            backendService.addListener(MainActivity.this);
        }
        @Override public void onServiceDisconnected(ComponentName name) { backendService = null; }
    };

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // Keep the installed launcher component stable while Main moves to Compose.
        // The build-time resource leaves LM on its original path. An explicit intent
        // provides the retained WebView fallback without adding a product UI toggle.
        if (getResources().getBoolean(R.bool.main_native_ui)
                && !getIntent().getBooleanExtra("railfan_web_fallback", false)) {
            startActivity(new Intent(this, MainComposeActivity.class)
                    .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP));
            finish();
            return;
        }
        Log.i(TAG, "启动一体化运行环境");

        FrameLayout root = new FrameLayout(this);

        // 把整块界面收进安全区：顶部（状态栏 / 挖孔）与底部（手势条）都不占用。
        //
        // 为什么必须在 Java 里做，而不是只靠 CSS 的 env(safe-area-inset-*)：
        // Android 15（API 35）对 targetSdk 35 的应用**强制边到边**，内容会铺到状态栏
        // 与手势条底下；而 WebView 的 env(safe-area-inset-*) 在 Android 上只反映
        // **挖孔**——没有挖孔的机器会得到 0，于是按钮又贴回状态栏。按 window insets
        // 缩进才是通吃的做法。
        // 症状就是用户报的：「＋ 新对话」贴着系统栏，既难点中、也不该被遮住。
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            root.setOnApplyWindowInsetsListener((v, insets) -> {
                android.graphics.Insets bars = insets.getInsets(
                        WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                // 底部还要算上输入法：边到边模式下 `adjustResize` **不再自动压缩窗口**
                // （实测键盘弹起时本窗口 frame 仍是 0,0-1080,2400），底部输入框会被键盘
                // 整个盖住。把输入法 insets 并进底部内边距，键盘弹起时视图才真的让位。
                android.graphics.Insets ime = insets.getInsets(WindowInsets.Type.ime());
                v.setPadding(bars.left, bars.top, bars.right,
                        Math.max(bars.bottom, ime.bottom));
                return insets;
            });
        }

        // ---- 启动/诊断面板：失败时它就是唯一的排障入口 ----
        // 颜色**写死**，不跟随主题：这块面板是"白屏时唯一能看见的东西"，
        // 一旦随主题变成深底深字就等于白屏，而白色窗口底在冷启动时又会闪一下。
        final int panelBg = 0xFF101216;
        final int panelFg = 0xFFE6E9EF;
        root.setBackgroundColor(panelBg);

        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(40, 56, 40, 40);

        hintView = new TextView(this);
        hintView.setTextSize(17f);
        hintView.setTypeface(Typeface.DEFAULT_BOLD);
        hintView.setTextColor(panelFg);
        hintView.setText("正在启动本地服务…");
        panel.addView(hintView);

        logView = new TextView(this);
        logView.setTextSize(12f);
        logView.setTypeface(Typeface.MONOSPACE);
        logView.setPadding(0, 20, 0, 20);
        logView.setTextColor(panelFg);
        logView.setTextIsSelectable(true);      // 方便用户复制给我们排查
        panel.addView(logView);

        Button retry = new Button(this);
        retry.setText("重试");
        retry.setOnClickListener(v -> {
            if (backendService != null) backendService.retry();
            else startService(new Intent(this, BackendService.class));
        });
        panel.addView(retry);

        ScrollView scroller = new ScrollView(this);
        scroller.addView(panel);
        root.addView(scroller, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        // ---- WebView ----
        web = new WebView(this);
        web.setVisibility(View.GONE);
        // 仅在 debuggable 构建里开启 WebView 远程调试：可用 CDP 在真机/模拟器上直接执行
        // 任意 JS（查布局、量尺寸、模拟点击），比"改一次探针就重装一次包"高效得多。
        // 用 ApplicationInfo 的 debuggable 标志判断，无需为 BuildConfig 开启 buildConfig 特性。
        boolean debuggable =
            (getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0;
        if (debuggable) {
            WebView.setWebContentsDebuggingEnabled(true);
            Log.i(TAG, "已开启 WebView 远程调试（debug 构建）");
        }
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);        // 桥不可用时的退路：前端会退回 localStorage
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setAllowFileAccess(false);         // 页面只来自本机 HTTP，不需要文件访问
        s.setAllowContentAccess(false);

        // 接原生桥：前端通过 window.RailNative 调用（契约见 frontend/src/native.js）。
        //
        // 为什么非要有它：WebView 的 localStorage 按**源**隔离，而源里含端口
        // （http://127.0.0.1:<port>）。后端端口一旦变化，整个应用在浏览器眼里就成了
        // 另一个站点 —— 对话、供应商配置、记住的 Key 全部读不到，用户看到的是"数据没了"。
        // 桥把状态写进应用私有目录，与端口无关；顺带提供剪贴板（粘贴 Key 用）、分享，
        // 以及把 Key 交给 Android Keystore 加密，而不是明文躺在 localStorage 里。
        //
        // 安全前提：外链一律交给系统浏览器（见 openExternally 与 shouldOverrideUrlLoading），
        // WebView 永远停在 127.0.0.1 上，因此**不存在第三方页面能调到本桥的路径**。
        // 将来若允许 WebView 内打开第三方页面，必须先重新评估这个前提。
        web.addJavascriptInterface(new RailBridge(), "RailNative");

        // target="_blank" 的链接不会走 shouldOverrideUrlLoading，必须由 onCreateWindow 接住，
        // 否则同样是"点了没反应"（前端那个 GitHub 链接就带 _blank）。
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onCreateWindow(WebView view, boolean isDialog,
                                          boolean isUserGesture, android.os.Message resultMsg) {
                // 不为新窗口建 WebView：取到目标地址后交给系统浏览器
                WebView probe = new WebView(MainActivity.this);
                probe.setWebViewClient(new WebViewClient() {
                    @Override
                    public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
                        openExternally(req.getUrl().toString());
                        return true;
                    }
                });
                ((WebView.WebViewTransport) resultMsg.obj).setWebView(probe);
                resultMsg.sendToTarget();
                return true;
            }
        });

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String host = request.getUrl().getHost();
                if (host != null && (host.equals("127.0.0.1") || host.equals("localhost"))) {
                    return false;               // 本地页面：WebView 内处理
                }
                // 外部链接**交给系统浏览器打开**。
                // 早期版本直接 return true 把跳转吞掉，结果「联系我们」里的
                // GitHub issue 链接点了毫无反应（用户实测反馈）。
                openExternally(request.getUrl().toString());
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                // 只有主页面真正加载完成才撤掉诊断面板，避免"白屏且无任何提示"
                if (!pageLoaded) {
                    pageLoaded = true;
                    boot("页面加载完成：" + url);
                    showWebView();
                    probeRenderedPage(view);
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        WebResourceError error) {
                if (request.isForMainFrame()) {
                    fail("WebView 无法加载主页面：\n  URL: " + request.getUrl()
                            + "\n  错误码: " + error.getErrorCode()
                            + "\n  描述: " + error.getDescription());
                }
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request,
                                            WebResourceResponse response) {
                // 静态资源 404 不会触发 onReceivedError，只会走这里 —— 上一版
                // index.html 里 ./src/main.js 404 就是这种情况：页面在、脚本全无。
                String msg = "HTTP " + response.getStatusCode() + "：" + request.getUrl();
                boot(msg);
                if (request.isForMainFrame()) {
                    fail("主页面返回异常：\n  " + msg);
                }
            }
        });
        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);

        Intent backend = new Intent(this, BackendService.class);
        startService(backend);
        backendBound = bindService(backend, backendConnection, Context.BIND_AUTO_CREATE);
    }

    /** 用系统浏览器打开外部链接；没有可用浏览器时给出可见提示而不是静默失败。 */
    private void openExternally(String url) {
        try {
            android.content.Intent it = new android.content.Intent(
                    android.content.Intent.ACTION_VIEW, android.net.Uri.parse(url));
            it.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(it);
            boot("已在系统浏览器打开：" + url);
        } catch (Exception e) {
            Log.w(TAG, "打开外部链接失败：" + url, e);
            boot("无法打开外部链接（设备上没有可用浏览器）：" + url
                    + "\n可长按链接或使用「复制链接」按钮。");
        }
    }

    private void showWebView() {
        runOnUiThread(() -> {
            logView.setVisibility(View.GONE);
            hintView.setVisibility(View.GONE);
            web.setVisibility(View.VISIBLE);
        });
    }

    /** 追加一行启动日志（显示在屏幕上，同时写 logcat）。 */
    private void boot(String line) {
        Log.i(TAG, line);
        runOnUiThread(() -> {
            if (bootLog.length() > 0) bootLog.append('\n');
            bootLog.append(line);
            logView.setText(bootLog.toString());
        });
    }

    /** 致命失败：保持诊断面板可见，并把原因写清楚。 */
    private void fail(String detail) {
        Log.e(TAG, detail);
        runOnUiThread(() -> {
            hintView.setText("启动失败");
            if (bootLog.length() > 0) bootLog.append('\n');
            bootLog.append("\n").append(detail);
            logView.setText(bootLog.toString());
            logView.setVisibility(View.VISIBLE);
            hintView.setVisibility(View.VISIBLE);
            web.setVisibility(View.GONE);
        });
    }

    private void bootstrap() {
        try {
            File webappDir = new File(getFilesDir(), "webapp");
            File dataDir = getFilesDir();

            boot("应用目录：" + getFilesDir());
            int n = extractAssets(ASSET_WEBAPP, webappDir);
            boot(n < 0 ? "前端资源已是最新，跳过解包" : ("解包前端资源：" + n + " 个文件 → " + webappDir));
            File index = new File(webappDir, "index.html");
            File mainJs = new File(webappDir, "src/main.js");
            boot("  index.html：" + (index.isFile() ? "有" : "**缺失**"));
            boot("  src/main.js：" + (mainJs.isFile() ? "有" : "**缺失**"));
            if (!index.isFile()) {
                fail("前端首页缺失，无法启动。请把以上信息反馈给开发者。");
                return;
            }
            extractAssets(ASSET_DICT, dataDir, false);   // dataDir 与 webapp/ 等共用，绝不能清空

            boot("启动 Python 解释器…");
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(getApplicationContext()));
            }
            boot("Python 已启动（" + Python.getInstance().getModule("sys").get("version") + "）");
            startPythonLogPump();       // 从此刻起把 Python 日志同步到屏幕
            PyObject server = Python.getInstance().getModule("server");
            boot("调用 server.serve()…");
            // 设备端本地推理要用的两个路径，只能由 Java 侧给出：
            //   nativeLibraryDir —— Android 10+ 的 W^X 之下，**只有这个目录允许 exec 二进制**，
            //                       可写目录（filesDir 等）一律被拒，所以推理二进制必须在那儿；
            //   外部应用目录     —— 模型 1.2–2.5GB 不可能进 APK，放这里可直接 adb push
            //                       （内部 filesDir 需要 run-as 才能写）。
            String nativeLibDir = getApplicationInfo().nativeLibraryDir;
            File extDir = getExternalFilesDir(null);
            boolean lmBuild = getPackageManager().getPackageInfo(getPackageName(), 0)
                    .versionName.startsWith("lm");
            File modelsDir = lmBuild ? new File(extDir != null ? extDir : dataDir, "models") : null;
            if (modelsDir != null) boot("本地模型目录：" + modelsDir);
            // 内部调试轨（包名带 .internal）与封测轨是**两个独立应用**，各自有各自的
            // Android/data/<包名>/files/models —— 于是调试版看不到封测版已经下好的模型，
            // 而一份 2B/4B 要 1.2–2.6 GB。Android 11+ 又禁止应用读**其它包**的 Android/data，
            // 只有拿到「所有文件访问权限」才读得到。
            // 所以这里把**兄弟包**的模型目录一并报给 Python，让它能原地认出来（不拷贝、不重下）。
            // 没授权时那些路径读不到，Python 侧会自然跳过 —— 不会因此报错。
            String siblingDirs = lmBuild ? siblingModelDirs(extDir) : "";
            if (lmBuild && !siblingDirs.isEmpty()) {
                boot("尝试复用其它版本的模型目录：" + siblingDirs);
            }
            // serve() 内部起 uvicorn 并常驻；就绪/失败都通过回调回到主线程
            server.callAttr("serve", this, webappDir.getAbsolutePath(), dataDir.getAbsolutePath(),
                    nativeLibDir == null ? "" : nativeLibDir,
                    modelsDir == null ? "" : modelsDir.getAbsolutePath(),
                    siblingDirs);
        } catch (Throwable t) {
            Log.e(TAG, "启动失败", t);
            fail("启动异常：" + t + "\n" + Log.getStackTraceString(t));
        }
    }

    /**
     * 其它 RailFanAI 包的模型目录（**只读候选**，`:` 分隔；空串表示没有）。
     *
     * 为什么需要：内部调试轨是独立包名（`.internal`），与封测轨属于**两个应用**，
     * 各自有 `/Android/data/<包名>/files/models`。调试时不可能为每一版重下 2.6 GB。
     * 把兄弟目录报给 Python，它就能把那边已下好的模型**原地认出来**。
     *
     * 只在拿到「所有文件访问权限」时真的读得到；没授权就只是几个读不到的路径，
     * Python 侧自然跳过，不会因此失败。**刻意不做存在性检查**：即使现在读不到，
     * 用户随后去系统设置里授权，也应重启后即生效，而不是被这一层缓存住。
     */
    private String siblingModelDirs(File extDir) {
        if (extDir == null) return "";
        // .../Android/data/<pkg>/files → 取两级父目录，即 Android/data
        File parent = extDir.getParentFile();
        File dataRoot = parent == null ? null : parent.getParentFile();
        if (dataRoot == null || !dataRoot.isDirectory()) return "";
        File[] kids = dataRoot.listFiles();
        if (kids == null) return "";
        String self = getPackageName();
        StringBuilder sb = new StringBuilder();
        for (File kid : kids) {
            String name = kid.getName();
            // 只认自家系列（org.openrailfanai.app / .debug / .internal），不去扫别人的目录
            if (!name.startsWith("org.openrailfanai.app") || name.equals(self)) continue;
            if (sb.length() > 0) sb.append(':');
            sb.append(new File(kid, "files/models").getAbsolutePath());
        }
        return sb.toString();
    }

    // ---------------------------------------------------------------- 文件导出（SAF）
    /** 请求码：SAF 的「保存到…」 */
    private static final int REQ_SAVE_FILE = 4001;
    /** 等待用户选完位置再写盘的内容（选完才在 onActivityResult 里落盘）。 */
    private String pendingExportText = null;
    private String pendingExportName = "railfanai.txt";

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQ_SAVE_FILE) return;
        String text = pendingExportText;
        pendingExportText = null;
        if (resultCode != Activity.RESULT_OK || data == null || data.getData() == null || text == null) {
            Log.i(TAG, "用户取消了保存");
            return;
        }
        // 注意用 try-with-resources：ContentResolver 拿到的流必须关，
        // 否则 SAF 那边可能迟迟不落盘（实测在小文件上看不出来，大文件会）。
        try (OutputStream os = getContentResolver().openOutputStream(data.getData())) {
            if (os == null) throw new IllegalStateException("openOutputStream 返回 null");
            os.write(text.getBytes(StandardCharsets.UTF_8));
            os.flush();
            Log.i(TAG, "导出成功：" + data.getData());
        } catch (Exception e) {
            Log.e(TAG, "导出写盘失败", e);
        }
    }

    // ---------------------------------------------------------------- 原生桥（window.RailNative）
    /**
     * 暴露给前端页面的原生能力。契约见 {@code frontend/src/native.js}。
     *
     * 为什么自己写而不是引 Capacitor：本项目只需要"稳定存一份状态"加上几个系统调用，
     * 百来行就能覆盖；而引一整套运行时还要在构建链里插 `npx cap sync`，且**解决不了**
     * 这里真正要解决的问题（源随端口变化）。零依赖，代价可控。
     *
     * 线程：@JavascriptInterface 的方法跑在 WebView 的 JavaBridge 线程上，不是 UI 线程。
     * 读写文件在这里正合适；要碰 UI 的（分享面板）走 startActivity，任意线程可用。
     * 注意 **JS 侧是同步等返回值的** —— 写盘会阻塞页面，所以前端 store.js 做了写入合并。
     */
    public final class RailBridge {

        @JavascriptInterface
        public String platform() {
            return "android";
        }

        // ---------- 状态持久化（store.js 的 db 原生后端）----------

        @JavascriptInterface
        public String readState() {
            File f = stateFile();
            if (!f.isFile()) return "";
            try (FileInputStream in = new FileInputStream(f)) {
                return new String(readAll(in), StandardCharsets.UTF_8);
            } catch (Exception e) {
                Log.w(TAG, "读取本机状态失败", e);
                return "";
            }
        }

        @JavascriptInterface
        public void writeState(String json) {
            if (json == null) return;
            File f = stateFile();
            File tmp = new File(f.getParentFile(), f.getName() + ".tmp");
            try (FileOutputStream out = new FileOutputStream(tmp)) {
                out.write(json.getBytes(StandardCharsets.UTF_8));
                out.getFD().sync();
            } catch (Exception e) {
                Log.w(TAG, "写入本机状态失败", e);
                return;
            }
            // 先写临时文件、刷盘、再改名：应用在写到一半被系统杀掉时不会留下半个 JSON
            // ——那会让下次启动读到损坏状态，用户看到的是"数据全没了"。
            if (!tmp.renameTo(f)) {
                //noinspection ResultOfMethodCallIgnored
                f.delete();
                if (!tmp.renameTo(f)) Log.w(TAG, "状态文件改名失败：" + f);
            }
        }

        // ---------- BYOK Key 的静态加密（Android Keystore）----------

        @JavascriptInterface
        public boolean secureAvailable() {
            try {
                keystoreKey();
                return true;
            } catch (Exception e) {
                Log.w(TAG, "系统密钥库不可用，Key 将退回明文存储", e);
                return false;
            }
        }

        @JavascriptInterface
        public String secureGet(String id) {
            try {
                String b64 = readSecrets().optString(id, "");
                if (b64.isEmpty()) return "";
                byte[] blob = Base64.decode(b64, Base64.NO_WRAP);
                if (blob.length <= GCM_IV_LEN) return "";
                Cipher c = Cipher.getInstance(GCM_TRANSFORM);
                c.init(Cipher.DECRYPT_MODE, keystoreKey(),
                        new GCMParameterSpec(GCM_TAG_BITS, blob, 0, GCM_IV_LEN));
                byte[] pt = c.doFinal(blob, GCM_IV_LEN, blob.length - GCM_IV_LEN);
                return new String(pt, StandardCharsets.UTF_8);
            } catch (Exception e) {
                // 密钥库被重置（刷机/清除应用数据）后旧密文解不开，这时只能如实返回空，
                // 让用户重填 —— 比抛异常把整个设置页带崩好。
                Log.w(TAG, "读取密钥失败（可能密钥库已重置）", e);
                return "";
            }
        }

        @JavascriptInterface
        public void securePut(String id, String value) {
            try {
                JSONObject all = readSecrets();
                if (value == null || value.isEmpty()) {
                    all.remove(id);
                    writeSecrets(all);
                    return;
                }
                Cipher c = Cipher.getInstance(GCM_TRANSFORM);
                c.init(Cipher.ENCRYPT_MODE, keystoreKey());
                byte[] iv = c.getIV();
                byte[] ct = c.doFinal(value.getBytes(StandardCharsets.UTF_8));
                byte[] blob = new byte[iv.length + ct.length];
                System.arraycopy(iv, 0, blob, 0, iv.length);
                System.arraycopy(ct, 0, blob, iv.length, ct.length);
                all.put(id, Base64.encodeToString(blob, Base64.NO_WRAP));
                writeSecrets(all);
            } catch (Exception e) {
                Log.w(TAG, "保存密钥失败", e);
            }
        }

        @JavascriptInterface
        public void secureRemove(String id) {
            try {
                JSONObject all = readSecrets();
                all.remove(id);
                writeSecrets(all);
            } catch (Exception e) {
                Log.w(TAG, "删除密钥失败", e);
            }
        }

        // ---------- 系统能力 ----------

        /** 读剪贴板。Android 10+ 只允许**有焦点**的应用读，读不到时如实返回空串。 */
        @JavascriptInterface
        public String readClipboard() {
            try {
                ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                if (cm == null || !cm.hasPrimaryClip()) return "";
                ClipData clip = cm.getPrimaryClip();
                if (clip == null || clip.getItemCount() == 0) return "";
                CharSequence t = clip.getItemAt(0).coerceToText(MainActivity.this);
                return t == null ? "" : t.toString();
            } catch (Exception e) {
                Log.w(TAG, "读取剪贴板失败", e);
                return "";
            }
        }

        @JavascriptInterface
        public void copyText(String text) {
            try {
                ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                if (cm != null) {
                    cm.setPrimaryClip(ClipData.newPlainText("RailFanAI",
                            text == null ? "" : text));
                }
            } catch (Exception e) {
                Log.w(TAG, "写入剪贴板失败", e);
            }
        }

        /**
         * 让**用户自己选位置**保存一个文本文件（SAF 的「保存到…」）。
         *
         * 为什么要它：App 的外部私有目录（`/sdcard/Android/data/<pkg>/files`）
         * 在 Android 11+ **对文件管理器是屏蔽的** —— 文件写得出来，用户却找不到，
         * 实测因此拿不到日志。SAF 由系统弹框、用户选下载/文档/网盘，**不需要任何存储权限**，
         * 落点是用户自己知道的地方。
         *
         * 返回 true 表示已唤起选择器（真正写盘在 onActivityResult 里完成）。
         */
        @JavascriptInterface
        public boolean saveTextFile(String name, String text) {
            if (text == null) return false;
            pendingExportText = text;
            pendingExportName = (name == null || name.isEmpty()) ? "railfanai.txt" : name;
            // startActivityForResult 必须在 UI 线程；本方法跑在 JavaBridge 线程
            runOnUiThread(() -> {
                try {
                    Intent i = new Intent(Intent.ACTION_CREATE_DOCUMENT);
                    i.addCategory(Intent.CATEGORY_OPENABLE);
                    i.setType("text/plain");
                    i.putExtra(Intent.EXTRA_TITLE, pendingExportName);
                    startActivityForResult(i, REQ_SAVE_FILE);
                } catch (Exception e) {
                    Log.w(TAG, "唤起保存对话框失败", e);
                    pendingExportText = null;
                }
            });
            return true;
        }

        /** 调起系统分享面板。返回是否成功唤起，供前端决定要不要提示失败。 */
        @JavascriptInterface
        public boolean shareText(String text) {
            try {
                Intent send = new Intent(Intent.ACTION_SEND);
                send.setType("text/plain");
                send.putExtra(Intent.EXTRA_TEXT, text == null ? "" : text);
                Intent chooser = Intent.createChooser(send, "分享");
                chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(chooser);
                return true;
            } catch (Exception e) {
                Log.w(TAG, "调起分享失败", e);
                return false;
            }
        }

        /**
         * NPU 探测的 **Java 侧**那一半。为什么必须单独做一遍：
         *
         * 原生探针是用 subprocess 起出来的**独立可执行文件**，拿到的是裸的
         * `(default)` linker namespace —— 而 `<uses-native-library>` 的授权加在
         * **App 的 classloader namespace** 上。实测报错原文就是
         * `is not accessible for the namespace "(default)"`，且 permitted_paths 里
         * **没有 `/vendor/lib64`**。所以那个 "not found" 是**测量方式的假阴性**：
         * 换个地方 dlopen，结论可能完全不同。
         *
         * 本方法跑在 **App 主进程**里、走 classloader namespace ——
         * 也就是"普通 App 真正会用到的那条路"。
         *
         * 返回**文本**而不是布尔值：排障要看具体原因，不是"失败了"三个字。
         */
        @JavascriptInterface
        public String probeNpuJava() {
            String report = buildNpuJavaReport();
            // **顺手缓存到文件**：Python 侧的「导出诊断」够不着 Java（它只能读文件），
            // 而 Java 这半边才是决定性的（loadLibrary 成不成 + open 设备节点成不成）。
            // 不缓存的话，导出的报告就**只有原生那半** —— 实测 vivo 那份诊断正是如此：
            // 决定性数据缺失，而文本看上去毫无异常。缓存之后，直接导出即完整。
            try (FileOutputStream out = new FileOutputStream(npuJavaReportFile())) {
                out.write(report.getBytes("UTF-8"));
            } catch (Throwable t) {
                Log.w(TAG, "NPU Java 报告缓存写入失败（导出诊断里会缺这一节）", t);
            }
            return report;
        }
    }

    /** Java 侧 NPU 报告的缓存文件（供 Python 的 diagnostics() 读取，见 probeNpuJava）。 */
    private File npuJavaReportFile() {
        return new File(getFilesDir(), "npu-java-report.txt");
    }

    /** Java 侧探测的实际内容。调用方见 {@link RailBridge#probeNpuJava()}。 */
    private String buildNpuJavaReport() {
        StringBuilder sb = new StringBuilder();
        sb.append("=== Java 侧探测（App 主进程 / classloader namespace）===\n");
        sb.append("pid ").append(Process.myPid())
          .append("  uid ").append(Process.myUid()).append("\n\n");

        // ---- 1) 能不能加载 FastRPC 库 ----
        // 这才是 `<uses-native-library>` 真正管辖的事。
        // 原生探针（独立 exec 出来的进程）在这里**必然失败** —— 它拿到的是裸的
        // `(default)` namespace。两半的差异正是这个探针存在的理由。
        for (String n : new String[]{"cdsprpc", "adsprpc"}) {
            try {
                System.loadLibrary(n);
                sb.append("loadLibrary(\"").append(n).append("\")  → **成功**\n");
            } catch (Throwable t) {
                sb.append("loadLibrary(\"").append(n).append("\")  → 失败：")
                  .append(t.getClass().getSimpleName()).append(": ")
                  .append(String.valueOf(t.getMessage())).append("\n");
            }
        }
        for (String p : new String[]{"/vendor/lib64/libcdsprpc.so"}) {
            try {
                System.load(p);
                sb.append("load(\"").append(p).append("\")  → **成功**\n");
            } catch (Throwable t) {
                sb.append("load(\"").append(p).append("\")  → 失败：")
                  .append(t.getClass().getSimpleName()).append(": ")
                  .append(String.valueOf(t.getMessage())).append("\n");
            }
        }

        // ---- 2) DSP 设备节点能不能碰（**决定性的那一步**）----
        // 与"库能不能加载"是两回事：库只是用户态包装，真正 open/ioctl 设备节点的是它。
        // 「能加载 ≠ 能用」—— loadLibrary 成功那一下最容易让人以为整条路通了。
        sb.append("\n");
        for (String d : new String[]{"/dev/fastrpc-cdsp", "/dev/fastrpc-cdsp-secure"}) {
            File f = new File(d);
            String open;
            try (FileInputStream in = new FileInputStream(d)) {
                open = "**open 成功**（首字节 " + in.read() + "）";
            } catch (Throwable t) {
                open = "open 失败：" + t.getClass().getSimpleName() + ": "
                       + String.valueOf(t.getMessage());
            }
            sb.append(d).append("\n  exists=").append(f.exists())
              .append(" canRead=").append(f.canRead())
              .append("  → ").append(open).append("\n");
        }
        // 为什么要顺带打 exists/canRead：`File.exists()` 走的是 `access(F_OK)`，
        // 不需要 SELinux 的 `getattr`；而原生探针的 `stat()` 需要 —— 两者**本来就可能不一致**。
        // 都打出来，省得把这个差异误读成"两个进程权限不同"（第一版就这么怀疑过）。
        sb.append("\n判据：**只有 `open` 那一行的成败算结论。**\n");
        sb.append("exists/canRead 仅作参考（access(2) 与 stat(2) 受的检查不同）。\n");
        return sb.toString();
    }

    // ---------------------------------------------------------------- 密钥库与文件工具

    private static final String KS_ALIAS = "railfan-byok-v1";
    private static final String SECRETS_FILE = "secrets.json";
    private static final String GCM_TRANSFORM = "AES/GCM/NoPadding";
    private static final int GCM_IV_LEN = 12;      // GCM 推荐 96 bit
    private static final int GCM_TAG_BITS = 128;

    private File stateFile() {
        return new File(getFilesDir(), "state.json");
    }

    private File secretsFile() {
        return new File(getFilesDir(), SECRETS_FILE);
    }

    /** 取（必要时生成）用于加密 BYOK Key 的 AES 密钥；密钥本身永不离开系统密钥库。 */
    private SecretKey keystoreKey() throws Exception {
        KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
        ks.load(null);
        KeyStore.Entry entry = ks.getEntry(KS_ALIAS, null);
        if (entry instanceof KeyStore.SecretKeyEntry) {
            return ((KeyStore.SecretKeyEntry) entry).getSecretKey();
        }
        KeyGenerator kg = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        kg.init(new KeyGenParameterSpec.Builder(KS_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build());
        return kg.generateKey();
    }

    private JSONObject readSecrets() {
        File f = secretsFile();
        if (!f.isFile()) return new JSONObject();
        try (FileInputStream in = new FileInputStream(f)) {
            return new JSONObject(new String(readAll(in), StandardCharsets.UTF_8));
        } catch (Exception e) {
            Log.w(TAG, "读取密钥文件失败，按空处理", e);
            return new JSONObject();
        }
    }

    private void writeSecrets(JSONObject all) {
        File f = secretsFile();
        File tmp = new File(f.getParentFile(), f.getName() + ".tmp");
        try (FileOutputStream out = new FileOutputStream(tmp)) {
            out.write(all.toString().getBytes(StandardCharsets.UTF_8));
            out.getFD().sync();
        } catch (Exception e) {
            Log.w(TAG, "写入密钥文件失败", e);
            return;
        }
        if (!tmp.renameTo(f)) {
            //noinspection ResultOfMethodCallIgnored
            f.delete();
            if (!tmp.renameTo(f)) Log.w(TAG, "密钥文件改名失败：" + f);
        }
    }

    private static byte[] readAll(InputStream in) throws IOException {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
        return bos.toByteArray();
    }

    // ---------------------------------------------------------------- 资产解包
    /** 解包 assets 子目录到目标目录，返回本次写入的文件数（已是最新版本时返回 -1）。 */
    private int extractAssets(String assetSubDir, File targetDir) throws IOException {
        return extractAssets(assetSubDir, targetDir, true);
    }

    /**
     * @param cleanTarget 解包前是否清空目标目录；**只有目标目录由本资产独占时才能为 true**。
     *
     * `dict` 的目标是 filesDir 本身，它和 webapp/、chaquopy/、.local_port 等共用同一个目录。
     * 早期版本对字典也传 true，于是 `deleteContents(filesDir)` 把**刚刚解包好的 webapp/**
     * 连同一切一起删掉，主页随即 404、界面只剩 `{"detail":"Not Found"}`。
     * 这个缺陷极难在开发时发现：debug 包默认不带字典（`am.list("dict")` 为空直接返回），
     * 只有 `-PincludeDict=true` 的 release 包才会触发 —— 也就是说，**打包开关本身
     * 决定了程序能不能跑**。
     */
    private int extractAssets(String assetSubDir, File targetDir, boolean cleanTarget) throws IOException {
        AssetManager am = getAssets();
        String[] children = am.list(assetSubDir);
        if (children == null || children.length == 0) {
            return 0;
        }

        File marker = new File(targetDir, "." + assetSubDir + ".version");
        String expected = buildVersionTag();
        if (expected.equals(readMarker(marker)) && targetDir.isDirectory()) {
            boot("assets/" + assetSubDir + " 已是 " + expected + "，跳过解包");
            return -1;
        }

        //noinspection ResultOfMethodCallIgnored
        targetDir.mkdirs();
        // 先清空旧内容：否则上一版"拍平"留下的散落文件会污染新版本（仅对独占目录成立）
        if (cleanTarget) {
            deleteContents(targetDir);
        }
        int count = copyAssets(am, assetSubDir, targetDir);
        writeMarker(marker, expected);
        return count;
    }

    private void deleteContents(File dir) {
        File[] files = dir.listFiles();
        if (files == null) return;
        for (File f : files) {
            if (f.isDirectory()) deleteContents(f);
            //noinspection ResultOfMethodCallIgnored
            f.delete();
        }
    }

    /**
     * 把 assets/assetPath 的**内容**递归解包到 outDir，保持相对目录结构。
     *
     * 这里曾有一个真实缺陷：早期实现把同一个 outDir 传给所有子项、并且只用文件名落盘，
     * 结果 `webapp/src/main.js` 被写成 `webapp/main.js` —— 目录结构被拍平，
     * index.html 里 `./src/main.js` 全部 404，页面只剩静态骨架（表现为"白屏"）。
     */
    private int copyAssets(AssetManager am, String assetPath, File outDir) throws IOException {
        String[] children = am.list(assetPath);
        if (children == null || children.length == 0) {
            // 叶子：单个文件，写到 outDir 下（用完整相对路径的最后一段）
            copyAssetFile(am, assetPath, new File(outDir, new File(assetPath).getName()));
            return 1;
        }
        int total = 0;
        for (String child : children) {
            String childPath = assetPath + "/" + child;
            String[] grand = am.list(childPath);
            if (grand != null && grand.length > 0) {
                File sub = new File(outDir, child);
                //noinspection ResultOfMethodCallIgnored
                sub.mkdirs();
                total += copyAssets(am, childPath, sub);
            } else {
                copyAssetFile(am, childPath, new File(outDir, child));
                total++;
            }
        }
        return total;
    }

    private void copyAssetFile(AssetManager am, String assetPath, File dest) throws IOException {
        File parent = dest.getParentFile();
        if (parent != null) {
            //noinspection ResultOfMethodCallIgnored
            parent.mkdirs();
        }
        try (InputStream in = am.open(assetPath);
             OutputStream out = new FileOutputStream(dest)) {
            byte[] buf = new byte[64 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        }
    }

    /**
     * 解包版本标记。
     *
     * 必须包含 `lastUpdateTime`：只用 versionCode/versionName 的话，同一个版本号
     * **覆盖安装**（修 bug 后最常见的动作）会被判定为"已是最新"而跳过解包，
     * 上一版的资源会原样留着 —— 表现为"装了新包但问题没变化"，极难排查。
     * 加入安装时间可保证每次安装/更新都重新解包。
     */
    private String buildVersionTag() {
        try {
            PackageInfo pi = getPackageManager().getPackageInfo(getPackageName(), 0);
            return pi.getLongVersionCode() + ":" + pi.versionName + ":" + pi.lastUpdateTime;
        } catch (Exception e) {
            return "unknown";
        }
    }

    private String readMarker(File f) {
        if (!f.isFile()) return "";
        try (InputStream in = new java.io.FileInputStream(f)) {
            byte[] buf = new byte[64];
            int n = in.read(buf);
            return n > 0 ? new String(buf, 0, n, "UTF-8").trim() : "";
        } catch (IOException e) {
            return "";
        }
    }

    private void writeMarker(File f, String value) {
        try (OutputStream out = new FileOutputStream(f)) {
            out.write(value.getBytes("UTF-8"));
        } catch (IOException e) {
            Log.w(TAG, "写版本标记失败（下次会重新解包）", e);
        }
    }

    // ---------------------------------------------------------------- Python 回调
    /**
     * 页面加载完成后，用 JS 把"实际渲染出来的内容"读回来并写进启动日志。
     *
     * 为什么需要：`onPageFinished` 只说明文档加载完了，**不代表界面渲染正确** ——
     * 前端脚本一旦抛错（或资源 404），页面会停在静态骨架甚至全空，而
     * WebView 不会因此报任何错。上一版"白屏但日志显示一切正常"正是这个盲区。
     * 这里把 DOM 的关键事实（正文、脚本错误横幅、未配置模型引导条、消息区子节点数）
     * 一次性取回来，让"是否真的渲染成应用界面"可以被文本核对，不依赖人工看截图。
     */
    private void probeRenderedPage(WebView view) {
        final String js =
            "(function(){try{"
            + "var err=document.getElementById('boot-error');"
            + "var chat=document.getElementById('chat');"
            + "var notice=document.getElementById('llm-notice');"
            + "var txt=(document.body.innerText||'').replace(/\\s+/g,' ').trim().slice(0,300);"
            + "return JSON.stringify({"
            + "  title:document.title,"
            + "  chatChildren:chat?chat.childElementCount:-1,"
            + "  bootError:(err&&err.style.display!=='none')?err.textContent.slice(0,300):null,"
            + "  noticeShown:!!(notice&&notice.className.indexOf('hidden')<0),"
            // 桥没接上时前端会**静默**退回 localStorage（对话仍在，只是又变得依赖端口）。
            // 这种"降级"从界面上完全看不出来，所以把它显式写进自检日志。
            + "  bridge:(window.RailNative?window.RailNative.platform():'缺失'),"
            + "  bodyText:txt});"
            + "}catch(e){return JSON.stringify({jsError:String(e)});}})()";
        view.evaluateJavascript(js, value -> boot("页面自检：" + value));
        // 再探一次：首探发生在 onPageFinished 的瞬间，而界面上的异步状态
        // （如"未配置模型"引导条要等 /api/providers 返回后才决定显隐）此时往往还没落定。
        // 间隔一次可区分"确实没显示"与"还没来得显示"。
        view.postDelayed(() -> {
            if (web != null) {
                web.evaluateJavascript(js, value -> boot("页面自检(+8s)：" + value));
            }
        }, 8000);
    }

    /** 供 Python 调用：后端启动的某个阶段（用于定位"卡在哪一步"）。 */
    @SuppressWarnings("unused")
    public void onBootStage(final String stage) {
        boot("  · " + stage);
    }

    /** 供 Python 调用：后端已在 127.0.0.1:port 上就绪。selfCheck 为自检结论（可为空）。 */
    @SuppressWarnings("unused")
    public void onServerReady(final int readyPort, final String selfCheck) {
        boot("后端就绪：http://127.0.0.1:" + readyPort + "/");
        if (selfCheck != null && !selfCheck.isEmpty()) {
            boot("自检：" + selfCheck);
        }
        runOnUiThread(() -> {
            web.setVisibility(View.VISIBLE);
            web.loadUrl("http://127.0.0.1:" + readyPort + "/");
        });
    }

    /** 供 Python 调用：后端启动过程中失败（含自检不通过）。 */
    @SuppressWarnings("unused")
    public void onStartupFailed(final String detail) {
        fail("后端启动失败：\n" + detail
                + "\n\n（若上面提到 HTTP 非 200／Connection refused，请把整屏内容反馈给开发者）");
    }

    /**
     * 把 Python 侧的日志搬运到屏幕上（轮询模块内的环形缓冲）。
     *
     * 为什么需要：Python 的 logging 输出只进 logcat，而真机排障拿不到 logcat ——
     * 上一版卡在 serve() 里时，界面上永远停在"调用 server.serve()…"，
     * Python 那侧发生了什么（导入到哪、报了什么错）完全不可见。
     * 轮询开销极小（只读一个 deque），且不依赖回调时机。
     */
    private void startPythonLogPump() {
        final PyObject[] server = new PyObject[1];
        final Thread t = new Thread(() -> {
            int shown = 0;
            while (!pageLoaded && !isFinishing()) {
                try {
                    if (server[0] == null) {
                        server[0] = Python.getInstance().getModule("server");
                    }
                    String text = server[0].callAttr("recent_logs", 60).toString();
                    if (text != null && !text.isEmpty()) {
                        String[] lines = text.split("\n");
                        if (lines.length > shown) {
                            for (int i = shown; i < lines.length; i++) {
                                boot("py| " + lines[i]);
                            }
                            shown = lines.length;
                        }
                    }
                } catch (Throwable ignored) {
                    // Python 可能尚未初始化或正忙；下一轮再试
                }
                try {
                    Thread.sleep(1200);
                } catch (InterruptedException e) {
                    return;
                }
            }
        }, "python-log-pump");
        t.setDaemon(true);
        t.start();
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (backendBound) {
            if (backendService != null) backendService.removeListener(this);
            unbindService(backendConnection);
            backendBound = false;
        }
        if (web != null) {
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }

    @Override public void onBackendStage(String stage) { boot("  · " + stage); }

    @Override public void onBackendReady(int readyPort, String selfCheck) {
        boot("后端就绪：http://127.0.0.1:" + readyPort + "/");
        if (selfCheck != null && !selfCheck.isEmpty()) boot("自检：" + selfCheck);
        runOnUiThread(() -> {
            if (web != null) {
                web.setVisibility(View.VISIBLE);
                web.loadUrl("http://127.0.0.1:" + readyPort + "/");
            }
        });
    }

    @Override public void onBackendFailed(String detail) { fail("后端启动失败：\n" + detail); }
}
