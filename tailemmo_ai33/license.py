"""Xác minh license offline theo mã máy và chữ ký số Ed25519.

Chỉ public key được phép phân phối cùng ứng dụng. Private key nằm trong
công cụ cấp key riêng của chủ sở hữu.
"""

import base64
import ctypes
import hashlib
import hmac
import json
import os
import platform
import time
from pathlib import Path

from .settings import APP_DIR, protect, unprotect


PRODUCT_ID = "TAILEMMO_VOICE_STUDIO"
PUBLIC_KEY_B64 = "GARQdsV5NdxCfoHrrcFvMA0fubx-hD1nUk38BpyjbSI"
LICENSE_FILE = APP_DIR / "activation.key"
CLOCK_FILE = APP_DIR / "license_clock.dat"
ROLLBACK_TOLERANCE_SECONDS = 300


class LicenseError(RuntimeError):
    pass


def _b64decode(value: str) -> bytes:
    value = value.strip()
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _machine_guid() -> str:
    try:
        import winreg
        flags = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            flags,
        ) as key:
            return str(winreg.QueryValueEx(key, "MachineGuid")[0])
    except Exception:
        return ""


def _system_volume_serial() -> str:
    try:
        root = os.environ.get("SystemDrive", "C:") + "\\"
        serial = ctypes.c_ulong()
        maximum = ctypes.c_ulong()
        flags = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), None, 0, ctypes.byref(serial),
            ctypes.byref(maximum), ctypes.byref(flags), None, 0,
        )
        return f"{serial.value:08X}" if ok else ""
    except Exception:
        return ""


def get_machine_id() -> str:
    """Mã ổn định từ Windows MachineGuid, ổ hệ thống và CPU."""
    parts = [
        _machine_guid(),
        _system_volume_serial(),
        os.environ.get("PROCESSOR_IDENTIFIER", ""),
        platform.machine(),
    ]
    material = "|".join(str(part).strip().upper() for part in parts if part)
    if not material:
        material = platform.node().strip().upper()
    digest = hashlib.sha256((PRODUCT_ID + "|" + material).encode("utf-8")).hexdigest().upper()[:32]
    return "TL-" + "-".join(digest[index:index + 4] for index in range(0, 32, 4))


# Phần xác minh Ed25519 thuần Python, không cần package mật mã bên ngoài.
_Q = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493


def _inv(value):
    return pow(value, _Q - 2, _Q)


_D = (-121665 * _inv(121666)) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)


def _xrecover(y):
    x_squared = (y * y - 1) * _inv(_D * y * y + 1) % _Q
    x = pow(x_squared, (_Q + 3) // 8, _Q)
    if (x * x - x_squared) % _Q:
        x = x * _I % _Q
    if x & 1:
        x = _Q - x
    return x


_BY = 4 * _inv(5) % _Q
_B = (_xrecover(_BY), _BY)


def _point_add(p, q):
    x1, y1 = p
    x2, y2 = q
    factor = _D * x1 * x2 * y1 * y2
    return (
        (x1 * y2 + x2 * y1) * _inv(1 + factor) % _Q,
        (y1 * y2 + x1 * x2) * _inv(1 - factor) % _Q,
    )


def _scalar_mult(point, scalar):
    result = (0, 1)
    while scalar:
        if scalar & 1:
            result = _point_add(result, point)
        point = _point_add(point, point)
        scalar >>= 1
    return result


def _encode_point(point):
    x, y = point
    return int(y | ((x & 1) << 255)).to_bytes(32, "little")


def _decode_point(raw):
    if len(raw) != 32:
        raise ValueError("Sai độ dài point")
    value = int.from_bytes(raw, "little")
    y = value & ((1 << 255) - 1)
    if y >= _Q:
        raise ValueError("Point không canonical")
    x = _xrecover(y)
    if (x & 1) != (value >> 255):
        x = _Q - x
    if (y * y - x * x - 1 - _D * x * x * y * y) % _Q:
        raise ValueError("Point không nằm trên curve")
    return x, y


def _verify_signature(signature: bytes, message: bytes, public_key: bytes) -> bool:
    try:
        if len(signature) != 64 or len(public_key) != 32:
            return False
        r_raw, s_raw = signature[:32], signature[32:]
        scalar_s = int.from_bytes(s_raw, "little")
        if scalar_s >= _L:
            return False
        point_r = _decode_point(r_raw)
        point_a = _decode_point(public_key)
        challenge = int.from_bytes(
            hashlib.sha512(r_raw + public_key + message).digest(), "little"
        ) % _L
        left = _encode_point(_scalar_mult(_B, scalar_s))
        right = _encode_point(_point_add(point_r, _scalar_mult(point_a, challenge)))
        return hmac.compare_digest(left, right)
    except Exception:
        return False


def _read_last_seen() -> int:
    try:
        return int(json.loads(unprotect(CLOCK_FILE.read_text(encoding="ascii")))["last_seen"])
    except Exception:
        return 0


def _write_last_seen(timestamp: int) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    encoded = protect(json.dumps({"last_seen": int(timestamp)}, separators=(",", ":")))
    temp = CLOCK_FILE.with_suffix(".tmp")
    temp.write_text(encoded, encoding="ascii")
    temp.replace(CLOCK_FILE)


def verify_license(
    key_text: str,
    *,
    machine_id: str | None = None,
    now: int | None = None,
    update_clock: bool = True,
) -> dict:
    key_text = "".join(str(key_text or "").split())
    pieces = key_text.split(".")
    if len(pieces) != 3 or pieces[0] != "TLV1":
        raise LicenseError("Key không đúng định dạng TAILEMMO.")
    try:
        payload_raw = _b64decode(pieces[1])
        signature = _b64decode(pieces[2])
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception as exc:
        raise LicenseError("Key bị hỏng hoặc không đọc được.") from exc
    public_key = _b64decode(PUBLIC_KEY_B64)
    if not _verify_signature(signature, payload_raw, public_key):
        raise LicenseError("Chữ ký số không hợp lệ. Key có thể đã bị sửa.")
    if payload.get("v") != 1 or payload.get("product") != PRODUCT_ID:
        raise LicenseError("Key không dành cho sản phẩm này.")
    current_machine = machine_id or get_machine_id()
    if str(payload.get("machine_id", "")).upper() != current_machine.upper():
        raise LicenseError("Key không khớp mã máy này.")
    current_time = int(time.time() if now is None else now)
    try:
        issued_at = int(payload["issued_at"])
        expires_at = int(payload["expires_at"])
    except Exception as exc:
        raise LicenseError("Key thiếu thông tin thời hạn.") from exc
    if expires_at <= issued_at:
        raise LicenseError("Thời hạn trong key không hợp lệ.")
    if current_time + ROLLBACK_TOLERANCE_SECONDS < issued_at:
        raise LicenseError("Thời gian Windows sớm hơn thời điểm cấp key.")
    if current_time >= expires_at:
        raise LicenseError("Gói kích hoạt đã hết hạn.")
    last_seen = _read_last_seen() if update_clock and now is None else 0
    if last_seen and current_time + ROLLBACK_TOLERANCE_SECONDS < last_seen:
        raise LicenseError("Phát hiện đồng hồ Windows bị chỉnh lùi.")
    if update_clock and now is None:
        _write_last_seen(max(current_time, last_seen))
    return payload


def save_license(key_text: str) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    temp = LICENSE_FILE.with_suffix(".tmp")
    temp.write_text("".join(key_text.split()), encoding="ascii")
    temp.replace(LICENSE_FILE)


def load_license() -> str:
    try:
        return LICENSE_FILE.read_text(encoding="ascii").strip()
    except Exception:
        return ""


def remaining_seconds(payload: dict, now: int | None = None) -> int:
    return max(0, int(payload.get("expires_at", 0)) - int(time.time() if now is None else now))


def format_remaining(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{days} ngày {hours:02d}:{minutes:02d}:{seconds:02d}"
