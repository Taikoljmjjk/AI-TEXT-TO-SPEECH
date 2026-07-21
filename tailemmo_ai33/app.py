import os
import sys
import threading
import time
import math
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot, QTimer, QEvent, QSize, QRectF
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QPixmap, QPainter, QColor, QPainterPath, QPen, QTextCursor
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QSpinBox, QDoubleSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QScrollArea,
    QVBoxLayout, QWidget, QGroupBox, QAbstractItemView, QDialogButtonBox, QInputDialog, QMenu,
    QGridLayout
)

from .api import APIError, AI33Client
from .activation import ActivationDialog
from .license import LicenseError, format_remaining, load_license, remaining_seconds, verify_license
from .settings import DEFAULT_OUTPUT_DIR, load_settings, save_settings
from .srt import format_duration, is_timing_line, parse_srt
from .text_processing import PUNCTUATION, add_punctuation_pauses
from .audio import AudioNormalizationError, normalize_loudness


ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "TOOL CLONE GIỌNG TÀI LÊ MMO"


AI_TAG_GROUPS = {
    "Tốc độ": [
        ("Rất chậm", "[speed_very_slow]", "Đặt trước đoạn cần đọc rất chậm"),
        ("Chậm", "[speed_slow]", "Đặt trước đoạn cần đọc chậm"),
        ("Nhanh", "[speed_fast]", "Đặt trước đoạn cần đọc nhanh"),
        ("Rất nhanh", "[speed_very_fast]", "Đặt trước đoạn cần đọc rất nhanh"),
    ],
    "Ngắt nghỉ": [
        ("Ngắt nghỉ", "[pause]", "Tạo một nhịp nghỉ"),
        ("Ngắt nghỉ lâu", "[long_pause]", "Tạo một nhịp nghỉ dài"),
        ("Buộc ngắt nghỉ…", "__BREAK__", "Chèn thẻ break với số giây tự chọn"),
    ],
    "Hiệu ứng": [
        ("Ho", "[cough]", "Hiệu ứng ho"), ("Cười", "[laughter]", "Hiệu ứng cười"),
        ("Khóc", "[crying]", "Hiệu ứng khóc"), ("Hét", "[screaming]", "Hiệu ứng hét"),
        ("Ợ", "[burping]", "Hiệu ứng ợ"), ("Ngâm nga", "[humming]", "Hiệu ứng ngâm nga"),
        ("Thở dài", "[sigh]", "Hiệu ứng thở dài"), ("Sụt sịt", "[sniff]", "Hiệu ứng sụt sịt"),
        ("Hắt hơi", "[sneeze]", "Hiệu ứng hắt hơi"),
        ("Thì thầm", "[whispering]", "Đọc nhỏ bằng giọng thì thầm"),
        ("Nói lớn", "[shouting]", "Đọc lớn hoặc hô lớn"),
    ],
}


class WorkerSignals(QObject):
    status = Signal(int, str, str)
    row_progress = Signal(int, int, bool)
    progress = Signal(int)
    done = Signal(str)
    error = Signal(str)
    voices = Signal(list)
    voices_error = Signal(str, int)
    voice_created = Signal(str)
    dictionaries = Signal(list)
    dictionaries_error = Signal(str)


class KeyCheckSignals(QObject):
    checked = Signal(str, bool, str, str)


class Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    @Slot()
    def run(self):
        self.fn()


class DictionaryEditor(QDialog):
    """Soạn quy tắc ở dạng dễ nhập nhưng gửi JSON đúng hợp đồng AI33 v3."""
    def __init__(self, dictionary=None, parent=None):
        super().__init__(parent)
        dictionary = dictionary or {}
        self.setWindowTitle("Từ điển phát âm AI33")
        self.resize(620, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("TÊN TỪ ĐIỂN"))
        self.name = QLineEdit(str(dictionary.get("name") or ""))
        self.name.setPlaceholderText("Ví dụ: Thương hiệu và từ viết tắt")
        layout.addWidget(self.name)
        note = QLabel(
            "Mỗi dòng: từ gốc => cách đọc | kiểu khớp | phân biệt hoa thường\n"
            "Kiểu khớp: word hoặc contains. Cột cuối: true hoặc false. Hai cột sau có thể bỏ trống."
        )
        note.setWordWrap(True); note.setObjectName("muted")
        layout.addWidget(note)
        self.rules = QTextEdit()
        self.rules.setPlaceholderText("AI => Ây Ai | word | true\nvivoo => vi vu | contains | false")
        lines = []
        for rule in dictionary.get("rules") or []:
            lines.append(
                f"{rule.get('from', '')} => {rule.get('to', '')} | "
                f"{rule.get('matchType', 'word')} | {str(bool(rule.get('caseSensitive', False))).lower()}"
            )
        self.rules.setPlainText("\n".join(lines))
        layout.addWidget(self.rules, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Lưu lên AI33")
        buttons.button(QDialogButtonBox.Cancel).setText("Hủy")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        rules = []
        for number, raw in enumerate(self.rules.toPlainText().splitlines(), 1):
            raw = raw.strip()
            if not raw:
                continue
            pair, *options = [part.strip() for part in raw.split("|")]
            if "=>" not in pair:
                raise APIError(f"Dòng {number}: cần dùng dấu => giữa từ gốc và cách đọc.")
            source, target = [part.strip() for part in pair.split("=>", 1)]
            match_type = options[0].lower() if options and options[0] else "word"
            case_sensitive = len(options) > 1 and options[1].lower() in {"true", "1", "yes", "có"}
            rules.append({"from": source, "to": target, "matchType": match_type,
                          "caseSensitive": case_sensitive})
        return AI33Client._validate_dictionary(self.name.text(), rules)

    def accept(self):
        try:
            self.values()
        except APIError as exc:
            QMessageBox.warning(self, "Quy tắc chưa hợp lệ", str(exc)); return
        super().accept()


class DictionaryManager(QDialog):
    def __init__(self, client, dictionaries, changed, parent=None):
        super().__init__(parent)
        self.client = client
        self.changed = changed
        self.items = list(dictionaries)
        self.setWindowTitle("Quản lý từ điển phát âm AI33")
        self.resize(680, 430)
        layout = QVBoxLayout(self)
        note = QLabel("Dữ liệu được đọc và lưu trực tiếp qua API /v3/dictionaries của tài khoản đang chọn.")
        note.setWordWrap(True); note.setObjectName("muted")
        layout.addWidget(note)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        create = QPushButton("Tạo từ điển mới")
        edit = QPushButton("Sửa mục đã chọn")
        delete = QPushButton("Xóa mục đã chọn")
        close = QPushButton("Đóng")
        create.clicked.connect(self.create_item); edit.clicked.connect(self.edit_item)
        delete.clicked.connect(self.delete_item); close.clicked.connect(self.accept)
        row.addWidget(create); row.addWidget(edit); row.addWidget(delete); row.addStretch(); row.addWidget(close)
        layout.addLayout(row)
        self.populate()

    def populate(self):
        self.list.clear()
        for item in self.items:
            rules = item.get("rules") or []
            entry = QListWidgetItem(f"{item.get('name', 'Không tên')}  •  {len(rules)} quy tắc")
            entry.setData(Qt.UserRole, item.get("id"))
            self.list.addItem(entry)

    def selected_id(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _reload(self):
        self.items = self.client.dictionaries()
        self.populate(); self.changed()

    def create_item(self):
        editor = DictionaryEditor(parent=self)
        if editor.exec() != QDialog.Accepted: return
        try:
            name, rules = editor.values(); self.client.create_dictionary(name, rules); self._reload()
        except Exception as exc: QMessageBox.critical(self, "Không tạo được từ điển", str(exc))

    def edit_item(self):
        dictionary_id = self.selected_id()
        if dictionary_id is None:
            QMessageBox.information(self, "Chưa chọn", "Hãy chọn một từ điển cần sửa."); return
        try: data = self.client.dictionary(dictionary_id)
        except Exception as exc: QMessageBox.critical(self, "Không đọc được từ điển", str(exc)); return
        editor = DictionaryEditor(data, self)
        if editor.exec() != QDialog.Accepted: return
        try:
            name, rules = editor.values(); self.client.update_dictionary(dictionary_id, name, rules); self._reload()
        except Exception as exc: QMessageBox.critical(self, "Không cập nhật được từ điển", str(exc))

    def delete_item(self):
        dictionary_id = self.selected_id()
        if dictionary_id is None:
            QMessageBox.information(self, "Chưa chọn", "Hãy chọn một từ điển cần xóa."); return
        if QMessageBox.question(self, "Xác nhận xóa", "Xóa từ điển đã chọn khỏi tài khoản AI33?") != QMessageBox.Yes:
            return
        try: self.client.delete_dictionary(dictionary_id); self._reload()
        except Exception as exc: QMessageBox.critical(self, "Không xóa được từ điển", str(exc))


class BouncingLoader(QWidget):
    """Ba quả bóng nảy tuần tự, dùng cho phản hồi click và tác vụ nền."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = 0.0
        self.persistent = False
        self.timer = QTimer(self)
        self.timer.setInterval(35)
        self.timer.timeout.connect(self._tick)
        self.setFixedSize(76, 30)
        self.hide()

    def sizeHint(self):
        return QSize(76, 30)

    def _tick(self):
        self.phase += 0.18
        self.update()

    def start(self, persistent=False):
        self.persistent = persistent
        self.show()
        if not self.timer.isActive(): self.timer.start()

    def pulse(self):
        if self.persistent: return
        self.start(False)
        QTimer.singleShot(850, self.stop_if_temporary)

    def stop_if_temporary(self):
        if not self.persistent: self.stop()

    def stop(self):
        self.persistent = False
        self.timer.stop()
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        colors = (QColor("#48c8ff"), QColor("#f5b82e"), QColor("#7ae6c5"))
        baseline = 22
        for index, color in enumerate(colors):
            bounce = abs(math.sin(self.phase + index * 0.75)) * 13
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(9 + index * 23, int(baseline - bounce), 11, 11)


class CompactProgressBar(QWidget):
    """Thanh tiến trình ngang nhỏ gọn cho từng câu."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0
        self.failed = False
        self.setMinimumHeight(22)
        self.setMinimumWidth(0)

    def setProgress(self, value, failed=False):
        self.value = max(0, min(100, int(value)))
        self.failed = failed
        self.setToolTip(f"Tiến trình: {self.value}%")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        margin = 5.0
        bar_height = min(16.0, self.height() - 6.0)
        track = QRectF(margin, (self.height() - bar_height) / 2, max(1.0, self.width() - margin * 2), bar_height)
        painter.setPen(QPen(QColor("#38506a"), 1))
        painter.setBrush(QColor("#101c2d"))
        painter.drawRoundedRect(track, bar_height / 2, bar_height / 2)
        fill_width = track.width() * self.value / 100
        if fill_width > 0:
            fill = QRectF(track.x(), track.y(), max(bar_height, fill_width), track.height())
            fill.setWidth(min(fill.width(), track.width()))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#ef5b61") if self.failed else QColor("#25b83f"))
            painter.drawRoundedRect(fill, bar_height / 2, bar_height / 2)
        painter.setPen(QColor("#ffffff"))
        font = painter.font()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(track, Qt.AlignCenter, f"{self.value}%")


class KeyManager(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.check_signals = KeyCheckSignals()
        self.check_signals.checked.connect(self.key_checked)
        self.setWindowTitle("Quản lý khóa API")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        note = QLabel("Khóa API được mã hóa an toàn và chỉ tài khoản Windows này có thể giải mã.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        self.list = QListWidget()
        layout.addWidget(self.list)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Ví dụ: Tài khoản chính")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("Nhập khóa API của tài khoản")
        form.addRow("Tên gợi nhớ", self.name)
        form.addRow("Khóa API", self.key)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.save_button = QPushButton("Kiểm tra và lưu")
        delete = QPushButton("Xóa")
        close = QPushButton("Xong")
        self.save_button.clicked.connect(self.save_key)
        delete.clicked.connect(self.delete_key)
        close.clicked.connect(self.accept)
        row.addWidget(self.save_button)
        row.addWidget(delete)
        row.addStretch()
        row.addWidget(close)
        layout.addLayout(row)
        self.list.currentRowChanged.connect(self.select_key)
        self.refresh()

    def refresh(self):
        self.list.clear()
        for item in self.settings.get("api_keys", []):
            tail = item.get("key", "")[-5:]
            plan = item.get("plan", "Chưa kiểm tra gói")
            if "AI33" in str(plan).upper():
                plan = "Đã kết nối API"
            self.list.addItem(f"{item.get('name', 'Không tên')}  •••••{tail}  •  {plan}")

    def select_key(self, row):
        keys = self.settings.get("api_keys", [])
        if 0 <= row < len(keys):
            self.name.setText(keys[row].get("name", ""))
            self.key.setText(keys[row].get("key", ""))

    def save_key(self):
        name, key = self.name.text().strip(), self.key.text().strip()
        if not name or not key:
            QMessageBox.warning(self, "Thiếu thông tin", "Hãy nhập tên và khóa API.")
            return
        keys = self.settings.setdefault("api_keys", [])
        row = self.list.currentRow()
        previous = keys[row] if 0 <= row < len(keys) else {}
        item = {"name": name, "key": key, "plan": "Đang kiểm tra…",
                "api_enabled": previous.get("api_enabled")}
        if 0 <= row < len(keys):
            keys[row] = item
            target_row = row
        else:
            keys.append(item)
            target_row = len(keys) - 1
        save_settings(self.settings)
        self.refresh()
        self.list.setCurrentRow(target_row)
        self.save_button.setEnabled(False)
        self.save_button.setText("Đang kiểm tra gói…")

        def work():
            try:
                status = AI33Client(key).account_status()
                self.check_signals.checked.emit(key, status["api_enabled"], status["plan"], status["message"])
            except Exception as exc:
                self.check_signals.checked.emit(key, False, "Không xác định", str(exc))
        QThreadPool.globalInstance().start(Task(work))

    @Slot(str, bool, str, str)
    def key_checked(self, key, enabled, plan, message):
        for item in self.settings.get("api_keys", []):
            if item.get("key") == key:
                item["plan"] = plan
                item["api_enabled"] = enabled
                break
        save_settings(self.settings)
        self.refresh()
        self.save_button.setEnabled(True)
        self.save_button.setText("Kiểm tra và lưu")
        icon = QMessageBox.Information if enabled else QMessageBox.Warning
        box = QMessageBox(icon, "Kết quả kiểm tra tài khoản", f"Gói/quyền truy cập: {plan}\n\n{message}", QMessageBox.Ok, self)
        box.exec()

    def delete_key(self):
        row = self.list.currentRow()
        keys = self.settings.setdefault("api_keys", [])
        if 0 <= row < len(keys):
            keys.pop(row)
            save_settings(self.settings)
            self.refresh()
            self.name.clear()
            self.key.clear()


class MainWindow(QMainWindow):
    def __init__(self, license_data=None):
        super().__init__()
        self.license_data = license_data or {}
        self.license_expired_shown = False
        self.license_tick = 0
        self.settings = load_settings()
        self.signals = WorkerSignals()
        self.pool = QThreadPool.globalInstance()
        self.cancel_event = threading.Event()
        self.busy_depth = 0
        self.sentences = []
        self.row_progress_values = []
        self.voices_data = []
        self.input_format = "text"
        self.srt_cues = []
        self.output_dir = Path(self.settings.get("output_dir") or DEFAULT_OUTPUT_DIR)
        self.setWindowTitle(APP_NAME)
        self.resize(1540, 900)
        icon = next((p for p in (ROOT / "icon.ico", ROOT / "icon.png", ROOT / "icon.jpg") if p.exists()), None)
        if icon:
            self.setWindowIcon(QIcon(str(icon)))
        self._build_ui()
        self._connect_signals()
        self.refresh_keys()
        if self.current_key():
            self.load_dictionaries()
        self.license_timer = QTimer(self)
        self.license_timer.setInterval(1000)
        self.license_timer.timeout.connect(self.update_license_badge)
        self.license_timer.start()
        self.update_license_badge()

    def _build_ui(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(18, 16, 18, 14)
        header_card = QFrame()
        header_card.setObjectName("headerCard")
        header_card.setFixedHeight(82)
        top = QHBoxLayout(header_card)
        top.setContentsMargins(12, 7, 12, 7)
        top.setSpacing(10)
        icon_path = next((p for p in (ROOT / "icon.ico", ROOT / "icon.png", ROOT / "icon.jpg") if p.exists()), None)
        if icon_path:
            logo = QLabel()
            logo.setPixmap(QPixmap(str(icon_path)).scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            logo.setFixedSize(52, 52)
            top.addWidget(logo)
        identity = QVBoxLayout()
        identity.setContentsMargins(0, 0, 0, 0)
        identity.setSpacing(0)
        brand = QLabel("TÀI LÊ MMO")
        brand.setObjectName("brand")
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        subtitle = QLabel("PHÒNG THU GIỌNG NÓI CHUYÊN NGHIỆP")
        subtitle.setObjectName("headerSubtitle")
        contact = QLabel(
            'ZALO / ĐIỆN THOẠI: '
            '<a style="color:#59d1f6;text-decoration:none" href="https://zalo.me/0394342601">0394342601</a>'
            '&nbsp;&nbsp; • &nbsp;&nbsp;'
            '<a style="color:#59d1f6;text-decoration:none" href="https://zalo.me/g/52xnntcopiemcbk3fvvh">CỘNG ĐỒNG ĐAM MÊ CÔNG NGHỆ</a>'
        )
        contact.setObjectName("headerContact")
        contact.setTextFormat(Qt.RichText)
        contact.setTextInteractionFlags(Qt.TextBrowserInteraction)
        contact.setOpenExternalLinks(True)
        identity.addWidget(brand)
        identity.addWidget(title)
        identity.addWidget(subtitle)
        identity.addWidget(contact)
        top.addLayout(identity, 1)

        license_card = QFrame()
        license_card.setObjectName("licenseCard")
        license_card.setFixedSize(300, 58)
        license_layout = QVBoxLayout(license_card)
        license_layout.setContentsMargins(12, 3, 12, 3)
        license_layout.setSpacing(0)
        license_caption = QLabel("THỜI HẠN SỬ DỤNG")
        license_caption.setObjectName("licenseCaption")
        license_caption.setAlignment(Qt.AlignCenter)
        self.license_plan = QLabel()
        self.license_plan.setObjectName("licensePlan")
        self.license_plan.setAlignment(Qt.AlignCenter)
        self.license_countdown = QLabel()
        self.license_countdown.setObjectName("licenseCountdownHeader")
        self.license_countdown.setAlignment(Qt.AlignCenter)
        license_layout.addWidget(license_caption)
        license_layout.addWidget(self.license_plan)
        license_layout.addWidget(self.license_countdown)
        top.addWidget(license_card)
        outer.addWidget(header_card)

        toolbar_card = QFrame()
        toolbar_card.setObjectName("toolbarCard")
        toolbar_card.setFixedHeight(52)
        toolbar = QHBoxLayout(toolbar_card)
        toolbar.setContentsMargins(12, 6, 12, 6)
        toolbar.setSpacing(8)
        account_label = QLabel("TÀI KHOẢN LÀM VIỆC")
        account_label.setObjectName("toolbarLabel")
        toolbar.addWidget(account_label)
        self.api_combo = QComboBox()
        self.api_combo.setMinimumWidth(360)
        self.api_combo.setFixedHeight(36)
        self.api_combo.currentIndexChanged.connect(self.load_dictionaries)
        manage = QPushButton("Quản lý khóa API")
        manage.setFixedHeight(36)
        manage.clicked.connect(self.manage_keys)
        test = QPushButton("Kiểm tra kết nối")
        test.setFixedHeight(36)
        test.clicked.connect(self.load_voices)
        toolbar.addWidget(self.api_combo, 1)
        toolbar.addWidget(manage)
        toolbar.addWidget(test)
        outer.addWidget(toolbar_card)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._text_panel())
        splitter.addWidget(self._settings_panel())
        splitter.addWidget(self._status_panel())
        splitter.setSizes([500, 390, 650])
        outer.addWidget(splitter, 1)

        bottom = QVBoxLayout()
        status_row = QHBoxLayout()
        self.summary = QLabel("Sẵn sàng • 0 câu")
        self.summary.setObjectName("muted")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.progress.setMinimumWidth(380)
        status_row.addWidget(self.summary)
        status_row.addStretch()
        self.loader = BouncingLoader()
        self.busy_label = QLabel("Đang xử lý…")
        self.busy_label.setObjectName("busy")
        self.busy_label.hide()
        status_row.addWidget(self.loader)
        status_row.addWidget(self.busy_label)
        bottom.addLayout(status_row)
        overall_row = QHBoxLayout()
        overall_label = QLabel("TIẾN TRÌNH TỔNG THỂ")
        overall_label.setObjectName("overall")
        overall_row.addWidget(overall_label)
        overall_row.addWidget(self.progress, 1)
        bottom.addLayout(overall_row)
        outer.addLayout(bottom)
        self.setCentralWidget(central)

    def update_license_badge(self):
        self.license_tick += 1
        if self.license_tick % 60 == 0:
            try:
                self.license_data = verify_license(load_license())
            except LicenseError:
                self.license_data = dict(self.license_data, expires_at=0)
        remaining = remaining_seconds(self.license_data)
        plan = self.license_data.get("plan") or "Chưa kích hoạt"
        self.license_plan.setText(plan.upper())
        self.license_countdown.setText(f"CÒN LẠI  •  {format_remaining(remaining)}")
        if remaining <= 0 and not self.license_expired_shown:
            self.license_expired_shown = True
            self.license_timer.stop()
            self.centralWidget().setEnabled(False)
            QTimer.singleShot(0, self._license_expired)

    def _license_expired(self):
        QMessageBox.critical(
            self,
            "Gói kích hoạt đã hết hạn",
            "Quyền sử dụng đã hết hạn. Hãy liên hệ chủ sở hữu để cấp key mới.",
        )
        QApplication.quit()

    def play_click_effect(self):
        if self.busy_depth == 0:
            self.loader.pulse()

    def begin_busy(self, text="Đang xử lý…"):
        self.busy_depth += 1
        self.busy_label.setText(text)
        self.busy_label.show()
        self.loader.start(True)

    def end_busy(self):
        self.busy_depth = max(0, self.busy_depth - 1)
        if self.busy_depth == 0:
            self.loader.stop()
            self.busy_label.hide()

    def _card(self):
        frame = QFrame()
        frame.setObjectName("card")
        return frame

    def _text_panel(self):
        card = self._card()
        layout = QVBoxLayout(card)
        head = QLabel("01  NỘI DUNG ĐẦU VÀO")
        head.setObjectName("section")
        layout.addWidget(head)
        self.project_name = QLineEdit("clone_giong_tai_le_mmo")
        self.project_name.setPlaceholderText("Tên dự án / tên file")
        layout.addWidget(self.project_name)
        self.text = QTextEdit()
        self.text.setPlaceholderText("Dán văn bản hoặc nhập file SRT cần chuyển thành giọng nói tại đây…")
        self.text.textChanged.connect(self.update_counter)
        layout.addWidget(self.text, 1)
        self.counter = QLabel("0 ký tự • 0 từ")
        self.counter.setObjectName("muted")
        layout.addWidget(self.counter)
        self.input_badge = QLabel()
        self.input_badge.setObjectName("tagNote")
        self.input_badge.setWordWrap(True)
        self.input_badge.hide()
        layout.addWidget(self.input_badge)
        row = QHBoxLayout()
        import_btn = QPushButton("Nhập TXT")
        import_btn.clicked.connect(self.import_text)
        import_srt_btn = QPushButton("Nhập SRT")
        import_srt_btn.setToolTip("Giữ nguyên số thứ tự, mốc thời gian và lời thoại để AI33 tạo audio theo SRT")
        import_srt_btn.clicked.connect(self.import_srt)
        clear = QPushButton("Xóa nội dung")
        clear.clicked.connect(self.clear_text)
        row.addWidget(import_btn)
        row.addWidget(import_srt_btn)
        row.addWidget(clear)
        layout.addLayout(row)
        return card

    def _settings_panel(self):
        card = self._card()
        layout = QVBoxLayout(card)
        head = QLabel("02  GIỌNG & CÀI ĐẶT")
        head.setObjectName("section")
        layout.addWidget(head)
        tabs = QTabWidget()
        tabs.addTab(self._scroll_tab(self._tts_tab()), "Tạo giọng nói")
        tabs.addTab(self._scroll_tab(self._clone_tab()), "Nhân bản giọng")
        layout.addWidget(tabs, 1)
        self.start_btn = QPushButton("BẮT ĐẦU TẠO ÂM THANH")
        self.start_btn.setObjectName("primary")
        self.start_btn.setMinimumHeight(46)
        self.start_btn.clicked.connect(self.start_processing)
        self.stop_btn = QPushButton("Dừng sau tác vụ hiện tại")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_processing)
        layout.addWidget(self.start_btn)
        layout.addWidget(self.stop_btn)
        return card

    def _scroll_tab(self, page):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    def _tts_tab(self):
        page = QWidget()
        form = QFormLayout(page)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(10)
        self.clone_voice_combo = QComboBox()
        self.clone_voice_combo.setMinimumContentsLength(20)
        self.clone_voice_combo.currentIndexChanged.connect(
            lambda index: self.voice_selection_changed("clone", index)
        )
        self.library_voice_combo = QComboBox()
        self.library_voice_combo.setMinimumContentsLength(20)
        self.library_voice_combo.currentIndexChanged.connect(
            lambda index: self.voice_selection_changed("library", index)
        )
        reload_btn = QPushButton("Làm mới thư viện giọng")
        reload_btn.clicked.connect(self.load_voices)
        form.addRow("Giọng đã clone", self.clone_voice_combo)
        form.addRow("Thư viện giọng có sẵn", self.library_voice_combo)
        self.voice_search = QLineEdit()
        self.voice_search.setPlaceholderText("Tìm trong cả giọng clone và thư viện có sẵn…")
        self.voice_search.returnPressed.connect(self.load_voices)
        form.addRow("Tìm trong thư viện", self.voice_search)
        form.addRow("", reload_btn)
        self.model_info = QLabel()
        self.model_info.setWordWrap(True)
        self.model_info.setObjectName("muted")
        form.addRow("", self.model_info)
        self.credit_estimate = QLabel("0 / 1.000.000 ký tự")
        self.credit_estimate.setObjectName("credit")
        form.addRow("", self.credit_estimate)
        dictionary_row = QWidget()
        dictionary_layout = QHBoxLayout(dictionary_row)
        dictionary_layout.setContentsMargins(0, 0, 0, 0)
        self.dictionary_combo = QComboBox()
        self.dictionary_combo.addItem("Không dùng từ điển", None)
        self.dictionary_combo.currentIndexChanged.connect(self.dictionary_option_changed)
        manage_dictionary = QPushButton("Quản lý")
        manage_dictionary.setToolTip("Tạo, sửa và xóa từ điển trực tiếp trên tài khoản AI33")
        manage_dictionary.clicked.connect(self.manage_dictionaries)
        dictionary_layout.addWidget(self.dictionary_combo, 1)
        dictionary_layout.addWidget(manage_dictionary)
        form.addRow("Từ điển phát âm", dictionary_row)

        tag_box = QWidget()
        tag_layout = QVBoxLayout(tag_box)
        tag_layout.setContentsMargins(0, 0, 0, 0)
        tag_layout.setSpacing(6)
        self.tag_note = QLabel(
            "Thẻ được chèn thật vào nội dung gửi AI33. Hãy đặt con trỏ trước đoạn cần áp dụng; "
            "khả năng thể hiện phụ thuộc giọng/model đã chọn."
        )
        self.tag_note.setWordWrap(True); self.tag_note.setObjectName("tagNote")
        tag_layout.addWidget(self.tag_note)
        tag_buttons = QHBoxLayout()
        tag_buttons.setSpacing(5)
        self.tag_buttons = {}
        for group_name, entries in AI_TAG_GROUPS.items():
            button = QPushButton(group_name)
            button.setObjectName("tagButton")
            menu = QMenu(button)
            for label, tag, description in entries:
                action = QAction(f"{label}    {tag if tag != '__BREAK__' else '<break time=\"s\" />'}", menu)
                action.setToolTip(description)
                action.triggered.connect(
                    lambda checked=False, value=tag, group=group_name, choice=label:
                    self.insert_ai_tag(value, group, choice)
                )
                menu.addAction(action)
            button.setMenu(menu)
            tag_buttons.addWidget(button)
            self.tag_buttons[group_name] = button
        tag_layout.addLayout(tag_buttons)

        self.loudness_switch = QCheckBox()
        self.loudness_switch.setObjectName("pauseSwitch")
        self.loudness_switch.setChecked(bool(self.settings.get("normalize_loudness", True)))
        self.loudness_switch.setToolTip(
            "Cân bằng âm lượng toàn bộ MP3 sau khi AI33 tạo xong, giúp hạn chế câu đột ngột quá to hoặc quá nhỏ."
        )
        self.loudness_switch.toggled.connect(self.loudness_option_changed)
        self.loudness_option_changed(self.loudness_switch.isChecked(), save=False)
        tag_layout.addWidget(self.loudness_switch)

        self.auto_pause_switch = QCheckBox()
        self.auto_pause_switch.setObjectName("pauseSwitch")
        self.auto_pause_switch.setChecked(bool(self.settings.get("auto_pause_enabled", False)))
        self.auto_pause_switch.toggled.connect(self.auto_pause_option_changed)
        tag_layout.addWidget(self.auto_pause_switch)

        pause_options = QWidget()
        pause_grid = QGridLayout(pause_options)
        pause_grid.setContentsMargins(0, 0, 0, 0)
        pause_grid.setHorizontalSpacing(8)
        pause_grid.setVerticalSpacing(5)
        saved_punctuation = set(self.settings.get("pause_punctuation") or PUNCTUATION)
        punctuation_labels = {".": "Chấm .", ",": "Phẩy ,", ";": "Chấm phẩy ;", ":": "Hai chấm :", "?": "Hỏi ?", "!": "Cảm thán !"}
        self.pause_checks = {}
        for index, mark in enumerate(PUNCTUATION):
            checkbox = QCheckBox(punctuation_labels[mark])
            checkbox.setChecked(mark in saved_punctuation)
            checkbox.toggled.connect(self.pause_settings_changed)
            self.pause_checks[mark] = checkbox
            pause_grid.addWidget(checkbox, index // 3, index % 3)
        self.short_pause = QDoubleSpinBox()
        self.short_pause.setRange(0.1, 5.0); self.short_pause.setSingleStep(0.05); self.short_pause.setDecimals(2)
        self.short_pause.setValue(float(self.settings.get("short_pause_seconds", 0.35)))
        self.short_pause.setSuffix(" giây")
        self.short_pause.valueChanged.connect(self.pause_settings_changed)
        self.long_pause = QDoubleSpinBox()
        self.long_pause.setRange(0.1, 10.0); self.long_pause.setSingleStep(0.1); self.long_pause.setDecimals(2)
        self.long_pause.setValue(float(self.settings.get("long_pause_seconds", 0.70)))
        self.long_pause.setSuffix(" giây")
        self.long_pause.valueChanged.connect(self.pause_settings_changed)
        pause_grid.addWidget(QLabel("Nghỉ ngắn (, ; :)"), 2, 0)
        pause_grid.addWidget(self.short_pause, 2, 1)
        pause_grid.addWidget(QLabel("Nghỉ dài (. ? !)"), 3, 0)
        pause_grid.addWidget(self.long_pause, 3, 1)
        self.pause_options = pause_options
        tag_layout.addWidget(pause_options)
        self.auto_pause_option_changed(self.auto_pause_switch.isChecked(), save=False)
        form.addRow("Thẻ điều khiển AI", tag_box)
        self.subtitle_switch = QCheckBox("Xuất kèm phụ đề SRT")
        self.subtitle_switch.setObjectName("subtitleSwitch")
        self.subtitle_switch.setChecked(bool(self.settings.get("with_transcript", False)))
        self.subtitle_switch.setText(
            "Đang bật • xuất kèm phụ đề SRT" if self.subtitle_switch.isChecked()
            else "Đang tắt • chỉ xuất file âm thanh"
        )
        self.subtitle_switch.setToolTip("Bật để tạo và tải file phụ đề .srt cùng file âm thanh")
        self.subtitle_switch.toggled.connect(self.subtitle_option_changed)
        form.addRow("Phụ đề", self.subtitle_switch)
        self.retries = QSpinBox()
        self.retries.setRange(0, 5)
        self.retries.setValue(3)
        self.retries.setSuffix(" lần")
        self.retries.setToolTip("Tạo lại riêng câu bị lỗi, không tạo lại các câu đã hoàn thành")
        form.addRow("Tự thử lại khi lỗi", self.retries)
        self.callback = QLineEdit()
        self.callback.setPlaceholderText("Không bắt buộc — để trống để app tự polling")
        self.callback.setToolTip("Không bắt buộc; dùng khi cần nhận kết quả qua webhook")
        form.addRow("Webhook nhận kết quả", self.callback)
        out_row = QWidget()
        out_lay = QHBoxLayout(out_row)
        out_lay.setContentsMargins(0, 0, 0, 0)
        self.out_label = QLineEdit(str(self.output_dir))
        self.out_label.setReadOnly(True)
        browse = QPushButton("…")
        browse.clicked.connect(self.choose_output)
        out_lay.addWidget(self.out_label)
        out_lay.addWidget(browse)
        form.addRow("Thư mục lưu", out_row)
        page.setMinimumHeight(form.sizeHint().height() + 24)
        page.setMinimumWidth(360)
        self.update_model_rules()
        self.update_tag_support()
        return page

    def _clone_tab(self):
        page = QWidget()
        form = QFormLayout(page)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(10)
        self.clone_name = QLineEdit()
        self.clone_name.setPlaceholderText("Tên giọng mới")
        self.sample_path = QLineEdit()
        self.sample_path.setReadOnly(True)
        choose = QPushButton("Chọn WAV / MP3 mẫu")
        choose.clicked.connect(self.choose_sample)
        self.sample_info = QLabel("Chưa chọn file mẫu")
        self.sample_info.setObjectName("muted")
        self.sample_info.setWordWrap(True)
        form.addRow("Tên giọng", self.clone_name)
        form.addRow("File mẫu (tối đa 10 MB)", self.sample_path)
        form.addRow("", choose)
        form.addRow("Thông tin mẫu", self.sample_info)
        clone = QPushButton("CLONE GIỌNG MỚI")
        clone.setObjectName("primary")
        clone.clicked.connect(self.clone_voice)
        form.addRow("", clone)
        note = QLabel("Mẹo: dùng đoạn thu sạch, chỉ một người nói, không nhạc nền. API chấp nhận file tối đa 10 MB.")
        note.setWordWrap(True); note.setObjectName("muted")
        form.addRow(note)
        page.setMinimumHeight(form.sizeHint().height() + 24)
        page.setMinimumWidth(360)
        return page

    def _status_panel(self):
        card = self._card()
        layout = QVBoxLayout(card)
        row = QHBoxLayout()
        head = QLabel("03  TRẠNG THÁI TẠO AUDIO")
        head.setObjectName("section")
        open_btn = QPushButton("Mở thư mục")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir))))
        row.addWidget(head); row.addStretch(); row.addWidget(open_btn)
        layout.addLayout(row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["#", "Nội dung", "Tiến trình", "Trạng thái", "Mã âm thanh"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(42)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        for column in range(1, 5):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
        header.resizeSection(0, 46)
        header.resizeSection(1, 300)
        header.resizeSection(2, 150)
        header.resizeSection(3, 125)
        header.resizeSection(4, 115)
        layout.addWidget(self.table, 1)
        return card

    def _connect_signals(self):
        self.signals.status.connect(self.set_row_status)
        self.signals.row_progress.connect(self.set_row_progress)
        self.signals.progress.connect(self.progress.setValue)
        self.signals.done.connect(self.processing_done)
        self.signals.error.connect(self.show_error)
        self.signals.voices.connect(self.populate_voices)
        self.signals.voices_error.connect(self.voice_load_failed)
        self.signals.voice_created.connect(self.voice_created)
        self.signals.dictionaries.connect(self.populate_dictionaries)
        self.signals.dictionaries_error.connect(self.dictionary_load_failed)

    def current_key(self):
        keys = self.settings.get("api_keys", [])
        idx = self.api_combo.currentIndex()
        return keys[idx].get("key", "") if 0 <= idx < len(keys) else ""

    def refresh_keys(self):
        old_index = self.api_combo.currentIndex()
        self.api_combo.clear()
        for item in self.settings.get("api_keys", []):
            plan = item.get("plan", "Chưa kiểm tra gói")
            if "AI33" in str(plan).upper():
                plan = "Đã kết nối API"
            self.api_combo.addItem(f"{item.get('name', 'Khóa API')}  •  {plan}")
        if self.api_combo.count():
            self.api_combo.setCurrentIndex(min(max(old_index, 0), self.api_combo.count() - 1))
        else:
            self.api_combo.addItem("Chưa có tài khoản — hãy chọn Quản lý khóa API")
            self.api_combo.setEnabled(False)
            return
        self.api_combo.setEnabled(True)

    def manage_keys(self):
        KeyManager(self.settings, self).exec()
        self.settings = load_settings()
        self.refresh_keys()

    def client(self):
        key = self.current_key()
        if not key:
            raise APIError("Chưa có khóa API. Hãy mở Quản lý khóa API để lưu khóa.")
        return AI33Client(key)

    def update_counter(self):
        text = self.text.toPlainText()
        self.counter.setText(f"{len(text):,} ký tự • {len(text.split()):,} từ")
        self._update_input_format(text)
        self.update_credit_estimate()

    def _update_input_format(self, text):
        if "-->" not in text:
            self.input_format = "text"
            self.srt_cues = []
            if hasattr(self, "input_badge"):
                self.input_badge.hide()
            return
        try:
            cues = parse_srt(text)
        except ValueError as exc:
            self.input_format = "invalid_srt"
            self.srt_cues = []
            if hasattr(self, "input_badge"):
                self.input_badge.setText(f"SRT chưa hợp lệ • {exc}")
                self.input_badge.show()
            return
        self.input_format = "srt"
        self.srt_cues = cues
        duration = max(cue["end_ms"] for cue in cues)
        if hasattr(self, "input_badge"):
            self.input_badge.setText(
                f"ĐẦU VÀO SRT • {len(cues):,} đoạn • {format_duration(duration)} • "
                "giữ nguyên mốc thời gian • AI33 tính hệ số đầu vào ×1,2"
            )
            self.input_badge.show()

    def update_model_rules(self):
        if hasattr(self, "model_info"):
            self.model_info.setText("Thư viện giọng đa nguồn • hỗ trợ giọng đã clone và giọng đọc có sẵn.")
        self.update_credit_estimate()

    def update_tag_support(self):
        if not hasattr(self, "tag_note"):
            return
        provider = self.selected_voice_provider()
        if provider == "elevenlabs":
            suffix = "Giọng ElevenLabs thường hỗ trợ đầy đủ nhất các thẻ biểu cảm."
        elif provider == "clone":
            suffix = "Đang chọn giọng đã clone; mức hỗ trợ thẻ phụ thuộc model tạo giọng."
        else:
            suffix = "Nhà cung cấp hiện tại có thể bỏ qua hoặc đọc thành tiếng một số thẻ thử nghiệm."
        self.tag_note.setText(
            "Thẻ được chèn thật vào nội dung gửi AI33. Hãy đặt con trỏ trước đoạn cần áp dụng. " + suffix
        )

    def insert_ai_tag(self, tag, group_name=None, choice_label=None):
        cursor = self.text.textCursor()
        if self.input_format in {"srt", "invalid_srt"}:
            line = cursor.block().text().strip()
            if line.isdigit() or is_timing_line(line):
                QMessageBox.warning(
                    self, "Vị trí chèn chưa đúng",
                    "Hãy đặt con trỏ trong dòng lời thoại SRT, không đặt ở số thứ tự hoặc mốc thời gian.",
                )
                return
        if tag == "__BREAK__":
            seconds, ok = QInputDialog.getDouble(
                self, "Buộc ngắt nghỉ", "Số giây nghỉ (0,1–10):", 1.0, 0.1, 10.0, 1
            )
            if not ok:
                return
            value = f"<break time=\"{seconds:g}s\" />"
        else:
            value = tag
        selected = cursor.selectedText()
        if selected:
            cursor.insertText(f"{value} {selected}")
        else:
            before = cursor.position() > 0 and not self.text.toPlainText()[cursor.position() - 1].isspace()
            cursor.insertText((" " if before else "") + value + " ")
        self.text.setTextCursor(cursor)
        self.text.setFocus()
        if group_name in self.tag_buttons and choice_label:
            self.tag_buttons[group_name].setText(f"{group_name} • {choice_label}")
            self.tag_buttons[group_name].setToolTip(f"Lựa chọn hiện tại: {choice_label} — {value}")

    def load_dictionaries(self):
        if not hasattr(self, "dictionary_combo") or not self.current_key():
            return
        def work():
            try: self.signals.dictionaries.emit(self.client().dictionaries())
            except Exception as exc: self.signals.dictionaries_error.emit(str(exc))
        self.pool.start(Task(work))

    @Slot(list)
    def populate_dictionaries(self, dictionaries):
        saved = self.settings.get("pronunciation_dictionary_id")
        self.dictionary_combo.blockSignals(True)
        self.dictionary_combo.clear()
        self.dictionary_combo.addItem("Không dùng từ điển", None)
        selected_index = 0
        for item in dictionaries:
            dictionary_id = item.get("id")
            self.dictionary_combo.addItem(
                f"{item.get('name', 'Không tên')}  •  {len(item.get('rules') or [])} quy tắc",
                dictionary_id,
            )
            if saved is not None and str(dictionary_id) == str(saved):
                selected_index = self.dictionary_combo.count() - 1
        self.dictionary_combo.setCurrentIndex(selected_index)
        self.dictionary_combo.blockSignals(False)

    @Slot(str)
    def dictionary_load_failed(self, message):
        self.dictionary_combo.setToolTip("Không tải được danh sách từ điển: " + message)

    def dictionary_option_changed(self):
        value = self.dictionary_combo.currentData()
        self.settings["pronunciation_dictionary_id"] = value
        save_settings(self.settings)

    def manage_dictionaries(self):
        if not self.current_key():
            QMessageBox.warning(self, "Chưa có khóa API", "Hãy thêm và chọn khóa API trước."); return
        dictionaries = []
        for index in range(1, self.dictionary_combo.count()):
            dictionaries.append({
                "id": self.dictionary_combo.itemData(index),
                "name": self.dictionary_combo.itemText(index).split("  •  ", 1)[0],
                "rules": [],
            })
        try:
            dictionaries = self.client().dictionaries()
            DictionaryManager(self.client(), dictionaries, self.load_dictionaries, self).exec()
        except Exception as exc:
            QMessageBox.critical(self, "Không mở được từ điển", str(exc))

    def subtitle_option_changed(self, checked):
        self.subtitle_switch.setText(
            "Đang bật • xuất kèm phụ đề SRT" if checked else "Đang tắt • chỉ xuất file âm thanh"
        )
        self.settings["with_transcript"] = bool(checked)
        save_settings(self.settings)

    def auto_pause_option_changed(self, checked, save=True):
        self.auto_pause_switch.setText(
            "Đang bật • tự thêm ngắt nghỉ theo dấu câu"
            if checked else "Đang tắt • không tự thêm ngắt nghỉ"
        )
        self.pause_options.setEnabled(bool(checked))
        if save:
            self.pause_settings_changed()

    def loudness_option_changed(self, checked, save=True):
        self.loudness_switch.setText(
            "Đang bật • ổn định âm lượng toàn bộ file"
            if checked else "Đang tắt • giữ nguyên âm lượng từ AI33"
        )
        if save:
            self.settings["normalize_loudness"] = bool(checked)
            save_settings(self.settings)

    def pause_settings_changed(self, *_):
        self.settings["auto_pause_enabled"] = self.auto_pause_switch.isChecked()
        self.settings["pause_punctuation"] = [mark for mark, box in self.pause_checks.items() if box.isChecked()]
        self.settings["short_pause_seconds"] = float(self.short_pause.value())
        self.settings["long_pause_seconds"] = float(self.long_pause.value())
        save_settings(self.settings)

    def update_credit_estimate(self):
        if not hasattr(self, "credit_estimate"):
            return
        characters = len(self.text.toPlainText()) if hasattr(self, "text") else 0
        self.credit_estimate.setText(f"{characters:,} / 1.000.000 ký tự tối đa mỗi yêu cầu")

    def import_text(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn văn bản", "", "Văn bản (*.txt);;Tất cả (*.*)")
        if path:
            try:
                self.text.setPlainText(Path(path).read_text(encoding="utf-8-sig"))
            except Exception as exc:
                QMessageBox.critical(self, "Không đọc được file", str(exc))

    def import_srt(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn phụ đề SRT", "", "Phụ đề SubRip (*.srt)")
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8-sig")
            cues = parse_srt(content)
        except (OSError, UnicodeError, ValueError) as exc:
            QMessageBox.critical(self, "File SRT chưa hợp lệ", str(exc))
            return
        self.text.setPlainText(content)
        self.input_format = "srt"
        self.srt_cues = cues
        if self.subtitle_switch.isChecked():
            self.subtitle_switch.setChecked(False)
        if not self.project_name.text().strip() or self.project_name.text() == "clone_giong_tai_le_mmo":
            self.project_name.setText(Path(path).stem)
        QMessageBox.information(
            self, "Đã nhập SRT",
            f"Đã nhận {len(cues):,} đoạn, thời lượng {format_duration(max(c['end_ms'] for c in cues))}.\n"
            "Mốc thời gian và nội dung sẽ được gửi nguyên vẹn lên AI33.",
        )

    def clear_text(self):
        self.text.clear(); self.input_format = "text"; self.srt_cues = []
        self.sentences = []; self.table.setRowCount(0); self.progress.setValue(0)

    def prepare_full_text(self):
        source_text = self.text.toPlainText()
        if "-->" in source_text:
            self.srt_cues = parse_srt(source_text)
            self.input_format = "srt"
        processed_text = source_text
        auto_pause_applied = False
        if source_text.strip() and self.input_format != "srt" and self.auto_pause_switch.isChecked():
            selected = [mark for mark, box in self.pause_checks.items() if box.isChecked()]
            processed_text = add_punctuation_pauses(
                source_text, selected, self.short_pause.value(), self.long_pause.value()
            )
            auto_pause_applied = processed_text != source_text
        # Gửi một request TTS liền mạch; bản gốc trên giao diện không bị sửa.
        self.sentences = [processed_text] if source_text.strip() else []
        self.row_progress_values = [0] * len(self.sentences)
        self.table.setRowCount(len(self.sentences))
        for i, sentence in enumerate(self.sentences):
            self.table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            content_item = QTableWidgetItem(sentence)
            content_item.setToolTip(sentence)
            self.table.setItem(i, 1, content_item)
            self.table.setCellWidget(i, 2, CompactProgressBar())
            self.table.setItem(i, 3, QTableWidgetItem("Sẵn sàng"))
            self.table.setItem(i, 4, QTableWidgetItem("—"))
            self.table.setRowHeight(i, 40)
        if self.sentences and self.input_format == "srt":
            duration = max(cue["end_ms"] for cue in self.srt_cues)
            self.summary.setText(
                f"Tạo từ SRT • {len(self.srt_cues):,} đoạn • {format_duration(duration)} • giữ nguyên mốc thời gian"
            )
        else:
            self.summary.setText(
            f"Tạo liền mạch • 1 tác vụ • {len(source_text):,} ký tự • "
            + ("đã tự thêm ngắt nghỉ theo dấu câu" if auto_pause_applied else "giữ nguyên nội dung")
            if self.sentences else "Tạo liền mạch • chưa có nội dung"
            )

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu", str(self.output_dir))
        if path:
            self.output_dir = Path(path); self.out_label.setText(path)
            self.settings["output_dir"] = path; save_settings(self.settings)

    def choose_sample(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn file giọng mẫu", "", "Âm thanh (*.wav *.mp3)")
        if path:
            try:
                info = AI33Client.inspect_audio_sample(path)
            except APIError as exc:
                self.sample_path.clear()
                self.sample_info.setText("File không hợp lệ")
                QMessageBox.warning(self, "File mẫu không hợp lệ", str(exc))
                return
            self.sample_path.setText(path)
            duration = f" • {info['duration']:.1f} giây" if info.get("duration") is not None else ""
            channels = f" • {info.get('channels')} kênh • {info.get('sample_rate'):,} Hz" if info.get("channels") else ""
            self.sample_info.setText(
                f"{info['format']} • {info['size'] / 1024 / 1024:.2f} MB{duration}{channels}"
            )

    def load_voices(self):
        if not self.current_key():
            return
        self.begin_busy("Đang tải thư viện giọng…")
        search = self.voice_search.text() if hasattr(self, "voice_search") else ""
        self.summary.setText("Đang tải giọng đã clone và thư viện giọng có sẵn…")
        def work():
            try:
                client = self.client()
                voices = client.all_voices(search=search)
                self.signals.voices.emit(voices)
            except APIError as exc: self.signals.voices_error.emit(str(exc), exc.status_code or 0)
            except Exception as exc: self.signals.voices_error.emit(str(exc), 0)
        self.pool.start(Task(work))

    @Slot(list)
    def populate_voices(self, voices):
        self.end_busy()
        self.voices_data = voices
        self.clone_voice_combo.blockSignals(True)
        self.library_voice_combo.blockSignals(True)
        self.clone_voice_combo.clear()
        self.library_voice_combo.clear()
        self.clone_voice_combo.addItem("— Chọn một giọng đã clone —", None)
        self.library_voice_combo.addItem("— Chọn một giọng có sẵn —", None)
        if not voices:
            self.clone_voice_combo.setItemText(0, "Tài khoản chưa có giọng clone")
            self.library_voice_combo.setItemText(0, "Thư viện giọng có sẵn đang trống")
            self.clone_voice_combo.blockSignals(False)
            self.library_voice_combo.blockSignals(False)
            self.summary.setText("Đã kết nối • thư viện giọng của tài khoản đang trống")
            return
        for voice in voices:
            details = [
                voice.get("language"), voice.get("gender"), voice.get("accent"),
            ]
            details = [str(value) for value in details if value]
            suffix = " • ".join(details)
            voice_id = voice.get("voice_id") or voice.get("id")
            source = voice.get("_provider") or ""
            source_label = source or "AI33"
            label = f"{voice.get('name', 'Không tên')}  •  {voice_id}"
            if source != "clone":
                label = f"[{source_label}] {label}"
            if suffix: label += f"  •  {suffix}"
            combo = self.clone_voice_combo if source == "clone" else self.library_voice_combo
            combo.addItem(label, voice_id)
            combo.setItemData(combo.count() - 1, source, Qt.UserRole + 1)
            combo.setItemData(combo.count() - 1,
                f"Tên: {voice.get('name', 'Không tên')}\nMã giọng: {voice_id}\nThông tin: {suffix or 'Không có'}",
                Qt.ToolTipRole)
        clone_count = sum(1 for voice in voices if voice.get("_provider") == "clone")
        library_count = len(voices) - clone_count
        if clone_count:
            self.clone_voice_combo.setCurrentIndex(1)
            self.library_voice_combo.setCurrentIndex(0)
        elif library_count:
            self.library_voice_combo.setCurrentIndex(1)
        self.clone_voice_combo.blockSignals(False)
        self.library_voice_combo.blockSignals(False)
        self.update_tag_support()
        self.summary.setText(f"Đã kết nối • {len(voices)} giọng • gồm {clone_count} giọng đã clone")

    def voice_selection_changed(self, source, index):
        if index > 0:
            other = self.library_voice_combo if source == "clone" else self.clone_voice_combo
            other.blockSignals(True)
            other.setCurrentIndex(0)
            other.blockSignals(False)
        self.update_tag_support()

    def selected_voice_id(self):
        clone_id = self.clone_voice_combo.currentData() if hasattr(self, "clone_voice_combo") else None
        library_id = self.library_voice_combo.currentData() if hasattr(self, "library_voice_combo") else None
        return clone_id or library_id

    def selected_voice_provider(self):
        if hasattr(self, "clone_voice_combo") and self.clone_voice_combo.currentData():
            return "clone"
        if hasattr(self, "library_voice_combo") and self.library_voice_combo.currentData():
            return self.library_voice_combo.currentData(Qt.UserRole + 1) or ""
        return ""

    @Slot(str, int)
    def voice_load_failed(self, message, status_code):
        self.end_busy()
        # Giữ danh sách đang hiển thị nếu trước đó đã tải thành công.
        if not self.voices_data:
            self.clone_voice_combo.clear()
            self.library_voice_combo.clear()
            if status_code == 403:
                message_text = "Gói hiện tại chưa được cấp quyền dùng API"
            else:
                message_text = "Không tải được thư viện giọng"
            self.clone_voice_combo.addItem(message_text, None)
            self.library_voice_combo.addItem(message_text, None)
        if status_code == 403:
            idx = self.api_combo.currentIndex()
            keys = self.settings.get("api_keys", [])
            if 0 <= idx < len(keys):
                keys[idx]["plan"] = "FREE — API công khai đang bị khóa"
                keys[idx]["api_enabled"] = False
                save_settings(self.settings)
                self.refresh_keys()
            self.summary.setText("Tài khoản hiện tại chưa được cấp quyền sử dụng API")
            QMessageBox.warning(self, "Gói tài khoản chưa hỗ trợ API",
                "Khóa API đã được nhận diện, nhưng tài khoản hiện tại chưa có gói trả phí.\n\n"
                "Hệ thống không trả về giọng nào cho tài khoản này. Sau khi thay đổi gói, "
                "hãy nhấn Làm mới thư viện giọng.")
        else:
            self.show_error(message)

    def clone_voice(self):
        name, path = self.clone_name.text().strip(), self.sample_path.text().strip()
        if not name or not path:
            QMessageBox.warning(self, "Thiếu thông tin", "Hãy nhập tên và chọn file mẫu."); return
        self.begin_busy("Đang tải và nhân bản giọng…")
        def work():
            try:
                client = self.client()
                self.signals.status.emit(-1, "Đang tải file mẫu…", "")
                voice_id = client.clone_voice(name=name, audio_file=path)
                self.signals.voice_created.emit(str(voice_id))
            except Exception as exc: self.signals.error.emit(str(exc))
        self.pool.start(Task(work))

    @Slot(str)
    def voice_created(self, voice_id):
        self.end_busy()
        QMessageBox.information(self, "Đã nhân bản giọng", f"Đăng ký thành công. Mã giọng: {voice_id}")
        self.load_voices()

    def start_processing(self):
        # Luôn dựng lại danh sách từ nội dung hiện tại để không dùng dữ liệu cũ sau khi sửa văn bản.
        try:
            self.prepare_full_text()
        except ValueError as exc:
            QMessageBox.warning(self, "SRT chưa hợp lệ", str(exc)); return
        if not self.sentences:
            QMessageBox.warning(self, "Chưa có nội dung", "Hãy nhập văn bản cần xử lý."); return
        if self.selected_voice_id() is None:
            QMessageBox.warning(self, "Chưa chọn giọng", "Hãy tải thư viện và chọn một giọng."); return
        if self.callback.text().strip() and not self.callback.text().strip().startswith(("http://", "https://")):
            QMessageBox.warning(self, "Địa chỉ không hợp lệ", "Địa chỉ nhận kết quả chưa đúng định dạng web."); return
        self.cancel_event.clear(); self.start_btn.setEnabled(False); self.stop_btn.setEnabled(True)
        self.begin_busy("Đang tạo audio liền mạch…")
        self.progress.setValue(0)
        for row in range(len(self.sentences)):
            self.set_row_status(row, "Đang chờ", "")
        config = dict(name=self.project_name.text().strip() or "clone_giong_tai_le_mmo",
            voice_id=self.selected_voice_id(), speed=1.0,
            with_transcript=self.subtitle_switch.isChecked(),
            pronunciation_dictionary_id=self.dictionary_combo.currentData(),
            callback=self.callback.text().strip(), retries=self.retries.value(),
            input_format=self.input_format,
            normalize_loudness=self.loudness_switch.isChecked())
        self.pool.start(Task(lambda: self._process(config)))

    def _process(self, cfg):
        try:
            client = self.client()
            project_dir = self.output_dir / cfg["name"]
            project_dir.mkdir(parents=True, exist_ok=True)
            # Ghi và gửi nguyên văn bản; không tách lại, không thêm/bớt từ.
            source_suffix = ".srt" if cfg["input_format"] == "srt" else ".txt"
            if cfg["input_format"] != "srt" and self.sentences[0] != self.text.toPlainText():
                (project_dir / "noi_dung_goc.txt").write_text(self.text.toPlainText(), encoding="utf-8-sig")
            (project_dir / f"noi_dung_gui_ai33{source_suffix}").write_text(
                self.sentences[0], encoding="utf-8-sig"
            )
            results = [None] * len(self.sentences)

            def generate_once(index, sentence, attempt):
                if self.cancel_event.is_set(): return
                if attempt:
                    if not self._wait_before_retry(index, attempt, cfg["retries"]):
                        return
                    self.signals.status.emit(index, f"Thử lại {attempt}/{cfg['retries']}", "")
                else:
                    self.signals.status.emit(index, "Đang gửi", "")
                task_id = client.create_tts(
                    text=sentence,
                    voice_id=cfg["voice_id"],
                    speed=cfg["speed"],
                    file_name=f"{cfg['name']}_HOAN_CHINH.mp3",
                    receive_url=cfg["callback"],
                    with_transcript=cfg["with_transcript"],
                    pronunciation_dictionary_id=cfg["pronunciation_dictionary_id"],
                )
                self.signals.status.emit(index, "Đang tổng hợp", str(task_id))
                deadline = time.monotonic() + 30 * 60
                last_progress = -1
                transcript_wait_started = None
                while time.monotonic() < deadline and not self.cancel_event.is_set():
                    try:
                        info = client.get_task(task_id)
                    except APIError as exc:
                        # Job mới có thể chưa xuất hiện ngay trong endpoint chi tiết.
                        if exc.status_code == 404:
                            self.signals.status.emit(index, "Đang chờ máy chủ ghi nhận", str(task_id))
                            if self.cancel_event.wait(1.0): return
                            continue
                        raise
                    state = str(info.get("status") or "processing").upper()
                    server_progress = int(info.get("progress") or 0)
                    if state in ("FAILED", "FAIL", "INTERNAL_ERROR", "ERROR"):
                        raise APIError(info.get("message") or f"Âm thanh {task_id} thất bại")
                    if info.get("audio_url") and cfg["with_transcript"] and not info.get("srt_url"):
                        if transcript_wait_started is None:
                            transcript_wait_started = time.monotonic()
                        if time.monotonic() - transcript_wait_started < 90:
                            self.signals.status.emit(index, "Đang hoàn tất phụ đề", str(task_id))
                            if self.cancel_event.wait(3.0):
                                return
                            continue
                        raise APIError("Máy chủ đã tạo audio nhưng chưa trả về file phụ đề SRT.")
                    if info.get("audio_url"):
                        target = project_dir / f"{cfg['name']}_HOAN_CHINH.mp3"
                        self.signals.status.emit(
                            index,
                            "Đang tải audio & phụ đề" if cfg["with_transcript"] else "Đang tải",
                            str(task_id),
                        )
                        if cfg["normalize_loudness"]:
                            original_target = project_dir / f"{cfg['name']}_AI33_GOC.mp3"
                            client.download(info["audio_url"], original_target)
                            self.signals.status.emit(index, "Đang ổn định âm lượng", str(task_id))
                            try:
                                normalize_loudness(original_target, target)
                            except AudioNormalizationError as exc:
                                # Không làm mất kết quả đã tạo nếu bộ chuẩn hóa gặp lỗi.
                                original_target.replace(target)
                                self.signals.status.emit(
                                    index, f"Giữ file gốc • chuẩn hóa lỗi: {exc}", str(task_id)
                                )
                            else:
                                original_target.unlink(missing_ok=True)
                        else:
                            client.download(info["audio_url"], target)
                        if cfg["with_transcript"]:
                            subtitle_target = project_dir / f"{cfg['name']}_PHU_DE.srt"
                            client.download(info["srt_url"], subtitle_target)
                        results[index] = target
                        self.signals.status.emit(index, "Hoàn thành", str(task_id))
                        return
                    if server_progress != last_progress:
                        last_progress = server_progress
                        self.signals.row_progress.emit(index, server_progress, False)
                        self.signals.status.emit(index, f"Đang tổng hợp • {server_progress}%", str(task_id))
                    if self.cancel_event.wait(3.0):
                        return
                if self.cancel_event.is_set():
                    self.signals.status.emit(index, "Đã dừng", str(task_id or "")); return
                raise APIError(f"Âm thanh {task_id} quá thời gian chờ")

            def run_batch(indices, attempt):
                failures = {}
                ordered = sorted(indices)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    futures = {
                        executor.submit(generate_once, index, self.sentences[index], attempt): index
                        for index in ordered
                    }
                    for future in as_completed(futures):
                        index = futures[future]
                        try:
                            future.result()
                        except Exception as exc:
                            failures[index] = exc
                            status = "Chờ xử lý lại" if attempt < cfg["retries"] else "Lỗi"
                            self.signals.status.emit(index, status, "")
                return failures

            # Lượt đầu chạy hết mọi câu. Chỉ khi hàng đợi đầu hoàn tất mới quay lại câu lỗi.
            failures = run_batch(range(len(self.sentences)), 0)
            for attempt in range(1, cfg["retries"] + 1):
                if not failures or self.cancel_event.is_set():
                    break
                self.signals.status.emit(
                    -1,
                    f"Đã chạy hết lượt đầu • đang xử lý lại {len(failures)} câu lỗi từ trên xuống",
                    "",
                )
                failures = run_batch(failures.keys(), attempt)

            errors = [f"Câu {index + 1}: {failures[index]}" for index in sorted(failures)]
            if self.cancel_event.is_set():
                self.signals.error.emit("Đã dừng theo yêu cầu. Các file hoàn thành vẫn được giữ lại."); return
            if errors:
                raise APIError("Có câu xử lý lỗi:\n" + "\n".join(errors[:8]))
            # File nguyên bản từ API đã là kết quả hoàn chỉnh; không trim/fade/ghép lại.
            self.signals.progress.emit(100)
            self.signals.status.emit(-1, "Hoàn thành audio liền mạch • không qua xử lý ghép", "")
            self.signals.done.emit(str(results[0]))
        except Exception as exc:
            self.signals.error.emit(str(exc))

    def _wait_before_retry(self, row, attempt, total_retries):
        delay = random.randint(30, 120)
        for remaining in range(delay, 0, -1):
            if self.cancel_event.is_set():
                return False
            self.signals.status.emit(
                row,
                f"Chờ thử lại {attempt}/{total_retries} • {remaining} giây",
                "",
            )
            if self.cancel_event.wait(1.0):
                return False
        return True

    @Slot(int, str, str)
    def set_row_status(self, row, status, audio_id):
        if row < 0:
            self.summary.setText(status); return
        if row >= self.table.rowCount(): return
        self.table.setItem(row, 3, QTableWidgetItem(status))
        if audio_id: self.table.setItem(row, 4, QTableWidgetItem(audio_id))
        stage_progress = {
            "Sẵn sàng": 0, "Đang chờ": 0, "Đang gửi": 2,
            "Đang chờ máy chủ ghi nhận": 3, "Đang tổng hợp": 5, "Đang tải": 98,
            "Đang hoàn tất phụ đề": 97, "Đang tải audio & phụ đề": 98,
            "Hoàn thành": 100, "Đã dừng": 0, "Chờ xử lý lại": 8, "Lỗi": 100,
        }
        if status.startswith("Chờ thử lại"):
            value = 8
        elif status.startswith("Thử lại"):
            value = 12
        else:
            value = stage_progress.get(status)
        if value is not None:
            self.set_row_progress(row, value, status == "Lỗi")
        self.summary.setText(f"{status} • câu {row + 1}/{len(self.sentences)}")

    @Slot(int, int, bool)
    def set_row_progress(self, row, value, failed=False):
        if 0 <= row < self.table.rowCount():
            value = max(0, min(100, int(value)))
            widget = self.table.cellWidget(row, 2)
            if isinstance(widget, CompactProgressBar):
                widget.setProgress(value, failed)
            if len(self.row_progress_values) != self.table.rowCount():
                self.row_progress_values = [0] * self.table.rowCount()
            self.row_progress_values[row] = value
            if self.row_progress_values:
                self.progress.setValue(round(sum(self.row_progress_values) / len(self.row_progress_values)))

    def stop_processing(self):
        self.cancel_event.set(); self.stop_btn.setEnabled(False); self.summary.setText("Đang dừng an toàn…")

    @Slot(str)
    def processing_done(self, path):
        self.end_busy()
        self.start_btn.setEnabled(True); self.stop_btn.setEnabled(False)
        is_folder = Path(path).is_dir()
        audio_path = Path(path)
        subtitle_path = audio_path.with_name(audio_path.name.replace("_HOAN_CHINH.mp3", "_PHU_DE.srt"))
        has_subtitle = subtitle_path.is_file()
        self.summary.setText(
            "Hoàn thành • audio + phụ đề SRT • 100%" if has_subtitle
            else "Hoàn thành • audio liền mạch 100%"
        )
        box = QMessageBox(self)
        box.setWindowTitle("Hoàn thành")
        box.setText(
            "Đã tạo xong file âm thanh và phụ đề SRT." if has_subtitle
            else "Đã tạo xong file âm thanh liền mạch."
        )
        box.setInformativeText(
            f"Audio: {path}\nPhụ đề: {subtitle_path}" if has_subtitle else path
        )
        open_btn = box.addButton("Mở thư mục", QMessageBox.AcceptRole)
        box.addButton("Đóng", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() == open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path) if is_folder else Path(path).parent)))

    @Slot(str)
    def show_error(self, message):
        self.end_busy()
        self.start_btn.setEnabled(True); self.stop_btn.setEnabled(False)
        self.summary.setText("Có lỗi • kiểm tra thông báo")
        QMessageBox.critical(self, "TAILEMMO — Giọng nói", message)


STYLE = """
* { font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QWidget { background: #08111f; color: #e7eef8; }
QLabel { background: transparent; }
QFrame#card { background: #0d192a; border: 1px solid #21334d; border-radius: 14px; }
QFrame#headerCard { background: #0b1727; border: 1px solid #203653; border-radius: 14px; }
QFrame#toolbarCard { background: #0d192a; border: 1px solid #1e314b; border-radius: 10px; }
QFrame#licenseCard { background: #0d2a29; border: 1px solid #238066; border-radius: 10px; }
QLabel#brand { color: #f5b82e; font-size: 11px; font-weight: 900; letter-spacing: 2px; }
QLabel#title { color: white; font-size: 18px; font-weight: 900; }
QLabel#headerSubtitle { color: #71869f; font-size: 8px; font-weight: 700; letter-spacing: 1px; }
QLabel#headerContact { color: #8da3bb; font-size: 8px; font-weight: 700; }
QLabel#headerContact a { color: #59d1f6; text-decoration: none; }
QLabel#toolbarLabel { color: #75d6ff; font-size: 11px; font-weight: 900; min-width: 155px; }
QLabel#licenseCaption { color: #6ecfb1; font-size: 8px; font-weight: 800; letter-spacing: 1px; }
QLabel#licensePlan { color: #d8fff2; font-size: 10px; font-weight: 900; }
QLabel#licenseCountdownHeader { color: #76f2c7; font-size: 11px; font-weight: 900; }
QLabel#section { color: #75d6ff; font-size: 13px; font-weight: 800; padding: 5px; }
QLabel#muted { color: #8698af; }
QLabel#busy { color: #7fdcff; font-weight: 700; padding-right: 8px; }
QLabel#api_value { color: #f5b82e; font-weight: 800; min-width: 92px; }
QLabel#overall { color: #75d6ff; font-size: 12px; font-weight: 800; min-width: 150px; }
QLabel#activationTitle { color: #75d6ff; font-size: 20px; font-weight: 900; }
QLineEdit#machineCode { color: #f5b82e; font-family: Consolas; font-weight: 800; letter-spacing: 1px; }
QLabel#licenseSuccess { color: #6ff0b5; font-size: 14px; font-weight: 800; }
QLabel#licenseError { color: #ff7c83; font-size: 14px; font-weight: 800; }
QLabel#licensePackage { color: #dbe8f7; font-weight: 700; }
QLabel#licenseCountdown { color: #f5b82e; font-size: 18px; font-weight: 900; padding-top: 5px; }
QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget, QTableWidget, QTabWidget::pane {
  background: #091423; border: 1px solid #253a57; border-radius: 7px; padding: 7px; selection-background-color: #1677d2;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { min-height: 22px; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: #091423; }
QTextEdit { font-size: 14px; line-height: 1.4; }
QPushButton { background: #182a42; border: 1px solid #2b4669; border-radius: 7px; padding: 8px 12px; font-weight: 600; }
QPushButton:hover { background: #233c5d; border-color: #50c7ff; }
QPushButton:disabled { color: #52647a; background: #101a28; }
QPushButton#primary { color: #03101c; background: #48c8ff; border: none; font-weight: 800; }
QPushButton#primary:hover { background: #75d8ff; }
QPushButton#tagButton { background: #13253a; color: #dceafa; border-color: #315273; padding: 6px 8px; }
QPushButton#tagButton:hover, QPushButton#tagButton:open { background: #1c3a56; border-color: #59d1f6; }
QLabel#tagNote { color: #8da3bb; font-size: 11px; padding: 3px 0; }
QMenu { background: #0d192a; color: #e7eef8; border: 1px solid #315273; padding: 5px; }
QMenu::item { padding: 7px 18px 7px 10px; border-radius: 4px; }
QMenu::item:selected { background: #1c5374; color: white; }
QHeaderView::section { background: #14233a; color: #9db0c7; border: none; border-bottom: 1px solid #29415f; padding: 8px; }
QTableWidget { gridline-color: #1a2b42; alternate-background-color: #0b1727; }
QProgressBar { background: #101c2d; border: 1px solid #263b58; border-radius: 8px; height: 18px; text-align: center; font-weight: 700; }
QProgressBar::chunk { background: #2bc5f4; border-radius: 7px; }
QTabBar::tab { background: #101e31; border: 1px solid #243955; padding: 9px 16px; }
QTabBar::tab:selected { color: #5ed2ff; border-bottom: 2px solid #42c7fa; }
QSplitter::handle { background: transparent; width: 8px; }
QToolTip { background: #16283f; color: white; border: 1px solid #4abff0; }
QCheckBox#subtitleSwitch { color: #d8e8f8; font-weight: 700; spacing: 10px; padding: 3px 0; }
QCheckBox#subtitleSwitch::indicator { width: 42px; height: 21px; border-radius: 10px; background: #1a2b42; border: 1px solid #3a526f; }
QCheckBox#subtitleSwitch::indicator:checked { background: #27bde8; border: 1px solid #72dcfa; }
QCheckBox#pauseSwitch { color: #d8e8f8; font-weight: 700; spacing: 10px; padding: 5px 0 2px 0; }
QCheckBox#pauseSwitch::indicator { width: 42px; height: 21px; border-radius: 10px; background: #1a2b42; border: 1px solid #3a526f; }
QCheckBox#pauseSwitch::indicator:checked { background: #27bde8; border: 1px solid #72dcfa; }
"""


class VoiceApplication(QApplication):
    def notify(self, receiver, event):
        if event.type() == QEvent.MouseButtonRelease and isinstance(receiver, QPushButton):
            owner = receiver
            while owner is not None and not isinstance(owner, MainWindow):
                owner = owner.parentWidget()
            if isinstance(owner, MainWindow):
                QTimer.singleShot(0, owner.play_click_effect)
        return super().notify(receiver, event)


def run():
    app = VoiceApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(STYLE)
    activation = ActivationDialog()
    if activation.exec() != QDialog.Accepted or not activation.license_data:
        return
    window = MainWindow(activation.license_data)
    window.show()
    sys.exit(app.exec())
