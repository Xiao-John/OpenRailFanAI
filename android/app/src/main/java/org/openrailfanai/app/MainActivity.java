package org.openrailfanai.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.pm.PackageInfo;
import android.content.res.AssetManager;
import android.graphics.Typeface;
import android.os.Bundle;
import android.util.Log;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebResourceError;
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

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

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
public class MainActivity extends Activity {

    private static final String TAG = "RailFanAI";

    private static final String ASSET_WEBAPP = "webapp";
    private static final String ASSET_DICT = "dict";

    private WebView web;
    private TextView logView;
    private TextView hintView;
    private final StringBuilder bootLog = new StringBuilder();
    private volatile boolean pageLoaded = false;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Log.i(TAG, "启动一体化运行环境");

        FrameLayout root = new FrameLayout(this);

        // ---- 启动/诊断面板：失败时它就是唯一的排障入口 ----
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(40, 56, 40, 40);

        hintView = new TextView(this);
        hintView.setTextSize(17f);
        hintView.setTypeface(Typeface.DEFAULT_BOLD);
        hintView.setText("正在启动本地服务…");
        panel.addView(hintView);

        logView = new TextView(this);
        logView.setTextSize(12f);
        logView.setTypeface(Typeface.MONOSPACE);
        logView.setPadding(0, 20, 0, 20);
        logView.setTextIsSelectable(true);      // 方便用户复制给我们排查
        panel.addView(logView);

        Button retry = new Button(this);
        retry.setText("重试");
        retry.setOnClickListener(v -> recreate());
        panel.addView(retry);

        ScrollView scroller = new ScrollView(this);
        scroller.addView(panel);
        root.addView(scroller, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        // ---- WebView ----
        web = new WebView(this);
        web.setVisibility(View.GONE);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);        // 前端用 localStorage 存对话与供应商设置
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setAllowFileAccess(false);         // 页面只来自本机 HTTP，不需要文件访问
        s.setAllowContentAccess(false);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String host = request.getUrl().getHost();
                if (host != null && (host.equals("127.0.0.1") || host.equals("localhost"))) {
                    return false;
                }
                boot("已拦截外部跳转：" + request.getUrl());
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

        new Thread(this::bootstrap, "railfan-bootstrap").start();
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
            extractAssets(ASSET_DICT, dataDir);

            boot("启动 Python 解释器…");
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(getApplicationContext()));
            }
            boot("Python 已启动（" + Python.getInstance().getModule("sys").get("version") + "）");
            startPythonLogPump();       // 从此刻起把 Python 日志同步到屏幕
            PyObject server = Python.getInstance().getModule("server");
            boot("调用 server.serve()…");
            // serve() 内部起 uvicorn 并常驻；就绪/失败都通过回调回到主线程
            server.callAttr("serve", this, webappDir.getAbsolutePath(), dataDir.getAbsolutePath());
        } catch (Throwable t) {
            Log.e(TAG, "启动失败", t);
            fail("启动异常：" + t + "\n" + Log.getStackTraceString(t));
        }
    }

    // ---------------------------------------------------------------- 资产解包
    /** 解包 assets 子目录到目标目录，返回本次写入的文件数（已是最新版本时返回 -1）。 */
    private int extractAssets(String assetSubDir, File targetDir) throws IOException {
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
        // 先清空旧内容：否则上一版"拍平"留下的散落文件会污染新版本
        deleteContents(targetDir);
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
        if (web != null) {
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }
}
