# MTM — Kick-off & Hướng dẫn bắt đầu (Getting Started)

*Tài liệu chuyển đổi trực tiếp từ slide gốc HTML. Giữ nguyên 100% nội dung song ngữ (Việt - Anh).*

---


## Slide 1: Xây dựng Digital Project Team cho MTM / Building your Digital Project Team for MTM

### 🇻🇳 Tiếng Việt

Cài đặt, thi đấu, debug

23-Oct-2026
Ban Tổ chức MTM 2026

### 🇬🇧 English

Setup, play, debug

23-Oct-2026
MTM 2026 Organizers


---


## Slide 2: Nội dung buổi hôm nay / Today's agenda

### 🇻🇳 Tiếng Việt

AGENDANội dung buổi hôm nay

**01**Quick Recap: Thể lệ cuộc thi
**06**Sample pack: cấu trúc, cách sửa, cách cài
**02**Demo video một trận đấu
**07**Init một trận (Practice Match)
**03**Tổng quan kiến trúc
**08**Hướng dẫn debug
**04**Checklist chuẩn bị
**09**Quick start: một trang mang về
**05**Cài MTM Player
**10**Q&A

### 🇬🇧 English

AGENDAToday's agenda

**01**Quick recap: competition rules
**06**Sample pack: structure, editing, install
**02**Demo video of a match
**07**Initialize a match (Practice Match)
**03**Architecture overview
**08**Debugging guide
**04**Preparation checklist
**09**Quick start: a one-page takeaway
**05**Install MTM Player
**10**Q&A


---


## Slide 3: Thông tin chung / General information

### 🇻🇳 Tiếng Việt

QUICK RECAP: THỂ LỆ CUỘC THIThông tin chung

> **Kịch bản:** BTC đóng vai khách hàng, mỗi đội là một vendor được giao take over hệ thống có sẵn của khách hàng: fix bug, làm CR, xử lý sự cố production. Vendor đáp ứng yêu cầu hiệu quả nhất sẽ được chính thức chuyển giao vận hành và phát triển tiếp hệ thống.

40′Tối đa mỗi trận. Hết giờ, Game Master tổng kết điểm.
WaveBacklog mới hiện trên /match/<match-id> vào thời điểm không báo trước.
−50Mỗi lần xin tiếp cận máy để sửa (tối đa 5 phút/lần).

1 · Start Preparation
2 · Start Match
3 · Thi đấu
4 · Tổng kết

### 🇬🇧 English

QUICK RECAP: COMPETITION RULESGeneral information

> **Scenario:** the organizers act as the customer, and each team is a vendor asked to take over the customer's existing system: fix bugs, implement CRs, resolve production incidents. The vendor that meets the requirements most effectively is officially handed operation and further development of the system.

40′Maximum per match. When time is up, Game Master finalizes scores.
WaveNew backlog appears on /match/<match-id> without notice.
−50Each time the team asks for computer access to fix (max 5 minutes each).

1 · Start Preparation
2 · Start Match
3 · Competition
4 · Final review


---


## Slide 4: Bốn loại backlog item / Four backlog item types

### 🇻🇳 Tiếng Việt

QUICK RECAP: THỂ LỆ CUỘC THIBốn loại backlog item

MãLoạiVerification Scenarios (MVS)Điểm
**TASK**Nhiệm vụ chungKhông cung cấpTheo đề của trận
**CR**Change Request: thay đổi theo specs mớiCó, bắt buộc pass và gửi evidenceTheo đề của trận
**BUG**Lỗi đã phát hiện trên sản phẩmCó, bắt buộc pass và gửi evidenceTheo đề của trận
**PI**Sự cố production, ưu tiên cao nhấtKhông cung cấp, Grader có tiêu chí riêngĐua: đội PASSED đầu tiên được 100%, đội sau giảm dần

MVS chỉ là mức tối thiểu: Grader chấm cả các scenario khác, nên agent cần chủ động test chặt hơn.

### 🇬🇧 English

QUICK RECAP: COMPETITION RULESFour backlog item types

CodeTypeVerification Scenarios (MVS)Points
**TASK**General taskNot providedPer match scenario
**CR**Change Request: changes per new specsProvided; must pass and submit evidencePer match scenario
**BUG**A defect found in the productProvided; must pass and submit evidencePer match scenario
**PI**Production incident, top priorityNot provided; Grader has its own criteriaRace: first team to PASS gets 100%, later teams get less

MVS is only the minimum: Grader also checks other scenarios, so the agent should test more thoroughly.


---


## Slide 5: Chấm điểm và xếp hạng / Scoring and ranking

### 🇻🇳 Tiếng Việt

QUICK RECAP: THỂ LỆ CUỘC THIChấm điểm và xếp hạng

Điểm chính thức

Tối đa 1.000 điểm cho mọi backlog của trận
Mỗi item chỉ submit một lần; phải Start Work trước
Item có deadline: quá hạn thì không submit được
Game Master nhận bài chưa có nghĩa là đạt; Grader mới chấm PASSED/FAILED
Trừ 50 điểm mỗi lần tiếp cận máy



Điểm phụ khi bằng điểm

**Thời gian:** mốc hoàn thành backlog item cuối cùng nhanh hơn trung bình (các đội cần so chỉ số phụ) bao nhiêu %, mỗi 1% được +10 điểm phụ
**Credit Codex:** dùng ít hơn trung bình (các đội cần so chỉ số phụ) bao nhiêu %, mỗi 1% được +10 điểm phụ
**Vẫn hòa:** Ban Giám khảo quyết định

Điểm phụ không cộng vào điểm chính thức.

### 🇬🇧 English

QUICK RECAP: COMPETITION RULESScoring and ranking

Official score

Up to 1,000 points across all backlog items of a match
Each item can be submitted only once; Start Work first
Items with a deadline cannot be submitted after it passes
Game Master accepting a submission does not mean it passed; Grader decides PASSED/FAILED
−50 points for each computer access



Tie-break points

**Time:** for each 1% your last completed backlog item is faster than the average (of the teams being compared), +10 tie-break points
**Codex credit:** for each 1% you use less than the average (of the teams being compared), +10 tie-break points
**Still tied:** the judges decide

Tie-break points are not added to the official score.


---


## Slide 6: Giải thưởng Daddy Shark / Daddy Shark prizes

### 🇻🇳 Tiếng Việt

QUICK RECAP: THỂ LỆ CUỘC THIGiải thưởng Daddy Shark

Á quân 1
20M
VND
2


Quán quân
30M
VND
1


Á quân 2
10M
VND
3



VÒNG TRIAL
8 giải
1M VND / giải
Ghi nhận ngay từ vòng thi thử cho các đội có màn thể hiện nổi bật.

### 🇬🇧 English

QUICK RECAP: COMPETITION RULESDaddy Shark prizes

1st runner-up
20M
VND
2


Champion
30M
VND
1


2nd runner-up
10M
VND
3



TRIAL ROUND
8 prizes
1M VND each
Awarded from the trial round onward to teams with standout performances.


---


## Slide 7: Một trận đấu thật / A real match

### 🇻🇳 Tiếng Việt

DEMOMột trận đấu thật

### 🇬🇧 English

DEMOA real match


---


## Slide 8: Bốn thành phần, một kênh trạng thái / Four components, one status channel

### 🇻🇳 Tiếng Việt

KIẾN TRÚCBốn thành phần, một kênh trạng thái

MTM PlayerKhởi chạy agent, cấp quyền theo phase, approvals, dashboard

Team OrchestratorCủa đội: chuẩn bị, chọn việc, code, test, submit

Game ServerTrận, backlog, thời gian, nhận bài và trả receipt

Progress log<matchId>.progress.ndjson

Git repo của độiSource, branch, folder được giao

Input theo phase

Game API

checkout · commit · push

ghi event (append)

Player đọc

Game Server quyết định trạng thái trận và kết quả nhận bài. Progress log là kênh duy nhất để Player biết đội đang làm gì.

### 🇬🇧 English

ARCHITECTUREFour components, one status channel

MTM PlayerLaunches the agent, grants per-phase access, approvals, dashboard

Team OrchestratorYours: prepare, pick work, code, test, submit

Game ServerMatch, backlog, time; accepts submissions, returns receipts

Progress log<matchId>.progress.ndjson

Team Git repoAssigned source, branch, folder

Per-phase input

Game API

checkout · commit · push

append events

Player reads

Game Server decides match status and submission intake. The progress log is the only way the Player knows what the team is doing.


---


## Slide 9: Bên trong Team Orchestrator: một PM và các chuyên gia / Inside the Team Orchestrator: a PM and specialists

### 🇻🇳 Tiếng Việt

KIẾN TRÚCBên trong Team Orchestrator: một PM và các chuyên gia

> Team Orchestrator · Project ManagerNhận input từ Player, chọn backlog, giao việc, review, tích hợp, quyết định cuối và submit

Business AnalystYêu cầu, acceptance criteria
DeveloperThiết kế, code, tích hợp
Cyber SecurityGuardrails, secrets, scope
TesterTest, regression, evidence

- MCP Helper (Node.js)Giữ session và credential trong bộ nhớ, gọi Game API, retry, ghi log
- Progress NDJSONReadiness, backlog, tests, blockers: Player đọc file này

### 🇬🇧 English

ARCHITECTUREInside the Team Orchestrator: a PM and specialists

> Team Orchestrator · Project ManagerReceives Player input, picks backlog, assigns work, reviews, integrates, makes the final call and submits

Business AnalystRequirements, acceptance criteria
DeveloperDesign, code, integration
Cyber SecurityGuardrails, secrets, scope
TesterTests, regression, evidence

- MCP Helper (Node.js)Keeps session and credentials in memory, calls the Game API, retries, writes logs
- Progress NDJSONReadiness, backlog, tests, blockers: the Player reads this file


---


## Slide 10: Đừng tin mọi thứ trong guide. / Don't trust everything in the guide.

### 🇻🇳 Tiếng Việt

LỜI NHẮC TỪ BAN TỔ CHỨC

Technical guide là điểm khởi đầu. Hãy kiểm chứng mọi thứ với MTM Player thật và Practice Match trước khi nộp pack.

### 🇬🇧 English

A REMINDER FROM THE ORGANIZERS

The technical guide is a starting point. Verify everything against the real MTM Player and a Practice Match before submitting your pack.


---


## Slide 11: Checklist trước khi bắt đầu / Checklist before you start

### 🇻🇳 Tiếng Việt

CHUẨN BỊChecklist trước khi bắt đầu

Windows và PowerShell 7 · `pwsh --version`
Codex CLI đã đăng nhập · `codex --version`
Node.js cho installer và helper của đội · `node --version`
Tài khoản Game Server, Team ID và Team name: BTC gửi các đội vào ngày 26-Oct
Match ID của Practice match: BTC cung cấp ngày 26-Oct. Match ID các trận chính thức: cung cấp ngay trước trận
Bộ cài MTM Player và sample pack 0.5.1 từ BTC
Thư mục workspace riêng, ví dụ `D:\mtm\<TEAM_ID>`
Quyền chạy exe và script PowerShell trên máy thi


Form BTC gửi qua email
Đội chơi cung cấp cho BTC **càng sớm càng tốt**:

Thông tin account thành viên sẽ nhận license Codex $50→ Nên là người chưa có account Codex Enterprise của FSO
Ký cam kết điện tử bảo mật thông tin (NDA) qua link trong form: chỉ sử dụng account Codex được cấp cho cuộc thi này. Xác nhận đồng ý trên form; BTC sẽ email lại file cam kết đã ký
Thông tin 1 account GitHub của đội, để BTC grant quyền vào repo của đội

### 🇬🇧 English

PREPARATIONChecklist before you start

Windows and PowerShell 7 · `pwsh --version`
Codex CLI signed in · `codex --version`
Node.js for your installer and helper · `node --version`
Game Server account, Team ID and Team name: sent by the organizers on 26-Oct
Practice match ID: provided on 26-Oct. Official match IDs: provided right before each match
MTM Player package and sample pack 0.5.1 from the organizers
A dedicated workspace folder, e.g. `D:\mtm\<TEAM_ID>`
Permission to run executables and PowerShell scripts on the match machine


The organizers' form (by email)
Please send the organizers **as soon as possible**:

The account of the member who will receive the $50 Codex license→ Preferably someone without an FSO Codex Enterprise account
Sign the electronic non-disclosure agreement (NDA) via the link in the form: use the provided Codex account only for this contest. Confirm on the form; the organizers will email back the signed agreement
One GitHub account of the team, so the organizers can grant access to your team repo


---


## Slide 12: Cài MTM Player: portable, không cần plugin / Install MTM Player: portable, no plugin needed

### 🇻🇳 Tiếng Việt

CÀI ĐẶTCài MTM Player: portable, không cần plugin

Giải nén mtm-player-<version>.zip từ BTC
Copy nguyên thư mục player-app (mtm-player.exe, app/) vào thư mục riêng cho version đó, ví dụ mtm-player-app-<version>
Tải Node.js executable node.exe và lưu vào thư mục mtm-player-app-<version>:[https://nodejs.org/dist/v24.12.0/win-x64/node.exe](https://nodejs.org/dist/v24.12.0/win-x64/node.exe)(hoặc chọn bản khác tại [https://nodejs.org/dist/v24.12.0/](https://nodejs.org/dist/v24.12.0/))
Chạy mtm-player.exe, không tham số
Trình duyệt tự động vào trang quản lý của MTM Player (http://127.0.0.1:54321/manage)

# kiểm tra môi trường
pwsh --version
codex --version

# chạy Player
mtm-player-app-<version>\
 mtm-player.exe

# kiểm tra
http://127.0.0.1:54321/health
http://127.0.0.1:54321/manage

Mỗi version một thư mục mới; không trộn app/ hay node.exe giữa các bản. Dừng Player qua giao diện, không kill process.

### 🇬🇧 English

INSTALLATIONInstall MTM Player: portable, no plugin needed

Unzip mtm-player-<version>.zip from the organizers
Copy the whole player-app folder (mtm-player.exe, app/) into its own folder for that version, e.g. mtm-player-app-<version>
Download the Node.js executable node.exe into the mtm-player-app-<version> folder:[https://nodejs.org/dist/v24.12.0/win-x64/node.exe](https://nodejs.org/dist/v24.12.0/win-x64/node.exe)(or pick another build at [https://nodejs.org/dist/v24.12.0/](https://nodejs.org/dist/v24.12.0/))
Run mtm-player.exe without arguments
The browser opens the MTM Player management page automatically (http://127.0.0.1:54321/manage)

# check environment
pwsh --version
codex --version

# run the Player
mtm-player-app-<version>\
 mtm-player.exe

# verify
http://127.0.0.1:54321/health
http://127.0.0.1:54321/manage

One new folder per version; never mix app/ or node.exe across builds. Stop the Player from its UI, don't kill the process.


---


## Slide 13: Sample pack chỉ là bộ khung, không phải Digital Project Team chạy sẵn / The sample pack is only a skeleton, not a ready-to-run Digital Project Team

### 🇻🇳 Tiếng Việt

SAMPLE PACK 0.5.1Sample pack chỉ là bộ khung, không phải Digital Project Team chạy sẵn

team-orchestrator-pack/
 agents/ # 5 profile .toml
 skills/ # SKILL.md (trống)
 bin/ src/ # MCP Helper (trống)
 scripts/ # install, check (trống)
 config/ contracts/ docs/
 install.ps1 # (trống)
 package.json # có sẵn
 team-pack-info.json # có sẵn
 team-runtime.json
 logs/

Có sẵnCấu trúc thư mục, identity, profile các vai trò (chỉ name và description)
Đội tự xâySkills, MCP Helper, installer, checker: toàn bộ “bộ não” và runtime
Ba bài tập sửaIdentity → Agent profile → Skill, rồi cài vào workspace

### 🇬🇧 English

SAMPLE PACK 0.5.1The sample pack is only a skeleton, not a ready-to-run Digital Project Team

team-orchestrator-pack/
 agents/ # 5 profile .toml
 skills/ # SKILL.md (empty)
 bin/ src/ # MCP Helper (empty)
 scripts/ # install, check (empty)
 config/ contracts/ docs/
 install.ps1 # (empty)
 package.json # provided
 team-pack-info.json # provided
 team-runtime.json
 logs/

ProvidedFolder structure, identity, role profiles (name and description only)
You buildSkills, MCP Helper, installer, checker: the whole “brain” and runtime
Three editing exercisesIdentity → Agent profile → Skill, then install into the workspace


---


## Slide 14: Đổi team identity trong team-pack-info.json / Change the team identity in team-pack-info.json

### 🇻🇳 Tiếng Việt

BÀI TẬP 1 · IDENTITYĐổi team identity trong team-pack-info.json

Trước{
 "schemaVersion": 1,
 "version": "1.0.0",
 "teamId": "YOUR_TEAM_ID",
 "teamName": "Your Team Name"
}
Sau{
 "schemaVersion": 1,
 "version": "1.0.0",
 "teamId": "[MÃ_ĐỘI]",
 "teamName": "[Tên đội]"
}

- teamId là mã đội đã đăng ký trên Game Server, không phải UUID
- Giữ đúng 4 field; version khớp với package.json

### 🇬🇧 English

EXERCISE 1 · IDENTITYChange the team identity in team-pack-info.json

Before{
 "schemaVersion": 1,
 "version": "1.0.0",
 "teamId": "YOUR_TEAM_ID",
 "teamName": "Your Team Name"
}
After{
 "schemaVersion": 1,
 "version": "1.0.0",
 "teamId": "[TEAM_ID]",
 "teamName": "[Team name]"
}

- teamId is the team code registered on the Game Server, not a UUID
- Keep exactly these 4 fields; version must match package.json


---


## Slide 15: Cho Developer một bộ chỉ dẫn riêng / Give the Developer its own instructions

### 🇻🇳 Tiếng Việt

BÀI TẬP 2 · AGENT PROFILECho Developer một bộ chỉ dẫn riêng

agents/team-developer.toml
name = "team_developer"
description = "Developer: hiện thực backlog item."
developer_instructions = """
Đọc đề và acceptance criteria do PM giao.
Chỉ sửa code trong thư mục match được giao.
Chạy test liên quan trước khi báo xong.
Trả về: file đã sửa, lệnh test, kết quả.
"""


**Giữ nguyên** name: Player dùng nó để nhận diện profile.
**Thêm** description rõ ràng và developer_instructions ngắn, cụ thể.
**Thử:** cài lại pack, chạy Practice Match, so sánh hành vi trong transcript.

### 🇬🇧 English

EXERCISE 2 · AGENT PROFILEGive the Developer its own instructions

agents/team-developer.toml
name = "team_developer"
description = "Developer: implements backlog items."
developer_instructions = """
Read the task and acceptance criteria from the PM.
Only edit code inside the assigned match folder.
Run the relevant tests before reporting done.
Return: files changed, test commands, results.
"""


**Keep** the name: the Player uses it to identify the profile.
**Add** a clear description and short, specific developer_instructions.
**Try it:** reinstall the pack, run a Practice Match, compare behaviour in the transcript.


---


## Slide 16: Viết chiến thuật cho Orchestrator / Write the Orchestrator's strategy

### 🇻🇳 Tiếng Việt

BÀI TẬP 3 · SKILLViết chiến thuật cho Orchestrator

skills/team-orchestrator/SKILL.md
---
name: team-orchestrator
description: Điều phối đội trong một trận MTM.
---
## Ưu tiên
1. PI trước, rồi item có deadline gần, rồi TASK.
2. Mỗi item có time box; quá giờ thì báo blocker.
## Vòng lặp
- Refresh trang match sau mỗi lần submit.
- Start Work trước khi sửa code.
- Chỉ báo SUBMITTED khi đã có receipt.


Skill là nơi đội thể hiện **chiến thuật**: ưu tiên, phân việc, khi nào dừng.
Ba skill cần viết: team-orchestrator, game-preparation, team-recovery.
Ngắn, rõ, có ví dụ thì agent làm theo tốt hơn.

### 🇬🇧 English

EXERCISE 3 · SKILLWrite the Orchestrator's strategy

skills/team-orchestrator/SKILL.md
---
name: team-orchestrator
description: Coordinates the team during an MTM match.
---
## Priorities
1. PI first, then items with near deadlines, then TASK.
2. Time-box every item; report a blocker when over.
## Loop
- Refresh the match page after every submit.
- Start Work before editing code.
- Report SUBMITTED only with a receipt.


Skills are where your **strategy** lives: priorities, delegation, when to stop.
Three skills to write: team-orchestrator, game-preparation, team-recovery.
Short, clear and with examples: agents follow them better.


---


## Slide 17: Xây phần còn lại, rồi cài vào workspace / Build the rest, then install into the workspace

### 🇻🇳 Tiếng Việt

SAMPLE PACKXây phần còn lại, rồi cài vào workspace

1 · Hoàn thiện runtime
Viết MCP Helper, installer, checker và mock test theo technical guide.
Khi thi đấu, Digital Project Team chạy trên Codex CLI do MTM Player khởi chạy.Tham khảo mục 5 của tài liệu team-orchestrator-technical-guide.html


2 · Cài và kiểm tra (ví dụ)
pwsh -NoProfile -File .\install.ps1 `
 -TeamWorkspace "D:\mtm\[TEAM_ID]"

node .\scripts\check-distribution.mjs `
 --team-workspace "D:\mtm\[TEAM_ID]"

Sửa pack xong thì cài lại. Không cài lại vào workspace đang có trận chạy.

### 🇬🇧 English

SAMPLE PACKBuild the rest, then install into the workspace

1 · Complete the runtime
Write the MCP Helper, installer, checker and mock tests following the technical guide.
During a match, the Digital Project Team runs on the Codex CLI launched by MTM Player.See section 5 of team-orchestrator-technical-guide.html


2 · Install and check (example)
pwsh -NoProfile -File .\install.ps1 `
 -TeamWorkspace "D:\mtm\[TEAM_ID]"

node .\scripts\check-distribution.mjs `
 --team-workspace "D:\mtm\[TEAM_ID]"

Reinstall after every pack change. Never reinstall into a workspace while its match is running.


---


## Slide 18: Chạy Practice Match / Run a Practice Match

### 🇻🇳 Tiếng Việt

INIT MỘT TRẬN ĐẤUChạy Practice Match

Mở MTM Player /manage, nhập đường dẫn workspace, bấm **Load team pack**
Kiểm tra Team ID, Team name, version hiển thị đúng
Bấm **Initialize Match**, điền tài khoản đội, Match ID của Practice Match, chọn model, bấm **Initialize**
Theo dõi Preparation trên dashboard của round
PREPARED → Practice Match tự động start sau 10 giây → agent tự làm
Vào https://masterthemachine.tech/match/<match-id> để biết Backlog mà Agent được giao
Vào https://masterthemachine.tech/spectator/<match-id> để xem Battle Dashboard của trận đấu
Khi trận kết thúc, vào https://masterthemachine.tech/match/<match-id> và click **Re-match** để tạo lại match luyện tập tiếp

Init luôn mở round mới. Muốn chạy tiếp round đang dừng, dùng Resume.

### 🇬🇧 English

INITIALIZE A MATCHRun a Practice Match

Open MTM Player /manage, enter the workspace path, click **Load team pack**
Check Team ID, Team name and version are correct
Click **Initialize Match**, fill in the team account, the Practice Match ID, choose a model, click **Initialize**
Watch Preparation on the round dashboard
PREPARED → the Practice Match starts automatically after 10 seconds → the agent works on its own
Open https://masterthemachine.tech/match/<match-id> to see the backlog assigned to the agent
Open https://masterthemachine.tech/spectator/<match-id> to watch the match's Battle Dashboard
When the match ends, open https://masterthemachine.tech/match/<match-id> and click **Re-match** to practice again

Init always opens a new round. To continue a stopped round, use Resume.


---


## Slide 19: Bốn chỗ để nhìn khi agent không như ý / Four places to look when the agent misbehaves

### 🇻🇳 Tiếng Việt

DEBUGBốn chỗ để nhìn khi agent không như ý

Dashboard của roundCác tab Transcript, Tool, Progress events; Chat để gửi chỉ dẫn ngắn cho agent.
Progress logFile .ndjson của trận: event nào đã ghi, event nào thiếu, đúng thứ tự không.
Session log của Codex`%USERPROFILE%\.codex\sessions`: toàn bộ lượt suy nghĩ, lệnh và tool call.
Round đã lưuPlayer lưu snapshot dashboard của các round trước để so sánh giữa các lần chạy.

### 🇬🇧 English

DEBUGFour places to look when the agent misbehaves

Round dashboardTranscript, Tool and Progress events tabs; Chat to send short guidance to the agent.
Progress logThe match's .ndjson file: which events were written, which are missing, in the right order or not.
Codex session logs`%USERPROFILE%\.codex\sessions`: every reasoning turn, command and tool call.
Saved roundsThe Player keeps dashboard snapshots of earlier rounds so you can compare runs.


---


## Slide 20: Triệu chứng thường gặp và chỗ cần xem / Common symptoms and what to check

### 🇻🇳 Tiếng Việt

DEBUGTriệu chứng thường gặp và chỗ cần xem

Triệu chứngKiểm tra
Load team pack báo lỗiIdentity, version, file thiếu; chạy checker của pack
Preparation đứng mãiAgent đã ghi event sẵn sàng vào progress log chưa
Dashboard không cập nhật backlogEvent có ghi đúng file, đúng schema không
Agent kết thúc khi chưa có backlog mớiSkill phải chờ và refresh, không trả kết quả cuối
Submit timeoutRetry cùng submission ID, không tạo ID mới
Phiên đăng nhập hết hạnHelper đăng nhập lại rồi refresh trang match
Đổi thư mục workspaceCài lại pack, mở session mới
Agent vẫn chạy nhưng công việc không tiến triểnToken/usage limit của tài khoản Codex đã bị vượt chưa; tìm lỗi limit trong transcript

### 🇬🇧 English

DEBUGCommon symptoms and what to check

SymptomCheck
Load team pack failsIdentity, version, missing files; run the pack's checker
Preparation never finishesHas the agent written the readiness event to the progress log?
Dashboard doesn't update the backlogAre events written to the right file, with the right schema?
Agent stops before new backlog arrivesThe skill must wait and refresh, not return a final result
Submit timeoutRetry with the same submission ID; never create a new one
Login session expiredThe helper logs in again, then refreshes the match page
Workspace folder movedReinstall the pack, start a new session
Agent is running but making no progressHas the Codex account hit its token/usage limit? Look for limit errors in the transcript


---


## Slide 21: Câu hỏi của các đội / Questions from the teams

### 🇻🇳 Tiếng Việt

HỎI & ĐÁP

### 🇬🇧 English

Q & A


---

