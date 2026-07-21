import subprocess
from pathlib import Path

import imageio_ffmpeg


class AudioNormalizationError(RuntimeError):
    pass


def normalize_loudness(source, target):
    """Normalize long-form speech to a stable EBU R128 loudness target."""
    source = Path(source)
    target = Path(target)
    if not source.is_file() or source.stat().st_size == 0:
        raise AudioNormalizationError("File âm thanh gốc không tồn tại hoặc đang trống.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.stem + ".normalizing.mp3")
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-af", "loudnorm=I=-16:LRA=7:TP=-1.5",
        "-codec:a", "libmp3lame", "-b:a", "192k",
        str(temporary),
    ]
    startup = None
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=30 * 60,
            startupinfo=startup, creationflags=creation_flags, check=False,
        )
        if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            detail = (result.stderr or "").strip().splitlines()
            raise AudioNormalizationError(
                "Không thể chuẩn hóa âm lượng" + (f": {detail[-1]}" if detail else ".")
            )
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target
