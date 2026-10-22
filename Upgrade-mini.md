## Mini Plan: Đồng bộ Orchestrator ↔ Worker CLI

Mục tiêu: Orchestrator chạy bằng Codex CLI thì Worker sử dụng Codex CLI; Orchestrator chạy bằng Antigravity 2.0 thì Worker sử dụng Anti CLI (`agy`). Không tự động chuyển sang CLI khác nếu chưa được cấu hình.

### Hiện trạng trong source

Sau khi kiểm tra code hiện tại:

- `WorkerLauncher` đang khởi tạo `CodexRuntime(prefer_codex=True, allow_agy_fallback=True)`.
- `CodexRuntime` đã có `build_agy_cmd()`, nhưng Antigravity chỉ hoạt động như fallback, chưa phải backend được chọn chủ động.
- `ModelRouter` chỉ đọc `codex_model` và `codex_effort`, mặc dù `config/model_profiles.yaml` đã có cấu hình `agy_model` và `agy_effort`.
- `TaskEnvelope` có giá trị mặc định cho `model` và `reasoning_effort`, dễ ghi đè quyết định routing.

### Kế hoạch triển khai

| Bước | File cần sửa                                | Nội dung                                                                                                                                                             |
| ---- | ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | `orchestrator.py`                           | Thêm cấu hình `worker_backend`: `codex` hoặc `antigravity`. Orchestrator truyền lựa chọn xuống Worker.                                                               |
| 2    | `contracts/task.py`, `contracts/routing.py` | Bổ sung trường `backend`; chuyển `model` và `reasoning_effort` sang cơ chế override rõ ràng, tránh default che mất routing.                                          |
| 3    | `routing/model_router.py`                   | Khi backend là `codex`, chọn `codex_model/effort`; khi là `antigravity`, chọn `agy_model/effort` từ YAML.                                                            |
| 4    | `workers/launcher.py`, `runtime/codex.py`   | Chọn CLI theo `backend` một cách tường minh. `codex` chạy Codex CLI, `antigravity` chạy `agy`. Nếu CLI được chọn không khả dụng thì báo lỗi, không âm thầm fallback. |
| 5    | `tests/`                                    | Thêm unit test và integration test cho hai backend, model routing, timeout, lỗi CLI và trace parsing.                                                                |

### Luồng sau khi bổ sung

CompetitionOrchestrator

worker_backend = codex | antigravity

ModelRouter → WorkerLauncher

Chọn model, effort, CLI theo backend

Codex Worker

`codex exec`

Codex model profiles

Antigravity Worker

`agy` CLI

Antigravity model profiles

RuntimeResult → QA Gate

Chuẩn hóa kết quả, trace, tokens và trạng thái Worker

### Cấu hình mong muốn

```
orchestrator:
  worker_backend: antigravity # codex | antigravity

worker:
  strict_backend: true
  allow_fallback: false
```

Tiêu chí hoàn thành:

- Chạy Orchestrator với `codex` → 100% Worker được khởi chạy bằng Codex CLI.
- Chạy Orchestrator với `antigravity` → 100% Worker được khởi chạy bằng `agy` CLI.
- Model và effort được chọn đúng theo từng backend.
- Giữ nguyên `WorkerResult`, QA Gate và quy trình submission hiện tại.
- Không xảy ra trường hợp Orchestrator chọn Antigravity nhưng Worker tự chuyển sang Codex.

Lưu ý: Cần kiểm chứng cú pháp và các tùy chọn thực tế của Anti CLI trong Antigravity 2.0 trước khi hoàn thiện adapter, vì code hiện tại mới có command builder cho `agy`, chưa chứng minh tương thích đầy đủ với phiên bản đó.

Phạm vi đề xuất: Đây là một thay đổi nhỏ ở lớp runtime selection/routing, không cần viết lại Orchestrator hoặc kiến trúc Worker.