# MASTER THE MACHINE 2026
## Remediation & End-to-End Verification Plan

**Baseline:** `81860902db7978f6f8d923dd087aacd3f8d86f1f`  
**Target:** Production-ready tournament orchestration  
**Phạm vi:** `mtm_core`, `mtm_competition`, cấu hình production, tests và CI. Giữ nguyên kiến trúc bốn package hiện tại.

---

## Phase 0 — Sửa CI và thiết lập baseline đáng tin cậy

**Mục tiêu:** Có bộ kiểm thử xanh trước khi sửa các thành phần nghiệp vụ.

**Công việc**

- Sửa `test_orchestrator_blocks_submission_if_push_fails` trong `tests/test_audit_fixes.py`.
- Cấu hình Git identity cho repository được clone trong test.
- Kiểm tra return code của toàn bộ lệnh `git commit`, `git push` khi chuẩn bị fixture.
- Cập nhật số test thực tế trong README; không hardcode một con số dễ lỗi thời.
- Chạy CI trên Python 3.12 và 3.13.
- Thêm test cho cả trường hợp clone repo rỗng, HEAD chưa tồn tại và Git thiếu identity.

**File liên quan**

`tests/test_audit_fixes.py`, `.github/workflows/ci.yml`, `README.md`, `src/mtm_core/git/repository.py`.

**Acceptance Criteria**

- CI cả hai phiên bản Python thành công.
- Toàn bộ test hiện hành chạy thành công; smoke test cần CLI thật được đánh dấu rõ là chưa chạy, không tính như một lần kiểm chứng thành công.
- Không có test phụ thuộc Git config toàn cục trên máy developer.

---

## Phase 1 — Hardening đường đi Submit (P0)

### P0.1 — Git Push & Revision Verification

**File:** `integration/integration_guard.py`, `integration/git_guard.py`, `orchestrator.py`.

- Production bắt buộc phải có remote Git hợp lệ.
- Không cho phép submit nếu `push_to_remote=False`, remote không tồn tại hoặc push thất bại.
- Xác minh SHA trực tiếp trên remote bằng `git ls-remote` hoặc cơ chế xác thực tương đương; không chỉ dựa vào remote-tracking branch local.
- Chỉ stage các file hợp lệ thuộc Worker patch.
- Kiểm tra đường dẫn, symlink, deletion và thay đổi nằm ngoài phạm vi tác vụ.
- Không dùng `reset --hard` hoặc `clean -fd` trên canonical repository khi có nguy cơ xóa thay đổi không liên quan.
- Thực hiện post-integration verification trước khi push.

**Done khi:** 100% submission trong production được gắn với một commit SHA xác nhận tồn tại trên remote.

### P0.2 — Independent QA & MVS Enforcement

**File:** `verification/qa_gate.py`, `verification/test_policy.py`, `orchestrator.py`, contracts.

- Production mặc định bật kiểm thử độc lập.
- Không chấp nhận `test_results.passed=True` của Worker như bằng chứng duy nhất.
- Thiết lập verification command do Orchestrator hoặc Game Master cung cấp, không tin tưởng tùy ý command do Worker sinh ra.
- Chạy test với timeout, working directory được kiểm soát, lưu exit code và log.
- Ánh xạ mỗi MVS do Game Master công bố với một kết quả kiểm thử.
- Chặn submission nếu một MVS bắt buộc chưa được thực thi hoặc chưa pass.
- Chạy lại bộ test đã xác định sau bước tích hợp patch vào canonical repository.

**Lưu ý:** Nếu Game Master chỉ cung cấp mô tả MVS mà không cung cấp lệnh thực thi, cần thêm lớp chuyển đổi hoặc mapping theo contract chính thức; không tự coi mô tả là test đã pass.

**Done khi:** Không thể submit với test giả, test rỗng, test fail hoặc thiếu MVS bắt buộc.

### P0.3 — Submission Idempotency

**File:** `adapters/official_game_master.py`, `integration/submission_guard.py`, `tasks/store.py`.

- Vô hiệu hóa retry tự động cho `POST /submit`, trừ khi server hỗ trợ idempotency key chính thức.
- Ghi trạng thái `SUBMIT_IN_FLIGHT` bằng thao tác database nguyên tử trước khi truyền request.
- Ngăn hai Worker hoặc hai process submit cùng một task.
- Khi bị timeout hoặc mất kết nối: không gửi lại ngay; kiểm tra trạng thái trên Game Master.
- Nếu trạng thái chưa xác định, giữ submission ở trạng thái cần reconcile.
- Khi restart, xử lý mọi submission đang `IN_FLIGHT` trước khi cho phép tạo submission mới.
- Phân biệt lỗi network, lỗi bị server từ chối và kết quả FAILED của Grader.

**Done khi:** Trong mọi kịch bản timeout, crash hoặc retry được kiểm thử, mỗi task có tối đa một submission thực sự được gửi.

### P0.4 — Grading & Scoring Lifecycle

**File:** `orchestrator.py`, `tasks/store.py`, `integration/submission_guard.py`.

- Bổ sung grading reconciliation loop.
- Theo dõi `ACCEPTED` / `GRADING` cho đến khi nhận `PASSED` hoặc `FAILED`, hoặc đến khi vượt ngưỡng chờ và ghi nhận trạng thái unresolved.
- Tách `provisional_points` khỏi `confirmed_points`.
- Chỉ tính điểm đã được Grader xác nhận vào tổng điểm nội bộ.
- Ngăn submit lại task có grading `FAILED` nếu luật không cho phép.
- Đồng bộ kết quả grading sau restart.

**Done khi:** Trạng thái cuối trong database khớp với Game Master; không cộng điểm chưa được xác nhận.

---

## Phase 2 — Worker Runtime, Preemption và Clock (P1)

### P1.1 — Supervisor Reliability

**File:** `workers/supervisor.py`, `workers/launcher.py`, `runtime/codex.py`.

- Sửa lỗi `_runner()` có thể gọi `launcher_fn()` lần thứ hai nếu lần đầu ném exception.
- Tách xử lý exception Worker khỏi logic kiểm tra function signature.
- Bổ sung cancellation state rõ ràng: `RUNNING`, `PREEMPT_REQUESTED`, `PREEMPTED`, `COMPLETED`, `FAILED`.
- Quản lý subprocess handle và process tree khi hủy Worker.
- Tránh race condition giữa preemption và Worker vừa hoàn thành.
- Cleanup workspace/worktree đúng cách.
- Dùng attempt ID duy nhất thay cho `{task_id}_att1` cố định.
- Đảm bảo concurrency limit của Supervisor và Scheduler thống nhất.

**Done khi:** Mỗi attempt chỉ launch một lần; PI preemption không tạo Worker mồ côi hoặc ghi nhầm kết quả của attempt cũ.

### P1.2 — PAUSED, Deadline và Timeout

**File:** `scheduling/scheduler.py`, `runtime/codex.py`, `orchestrator.py`.

- Sử dụng đồng hồ server làm nguồn thời gian thi đấu.
- Tách wall-clock timeout, active-execution timeout và match-time budget.
- Không để PAUSED làm hao hụt match-time budget.
- Bảo đảm policy PAUSED nhất quán với hành vi process đang chạy.
- Kiểm tra deadline lại ngay trước khi submit.
- Khi trận FINISHED, dừng dispatch task mới và kết thúc Worker theo policy.

**Done khi:** Thử tạm dừng rồi tiếp tục trận không tạo timeout sai hoặc submission quá deadline.

### P1.3 — Token & Cost Budget

**File:** `orchestrator.py`, `runtime/usage.py`, `routing/model_router.py`.

- Giới hạn token theo match và theo task.
- Giảm model/effort trước khi đạt giới hạn.
- Chặn launch task mới khi vượt hard budget.
- Phân biệt usage chính xác, usage ước lượng và usage không được CLI cung cấp.
- Không xem việc giảm `reasoning_effort` là biện pháp giới hạn chi phí duy nhất.
- Báo cáo token, elapsed time, số attempt và chi phí khi có dữ liệu giá đáng tin cậy.

**Done khi:** Vượt ngưỡng ngân sách gây hành động kiểm soát xác định; không âm thầm tiếp tục chạy.

### P1.4 — Backend & Configuration Consistency

**File:** `config/loader.py`, `config/development.yaml`, `config/production.yaml`, `workers/launcher.py`, `runtime/codex.py`.

- Chuẩn hóa `MTM_MODE` và `MTM_ENV` thành một cơ chế chọn environment thống nhất.
- Giữ thứ tự ưu tiên backend: explicit argument → environment override → configuration.
- Nếu backend không hợp lệ, báo lỗi thay vì tự chuyển sang Codex.
- Bắt buộc Worker dùng đúng backend được Orchestrator chọn.
- Kiểm chứng cú pháp CLI, model ID, effort và JSON trace của Codex và Antigravity thực.
- Bật checksum verification theo production mode.
- Xác định rõ project root chứa Skills/manifest, không phụ thuộc tùy ý vào current working directory.

**Done khi:** Chọn Codex chỉ chạy Codex; chọn Antigravity chỉ chạy `agy`; cấu hình không tạo kết quả mơ hồ.

---

## Phase 3 — Củng cố Game Master Contract

**File:** `adapters/official_game_master.py`, `adapters/base.py`, `orchestrator.py`.

- Xác nhận endpoints, authentication, payload và response schema với API/spec chính thức khi có.
- Không mặc định `server_epoch=time.time()` rồi coi đó là thời gian server đã được đồng bộ chính xác.
- Kiểm tra response của `Start Working` trước khi lấy MVS.
- Phân biệt server không có MVS, MVS chưa được mở khóa và lỗi lấy MVS.
- Xử lý rate limiting, 401/403, 409, 429 và 5xx theo từng phương thức HTTP.
- Chỉ retry các thao tác bảo đảm an toàn, hoặc có cơ chế idempotency được xác nhận.
- Bổ sung contract tests cho tất cả endpoint.

**Done khi:** Adapter đạt contract tests và chạy được với Game Master staging/mock HTTP server. Các giả định chưa có API chính thức phải được ghi rõ, không xem là đã xác minh.

---

## Phase 4 — E2E Test Suite

Thực hiện theo bốn tầng, mỗi tầng có báo cáo riêng.

### E2E-A — Component Integration

Không yêu cầu CLI thật hoặc server thật.

Test các đường đi:

| ID | Kịch bản | Kỳ vọng |
|---|---|---|
| A01 | Worker khai báo test pass giả | QA từ chối |
| A02 | Một MVS bắt buộc fail | Không submit |
| A03 | Git push thất bại | Không submit |
| A04 | Remote trả SHA không khớp | Không submit |
| A05 | Submission timeout nhưng server đã nhận | Không gửi lần hai |
| A06 | Grader trả FAILED | Điểm xác nhận bằng 0 |
| A07 | Restart khi `SUBMIT_IN_FLIGHT` | Reconcile an toàn |
| A08 | Worker CLI exit nonzero | Không READY_FOR_REVIEW |
| A09 | Backend không hợp lệ hoặc CLI thiếu | Fail rõ ràng |
| A10 | Patch thay đổi file ngoài phạm vi | Integration từ chối |

### E2E-B — Full Tournament Simulation

Sử dụng Mock Game Master, Git remote local và fake clock để tái hiện trận có ngân sách 2.400 giây mà không cần chờ đủ 40 phút.

Các sự kiện cần mô phỏng theo thứ tự:

1. Game Master cung cấp customer repository và Wave 1.
2. Orchestrator tiếp nhận TASK/CR/BUG.
3. Gọi Start Working, mở khóa MVS.
4. Worker bắt đầu xử lý một CR.
5. Wave 2 xuất hiện với PI khẩn cấp.
6. Supervisor preempt CR, scheduler ưu tiên PI.
7. Match chuyển PAUSED rồi RESUMED.
8. PI được sửa, kiểm thử, commit, push, submit.
9. Grader trả ACCEPTED trước, PASSED sau.
10. Các task còn lại tiếp tục chạy.
11. Chèn sự cố mạng và crash/restart.
12. Game Master trả FINISHED; hệ thống tạo báo cáo cuối trận.

**Kỳ vọng:** Không submit trùng, không mất trạng thái, không cộng điểm trước khi Grader xác nhận và không chạy quá budget.

### E2E-C — Real CLI Execution

Chạy với customer repository giả lập có lỗi thật và test thật.

**Matrix thực thi**

| Backend | Tác vụ | Kỳ vọng |
|---|---|---|
| Codex CLI | BUG đơn giản | Sửa code và chạy test |
| Codex CLI | CR nhiều file | Tạo patch hợp lệ |
| Codex CLI | Worker timeout | Process được dừng đúng |
| Anti CLI (`agy`) | BUG đơn giản | Sửa code và chạy test |
| Anti CLI (`agy`) | CR nhiều file | Tạo patch hợp lệ |
| Anti CLI (`agy`) | Worker timeout | Process được dừng đúng |

Kiểm tra cả prompt delivery, runtime exit code, workspace isolation, trace parsing, token usage và `worker_result.json`.

Không dùng mocked Worker cho tầng này. Nếu một CLI không sẵn có, đánh dấu BLOCKED; không chuyển thành PASS.

### E2E-D — Game Master Staging Integration

Chỉ thực hiện khi có endpoint hoặc môi trường thử nghiệm do ban tổ chức cung cấp.

- Kết nối bằng thông tin xác thực thử nghiệm.
- Đọc backlog và match state.
- Xác minh Start Working và MVS.
- Sử dụng repository và dữ liệu thử nghiệm.
- Kiểm tra nộp và grading khi staging cho phép.
- Không tự động gửi bài vào hệ thống thi chính thức để kiểm thử.

**Done khi:** Contract, credentials, thời gian server và trạng thái grading được xác nhận trên môi trường tích hợp thật.

---

## Phase 5 — Production Readiness & Release Gate

### Lệnh kiểm tra tối thiểu

```bash
python -m pip install -e ".[dev]"
python -m pytest -v
python -m pytest tests/test_e2e_contest_scenario.py -v
python -c "from mtm_core.config.loader import verify_skill_checksums; verify_skill_checksums('.')"
```

Ngoài các lệnh hiện hữu, cần bổ sung các runner hoặc marker riêng cho:

```bash
python -m pytest tests/e2e/ -v
python -m pytest -m live_cli -v
python -m pytest -m staging_game_master -v
```

Các đường dẫn và marker `tests/e2e/`, `live_cli`, `staging_game_master` là **hạng mục cần tạo trong kế hoạch**, chưa phải các test hiện có.

### Definition of Done

- [ ] CI xanh trên Python 3.12 và 3.13.
- [ ] 100% test P0/P1 mới pass.
- [ ] QA độc lập và kiểm chứng MVS bắt buộc trong production.
- [ ] Không có đường đi submit SHA chưa được xác nhận trên remote.
- [ ] Không có submission trùng trong fault-injection tests.
- [ ] Mọi điểm xác nhận đều xuất phát từ kết quả Grader.
- [ ] Preemption, cancellation và PAUSED không tạo tiến trình mồ côi.
- [ ] Codex CLI và Anti CLI hoàn thành smoke tests thực.
- [ ] Mô phỏng đầy đủ vòng đời trận 40 phút pass.
- [ ] Có báo cáo E2E chứa task states, điểm, token, timing, Git SHA và lỗi.
- [ ] Có cấu hình production và hướng dẫn rollback.
- [ ] Commit release được đóng băng bằng SHA và Skills checksums.

**Quy tắc phát hành:** Không coi hệ thống production-ready nếu bất kỳ điều kiện P0 nào còn fail, hoặc nếu các bước tích hợp với CLI/Game Master thật chưa được xác nhận nhưng được báo cáo như đã hoàn tất.

---

## Deliverables

1. Code hoàn thiện các điểm P0/P1.
2. Bộ regression tests và fault-injection tests.
3. E2E tournament simulator có thể tái chạy.
4. Bộ smoke tests Codex/Antigravity.
5. GitHub Actions CI với kết quả kiểm chứng rõ ràng.
6. `E2E_REPORT.md` ghi kết quả theo từng tầng và từng kịch bản.
7. `PRODUCTION_READINESS.md` gồm checklist vận hành, cấu hình và rollback.

**Thứ tự merge:** Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5. Không bỏ qua release gate để chuyển sang thi thật.