package org.openrailfanai.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.pm.PackageInfo;
import android.content.res.AssetManager;
import android.os.Bundle;
import android.util.Log;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
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
 * 这是一个"一体化"应用 —— 没有远程服务端，也没有引入任何 Web 组件框架
 * （Capacitor/Cordova/RN 之流），UI 就是平台自带的 android.webkit.WebView。
 *
 * 启动顺序：
 *   1. 把 APK assets 里的前端静态资源解包到应用私有目录（Python 的 StaticFiles 需要真实文件，
 *      而 Chaquopy 把 Python 源码打包进 .imy 归档，读不到其中的静态资源）；
 *   2. 后台线程启动 Python 后端（server.serve），监听 127.0.0.1 的空闲端口；
 *   3. Python 回调 onServerReady(port) 后，WebView 加载 http://127.0.0.1:port/。
 */
public class MainActivity extends Activity {

    private static final String TAG = "RailFanAI";

    /** 前端资源在 assets 下的根目录名（由 Gradle 的 stageWebApp 任务放入）。 */
    private static final String ASSET_WEBAPP = "webapp";
    /** 可选捆绑的本地数据字典（dict.db 约 14MB，由 Gradle 的 includeDict 开关决定是否打包）。 */
    private static final String ASSET_DICT = "dict";

    private WebView web;
    private TextView splash;
    private volatile String startupError = null;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Log.i(TAG, "启动一体化运行环境");

        // ---- 占位界面：解包与起服务期间显示，避免白屏 ----
        FrameLayout root = new FrameLayout(this);
        splash = new TextView(this);
        splash.setText("正在启动本地服务…");
        splash.setTextSize(16f);
        splash.setPadding(48, 48, 48, 48);
        root.addView(splash, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        web = new WebView(this);
        web.setVisibility(View.GONE);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);        // 前端用 localStorage 存对话与供应商设置
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(true);
        // 页面只来自本机回环：外部链接不接管，保持一体化应用的边界
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String host = request.getUrl().getHost();
                if (host != null && (host.equals("127.0.0.1") || host.equals("localhost"))) {
                    return false;           // 本地页面：WebView 内处理
                }
                return true;                // 外部链接：交给系统浏览器
            }
        });
        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);

        // ---- 后台线程：解包资源 → 起 Python 后端 ----
        new Thread(this::bootstrap, "railfan-bootstrap").start();
    }

    private void bootstrap() {
        try {
            File webappDir = new File(getFilesDir(), "webapp");
            File dataDir = getFilesDir();
            extractAssets(ASSET_WEBAPP, webappDir);
            extractAssets(ASSET_DICT, dataDir);

            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(getApplicationContext()));
            }
            PyObject server = Python.getInstance().getModule("server");
            // serve() 内部起 uvicorn 并常驻，因此本线程到此为止。
            // 就绪/失败都通过回调回到主线程（见 onServerReady / onStartupFailed）。
            server.callAttr("serve", this, webappDir.getAbsolutePath(), dataDir.getAbsolutePath());
        } catch (Throwable t) {
            Log.e(TAG, "启动失败", t);
            onStartupFailed(t.toString());
        }
    }

    /**
     * 把 assets 下的目录递归解包到目标目录。
     *
     * 带版本标记：应用升级后（versionCode 变化）强制重解，否则会继续用旧版前端资源，
     * 表现为"改了前端但装上新包没变化"这种极难排查的问题。
     */
    private void extractAssets(String assetSubDir, File targetDir) throws IOException {
        AssetManager am = getAssets();
        String[] children;
        try {
            children = am.list(assetSubDir);
        } catch (IOException e) {
            children = null;
        }
        if (children == null || children.length == 0) {
            Log.i(TAG, "assets/" + assetSubDir + " 为空，跳过解包");
            return;
        }

        File marker = new File(targetDir, "." + assetSubDir + ".version");
        String current = readMarker(marker);
        String expected = buildVersionTag();
        if (expected.equals(current) && targetDir.isDirectory()) {
            Log.i(TAG, "assets/" + assetSubDir + " 已是 " + expected + "，跳过解包");
            return;
        }

        //noinspection ResultOfMethodCallIgnored
        targetDir.mkdirs();
        int count = copyAssetDir(am, assetSubDir, targetDir);
        writeMarker(marker, expected);
        Log.i(TAG, "已解包 assets/" + assetSubDir + " → " + targetDir + "（" + count + " 个文件）");
    }

    private int copyAssetDir(AssetManager am, String assetPath, File outDir) throws IOException {
        String[] children = am.list(assetPath);
        if (children == null || children.length == 0) {
            // 叶子：是文件（assets 无空目录语义）
            try (InputStream in = am.open(assetPath);
                 OutputStream out = new FileOutputStream(new File(outDir, new File(assetPath).getName()))) {
                byte[] buf = new byte[64 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            }
            return 1;
        }
        int total = 0;
        for (String child : children) {
            total += copyAssetDir(am, assetPath + "/" + child, outDir);
        }
        return total;
    }

    private String buildVersionTag() {
        try {
            PackageInfo pi = getPackageManager().getPackageInfo(getPackageName(), 0);
            long code = pi.getLongVersionCode();
            return code + ":" + pi.versionName;
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

    /** 供 Python 调用：后端已在 127.0.0.1:port 上就绪。 */
    @SuppressWarnings("unused")
    public void onServerReady(final int readyPort) {
        Log.i(TAG, "本地后端就绪，端口 " + readyPort);
        runOnUiThread(() -> {
            splash.setVisibility(View.GONE);
            web.setVisibility(View.VISIBLE);
            web.loadUrl("http://127.0.0.1:" + readyPort + "/");
        });
    }

    /** 供 Python 调用：后端启动抛异常，把 traceback 显示出来便于用户反馈。 */
    @SuppressWarnings("unused")
    public void onStartupFailed(final String detail) {
        Log.e(TAG, "后端启动失败：" + detail);
        startupError = detail;
        runOnUiThread(() -> {
            splash.setVisibility(View.VISIBLE);
            splash.setText("本地服务启动失败：\n\n" + detail + "\n\n请把以上信息反馈给开发者。");
        });
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();       // 内容页/设置页返回对话，而不是直接退出应用
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
