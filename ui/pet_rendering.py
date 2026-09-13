"""Character-package and fallback drawing kept outside the desktop window."""

import math

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QRegion,
)

from core.character_package import CharacterPackage
from core.pet_animator import PetAnimator, PetState


class PetRenderer:
    """Own package frame caches and all painter-only character rendering."""

    def __init__(
        self,
        animator: PetAnimator,
        *,
        design_size: int = 180,
        edge_art_size: int = 96,
        edge_reveal_padding: int = 4,
    ):
        self.animator = animator
        self.design_size = design_size
        self.edge_art_size = edge_art_size
        self.edge_reveal_padding = edge_reveal_padding
        self.package = None
        self.character_frames = {}
        self._scaled_cache = {}
        self.dedicated_edge_states = set()
        self.edge_subject_bounds = {}
        self.edge_design_reveals = {}

    def set_package(self, package: CharacterPackage | None) -> None:
        self.package = package
        self.character_frames.clear()
        self._scaled_cache.clear()
        self.dedicated_edge_states.clear()
        self.edge_subject_bounds.clear()
        self.edge_design_reveals.clear()
        if not package:
            return
        source_cache = {}
        for state, animation in package.animations.items():
            frames = []
            for frame_spec in animation.frames:
                path = frame_spec.file
                source = source_cache.get(path)
                if source is None:
                    source = QPixmap(str(path))
                    source_cache[path] = source
                if source.isNull():
                    continue
                pixmap = source
                if frame_spec.rect is not None:
                    pixmap = source.copy(QRect(*frame_spec.rect))
                if not pixmap.isNull():
                    frames.append(pixmap)
            if not frames:
                continue
            self.character_frames[state] = (frames, animation)
            if state.startswith("edge_") and any(
                "edge" in {part.lower() for part in frame_spec.file.parts}
                for frame_spec in animation.frames
            ):
                self.dedicated_edge_states.add(state)

    def _scaled_frame(self, frame: QPixmap, size: int) -> QPixmap:
        """Scale each source frame at most once per render size.

        ``paintEvent`` can run more often than an animation frame changes.
        Keeping the scaled pixmap avoids repeating a relatively expensive
        smooth transformation on every timer tick.
        """
        key = (int(frame.cacheKey()), int(size))
        cached = self._scaled_cache.get(key)
        if cached is None:
            cached = frame.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._scaled_cache[key] = cached
        return cached

    def draw(
        self,
        painter: QPainter,
        *,
        tick: int,
        state_started_tick: int,
        dock_side: str | None,
        peek_size: int,
        window_width: int,
        edge_art: bool,
    ) -> None:
        if self.package:
            if edge_art:
                self.draw_package_peek(
                    painter,
                    tick=tick,
                    state_started_tick=state_started_tick,
                    dock_side=dock_side,
                    peek_size=peek_size,
                    window_width=window_width,
                )
            else:
                self.draw_package_character(
                    painter,
                    tick=tick,
                    state_started_tick=state_started_tick,
                )
        elif edge_art:
            self.draw_fallback_peek(
                painter,
                dock_side=dock_side,
                peek_size=peek_size,
                window_width=window_width,
            )
        else:
            self.draw_fallback_character(painter, tick=tick)

    def entry_for_state(self, state_name: str):
        entry = self.character_frames.get(state_name)
        if not entry and state_name.startswith("edge_"):
            entry = self.character_frames.get("edge_docked")
        if not entry:
            entry = self.character_frames.get("idle")
        return entry

    def current_package_frame(
        self,
        state_name: str,
        *,
        tick: int,
        state_started_tick: int,
    ):
        entry = self.entry_for_state(state_name)
        if not entry:
            return None
        frames, animation = entry
        frame_index = int(
            (tick - state_started_tick) * animation.fps / 25
        )
        if animation.playback == "ping_pong" and len(frames) > 1:
            cycle = len(frames) * 2 - 2
            position = frame_index % cycle
            frame_index = (
                position if position < len(frames) else cycle - position
            )
        elif animation.loop:
            frame_index %= len(frames)
        else:
            frame_index = min(frame_index, len(frames) - 1)
        return frames[frame_index]

    def draw_package_character(
        self,
        painter: QPainter,
        *,
        tick: int,
        state_started_tick: int,
    ) -> None:
        state_name = self.animator.current_state.value
        entry = self.entry_for_state(state_name)
        frame = self.current_package_frame(
            state_name,
            tick=tick,
            state_started_tick=state_started_tick,
        )
        if frame is None:
            self.draw_fallback_character(painter, tick=tick)
            return
        _frames, animation = entry
        scaled = self._scaled_frame(frame, 172)
        if animation.legacy_effects:
            x = (self.design_size - scaled.width()) // 2
            y = self.design_size - scaled.height()
        else:
            factor_x = scaled.width() / max(1, frame.width())
            factor_y = scaled.height() / max(1, frame.height())
            x = round(90 - animation.anchor[0] * factor_x)
            y = round(168 - animation.anchor[1] * factor_y)
        state = self.animator.current_state
        scale_x = 1.0
        scale_y = 1.0
        rotation = 0.0
        if animation.legacy_effects:
            if state == PetState.IDLE:
                y += int(math.sin(tick * 0.09) * 3)
            elif state == PetState.BLINK:
                scale_y = 0.96
            elif state == PetState.STRETCH:
                scale_x = 0.96
                scale_y = 1.06
                y -= 5
            elif state == PetState.WAVE:
                rotation = math.sin(tick * 0.28) * 4.5
            elif state == PetState.NOD:
                y += int(abs(math.sin(tick * 0.25)) * 6)
            elif state == PetState.SLEEP:
                rotation = -3.5
                y += 4
            elif state == PetState.DRAGGING:
                rotation = math.sin(tick * 0.34) * 7
            elif state == PetState.TALKING:
                scale_x = 1.0 + math.sin(tick * 0.28) * 0.018
                scale_y = 1.0 - math.sin(tick * 0.28) * 0.012
            elif state in {
                PetState.ALERTING,
                PetState.ALERTING_IMPORTANT,
                PetState.CELEBRATE_LIVE,
            }:
                y -= int(abs(math.sin(tick * 0.24)) * 12)
                rotation = math.sin(tick * 0.3) * 3
        painter.save()
        center_x = x + scaled.width() / 2
        center_y = y + scaled.height() / 2
        painter.translate(center_x, center_y)
        painter.rotate(rotation)
        painter.scale(scale_x, scale_y)
        painter.translate(-center_x, -center_y)
        painter.drawPixmap(x, y, scaled)
        painter.restore()

    def draw_package_peek(
        self,
        painter: QPainter,
        *,
        tick: int,
        state_started_tick: int,
        dock_side: str | None,
        peek_size: int,
        window_width: int,
    ) -> None:
        current = self.animator.current_state.value
        directional = (
            current
            if current.startswith("edge_")
            else f"edge_idle_{dock_side}"
            if dock_side
            else "edge_docked"
        )
        frame = self.current_package_frame(
            directional,
            tick=tick,
            state_started_tick=state_started_tick,
        )
        if frame is None:
            self.draw_fallback_peek(
                painter,
                dock_side=dock_side,
                peek_size=peek_size,
                window_width=window_width,
            )
            return
        if directional in self.dedicated_edge_states:
            self._draw_directional_package_peek(
                painter,
                frame,
                dock_side=dock_side,
            )
            return
        peek = peek_size * self.design_size / max(1, window_width)
        scaled = self._scaled_frame(frame, 64)
        if dock_side == "left":
            cx, cy = self.design_size - peek / 2, 90
        elif dock_side == "right":
            cx, cy = peek / 2, 90
        elif dock_side == "top":
            cx, cy = 90, self.design_size - peek / 2
        else:
            cx, cy = 90, peek / 2
        painter.drawPixmap(
            int(cx - scaled.width() / 2),
            int(cy - scaled.height() / 2),
            scaled,
        )

    def _draw_directional_package_peek(
        self,
        painter: QPainter,
        frame: QPixmap,
        *,
        dock_side: str | None,
    ) -> None:
        scaled, subject = self.scaled_edge_art(frame)
        reveal = self.dedicated_edge_design_reveal(dock_side)
        x = (self.design_size - scaled.width()) // 2
        y = (self.design_size - scaled.height()) // 2
        if dock_side == "left":
            x = self.design_size - reveal - subject.left()
        elif dock_side == "right":
            x = reveal - subject.right() - 1
        elif dock_side == "top":
            y = self.design_size - reveal - subject.top()
        elif dock_side == "bottom":
            y = reveal - subject.bottom() - 1
        painter.drawPixmap(x, y, scaled)

    def scaled_edge_art(self, frame):
        scaled = self._scaled_frame(frame, self.edge_art_size)
        cache_key = frame.cacheKey()
        subject = self.edge_subject_bounds.get(cache_key)
        if subject is None:
            subject = QRegion(scaled.mask()).boundingRect()
            if subject.isEmpty():
                subject = scaled.rect()
            self.edge_subject_bounds[cache_key] = subject
        return scaled, subject

    def dedicated_edge_design_reveal(self, side) -> int:
        cached = self.edge_design_reveals.get(side)
        if cached is not None:
            return cached
        extent = 0
        for phase in ("enter", "idle", "hover", "exit"):
            entry = self.character_frames.get(f"edge_{phase}_{side}")
            if not entry:
                continue
            frames, _animation = entry
            for frame in frames:
                _scaled, subject = self.scaled_edge_art(frame)
                span = (
                    subject.width()
                    if side in {"left", "right"}
                    else subject.height()
                )
                extent = max(extent, span)
        reveal = min(
            self.edge_art_size,
            max(1, extent) + self.edge_reveal_padding,
        )
        self.edge_design_reveals[side] = reveal
        return reveal

    def draw_fallback_character(self, painter: QPainter, *, tick: int) -> None:
        state = self.animator.current_state
        cx = 90
        float_y = math.sin(tick * 0.09) * 2.5 if state == PetState.IDLE else 0
        if state == PetState.ALERTING:
            float_y = -abs(math.sin(tick * 0.24)) * 15
        elif state == PetState.NOD:
            float_y = abs(math.sin(tick * 0.22)) * 5
        elif state == PetState.STRETCH:
            float_y = -4

        shadow_w = 88 + (8 if state == PetState.ALERTING else 0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(44, 34, 85, 42))
        painter.drawEllipse(int(cx - shadow_w / 2), 149, int(shadow_w), 13)

        painter.setPen(
            QPen(
                QColor("#7668E7"),
                15,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        tail = QPainterPath()
        tail.moveTo(126, 119 + float_y)
        tail.cubicTo(158, 112 + float_y, 153, 84 + float_y, 137, 91 + float_y)
        painter.drawPath(tail)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#6C5FDC"))
        left_ear = QPainterPath()
        left_ear.moveTo(51, 69 + float_y)
        left_ear.cubicTo(37, 31 + float_y, 47, 23 + float_y, 72, 55 + float_y)
        painter.drawPath(left_ear)
        right_ear = QPainterPath()
        right_ear.moveTo(129, 69 + float_y)
        right_ear.cubicTo(
            143,
            31 + float_y,
            133,
            23 + float_y,
            108,
            55 + float_y,
        )
        painter.drawPath(right_ear)
        painter.setBrush(QColor("#F4A9C3"))
        painter.drawPolygon(
            QPoint(49, 38 + int(float_y)),
            QPoint(57, 61 + int(float_y)),
            QPoint(65, 54 + int(float_y)),
        )
        painter.drawPolygon(
            QPoint(131, 38 + int(float_y)),
            QPoint(123, 61 + int(float_y)),
            QPoint(115, 54 + int(float_y)),
        )

        body_gradient = QLinearGradient(52, 55, 128, 144)
        body_gradient.setColorAt(0, QColor("#8A7CF2"))
        body_gradient.setColorAt(0.55, QColor("#7467E8"))
        body_gradient.setColorAt(1, QColor("#5D50CD"))
        painter.setBrush(QBrush(body_gradient))
        painter.setPen(QPen(QColor(69, 57, 155, 95), 1.4))
        painter.drawRoundedRect(43, int(54 + float_y), 94, 92, 45, 45)

        highlight = QRadialGradient(72, 67 + float_y, 47)
        highlight.setColorAt(0, QColor(255, 255, 255, 72))
        highlight.setColorAt(1, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(highlight)
        painter.drawEllipse(48, int(57 + float_y), 84, 72)
        painter.setBrush(QColor("#F8F6FF"))
        painter.drawEllipse(56, int(79 + float_y), 68, 58)

        eye_y = 92 + float_y
        self._draw_face(painter, cx, eye_y, state, tick=tick)
        self._draw_arms(painter, state, float_y, tick=tick)

        if state == PetState.SLEEP:
            painter.setPen(QPen(QColor("#7568D7"), 2))
            painter.drawText(130, int(63 + float_y), "z")
            painter.drawText(142, int(48 + float_y), "Z")
        elif state == PetState.ALERTING:
            painter.setPen(
                QPen(
                    QColor("#F0A526"),
                    3,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawLine(35, 52, 25, 42)
            painter.drawLine(145, 52, 155, 42)
            painter.drawLine(90, 30, 90, 17)

    @staticmethod
    def _draw_face(
        painter,
        _cx,
        eye_y,
        state,
        *,
        tick: int,
    ) -> None:
        dark = QColor("#312A59")
        if state in (PetState.BLINK, PetState.SLEEP):
            painter.setPen(
                QPen(
                    dark,
                    3,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawLine(67, int(eye_y), 78, int(eye_y))
            painter.drawLine(102, int(eye_y), 113, int(eye_y))
        else:
            eye_h = 22 if state == PetState.DRAGGING else 18
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(64, int(eye_y - 8), 18, eye_h)
            painter.drawEllipse(98, int(eye_y - 8), 18, eye_h)
            pupil_shift = int(math.sin(tick * 0.035) * 2)
            painter.setBrush(dark)
            painter.drawEllipse(69 + pupil_shift, int(eye_y - 3), 9, 11)
            painter.drawEllipse(103 + pupil_shift, int(eye_y - 3), 9, 11)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(71 + pupil_shift, int(eye_y - 1), 3, 3)
            painter.drawEllipse(105 + pupil_shift, int(eye_y - 1), 3, 3)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(245, 125, 158, 115))
        painter.drawEllipse(56, int(eye_y + 12), 14, 7)
        painter.drawEllipse(110, int(eye_y + 12), 14, 7)

        painter.setPen(
            QPen(
                dark,
                2,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        if state in (PetState.ALERTING, PetState.STRETCH):
            painter.setBrush(QColor("#EB6B8C"))
            painter.drawEllipse(82, int(eye_y + 13), 16, 13)
        elif state == PetState.TALKING:
            mouth_h = 7 + int(abs(math.sin(tick * 0.28)) * 7)
            painter.setBrush(QColor("#EB6B8C"))
            painter.drawEllipse(84, int(eye_y + 15), 12, mouth_h)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            mouth = QPainterPath()
            mouth.moveTo(82, eye_y + 16)
            mouth.quadTo(86, eye_y + 21, 90, eye_y + 16)
            mouth.quadTo(94, eye_y + 21, 98, eye_y + 16)
            painter.drawPath(mouth)

    @staticmethod
    def _draw_arms(painter, state, float_y, *, tick: int) -> None:
        painter.setPen(
            QPen(
                QColor("#695BD7"),
                13,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        if state == PetState.WAVE:
            wave = math.sin(tick * 0.33) * 10
            painter.drawLine(
                48,
                int(105 + float_y),
                int(27 + wave),
                int(75 + float_y),
            )
            painter.drawLine(132, int(105 + float_y), 143, int(124 + float_y))
        elif state == PetState.STRETCH:
            painter.drawLine(51, int(102 + float_y), 30, int(72 + float_y))
            painter.drawLine(129, int(102 + float_y), 150, int(72 + float_y))
        else:
            painter.drawLine(48, int(109 + float_y), 37, int(126 + float_y))
            painter.drawLine(132, int(109 + float_y), 143, int(126 + float_y))

    def draw_fallback_peek(
        self,
        painter,
        *,
        dock_side: str | None,
        peek_size: int,
        window_width: int,
    ) -> None:
        peek = peek_size * self.design_size / max(1, window_width)
        if dock_side == "left":
            cx, cy = self.design_size - peek / 2, 90
        elif dock_side == "right":
            cx, cy = peek / 2, 90
        elif dock_side == "top":
            cx, cy = 90, self.design_size - peek / 2
        else:
            cx, cy = 90, peek / 2

        painter.setPen(QPen(QColor(70, 57, 157, 100), 1))
        gradient = QRadialGradient(cx - 8, cy - 10, 38)
        gradient.setColorAt(0, QColor("#9184F6"))
        gradient.setColorAt(1, QColor("#6255D1"))
        painter.setBrush(gradient)
        painter.drawEllipse(int(cx - 31), int(cy - 29), 62, 58)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(int(cx - 16), int(cy - 6), 10, 13)
        painter.drawEllipse(int(cx + 6), int(cy - 6), 10, 13)
        painter.setBrush(QColor("#332B5C"))
        painter.drawEllipse(int(cx - 12), int(cy - 2), 5, 7)
        painter.drawEllipse(int(cx + 9), int(cy - 2), 5, 7)
        painter.setPen(QPen(QColor("#332B5C"), 2))
        painter.drawArc(
            int(cx - 6),
            int(cy + 8),
            12,
            8,
            0,
            -180 * 16,
        )
