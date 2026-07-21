import tempfile
import unittest
import wave
import math
import struct
from pathlib import Path
from unittest.mock import patch

from tailemmo_ai33.api import AI33Client, APIError
from tailemmo_ai33.srt import MAX_SRT_DURATION_MS, format_duration, parse_srt
from tailemmo_ai33.text_processing import add_punctuation_pauses
from tailemmo_ai33.app import AI_TAG_GROUPS
from tailemmo_ai33.audio import normalize_loudness


class AI33ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = AI33Client("test-key")

    def test_create_tts_uses_formdata_fields(self):
        with patch.object(self.client, "_request", return_value={"success": True, "task_id": "abc"}) as request:
            task_id = self.client.create_tts(
                text="Xin chào", voice_id="clone_123", speed=1.25,
                file_name="demo.mp3", receive_url="https://example.com/hook",
            )
        self.assertEqual(task_id, "abc")
        form = request.call_args.kwargs["files"]
        self.assertEqual(form["voice_id"], (None, "clone_123"))
        self.assertEqual(form["speed"], (None, "1.25"))
        self.assertEqual(form["with_transcript"], (None, "false"))

    def test_create_tts_rejects_voice_without_provider_prefix(self):
        with self.assertRaises(APIError):
            self.client.create_tts(text="Xin chào", voice_id="123")

    def test_create_tts_enables_transcript_in_multipart(self):
        with patch.object(self.client, "_request", return_value={"success": True, "task_id": "abc"}) as request:
            self.client.create_tts(text="Xin chào", voice_id="clone_123", with_transcript=True)
        self.assertEqual(request.call_args.kwargs["files"]["with_transcript"], (None, "true"))

    def test_create_tts_keeps_ai_tags_and_sends_dictionary_id(self):
        tagged = '[speed_slow] Xin chào. <break time="1.5s" /> [laughter]'
        with patch.object(self.client, "_request", return_value={"success": True, "task_id": "abc"}) as request:
            self.client.create_tts(
                text=tagged, voice_id="elevenlabs_voice123", pronunciation_dictionary_id=42
            )
        form = request.call_args.kwargs["files"]
        self.assertEqual(form["text"], (None, tagged))
        self.assertEqual(form["pronunciation_dictionary_id"], (None, "42"))

    def test_create_tts_keeps_raw_srt_structure(self):
        source = "1\n00:00:00,000 --> 00:00:02,400\n[whispering] Xin chào.\n\n2\n00:00:02,700 --> 00:00:05,200\n[laughter] Tuyệt vời!"
        with patch.object(self.client, "_request", return_value={"success": True, "task_id": "srt-1"}) as request:
            self.client.create_tts(text=source, voice_id="elevenlabs_voice123")
        self.assertEqual(request.call_args.kwargs["files"]["text"], (None, source))

    def test_all_voices_includes_clone_and_deduplicates(self):
        def response(method, path, **kwargs):
            provider = kwargs["params"]["provider"]
            data = [{"voice_id": "clone_1", "name": "Bản clone"}] if provider == "clone" else [{"voice_id": "shared", "name": provider}]
            return {"data": data}
        with patch.object(self.client, "_request", side_effect=response):
            voices = self.client.all_voices()
        self.assertTrue(any(item["voice_id"] == "clone_1" and item["_provider"] == "clone" for item in voices))
        self.assertEqual(sum(item["voice_id"] == "shared" for item in voices), 1)

    def test_dictionary_endpoints_use_json_contract(self):
        rules = [{"from": "AI", "to": "Ây Ai", "matchType": "word", "caseSensitive": True}]
        with patch.object(self.client, "_request", return_value={"dictionary": {"id": 7}}) as request:
            result = self.client.create_dictionary("Brand", rules)
        self.assertEqual(result["id"], 7)
        self.assertEqual(request.call_args.args[:2], ("POST", "/v3/dictionaries"))
        self.assertEqual(request.call_args.kwargs["json"]["rules"], rules)

    def test_dictionary_validation_rejects_invalid_match_type(self):
        with self.assertRaises(APIError):
            self.client.create_dictionary(
                "Brand", [{"from": "AI", "to": "Ây Ai", "matchType": "regex"}]
            )

    def test_clone_normalizes_voice_id(self):
        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder) / "sample.mp3"
            sample.write_bytes(b"audio")
            with patch.object(self.client, "_request", return_value={"success": True, "data": {"voice_id": "123"}}):
                self.assertEqual(self.client.clone_voice(name="Demo", audio_file=str(sample)), "clone_123")

    def test_inspect_valid_pcm_wav(self):
        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder) / "sample.wav"
            with wave.open(str(sample), "wb") as out:
                out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
                out.writeframes(b"\x00\x00" * 16000)
            info = self.client.inspect_audio_sample(str(sample))
        self.assertEqual(info["format"], "WAV")
        self.assertAlmostEqual(info["duration"], 1.0)

    def test_clone_retries_one_server_error_with_fresh_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder) / "sample.mp3"
            sample.write_bytes(b"ID3" + b"audio" * 20)
            with patch("tailemmo_ai33.api.time.sleep"), patch.object(
                self.client, "_request",
                side_effect=[APIError("server", 500), {"success": True, "data": {"voice_id": "456"}}],
            ) as request:
                result = self.client.clone_voice(name="Demo", audio_file=str(sample))
        self.assertEqual(result, "clone_456")
        self.assertEqual(request.call_count, 2)

    def test_task_info_reads_nested_audio_url(self):
        info = self.client.task_info({"data": {"status": "completed", "result": {"audio_url": "https://cdn/a.mp3"}}})
        self.assertEqual(info["status"], "completed")
        self.assertEqual(info["audio_url"], "https://cdn/a.mp3")

    def test_task_info_reads_list_item_metadata_and_progress(self):
        info = self.client.task_info({
            "data": {
                "id": "task-1", "status": "done", "progress": 100,
                "metadata": {"audio_url": "https://cdn/result.mp3", "srt_url": "https://cdn/result.srt"},
            }
        })
        self.assertEqual(info["id"], "task-1")
        self.assertEqual(info["progress"], 100)
        self.assertEqual(info["audio_url"], "https://cdn/result.mp3")
        self.assertEqual(info["srt_url"], "https://cdn/result.srt")

    def test_get_task_uses_v1_detail_endpoint(self):
        response = {"id": "wanted", "status": "done", "progress": 100,
                    "metadata": {"audio_url": "https://cdn/result.mp3"}}
        with patch.object(self.client, "_request", return_value=response) as request:
            result = self.client.get_task("wanted")
        self.assertEqual(result["id"], "wanted")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["progress"], 100)
        self.assertEqual(request.call_args.args[:2], ("GET", "/v1/task/wanted"))


class SRTTests(unittest.TestCase):
    def test_parse_valid_multiline_srt(self):
        source = "\ufeff1\r\n00:00:00,000 --> 00:00:02,400\r\nXin chào\r\ndòng hai\r\n\r\n2\r\n00:00:02,700 --> 00:00:08,200\r\nTạm biệt"
        cues = parse_srt(source)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["text"], "Xin chào\ndòng hai")
        self.assertEqual(cues[-1]["end_ms"], 8200)
        self.assertEqual(format_duration(8200), "00:00:08")

    def test_rejects_end_before_start(self):
        with self.assertRaisesRegex(ValueError, "kết thúc"):
            parse_srt("1\n00:00:03,000 --> 00:00:02,000\nSai")

    def test_rejects_duration_over_five_hours(self):
        self.assertEqual(MAX_SRT_DURATION_MS, 18_000_000)
        with self.assertRaisesRegex(ValueError, "5 giờ"):
            parse_srt("1\n00:00:00,000 --> 05:00:00,001\nQuá dài")


class TextProcessingTests(unittest.TestCase):
    def test_adds_selected_short_and_long_pauses(self):
        result = add_punctuation_pauses("Xin chào, bạn khỏe không?", {",", "?"}, 0.3, 0.8)
        self.assertEqual(result, 'Xin chào, <break time="0.3s" /> bạn khỏe không? <break time="0.8s" />')

    def test_does_not_duplicate_break_or_break_decimal_and_url(self):
        source = 'Bản 1.5, xem https://ai33.pro. <break time="1s" /> Xong.'
        result = add_punctuation_pauses(source, {".", ",", ":"}, 0.25, 0.7)
        self.assertIn("1.5", result)
        self.assertIn("https://", result)
        self.assertEqual(result.count('<break time="1s" />'), 1)
        self.assertEqual(result.count('<break time="0.7s" />'), 1)

    def test_speed_is_controlled_by_supported_ai_tags(self):
        speed_tags = {tag for _, tag, _ in AI_TAG_GROUPS["Tốc độ"]}
        self.assertEqual(speed_tags, {
            "[speed_very_slow]", "[speed_slow]", "[speed_fast]", "[speed_very_fast]"
        })


class AudioNormalizationTests(unittest.TestCase):
    def test_normalizes_wav_to_nonempty_mp3(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "varying.wav"
            target = Path(folder) / "normalized.mp3"
            rate = 16000
            frames = []
            for index in range(rate * 2):
                amplitude = 2500 if index < rate else 14000
                frames.append(struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 220 * index / rate))))
            with wave.open(str(source), "wb") as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(rate)
                output.writeframes(b"".join(frames))
            normalize_loudness(source, target)
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
