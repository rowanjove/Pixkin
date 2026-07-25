import json
import shutil
from pathlib import Path

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QInputDialog,
    QProgressBar, QPushButton, QTextEdit, QVBoxLayout,
)

from core.character_package import CharacterPackageError, CharacterPackageManager
from core.config import ConfigManager
from core.paths import resource_path
from core.pet_generation_run import (
    PetGenerationRunError,
    PetGenerationRunStore,
)
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
        run_store: PetGenerationRunStore = None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.package_manager = package_manager
        self.generated_package = None
        self.reference_paths = []
        self.worker = None
        self.run_store = run_store or PetGenerationRunStore()
        self.active_run_id = None
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
        self._refresh_runs()

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
        self.mode_input.addItem("基础孵化 · 4 个核心动作", False)
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
        self.status = QLabel("完整孵化会进行 10 次图像生成；基础孵化会进行 5 次。")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)

        resume_row = QHBoxLayout()
        self.run_input = QComboBox()
        self.run_input.setToolTip("身份待确认、失败或尚未安装的孵化任务")
        self.run_input.currentIndexChanged.connect(self._on_run_changed)
        self.continue_btn = QPushButton("继续未完成任务")
        self.continue_btn.setObjectName("secondary")
        self.continue_btn.clicked.connect(self._continue_run)
        resume_row.addWidget(self.run_input, 1)
        resume_row.addWidget(self.continue_btn)
        layout.addLayout(resume_row)

        retry_row = QHBoxLayout()
        self.retry_task_input = QComboBox()
        self.retry_task_input.setToolTip("选择要重试或切换历史版本的任务")
        self.retry_task_input.currentIndexChanged.connect(
            self._on_task_tool_changed
        )
        self.retry_btn = QPushButton("重试选中动作")
        self.retry_btn.setObjectName("secondary")
        self.retry_btn.clicked.connect(self._retry_selected_task)
        self.candidate_btn = QPushButton("查看候选版本")
        self.candidate_btn.setObjectName("secondary")
        self.candidate_btn.clicked.connect(
            self._choose_selected_candidate
        )
        retry_row.addWidget(self.retry_task_input, 1)
        retry_row.addWidget(self.retry_btn)
        retry_row.addWidget(self.candidate_btn)
        layout.addLayout(retry_row)

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
            run_store=self.run_store,
        )
        self._start_worker(worker)

    def _start_worker(self, worker):
        self.worker = worker
        worker.progress_changed.connect(self._on_progress)
        worker.run_created.connect(self._on_run_created)
        worker.review_ready.connect(self._on_review_ready)
        worker.core_review_ready.connect(self._on_core_review_ready)
        worker.qa_review_ready.connect(self._on_qa_review_ready)
        worker.error_occurred.connect(self._on_error)
        worker.package_ready.connect(self._on_package_ready)
        worker.finished.connect(
            lambda finished_worker=worker: self._on_worker_finished(
                finished_worker
            )
        )
        self.hatch_btn.setDisabled(True)
        self.continue_btn.setDisabled(True)
        self.retry_btn.setDisabled(True)
        self.candidate_btn.setDisabled(True)
        self.progress.show()
        self.progress.setValue(0)
        worker.start()

    def _on_run_created(self, run_id: str):
        self.active_run_id = run_id
        self._refresh_runs(run_id)

    def _on_progress(self, value: int, text: str):
        self.progress.setValue(value)
        self.status.setText(text)

    def _on_error(self, message: str):
        self.status.setText("孵化没有完成。请检查接口、模型和额度后重试。")
        QMessageBox.critical(self, "伙伴工坊遇到问题", message)
        self._refresh_runs(self.active_run_id)

    def _on_review_ready(self, run_id: str, image_path: str):
        self.active_run_id = run_id
        decision = self._confirm_canonical(image_path)
        if decision == "accepted":
            self.run_store.record_review(run_id, "canonical", "accepted")
            self.run_store.update_stage(
                run_id, "action_generation", status="pending"
            )
            self._resume_run(run_id)
        elif decision == "rejected":
            self.run_store.record_review(run_id, "canonical", "rejected")
            self.run_store.reset_task(run_id, "canonical")
            self.run_store.update_stage(
                run_id, "canonical_generation", status="pending"
            )
            self._resume_run(run_id, retry_task_id="canonical")
        elif decision == "switched":
            canonical = (
                self.run_store.workspace(run_id)
                / "images"
                / "canonical.png"
            )
            self._on_review_ready(run_id, str(canonical))
        else:
            self.run_store.record_review(run_id, "canonical", "deferred")
            self.status.setText(
                "身份稿已保留。可随时从“未完成任务”继续。"
            )
            self._refresh_runs(run_id)

    def _confirm_canonical(self, image_path: str) -> str:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("确认伙伴身份稿")
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText("先确认角色身份，再生成全部动作。")
        dialog.setInformativeText(
            "请检查轮廓、配色、脸部特征和配饰是否正确。"
            "确认后，后续姿态会以这张身份稿为唯一基准。"
        )
        accept = dialog.addButton(
            "身份正确，继续", QMessageBox.ButtonRole.AcceptRole
        )
        regenerate = dialog.addButton(
            "重新生成", QMessageBox.ButtonRole.DestructiveRole
        )
        later = dialog.addButton(
            "稍后处理", QMessageBox.ButtonRole.RejectRole
        )
        history = None
        if self._candidate_count(self.active_run_id, "canonical") > 1:
            history = dialog.addButton(
                "查看历史版本", QMessageBox.ButtonRole.ActionRole
            )
        pixmap = QPixmap(image_path)
        if not pixmap.isNull():
            dialog.setIconPixmap(pixmap.scaled(
                220,
                220,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is accept:
            return "accepted"
        if clicked is regenerate:
            return "rejected"
        if clicked is later:
            return "deferred"
        if history is not None and clicked is history:
            if self._choose_candidate_version(
                self.active_run_id,
                "canonical",
                continue_flow=False,
            ):
                return "switched"
            return "deferred"
        return "deferred"

    def _on_core_review_ready(
        self,
        run_id: str,
        sheet_path: str,
        report_path: str,
    ):
        self.active_run_id = run_id
        result = self._confirm_action_review(
            title="审核核心动作一致性",
            sheet_path=sheet_path,
            report_path=report_path,
            allow_accept=True,
        )
        if result[0] == "accepted":
            self.run_store.record_review(
                run_id, "core_actions", "accepted"
            )
            self.run_store.update_stage(
                run_id, "action_generation", status="pending"
            )
            self._resume_run(run_id)
        elif result[0] == "retry" and result[1]:
            task_id = result[1]
            self.run_store.record_review(
                run_id,
                "core_actions",
                "rejected",
                note=f"retry:{task_id}",
            )
            self.run_store.reset_task(run_id, task_id)
            self.run_store.update_stage(
                run_id, "action_generation", status="pending"
            )
            self._resume_run(run_id, retry_task_id=task_id)
        elif result[0] == "switched":
            self._resume_run(run_id)
        else:
            self.run_store.record_review(
                run_id, "core_actions", "deferred"
            )
            self.status.setText(
                "核心动作审核已暂存，可从未完成任务继续。"
            )
            self._refresh_runs(run_id)

    def _on_qa_review_ready(
        self,
        run_id: str,
        sheet_path: str,
        report_path: str,
    ):
        self.active_run_id = run_id
        result = self._confirm_action_review(
            title="自动 QA 需要返工",
            sheet_path=sheet_path,
            report_path=report_path,
            allow_accept=False,
        )
        if result[0] == "retry" and result[1]:
            task_id = result[1]
            self.run_store.record_review(
                run_id,
                "automatic_qa",
                "rejected",
                note=f"retry:{task_id}",
            )
            self.run_store.reset_task(run_id, task_id)
            self.run_store.update_stage(
                run_id, "action_generation", status="pending"
            )
            self._resume_run(run_id, retry_task_id=task_id)
        elif result[0] == "switched":
            self._resume_run(run_id)
        else:
            self.run_store.record_review(
                run_id, "automatic_qa", "deferred"
            )
            self.status.setText(
                "QA 报告和接触表已保留，可稍后选择动作返工。"
            )
            self._refresh_runs(run_id)

    def _confirm_action_review(
        self,
        *,
        title: str,
        sheet_path: str,
        report_path: str,
        allow_accept: bool,
    ):
        try:
            report = json.loads(
                Path(report_path).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            self._on_error(f"QA 报告无法读取：{exc}")
            return "deferred", None

        summary = report.get("summary", {})
        error_count = int(summary.get("errors", 0))
        warning_count = int(summary.get("warnings", 0))
        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(
            f"自动检查：{error_count} 个错误，{warning_count} 个提醒。"
        )
        messages = [
            str(issue.get("message", ""))
            for issue in [
                *report.get("errors", []),
                *report.get("warnings", []),
            ][:5]
        ]
        dialog.setInformativeText(
            "\n".join(messages)
            or "请对照身份稿检查轮廓、配色、配饰和动作语义。"
        )
        accept = None
        if allow_accept and not error_count:
            accept = dialog.addButton(
                "一致，继续", QMessageBox.ButtonRole.AcceptRole
            )
        retry = dialog.addButton(
            "选择动作返工", QMessageBox.ButtonRole.DestructiveRole
        )
        history = None
        versioned_tasks = self._tasks_with_candidates(
            self.active_run_id,
            report.get("expected_states", []),
        )
        if versioned_tasks:
            history = dialog.addButton(
                "查看候选版本", QMessageBox.ButtonRole.ActionRole
            )
        later = dialog.addButton(
            "稍后处理", QMessageBox.ButtonRole.RejectRole
        )
        pixmap = QPixmap(sheet_path)
        if not pixmap.isNull():
            dialog.setIconPixmap(pixmap.scaled(
                420,
                300,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        dialog.exec()
        clicked = dialog.clickedButton()
        if accept is not None and clicked is accept:
            return "accepted", None
        if history is not None and clicked is history:
            task_id = versioned_tasks[0]
            if len(versioned_tasks) > 1:
                task_id, confirmed = QInputDialog.getItem(
                    self,
                    "选择动作",
                    "查看哪个动作的候选版本：",
                    versioned_tasks,
                    0,
                    False,
                )
                if not confirmed:
                    return "deferred", None
            if self._choose_candidate_version(
                self.active_run_id,
                task_id,
                continue_flow=False,
            ):
                return "switched", task_id
            return "deferred", None
        if clicked is not retry:
            return "deferred", None

        candidates = self._qa_retry_candidates(report)
        if not candidates:
            return "deferred", None
        selected, confirmed = QInputDialog.getItem(
            self,
            "选择返工动作",
            "只重新生成这个动作：",
            candidates,
            0,
            False,
        )
        if not confirmed:
            return "deferred", None
        return "retry", selected

    @staticmethod
    def _qa_retry_candidates(report):
        candidates = []
        for issue in report.get("errors", []):
            states = issue.get("states") or [issue.get("state")]
            for state in states:
                if state and state != "canonical" and state not in candidates:
                    candidates.append(str(state))
        for state in report.get("expected_states", []):
            if state not in candidates:
                candidates.append(str(state))
        return candidates

    def _on_package_ready(self, zip_path: str):
        try:
            inspected = self.package_manager.inspect_zip(zip_path)
            installed = {
                package.package_id: package
                for package in self.package_manager.list_packages()
            }
            existing = installed.get(inspected.package_id)
            confirmed = (
                self._confirm_replace(zip_path, inspected, existing)
                if existing
                else self._confirm_install(zip_path, inspected)
            )
            if not confirmed:
                self._record_final_review("deferred")
                self.progress.setValue(100)
                self.status.setText(
                    "最终角色包已保留，但尚未安装。可从未完成任务继续。"
                )
                self._refresh_runs(self.active_run_id)
                return
            self.generated_package = self.package_manager.import_zip(
                zip_path, replace=existing is not None
            )
        except CharacterPackageError as exc:
            self._on_error(str(exc))
            return
        self._record_final_review("accepted")
        if self.active_run_id:
            try:
                self.run_store.update_stage(
                    self.active_run_id,
                    "installed",
                    status="complete",
                    artifacts={
                        "installed_package_id":
                            self.generated_package.package_id
                    },
                )
            except PetGenerationRunError:
                pass
        self.progress.setValue(100)
        self.status.setText("孵化完成，角色已经安装并设为当前伙伴。")
        QMessageBox.information(
            self, "欢迎加入 Pixkin",
            f"{self.generated_package.name} 已经来到你的桌面。"
        )
        self.accept()

    def _confirm_install(self, zip_path: str, inspected) -> bool:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("最终预览与安装")
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText(f"安装“{inspected.name}”并设为当前伙伴？")
        dialog.setInformativeText(
            "这是安装前的最终确认。选择“否”会保留角色包，稍后仍可继续。"
        )
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        self._set_package_preview(dialog, zip_path)
        return dialog.exec() == QMessageBox.StandardButton.Yes

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
        self._set_package_preview(dialog, zip_path)
        return dialog.exec() == QMessageBox.StandardButton.Yes

    def _set_package_preview(self, dialog, zip_path: str):
        if self.active_run_id:
            try:
                record = self.run_store.load(self.active_run_id)
                sheet = Path(str(
                    record["artifacts"].get("qa_contact_sheet", "")
                ))
                preview = QPixmap(str(sheet))
                if sheet.is_file() and not preview.isNull():
                    dialog.setIconPixmap(preview.scaled(
                        420,
                        300,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ))
                    return
            except PetGenerationRunError:
                pass
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

    def _record_final_review(self, decision: str):
        if not self.active_run_id:
            return
        try:
            self.run_store.record_review(
                self.active_run_id, "final_package", decision
            )
        except PetGenerationRunError:
            pass

    def _refresh_runs(self, selected_id=None):
        self.run_input.blockSignals(True)
        self.run_input.clear()
        resumable = [
            record
            for record in self.run_store.list_runs()
            if record["status"] != "complete"
        ]
        stage_labels = {
            "canonical_review": "身份待确认",
            "core_review": "核心动作待确认",
            "action_generation": "动作生成",
            "qa_review": "QA 待返工",
            "final_review": "待安装",
            "failed": "失败",
            "canceled": "已取消",
        }
        for record in resumable:
            request = record.get("request", {})
            name = request.get("pet_name") or record["id"]
            stage = stage_labels.get(
                record.get("stage"), record.get("stage", "未完成")
            )
            self.run_input.addItem(f"{name} · {stage}", record["id"])
        if not resumable:
            self.run_input.addItem("没有未完成任务", None)
        if selected_id:
            index = self.run_input.findData(selected_id)
            if index >= 0:
                self.run_input.setCurrentIndex(index)
        self.run_input.blockSignals(False)
        busy = self.worker is not None and self.worker.isRunning()
        self.continue_btn.setEnabled(bool(resumable) and not busy)
        self._on_run_changed()

    def _on_run_changed(self, _index=None):
        self.retry_task_input.clear()
        run_id = self.run_input.currentData()
        if not run_id:
            self.retry_task_input.addItem("没有可用的任务工具", None)
            self.retry_btn.setEnabled(False)
            self.candidate_btn.setEnabled(False)
            return
        try:
            record = self.run_store.load(run_id)
        except PetGenerationRunError:
            self.retry_btn.setEnabled(False)
            self.candidate_btn.setEnabled(False)
            return
        for task_id, task in record["tasks"].items():
            candidate_count = len(task.get("candidates", []))
            if (
                task["status"] in {"failed", "canceled"}
                or candidate_count > 1
            ):
                details = []
                if task["status"] in {"failed", "canceled"}:
                    details.append(task["status"])
                if candidate_count > 1:
                    details.append(f"{candidate_count} 个候选")
                self.retry_task_input.addItem(
                    f"{task_id} · {' · '.join(details)}", task_id
                )
        if self.retry_task_input.count() == 0:
            self.retry_task_input.addItem("没有可用的任务工具", None)
        self._on_task_tool_changed()

    def _on_task_tool_changed(self, _index=None):
        run_id = self.run_input.currentData()
        task_id = self.retry_task_input.currentData()
        busy = self.worker is not None and self.worker.isRunning()
        retryable = False
        candidate_count = 0
        if run_id and task_id:
            try:
                task = self.run_store.load(run_id)["tasks"][task_id]
                retryable = task["status"] in {"failed", "canceled"}
                candidate_count = len(task.get("candidates", []))
            except (KeyError, PetGenerationRunError):
                pass
        self.retry_btn.setEnabled(retryable and not busy)
        self.candidate_btn.setEnabled(candidate_count > 1 and not busy)

    def _choose_selected_candidate(self):
        if self.worker is not None and self.worker.isRunning():
            return
        run_id = self.run_input.currentData()
        task_id = self.retry_task_input.currentData()
        if run_id and task_id:
            self._choose_candidate_version(run_id, task_id)

    def _choose_candidate_version(
        self,
        run_id: str,
        task_id: str,
        *,
        continue_flow: bool = True,
    ) -> bool:
        try:
            record = self.run_store.load(run_id)
            task = record["tasks"][task_id]
        except (KeyError, PetGenerationRunError) as exc:
            self._on_error(str(exc))
            return False
        candidates = task.get("candidates", [])
        if len(candidates) < 2:
            return False

        dialog = QDialog(self)
        dialog.setWindowTitle(f"选择 {task_id} 候选版本")
        dialog.setMinimumSize(620, 500)
        dialog.resize(620, 500)
        layout = QVBoxLayout(dialog)
        copy = QLabel(
            "所有原始结果都会保留。选择一个版本作为当前精灵，"
            "随后重新执行受影响的审核与 QA。"
        )
        copy.setWordWrap(True)
        copy.setObjectName("muted")
        layout.addWidget(copy)
        candidate_list = QListWidget()
        candidate_list.setViewMode(QListWidget.ViewMode.IconMode)
        candidate_list.setIconSize(QSize(150, 162))
        candidate_list.setGridSize(QSize(180, 205))
        candidate_list.setSpacing(6)
        candidate_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        candidate_list.setMovement(QListWidget.Movement.Static)
        layout.addWidget(candidate_list, 1)
        selected_item = None
        for index, candidate in enumerate(candidates, start=1):
            sprite_path = self.run_store.artifact_path(
                run_id, candidate["sprite_artifact"]
            )
            label = f"版本 {index} · {candidate['id']}"
            if candidate["id"] == task.get("selected_candidate"):
                label += " · 当前"
            item = QListWidgetItem(
                QIcon(QPixmap(str(sprite_path))),
                label,
            )
            item.setData(
                Qt.ItemDataRole.UserRole, candidate["id"]
            )
            item.setToolTip(str(sprite_path))
            candidate_list.addItem(item)
            if candidate["id"] == task.get("selected_candidate"):
                selected_item = item
        if selected_item is not None:
            candidate_list.setCurrentItem(selected_item)
        elif candidate_list.count():
            candidate_list.setCurrentRow(0)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(
            QDialogButtonBox.StandardButton.Ok
        ).setText("使用此版本")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        candidate_list.itemDoubleClicked.connect(
            lambda _item: dialog.accept()
        )
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        item = candidate_list.currentItem()
        if item is None:
            return False
        candidate_id = item.data(Qt.ItemDataRole.UserRole)
        if candidate_id == task.get("selected_candidate"):
            return False

        if task_id == "canonical" and any(
            other_id != "canonical"
            and other["status"] == "complete"
            for other_id, other in record["tasks"].items()
        ):
            answer = QMessageBox.warning(
                self,
                "切换身份稿会使动作失效",
                "已有动作基于另一个身份版本。切换后会保留它们的候选文件，"
                "但必须重新生成动作。确定继续吗？",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False

        candidate = next(
            item for item in candidates
            if item["id"] == candidate_id
        )
        source = self.run_store.artifact_path(
            run_id, candidate["sprite_artifact"]
        )
        active_artifact = (
            "images/canonical.png"
            if task_id == "canonical"
            else f"images/{task_id}.png"
        )
        target = self.run_store.artifact_path(
            run_id, active_artifact
        )
        if not source.is_file():
            self._on_error(f"候选版本文件不存在：{source}")
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        self.run_store.select_candidate(
            run_id,
            task_id,
            candidate_id,
            active_artifact=active_artifact,
        )
        self.run_store.invalidate_after_candidate_selection(
            run_id,
            task_id,
            core_task_ids=(
                "idle", "talking", "dragging", "alerting"
            ),
        )
        self.active_run_id = run_id
        self.status.setText(
            f"已切换 {task_id} 到 {candidate_id}，"
            "受影响的审核与 QA 将重新执行。"
        )
        self._refresh_runs(run_id)
        if continue_flow:
            if task_id == "canonical":
                self._on_review_ready(run_id, str(target))
            else:
                self._resume_run(run_id)
        return True

    def _candidate_count(self, run_id, task_id):
        if not run_id:
            return 0
        try:
            return len(
                self.run_store.load(run_id)["tasks"][task_id].get(
                    "candidates", []
                )
            )
        except (KeyError, PetGenerationRunError):
            return 0

    def _tasks_with_candidates(self, run_id, task_ids):
        if not run_id:
            return []
        try:
            tasks = self.run_store.load(run_id)["tasks"]
        except PetGenerationRunError:
            return []
        return [
            task_id
            for task_id in task_ids
            if (
                task_id in tasks
                and len(tasks[task_id].get("candidates", [])) > 1
            )
        ]

    def _continue_run(self):
        if self.worker is not None and self.worker.isRunning():
            return
        run_id = self.run_input.currentData()
        if not run_id:
            return
        try:
            record = self.run_store.load(run_id)
            canonical = Path(str(record["artifacts"].get("canonical", "")))
            if (
                record.get("stage") == "canonical_review"
                and canonical.is_file()
            ):
                self._on_review_ready(run_id, str(canonical))
                return
            if record.get("stage") == "core_review":
                sheet = Path(str(
                    record["artifacts"].get("core_contact_sheet", "")
                ))
                report = Path(str(
                    record["artifacts"].get("core_qa_report", "")
                ))
                if sheet.is_file() and report.is_file():
                    self._on_core_review_ready(
                        run_id, str(sheet), str(report)
                    )
                    return
            if record.get("stage") == "qa_review":
                sheet = Path(str(
                    record["artifacts"].get("qa_contact_sheet", "")
                ))
                report = Path(str(
                    record["artifacts"].get("qa_report", "")
                ))
                if sheet.is_file() and report.is_file():
                    self._on_qa_review_ready(
                        run_id, str(sheet), str(report)
                    )
                    return
            if record.get("stage") == "final_review":
                package = Path(str(record["artifacts"].get("package", "")))
                qa_report = Path(str(
                    record["artifacts"].get("qa_report", "")
                ))
                if package.is_file() and qa_report.is_file():
                    self.active_run_id = run_id
                    self._on_package_ready(str(package))
                    return
            self._resume_run(run_id)
        except PetGenerationRunError as exc:
            self._on_error(str(exc))

    def _retry_selected_task(self):
        if self.worker is not None and self.worker.isRunning():
            return
        run_id = self.run_input.currentData()
        task_id = self.retry_task_input.currentData()
        if run_id and task_id:
            self._resume_run(run_id, retry_task_id=task_id)

    def _resume_run(self, run_id: str, retry_task_id=None):
        api_key = self.api_key.text().strip()
        try:
            record = self.run_store.load(run_id)
        except PetGenerationRunError as exc:
            self._on_error(str(exc))
            return
        needs_generation = (
            retry_task_id is not None
            or any(
                task["status"] != "complete"
                for task in record["tasks"].values()
            )
        )
        if not api_key and needs_generation:
            QMessageBox.warning(
                self,
                "需要图像 API Key",
                "继续生成或重试动作需要图像 API Key。",
            )
            return
        try:
            worker = PetGenerationWorker.resume_from(
                run_id=run_id,
                api_key=api_key,
                run_store=self.run_store,
                retry_task_id=retry_task_id,
            )
        except PetGenerationRunError as exc:
            self._on_error(str(exc))
            return
        self.active_run_id = run_id
        self._start_worker(worker)

    def _on_worker_finished(self, worker):
        is_current = self.worker is worker
        if is_current:
            self.worker = None
        worker.deleteLater()
        if self._close_after_cancel and is_current:
            self._close_after_cancel = False
            QDialog.reject(self)
        elif self.generated_package is None and is_current:
            self.hatch_btn.setDisabled(False)
            self._refresh_runs(self.active_run_id)

    def reject(self):
        if self.worker and self.worker.isRunning():
            self._close_after_cancel = True
            self.worker.cancel()
            self.status.setText("正在取消：完成当前图片请求后会安全停止…")
            self.hatch_btn.setDisabled(True)
            return
        super().reject()
