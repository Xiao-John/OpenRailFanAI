package org.openrailfanai.app;

import android.content.Context;
import android.system.Os;
import java.io.*;
import java.security.MessageDigest;

/** Bundled reference data lives apart from the writable runtime database. */
final class BundledDictionary {
    static File prepare(Context context, String packageTag) throws Exception {
        String[] entries = context.getAssets().list("dict");
        boolean available = false;
        if (entries != null) for (String entry : entries) if ("dict.db".equals(entry)) available = true;
        if (!available) return null;
        File dir = new File(context.getFilesDir(), "bundled-dict");
        if (!dir.mkdirs() && !dir.isDirectory()) throw new IOException("无法创建包内词典目录");
        File target = new File(dir, "dict.db");
        File marker = new File(dir, "package-tag");
        if (target.isFile() && marker.isFile()) {
            byte[] bytes = new byte[(int) Math.min(marker.length(), 1024)];
            try (InputStream input = new FileInputStream(marker)) {
                int count = input.read(bytes);
                if (count > 0 && packageTag.equals(new String(bytes, 0, count, "UTF-8"))) return target;
            }
        }
        File temp = File.createTempFile("bundle-", ".tmp", dir);
        File tempMarker = File.createTempFile("tag-", ".tmp", dir);
        try {
            try (InputStream input = context.getAssets().open("dict/dict.db"); FileOutputStream output = new FileOutputStream(temp)) {
                byte[] buffer = new byte[64 * 1024]; int count;
                while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                output.getFD().sync();
            }
            if (temp.length() < 16) throw new IOException("包内词典文件无效");
            try (InputStream input = new FileInputStream(temp)) {
                byte[] header = new byte[16];
                if (input.read(header) != 16 || !"SQLite format 3\u0000".equals(new String(header, "US-ASCII"))) throw new IOException("包内词典格式无效");
            }
            try (FileOutputStream output = new FileOutputStream(tempMarker)) { output.write(packageTag.getBytes("UTF-8")); output.getFD().sync(); }
            Os.rename(temp.getAbsolutePath(), target.getAbsolutePath());
            target.setReadOnly();
            Os.rename(tempMarker.getAbsolutePath(), marker.getAbsolutePath());
            return target;
        } finally { temp.delete(); tempMarker.delete(); }
    }
}
