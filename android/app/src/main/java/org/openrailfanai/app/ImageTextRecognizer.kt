package org.openrailfanai.app

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.nio.FloatBuffer
import kotlin.math.ceil
import kotlin.math.max

/** Fully local PP-OCRv5 mobile recognition. Call from a worker thread. */
internal object ImageTextRecognizer {
    private const val MAX_PIXELS = 24_000_000L
    private const val MAX_TEXT_CHARS = 60_000
    private const val DET_LIMIT = 960
    private const val REC_HEIGHT = 48
    private const val REC_WIDTH = 320

    private data class Runtime(val env: OrtEnvironment, val det: OrtSession, val rec: OrtSession, val chars: List<String>)
    private data class Box(val left: Int, val top: Int, val right: Int, val bottom: Int) {
        val width get() = right - left
        val height get() = bottom - top
        val centerY get() = (top + bottom) / 2
    }

    private val deadline = ThreadLocal<Long>()
    @Volatile private var loaded: Runtime? = null

    private fun runtime(context: Context): Runtime {
        loaded?.let { return it }
        return synchronized(this) {
            loaded?.let { return@synchronized it }
            val env = OrtEnvironment.getEnvironment()
            fun session(path: String): OrtSession {
                val options = OrtSession.SessionOptions().apply {
                    setIntraOpNumThreads(2)
                    setInterOpNumThreads(1)
                    setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT)
                }
                return try {
                    context.assets.open("v5/$path").use { env.createSession(it.readBytes(), options) }
                } finally {
                    options.close()
                }
            }
            val characters = context.assets.open("v5/dict.txt").bufferedReader(Charsets.UTF_8).use { reader ->
                reader.readLines().filter(String::isNotEmpty)
            }
            val detector = session("det.onnx")
            try {
                Runtime(env, detector, session("rec.onnx"), characters).also { loaded = it }
            } catch (failure: Throwable) {
                detector.close()
                throw failure
            }
        }
    }

    fun text(context: Context, bytes: ByteArray): String {
        require(bytes.size <= AppAgent.MAX_INPUT_BYTES) { "图片超过 8 MiB" }
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        require(bounds.outWidth > 0 && bounds.outHeight > 0) { "无法读取图片" }
        require(bounds.outWidth.toLong() * bounds.outHeight <= MAX_PIXELS) { "图片超过 2400 万像素，请缩小后重试。" }
        var sample = 1
        while (max(bounds.outWidth, bounds.outHeight) / sample > 4096) sample *= 2
        val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
            ?: error("无法解码图片")
        val orientation = runCatching {
            ExifInterface(ByteArrayInputStream(bytes)).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
        }.getOrDefault(ExifInterface.ORIENTATION_NORMAL)
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
        val oriented = Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true)
        deadline.set(android.os.SystemClock.elapsedRealtime() + 45_000L)
        try {
            checkCancelled()
            val engine = runtime(context.applicationContext)
            val boxes = detect(engine, oriented)
            val lines = ArrayList<String>(boxes.size)
            for (box in boxes) {
                checkCancelled()
                val crop = Bitmap.createBitmap(
                    oriented,
                    box.left.coerceIn(0, oriented.width - 1),
                    box.top.coerceIn(0, oriented.height - 1),
                    box.width.coerceAtLeast(1).coerceAtMost(oriented.width - box.left.coerceIn(0, oriented.width - 1)),
                    box.height.coerceAtLeast(1).coerceAtMost(oriented.height - box.top.coerceIn(0, oriented.height - 1)),
                )
                try { recognize(engine, crop)?.let(lines::add) } finally { if (crop !== oriented) crop.recycle() }
                if (lines.sumOf(String::length) > MAX_TEXT_CHARS) throw IllegalArgumentException("识别文字过长，请缩小图片范围。")
            }
            val text = lines.joinToString("\n").trim()
            require(text.isNotBlank()) { "本地 PaddleOCR 未识别出文字；请换清晰图片，或为支持看图的模型启用图片输入。" }
            require(text.length <= MAX_TEXT_CHARS) { "识别文字过长，请缩小图片范围。" }
            return text
        } finally {
            deadline.remove()
            if (oriented !== bitmap) oriented.recycle()
            bitmap.recycle()
        }
    }

    private fun detect(engine: Runtime, bitmap: Bitmap): List<Box> {
        checkCancelled()
        val scale = minOf(1f, DET_LIMIT.toFloat() / max(bitmap.width, bitmap.height))
        val width = ceil(bitmap.width * scale / 32f).toInt().coerceAtLeast(1) * 32
        val height = ceil(bitmap.height * scale / 32f).toInt().coerceAtLeast(1) * 32
        val resized = Bitmap.createScaledBitmap(bitmap, width, height, true)
        try {
            val input = FloatArray(3 * width * height)
            val pixels = IntArray(width * height)
            resized.getPixels(pixels, 0, width, 0, 0, width, height)
            val plane = width * height
            for (i in pixels.indices) {
                if (i % width == 0) checkCancelled()
                val pixel = pixels[i]
                val b = pixel and 0xff
                val g = (pixel ushr 8) and 0xff
                val r = (pixel ushr 16) and 0xff
                input[i] = normalize(b, 0.485f, 0.229f)
                input[plane + i] = normalize(g, 0.456f, 0.224f)
                input[2 * plane + i] = normalize(r, 0.406f, 0.225f)
            }
            val tensor = OnnxTensor.createTensor(engine.env, FloatBuffer.wrap(input), longArrayOf(1, 3, height.toLong(), width.toLong()))
            val output = try {
                engine.det.run(mapOf(engine.det.inputNames.first() to tensor)).use { result ->
                    val batch = result[0].value as? Array<*> ?: error("PaddleOCR 检测输出格式无效")
                    check(batch.size == 1) { "PaddleOCR 检测输出批次无效" }
                    val channels = batch[0] as? Array<*> ?: error("PaddleOCR 检测输出维度无效")
                    check(channels.size == 1) { "PaddleOCR 检测输出通道无效" }
                    val rows = channels[0] as? Array<*> ?: error("PaddleOCR 检测输出维度无效")
                    check(rows.isNotEmpty() && rows.all { it is FloatArray }) { "PaddleOCR 检测输出数据无效" }
                    Array(rows.size) { rows[it] as FloatArray }
                }
            } finally { tensor.close() }
            return componentBoxes(output, bitmap.width.toFloat() / width, bitmap.height.toFloat() / height)
        } finally {
            if (resized !== bitmap) resized.recycle()
        }
    }

    private fun componentBoxes(map: Array<FloatArray>, scaleX: Float, scaleY: Float): List<Box> {
        val h = map.size
        val w = map.firstOrNull()?.size ?: return emptyList()
        if (w == 0 || h == 0) return emptyList()
        val size = w * h
        val foreground = BooleanArray(size)
        for (y in 0 until h) {
            checkCancelled()
            val row = map[y]
            for (x in 0 until w) foreground[y * w + x] = row[x] >= 0.3f
        }
        // DB maps contain narrow gaps between characters. A small horizontal
        // dilation joins the character blobs into line candidates.
        val joined = BooleanArray(size)
        for (y in 0 until h) {
            checkCancelled()
            for (x in 0 until w) {
                var on = false
                for (dy in -1..1) for (dx in -2..2) {
                    val yy = y + dy; val xx = x + dx
                    if (yy in 0 until h && xx in 0 until w && foreground[yy * w + xx]) on = true
                }
                joined[y * w + x] = on
            }
        }
        val visited = BooleanArray(size)
        val stack = IntArray(size)
        val boxes = ArrayList<Box>()
        for (start in 0 until size) {
            if (!joined[start] || visited[start]) continue
            checkCancelled()
            var top = start
            var count = 0
            var minX = w; var minY = h; var maxX = 0; var maxY = 0
            var score = 0f; var scoreCount = 0
            visited[start] = true
            stack[count++] = start
            var cursor = 0
            while (cursor < count) {
                val p = stack[cursor++]
                val x = p % w; val y = p / w
                minX = minOf(minX, x); maxX = maxOf(maxX, x)
                minY = minOf(minY, y); maxY = maxOf(maxY, y)
                if (foreground[p]) { score += map[y][x]; scoreCount++ }
                for (dy in -1..1) for (dx in -1..1) {
                    if (dx == 0 && dy == 0) continue
                    val xx = x + dx; val yy = y + dy
                    if (xx !in 0 until w || yy !in 0 until h) continue
                    val next = yy * w + xx
                    if (joined[next] && !visited[next]) { visited[next] = true; stack[count++] = next }
                }
            }
            if (scoreCount < 3 || score / scoreCount < 0.6f) continue
            val boxWidth = maxX - minX + 1
            val boxHeight = maxY - minY + 1
            if (boxWidth * boxHeight < 12) continue
            // DB's polygon unclip is not available without OpenCV; add a small
            // proportional margin to retain stroke edges during crop/resize.
            val marginX = max(2, (boxWidth * 0.10f).toInt())
            val marginY = max(2, (boxHeight * 0.10f).toInt())
            require(boxes.size < 1000) { "图片文字区域过多，请裁切后分次识别。" }
            boxes += Box(
                ((minX - marginX).coerceAtLeast(0) * scaleX).toInt().coerceAtLeast(0),
                ((minY - marginY).coerceAtLeast(0) * scaleY).toInt().coerceAtLeast(0),
                ((maxX + 1 + marginX).coerceAtMost(w) * scaleX).toInt().coerceAtLeast(1),
                ((maxY + 1 + marginY).coerceAtMost(h) * scaleY).toInt().coerceAtLeast(1),
            )
        }
        return mergeNearby(boxes).sortedWith(compareBy<Box> { it.centerY }.thenBy { it.left })
    }

    private fun mergeNearby(input: List<Box>): List<Box> {
        val pending = input.sortedWith(compareBy<Box> { it.centerY }.thenBy { it.left }).toMutableList()
        val merged = ArrayList<Box>()
        while (pending.isNotEmpty()) {
            checkCancelled()
            var line = pending.removeAt(0)
            var changed: Boolean
            do {
                changed = false
                val iterator = pending.iterator()
                while (iterator.hasNext()) {
                    val other = iterator.next()
                    val verticalOverlap = minOf(line.bottom, other.bottom) - maxOf(line.top, other.top)
                    val gap = maxOf(line.left - other.right, other.left - line.right, 0)
                    if (verticalOverlap >= minOf(line.height, other.height) * 0.45f && gap <= max(line.height, other.height) * 0.7f) {
                        line = Box(minOf(line.left, other.left), minOf(line.top, other.top), maxOf(line.right, other.right), maxOf(line.bottom, other.bottom))
                        iterator.remove()
                        changed = true
                    }
                }
            } while (changed)
            merged += line
        }
        return merged
    }

    private fun recognize(engine: Runtime, crop: Bitmap): String? {
        checkCancelled()
        var oriented = crop
        if (crop.height > crop.width * 1.35f) {
            val matrix = Matrix().apply { setRotate(90f) }
            oriented = Bitmap.createBitmap(crop, 0, 0, crop.width, crop.height, matrix, true)
        }
        try {
            // Match PaddleOCR's Chinese rec resize: preserve aspect ratio up to
            // the configured 320 px width, then pad the remainder with zeros.
            val targetWidth = ceil(REC_HEIGHT * oriented.width.toFloat() / oriented.height).toInt().coerceIn(1, REC_WIDTH)
            val inputWidth = maxOf(REC_WIDTH, targetWidth)
            val resized = Bitmap.createScaledBitmap(oriented, targetWidth, REC_HEIGHT, true)
            try {
                val pixels = IntArray(targetWidth * REC_HEIGHT)
                resized.getPixels(pixels, 0, targetWidth, 0, 0, targetWidth, REC_HEIGHT)
                val input = FloatArray(3 * REC_HEIGHT * inputWidth)
                val plane = REC_HEIGHT * inputWidth
                for (y in 0 until REC_HEIGHT) {
                    checkCancelled()
                    for (x in 0 until targetWidth) {
                        val p = pixels[y * targetWidth + x]
                        val i = y * inputWidth + x
                        input[i] = normalize(p and 0xff, 0.5f, 0.5f)
                        input[plane + i] = normalize((p ushr 8) and 0xff, 0.5f, 0.5f)
                        input[2 * plane + i] = normalize((p ushr 16) and 0xff, 0.5f, 0.5f)
                    }
                }
                val tensor = OnnxTensor.createTensor(engine.env, FloatBuffer.wrap(input), longArrayOf(1, 3, REC_HEIGHT.toLong(), inputWidth.toLong()))
                val time = try {
                    engine.rec.run(mapOf(engine.rec.inputNames.first() to tensor)).use { result ->
                        val batch = result[0].value as? Array<*> ?: error("PaddleOCR 识别输出格式无效")
                        check(batch.size == 1) { "PaddleOCR 识别输出批次无效" }
                        val time = batch[0] as? Array<*> ?: error("PaddleOCR 识别输出维度无效")
                        check(time.isNotEmpty() && time.all { it is FloatArray && (it as FloatArray).size == engine.chars.size + 2 }) {
                            "PaddleOCR 识别输出字典维度无效"
                        }
                        time
                    }
                } finally { tensor.close() }
                return decodeCtc(time, engine.chars)
            } finally { if (resized !== oriented) resized.recycle() }
        } finally { if (oriented !== crop) oriented.recycle() }
    }

    private fun decodeCtc(time: Array<*>, dictionary: List<String>): String {
        val classes = (time.firstOrNull() as? FloatArray)?.size ?: return ""
        val chars = when (classes - dictionary.size) {
            1 -> dictionary
            2 -> dictionary + " "
            else -> error("PaddleOCR 字典与模型输出不匹配")
        }
        val blank = 0
        val offset = 1
        val text = StringBuilder()
        var previous = blank
        for (t in time.indices) {
            checkCancelled()
            val row = time[t] as FloatArray
            var index = 0
            for (i in 1 until row.size) if (row[i] > row[index]) index = i
            if (index != blank && index != previous) {
                val charIndex = index - offset
                if (charIndex in chars.indices) text.append(chars[charIndex])
            }
            previous = index
        }
        return text.toString().trim()
    }

    private fun normalize(channel: Int, mean: Float, std: Float): Float = (channel / 255f - mean) / std

    private fun checkCancelled() {
        if (Thread.currentThread().isInterrupted) throw InterruptedException("Request cancelled")
        deadline.get()?.let { limit ->
            check(android.os.SystemClock.elapsedRealtime() < limit) { "本地文字识别超时，请裁切图片后重试。" }
        }
    }

    fun convert(context: Context, files: JSONArray): JSONArray = JSONArray().apply {
        for (index in 0 until files.length()) {
            val item = files.getJSONObject(index)
            if (!AppAgent.isImage(item.optString("name"), item.optString("mime"))) put(item)
            else {
                val text = text(context, Base64.decode(item.getString("content_base64"), Base64.DEFAULT))
                put(JSONObject().put("name", item.optString("name").substringBeforeLast('.').take(152) + "-ocr.txt")
                    .put("mime", "text/plain").put("source", "local_ocr")
                    .put("content_base64", Base64.encodeToString(text.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)))
            }
        }
    }
}
