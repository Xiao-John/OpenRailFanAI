package org.openrailfanai.app

import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.util.Base64
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.chinese.ChineseTextRecognizerOptions
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.util.concurrent.TimeUnit

/** Device-only OCR. Call on a worker thread, never the main thread. No image leaves the device. */
internal object ImageTextRecognizer {
    fun text(bytes: ByteArray): String {
        require(bytes.size <= AppAgent.MAX_INPUT_BYTES) { "图片超过 8 MiB" }
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        require(bounds.outWidth > 0 && bounds.outHeight > 0) { "无法读取图片" }
        require(bounds.outWidth.toLong() * bounds.outHeight <= 24_000_000) { "图片超过 2400 万像素，请缩小后重试。" }
        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 4096) sample *= 2
        val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
            ?: error("无法解码图片")
        val orientation = runCatching { ExifInterface(ByteArrayInputStream(bytes)).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL) }.getOrDefault(1)
        val matrix = Matrix().apply {
            when (orientation) {
                2 -> setScale(-1f, 1f)
                3 -> setRotate(180f)
                4 -> setScale(1f, -1f)
                5 -> { setRotate(90f); postScale(-1f, 1f) }
                6 -> setRotate(90f)
                7 -> { setRotate(270f); postScale(-1f, 1f) }
                8 -> setRotate(270f)
            }
        }
        val oriented = android.graphics.Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true)
        val recognizer = TextRecognition.getClient(ChineseTextRecognizerOptions.Builder().build())
        var processing: com.google.android.gms.tasks.Task<com.google.mlkit.vision.text.Text>? = null
        fun release() {
            recognizer.close()
            if (oriented !== bitmap) oriented.recycle()
            bitmap.recycle()
        }
        try {
            if (Thread.currentThread().isInterrupted) throw InterruptedException("Request cancelled")
            processing = recognizer.process(InputImage.fromBitmap(oriented, 0))
            val text = Tasks.await(processing, 45, TimeUnit.SECONDS).text.trim()
            require(text.isNotBlank()) { "本地 OCR 未识别出文字；请换清晰图片，或为支持看图的模型启用图片输入。" }
            require(text.length <= 60_000) { "识别文字过长，请缩小图片范围。" }
            return text
        } finally {
            val pending = processing
            if (pending == null || pending.isComplete) release() else pending.addOnCompleteListener { release() }
        }
    }

    fun convert(files: JSONArray): JSONArray = JSONArray().apply {
        for (index in 0 until files.length()) {
            val item = files.getJSONObject(index)
            if (!AppAgent.isImage(item.optString("name"), item.optString("mime"))) put(item)
            else {
                val text = text(Base64.decode(item.getString("content_base64"), Base64.DEFAULT))
                put(JSONObject().put("name", item.optString("name").substringBeforeLast('.').take(152) + "-ocr.txt")
                    .put("mime", "text/plain").put("source", "local_ocr")
                    .put("content_base64", Base64.encodeToString(text.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)))
            }
        }
    }
}
