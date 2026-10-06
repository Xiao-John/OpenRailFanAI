package org.openrailfanai.app;

import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.res.AssetManager;
import android.os.Binder;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.Log;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.concurrent.CopyOnWriteArraySet;
import java.util.concurrent.atomic.AtomicBoolean;

/** Process-scoped owner for the embedded Python server. Activities only observe its state. */
public final class BackendService extends Service {
    private static final String TAG = "RailFanBackend";
    private static final String WEBAPP = "webapp";
    private static final String DICT = "dict";

    public interface Listener {
        void onBackendStage(String stage);
        void onBackendReady(int port, String selfCheck);
        void onBackendFailed(String detail);
    }

    public final class LocalBinder extends Binder {
        public BackendService service() { return BackendService.this; }
    }

    private final IBinder binder = new LocalBinder();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final CopyOnWriteArraySet<Listener> listeners = new CopyOnWriteArraySet<>();
    private final AtomicBoolean started = new AtomicBoolean(false);
    private volatile String stage = "等待启动";
    private volatile int port = 0;
    private volatile String selfCheck = "";
    private volatile String failure = "";

    @Override public void onCreate() {
        super.onCreate();
        startBackendOnce();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        startBackendOnce();
        return START_STICKY;
    }

    @Override public IBinder onBind(Intent intent) { return binder; }

    public void addListener(Listener listener) {
        listeners.add(listener);
        main.post(() -> deliverSnapshot(listener));
    }

    public void removeListener(Listener listener) { listeners.remove(listener); }

    private void deliverSnapshot(Listener listener) {
        if (!listeners.contains(listener)) return;
        if (!failure.isEmpty()) listener.onBackendFailed(failure);
        else if (port > 0) listener.onBackendReady(port, selfCheck);
        else listener.onBackendStage(stage);
    }

    private void publishStage(String value) {
        stage = value;
        main.post(() -> { for (Listener listener : listeners) listener.onBackendStage(value); });
    }

    private void publishReady(int readyPort, String detail) {
        port = readyPort;
        selfCheck = detail == null ? "" : detail;
        failure = "";
        main.post(() -> { for (Listener listener : listeners) listener.onBackendReady(readyPort, selfCheck); });
    }

    private void publishFailure(String detail) {
        failure = detail == null ? "启动失败" : detail;
        main.post(() -> { for (Listener listener : listeners) listener.onBackendFailed(failure); });
    }

    /** Python callbacks. These methods must remain public for Chaquopy reflection. */
    public void onBootStage(String value) { publishStage(value); }
    public void onServerReady(int readyPort, String detail) { publishReady(readyPort, detail); }
    public void onStartupFailed(String detail) { publishFailure(detail); }

    public void retry() {
        if (port > 0 && failure.isEmpty()) return;
        failure = "";
        started.set(false);
        startBackendOnce();
    }

    private void startBackendOnce() {
        if (!started.compareAndSet(false, true)) return;
        new Thread(this::bootstrap, "railfan-backend-bootstrap").start();
    }

    private void bootstrap() {
        try {
            File dataDir = getFilesDir();
            File webappDir = new File(dataDir, WEBAPP);
            publishStage("准备本地资源…");
            int copied = extractAssets(WEBAPP, webappDir, true);
            File index = new File(webappDir, "index.html");
            if (!index.isFile()) throw new IOException("前端首页缺失：" + index);
            publishStage(copied < 0 ? "前端资源已是最新" : "已准备前端资源：" + copied + " 个文件");
            String version = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
            boolean lmBuild = version != null && version.startsWith("lm");
            File bundledDictionary = null;
            if (lmBuild) extractAssets(DICT, dataDir, false);
            else bundledDictionary = BundledDictionary.prepare(this, buildVersionTag());

            publishStage("启动 Python 解释器…");
            if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext()));
            if (!lmBuild) {
                PyObject environment = Python.getInstance().getModule("os").get("environ");
                environment.callAttr("__setitem__", "DICT_DB_PATH", new File(dataDir, "dict.db").getAbsolutePath());
                if (bundledDictionary != null) environment.callAttr("__setitem__", "DICT_BUNDLED_DB_PATH", bundledDictionary.getAbsolutePath());
                else environment.callAttr("pop", "DICT_BUNDLED_DB_PATH", "");
            }
            PyObject server = Python.getInstance().getModule("server");
            String nativeLibDir = getApplicationInfo().nativeLibraryDir;
            File external = getExternalFilesDir(null);
            File models = lmBuild ? new File(external != null ? external : dataDir, "models") : null;
            String siblings = lmBuild ? siblingModelDirs(external) : "";
            publishStage("启动设备内 FastAPI 服务…");
            // serve() stays on this dedicated thread; callbacks target this service, never an Activity.
            server.callAttr("serve", this, webappDir.getAbsolutePath(), dataDir.getAbsolutePath(),
                    nativeLibDir == null ? "" : nativeLibDir,
                    models == null ? "" : models.getAbsolutePath(), siblings);
        } catch (Throwable error) {
            Log.e(TAG, "Backend startup failed", error);
            publishFailure(error.toString() + "\n" + Log.getStackTraceString(error));
            started.set(false);
        }
    }

    private String siblingModelDirs(File external) {
        if (external == null) return "";
        File parent = external.getParentFile();
        File dataRoot = parent == null ? null : parent.getParentFile();
        if (dataRoot == null || !dataRoot.isDirectory()) return "";
        File[] children = dataRoot.listFiles();
        if (children == null) return "";
        StringBuilder paths = new StringBuilder();
        for (File child : children) {
            String name = child.getName();
            if (!name.startsWith("org.openrailfanai.app") || name.equals(getPackageName())) continue;
            if (paths.length() > 0) paths.append(':');
            paths.append(new File(child, "files/models").getAbsolutePath());
        }
        return paths.toString();
    }

    private int extractAssets(String assetPath, File target, boolean clean) throws IOException {
        AssetManager assets = getAssets();
        String[] children = assets.list(assetPath);
        if (children == null || children.length == 0) return 0;
        File marker = new File(target, "." + assetPath + ".version");
        String expected = buildVersionTag();
        if (expected.equals(readMarker(marker)) && target.isDirectory()) return -1;
        if (!target.mkdirs() && !target.isDirectory()) throw new IOException("Cannot create " + target);
        if (clean) deleteContents(target);
        int count = copyAssets(assets, assetPath, target);
        try (OutputStream out = new FileOutputStream(marker)) { out.write(expected.getBytes("UTF-8")); }
        return count;
    }

    private int copyAssets(AssetManager assets, String path, File outDir) throws IOException {
        String[] children = assets.list(path);
        if (children == null || children.length == 0) {
            copyAssetFile(assets, path, new File(outDir, new File(path).getName()));
            return 1;
        }
        int count = 0;
        for (String child : children) {
            String childPath = path + "/" + child;
            String[] descendants = assets.list(childPath);
            File output = new File(outDir, child);
            if (descendants != null && descendants.length > 0) {
                if (!output.mkdirs() && !output.isDirectory()) throw new IOException("Cannot create " + output);
                count += copyAssets(assets, childPath, output);
            } else {
                copyAssetFile(assets, childPath, output);
                count++;
            }
        }
        return count;
    }

    private void copyAssetFile(AssetManager assets, String path, File dest) throws IOException {
        File parent = dest.getParentFile();
        if (parent != null && !parent.mkdirs() && !parent.isDirectory()) throw new IOException("Cannot create " + parent);
        try (InputStream in = assets.open(path); OutputStream out = new FileOutputStream(dest)) {
            byte[] buffer = new byte[64 * 1024];
            int size;
            while ((size = in.read(buffer)) > 0) out.write(buffer, 0, size);
        }
    }

    private void deleteContents(File dir) {
        File[] children = dir.listFiles();
        if (children == null) return;
        for (File child : children) {
            if (child.isDirectory()) deleteContents(child);
            if (!child.delete()) Log.w(TAG, "Could not remove stale asset " + child);
        }
    }

    private String buildVersionTag() {
        try {
            PackageInfo info = getPackageManager().getPackageInfo(getPackageName(), 0);
            long code = android.os.Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode;
            return code + ":" + info.versionName + ":" + info.lastUpdateTime;
        } catch (Exception e) {
            return "unknown";
        }
    }

    private String readMarker(File marker) {
        if (!marker.isFile()) return "";
        try (InputStream in = new FileInputStream(marker)) {
            byte[] bytes = new byte[128];
            int count = in.read(bytes);
            return count > 0 ? new String(bytes, 0, count, "UTF-8").trim() : "";
        } catch (IOException e) { return ""; }
    }
}
