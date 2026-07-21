from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import ctypes
import textwrap
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import imageio_ffmpeg

from api_client import ApiError, ElevenLabsClient
from license_manager import format_remaining, remaining_seconds
from ui_assets import apply_app_icon


ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config" / "settings.json"
OUTPUT_DIR = ROOT / "outputs"

OUTPUT_FORMATS = [
    "mp3_22050_32",
    "mp3_24000_48",
    "mp3_44100_32",
    "mp3_44100_64",
    "mp3_44100_96",
    "mp3_44100_128",
    "mp3_44100_192",
    "pcm_8000",
    "pcm_16000",
    "pcm_22050",
    "pcm_24000",
    "pcm_32000",
    "pcm_44100",
    "pcm_48000",
    "wav_8000",
    "wav_16000",
    "wav_22050",
    "wav_24000",
    "wav_32000",
    "wav_44100",
    "wav_48000",
    "ulaw_8000",
    "alaw_8000",
    "opus_48000_32",
    "opus_48000_64",
    "opus_48000_96",
    "opus_48000_128",
    "opus_48000_192",
]


def output_extension(output_format: str) -> str:
    codec = output_format.split("_", 1)[0].lower()
    return {
        "mp3": ".mp3",
        "pcm": ".pcm",
        "wav": ".wav",
        "ulaw": ".ulaw",
        "alaw": ".alaw",
        "opus": ".opus",
    }.get(codec, ".audio")


def safe_output_stem(title: str, fallback: str) -> str:
    """Return a Windows-safe filename stem while keeping readable Unicode."""
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title.strip())
    stem = re.sub(r"\s+", " ", stem).rstrip(" .")
    for suffix in (".mp3", ".wav", ".opus", ".pcm", ".ulaw", ".alaw", ".srt"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)].rstrip(" .")
            break
    if not stem:
        stem = fallback
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if stem.split(".", 1)[0].upper() in reserved:
        stem = f"_{stem}"
    return stem[:120].rstrip(" .") or fallback


def unique_output_path(directory: Path, stem: str, extension: str, with_srt: bool) -> Path:
    """Avoid silently overwriting an existing audio or matching SRT file."""
    candidate = directory / f"{stem}{extension}"
    index = 2
    while candidate.exists() or (with_srt and candidate.with_suffix(".srt").exists()):
        candidate = directory / f"{stem}_{index}{extension}"
        index += 1
    return candidate


def srt_to_plain_text(content: str) -> str:
    """Remove SRT indexes/timestamps while preserving cue order and readable pauses."""
    content = content.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    timestamp = re.compile(
        r"^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}.*$"
    )
    cues: list[str] = []
    for block in re.split(r"\n\s*\n", content):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines and lines[0].isdigit():
            lines.pop(0)
        lines = [line for line in lines if not timestamp.match(line)]
        text = " ".join(lines)
        text = re.sub(r"<[^>]+>", "", text).strip()
        if text:
            cues.append(text)
    return "\n".join(cues)


def summarize_tts_failures(failures: list[str], required_characters: int) -> str:
    """Turn many repeated ElevenLabs failures into one readable message."""
    quota = 0
    disabled = 0
    rate_limited = 0
    unauthorized = 0
    other = 0
    remaining_values: list[int] = []
    for failure in failures:
        lowered = failure.lower()
        if "exceeds your quota" in lowered or "not enough credits" in lowered:
            quota += 1
            match = re.search(r"you have\s+([\d,]+)\s+credits remaining", lowered)
            if match:
                remaining_values.append(int(match.group(1).replace(",", "")))
        elif "unusual activity" in lowered or "free tier access has been disabled" in lowered:
            disabled += 1
        elif "http 429" in lowered or "rate limit" in lowered:
            rate_limited += 1
        elif "http 401" in lowered or "unauthorized" in lowered:
            unauthorized += 1
        else:
            other += 1

    lines = [
        f"Không API nào tạo được audio cho {required_characters:,} ký tự.",
        "",
    ]
    if quota:
        maximum = max(remaining_values) if remaining_values else None
        detail = f"; cao nhất còn {maximum:,} credit" if maximum is not None else ""
        lines.append(f"• {quota} API không đủ credit/quota{detail}.")
    if disabled:
        lines.append(f"• {disabled} API Free Tier đã bị ElevenLabs vô hiệu hóa do hoạt động bất thường.")
    if unauthorized:
        lines.append(f"• {unauthorized} API sai key, hết quyền hoặc bị từ chối truy cập.")
    if rate_limited:
        lines.append(f"• {rate_limited} API đang bị giới hạn tần suất.")
    if other:
        lines.append(f"• {other} API gặp lỗi khác.")
    lines.extend(
        [
            "",
            "ElevenLabs yêu cầu một API phải đủ credit cho toàn bộ một request; credit của nhiều API không được cộng chung.",
            "Hãy chia nội dung thành phần ngắn hơn số credit còn lại cao nhất hoặc sử dụng API có gói phù hợp.",
        ]
    )
    return "\n".join(lines)


def classify_api_error(message: str) -> str:
    lowered = message.lower()
    if "unusual activity" in lowered or "free tier access has been disabled" in lowered:
        return "disabled"
    if "http 429" in lowered or "rate limit" in lowered:
        return "rate_limited"
    if "exceeds your quota" in lowered or "not enough credits" in lowered or "http 402" in lowered:
        return "quota"
    if "http 401" in lowered or "http 403" in lowered or "unauthorized" in lowered:
        return "unauthorized"
    return "error"


class TaskCancelled(Exception):
    """Raised when the user intentionally stops a running task."""

    def __init__(self, message: str, balances_verified: bool = False) -> None:
        super().__init__(message)
        self.balances_verified = balances_verified


def take_text_chunk(content: str, limit: int) -> tuple[str, str]:
    """Take a natural-language chunk no longer than limit characters."""
    if len(content) <= limit:
        return content, ""
    minimum = max(1, int(limit * 0.55))
    window = content[: limit + 1]
    candidates: list[int] = []
    for match in re.finditer(r"(?:\n\s*\n|[.!?…]+[\"'”’)]*\s+|[;:]\s+|,\s+|\s+)", window):
        if minimum <= match.end() <= limit:
            candidates.append(match.end())
    cut = candidates[-1] if candidates else limit
    # Preserve every character around the boundary. This makes joining all
    # chunks reproduce the original input byte-for-byte.
    return content[:cut], content[cut:]


def audio_duration_seconds(path: Path, output_format: str) -> float:
    codec, _, rate_text = output_format.partition("_")
    try:
        sample_rate = int(rate_text.split("_", 1)[0])
    except ValueError:
        sample_rate = 0
    if codec == "pcm" and sample_rate:
        return path.stat().st_size / float(sample_rate * 2)
    if codec in {"ulaw", "alaw"} and sample_rate:
        return path.stat().st_size / float(sample_rate)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    probe = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr or "")
    duration = (
        int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
        if match
        else 0.0
    )
    if duration <= 0:
        raise ApiError(f"Không đo được thời lượng phần audio: {path.name}")
    return duration


def merge_audio_parts(parts: list[Path], destination: Path, output_format: str) -> None:
    if not parts:
        raise ApiError("Không có phần audio để ghép.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    codec = output_format.split("_", 1)[0].lower()
    if codec in {"pcm", "ulaw", "alaw"}:
        with destination.open("wb") as target:
            for part in parts:
                with part.open("rb") as source:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
        return

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    list_path = parts[0].parent / "concat_audio.txt"
    list_lines = []
    for part in parts:
        escaped = part.resolve().as_posix().replace("'", "'\\''")
        list_lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(list_lines) + "\n", encoding="utf-8")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def run(extra: list[str]) -> subprocess.CompletedProcess[str]:
        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            *extra,
            str(destination),
        ]
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            creationflags=creation_flags,
        )

    result = run(["-c:a", "copy"])
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        encode_args = {
            "mp3": ["-c:a", "libmp3lame"],
            "wav": ["-c:a", "pcm_s16le"],
            "opus": ["-c:a", "libopus"],
        }.get(codec)
        if encode_args is None:
            raise ApiError(f"Chưa hỗ trợ ghép định dạng {output_format}.")
        bitrate = output_format.rsplit("_", 1)[-1]
        if codec in {"mp3", "opus"} and bitrate.isdigit():
            encode_args.extend(["-b:a", f"{bitrate}k"])
        result = run(encode_args)
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        detail = (result.stderr or result.stdout or "Lỗi FFmpeg không xác định").strip()[-800:]
        raise ApiError(f"Không ghép được các phần audio: {detail}")


def merge_alignment_segments(alignments: list[dict[str, Any]], durations: list[float]) -> dict[str, Any]:
    if len(alignments) != len(durations):
        raise ApiError("Số phần timestamp không khớp số phần audio.")
    merged: dict[str, list[Any]] = {
        "characters": [],
        "character_start_times_seconds": [],
        "character_end_times_seconds": [],
    }
    offset = 0.0
    for index, (alignment, duration) in enumerate(zip(alignments, durations)):
        characters = list(alignment.get("characters") or [])
        starts = list(alignment.get("character_start_times_seconds") or [])
        ends = list(alignment.get("character_end_times_seconds") or [])
        length = min(len(characters), len(starts), len(ends))
        if not length:
            raise ApiError(f"Phần audio {index + 1} không có timestamp để ghép SRT.")
        if index and merged["characters"] and not str(merged["characters"][-1]).isspace():
            merged["characters"].append(" ")
            merged["character_start_times_seconds"].append(offset)
            merged["character_end_times_seconds"].append(offset)
        merged["characters"].extend(str(value) for value in characters[:length])
        merged["character_start_times_seconds"].extend(offset + float(value) for value in starts[:length])
        merged["character_end_times_seconds"].extend(offset + float(value) for value in ends[:length])
        offset += max(float(duration), float(ends[length - 1]))
    return merged


def enable_windows_dpi_awareness() -> None:
    """Keep Tk text sharp when Windows display scaling is above 100%."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


class AudioPreviewPlayer:
    def __init__(self) -> None:
        self.alias = "voice11_preview"
        self.temporary_path: Path | None = None

    @staticmethod
    def _send(command: str) -> None:
        buffer = ctypes.create_unicode_buffer(256)
        error = ctypes.windll.winmm.mciSendStringW(command, buffer, 255, 0)
        if error:
            message = ctypes.create_unicode_buffer(256)
            ctypes.windll.winmm.mciGetErrorStringW(error, message, 255)
            raise ApiError(f"Không phát được audio: {message.value}")

    def stop(self) -> None:
        try:
            ctypes.windll.winmm.mciSendStringW(f"stop {self.alias}", None, 0, 0)
            ctypes.windll.winmm.mciSendStringW(f"close {self.alias}", None, 0, 0)
        except Exception:
            pass
        if self.temporary_path is not None:
            try:
                self.temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.temporary_path = None

    @staticmethod
    def _prepare_mp3(path: Path) -> Path:
        """Strip ID3 metadata that makes legacy Windows MCI reject some previews."""
        with path.open("rb") as source:
            header = source.read(10)
            if len(header) < 10 or header[:3] != b"ID3":
                return path
            tag_size = (
                ((header[6] & 0x7F) << 21)
                | ((header[7] & 0x7F) << 14)
                | ((header[8] & 0x7F) << 7)
                | (header[9] & 0x7F)
            )
            audio_offset = 10 + tag_size + (10 if header[5] & 0x10 else 0)
            if audio_offset >= path.stat().st_size:
                raise ApiError("File nghe thử không chứa dữ liệu audio MP3 hợp lệ.")
            source.seek(audio_offset)
            with tempfile.NamedTemporaryFile(prefix="voice11_preview_", suffix=".mp3", delete=False) as target:
                while chunk := source.read(1024 * 256):
                    target.write(chunk)
                return Path(target.name)

    def play(self, path: Path) -> None:
        self.stop()
        prepared = self._prepare_mp3(path)
        if prepared != path:
            self.temporary_path = prepared
        safe_path = str(prepared.resolve()).replace('"', '')
        try:
            self._send(f'open "{safe_path}" type mpegvideo alias {self.alias}')
            self._send(f"play {self.alias}")
        except Exception:
            self.stop()
            raise


class SegmentedProgressBar(tk.Canvas):
    """A crisp segmented progress indicator that follows the ttk value API."""

    def __init__(self, master: tk.Misc, maximum: float = 100, segments: int = 32, **kwargs: Any) -> None:
        super().__init__(
            master,
            height=24,
            background="#f7f9fb",
            highlightthickness=0,
            borderwidth=0,
            **kwargs,
        )
        self._value = 0.0
        self._maximum = float(maximum)
        self._segments = segments
        self.bind("<Configure>", lambda _event: self._draw())

    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:
        if cnf:
            kwargs.update(cnf)
        value = kwargs.pop("value", None)
        maximum = kwargs.pop("maximum", None)
        kwargs.pop("mode", None)
        if value is not None:
            self._value = max(0.0, min(float(value), self._maximum))
        if maximum is not None:
            self._maximum = max(1.0, float(maximum))
            self._value = min(self._value, self._maximum)
        result = super().configure(**kwargs) if kwargs else None
        self._draw()
        return result

    config = configure

    def cget(self, key: str) -> Any:
        if key == "value":
            return self._value
        if key == "maximum":
            return self._maximum
        if key == "mode":
            return "determinate"
        return super().cget(key)

    def _draw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        if width < 8 or height < 8:
            return
        outer = 2
        self.create_rectangle(
            outer,
            outer,
            width - outer,
            height - outer,
            fill="#eef2f4",
            outline="#35627a",
            width=2,
        )
        gap = 2
        left = 6
        right = width - 6
        top = 6
        bottom = height - 6
        available = max(1, right - left)
        segment_width = (available - gap * (self._segments - 1)) / self._segments
        filled = round((self._value / self._maximum) * self._segments)
        for index in range(self._segments):
            x1 = left + index * (segment_width + gap)
            x2 = x1 + segment_width
            color = "#28a9e2" if index < filled else "#dfe7eb"
            self.create_rectangle(x1, top, x2, bottom, fill=color, outline=color)


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def alignment_to_srt(alignment: dict[str, Any]) -> str:
    characters = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    length = min(len(characters), len(starts), len(ends))
    if not length:
        raise ApiError("ElevenLabs không trả timestamp để tạo SRT.")

    words: list[tuple[str, float, float]] = []
    current: list[str] = []
    word_start = 0.0
    word_end = 0.0
    for index in range(length):
        character = str(characters[index])
        if character.isspace():
            if current:
                words.append(("".join(current), word_start, word_end))
                current = []
            continue
        if not current:
            word_start = float(starts[index])
        current.append(character)
        word_end = float(ends[index])
    if current:
        words.append(("".join(current), word_start, word_end))
    if not words:
        raise ApiError("Không tách được từ từ dữ liệu timestamp.")

    captions: list[tuple[str, float, float]] = []
    group: list[tuple[str, float, float]] = []
    for word in words:
        candidate = " ".join(item[0] for item in [*group, word])
        duration = word[2] - (group[0][1] if group else word[1])
        should_break = bool(group) and (len(candidate) > 78 or duration > 5.5)
        if should_break:
            captions.append((" ".join(item[0] for item in group), group[0][1], group[-1][2]))
            group = []
        group.append(word)
        text = " ".join(item[0] for item in group)
        if len(text) >= 24 and text.rstrip().endswith((".", "?", "!", ";", ":")):
            captions.append((text, group[0][1], group[-1][2]))
            group = []
    if group:
        captions.append((" ".join(item[0] for item in group), group[0][1], group[-1][2]))

    blocks: list[str] = []
    for index, (caption, start, end) in enumerate(captions, 1):
        wrapped = "\n".join(textwrap.wrap(caption, width=42, break_long_words=False, break_on_hyphens=False))
        blocks.append(f"{index}\n{_srt_time(start)} --> {_srt_time(max(end, start + 0.2))}\n{wrapped}")
    return "\n\n".join(blocks) + "\n"


class VoiceStudio(tk.Tk):
    def __init__(self, license_data: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.title("VOICE 11 LABS Studio | Nhà phát triển: TAILEMMO | Zalo: 0394342601")
        apply_app_icon(self)
        self.geometry("1120x820")
        self.minsize(960, 700)
        self.option_add("*Font", "{Segoe UI} 11")
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.license_data = license_data
        self.license_status = tk.StringVar(value="Bản quyền: chế độ phát triển")
        self.busy_count = 0
        self.audio_pause_event = threading.Event()
        self.audio_stop_event = threading.Event()
        self.audio_pause_had_real_progress = False
        self.settings = self._load_settings()
        stored_keys = self.settings.get("api_keys", [])
        if not stored_keys and self.settings.get("api_key"):
            stored_keys = [self.settings["api_key"]]
        self.api_keys: list[str] = [str(key).strip() for key in stored_keys if str(key).strip()]
        self.api_index = 0
        saved_base_url = self.settings.get("base_url", "https://api.elevenlabs.io")
        if "elevenlabs.click" in saved_base_url:
            saved_base_url = "https://api.elevenlabs.io"
        self.base_url = tk.StringVar(value=saved_base_url)
        self.status = tk.StringVar(value="Sẵn sàng")
        self.credits = tk.StringVar(value="API vừa kiểm tra: --")
        self.clone_file = tk.StringVar()
        self.clone_name = tk.StringVar()
        self.clone_gender = tk.StringVar(value="Nữ")
        self.create_srt = tk.BooleanVar(value=True)
        saved_output = self.settings.get("output_dir") or str(OUTPUT_DIR)
        self.output_dir = tk.StringVar(value=str(Path(saved_output)))
        self.total_available_characters: int | None = None
        self.character_counter = tk.StringVar(value="Nội dung: 0 ký tự | Tổng toàn bộ API: chưa kiểm tra")
        self.api_count = tk.StringVar(value=f"{len(self.api_keys)} API key")
        self.total_credits = tk.StringVar(value="Tổng credit: --")
        self.api_health_summary = tk.StringVar(value="Hợp lệ: -- | IVC: --")
        self.api_summary = tk.StringVar(value="Chưa kiểm tra tổng danh sách API")
        self.voice_count = tk.StringVar(value="0 giọng khả dụng")
        self.notice_text = tk.StringVar(value="●  Sẵn sàng nhận tác vụ")
        self.progress_text = tk.StringVar(value="0%")
        self.progress_has_real_value = False
        self.progress_completion_shown = True
        self.button_jobs: dict[ttk.Button | tk.Button, tuple[str, str, int]] = {}
        self.clone_rows: dict[str, dict[str, Any]] = {}
        self.voice_rows: dict[str, dict[str, Any]] = {}
        saved_disabled = {
            str(key).strip() for key in self.settings.get("disabled_api_keys", []) if str(key).strip() in self.api_keys
        }
        self.api_states: dict[str, dict[str, Any]] = {
            key: {"status": "disabled", "remaining": None, "reason": "Đã bị ElevenLabs vô hiệu hóa ở lần kiểm tra trước."}
            for key in saved_disabled
        }
        self.bad_api_records: dict[str, dict[str, str]] = {
            key: {"status": "disabled", "reason": "Đã bị ElevenLabs vô hiệu hóa ở lần kiểm tra trước."}
            for key in saved_disabled
        }
        self.api_backoff_until: dict[str, float] = {}
        self.last_created_dir: Path | None = None
        self.preview_player = AudioPreviewPlayer()
        self._style()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(0, self._maximize_window)
        self.after(100, self._drain_events)
        self.after(800, self._auto_check_apis)
        self.after(1000, self._tick_license_status)

    def _style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure(".", font=("Segoe UI", 11), foreground="#111111")
        style.configure("TLabel", foreground="#111111")
        style.configure("TLabelframe.Label", font=("Segoe UI Semibold", 11), foreground="#111111")
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 16), foreground="#0b1f33")
        style.configure("Header.TLabel", font=("Segoe UI Semibold", 12), foreground="#0b1f33")
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 11), padding=(16, 9))
        style.configure("CompactAccent.TButton", font=("Segoe UI Semibold", 11), padding=(12, 5))
        style.configure("Treeview", font=("Segoe UI", 10), rowheight=30, foreground="#111111")
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10), foreground="#111111")
        style.configure("TNotebook.Tab", font=("Segoe UI Semibold", 11), padding=(14, 8))
        style.configure("Thick.Horizontal.TProgressbar", thickness=18)

    def _build(self) -> None:
        header = ttk.Frame(self, padding=(18, 3, 18, 2))
        header.pack(fill="x")
        ttk.Label(header, text="VOICE 11 LABS Studio", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.license_status, style="Header.TLabel").grid(row=0, column=1, sticky="e")
        header.columnconfigure(0, weight=1)

        stats = ttk.Frame(header)
        stats.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(1, 0))
        ttk.Label(stats, textvariable=self.api_count).pack(side="left")
        self.check_api_button = ttk.Button(stats, text="Kiểm tra API", command=self.check_api)
        self.check_api_button.pack(side="left", padx=10)
        ttk.Label(stats, textvariable=self.api_health_summary).pack(side="left", padx=(0, 12))
        ttk.Label(stats, textvariable=self.total_credits, style="Header.TLabel").pack(side="left", padx=(0, 12))
        ttk.Label(stats, textvariable=self.credits).pack(side="left")

        keybar = ttk.Frame(self, padding=(8, 4), relief="groove", borderwidth=1)
        keybar.pack(fill="x", padx=18, pady=(0, 5))
        ttk.Label(keybar, text="Kết nối API  •  Base URL").grid(row=0, column=0, sticky="w")
        ttk.Entry(keybar, textvariable=self.base_url).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(keybar, text="Xoay vòng API tự động.").grid(row=0, column=2, padx=12)
        ttk.Button(keybar, text="Lưu cấu hình", command=self.save_settings).grid(row=0, column=3)
        keybar.columnconfigure(1, weight=1)

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=18)
        self.tts_tab = ttk.Frame(self.tabs, padding=14)
        self.clone_tab = ttk.Frame(self.tabs, padding=14)
        self.voice_tab = ttk.Frame(self.tabs, padding=14)
        self.api_tab = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(self.tts_tab, text="  Tạo audio  ")
        self.tabs.add(self.clone_tab, text="  Clone voice  ")
        self.tabs.add(self.voice_tab, text="  Kho giọng  ")
        self.tabs.add(self.api_tab, text="  Danh sách API  ")
        self._build_tts()
        self._build_clone()
        self._build_voices()
        self._build_api_keys()

        progress_panel = ttk.LabelFrame(self, text="Tiến trình tác vụ", padding=(8, 4))
        progress_panel.pack(side="bottom", fill="x", padx=18, pady=(3, 6), before=self.tabs)
        self.progress = SegmentedProgressBar(progress_panel, maximum=100, segments=36)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress_percent_label = ttk.Label(
            progress_panel,
            textvariable=self.progress_text,
            width=7,
            anchor="center",
            style="Header.TLabel",
        )
        self.progress_percent_label.grid(row=0, column=1, padx=(12, 4))
        self.notice_bar = tk.Frame(
            progress_panel,
            background="#e8f1fb",
            highlightthickness=1,
            highlightbackground="#9bbfe5",
        )
        self.notice_bar.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        self.notice_label = tk.Label(
            self.notice_bar,
            textvariable=self.notice_text,
            background="#e8f1fb",
            foreground="#123b63",
            font=("Segoe UI Semibold", 10),
            anchor="w",
            padx=10,
            pady=3,
        )
        self.notice_label.pack(fill="both", expand=True)
        progress_panel.columnconfigure(0, weight=3)
        progress_panel.columnconfigure(2, weight=2, minsize=280)

        footer = ttk.Frame(self, padding=(18, 3, 18, 3))
        footer.pack(side="bottom", fill="x", before=self.tabs)
        ttk.Label(
            footer,
            text="Nhà phát triển: TAILEMMO   •   Zalo: 0394342601",
            style="Header.TLabel",
        ).pack(side="left")
        ttk.Button(footer, text="Mở vị trí lưu", command=self.open_output_dir).pack(side="right")
        self.open_last_created_button = ttk.Button(
            footer,
            text="Mở thư mục vừa tạo",
            command=self.open_last_created_dir,
            state="disabled",
        )
        self.open_last_created_button.pack(side="right", padx=(0, 8))

    def _build_tts(self) -> None:
        def saved_float(name: str, default: float) -> float:
            try:
                return float(self.settings.get(name, default))
            except (TypeError, ValueError):
                return default

        self.engine = tk.StringVar(value="ElevenLabs")
        self.output_title = tk.StringVar()
        self.voice_id = tk.StringVar()
        self.model_id = tk.StringVar(value="eleven_flash_v2_5")
        self.output_format = tk.StringVar(value=str(self.settings.get("tts_output_format", "mp3_44100_128")))
        self.speed = tk.DoubleVar(value=saved_float("tts_speed", 1.0))
        self.stability = tk.DoubleVar(value=saved_float("tts_stability", 0.5))
        self.similarity_boost = tk.DoubleVar(value=saved_float("tts_similarity_boost", 0.75))
        self.style_exaggeration = tk.DoubleVar(value=saved_float("tts_style", 0.0))
        self.speaker_boost = tk.BooleanVar(value=bool(self.settings.get("tts_speaker_boost", True)))
        self.language_override = tk.BooleanVar(value=bool(self.settings.get("tts_language_override", False)))
        self.language_code = tk.StringVar(value=str(self.settings.get("tts_language_code", "vi")))
        self.speed_value = tk.StringVar()
        self.stability_value = tk.StringVar()
        self.similarity_value = tk.StringVar()
        self.style_value = tk.StringVar()
        self.pitch = tk.IntVar(value=0)
        self.volume = tk.DoubleVar(value=1.0)

        actions = ttk.Frame(self.tts_tab, padding=(0, 5, 8, 3))
        actions.pack(side="bottom", fill="x")
        ttk.Label(actions, text="Lưu tại:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(actions, textvariable=self.output_dir, state="readonly").grid(row=0, column=1, sticky="ew")
        tk.Button(
            actions,
            text="Chọn thư mục",
            command=self.choose_output_dir,
            background="#2e7d32",
            foreground="#ffffff",
            activebackground="#1b5e20",
            activeforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI Semibold", 10),
            padx=10,
            pady=5,
            cursor="hand2",
        ).grid(row=0, column=2, padx=(6, 12))
        ttk.Button(actions, text="Xóa", command=self._clear_text_input).grid(row=0, column=3, padx=(0, 6))
        self.create_audio_button = tk.Button(
            actions,
            text="TẠO AUDIO",
            command=self.create_audio,
            background="#1565c0",
            foreground="#ffffff",
            activebackground="#0d47a1",
            activeforeground="#ffffff",
            disabledforeground="#d9e8fa",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI Semibold", 10),
            padx=12,
            pady=5,
            cursor="hand2",
        )
        self.create_audio_button.grid(row=0, column=4, sticky="e")
        self.pause_audio_button = tk.Button(
            actions,
            text="TẠM DỪNG",
            command=self.toggle_pause_audio,
            state="disabled",
            background="#f9a825",
            foreground="#1f1a00",
            activebackground="#fbc02d",
            activeforeground="#1f1a00",
            disabledforeground="#6d5600",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI Semibold", 10),
            padx=10,
            pady=5,
            cursor="hand2",
        )
        self.pause_audio_button.grid(row=0, column=5, padx=(6, 0))
        self.stop_audio_button = tk.Button(
            actions,
            text="STOP",
            command=self.stop_audio,
            state="disabled",
            background="#c62828",
            foreground="#ffffff",
            activebackground="#8e0000",
            activeforeground="#ffffff",
            disabledforeground="#ffd7d7",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI Semibold", 10),
            padx=12,
            pady=5,
            cursor="hand2",
        )
        self.stop_audio_button.grid(row=0, column=6, padx=(6, 0))
        actions.columnconfigure(1, weight=1)

        split = ttk.Panedwindow(self.tts_tab, orient="horizontal")
        split.pack(fill="both", expand=True)
        input_panel = ttk.LabelFrame(split, text="Thông tin đầu vào", padding=10)
        settings_host = ttk.LabelFrame(split, text="Thông số cài đặt", padding=(8, 6))
        split.add(input_panel, weight=3)
        split.add(settings_host, weight=2)

        input_toolbar = ttk.Frame(input_panel)
        input_toolbar.pack(fill="x", pady=(0, 7))
        ttk.Label(input_toolbar, text="Nhập, dán văn bản hoặc tải file TXT/SRT.").pack(side="left")
        ttk.Button(input_toolbar, text="Đọc file TXT / SRT", command=self.load_text_or_srt).pack(side="right")
        ttk.Button(input_toolbar, text="Dán clipboard", command=self.paste_content).pack(side="right", padx=(0, 7))
        title_row = ttk.Frame(input_panel)
        title_row.pack(fill="x", pady=(0, 7))
        ttk.Label(title_row, text="Tiêu đề / tên file:").pack(side="left", padx=(0, 8))
        self.output_title_entry = ttk.Entry(title_row, textvariable=self.output_title)
        self.output_title_entry.pack(side="left", fill="x", expand=True)
        text_frame = ttk.Frame(input_panel)
        text_frame.pack(fill="both", expand=True)
        self.text_input = tk.Text(
            text_frame,
            height=12,
            wrap="word",
            font=("Segoe UI", 11),
            undo=True,
            relief="solid",
            borderwidth=1,
            padx=9,
            pady=8,
        )
        text_scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.text_input.yview)
        self.text_input.configure(yscrollcommand=text_scroll.set)
        self.text_input.pack(side="left", fill="both", expand=True)
        text_scroll.pack(side="right", fill="y")
        self.text_input.bind("<KeyRelease>", self._update_character_counter)
        self.char_label = ttk.Label(
            input_panel,
            textvariable=self.character_counter,
            style="Header.TLabel",
            anchor="w",
            justify="left",
        )
        # Reserve a strip below the expanding editor so the counter cannot be
        # pushed out of view when the application is used on a short screen.
        self.char_label.pack(side="bottom", fill="x", pady=(7, 0), before=text_frame)
        input_panel.bind(
            "<Configure>",
            lambda event: self.char_label.configure(wraplength=max(240, event.width - 24)),
        )

        self.tts_canvas = tk.Canvas(settings_host, highlightthickness=0, borderwidth=0, background="#f3f3f3")
        settings_scroll = ttk.Scrollbar(settings_host, orient="vertical", command=self.tts_canvas.yview)
        self.tts_canvas.configure(yscrollcommand=settings_scroll.set)
        settings_scroll.pack(side="right", fill="y")
        self.tts_canvas.pack(side="left", fill="both", expand=True)
        self.tts_content = ttk.Frame(self.tts_canvas, padding=(2, 2, 8, 6))
        content_window = self.tts_canvas.create_window((0, 0), window=self.tts_content, anchor="nw")
        self.tts_content.bind("<Configure>", lambda _event: self.tts_canvas.configure(scrollregion=self.tts_canvas.bbox("all")))
        self.tts_canvas.bind("<Configure>", lambda event: self.tts_canvas.itemconfigure(content_window, width=event.width))
        self.bind_all("<MouseWheel>", self._on_tts_mousewheel, add="+")

        form = ttk.LabelFrame(self.tts_content, text="Giọng và đầu ra", padding=(9, 6))
        form.pack(fill="x", pady=(0, 7))
        ttk.Label(form, text="Nền tảng").grid(row=0, column=0, sticky="w", pady=3)
        engine_box = ttk.Combobox(form, textvariable=self.engine, values=["ElevenLabs chính thức"], state="readonly")
        self.engine.set("ElevenLabs chính thức")
        engine_box.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=3)
        ttk.Label(form, text="Voice ID").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=self.voice_id).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=3)
        ttk.Label(form, text="Model").grid(row=2, column=0, sticky="w", pady=3)
        self.model_box = ttk.Combobox(
            form,
            textvariable=self.model_id,
            values=["eleven_flash_v2_5", "eleven_multilingual_v2", "eleven_v3"],
            state="readonly",
        )
        self.model_box.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=3)
        ttk.Label(form, text="Định dạng").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Combobox(form, textvariable=self.output_format, values=OUTPUT_FORMATS, state="readonly").grid(
            row=3, column=1, sticky="ew", padx=(8, 0), pady=3
        )
        form.columnconfigure(1, weight=1)
        engine_box.bind("<<ComboboxSelected>>", self._engine_changed)
        self.model_box.bind("<<ComboboxSelected>>", self._model_changed)

        settings_box = ttk.LabelFrame(self.tts_content, text="Điều chỉnh giọng đọc", padding=(9, 6))
        settings_box.pack(fill="x")
        controls = [
            ("Tốc độ", self.speed, 0.7, 1.2, self.speed_value),
            ("Sự ổn định", self.stability, 0.0, 1.0, self.stability_value),
            ("Sự tương đồng", self.similarity_boost, 0.0, 1.0, self.similarity_value),
            ("Phong cách phóng đại", self.style_exaggeration, 0.0, 1.0, self.style_value),
        ]
        self.setting_scales: list[ttk.Scale] = []
        for index, (label, variable, minimum, maximum, display) in enumerate(controls):
            panel = ttk.Frame(settings_box)
            panel.grid(row=index, column=0, sticky="ew", pady=3)
            ttk.Label(panel, text=label, width=13, anchor="w").grid(row=0, column=0, sticky="w")
            scale = ttk.Scale(panel, variable=variable, from_=minimum, to=maximum, command=lambda _value: self._update_tts_setting_labels())
            scale.grid(row=0, column=1, sticky="ew", padx=8)
            ttk.Label(panel, textvariable=display, width=6, anchor="e").grid(row=0, column=2, sticky="e")
            panel.columnconfigure(1, weight=1)
            self.setting_scales.append(scale)
        settings_box.columnconfigure(0, weight=1)

        toggles = ttk.Frame(settings_box)
        toggles.grid(row=4, column=0, sticky="ew", pady=(7, 0))
        self.speaker_boost_check = ttk.Checkbutton(toggles, text="Tăng cường loa", variable=self.speaker_boost)
        self.speaker_boost_check.grid(row=0, column=0, sticky="w")
        self.language_override_check = ttk.Checkbutton(
            toggles,
            text="Ghi đè ngôn ngữ",
            variable=self.language_override,
            command=self._language_override_changed,
        )
        self.language_override_check.grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.language_box = ttk.Combobox(
            toggles,
            textvariable=self.language_code,
            values=["vi", "en", "es", "fr", "de", "it", "pt", "pl", "nl", "ja", "ko", "zh", "hi", "id", "tr", "ru", "ar"],
            width=8,
        )
        self.language_box.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(5, 0))
        bottom_options = ttk.Frame(settings_box)
        bottom_options.grid(row=5, column=0, sticky="ew", pady=(7, 0))
        ttk.Checkbutton(bottom_options, text="Tạo file SRT khớp giọng", variable=self.create_srt).pack(side="left")
        ttk.Button(bottom_options, text="Đặt lại", command=self._reset_tts_settings).pack(side="right")
        self.tts_model_note = ttk.Label(
            settings_box,
            text="Khuyến nghị: ổn định 0.50 • tương đồng 0.75 • phong cách 0.00",
            wraplength=520,
            justify="left",
        )
        self.tts_model_note.grid(row=6, column=0, sticky="ew", pady=(7, 0))
        settings_box.bind(
            "<Configure>",
            lambda event: self.tts_model_note.configure(wraplength=max(180, event.width - 24)),
            add="+",
        )
        self._update_tts_setting_labels()
        self._language_override_changed()
        self._model_changed()

    def _build_clone(self) -> None:
        pick = ttk.LabelFrame(self.clone_tab, text="Thông tin giọng cần clone", padding=(10, 7))
        pick.pack(fill="x")
        ttk.Label(pick, text="Tên giọng").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(pick, textvariable=self.clone_name).grid(row=0, column=1, sticky="ew", padx=(0, 12))
        ttk.Label(pick, text="Giới tính").grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Combobox(
            pick,
            textvariable=self.clone_gender,
            values=["Nữ", "Nam", "Khác"],
            state="readonly",
            width=12,
        ).grid(row=0, column=3, sticky="ew")
        ttk.Label(pick, text="Giọng mẫu").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(7, 0))
        ttk.Entry(pick, textvariable=self.clone_file).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(0, 8), pady=(7, 0)
        )
        ttk.Button(pick, text="Chọn file", command=self.pick_clone_file).grid(
            row=1, column=3, sticky="ew", pady=(7, 0)
        )
        self.clone_button = ttk.Button(
            pick,
            text="TẠO GIỌNG CLONE",
            style="CompactAccent.TButton",
            command=self.clone_voice,
        )
        self.clone_button.grid(row=0, column=4, rowspan=2, sticky="ns", padx=(10, 0))
        pick.columnconfigure(1, weight=3)
        pick.columnconfigure(3, weight=1)
        clone_note = ttk.Label(
            self.clone_tab,
            text=(
                "Mẫu sạch, một người nói, không nhạc nền, tổng thời lượng dưới 2 phút. "
                "API key cần quyền can_use_instant_voice_cloning."
            ),
            justify="left",
        )
        clone_note.pack(fill="x", pady=(6, 6))
        self.clone_tab.bind(
            "<Configure>",
            lambda event: clone_note.configure(wraplength=max(360, event.width - 30)),
            add="+",
        )

        bar = ttk.Frame(self.clone_tab)
        bar.pack(fill="x")
        ttk.Label(bar, text="Giọng Instant Voice Clone", style="Header.TLabel").pack(side="left")
        self.refresh_clones_button = ttk.Button(bar, text="Làm mới", command=self.load_clones)
        self.refresh_clones_button.pack(side="right")
        self.delete_clone_button = ttk.Button(bar, text="Xóa giọng đã chọn", command=self.delete_clone)
        self.delete_clone_button.pack(side="right", padx=6)
        columns = ("id", "name", "tags", "status")
        clone_list = ttk.Frame(self.clone_tab)
        clone_list.pack(fill="both", expand=True, pady=(6, 0))
        self.clone_tree = ttk.Treeview(clone_list, columns=columns, show="headings", selectmode="browse")
        for key, title, width in [("id", "Voice ID", 190), ("name", "Tên", 220), ("tags", "Nhãn", 320), ("status", "Trạng thái", 100)]:
            self.clone_tree.heading(key, text=title)
            self.clone_tree.column(key, width=width, anchor="w")
        clone_scroll_y = ttk.Scrollbar(clone_list, orient="vertical", command=self.clone_tree.yview)
        clone_scroll_x = ttk.Scrollbar(clone_list, orient="horizontal", command=self.clone_tree.xview)
        self.clone_tree.configure(yscrollcommand=clone_scroll_y.set, xscrollcommand=clone_scroll_x.set)
        self.clone_tree.grid(row=0, column=0, sticky="nsew")
        clone_scroll_y.grid(row=0, column=1, sticky="ns")
        clone_scroll_x.grid(row=1, column=0, sticky="ew")
        clone_list.rowconfigure(0, weight=1)
        clone_list.columnconfigure(0, weight=1)
        self.clone_tree.bind("<Double-1>", lambda _e: self.use_selected_clone())

    def _build_voices(self) -> None:
        toolbar = ttk.Frame(self.voice_tab)
        toolbar.pack(fill="x")
        self.voice_source = tk.StringVar(value="ElevenLabs")
        ttk.Combobox(toolbar, textvariable=self.voice_source, values=["ElevenLabs"], state="readonly", width=16).pack(side="left")
        self.load_voices_button = ttk.Button(toolbar, text="Tải danh sách", command=self.load_voice_library)
        self.load_voices_button.pack(side="left", padx=8)
        ttk.Label(toolbar, textvariable=self.voice_count, style="Header.TLabel").pack(side="left", padx=(2, 10))
        self.preview_voice_button = ttk.Button(toolbar, text="▶ Nghe thử giọng", command=self.preview_selected_voice)
        self.preview_voice_button.pack(side="left", padx=(4, 0))
        self.stop_preview_button = ttk.Button(toolbar, text="■ Dừng", command=self.stop_voice_preview)
        self.stop_preview_button.pack(side="left", padx=(4, 0))
        self.select_voice_button = ttk.Button(toolbar, text="Chọn giọng để tạo voice", style="Accent.TButton", command=self.select_voice_for_tts)
        self.select_voice_button.pack(side="right")

        panes = ttk.Panedwindow(self.voice_tab, orient="vertical")
        panes.pack(fill="both", expand=True, pady=(10, 0))
        list_frame = ttk.Frame(panes)
        detail_frame = ttk.LabelFrame(panes, text="Thông tin đầy đủ giọng đang chọn", padding=8)
        panes.add(list_frame, weight=4)
        panes.add(detail_frame, weight=2)

        columns = ("id", "name", "category", "gender", "age", "language", "accent", "use_case", "description")
        self.voice_tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse")
        column_settings = [
            ("id", "Voice ID", 205),
            ("name", "Tên giọng", 280),
            ("category", "Loại", 100),
            ("gender", "Giới tính", 90),
            ("age", "Độ tuổi", 105),
            ("language", "Ngôn ngữ", 95),
            ("accent", "Accent", 120),
            ("use_case", "Mục đích", 190),
            ("description", "Mô tả", 360),
        ]
        for key, title, width in column_settings:
            self.voice_tree.heading(key, text=title)
            self.voice_tree.column(key, width=width, minwidth=70, anchor="w", stretch=False)
        voice_scroll_y = ttk.Scrollbar(list_frame, orient="vertical", command=self.voice_tree.yview)
        voice_scroll_x = ttk.Scrollbar(list_frame, orient="horizontal", command=self.voice_tree.xview)
        self.voice_tree.configure(yscrollcommand=voice_scroll_y.set, xscrollcommand=voice_scroll_x.set)
        self.voice_tree.grid(row=0, column=0, sticky="nsew")
        voice_scroll_y.grid(row=0, column=1, sticky="ns")
        voice_scroll_x.grid(row=1, column=0, sticky="ew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        detail_actions = ttk.Frame(detail_frame)
        detail_actions.pack(fill="x", pady=(0, 5))
        ttk.Label(detail_actions, text="Chọn một dòng để xem thông tin giọng dưới dạng văn bản dễ đọc.").pack(side="left")
        ttk.Button(detail_actions, text="Sao chép Voice ID", command=self.copy_selected_voice_id).pack(side="right")
        self.voice_detail = tk.Text(
            detail_frame,
            height=10,
            wrap="word",
            state="disabled",
            font=("Consolas", 10),
            foreground="#111111",
            background="#f7f9fc",
            relief="solid",
            borderwidth=1,
        )
        detail_scroll = ttk.Scrollbar(detail_frame, orient="vertical", command=self.voice_detail.yview)
        self.voice_detail.configure(yscrollcommand=detail_scroll.set)
        self.voice_detail.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        self.voice_tree.bind("<Double-1>", lambda _e: self.use_library_voice())
        self.voice_tree.bind("<<TreeviewSelect>>", self.show_selected_voice_details)

    def _build_api_keys(self) -> None:
        ttk.Label(self.api_tab, text="Danh sách API key", style="Header.TLabel").pack(anchor="w")
        ttk.Label(self.api_tab, text="Mỗi dòng nhập một API key. Tool xoay vòng lần lượt qua các key khi bắt đầu tác vụ mới.").pack(anchor="w", pady=(4, 8))

        panes = ttk.Panedwindow(self.api_tab, orient="vertical")
        panes.pack(fill="both", expand=True)
        key_frame = ttk.Frame(panes)
        result_frame = ttk.Frame(panes)
        panes.add(key_frame, weight=3)
        panes.add(result_frame, weight=2)

        self.api_text = tk.Text(
            key_frame,
            height=8,
            wrap="none",
            font=("Consolas", 11),
            foreground="#111111",
            background="#ffffff",
            insertbackground="#111111",
            relief="solid",
            borderwidth=1,
        )
        api_scroll_y = ttk.Scrollbar(key_frame, orient="vertical", command=self.api_text.yview)
        api_scroll_x = ttk.Scrollbar(key_frame, orient="horizontal", command=self.api_text.xview)
        self.api_text.configure(yscrollcommand=api_scroll_y.set, xscrollcommand=api_scroll_x.set)
        self.api_text.grid(row=0, column=0, sticky="nsew")
        api_scroll_y.grid(row=0, column=1, sticky="ns")
        api_scroll_x.grid(row=1, column=0, sticky="ew")
        key_frame.rowconfigure(0, weight=1)
        key_frame.columnconfigure(0, weight=1)
        self.api_text.insert("1.0", "\n".join(self.api_keys))
        self.api_text.bind("<KeyRelease>", self._on_api_list_changed)

        controls = ttk.Frame(result_frame)
        controls.pack(fill="x", pady=(6, 0))
        ttk.Button(controls, text="Xóa danh sách", command=self._clear_api_keys).pack(side="left")
        ttk.Button(controls, text="Xuất key lỗi", command=self.export_bad_api_keys).pack(side="left", padx=(6, 0))
        ttk.Button(controls, text="Xóa key vô hiệu", command=self.remove_disabled_api_keys).pack(side="left", padx=(6, 0))
        self.check_all_button = ttk.Button(controls, text="Kiểm tra tất cả API", command=self.check_all_apis)
        self.check_all_button.pack(side="right", padx=(8, 0))
        ttk.Button(controls, text="LƯU DANH SÁCH API", style="CompactAccent.TButton", command=self.save_settings).pack(side="right")

        summary = ttk.LabelFrame(result_frame, text="Bộ đếm tổng toàn bộ API", padding=(10, 6))
        summary.pack(fill="x", pady=(6, 0))
        self.api_summary_label = ttk.Label(
            summary,
            textvariable=self.api_summary,
            style="Header.TLabel",
            justify="left",
            anchor="w",
        )
        self.api_summary_label.pack(fill="x")
        summary.bind(
            "<Configure>",
            lambda event: self.api_summary_label.configure(wraplength=max(300, event.width - 24)),
        )

        result_list = ttk.Frame(result_frame)
        result_list.pack(fill="both", expand=True, pady=(6, 0))
        self.api_result = tk.Text(
            result_list,
            height=4,
            wrap="none",
            state="disabled",
            font=("Consolas", 10),
            foreground="#111111",
            background="#f5f7fa",
        )
        result_scroll_y = ttk.Scrollbar(result_list, orient="vertical", command=self.api_result.yview)
        result_scroll_x = ttk.Scrollbar(result_list, orient="horizontal", command=self.api_result.xview)
        self.api_result.configure(yscrollcommand=result_scroll_y.set, xscrollcommand=result_scroll_x.set)
        self.api_result.grid(row=0, column=0, sticky="nsew")
        result_scroll_y.grid(row=0, column=1, sticky="ns")
        result_scroll_x.grid(row=1, column=0, sticky="ew")
        result_list.rowconfigure(0, weight=1)
        result_list.columnconfigure(0, weight=1)

    def _client(self) -> ElevenLabsClient:
        return self._clients_for_operation()[0]

    def _clients_for_operation(self, required_credit: int = 0) -> list[ElevenLabsClient]:
        keys = self._keys_from_editor()
        if not keys:
            raise ApiError("Bạn chưa nhập API key trong tab Danh sách API.")
        now = time.time()
        usable: list[str] = []
        for key in keys:
            state = self.api_states.get(key, {})
            if state.get("status") in {"disabled", "unauthorized"}:
                continue
            if self.api_backoff_until.get(key, 0) > now:
                continue
            remaining = state.get("remaining")
            if required_credit and isinstance(remaining, (int, float)) and remaining < required_credit:
                continue
            usable.append(key)
        if not usable:
            raise ApiError("Không còn API khả dụng. Hãy kiểm tra danh sách API hoặc xuất danh sách key lỗi để thay thế.")
        start = self.api_index % len(usable)
        ordered_keys = usable[start:] + usable[:start]
        self.api_index = (self.api_index + 1) % len(usable)
        self.status.set(f"Bắt đầu bằng API khả dụng {start + 1}/{len(usable)}")
        base_url = self.base_url.get().strip()
        return [ElevenLabsClient(key, base_url) for key in ordered_keys]

    def _keys_from_editor(self) -> list[str]:
        raw = self.api_text.get("1.0", "end-1c") if hasattr(self, "api_text") else "\n".join(self.api_keys)
        keys: list[str] = []
        for line in raw.splitlines():
            # Lines may include quota details appended after a check.
            # Keep only the original key before the first separator.
            key = line.split("|", 1)[0].strip().strip(",;")
            if key and key not in keys:
                keys.append(key)
        return keys

    def _refresh_api_editor_balances(self) -> None:
        """Render the latest per-key balance without triggering the editor change handler."""
        if not hasattr(self, "api_text"):
            return
        lines: list[str] = []
        for key in self._keys_from_editor():
            state = self.api_states.get(key, {})
            status = str(state.get("status") or "unknown")
            if status == "ok":
                remaining = state.get("remaining")
                remaining_text = f"{int(remaining):,}" if isinstance(remaining, (int, float)) else "--"
                used = state.get("used")
                limit = state.get("limit")
                used_text = f"{int(used):,}" if isinstance(used, (int, float)) else "--"
                limit_text = f"{int(limit):,}" if isinstance(limit, (int, float)) else "--"
                tier = str(state.get("tier") or "--")
                ivc = "Có" if state.get("ivc") else "Không"
                lines.append(
                    f"{key} | OK | Gói: {tier} | Còn lại: {remaining_text} ký tự | "
                    f"Đã dùng: {used_text}/{limit_text} | Clone: {ivc}"
                )
            else:
                label = {
                    "disabled": "VÔ HIỆU",
                    "rate_limited": "CHỜ 429",
                    "quota": "HẾT CREDIT",
                    "unauthorized": "SAI QUYỀN",
                }.get(status, "CHƯA KIỂM TRA" if status == "unknown" else "LỖI")
                reason = str(state.get("reason") or "")
                lines.append(f"{key} | {label}" + (f" | {reason[:140]}" if reason else ""))
        self.api_text.delete("1.0", "end")
        self.api_text.insert("1.0", "\n".join(lines))

    def _sync_credit_ui_from_states(self, verified: bool) -> int:
        balances = [
            int(state["remaining"])
            for state in self.api_states.values()
            if state.get("status") == "ok" and isinstance(state.get("remaining"), (int, float))
        ]
        total = sum(balances)
        suffix = "đã xác minh" if verified else "ước tính"
        self.total_available_characters = total
        self.total_credits.set(f"Tổng credit: {total:,} ({suffix})")
        self._refresh_api_editor_balances()
        self._update_character_counter()
        return total

    def _on_api_list_changed(self, _event: Any = None) -> None:
        current_keys = self._keys_from_editor()
        count = len(current_keys)
        self.api_states = {key: value for key, value in self.api_states.items() if key in current_keys}
        self.bad_api_records = {key: value for key, value in self.bad_api_records.items() if key in current_keys}
        self.api_count.set(f"{count} API key")
        self.total_credits.set("Tổng credit: cần kiểm tra lại")
        self.api_health_summary.set("Hợp lệ: -- | IVC: --")
        self.api_summary.set(f"Đang có {count} API key — bấm Kiểm tra tất cả API để tính tổng")
        self.total_available_characters = None
        self._update_character_counter()

    def _clear_api_keys(self) -> None:
        self.api_text.delete("1.0", "end")
        self._on_api_list_changed()

    def export_bad_api_keys(self) -> None:
        if not self.bad_api_records:
            messagebox.showinfo("Không có key lỗi", "Chưa phát hiện API lỗi. Hãy kiểm tra toàn bộ API trước.")
            return
        default_name = f"api_loi_{datetime.now():%Y%m%d_%H%M%S}.txt"
        path = filedialog.asksaveasfilename(
            title="Xuất danh sách API lỗi",
            initialdir=self.output_dir.get().strip() or str(OUTPUT_DIR),
            initialfile=default_name,
            defaultextension=".txt",
            filetypes=[("Text", "*.txt")],
        )
        if not path:
            return
        lines = []
        for key, record in self.bad_api_records.items():
            lines.append(f"{key} | {record.get('status', 'error')} | {record.get('reason', '')}")
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
        self._notify(f"Đã xuất {len(lines)} API lỗi: {path}", "success")

    def remove_disabled_api_keys(self) -> None:
        disabled = {
            key for key, state in self.api_states.items() if state.get("status") in {"disabled", "unauthorized"}
        }
        if not disabled:
            messagebox.showinfo("Không có key vô hiệu", "Chưa phát hiện key bị khóa hoặc không còn quyền truy cập.")
            return
        if not messagebox.askyesno(
            "Xóa key vô hiệu",
            f"Xóa {len(disabled)} key bị khóa/không có quyền khỏi danh sách?\nBạn nên xuất key lỗi trước nếu cần lưu lại.",
        ):
            return
        remaining = [key for key in self._keys_from_editor() if key not in disabled]
        self.api_text.delete("1.0", "end")
        self.api_text.insert("1.0", "\n".join(remaining))
        self.api_keys = remaining
        self._on_api_list_changed()
        self._persist_settings()
        self._notify(f"Đã xóa {len(disabled)} key vô hiệu; còn {len(remaining)} key.", "success")

    def _update_character_counter(self, _event: Any = None) -> None:
        count = len(self.text_input.get("1.0", "end-1c")) if hasattr(self, "text_input") else 0
        if self.total_available_characters is None:
            self.character_counter.set(f"Nội dung: {count:,} ký tự | Tổng toàn bộ API: chưa kiểm tra")
            return
        remaining = self.total_available_characters - count
        if remaining >= 0:
            self.character_counter.set(
                f"Nội dung: {count:,} ký tự | Tổng API: {self.total_available_characters:,} | Còn lại dự kiến: {remaining:,}"
            )
        else:
            self.character_counter.set(
                f"Nội dung: {count:,} ký tự | Tổng API: {self.total_available_characters:,} | Vượt: {abs(remaining):,}"
            )

    def _clear_text_input(self) -> None:
        self.text_input.delete("1.0", "end")
        self._update_character_counter()

    def _start_audio_task_controls(self) -> None:
        self.audio_pause_event.clear()
        self.audio_stop_event.clear()
        self.audio_pause_had_real_progress = False
        self.pause_audio_button.configure(
            state="normal",
            text="TẠM DỪNG",
            background="#f9a825",
            activebackground="#fbc02d",
            foreground="#1f1a00",
        )
        self.stop_audio_button.configure(state="normal")

    def _finish_audio_task_controls(self) -> None:
        self.audio_pause_event.clear()
        self.audio_stop_event.clear()
        self.pause_audio_button.configure(
            state="disabled",
            text="TẠM DỪNG",
            background="#f9a825",
            activebackground="#fbc02d",
            foreground="#1f1a00",
        )
        self.stop_audio_button.configure(state="disabled")

    def toggle_pause_audio(self) -> None:
        if str(self.pause_audio_button.cget("state")) == "disabled":
            return
        if self.audio_pause_event.is_set():
            self.audio_pause_event.clear()
            self.pause_audio_button.configure(
                text="TẠM DỪNG",
                background="#f9a825",
                activebackground="#fbc02d",
                foreground="#1f1a00",
            )
            if not self.audio_pause_had_real_progress:
                self.progress_has_real_value = False
                self.after(250, self._smooth_progress_tick)
            self._notify("Đã tiếp tục tác vụ tạo audio.", "running")
        else:
            self.audio_pause_had_real_progress = self.progress_has_real_value
            self.progress_has_real_value = True
            self.audio_pause_event.set()
            self.pause_audio_button.configure(
                text="TIẾP TỤC",
                background="#2e7d32",
                activebackground="#1b5e20",
                foreground="#ffffff",
            )
            self._notify("Đang tạm dừng; tool sẽ dừng trước phần tiếp theo.", "warning")

    def stop_audio(self) -> None:
        if str(self.stop_audio_button.cget("state")) == "disabled":
            return
        self.audio_stop_event.set()
        self.audio_pause_event.clear()
        self.progress_has_real_value = True
        self.pause_audio_button.configure(
            state="disabled",
            text="TẠM DỪNG",
            background="#f9a825",
            activebackground="#fbc02d",
            foreground="#1f1a00",
        )
        self.stop_audio_button.configure(state="disabled")
        self._notify("Đang dừng tác vụ sau request hiện tại...", "warning")

    def _on_tts_mousewheel(self, event: tk.Event) -> str | None:
        if not hasattr(self, "tts_content") or self.tabs.select() != str(self.tts_tab):
            return None
        if event.widget == self.text_input:
            return None
        widget: Any = event.widget
        inside_tts = False
        while widget is not None:
            if widget == self.tts_content:
                inside_tts = True
                break
            widget = getattr(widget, "master", None)
        if not inside_tts or not event.delta:
            return None
        self.tts_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def paste_content(self) -> None:
        try:
            content = self.clipboard_get()
        except tk.TclError:
            self._notify("Clipboard không có nội dung văn bản.", "warning")
            return
        try:
            self.text_input.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        self.text_input.insert("insert", content)
        self.text_input.focus_set()
        self._update_character_counter()
        self._notify(f"Đã dán {len(content):,} ký tự vào nội dung.", "success")

    def load_text_or_srt(self) -> None:
        selected = filedialog.askopenfilename(
            title="Chọn file nội dung TXT hoặc SRT",
            filetypes=[("Nội dung và phụ đề", "*.txt *.srt"), ("File TXT", "*.txt"), ("File SRT", "*.srt"), ("Tất cả", "*.*")],
        )
        if not selected:
            return
        path = Path(selected)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            messagebox.showerror("Không đọc được file", str(exc))
            return
        content = ""
        for encoding in ("utf-8-sig", "utf-8", "cp1258", "cp1252"):
            try:
                content = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not content:
            content = raw.decode("utf-8", errors="replace")
        if path.suffix.lower() == ".srt":
            content = srt_to_plain_text(content)
        content = content.strip()
        if not content:
            messagebox.showwarning("File trống", "Không tìm thấy nội dung có thể đọc trong file đã chọn.")
            return
        self.text_input.delete("1.0", "end")
        self.text_input.insert("1.0", content)
        if not self.output_title.get().strip():
            self.output_title.set(path.stem)
        self.text_input.focus_set()
        self._update_character_counter()
        self.text_input.see("1.0")
        kind = "phụ đề SRT" if path.suffix.lower() == ".srt" else "văn bản"
        self._notify(f"Đã đọc {kind}: {path.name} • {len(content):,} ký tự.", "success")

    def _persist_settings(self) -> None:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(
                {
                    "api_keys": self._keys_from_editor(),
                    "disabled_api_keys": [
                        key for key, state in self.api_states.items() if state.get("status") == "disabled"
                    ],
                    "base_url": self.base_url.get().strip(),
                    "output_dir": self.output_dir.get().strip(),
                    "tts_output_format": self.output_format.get().strip(),
                    "tts_speed": self.speed.get(),
                    "tts_stability": self.stability.get(),
                    "tts_similarity_boost": self.similarity_boost.get(),
                    "tts_style": self.style_exaggeration.get(),
                    "tts_speaker_boost": self.speaker_boost.get(),
                    "tts_language_override": self.language_override.get(),
                    "tts_language_code": self.language_code.get().strip(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def choose_output_dir(self) -> None:
        current = self.output_dir.get().strip()
        initial = current if current and Path(current).is_dir() else str(ROOT)
        selected = filedialog.askdirectory(title="Chọn vị trí lưu audio và SRT", initialdir=initial, mustexist=True)
        if not selected:
            return
        target = Path(selected)
        target.mkdir(parents=True, exist_ok=True)
        self.output_dir.set(str(target))
        self._persist_settings()
        self._notify(f"Đã chọn vị trí lưu: {target}", "success")

    def open_output_dir(self) -> None:
        target = Path(self.output_dir.get().strip() or OUTPUT_DIR)
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(target)

    def open_last_created_dir(self) -> None:
        if self.last_created_dir is None or not self.last_created_dir.is_dir():
            self._notify("Chưa có thư mục kết quả mới được tạo.", "warning")
            return
        os.startfile(self.last_created_dir)
        self._notify(f"Đã mở thư mục kết quả: {self.last_created_dir}", "info")

    def _notify(self, message: str, level: str = "info") -> None:
        colors = {
            "info": ("#e8f1fb", "#123b63", "#9bbfe5", "●"),
            "running": ("#fff4d6", "#6b4700", "#e5be55", "●"),
            "success": ("#e3f6e8", "#185b2b", "#78c48c", "✓"),
            "error": ("#fde8e8", "#8a1c1c", "#e59a9a", "✕"),
            "warning": ("#fff0df", "#784000", "#e8b16f", "!"),
        }
        background, foreground, border, icon = colors.get(level, colors["info"])
        self.notice_text.set(f"{icon}  {message}")
        self.notice_bar.configure(background=background, highlightbackground=border)
        self.notice_label.configure(background=background, foreground=foreground)
        self.status.set(message)

    def _start_button_effect(self, button: ttk.Button | tk.Button | None, text: str) -> None:
        if button is None:
            return
        original = str(button.cget("text"))
        self.button_jobs[button] = (original, text, 0)
        button.configure(state="disabled", text=f"{text}.")
        if len(self.button_jobs) == 1:
            self.after(350, self._animate_buttons)

    def _animate_buttons(self) -> None:
        if not self.button_jobs:
            return
        for button, (original, running, frame) in list(self.button_jobs.items()):
            if button.winfo_exists():
                next_frame = (frame + 1) % 4
                button.configure(text=f"{running}{'.' * (next_frame + 1)}")
                self.button_jobs[button] = (original, running, next_frame)
        self.after(350, self._animate_buttons)

    def _finish_button_effect(self, button: ttk.Button | tk.Button | None) -> None:
        if button is None:
            return
        job = self.button_jobs.pop(button, None)
        if job and button.winfo_exists():
            button.configure(state="normal", text=job[0])

    def _reset_progress(self) -> None:
        if self.busy_count == 0:
            self.progress.configure(mode="determinate", value=0)
            self.progress_text.set("0%")

    def _smooth_progress_tick(self) -> None:
        if self.busy_count == 0 or self.progress_has_real_value:
            return
        current = float(self.progress.cget("value"))
        increment = max(0.7, (90.0 - current) * 0.045)
        value = min(90.0, current + increment)
        self.progress.configure(mode="determinate", value=value)
        self.progress_text.set(f"{round(value)}%")
        self.after(250, self._smooth_progress_tick)

    def _run(
        self,
        work: Callable[[], Any],
        done: Callable[[Any], None] | None = None,
        button: ttk.Button | tk.Button | None = None,
        running_text: str = "Đang chạy",
    ) -> None:
        was_idle = self.busy_count == 0
        self.busy_count += 1
        if was_idle:
            self.progress_has_real_value = False
            self.progress_completion_shown = False
            self.progress.configure(mode="determinate", value=3)
            self.progress_text.set("3%")
            self.after(250, self._smooth_progress_tick)
        self._start_button_effect(button, running_text)

        def target() -> None:
            try:
                self.events.put(("done", (done, work(), button)))
            except TaskCancelled as exc:
                self.events.put(("cancelled", (exc, button)))
            except Exception as exc:
                self.events.put(("error", (exc, button)))

        threading.Thread(target=target, daemon=True).start()

    def _drain_events(self) -> None:
        terminal_processed = False
        cancelled_processed = False
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind in {"done", "error", "cancelled"}:
                    self.busy_count = max(0, self.busy_count - 1)
                    terminal_processed = True
                    terminal_button = payload[1] if kind in {"error", "cancelled"} else payload[2]
                    if terminal_button is getattr(self, "create_audio_button", None):
                        self._finish_audio_task_controls()
                if kind == "error":
                    error, button = payload
                    self._finish_button_effect(button)
                    self._notify(str(error), "error")
                    if button is getattr(self, "create_audio_button", None):
                        self._sync_credit_ui_from_states(verified=False)
                    try:
                        self._persist_settings()
                    except OSError:
                        pass
                    messagebox.showerror("Lỗi", str(error))
                elif kind == "cancelled":
                    cancellation, button = payload
                    cancelled_processed = True
                    self._finish_button_effect(button)
                    self._sync_credit_ui_from_states(
                        verified=bool(getattr(cancellation, "balances_verified", False))
                    )
                    try:
                        self._persist_settings()
                    except OSError:
                        pass
                    self.status.set("Đã dừng tác vụ")
                    self._notify(str(cancellation) or "Đã dừng tác vụ tạo audio.", "warning")
                elif kind == "done":
                    callback, result, button = payload
                    self._finish_button_effect(button)
                    if callback:
                        callback(result)
                    self._notify(self.status.get() if self.status.get().startswith("Hoàn thành") else "Tác vụ đã hoàn thành.", "success")
                elif kind == "progress":
                    self._notify(str(payload), "running")
                elif kind == "progress_value":
                    percent, message = payload
                    self.progress_has_real_value = True
                    self.progress.configure(mode="determinate", value=percent)
                    self.progress_text.set(f"{percent}%")
                    self._notify(message, "running")
        except queue.Empty:
            pass
        if cancelled_processed and self.busy_count == 0:
            self.progress_completion_shown = True
            self.progress.configure(mode="determinate", value=0)
            self.progress_text.set("0%")
        elif terminal_processed and self.busy_count == 0 and not self.progress_completion_shown:
            self.progress_completion_shown = True
            self.progress.configure(mode="determinate", value=100)
            self.progress_text.set("100%")
            self.after(1800, self._reset_progress)
        self.after(100, self._drain_events)

    def _load_settings(self) -> dict[str, Any]:
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_settings(self) -> None:
        self.api_keys = self._keys_from_editor()
        self.api_count.set(f"{len(self.api_keys)} API key")
        if self.api_keys:
            self.api_index %= len(self.api_keys)
        else:
            self.api_index = 0
        self._persist_settings()
        self._notify(f"Đã lưu {len(self.api_keys)} API key.", "success")
        messagebox.showinfo("Đã lưu", f"Đã lưu {len(self.api_keys)} API key. Tool sẽ xoay vòng theo thứ tự từ trên xuống.")

    def check_api(self) -> None:
        self._notify("Đang kiểm tra kết nối API...", "running")
        try:
            client = self._client()
        except ApiError as exc:
            messagebox.showerror("Lỗi", str(exc))
            return
        def done(result: tuple[Any, Any]) -> None:
            credits, health = result
            value = credits.get("credits", "--") if isinstance(credits, dict) else "--"
            self.credits.set(f"API vừa kiểm tra: {value:,}" if isinstance(value, (int, float)) else f"API vừa kiểm tra: {value}")
            messagebox.showinfo("Kết nối thành công", "API hoạt động.\n" + json.dumps(health, ensure_ascii=False, indent=2))
        self._run(lambda: (client.credits(), client.health()), done, self.check_api_button, "Đang kiểm tra")

    def _auto_check_apis(self) -> None:
        """Check the saved API list once after the main window is ready."""
        if not self.winfo_exists() or not self._keys_from_editor():
            return
        if self.busy_count:
            self.after(500, self._auto_check_apis)
            return
        self._notify("Tự động kiểm tra danh sách API khi khởi động...", "running")
        self.check_all_apis()

    def check_all_apis(self) -> None:
        keys = self._keys_from_editor()
        base_url = self.base_url.get().strip()
        if not keys:
            messagebox.showwarning("Chưa có API", "Hãy nhập ít nhất một API key.")
            return
        self._notify(f"Đang kiểm tra 0/{len(keys)} API key...", "running")

        def work() -> dict[str, Any]:
            lines: list[str] = []
            annotated_lines: list[str] = []
            states: dict[str, dict[str, Any]] = {}
            bad_records: dict[str, dict[str, str]] = {}
            valid = 0
            total_remaining = 0
            total_used = 0
            total_limit = 0
            ivc_allowed = 0
            for index, key in enumerate(keys, 1):
                masked = f"{key[:5]}…{key[-4:]}" if len(key) > 10 else "••••••"
                previous = self.api_states.get(key, {})
                if previous.get("status") == "disabled":
                    reason = str(previous.get("reason") or "Đã bị ElevenLabs vô hiệu hóa ở lần kiểm tra trước.")
                    states[key] = {"status": "disabled", "remaining": None, "reason": reason}
                    bad_records[key] = {"status": "disabled", "reason": reason}
                    lines.append(f"API {index:02d}  {masked:<18}  BỎ QUA | Key đã bị vô hiệu hóa")
                    annotated_lines.append(f"{key} | VÔ HIỆU | {reason[:140]}")
                    percent = round(index * 100 / len(keys))
                    self.events.put(("progress_value", (percent, f"Đang kiểm tra API {index}/{len(keys)}...")))
                    continue
                try:
                    result = ElevenLabsClient(key, base_url).credits()
                    credit = result.get("credits", "--") if isinstance(result, dict) else "--"
                    raw = result.get("raw", {}) if isinstance(result, dict) else {}
                    ivc = "Có" if raw.get("can_use_instant_voice_cloning") else "Không"
                    tier = str(raw.get("tier") or result.get("tier") or "--")
                    used = raw.get("voice_slots_used", "--")
                    limit = raw.get("voice_limit", "--")
                    lines.append(
                        f"API {index:02d}  {masked:<18}  OK | Tier: {tier} | Credit: {credit} | IVC: {ivc} | Voice: {used}/{limit}"
                    )
                    valid += 1
                    if isinstance(credit, (int, float)):
                        total_remaining += int(credit)
                    character_used = raw.get("character_count")
                    character_limit = raw.get("character_limit")
                    remaining_text = f"{int(credit):,}" if isinstance(credit, (int, float)) else str(credit)
                    used_text = f"{int(character_used):,}" if isinstance(character_used, (int, float)) else "--"
                    limit_text = f"{int(character_limit):,}" if isinstance(character_limit, (int, float)) else "--"
                    annotated_lines.append(
                        f"{key} | OK | Gói: {tier} | Còn lại: {remaining_text} ký tự | Đã dùng: {used_text}/{limit_text} | Clone: {ivc}"
                    )
                    states[key] = {
                        "status": "ok",
                        "remaining": int(credit) if isinstance(credit, (int, float)) else None,
                        "used": int(character_used) if isinstance(character_used, (int, float)) else None,
                        "limit": int(character_limit) if isinstance(character_limit, (int, float)) else None,
                        "tier": tier,
                        "ivc": bool(raw.get("can_use_instant_voice_cloning")),
                    }
                    if isinstance(character_used, (int, float)):
                        total_used += int(character_used)
                    if isinstance(character_limit, (int, float)):
                        total_limit += int(character_limit)
                    if raw.get("can_use_instant_voice_cloning"):
                        ivc_allowed += 1
                except Exception as exc:
                    reason = str(exc)
                    status = classify_api_error(reason)
                    status_label = {
                        "disabled": "VÔ HIỆU",
                        "rate_limited": "CHỜ 429",
                        "quota": "HẾT CREDIT",
                        "unauthorized": "SAI QUYỀN",
                    }.get(status, "LỖI")
                    lines.append(f"API {index:02d}  {masked:<18}  {status_label} | {reason[:120]}")
                    annotated_lines.append(f"{key} | {status_label} | {reason[:140]}")
                    states[key] = {"status": status, "remaining": 0 if status == "quota" else None, "reason": reason}
                    bad_records[key] = {"status": status, "reason": reason}
                percent = round(index * 100 / len(keys))
                self.events.put(("progress_value", (percent, f"Đang kiểm tra API {index}/{len(keys)}...")))
            return {
                "lines": lines,
                "annotated_lines": annotated_lines,
                "valid": valid,
                "invalid": len(keys) - valid,
                "total": len(keys),
                "remaining": total_remaining,
                "used": total_used,
                "limit": total_limit,
                "ivc": ivc_allowed,
                "states": states,
                "bad_records": bad_records,
            }

        def done(result: dict[str, Any]) -> None:
            lines = result["lines"]
            self.api_states = result["states"]
            self.bad_api_records = result["bad_records"]
            for key, state in self.api_states.items():
                if state.get("status") == "rate_limited":
                    self.api_backoff_until[key] = time.time() + 60
            self.api_text.delete("1.0", "end")
            self.api_text.insert("1.0", "\n".join(result["annotated_lines"]))
            self.api_result.configure(state="normal")
            self.api_result.delete("1.0", "end")
            self.api_result.insert("1.0", "\n".join(lines))
            self.api_result.configure(state="disabled")
            self.total_credits.set(f"Tổng credit: {result['remaining']:,}")
            self.total_available_characters = int(result["remaining"])
            self._update_character_counter()
            self.api_health_summary.set(f"Hợp lệ: {result['valid']}/{result['total']} | IVC: {result['ivc']}")
            self.api_summary.set(
                f"Tổng {result['total']} API  •  Hợp lệ {result['valid']}  •  Lỗi {result['invalid']}  •  "
                f"Còn lại {result['remaining']:,} credit  •  Đã dùng {result['used']:,}/{result['limit']:,}  •  "
                f"Có quyền clone {result['ivc']}"
            )
            self.status.set("Đã kiểm tra xong danh sách API")
            self._persist_settings()

        self._run(work, done, self.check_all_button, "Đang kiểm tra")

    def _engine_changed(self, _event: Any = None) -> None:
        if not self.model_id.get():
            self.model_id.set("eleven_flash_v2_5")

    def _update_tts_setting_labels(self) -> None:
        self.speed_value.set(f"{self.speed.get():.2f}")
        self.stability_value.set(f"{self.stability.get():.2f}")
        self.similarity_value.set(f"{self.similarity_boost.get():.2f}")
        self.style_value.set(f"{self.style_exaggeration.get():.2f}")

    def _language_override_changed(self) -> None:
        self.language_box.configure(state="normal" if self.language_override.get() else "disabled")

    def _model_changed(self, _event: Any = None) -> None:
        model = self.model_id.get().strip()
        is_v3 = model == "eleven_v3"
        is_multilingual_v2 = model == "eleven_multilingual_v2"
        state = "disabled" if is_v3 else "normal"
        self.setting_scales[0].configure(state=state)
        self.setting_scales[2].configure(state=state)
        self.speaker_boost_check.configure(state=state)
        self.language_override_check.configure(state="disabled" if is_multilingual_v2 else "normal")
        if is_multilingual_v2:
            self.language_box.configure(state="disabled")
        else:
            self._language_override_changed()
        if is_v3:
            self.tts_model_note.configure(
                text="Eleven v3: API không hỗ trợ tốc độ, tương đồng và tăng cường loa; tool sẽ không gửi các trường này."
            )
        elif is_multilingual_v2:
            self.tts_model_note.configure(
                text="Multilingual v2: API không hỗ trợ ghi đè language_code; ngôn ngữ được nhận diện từ nội dung."
            )
        else:
            self.tts_model_note.configure(text="Khuyến nghị: ổn định 0.50 • tương đồng 0.75 • phong cách 0.00")

    def _reset_tts_settings(self) -> None:
        self.speed.set(1.0)
        self.stability.set(0.5)
        self.similarity_boost.set(0.75)
        self.style_exaggeration.set(0.0)
        self.speaker_boost.set(True)
        self.language_override.set(False)
        self.language_code.set("vi")
        self.output_format.set("mp3_44100_128")
        self._update_tts_setting_labels()
        self._language_override_changed()
        self._notify("Đã đặt lại cấu hình giọng đọc theo giá trị khuyến nghị.", "info")

    def create_audio(self) -> None:
        text = self.text_input.get("1.0", "end-1c").strip()
        output_title = self.output_title.get().strip()
        voice = self.voice_id.get().strip()
        if not text or not voice:
            messagebox.showwarning("Thiếu dữ liệu", "Hãy nhập Voice ID và nội dung.")
            return
        model = self.model_id.get().strip()
        output_format = self.output_format.get().strip()
        speed = self.speed.get()
        stability = self.stability.get()
        similarity_boost = self.similarity_boost.get()
        style = self.style_exaggeration.get()
        use_speaker_boost = self.speaker_boost.get()
        language_code = (
            self.language_code.get().strip().lower()
            if self.language_override.get() and model != "eleven_multilingual_v2"
            else None
        )
        if not 0.7 <= speed <= 1.2:
            messagebox.showwarning("Tốc độ không hợp lệ", "Tốc độ phải nằm trong khoảng 0.70 đến 1.20.")
            return
        if any(not 0.0 <= value <= 1.0 for value in (stability, similarity_boost, style)):
            messagebox.showwarning("Thiết lập không hợp lệ", "Ổn định, tương đồng và phong cách phải nằm trong khoảng 0.00 đến 1.00.")
            return
        if language_code and not re.fullmatch(r"[a-z]{2}", language_code):
            messagebox.showwarning("Mã ngôn ngữ không hợp lệ", "Hãy nhập mã ISO 639-1 gồm hai chữ cái, ví dụ: vi, en, ja.")
            return
        with_srt = self.create_srt.get()
        output_directory = Path(self.output_dir.get().strip() or OUTPUT_DIR)
        try:
            output_directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Vị trí lưu không hợp lệ", f"Không thể tạo hoặc truy cập thư mục lưu:\n{exc}")
            return
        try:
            clients = self._clients_for_operation()
        except ApiError as exc:
            messagebox.showerror("Lỗi", str(exc))
            return
        self._notify("Đang gửi nội dung đến ElevenLabs để tạo audio...", "running")

        def work() -> dict[str, Any]:
            failures: list[str] = []
            states = {key: dict(value) for key, value in self.api_states.items()}
            bad_records = {key: dict(value) for key, value in self.bad_api_records.items()}
            backoff_keys: set[str] = set()
            candidates: list[dict[str, Any]] = []
            used_keys: set[str] = set()
            balances_verified = True

            def publish_runtime_state() -> None:
                # Plain dictionaries are safe to replace atomically; this also
                # preserves newly disabled keys when generation ends in error.
                self.api_states = {key: dict(value) for key, value in states.items()}
                self.bad_api_records = {key: dict(value) for key, value in bad_records.items()}
                now = time.time()
                for backoff_key in backoff_keys:
                    self.api_backoff_until[backoff_key] = now + 60

            def verify_used_balances() -> None:
                """Replace local estimates with balances reported by ElevenLabs."""
                nonlocal balances_verified
                clients_by_key = {client.api_key: client for client in clients}
                for used_key in used_keys:
                    client = clients_by_key.get(used_key)
                    if client is None:
                        balances_verified = False
                        continue
                    try:
                        credit_result = client.credits()
                        remaining_now = credit_result.get("credits")
                        raw = credit_result.get("raw", {}) if isinstance(credit_result, dict) else {}
                        if not isinstance(remaining_now, (int, float)):
                            balances_verified = False
                            continue
                        state = states.setdefault(used_key, {})
                        state.update(
                            {
                                "status": "ok",
                                "remaining": int(remaining_now),
                                "used": int(raw["character_count"])
                                if isinstance(raw.get("character_count"), (int, float))
                                else state.get("used"),
                                "limit": int(raw["character_limit"])
                                if isinstance(raw.get("character_limit"), (int, float))
                                else state.get("limit"),
                                "tier": str(raw.get("tier") or credit_result.get("tier") or state.get("tier") or "--"),
                                "ivc": bool(raw.get("can_use_instant_voice_cloning", state.get("ivc", False))),
                            }
                        )
                    except Exception:
                        # The request was already deducted locally. Keep the
                        # estimate, but do not present it as a verified value.
                        balances_verified = False

            def obey_audio_controls() -> None:
                while self.audio_pause_event.is_set() and not self.audio_stop_event.is_set():
                    time.sleep(0.1)
                if self.audio_stop_event.is_set():
                    verify_used_balances()
                    publish_runtime_state()
                    raise TaskCancelled(
                        "Đã dừng hẳn tác vụ tạo audio. Các phần tạm chưa ghép đã được hủy; "
                        "credit đã sử dụng vẫn được cập nhật.",
                        balances_verified=balances_verified,
                    )

            for client in clients:
                obey_audio_controls()
                key = client.api_key
                state = states.get(key, {})
                remaining = state.get("remaining")
                if not isinstance(remaining, (int, float)):
                    try:
                        credit_result = client.credits()
                        remaining = credit_result.get("credits")
                        raw = credit_result.get("raw", {}) if isinstance(credit_result, dict) else {}
                        state = {
                            "status": "ok",
                            "remaining": int(remaining) if isinstance(remaining, (int, float)) else None,
                            "used": int(raw["character_count"])
                            if isinstance(raw.get("character_count"), (int, float))
                            else None,
                            "limit": int(raw["character_limit"])
                            if isinstance(raw.get("character_limit"), (int, float))
                            else None,
                            "tier": str(raw.get("tier") or credit_result.get("tier") or "--"),
                            "ivc": bool(raw.get("can_use_instant_voice_cloning")),
                        }
                        states[key] = state
                    except ApiError as exc:
                        reason = str(exc)
                        status = classify_api_error(reason)
                        states[key] = {"status": status, "remaining": None, "reason": reason}
                        bad_records[key] = {"status": status, "reason": reason}
                        if status == "rate_limited":
                            backoff_keys.add(key)
                        failures.append(reason)
                        continue
                if isinstance(remaining, (int, float)) and int(remaining) > 50:
                    candidates.append({"client": client, "key": key, "remaining": int(remaining)})

            total_capacity = sum(max(0, item["remaining"] - 50) for item in candidates)
            if total_capacity < len(text):
                publish_runtime_state()
                raise ApiError(
                    f"Tổng credit khả dụng chỉ còn khoảng {total_capacity:,}, không đủ cho {len(text):,} ký tự.\n"
                    "Hãy bổ sung API hợp lệ/paid hoặc rút ngắn nội dung."
                )

            generated: list[tuple[bytes, dict[str, Any], str, str]] = []
            remaining_text = text
            cursor = 0
            while remaining_text:
                obey_audio_controls()
                usable = [item for item in candidates if item["remaining"] > 50]
                if not usable:
                    publish_runtime_state()
                    raise ApiError(summarize_tts_failures(failures, len(remaining_text)))
                selected = usable[cursor % len(usable)]
                # Keep each request comfortably below common Free Tier limits;
                # paid/workspace keys can still serve multiple consecutive parts.
                chunk_limit = min(9000, max(1, selected["remaining"] - 50))
                chunk, rest = take_text_chunk(remaining_text, chunk_limit)
                client = selected["client"]
                key = selected["key"]
                try:
                    if with_srt:
                        audio, alignment = client.create_speech_with_timestamps(
                            voice,
                            chunk,
                            model,
                            output_format,
                            speed,
                            stability,
                            similarity_boost,
                            style,
                            use_speaker_boost,
                            language_code,
                        )
                    else:
                        audio = client.create_speech(
                            voice,
                            chunk,
                            model,
                            output_format,
                            speed,
                            stability,
                            similarity_boost,
                            style,
                            use_speaker_boost,
                            language_code,
                        )
                        alignment = {}
                    generated.append((audio, alignment, chunk, key))
                    used_keys.add(key)
                    selected["remaining"] = max(0, selected["remaining"] - len(chunk))
                    states.setdefault(key, {})["status"] = "ok"
                    states[key]["remaining"] = selected["remaining"]
                    remaining_text = rest
                    cursor += 1
                    completed = len(text) - len(remaining_text)
                    percent = max(1, min(99, round(completed * 100 / len(text))))
                    self.events.put(
                        ("progress_value", (percent, f"Đã tạo {len(generated)} phần • {completed:,}/{len(text):,} ký tự"))
                    )
                    obey_audio_controls()
                except ApiError as exc:
                    message = str(exc)
                    failures.append(message)
                    status = classify_api_error(message)
                    if status in {"disabled", "unauthorized"}:
                        states[key] = {"status": status, "remaining": None, "reason": message}
                        bad_records[key] = {"status": status, "reason": message}
                    elif status == "rate_limited":
                        states[key] = {"status": status, "remaining": selected["remaining"], "reason": message}
                        bad_records[key] = {"status": status, "reason": message}
                        backoff_keys.add(key)
                    elif status == "quota":
                        states[key] = {"status": "quota", "remaining": 0, "reason": message}
                        bad_records[key] = {"status": "quota", "reason": message}
                    # Do not retry this key during the current operation.
                    selected["remaining"] = 0
                    obey_audio_controls()
                    if not any(item["remaining"] > 50 for item in candidates):
                        verify_used_balances()
                        publish_runtime_state()
                        raise ApiError(summarize_tts_failures(failures, len(remaining_text)))

            verify_used_balances()
            publish_runtime_state()

            reconstructed_text = "".join(item[2] for item in generated)
            if reconstructed_text != text:
                raise ApiError(
                    "Kiểm tra toàn vẹn thất bại: nội dung sau khi chia không khớp văn bản gốc. "
                    "Tool đã dừng trước khi xuất để tránh bỏ sót câu."
                )

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_voice = re.sub(r"[^A-Za-z0-9_-]", "_", voice)[:35]
            extension = output_extension(output_format)
            output_stem = safe_output_stem(output_title, f"{stamp}_{safe_voice}")
            destination = unique_output_path(output_directory, output_stem, extension, with_srt)
            srt_path = destination.with_suffix(".srt")
            files: list[Path] = [destination]
            if len(generated) == 1:
                obey_audio_controls()
                destination.write_bytes(generated[0][0])
                if with_srt:
                    srt_path.write_text(alignment_to_srt(generated[0][1]), encoding="utf-8-sig")
                    files.append(srt_path)
            else:
                self.events.put(("progress_value", (99, f"Đang ghép {len(generated)} phần thành một file hoàn chỉnh...")))
                with tempfile.TemporaryDirectory(prefix="voice11_merge_", dir=output_directory) as temp_name:
                    temp_dir = Path(temp_name)
                    part_paths: list[Path] = []
                    for index, (audio, _alignment, _chunk, _key) in enumerate(generated, 1):
                        obey_audio_controls()
                        part_path = temp_dir / f"part{index:03d}{extension}"
                        part_path.write_bytes(audio)
                        part_paths.append(part_path)
                    durations = [audio_duration_seconds(path, output_format) for path in part_paths] if with_srt else []
                    merge_audio_parts(part_paths, destination, output_format)
                    if with_srt:
                        merged_alignment = merge_alignment_segments(
                            [item[1] for item in generated],
                            durations,
                        )
                        srt_path.write_text(alignment_to_srt(merged_alignment), encoding="utf-8-sig")
                        files.append(srt_path)
            return {
                "files": files,
                "parts": len(generated),
                "states": states,
                "bad_records": bad_records,
                "backoff_keys": list(backoff_keys),
                "balances_verified": balances_verified,
            }

        def done(result: dict[str, Any]) -> None:
            files = result["files"]
            self.api_states = result["states"]
            self.bad_api_records = result["bad_records"]
            for key in result["backoff_keys"]:
                self.api_backoff_until[key] = time.time() + 60
            remaining_total = self._sync_credit_ui_from_states(bool(result["balances_verified"]))
            self._persist_settings()
            self.status.set("Hoàn thành tạo audio")
            self.last_created_dir = files[0].parent
            self.open_last_created_button.configure(state="normal")
            if result["parts"] > 1:
                message = (
                    f"Đã tạo {result['parts']} phần theo quota và tự ghép đúng thứ tự thành một file hoàn chỉnh:\n"
                    + "\n".join(str(path) for path in files)
                )
            else:
                message = "Đã tạo:\n" + "\n".join(str(path) for path in files)
            message += f"\n\nTổng số ký tự hiện còn của các API: {remaining_total:,}."
            if not result["balances_verified"]:
                message += " (Ước tính vì chưa xác minh lại được một hoặc nhiều API.)"
            messagebox.showinfo("Tạo audio thành công", message)
        self._start_audio_task_controls()
        self._run(work, done, self.create_audio_button, "Đang tạo audio")

    def pick_clone_file(self) -> None:
        path = filedialog.askopenfilename(title="Chọn audio mẫu", filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac *.flac"), ("Tất cả", "*.*")])
        if path:
            self.clone_file.set(path)

    def clone_voice(self) -> None:
        path = self.clone_file.get().strip()
        voice_name = self.clone_name.get().strip()
        gender_labels = {"Nữ": "female", "Nam": "male", "Khác": "other"}
        gender = gender_labels.get(self.clone_gender.get(), self.clone_gender.get())
        if not voice_name:
            messagebox.showwarning("Thiếu tên giọng", "Hãy điền tên cho giọng clone.")
            return
        if not path:
            messagebox.showwarning("Thiếu file", "Hãy chọn file audio mẫu.")
            return
        try:
            clients = self._clients_for_operation()
        except ApiError as exc:
            messagebox.showerror("Lỗi", str(exc))
            return
        self._notify("Đang kiểm tra quyền và tải giọng mẫu lên ElevenLabs...", "running")
        def done(voice: str) -> None:
            self.status.set("Hoàn thành clone voice")
            self.voice_id.set(voice)
            messagebox.showinfo("Clone thành công", f"Voice ID: {voice}\nĐã tự điền vào tab Tạo audio.")
            self.load_clones()
        def work() -> str:
            failures: list[str] = []
            for client in clients:
                try:
                    subscription = client.subscription()
                    if not subscription.get("can_use_instant_voice_cloning"):
                        failures.append("API key không có quyền Instant Voice Cloning")
                        continue
                    return client.clone_voice(path, voice_name, gender)
                except ApiError as exc:
                    message = str(exc)
                    if not any(code in message for code in ("HTTP 401", "HTTP 402", "HTTP 403", "HTTP 429")):
                        raise
                    failures.append(message)
            raise ApiError("Không API nào còn dùng được: " + " | ".join(failures))

        self._run(work, done, self.clone_button, "Đang clone")

    def load_clones(self) -> None:
        try:
            client = self._client()
        except ApiError as exc:
            messagebox.showerror("Lỗi", str(exc))
            return
        self._notify("Đang tải danh sách giọng clone...", "running")
        def done(rows: list[dict[str, Any]]) -> None:
            self.clone_tree.delete(*self.clone_tree.get_children())
            self.clone_rows.clear()
            for row in rows:
                voice = str(row.get("voice_id") or row.get("id") or "")
                labels = row.get("labels") or row.get("tag_list") or {}
                if isinstance(labels, dict):
                    labels = ", ".join(f"{key}: {value}" for key, value in labels.items())
                elif isinstance(labels, list):
                    labels = ", ".join(map(str, labels))
                item = self.clone_tree.insert("", "end", values=(voice, row.get("voice_name") or row.get("name") or "", labels, row.get("category", "cloned")))
                self.clone_rows[item] = row
            self.status.set(f"Đã tải {len(rows)} giọng clone")
        self._run(lambda: client.cloned_voices(), done, self.refresh_clones_button, "Đang tải")

    def delete_clone(self) -> None:
        selected = self.clone_tree.selection()
        if not selected:
            messagebox.showwarning("Chưa chọn", "Hãy chọn một giọng clone.")
            return
        voice = str(self.clone_tree.item(selected[0], "values")[0])
        if not messagebox.askyesno("Xác nhận xóa", f"Xóa giọng clone {voice}? Thao tác này không thể hoàn tác."):
            return
        try:
            client = self._client()
        except ApiError as exc:
            messagebox.showerror("Lỗi", str(exc))
            return
        self._notify(f"Đang xóa giọng {voice}...", "running")
        self._run(
            lambda: client.delete_cloned_voice(voice),
            lambda _r: (messagebox.showinfo("Đã xóa", f"Đã xóa {voice}."), self.load_clones()),
            self.delete_clone_button,
            "Đang xóa",
        )

    def use_selected_clone(self) -> None:
        selected = self.clone_tree.selection()
        if selected:
            self.voice_id.set(str(self.clone_tree.item(selected[0], "values")[0]))
            self.engine.set("ElevenLabs chính thức")
            self._engine_changed()
            self.status.set("Đã chọn giọng clone cho tab Tạo audio")

    def load_voice_library(self) -> None:
        source = self.voice_source.get()
        try:
            client = self._client()
        except ApiError as exc:
            messagebox.showerror("Lỗi", str(exc))
            return
        self._notify(f"Đang tải kho giọng {source}...", "running")
        def work() -> list[dict[str, Any]]:
            result = client.eleven_voices()
            if isinstance(result, list):
                return result
            for key in ("voices", "data", "items"):
                value = result.get(key) if isinstance(result, dict) else None
                if isinstance(value, list):
                    return value
                if isinstance(value, dict) and isinstance(value.get("voices"), list):
                    return value["voices"]
            return []
        def done(rows: list[dict[str, Any]]) -> None:
            self.voice_tree.delete(*self.voice_tree.get_children())
            self.voice_rows.clear()
            for row in rows:
                voice = row.get("voice_id") or row.get("id") or ""
                name = row.get("voice_name") or row.get("name") or row.get("display_name") or ""
                labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
                item = self.voice_tree.insert(
                    "",
                    "end",
                    values=(
                        voice,
                        name,
                        row.get("category") or "",
                        labels.get("gender") or "",
                        labels.get("age") or "",
                        labels.get("language") or "",
                        labels.get("accent") or "",
                        labels.get("use_case") or "",
                        row.get("description") or labels.get("descriptive") or "",
                    ),
                )
                self.voice_rows[item] = row
            available = sum(1 for row in rows if row.get("voice_id") or row.get("id"))
            self.voice_count.set(f"{available:,} giọng khả dụng")
            children = self.voice_tree.get_children()
            if children:
                self.voice_tree.selection_set(children[0])
                self.voice_tree.focus(children[0])
                self.voice_tree.see(children[0])
                self.show_selected_voice_details()
            self.status.set(f"Đã tải {len(rows)} giọng")
        self._run(work, done, self.load_voices_button, "Đang tải")

    def use_library_voice(self) -> None:
        selected = self.voice_tree.selection()
        if selected:
            self.voice_id.set(str(self.voice_tree.item(selected[0], "values")[0]))
            self.engine.set("ElevenLabs chính thức")
            self._engine_changed()
            self.status.set("Đã chọn Voice ID cho tab Tạo audio")

    def show_selected_voice_details(self, _event: Any = None) -> None:
        selected = self.voice_tree.selection()
        if not selected:
            return
        row = self.voice_rows.get(selected[0], {})
        labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
        samples = row.get("samples") if isinstance(row.get("samples"), list) else []
        models = row.get("high_quality_base_model_ids") or []
        languages = row.get("verified_languages") or []
        fine_tuning = row.get("fine_tuning") if isinstance(row.get("fine_tuning"), dict) else {}
        verification = row.get("voice_verification") if isinstance(row.get("voice_verification"), dict) else {}
        state = fine_tuning.get("state") if isinstance(fine_tuning.get("state"), dict) else {}
        label_text = ", ".join(f"{key}: {value}" for key, value in labels.items()) or "--"
        summary_lines = [
            "THÔNG TIN GIỌNG ĐỌC",
            "",
            f"Tên giọng: {row.get('name') or row.get('voice_name') or '--'}",
            f"Voice ID: {row.get('voice_id') or row.get('id') or '--'}",
            f"Loại giọng: {row.get('category') or '--'}",
            f"Giới tính: {labels.get('gender') or '--'}    |    Độ tuổi: {labels.get('age') or '--'}",
            f"Ngôn ngữ: {labels.get('language') or '--'}    |    Accent: {labels.get('accent') or '--'}",
            f"Mục đích sử dụng: {labels.get('use_case') or '--'}",
            f"Mô tả: {row.get('description') or labels.get('descriptive') or '--'}",
            f"Nhãn giọng: {label_text}",
            f"Preview URL: {row.get('preview_url') or '--'}",
            "",
            "TRẠNG THÁI VÀ QUYỀN SỬ DỤNG",
            f"Chủ sở hữu: {'Có' if row.get('is_owner') else 'Không'}",
            f"Giọng mặc định cũ: {'Có' if row.get('is_legacy') else 'Không'}    |    Giọng phối trộn: {'Có' if row.get('is_mixed') else 'Không'}",
            f"Đã đánh dấu yêu thích: {'Có' if row.get('is_bookmarked') else 'Không'}",
            f"Chất lượng bản ghi: {row.get('recording_quality') or '--'}",
            f"Trạng thái gắn nhãn: {row.get('labelling_status') or '--'}",
            f"Kiểm soát an toàn: {row.get('safety_control') or '--'}",
            f"Các gói được phép dùng: {', '.join(map(str, row.get('available_for_tiers') or [])) or '--'}",
            f"Yêu cầu xác minh: {'Có' if verification.get('requires_verification') else 'Không'}    |    Đã xác minh: {'Có' if verification.get('is_verified') else 'Không'}",
            f"Được phép fine-tune: {'Có' if fine_tuning.get('is_allowed_to_fine_tune') else 'Không'}",
            f"Trạng thái model: {', '.join(f'{key}: {value}' for key, value in state.items()) or '--'}",
            "",
            "MODEL VÀ NGÔN NGỮ",
            f"Model chất lượng cao: {', '.join(map(str, models)) if models else '--'}",
            f"Số ngôn ngữ đã xác minh: {len(languages)}",
            "",
            "FILE MẪU",
            f"Số file mẫu: {len(samples)}",
        ]
        for index, language in enumerate(languages, 1):
            if isinstance(language, dict):
                summary_lines.append(
                    f"Ngôn ngữ {index}: {language.get('language') or '--'} | "
                    f"Accent: {language.get('accent') or '--'} | Locale: {language.get('locale') or '--'}"
                )
        for index, sample in enumerate(samples, 1):
            if isinstance(sample, dict):
                duration = sample.get("duration_secs")
                duration_text = f"{duration} giây" if duration is not None else "--"
                summary_lines.append(
                    f"Mẫu {index}: {sample.get('file_name') or sample.get('sample_id') or '--'} | "
                    f"Định dạng: {sample.get('mime_type') or '--'} | Thời lượng: {duration_text}"
                )
        self.voice_detail.configure(state="normal")
        self.voice_detail.delete("1.0", "end")
        self.voice_detail.insert("1.0", "\n".join(summary_lines))
        self.voice_detail.configure(state="disabled")
        self.voice_detail.see("1.0")

    def copy_selected_voice_id(self) -> None:
        selected = self.voice_tree.selection()
        if not selected:
            self._notify("Hãy chọn một giọng trước khi sao chép Voice ID.", "warning")
            return
        voice_id = str(self.voice_tree.item(selected[0], "values")[0])
        self.clipboard_clear()
        self.clipboard_append(voice_id)
        self._notify(f"Đã sao chép Voice ID: {voice_id}", "success")

    def select_voice_for_tts(self) -> None:
        selected = self.voice_tree.selection()
        if not selected:
            self._notify("Hãy chọn một giọng trong danh sách.", "warning")
            messagebox.showwarning("Chưa chọn giọng", "Hãy chọn một giọng trong danh sách.")
            return
        self.use_library_voice()
        voice_id, name = self.voice_tree.item(selected[0], "values")[:2]
        self.tabs.select(self.tts_tab)
        self._notify(f"Đã chọn giọng {name} ({voice_id}) để tạo audio.", "success")

    def preview_selected_voice(self) -> None:
        selected = self.voice_tree.selection()
        if not selected:
            self._notify("Hãy chọn một giọng để nghe thử.", "warning")
            messagebox.showwarning("Chưa chọn giọng", "Hãy chọn một giọng để nghe thử.")
            return
        item = selected[0]
        row = self.voice_rows.get(item, {})
        preview_url = row.get("preview_url")
        voice_id = str(row.get("voice_id") or self.voice_tree.item(item, "values")[0])
        name = str(row.get("name") or self.voice_tree.item(item, "values")[1])
        if not preview_url:
            self._notify(f"Giọng {name} không có audio nghe thử.", "warning")
            messagebox.showwarning("Không có bản nghe thử", "ElevenLabs không trả preview_url cho giọng này.")
            return
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", voice_id)
        destination = ROOT / "samples" / "previews" / f"{safe_id}.mp3"
        self._notify(f"Đang chuẩn bị bản nghe thử: {name}...", "running")

        def work() -> Path:
            if destination.is_file() and destination.stat().st_size > 0:
                return destination
            return ElevenLabsClient.download_url(str(preview_url), destination)

        def done(path: Path) -> None:
            try:
                self.preview_player.play(path)
                self.status.set(f"Hoàn thành tải và đang phát: {name}")
            except ApiError as exc:
                self._notify(str(exc), "error")
                messagebox.showerror("Không phát được", str(exc))

        self._run(work, done, self.preview_voice_button, "Đang tải mẫu")

    def stop_voice_preview(self) -> None:
        self.preview_player.stop()
        self._notify("Đã dừng bản nghe thử.", "info")

    def _on_close(self) -> None:
        self.preview_player.stop()
        self.destroy()

    def _maximize_window(self) -> None:
        if self.state() == "withdrawn":
            return
        try:
            self.state("zoomed")
        except tk.TclError:
            width = max(960, self.winfo_screenwidth() - 60)
            height = max(700, self.winfo_screenheight() - 90)
            self.geometry(f"{width}x{height}+20+20")

    def _tick_license_status(self) -> None:
        if not self.license_data:
            return
        remaining = remaining_seconds(self.license_data)
        customer = self.license_data.get("customer") or "Khách hàng"
        self.license_status.set(f"License: {customer} • {format_remaining(remaining)}")
        if remaining == 0:
            messagebox.showerror("License hết hạn", "Gói kích hoạt đã hết hạn. Tool sẽ đóng.", parent=self)
            self.destroy()
            return
        self.after(1000, self._tick_license_status)


if __name__ == "__main__":
    enable_windows_dpi_awareness()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    from activation_ui import run_activation_gate

    active_license = run_activation_gate()
    if active_license:
        VoiceStudio(active_license).mainloop()
