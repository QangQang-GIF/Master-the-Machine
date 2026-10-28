# CHECKLIST VẬN HÀNH THI ĐẤU — MASTER THE MACHINE 2026
*(Dành riêng cho Thí sinh / Operator — Các bước thực hiện trước giờ thi)*

> **LƯU Ý QUAN TRỌNG:**  
> Giai đoạn giải đề, code, chạy test, commit, push git và nộp bài (Wave 1, Wave 2, Wave 3) hoàn toàn do **Codex CLI tự động thực hiện 100%**.  
> Thí sinh **KHÔNG CAN THIỆP** vào code trong suốt trận đấu theo quy định *"Người rời máy"* của Ban Tổ Chức.  
> Dưới đây là **toàn bộ những gì Thí sinh cần làm** khi nhận đề thi mới.

---

## 0. Thông tin nhận từ Ban Tổ Chức (BTC)
Khi nhận trận đấu mới, bạn sẽ có các thông tin sau:
- **Game Master URL:** `https://masterthemachine.tech`
- **Match ID:** Mã trận đấu gồm 8 ký tự (Ví dụ: `NGSKELHS`)
- **Team ID:** `BAN` (Tên đội: `[GIF] Bảnh Team`)
- **API Key:** `gm_live_...`
- **Team Repo:** `https://github.com/lqdung863/mtm-ban` (branch `main`)

---

## 1. Checklist 4 Bước Thực Hiện (Thời gian: ~2-3 phút)

### Bước 1: Khởi động MTM Player Desktop
Mở 1 Terminal mới và chạy:
```bash
node /Volumes/WorkSpace/Project/mtm-player/app/scripts/desktop.mjs --workspace /Volumes/WorkSpace/Project/Master-the-Machine
```
*Giao diện MTM Player sẽ mở tại trình duyệt: `http://127.0.0.1:54321`.*

---

### Bước 2: Clone repository đề thi của trận đấu
Mở Terminal thứ 2, clone code trận đấu vào thư mục `matches/<MATCH_ID>`:
```bash
cd /Volumes/WorkSpace/Project/Master-the-Machine

# Clone repo vào matches/<MATCH_ID> (nếu chưa clone)
git clone https://github.com/lqdung863/mtm-ban matches/<MATCH_ID>

# Nếu repo đã có sẵn từ trước, chỉ cần pull mới nhất:
# cd matches/<MATCH_ID> && git checkout main && git pull
```
*Tạo file môi trường nếu dự án yêu cầu:*
```bash
cp matches/<MATCH_ID>/<MATCH_ID>/.env.example matches/<MATCH_ID>/<MATCH_ID>/.env 2>/dev/null || true
```

---

### Bước 3: Cấu hình Match ID & Chuẩn bị Môi trường
1. Mở file `AGENTS.md` tại thư mục gốc, cập nhật block `MTM-PLAYER:BEGIN`:
   - Đổi `"matchId": "<MATCH_ID>"`
   - Đổi `"folder": "<MATCH_ID>"`
   - Đổi `checkoutRoot`: `/Volumes/WorkSpace/Project/Master-the-Machine/matches/<MATCH_ID>`
   - Đổi `matchRoot`: `/Volumes/WorkSpace/Project/Master-the-Machine/matches/<MATCH_ID>/<MATCH_ID>`
2. Mở trình duyệt vào `http://127.0.0.1:54321`:
   - **Match ID:** Điền mã trận đấu (ví dụ: `NGSKELHS`).
   - **Model:** Chọn **`gpt-6-luna`** | Reasoning Effort: **`low`** *(Đã kiểm chứng tối ưu chi phí ~1.5M token và tốc độ nhanh nhất)*.
   - Nhấn nút **"Init / Prepare"**.

---

### Bước 4: Kiểm tra trạng thái và "RỜI MÁY"
- Nhìn màn hình Dashboard MTM Player: trạng thái báo **`ENVIRONMENT_READY`** (hoặc `PREPARE_COMPLETE`).
- Kiểm tra trang Game Master: `https://masterthemachine.tech/match/<MATCH_ID>` hiển thị trạng thái chuẩn bị xong.
- Khi trận đấu chính thức bắt đầu (trạng thái chuyển sang `IN_PROGRESS`):
  - **DỪNG MỌI THAO TÁC TRÊN BÀN PHÍM.**
  - Codex CLI sẽ tự động bắt sự kiện từng Wave, giải quyết 5/5 task, chạy test, commit, push git và nộp bài tự động.
  - Thí sinh chỉ cần theo dõi điểm số nhảy trên màn hình Live Dashboard của Ban Tổ Chức!

---

## 2. Xử lý sự cố nhanh (Dành riêng cho Operator)

| Tình huống | Thao tác khắc phục |
|---|---|
| **Nút "Start Work" trên GM chưa kích hoạt sau khi Prepare xong** | Chạy lệnh curl gửi tín hiệu hoàn tất chuẩn bị lên GM:<br>`curl -X POST "https://masterthemachine.tech/api/v1/matches/<MATCH_UUID>/preparation/complete" -H "Authorization: Bearer <API_KEY>"` |
| **Hết dung lượng ổ đĩa (`no space left on device`)** | Dọn dẹp log cũ:<br>`rm -f /Users/nxc/.gemini/antigravity/brain/*/.system_generated/steps/*/output.txt` |
| **Muốn xem tiến độ Codex đang làm đến đâu** | Mở snapshot trên trình duyệt:<br>`http://127.0.0.1:54321/<MATCH_ID>/round01/snapshot` |
