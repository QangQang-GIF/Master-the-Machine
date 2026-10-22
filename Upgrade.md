# PHÂN TÍCH DỰ ÁN MASTER THE MACHINE 2026

Repository: [QangQang-GIF/Master-the-Machine](https://github.com/QangQang-GIF/Master-the-Machine?utm_source=chatgpt.com) · Nhánh `main` · Commit `193eda6` (22/10/2026)

## 1. Đánh giá tổng quan

Dự án đang đi theo hướng phù hợp với mục tiêu của cuộc thi Daddy Shark — Master The Machine 2026: xây dựng một Digital Project Team có khả năng tự tiếp nhận, phân tích, thực hiện, kiểm thử và nộp các yêu cầu phát triển phần mềm mà không cần con người điều khiển trong trận.

Điểm đáng chú ý là source không chỉ chứa các prompt điều khiển AI. Repository đã được tổ chức thành một hệ thống phần mềm có các lớp quản lý nhiệm vụ, điều phối công việc, thực thi Codex, kiểm thử, quản lý Git, mô phỏng thi đấu và cải tiến Agent Skills.

# 4

Package Python chính

Core, Competition, Arena, Training

# 7

Native Agent Skills

Khởi tạo, worker và verification

# 55

File trong `src/`

Bao gồm các file khởi tạo package

# 472

File trong repository

Bao gồm AI-Arena và dữ liệu benchmark

Nhận định ban đầu: Đây là nền tảng có định hướng kiến trúc tốt, đặc biệt ở việc tách logic điều phối xác định khỏi suy luận của LLM. Tuy nhiên, sự hiện diện của các module trong source chưa đồng nghĩa toàn bộ chu trình thi đấu đã sẵn sàng chạy với Game Player và Game Master thật.

Phần đánh giá dưới đây phân biệt giữa chức năng đã hiện thực trong code, chức năng mới được mô tả trong tài liệu, và những khả năng cần kiểm chứng bằng thử nghiệm tích hợp.

## 2. Cuộc thi thực sự yêu cầu điều gì?

Tôi sử dụng hai tài liệu của BTC làm chuẩn để đánh giá source, thay vì coi README hoặc tài liệu thiết kế trong repository là bằng chứng rằng chức năng đã hoàn thiện.

Thời gian trận đấu

# 40 phút

Điểm tối đa

# 1.000

Trận đấu mô phỏng việc một vendor tiếp nhận hệ thống phần mềm của khách hàng, giải quyết backlog liên tục và bàn giao kết quả. Sau khi bắt đầu trận, Agent phải hoạt động độc lập; người tham gia không được trực tiếp điều khiển máy, trừ phiên xin tiếp cận máy có trừ điểm.

### 2.1. Bốn loại backlog

| Loại   | Yêu cầu chính                       | Điều kiện quan trọng               |
| ------ | ----------------------------------- | ---------------------------------- |
| `TASK` | Thực hiện một nhiệm vụ chung        | Không có MVS                       |
| `CR`   | Thay đổi theo specification mới     | Pass toàn bộ MVS, có evidence      |
| `BUG`  | Sửa lỗi, không gây regression       | Pass toàn bộ MVS, có evidence      |
| `PI`   | Điều tra, xử lý production incident | Ưu tiên cao nhất, chấm theo tốc độ |

MVS (Minimum Verification Scenarios) là các kịch bản kiểm chứng tối thiểu. BTC chỉ cung cấp sau khi Agent thực hiện `Start working`; ngoài MVS, Grader còn có những tình huống kiểm thử khác.

### 2.2. Những luật ảnh hưởng trực tiếp đến kiến trúc Agent

1. Agent phải phát hiện backlog mới có thể xuất hiện bất kỳ lúc nào.
2. Phải thực hiện `Start working` trước khi submit.
3. Mỗi item chỉ được submit một lần sau khi server đã `ACCEPTED`.
4. Phải nộp kèm `gitRevision` tương ứng với commit đã push.
5. Deadline được tính theo đồng hồ Game Master, không tính thời gian trận `PAUSED`.
6. Điểm lúc nhận bài chỉ là tạm thời; `PASSED` hoặc `FAILED` từ Grader mới quyết định kết quả.
7. Xin vào máy sửa Agent bị trừ 50 điểm/lần, tối đa 5 phút/lần.

Riêng `PI`, hệ số điểm theo thứ tự hoàn thành hợp lệ là 100%, 90%, 75% và 60% cho các đội còn lại. `FAILED` hoặc không hoàn thành nhận 0 điểm. Nói cách khác, Agent cần tối ưu cả khả năng giải đúng, tốc độ, tính ổn định và credits.

### 2.3. Lịch thi và môi trường

Theo PDF giới thiệu, máy đội chơi cần cài Codex CLI. Game Player của BTC khởi tạo trận và spawn Agent chạy trên GPT models; Game Master Server được dùng cho luyện tập và thi chính thức.

1. 28/09 – 23/10/2026: Đăng ký

   Hạn cuối đăng ký là 23/10/2026.
2. 26/10 – 06/11/2026: Trial Round

   Thi trực tuyến, kiểm tra Agent trên môi trường BTC.
3. 09/11 – 13/11/2026: Face-off Round

   60 đội thi tại các địa điểm tổ chức.
4. 23/11 – 27/11/2026: Grand Final

   Top 6 thi tại Hà Nội.

PDF cũng nêu khoản hỗ trợ $50 Codex Pay-As-You-Go cho 60 đội đăng ký đầu tiên. Đây là lý do quản lý token và credits cần là một chức năng vận hành thực sự, không chỉ là số liệu báo cáo sau trận.

## 3. Kiến trúc phần mềm hiện tại

Source được tổ chức thành bốn package có mục đích khá rõ ràng.

BTC Game Player / Game Master

Phát hành backlog, cung cấp MVS, nhận submit và trả kết quả

`mtm_competition`

Điều phối trận

Adapter

Task Store

Scheduler

Routing

Worker Launcher

QA / Submit Guard

`mtm_core` — Runtime dùng chung

Codex CLI

Git / Worktree

Contracts / Trace

`mtm_arena`

Benchmark, mô phỏng trận và đo hiệu năng

`mtm_training`

Phân tích thất bại và cải tiến Skills offline

Đây là mô hình kiến trúc logic theo source; sơ đồ không khẳng định mọi kết nối đã hoạt động với hệ thống BTC thật.

### 3.1. Phân tích từng package

| Package           | Đã có trong source                                                         | Đánh giá                                                                          |
| ----------------- | -------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `mtm_core`        | Codex Runtime, trace parser, Git worktree, task/result contracts           | Nền tảng tương đối rõ, có thể tái sử dụng                                         |
| `mtm_competition` | Orchestrator, scheduler, priority, worker launcher, QA và submission guard | Đúng trọng tâm nhưng còn thiếu các kết nối và hành vi runtime quan trọng          |
| `mtm_arena`       | Scorecard, simulator; hệ benchmark lớn nằm trong `AI-Arena/`               | Có giá trị cho thử nghiệm trước trận, chưa thay thế được trận giả lập sát thực tế |
| `mtm_training`    | Failure classifier, lesson extractor, skill evolver và promotion gate      | Có pipeline cải tiến cơ bản nhưng chủ yếu mới ở mức heuristic                     |

Quyết định tách `mtm_training` khỏi môi trường production là hợp lý. Trong trận cần vận hành ổn định bằng Skills đã chốt; việc để Agent tự sửa logic điều khiển ngay giữa trận có thể gây rủi ro khó kiểm soát.

### 3.2. Bảy Agent Skills

Repository đã có các Skills:

- `mtm-init`: hướng dẫn chung, tiết kiệm context/token và tuân thủ contracts.
- `mtm-task-worker`: nhiệm vụ chung.
- `mtm-cr-worker`: Change Request.
- `mtm-bug-worker`: tái hiện, xác định nguyên nhân và sửa bug.
- `mtm-pi-worker`: ưu tiên khôi phục nhanh sự cố production.
- `mtm-verification`: kiểm thử và kiểm tra MVS.
- `mtm-evidence`: thu thập bằng chứng.

Các Skills có phân công đúng nghiệp vụ và tương đối ngắn gọn, phù hợp chiến lược giảm token. Tuy nhiên, code hiện chỉ đưa danh sách tên Skills vào `TASK.md`; chưa bảo đảm Codex trong worktree của khách hàng thực sự nhìn thấy và kích hoạt đúng các Skills tương ứng.

Theo cơ chế Codex, Skills được phát hiện từ các thư mục theo phạm vi repository hoặc user, và có thể kích hoạt rõ ràng bằng tên Skill. Vì vậy cần kiểm tra quy trình cài/pin Skills và cách invoke khi Game Player khởi chạy.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://learn.chatgpt.com\&sz=32)

ChatGPT Learn



## 4. Đối chiếu source với yêu cầu của cuộc thi

Đây là phần quan trọng nhất: kiểm tra xem những gì repository đã xây dựng có thực sự đáp ứng luật thi hay chưa.

| Yêu cầu của BTC                     | Thành phần trong source                  | Kết quả đánh giá                                                   |
| ----------------------------------- | ---------------------------------------- | ------------------------------------------------------------------ |
| Tự phát hiện backlog mới            | `sync_backlog()`                         | Có chức năng đọc backlog, nhưng chưa có vòng lặp giám sát liên tục |
| `Start working` trước khi làm       | `CompetitionOrchestrator.step()`         | Đúng thứ tự cơ bản                                                 |
| Nhận MVS sau Start working          | `GameMasterAdapter`                      | Có interface, chưa kết nối API thật                                |
| Phân loại TASK/CR/BUG/PI            | `SkillRouter`, `ModelRouter`             | Đã triển khai                                                      |
| Ưu tiên PI khi xuất hiện            | `PriorityQueue`, `PreemptionPolicy`      | Có ưu tiên khi chọn việc, chưa preempt worker đang chạy            |
| Tự code và test                     | `WorkerLauncher`, `CodexRuntime`         | Có khả năng gọi Codex CLI, chất lượng xác minh còn yếu             |
| MVS bắt buộc pass                   | `QAGate` và Skills                       | Có hướng dẫn, chưa có cơ chế xác minh độc lập đầy đủ               |
| Push commit trước submit            | `IntegrationGuard`, `GitGuard`           | Thiếu bước push và xác minh remote                                 |
| Chỉ submit một lần                  | `SubmissionGuard`, `TaskStore`           | Có guard cơ bản, chưa an toàn trước lỗi mạng hoặc restart          |
| Theo dõi `PASSED` / `FAILED`        | `get_submission_status()`                | Interface tồn tại, nhưng chưa được sử dụng trong orchestrator      |
| Deadline theo server và PAUSED      | `TournamentScheduler`, `SubmissionGuard` | Chưa đúng luật                                                     |
| Tiết kiệm Codex credits             | `ExecutionTrace`, `TokenUsage`           | Có đo token, chưa có giới hạn chi phí toàn trận                    |
| Hoạt động không cần người can thiệp | `CompetitionOrchestrator`                | Chưa chứng minh được khả năng vận hành trọn 40 phút                |

Kết luận của bảng này: Repository đã có phần lớn các khối xây dựng cần thiết, nhưng chưa ghép chúng thành một hệ thống thi đấu thực chiến hoàn chỉnh.

## 5. Những vấn đề kỹ thuật cần sửa

### P0 — Các vấn đề có thể làm Agent không thi được hoặc mất điểm trực tiếp

P0.1 — Kết nối Game Master hiện chỉ là placeholder

File [official_game_master.py](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/src/mtm_competition/adapters/official_game_master.py?utm_source=chatgpt.com) chưa thực hiện HTTP request tới hệ thống BTC. `list_backlog()` trả danh sách rỗng; `start_working()` trả ACK giả lập; `submit()` trả `PENDING`.

Vì vậy, với adapter hiện tại, Agent không thể tự nhận và nộp backlog trên hệ thống thật. Tại ngày 22/10, đây là phần phụ thuộc vào việc BTC cung cấp giao thức chính thức, không nhất thiết là lỗi thiết kế của đội.

P0.2 — Chưa push Git commit trước khi submit

Trong [integration_guard.py](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/src/mtm_competition/integration/integration_guard.py?utm_source=chatgpt.com), code chép các file đã thay đổi từ worktree về repository chính, `git add -A`, sau đó `git commit`.

Tuy nhiên, không có bước `git push` trong pipeline này. [GitGuard](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/src/mtm_competition/integration/git_guard.py?utm_source=chatgpt.com) chỉ xác nhận SHA cục bộ.

Theo luật, `gitRevision` phải là commit đã được push. Đây là lỗi tương thích quy trình nộp bài có mức ưu tiên rất cao.

P0.3 — QA Gate có thể chấp nhận kết quả chưa thật sự được kiểm thử

[qa_gate.py](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/src/mtm_competition/verification/qa_gate.py?utm_source=chatgpt.com) xác minh schema, sự tồn tại của file và trạng thái các test mà Worker báo cáo, nhưng không tự chạy lại test để kiểm chứng kết quả.

Đặc biệt, nếu `test_results` là danh sách rỗng, vòng kiểm tra không phát hiện test thất bại. [WorkerLauncher](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/src/mtm_competition/workers/launcher.py?utm_source=chatgpt.com) còn có nhánh tự tổng hợp `READY_FOR_REVIEW` khi phát hiện file thay đổi, kể cả khi không có test result, và không kiểm tra mã thoát khác 0 của runtime trong nhánh không timeout.

Với CR và BUG, điều này tạo nguy cơ nộp bài mà chưa chứng minh được tất cả MVS đã pass.

P0.4 — Đồng hồ và deadline chưa tuân theo Game Master

`TournamentScheduler` tự tính 40 phút bằng `time.time()` kể từ khi object được khởi tạo. `SubmissionGuard` cũng so deadline với thời gian máy cục bộ.

Luật yêu cầu dùng đồng hồ server, loại trừ thời gian `PAUSED`. Nếu máy đội và Game Master lệch giờ hoặc trận bị tạm dừng, scheduler có thể đánh giá sai thời gian còn lại và thời hạn nộp.

P0.5 — Chưa phân biệt ACCEPTED với PASSED

[submission_guard.py](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/src/mtm_competition/integration/submission_guard.py?utm_source=chatgpt.com) đánh dấu đã submit và có thể ghi điểm tối đa khi server trả `ACCEPTED`.

Nhưng `ACCEPTED` chỉ xác nhận nhận bài, còn Grader mới quyết định `PASSED` hay `FAILED`. Orchestrator hiện không gọi `get_submission_status()` để chờ kết quả chính thức.

Hệ quả là Agent có thể tưởng mình đã giành điểm trong khi bài vẫn đang được chấm hoặc đã thất bại.

### P1 — Các vấn đề ảnh hưởng mạnh đến chiến thuật và tính ổn định

#### 5.6. Chưa xử lý PI mới xuất hiện khi Worker đang chạy

Repository có `PreemptionPolicy.should_preempt()`, nhưng chính sách này chưa được tích hợp vào vòng điều phối thực tế.

`CompetitionOrchestrator.step()` gọi `launcher.launch()` theo kiểu đồng bộ. Nếu đang xử lý CR mất vài phút, orchestrator không đồng thời polling để phát hiện PI mới và quyết định dừng công việc đang chạy.

Trong khi đó, PI là loại có độ ưu tiên cao nhất và điểm phụ thuộc mạnh vào tốc độ.

Đề xuất: Chuyển Worker sang mô hình tiến trình bất đồng bộ được giám sát. Orchestrator tiếp tục đọc backlog trong lúc Worker chạy, đồng thời có cơ chế hủy, lưu lại trạng thái hoặc tái lập công việc bị ngắt.

#### 5.7. `ModelRouter` có thể bị vô hiệu hóa bởi giá trị mặc định

Trong `TaskEnvelope`, các giá trị mặc định gồm:

```
model = "gpt-6.1-sol"reasoning_effort = "medium"time_budget_seconds = 420
```

Nhưng WorkerLauncher chọn cấu hình bằng biểu thức như:

```
effort = env_dict.get("reasoning_effort") or routing.reasoning_effort
```

Khi envelope đã chứa `medium`, lựa chọn của router không còn tác dụng. Chẳng hạn, `ModelRouter` muốn cấp `high` cho PI nhưng Worker có thể vẫn chạy `medium`.

Ngoài ra, `sync_backlog()` có đọc thời gian budget từ item, nhưng `step()` dựng một envelope mới không chuyển tiếp trường này, khiến budget lại quay về mặc định.

Cần phân biệt rõ giá trị do BTC cung cấp, giá trị do scheduler quyết định và giá trị mặc định khi không có cấu hình.

#### 5.8. Recovery và cơ chế submit một lần chưa đủ an toàn

`TaskStore` có SQLite, event log và kiểm tra item đã submit. Đây là nền tảng tốt. Tuy nhiên:

- Mặc định orchestrator dùng `":memory:"`; restart sẽ mất lịch sử.
- Request submit ra mạng được thực hiện trước khi lưu một bản ghi in-flight chắc chắn.
- Nếu mạng timeout sau khi Game Master đã nhận bài, client không biết submit có thành công hay chưa.
- Chưa có cơ chế đối soát trạng thái với Game Master trước khi quyết định gửi lại.
- `REJECTED` có thể được đưa trở lại queue theo state machine thiết kế, nhưng scheduler hiện không chủ động làm vậy.

Nên áp dụng trạng thái như `SUBMIT_PENDING` → `SUBMIT_IN_FLIGHT` → `ACCEPTED` → `PASSED/FAILED`, với lưu trữ bền vững và kiểm tra lại trạng thái server sau lỗi.

#### 5.9. Rủi ro khi tích hợp thay đổi từ worktree

Hiện `IntegrationGuard` chủ yếu chép các file trong `worker_result.changed_files`, sau đó chạy `git add -A` trên repository chính.

Cách này có thể làm mất thay đổi kiểu xóa/đổi tên, vô tình stage file không liên quan hoặc tích hợp thay đổi dựa trên một commit đã cũ. Đặc biệt, chưa có bước chạy test trên đúng revision sẽ được push và submit.

Đề xuất sử dụng patch hoặc cherry-pick được kiểm soát, xác minh base revision, kiểm tra conflict, chạy verification trên snapshot tích hợp cuối cùng rồi mới push.

#### 5.10. Production config chưa được thực thi đầy đủ

`config/production.yaml` đã khai báo frozen Skills, checksum verification, giới hạn concurrency và các chính sách nộp bài. File `releases/checksums.sha256` cũng đã có.

Tuy nhiên, tôi chưa thấy pipeline production thực sự kiểm tra hash Skills trước khi launch hoặc bắt buộc tắt Antigravity fallback.

Điều này tạo khoảng cách giữa policy được khai báo và policy được thực thi.

## 6. AI-Arena: Phần có giá trị nổi bật nhất cho việc tối ưu

Trong repository, `AI-Arena/` không chỉ là dữ liệu mẫu. Đây là bộ công cụ benchmark có hệ thống riêng, bao gồm:

- SWE-bench Verified, Multilingual, Pro và Multi-SWE-bench.
- Bộ COBOL tự xây, với test và Docker harness.
- Adapter cho các hệ thống benchmark khác nhau.
- Công cụ đánh giá, xuất báo cáo và dashboard.
- Nhiều cấu hình Agent để so sánh Codex thuần với những harness bổ sung.

Theo tài liệu benchmark của dự án, catalog được tổng hợp từ 3.278 item, 12 ngôn ngữ và 99 repository. Đây là số liệu catalog được tác giả báo cáo, không phải 3.278 bài đã được chạy và chấm trong phiên phân tích này. Dữ liệu tải từ các nguồn benchmark và Docker images không được commit đầy đủ lên GitHub.

### 6.1. Kết quả benchmark đã được báo cáo

Từ [REPORT_lean_s.md](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/AI-Arena/arena/docs/REPORT_lean_s.md?utm_source=chatgpt.com), cấu hình Lean2 cho kết quả sau trên bộ `s` gồm 8 item, cùng một model Codex ở reasoning effort thấp:

| Chỉ số                      | Baseline | Lean2    |
| --------------------------- | -------- | -------- |
| Số item giải thành công     | 8/8      | 8/8      |
| Thời gian trung vị mỗi item | 1,5 phút | 0,9 phút |
| Billable token mỗi item     | 24k      | 16k      |
| Điểm/phút theo benchmark    | 138      | 190      |

Hiệu quả Lean2 so với baseline

Số liệu báo cáo trên 8 benchmark item; nhỏ hơn là tốt hơn.

Baseline

Lean2

00,651,31,952,6Thời gian (phút)Billable token (10k)

Về mặt định hướng, đây là bằng chứng hữu ích cho phương pháp đọc source có chọn lọc, hạn chế tool output, giảm giải thích thừa và tập trung sửa đúng chỗ. Nhưng cỡ mẫu 8 item là nhỏ, và chưa kiểm tra đầy đủ yêu cầu 40 phút, nhiều wave, một repo chung hay phối hợp nhiều backlog.

Một thí nghiệm khác trong [REPORT_harness_xs.md](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/AI-Arena/arena/docs/REPORT_harness_xs.md?utm_source=chatgpt.com) cho thấy các harness bổ sung tốn billable token hơn Codex thuần trên hai item đều pass. Do đó, việc giữ Codex CLI làm coding engine, chỉ tự phát triển control plane phía trên là lựa chọn hợp lý với bằng chứng hiện có.

### 6.2. Điểm yếu của benchmark so với thi thật

| Khía cạnh | AI-Arena hiện tại                      | Yêu cầu trận thực chiến                                |
| --------- | -------------------------------------- | ------------------------------------------------------ |
| Thời gian | Thường giới hạn từng item độc lập      | Một đồng hồ chung 40 phút                              |
| Backlog   | Đánh giá tuần tự                       | Nhiều item và wave xuất hiện bất ngờ                   |
| Git       | Mỗi benchmark item có môi trường riêng | Các bài trong trận cùng ảnh hưởng repository được giao |
| Grading   | Hidden tests của benchmark             | Game Master/Grader của BTC                             |
| PI        | So tốc độ giữa các Agent benchmark     | So thời điểm `PASSED` với đội khác                     |
| Credits   | Đo billable token                      | Phải theo dõi ngân sách thực tế toàn trận              |
| Stack     | Phủ nhiều ngôn ngữ                     | Stack sản phẩm BTC chưa biết trước                     |

Tài liệu [VS_CONTEST.md](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/AI-Arena/arena/docs/VS_CONTEST.md?utm_source=chatgpt.com) cũng tự xác nhận các khoảng cách này.

Một thiếu hụt đáng chú ý là benchmark hiện chưa có bài chạy tương ứng `.NET` và Angular, dù tài liệu dự án xác định đây là các stack cần chuẩn bị.

Đề xuất: Không mở rộng Arena chỉ để tăng số lượng bài. Ưu tiên tạo một bộ benchmark mô phỏng chính xác hành vi trận đấu: một repo chung, nhiều wave, một deadline tổng, sự cố PI bất ngờ và một lần submit duy nhất.

## 7. Self-learning: Có pipeline, nhưng chưa phải cơ chế tự học hoàn chỉnh

`mtm_training` hiện gồm bốn phần:

Execution Trace / WorkerResult

Failure Analysis

Nhận diện lỗi như timeout, over-edit, token waste, regression

Lesson Extractor & Skill Evolver

Tạo quy tắc xử lý mới và đề xuất phiên bản Skill

Promotion Gate

So sánh pass rate/token rồi quyết định nâng cấp

Ý tưởng này phù hợp để cải thiện Agent trước trận mà không tạo ra learning loop khó kiểm soát trong production.

Tuy nhiên, code thực tế còn đơn giản:

- `FailureClassifier` phần lớn phân loại bằng các ngưỡng cố định, ví dụ số file thay đổi hoặc token tiêu thụ.
- `LessonExtractor` sinh một số quy tắc mẫu cố định, chưa thật sự tổng hợp đa dạng bài học từ trace.
- `SkillEvolver` chủ yếu thêm một mục hướng dẫn mới vào cuối file `SKILL.md`.
- `PromotionGate` nhận sẵn các số liệu đánh giá thay vì tự chạy một quy trình held-out A/B bắt buộc.

Có cả một sai lệch nhỏ cần sửa: comment trong `promotion.py` nói token không được tăng quá 10%, nhưng điều kiện trong code chấp nhận đến 15%.

Vì vậy, nên gọi đây là nền tảng self-improvement offline bước đầu, không nên coi là Agent đã có khả năng tự học và tự tối ưu đã được xác minh đầy đủ.

## 8. Đánh giá bộ kiểm thử và khả năng triển khai

Tôi kiểm đếm được 42 hàm test trong bảy file Python `tests/test_*.py`. Các nhóm test bao phủ:

| Nhóm                 | Số test | Nội dung                                     |
| -------------------- | ------- | -------------------------------------------- |
| Contracts / QA       | 13      | Schema, ID, file, evidence, path escape      |
| Competition pipeline | 13      | State, scheduler, submit, mock E2E, recovery |
| Runtime              | 7       | Codex command, timeout, usage                |
| Git & Config         | 3       | Git, worktree, config                        |
| Arena & Training     | 3       | Metrics, failure classifier, skill evolution |
| Trace parser         | 2       | Codex/Antigravity trace                      |
| Smoke worker         | 1       | Chạy worker với CLI khi có sẵn               |
| Tổng                 | 42      |                                              |

Các test là điểm cộng về kỹ thuật, nhưng không chứng minh hệ thống đã đủ điều kiện thi thực tế.

### Những giới hạn của test hiện tại

Thứ nhất, test E2E chính mock `WorkerLauncher` để tạo sẵn patch và mock Game Master để nhận bài. Nó không xác nhận một Codex thực sự đã giải được bài, push thành công lên remote rồi được Game Master chấm `PASSED`.

Thứ hai, `test_smoke_worker.py` tự skip nếu không có Codex hoặc Antigravity. Đường dẫn fixture `tests/fixtures/sample_repo` xuất hiện trong Git tree như một `gitlink` thay vì thư mục file bình thường; đồng thời không thấy `.gitmodules` tương ứng. Cần kiểm tra khả năng chạy lại smoke test từ một fresh clone.

Thứ ba, repository chưa có GitHub Actions workflow để tự động chạy regression test mỗi lần cập nhật.

Thứ tư, README vẫn nhắc đến 34 automated tests, trong khi mã nguồn hiện có 42 hàm test. Đây không phải lỗi lớn nhưng phản ánh tài liệu chưa đồng bộ hoàn toàn sau đợt refactor.

Tôi chưa trực tiếp chạy pytest hoặc một trận Codex–Game Master hoàn chỉnh trên môi trường của đội. Vì vậy, không thể khẳng định 42 test đều pass. Những phát hiện ở đây dựa trên việc đọc mã nguồn tại commit đã nêu và đối chiếu với kết quả benchmark được lưu trong repository.

## 9. Lộ trình cải thiện theo thứ tự ưu tiên

Tôi đề xuất dùng mức độ ảnh hưởng tới điểm thi làm tiêu chí ưu tiên, thay vì tiếp tục mở rộng tính năng AI trước.

### Giai đoạn P0 — Bắt buộc để thi được

| Ưu tiên | Công việc                             | Điều kiện hoàn thành                                                 |
| ------- | ------------------------------------- | -------------------------------------------------------------------- |
| 1       | Tích hợp official Game Master Adapter | Đọc backlog, start working, nhận MVS, submit và truy vấn grader thật |
| 2       | Hoàn thiện Git push pipeline          | SHA submit trùng commit tồn tại trên remote                          |
| 3       | Sửa trạng thái submission             | Không nhầm `ACCEPTED` thành `PASSED`; đối soát được khi timeout      |
| 4       | Thực thi QA Gate độc lập              | CR/BUG chỉ được submit khi tất cả MVS thật pass và evidence hợp lệ   |
| 5       | Dùng server clock                     | Đúng thời gian khi PAUSED, deadline và FINISHED                      |
| 6       | Có tiến trình điều phối tự chạy       | Không cần người gọi thủ công `sync_backlog()` và `step()`            |
| 7       | Lưu task state bền vững               | Restart không làm mất lịch sử hoặc gửi bài trùng                     |

Trong nhóm này, official adapter, Git push và trạng thái grading là ba điểm nghẽn trực tiếp nhất.

### Giai đoạn P1 — Tăng độ tin cậy và điểm số

| Công việc                                   | Lợi ích                                                             |
| ------------------------------------------- | ------------------------------------------------------------------- |
| Worker bất đồng bộ và PI preemption         | Phản ứng được với sự cố mới                                         |
| Sửa precedence cấu hình trong ModelRouter   | Phân bổ reasoning effort đúng chiến thuật                           |
| Watchdog tiến trình và ngân sách            | Hạn chế Agent treo hoặc tiêu hết credits                            |
| Đảm bảo Skills được load và checksum hợp lệ | Giữ hành vi production nhất quán                                    |
| Kiểm thử sau integration                    | Không submit revision chưa được xác minh                            |
| Thêm tracing của từng giai đoạn             | Biết thời gian thất thoát ở polling, reasoning, test, push, grading |

### Giai đoạn P2 — Tối ưu khả năng cạnh tranh

Tập trung cải tiến Arena thành bộ mô phỏng 40 phút với tải và hành vi gần trận thật. Dùng các benchmark đã có để ước lượng xác suất pass và thời gian theo từng loại item/stack, thay cho các hằng số cố định trong `tasks/scoring.py`.

Sau đó mới thực hiện A/B evaluation cho Skills và model profiles, với tập bài kiểm tra chưa từng được dùng để xây dựng quy tắc, tránh tối ưu quá mức cho benchmark đã biết.

## 10. Kịch bản thử nghiệm bắt buộc trước Trial Round

Một bài thử end-to-end đủ sức xác nhận kiến trúc nên có các sự kiện sau:

1. Game Master phát hành một repo khách hàng và đợt backlog đầu tiên.
2. Agent tự phát hiện CR/BUG, gọi `Start working`, nhận MVS và bắt đầu xử lý.
3. Khi Worker còn chạy, Game Master phát hành PI. Scheduler phải phát hiện và điều chỉnh ưu tiên.
4. Trận chuyển sang `PAUSED` rồi `RUNNING`. Deadline phải sử dụng thời gian từ server.
5. Worker pass test thật, integration tạo revision cuối, push lên remote và kiểm tra SHA.
6. Agent submit đúng một lần. Server trả `ACCEPTED`, sau đó Grader trả `PASSED` hoặc `FAILED`.
7. Giả lập mất mạng hoặc restart ngay khi submit để xác nhận không gửi trùng.
8. Hết 40 phút, hệ thống chốt báo cáo theo điểm được chấm, thời gian, credits và số phiên tiếp cận máy.

Nếu chạy qua kịch bản này một cách tự động, với dữ liệu và trạng thái được kiểm chứng ở cả hai phía, mức độ tin cậy sẽ tăng đáng kể so với chỉ chạy unit tests.

## 11. Kết luận cuối cùng

Từ toàn bộ cấu trúc và những module chính đã kiểm tra, tôi đánh giá dự án theo bốn góc nhìn:

| Góc nhìn                             | Đánh giá                                                                                       |
| ------------------------------------ | ---------------------------------------------------------------------------------------------- |
| Tư duy kiến trúc                     | Tốt. Đúng hướng Codex engine + deterministic orchestration + specialized Skills.               |
| Khả năng phát triển phần mềm tự động | Đã có nền tảng worker và runtime, nhưng thiếu xác minh độc lập đủ mạnh.                        |
| Khả năng thi đấu thực tế             | Chưa sẵn sàng kết nối BTC với source hiện tại do adapter, push và submission lifecycle.        |
| Tiềm năng tối ưu trước trận          | Tốt nhờ AI-Arena và kết quả benchmark Lean2; cần chuyển thành lợi thế trong trận 40 phút thật. |

Điểm mạnh nhất của dự án không nằm ở việc có nhiều Agent. Nó nằm ở quyết định tách vai trò: Python quản lý trạng thái, thời gian, Git và quy tắc nộp bài; Codex tập trung suy luận, sửa code và kiểm thử; Skills lưu lại cách làm; Arena cung cấp dữ liệu để cải thiện.

Nhưng để phù hợp chính xác với Master The Machine 2026, mục tiêu tiếp theo không nên là thêm nhiều Skills hoặc framework Agent khác.

Ưu tiên số một là biến các module hiện có thành một vòng lặp tự trị, bền vững và tuân thủ đầy đủ giao thức BTC:

`Observe → Prioritize → Start Working → Execute → Verify → Push → Submit Once → Confirm Grading → Observe Again`

Khi vòng lặp đó hoạt động chính xác, AI-Arena và self-learning mới có thể tạo ra lợi thế thực sự về điểm số, tốc độ và credits.

Đó là ranh giới giữa một dự án Agentic AI có thiết kế tốt và một Digital Project Team có thể tự thi đấu để giành điểm.