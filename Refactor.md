# Master the Machine — Complete Refactor & Build Plan v2

## 0. Mục tiêu cuối cùng

Sau refactor, repository phải chứa **ba hệ thống độc lập về trách nhiệm nhưng dùng chung core runtime**:

```text
1. COMPETITION AGENT
   → thứ thực sự mang đi thi

2. AI-ARENA
   → benchmark + tournament simulator + evaluator

3. TRAINING / SELF-LEARNING
   → dùng Arena results để cải tiến Skills offline
```

Kiến trúc production đích:

```text
                    BTC GAME PLAYER
                          │
                          ▼
                 Competition Adapter
                          │
                          ▼
                ┌──────────────────┐
                │   ORCHESTRATOR   │
                └────────┬─────────┘
                         │
         ┌───────────────┼────────────────┐
         │               │                │
         ▼               ▼                ▼
     Scheduler       Skill Router    Model Router
         │               │                │
         └───────────────┼────────────────┘
                         ▼
                     Dispatcher
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
      Codex CLI       Codex CLI      Codex CLI
       Worker          Worker         Worker
          │              │              │
          ▼              ▼              ▼
      BUG Skill       CR Skill       PI Skill
          │              │              │
          └──────────────┼──────────────┘
                         ▼
                  WorkerResult
                         │
                         ▼
                   Verification
                         │
                         ▼
                Integration Guard
                         │
                         ▼
                     Git Guard
                         │
                         ▼
                Submission Guard
                         │
                         ▼
                    GAME MASTER
```

Định hướng này cũng khớp với kiến trúc đã nêu trong plan cũ: Game Player → Orchestrator → backlog scheduler → skill/model router → Codex workers → verification → guarded submit.

---

# 1. Sáu nguyên tắc kiến trúc phải chốt trước khi code

## 1.1 Competition Agent là sản phẩm chính

`competition/` là code production.

`arena/` không được trở thành nơi chứa production orchestration.

Quy tắc:

```text
Competition KHÔNG phụ thuộc Arena.

Arena ĐƯỢC PHÉP phụ thuộc Competition.
```

Arena phải benchmark **chính Worker và Orchestrator production**.

---

## 1.2 Skills chính là procedural memory

Không xây một hệ thống “agent chat memory” phức tạp.

Knowledge dài hạn phải được distill thành:

```text
Skills
├── workflow
├── learned rules
├── common failure patterns
├── verification strategy
└── task-specific handling
```

Ví dụ:

```text
BUG experiences
      ↓
generalize
      ↓
mtm-bug-worker v1.3
      ↓
mọi BUG worker tương lai kế thừa
```

---

## 1.3 Self-learning chỉ chạy offline

Development:

```text
Skill
 ↓
Arena
 ↓
Traces
 ↓
Failure Analysis
 ↓
Candidate Skill
 ↓
Held-out A/B
 ↓
Promote / Reject
```

Production:

```text
Frozen Skills
 ↓
Competition
```

Không cho Production tự sửa Skills giữa trận.

Plan cũ cũng đã yêu cầu production sử dụng Skills locked/frozen và Skill Evolver chỉ hoạt động ở Development.

---

## 1.4 Orchestrator quyết định WHAT / WHEN / WHO

Orchestrator không cần biết chi tiết cách debug bug.

Nó quyết định:

```text
WHAT  → làm backlog nào
WHEN  → làm lúc nào / preempt gì
WHO   → worker nào
HOW MUCH → model / effort / timeout
HOW   → Skill nào
```

Skill quyết định:

```text
HOW TO HANDLE TASK
```

Codex Worker thực hiện:

```text
REASON + CODE + TEST
```

---

## 1.5 Deterministic code giữ “truth”

LLM không được chịu trách nhiệm về:

```text
deadline
task states
submission count
git SHA
worker locks
evidence existence
timeout
budget
remote revision
```

LLM được dùng cho:

```text
complexity estimation
risk estimation
code reasoning
debugging
implementation
task strategy
```

---

## 1.6 Không build lại Codex

Codex CLI vẫn là coding/runtime engine.

Project chỉ build:

```text
control plane
skills
verification
simulation
learning
```

---

# 2. Repository structure đích

Sau refactor, repo nên thành:

```text
Master-the-Machine/
│
├── README.md
├── AGENTS.md
├── pyproject.toml
├── uv.lock
│
├── config/
│   ├── development.yaml
│   ├── production.yaml
│   ├── models.yaml
│   ├── routing.yaml
│   └── arena.yaml
│
├── src/
│   │
│   ├── mtm_core/
│   │   ├── contracts/
│   │   │   ├── task.py
│   │   │   ├── worker_result.py
│   │   │   ├── routing.py
│   │   │   └── events.py
│   │   │
│   │   ├── runtime/
│   │   │   ├── codex.py
│   │   │   ├── process.py
│   │   │   ├── trace.py
│   │   │   └── usage.py
│   │   │
│   │   ├── git/
│   │   │   ├── repository.py
│   │   │   ├── revision.py
│   │   │   └── worktree.py
│   │   │
│   │   └── config/
│   │       └── loader.py
│   │
│   ├── mtm_competition/
│   │   ├── orchestrator.py
│   │   │
│   │   ├── tasks/
│   │   │   ├── store.py
│   │   │   ├── state_machine.py
│   │   │   └── scoring.py
│   │   │
│   │   ├── scheduling/
│   │   │   ├── scheduler.py
│   │   │   ├── priority.py
│   │   │   └── preemption.py
│   │   │
│   │   ├── routing/
│   │   │   ├── skill_router.py
│   │   │   ├── model_router.py
│   │   │   └── prompt_builder.py
│   │   │
│   │   ├── workers/
│   │   │   ├── launcher.py
│   │   │   ├── supervisor.py
│   │   │   └── workspace.py
│   │   │
│   │   ├── verification/
│   │   │   ├── qa_gate.py
│   │   │   ├── test_policy.py
│   │   │   └── evidence.py
│   │   │
│   │   ├── integration/
│   │   │   ├── integration_guard.py
│   │   │   ├── git_guard.py
│   │   │   └── submission_guard.py
│   │   │
│   │   └── adapters/
│   │       ├── base.py
│   │       ├── mock_game_master.py
│   │       └── official_game_master.py
│   │
│   ├── mtm_arena/
│   │   ├── benchmarks/
│   │   ├── adapters/
│   │   ├── graders/
│   │   ├── simulator/
│   │   ├── metrics/
│   │   └── reports/
│   │
│   └── mtm_training/
│       ├── traces/
│       ├── failure_analysis/
│       ├── lesson_extractor/
│       ├── skill_evolver/
│       ├── evaluation/
│       └── promotion/
│
├── .agents/
│   └── skills/
│       ├── mtm-init/
│       ├── mtm-orchestrator/
│       ├── mtm-task-worker/
│       ├── mtm-bug-worker/
│       ├── mtm-cr-worker/
│       ├── mtm-pi-worker/
│       ├── mtm-verification/
│       ├── mtm-evidence/
│       └── mtm-skill-evolver/
│
├── skills_registry/
│   ├── manifest.json
│   ├── history/
│   └── candidates/
│
├── releases/
│   ├── prod_manifest.json
│   └── checksums.sha256
│
├── artifacts/
│
└── tests/
    ├── unit/
    ├── integration/
    ├── competition/
    ├── arena/
    └── end_to_end/
```

---

# 3. R0 — Freeze Baseline trước khi Refactor

## Mục tiêu

Không để refactor làm mất những thứ hiện tại đã chạy tốt.

## Việc cần làm

Tạo tag:

```text
pre-refactor-v1
```

Lưu baseline của:

```text
root tests
Arena xs
Arena s
Lean2
Codex baseline
```

Lưu các metric:

```text
resolved
wall time
billable tokens
commands
timeouts
MVS→hidden failure
```

Đặc biệt giữ lại các finding của Lean2.

## Cleanup

Xóa khỏi Git:

```text
__pycache__
*.pyc
```

Sửa fixture `tests/fixtures/sample_repo`.

Thêm:

```text
pyproject.toml
uv.lock
```

Pin dependency.

Không cho `jsonschema` trở thành optional dependency.

## Exit gate

```text
✓ fresh clone chạy test
✓ Arena xs reproduce được
✓ baseline metrics được snapshot
✓ tag pre-refactor-v1 tồn tại
```

---

# 4. R1 — Extract Common Core Runtime

Đây là refactor quan trọng nhất.

## Mục tiêu

Competition và Arena phải sử dụng **cùng một Codex implementation**.

Hiện tại có duplication giữa:

```text
runner/codex_adapter.py
AI-Arena/arena/runner.py
```

Sau refactor:

```text
mtm_core.runtime.codex
```

là implementation duy nhất.

## Migration

```text
runner/trace_parser.py
→ src/mtm_core/runtime/trace.py

runner/codex_adapter.py
→ src/mtm_core/runtime/codex.py

schemas/*
→ src/mtm_core/contracts/

git logic trong Arena
→ src/mtm_core/git/
```

## `CodexRuntime`

Interface:

```python
run(
    workspace,
    prompt,
    model,
    effort,
    timeout,
    sandbox_policy,
) -> RuntimeResult
```

`RuntimeResult`:

```text
exit_code
timed_out
trace
usage
commands
file_changes
last_message
elapsed
```

Không chứa business logic Competition.

## Timeout semantics

Phải sửa ngay bug hiện tại:

```text
timeout
≠
READY_FOR_REVIEW
```

Nếu timeout:

```text
status = TIMED_OUT
```

bất kể worker đã sửa file hay chưa.

## Exit gate

```text
✓ Arena Codex runner dùng mtm_core
✓ Competition runner dùng mtm_core
✓ trace parsing tests pass
✓ timeout test pass
✓ token accounting giống baseline
✓ không còn hai Codex implementations
```

---

# 5. R2 — Chuẩn hóa Contracts

`TaskEnvelope` và `WorkerResult` hiện tại nên giữ nhưng nâng cấp.

## TaskEnvelope v2

```text
task_id
task_type
title
description

points
deadline
match_remaining_time

repo_path
base_revision

mvs
evidence_required

skill_profile
model_profile
reasoning_effort

time_budget
token_budget

sandbox_policy
allowed_paths

attempt_number
```

Không đưa MVS vào trước `Start Working`.

Luật hiện tại cũng cho thấy MVS chỉ xuất hiện sau khi Agent bắt đầu item và hidden grader còn rộng hơn MVS.

## WorkerResult v2

```text
task_id
status

skills_used
model
effort

changed_files

tests
mvs_results
evidence

risk_assessment
confidence

elapsed
usage

base_revision
result_revision
```

## QA Gate phải cross-check Envelope ↔ Result

Không chỉ schema.

Phải kiểm tra:

```text
same task_id
allowed paths
base revision
declared changed files
required evidence
tests
MVS
timeout
status consistency
```

## Exit gate

Ít nhất có test:

```text
invalid task ID
path escape
missing evidence
timeout result
fake changed file
failed required test
wrong base SHA
```

---

# 6. R3 — Xây Init Skill

Đây là điểm bắt đầu của procedural memory.

## `mtm-init`

Nó không xử lý backlog trực tiếp.

Nó định nghĩa baseline policy:

```text
competition boundaries
repo exploration
minimal changes
token/time discipline
testing principles
evidence handling
worker output contract
escalation
forbidden behaviours
```

Structure:

```text
.agents/skills/mtm-init/
├── SKILL.md
├── references/
│   ├── competition-rules.md
│   ├── worker-contract.md
│   ├── efficient-repo-reading.md
│   └── verification-baseline.md
└── tests/
    ├── positive.md
    └── negative.md
```

Lean2 findings nên được seed vào đây:

```text
use rg
bounded reads
avoid whole-tree dumps
tool-output limit
low verbosity
focused verification
```

## Exit gate

Arena `s`:

```text
Init baseline
vs
current Lean2
```

Không được regression đáng kể.

---

# 7. R4 — Xây Specialized Worker Skills

BTC đã xác nhận type được gắn sẵn nên primary skill routing có thể deterministic.

```text
TASK → mtm-task-worker
BUG  → mtm-bug-worker
CR   → mtm-cr-worker
PI   → mtm-pi-worker
```

BTC xác nhận issue đã được đánh type sẵn.

## BUG Pipeline

```text
Inspect
 ↓
Reproduce
 ↓
BEFORE evidence nếu cần
 ↓
Hypothesis
 ↓
Root Cause
 ↓
Minimal Patch
 ↓
Targeted Tests
 ↓
MVS
 ↓
Impact Regression
 ↓
AFTER evidence
 ↓
WorkerResult
```

## CR Pipeline

```text
Parse Requirement
 ↓
Acceptance Matrix
 ↓
Locate Impact
 ↓
Implement
 ↓
Positive Tests
 ↓
Negative Tests
 ↓
Edge Cases
 ↓
MVS
 ↓
Regression
 ↓
Evidence
```

## TASK Pipeline

```text
Understand Definition of Done
 ↓
Locate Area
 ↓
Minimal Implementation
 ↓
Focused Verify
 ↓
Regression if required
 ↓
Result
```

## PI Pipeline

```text
Urgent Triage
 ↓
Fast Localization
 ↓
Hypothesis
 ↓
Quickest Reliable Fix
 ↓
Health/Reproduction Check
 ↓
Minimal Regression
 ↓
Immediate READY_FOR_REVIEW
```

PI pipeline phải ưu tiên latency; BTC đã xác nhận PI bị giảm điểm nếu chậm hơn đội khác.

## Support Skills

Hai skill riêng:

```text
mtm-verification
mtm-evidence
```

Evidence phải là first-class vì backlog có thể yêu cầu screenshot trước/sau hoặc artifact khác.

## Exit gate

Arena phải chạy riêng:

```text
BUG suite
CR suite
TASK suite
PI suite
```

So sánh:

```text
baseline
init-only
specialized-skill
```

---

# 8. R5 — Skill Registry & Versioning

Không cho Skills là một đống file mutable.

## Manifest

```json
{
  "mtm-bug-worker": {
    "version": "1.2.0",
    "sha256": "...",
    "status": "production"
  }
}
```

Candidate:

```text
skills_registry/candidates/
```

Approved:

```text
.agents/skills/
```

History:

```text
skills_registry/history/
```

## Version rules

```text
PATCH
→ wording/procedure refinement

MINOR
→ new learned behavior

MAJOR
→ pipeline contract change
```

## Production

Trước trận:

```text
build manifest
calculate checksum
freeze
```

Không cho self-learning mutate production directory.

---

# 9. R6 — Xây Competition Task Store + State Machine

Đây là nền móng Orchestrator.

## Task states

Đề xuất:

```text
DISCOVERED
   ↓
AVAILABLE
   ↓
STARTING
   ↓
WORKING
   ↓
VERIFYING
   ↓
READY
   ↓
INTEGRATING
   ↓
SUBMITTING
   ↓
ACCEPTED
   ↓
PASSED / FAILED
```

Ngoài ra:

```text
BLOCKED
TIMED_OUT
ABANDONED
CLOSED
```

## Task Store

Không để LLM giữ state trong context.

Dùng:

```text
SQLite
```

là hợp lý nhất.

Tables:

```text
matches
tasks
workers
attempts
submissions
events
```

## Idempotency

Ví dụ:

```text
submit(task_id)
```

phải kiểm tra DB trước.

Process restart không được gây duplicate submission.

---

# 10. R7 — Xây Scheduler

Đây là “brain chiến thuật”.

Khoảng 10 backlog/trận, deadline không đồng nhất và PI speed-sensitive khiến scheduler là một thành phần bắt buộc.

## Priority model ban đầu

```text
ExpectedValue =
P(PASS)
× Points
× DeadlineFactor
× UrgencyFactor
───────────────
ExpectedTime
```

Thêm:

```text
RiskPenalty
DependencyPenalty
ConflictPenalty
```

PI:

```text
very high urgency
```

## P(PASS), ExpectedTime

Giai đoạn đầu:

```text
heuristic
```

Sau này:

```text
Arena empirical routing table
```

## Scheduler phải hỗ trợ

```text
new item arrival
deadline approaching
worker available
worker timeout
PI preemption
task abandonment
retry decision
```

## Không dùng LLM cho

```text
deadline expiration
task status validity
```

## Có thể dùng LLM cho

```text
complexity
scope
risk
estimated implementation difficulty
```

## Exit gate

Simulator tests:

```text
easy BUG vs hard CR
deadline task
PI appears mid-run
worker stuck
limited concurrency
```

Scheduler phải chọn đúng theo expected score.

---

# 11. R8 — Skill Router + Model/Effort Router

## Skill Router

Primary skill deterministic theo type:

```text
BUG
CR
TASK
PI
```

Support skills dynamic:

```text
evidence required?
→ mtm-evidence

verification complex?
→ mtm-verification
```

## Model Router

Không hardcode:

```text
PI → high
BUG → low
```

Feature vector:

```text
task type
description complexity
estimated files
stack
deadline
points
previous attempt
remaining match time
remaining credits
```

Output:

```text
model
effort
timeout
retry policy
```

## Profiles

Ví dụ ban đầu:

```text
fast
standard
deep
emergency
```

## Escalation

```text
LOW
 ↓ failure/blocker
MEDIUM
 ↓ failure
HIGH
```

Nhưng chỉ escalate nếu:

```text
remaining expected value > cost
```

---

# 12. R9 — Prompt Builder

Không để mỗi runner tự ghép prompt.

Một component duy nhất:

```text
PromptBuilder
```

Composition:

```text
Core Worker Protocol
        +
Primary Skill
        +
Support Skills
        +
TaskEnvelope
        +
Relevant repo notes
```

Không inject:

```text
Arena hidden tests
gold patch
training answers
```

PromptBuilder cũng phải đảm bảo MVS chỉ xuất hiện sau khi hợp lệ.

---

# 13. R10 — Worker Launcher + Workspace Isolation

BTC không giới hạn số agent nhưng runtime/concurrency thực tế vẫn phải xác minh với Game Player. Plan hiện tại cũng để đây là điểm cần Training Camp kiểm chứng.

## Worker Launcher phải quản lý

```text
concurrency
timeouts
process handles
workspaces
models
skills
usage
```

## Git strategy

Mặc định development:

```text
one worktree / attempt
```

Worker không push.

Worker chỉ trả:

```text
patch/result
```

Integration Guard mới có quyền tác động submission branch.

## Conflict detection

Trước khi spawn có thể track:

```text
predicted files
actual files
```

Nếu hai worker sửa cùng khu vực:

```text
conflict risk ↑
```

---

# 14. R11 — Verification System

Verification không được phụ thuộc lời agent nói:

> “tests passed”.

## Verification Gate tự chạy

Theo policy:

```text
targeted
MVS
impact regression
evidence check
```

Tùy task/time budget.

## Verification result

```text
VERIFIED
FAILED
INCONCLUSIVE
TOO_EXPENSIVE
```

## MVS

Pass MVS không đồng nghĩa PASS hidden grader, nên policy phải có impact-based regression khi thời gian cho phép. Đây cũng là nguyên tắc từ luật hiện tại.

---

# 15. R12 — Integration Guard

Worker không merge/push/submit.

Integration Guard:

```text
WorkerResult
 ↓
validate base revision
 ↓
apply patch
 ↓
resolve compatibility
 ↓
run integration verification
 ↓
create commit
```

Nếu nhiều task thay đổi codebase:

```text
task B base
≠
current integration HEAD
```

thì:

```text
rebase/revalidate
```

không submit patch cũ mù quáng.

---

# 16. R13 — Git Guard

Sau integration:

```text
commit
 ↓
push
 ↓
remote fetch/verify
 ↓
confirm SHA exists
```

Output:

```text
VerifiedRevision
```

Submission Guard chỉ nhận `VerifiedRevision`.

---

# 17. R14 — Submission Guard

Đây là thành phần critical nhất.

Luật cho thấy submission cần `gitRevision`, sau `ACCEPTED` không thể submit lại, và `ACCEPTED` chưa phải `PASSED`.

## Pre-submit checklist

```text
task state valid?
deadline valid?
not already accepted?
verified revision?
remote SHA exists?
MVS required and green?
evidence required and present?
verification policy satisfied?
integration HEAD correct?
```

Sau đó:

```text
mark SUBMITTING transactionally
 ↓
submit
 ↓
record response
 ↓
reconcile
```

## Phải chịu được

```text
API timeout after request
process crash
duplicate callback
network failure
```

Không được retry mù và submit hai lần.

---

# 18. R15 — Game Master Adapter

Không hardcode API chưa được BTC cung cấp.

Interface:

```python
class GameMasterAdapter:
    get_match_state()
    list_backlog()
    get_task()
    start_working()
    get_mvs()
    submit()
    get_submission_status()
```

Hai implementations:

```text
MockGameMasterAdapter
OfficialGameMasterAdapter
```

Cho tới khi BTC phát tài liệu thật:

```text
OfficialGameMasterAdapter = explicit stub
```

Không invent endpoints.

---

# 19. R16 — Repo Bootstrap

Mỗi vòng là source base khác nên bootstrap phải tự động. BTC đã xác nhận mỗi vòng sử dụng repo/source base khác.

## Bootstrap pipeline

```text
clone/pull
 ↓
detect stack
 ↓
detect package manager
 ↓
detect build
 ↓
detect test framework
 ↓
baseline
 ↓
identify modules
 ↓
write repo notes
```

Output:

```text
.project/
├── repo_profile.json
├── test_commands.json
└── architecture_notes.md
```

Đây là **ephemeral repo context**, không phải long-term memory.

---

# 20. R17 — Refactor AI-Arena

Sau khi Competition Worker tồn tại, Arena không được sử dụng runner riêng.

Sai:

```text
Arena → arena.runner
Competition → competition.runner
```

Đúng:

```text
Arena
  ↓
Competition WorkerLauncher
  ↓
Core CodexRuntime
```

## Giữ lại từ Arena hiện tại

```text
benchmark adapters
Docker graders
catalog
sets xs/s/m/full
metrics
reports
dashboard
hidden-test model
MVS model
COBOL suite
```

## Xóa/giảm

```text
duplicate Codex runner
production-like orchestration logic
```

## Arena modes

```text
worker-eval
skill-eval
model-eval
match-sim
```

---

# 21. R18 — Tournament Simulator

Worker benchmark chưa đủ.

Phải có **Match Simulator**.

BTC hiện mô tả khoảng 10 backlog, có deadline khác nhau, PI speed-sensitive.

## Simulator phải mô phỏng

```text
40-minute global clock
~10 items
wave reveals
TASK / CR / BUG / PI
deadlines
MVS reveal after Start Working
evidence requirements
one-shot submission
common evolving repository
limited workers
cost
PAUSE
```

## Scenario example

```text
00:00 BUG-01
00:00 TASK-01
04:00 CR-01
07:30 PI-01
13:00 BUG-02 deadline +8m
...
```

## Score

Không chỉ worker PASS.

Phải đo:

```text
Score@40min
deadline misses
PI latency
credits
late work
wasted attempts
```

---

# 22. R19 — Arena Metrics v2

Giữ metrics hiện có:

```text
pass rate
median/P90
billable tokens
commands
first edit
MVS-green-hidden-fail
```

Thêm competition metrics:

```text
Score@40m
Expected points/min
Expected points/credit
Deadline Miss Rate
PI Time-to-PASS
Worker Utilization
Scheduler Regret
Abandonment Rate
Conflict Rate
Verification Failure Rate
Submission Failure Rate
Model Escalation Rate
Skill Compliance
```

---

# 23. R20 — Self-Learning System

Chỉ làm sau khi:

```text
Skills ổn
Arena ổn
Competition worker ổn
```

## Pipeline

```text
Production Skill vN
       ↓
Training Benchmark
       ↓
Traces + Metrics
       ↓
Failure Classifier
       ↓
Failure Clusters
       ↓
Lesson Extractor
       ↓
Skill Evolver
       ↓
Candidate vN+1
       ↓
Validation Set
       ↓
Held-out Set
       ↓
Promotion Gate
```

---

# 24. Failure taxonomy

Chuẩn hóa failure:

```text
REPO_DISCOVERY
TASK_UNDERSTANDING
WRONG_HYPOTHESIS
OVER_EDIT
UNDER_EDIT
TOOL_MISUSE
TOKEN_WASTE
MVS_OVERFIT
REGRESSION
EVIDENCE
TIMEOUT
MODEL_CAPABILITY
SKILL_ROUTING
INTEGRATION
SUBMISSION
```

Self-learning học trên cluster.

Không học từ một task đơn lẻ nếu chưa đủ bằng chứng.

---

# 25. Lesson Extractor

Input:

```text
5–20 similar failures
```

Output:

```text
general lesson
confidence
evidence count
target skill
expected impact
```

Ví dụ:

```text
Observed:
7 BUG workers passed MVS but failed broader grader.

Lesson:
After MVS green, run one impact-targeted regression
when remaining budget permits.
```

Không lưu:

```text
file X line 53
gold patch
specific benchmark answer
```

---

# 26. Skill Evolver

Không được rewrite tùy ý.

Output phải là:

```text
base version
target section
proposed diff
reason
failure cluster
expected trade-off
```

Ví dụ:

```text
mtm-bug-worker 1.2.0
→ candidate 1.3.0
```

---

# 27. Promotion Gate

Candidate chỉ được promote nếu vượt gate.

Ví dụ:

```text
Primary:
pass_rate_candidate >= baseline

AND

ScoreEfficiency improves

AND

no major regression by stack/type
```

Có tolerance.

Không chọn Skill chỉ vì sample nhỏ thắng 1/1.

## Data split

```text
TRAIN
VALIDATION
HELD-OUT
```

Held-out ưu tiên repo chưa dùng để evolve Skill.

---

# 28. R21 — Routing Learning

Arena report nên xuất artifact máy đọc được:

```text
artifacts/routing.json
```

Ví dụ:

```json
{
  "BUG": {
    "react": {
      "fast": {
        "pass_rate": 0.88,
        "median_seconds": 95,
        "billable_tokens": 14000
      },
      "deep": {
        "pass_rate": 0.92,
        "median_seconds": 170,
        "billable_tokens": 31000
      }
    }
  }
}
```

Production router dùng frozen version artifact này.

Không để Orchestrator “đoán cảm tính” model tốt nhất.

---

# 29. R22 — Production Freeze

Trước trận build release:

```text
release/
├── skills/
├── routing.json
├── models.yaml
├── prod_manifest.json
└── checksums.sha256
```

Manifest:

```text
git revision
skill versions
skill hashes
routing version
config version
test results
```

Production startup:

```text
verify checksums
verify Codex login
verify CLI capabilities
verify repository access
verify Game Master connectivity
```

Nếu Skill bị sửa:

```text
REFUSE TO START
```

---

# 30. R23 — End-to-End Test Matrix

Không gọi project “complete” cho tới khi có các test sau.

### Worker

```text
BUG success
BUG cannot reproduce
CR success
TASK success
PI success
worker timeout
Codex crash
```

### Verification

```text
MVS failure
MVS green + regression failure
missing evidence
path escape
fake result
```

### Orchestration

```text
new backlog wave
deadline
PI preemption
no worker available
worker timeout
retry escalation
```

### Integration

```text
non-conflicting patches
conflicting patches
stale base revision
```

### Submission

```text
success
network timeout
duplicate attempt
already accepted
deadline expired
wrong SHA
remote SHA missing
```

### Recovery

```text
restart Orchestrator
restart during worker
restart during SUBMITTING
state recovery
```

---

# 31. Definition of Done cho “Competition Ready”

Project chỉ được coi là competition-ready khi:

```text
✓ Game Player có thể spawn Orchestrator
✓ Orchestrator tự discover backlog
✓ tự Start Working
✓ route đúng Skill
✓ route model/effort
✓ launch Codex worker
✓ worker tuân specialized pipeline
✓ verification deterministic
✓ evidence đầy đủ
✓ integration an toàn
✓ commit/push đúng
✓ remote SHA verified
✓ once-only submit
✓ theo dõi result
✓ xử lý task mới trong lúc worker chạy
✓ PI có thể được ưu tiên
✓ deadlines được dùng từ server
✓ crash/restart không duplicate submit
✓ Production Skills immutable
```

Checklist này cũng tương ứng với acceptance checklist cũ: Skill routing, model/effort allowlist, evidence đúng revision, Integration Guard giữ quyền submit, isolation, timeout/recovery và benchmark không nhiễm hidden/gold answers.

---

# 32. Definition of Done cho “Self-Learning Ready”

```text
✓ Arena chạy chính production worker
✓ traces chuẩn hóa
✓ failure taxonomy hoạt động
✓ failure clustering
✓ lesson extraction
✓ candidate Skill versioning
✓ validation A/B
✓ held-out evaluation
✓ automatic reject
✓ promotion manifest
✓ rollback
✓ no gold leakage
```

---

# 33. Thứ tự triển khai thực tế

Không nên implement tất cả song song.

```text
R0  Baseline Freeze
 ↓
R1  Common Core Runtime
 ↓
R2  Contracts
 ↓
R3  Init Skill
 ↓
R4  Worker Skills
 ↓
R5  Skill Registry
 ↓
R6  Task Store
 ↓
R7  Scheduler
 ↓
R8  Routing
 ↓
R9  Prompt Builder
 ↓
R10 Worker Launcher
 ↓
R11 Verification
 ↓
R12 Integration
 ↓
R13 Git Guard
 ↓
R14 Submission Guard
 ↓
R15 Game Adapter
 ↓
R16 Repo Bootstrap
 ↓
R17 Arena Refactor
 ↓
R18 Tournament Simulator
 ↓
R19 Metrics v2
 ↓
R20 Self Learning
 ↓
R21 Routing Learning
 ↓
R22 Freeze
 ↓
R23 E2E Hardening
```

---

# 34. Có thể gom thành 7 milestone delivery

| Milestone | Nội dung | Exit Criteria |
|---|---|---|
| **M0 Refactor Foundation** | R0–R2 | Core chung, tests xanh |
| **M1 Skill Workers** | R3–R5 | 4 loại worker chạy qua Arena |
| **M2 Orchestrator Brain** | R6–R10 | tự schedule/route/dispatch |
| **M3 Safe Delivery** | R11–R16 | verify → git → submit end-to-end |
| **M4 Arena 2.0** | R17–R19 | worker + match benchmark |
| **M5 Self-Learning** | R20–R21 | Skill candidate promote/rollback |
| **M6 Production Freeze** | R22–R23 | full tournament smoke test |

---

# 35. Phần nào của repo hiện tại nên giữ

## Giữ gần như nguyên vẹn

```text
AI-Arena benchmark adapters
Docker grading
metrics formulas
data sets
reports
dashboard
COBOL benchmark
SWE adapters
```

## Refactor/reuse

```text
runner/codex_adapter.py
runner/trace_parser.py
runner/qa_gate.py
schemas/*
config/*
```

## Không lấy làm production architecture

```text
Ruflo harness
Hermes harness
OpenCode harness
```

Có thể giữ làm Arena experimental agents.

---

# 36. Migration mapping cụ thể

```text
OLD
runner/codex_adapter.py
→
NEW
src/mtm_core/runtime/codex.py
```

```text
OLD
runner/trace_parser.py
→
NEW
src/mtm_core/runtime/trace.py
```

```text
OLD
runner/qa_gate.py
→
NEW
src/mtm_competition/verification/qa_gate.py
```

```text
OLD
schemas/*
→
NEW
src/mtm_core/contracts/
```

```text
OLD
AI-Arena/arena/runner.py
→
NEW
thin adapter calling:
mtm_competition.workers.WorkerLauncher
```

```text
OLD
AI-Arena/arena/metrics.py
→
NEW
src/mtm_arena/metrics/
```

```text
OLD
AI-Arena/arena/adapters/*
→
NEW
src/mtm_arena/adapters/
```

Không migrate blindly toàn bộ repo; mỗi file phải có owner/domain rõ.

---

# 37. Những thứ không nên build ngay

Để tránh overengineering:

```text
✗ vector database memory
✗ agent-to-agent messaging bus
✗ custom LLM runtime
✗ custom model server
✗ complex distributed scheduler
✗ Kubernetes
✗ arbitrary MCP swarm
✗ online Skill mutation
✗ RL training
```

Không cần để thắng bài toán này ở giai đoạn hiện tại.

---

# 38. Các câu hỏi BTC còn phải resolve trước Official Integration

Không nên block phần còn lại của project nhưng adapter phải chừa interface cho chúng:

```text
Game Player spawn Agent thế nào?
native subagents / multiple codex exec có hợp lệ không?
model/effort selection có bị hạn chế không?
MVS schema cụ thể?
evidence schema?
submission endpoint?
gitRevision được grader checkout chính xác ra sao?
parallel Start Working có giới hạn?
credits worker con được đo thế nào?
```

Plan hiện tại cũng đã đánh dấu chính các điểm này là thứ cần xác minh khi Training Camp.

---

# 39. Kiến trúc triết lý cuối cùng

Toàn bộ project có thể được tóm thành:

```text
                 AI-ARENA
                     │
                 evaluates
                     ▼
                ┌─────────┐
                │ SKILLS  │
                └────┬────┘
                     │ memory
                     ▼
GAME MASTER → ORCHESTRATOR
                 │
          WHAT / WHEN / WHO
                 │
                 ▼
            CODEX WORKERS
                 │
             HOW via Skills
                 │
                 ▼
           VERIFICATION
                 │
                 ▼
             SUBMISSION
```

Và Training loop:

```text
Experience
    ↓
Trace
    ↓
Failure
    ↓
Generalized Lesson
    ↓
Skill Candidate
    ↓
Arena Validation
    ↓
Promotion
```

Đây là core design mà mọi PR sau refactor phải tuân theo.

---

# 40. Ưu tiên ngay lập tức

Nếu bắt đầu build từ repo hiện tại, thứ tự PR hợp lý nhất là:

```text
PR-01  Freeze baseline + dependency cleanup
PR-02  Extract mtm_core Runtime
PR-03  Contract v2 + hardened QA
PR-04  Init Skill + Skill registry
PR-05  BUG/CR/TASK/PI Skills
PR-06  Competition WorkerLauncher
PR-07  TaskStore + StateMachine
PR-08  Scheduler + Router
PR-09  Verification + Evidence
PR-10  Integration/Git/Submission Guards
PR-11  Mock Game Master + E2E flow
PR-12  Refactor Arena onto production worker
PR-13  40-minute Tournament Simulator
PR-14  Self-Learning pipeline
PR-15  Production freeze / release tooling
PR-16  Official BTC adapter
```

**PR-10 phải hoàn tất trước khi coi Competition Agent là an toàn.**

**PR-13 phải hoàn tất trước khi tin rằng Scheduler thực sự tốt.**

**PR-14 chỉ bắt đầu khi Worker và Arena đã ổn định.**