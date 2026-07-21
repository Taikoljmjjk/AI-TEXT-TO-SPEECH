import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


# Tat ca du lieu do ung dung tao ra duoc dat tren o D. Bien moi truong
# TAILEMMO_DATA_ROOT chi dung de kiem thu/quan tri, khong phu thuoc APPDATA.
DATA_ROOT = Path(os.getenv("TAILEMMO_DATA_ROOT", r"D:\TAI_LE_MMO\TOOL_CLONE_GIONG_TAI_LE_MMO"))
APP_DIR = DATA_ROOT / "DuLieu"
DEFAULT_OUTPUT_DIR = DATA_ROOT / "AmThanh"
SETTINGS_FILE = APP_DIR / "settings.json"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def protect(value: str) -> str:
    if not value:
        return ""
    raw, keep = _blob(value.encode("utf-8"))
    out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(raw), None, None, None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return base64.b64encode(ctypes.string_at(out.pbData, out.cbData)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def unprotect(value: str) -> str:
    if not value:
        return ""
    raw, keep = _blob(base64.b64decode(value))
    out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(raw), None, None, None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        for item in data.get("api_keys", []):
            item["key"] = unprotect(item.get("key", ""))
        return data
    except Exception:
        return {"api_keys": []}


def save_settings(data: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    safe = dict(data)
    safe["api_keys"] = [dict(item, key=protect(item.get("key", ""))) for item in data.get("api_keys", [])]
    temp = SETTINGS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(SETTINGS_FILE)
