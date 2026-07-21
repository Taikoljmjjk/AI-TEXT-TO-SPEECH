# TOOL CLONE GIỌNG TÀI LÊ MMO

Ứng dụng desktop độc lập dùng Unified AI33 v3. Dự án này không sửa hoặc dùng chung cấu hình với tool cũ.

## Chạy ứng dụng

Nhấp đúp `CHAY_TOOL_CLONE_GIONG.bat`. Lần chạy đầu script sẽ tạo `.venv` riêng và cài PySide6 cùng Requests.

## Luồng sử dụng

1. Thêm `xi-api-key` tại **Quản lý khóa API**.
2. Mở tab **Nhân bản giọng**, nhập tên, chọn file WAV/MP3 tối đa 10 MB và clone.
3. Trong tab **Tạo giọng nói**, chọn nguồn `Giọng đã clone`, làm mới thư viện và chọn `clone_<voice_id>`.
4. Nhập nội dung, chọn tốc độ 0,5–1,5 và tạo file MP3.
5. Bật công tắc **Xuất kèm phụ đề SRT** nếu muốn nhận thêm file `_PHU_DE.srt`
   trong cùng thư mục với audio; tắt công tắc nếu chỉ cần MP3.

API được dùng: `/v3/voices`, `/v3/text-to-speech/voice-clone`, `/v3/text-to-speech`
và `/v1/task/{task_id}` để polling kết quả.

Các trường TTS được gửi bằng `multipart/form-data` đúng chuẩn Unified v3. Nếu nền tảng cấp
một đường dẫn GET Task riêng, đặt biến môi trường `AI33_TASK_ENDPOINT`; dùng `{task_id}` làm
vị trí chèn mã tác vụ.

Khi clone, app kiểm tra file trước khi tải lên. WAV cần là PCM hợp lệ, 1–2 kênh; nên dùng
đoạn thu sạch, một người nói, không nhạc nền. Lỗi máy chủ 5xx tạm thời sẽ được thử lại một lần.
