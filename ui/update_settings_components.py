"""Non-blocking update controls for installed Pixkin builds."""

from __future__ import annotations

from PyQt6.QtCore import QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.services.update_service import (
    UpdateRelease,
    UpdateService,
    detect_install_mode,
)
from core.version import VERSION


class UpdateCheckWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        service: UpdateService,
        manifest_url: str,
        channel: str,
    ):
        super().__init__(QApplication.instance())
        self.service = service
        self.manifest_url = manifest_url
        self.channel = channel

    def run(self):
        try:
            release = self.service.fetch_release(
                self.manifest_url,
                channel=self.channel,
                current_version=VERSION,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(release)


class UpdateDownloadWorker(QThread):
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, service: UpdateService, release: UpdateRelease):
        super().__init__(QApplication.instance())
        self.service = service
        self.release = release

    def run(self):
        try:
            installer = self.service.download_installer(self.release)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(str(installer))


class UpdateSettingsPanel(QWidget):
    install_requested = pyqtSignal(str)
    rollback_requested = pyqtSignal(str)

    def __init__(
        self,
        *,
        service: UpdateService | None,
        manifest_urls: dict[str, str] | None,
        channel: str,
        automatic_updates: bool,
        install_mode: str | None = None,
    ):
        super().__init__()
        self.service = service
        self.manifest_urls = manifest_urls or {}
        self.install_mode = install_mode or detect_install_mode()
        self._release: UpdateRelease | None = None
        self._worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.mode_label = QLabel(
            "安装版：支持签名清单自动检查、验证下载和确认安装。"
            if self.install_mode == "installed"
            else "便携/文件夹版：不会调用安装版自动更新路径，请手动下载新版本。"
        )
        self.mode_label.setObjectName("muted")
        self.mode_label.setWordWrap(True)
        layout.addWidget(self.mode_label)

        row = QHBoxLayout()
        self.channel_input = QComboBox()
        self.channel_input.addItem("稳定版", "stable")
        self.channel_input.addItem("测试版", "beta")
        selected = self.channel_input.findData(channel)
        self.channel_input.setCurrentIndex(max(0, selected))
        row.addWidget(QLabel("更新渠道"))
        row.addWidget(self.channel_input, 1)
        layout.addLayout(row)

        self.automatic_input = QCheckBox("每天最多自动检查一次")
        self.automatic_input.setChecked(automatic_updates)
        self.automatic_input.setEnabled(self.install_mode == "installed")
        layout.addWidget(self.automatic_input)

        actions = QHBoxLayout()
        self.check_button = QPushButton("检查更新")
        self.check_button.setObjectName("secondary")
        self.check_button.clicked.connect(self.check_now)
        self.download_button = QPushButton("下载已验证安装器")
        self.download_button.setObjectName("primary")
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self.download)
        actions.addWidget(self.check_button)
        actions.addWidget(self.download_button)
        actions.addStretch()
        layout.addLayout(actions)

        self.status_label = QLabel(f"当前版本：{VERSION}")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.notes_button = QPushButton("查看发布说明")
        self.notes_button.setObjectName("link")
        self.notes_button.setVisible(False)
        self.notes_button.clicked.connect(self.open_release_notes)
        layout.addWidget(self.notes_button)
        self.rollback_installer = (
            service.available_rollback(current_version=VERSION)
            if service is not None and self.install_mode == "installed"
            else None
        )
        self.rollback_button = QPushButton("回滚到上一稳定安装器")
        self.rollback_button.setObjectName("danger")
        self.rollback_button.setVisible(
            self.rollback_installer is not None
        )
        self.rollback_button.clicked.connect(self.request_rollback)
        layout.addWidget(self.rollback_button)

        available = service is not None and bool(self._manifest_url())
        self.check_button.setEnabled(available)
        if not available:
            self.status_label.setText(
                f"当前版本：{VERSION}。发布端尚未配置更新清单地址。"
            )

    def settings(self) -> tuple[str, bool]:
        return (
            str(self.channel_input.currentData()),
            self.automatic_input.isChecked(),
        )

    def _manifest_url(self) -> str:
        return str(
            self.manifest_urls.get(
                str(self.channel_input.currentData()),
                "",
            )
            or ""
        )

    def check_now(self):
        if self.service is None or not self._manifest_url():
            return
        self._set_busy(True, "正在验证更新清单……")
        worker = UpdateCheckWorker(
            self.service,
            self._manifest_url(),
            str(self.channel_input.currentData()),
        )
        worker.completed.connect(self._check_completed)
        worker.failed.connect(self._operation_failed)
        worker.finished.connect(self._worker_finished)
        self._worker = worker
        worker.start()

    def _check_completed(self, release: UpdateRelease):
        self._release = release
        self.status_label.setText(
            f"发现 {release.version}（{release.channel}），"
            "清单签名与安装器元数据已验证。"
        )
        self.notes_button.setVisible(True)
        self.download_button.setEnabled(
            self.install_mode == "installed"
        )

    def download(self):
        if self.service is None or self._release is None:
            return
        answer = QMessageBox.question(
            self,
            "下载更新",
            f"下载 Pixkin {self._release.version} 安装器？\n\n"
            "下载完成后仍会再次询问是否安装。",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True, "正在下载并校验安装器……")
        worker = UpdateDownloadWorker(self.service, self._release)
        worker.completed.connect(self._download_completed)
        worker.failed.connect(self._operation_failed)
        worker.finished.connect(self._worker_finished)
        self._worker = worker
        worker.start()

    def _download_completed(self, installer: str):
        self.status_label.setText(
            "安装器大小与 SHA-256 均已验证，等待安装确认。"
        )
        self.install_requested.emit(installer)

    def _operation_failed(self, message: str):
        self.status_label.setText(f"更新操作未完成：{message}")

    def _worker_finished(self):
        self._worker = None
        self._set_busy(False)

    def _set_busy(self, busy: bool, message: str = ""):
        self.check_button.setEnabled(
            not busy and self.service is not None and bool(self._manifest_url())
        )
        self.download_button.setEnabled(
            not busy
            and self._release is not None
            and self.install_mode == "installed"
        )
        if message:
            self.status_label.setText(message)

    def open_release_notes(self):
        if self._release is not None:
            QDesktopServices.openUrl(QUrl(self._release.release_notes_url))

    def request_rollback(self):
        if self.rollback_installer is not None:
            self.rollback_requested.emit(str(self.rollback_installer))
