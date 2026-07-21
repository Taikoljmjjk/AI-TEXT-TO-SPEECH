import re


MAX_SRT_DURATION_MS = 5 * 60 * 60 * 1000

_TIMESTAMP = r"(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})"
_TIMING_LINE = re.compile(
    rf"^\s*{_TIMESTAMP}\s*-->\s*{_TIMESTAMP}(?:\s+.*)?$"
)


def _to_milliseconds(parts):
    hours, minutes, seconds, milliseconds = (int(value) for value in parts)
    if minutes > 59 or seconds > 59:
        raise ValueError("Mốc thời gian SRT có phút hoặc giây lớn hơn 59.")
    return (((hours * 60) + minutes) * 60 + seconds) * 1000 + milliseconds


def format_duration(milliseconds):
    total_seconds = max(0, int(milliseconds)) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_srt(text):
    """Validate SRT while leaving the caller's original text untouched."""
    normalized = str(text or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        raise ValueError("File SRT không có nội dung.")

    blocks = re.split(r"\n\s*\n", normalized.strip())
    cues = []
    previous_start = -1
    for block_number, block in enumerate(blocks, 1):
        lines = block.split("\n")
        if lines and lines[0].strip().isdigit():
            sequence = int(lines.pop(0).strip())
        else:
            sequence = block_number
        if not lines:
            raise ValueError(f"Đoạn SRT {block_number} thiếu dòng thời gian.")
        match = _TIMING_LINE.match(lines.pop(0))
        if not match:
            raise ValueError(f"Đoạn SRT {block_number} có dòng thời gian không hợp lệ.")
        start_ms = _to_milliseconds(match.groups()[:4])
        end_ms = _to_milliseconds(match.groups()[4:8])
        if end_ms <= start_ms:
            raise ValueError(f"Đoạn SRT {block_number} phải có thời điểm kết thúc sau thời điểm bắt đầu.")
        if start_ms < previous_start:
            raise ValueError(f"Đoạn SRT {block_number} không được đứng trước đoạn thời gian trước đó.")
        if not any(line.strip() for line in lines):
            raise ValueError(f"Đoạn SRT {block_number} chưa có lời thoại.")
        cues.append({
            "sequence": sequence,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": "\n".join(lines),
        })
        previous_start = start_ms

    duration_ms = max(cue["end_ms"] for cue in cues)
    if duration_ms > MAX_SRT_DURATION_MS:
        raise ValueError("Tổng thời lượng SRT vượt giới hạn 5 giờ của AI33.")
    return cues


def is_timing_line(line):
    return bool(_TIMING_LINE.match(str(line or "")))
