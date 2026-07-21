from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from license_manager import (
    LicenseError,
    format_remaining,
    get_machine_id,
    load_license,
    remaining_seconds,
    save_license,
    verify_license,
)
from ui_assets import apply_app_icon


class ActivationGate(tk.Tk):
    """Cổng kích hoạt bắt buộc trước khi mở VOICE 11 LABS Studio."""

    def __init__(self) -> None:
        super().__init__()
        self.title("TAILEMMO — Kích hoạt VOICE 11 LABS Studio")
        apply_app_icon(self)
        self.geometry("780x660")
        self.minsize(700, 600)
        self.configure(background="#eef3f8")
        self.option_add("*Font", "{Segoe UI} 11")
        self.machine_id = get_machine_id()
        self.license_data: dict | None = None
        self.accepted_payload: dict | None = None
        self.machine_var = tk.StringVar(value=self.machine_id)
        self.status_var = tk.StringVar(value="Chưa kích hoạt")
        self.package_var = tk.StringVar(value="Gói hiện tại: —")
        self.countdown_var = tk.StringVar(value="THỜI GIAN CÒN LẠI: —")
        self._style()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.reject)
        self.after(1000, self._tick)
        self._load_existing()
        self.after(0, self._center)

    def _style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("ActivationTitle.TLabel", font=("Segoe UI Semibold", 20), foreground="#102a43")
        style.configure("Section.TLabel", font=("Segoe UI Semibold", 11), foreground="#163a5f")
        style.configure("Status.TLabel", font=("Segoe UI Semibold", 11), foreground="#8a1c1c")
        style.configure("Package.TLabel", font=("Segoe UI Semibold", 11), foreground="#124f2b")
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 11), padding=(18, 10))

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="KÍCH HOẠT VOICE 11 LABS STUDIO", style="ActivationTitle.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Key được ràng buộc theo mã máy, thời hạn và chữ ký số Ed25519.",
        ).pack(anchor="w", pady=(2, 4))
        ttk.Label(
            outer,
            text="Nhà phát triển: TAILEMMO   •   Zalo: 0394342601",
            style="Section.TLabel",
        ).pack(anchor="w", pady=(0, 16))

        machine_card = ttk.LabelFrame(outer, text="MÃ MÁY CỦA KHÁCH HÀNG", padding=12)
        machine_card.pack(fill="x", pady=(0, 12))
        ttk.Entry(machine_card, textvariable=self.machine_var, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(machine_card, text="Sao chép mã máy", command=self.copy_machine).pack(side="left", padx=(8, 0))

        key_card = ttk.LabelFrame(outer, text="KEY KÍCH HOẠT TLV1", padding=12)
        key_card.pack(fill="both", expand=True, pady=(0, 12))
        self.key_input = tk.Text(
            key_card,
            height=6,
            wrap="word",
            font=("Consolas", 10),
            foreground="#111111",
            background="#ffffff",
            insertbackground="#111111",
            relief="solid",
            borderwidth=1,
        )
        self.key_input.pack(fill="both", expand=True)
        self.activate_button = ttk.Button(
            key_card,
            text="KÍCH HOẠT KEY",
            style="Primary.TButton",
            command=self.activate_key,
        )
        self.activate_button.pack(anchor="e", pady=(10, 0))

        status_card = ttk.LabelFrame(outer, text="TRẠNG THÁI BẢN QUYỀN", padding=12)
        status_card.pack(fill="x", pady=(0, 12))
        self.status_label = ttk.Label(status_card, textvariable=self.status_var, style="Status.TLabel", wraplength=690)
        self.status_label.pack(anchor="w")
        ttk.Label(status_card, textvariable=self.package_var, style="Package.TLabel", wraplength=690).pack(anchor="w", pady=(5, 0))
        ttk.Label(status_card, textvariable=self.countdown_var, style="Package.TLabel").pack(anchor="w", pady=(4, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        ttk.Button(actions, text="Thoát", command=self.reject).pack(side="left")
        self.enter_button = ttk.Button(
            actions,
            text="VÀO TOOL VOICE 11 LABS",
            style="Primary.TButton",
            command=self.accept,
            state="disabled",
        )
        self.enter_button.pack(side="right")

    def _center(self) -> None:
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def copy_machine(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.machine_id)
        self.status_var.set("Đã sao chép mã máy. Hãy gửi mã này cho TAILEMMO để được cấp key.")

    def _load_existing(self) -> None:
        key = load_license()
        if not key:
            self.status_var.set("Chưa có key kích hoạt trên máy này.")
            return
        try:
            self._set_valid(verify_license(key, machine_id=self.machine_id))
        except LicenseError as exc:
            self.license_data = None
            self.enter_button.configure(state="disabled")
            self.status_var.set(str(exc))

    def _set_valid(self, payload: dict) -> None:
        self.license_data = payload
        customer = payload.get("customer") or "Khách hàng"
        package = payload.get("plan") or "Gói kích hoạt"
        expiry = (
            "VĨNH VIỄN"
            if payload.get("permanent") is True
            else datetime.fromtimestamp(int(payload["expires_at"])).strftime("%d/%m/%Y %H:%M:%S")
        )
        self.status_var.set(f"Đã kích hoạt hợp lệ cho: {customer}")
        self.package_var.set(f"Gói hiện tại: {package}  •  Hết hạn: {expiry}")
        self.enter_button.configure(state="normal")
        self._update_countdown()

    def _update_countdown(self) -> None:
        if not self.license_data:
            return
        remaining = remaining_seconds(self.license_data)
        self.countdown_var.set(
            "TRẠNG THÁI: KÍCH HOẠT VĨNH VIỄN"
            if remaining < 0
            else f"THỜI GIAN CÒN LẠI: {format_remaining(remaining)}"
        )
        if remaining == 0:
            self.enter_button.configure(state="disabled")
            self.status_var.set("Gói kích hoạt đã hết hạn.")
            self.license_data = None

    def _tick(self) -> None:
        if self.winfo_exists():
            self._update_countdown()
            self.after(1000, self._tick)

    def activate_key(self) -> None:
        key = "".join(self.key_input.get("1.0", "end-1c").split())
        if not key:
            messagebox.showwarning("Thiếu key", "Hãy dán key kích hoạt vào ô trống.", parent=self)
            return
        self.activate_button.configure(state="disabled", text="ĐANG XÁC MINH...")
        self.update_idletasks()
        try:
            payload = verify_license(key, machine_id=self.machine_id)
            save_license(key)
            self._set_valid(payload)
        except LicenseError as exc:
            self.license_data = None
            self.enter_button.configure(state="disabled")
            self.status_var.set(str(exc))
            messagebox.showerror("Kích hoạt thất bại", str(exc), parent=self)
            return
        finally:
            self.activate_button.configure(state="normal", text="KÍCH HOẠT KEY")
        messagebox.showinfo(
            "Kích hoạt thành công",
            f"Key đã được xác minh bằng chữ ký số.\n\n{self.countdown_var.get()}",
            parent=self,
        )
        self.accept()

    def accept(self) -> None:
        if not self.license_data:
            return
        try:
            key = load_license()
            self.accepted_payload = verify_license(key, machine_id=self.machine_id)
        except LicenseError as exc:
            self.status_var.set(str(exc))
            self.enter_button.configure(state="disabled")
            return
        self.destroy()

    def reject(self) -> None:
        self.accepted_payload = None
        self.destroy()

    def run(self) -> dict | None:
        self.mainloop()
        return self.accepted_payload


def run_activation_gate() -> dict | None:
    # Key đã kích hoạt được lưu mã hóa bằng Windows DPAPI. Nếu vẫn hợp lệ,
    # xác thực ngầm và vào thẳng tool, không yêu cầu người dùng kích hoạt lại.
    stored_key = load_license()
    if stored_key:
        try:
            return verify_license(stored_key, machine_id=get_machine_id())
        except LicenseError:
            pass
    return ActivationGate().run()
