# Cài đặt và sử dụng bộ benchmark

Hướng dẫn cho người muốn đo một agent system mới trên bộ benchmark `arena`, từ cài đặt đến xem
dashboard. Đặc tả bộ benchmark (nguồn, các set, cách chấm) nằm ở [BENCHMARK.md](BENCHMARK.md).

## 1. Yêu cầu

| Thành phần | Dùng để | Ghi chú |
|---|---|---|
| Docker Desktop (amd64) | chạy test/chấm điểm trong môi trường của từng sản phẩm | image kéo theo từng item, 0,5–4 GB mỗi cái |
| Python 3.12 + `uv` | chạy `arena` | `.venv` riêng của project |
| Codex CLI đã đăng nhập | agent runner mặc định (`runner: codex`) | ChatGPT login hoặc API key |
| ~2 GB đĩa cho metadata | `arena setup` | Multi-SWE-bench full 1,8 GB; `--msb mini` nhẹ hơn |

## 1b. Docker engine: cài, cấu hình, vận hành

Mọi bước build/test/chấm chạy trong container, nên Docker là thứ hay gây lỗi nhất. Checklist:

| Bước | Windows | Linux / macOS |
|---|---|---|
| Cài | Docker Desktop (backend **WSL 2**; bật "Use the WSL 2 based engine"), Windows 11 cần bật tính năng WSL + Virtual Machine Platform | Linux: Docker Engine (`apt install docker-ce`), thêm user vào nhóm `docker`; macOS: Docker Desktop hoặc Colima |
| Tài nguyên | Settings → Resources: ≥ 8 GB RAM cho engine, ≥ 60 GB đĩa (ảnh 0,5–4 GB mỗi item; set `full` ~40 ảnh) | tương tự; Linux dùng thẳng tài nguyên máy |
| Khởi động | **Mở Docker Desktop bằng tay** (Start menu) và chờ "Engine running". Không khởi động nó từ shell/sandbox của một app khác: `AppData\Local` bị ảo hoá làm backend crash | `systemctl start docker` |
| Kiểm tra | `docker info` trả về phiên bản server, `docker run --rm hello-world` chạy được | như bên |
| Kiến trúc | ảnh benchmark là **amd64**; máy ARM (Apple Silicon) chạy qua emulation, chậm 2–5 lần | |

Trong lúc chạy benchmark:

- Ảnh được kéo **theo từng item** đúng lúc `check`/`eval` chạy tới item đó (`core.ensure_image`), từ Docker Hub
  (`swebench/*`, `mswebench/*`) và GHCR (`ghcr.io/scaleapi/swe-bench_pro-v2`). Không cần đăng nhập registry.
- Mỗi lệnh `bench.py exec`/`check` tạo một container mới từ ảnh (1–2 s) và xoá sau khi xong; grader cũng vậy.
  Container treo lại sau khi dừng giữa chừng: `docker ps -a --filter name=arena- -q | xargs docker rm -f`.
- Dọn đĩa: `docker images "swebench/*"`, `docker rmi <image>` cho item không còn dùng; `docker system df` để xem
  tổng; `docker builder prune` sau khi build ảnh COBOL.

Lỗi thường gặp:

| Thông báo | Nguyên nhân | Xử lý |
|---|---|---|
| `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine` | engine chưa chạy | mở Docker Desktop, chờ Engine running |
| `pull access denied … repository does not exist` | ảnh không được publish (ví dụ Multi-SWE-bench Kotlin) | item/stack bị loại khỏi set ở lần `sample` sau; không phải lỗi của bạn |
| `UnicodeDecodeError: 'charmap'` khi chấm trên Windows | parser của `swebench` đọc log bằng cp1252 | đặt `PYTHONUTF8=1` |
| gold patch cũng FAILED trong `check` | item hỏng (test hoặc ảnh không khớp) | `sample` tự loại item có `oracle` fail |
| `check`/`exec` chạy > 10 phút | repo Java/TS build cả suite (Multi-SWE-bench) | set nhỏ đã tránh suite này; với item khác, tăng `--timeout-min` |

## 2. Cài đặt một lần

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv swebench datasets pip
.venv/Scripts/python -m pip install multi-swe-bench      # uv từ chối wheel này trên Windows
.venv/Scripts/python -m arena setup                      # tải metadata 5 benchmark, dựng catalog (3.278 item)
.venv/Scripts/python -m arena stats                      # bảng thống kê theo suite và theo stack
```

Trên macOS/Linux thay `.venv/Scripts/python` bằng `.venv/bin/python`. Các set `data/sets/*.json` đã có sẵn
trong repo (cùng item cho mọi máy); chỉ chạy `sample` khi muốn đổi luật chọn, và khi đó commit lại file set.

`setup` chỉ tải mô tả issue, patch và test (metadata). Docker image được kéo về **từng item**,
đúng lúc item đó chạy, nên chi phí tải phụ thuộc set bạn chọn chứ không phụ thuộc kích thước kho.

## 3. Thêm một agent system mới

### 3.1. Agent chạy trên Codex CLI (chỉ khác model/effort/prompt)

Thêm một dòng vào `arena/agents.json`:

```json
"my-agent": {"runner": "codex", "model": "gpt-6.1-sol", "effort": "medium",
             "note": "mô tả ngắn, hiện trên dashboard",
             "price": {"input": 2.5, "cached_input": 0.25, "output": 10}}
```

`price` (USD / 1M token) không bắt buộc; có thì report tính thêm cột USD. Prompt chung cho mọi agent
Codex là `arena/agent/AGENTS.md`. Các tuỳ chọn khác của một agent Codex:

| Khoá | Ý nghĩa | Ví dụ |
|---|---|---|
| `agents_md` | dùng một bản AGENTS.md khác trong `arena/agent/` | `"AGENTS_lean.md"` |
| `inline_task` | nhúng TASK.md vào prompt khởi động (bớt một lượt đọc) | `true` |
| `codex_config` | khoá `-c` thêm cho `codex exec` (cap output, verbosity, MCP server…) | `{"tool_output_token_limit": 2000, "model_verbosity": "low"}` |
| `harness` | chép file của một harness vào thư mục chạy (`arena/harness/<tên>`) | `"ruflo"` |
| `run_root` | thư mục chạy riêng (OpenCode không chịu `%20` trong đường dẫn) | `"D:/arena-runs"` |

`codex-lean2-low` trong `agents.json` là cấu hình tiết kiệm nhất đã đo (xem `OPTIMIZATION.md`).

### 3.2. Harness khác đã nối sẵn: Hermes, OpenCode, Ruflo

| Agent | Runner | Đăng nhập | Ghi chú |
|---|---|---|---|
| `hermes-low` | `hermes` (`hermes --in <run_dir> -z …`, token từ `--usage-file`) | `hermes auth add openai-codex --type oauth` (device code, phiên riêng; **không** import token của Codex CLI) | `hermes` phải có trên PATH (hoặc đặt `"command"` trong agents.json); trên Windows cài vào đường dẫn ngắn (ví dụ `D:\hermes`, `HERMES_HOME`) vì venv của nó vượt 260 ký tự; nếu chạy trong sandbox phải `hermes gateway run` nền trước |
| `opencode-low` | `opencode` (`opencode run --dir … --format json --auto`) | `opencode auth login --provider openai --method "ChatGPT Pro/Plus (headless)"` | nếu đường dẫn clone chứa `%` (ví dụ `AI%20Arena`) đặt `"run_root"` tới một thư mục không có `%` |
| `codex-ruflo-low` | `codex` + `harness: ruflo` + MCP qua `codex_config` | như Codex | `ruflo init` tự ghi vào `~/.codex/config.toml`; hoàn nguyên sau khi chạy tay |

Kết quả so sánh 4 harness trên xs: `REPORT_harness_xs.md`.

### 3.3. Agent system khác (không phải `codex exec`)

Mỗi item được chuẩn bị thành một thư mục làm việc `data/runs/<agent>/<item>/`:

| Trong thư mục | Ý nghĩa |
|---|---|
| `TASK.md` | đề bài: loại item, mô tả (PI chỉ có đoạn đầu), danh sách test MVS với CR/BUG |
| `work/` | mã nguồn sản phẩm ở base commit, là một git repo sạch; agent sửa thẳng vào đây |
| `mvs.tests.patch` | code test MVS (CR/BUG) để đọc; grader tự áp, agent không copy vào `work/` |
| `tools/bench.py` | `exec "CMD"` chạy lệnh trong container của sản phẩm với thay đổi hiện tại; `check` chạy test MVS; `diff` xem thay đổi |
| `bench.json` | địa chỉ bench server cục bộ mà `tools/bench.py` gọi |
| `AGENTS.md` | hướng dẫn cho agent |

Hợp đồng với agent: **đọc `TASK.md`, sửa `work/`, dùng `tools/bench.py` để build/test, rồi dừng**.
Kết quả được lấy bằng `git diff` của `work/` (trừ các file thuộc test patch). Agent không cần biết
gì về Docker hay grader.

Để nối một runner mới, thêm một nhánh trong `arena/runner.py::eval_item` (cạnh các nhánh `codex`, `hermes`,
`opencode`) gọi hệ thống của bạn với `run_dir` và timeout, rồi trả về dict cùng dạng `run_codex`
(`run_hermes` và `run_opencode` là hai mẫu có sẵn):

```python
{"usage": {"input_tokens": .., "cached_input_tokens": .., "output_tokens": ..},
 "timed_out": bool, "exit_code": int,
 "actions": {"commands": n, "bench_check": n, "bench_exec": n, "file_changes": n, "turns": n},
 "first_edit_seconds": int | None, "mvs_green": bool | None}
```

`usage` là bắt buộc để tính token tính phí (tie-breaker credit); các trường còn lại chỉ phục vụ
thống kê. Khai báo agent với `"runner": "<tên runner mới>"` trong `agents.json`.

## 4. Chạy một lượt đánh giá

```bash
.venv/Scripts/python -m arena sample --size s                 # ghi 4 set lồng nhau xs/s/m/full, in set s
.venv/Scripts/python -m arena check --size s                  # hạ tầng: gold patch phải pass, patch rỗng phải fail (không tốn token)
.venv/Scripts/python -m arena eval --agent my-agent --size s  # chạy agent, chấm từng item, ghi data/results/my-agent.jsonl
.venv/Scripts/python -m arena report --size s                 # bảng so sánh mọi agent có kết quả trên set này -> data/report.md
```

Quy ước nên theo:

- **Chạy `check` trước `eval`** trên set mới: lỗi hạ tầng (image hỏng, test chuẩn không pass) sẽ bị
  loại khỏi set ở lần `sample` sau, không tốn token của agent vào item hỏng.
- **So sánh agent trên cùng một size.** `report` tự giới hạn vào các item mà mọi agent đã chạy.
- `eval` tiếp tục được: item đã chấm bị bỏ qua, `--redo` chấm lại. Các set lồng nhau, nên sau
  `eval --size s`, `eval --size m` chỉ chạy phần thêm.
- Chọn size theo câu hỏi cần trả lời (xem BENCHMARK.md §2); `full` nên chia hai cửa sổ quota.
- Trên Windows đặt `PYTHONUTF8=1 PYTHONIOENCODING=utf-8` cho mọi lệnh `arena`: parser log của `swebench`
  đọc file bằng cp1252 và `report` in ký tự `→`.
- **Docker Desktop phải do bạn mở** (Start menu). Mở từ shell sandbox của app Claude thì backend crash vì
  `AppData\Local` bị ảo hoá. Ảnh Multi-SWE-bench Kotlin không có trên Docker Hub; item/stack hỏng được
  `sample` tự loại ở lần sau.

Thư mục kết quả:

| Đường dẫn | Nội dung |
|---|---|
| `data/results/<agent>.jsonl` | một dòng mỗi item đã chấm: resolved, điểm, giây agent/chấm, usage, actions |
| `data/results/logs/<agent>/<item>.log` | log của grader |
| `data/runs/<agent>/<item>/` | thư mục làm việc, `agent.jsonl` (mọi sự kiện Codex), `patch.diff` |
| `data/sets/<size>.json` | danh sách item, deadline từng item, item bị loại và lý do |
| `data/report.md`, `data/stats.md` | output của `report` và `stats` |

## 5. Dashboard

```bash
.venv/Scripts/python -m arena dashboard --size s --agents my-agent codex-medium   # http://127.0.0.1:8780
```

- Mở trước hoặc trong lúc `eval`; trang tự làm mới 2 giây một lần, đọc thẳng `data/results` và
  `data/runs`, không cần khởi động lại khi chạy thêm agent hay đổi set.
- Chọn **set** và **agent** ngay trên trang (URL cập nhật theo, dán được cho người khác). Dải trên cùng:
  số item, điểm tối đa, stack, số item đã chấm, tổng token, tổng phút; thẻ điểm có xếp hạng #1/#2…
- Ba khối: **Match score** (thẻ điểm theo luật thi, có xếp hạng), **Items** (lưới item × agent: điểm
  nhận được, PASSED/FAILED/LATE/TIMEOUT, phút, token, số lệnh; màu vàng là agent đang làm, xám là chưa
  chạy), **Live** (agent đang làm gì: câu cuối nó nói, các lệnh gần nhất và lệnh đang chạy).
- Số liệu trên dashboard và trong `report` là cùng một hàm (`metrics.match_table`), nên hai nơi không
  lệch nhau.

## 6. Dọn dẹp

- Image Docker chiếm nhiều đĩa nhất: `docker images | grep -E "swebench|mswebench|swe-bench_pro|arena"`
  rồi `docker rmi` những image của item không còn trong set.
- `data/runs/` có thể xoá sau khi đã có `data/results`; `report` và dashboard không cần nó, trừ phần Live.
