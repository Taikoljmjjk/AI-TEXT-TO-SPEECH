from __future__ import annotations

import json
import base64
import time
from pathlib import Path
from typing import Any

import requests


class ApiError(RuntimeError):
    pass


class ElevenLabsClient:
    """Client for the official ElevenLabs REST API."""

    def __init__(self, api_key: str, base_url: str = "https://api.elevenlabs.io", timeout: int = 120):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"xi-api-key": self.api_key})

    @staticmethod
    def _error_detail(response: requests.Response) -> str:
        try:
            body = response.json()
            detail = body.get("detail", body) if isinstance(body, dict) else body
            if isinstance(detail, dict):
                return str(detail.get("message") or detail.get("status") or json.dumps(detail, ensure_ascii=False))
            return str(detail)
        except ValueError:
            return response.text[:1000] or response.reason

    def _response(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        response: requests.Response | None = None
        for attempt in range(3):
            try:
                response = self.session.request(method, f"{self.base_url}{path}", timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                raise ApiError(f"Không thể kết nối ElevenLabs: {exc}") from exc
            if response.status_code != 429 or attempt == 2:
                break
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = float(retry_after)
            except (TypeError, ValueError):
                delay = float(2**attempt)
            time.sleep(max(1.0, min(delay, 8.0)))
        if response is None:
            raise ApiError("Không nhận được phản hồi từ ElevenLabs.")
        if not response.ok:
            raise ApiError(f"ElevenLabs HTTP {response.status_code}: {self._error_detail(response)}")
        return response

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._response(method, path, **kwargs)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError("ElevenLabs trả về dữ liệu không phải JSON.") from exc

    def subscription(self) -> dict[str, Any]:
        return self._json("GET", "/v1/user/subscription")

    def credits(self) -> dict[str, Any]:
        data = self.subscription()
        used = int(data.get("character_count") or 0)
        limit = data.get("character_limit")
        remaining = max(0, int(limit) - used) if isinstance(limit, (int, float)) else data.get("credit_count", "--")
        return {"credits": remaining, "used": used, "limit": limit, "tier": data.get("tier", "--"), "raw": data}

    def health(self) -> dict[str, Any]:
        models = self.models()
        return {"success": True, "models": len(models) if isinstance(models, list) else "OK"}

    def models(self) -> Any:
        return self._json("GET", "/v1/models")

    def eleven_voices(self) -> Any:
        return self._json("GET", "/v2/voices", params={"page_size": 100, "include_total_count": "true"})

    def shared_voices(self) -> Any:
        return self._json("GET", "/v1/shared-voices")

    def create_speech(
        self,
        voice_id: str,
        text: str,
        model_id: str = "eleven_multilingual_v2",
        output_format: str = "mp3_44100_128",
        speed: float = 1.0,
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        style: float = 0.0,
        use_speaker_boost: bool = True,
        language_code: str | None = None,
    ) -> bytes:
        voice_settings: dict[str, Any] = {
            "stability": stability,
            "style": style,
        }
        if model_id != "eleven_v3":
            voice_settings.update(
                {
                    "speed": speed,
                    "similarity_boost": similarity_boost,
                    "use_speaker_boost": use_speaker_boost,
                }
            )
        payload: dict[str, Any] = {
            "text": text,
            "model_id": model_id,
            "voice_settings": voice_settings,
        }
        if language_code:
            payload["language_code"] = language_code
        response = self._response(
            "POST",
            f"/v1/text-to-speech/{voice_id}",
            params={"output_format": output_format},
            json=payload,
            headers={"Accept": "application/octet-stream"},
        )
        return response.content

    def create_speech_with_timestamps(
        self,
        voice_id: str,
        text: str,
        model_id: str = "eleven_multilingual_v2",
        output_format: str = "mp3_44100_128",
        speed: float = 1.0,
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        style: float = 0.0,
        use_speaker_boost: bool = True,
        language_code: str | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        voice_settings: dict[str, Any] = {
            "stability": stability,
            "style": style,
        }
        if model_id != "eleven_v3":
            voice_settings.update(
                {
                    "speed": speed,
                    "similarity_boost": similarity_boost,
                    "use_speaker_boost": use_speaker_boost,
                }
            )
        payload: dict[str, Any] = {
            "text": text,
            "model_id": model_id,
            "voice_settings": voice_settings,
        }
        if language_code:
            payload["language_code"] = language_code
        result = self._json(
            "POST",
            f"/v1/text-to-speech/{voice_id}/with-timestamps",
            params={"output_format": output_format},
            json=payload,
        )
        encoded = result.get("audio_base64") if isinstance(result, dict) else None
        if not encoded:
            raise ApiError("ElevenLabs không trả về audio_base64.")
        alignment = result.get("alignment") or result.get("normalized_alignment") or {}
        try:
            audio = base64.b64decode(encoded)
        except (ValueError, TypeError) as exc:
            raise ApiError("Dữ liệu audio_base64 không hợp lệ.") from exc
        return audio, alignment

    def clone_voice(self, audio_path: str | Path, voice_name: str, gender: str = "") -> str:
        path = Path(audio_path)
        if not path.is_file():
            raise ApiError("Không tìm thấy file audio mẫu.")
        if not voice_name.strip():
            raise ApiError("Tên giọng clone không được để trống.")
        labels = {"gender": gender} if gender else {}
        with path.open("rb") as handle:
            result = self._json(
                "POST",
                "/v1/voices/add",
                files=[("files", (path.name, handle, "application/octet-stream"))],
                data={"name": voice_name.strip(), "labels": json.dumps(labels, ensure_ascii=False)},
            )
        voice_id = result.get("voice_id") if isinstance(result, dict) else None
        if not voice_id:
            raise ApiError("Clone thành công nhưng ElevenLabs không trả về voice_id.")
        return str(voice_id)

    def cloned_voices(self) -> list[dict[str, Any]]:
        result = self._json(
            "GET",
            "/v2/voices",
            params={"category": "cloned", "page_size": 100, "include_total_count": "true"},
        )
        return result.get("voices", []) if isinstance(result, dict) else []

    def delete_cloned_voice(self, voice_id: str) -> Any:
        return self._json("DELETE", f"/v1/voices/{voice_id}")

    @staticmethod
    def download_url(url: str, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with requests.get(url, stream=True, timeout=90) as response:
                response.raise_for_status()
                with target.open("wb") as handle:
                    for chunk in response.iter_content(1024 * 128):
                        if chunk:
                            handle.write(chunk)
        except requests.RequestException as exc:
            raise ApiError(f"Không tải được audio nghe thử: {exc}") from exc
        return target


# Backward-compatible import name used by older app versions.
ElevenClickClient = ElevenLabsClient
