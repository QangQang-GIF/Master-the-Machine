# Master The Machine 2026 — Plan: Skill-Driven Codex Orchestrator & Workers

**Version:** v0.1 • 22/10/2026  
**Mục tiêu:** Không phát triển lại một AI runtime; tận dụng Codex Desktop/Antigravity 2.0 để phát triển và thử nghiệm, triển khai Codex CLI với một bộ Skills được đánh giá và đóng băng cho trận thi.

## 1. Các nguyên tắc đã chốt

- **Dev Mode:** cho phép khám phá, học từ execution traces, tạo/chỉnh sửa Skills, thử nghiệm A/B, promote/rollback có kiểm soát. Supervisor có thể là Codex Desktop hoặc Antigravity 2.0; Worker đích là Codex CLI.
- **Production Mode:** Game Player khởi chạy Codex Orchestrator theo luật BTC; sử dụng các Skills đã đóng băng; không học hay sửa Skills trong trận, không đòi thao tác của con người. Có thể thích ứng việc chọn Skill/model/effort nếu runtime và BTC cho phép.
- **Auth:** Codex CLI có thể `codex login` bằng ChatGPT/Codex account (không cần API key riêng). Gọi OpenAI Decisions API trực tiếp là tính năng *optional* và cần API key + xác nhận BTC; mặc định decision backend là heuristic hoặc Codex. Đăng nhập Antigravity và Codex là hai cơ chế riêng.
- **Skills không thay thế deterministic code:** trạng thái item, thời gian, quyền nộp một lần, giới hạn tài nguyên, Git integration, schema và kết quả kiểm thử phải được đảm bảo bởi script/adapter, không bằng lời nhắc đơn thuần.
- **Bảo mật:** không truy cập task/MVS chưa công bố; không đem lời giải chuẩn, hidden tests hoặc secret/token vào prompt; dùng sandbox/worktree; không sửa codebase bằng migration tech stack.

## 2. Phân tầng và đường đi chính

```text
DEV SUPERVISOR (Codex Desktop hoặc Antigravity 2.0)
  └── Training Orchestrator Skill
       ├── Benchmark selector → task package
       ├── Dispatch policy → Worker profile + skills + model + effort
       ├── Codex CLI worker (`codex exec`)
       ├── Test oracle + execution trace + metrics
       ├── Failure taxonomy → Skill Evolver
       └── Held-out eval → Skill Registry/Promotion

PRODUCTION (KHÔNG PHỤ THUỘC DESKTOP)
  Game Player → Codex Orchestrator
       ├── backlog monitor + scheduler
       ├── skill finder + model/effort router
       ├── Codex worker(s) + locked worker skills
       ├── verification/evidence/QA gate
       └── guarded git push + once-only submit
```

Ưu tiên **native Codex subagents** nếu plugin của BTC tương thích và đo lường đúng. Nếu không, worker dùng các `codex exec` subprocess, phải kiểm tra phép đồng thời, rate limits, worktree và tracking credits trong Training Camp.

## 3. Cấu trúc repository cho hệ thống Skills

```text
mtm-skill-system/
├── AGENTS.md                         # Ranh giới Dev/Prod và quy tắc chung
├── README.md
├── config/
│   ├── development.yaml               # mutable registry + train setup
│   ├── production.yaml                # locked skills + bounded runtime
│   └── model_profiles.yaml            # allowlist, effort, fallback
├── .agents/skills/
│   ├── mtm-orchestrator/
│   │   ├── SKILL.md
│   │   └── references/{routing,states,scoring}.md
│   ├── mtm-backlog-scheduler/SKILL.md
│   ├── mtm-skill-finder/SKILL.md
│   ├── mtm-worker-dispatch/SKILL.md
│   ├── mtm-integration-guard/SKILL.md
│   ├── mtm-repo-bootstrap/SKILL.md
│   ├── mtm-task-worker/SKILL.md
│   ├── mtm-cr-worker/SKILL.md
│   ├── mtm-bug-worker/SKILL.md
│   ├── mtm-pi-worker/SKILL.md
│   ├── mtm-verification/SKILL.md
│   ├── mtm-evidence/SKILL.md
│   └── mtm-skill-evolver/SKILL.md    # DEV-ONLY, KHÔNG EXPORT PROD
├── runner/
│   ├── codex_adapter.py               # CLI invoke/timeout/log/schema
│   ├── worker_launcher.py             # profiles + worktree + concurrency
│   ├── task_store.py                  # state + idempotency + deadlines
│   ├── grade_adapter.py               # benchmark interface
│   ├── game_adapter.py                # stub đến Training Camp
│   ├── skill_registry.py              # select/pin/hash/version
│   └── qa_gate.py                     # deterministic validation
├── schemas/{task_envelope,worker_result,skill_evaluation}.json
├── benchmarks/{swe_gym,fullstack_mtm}/
├── experiments/{train,validation,heldout}/
├── memory/{failure_patterns,lessons}/
├── releases/{prod_manifest.json,checksums.sha256}
└── tests/{test_contracts,test_router,test_guard,test_skills}/
```

**Phân phối:** Repo làm source of truth. Codex và Antigravity cùng đọc workspace Skills từ `.agents/skills/`; trước trận copy/pin các Worker Skills đã duyệt sang `$HOME/.agents/skills` (hoặc skill scope tương đương) để CLI có thể dùng khi working directory chuyển sang repo khách hàng. Không tự động ghi đè `.agents/skills` không tin cậy trong customer repo.

## 4. Contracts: Orchestrator ↔ Worker

### TaskEnvelope — đầu vào mỗi worker

```json
{
  "task_id": "BUG-07",
  "task_type": "BUG",
  "title": "Auth token expires too early",
  "description": "...",
  "repository_path": "/workspace/worktrees/BUG-07",
  "base_revision": "<commit SHA>",
  "deadline_epoch": null,
  "points": 100,
  "mvs": [{"id":"MVS-1","description":"..."}],
  "evidence_required": ["test_log", "before_screenshot", "after_screenshot"],
  "selected_skills": ["mtm-bug-worker", "mtm-verification", "mtm-evidence"],
  "model": "<allowed model>",
  "reasoning_effort": "medium",
  "time_budget_seconds": 420,
  "limits": {"network": "scoped", "allowed_paths": ["/workspace/worktrees/BUG-07"]}
}
```

**MVS chỉ được đưa vào sau khi Game Master chính thức tiết lộ qua Start Working.** Đừng mô phỏng hoặc khai thác hidden MVS của trận đấu.

### WorkerResult — đầu ra bắt buộc

```json
{
  "task_id": "BUG-07",
  "status": "READY_FOR_REVIEW",
  "skill_ids": ["mtm-bug-worker@1.1.0", "mtm-verification@1.0.2"],
  "changed_files": ["src/auth.py", "tests/test_auth.py"],
  "test_results": [{"command": "pytest tests/test_auth.py", "passed": true}],
  "evidence_paths": ["artifacts/BUG-07/tests.log"],
  "git_revision": "<commit SHA or null>",
  "unresolved_risks": [],
  "elapsed_seconds": 198,
  "usage": {"input_tokens": null, "output_tokens": null, "credits": null}
}
```

QA Gate sẽ xác thực schema, test outcome, file paths, revision tồn tại, evidence requirements và luật trạng thái. Worker **không tự submit** trực tiếp nếu policy tập trung giao quyền submit cho Integration Guard.

## 5. Bộ Skills và hành vi chi tiết

### Orchestrator Skills

1. **mtm-orchestrator:** Tiếp nhận tình huống/mode, đọc luật, duy trì state machine; phối hợp scheduler → finder → dispatcher → QA → submission; hạn chế context pollution; không sửa skill tại PROD.
2. **mtm-backlog-scheduler:** Theo dõi item mới, deadline, điểm, dependency, PI; ước lượng khả năng PASS/thời gian; chọn item với expected score và rủi ro; preempt PI khi có lợi; không dùng AI cho quy tắc deadline/state xác định.
3. **mtm-skill-finder:** Dựa vào type BTC gán sẵn, mô tả, stack và thất bại gần đây để chọn tối đa 1 primary worker skill + các support skills cần thiết; xuất routing JSON; explicit invocation `$skill-name`.
4. **mtm-worker-dispatch:** Map task → model/effort/profile, isolation, timeout, retry/escalation; không cấp thêm process khi bị giới hạn; tránh hai worker sửa cùng một file nếu chưa có chiến lược merge.
5. **mtm-integration-guard:** Thu kết quả, chạy QA, hợp nhất diff nếu có, push đúng commit; kiểm tra item OPEN/WORKING, chưa ACCEPTED, evidence đủ; đánh dấu `SUBMITTING` và reconcile trạng thái khi timeout; submit tối đa một lần được chấp nhận.
6. **mtm-repo-bootstrap:** Nhận diện tech stack và lệnh build/test, baseline tests, symbol map nhẹ, module/test map. Có đường đi nhanh nếu không có thời gian Pre-Match.

### Worker Skills

7. **mtm-task-worker:** Thực hiện nhiệm vụ được mô tả; xác định acceptance criteria; thay đổi tối thiểu; test; trả worker result.
8. **mtm-cr-worker:** Spec → acceptance matrix → implementation → positive/negative/edge tests → regression. MVS không là giới hạn duy nhất của verification.
9. **mtm-bug-worker:** Reproduce → capture BEFORE evidence → hypothesis → minimal patch → AFTER evidence → targeted/regression tests. Nếu không reproduce được, nêu tình trạng và bằng chứng, không tự nhận đã sửa.
10. **mtm-pi-worker:** Urgent triage; logs/metrics → hypotheses → quickest reliable fix → health/recovery check → evidence; ưu tiên time-to-PASS và không mở rộng scope quá mức.
11. **mtm-verification:** Chọn test theo change impact, chạy MVS chính thức, targeted/regression; cân bằng thời gian còn lại, không sử dụng hidden/gold tests.
12. **mtm-evidence:** Chụp before/after đúng thời điểm nếu được yêu cầu, sinh logs/reports, chứng minh evidence có thể truy cập từ revision, không tạo bằng chứng giả.

### Development-only Skill

13. **mtm-skill-evolver:** Dựa trên nhiều traces để rút *generalizable lessons* → đưa ra proposed diff cho Skill → viết skill tests → eval trên validation → promote/rollback. Chỉ tồn tại ở Dev Mode.

## 6. Cấu trúc tối thiểu của từng SKILL.md

```markdown
---
name: mtm-bug-worker
description: Use for officially classified BUG backlog items requiring reproduction, minimal fix, regression verification, and evidence. Do not use for CR or PI.
---

# BUG Worker
## Inputs
Read TaskEnvelope and repository constraints. Never assume unavailable MVS.
## Procedure
1. Inspect repo map and read relevant files.
2. Reproduce the reported defect; capture before evidence if required.
3. Form hypotheses and identify root cause.
4. Make minimal change with targeted tests.
5. Run official MVS when legitimately available and regression tests.
6. Capture after evidence, return WorkerResult JSON.
## Failure / Escalation
If cannot reproduce or tests fail, report exact evidence and blockers; do not claim PASS.
## Forbidden
Do not read hidden tests, change tech stack, fabricate evidence, or submit independently.
```

Giữ SKILL.md ngắn, đặt checklist/command templates theo stack vào `references/` hoặc `scripts/` để tránh prompt bloat. Cung cấp test cases cho **nên trigger** / **không nên trigger** Skill.

## 7. Model, effort và CLI adapter (không cần API key ngoài Codex)

- Chạy `codex login` bằng tài khoản được phép và `codex login status` để xác nhận.
- `codex exec --model <available-model> -c 'model_reasoning_effort="medium"' --json --output-last-message <path> -` nhận prompt từ stdin; parser cần xử lý JSONL và exit code.
- Default policy: task hẹp, chắc chắn → profile nhanh; không rõ/multi-file → profile Sol có effort cao hơn; nhiều lần fail → escalation theo deadline/budget. Kiểm tra availability của model/effort thực tế, không hardcode mọi model đều có.
- Cần **timeout, retry có điều kiện và idempotent state store**. Tránh dùng `--yolo`/tắt sandbox trên source không tin cậy.
- DEV optional: dùng `agy -p "..." --output-format stream-json` cho Antigravity worker trong thử nghiệm *portability*, nhưng Production chỉ Codex theo BTC.

## 8. Skill evolution loop (Dev Mode)

1. **Baseline:** Chạy task với Codex không dùng custom skills, lưu pass/time/credits/trace.
2. **Categorize:** Phân loại thất bại (repo discovery, reasoning, tool misuse, verification, evidence, timeout, integration).
3. **Propose:** Skill Evolver tạo diff một Skill cụ thể, kèm lý do và dự kiến thay đổi hành vi.
4. **Lint:** Kiểm tra frontmatter, links, path, scripts, forbidden instruction, trigger examples.
5. **Evaluate:** A/B thử baseline và candidate trên cùng task validation, reset snapshot/memory, cùng model/effort/budget.
6. **Promote:** Chỉ chấp nhận cải tiến theo metric đặt trước; giữ lại version và trace; không copy đáp án task cụ thể vào Skill.
7. **Freeze:** Chạy held-out test (ưu tiên repo chưa thấy), pin Skill manifest/hash; export Production. Không viết thêm vào Skills khi production.

**Memory chia hai cấp:** (a) shared per-match incident/workspace event state (trong Production cập nhật trạng thái, không sửa skill); (b) cross-match lessons trong Dev để tiến hóa Skill. Cấm truyền các gold patches từ benchmark vào prompt Worker.

## 9. Benchmark và lịch trình

- **SWE-Gym Lite:** 20–30 BUG tasks đầu, lựa chọn theo repository có sẵn grader. Chia theo repository hoặc họ lỗi để tránh leakage.
- **Full Stack FastAPI Template:** Build 10 backlog giả lập chung codebase: 2 TASK, 3 CR, 3 BUG, 2 PI; có deadlines, evidence, test oracle, code state continuity.
- **Tournament Simulator:** 40 phút, backlog reveal theo waves, điểm/PI rank, deadline, chính sách once-only submit và cost. Chạy 1 agent, rồi 2, rồi 3; chỉ tăng concurrency khi score tăng.
- **Metrics:** Score@40min, PASSED items, regression/evidence failures, PI time-to-PASS, Skill Selection Precision, Skill Compliance, wall-clock, token/credits, runtime failure, model escalation.

### Milestones

| Phase | Deliverables | Exit criteria |
|---|---|---|
| P0 — Foundation | Auth CLI, task/result schemas, one worker run, trace parser | Reproducible single task + valid WorkerResult |
| P1 — Worker Skills | TASK/CR/BUG/PI + Verification/Evidence | Triggers đúng, tests/evidence kiểm tra được |
| P2 — Orchestrator Skills | Skill Finder, Scheduler, Dispatcher, Integration Guard | Xử lý một chuỗi backlog giả lập không can thiệp người |
| P3 — Skill Learning | Evolver, failure memory, A/B, promotion, registry | Candidate cải thiện held-out hoặc rollback tự động |
| P4 — Tournament Simulation | 10-item/40-minute generator + scoring | Có baseline và so sánh Score@40min |
| P5 — Official Integration | Game Player adapter, repo bootstrap, auth/runtime tests | End-to-end trial theo API chính thức; no duplicate submission |
| P6 — Freeze | Signed/checksummed skill set, prod config, smoke tests | Production không thể sửa Skills; chạy được sau restart |

### Scope for 22–25/10

Tập trung P0 + P1 (BUG/CR/Verification trước) + tối thiểu P2. Không cố phát triển đủ tất cả tính năng training và parallelism trước Training Camp 26/10. Giai đoạn 26/10–06/11 là lúc tích hợp Game Player và thực nghiệm end-to-end.

## 10. Production acceptance checklist

- [ ] Skills được lint và pin version/hash; có rollback package.
- [ ] Chạy từ Game Player / Codex CLI mà không cần GUI hay API key phụ.
- [ ] Skill Finder chọn đúng nhóm, Codex load đúng Skill (được xác nhận từ trace).
- [ ] Số worker/effort/model tuân thủ allowlist, tài khoản và rate limits.
- [ ] MVS chỉ đọc sau khi được công bố qua quy trình hợp lệ.
- [ ] Test/evidence có thật, gắn đúng item và git revision.
- [ ] Chỉ integration guard có quyền submit và tuân thủ once-only.
- [ ] Sandbox và cô lập git/worktree tránh xung đột.
- [ ] Có timeout/recovery/reconcile, vẫn hoạt động khi worker lỗi.
- [ ] Dev-only Evolver không được bundle trong Production.
- [ ] Benchmark công bằng, không nhiễm gold patch hoặc hidden tests.

## 11. Các thông tin cần BTC xác nhận/kiểm thử 26/10

1. Game Player spawn Orchestrator thế nào, cách nạp AGENTS.md/Skills và môi trường làm việc.
2. Cho phép spawn native Codex subagents hay nhiều `codex exec` sessions; cách tracking chi phí các agent con.
3. Chọn model/effort cho từng worker có được phép không; credits được đo thế nào.
4. MVS/evidence JSON và `gitRevision` SHA grading; có giới hạn parallel Start Working không.
5. Thời gian chuẩn bị codebase trước Start Match, quyền chạy pre-indexing/baseline test.

## 12. Nguồn chính thức

- Codex CLI reference: https://developers.openai.com/codex/cli/reference
- Codex authentication: https://developers.openai.com/codex/auth
- Codex skills: https://developers.openai.com/codex/skills
- Codex subagents: https://developers.openai.com/codex/multi-agent
- Codex as platform: https://developers.openai.com/blog/codex-as-a-platform
- Antigravity CLI headless: https://www.antigravity.google/docs/cli/headless/
- Antigravity Skills: https://www.antigravity.google/docs/skills
- SWE-Gym: https://github.com/SWE-Gym/SWE-Gym
- Full-stack FastAPI Template: https://github.com/fastapi/full-stack-fastapi-template
