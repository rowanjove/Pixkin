from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QMessageBox, QFrame, QGraphicsDropShadowEffect
)

from core.character_package import CharacterPackageError, CharacterPackageManager
from core.paths import resource_path
from ui.theme import resolved_theme


class FirstRunWindow(QDialog):
    """首次启动必须导入一个角色包，成功后才进入主程序。"""

    def __init__(self, package_manager: CharacterPackageManager, parent=None):
        super().__init__(parent)
        self.package_manager = package_manager
        self.imported_package = None
        self.setWindowTitle("欢迎使用 Pixkin")
        self.setWindowIcon(QIcon(str(resource_path("assets/pixkin.ico"))))
        self.setModal(True)
        self.setMinimumSize(680, 520)
        self.resize(720, 560)
        self.setFont(QFont("Microsoft YaHei UI", 9))
        self.setAcceptDrops(True)
        self.setStyleSheet(
            self._style(resolved_theme(self.package_manager.config))
        )
        self._build_ui()

    @staticmethod
    def _style(theme="dark"):
        base = """
            QDialog { background: #0D1320; color: #F7F5FF; }
            QFrame#hero {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #172238, stop:1 #222B47);
                border: 1px solid #33415D;
                border-radius: 24px;
            }
            QLabel#eyebrow { color: #7BE0D0; font-size: 11px; font-weight: 800; letter-spacing:2px; }
            QLabel#heroTitle { color: white; font-size: 29px; font-weight: 800; }
            QLabel#heroText { color: #ADB7C9; font-size: 13px; }
            QFrame#card { background: #171F30; border: 1px solid #2D3850; border-radius: 18px; }
            QLabel#cardTitle { color: #F7F5FF; font-size: 17px; font-weight: 750; }
            QLabel#hint { color: #8792A8; font-size: 12px; }
            QPushButton {
                border: none; border-radius: 11px; padding: 11px 18px;
                font-size: 13px; font-weight: 650;
            }
            QPushButton#primary { background: #7BE0D0; color: #101624; }
            QPushButton#primary:hover { background: #95EBDD; }
            QPushButton#secondary { background: #263249; color: #DDE3EE; }
            QPushButton#secondary:hover { background: #303D57; }
            QPushButton#quiet { background: transparent; color: #8792A8; }
            QPushButton#quiet:hover { background: #202A3D; }
            QLabel#formatHint {
                background: #202A3D; color: #94A0B5;
                border-radius: 9px; padding: 9px 12px;
            }
        """
        if theme != "light":
            return base
        return base + """
            QDialog { background: #EEF2F7; color: #172033; }
            QFrame#hero {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #FFFFFF, stop:1 #EAF4F3);
                border-color: #D5DEE9;
            }
            QLabel#eyebrow { color: #16897D; }
            QLabel#heroTitle, QLabel#cardTitle { color: #172033; }
            QLabel#heroText, QLabel#hint { color: #667085; }
            QFrame#card { background: #FFFFFF; border-color: #D7DFEA; }
            QPushButton#primary { background: #6ED8C9; color: #10241F; }
            QPushButton#secondary { background: #E8EDF4; color: #344054; }
            QPushButton#quiet { color: #667085; }
            QPushButton#quiet:hover { background: #E8EDF4; }
            QLabel#formatHint { background: #F0F3F7; color: #667085; }
        """

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 22)
        root.setSpacing(16)

        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(30, 24, 28, 24)
        text_stack = QVBoxLayout()
        text_stack.setSpacing(7)
        eyebrow = QLabel("PIXKIN · 初次见面")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("先把你的角色带进来")
        title.setObjectName("heroTitle")
        text = QLabel(
            "Pixkin 使用 ZIP 角色包工作。角色包里包含透明图片、动作帧和 "
            "character.md 人设配置，导入后全部保存在本机。"
        )
        text.setObjectName("heroText")
        text.setWordWrap(True)
        text.setMaximumWidth(470)
        text_stack.addWidget(eyebrow)
        text_stack.addWidget(title)
        text_stack.addWidget(text)
        hero_layout.addLayout(text_stack, 1)
        badge = QLabel()
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(106, 106)
        badge.setPixmap(
            QPixmap(str(resource_path("assets/pixkin/pip-avatar.png"))).scaled(
                102, 102,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        hero_layout.addWidget(badge)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(60, 43, 122, 75))
        hero.setGraphicsEffect(shadow)
        root.addWidget(hero)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 20, 24, 20)
        card_layout.setSpacing(11)
        card_title = QLabel("导入角色包")
        card_title.setObjectName("cardTitle")
        card_layout.addWidget(card_title)
        hint = QLabel(
            "你可以选择自己的角色包，也可以先导入随软件附带的示例包。"
            "支持 PNG / WebP 多帧动作；单包解压后上限 100 MB。"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        card_layout.addWidget(hint)

        button_row = QHBoxLayout()
        choose = QPushButton("选择 ZIP 文件")
        choose.setObjectName("primary")
        choose.clicked.connect(self._choose_zip)
        sample = QPushButton("导入示例角色")
        sample.setObjectName("secondary")
        sample.clicked.connect(self._import_sample)
        button_row.addWidget(choose)
        button_row.addWidget(sample)
        button_row.addStretch()
        card_layout.addLayout(button_row)

        format_hint = QLabel(
            "包结构：character.md  +  images/idle.png  +  其他动作图片"
        )
        format_hint.setObjectName("hint")
        format_hint.setObjectName("formatHint")
        card_layout.addWidget(format_hint)
        root.addWidget(card)
        root.addStretch()

        footer = QHBoxLayout()
        privacy = QLabel("角色图片与配置只保存在这台电脑上")
        privacy.setObjectName("hint")
        cancel = QPushButton("退出")
        cancel.setObjectName("quiet")
        cancel.clicked.connect(self.reject)
        footer.addWidget(privacy)
        footer.addStretch()
        footer.addWidget(cancel)
        root.addLayout(footer)

    def _choose_zip(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择角色包", "", "Pixkin 角色包 (*.zip)"
        )
        if path:
            self._import(path)

    def _import_sample(self):
        sample = resource_path("character-packs/shanshan.zip")
        if not sample.is_file():
            QMessageBox.warning(
                self, "没有找到示例包", "当前发行包中没有附带示例角色。"
            )
            return
        self._import(str(sample), replace=True)

    def _import(self, path: str, replace=False):
        try:
            self.imported_package = self.package_manager.import_zip(
                path, replace=replace
            )
        except CharacterPackageError as exc:
            QMessageBox.warning(self, "角色包无法导入", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", f"导入角色包时发生错误：\n{exc}")
            return
        self.package_manager.config.update_section(
            "app", {"first_run_complete": True}
        )
        QMessageBox.information(
            self,
            "导入成功",
            f"角色“{self.imported_package.name}”已经准备好了。",
        )
        self.accept()

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].toLocalFile().lower().endswith(".zip"):
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = event.mimeData().urls()[0].toLocalFile()
        if Path(path).is_file():
            self._import(path)
