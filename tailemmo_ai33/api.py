import json
import mimetypes
import os
import re
import threading
import time
import wave
from pathlib import Path

import requests


class APIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class RateLimiter:
    def __init__(self, interval: float = 0.35):
        self.interval = interval
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self):
        with self.lock:
            delay = self.interval - (time.monotonic() - self.last)
            if delay > 0:
                time.sleep(delay)
            self.last = time.monotonic()


class AI33Client:
    """Client cho Unified v3 API của api.ai33.pro."""

    BASE = "https://api.ai33.pro"
    PROVIDERS = ("clone", "elevenlabs", "minimax", "edge", "kokoro", "vbee", "fishaudio")

    def __init__(self, api_key: str, timeout: int = 60):
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.rate = RateLimiter()

    def _request(self, method: str, path: str, *, retries=2, **kwargs):
        url = path if path.startswith("http") else self.BASE + path
        headers = kwargs.pop("headers", {})
        request_timeout = kwargs.pop("timeout", self.timeout)
        headers.setdefault("xi-api-key", self.api_key)
        headers.setdefault("accept", "application/json")
        for attempt in range(retries + 1):
            self.rate.wait()
            try:
                response = requests.request(method, url, headers=headers, timeout=request_timeout, **kwargs)
            except requests.RequestException as exc:
                if attempt == retries:
                    raise APIError("Không thể kết nối máy chủ. Hãy kiểm tra Internet và thử lại.") from exc
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 429 and attempt < retries:
                try:
                    retry_after = float(response.headers.get("Retry-After", 0) or 0)
                except ValueError:
                    retry_after = 0
                time.sleep(max(1.0, retry_after, 2 ** attempt))
                continue
            if not response.ok:
                try:
                    payload = response.json()
                    detail = payload.get("message") or payload.get("detail") or payload.get("error") or payload
                except ValueError:
                    detail = response.text[:400]
                detail = re.sub(r"https?://\S+", "", str(detail or "")).strip(" .,-")
                if response.status_code == 401:
                    detail = "Khóa API không hợp lệ hoặc đã hết hiệu lực."
                raise APIError(f"HTTP {response.status_code}: {detail or 'API từ chối yêu cầu'}", response.status_code)
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError as exc:
                raise APIError("Máy chủ trả về dữ liệu không hợp lệ.") from exc
        raise APIError("Không thể kết nối máy chủ.")

    def voices(self, provider="clone", search="", page=1, page_size=100):
        if provider not in self.PROVIDERS:
            raise APIError("Nhà cung cấp giọng không hợp lệ.")
        params = {"provider": provider, "page": page, "page_size": min(100, page_size)}
        if search.strip():
            params["search"] = search.strip()
        payload = self._request("GET", "/v3/voices", params=params)
        voices = payload.get("data") or []
        return [dict(voice, _provider=provider) for voice in voices if isinstance(voice, dict)]

    def all_voices(self, search="", page_size=100):
        """Combine every provider and cloned voices, deduplicated by provider voice id."""
        combined = []
        seen = set()
        errors = []
        for provider in self.PROVIDERS:
            try:
                voices = self.voices(provider=provider, search=search, page_size=page_size)
            except APIError as exc:
                errors.append(f"{provider}: {exc}")
                continue
            for voice in voices:
                voice_id = str(voice.get("voice_id") or voice.get("id") or "")
                if not voice_id or voice_id in seen:
                    continue
                seen.add(voice_id)
                combined.append(voice)
        if not combined and errors:
            raise APIError("Không tải được thư viện giọng: " + "; ".join(errors[:3]))
        return combined

    def account_status(self):
        self.voices("clone", page_size=1)
        return {
            "plan": "Đã kết nối API",
            "api_enabled": True,
            "message": "Khóa hợp lệ và đã truy cập được thư viện giọng.",
        }

    @staticmethod
    def inspect_audio_sample(audio_file: str) -> dict:
        path = Path(audio_file)
        if not path.is_file():
            raise APIError("Không tìm thấy file giọng mẫu.")
        if path.suffix.lower() not in {".wav", ".mp3"}:
            raise APIError("File giọng mẫu phải có định dạng WAV hoặc MP3.")
        size = path.stat().st_size
        if size <= 0:
            raise APIError("File giọng mẫu đang trống.")
        if size > 10 * 1024 * 1024:
            raise APIError("File giọng mẫu vượt quá giới hạn 10 MB.")
        info = {"size": size, "format": path.suffix.lower().lstrip(".").upper(), "duration": None}
        if path.suffix.lower() == ".wav":
            try:
                with wave.open(str(path), "rb") as sample:
                    if sample.getcomptype() != "NONE":
                        raise APIError("File WAV phải là âm thanh PCM không nén.")
                    if sample.getnchannels() not in (1, 2):
                        raise APIError("File WAV chỉ được dùng 1 hoặc 2 kênh âm thanh.")
                    if sample.getframerate() <= 0 or sample.getnframes() <= 0:
                        raise APIError("File WAV không có dữ liệu âm thanh hợp lệ.")
                    info.update({
                        "duration": sample.getnframes() / sample.getframerate(),
                        "channels": sample.getnchannels(),
                        "sample_rate": sample.getframerate(),
                        "sample_width": sample.getsampwidth(),
                    })
            except APIError:
                raise
            except (wave.Error, EOFError, OSError) as exc:
                raise APIError("File WAV bị lỗi hoặc không phải WAV PCM hợp lệ.") from exc
        return info

    def clone_voice(self, *, name: str, audio_file: str) -> str:
        path = Path(audio_file)
        self.inspect_audio_sample(str(path))
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        payload = None
        for attempt in range(2):
            try:
                # Mở lại stream ở mỗi lần thử để multipart luôn gửi đủ file từ byte đầu tiên.
                with path.open("rb") as stream:
                    payload = self._request(
                        "POST", "/v3/text-to-speech/voice-clone",
                        data={"voice_name": name.strip()},
                        files={"audio_file": (path.name, stream, mime)},
                        timeout=max(self.timeout, 180),
                        retries=0,
                    )
                break
            except APIError as exc:
                transient = exc.status_code in {429, 500, 502, 503, 504}
                if transient and attempt == 0:
                    time.sleep(3)
                    continue
                if exc.status_code and exc.status_code >= 500:
                    raise APIError(
                        "Máy chủ không xử lý được file mẫu. Hãy thử tên giọng khác hoặc dùng "
                        "WAV PCM/MP3 thu sạch, một người nói và dung lượng dưới 10 MB.",
                        exc.status_code,
                    ) from exc
                raise
        if not isinstance(payload, dict):
            raise APIError("Máy chủ không trả về kết quả clone hợp lệ.")
        voice_id = (payload.get("data") or {}).get("voice_id")
        if voice_id is None:
            raise APIError("Máy chủ không trả về mã giọng sau khi clone.")
        voice_id = str(voice_id)
        return voice_id if voice_id.startswith("clone_") else f"clone_{voice_id}"

    def delete_clone(self, voice_id: str):
        raw_id = str(voice_id).removeprefix("clone_")
        return self._request("DELETE", f"/v3/text-to-speech/voice-clone/{raw_id}")

    def dictionaries(self) -> list[dict]:
        """Danh sách từ điển phát âm của tài khoản theo Unified v3."""
        payload = self._request("GET", "/v3/dictionaries")
        data = payload.get("data")
        if data is None:
            data = payload.get("dictionaries")
        return data if isinstance(data, list) else []

    def dictionary(self, dictionary_id: int) -> dict:
        payload = self._request("GET", f"/v3/dictionaries/{int(dictionary_id)}")
        data = payload.get("dictionary") or payload.get("data") or payload
        if not isinstance(data, dict):
            raise APIError("Máy chủ không trả về từ điển hợp lệ.")
        return data

    @staticmethod
    def _validate_dictionary(name: str, rules: list[dict]):
        if not str(name).strip():
            raise APIError("Tên từ điển đang trống.")
        if not isinstance(rules, list) or not rules:
            raise APIError("Từ điển cần ít nhất một quy tắc phát âm.")
        normalized = []
        for rule in rules:
            source = str(rule.get("from") or "").strip()
            target = str(rule.get("to") or "").strip()
            match_type = str(rule.get("matchType") or "word")
            if not source or not target:
                raise APIError("Mỗi quy tắc phải có từ gốc và cách đọc thay thế.")
            if match_type not in {"word", "contains"}:
                raise APIError("Kiểu khớp từ điển phải là word hoặc contains.")
            normalized.append({
                "from": source,
                "to": target,
                "matchType": match_type,
                "caseSensitive": bool(rule.get("caseSensitive", False)),
            })
        return str(name).strip(), normalized

    def create_dictionary(self, name: str, rules: list[dict]) -> dict:
        name, rules = self._validate_dictionary(name, rules)
        payload = self._request("POST", "/v3/dictionaries", json={"name": name, "rules": rules})
        return payload.get("dictionary") or payload.get("data") or payload

    def update_dictionary(self, dictionary_id: int, name: str, rules: list[dict]) -> dict:
        name, rules = self._validate_dictionary(name, rules)
        payload = self._request(
            "PUT", f"/v3/dictionaries/{int(dictionary_id)}", json={"name": name, "rules": rules}
        )
        return payload.get("dictionary") or payload.get("data") or payload

    def delete_dictionary(self, dictionary_id: int):
        return self._request("DELETE", f"/v3/dictionaries/{int(dictionary_id)}")

    def preview_dictionary(self, text: str, rules: list[dict]) -> dict:
        _, rules = self._validate_dictionary("preview", rules)
        return self._request("POST", "/v3/dictionaries/preview", json={"text": str(text), "rules": rules})

    def create_tts(self, *, text, voice_id, speed=1.0, file_name="", receive_url="",
                   with_transcript=False, pronunciation_dictionary_id=None):
        if not str(text).strip():
            raise APIError("Nội dung chuyển giọng nói đang trống.")
        if len(text) > 1_000_000:
            raise APIError("Nội dung vượt giới hạn 1.000.000 ký tự mỗi yêu cầu.")
        if not 0.5 <= float(speed) <= 1.5:
            raise APIError("Tốc độ đọc phải trong khoảng 0,5–1,5.")
        voice_id = str(voice_id)
        if not any(voice_id.startswith(prefix + "_") for prefix in self.PROVIDERS):
            raise APIError("Mã giọng thiếu tiền tố nhà cung cấp (clone_, edge_, minimax_…).")
        form = {
            "text": text,
            "voice_id": voice_id,
            "speed": str(float(speed)),
            "with_transcript": "true" if with_transcript else "false",
        }
        if file_name:
            form["file_name"] = file_name
        if receive_url:
            form["receive_url"] = receive_url
        if pronunciation_dictionary_id is not None:
            form["pronunciation_dictionary_id"] = str(pronunciation_dictionary_id)
        # requests chỉ tạo multipart/form-data khi dùng ``files``. Mỗi tuple (None, value)
        # tương đương một trường ``curl -F`` không có file, đúng hợp đồng Unified v3.
        multipart = {name: (None, str(value)) for name, value in form.items()}
        payload = self._request("POST", "/v3/text-to-speech", files=multipart)
        if payload.get("success") is False:
            raise APIError(str(payload.get("message") or "Máy chủ từ chối tạo âm thanh."))
        task_id = payload.get("task_id") or (payload.get("data") or {}).get("task_id")
        if not task_id:
            raise APIError("Máy chủ không trả về mã tác vụ.")
        return str(task_id)

    def get_task(self, task_id: str) -> dict:
        """Polling đúng Common API: GET /v1/task/{task_id}."""
        configured = os.getenv("AI33_TASK_ENDPOINT", "").strip()
        path = configured.format(task_id=task_id) if configured else f"/v1/task/{task_id}"
        payload = self._request(
            "GET", path,
            headers={"Content-Type": "application/json"},
            retries=3,
        )
        return self.task_info(payload, expected_task_id=task_id)

    def task(self, task_id: str):
        """Tương thích với code cũ; trả về dữ liệu trạng thái đã chuẩn hóa."""
        return self.get_task(task_id)

    @staticmethod
    def task_info(payload: dict, expected_task_id: str | None = None) -> dict:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        task_id = str(data.get("id") or data.get("task_id") or "")
        if expected_task_id and task_id and task_id != str(expected_task_id):
            raise APIError("Phản hồi không khớp mã tác vụ.", 404)
        raw_status = str(data.get("status") or data.get("state") or data.get("task_status") or "processing").lower()
        if raw_status in {"done", "success", "succeed", "succeeded", "completed", "finished"}:
            status = "completed"
        elif raw_status in {"failed", "fail", "error", "internal_error", "cancelled", "canceled"}:
            status = "failed"
        else:
            status = "processing"
        message = (
            data.get("error_message") or data.get("message") or
            data.get("error") or data.get("reason") or ""
        )
        audio_url = data.get("audio_url") or data.get("url") or data.get("output_url") or metadata.get("audio_url")
        srt_url = data.get("srt_url") or metadata.get("srt_url")
        json_url = data.get("json_url") or metadata.get("json_url")
        metadata_data = metadata.get("data") if isinstance(metadata.get("data"), dict) else {}
        audio_url = audio_url or metadata_data.get("audio_url") or metadata_data.get("url")
        srt_url = srt_url or metadata_data.get("srt_url")
        json_url = json_url or metadata_data.get("json_url")
        if not audio_url:
            result = data.get("result") or data.get("output")
            if isinstance(result, dict):
                audio_url = result.get("audio_url") or result.get("url")
            elif isinstance(result, str) and result.startswith("http"):
                audio_url = result
        try:
            progress = max(0, min(100, int(float(data.get("progress", 0) or 0))))
        except (TypeError, ValueError):
            progress = 0
        return {
            "id": task_id,
            "status": status,
            "raw_status": raw_status,
            "progress": progress,
            "audio_url": audio_url,
            "srt_url": srt_url,
            "json_url": json_url,
            "message": str(message),
        }

    @staticmethod
    def download(url: str, target: Path):
        try:
            with requests.get(url, stream=True, timeout=180) as response:
                response.raise_for_status()
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as out:
                    for chunk in response.iter_content(256 * 1024):
                        if chunk:
                            out.write(chunk)
        except requests.RequestException as exc:
            raise APIError("Không tải được file âm thanh từ máy chủ.") from exc
