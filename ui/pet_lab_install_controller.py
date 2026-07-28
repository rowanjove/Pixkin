"""Final package preview and install-confirmation dialogs for Pet Lab."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QMessageBox, QWidget

from core.character_package import CharacterPackageError
from core.pet_generation_run import (
    PetGenerationRunError,
    PetGenerationRunStore,
)
from core.services.character_service import CharacterService
from ui.pet_lab_components import (
    AnimationPreviewDialog,
    PackageInstallDialog,
)


class PetLabInstallDialogController:
    def __init__(
        self,
        *,
        parent: QWidget,
        run_store: PetGenerationRunStore,
        character_service: CharacterService,
        active_run_id: Callable[[], str | None],
        animation_previews: Callable[
            [str | None], Mapping[str, Path]
        ],
    ):
        self.parent = parent
        self.run_store = run_store
        self.character_service = character_service
        self.active_run_id = active_run_id
        self.animation_previews = animation_previews

    def confirm_install(self, zip_path: str, inspected: Any) -> bool:
        dialog = PackageInstallDialog(
            title="最终预览与安装",
            icon=QMessageBox.Icon.Question,
            text=f"安装“{inspected.name}”并设为当前伙伴？",
            informative_text=(
                "这是安装前的最终确认。选择“否”会保留角色包，"
                "稍后仍可继续。"
            ),
            preview=self.package_preview_pixmap(zip_path),
            animation_previews=self.available_animation_previews(),
            parent=self.parent,
        )
        return dialog.exec_confirmation()

    def confirm_replace(
        self,
        zip_path: str,
        inspected: Any,
        existing: Any,
    ) -> bool:
        dialog = PackageInstallDialog(
            title="确认覆盖角色",
            icon=QMessageBox.Icon.Warning,
            text=f"已经安装了同 ID 角色“{existing.name}”。",
            informative_text=(
                f"新生成的“{inspected.name}”将永久替换原角色图片和人设。\n"
                "确认使用下方最终预览并覆盖吗？"
            ),
            preview=self.package_preview_pixmap(zip_path),
            animation_previews=self.available_animation_previews(),
            parent=self.parent,
        )
        return dialog.exec_confirmation()

    def available_animation_previews(self) -> Mapping[str, Path]:
        return self.animation_previews(self.active_run_id())

    def show_animation_previews(
        self,
        previews: Mapping[str, Path],
    ):
        if previews:
            AnimationPreviewDialog(
                previews,
                self.parent,
            ).exec_preview()

    def set_package_preview(self, dialog: Any, zip_path: str):
        preview = self.package_preview_pixmap(zip_path)
        if not preview.isNull():
            dialog.setIconPixmap(preview)

    def package_preview_pixmap(self, zip_path: str) -> QPixmap:
        run_id = self.active_run_id()
        if run_id:
            try:
                record = self.run_store.load(run_id)
                sheet = Path(
                    str(
                        record["artifacts"].get(
                            "qa_contact_sheet", ""
                        )
                    )
                )
                preview = QPixmap(str(sheet))
                if sheet.is_file() and not preview.isNull():
                    return preview.scaled(
                        420,
                        300,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
            except PetGenerationRunError:
                pass
        try:
            raw = self.character_service.read_archive_preview(zip_path)
            preview = QPixmap()
            if raw and preview.loadFromData(raw):
                return preview.scaled(
                    160,
                    160,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
        except CharacterPackageError:
            pass
        return QPixmap()
