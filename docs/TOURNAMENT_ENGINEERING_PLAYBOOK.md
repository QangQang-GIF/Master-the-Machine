# BỘ KỸ THUẬT VÀ QUY TRÌNH THI ĐẤU TOURNAMENT (MTM SDLC PLAYBOOK)
> **Cập nhật & Đóng băng sau trận đấu thành công: `BKMYBY77`**  
> **Kết quả ghi nhận:** Hoàn thành 9/9 Task Backlog (`BUG-01`, `BUG-02`, `BUG-03`, `BUG-04`, `TASK-04`, `CR-01`, `CR-02`, `CR-03`, `PI-01`), Game Master trả về HTTP 201 Succeeded, Match Status: `FINISHED`.

---

## 1. TỔNG QUAN KIẾN TRÚC & NGUYÊN TẮC VÀNG

Trong các trận đấu lập trình tự động (Agentic SDLC Tournament), tốc độ và sự ổn định phụ thuộc vào 3 yếu tố cốt lõi:
1. **Không nghẽn tài nguyên phần cứng:** Ổ cứng không bị tràn (`ENOSPC`), RAM không bị chiếm dụng bởi giao diện đồ họa (GUI).
2. **Thực thi lệnh Windows không lỗi:** Xử lý triệt để đường dẫn batch scripts (`pnpm.cmd`, `pwsh`).
3. **Tuân thủ 100% hợp đồng chấm thi (Zero Grader Rejection):** Đúng cổng database, không fake bằng chứng, checksum toàn vẹn và không over-engineering.

---

## 2. KỸ THUẬT 1: STORAGE & CACHE SANDBOX (ĐỔI 100% SANG Ổ D)

### Vấn đề thực tế
* Ổ `C:` trên máy dự thi thường có dung lượng hạn chế. Khi Next.js compile, Vitest sinh file tạm và Prisma tải query engine binary, hệ điều hành mặc định ghi vào `%TEMP%` (`C:\Users\...\AppData\Local\Temp`), dẫn đến lỗi:
  ```text
  Error: ENOSPC: no space left on device, write
  <turn_aborted> The previous turn was interrupted on purpose
  ```

### Giải pháp kỹ thuật đã triển khai
1. **Tạo vùng đệm chuyên dụng trên ổ D:** `D:\Temp`, `D:\Temp\prisma`, `D:\Temp\npm-cache`, `D:\Temp\uv`, `D:\Temp\pip`.
2. **Cập nhật Biến môi trường hệ thống (User Environment Variables):**
   * `TEMP = D:\Temp`
   * `TMP = D:\Temp`
   * `PRISMA_ENGINES_CACHE_DIR = D:\Temp\prisma`
   * `npm_config_cache = D:\Temp\npm-cache`
   * `UV_CACHE_DIR = D:\Temp\uv`
   * `PIP_CACHE_DIR = D:\Temp\pip`
3. **Tự động inject trong code [`runner/project_runtime.py`](file:///D:/Working/Master_the_Machine/Master-the-Machine-main/runner/project_runtime.py):**
   Mọi tiến trình con (`prepare`, `doctor`, `build`, `test`, `web_session`) trong hàm `_build_env()` đều tự động được ép trỏ về `D:\Temp`:
   ```python
   d_temp = Path("D:/Temp")
   if d_temp.is_dir():
       env["TEMP"] = str(d_temp)
       env["TMP"] = str(d_temp)
       env["TMPDIR"] = str(d_temp)
       env.setdefault("PRISMA_ENGINES_CACHE_DIR", str(d_temp / "prisma"))
       env.setdefault("npm_config_cache", str(d_temp / "npm-cache"))
       env.setdefault("UV_CACHE_DIR", str(d_temp / "uv"))
       env.setdefault("PIP_CACHE_DIR", str(d_temp / "pip"))
   ```

---

## 3. KỸ THUẬT 2: WIN32 SUBPROCESS RESOLUTION (CHỐNG LỖI WINERROR 193)

### Vấn đề thực tế
* Trên Windows, lệnh `shutil.which('pnpm')` trong Python thường nhặt phải file script shell POSIX không đuôi trong thư mục global npm (`C:\Users\...\AppData\Roaming\npm\pnpm`). Khi truyền vào `subprocess.run(shell=False)` sẽ văng lỗi hệ điều hành:
  ```text
  OSError: [WinError 193] %1 is not a valid Win32 application
  ```

### Giải pháp kỹ thuật đã triển khai
Trong cả `runner/project_runtime.py` và `runner/web_session.py`, bổ sung bộ phân giải ưu tiên phần mở rộng Windows:
```python
if sys.platform == "win32" and cmd_args:
    base_name = cmd_args[0]
    resolved = None
    for ext in (".cmd", ".exe", ".bat"):
        cand = shutil.which(base_name + ext if not base_name.lower().endswith(ext) else base_name)
        if cand and Path(cand).suffix.lower() in {".cmd", ".exe", ".bat"}:
            resolved = cand
            break
    if not resolved:
        resolved = shutil.which(base_name)
    if resolved:
        if Path(resolved).suffix.lower() in {".cmd", ".bat"}:
            cmd_args = ["cmd.exe", "/c", resolved] + cmd_args[1:]
        elif Path(resolved).suffix.lower() == ".exe":
            cmd_args[0] = resolved
```

---

## 4. KỸ THUẬT 3: POSTGRESQL 16 & QUYỀN TRUY CẬP DATABASE

### Vấn đề thực tế
* Ban tổ chức kiểm tra nghiêm ngặt `doctor.ps1`. Nếu database không đáp ứng quyền `SET session_replication_role = replica` hoặc không có quyền `CREATEDB`, doctor sẽ trả về `NOT_READY` (exit code 2).
* Ngoài ra, `ProjectRuntime` ép buộc kiểm tra loopback: `if host not in {"localhost", "127.0.0.1", "::1"}: raise ValueError`.

### Quy chuẩn cấu hình
1. **Port hoạt động:** Chạy PostgreSQL 16 trên port `5433` (Data path: `D:\PostgreSQL\16\data`).
2. **Phân quyền người dùng `freelanceos`:**
   ```sql
   ALTER ROLE freelanceos WITH CREATEDB SUPERUSER;
   GRANT SET ON PARAMETER session_replication_role TO freelanceos;
   ```
3. **Xử lý tiến trình nền (Daemon):**
   * Nếu máy tính tắt đột ngột hoặc tiến trình bị ngắt, file `D:\PostgreSQL\16\data\postmaster.pid` có thể tồn tại dạng rác làm PostgreSQL không thể khởi động lại.
   * Lệnh khởi động sạch:
     ```powershell
     Remove-Item "D:\PostgreSQL\16\data\postmaster.pid" -Force -ErrorAction SilentlyContinue
     & "D:\PostgreSQL\16\bin\postgres.exe" -D "D:\PostgreSQL\16\data"
     ```

---

## 5. KỸ THUẬT 4: ĐỒNG BỘ MÃ BĂM TOÀN VẸN (SKILL CHECKSUM INTEGRITY)

### Vấn đề thực tế
* Khi Orchestrator chuẩn bị trận đấu, hàm `configure_session` sẽ gọi `verify_skill_checksums(_pack_root)`. Hàm này đối chiếu SHA256 của từng file chạy với `releases/checksums.sha256`.
* Nếu chúng ta chỉnh sửa code runner hoặc skills mà quên cập nhật file checksums, Agent sẽ bị dừng ngay lập tức tại bước `MATCH_LOGIN (BLOCKED)`.

### Quy chuẩn tính toán mã băm SHA256
* Hàm `verify_skill_checksums` chuẩn hóa ký tự xuống dòng từ Windows CRLF (`\r\n`) thành Unix LF (`\n`) trước khi băm:
  ```python
  file_bytes = target_file.read_bytes().replace(b"\r\n", b"\n")
  actual_hash = hashlib.sha256(file_bytes).hexdigest()
  ```
* Bất cứ khi nào cập nhật file trong pack, chạy script đồng bộ checksum cho cả 3 thư mục:
  1. `Thi\Workspace\team-sdlc-pack\releases\checksums.sha256`
  2. `Thi\Workspace\team-sdlc-pack\runtime\releases\checksums.sha256`
  3. `Master-the-Machine-main\releases\checksums.sha256`

---

## 6. KỸ THUẬT 5: HEADLESS EVIDENCE SANDBOX (CHỤP ẢNH BẰNG CHỨNG SIÊU TỐC)

### Vấn đề thực tế
* Agent mở trình duyệt Chromium thông thường (headed), gõ form login, click từng nút, chờ tải trang... làm mất 45 - 60 giây cho mỗi bằng chứng UI, ngốn CPU và GPU.

### Giải pháp kỹ thuật Headless Sandbox
1. **Chạy ngầm 100% (Headless Chromium):**
   Sử dụng Playwright với Chromium không cửa sổ:
   ```javascript
   const browser = await chromium.launch({
     headless: true,
     args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
   });
   ```
2. **Sử dụng Server Production (`next start` hoặc Web Session Pre-warmed):**
   Vì project đã được compile static pages ở bước prepare, trang web phản hồi trong **dưới 10ms**.
3. **Template chụp ảnh 1 dòng lệnh [`scripts/capture_template.mjs`](file:///D:/Working/Master_the_Machine/Master-the-Machine-main/scripts/capture_template.mjs):**
   ```bash
   node scripts/capture_template.mjs answers/<TASK_ID>/evidence /<route> <filename>.png [TabName]
   ```
   * Thời gian hoàn tất: **1.5 - 2.5 giây**.
   * Chất lượng: Độ phân giải 1440x900 full-page, pixel hoàn hảo, đạt 100% điểm grader.

---

## 7. KỸ THUẬT 6: PHÒNG CHỐNG OVER-ENGINEERING & FIXTURE MISSING

1. **Zero Fixture Missing:**
   * Sử dụng client `company_tools` (`qa_data`) để tải tự động fixture chính thức của BTC về thư mục: `answers/<key>/evidence/<key.lower()>-test-data.json`.
   * Tuyệt đối **không tự bịa (fabricate)** dữ liệu mẫu hoặc sinh placeholder PNG giả (`png_fallback.py` bị cấm).
2. **Chạy Official Check Script Trước Khi Submit:**
   * Khi trong đề thi có file `challenge/<KEY>-run-checks.mjs`, luôn thực thi lệnh này để sinh file `test.log` và `typecheck.log` đạt chuẩn định dạng ban tổ chức yêu cầu (`command: ...`, `exit_code: 0`).
3. **Nguyên tắc Thay đổi Tối thiểu (Minimal Diff):**
   * Chỉ sửa đúng các dòng code gây bug hoặc tính năng được yêu cầu. Không tự ý refactor lại kiến trúc, không đổi thư viện UI/CSS, không cài thêm dependency không cần thiết.

---

## 8. DANH MỤC KIỂM TRA NHANH TRƯỚC MỖI TRẬN ĐẤU (PRE-MATCH CHECKLIST)

- [x] **Ổ cứng:** Đảm bảo `D:\Temp` tồn tại, ổ D còn trên 20 GB trống.
- [x] **PostgreSQL 16:** Đang lắng nghe trên port `5433` (`SELECT 1`).
- [x] **Kiểm tra phân phối:** Chạy `python scripts/check-distribution.py --team-workspace ...` báo `ALL_VERIFIED READY`.
- [x] **Mã băm đóng băng:** Chạy `verify_skill_checksums` trả về `SUCCESS: ALL CHECKSUMS MATCH!`.
- [x] **Git Repository:** Nhánh `main` và nhánh tính năng đã được push đồng bộ lên origin.
