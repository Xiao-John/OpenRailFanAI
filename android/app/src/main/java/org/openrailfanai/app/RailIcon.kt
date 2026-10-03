package org.openrailfanai.app

import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp

/** Small native vector marks used by Main's controls and result cards. */
@Composable
internal fun RailIcon(name: String, modifier: Modifier = Modifier, tint: Color = NativeColors.blue, semanticTag: String? = null) {
    Canvas(modifier.testTag(semanticTag ?: "icon-$name")) {
        val s = size.minDimension
        val stroke = (s * .085f).coerceAtLeast(1.5.dp.toPx())
        val left = (size.width - s) / 2f
        val top = (size.height - s) / 2f
        fun p(x: Float, y: Float) = Offset(left + x * s, top + y * s)
        fun line(x1: Float, y1: Float, x2: Float, y2: Float, color: Color = tint, width: Float = stroke) =
            drawLine(color, p(x1, y1), p(x2, y2), width, cap = StrokeCap.Round)
        when (name) {
            "calendar" -> {
                val calendarYScale = 1.15f
                fun calendarPoint(x: Float, y: Float) = p(x, .5f + (y - .5f) * calendarYScale)
                fun calendarLine(x1: Float, y1: Float, x2: Float, y2: Float) =
                    drawLine(tint, calendarPoint(x1, y1), calendarPoint(x2, y2), stroke, cap = StrokeCap.Round)
                drawRoundRect(tint, calendarPoint(.12f, .2f), Size(.76f * s, .68f * s * calendarYScale),
                    androidx.compose.ui.geometry.CornerRadius(.1f * s), style = Stroke(stroke))
                calendarLine(.12f,.38f,.88f,.38f); calendarLine(.34f,.12f,.34f,.3f); calendarLine(.66f,.12f,.66f,.3f)
                drawRoundRect(tint, calendarPoint(.61f,.52f), Size(.11f * s, .11f * s),
                    androidx.compose.ui.geometry.CornerRadius(.018f * s))
            }
            "clock" -> {
                drawCircle(tint, s*.38f, p(.5f,.5f), style=Stroke(stroke))
                line(.5f,.25f,.5f,.5f); line(.5f,.5f,.7f,.62f)
            }
            "link" -> {
                val linkStroke = (s * .09f).coerceAtLeast(1.5.dp.toPx())
                val linkSize = Size(.48f * s, .24f * s)
                fun chain(centerX: Float, centerY: Float) {
                    val center = p(centerX, centerY)
                    rotate(-45f, center) {
                        drawRoundRect(
                            tint,
                            Offset(center.x - linkSize.width / 2f, center.y - linkSize.height / 2f),
                            linkSize,
                            androidx.compose.ui.geometry.CornerRadius(linkSize.height / 2f),
                            style = Stroke(linkStroke),
                        )
                    }
                }
                chain(.38f, .62f)
                chain(.62f, .38f)
            }
            "info" -> {
                drawCircle(tint,s*.39f,p(.5f,.5f),style=Stroke(stroke)); line(.5f,.45f,.5f,.72f); drawCircle(tint,s*.045f,p(.5f,.3f))
            }
            "chevron" -> { line(.35f,.2f,.68f,.5f); line(.68f,.5f,.35f,.8f) }
            "chevron-down" -> { line(.2f,.35f,.5f,.68f); line(.5f,.68f,.8f,.35f) }
            "chevron-up" -> { line(.1f,.65f,.5f,.32f); line(.5f,.32f,.9f,.65f) }
            "chevron-left" -> { line(.7f,.08f,.32f,.5f); line(.32f,.5f,.7f,.92f) }
            "search" -> {
                if (semanticTag == "history-search-icon") {
                    drawDesignGlyph(NativeDesignPaths.historySearch, if (NativeColors.dark) tint else null)
                } else {
                    drawCircle(tint,s*.27f,p(.42f,.42f),style=Stroke(stroke)); line(.62f,.62f,.86f,.86f)
                }
            }
            "train-search" -> {
                drawDesignGlyph(NativeDesignPaths.trainSearch, if (NativeColors.dark) tint else null)
            }
            "train-logo" -> {
                // Header and reply avatars use their own source glyph, including
                // the original window and headlight fills; their layout slots stay fixed.
                val glyph = if (semanticTag == "main-assistant-avatar-icon")
                    NativeDesignPaths.assistantTrain else NativeDesignPaths.brandTrain
                if (semanticTag == "main-assistant-avatar-icon") {
                    // At the projected 28px height, a source shade row lands
                    // exactly between pixel centres. Preserve that row instead
                    // of dropping that source colour detail during rasterisation.
                    val sourceRowPhase = .25.dp.toPx()
                    withTransform({ translate(top = sourceRowPhase) }) {
                        drawDesignGlyph(glyph, if (NativeColors.dark) tint else null)
                    }
                } else drawDesignGlyph(glyph, if (NativeColors.dark) tint else null)
            }
            "person" -> {
                fun personPoint(x: Float, y: Float) = p(x, .5f + (y - .5f) * 1.25f)
                drawCircle(tint,s*.17f,personPoint(.5f,.29f))
                val shoulders = Path().apply {
                    moveTo(personPoint(.14f,.88f).x,personPoint(.14f,.88f).y)
                    cubicTo(personPoint(.14f,.67f).x,personPoint(.14f,.67f).y,personPoint(.29f,.53f).x,personPoint(.29f,.53f).y,personPoint(.5f,.53f).x,personPoint(.5f,.53f).y)
                    cubicTo(personPoint(.71f,.53f).x,personPoint(.71f,.53f).y,personPoint(.86f,.67f).x,personPoint(.86f,.67f).y,personPoint(.86f,.88f).x,personPoint(.86f,.88f).y)
                    close()
                }
                drawPath(shoulders,tint)
            }
            "cloud-error" -> {
                val error = NativeColors.danger
                fun cloudPoint(x: Float, y: Float) = Offset(x * size.width, y * size.height)
                val cloudStroke = (size.height * .065f).coerceAtLeast(1.5.dp.toPx())
                val cloud=Path().apply { moveTo(cloudPoint(.13f,.68f).x,cloudPoint(.13f,.68f).y); cubicTo(cloudPoint(.04f,.54f).x,cloudPoint(.04f,.54f).y,cloudPoint(.08f,.38f).x,cloudPoint(.08f,.38f).y,cloudPoint(.22f,.35f).x,cloudPoint(.22f,.35f).y); cubicTo(cloudPoint(.25f,.17f).x,cloudPoint(.25f,.17f).y,cloudPoint(.42f,.1f).x,cloudPoint(.42f,.1f).y,cloudPoint(.55f,.16f).x,cloudPoint(.55f,.16f).y); cubicTo(cloudPoint(.67f,.2f).x,cloudPoint(.67f,.2f).y,cloudPoint(.72f,.3f).x,cloudPoint(.72f,.3f).y,cloudPoint(.74f,.4f).x,cloudPoint(.74f,.4f).y); cubicTo(cloudPoint(.85f,.4f).x,cloudPoint(.85f,.4f).y,cloudPoint(.91f,.5f).x,cloudPoint(.91f,.5f).y,cloudPoint(.88f,.62f).x,cloudPoint(.88f,.62f).y); lineTo(cloudPoint(.83f,.67f).x,cloudPoint(.83f,.67f).y); lineTo(cloudPoint(.13f,.68f).x,cloudPoint(.13f,.68f).y); close() }
                drawPath(cloud,error,style=Stroke(cloudStroke,cap=StrokeCap.Round,join=androidx.compose.ui.graphics.StrokeJoin.Round))
                val alertCenter = cloudPoint(.78f,.72f)
                drawCircle(error,size.height*.235f,alertCenter)
                drawLine(Color.White, cloudPoint(.78f,.62f), cloudPoint(.78f,.75f), cloudStroke*.72f, cap = StrokeCap.Round)
                drawCircle(Color.White,size.height*.022f,cloudPoint(.78f,.82f))
            }
            "file" -> {
                val page = Path().apply {
                    moveTo(p(.24f,.1f).x,p(.24f,.1f).y); lineTo(p(.6f,.1f).x,p(.6f,.1f).y)
                    lineTo(p(.79f,.29f).x,p(.79f,.29f).y); lineTo(p(.79f,.9f).x,p(.79f,.9f).y)
                    lineTo(p(.24f,.9f).x,p(.24f,.9f).y); close()
                }
                drawPath(page,tint,style=Stroke(stroke,join=androidx.compose.ui.graphics.StrokeJoin.Round))
                line(.6f,.1f,.6f,.3f); line(.6f,.3f,.79f,.3f)
                line(.36f,.44f,.65f,.44f); line(.36f,.6f,.65f,.6f); line(.36f,.76f,.65f,.76f)
            }
            "plus" -> { line(.5f,.17f,.5f,.83f); line(.17f,.5f,.83f,.5f) }
            "back" -> { line(.76f,.5f,.22f,.5f); line(.22f,.5f,.48f,.23f); line(.22f,.5f,.48f,.77f) }
            "down" -> { line(.5f,.2f,.5f,.78f); line(.22f,.52f,.5f,.8f); line(.5f,.8f,.78f,.52f) }
            "up" -> {
                val arrowStroke = 2.appDp.toPx()
                line(.5f,.83f,.5f,.17f,width = arrowStroke)
                line(.22f,.43f,.5f,.17f,width = arrowStroke)
                line(.5f,.17f,.78f,.43f,width = arrowStroke)
            }
            "edit" -> {
                if (semanticTag == "icon-edit") {
                    drawDesignGlyph(NativeDesignPaths.historyEdit, if (NativeColors.dark) tint else null)
                } else {
                    line(.25f,.78f,.32f,.53f); line(.32f,.53f,.69f,.16f); line(.69f,.16f,.84f,.31f); line(.84f,.31f,.47f,.68f); line(.47f,.68f,.25f,.78f)
                    line(.18f,.88f,.82f,.88f)
                }
            }
            "trash" -> {
                if (semanticTag == "icon-trash") {
                    drawDesignGlyph(NativeDesignPaths.historyTrash, if (NativeColors.dark) tint else null)
                } else {
                    line(.28f,.3f,.33f,.84f); line(.33f,.84f,.67f,.84f); line(.67f,.84f,.72f,.3f); line(.22f,.3f,.78f,.3f)
                    line(.38f,.18f,.62f,.18f); line(.42f,.42f,.42f,.71f); line(.58f,.42f,.58f,.71f)
                }
            }
            "history" -> {
                val historyScale = 1.15f
                fun hp(x: Float, y: Float) = p(.5f + (x - .5f) * historyScale, .5f + (y - .5f) * historyScale)
                val historyStroke = (s * .095f).coerceAtLeast(1.5.dp.toPx())
                drawArc(tint,35f,300f,false,hp(.12f,.12f),Size(.76f*s*historyScale,.76f*s*historyScale),style=Stroke(historyStroke,cap=StrokeCap.Round))
                drawLine(tint, hp(.16f,.14f), hp(.17f,.39f), historyStroke, cap = StrokeCap.Round)
                drawLine(tint, hp(.16f,.14f), hp(.4f,.14f), historyStroke, cap = StrokeCap.Round)
                drawLine(tint, hp(.5f,.3f), hp(.5f,.52f), historyStroke, cap = StrokeCap.Round)
                drawLine(tint, hp(.5f,.52f), hp(.67f,.62f), historyStroke, cap = StrokeCap.Round)
            }
            "settings" -> {
                val gear = Path().apply {
                    moveTo(p(.39f,.08f).x,p(.39f,.08f).y)
                    lineTo(p(.61f,.08f).x,p(.61f,.08f).y); lineTo(p(.66f,.22f).x,p(.66f,.22f).y)
                    lineTo(p(.78f,.28f).x,p(.78f,.28f).y); lineTo(p(.92f,.22f).x,p(.92f,.22f).y)
                    lineTo(p(.92f,.39f).x,p(.92f,.39f).y); lineTo(p(.78f,.44f).x,p(.78f,.44f).y)
                    lineTo(p(.78f,.56f).x,p(.78f,.56f).y); lineTo(p(.92f,.61f).x,p(.92f,.61f).y)
                    lineTo(p(.92f,.78f).x,p(.92f,.78f).y); lineTo(p(.78f,.72f).x,p(.78f,.72f).y)
                    lineTo(p(.66f,.78f).x,p(.66f,.78f).y); lineTo(p(.61f,.92f).x,p(.61f,.92f).y)
                    lineTo(p(.39f,.92f).x,p(.39f,.92f).y); lineTo(p(.34f,.78f).x,p(.34f,.78f).y)
                    lineTo(p(.22f,.72f).x,p(.22f,.72f).y); lineTo(p(.08f,.78f).x,p(.08f,.78f).y)
                    lineTo(p(.08f,.61f).x,p(.08f,.61f).y); lineTo(p(.22f,.56f).x,p(.22f,.56f).y)
                    lineTo(p(.22f,.44f).x,p(.22f,.44f).y); lineTo(p(.08f,.39f).x,p(.08f,.39f).y)
                    lineTo(p(.08f,.22f).x,p(.08f,.22f).y); lineTo(p(.22f,.28f).x,p(.22f,.28f).y)
                    lineTo(p(.34f,.22f).x,p(.34f,.22f).y); close()
                }
                drawPath(gear, tint, style = Stroke(stroke, cap = StrokeCap.Round, join = androidx.compose.ui.graphics.StrokeJoin.Round))
                drawCircle(tint, s * .13f, p(.5f,.5f), style = Stroke(stroke))
            }
            "help" -> {
                drawCircle(tint, s * .39f, p(.5f,.5f), style = Stroke(stroke))
                val question = Path().apply {
                    moveTo(p(.37f,.38f).x,p(.37f,.38f).y)
                    cubicTo(p(.38f,.2f).x,p(.38f,.2f).y,p(.65f,.2f).x,p(.65f,.2f).y,p(.66f,.39f).x,p(.66f,.39f).y)
                    cubicTo(p(.66f,.51f).x,p(.66f,.51f).y,p(.5f,.52f).x,p(.5f,.52f).y,p(.5f,.65f).x,p(.5f,.65f).y)
                }
                drawPath(question, tint, style = Stroke(stroke, cap = StrokeCap.Round))
                drawCircle(tint, s * .04f, p(.5f,.79f))
            }
            "close" -> {
                if (semanticTag == "history-close-icon") {
                    drawDesignGlyph(NativeDesignPaths.historyClose, if (NativeColors.dark) tint else null)
                } else { line(.25f,.25f,.75f,.75f); line(.75f,.25f,.25f,.75f) }
            }
            "stop" -> drawRoundRect(tint,p(.25f,.25f),Size(.5f*s,.5f*s),androidx.compose.ui.geometry.CornerRadius(.04f*s))
            "send" -> {
                val path=Path().apply { moveTo(p(.12f,.12f).x,p(.12f,.12f).y); lineTo(p(.9f,.5f).x,p(.9f,.5f).y); lineTo(p(.12f,.88f).x,p(.12f,.88f).y); lineTo(p(.3f,.52f).x,p(.3f,.52f).y); lineTo(p(.65f,.5f).x,p(.65f,.5f).y); lineTo(p(.3f,.48f).x,p(.3f,.48f).y); close() }
                drawPath(path,tint)
            }
            "check" -> { line(.2f,.52f,.43f,.73f,Color.White,stroke); line(.43f,.73f,.82f,.28f,Color.White,stroke) }
            "warning" -> { line(.5f,.25f,.5f,.59f,Color.White,stroke); drawCircle(Color.White,s*.04f,p(.5f,.76f)) }
            "copy" -> { drawRoundRect(tint,p(.28f,.18f),Size(.58f*s,.63f*s),androidx.compose.ui.geometry.CornerRadius(.06f*s),style=Stroke(stroke)); line(.17f,.34f,.17f,.86f); line(.17f,.86f,.69f,.86f) }
            "refresh" -> { drawArc(tint,205f,250f,false,p(.15f,.15f),Size(.7f*s,.7f*s),style=Stroke(stroke,cap=StrokeCap.Round)); line(.14f,.2f,.16f,.45f); line(.14f,.2f,.39f,.18f) }
            else -> drawCircle(tint,s*.3f,p(.5f,.5f),style=Stroke(stroke))
        }
    }
}
