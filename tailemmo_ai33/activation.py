from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
)

from .license import (
    LicenseError, format_remaining, get_machine_id, load_license,
    remaining_seconds, save_license, verify_license,
)


ROOT = Path(__file__).resolve().parent.parent


class ActivationDialog(QDialog):
    """Cổng bắt buộc trước khi mở bảng điều khiển."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.machine_id = get_machine_id()
        self.license_data = None
        self.setWindowTitle("TOOL CLONE GIỌNG TÀI LÊ MMO — Kích hoạt")
        self.setMinimumSize(690, 570)
        self.setModal(True)
        icon = next((p for p in (ROOT / "icon.ico", ROOT / "icon.png", ROOT / "icon.jpg") if p.exists()), None)
        if icon:
            self.setWindowIcon(QIcon(str(icon)))
        self._build_ui(icon)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._update_countdown)
        self.timer.start()
        self._load_existing()

    def _build_ui(self, icon):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(15)

        header = QHBoxLayout()
        if icon:
            logo = QLabel()
            logo.setPixmap(QPixmap(str(icon)).scaled(74, 74, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            logo.setFixedSize(80, 80)
            header.addWidget(logo)
        titles = QVBoxLayout()
        title = QLabel("KÍCH HOẠT TOOL CLONE GIỌNG TÀI LÊ MMO")
        title.setObjectName("activationTitle")
        subtitle = QLabel("Key được ràng buộc theo mã máy, thời hạn và chữ ký số.")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        outer.addLayout(header)

        machine_card = QFrame()
        machine_card.setObjectName("card")
        machine_layout = QVBoxLayout(machine_card)
        machine_title = QLabel("MÃ MÁY CỦA KHÁCH HÀNG")
        machine_title.setObjectName("section")
        machine_layout.addWidget(machine_title)
        machine_row = QHBoxLayout()
        self.machine_box = QLineEdit(self.machine_id)
        self.machine_box.setReadOnly(True)
        self.machine_box.setObjectName("machineCode")
        copy_machine = QPushButton("Sao chép mã máy")
        copy_machine.clicked.connect(lambda: QApplication.clipboard().setText(self.machine_id))
        machine_row.addWidget(self.machine_box, 1)
        machine_row.addWidget(copy_machine)
        machine_layout.addLayout(machine_row)
        outer.addWidget(machine_card)

        license_card = QFrame()
        license_card.setObjectName("card")
        license_layout = QVBoxLayout(license_card)
        license_title = QLabel("KEY KÍCH HOẠT CÓ CHỮ KÝ SỐ")
        license_title.setObjectName("section")
        license_layout.addWidget(license_title)
        self.key_edit = QTextEdit()
        self.key_edit.setPlaceholderText("Dán key TLV1... do chủ sở hữu cấp vào đây")
        self.key_edit.setMaximumHeight(105)
        license_layout.addWidget(self.key_edit)
        self.activate_button = QPushButton("KÍCH HOẠT KEY")
        self.activate_button.setObjectName("primary")
        self.activate_button.clicked.connect(self.activate_key)
        license_layout.addWidget(self.activate_button)
        outer.addWidget(license_card)

        status_card = QFrame()
        status_card.setObjectName("card")
        status_layout = QVBoxLayout(status_card)
        self.status_label = QLabel("Chưa kích hoạt")
        self.status_label.setObjectName("licenseError")
        self.status_label.setWordWrap(True)
        self.package_label = QLabel("Gói hiện tại: —")
        self.package_label.setObjectName("licensePackage")
        self.countdown_label = QLabel("Còn lại: —")
        self.countdown_label.setObjectName("licenseCountdown")
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.package_label)
        status_layout.addWidget(self.countdown_label)
        outer.addWidget(status_card)

        buttons = QHBoxLayout()
        exit_button = QPushButton("Thoát")
        exit_button.clicked.connect(self.reject)
        self.enter_button = QPushButton("VÀO BẢNG ĐIỀU KHIỂN")
        self.enter_button.setObjectName("primary")
        self.enter_button.setMinimumHeight(44)
        self.enter_button.setEnabled(False)
        self.enter_button.clicked.connect(self.accept)
        buttons.addWidget(exit_button)
        buttons.addStretch()
        buttons.addWidget(self.enter_button)
        outer.addLayout(buttons)

    def _load_existing(self):
        key = load_license()
        if not key:
            self.status_label.setText("Chưa có key kích hoạt trên máy này.")
            return
        try:
            self._set_valid(verify_license(key, machine_id=self.machine_id))
        except LicenseError as exc:
            self.license_data = None
            self.enter_button.setEnabled(False)
            self.status_label.setObjectName("licenseError")
            self.status_label.setText(str(exc))
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)

    def _set_valid(self, payload):
        self.license_data = payload
        customer = payload.get("customer") or "Khách hàng"
        package = payload.get("plan") or "Gói kích hoạt"
        duration_days = max(0, (int(payload["expires_at"]) - int(payload["issued_at"])) // 86400)
        expiry = datetime.fromtimestamp(int(payload["expires_at"])).strftime("%d/%m/%Y %H:%M:%S")
        self.status_label.setObjectName("licenseSuccess")
        self.status_label.setText(f"Đã kích hoạt hợp lệ cho: {customer}")
        self.package_label.setText(
            f"Gói hiện tại: {package}  •  Thời hạn key: {duration_days} ngày  •  Hết hạn: {expiry}"
        )
        self.enter_button.setEnabled(True)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self._update_countdown()

    def _update_countdown(self):
        if not self.license_data:
            return
        remaining = remaining_seconds(self.license_data)
        self.countdown_label.setText(f"THỜI GIAN CÒN LẠI: {format_remaining(remaining)}")
        if remaining <= 0:
            self.enter_button.setEnabled(False)
            self.status_label.setObjectName("licenseError")
            self.status_label.setText("Gói kích hoạt đã hết hạn.")

    def activate_key(self):
        key = "".join(self.key_edit.toPlainText().split())
        if not key:
            QMessageBox.warning(self, "Thiếu key", "Hãy dán key kích hoạt vào ô trống.")
            return
        try:
            payload = verify_license(key, machine_id=self.machine_id)
            save_license(key)
            self._set_valid(payload)
        except LicenseError as exc:
            self.license_data = None
            self.enter_button.setEnabled(False)
            QMessageBox.critical(self, "Kích hoạt thất bại", str(exc))
            return
        QMessageBox.information(
            self,
            "Kích hoạt thành công",
            f"Key đã được xác minh bằng chữ ký số.\n\n{self.countdown_label.text()}",
        )
        self.accept()
