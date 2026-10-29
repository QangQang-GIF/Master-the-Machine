# Tối ưu MTM cho BAN — BN1MS5DF

## Kết luận và cấu hình đã chọn

Người dùng chọn A: một worker thường và một suất dự phòng cho PI/task gần deadline. Root điều phối, tích hợp và nộp bài. Tất cả dùng `gpt-6-luna`, effort `low`; worker chạy bằng subagent native trong cùng phiên Codex do Player quản lý. Không chạy thêm tiến trình Codex cho từng task.

## Phân tích cuộc thi

- Điểm chính thức đến từ bài được grader chấm, không từ trạng thái tự báo hoàn thành. `ACCEPTED` chỉ xác nhận nhận bài; phải theo dõi `PASSED`/`FAILED` riêng.
- Mỗi item chỉ nộp một lần sau khi Start Work. Chất lượng lần nộp đầu và receipt bền vững là bắt buộc.
- CR/BUG phải qua toàn bộ MVS công bố và có Evidence. MVS chỉ là mức tối thiểu; cần kiểm tra thêm các biên liên quan, không truy cập test ẩn.
- PI tính tốc độ đến lúc được chấm PASSED. Dành sẵn năng lực xử lý PI giúp tránh bị task dài chặn.
- Task đóng theo thời gian server, có điều chỉnh khi PAUSED. Không suy đoán lịch mở wave hoặc tin đồng hồ máy.
- Luật chung mô tả 40 phút/1.000 điểm. Metadata công khai của BN1MS5DF tại lúc chuẩn bị ghi `configuredDurationSec=3600`, `remainingSec=3600`, trạng thái PREPARING, chưa có backlog công bố. Runtime lấy cấu hình server.
- Can thiệp khi thi có thể bị trừ điểm. Sau khởi tạo, giám sát chỉ đọc mỗi 10 phút; không nhắn agent, sửa Skill, restart, tự nộp hoặc cứu bài.

## Các lựa chọn

| Phương án | Worker thường + suất ưu tiên | Ưu điểm | Đánh đổi |
|---|---:|---|---|
| A — đã chọn | 1 + 1 | Ít token, ít tranh tài nguyên và xung đột tích hợp | Thông lượng backlog thường thấp hơn |
| B | 2 + 1 | Cân bằng tốc độ và độ phức tạp | Nhiều context và kiểm tra tích hợp hơn |
| C | 3 + 1 | Thông lượng cao khi task độc lập | Tốn token/CPU/DB hơn; nguy cơ nghẽn tích hợp cao hơn |

Cả ba dùng cùng cổng kiểm tra bằng code. Suất ưu tiên chỉ cấp cho PI hoặc task có slack thấp; không bị task thường chiếm hết. Không thể hứa hoàn thành mọi đề trong ngân sách hữu hạn; cơ chế này phát hiện quá hạn, thiếu Evidence và bài đã giải nhưng chưa nộp, đồng thời giữ công việc khác tiếp tục.

## Skill cô đọng và tổng quát hơn

Mười Skill nguồn giảm từ 32.163 xuống 18.205 bytes (43,4%); số từ giảm từ 4.080 xuống 2.357 (42,2%). Đây là phép đo dung lượng và số từ, không phải số token chính xác của tokenizer. Lượng token thực tế còn phụ thuộc Skill được nạp, lịch sử, tool output và cache.

Bỏ lịch wave cố định phút 14/29, cấu hình DB và tên fixture của dự án cũ, lặp lại hướng dẫn chung, yêu cầu không bao giờ báo BLOCKED và quyền Submit từ worker. Giữ MVS đầy đủ, Evidence trước thay đổi khi đề yêu cầu, kết quả test thật, baseline có chứng minh, phạm vi workspace, quy tắc secrets, pause/end và receipt.

## Các bảo đảm bằng code

- Coordinator SQLite theo match: claim nguyên tử, giới hạn đồng thời, ưu tiên PI/deadline, thời gian server, trạng thái pause/end và worker quá budget. Quá budget chỉ sinh yêu cầu dừng native; không tự giải phóng suất của worker còn ghi file.
- Worktree riêng; matchRoot luôn là `worktreeRoot/repository.folder`. Start Work files/template được chuyển vào đúng worktree với kiểm tra containment.
- TaskEnvelope sạch không chứa descriptor credentials; WorkerResult có kiểm tra test command, PASS/exit code, paths và Evidence.
- Khóa riêng bao trùm tích hợp → kiểm tra → push → Submit để tránh tranh remote ref. Worker khác vẫn chạy.
- Cổng nộp kiểm tra parity answer/commit, test ID công bố từ template/MVS, test PASS, blob Evidence không rỗng, đúng thư mục, SHA/branch/remote. Không bỏ qua regression mới; lỗi baseline không liên quan không chặn item đã giải.
- Journal giữ nguyên canonical request/idempotency identity; receipt ACCEPTED được hòa giải với coordinator sau crash, tránh nộp lại hoặc khóa treo.
- Recovery nạp lại coordinator cho `RESUME_GAME`; giữ worktree/checkpoint và xác nhận worker đã dừng trước khi giao lại.
- Helper giữ credentials trong bộ nhớ; bỏ việc quét thư mục private của host và tự ghi session secrets ra disk. Player tiếp tục quản lý xác thực theo giao thức chính thức.
- Installer đồng bộ Skill nguồn/bản cài và checksum đóng băng; kiểm tra checksum khi cấu hình MCP ở production.

Các cổng cấu trúc không chứng minh nội dung ảnh là đúng hoặc test báo PASS phản ánh đầy đủ yêu cầu nghiệp vụ. Root vẫn phải kiểm tra patch, chạy lại xác minh liên quan và đánh giá MVS trước lần Submit duy nhất.

## Kiểm chứng

- 189 kiểm thử pipeline/MCP/contract/Git/journal đã pass; sau các sửa bổ sung, 67 kiểm thử scheduler/Evidence/config/native MCP đã pass.
- E2E cục bộ đi từ public board → Start Work → claim worktree → Evidence → commit/cherry-pick/push → Submit → receipt; không gọi Game Master thật.
- Codex CLI 0.164.0 đã chạy subagent native thật. Bản này cần `features.multi_agent_v2=true`; kiểm tra chỉ dựa trên câu trả lời PASS là không đủ. Đã xác minh root có `subAgentActivity` và thread con có `commandExecution`, exit 0, đọc fixture thành công.
- Root smoke: `01a186b2-b231-77b0-b6a5-e7dd0d65c222`; child: `01a186b2-ced4-7592-8d2b-9bb32072af8e`.
- Bộ cài và check-distribution báo READY; checksum nguồn/bản cài trùng.
- Log DEV lưu tại `state/skill-optimization/`; đây là bằng chứng kiểm thử runtime, không phải Evidence nộp cho các backlog chưa công bố.

## Giám sát trận

Sau khi Player nhận khởi tạo BN1MS5DF, chỉ đọc dashboard/progress/transcript mỗi 10 phút. Không đọc secret store, không gọi Start Work/Submit thay CLI, không thay đổi file/config/quyền và không nhắn/stop/resume agent. Thông báo khi có thay đổi đáng kể hoặc lỗi cần organizer; không tự can thiệp. Khi FINISHED, ghi nhận kết quả cuối và dừng lịch giám sát.

Tài liệu kiểm tra native: https://learn.chatgpt.com/docs/agent-configuration/subagents

Model Luna và effort hỗ trợ: https://developers.openai.com/api/docs/models/gpt-6-luna
