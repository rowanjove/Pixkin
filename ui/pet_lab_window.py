from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QFileDialog,
    QFrame, QHBoxLayout,
    QLabel, QMessageBox, QInputDialog,
    QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core.character_package import CharacterPackageError, CharacterPackageManager
from core.config import ConfigManager
from core.paths import resource_path
from core.pet_generation_run import (
    PetGenerationRunError,
    PetGenerationRunStore,
)
from core.pet_generation_diagnostics import PetGenerationDiagnostics
from core.generation.state_machine import GenerationStateMachine
from core.pet_generator import PetGenerationWorker
from core.secrets import SecretStore
from core.services.character_service import CharacterService
from core.services.pet_lab_service import (
    PetLabCommandKind,
    PetLabContinuationKind,
    PetLabService,
)
from core.services.session_secret_store import SessionSecretStore
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionOperation,
    PermissionResource,
)
from ui.pet_lab_components import (
    CandidateSelectionDialog,
    PetLabHatchForm,
)
from ui.pet_lab_diagnostics_controller import (
    PetLabDiagnosticsController,
)
from ui.pet_lab_install_controller import PetLabInstallDialogController
from ui.pet_lab_review_controller import PetLabReviewDialogController
from ui.pet_lab_workflow_components import (
    PetLabRunOption,
    PetLabTaskPanel,
    ReferenceImagePicker,
)
from ui.theme import resolved_theme


class PetLabWindow(QDialog):
    """至少一张示意图 → 强制卡通化 → 可导入 Pixkin 角色包。"""

    def __init__(
        self,
        config_manager: ConfigManager,
        package_manager: CharacterPackageManager,
        parent=None,
        run_store: PetGenerationRunStore = None,
        character_service: CharacterService = None,
        pet_lab_service: PetLabService = None,
        session_secret_store: SessionSecretStore = None,
        context_permission_service: ContextPermissionService = None,
        provider_catalog=None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.package_manager = package_manager
        self.character_service = character_service or CharacterService(
            package_manager
        )
        self.generated_package = None
        self._reference_paths = []
        self.worker = None
        self.run_store = run_store or PetGenerationRunStore()
        self.pet_lab_service = pet_lab_service or PetLabService(
            self.run_store
        )
        self.session_secret_store = (
            session_secret_store or SessionSecretStore()
        )
        # Standalone embedders must keep the same default-deny boundary as the
        # desktop composition root; a missing service must never authorize a
        # generation request implicitly.
        self.context_permission_service = (
            context_permission_service or ContextPermissionService()
        )
        self.provider_catalog = provider_catalog or {}
        self.review_dialog_controller = PetLabReviewDialogController(
            parent=self,
            candidate_count=self._candidate_count,
            tasks_with_candidates=self._tasks_with_candidates,
            choose_candidate=self._choose_candidate_version,
            show_error=self._on_error,
        )
        self.install_dialog_controller = PetLabInstallDialogController(
            parent=self,
            run_store=self.run_store,
            character_service=self.character_service,
            active_run_id=lambda: self.active_run_id,
            animation_previews=(
                self.pet_lab_service.animation_previews
            ),
        )
        self.active_run_id = None
        self._close_after_cancel = False
        self.setWindowTitle("Pixkin · 伙伴工坊")
        self.setWindowIcon(QIcon(str(resource_path("assets/pixkin.ico"))))
        self.setFont(QFont("Microsoft YaHei UI", 10))
        self.setMinimumSize(920, 650)
        self.resize(1040, 760)
        self.setStyleSheet(
            self._style(resolved_theme(self.config_manager))
        )
        self._build_ui()
        self._refresh_runs()

    @property
    def reference_paths(self) -> list[Path]:
        if hasattr(self, "reference_picker"):
            return self.reference_picker.paths
        return list(self._reference_paths)

    @reference_paths.setter
    def reference_paths(self, paths):
        normalized = [Path(path) for path in paths]
        self._reference_paths = normalized
        if hasattr(self, "reference_picker"):
            self.reference_picker.set_paths(normalized)

    @staticmethod
    def _style(theme="dark"):
        base = """
            QDialog { background: #0D1320; color: #F7F5FF; font-size: 13px; }
            QFrame#hero {
                background: #151E2E; border: 1px solid #28334A;
                border-radius: 22px;
            }
            QFrame#formCard {
                background: #171F30; border: 1px solid #2D3850;
                border-radius: 16px;
            }
            QLabel#brand {
                color: #7BE0D0; font-size: 12px; font-weight: 900;
                letter-spacing: 3px;
            }
            QLabel#heroTitle { color: white; font-size: 24px; font-weight: 900; }
            QLabel#heroCopy { color: #A1AABD; font-size: 12px; line-height: 1.4; }
            QLabel#rule {
                color: #FFD5C9; background: #38241F; border: 1px solid #674037;
                border-radius: 12px; padding: 9px 11px; font-size: 10px;
            }
            QLabel#step { color: #7BE0D0; font-size: 11px; font-weight: 800; }
            QLabel#sectionTitle { color: white; font-size: 15px; font-weight: 800; }
            QLabel#muted { color: #8792A8; font-size: 11px; }
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
            QScrollArea#petLabScroll {
                background: transparent; border: none;
            }
            QScrollArea#petLabScroll > QWidget > QWidget {
                background: transparent;
            }
            QScrollBar:vertical {
                background: transparent; width: 10px; margin: 4px 0;
            }
            QScrollBar::handle:vertical {
                background: #3B4760; border-radius: 5px; min-height: 34px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0; background: transparent;
            }
        """
        if theme != "light":
            return base
        return base + """
            QDialog { background: #EEF2F7; color: #172033; }
            QFrame#hero {
                background: #F3F6FA; border-color: #D7DFEA;
            }
            QFrame#formCard {
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
            QScrollBar::handle:vertical { background: #B9C4D3; }
        """

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(18)

        hero = QFrame()
        hero.setObjectName("hero")
        hero.setFixedWidth(250)
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(26, 26, 26, 26)
        hero_layout.setSpacing(12)
        brand = QLabel("PIXKIN 伙伴工坊")
        brand.setObjectName("brand")
        title = QLabel("孵化一个\n桌面伙伴")
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
                168, 168,
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

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)
        scroll = QScrollArea()
        scroll.setObjectName("petLabScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        card = QFrame()
        card.setObjectName("formCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        page_title = QLabel("创建新伙伴")
        page_title.setObjectName("heroTitle")
        page_title.setStyleSheet("font-size: 22px;")
        page_hint = QLabel(
            "分步填写参考图、身份和生成配置；任务中断后可以继续。"
        )
        page_hint.setObjectName("muted")
        page_hint.setWordWrap(True)
        layout.addWidget(page_title)
        layout.addWidget(page_hint)

        step = QLabel("01 · 风格参考")
        step.setObjectName("step")
        layout.addWidget(step)
        refs_title = QLabel("添加 1–4 张风格示意图")
        refs_title.setObjectName("sectionTitle")
        layout.addWidget(refs_title)
        self.reference_picker = ReferenceImagePicker(
            self._reference_paths
        )
        self.references = self.reference_picker.list_widget
        layout.addWidget(self.reference_picker)

        step2 = QLabel("02 · 身份与性格")
        step2.setObjectName("step")
        layout.addWidget(step2)
        settings = self.config_manager.get("image_generation", {})
        self.hatch_form = PetLabHatchForm(
            settings,
            (
                self.session_secret_store.get_image_api_key()
                or SecretStore.get_image_api_key()
            ),
        )
        self.hatch_form.session_key.setChecked(
            bool(self.session_secret_store.get_image_api_key())
        )
        layout.addWidget(self.hatch_form)
        # Compatibility aliases for existing integrations while the parent
        # window migrates to the normalized form data contract.
        for name in (
            "name_input",
            "personality_input",
            "style_input",
            "mode_input",
            "symmetry_input",
            "call_budget_input",
            "api_url",
            "api_model",
            "api_key",
            "session_key",
            "quality",
        ):
            setattr(self, name, getattr(self.hatch_form, name))

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.hatch_form.mode_hint_changed.connect(self.status.setText)
        self.status.setText(self.hatch_form.mode_hint)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)

        self.task_panel = PetLabTaskPanel()
        layout.addWidget(self.task_panel)
        for name in (
            "run_input",
            "continue_btn",
            "run_health",
            "diagnostic_btn",
            "copy_diagnostic_btn",
            "export_diagnostic_btn",
            "copy_issue_btn",
            "inspect_issue_btn",
            "retry_task_input",
            "retry_btn",
            "candidate_btn",
        ):
            setattr(self, name, getattr(self.task_panel, name))
        self.diagnostics_controller = PetLabDiagnosticsController(
            parent=self,
            run_store=self.run_store,
            selected_run_id=lambda: self.task_panel.selected_run_id,
            set_status=self.status.setText,
            show_error=self._on_error,
            file_dialog=QFileDialog,
            message_box=QMessageBox,
        )
        self.run_input.currentIndexChanged.connect(self._on_run_changed)
        self.continue_btn.clicked.connect(self._continue_run)
        self.diagnostic_btn.clicked.connect(
            self._show_generation_diagnostics
        )
        self.copy_diagnostic_btn.clicked.connect(
            self._copy_generation_summary
        )
        self.export_diagnostic_btn.clicked.connect(
            self._export_generation_issue_bundle
        )
        self.copy_issue_btn.clicked.connect(
            self._copy_github_issue_markdown
        )
        self.inspect_issue_btn.clicked.connect(
            self._inspect_generation_issue_bundle
        )
        self.retry_btn.clicked.connect(self._retry_selected_task)
        self.candidate_btn.clicked.connect(
            self._choose_selected_candidate
        )

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
        scroll.setWidget(card)
        right_layout.addWidget(scroll, 1)
        right_layout.addLayout(footer)
        root.addWidget(right, 1)

    def _choose_references(self):
        self.reference_picker.choose_files()

    def _remove_reference(self):
        self.reference_picker.remove_selected()

    def _start_hatch(self):
        form = self.hatch_form.values()
        issue = form.validation_issue(len(self.reference_paths))
        if issue is not None:
            QMessageBox.warning(self, issue.title, issue.message)
            return

        permission_token = self.context_permission_service.new_handoff_token()
        decision = self.context_permission_service.decide(
            PermissionResource.NETWORK,
            PermissionOperation.WRITE,
            handoff_token=permission_token,
        )
        if not decision.allowed:
            QMessageBox.warning(
                self,
                "网络权限未允许",
                "伙伴工坊需要访问图像接口才能生成图片。"
                "请先在“隐私与审计”中授权网络访问，再开始孵化。",
            )
            self._clear_generation_permission(permission_token)
            return

        answer = QMessageBox.question(
            self,
            "确认图像调用预算",
            f"本次计划调用 {form.planned_api_calls} 次图像 API，"
            f"最大预算为 {form.max_api_calls} 次。\n"
            "身份稿确认后才会继续生成动作；达到预算时任务会安全停止。"
            "限流、超时和服务异常最多自动退避重试 2 次，且每次都计入预算。"
            "\n确认开始吗？",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._clear_generation_permission(permission_token)
            return

        try:
            image_provider = self._create_image_provider(
                api_key=form.api_key,
                base_url=form.base_url,
                model=form.model,
                quality=form.quality,
            )
        except (KeyError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "图像 Provider 不可用", str(exc))
            self._clear_generation_permission(permission_token)
            return

        previous_api_key = SecretStore.get_image_api_key()
        previous_session_key = (
            self.session_secret_store.get_image_api_key()
        )
        saved = False
        if form.session_only_key:
            self.session_secret_store.set_image_api_key(form.api_key)
        else:
            saved = SecretStore.set_image_api_key(form.api_key)
            credential_available = bool(
                saved
                or (
                    previous_api_key
                    and previous_api_key == form.api_key
                )
            )
            if (
                not saved
                and previous_api_key
                and previous_api_key != form.api_key
            ):
                QMessageBox.critical(
                    self,
                    "图像 API Key 保存失败",
                    "Windows 凭据管理器仍保留旧 API Key；"
                    "为避免旧凭据覆盖新设置，本次孵化没有开始。",
                )
                self._clear_generation_permission(permission_token)
                return
            if form.api_key and not credential_available:
                QMessageBox.critical(
                    self,
                    "图像 API Key 保存失败",
                    "Windows 凭据管理器不可用。为避免明文密钥写入配置文件，"
                    "本次孵化没有开始；请修复凭据管理器后重试。",
                )
                self._clear_generation_permission(permission_token)
                return
        config_saved = self.config_manager.update_section("image_generation", {
            "base_url": form.base_url,
            "model": form.model,
            "quality": form.quality,
            "api_key": "",
        })
        if not config_saved:
            rollback_ok = True
            if form.session_only_key:
                self.session_secret_store.set_image_api_key(
                    previous_session_key
                )
            elif saved:
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
            self._clear_generation_permission(permission_token)
            return
        if not form.session_only_key:
            self.session_secret_store.set_image_api_key("")
        worker = PetGenerationWorker(
            api_key=form.api_key,
            base_url=form.base_url,
            model=form.model,
            quality=form.quality,
            pet_name=form.pet_name,
            personality=form.personality,
            style_notes=form.style_notes,
            reference_paths=self.reference_paths,
            full_hatch=form.full_hatch,
            generation_mode=form.generation_mode,
            allow_horizontal_mirror=form.allow_horizontal_mirror,
            max_api_calls=form.max_api_calls,
            run_store=self.run_store,
            image_provider=image_provider,
            permission_service=self.context_permission_service,
            permission_token=permission_token,
        )
        self._start_worker(worker)

    def _clear_generation_permission(self, permission_token: str | None) -> None:
        if not permission_token:
            return
        self.context_permission_service.clear_pending(
            PermissionResource.NETWORK,
            PermissionOperation.WRITE,
            handoff_token=permission_token,
        )

    def _create_image_provider(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        quality: str,
    ):
        """Create image providers only through the application registry."""
        registry = self.provider_catalog.get("image")
        if registry is None:
            # Keep standalone/legacy embedders working; the application
            # composition root always supplies the registry.
            return None
        provider_id = registry.default_id or "openai_compatible"
        return registry.create(
            provider_id,
            api_key=api_key,
            base_url=base_url,
            model=model,
            quality=quality,
        )

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
        self.task_panel.set_busy(True)
        self.progress.show()
        self.progress.setValue(0)
        worker.start()

    def _on_run_created(self, run_id: str):
        self.active_run_id = run_id
        self._refresh_runs(run_id)

    def _on_progress(self, value: int, text: str):
        self.progress.setValue(value)
        self.status.setText(text)
        if self.active_run_id:
            try:
                self._update_run_health(
                    self.run_store.load(self.active_run_id)
                )
            except PetGenerationRunError:
                pass

    def _on_error(self, message: str):
        self.status.setText("孵化没有完成。请检查接口、模型和额度后重试。")
        QMessageBox.critical(self, "伙伴工坊遇到问题", message)
        self._refresh_runs(self.active_run_id)

    def _on_review_ready(self, run_id: str, image_path: str):
        self.active_run_id = run_id
        decision = self._confirm_canonical(image_path)
        if decision == "switched":
            canonical = (
                self.run_store.workspace(run_id)
                / "images"
                / "canonical.png"
            )
            self._on_review_ready(run_id, str(canonical))
            return
        command = self.pet_lab_service.apply_canonical_review(
            run_id,
            decision,
        )
        self._execute_review_command(
            command,
            "身份稿已保留。可随时从“未完成任务”继续。",
        )

    def _confirm_canonical(self, image_path: str) -> str:
        return self.review_dialog_controller.confirm_canonical(
            str(self.active_run_id),
            image_path,
        )

    def _on_core_review_ready(
        self,
        run_id: str,
        sheet_path: str,
        report_path: str,
    ):
        self.active_run_id = run_id
        try:
            mode = self.run_store.load(run_id)["request"].get("mode")
        except PetGenerationRunError:
            mode = None
        result = self._confirm_action_review(
            run_id=run_id,
            title=(
                "审核核心动作与右向步态"
                if mode == "full"
                else "审核核心动作一致性"
            ),
            sheet_path=sheet_path,
            report_path=report_path,
            allow_accept=True,
        )
        if result[0] == "switched":
            self._resume_run(run_id)
            return
        command = self.pet_lab_service.apply_core_review(
            run_id,
            result[0],
            result[1],
        )
        self._execute_review_command(
            command,
            "核心动作审核已暂存，可从未完成任务继续。",
        )

    def _on_qa_review_ready(
        self,
        run_id: str,
        sheet_path: str,
        report_path: str,
    ):
        self.active_run_id = run_id
        result = self._confirm_action_review(
            run_id=run_id,
            title="自动 QA 需要返工",
            sheet_path=sheet_path,
            report_path=report_path,
            allow_accept=False,
        )
        if result[0] == "switched":
            self._resume_run(run_id)
            return
        command = self.pet_lab_service.apply_qa_review(
            run_id,
            result[0],
            result[1],
        )
        self._execute_review_command(
            command,
            "QA 报告和接触表已保留，可稍后选择动作返工。",
        )

    def _execute_review_command(self, command, deferred_text: str):
        if command.kind is PetLabCommandKind.RESUME:
            self._resume_run(command.run_id)
            return
        if command.kind is PetLabCommandKind.RETRY:
            self._resume_run(
                command.run_id,
                retry_task_id=command.retry_task_id,
            )
            return
        self.status.setText(deferred_text)
        self._refresh_runs(command.run_id)

    def _confirm_action_review(
        self,
        *,
        run_id: str,
        title: str,
        sheet_path: str,
        report_path: str,
        allow_accept: bool,
    ):
        return self.review_dialog_controller.confirm_action_review(
            run_id=run_id,
            title=title,
            sheet_path=sheet_path,
            report_path=report_path,
            allow_accept=allow_accept,
        )

    @staticmethod
    def _qa_retry_candidates(report):
        return PetLabService.qa_retry_candidates(report)

    def _on_package_ready(self, zip_path: str):
        try:
            plan = self.character_service.prepare_install(zip_path)
            if not plan.can_install:
                raise CharacterPackageError(
                    "内置角色不能被第三方角色包覆盖。"
                )
            existing = next(
                (
                    package
                    for package in self.character_service.list_packages()
                    if package.package_id == plan.package.package_id
                ),
                None,
            )
            confirmed = (
                self._confirm_replace(zip_path, plan.package, existing)
                if existing
                else self._confirm_install(zip_path, plan.package)
            )
            if not confirmed:
                try:
                    self._record_final_review("deferred")
                except PetGenerationRunError as exc:
                    QMessageBox.warning(
                        self,
                        "任务记录保存失败",
                        "角色包已保留，但暂缓决定无法写入任务记录："
                        f"\n{exc}",
                    )
                self.progress.setValue(100)
                self.status.setText(
                    "最终角色包已保留，但尚未安装。可从未完成任务继续。"
                )
                self._refresh_runs(self.active_run_id)
                return
            self.generated_package = self.character_service.install(
                plan,
                replace_confirmed=existing is not None,
                activate=not bool(self.active_run_id),
            ).package
        except CharacterPackageError as exc:
            self._on_error(str(exc))
            return
        if self.active_run_id:
            try:
                self.run_store.complete_install(
                    self.active_run_id,
                    self.generated_package.package_id,
                )
            except PetGenerationRunError as exc:
                self.progress.setValue(100)
                self.status.setText(
                    "角色包已导入，但任务记录失败，尚未启用。"
                )
                QMessageBox.warning(
                    self,
                    "安装记录未完成",
                    "角色包已经安全导入，但在写入任务记录前未设为"
                    f"当前伙伴。\n\n{exc}\n\n"
                    "请检查磁盘空间或目录权限后，从未完成任务重试。",
                )
                self._refresh_runs(self.active_run_id)
                return
            try:
                self.generated_package = self.character_service.activate(
                    self.generated_package.package_id
                )
            except CharacterPackageError as exc:
                self.progress.setValue(100)
                self.status.setText(
                    "角色包已安装，但无法设为当前伙伴。"
                )
                QMessageBox.warning(
                    self,
                    "角色启用失败",
                    "任务与角色包均已保存，但当前伙伴切换失败："
                    f"\n{exc}\n\n可稍后从角色管理中手动启用。",
                )
                self._refresh_runs(self.active_run_id)
                return
        self.progress.setValue(100)
        self.status.setText("孵化完成，角色已经安装并设为当前伙伴。")
        QMessageBox.information(
            self, "欢迎加入 Pixkin",
            f"{self.generated_package.name} 已经来到你的桌面。"
        )
        self.accept()

    def _confirm_install(self, zip_path: str, inspected) -> bool:
        return self.install_dialog_controller.confirm_install(
            zip_path,
            inspected,
        )

    def _confirm_replace(self, zip_path: str, inspected, existing) -> bool:
        return self.install_dialog_controller.confirm_replace(
            zip_path,
            inspected,
            existing,
        )

    def _available_animation_previews(self):
        return (
            self.install_dialog_controller
            .available_animation_previews()
        )

    def _show_animation_previews(self, previews):
        self.install_dialog_controller.show_animation_previews(
            previews
        )

    def _set_package_preview(self, dialog, zip_path: str):
        self.install_dialog_controller.set_package_preview(
            dialog,
            zip_path,
        )

    def _package_preview_pixmap(self, zip_path: str) -> QPixmap:
        return self.install_dialog_controller.package_preview_pixmap(
            zip_path
        )

    def _record_final_review(self, decision: str):
        if not self.active_run_id:
            return None
        return self.run_store.record_review(
            self.active_run_id, "final_package", decision
        )

    def _refresh_runs(self, selected_id=None):
        resumable = [
            record
            for record in self.run_store.list_runs()
            if record["status"] != "complete"
        ]
        options = []
        for record in resumable:
            request = record.get("request", {})
            name = request.get("pet_name") or record["id"]
            stage = GenerationStateMachine.label(record.get("stage"))
            used = PetGenerationWorker.api_calls_used(record)
            budget = request.get("max_api_calls")
            usage = (
                f"{used}/{int(budget)}"
                if budget is not None
                else str(used)
            )
            timing = PetGenerationWorker.api_timing_summary(record)
            elapsed = (
                f" · {timing['total_ms'] / 1000:.1f}s"
                if timing["count"]
                else ""
            )
            options.append(
                PetLabRunOption(
                    run_id=str(record["id"]),
                    label=(
                        f"{name} · {stage} · API {usage}{elapsed}"
                    ),
                )
            )
        busy = self.worker is not None and self.worker.isRunning()
        self.task_panel.set_runs(
            options,
            selected_id=selected_id,
            busy=busy,
        )
        self._on_run_changed()

    def _on_run_changed(self, _index=None):
        run_id = self.run_input.currentData()
        if not run_id:
            self.task_panel.clear_run()
            return
        try:
            record = self.run_store.load(run_id)
            tools = self.pet_lab_service.task_tools(run_id)
        except PetGenerationRunError:
            self.task_panel.clear_run("任务记录无法读取。")
            return
        health = PetGenerationDiagnostics.summarize(record)
        busy = self.worker is not None and self.worker.isRunning()
        self.task_panel.show_record(
            PetGenerationDiagnostics.compact_text(health),
            tools,
            busy=busy,
        )

    def _update_run_health(self, record):
        health = PetGenerationDiagnostics.summarize(record)
        self.run_health.setText(
            PetGenerationDiagnostics.compact_text(health)
        )

    def _show_generation_diagnostics(self):
        self.diagnostics_controller.show()

    def _copy_generation_summary(self):
        self.diagnostics_controller.copy_summary()

    def _copy_github_issue_markdown(self):
        self.diagnostics_controller.copy_issue_markdown()

    def _export_generation_issue_bundle(self):
        self.diagnostics_controller.export_issue_bundle()

    def _inspect_generation_issue_bundle(self):
        self.diagnostics_controller.inspect_issue_bundle()

    def _on_task_tool_changed(self, _index=None):
        busy = self.worker is not None and self.worker.isRunning()
        self.task_panel.set_busy(busy)

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
            candidates = self.pet_lab_service.candidate_options(
                run_id,
                task_id,
            )
        except PetGenerationRunError as exc:
            self._on_error(str(exc))
            return False
        if len(candidates) < 2:
            return False

        candidate_id = CandidateSelectionDialog(
            task_id,
            candidates,
            self,
        ).exec_selection()
        if candidate_id is None:
            return False
        selected = next(
            candidate for candidate in candidates
            if candidate.candidate_id == candidate_id
        )
        if selected.selected:
            return False

        if self.pet_lab_service.canonical_switch_invalidates_actions(
            run_id,
            task_id,
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

        try:
            result = self.pet_lab_service.select_candidate(
                run_id,
                task_id,
                str(candidate_id),
            )
        except PetGenerationRunError as exc:
            self._on_error(str(exc))
            return False
        self.active_run_id = run_id
        self.status.setText(
            f"已切换 {task_id} 到 {candidate_id}，"
            "受影响的审核与 QA 将重新执行。"
        )
        self._refresh_runs(run_id)
        if continue_flow:
            if task_id == "canonical":
                self._on_review_ready(
                    run_id,
                    str(result.active_path),
                )
            else:
                self._resume_run(run_id)
        return True

    def _candidate_count(self, run_id, task_id):
        return self.pet_lab_service.candidate_count(run_id, task_id)

    def _tasks_with_candidates(self, run_id, task_ids):
        return self.pet_lab_service.tasks_with_candidates(
            run_id,
            task_ids,
        )

    def _continue_run(self):
        if self.worker is not None and self.worker.isRunning():
            return
        run_id = self.run_input.currentData()
        if not run_id:
            return
        try:
            action = self.pet_lab_service.continuation(run_id)
        except PetGenerationRunError as exc:
            self._on_error(str(exc))
            return
        if action.kind is PetLabContinuationKind.CANONICAL_REVIEW:
            self._on_review_ready(
                run_id,
                str(action.artifacts["canonical"]),
            )
        elif action.kind is PetLabContinuationKind.CORE_REVIEW:
            self._on_core_review_ready(
                run_id,
                str(action.artifacts["sheet"]),
                str(action.artifacts["report"]),
            )
        elif action.kind is PetLabContinuationKind.QA_REVIEW:
            self._on_qa_review_ready(
                run_id,
                str(action.artifacts["sheet"]),
                str(action.artifacts["report"]),
            )
        elif action.kind is PetLabContinuationKind.FINAL_REVIEW:
            self.active_run_id = run_id
            self._on_package_ready(str(action.artifacts["package"]))
        else:
            self._resume_run(run_id)

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
        budget = record.get("request", {}).get("max_api_calls")
        used = PetGenerationWorker.api_calls_used(record)
        if needs_generation and budget is not None and used >= int(budget):
            increased, confirmed = QInputDialog.getInt(
                self,
                "提高图像调用预算",
                f"当前已使用 {used}/{int(budget)} 次。"
                "继续生成至少需要再增加 1 次预算：",
                min(1000, used + 3),
                used + 1,
                1000,
                1,
            )
            if not confirmed:
                return
            self.run_store.update_request(
                run_id,
                {"max_api_calls": increased},
            )
        permission_token = None
        if needs_generation:
            permission_token = self.context_permission_service.new_handoff_token()
            decision = self.context_permission_service.decide(
                PermissionResource.NETWORK,
                PermissionOperation.WRITE,
                handoff_token=permission_token,
            )
            if not decision.allowed:
                QMessageBox.warning(
                    self,
                    "网络权限未允许",
                    "继续生成需要访问图像接口，请先授权网络访问。",
                )
                self._clear_generation_permission(permission_token)
                return
        try:
            image_provider = self._create_image_provider(
                api_key=api_key,
                base_url=str(record.get("request", {}).get("image_base_url", "")),
                model=str(record.get("request", {}).get("image_model", "gpt-image-2")),
                quality=str(record.get("request", {}).get("image_quality", "medium")),
            )
            worker = PetGenerationWorker.resume_from(
                run_id=run_id,
                api_key=api_key,
                run_store=self.run_store,
                retry_task_id=retry_task_id,
                image_provider=image_provider,
                permission_service=self.context_permission_service,
                permission_token=permission_token,
            )
        except (PetGenerationRunError, KeyError, ValueError, RuntimeError) as exc:
            self._clear_generation_permission(permission_token)
            self._on_error(str(exc))
            return
        self.active_run_id = run_id
        self._start_worker(worker)

    def _on_worker_finished(self, worker):
        is_current = self.worker is worker
        if is_current:
            self.worker = None
            self.task_panel.set_busy(False)
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
