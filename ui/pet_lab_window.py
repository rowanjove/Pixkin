from pathlib import Path

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QPushButton, QTextEdit, QVBoxLayout,
)

from core.character_package import CharacterPackageError, CharacterPackageManager
from core.config import ConfigManager
from core.paths import resource_path
from core.pet_generator import PetGenerationWorker
from core.secrets import SecretStore
from ui.theme import resolved_theme


class PetLabWindow(QDialog):
    """至少一张示意图 → 强制卡通化 → 可导入 Pixkin 角色包。"""

    def __init__(
        self,
        config_manager: ConfigManager,
        package_manager: CharacterPackageManager,
        parent=None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.package_manager = package_manager
        self.generated_package = None
        self.reference_paths = []
        self.worker = None
        self._close_after_cancel = False
        self.setWindowTitle("Pixkin · 伙伴工坊")
        self.setWindowIcon(QIcon(str(resource_path("assets/pixkin.ico"))))
        self.setFont(QFont("Microsoft YaHei UI", 9))
        self.setMinimumSize(860, 650)
        self.resize(920, 700)
        self.setStyleSheet(
            self._style(resolved_theme(self.config_manager))
        )
        self._build_ui()

    @staticmethod
    def _style(theme="dark"):
        base = """
            QDialog { background: #0D1320; color: #F7F5FF; }
            QFrame#hero {
                background: #151E2E; border: 1px solid #28334A;
                border-radius: 24px;
            }
            QFrame#formCard {
                background: #171F30; border: 1px solid #2D3850;
                border-radius: 20px;
            }
            QLabel#brand {
                color: #7BE0D0; font-size: 12px; font-weight: 900;
                letter-spacing: 3px;
            }
            QLabel#heroTitle { color: white; font-size: 28px; font-weight: 900; }
            QLabel#heroCopy { color: #A1AABD; font-size: 12px; }
            QLabel#rule {
                color: #FFD5C9; background: #38241F; border: 1px solid #674037;
                border-radius: 12px; padding: 9px 11px; font-size: 10px;
            }
            QLabel#step { color: #7BE0D0; font-size: 10px; font-weight: 800; }
            QLabel#sectionTitle { color: white; font-size: 15px; font-weight: 800; }
            QLabel#muted { color: #8792A8; font-size: 10px; }
            QLineEdit, QTextEdit, QComboBox {
                color: #F7F5FF; background: #202A3D; border: 1px solid #35415B;
                border-radius: 10px; padding: 8px 10px;
                selection-background-color: #7357FF;
            }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
                border-color: #7BE0D0; background: #222D42;
            }
            QListWidget {
                color: #C6CEDD; background: #111827; border: 1px dashed #3C4964;
                border-radius: 14px; padding: 8px; outline: none;
            }
            QListWidget::item {
                background: #1E283B; border: 1px solid #34415B;
                border-radius: 10px; padding: 5px; margin: 3px;
            }
            QListWidget::item:selected { border-color: #7BE0D0; }
            QPushButton {
                border: none; border-radius: 10px; padding: 9px 15px;
                font-weight: 750;
            }
            QPushButton#primary { color: #101624; background: #7BE0D0; }
            QPushButton#primary:hover { background: #95EBDD; }
            QPushButton#primary:disabled { color: #717C91; background: #374357; }
            QPushButton#secondary { color: #DDE3EE; background: #263249; }
            QPushButton#secondary:hover { background: #303D57; }
            QPushButton#danger { color: #FFB7A7; background: #35231F; }
            QProgressBar {
                color: white; background: #202A3D; border: none;
                border-radius: 7px; text-align: center; height: 14px;
            }
            QProgressBar::chunk { background: #7357FF; border-radius: 7px; }
        """
        if theme != "light":
            return base
        return base + """
            QDialog { background: #EEF2F7; color: #172033; }
            QFrame#hero, QFrame#formCard {
                background: #FFFFFF; border-color: #D7DFEA;
            }
            QLabel#brand, QLabel#step { color: #16897D; }
            QLabel#heroTitle, QLabel#sectionTitle { color: #172033; }
            QLabel#heroCopy, QLabel#muted { color: #667085; }
            QLabel#rule {
                color: #9B3C2A; background: #FFF0EC; border-color: #F1C9C0;
            }
            QLineEdit, QTextEdit, QComboBox {
                color: #172033; background: #FFFFFF; border-color: #C8D2E0;
            }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
                background: #FFFFFF; border-color: #27A99A;
            }
            QListWidget {
                color: #344054; background: #F6F8FB; border-color: #BCC8D8;
            }
            QListWidget::item {
                background: #FFFFFF; border-color: #D5DEE9;
            }
            QPushButton#primary { color: #10241F; background: #6ED8C9; }
            QPushButton#primary:disabled { color: #526174; background: #DCE3EC; }
            QPushButton#secondary { color: #344054; background: #E8EDF4; }
            QPushButton#danger { color: #A43B29; background: #FFF0EC; }
            QProgressBar { color: #344054; background: #E8EDF4; }
            QProgressBar::chunk { background: #6654E8; }
        """

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(18)

        hero = QFrame()
        hero.setObjectName("hero")
        hero.setFixedWidth(300)
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(26, 26, 26, 26)
        hero_layout.setSpacing(12)
        brand = QLabel("PIXKIN 伙伴工坊")
        brand.setObjectName("brand")
        title = QLabel("用你的风格\n孵化一个伙伴")
        title.setObjectName("heroTitle")
        copy = QLabel(
            "给我至少一张风格示意图。Pixkin 会先锁定角色身份，再绘制动作姿态并组装成可直接使用的角色包。"
        )
        copy.setObjectName("heroCopy")
        copy.setWordWrap(True)
        mascot = QLabel()
        mascot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(resource_path("assets/pixkin/pip-avatar.png")))
        mascot.setPixmap(
            pixmap.scaled(
                210, 210,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        rule = QLabel(
            "卡通锁定\n无论参考图是真人、主播照片还是写实动物，输出都会被强制转译成原创 Q 版卡通桌宠。"
        )
        rule.setObjectName("rule")
        rule.setWordWrap(True)
        hero_layout.addWidget(brand)
        hero_layout.addWidget(title)
        hero_layout.addWidget(copy)
        hero_layout.addStretch()
        hero_layout.addWidget(mascot)
        hero_layout.addStretch()
        hero_layout.addWidget(rule)
        root.addWidget(hero)

        card = QFrame()
        card.setObjectName("formCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        step = QLabel("01 · 风格参考")
        step.setObjectName("step")
        layout.addWidget(step)
        refs_title = QLabel("添加 1–4 张风格示意图")
        refs_title.setObjectName("sectionTitle")
        layout.addWidget(refs_title)
        self.references = QListWidget()
        self.references.setViewMode(QListWidget.ViewMode.IconMode)
        self.references.setIconSize(QSize(92, 92))
        self.references.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.references.setMovement(QListWidget.Movement.Static)
        self.references.setFixedHeight(138)
        layout.addWidget(self.references)
        refs_actions = QHBoxLayout()
        add = QPushButton("＋ 添加图片")
        add.setObjectName("secondary")
        add.clicked.connect(self._choose_references)
        remove = QPushButton("移除选中")
        remove.setObjectName("danger")
        remove.clicked.connect(self._remove_reference)
        refs_actions.addWidget(add)
        refs_actions.addWidget(remove)
        refs_actions.addStretch()
        layout.addLayout(refs_actions)

        step2 = QLabel("02 · 身份与性格")
        step2.setObjectName("step")
        layout.addWidget(step2)
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("例如：Mochi、Nova、团子")
        self.personality_input = QLineEdit()
        self.personality_input.setPlaceholderText("聪明、温暖、毒舌但可靠…")
        self.style_input = QTextEdit()
        self.style_input.setPlaceholderText(
            "可补充配色、服饰或主播标志性元素。这里无法关闭卡通限制。"
        )
        self.style_input.setFixedHeight(66)
        self.mode_input = QComboBox()
        self.mode_input.addItem("完整孵化 · 9 个动作姿态", True)
        self.mode_input.addItem("快速草稿 · 单姿态预览", False)
        form.addRow("角色名字", self.name_input)
        form.addRow("性格设定", self.personality_input)
        form.addRow("风格补充", self.style_input)
        form.addRow("孵化模式", self.mode_input)
        layout.addLayout(form)

        settings = self.config_manager.get("image_generation", {})
        self.api_url = QLineEdit(settings.get("base_url", "https://api.openai.com/v1"))
        self.api_model = QLineEdit(settings.get("model", "gpt-image-2"))
        self.api_key = QLineEdit(
            SecretStore.get_image_api_key()
            or settings.get("api_key", "")
        )
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.quality = QComboBox()
        for label, value in (("低", "low"), ("标准", "medium"), ("高", "high")):
            self.quality.addItem(label, value)
        quality_index = self.quality.findData(settings.get("quality", "medium"))
        self.quality.setCurrentIndex(max(0, quality_index))
        api_form = QFormLayout()
        api_form.setHorizontalSpacing(14)
        api_form.addRow("图像接口", self.api_url)
        api_form.addRow("图像模型", self.api_model)
        api_form.addRow("API Key", self.api_key)
        api_form.addRow("生成质量", self.quality)
        layout.addLayout(api_form)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        self.status = QLabel("完整孵化会进行 10 次图像生成；快速草稿会进行 2 次。")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)

        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton("取消")
        cancel.setObjectName("secondary")
        cancel.clicked.connect(self.reject)
        self.hatch_btn = QPushButton("开始孵化")
        self.hatch_btn.setObjectName("primary")
        self.hatch_btn.clicked.connect(self._start_hatch)
        footer.addWidget(cancel)
        footer.addWidget(self.hatch_btn)
        layout.addLayout(footer)
        root.addWidget(card, 1)

    def _choose_references(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择风格示意图",
            "",
            "图片 (*.png *.jpg *.jpeg *.webp)",
        )
        for raw in paths:
            path = Path(raw)
            if path in self.reference_paths or len(self.reference_paths) >= 4:
                continue
            self.reference_paths.append(path)
            pixmap = QPixmap(str(path))
            item = QListWidgetItem(QIcon(pixmap), path.stem[:16])
            item.setToolTip(str(path))
            self.references.addItem(item)

    def _remove_reference(self):
        row = self.references.currentRow()
        if row < 0:
            return
        self.references.takeItem(row)
        self.reference_paths.pop(row)

    def _start_hatch(self):
        if not self.reference_paths:
            QMessageBox.warning(self, "还差一张图", "请至少添加一张风格示意图。")
            return
        if not self.name_input.text().strip():
            QMessageBox.warning(self, "给它一个名字", "请输入新伙伴的名字。")
            return
        api_key = self.api_key.text().strip()
        if not api_key:
            QMessageBox.warning(
                self, "需要图像 API Key",
                "伙伴工坊需要支持图像编辑接口的 API Key。"
            )
            return

        previous_api_key = SecretStore.get_image_api_key()
        saved = SecretStore.set_image_api_key(api_key)
        if not saved and previous_api_key and previous_api_key != api_key:
            QMessageBox.critical(
                self,
                "图像 API Key 保存失败",
                "Windows 凭据管理器仍保留旧 API Key；为避免旧凭据覆盖新设置，"
                "本次孵化没有开始。",
            )
            return
        config_saved = self.config_manager.update_section("image_generation", {
            "base_url": self.api_url.text().strip(),
            "model": self.api_model.text().strip() or "gpt-image-2",
            "quality": self.quality.currentData(),
            "api_key": "" if saved else api_key,
        })
        if not config_saved:
            rollback_ok = True
            if saved:
                rollback_ok = SecretStore.set_image_api_key(previous_api_key)
            QMessageBox.critical(
                self,
                "设置保存失败",
                (
                    "图像模型配置无法写入，API Key 已回滚。"
                    if rollback_ok
                    else "图像模型配置无法写入，且 API Key 回滚失败；"
                    "请立即检查 Windows 凭据管理器。"
                )
                + "请检查磁盘空间和目录权限。",
            )
            return
        worker = PetGenerationWorker(
            api_key=api_key,
            base_url=self.api_url.text().strip(),
            model=self.api_model.text().strip(),
            quality=self.quality.currentData(),
            pet_name=self.name_input.text().strip(),
            personality=self.personality_input.text().strip(),
            style_notes=self.style_input.toPlainText().strip(),
            reference_paths=self.reference_paths,
            full_hatch=bool(self.mode_input.currentData()),
        )
        self.worker = worker
        worker.progress_changed.connect(self._on_progress)
        worker.error_occurred.connect(self._on_error)
        worker.package_ready.connect(self._on_package_ready)
        worker.finished.connect(
            lambda finished_worker=worker: self._on_worker_finished(
                finished_worker
            )
        )
        self.hatch_btn.setDisabled(True)
        self.progress.show()
        self.progress.setValue(0)
        worker.start()

    def _on_progress(self, value: int, text: str):
        self.progress.setValue(value)
        self.status.setText(text)

    def _on_error(self, message: str):
        self.status.setText("孵化没有完成。请检查接口、模型和额度后重试。")
        QMessageBox.critical(self, "伙伴工坊遇到问题", message)

    def _on_package_ready(self, zip_path: str):
        try:
            inspected = self.package_manager.inspect_zip(zip_path)
            installed = {
                package.package_id: package
                for package in self.package_manager.list_packages()
            }
            existing = installed.get(inspected.package_id)
            if existing and not self._confirm_replace(
                zip_path, inspected, existing
            ):
                self.progress.setValue(100)
                self.status.setText(
                    "孵化结果已保留，但没有覆盖当前已安装角色。"
                )
                return
            self.generated_package = self.package_manager.import_zip(
                zip_path, replace=existing is not None
            )
        except CharacterPackageError as exc:
            self._on_error(str(exc))
            return
        self.progress.setValue(100)
        self.status.setText("孵化完成，角色已经安装并设为当前伙伴。")
        QMessageBox.information(
            self, "欢迎加入 Pixkin",
            f"{self.generated_package.name} 已经来到你的桌面。"
        )
        self.accept()

    def _confirm_replace(self, zip_path: str, inspected, existing) -> bool:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("确认覆盖角色")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(
            f"已经安装了同 ID 角色“{existing.name}”。"
        )
        dialog.setInformativeText(
            f"新生成的“{inspected.name}”将永久替换原角色图片和人设。\n"
            "确认使用下方最终预览并覆盖吗？"
        )
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        try:
            raw = self.package_manager.read_zip_preview(zip_path)
            preview = QPixmap()
            if raw and preview.loadFromData(raw):
                dialog.setIconPixmap(preview.scaled(
                    160,
                    160,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
        except CharacterPackageError:
            pass
        return dialog.exec() == QMessageBox.StandardButton.Yes

    def _on_worker_finished(self, worker):
        if self.worker is worker:
            self.worker = None
        worker.deleteLater()
        if self._close_after_cancel:
            self._close_after_cancel = False
            QDialog.reject(self)
        elif self.generated_package is None:
            self.hatch_btn.setDisabled(False)

    def reject(self):
        if self.worker and self.worker.isRunning():
            self._close_after_cancel = True
            self.worker.cancel()
            self.status.setText("正在取消：完成当前图片请求后会安全停止…")
            self.hatch_btn.setDisabled(True)
            return
        super().reject()
