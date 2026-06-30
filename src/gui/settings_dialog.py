from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QDoubleSpinBox,
    QLineEdit,
    QLabel,
    QVBoxLayout,
)


@dataclass(slots=True)
class AppSettings:
    probability_threshold: float
    model_name: str
    pretrained: str
    language: str


class SettingsDialog(QDialog):
    def __init__(self, parent=None, *, settings: AppSettings):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self._build_ui(settings)

    def _build_ui(self, settings: AppSettings):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        header = QLabel("AI analyzer configuration")
        header.setObjectName("DialogTitle")
        layout.addWidget(header)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.0, 1.0)
        self.threshold_spin.setSingleStep(0.01)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setValue(float(settings.probability_threshold))

        self.model_name_edit = QLineEdit(settings.model_name)
        self.model_name_edit.setPlaceholderText("ViT-B-32")

        self.pretrained_edit = QLineEdit(settings.pretrained)
        self.pretrained_edit.setPlaceholderText("laion2b_s34b_b79k")

        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("简体中文", "zh")
        self._select_language(settings.language)

        form.addRow("Probability threshold", self.threshold_spin)
        form.addRow("Model name", self.model_name_edit)
        form.addRow("Pretrained", self.pretrained_edit)
        form.addRow("Language", self.language_combo)

        section = QFrame()
        section.setObjectName("DialogSection")
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.addLayout(form)
        layout.addWidget(section)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _select_language(self, language: str):
        index = self.language_combo.findData(language)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)

    def values(self) -> AppSettings:
        return AppSettings(
            probability_threshold=float(self.threshold_spin.value()),
            model_name=self.model_name_edit.text().strip() or "ViT-B-32",
            pretrained=self.pretrained_edit.text().strip() or "laion2b_s34b_b79k",
            language=str(self.language_combo.currentData()),
        )
