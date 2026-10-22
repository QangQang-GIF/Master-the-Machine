# Benchmark và mock so với cuộc thi

Đối chiếu những gì đã dựng (benchmark `arena`, mock Game Master `mock_gm`, orchestrator `agent/`) với
Game Rules (`doc/MTM_Game_Rules.html`) và Q&A của BTC, để biết số đo nào tin được và chỗ nào còn khác.

## 1. Giống

| Luật thi | Đã có | Ở đâu |
|---|---|---|
| 4 loại item TASK / CR / BUG / PI, mỗi item một loại, có type sẵn | mọi item trong kho và trong mock đều mang đúng một trong 4 loại | `arena`, `mock_gm` |
| CR/BUG có MVS, Grader chấm rộng hơn MVS | agent nhận 2 test MVS, grader chạy toàn bộ test ẩn | `arena` check/grade, `mock_gm` |
| PI ít thông tin, không MVS, đua tốc độ 100/90/75/60% | PI chỉ có đoạn đầu mô tả; điểm theo thứ hạng thời gian tới PASSED | `arena` (hạng giữa các agent), `mock_gm` (mốc phút cố định) |
| FAILED hoặc chưa xong = 0, không điểm âm | như luật | cả hai |
| Deadline tính từ lúc đề hiện, nộp trễ bị từ chối | item có deadline → 0 nếu nộp trễ | `arena` (một phần item), `mock_gm` (CLOSED) |
| Submit một lần, kèm gitRevision đã push | mock từ chối submit lần 2 và sha chưa push | `mock_gm` |
| Điểm chính thức = tổng item − 50 mỗi lần vào máy | mock có nút access −50; benchmark không có vào máy | `mock_gm` |
| Tie-breaker: mốc hoàn thành cuối và credit Codex, +10 mỗi 1% so với trung bình | tính đúng công thức giữa các agent đã chạy cùng bộ item | `arena` report/dashboard |
| Không người can thiệp, agent tự đọc board, tự làm, tự nộp | scheduler Python + worker Codex, không prompt tay | `agent/` |
| Codex CLI là runtime | mọi agent chạy `codex exec` với ChatGPT login | cả hai |
| Wave xuất hiện bất kỳ lúc nào, pause không tính giờ | mock có `reveal_min`, PAUSED | `mock_gm` |

## 2. Khác

| Khía cạnh | Cuộc thi | Hiện tại | Hệ quả / việc cần làm |
|---|---|---|---|
| Tech stack sản phẩm | chỉ biết lúc publish đề; mỗi vòng một repo; không được đổi stack (Q&A #1, #2, #16) | kho có 12 ngôn ngữ nhưng **thiếu .NET và Angular**, Kotlin không có ảnh Docker; mock chỉ có Django | thêm SWE-Sharp-Bench (.NET), tự xây suite Spring Boot + Angular; mock đa stack từ catalog |
| Một repo chung, một branch | một repo GitHub, đội push rồi submit sha | mock: mỗi item một branch `item/<ID>` ở base commit riêng (lấy từ SWE-bench) | bench đã push theo branch; đổi sang một branch chung khi có spec thật (Training Camp 26/10) |
| Môi trường test | đội tự dựng cho sản phẩm được giao | image Docker có sẵn theo item (SWE-bench), lệnh test cho sẵn | cần bước "onboarding" đầu trận: tự tìm cách build/test, không `clean`, chạy test tập trung |
| Số item / nhịp trận | ~10 item trong 40 phút, nhiều wave, có item có deadline có item không (Q&A #18) | benchmark chạy **tuần tự từng item**, timeout riêng; mock mới 3 item, 2 wave | mock ~10 item nhiều wave để thử scheduler với tải thật |
| Đồng hồ | một đồng hồ server 40 phút cho cả trận | benchmark: mỗi item đồng hồ riêng; mock: đúng kiểu server clock | so sánh agent trên benchmark không phản ánh tranh chấp thời gian giữa các item |
| PI xếp hạng | so với **các đội khác** trong trận | `arena`: so giữa các agent trong report; `mock_gm`: mốc 10/15/20 phút tự đặt | chỉ là xấp xỉ; không có đối thủ thật |
| Evidence | item có thể yêu cầu evidence khác, ví dụ ảnh trước/sau khi sửa (Q&A #20); có thể báo "bug đã sửa sẵn" | chỉ gửi output test MVS | worker phải đọc yêu cầu evidence trong template và tạo đúng loại |
| Nhãn loại và MVS | BTC gán sẵn | `arena` gán bằng từ khoá, MVS = 2 test đầu FAIL_TO_PASS | nhãn gần đúng; chỉ dùng cho luyện tập |
| Cách khởi chạy | "Codex Plugin" của BTC xác thực, spawn Orchestrator, theo dõi token (Q&A #3) | `run_match.py` tự spawn scheduler + worker | hỏi BTC token của worker con có được plugin tính không; API GM thật (URL, API key, team id) chỉ có từ 26/10 |
| Cap chi phí | $50 không nạp thêm; đổi account không bị phạt (Q&A #13, #17) | watchdog theo token từng worker; chưa theo tổng $ của trận | watchdog tổng trận + chế độ tiết kiệm khi gần cap; runbook đổi account |
| Vào máy −50 | có | mock có; orchestrator chưa tự phục hồi hết mọi lỗi | watchdog/restart đã có cho worker; cần cho cả scheduler |
| Điểm tối đa | 1.000 mỗi trận | benchmark chuẩn hoá `/1000` theo điểm tối đa của set; mock dùng bố cục trận mẫu | so sánh tương đối, không phải điểm trận |

## 2b. Harness: những gì đã thử

Codex CLI là runtime bắt buộc; thứ chọn được là lớp bên trên. Đã đo trên set xs (cùng gpt-6.1-sol, low):
Codex thuần 43k token / 2,3', Codex + Ruflo 78k, Hermes 73k, OpenCode 90k, tất cả cùng 2/2 PASSED. Cấu hình
`lean2` (cap output, verbosity thấp, luật đọc lát, đề nhúng) giảm 33% token trên set s với cùng 8/8. Kết luận
cho trận: giữ Codex thuần + lớp orchestrator của mình, không thêm harness ngoài. Chi tiết:
`REPORT_harness_xs.md`, `OPTIMIZATION.md`.

## 3. Số đo nào tin được

| Số đo | Độ tin cậy | Vì sao |
|---|---|---|
| Đúng/sai (resolved) | cao | test ẩn của benchmark gốc chấm, đã `check` bằng gold patch và patch rỗng |
| Token tính phí, số lệnh, phút | cao, nhưng **phút gồm cả thời gian chờ build/test** | đo thật; với Java, máy biên dịch có thể chiếm phần lớn |
| Điểm theo loại, deadline, tie-breaker | đúng công thức luật | xấp xỉ ở chỗ: không có đội khác, không có đồng hồ chung |
| Điểm PI | xấp xỉ | xếp hạng giữa các agent / mốc phút tự đặt |
| Routing `p`, `t` theo loại | chỉ có nghĩa từ set `m` trở lên | cần ≥ 2 item mỗi loại; sai số ±17 điểm với 8 item, ±11 với 20, ±8 với 40 |
| Tỉ lệ pass theo stack | set s: 8/8 trên React, COBOL, Java, C, Go, JS, Python, Ruby với mọi cấu hình Codex | mới 1 item mỗi stack; .NET/Angular chưa có |

## 4. Việc còn lại để khớp cuộc thi hơn (theo thứ tự)

1. Bench không phụ thuộc stack + bước onboarding đầu trận (tự build/test; test tập trung; không `clean`).
2. .NET (SWE-Sharp-Bench + image .NET SDK) và suite Angular/Spring Boot tự xây.
3. Mock Game Master ~10 item, đa stack, nhiều wave; một branch chung.
4. Watchdog tổng chi phí trận so với $50; evidence theo yêu cầu item.
5. Khớp API/giao diện Game Master thật sau Workshop 3 (23/10) và Training Camp (26/10).
