# 🎓 FuOverflow Exam App - Hệ Thống Luyện Thi Thông Minh

Chào mừng bạn đến với **FuOverflow Exam App**, ứng dụng luyện thi PWA (Progressive Web App) chuyên sâu, được thiết kế để tối ưu hóa việc ôn tập đề thi trắc nghiệm một cách khoa học nhất.

---

## 🚀 Tính Năng Nổi Bật

### 1. Hệ Thống Bảo Mật (Login System)
*   Màn hình đăng nhập bảo vệ nội dung ôn tập.
*   **Mật mã mặc định:** `fpt123`.

### 2. Hai Chế Độ Học Tập Toàn Diện
*   **📚 Học Tập Chuyên Sâu (Study Hub):**
    *   Sử dụng đồ họa dạng lưới (Grid Map) để quản lý hàng trăm câu hỏi.
    *   Phân loại màu sắc: Câu đã học (Xanh), Câu chưa học (Trắng), Câu đang xem (Tím).
    *   Lọc câu hỏi theo: Mã bộ đề, Số lượng đáp án, hoặc danh sách Đánh dấu sao.
*   **🎯 Thi Thử Thực Chiến (Mock Exam):**
    *   Tự động sinh đề thi ngẫu nhiên (Random Generator).
    *   Tùy chỉnh số lượng câu hỏi (mặc định 50 câu).
    *   Hỗ trợ chế độ "Luyện Trừng Phạt": Làm lại danh sách các câu đã sai sau khi kết thúc bài thi.

### 3. Thuật Toán Ghi Nhớ Thông Minh (Spaced Repetition)
*   Hệ thống tự động ghi lại các câu bạn hay làm sai.
*   Ưu tiên bốc thăm những câu khó này vào đề thi lần sau để bạn học kỹ hơn.

### 4. Chế Độ Toàn Màn Hình (Premium Fullscreen)
*   Giao diện tối giản, tập trung tối đa vào hình ảnh đề thi.
*   Hỗ trợ đầy đủ phím tắt và nút điều hướng mờ ảo.
*   Hiển thị tiến độ làm bài (Ví dụ: 15 / 50) trực quan.

### 5. Biểu Đồ Thống Kê (Analytics Dashboard)
*   Theo dõi lịch sử điểm số của các lượt thi gần nhất.
*   Vẽ biểu đồ dạng đường để bạn thấy được sự tiến bộ của bản thân.

---

## ⌨️ Hệ Thống Phím Tắt (Hotkeys)

Dành cho người dùng máy tính để tối ưu thao tác bằng tay phải (vùng phím số):
*   **Phím 4 / A:** Chọn đáp án A.
*   **Phím 5 / B:** Chọn đáp án B.
*   **Phím 6 / C:** Chọn đáp án C.
*   **Phím + / D:** Chọn đáp án D.
*   **Phím . (dấu chấm) / S:** Đánh dấu sao (Bookmark) để lưu lại câu hay.
*   **Phím Enter:** Chốt đáp án / Chuyển sang câu tiếp theo.
*   **Phím Esc:** Thoát chế độ xem câu lẻ / Thoát Toàn màn hình.
*   **Mũi tên Trái / Phải:** Chuyển câu Lùi / Tiến.

---

## 💽 Quản Lý Dữ Liệu (Backup & Restore)

Để không mất lịch sử học tập khi chuyển từ Máy tính sang Điện thoại hoặc máy khác:
1.  **Bước 1:** Tại máy cũ, chọn môn học và bấm **⬇ Tải Tiến Trình (Backup)**.
2.  **Bước 2:** Lưu file `.json` nhận được.
3.  **Bước 3:** Tại máy mới, mở web app, chọn môn học và bấm **⬆ Nhập Tiến Trình (Restore)** rồi chọn file `.json` đó.

---

## 🛠 Hướng Dẫn Cài Đặt (Netlify)

1.  Truy cập [Netlify Drop](https://app.netlify.com/drop).
2.  Kéo thả toàn bộ thư mục chứa mã nguồn vào trình duyệt.
3.  Web App sẽ được triển khai ngay lập tức.
4.  Để cài đặt lên màn hình chính điện thoại (PWA):
    *   **iOS (Safari):** Bấm Share (nút giữa hàng dưới) -> Chọn "Add to Home Screen".
    *   **Android (Chrome):** Bấm dấu 3 chấm -> Chọn "Install App".

---

**Chúc bạn ôn thi hiệu quả và đạt kết quả cao với FuOverflow!** 🎓💎🏆
