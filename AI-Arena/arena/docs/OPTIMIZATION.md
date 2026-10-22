# Tối ưu token và thời gian cho worker Codex

Đo trên set `xs` (element-web PI React, CardDemo BUG COBOL), cùng model `gpt-6.1-sol`, effort `low`,
sandbox `workspace-write`. Ngày 2026-10-22.

## Token đi đâu (baseline `agent-test-0`)

Token tính phí = input không cache + output. Codex cache gần hết prefix (90–95% input là cache), nên
phần tính phí mỗi lượt gọi chủ yếu là **output của lệnh vừa chạy** cộng phần model viết ra.

| Item | Lượt gọi | Output lệnh | Nặng nhất | Token tính phí |
|---|---|---|---|---|
| element-web | 9 lệnh | ~56k ký tự (~14k token) | `Get-Content` cả file 26k ký tự, 10k ký tự | 23k |
| CardDemo | 6 lệnh | ~60k ký tự (~15k token) | `rg --files` in cả cây 46k ký tự | 20k |

Khởi động container cho `bench.py exec` chỉ 1,4–2,1 s/lệnh: không phải nút thắt. Thời gian nằm ở số lượt
gọi model (5–10 s mỗi lượt) và chính bài test.

## Các đòn bẩy đã thử

| Đòn bẩy | Cách | Tác dụng |
|---|---|---|
| Cắt output lệnh | Codex `-c tool_output_token_limit=2000` | mỗi lệnh tối đa ~2k token vào context |
| Model nói ít | `-c model_verbosity="low"`, `-c model_reasoning_summary="none"` | bớt output token |
| Prompt hệ thống gọn | `-c include_apps_instructions=false`, `include_collaboration_mode_instructions=false`, `project_doc_max_bytes=16384` | bớt input lượt đầu |
| Luật đọc/ghi trong AGENTS.md | chỉ `rg -n` + lát ≤120 dòng, cấm `rg --files`/in cả file, không nhắn trước câu cuối, verify một lần | bớt lượt gọi và output |
| Nhúng đề vào prompt khởi động | `inline_task` (runner) / prompt theo item (worker) | bớt một lượt đọc TASK.md |
| Effort `minimal` | — | **không hỗ trợ** với gpt-6.1-sol (chỉ low/medium/high/xhigh/max) |

## Kết quả

| Cấu hình | PASSED | Token tính phí | Thời gian | Ghi chú |
|---|---|---|---|---|
| baseline | 2/2 | 43k | 2,3' | |
| lean (luật + cap 2,5k + verbosity low) | 2/2 | **24k (−44%)** | 2,1' | |
| lean2 (lean + đề nhúng + cap 2k) | 2/2 | 40k | **1,6' (−30%)** | đọc nhiều lát hơn ở element-web: nhiễu của model với 2 item |

Kết luận: tỉ lệ pass không đổi; thời gian giảm; token giảm mạnh ở lean. Chênh lệch lean/lean2 nằm
trong nhiễu, cần set `s` để chốt. Hai thứ vẫn chưa ép được: Codex luôn gửi một câu mở đầu ("I'll…")
dù bị cấm, và cap output chỉ cắt phần đưa vào context chứ không ngăn agent đọc nhiều lát.

## Kết quả trên set `s` (8 item, 7 stack: React, COBOL, Java, C, Go, JS, Python, Ruby)

| Cấu hình | PASSED | Token tính phí | Phút trung vị / P90 | Mốc cuối |
|---|---|---|---|---|
| baseline | 8/8 | 195k | 1,5 / 2,1 | 3,9' |
| lean | 8/8 | 135k (−31%) | | 3,8' |
| lean2 | 8/8 | **131k (−33%)** | | **3,7'** |

lean2 nhanh bằng hoặc hơn baseline ở 8/8 item và ít token hơn ở 6/8; chỉ element-web tốn hơn (đọc nhiều
lát hơn, nhiễu của model). Chọn **lean2** cho worker thi đấu (đã là cấu hình trong `agent/worker.py`).
Hạ tầng phát hiện khi chạy set s: ảnh Multi-SWE-bench Kotlin không có trên Docker Hub (loại stack Kotlin),
item php-cs-fixer-7998 hỏng (gold patch fail), và `arena` cần `PYTHONUTF8=1` trên Windows vì parser của
`swebench` đọc log bằng cp1252.

## Đã áp dụng vào worker thi đấu

`agent/worker.py`: `LEAN_CONFIG` (các khoá Codex ở trên) được thêm vào mọi `codex exec`; prompt theo
item nói rõ không đọc lại AGENTS.md và không nhắn trước câu cuối. `agent/AGENTS.md`: mục Budget với
các luật đọc lát, verify một lần, commit rồi dừng ngay.

## Còn có thể làm

- Bench test cho Java/TS: lệnh tập trung (`-Dtest=`, `--tests`, `jest -t`), không `clean`, giữ cache build.
- Onboarding đầu trận: build một lần, ghi lệnh test chuẩn vào `PROJECT_NOTES.md` cho mọi worker.
- Đo lại trên set `s` (8 item) để chốt giữa lean và lean2.
