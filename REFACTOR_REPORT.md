# Báo Cáo Tái Cấu Trúc Toàn Diện: Master The Machine 2026 (Codex CLI Migration)

**Ngày hoàn thành**: 2026-10-28  
**Mục tiêu**: Chuyển đổi toàn bộ hệ thống sang cơ chế thực thi duy nhất **Codex CLI (`codex-cli 0.164.0`)**, tinh giản triệt để dead weight, chuẩn hóa constants tập trung, bám sát Digital Cuder wire contract và đạt chuẩn xuất xưởng `READY` theo [`Upgrade2.md`](file:///Volumes/WorkSpace/Project/Master-the-Machine/Upgrade2.md).

---

## 1. Tóm tắt kết quả kiểm định chất lượng

| Chỉ số / Bài kiểm tra | Trước Refactor | Sau Refactor | Trạng thái |
|---|:---:|:---:|:---:|
| **Full Pytest Test Suite** | 569 passed, 23 skipped (có test rác) | **558 passed, 22 skipped, 0 failed** | **PASS (100%)** |
| **Contract & Journal Tests** | 86 passed | **100 passed, 0 failed** | **PASS (100%)** |
| **Distribution Check (Node.js)** | PASS | **ALL_VERIFIED -> READY** | **PASS (Exit 0)** |
| **Distribution Check (Python)** | PASS (có điểm mù) | **ALL_VERIFIED -> READY** (đã test `mtm.constants`) | **PASS (Exit 0)** |
| **Runtime YAML File I/O** | Có đọc đĩa runtime | **Zero File I/O (In-memory constants)** | **HOÀN THÀNH** |
| **Fallback Endpoints / Multi-Login** | 11 fallback routes | **0 fallback (Chỉ 2 official endpoints)** | **TRIỆT TIÊU** |
| **Argument Stripping (`TypeError`)** | Có cắt xén đối số | **Bị xóa hoàn toàn (Strict Signature)** | **AN TOÀN** |
| **Tournament Runtime Synchronization** | Lệch pha pack | **Khớp 100% giữa `src/` và `team-sdlc-pack/`** | **ĐỒNG BỘ** |

---

## 2. Chi tiết các giai đoạn thực thi

### Giai đoạn 1: Loại bỏ triệt để Dead Weight & Nền tảng thừa (R1)
Đã xóa bỏ hoàn toàn 8 thư mục và tệp tin platform/framework không phục vụ thi đấu trực tiếp:
1. `src/mtm_training/` (toàn bộ pipeline huấn luyện / learning offline).
2. `src/mtm_arena/` (simulator và metrics cũ).
3. `AI-Arena/` (374 tệp tin benchmark và COBOL test harness không dùng trong giải).
4. `src/mtm_backends/` (lớp trừu tượng đa backend không cần thiết).
5. `src/mtm_competition/routing/model_router.py` & `src/mtm_core/contracts/routing.py` (loại bỏ cơ chế model routing động và scoring).
6. `tests/test_arena_and_training.py` & `tests/test_mini_plan_backend.py`.
7. `CodexRuntime` (`src/mtm_core/runtime/codex.py`): Tinh giản thành một luồng thực thi duy nhất gọi trực tiếp `codex exec`. Xóa bỏ hoàn toàn nhánh fallback sang Antigravity (`build_agy_cmd`). Lỗi là lỗi ngay (`fail-closed`), không âm thầm retry chéo backend.
8. `WorkerLauncher`: Cố định model thi đấu `gpt-6.1-sol`, reasoning effort `medium`.

### Giai đoạn 2: Chuẩn hóa Constants & Digital Cuder Wire Contract (R2)
1. **Module Constants tập trung (`src/mtm/constants.py` & `src/mtm_core/constants.py`)**:
   - Model: `MODEL = "gpt-6.1-sol"`, `REASONING_EFFORT = "medium"`, `CONCURRENCY = 1`.
   - Timeouts: `MATCH_DURATION_SECONDS = 2400.0` (40 phút), `DEFAULT_TASK_TIMEOUT_SECONDS = 420.0` (7 phút), `TOOL_TIMEOUT_SECONDS = 120.0`, `HTTP_TIMEOUT_SECONDS = 15.0`.
   - Wire Endpoints: `DEFAULT_GAME_MASTER_ORIGIN = "https://masterthemachine.tech"`, `START_WORK_PATH = "/api/v1/matches/{match_id}/backlogs/start"`, `SUBMIT_PATH = "/api/v1/matches/{match_id}/submissions"`, `AUTH_LOGIN_PATH = "/api/v1/auth/login"`.
   - Security: Tập trung danh sách cấm stage vào Git (`.env*`, `exam-info.json`, file nhạy cảm).
2. **Cắt đứt hoàn toàn 11 fallback legacy**:
   - `OfficialGameMasterAdapter`: Rút gọn đúng 2 endpoint REST chính thức: `start_working` và `submit_work`. Tất cả các hàm legacy cũ (`get_match_state`, `list_backlog`, `get_task`, `get_mvs`, `get_submission_status`, fallback `POST /tasks/{id}/submit`) đều ném `NotImplementedError`.
   - Cắt bỏ multi-endpoint login probing, chỉ đăng nhập duy nhất qua `POST /api/v1/auth/login`.
   - Cô lập bộ bóc tách HTML Next.js chỉ cho trang spectator (`/match/{match_id}`).
3. **Gia cố Guard & SubmissionJournal**:
   - Xóa bỏ `argument stripping on TypeError` trong `SubmissionGuard` (không cắt xén đối số để giấu lỗi).
   - Bảo toàn lock idempotency trong SQLite khi gặp lỗi HTTP 409 `ALREADY_SUBMITTED`.
   - `PreSubmissionGitGuard` và `IntegrationGuard` liên kết trực tiếp với bộ pattern cấm trong `constants.py`.

### Giai đoạn 3: Chuẩn hóa Profiles & Skills cho Codex CLI (R3)
1. **4 Profile Worker chuyên biệt**:
   - `team_bug_worker.toml`: Quy trình Reproduce -> Locate Defect -> Minimal Patch -> Tests.
   - `team_task_worker.toml`: Quy trình Understand Requirement -> Inspect Project -> Implementation -> Tests.
   - `team_cr_worker.toml`: Quy trình Impact Analysis -> Implementation -> Regression Verification -> Evidence.
   - `team_pi_worker.toml`: Quy trình Immediate Triage -> Minimal High-Confidence Fix -> Verification.
   - Giữ nguyên các profile tĩnh bắt buộc theo quy định phân phối (`team_orchestrator`, `team_repo_scout`, `team_feature_worker`, `team_quality_checker`).
2. **Cấu hình Codex CLI (`.codex/config.toml`)**:
   - Cấu hình server `mtm_minimal` chạy qua `python3.14 -m runner.mcp_server` với stdio JSON-RPC.
   - Sửa lỗi đường dẫn lặp `team-sdlc-pack/team-sdlc-pack` trong cấu hình pack.

### Giai đoạn 4: Đồng bộ Phân phối & Kiểm thử Hoàn chỉnh (R4)
1. **Đồng bộ hóa 100% `team-sdlc-pack/src/`**:
   - Sao chép toàn bộ `src/mtm/` và các cập nhật mới sang `team-sdlc-pack/src/` và `team-sdlc-pack/runtime/src/`.
   - Dọn sạch các thư mục rác trong pack.
   - Xác thực import độc lập trong môi trường pack (`PYTHONPATH=team-sdlc-pack/src:...`) thành công 100%.
2. **Vá điểm mù kiểm tra phân phối**:
   - Cập nhật `check-distribution.py` để kiểm tra bắt buộc `import mtm; import mtm.constants; from mtm_core import constants;`.
   - Cả 2 công cụ `check-distribution.mjs` và `check-distribution.py` đều thông báo **`READY` (Exit 0)**.

---

## 3. Kiến trúc Đích Thực Tế

```text
Game Master (https://masterthemachine.tech)
    │
    ▼ [Official Digital Cuder Wire Contract]
GameMasterClient (src/mtm_competition/adapters/official_game_master.py)
    │
    ▼ [JSON-RPC Stdio Transport]
Thin MCP Server (runner/mcp_server.py)
    │
    ▼
Team Orchestrator (team_orchestrator)
    │
    ├── BUG  → Bug Worker (team_bug_worker / .agents/skills/mtm-bug-worker)
    ├── TASK → Task Worker (team_task_worker / .agents/skills/mtm-task-worker)
    ├── CR   → Change Request Worker (team_cr_worker / .agents/skills/mtm-cr-worker)
    └── PI   → Incident Worker (team_pi_worker / .agents/skills/mtm-pi-worker)
                 │
                 ▼
          Verification Gate (.agents/skills/mtm-verification)
                 │
                 ▼
             Git Guard (src/mtm_competition/integration/git_guard.py)
                 │
                 ▼
         Submission Journal (src/mtm_competition/tasks/journal.py)
                 │
                 ▼
             Submit (POST /api/v1/matches/{matchId}/submissions)
```

Toàn bộ hệ thống hiện tại hoạt động theo nguyên tắc: **Một đường chạy duy nhất, ít abstraction, lỗi là lỗi (fail-closed), không tự cứu bằng fallback ngầm.**
