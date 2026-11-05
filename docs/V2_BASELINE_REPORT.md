# V2 Phase 0 — Baseline V1 Report

- Baseline: `feat/five-worker-lanes-dispatch` @ `f4d8058` (khớp plan; nạp từ five-worker-lanes.bundle, prerequisite `ff35c1a` = origin/main)
- Nhánh V2: `feat/adaptive-hierarchical-orchestrator-v2` (tạo từ baseline, chưa sửa code)
- Môi trường chạy: Linux cloud container, Python 3.13, chạy bằng root, không có pytest-timeout, cài editable thất bại (dùng pythonpath=src)

## Checksum
`python scripts/update_checksums.py --check` -> ALL CHECKSUMS MATCH

## Test (`-m "not live_cli and not staging_game_master"`)
732 test thu thập + 15 deselected: **727 passed, 5 failed, 8 skipped** (151s)

| Test fail | Dòng lỗi | Nghi nguyên nhân (CHƯA xác minh) |
|---|---|---|
| test_agent_upgrade.py::test_dispatcher_read_only_preexisting_file | assert not True | Quyền file Windows/read-only, khác trên Linux |
| test_agent_upgrade.py::test_dispatcher_fail_closed_on_posix_unreadable_file | DID NOT RAISE | Chạy bằng root nên bỏ qua quyền file |
| test_challenger2_adversarial.py::test_preexisting_windows_read_only_and_restricted_attributes | file không read-only | Thuộc tính read-only kiểu Windows |
| test_stream_deadlock.py::test_high_volume_stream_pause_resume_deadline_freeze | Elapsed 1.17s | Phụ thuộc thời gian/máy |
| test_web_session.py::test_session_shares_environment_and_reaps_server_tree | descendant survived teardown | Quản lý process tree khác Windows |

Cả 5 đều có vẻ phụ thuộc nền tảng (Windows/root/timing). Cần chạy lại trên máy Windows của dự án để xác nhận; đến lúc đó coi 5 test này là "baseline không xác định".

## Không chạy được / bỏ qua
- `live_cli` và `staging_game_master` (cần CLI thật và Game Master staging) — 15 deselected
- 8 test skipped theo điều kiện của chính test
- Lint: repo không có cấu hình ruff; ruff mặc định báo 1289 lỗi, không dùng làm gate
- Benchmark V1 (deterministic replay, Practice Match): chưa thiết lập, thuộc phần tiếp theo của Phase 0

## Exit gate Phase 0
Chưa đạt hoàn toàn: còn thiếu benchmark V1 và xác nhận 5 test fail trên Windows.
