# VOICE 11 LABS Studio

**Nhà phát triển:** TAILEMMO — **Zalo:** 0394342601

## Kích hoạt bản quyền

- Tool bắt buộc xác minh key TLV1 trước khi mở giao diện chính.
- Sao chép mã máy trong cửa sổ kích hoạt và gửi cho TAILEMMO để được cấp key.
- Key được kiểm tra offline bằng chữ ký Ed25519, đúng mã máy và đúng thời hạn.
- Key kích hoạt được lưu riêng cho tool này và mã hóa bằng Windows DPAPI.
- Hỗ trợ gói Vĩnh viễn; key hợp lệ đã lưu được tự xác thực để lần mở sau không phải kích hoạt lại.
- Một key do `TAILEMMO LICENSE ADMIN MOI` cấp dùng được cho cả 3 tool trên cùng máy.
- Tool phân phối chỉ chứa public key; không chứa `private_seed.key` hoặc mã ký key.

Ứng dụng desktop tiếng Việt dùng ElevenAPI chính thức tại `https://api.elevenlabs.io`.

## Chức năng

- Tạo audio trực tiếp từ văn bản và lưu MP3/PCM.
- Instant Voice Clone chính thức từ file audio mẫu.
- Tạo audio bằng voice đã clone và điều chỉnh tốc độ.
- Xem/xóa giọng clone và chọn nhanh Voice ID.
- Nghe thử `preview_url`, dừng phát và chọn giọng từ tab Kho giọng để tạo audio.
- Tự làm sạch metadata ID3 không tương thích trước khi nghe thử, tránh lỗi khởi tạo MCI với một số giọng ElevenLabs.
- Kho giọng có cột riêng cho loại, giới tính, tuổi, ngôn ngữ, accent, mục đích và mô tả; khung chi tiết chỉ hiển thị văn bản dễ đọc, không hiển thị JSON thô.
- Kho giọng hiển thị bộ đếm tổng số giọng khả dụng và tự cập nhật sau mỗi lần tải danh sách.
- Hỗ trợ sao chép nhanh Voice ID của giọng đang chọn.
- Tạo file SRT đồng bộ theo timestamp ký tự thực tế do ElevenLabs trả về.
- Trường **Tiêu đề / tên file** nằm ngay trên vùng nhập nội dung; audio và SRT dùng chung tên này, tự làm sạch ký tự không hợp lệ và tự thêm hậu tố để không ghi đè file cũ.
- Điều chỉnh đầy đủ voice settings theo API: tốc độ, ổn định, tương đồng, phong cách phóng đại và tăng cường loa.
- Hỗ trợ ghi đè mã ngôn ngữ ISO 639-1, 28 định dạng đầu ra và tự loại tham số không tương thích với Eleven v3.
- Chọn và ghi nhớ thư mục lưu audio/SRT; có nút mở nhanh vị trí lưu.
- Nút **Mở thư mục vừa tạo** tự kích hoạt sau khi tạo audio thành công và mở thư mục kết quả mới nhất.
- Quản lý nhiều API key, xem credit/quyền IVC và tự xoay vòng theo tác vụ.
- Tự động kiểm tra toàn bộ danh sách API đã lưu sau khi khởi động, đồng bộ ngay credit, quyền clone và bộ đếm ký tự.
- Bộ đếm tổng toàn danh sách: API hợp lệ/lỗi, tổng credit còn lại, đã dùng/hạn mức và tổng key có quyền clone.
- Sau khi kiểm tra, từng dòng API hiển thị trực tiếp số ký tự còn lại, số đã dùng/hạn mức và quyền clone.
- Thanh tiến trình dạng phân đoạn xanh/xám được đặt cố định phía dưới, hiển thị phần trăm và trạng thái tác vụ.
- Tab tạo audio có vùng nội dung lớn với thanh cuộn riêng, thanh cuộn toàn trang, nút dán clipboard và đọc trực tiếp TXT/SRT.
- Vị trí lưu, bộ đếm và các nút tạo/xóa được gom thành một thanh thao tác nhỏ gọn cố định phía dưới tab.
- Tab tạo audio dùng bố cục hai cột có vách kéo: đầu vào TXT/SRT bên trái và toàn bộ thông số cài đặt bên phải.
- Bố cục đã được kiểm tra ở kích thước tối thiểu 960×700: bộ đếm ký tự luôn nằm dưới vùng nhập; các tab Clone voice, Kho giọng và Danh sách API luôn giữ vùng nội dung cùng thanh cuộn cần thiết.
- Khu vực tiêu đề, thống kê và kết nối API được thiết kế dạng thanh nhỏ gọn để dành thêm chiều cao cho nội dung chính.
- Khi nhập SRT, tool tự bỏ số thứ tự, timestamp và thẻ định dạng trước khi gửi văn bản tới ElevenLabs.
- Bộ đếm nội dung đồng bộ với tổng credit của tất cả API, hiển thị số ký tự và số dư dự kiến sau tác vụ.
- Sau khi tạo audio, tool truy vấn lại số dư của từng API vừa sử dụng, cập nhật ngay tổng credit và số ký tự còn lại trên từng dòng; nếu không xác minh được do lỗi mạng, số dư được ghi rõ là ước tính.
- Tự chuyển sang key tiếp theo khi gặp HTTP 401, 402 hoặc 429.
- Loại key bị ElevenLabs vô hiệu hóa/sai quyền khỏi vòng xoay; key HTTP 429 được retry có backoff rồi tạm nghỉ 60 giây.
- Trước khi tạo audio, tool ưu tiên API còn đủ credit; văn bản dài được chia ở ranh giới câu, tạo tạm từng phần rồi tự ghép đúng thứ tự thành một file audio và một SRT hoàn chỉnh.
- Nút **TẠM DỪNG / TIẾP TỤC** dừng an toàn trước request kế tiếp; nút **STOP** kết thúc tác vụ, hủy phần tạm chưa ghép và cập nhật lại credit đã sử dụng.
- Thanh thao tác dùng mã màu rõ ràng: tạo audio xanh dương, chọn/tiếp tục xanh lá, tạm dừng vàng và STOP đỏ.
- Hiển thị loại gói của từng API (Free/Paid/Workspace theo dữ liệu ElevenLabs trả về) và dùng chung cơ chế quota cho các workspace hợp lệ.
- Tab Danh sách API có nút xuất toàn bộ key lỗi ra TXT và xóa nhanh key bị vô hiệu hóa/sai quyền.
- Khi toàn bộ API thất bại, hộp lỗi chỉ hiển thị thống kê gọn theo nhóm thiếu credit, Free Tier bị khóa, sai quyền và giới hạn tần suất; không ghép hàng trăm lỗi lặp lại lên màn hình.
- Giao diện DPI-aware, chữ rõ trên màn hình Windows dùng scaling.
- Biểu tượng TAILEMMO được dùng đồng nhất cho cửa sổ kích hoạt, giao diện chính, taskbar và file EXE.
- Thanh thông báo màu được gộp cùng hàng với thanh tiến trình cố định phía dưới: tiến độ, phần trăm và trạng thái tác vụ hiển thị tại một vị trí.
- Nút đang thực thi được khóa tạm thời để tránh gửi yêu cầu trùng lặp.
- Thanh tiến trình nằm cố định dưới thanh thông báo; hàng nút Tạo audio luôn được ưu tiên hiển thị khi cửa sổ thấp hoặc dùng DPI cao.
- Thanh tiến trình dạng phần trăm mượt, không dùng hiệu ứng nhấp nháy; hoàn thành lên 100% rồi trở về 0% một lần.

## Cách chạy

1. Double-click `CHAY_TOOL.bat`.
2. Vào tab **Danh sách API**, nhập mỗi API key trên một dòng rồi lưu.
3. Bấm **Kiểm tra tất cả API** để xem credit, quyền IVC và voice slot.
4. Chọn tab **Tạo audio** hoặc **Clone voice**.

## Tạo bản phân phối Windows

- Chạy `TAO_FILE_PHAN_PHOI.bat` để tạo EXE và ZIP sạch trong thư mục `BAN_PHAN_PHOI`.
- Bản phân phối không chứa `settings.json`, API key cá nhân, hồ sơ khách hàng hoặc private seed của License Admin.

Kết quả nằm trong thư mục `outputs`. Cấu hình chỉ được lưu cục bộ tại `config/settings.json`.
Khi bật **Tạo file SRT khớp giọng**, file `.mp3`/`.pcm` và `.srt` có cùng tên để nhập thẳng vào phần mềm dựng phim.

## Lưu ý theo tài liệu ElevenLabs

- Tool dùng `POST /v1/voices/add` cho Instant Voice Clone.
- Mẫu clone nên sạch, chỉ một người nói, không nhạc nền và có tổng thời lượng dưới 2 phút.
- API key phải có `can_use_instant_voice_cloning=true` mới clone được.
- Model mặc định `eleven_flash_v2_5` hỗ trợ tiếng Việt. Tool cũng cho chọn Multilingual v2 và Eleven v3.
- Community Voice Library không khả dụng qua API cho tài khoản free.
- Tên và giới tính được gửi dạng multipart và `labels` của ElevenLabs.
- Tạo audio và clone có thể tiêu hao credit.
