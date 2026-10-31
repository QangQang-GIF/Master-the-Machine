# Kế hoạch Triển khai Tối ưu hóa Toàn diện Hệ thống MTM 2026

> **Dành cho Agent/Kỹ sư triển khai:** Kế hoạch này được thiết kế theo chuẩn module hóa từng bước. Các bước sử dụng cú pháp checkbox (`- [ ]`) để dễ dàng theo dõi tiến độ.

**Mục tiêu:** 
1. Tối ưu hóa thứ tự chọn task theo chuỗi ưu tiên: `PI` $\rightarrow$ `Task có Deadline (EDF)` $\rightarrow$ `Bug ~ CR` (chỉ số $\text{ROI} = \frac{\text{Điểm}}{\text{Độ khó}}$).
2. Xử lý dứt điểm lỗi rò rỉ worker/slot khi nộp bài thất bại (quá hạn deadline / mã lỗi 409 như ở `BUG-02`).
3. Thiết lập cơ chế **Deadline Hard-Cutoff** (dừng kiểm thử thừa trước hạn 120s để nộp bài cứu điểm) và **Late-Game Fast-track Submit** (chống nghẽn khóa tích hợp ở 3 phút cuối như `BUG-04`).
4. Khóa chặt vai trò Orchestrator: chỉ điều phối và nộp bài, tuyệt đối không làm thay worker.

**Tech Stack:** Python 3.12, SQLite WAL, Git Worktrees, Codex CLI Subagents, JSON Schema 2020-12.

---

## Bảng ánh xạ các File cần can thiệp

| File | Thành phần phụ trách | Nội dung can thiệp |
|---|---|---|
| `team-sdlc-pack/src/mtm_competition/scheduling/native.py` | Scheduler & Coordinator | Bổ sung hàm tính độ khó, sắp xếp Tier 3 theo ROI, Hard-cutoff 120s, rút ngắn lock cuối trận |
| `team-sdlc-pack/runner/mcp_server.py` | MCP Helper & Submission | Bắt lỗi 409/4xx trong `submit_work`: giải phóng ngay `integration_owner`, thu hồi worker, emit `CLOSED` |
| `team-sdlc-pack/.agents/skills/mtm-worker/SKILL.md` | Worker Execution Skill | Thêm quy tắc ưu tiên test logic trước UI screenshot (tối đa 60s cho ảnh) |
| `team-sdlc-pack/.agents/skills/team-orchestrator/SKILL.md` | Orchestrator Skill | Cấm Orchestrator tự chạy test/browser thay worker; nộp ngay `PARTIAL` nếu thiếu bằng chứng |
| `team-sdlc-pack/.codex/agents/team_orchestrator.toml` | Root Agent Profile | Bổ sung chỉ thị nghiêm ngặt về quyền hạn tích hợp |

---

## TASK 1: Nâng cấp Thuật toán Chọn Task (Thứ tự ưu tiên & Chỉ số ROI Điểm/Độ khó)

### 1.1. Bối cảnh & Mục tiêu
- **Thứ tự mong muốn:** $\text{PI} \longrightarrow \text{Task có Deadline} \longrightarrow \text{Bug} \sim \text{CR}$ (sắp xếp theo $\text{ROI} = \frac{\text{Điểm}}{\text{Độ khó}}$).
- **Vấn đề hiện tại:** Code trong `src/mtm_competition/scheduling/native.py` đang hardcode `prio_sub = -points` ở Tier 3, dẫn đến việc luôn chọn task điểm to nhưng tốn thời gian (`CR-01`, `CR-02`) và bỏ rơi task dễ (`BUG-04`, `TASK-04`).

### 1.2. Kế hoạch sửa đổi code
- **File:** `D:\Working\Master_the_Machine\Master-the-Machine-main\src\mtm_competition\scheduling\native.py`

#### Bước 1: Thêm hàm tính Độ khó (Difficulty)
```python
def _calculate_task_difficulty(self, task: dict) -> float:
    """Tính độ khó ước lượng của task dựa trên loại task, từ khóa UI và số lượng kịch bản MVS.
    
    Điểm càng cao -> Task càng phức tạp, tốn thời gian.
    """
    task_type = str(task.get("type", "TASK")).upper()
    
    # 1. Base difficulty theo loại task
    base_diff = {
        "TASK": 1.0,    # Thường là trace data, tính toán, query log
        "BUG": 1.5,     # Sửa 1-2 hàm logic cụ thể
        "CR": 2.5,      # Tính năng mới, tạo hàm mới, nhiều file
        "PI": 2.0       # Điều tra sự cố
    }.get(task_type, 2.0)
    
    # 2. Phạt độ khó nếu dính tới UI/Browser/Screenshot (rất tốn thời gian)
    ui_penalty = 0.0
    text_content = (str(task.get("title", "")) + " " + 
                    str(task.get("description", "")) + " " + 
                    str(task.get("descriptionMd", ""))).lower()
    ui_keywords = ("screenshot", "layout", "sidebar", "responsive", "browser", 
                   "dom", "component", "table", "render", "css")
    if any(k in text_content for k in ui_keywords):
        ui_penalty = 1.0
        
    # 3. Phạt nếu có nhiều kịch bản nghiệm thu (>= 5 MVS/test cases)
    mvs_penalty = 0.0
    mvs = task.get("minimumVerificationScenarios") or task.get("mvs") or []
    if isinstance(mvs, list) and len(mvs) >= 5:
        mvs_penalty = 0.5
    elif isinstance(mvs, str) and mvs.count("TC-") >= 5:
        mvs_penalty = 0.5
        
    return max(0.5, base_diff + ui_penalty + mvs_penalty)
```

#### Bước 2: Cập nhật hàm `_plan(db)`
- Thay đổi logic phân loại Tier và tính toán điểm xếp hạng:
```python
# Multi-level priority:
# Tier 0: Task có Deadline sắp hết hạn (slack <= 180s)
# Tier 1: Product Incident (PI) - tốc độ là tiền bạc
# Tier 2: Task có Deadline (Earliest Deadline First)
# Tier 3: Toàn bộ task còn lại (BUG ~ CR), sắp xếp theo ROI = Điểm / Độ khó

deadline = task.get("deadline_epoch")
is_pi = (task.get("type") == "PI")
has_deadline = (deadline is not None or task.get("deadlineMinutes") is not None)

if deadline is not None and slack <= 180:
    prio_tier = 0
    prio_sub = slack
elif is_pi:
    prio_tier = 1
    prio_sub = slack if slack is not None else 0
elif has_deadline:
    prio_tier = 2
    prio_sub = slack if slack is not None else 9999
else:
    prio_tier = 3
    points = float(task.get("points", 0))
    difficulty = self._calculate_task_difficulty(task)
    roi = points / difficulty
    prio_sub = -roi  # Giá trị ROI càng cao càng được xếp trước (-roi càng nhỏ)
```

### 1.3. Các bước kiểm thử
- [x] Viết test `tests/test_roi_scheduler.py` giả lập 4 task:
  - `TASK-04` (70đ, TASK thuần logic) $\rightarrow \text{ROI} = 70.0$
  - `BUG-04` (80đ, BUG logic) $\rightarrow \text{ROI} = 53.3$
  - `CR-01` (140đ, CR + CSV/UI) $\rightarrow \text{ROI} = 40.0$
  - `CR-02` (120đ, CR + Table/UI) $\rightarrow \text{ROI} = 34.3$
- [x] Chạy test và khẳng định thứ tự xuất phát: `TASK-04` $\rightarrow$ `BUG-04` $\rightarrow$ `CR-01` $\rightarrow$ `CR-02`.

---

## TASK 2: Khắc phục Triệt để Lỗi Rò rỉ Worker khi Submit Lỗi (`BUG-02` Fix)

### 2.1. Bối cảnh & Mục tiêu
- Khi nộp bài bị Game Master từ chối (như `HTTP 409 [BACKLOG_ITEM_CLOSED]` do trễ deadline), hàm `submit_work` trong `runner/mcp_server.py` chỉ raise exception mà không dọn dẹp coordinator.
- Hậu quả: Khóa tích hợp `integration_owner` bị treo, worker vẫn bị tính là `RUNNING` trên Dashboard, làm rò rỉ slot và giảm số lượng worker thực tế.

### 2.2. Kế hoạch sửa đổi code
- **File 1:** `D:\Working\Master_the_Machine\Master-the-Machine-main\runner\mcp_server.py` (hàm `submit_work`)
- **File 2:** `D:\Working\Master_the_Machine\Master-the-Machine-main\src\mtm_competition\scheduling\native.py` (hàm `reconcile_worker`)

#### Bước 1: Bắt lỗi và dọn dẹp trong `runner/mcp_server.py`
Trong khối `except Exception as exc:` của `submit_work`:
```python
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            req_id = getattr(exc, "request_id", None)
            err_code = getattr(exc, "error_code", None) or getattr(exc, "code", None)
            exc_str = str(exc).lower()

            # Kiểm tra xem đây có phải lỗi từ chối dứt điểm (Terminal Rejection) không
            is_terminal_rejection = (
                (status_code == 409 and ("closed" in exc_str or "not allowed" in exc_str))
                or (status_code in (400, 404, 410, 422))
            )

            if is_terminal_rejection and self.native:
                try:
                    # 1. Thu hồi và giải phóng worker ngay lập tức
                    self.native.reconcile_worker(
                        backlog_item_key,
                        confirmed_stopped=True,
                        reason=f"GM_TERMINAL_REJECTED_{status_code}: {exc}",
                        requeue=False  # Chuyển thẳng sang CLOSED, không cho requeue
                    )
                except Exception as rec_err:
                    sys.stderr.write(f"Warning: reconcile_worker failed: {rec_err}\n")

                try:
                    # 2. Giải phóng khóa tích hợp ngay lập tức, không chờ timeout 30s
                    self.native.end_integration(backlog_item_key, success=False)
                except Exception as lock_err:
                    sys.stderr.write(f"Warning: end_integration release failed: {lock_err}\n")

                # 3. Phát event cập nhật Dashboard sang CLOSED
                if self.telemetry:
                    self.telemetry.emit_backlog_status(
                        backlog_id=backlog_item_key,
                        title=self._get_backlog_title(backlog_item_key),
                        status="CLOSED",
                        summary=f"Item closed/rejected by Game Master ({status_code}): {exc}"
                    )
```

#### Bước 2: Tối ưu `reconcile_worker` trong `native.py`
- Khi `requeue=False`: Đảm bảo câu lệnh SQL đặt `phase='CLOSED'`, `worker_id=NULL`, `worktree=NULL`.
- Đồng thời xóa bỏ `integration_owner` nếu task này đang nắm giữ khóa:
```python
if self._meta(db, "integration_owner") == key:
    self._set_meta(db, "integration_owner", None)
    self._set_meta(db, "integration_acquired_at", None)
```

### 2.3. Các bước kiểm thử
- [x] Viết mock test giả lập gọi `submit_work` với một task và trả về `HTTP Error 409: [BACKLOG_ITEM_CLOSED]`.
- [x] Xác minh:
  - Task chuyển sang `phase: CLOSED`.
  - Khóa `integration_owner` trả về `None`.
  - Dung lượng slot khả dụng (`availableSlots`) phục hồi về nguyên trạng (không bị trừ mất 1 slot).

---

## TASK 3: Cơ chế "Deadline Hard-Cutoff" & "Late-Game Emergency Flush"

### 3.1. Bối cảnh & Mục tiêu
- `BUG-02` trễ hạn chỉ vì mất 8.5 phút cố gắng chụp ảnh UI dù code logic và 15 bài test đã pass.
- `BUG-04` làm xong hoàn hảo sau 85s nhưng bị kẹt ở hàng đợi tích hợp vì `CR-03` giữ lock quá lâu ở cuối trận.
- **Mục tiêu:** 
  1. Khi task sắp cạn deadline ($\le 120\text{s}$): Cưỡng bức dừng kiểm thử phụ, gom code nộp ngay.
  2. Khi trận đấu còn $\le 180\text{s}$: Kích hoạt chế độ Fast-track Submit (rút ngắn timeout lock từ 30s xuống 8s).

### 3.2. Kế hoạch sửa đổi code
- **File 1:** `src/mtm_competition/scheduling/native.py`
- **File 2:** `.agents/skills/mtm-worker/SKILL.md`

#### Bước 1: Nâng cấp Emergency Deadline Flush trong `_plan` (`native.py`)
- Tìm đoạn Emergency Deadline Flush hiện tại trong `_plan` và sửa điều kiện:
```python
# Kích hoạt Emergency Flush khi task có deadline và slack <= 120s (thay vì 210s)
if clock_ready and slack is not None and 0 < slack <= 120:
    key = row["key"]
    wt = Path(row["worktree"]) / self.repository_folder
    ans_file = wt / "answers" / key / "submit.json"
    
    # Kiểm tra xem worker đã có sửa đổi mã nguồn hoặc đã tạo submit.json chưa
    has_changes = False
    try:
        diff_res = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"],
                                  capture_output=True, text=True, timeout=5, check=False)
        if diff_res.returncode == 0 and diff_res.stdout.strip():
            has_changes = True
    except Exception:
        pass
        
    if ans_file.is_file() or has_changes:
        # Nếu chưa có submit.json, tạo ngay template PARTIAL từ đề bài
        if not ans_file.is_file():
            ans_file.parent.mkdir(parents=True, exist_ok=True)
            tpl = task.get("start_details", {}).get("submitTemplate") or {
                "schemaVersion": 1, "backlogId": key, "status": "PARTIAL", "evidenceFiles": []
            }
            tpl["status"] = "PARTIAL"
            ans_file.write_text(json.dumps(tpl, indent=2), encoding='utf-8')
            
        emergency_result = {
            "task_id": key, "worker_id": row["worker_id"], "status": "PARTIAL",
            "skill_ids": ["mtm-emergency-flush"], "changed_files": [f"answers/{key}/submit.json"],
            "test_results": [{"command": "emergency-deadline-cutoff", "passed": True, "exit_code": 0}],
            "evidence_paths": [], "elapsed_seconds": 0,
            "note": f"Hard cutoff triggered at {int(slack)}s remaining before deadline"
        }
        db.execute("UPDATE tasks SET phase='READY_TO_INTEGRATE',result=? WHERE key=?",
                   (json.dumps(emergency_result), key))
```

#### Bước 2: Tối ưu Khóa tích hợp cuối trận (`begin_integration`)
- Trong `begin_integration` (`native.py`):
```python
# Nếu trận đấu còn dưới 180s (3 phút) hoặc task này có slack <= 120s:
status_clk, now_clk, rem_clk = self._clock(db)
is_endgame = (rem_clk is not None and rem_clk <= 180)
if is_endgame and lock_duration > 8:
    # Cưỡng bức thu hồi lock của task trước nếu đã giữ quá 8 giây
    db.execute("UPDATE tasks SET phase='READY_TO_INTEGRATE' WHERE key=? AND phase='INTEGRATING'", (owner,))
    owner = None
```

#### Bước 3: Quy tắc Worker "Logic First, UI Second" trong `SKILL.md`
- Cập nhật `.agents/skills/mtm-worker/SKILL.md`:
  > **QUY TẮC BẢO TOÀN ĐIỂM SỐ:**
  > 1. Ưu tiên tuyệt đối: Sửa logic code $\rightarrow$ chạy `pnpm test` (vitest) và `pnpm typecheck` đến khi `passed: true`.
  > 2. Chụp ảnh UI bằng chứng: Chỉ chạy script kiểm tra giao diện khi bước 1 đã pass. Giới hạn tối đa **60 giây** cho việc chạy Playwright/chụp ảnh.
  > 3. Nếu browser bị timeout, render chậm hoặc task còn dưới 2 phút trước deadline $\rightarrow$ Lập tức dừng chụp ảnh, xuất `WorkerResult` với trạng thái `PARTIAL` hoặc `DONE` dựa trên unit test. Không bao giờ để rủi ro giao diện làm mất điểm logic!

---

## TASK 4: Ràng buộc Vai trò Orchestrator (Cấm làm thay Worker)

### 4.1. Bối cảnh & Mục tiêu
- Trong trận đấu, Orchestrator tự ý mở main checkout để chạy browser verify CSV export (`CR-01`) và chụp ảnh contact card (`CR-03`), làm tiêu tốn 4–5 phút và hàng triệu token.
- **Mục tiêu:** Cấm tiệt Orchestrator thực hiện các lệnh shell nặng hoặc mở browser. Orchestrator chỉ làm nhiệm vụ nạp kết quả và nộp.

### 4.2. Kế hoạch sửa đổi Skill & Profile
- **File 1:** `team-sdlc-pack/.codex/agents/team_orchestrator.toml`
- **File 2:** `team-sdlc-pack/.agents/skills/team-orchestrator/SKILL.md`

#### Nội dung cập nhật:
```toml
# Bổ sung vào developer_instructions trong team_orchestrator.toml
"""
STRICT ROLE BOUNDARY:
- Orchestrator is ONLY a coordinator, integrator, and submitter.
- NEVER launch playwright, browser scripts, dev servers, or execute test commands in the main checkout.
- NEVER attempt to complete or fix verification/evidence on behalf of a worker.
- If a worker result reports missing evidence or partial tests, accept it AS-IS and submit as PARTIAL immediately.
- Your sole job upon worker completion: Git cherry-pick/merge -> Verify submit.json schema -> Git push -> submit_work RPC -> receive receipt.
"""
```

---

## Tóm tắt Tiến độ Triển khai Dự kiến

- [x] **Bước 1:** Cập nhật hàm `_calculate_task_difficulty` và sửa logic xếp thứ tự Tier 3 theo `prio_sub = -roi` trong `native.py`.
- [x] **Bước 2:** Bổ sung cơ chế cleanup khi submit lỗi 409 trong `runner/mcp_server.py`.
- [x] **Bước 3:** Cập nhật Hard-cutoff 120s và rút ngắn lock 8s cuối trận trong `native.py`.
- [x] **Bước 4:** Cập nhật hướng dẫn vai trò Orchestrator trong `team_orchestrator.toml` và `SKILL.md`.
- [x] **Bước 5:** Chạy bộ test hồi quy của orchestrator (`pytest`) để đảm bảo hệ thống vượt qua toàn bộ unit tests hiện hữu.
