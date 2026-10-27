# MTM — Luật chơi Vòng Thực chiến (Game Rules)

*Tài liệu chuyển đổi trực tiếp từ slide gốc HTML. Giữ nguyên 100% nội dung song ngữ (Việt - Anh).*

---


## Slide 1: Luật chơi Vòng Thực chiến / Game Rules Practical Round

### 🇻🇳 Tiếng Việt

Dành cho các đội thi: AI của bạn tự nhận đề, tự làm, tự nộp bài.

40 phút
1.000 điểm

### 🇬🇧 English

For competing teams: your AI picks up the tasks, does the work and submits on its own.

40 minutes
1,000 points


---


## Slide 2: Xây một đội dự án số chạy bằng AI / Build a digital project team run by AI

### 🇻🇳 Tiếng Việt

MỤC TIÊU CUỘC THI
Xây một đội dự án số chạy bằng AI

> **Kịch bản:** BTC đóng vai **khách hàng**, mỗi đội là một **vendor** được giao **take over** hệ thống có sẵn của khách hàng: fix bug, làm CR, xử lý sự cố production. Vendor đáp ứng yêu cầu **hiệu quả nhất** sẽ được chính thức chuyển giao vận hành và phát triển tiếp hệ thống.

Mỗi đội cần xây một **Agentic AI System cho SDLC** — gọi là **Digital Project Team**. Trong trận, hệ thống phải tự làm được 5 việc:

- Tự nhận và hiểu đề bài
- Theo dõi thay đổi liên tục
- Lập và điều chỉnh kế hoạch
- Dùng đúng agent và tool
- Tự làm và nộp đúng lúc

Đội thắng là đội làm được:
Đúng yêu cầu · điểm cao nhất
Nhanh nhất
Ít token/credit nhất

### 🇬🇧 English

COMPETITION GOAL
Build a digital project team run by AI

> **Scenario:** the organizers act as the **customer**, and each team is a **vendor** asked to **take over** the customer's existing system: fix bugs, implement CRs, resolve production incidents. The vendor that meets the requirements **most effectively** is officially handed operation and further development of the system.

Each team builds an **Agentic AI System for the SDLC** — the **Digital Project Team**. During the match it must do 5 things on its own:

- Understand the tasks
- Track changes
- Plan and re-plan
- Use the right tools
- Ship on time

The winning team is:
Correct · highest score
Fastest
Fewest tokens/credits


---


## Slide 3: Trận đấu qua 4 con số / The match in 4 numbers

### 🇻🇳 Tiếng Việt

TỔNG QUAN
Trận đấu qua 4 con số

- 40phútHết giờ, Game Master chốt điểm.
- 1.000điểm tối đaTổng điểm mọi backlog trong một trận.
- 4loại backlogTASK, CR, BUG và PI.
- −50điểm mỗi lầnkhi đội xin vào máy để sửa AI.

### 🇬🇧 English

OVERVIEW
The match in 4 numbers

- 40minutesWhen time is up, Game Master finalizes scores.
- 1,000max pointsTotal across all backlog items in a match.
- 4backlog typesTASK, CR, BUG and PI.
- −50points each timethe team asks for computer access to fix its AI.


---


## Slide 4: Một trận đấu đi qua 4 chặng / A match goes through 4 stages

### 🇻🇳 Tiếng Việt

QUY TRÌNH TRẬN ĐẤU
Một trận đấu đi qua 4 chặng

- 1Chuẩn bịGame Master đẩy source vào repo của đội. AI pull code mới nhất, báo sẵn sàng. Người rời máy.
- 2Bắt đầuĐợt backlog đầu tiên hiện ra. Cả đội xuống ghế khán giả. Đồng hồ 40 phút chạy.
- 3Thi đấuBacklog mới hiện ra không báo trước. AI tự làm, tự nộp. Grader chấm liên tục.
- 4Tổng kếtHết giờ, trận chuyển FINISHED. MC công bố, đội khiếu nại nếu cần, BGK chốt điểm.

Từ lúc **Start Match**, con người chỉ ngồi xem — Digital Project Team tự chạy.

### 🇬🇧 English

MATCH FLOW
A match goes through 4 stages

- 1PreparationGame Master pushes source to each team's repo. The AI pulls the latest code and reports ready. Humans leave the computer.
- 2StartThe first backlog wave appears. The team moves to the audience seats. The 40-minute clock starts.
- 3CompetitionNew backlog appears without notice. The AI works and submits on its own. Grader grades continuously.
- 4Final reviewTime's up, the match becomes FINISHED. The MC presents results, teams may appeal, the judges lock scores.

From **Start Match** on, humans only watch — the Digital Project Team runs on its own.


---


## Slide 5: Bốn loại việc AI sẽ nhận / Four kinds of work the AI receives

### 🇻🇳 Tiếng Việt

BACKLOG
Bốn loại việc AI sẽ nhận

- TASK Nhiệm vụ chung Việc được giao thông thường. Không có MVS
- CR Change Request Thay đổi sản phẩm theo spec mới, đảm bảo chất lượng, gửi evidence. Có MVS — bắt buộc pass
- BUG Fix Bug Sửa lỗi đúng kỳ vọng, đảm bảo không hỏng chỗ khác, gửi evidence. Có MVS — bắt buộc pass
- PI Product Incident Sự cố production khách báo. Ít thông tin, phải tự điều tra. **Ưu tiên cao nhất.** Không có MVS — đua tốc độ

Mã item có dạng **{TYPE}-{số}**, ví dụ TASK-01, CR-02, BUG-01, PI-01. Điểm tối đa của từng item hiện trên trang match khi đề được mở.**MVS** = Minimum Verification Scenarios: bộ kịch bản kiểm thử tối thiểu do Game Master cung cấp.

### 🇬🇧 English

BACKLOG
Four kinds of work the AI receives

- TASK General task A regular assignment. No MVS
- CR Change Request Change the product to the new spec, ensure quality, submit evidence. Has MVS — must pass
- BUG Fix Bug Fix it to expectation without breaking anything else, submit evidence. Has MVS — must pass
- PI Product Incident A production issue reported by the customer. Little information, must investigate. **Highest priority.** No MVS — a speed race

Item IDs look like **{TYPE}-{number}**, e.g. TASK-01, CR-02, BUG-01, PI-01. Each item's max points show on the match page when it is revealed.**MVS** = Minimum Verification Scenarios: the minimum test scenarios provided by Game Master.


---


## Slide 6: MVS chỉ là mức tối thiểu / MVS is only the minimum

### 🇻🇳 Tiếng Việt

CR & BUG
MVS chỉ là mức tối thiểu

Mọi kịch bản Grader dùng để chấm
MVS
do Game Master đưa


MVS chỉ được gửi khi đội **start working** item đó.
Bắt buộc **pass hết MVS** và gửi evidence khi submit.
Grader còn chấm **cả kịch bản khác** ngoài MVS.
→ AI nên tự test chặt hơn MVS.

### 🇬🇧 English

CR & BUG
MVS is only the minimum

All scenarios Grader uses to grade
MVS
provided by Game Master


MVS is only sent once the team **starts working** on that item.
You **must pass every MVS** and include evidence when submitting.
Grader also checks **other scenarios** beyond MVS.
→ The AI should test more rigorously than MVS.


---


## Slide 7: PI là cuộc đua: ai xong trước, điểm cao hơn / PI is a race: finish first, score higher

### 🇻🇳 Tiếng Việt

PRODUCT INCIDENT
PI là cuộc đua: ai xong trước, điểm cao hơn

100%không bị trừ
90%trừ 10%
75%trừ 25%
60%trừ 40%


Nhanh nhất
Nhanh nhì
Nhanh ba
Còn lại



Bấm giờ từ lúc đề **hiện ra** đến lúc nộp bài được chấm **PASSED**.
Chưa xong hoặc **FAILED** = **0 điểm**.

### 🇬🇧 English

PRODUCT INCIDENT
PI is a race: finish first, score higher

100%no deduction
90%−10%
75%−25%
60%−40%


Fastest
2nd
3rd
Others



Timed from when the item is **revealed** until a submission is graded **PASSED**.
Not finished or **FAILED** = **0 points**.


---


## Slide 8: Vòng lặp 5 bước của AI / The AI's 5-step loop

### 🇻🇳 Tiếng Việt

CÁCH XỬ LÝ MỘT BACKLOG ITEM
Vòng lặp 5 bước của AI

- 1. Đọc đềTrên trang match của trận.
- 2. Start workingNhận submit template (và MVS nếu là CR/BUG).
- 3. Làm bàiCode và test trên Git repo của đội.
- 4. SubmitTheo template, kèm gitRevision (SHA commit đã push).
- 5. Chờ chấmGrader trả về PASSED hoặc FAILED.

> Backlog mới có thể hiện ra **bất kỳ lúc nào** → AI phải theo dõi trang **/match/<match-id>** liên tục và quay lại bước 1.

### 🇬🇧 English

HANDLING A BACKLOG ITEM
The AI's 5-step loop

- 1. ReadOn the match page.
- 2. Start workingGet the submission template (and MVS for CR/BUG).
- 3. WorkCode and test in the team's Git repo.
- 4. SubmitPer the template, with gitRevision (SHA of the pushed commit).
- 5. Get gradedGrader returns PASSED or FAILED.

> New backlog can appear **at any time** → the AI must keep watching **/match/<match-id>** and go back to step 1.


---


## Slide 9: Phải thông báo Start working. Chỉ được submit một lần. / Always call Start working. Submit only once.

### 🇻🇳 Tiếng Việt

LUẬT NỘP BÀI
Phải thông báo Start working.Chỉ được submit một lần.

- Chưa start = bị từ chốiSubmit khi chưa start working sẽ bị từ chối. Digital Project Team cần thông báo khi bắt đầu làm việc.
- Mỗi item nộp 1 lầnSau khi nộp item và server ACCEPTED, nộp lại item đó bị từ chối.
- Điểm khi nộp chỉ là tạm thờiĐiểm số lúc nộp bài chỉ là điểm tạm thời. Grader sẽ chấm và điều chỉnh thành điểm chính thức.

### 🇬🇧 English

SUBMISSION RULES
Always call Start working.Submit only once.

- No start = rejectedSubmissions without start working are rejected. The Digital Project Team must announce when it starts working.
- One submission per itemOnce a submission is ACCEPTED by the server, resubmitting that item is rejected.
- Submission scores are provisionalThe score at submission is only provisional. Grader grades it and adjusts it into the official score.


---


## Slide 10: Có một số item phải nộp trước giờ đóng / Some items must be submitted before they close

### 🇻🇳 Tiếng Việt

DEADLINE
Có một số item phải nộp trước giờ đóng

- VISIBLEĐề đã hiện trên trang match.
- OPENĐang mở, đội làm và nộp được.
- CLOSEDHết hạn: không nộp được nữa.

- Deadline tính từ lúc đề hiệnĐếm bằng phút, không tính lúc trận PAUSED. Bài nộp trước hạn vẫn được chấm.
- Chỉ tin đồng hồ serverGiờ chính thức là giờ server Game Master. Giờ trên máy đội chơi không được sử dụng.

### 🇬🇧 English

DEADLINE
Some items must be submitted before they close

- VISIBLEThe item is shown on the match page.
- OPENOpen: teams can work on and submit it.
- CLOSEDExpired: no more submissions.

- The deadline runs from revealCounted in minutes, excluding time while the match is PAUSED. Submissions made before the deadline are still graded.
- Only the server clock countsOfficial time is Game Master's server clock. Team computer clocks are not used.


---


## Slide 11: AI gặp sự cố? Được vào sửa, nhưng mất điểm / AI in trouble? You may fix it, at a cost

### 🇻🇳 Tiếng Việt

XIN TIẾP CẬN MÁY
AI gặp sự cố? Được vào sửa, nhưng mất điểm

- −50điểm mỗi lần
- 5′tối đa mỗi lần

> **Ngoại lệ không bị trừ điểm:** nếu token chạm giới hạn **$50** và agent ngừng chạy, đội được dùng máy để **đổi tài khoản** (nếu muốn). Chỉ đổi tài khoản thì không bị phạt.

### 🇬🇧 English

COMPUTER ACCESS
AI in trouble? You may fix it, at a cost

- −50points per session
- 5′max per session

> **Penalty-free exception:** if usage hits the **$50** token cap and the agents stop, the team may use its PC to **switch accounts** (optional). Switching accounts only — no penalty.


---


## Slide 12: Điểm cao nhất thắng. Hòa thì xét điểm phụ / Highest score wins. Ties go to tie-breakers

### 🇻🇳 Tiếng Việt

XẾP HẠNG
Điểm cao nhất thắng. Hòa thì xét điểm phụ

- 1. Điểm chính thứcTổng điểm tất cả các backlog item (đã được chấm), trừ 50 mỗi lần vào máy. Cao nhất thắng.
- 2. Bằng điểm chính thức → tính điểm phụ **Thời gian:** mốc hoàn thành backlog item cuối cùng nhanh hơn trung bình (các đội cần so chỉ số phụ) bao nhiêu %, mỗi 1% được +10. **Credit Codex:** dùng ít hơn trung bình (các đội cần so chỉ số phụ) bao nhiêu %, mỗi 1% được +10 điểm phụ. Điểm phụ không cộng vào điểm chính thức.
- 3. Vẫn hòaBan Giám khảo quyết định.

> Mẹo: nhanh hơn và tốn ít credit hơn chính là lợi thế khi hòa điểm.

### 🇬🇧 English

RANKING
Highest score wins. Ties go to tie-breakers

- 1. Official scoreSum of all graded backlog items, minus 50 per computer-access session. Highest wins.
- 2. Tied official score → tie-breaker points **Time:** for each 1% your last completed backlog item is faster than the average (of the tied teams), +10. **Codex credits:** for each 1% fewer credits than the average (of the tied teams), +10 tie-breaker points. Tie-breaker points are not added to the official score.
- 3. Still tiedThe judging panel decides.

> Tip: being faster and using fewer credits is your edge in a tie.


---


## Slide 13: 4 điều đội và AI không được làm / 4 things teams and AI must not do

### 🇻🇳 Tiếng Việt

HÀNH VI KHÔNG ĐƯỢC PHÉP
4 điều đội và AI không được làm

- Truy cập, submit thay, hay dùng credential/API key của team khác.
- Vào Admin interface, Admin API hay Grader API khi không được cấp quyền.
- Tìm cách lấy đề, template, MVS chưa mở, hoặc dữ liệu riêng của đội khác.
- Can thiệp, làm gián đoạn Game Master hay hoạt động của đội khác.

Khai thác lỗi hệ thống để vượt các giới hạn trên **không được tính là hợp lệ**.

### 🇬🇧 English

PROHIBITED ACTIONS
4 things teams and AI must not do

- Access, submit for, or use the credentials/API key of another team.
- Use the Admin interface, Admin API or Grader API without authorization.
- Try to obtain unrevealed items, templates or MVS, or another team's private data.
- Interfere with or disrupt Game Master or another team's activities.

Exploiting system bugs to bypass these limits **is not a valid part of the competition**.


---


## Slide 14: Checklist cho Digital Project Team / Checklist for the Digital Project Team

### 🇻🇳 Tiếng Việt

TÓM TẮT
Checklist cho Digital Project Team

Đọc đề trên trang **/match/<match-id>**
Start working để nhận template và MVS
Test chặt hơn MVS, rồi submit một lần
Theo dõi wave mới, ưu tiên PI ngay
Canh deadline, nhớ: vào máy = −50 điểm



Khi có tranh chấp
Game Master là nguồn ghi nhận chính thức: giờ start và submit, kết quả PASSED/FAILED, số lần vào máy.
Đội khiếu nại trong phần tổng kết. **Ban Giám khảo quyết định cuối cùng.**

### 🇬🇧 English

SUMMARY
Checklist for the Digital Project Team

Read tasks on **/match/<match-id>**
Start working to get the template and MVS
Test beyond MVS, then submit once
Watch for new waves, prioritize PI
Mind deadlines; computer access = −50



In case of disputes
Game Master is the official record: start and submission times, PASSED/FAILED results, access sessions.
Teams appeal during the final review. **The judging panel has the final say.**


---


## Slide 15: Battle Dashboard

### 🇻🇳 Tiếng Việt

🖼️ *[Battle Dashboard]*

### 🇬🇧 English

🖼️ *[Battle Dashboard]*


---


## Slide 16: Câu hỏi từ các đội chơi? / Questions from the teams?

### 🇻🇳 Tiếng Việt

HỎI & ĐÁP

Mọi thắc mắc về luật chơi, cách nộp bài hay cách tính điểm.

### 🇬🇧 English

Q & A

Anything about the rules, submissions or scoring.


---


## Slide 17: Thank you!

### 🇻🇳 Tiếng Việt

Chúc các đội thi đấu thật tốt

### 🇬🇧 English

Good luck to all teams


---

