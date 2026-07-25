import logging
import math

from PyQt6.QtCore import (
    Qt, QPoint, QRect, QPropertyAnimation, QEasingCurve, QTimer
)
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QPen, QPainterPath, QMouseEvent,
    QRadialGradient, QLinearGradient, QPixmap
)

from core.character_package import CharacterPackage
from core.config import ConfigManager
from core.pet_animator import PetAnimator, PetState
from ui.chat_window import ChatBubbleWindow


LOGGER = logging.getLogger("desktop_pet.pet_window")


class PetWindow(QWidget):
    """透明桌面挂件：负责角色绘制、拖拽、单击对话与多屏贴边。"""

    DESIGN_SIZE = 180

    def __init__(
        self,
        config_manager: ConfigManager,
        character_package: CharacterPackage = None,
    ):
        super().__init__()
        self.setWindowTitle("Pixkin")
        self.config_manager = config_manager
        self.animator = PetAnimator()
        self.dock_side = None
        self.is_docked = False
        self._is_dragging = False
        self._drag_started = False
        self._drag_pos = QPoint()
        self._press_global = QPoint()
        self._ignore_next_release = False
        self.peek_size = 45
        self._pending_redock = None
        self.character_package = None
        self._character_frames = {}
        self._dedicated_edge_states = set()
        self._state_started_tick = 0
        self._roam_animation = None
        self._roam_state = None

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("单击和我聊天 · 拖动到屏幕边缘可以藏起来")

        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._handle_click)

        self._edge_hover_timer = QTimer(self)
        self._edge_hover_timer.setSingleShot(True)
        self._edge_hover_timer.timeout.connect(self._show_edge_hover)

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._next_frame)
        self.anim_timer.start(40)
        self.tick = 0

        self.chat_window = ChatBubbleWindow(self.config_manager)
        self.chat_window.hide()
        self.animator.state_changed.connect(self._on_state_changed)
        self.apply_config(initial=True)
        self.set_character(character_package)
        self._position_default()

    def apply_config(self, initial=False):
        """把设置立即应用到挂件，无需重启。"""
        was_visible = self.isVisible()
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.config_manager.get("pet", "topmost", True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        flags &= ~Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)

        scale = max(0.7, min(1.6, float(self.config_manager.get("pet", "scale", 1.0))))
        size = round(self.DESIGN_SIZE * scale)
        self.resize(size, size)
        self.peek_size = max(
            36,
            min(64, int(self.config_manager.get("pet", "peek_size", 45))),
        )
        if self.is_docked and not self._edge_side_enabled(self.dock_side):
            self._undock()

        if was_visible and not initial:
            self.show()
        self.update()
        self.chat_window.apply_theme()

    def _next_frame(self):
        self.tick += 1
        self.update()

    def _position_default(self):
        saved = self.config_manager.get("pet", "position", {})
        if isinstance(saved, dict) and "x" in saved and "y" in saved:
            x, y = int(saved["x"]), int(saved["y"])
            center = QPoint(x + self.width() // 2, y + self.height() // 2)
            if any(
                screen.availableGeometry().contains(center)
                for screen in QApplication.screens()
            ):
                self.move(x, y)
                return
        geo = QApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - self.width() - 28, geo.bottom() - self.height() - 44)

    def save_position(self):
        if not self.is_docked:
            saved = self.config_manager.update_section(
                "pet", {"position": {"x": self.x(), "y": self.y()}}
            )
            if not saved:
                LOGGER.warning("桌宠位置保存失败")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = self.width() / self.DESIGN_SIZE
        painter.scale(scale, scale)

        if self.character_package:
            if self.is_docked and self._is_edge_state():
                self._draw_package_peek(painter)
            else:
                self._draw_package_character(painter)
        elif self.is_docked and self._is_edge_state():
            self._draw_peek(painter)
        else:
            self._draw_character(painter)

    def set_character(self, package: CharacterPackage):
        self.character_package = package
        self._character_frames = {}
        self._dedicated_edge_states = set()
        if package:
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
                if frames:
                    self._character_frames[state] = (frames, animation)
                    if (
                        state.startswith("edge_")
                        and any(
                            "edge" in {
                                part.lower()
                                for part in frame_spec.file.parts
                            }
                            for frame_spec in animation.frames
                        )
                    ):
                        self._dedicated_edge_states.add(state)
            self.animator.configure_animation_policies(package.animations)
            self.animator.configure_behavior(package.behavior)
            self.chat_window.set_character(package.name, package.preview)
        else:
            self.animator.configure_animation_policies({})
            self.animator.configure_behavior({})
            self.chat_window.set_character("山山")
        self.update()

    def _is_edge_state(self):
        value = self.animator.current_state.value
        return value == "edge_docked" or value.startswith("edge_")

    @staticmethod
    def _edge_state_for(side: str, phase: str):
        try:
            return PetState(f"edge_{phase}_{side}")
        except ValueError:
            return PetState.EDGE_DOCKED

    def _edge_side_enabled(self, side: str) -> bool:
        if not self.config_manager.get("pet", "edge_dock_enabled", True):
            return False
        defaults = {
            "left": True,
            "right": True,
            "top": True,
            "bottom": False,
        }
        sides = self.config_manager.get("pet", "edge_sides", defaults)
        if not isinstance(sides, dict):
            sides = defaults
        return bool(sides.get(side, defaults.get(side, False)))

    def _entry_for_state(self, state_name: str):
        entry = self._character_frames.get(state_name)
        if not entry and state_name.startswith("edge_"):
            entry = self._character_frames.get("edge_docked")
        if not entry:
            entry = self._character_frames.get("idle")
        return entry

    def _current_package_frame(self, state_name: str):
        entry = self._entry_for_state(state_name)
        if not entry:
            return None
        frames, animation = entry
        frame_index = int((self.tick - self._state_started_tick) * animation.fps / 25)
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

    def _draw_package_character(self, painter: QPainter):
        state_name = self.animator.current_state.value
        entry = self._entry_for_state(state_name)
        frame = self._current_package_frame(state_name)
        if frame is None:
            self._draw_character(painter)
            return
        _frames, animation = entry
        target_width = 172
        target_height = 172
        scaled = frame.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if animation.legacy_effects:
            x = (self.DESIGN_SIZE - scaled.width()) // 2
            y = self.DESIGN_SIZE - scaled.height()
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
                y += int(math.sin(self.tick * 0.09) * 3)
            elif state == PetState.BLINK:
                scale_y = 0.96
            elif state == PetState.STRETCH:
                scale_x = 0.96
                scale_y = 1.06
                y -= 5
            elif state == PetState.WAVE:
                rotation = math.sin(self.tick * 0.28) * 4.5
            elif state == PetState.NOD:
                y += int(abs(math.sin(self.tick * 0.25)) * 6)
            elif state == PetState.SLEEP:
                rotation = -3.5
                y += 4
            elif state == PetState.DRAGGING:
                rotation = math.sin(self.tick * 0.34) * 7
            elif state == PetState.TALKING:
                scale_x = 1.0 + math.sin(self.tick * 0.28) * 0.018
                scale_y = 1.0 - math.sin(self.tick * 0.28) * 0.012
            elif state in {
                PetState.ALERTING,
                PetState.ALERTING_IMPORTANT,
                PetState.CELEBRATE_LIVE,
            }:
                y -= int(abs(math.sin(self.tick * 0.24)) * 12)
                rotation = math.sin(self.tick * 0.3) * 3
        painter.save()
        center_x = x + scaled.width() / 2
        center_y = y + scaled.height() / 2
        painter.translate(center_x, center_y)
        painter.rotate(rotation)
        painter.scale(scale_x, scale_y)
        painter.translate(-center_x, -center_y)
        painter.drawPixmap(x, y, scaled)
        painter.restore()

    def _draw_package_peek(self, painter: QPainter):
        current = self.animator.current_state.value
        directional = current if current.startswith("edge_") else (
            f"edge_idle_{self.dock_side}" if self.dock_side else "edge_docked"
        )
        frame = self._current_package_frame(directional)
        if frame is None:
            self._draw_peek(painter)
            return
        if directional in self._dedicated_edge_states:
            self._draw_directional_package_peek(painter, frame)
            return
        peek = self.peek_size * self.DESIGN_SIZE / self.width()
        scaled = frame.scaled(
            64, 64,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if self.dock_side == "left":
            cx, cy = self.DESIGN_SIZE - peek / 2, 90
        elif self.dock_side == "right":
            cx, cy = peek / 2, 90
        elif self.dock_side == "top":
            cx, cy = 90, self.DESIGN_SIZE - peek / 2
        else:
            cx, cy = 90, peek / 2
        painter.drawPixmap(
            int(cx - scaled.width() / 2),
            int(cy - scaled.height() / 2),
            scaled,
        )

    def _draw_directional_package_peek(
        self, painter: QPainter, frame: QPixmap
    ):
        """Align partial-head art so the OS crop becomes the screen edge."""
        scaled = frame.scaled(
            96,
            96,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (self.DESIGN_SIZE - scaled.width()) // 2
        y = (self.DESIGN_SIZE - scaled.height()) // 2
        if self.dock_side == "left":
            x = self.DESIGN_SIZE - scaled.width()
        elif self.dock_side == "right":
            x = 0
        elif self.dock_side == "top":
            y = self.DESIGN_SIZE - scaled.height()
        elif self.dock_side == "bottom":
            y = 0
        painter.drawPixmap(x, y, scaled)

    def _draw_character(self, painter: QPainter):
        state = self.animator.current_state
        t = self.tick
        cx = 90
        float_y = math.sin(t * 0.09) * 2.5 if state == PetState.IDLE else 0
        if state == PetState.ALERTING:
            float_y = -abs(math.sin(t * 0.24)) * 15
        elif state == PetState.NOD:
            float_y = abs(math.sin(t * 0.22)) * 5
        elif state == PetState.STRETCH:
            float_y = -4

        # 柔和落地阴影
        shadow_w = 88 + (8 if state == PetState.ALERTING else 0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(44, 34, 85, 42))
        painter.drawEllipse(int(cx - shadow_w / 2), 149, int(shadow_w), 13)

        # 尾巴
        tail_pen = QPen(QColor("#7668E7"), 15, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(tail_pen)
        tail = QPainterPath()
        tail.moveTo(126, 119 + float_y)
        tail.cubicTo(158, 112 + float_y, 153, 84 + float_y, 137, 91 + float_y)
        painter.drawPath(tail)

        # 耳朵与内耳
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#6C5FDC"))
        left_ear = QPainterPath()
        left_ear.moveTo(51, 69 + float_y)
        left_ear.cubicTo(37, 31 + float_y, 47, 23 + float_y, 72, 55 + float_y)
        painter.drawPath(left_ear)
        right_ear = QPainterPath()
        right_ear.moveTo(129, 69 + float_y)
        right_ear.cubicTo(143, 31 + float_y, 133, 23 + float_y, 108, 55 + float_y)
        painter.drawPath(right_ear)
        painter.setBrush(QColor("#F4A9C3"))
        painter.drawPolygon(QPoint(49, 38 + int(float_y)), QPoint(57, 61 + int(float_y)), QPoint(65, 54 + int(float_y)))
        painter.drawPolygon(QPoint(131, 38 + int(float_y)), QPoint(123, 61 + int(float_y)), QPoint(115, 54 + int(float_y)))

        # 主体渐变
        body_gradient = QLinearGradient(52, 55, 128, 144)
        body_gradient.setColorAt(0, QColor("#8A7CF2"))
        body_gradient.setColorAt(0.55, QColor("#7467E8"))
        body_gradient.setColorAt(1, QColor("#5D50CD"))
        painter.setBrush(QBrush(body_gradient))
        painter.setPen(QPen(QColor(69, 57, 155, 95), 1.4))
        painter.drawRoundedRect(43, int(54 + float_y), 94, 92, 45, 45)

        # 额头高光与脸部
        highlight = QRadialGradient(72, 67 + float_y, 47)
        highlight.setColorAt(0, QColor(255, 255, 255, 72))
        highlight.setColorAt(1, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(highlight)
        painter.drawEllipse(48, int(57 + float_y), 84, 72)
        painter.setBrush(QColor("#F8F6FF"))
        painter.drawEllipse(56, int(79 + float_y), 68, 58)

        eye_y = 92 + float_y
        self._draw_face(painter, cx, eye_y, state)
        self._draw_arms(painter, state, float_y)

        if state == PetState.SLEEP:
            painter.setPen(QPen(QColor("#7568D7"), 2))
            painter.drawText(130, int(63 + float_y), "z")
            painter.drawText(142, int(48 + float_y), "Z")
        elif state == PetState.ALERTING:
            painter.setPen(QPen(QColor("#F0A526"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(35, 52, 25, 42)
            painter.drawLine(145, 52, 155, 42)
            painter.drawLine(90, 30, 90, 17)

    def _draw_face(self, painter, cx, eye_y, state):
        dark = QColor("#312A59")
        if state in (PetState.BLINK, PetState.SLEEP):
            painter.setPen(QPen(dark, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(67, int(eye_y), 78, int(eye_y))
            painter.drawLine(102, int(eye_y), 113, int(eye_y))
        else:
            eye_h = 22 if state == PetState.DRAGGING else 18
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(64, int(eye_y - 8), 18, eye_h)
            painter.drawEllipse(98, int(eye_y - 8), 18, eye_h)
            pupil_shift = int(math.sin(self.tick * 0.035) * 2)
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

        painter.setPen(QPen(dark, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if state in (PetState.ALERTING, PetState.STRETCH):
            painter.setBrush(QColor("#EB6B8C"))
            painter.drawEllipse(82, int(eye_y + 13), 16, 13)
        elif state == PetState.TALKING:
            mouth_h = 7 + int(abs(math.sin(self.tick * 0.28)) * 7)
            painter.setBrush(QColor("#EB6B8C"))
            painter.drawEllipse(84, int(eye_y + 15), 12, mouth_h)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            mouth = QPainterPath()
            mouth.moveTo(82, eye_y + 16)
            mouth.quadTo(86, eye_y + 21, 90, eye_y + 16)
            mouth.quadTo(94, eye_y + 21, 98, eye_y + 16)
            painter.drawPath(mouth)

    def _draw_arms(self, painter, state, float_y):
        painter.setPen(QPen(QColor("#695BD7"), 13, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if state == PetState.WAVE:
            wave = math.sin(self.tick * 0.33) * 10
            painter.drawLine(48, int(105 + float_y), int(27 + wave), int(75 + float_y))
            painter.drawLine(132, int(105 + float_y), 143, int(124 + float_y))
        elif state == PetState.STRETCH:
            painter.drawLine(51, int(102 + float_y), 30, int(72 + float_y))
            painter.drawLine(129, int(102 + float_y), 150, int(72 + float_y))
        else:
            painter.drawLine(48, int(109 + float_y), 37, int(126 + float_y))
            painter.drawLine(132, int(109 + float_y), 143, int(126 + float_y))

    def _draw_peek(self, painter):
        """在屏幕可见的窄条里画一张完整小脸，真正形成“偷偷探头”。"""
        peek = self.peek_size * self.DESIGN_SIZE / self.width()
        if self.dock_side == "left":
            cx, cy = self.DESIGN_SIZE - peek / 2, 90
        elif self.dock_side == "right":
            cx, cy = peek / 2, 90
        elif self.dock_side == "top":
            cx, cy = 90, self.DESIGN_SIZE - peek / 2
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
        painter.drawArc(int(cx - 6), int(cy + 8), 12, 8, 0, -180 * 16)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._stop_roaming()
            self._pending_redock = None
            self._is_dragging = True
            self._drag_started = False
            self._press_global = event.globalPosition().toPoint()
            self._drag_pos = self._press_global - self.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.grabMouse()
            LOGGER.info(
                "收到鼠标按下：global=(%s,%s), window=(%s,%s)",
                self._press_global.x(), self._press_global.y(), self.x(), self.y(),
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_dragging and event.buttons() & Qt.MouseButton.LeftButton:
            global_pos = event.globalPosition().toPoint()
            if not self._drag_started:
                distance = (global_pos - self._press_global).manhattanLength()
                if distance < QApplication.startDragDistance():
                    return
                self._drag_started = True
                self.animator.set_state(PetState.DRAGGING)
                LOGGER.info("开始拖动桌宠")
                if self.is_docked:
                    self.is_docked = False
                    self.dock_side = None
            self.move(global_pos - self._drag_pos)
            self._update_chat_position()
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if self._ignore_next_release:
            self._ignore_next_release = False
            event.accept()
            return
        was_dragging = self._drag_started
        self._is_dragging = False
        self._drag_started = False
        if was_dragging:
            self.animator.set_state(PetState.IDLE)
            docked = False
            if self.config_manager.get("pet", "edge_dock_enabled", True):
                docked = self._check_edge_docking()
            if not docked:
                self.save_position()
            LOGGER.info(
                "拖动结束：position=(%s,%s), docked=%s, side=%s",
                self.x(), self.y(), docked, self.dock_side,
            )
        else:
            self._click_timer.start(180)
            LOGGER.info("收到单击，等待确认是否双击")
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self._click_timer.stop()
        self._ignore_next_release = True
        self._handle_click()
        event.accept()

    def _handle_click(self):
        if self.is_docked:
            self._undock(open_chat=True)
            return
        self.animator.request_state(
            PetState.TOUCH,
            duration_ms=900,
            restore=False,
            priority=80,
        )
        self._toggle_chat_window()

    def _screen_geometry(self, point=None):
        point = point or self.frameGeometry().center()
        screen = QApplication.screenAt(point) or QApplication.primaryScreen()
        return screen.availableGeometry()

    def _check_edge_docking(self):
        geo = self._screen_geometry()
        x, y, w, h = self.x(), self.y(), self.width(), self.height()
        margin = int(self.config_manager.get("pet", "dock_margin", 15)) + 18

        right_edge = geo.right() + 1
        bottom_edge = geo.bottom() + 1
        distances = {}
        if self._edge_side_enabled("left") and x <= geo.left() + margin:
            distances["left"] = max(0, x - geo.left())
        if (
            self._edge_side_enabled("right")
            and x + w >= right_edge - margin
        ):
            distances["right"] = max(0, right_edge - (x + w))
        if self._edge_side_enabled("top") and y <= geo.top() + margin:
            distances["top"] = max(0, y - geo.top())
        if (
            self._edge_side_enabled("bottom")
            and y + h >= bottom_edge - margin
        ):
            distances["bottom"] = max(0, bottom_edge - (y + h))
        if not distances:
            return False
        nearest = min(distances, key=distances.get)
        self._dock_to_side(nearest, geo=geo)
        return True

    def _dock_target(self, side, geo=None, reveal=None):
        geo = geo or self._screen_geometry()
        x, y, w, h = self.x(), self.y(), self.width(), self.height()
        reveal = self.peek_size if reveal is None else int(reveal)
        reveal = max(36, min(min(w, h), reveal))
        if side == "left":
            x = geo.left() - w + reveal
        elif side == "right":
            x = geo.right() + 1 - reveal
        elif side == "top":
            y = geo.top() - h + reveal
        elif side == "bottom":
            y = geo.bottom() + 1 - reveal
        return int(x), int(y)

    def _dock_to_side(self, side, *, geo=None):
        if not self._edge_side_enabled(side):
            return False
        geo = geo or self._screen_geometry()
        target_x, target_y = self._dock_target(side, geo)
        self.dock_side = side
        self.is_docked = True
        self.animator.set_state(self._edge_state_for(side, "enter"))
        self.chat_window.hide()
        self._animate_move(
            target_x,
            target_y,
            duration=420,
            easing=QEasingCurve.Type.InOutSine,
            on_finished=lambda expected=side: self._finish_docking(expected),
        )
        LOGGER.info("吸附到屏幕%s侧，露出%s像素", side, self.peek_size)
        return True

    def _finish_docking(self, expected_side):
        if self.is_docked and self.dock_side == expected_side:
            self.animator.set_state(
                self._edge_state_for(expected_side, "idle")
            )

    def _undock(
        self,
        *,
        open_chat=False,
        on_finished=None,
        preserve_redock=False,
    ):
        if not self.is_docked:
            if on_finished:
                on_finished()
            return
        if not preserve_redock:
            self._pending_redock = None
        self._edge_hover_timer.stop()
        geo = self._screen_geometry()
        x, y, w, h = self.x(), self.y(), self.width(), self.height()
        target_x, target_y = x, y
        side = self.dock_side
        if side == "left":
            target_x = geo.left() + 10
        elif side == "right":
            target_x = geo.right() - w - 9
        elif side == "top":
            target_y = geo.top() + 10
        elif side == "bottom":
            target_y = geo.bottom() - h - 9
        self.is_docked = False
        self.animator.set_state(self._edge_state_for(side, "exit"))

        def finish():
            self.dock_side = None
            self.animator.set_state(PetState.IDLE)
            if open_chat:
                self._toggle_chat_window()
            if on_finished:
                on_finished()

        self._animate_move(
            target_x,
            target_y,
            duration=380,
            easing=QEasingCurve.Type.OutCubic,
            on_finished=finish,
        )

    def _animate_move(
        self,
        end_x,
        end_y,
        *,
        duration=330,
        easing=QEasingCurve.Type.OutCubic,
        on_finished=None,
    ):
        self.anim = QPropertyAnimation(self, b"pos")
        self.anim.setDuration(max(1, int(duration)))
        self.anim.setStartValue(self.pos())
        self.anim.setEndValue(QPoint(int(end_x), int(end_y)))
        self.anim.setEasingCurve(easing)
        if on_finished:
            self.anim.finished.connect(on_finished)
        self.anim.start()

    def enterEvent(self, event):
        if (
            self.is_docked
            and self.config_manager.get("pet", "edge_hover_enabled", True)
        ):
            self._edge_hover_timer.start(180)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._edge_hover_timer.stop()
        if self.is_docked:
            self._show_edge_idle()
        super().leaveEvent(event)

    def _show_edge_hover(self):
        if not self.is_docked or not self.dock_side:
            return
        self.animator.set_state(
            self._edge_state_for(self.dock_side, "hover")
        )
        reveal = min(
            min(self.width(), self.height()),
            self.peek_size + round(min(self.width(), self.height()) * 0.2),
        )
        target_x, target_y = self._dock_target(
            self.dock_side, reveal=reveal
        )
        self._animate_move(
            target_x,
            target_y,
            duration=280,
            easing=QEasingCurve.Type.InOutSine,
        )

    def _show_edge_idle(self):
        if not self.is_docked or not self.dock_side:
            return
        self.animator.set_state(
            self._edge_state_for(self.dock_side, "idle")
        )
        target_x, target_y = self._dock_target(self.dock_side)
        self._animate_move(
            target_x,
            target_y,
            duration=320,
            easing=QEasingCurve.Type.InOutSine,
        )

    def play_contextual_alert(
        self, state=PetState.ALERTING_IMPORTANT, *, duration_ms=3200
    ):
        should_restore = bool(
            self.is_docked
            and self.dock_side
            and self.config_manager.get(
                "pet", "return_to_edge_after_alert", True
            )
        )
        if should_restore:
            geo = self._screen_geometry()
            if self.dock_side in {"left", "right"}:
                span = max(1, geo.height() - self.height())
                along = (self.y() - geo.top()) / span
            else:
                span = max(1, geo.width() - self.width())
                along = (self.x() - geo.left()) / span
            self._pending_redock = {
                "side": self.dock_side,
                "geometry": QRect(geo),
                "along": max(0.0, min(1.0, along)),
            }
            self._undock(
                on_finished=lambda: self._start_contextual_alert(
                    state, duration_ms
                ),
                preserve_redock=True,
            )
            return
        self._pending_redock = None
        self._start_contextual_alert(state, duration_ms)

    def _start_contextual_alert(self, state, duration_ms):
        accepted = self.animator.request_state(
            state,
            duration_ms=duration_ms,
            restore=False,
            priority=90,
        )
        if accepted and self._pending_redock:
            QTimer.singleShot(
                int(duration_ms) + 50,
                self._restore_pending_dock,
            )
        elif not accepted:
            self._pending_redock = None

    def _restore_pending_dock(self):
        context = self._pending_redock
        self._pending_redock = None
        if not context or self._is_dragging:
            return
        side = context["side"]
        if not self._edge_side_enabled(side):
            return
        geo = context["geometry"]
        along = context["along"]
        if side in {"left", "right"}:
            y = geo.top() + round(
                along * max(0, geo.height() - self.height())
            )
            x = geo.left() + 10 if side == "left" else geo.right() - self.width() - 9
        else:
            x = geo.left() + round(
                along * max(0, geo.width() - self.width())
            )
            y = geo.top() + 10 if side == "top" else geo.bottom() - self.height() - 9
        self.move(int(x), int(y))
        self._dock_to_side(side, geo=geo)

    def _toggle_chat_window(self):
        if self.chat_window.isVisible():
            self.chat_window.hide()
        else:
            self._update_chat_position()
            self.chat_window.show()
            self.chat_window.raise_()
            self.chat_window.activateWindow()

    def _update_chat_position(self):
        geo = self._screen_geometry()
        chat_w, chat_h = self.chat_window.width(), self.chat_window.height()
        x = self.x() + (self.width() - chat_w) // 2
        y = self.y() - chat_h - 8
        if y < geo.top() + 8:
            y = self.y() + self.height() + 8
        x = max(geo.left() + 8, min(x, geo.right() - chat_w + 1 - 8))
        y = max(geo.top() + 8, min(y, geo.bottom() - chat_h + 1 - 8))
        self.chat_window.move(x, y)

    def _on_state_changed(self, new_state):
        self._state_started_tick = self.tick
        movement_states = {
            PetState.WALK_LEFT,
            PetState.WALK_RIGHT,
            PetState.RUN_LEFT,
            PetState.RUN_RIGHT,
        }
        if new_state in movement_states:
            QTimer.singleShot(
                0,
                lambda expected=new_state: self._start_roaming(expected),
            )
        else:
            self._stop_roaming()
        self.update()

    def _start_roaming(self, state: PetState):
        if (
            self.animator.current_state != state
            or self.is_docked
            or self._is_dragging
            or self.chat_window.isVisible()
        ):
            return
        geo = self._screen_geometry()
        left_limit = geo.left()
        right_limit = geo.right() - self.width() + 1
        direction = -1 if state in {
            PetState.WALK_LEFT, PetState.RUN_LEFT
        } else 1
        distance = (
            round(self.width() * 0.75)
            if state in {PetState.WALK_LEFT, PetState.WALK_RIGHT}
            else round(self.width() * 1.25)
        )
        target_x = max(
            left_limit,
            min(right_limit, self.x() + direction * distance),
        )
        edge_side = None
        if target_x <= left_limit:
            edge_side = "left"
        elif target_x >= right_limit:
            edge_side = "right"

        self._stop_roaming()
        animation = QPropertyAnimation(self, b"pos")
        self._roam_animation = animation
        self._roam_state = state
        animation.setDuration(
            max(240, self.animator._duration_for(state) - 90)
        )
        animation.setStartValue(self.pos())
        animation.setEndValue(QPoint(int(target_x), self.y()))
        animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        animation.finished.connect(
            lambda expected=state, side=edge_side: self._finish_roaming(
                expected, side
            )
        )
        animation.start()

    def _finish_roaming(self, expected_state: PetState, edge_side):
        self._roam_animation = None
        self._roam_state = None
        if self.animator.current_state != expected_state:
            return
        if (
            edge_side
            and self.config_manager.get("pet", "edge_dock_enabled", True)
            and self._edge_side_enabled(edge_side)
        ):
            self._dock_to_side(edge_side)

    def _stop_roaming(self):
        animation = self._roam_animation
        self._roam_animation = None
        self._roam_state = None
        if animation is not None:
            animation.stop()
            animation.deleteLater()
