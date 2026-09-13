"""Model connection controls and non-blocking provider health checks."""

from collections.abc import Callable

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.providers.chat.base import ChatProviderHealth
from core.secrets import SecretStore
from core.services.session_secret_store import SessionSecretStore
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionOperation,
    PermissionResource,
)


class ChatProviderHealthWorker(QThread):
    checked = pyqtSignal(object)

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        provider_factory: Callable | None = None,
        permission_service: ContextPermissionService | None = None,
        permission_token: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.provider_factory = provider_factory
        self.permission_service = permission_service or ContextPermissionService()
        self.permission_token = permission_token

    def run(self) -> None:
        provider = None
        try:
            decision = self.permission_service.decide(
                PermissionResource.NETWORK,
                PermissionOperation.READ,
                resolve_ask=False,
                consume_pending=True,
                handoff_token=self.permission_token,
            )
            if not decision.allowed:
                raise PermissionError("网络权限未允许")
            if self.provider_factory is None:
                raise RuntimeError("未注册聊天 Provider")
            provider = self.provider_factory(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=15.0,
            )
            health = provider.health_check(self.model)
        except Exception as exc:
            health = ChatProviderHealth(
                False,
                f"连接检查失败：{type(exc).__name__}",
                category="unknown",
                recovery="检查接口地址后重试。",
            )
        finally:
            if provider is not None:
                try:
                    provider.close()
                except Exception:
                    pass
        self.checked.emit(health)


class ModelSettingsPanel(QWidget):
    """Own model credentials, connection status and model discovery."""

    def __init__(
        self,
        config_manager,
        session_secrets: SessionSecretStore,
        *,
        provider_registry=None,
        permission_service: ContextPermissionService | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.session_secrets = session_secrets
        self.provider_registry = provider_registry
        self.permission_service = permission_service or ContextPermissionService()
        self._models: tuple[str, ...] = ()
        self._health_worker = None
        self._permission_token = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setSpacing(11)
        self.api_url_input = QLineEdit(
            self.config_manager.get("api", "base_url", "")
        )
        self.api_url_input.setPlaceholderText(
            "https://api.example.com/v1"
        )
        stored_key = (
            self.session_secrets.get_chat_api_key()
            or SecretStore.get_api_key()
            or self.config_manager.get("api", "api_key", "")
        )
        self.api_key_input = QLineEdit(stored_key)
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("sk-...")
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key_input, 1)
        reveal = QPushButton("显示")
        reveal.setObjectName("link")
        reveal.setCheckable(True)
        reveal.toggled.connect(
            lambda checked: self._toggle_key(reveal, checked)
        )
        key_row.addWidget(reveal)
        self.api_model_input = QLineEdit(
            self.config_manager.get("api", "model", "")
        )
        model_row = QHBoxLayout()
        model_row.addWidget(self.api_model_input, 1)
        self.choose_model_btn = QPushButton("选择模型")
        self.choose_model_btn.setObjectName("secondary")
        self.choose_model_btn.setEnabled(False)
        self.choose_model_btn.clicked.connect(self._choose_model)
        model_row.addWidget(self.choose_model_btn)
        form.addRow("接口地址", self.api_url_input)
        form.addRow("API Key", key_row)
        self.session_api_key_cb = QCheckBox(
            "仅本次运行使用 API Key（不写入凭据管理器）"
        )
        self.session_api_key_cb.setChecked(
            bool(self.session_secrets.get_chat_api_key())
        )
        form.addRow("", self.session_api_key_cb)
        form.addRow("模型名称", model_row)
        self.preset_input = QComboBox()
        self.preset_input.addItem("快速", "fast")
        self.preset_input.addItem("均衡", "balanced")
        self.preset_input.addItem("深入", "deep")
        preset_index = self.preset_input.findData(
            self.config_manager.get("api", "preset", "balanced")
        )
        self.preset_input.setCurrentIndex(max(0, preset_index))
        form.addRow("回答预设", self.preset_input)
        self.input_cost = QDoubleSpinBox()
        self.input_cost.setRange(0.0, 10000.0)
        self.input_cost.setDecimals(4)
        self.input_cost.setSuffix(" / 百万 token")
        self.input_cost.setValue(
            float(
                self.config_manager.get(
                    "api", "input_cost_per_million", 0.0
                )
            )
        )
        self.output_cost = QDoubleSpinBox()
        self.output_cost.setRange(0.0, 10000.0)
        self.output_cost.setDecimals(4)
        self.output_cost.setSuffix(" / 百万 token")
        self.output_cost.setValue(
            float(
                self.config_manager.get(
                    "api", "output_cost_per_million", 0.0
                )
            )
        )
        form.addRow("输入单价", self.input_cost)
        form.addRow("输出单价", self.output_cost)
        layout.addLayout(form)

        health_row = QHBoxLayout()
        self.test_connection_btn = QPushButton("测试连接与模型")
        self.test_connection_btn.setObjectName("secondary")
        self.test_connection_btn.clicked.connect(
            self._test_connection
        )
        self.connection_status = QLabel(
            "尚未检查；测试不会发送聊天内容。"
        )
        self.connection_status.setObjectName("muted")
        self.connection_status.setWordWrap(True)
        health_row.addWidget(self.test_connection_btn)
        health_row.addWidget(self.connection_status, 1)
        layout.addLayout(health_row)
        self.capability_status = QLabel(
            "能力：流式输出 · 工具调用 · 取消生成 · 模型健康检查"
        )
        self.capability_status.setObjectName("muted")
        self.capability_status.setWordWrap(True)
        layout.addWidget(self.capability_status)

    def _test_connection(self) -> None:
        base_url = self.api_url_input.text().strip()
        api_key = self.api_key_input.text().strip()
        model = self.api_model_input.text().strip()
        if not base_url or not api_key or not model:
            self.connection_status.setText(
                "请先填写接口地址、API Key 和模型名称。"
            )
            return
        if (
            self._health_worker is not None
            and self._health_worker.isRunning()
        ):
            return
        permission_token = self.permission_service.new_handoff_token()
        self._permission_token = permission_token
        decision = self.permission_service.decide(
            PermissionResource.NETWORK,
            PermissionOperation.READ,
            handoff_token=permission_token,
        )
        if not decision.allowed:
            self.connection_status.setText(
                "网络权限未允许；请先在“隐私与审计”中授权。"
            )
            self._clear_permission_token()
            return
        self.test_connection_btn.setDisabled(True)
        self.connection_status.setText("正在检查连接和模型列表…")
        worker = ChatProviderHealthWorker(
            base_url=base_url,
            api_key=api_key,
            model=model,
            parent=QApplication.instance(),
            provider_factory=(
                (lambda **kwargs: self.provider_registry.create(
                    "openai_compatible", **kwargs
                ))
                if self.provider_registry is not None
                else None
            ),
            permission_service=self.permission_service,
            permission_token=permission_token,
        )
        self._health_worker = worker
        worker.checked.connect(self._on_health_checked)
        worker.finished.connect(self._clear_permission_token)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _clear_permission_token(self) -> None:
        self.permission_service.clear_pending(
            PermissionResource.NETWORK,
            PermissionOperation.READ,
            handoff_token=self._permission_token,
        )
        self._permission_token = None

    def _on_health_checked(self, health: object) -> None:
        self.test_connection_btn.setEnabled(True)
        if not isinstance(health, ChatProviderHealth):
            self.connection_status.setText("连接检查返回了无效结果。")
            return
        self._models = health.models
        self.choose_model_btn.setEnabled(bool(self._models))
        if health.healthy:
            suffix = (
                f" 已发现 {len(self._models)} 个模型。"
                if self._models
                else ""
            )
            self.connection_status.setText(health.message + suffix)
        else:
            self.connection_status.setText(
                f"{health.message} {health.recovery}".strip()
            )

    def _choose_model(self) -> None:
        if not self._models:
            return
        current = self.api_model_input.text().strip()
        index = (
            self._models.index(current)
            if current in self._models
            else 0
        )
        selected, accepted = QInputDialog.getItem(
            self,
            "选择接口模型",
            "可用模型：",
            list(self._models),
            index,
            False,
        )
        if accepted and selected:
            self.api_model_input.setText(selected)

    def _toggle_key(self, button: QPushButton, checked: bool) -> None:
        self.api_key_input.setEchoMode(
            QLineEdit.EchoMode.Normal
            if checked
            else QLineEdit.EchoMode.Password
        )
        button.setText("隐藏" if checked else "显示")
