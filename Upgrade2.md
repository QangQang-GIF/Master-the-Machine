Dựa trên kiến trúc repo hiện tại, tôi đề xuất refactor theo hướng **“một đường chạy duy nhất, ít abstraction, lỗi là lỗi, không tự cứu bằng fallback”**. Mục tiêu không phải làm framework tổng quát, mà làm một hệ thống thi Master The Machine đơn giản, dễ debug và dễ chỉnh trước ngày thi.

## 1. Kiến trúc đích

Hiện tại repo có nhiều lớp có tính chất platform/framework: routing model, learning/training, nhiều backend, competition engine tổng quát, simulator/arena, config production, recovery/fallback...

Sau refactor, runtime chính nên chỉ còn:

```text
Game Master
    │
    ▼
MCP Helper / Competition Bridge
    │
    ▼
Team Orchestrator
    │
    ├── BUG  → Bug Agent
    ├── TASK → Task Agent
    ├── CR   → Change Request Agent
    └── PI   → Incident Agent
                 │
                 ▼
           Verification
                 │
                 ▼
          Git Guard
                 │
                 ▼
             Submit
                 │
                 ▼
       Official Receipt / Error
```

Không có:

```text
Model Router
Learning System
Backend Fallback
Provider Fallback
Submission Fallback
Generic Worker Scheduler
Complex Runtime Config
Automatic Recovery Chain
Multi-engine orchestration
```

Một lỗi ở bất kỳ bước nào sẽ đi theo:

```text
Operation
   │
   ├── Success → continue
   │
   └── Failure → raise explicit error → orchestrator decides
```

Thay vì:

```text
A failed
 → try B
 → B failed
 → use cached C
 → C missing
 → retry D
 → fallback E
```

Đây là thay đổi quan trọng nhất của toàn bộ refactor.

---

# 2. Scope refactor theo module

| Module hiện tại | Hướng xử lý | Kiến trúc mới |
|---|---|---|
| `mtm_training` | **DELETE** | Không Learning System |
| `routing/model_router.py` | **DELETE** | Model cố định |
| Multiple model configs | **DELETE** | Hardcode Codex/model dùng thi |
| `mtm_backends` | **DELETE / collapse mạnh** | Runtime gọi trực tiếp backend cần thiết |
| Generic Competition Engine | **DELETE phần lớn** | Competition Bridge tối thiểu |
| `orchestrator.py` engine cũ | **REPLACE** | Một TeamOrchestrator đơn giản |
| Worker scheduler | **DELETE** | Orchestrator dispatch trực tiếp |
| Worker supervisor | **DELETE** nếu không cần | Agent lỗi → báo lỗi |
| Retry/fallback framework | **DELETE** | Explicit errors |
| `mtm_agents` | **SIMPLIFY** | 4 sub-agent theo loại task |
| `mtm_knowledge` | **SIMPLIFY** | Project context / repo map |
| `mtm_arena` | **MOVE/DELETE** | Chỉ giữ test thật sự hữu ích |
| AI-Arena | **REMOVE khỏi runtime** | Có thể archive riêng |
| `config/production.yaml` | **DELETE** | Constants trong source |
| release config/manifest | **DELETE nếu không phục vụ launch** | Build đơn giản |
| official Game Master adapter | **KEEP + simplify** | Competition bridge |
| Git Guard | **KEEP** | Submission safety |
| Submission Journal | **KEEP nhưng simplify** | Idempotency/receipt |
| QA gate | **KEEP + simplify** | Một deterministic verification gate |
| `runner/mcp_server.py` | **KEEP nhưng giảm mạnh** | Thin MCP/competition bridge |

Điểm đặc biệt: tôi **không khuyến nghị xoá `GitGuard`, submission journal hoặc verification gate**. Đây không phải những abstraction dư thừa; chúng bảo vệ những thứ trực tiếp ảnh hưởng điểm thi: commit đúng, answer đúng, evidence đúng, submission không bị gửi hai lần.

Ngược lại, generic scheduler, model routing và training system không trực tiếp giúp vòng thi hiện tại và nên bị loại bỏ. 

---

# 3. Hardcode nhưng không hardcode rải rác

Yêu cầu “Hardcode only” là hợp lý cho giai đoạn này, nhưng không nên để literal xuất hiện 30 nơi.

Nên có đúng **một file code chứa runtime constants**, ví dụ:

```text
src/mtm/constants.py
```

Ví dụ:

```python
MODEL = "..."
CONCURRENCY = 1

BUG_AGENT = "bug"
TASK_AGENT = "task"
CR_AGENT = "cr"
PI_AGENT = "pi"

MATCH_DURATION_SECONDS = 2400

START_WORK_PATH = "/api/v1/matches/{match_id}/backlogs/start"
SUBMIT_PATH = "/api/v1/matches/{match_id}/submissions"

REQUIRE_MVS = True
REQUIRE_EVIDENCE = True
```

Như vậy:

```text
Không YAML
Không TOML config runtime
Không config loader
Không env-driven feature flags
Không config layering
Không fallback configuration
```

Các credential bắt buộc vẫn phải tới từ environment/secrets:

```text
API token
session credential
Git credential
```

Không hardcode secret vào repository.

---

# 4. Refactor Orchestrator

Orchestrator mới không cần là một scheduling engine.

Nó chỉ cần làm 6 việc:

```text
receive task
→ classify based on official task type
→ select sub-agent
→ run agent
→ verify result
→ submit
```

Pseudo-flow:

```python
def execute(task):
    agent = get_agent(task.type)

    result = agent.solve(task)

    verification = verify(task, result)
    if not verification.ok:
        raise VerificationError(verification.reason)

    submission = prepare_submission(result)

    receipt = submit(submission)

    return receipt
```

Routing cũng không cần scoring/model routing:

```python
def get_agent(task_type):
    if task_type == "BUG":
        return bug_agent
    if task_type == "TASK":
        return task_agent
    if task_type == "CR":
        return cr_agent
    if task_type == "PI":
        return incident_agent

    raise UnsupportedTaskType(task_type)
```

Task type do Game Master cung cấp thì sử dụng trực tiếp.

Không cần AI classifier cho thứ server đã biết.

---

# 5. Sub-Agent architecture

Tôi đề xuất chỉ giữ **4 execution agent**.

### Bug Agent

Focus:

```text
reproduce
→ locate defect
→ minimal patch
→ tests
```

### Task Agent

```text
understand requirement
→ inspect project
→ implementation
→ tests
```

### Change Request Agent

```text
analyse impact
→ implementation
→ regression verification
```

### Incident / PI Agent

```text
identify immediate failure
→ minimal high-confidence fix
→ verification
```

Không cần BA agent riêng.

Không cần QA agent riêng.

Không cần Security agent riêng ở runtime.

Các concern này có thể nằm trong instruction/skill của từng agent.

Ví dụ CR agent có checklist:

```text
requirements
security impact
regression
tests
evidence
```

Nhờ vậy:

```text
1 Orchestrator
+
4 Worker types
```

thay vì một graph agent phức tạp.

---

# 6. Skills cũng cần giảm

Repo hiện tại có khá nhiều skills và profile. Không nên giữ skill chỉ vì đã viết rồi.

Target có thể là:

```text
.agents/
└── skills/
    ├── team-orchestrator/
    ├── bug/
    ├── task/
    ├── change-request/
    ├── incident/
    └── verification/
```

`evidence` có thể merge vào `verification`.

`mtm-init` có thể merge vào orchestrator.

Các worker skill/profile trùng chức năng nên hợp nhất.

Nguyên tắc:

> Một behavior chỉ nên có một nguồn instruction chính.

Tránh:

```text
profile says X
skill says Y
production yaml says Z
master plan says W
```

---

# 7. Competition Engine → Competition Bridge

Đây là khu vực nên refactor mạnh nhất sau model routing.

Hiện tại có sự chồng lấn giữa generic Competition Engine và production MCP path. Sau refactor chỉ nên tồn tại một đường chạy. 

Target:

```text
src/mtm/competition/
├── client.py
├── session.py
├── submission.py
├── git_guard.py
├── journal.py
├── types.py
└── errors.py
```

### `client.py`

Chỉ chịu trách nhiệm HTTP tới Game Master:

```text
login
start_work
refresh
submit
```

Không retry chain.

Không alternate endpoint.

Không legacy endpoint.

Không silent fallback.

Ví dụ:

```python
response = client.start_work(key)

if response.status_code != 200:
    raise StartWorkError(...)
```

### `session.py`

Chỉ giữ:

```text
match id
match code
assigned branch
authentication/session
current backlog item
```

### `submission.py`

Chỉ:

```text
build request
→ validate
→ journal
→ submit
→ validate receipt
```

### `git_guard.py`

Giữ.

Nhưng chỉ kiểm tra những thứ cần trước submit:

```text
commit exists
remote branch contains SHA
answer exists in commit
required evidence exists
prohibited files untouched
```

### `journal.py`

Giữ vì idempotency là quan trọng.

Nhưng API có thể thu lại thành:

```python
journal.begin(submission)
journal.accept(receipt)
journal.reject(error)
```

Không cần xây state framework tổng quát nếu không sử dụng.

---

# 8. Loại bỏ legacy Game Master fallback

Adapter hiện tại có cả official flow lẫn một số compatibility/legacy behavior.

Refactor nên có nguyên tắc:

```text
Official API only.
```

Nếu official API sai:

```text
raise ProtocolError
```

Nếu credential sai:

```text
raise AuthenticationError
```

Nếu response shape sai:

```text
raise InvalidGameMasterResponse
```

Nếu receipt không hợp lệ:

```text
raise InvalidSubmissionReceipt
```

Không:

```text
try legacy endpoint
try alternate parser
assume response
continue anyway
```

Điều này sẽ làm thi thử ban đầu có thể fail nhiều hơn.

Đó lại là điều tốt ở giai đoạn này, vì các failure đó cho chúng ta biết chính xác contract thật của server.

---

# 9. Verification nên được đưa thành hard gate

Có một điểm tôi đánh giá quan trọng từ kiến trúc hiện tại: production submit path và QA engine đang không hoàn toàn là cùng một pipeline. 

Refactor nên loại sự khác biệt này.

Pipeline mới bắt buộc:

```text
Agent result
    │
    ▼
Verification
    │
    ├── FAIL → stop
    │
    ▼
Git Guard
    │
    ├── FAIL → stop
    │
    ▼
Submit
```

Không tồn tại đường submit bypass verification.

Verification tối thiểu:

```text
required files
required evidence
MVS returned by Start Work
project-specific test
git diff sanity
answer JSON validation
```

Nếu MVS fail:

```text
raise VerificationError
```

Không fallback.

Không submit “best effort”.

---

# 10. Knowledge system

Không nhất thiết xoá toàn bộ `mtm_knowledge`.

Khả năng scan project và tạo repo context vẫn hữu ích cho agent.

Nhưng nó nên được giảm thành:

```text
src/mtm/project/
├── inspect.py
├── context.py
└── types.py
```

Chức năng:

```text
detect language/framework
find package manager
find tests
find build command
find important directories
produce concise project context
```

Không cần:

```text
knowledge learning
historical learning
adaptive routing
model scoring
complex knowledge persistence
```

---

# 11. Repo structure đích

Tôi đề xuất cuối refactor repo gần dạng:

```text
Master-the-Machine/
│
├── .agents/
│   └── skills/
│       ├── team-orchestrator/
│       ├── bug/
│       ├── task/
│       ├── change-request/
│       ├── incident/
│       └── verification/
│
├── runner/
│   └── mcp_server.py
│
├── src/
│   └── mtm/
│       ├── __init__.py
│       ├── constants.py
│       ├── errors.py
│       ├── types.py
│       │
│       ├── orchestrator.py
│       │
│       ├── agents/
│       │   ├── bug.py
│       │   ├── task.py
│       │   ├── change_request.py
│       │   └── incident.py
│       │
│       ├── project/
│       │   ├── inspect.py
│       │   └── context.py
│       │
│       ├── verification/
│       │   └── verify.py
│       │
│       └── competition/
│           ├── client.py
│           ├── session.py
│           ├── submission.py
│           ├── git_guard.py
│           └── journal.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── COMPETITION_PROTOCOL.md
│   ├── PRACTICE_RUNBOOK.md
│   └── REFACTOR_REPORT.md
│
├── pyproject.toml
├── README.md
└── LICENSE
```

Đây là mức complexity tôi cho là hợp lý với mục tiêu hiện tại.

---

# 12. Phase 0 — Freeze baseline

Trước khi xoá code, tạo baseline:

```text
baseline commit SHA
current CI result
existing tests
list runtime entrypoints
list import dependencies
list competition-critical modules
```

Không sửa architecture ở phase này.

Mục đích là có điểm để so sánh:

```text
Before refactor
vs
After refactor
```

Đồng thời đánh dấu ba nhóm:

```text
RUNTIME
TEST-ONLY
DEAD / LEGACY
```

---

# 13. Phase 1 — Delete Model Routing + Learning

Đây nên là PR đầu tiên vì khá độc lập.

Delete:

```text
src/mtm_training/
src/mtm_competition/routing/model_router.py
routing configs
learning configs
training pipeline
learning tests
model scoring/routing tests
unused dependencies
```

Thay tất cả model selection bằng một model constant.

Ví dụ:

```python
MODEL = "TARGET_MODEL"
```

Acceptance:

```text
No import mtm_training
No import model_router
No runtime model selection
No fallback model
Main test suite passes
```

---

# 14. Phase 2 — Remove backend/fallback abstractions

Tìm và xoá toàn bộ patterns kiểu:

```python
try:
    primary()
except:
    fallback()
```

trong những nơi không thực sự cần recovery.

Thay bằng typed error.

Ví dụ:

```python
result = codex.execute(request)

if not result.ok:
    raise AgentExecutionError(result.error)
```

Đồng thời giảm `mtm_backends`.

Nếu chỉ còn Codex production path thì backend abstraction có thể biến mất hoàn toàn.

Acceptance:

```text
one production execution backend
zero provider fallback
zero auto backend switching
errors propagated explicitly
```

---

# 15. Phase 3 — Replace Competition Engine

Đây là phase lớn nhất.

Loại bỏ:

```text
generic scheduler
worker supervisor
priority runtime framework
complex worker lifecycle
generic execution runtime
duplicate orchestration path
```

Giữ:

```text
Game Master client
session
submission
journal
git guard
verification
```

Sau phase này phải có đúng một:

```text
start_work()
```

và đúng một:

```text
submit_work()
```

production path.

Không có legacy submit route.

---

# 16. Phase 4 — Rewrite Orchestrator

Sau khi engine đã gọn, viết orchestrator mới.

Target size hợp lý:

```text
~150–300 LOC
```

chứ không phải một framework hàng nghìn dòng.

Nó chỉ quản lý lifecycle:

```text
START
→ FETCH TASK
→ SELECT AGENT
→ EXECUTE
→ VERIFY
→ COMMIT/PUSH
→ SUBMIT
→ RECEIPT
→ DONE
```

State cũng chỉ cần những trạng thái thật sự có ý nghĩa:

```python
IDLE
WORKING
VERIFYING
SUBMITTING
DONE
FAILED
```

Không cần elaborate state machine nếu Game Master đã là authoritative state.

---

# 17. Phase 5 — Simplify MCP Server

`runner/mcp_server.py` hiện tại có khá nhiều trách nhiệm. 

Sau refactor MCP server nên là **transport layer**, không phải business engine.

Nó nên kiểu:

```python
@tool
def start_work(key):
    return competition.start_work(key)


@tool
def submit_work(...):
    return competition.submit_work(...)


@tool
def get_status():
    return competition.status()
```

Business logic chuyển vào:

```text
src/mtm/
```

Mục tiêu:

```text
runner/mcp_server.py
≈ protocol translation + tool exposure
```

Không:

```text
verification engine
routing logic
config resolution
fallback
workflow policy
```

---

# 18. Phase 6 — Simplify agents + skills

Merge/rename skills theo architecture mới.

Bỏ những prompt/profile duplicate.

Mỗi task có:

```text
1 skill
1 agent implementation
1 clear responsibility
```

Orchestrator instruction chứa logic chung.

Verification chứa quy tắc chung.

Agent không tự submit.

Agent không tự lựa model.

Agent không tự retry model khác.

Agent không thay competition state.

Đây là boundary rất quan trọng.

---

# 19. Phase 7 — Tests cleanup

Không nên cố giữ tất cả test cũ.

Refactor architecture lớn mà cố giữ test của abstraction đã xoá chỉ làm repo phức tạp trở lại.

Test mới nên tập trung vào runtime-critical behavior.

### Unit

Test:

```text
task → correct agent
unknown task → error

Game Master bad response → error
auth failure → error

verification failure → no submit

git guard failure → no submit

receipt malformed → error

journal idempotency
```

### Integration

Test:

```text
fake Game Master
→ Start Work
→ Agent
→ verification
→ git guard
→ submit
→ receipt
```

### Không cần unit-test framework đã xoá

Ví dụ:

```text
model router tests
learning tests
scheduler edge cases
fallback chain tests
unused backend tests
```

Delete cùng module.

---

# 20. E2E strategy: thi thử là source of truth

Tôi đồng ý với mục tiêu:

> E2E cuối cùng phải được đánh giá bằng thi thử thực tế, không phải simulator.

CI chỉ chứng minh:

```text
code internally consistent
```

Nó không chứng minh:

```text
competition integration correct
```

Thậm chí pipeline hiện tại có trường hợp CI xanh nhưng live staging integration bị skip vì thiếu credential. 

Do đó acceptance hierarchy nên là:

```text
Unit tests
    ↓
Integration tests
    ↓
Local dry run
    ↓
Official practice match
    ↓
1000 / 1000
```

### 1000/1000 mới là Final E2E Gate

Không tuyên bố:

```text
PRODUCTION READY
```

chỉ vì:

```text
pytest = green
```

Final readiness chỉ khi:

```text
Practice Game Master
+
real repository
+
real task waves
+
real commits
+
real submissions
+
official scoring
=
1000/1000
```

---

# 21. Sau lần thi thử đầu tiên mới tối ưu reliability

Điều này phù hợp chính xác với mục tiêu bạn đưa ra.

Ở refactor hiện tại:

```text
Don't solve hypothetical failures.
```

Thi thử sẽ cho biết failure nào thực sự tồn tại.

Ví dụ nếu phát hiện:

```text
Game Master occasionally timeout
```

lúc đó mới implement timeout handling.

Nếu:

```text
Next.js match page changes
```

lúc đó mới fix parser.

Nếu:

```text
submission occasionally returns transient 5xx
```

lúc đó mới quyết định có retry hay không.

Không viết 500 LOC để bảo vệ một lỗi chưa từng xảy ra.

---

# 22. Phase 8 — Repo cleanup

Sau khi runtime mới hoạt động, mới cleanup mạnh.

Các nhóm cần kiểm tra để delete:

```text
old configs
old release manifests
deprecated docs
legacy scripts
unused adapters
unused fixtures
learning artifacts
model routing artifacts
old profiles
duplicated skills
unused simulator assets
benchmark-only modules
generated reports
stale production readiness docs
dead test fixtures
```

`AI-Arena/` nên đặc biệt xem xét.

Nếu nó không tham gia:

```text
Start Work → Solve → Submit
```

thì không nên nằm trong production repository.

Có thể:

```text
delete
```

hoặc chuyển thành repository benchmark riêng.

---

# 23. Docs cũng phải refactor

Hiện tại repo có nhiều tài liệu mô tả architecture ở nhiều giai đoạn khác nhau.

Sau refactor chỉ nên còn một số docs authoritative.

### `README.md`

Chỉ:

```text
what
how install
how run
how test
how practice
```

### `ARCHITECTURE.md`

Mô tả architecture thực tế.

Không roadmap lịch sử.

### `COMPETITION_PROTOCOL.md`

Chỉ chứa:

```text
Game Master contract
Start Work
Submit
receipt
branch/commit rules
MVS
```

### `PRACTICE_RUNBOOK.md`

Hướng dẫn thi thử.

### `REFACTOR_REPORT.md`

Report sau cùng.

---

# 24. Report cuối refactor

Report cuối nên có các metric cụ thể:

| Metric | Before | After |
|---|---:|---:|
| Python modules | X | Y |
| Runtime packages | 7 | ~1 main package |
| Agent types | X | 4 |
| Model routing implementations | X | 0 |
| Learning modules | X | 0 |
| Production backends | X | 1 |
| Fallback paths | X | 0 |
| Runtime config files | X | 0 |
| Competition orchestrators | X | 1 |
| Production submit paths | X | 1 |
| LOC | X | Y |
| Test count | X | Y |
| E2E score | — | 1000/1000 |

Đồng thời report phải liệt kê:

```text
deleted files
moved files
renamed files
retained modules
known limitations
practice discoveries
remaining optimization opportunities
```

---

# 25. Thứ tự PR / commit tôi khuyến nghị

Tôi sẽ không làm một mega-commit duy nhất dù đây là refactor lớn.

| PR | Scope |
|---|---|
| **R01** | Baseline + dependency/runtime inventory |
| **R02** | Delete Learning System |
| **R03** | Delete Model Router + model fallback |
| **R04** | Collapse backend layer |
| **R05** | Hardcode runtime constants, delete config system |
| **R06** | Simplify Competition Bridge |
| **R07** | Replace Orchestrator |
| **R08** | Simplify Sub-Agents / Skills |
| **R09** | Hard verification → git → submit pipeline |
| **R10** | Simplify MCP server |
| **R11** | Rewrite critical tests |
| **R12** | Repository cleanup |
| **R13** | Documentation cleanup + Refactor Report |
| **R14** | Practice E2E fixes |
| **R15** | 1000/1000 release candidate |

Sau mỗi PR:

```text
tests green
no broken imports
no dead references
no compatibility shim added
```

---

# 26. Những thứ cố tình KHÔNG làm trong refactor này

Để tránh scope creep, tôi sẽ đặt các mục này ngoài scope:

```text
multi-model optimization
adaptive model selection
learning from previous matches
automatic fallback
distributed workers
complex concurrency
dynamic configuration
advanced retries
self-healing
generic competition framework
generic provider framework
production-grade observability
automatic performance tuning
```

Chúng chỉ quay lại khi thi thử chứng minh có nhu cầu.

---

# 27. Definition of Done

Refactor chỉ được coi là hoàn tất khi thỏa đồng thời:

1. `mtm_training` không còn trong repo.
2. Model Router không còn.
3. Không có production model fallback.
4. Không có backend fallback.
5. Không còn runtime YAML config.
6. Model/runtime policy được hardcode trong source.
7. Chỉ còn một production orchestrator.
8. Chỉ còn một official Game Master path.
9. Chỉ còn một submission pipeline.
10. Orchestrator dispatch trực tiếp sang 4 task agents.
11. Verification bắt buộc trước submission.
12. Git guard bắt buộc trước submission.
13. Không submit nếu MVS fail.
14. Agent không tự submit.
15. MCP server chỉ còn thin bridge.
16. Dead modules/tests/docs đã được xoá.
17. Runtime structure được tổ chức lại rõ ràng.
18. Có `REFACTOR_REPORT.md`.
19. Có practice runbook.
20. **Official practice E2E đạt 1000/1000.**

---

## Đích cuối cùng

Từ kiến trúc hiện tại kiểu:

```text
Core
Competition Engine
Model Router
Backends
Agents
Knowledge
Arena
Training
Config
Scheduler
Supervisor
Fallback
MCP
Game Master Adapter
```

đưa xuống:

```text
             ┌─────────────┐
             │ Game Master │
             └──────┬──────┘
                    │
           ┌────────▼────────┐
           │ Competition     │
           │ Bridge / MCP    │
           └────────┬────────┘
                    │
             ┌──────▼──────┐
             │ Orchestrator│
             └──────┬──────┘
                    │
       ┌────────────┼────────────┐
       │            │            │
      BUG          TASK         CR          PI
       │            │            │           │
       └────────────┴──────┬─────┴───────────┘
                           │
                    ┌──────▼──────┐
                    │ Verification│
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  Git Guard  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   Submit    │
                    └──────┬──────┘
                           │
                       Receipt
```

Tôi xem đây là hướng refactor phù hợp nhất với giai đoạn hiện tại: **xóa complexity trước, làm cho đường thi thật có thể nhìn thấy toàn bộ từ đầu đến cuối, dùng thi thử để khám phá lỗi thực tế, rồi chỉ thêm reliability đúng vào những chỗ đã chứng minh là cần.**