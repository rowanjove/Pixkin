"""Settings controls for proactive companion nudges and screen awareness."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class ProactiveSettings:
    enabled: bool
    quiet_fullscreen: bool
    work_stretch_reminder: bool
    work_stretch_interval_minutes: int
    sleep_guard: bool


class ProactiveSettingsPanel(QWidget):
    """Settings card for configuring proactive companion behaviour."""

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        disclosure = QLabel(
            "Pixkin 仅在本地内存中检测前台窗口标题与系统空闲状态，"
            "绝不截屏或持久化窗口隐私，仅在适当时刻给予温和陪伴关怀。"
        )
        disclosure.setObjectName("muted")
        disclosure.setWordWrap(True)
        layout.addWidget(disclosure)

        self.enabled_input = QCheckBox("启用主动关怀与情境感知")
        self.enabled_input.setChecked(
            bool(self.config_manager.get("proactive", "enabled", False))
        )
        layout.addWidget(self.enabled_input)

        self.quiet_fullscreen_input = QCheckBox("全屏游戏或视频时自动静默 (防打扰)")
        self.quiet_fullscreen_input.setChecked(
            bool(self.config_manager.get("proactive", "quiet_fullscreen", True))
        )
        layout.addWidget(self.quiet_fullscreen_input)

        self.stretch_input = QCheckBox("久坐与连续工作关怀提醒")
        self.stretch_input.setChecked(
            bool(
                self.config_manager.get(
                    "proactive", "work_stretch_reminder", True
                )
            )
        )
        layout.addWidget(self.stretch_input)

        form = QFormLayout()
        form.setSpacing(8)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(15, 240)
        self.interval_spin.setSingleStep(15)
        self.interval_spin.setSuffix(" 分钟")
        self.interval_spin.setValue(
            int(
                self.config_manager.get(
                    "proactive", "work_stretch_interval_minutes", 90
                )
            )
        )
        form.addRow("连续专注提醒间隔", self.interval_spin)
        layout.addLayout(form)

        self.sleep_guard_input = QCheckBox("深夜作息守护提醒 (23:30 后晚安关怀)")
        self.sleep_guard_input.setChecked(
            bool(self.config_manager.get("proactive", "sleep_guard", False))
        )
        layout.addWidget(self.sleep_guard_input)

    def values(self) -> ProactiveSettings:
        return ProactiveSettings(
            enabled=self.enabled_input.isChecked(),
            quiet_fullscreen=self.quiet_fullscreen_input.isChecked(),
            work_stretch_reminder=self.stretch_input.isChecked(),
            work_stretch_interval_minutes=int(self.interval_spin.value()),
            sleep_guard=self.sleep_guard_input.isChecked(),
        )

    def save(self) -> None:
        vals = self.values()
        self.config_manager.set("proactive", "enabled", vals.enabled)
        self.config_manager.set(
            "proactive", "quiet_fullscreen", vals.quiet_fullscreen
        )
        self.config_manager.set(
            "proactive", "work_stretch_reminder", vals.work_stretch_reminder
        )
        self.config_manager.set(
            "proactive",
            "work_stretch_interval_minutes",
            vals.work_stretch_interval_minutes,
        )
        self.config_manager.set("proactive", "sleep_guard", vals.sleep_guard)
