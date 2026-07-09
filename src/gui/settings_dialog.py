from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from src.gui.i18n import available_languages, normalize_language, tr


@dataclass(slots=True)
class AppSettings:
    probability_threshold: float
    model_name: str
    pretrained: str
    language: str


class SettingsDialog(QDialog):
    def __init__(self, parent=None, *, settings: AppSettings):
        super().__init__(parent)
        self._language = normalize_language(settings.language)
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setWindowTitle(tr(self._language, "settings_title"))
        self._build_ui(settings)

    def _build_ui(self, settings: AppSettings):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        shell = QFrame()
        shell.setObjectName("SettingsShell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(24, 24, 24, 24)
        shell_layout.setSpacing(18)

        header = QFrame()
        header.setObjectName("SettingsHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        title = QLabel(tr(self._language, "settings_header"))
        title.setObjectName("DialogTitle")
        subtitle = QLabel(tr(self._language, "settings_description"))
        subtitle.setObjectName("DialogSubtitle")
        subtitle.setWordWrap(True)
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        shell_layout.addWidget(header)

        card = QFrame()
        card.setObjectName("SettingsCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(14)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.0, 1.0)
        self.threshold_spin.setSingleStep(0.01)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setValue(float(settings.probability_threshold))

        self.model_name_edit = QLineEdit(settings.model_name)
        self.model_name_edit.setPlaceholderText("xlm-roberta-base-ViT-B-32")

        self.pretrained_edit = QLineEdit(settings.pretrained)
        self.pretrained_edit.setPlaceholderText("laion5b_s13b_b90k")

        self.language_combo = QComboBox()
        for code, name in available_languages():
            self.language_combo.addItem(name, code)
        self._select_language(settings.language)

        form.addRow(tr(self._language, "probability_threshold"), self.threshold_spin)
        form.addRow(tr(self._language, "model_name"), self.model_name_edit)
        form.addRow(tr(self._language, "pretrained"), self.pretrained_edit)
        form.addRow(tr(self._language, "language"), self.language_combo)

        self.model_tip = QLabel(tr(self._language, "settings_model_tip"))
        self.model_tip.setObjectName("SettingsHint")
        self.pretrained_tip = QLabel(tr(self._language, "settings_pretrained_tip"))
        self.pretrained_tip.setObjectName("SettingsHint")
        self.model_tip.setWordWrap(True)
        self.pretrained_tip.setWordWrap(True)

        card_layout.addLayout(form)
        card_layout.addWidget(self.model_tip)
        card_layout.addWidget(self.pretrained_tip)
        shell_layout.addWidget(card)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer_note = QLabel(tr(self._language, "apply_note"))
        footer_note.setObjectName("SettingsFooterNote")
        footer_note.setWordWrap(True)
        footer.addWidget(footer_note, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText(tr(self._language, "ok"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr(self._language, "cancel"))
        buttons.button(QDialogButtonBox.Ok).setObjectName("PrimaryButton")
        buttons.button(QDialogButtonBox.Cancel).setObjectName("SecondaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons, 0, Qt.AlignRight)

        shell_layout.addLayout(footer)
        root.addWidget(shell)

    def _select_language(self, language: str):
        index = self.language_combo.findData(language)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)

    def values(self) -> AppSettings:
        return AppSettings(
            probability_threshold=float(self.threshold_spin.value()),
            model_name=self.model_name_edit.text().strip() or "xlm-roberta-base-ViT-B-32",
            pretrained=self.pretrained_edit.text().strip() or "laion5b_s13b_b90k",
            language=str(self.language_combo.currentData()),
        )
