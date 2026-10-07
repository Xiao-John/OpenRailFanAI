package org.openrailfanai.app

import android.app.*
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.*
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import kotlinx.coroutines.*

/** Active downloads/imports only. No UI ownership, no boot receiver, no permanent idle process. */
class UpdateTaskService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private lateinit var tasks: UpdateTasks
    private val involved = linkedSetOf<String>()
    private var wakeLock: PowerManager.WakeLock? = null
    override fun onCreate() {
        super.onCreate()
        tasks = UpdateTasks.get(this)
        val notifications = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) notifications.createNotificationChannel(NotificationChannel(CHANNEL, "软件与词典更新", NotificationManager.IMPORTANCE_LOW))
        ServiceCompat.startForeground(this, ID, notification(), if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0)
        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "$packageName:updates").apply { setReferenceCounted(false); acquire(35 * 60 * 1000L) }
        scope.launch {
            while (true) {
                delay(750)
                if (!tasks.hasForegroundWork) {
                    ServiceCompat.stopForeground(this@UpdateTaskService, ServiceCompat.STOP_FOREGROUND_REMOVE)
                    // A completed APK is offered in settings; never open the installer from the background.
                    notifications.notify(ID + 1, NotificationCompat.Builder(this@UpdateTaskService, CHANNEL)
                        .setSmallIcon(android.R.drawable.stat_sys_download_done).setContentTitle("更新任务已结束")
                        .setContentText(involved.joinToString("；") { kind -> if (kind == "software") tasks.softwareMessage else tasks.dictionaryMessage })
                        .setStyle(NotificationCompat.BigTextStyle().bigText(involved.joinToString("\n") { kind ->
                            if (kind == "software") "软件：${tasks.softwareMessage}" else "词典：${tasks.dictionaryMessage}"
                        }))
                        .setContentIntent(openApp()).setAutoCancel(true).build())
                    stopSelf(); break
                }
                notifications.notify(ID, notification())
            }
        }
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "cancel") tasks.cancel(intent.getStringExtra("kind").orEmpty())
        else if (intent != null) {
            intent.getStringExtra("kind")?.let { involved.add(it) }
            wakeLock?.acquire(35 * 60 * 1000L); tasks.execute(intent)
        }
        return START_NOT_STICKY
    }
    private fun openApp(): PendingIntent = PendingIntent.getActivity(this, 0,
        Intent(this, MainComposeActivity::class.java).putExtra("open_update_settings", true).putExtra("update_focus", tasks.foregroundKinds().firstOrNull() ?: involved.firstOrNull() ?: "software").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    private fun notification(): Notification {
        val kinds = tasks.foregroundKinds()
        val lines = kinds.map { kind ->
            val event = if (kind == "software") tasks.softwareProgress else tasks.dictionaryProgress
            val total = event?.optLong("total", -1) ?: -1
            val done = event?.optLong("completed", -1) ?: -1
            val stage = event?.optString("stage").orEmpty()
            val label = when (stage) { "download" -> "下载"; "verify", "signature", "validate" -> "校验"; "import" -> "导入"; "commit" -> "保存"; "refresh" -> "确认版本"; else -> "准备" }
            (if (kind == "software") "软件" else "词典") + " · $label" +
                if (stage == "download" && total > 0 && done in 0..total) " ${done * 100 / total}%（${formatUpdateBytes(done)} / ${formatUpdateBytes(total)}）" else "中…"
        }
        val builder = NotificationCompat.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle("RailFanAI 后台更新").setContentText(lines.joinToString("；").ifBlank { "正在准备更新…" })
            .setStyle(NotificationCompat.BigTextStyle().bigText(lines.joinToString("\n"))).setContentIntent(openApp())
            .setOngoing(true).setOnlyAlertOnce(true).setSilent(true)
        val event = if (kinds.size == 1 && kinds.first() == "software") tasks.softwareProgress else if (kinds.size == 1) tasks.dictionaryProgress else null
        val total = event?.optLong("total", -1) ?: -1
        val done = event?.optLong("completed", -1) ?: -1
        val measured = event?.optString("stage") == "download" && total > 0 && done in 0..total
        builder.setProgress(100, if (measured) (done * 100 / total).toInt() else 0, !measured)
        kinds.forEach { kind ->
            val pending = PendingIntent.getService(this, if (kind == "software") 1 else 2,
                Intent(this, UpdateTaskService::class.java).setAction("cancel").putExtra("kind", kind), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            builder.addAction(0, if (kind == "software") "取消软件下载" else "取消词典更新", pending)
        }
        return builder.build()
    }
    override fun onTimeout(startId: Int, fgsType: Int) {
        tasks.stopForegroundTasks("系统已停止后台更新，请返回设置确认状态。")
        stopForeground(STOP_FOREGROUND_REMOVE); stopSelf()
    }
    override fun onDestroy() {
        tasks.stopForegroundTasks("后台更新服务已结束，请返回设置确认状态。")
        scope.cancel(); wakeLock?.takeIf { it.isHeld }?.release()
        super.onDestroy()
    }
    override fun onBind(intent: Intent?) = null
    companion object { private const val CHANNEL = "railfan_updates"; private const val ID = 4200 }
}
