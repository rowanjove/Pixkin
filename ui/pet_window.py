import logging

from PyQt6.QtCore import (
    Qt, QPoint, QRect, QPropertyAnimation, QEasingCurve, QTimer
)
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtGui import QMouseEvent, QPainter

from core.character_package import CharacterPackage
from core.config import ConfigManager
from core.pet_animator import PetAnimator, PetState
from core.services.display_layout import (
    DisplayRect,
    overlay_position,
    pet_position,
)
from ui.chat_window import ChatBubbleWindow
from ui.pet_rendering import PetRenderer


LOGGER = logging.getLogger("desktop_pet.pet_window")


class PetWindow(QWidget):
    """透明桌面挂件：负责角色绘制、拖拽、单击对话与多屏贴边。"""

    DESIGN_SIZE = 180
    EDGE_ART_SIZE = 96
    EDGE_REVEAL_PADDING = 4

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
        self._state_started_tick = 0
        self._roam_animation = None
        self._roam_state = None
        self._last_render_frame_key = None
        self._tick_remainder = 0.0
        self.character_package = None
        self.character_renderer = PetRenderer(
            self.animator,
            design_size=self.DESIGN_SIZE,
            edge_art_size=self.EDGE_ART_SIZE,
            edge_reveal_padding=self.EDGE_REVEAL_PADDING,
        )
        # 兼容既有测试与调用；缓存由渲染器原地维护。
        self._character_frames = self.character_renderer.character_frames
        self._dedicated_edge_states = (
            self.character_renderer.dedicated_edge_states
        )
        self._edge_subject_bounds = (
            self.character_renderer.edge_subject_bounds
        )
        self._edge_design_reveals = (
            self.character_renderer.edge_design_reveals
        )

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
        # Keep the renderer's logical 25 Hz clock stable even when the
        # adaptive timer is deliberately slower in low-FPS idle animations.
        self._tick_remainder += self.anim_timer.interval() / 40.0
        tick_delta = max(1, int(self._tick_remainder))
        self._tick_remainder -= tick_delta
        self.tick += tick_delta
        if self.character_package is not None:
            frame = self.character_renderer.current_package_frame(
                self.animator.current_state.value,
                tick=self.tick,
                state_started_tick=self._state_started_tick,
            )
            frame_key = int(frame.cacheKey()) if frame is not None else None
            if frame_key == self._last_render_frame_key:
                return
            self._last_render_frame_key = frame_key
        self.update()

    def _position_default(self):
        saved = self.config_manager.get("pet", "position", {})
        screens = [
            self._display_rect(screen.availableGeometry())
            for screen in QApplication.screens()
        ]
        primary = self._display_rect(
            QApplication.primaryScreen().availableGeometry()
        )
        self.move(
            *pet_position(
                saved,
                window_size=(self.width(), self.height()),
                screens=screens,
                primary=primary,
            )
        )

    @staticmethod
    def _display_rect(geometry: QRect) -> DisplayRect:
        return DisplayRect(
            geometry.left(),
            geometry.top(),
            geometry.width(),
            geometry.height(),
        )

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
        self.character_renderer.draw(
            painter,
            tick=self.tick,
            state_started_tick=self._state_started_tick,
            dock_side=self.dock_side,
            peek_size=self.peek_size,
            window_width=self.width(),
            edge_art=self._should_draw_edge_art(),
        )

    def set_character(self, package: CharacterPackage):
        self.character_package = package
        self._last_render_frame_key = None
        self.character_renderer.set_package(package)
        if package:
            behavior = dict(package.behavior)
            overrides = self.config_manager.get(
                "character_behavior_overrides", default={}
            )
            override = (
                overrides.get(package.package_id, {})
                if isinstance(overrides, dict)
                else {}
            )
            if isinstance(override, dict):
                for key in (
                    "ambient_weights",
                    "cooldown_seconds",
                ):
                    if isinstance(override.get(key), dict):
                        behavior[key] = dict(override[key])
                if "peek_size" in override:
                    self.peek_size = max(
                        36,
                        min(96, int(override["peek_size"])),
                    )
            self.animator.configure_animation_policies(package.animations)
            self.animator.configure_behavior(behavior)
            self.chat_window.set_character(package.name, package.preview)
        else:
            self.animator.configure_animation_policies({})
            self.animator.configure_behavior({})
            self.chat_window.set_character("山山")
        self._configure_animation_timer()
        self.update()

    def _configure_animation_timer(self) -> None:
        """Match timer cadence to the active package without slowing motion."""
        interval_ms = 40
        if self.character_package is not None:
            entry = self.character_renderer.entry_for_state(
                self.animator.current_state.value
            )
            animation = entry[1] if entry is not None else None
            try:
                fps = max(1.0, float(animation.fps))
            except (AttributeError, TypeError, ValueError):
                fps = 6.0
            # A package's declared FPS is a visual upper bound.  Capping the
            # redraw cadence prevents the idle window from waking 25 times a
            # second while preserving the logical animation clock above.
            interval_ms = max(100, min(200, round(1000.0 / fps)))
        self._tick_remainder = 0.0
        self.anim_timer.setInterval(interval_ms)
        if not self.anim_timer.isActive():
            self.anim_timer.start()

    def _is_edge_state(self):
        value = self.animator.current_state.value
        return value == "edge_docked" or value.startswith("edge_")

    def _should_draw_edge_art(self):
        """Keep edge art active through undocking until its side is cleared."""
        return bool(self.dock_side and self._is_edge_state())

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

    def _current_package_frame(self, state_name: str):
        return self.character_renderer.current_package_frame(
            state_name,
            tick=self.tick,
            state_started_tick=self._state_started_tick,
        )

    def _scaled_edge_art(self, frame):
        return self.character_renderer.scaled_edge_art(frame)

    def _dedicated_edge_design_reveal(self, side):
        return self.character_renderer.dedicated_edge_design_reveal(side)

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
        reveal = (
            self._edge_reveal(side)
            if reveal is None
            else int(reveal)
        )
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

    def _edge_reveal(self, side):
        """Return enough screen space to show dedicated edge art uncropped."""
        state = f"edge_idle_{side}"
        if state in self._dedicated_edge_states:
            scale = min(self.width(), self.height()) / self.DESIGN_SIZE
            dedicated = round(
                self._dedicated_edge_design_reveal(side) * scale
            )
            return max(self.peek_size, dedicated)
        return self.peek_size

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
        LOGGER.info(
            "吸附到屏幕%s侧，露出%s像素",
            side,
            self._edge_reveal(side),
        )
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

    def _show_edge_idle(self):
        if not self.is_docked or not self.dock_side:
            return
        self.animator.set_state(
            self._edge_state_for(self.dock_side, "idle")
        )
        target_x, target_y = self._dock_target(self.dock_side)
        if self.x() == target_x and self.y() == target_y:
            return
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
        self.chat_window.move(
            *overlay_position(
                anchor_position=(self.x(), self.y()),
                anchor_size=(self.width(), self.height()),
                overlay_size=(
                    self.chat_window.width(),
                    self.chat_window.height(),
                ),
                screen=self._display_rect(geo),
            )
        )

    def _on_state_changed(self, new_state):
        self._state_started_tick = self.tick
        self._last_render_frame_key = None
        self._configure_animation_timer()
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
