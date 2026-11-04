# 5 worker, nộp bằng code và evidence lane

Nhánh `feat/five-worker-lanes-dispatch` (tách từ `main` ff35c1a8). Orchestrator không còn tự giao việc, tự gộp code hay tự chụp evidence: code làm, root chỉ spawn worker và gọi 2 tool.

## Vòng lặp của root

1. `dispatch_ready()`: chạy Start Work và claim cho mọi task planner chọn, trả `spawn[]` (mỗi phần tử là một `taskEnvelope`). Root spawn ngay từng worker.
2. `wait_for_update(timeoutMs=55000)`: thức khi có item mới, slot trống (kèm sẵn `dispatch`), worker ghi `worker_result.json`, hoặc deadline gần. Item sắp hết hạn được **code tự nộp** (`autoFinalized`).
3. `finalize_item(backlogItemKey)`: snapshot worktree, sửa `submit.json` cho trung thực (thiếu ảnh thì test đó `NOT_RUN`, item `PARTIAL`), cherry-pick, push, submit. Bản nộp IN_FLIGHT được phát lại nguyên văn, không nộp trùng.

## Làn worker

| Làn | Số slot | Ai được dùng | Thứ tự |
| --- | --- | --- | --- |
| Thường | 3 | mọi task không phải PI/Heavy | deadline ≤ 180 giây → deadline sớm nhất → thứ tự lộ ra (không xếp theo điểm) |
| Riêng | 2 | PI và task Heavy | PI trước, rồi Heavy |

- Heavy = tiêu đề bắt đầu `[Heavy Task]`, hoặc key trong `MTM_HEAVY_KEYS`, hoặc độ khó ≥ `MTM_HEAVY_MIN_DIFFICULTY`.
- Van an toàn: task thường có deadline ≤ 180 giây được mượn slot riêng khi slot riêng đang rảnh và không có PI/Heavy chờ. Tắt bằng `MTM_NATIVE_ALLOW_RESERVED_BORROW=0`.

## Biến cấu hình (do `scripts/install.py` ghi vào `.codex/config.toml`)

| Biến | Giá trị cài sẵn | Ý nghĩa |
| --- | --- | --- |
| `max_concurrent_threads_per_session` | 6 | 5 worker + root (hạ xuống 5 nếu Codex từ chối) |
| `MTM_NATIVE_MAX_WORKERS` | 5 | tổng slot |
| `MTM_NATIVE_NORMAL_WORKERS` | 3 | slot thường |
| `MTM_NATIVE_RESERVED_WORKERS` | 2 | slot riêng PI/Heavy; bỏ trống = chế độ cũ |
| `MTM_NATIVE_ALLOW_RESERVED_BORROW` | 1 | van an toàn cho deadline gấp |
| `MTM_HEAVY_MAX_SECONDS` | 900 | trần thời gian của worker Heavy |
| `MTM_HEAVY_KEYS` | (trống) | ép tay key là Heavy, ví dụ `CR-09,TASK-07` |
| `MTM_HEAVY_MIN_DIFFICULTY` | (trống) | ngưỡng độ khó tự nhận Heavy khi không có prefix |
| `MTM_AUTO_FINALIZE_SLACK` | 150 | tự nộp item có deadline khi còn ≤ 150 giây |
| `MTM_AUTO_FINALIZE_MATCH_SLACK` | 240 | tự nộp mọi item đang chạy khi trận còn ≤ 240 giây |

## Evidence lane (worker)

1. Task cần ảnh: lệnh đầu tiên là `project_runtime.py ... session-start --routes /a,/b`. Server `next dev` chạy nền và biên dịch trong lúc worker sửa code.
2. Chụp mọi ảnh một lần: `project_runtime.py ... run -- node <capture_multi_path> <spec> --cutoff-epoch <evidence_cutoff_local_epoch>`. Ảnh quá mốc cutoff bị bỏ qua và ghi `skipped`, không bao giờ tạo ảnh giả.
3. Xong thì `session-stop`. `finalize_item` và `reconcile_worker` cũng tự dọn server còn sót.

## Trước mỗi trận

```powershell
git switch feat/five-worker-lanes-dispatch
python scripts/update_checksums.py --check     # phải in ALL CHECKSUMS MATCH
python scripts/install.py --team-workspace "D:\Working\Master_the_Machine\Thi\Workspace"
```

Sửa bất kỳ skill, toml hay file `runner/` nào thì chạy `python scripts/update_checksums.py` rồi cài lại. Muốn quay về hành vi cũ: cài lại từ `main`.
