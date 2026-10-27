# MASTER THE MACHINE — MASTER SIMPLIFICATION & OFFICIAL-CONTRACT ALIGNMENT PLAN

**Bản kế hoạch:** 1.0 · **Ngày lập:** 27/10/2026 · **Trạng thái:** Kế hoạch triển khai; chưa thay đổi source, chưa chạy live contract tests.

## 0. Quyết định kiến trúc và tiêu chí hoàn thành

**Định hướng:** `MTM Player → one Codex root (team_orchestrator) + frozen Skills + minimal MCP Helper → official Game Master/Git`.

- **Codex sở hữu reasoning**: khám phá source, hoạch định task, phân vai qua native agents khi thật sự hữu ích, implement, test, quyết định PI preemption theo thông tin thực.
- **MCP/Engine sở hữu invariants**: trusted session, public match projection, Start Work, official Submit, idempotency, server-state/deadline, append progress NDJSON, Git remote SHA/evidence/answer checks, crash reconciliation.
- **MTM Player sở hữu lifecycle**: chuẩn bị/bắt đầu/pause/finish/restart, Codex process tree, readiness verification và Preparation Complete. Không tự spawn Codex CLI thứ hai hay giả lập Player.
- **Knowledge Bootstrap sở hữu repository orientation**: sau checkout, trước ENVIRONMENT_READY, tạo `PROJECT_MAP.md` Markdown. Trong game tra cứu chọn lọc; không quét trùng toàn repo mỗi task.

**Definition of Done:** Team pack load/validate được bằng Player thực, preflight của `mtm-ban` READY, API Start/Submit đúng wire contract, answer/evidence/source cùng pushed revision, chuẩn log, 40 phút simulation sạch lỗi giao thức, Codex CLI + practice match được xác minh thật; pass rate/latency/tokens không suy giảm so baseline đã đo. Giảm LOC chỉ là hệ quả, không phải KPI chính.

## 1. Nguồn đã đối chiếu và độ tin cậy

### 1.1 Nguồn cuộc thi

1. Tệp đính kèm `MTM_Game_Rules.html`: 40 phút/1.000 điểm; TASK/CR/BUG/PI; CR/BUG nhận MVS khi Start Work; PI đua tốc độ; mỗi item tối đa một accepted submission; `OPEN/CLOSED`, server clock và PAUSED; -50 điểm/lần tiếp cận máy.
2. Tệp đính kèm `Master the Machine 2026_LaunchEvent.pdf`: Game Player khởi chạy agent, Codex CLI bắt buộc, Game Master server, credit tiết kiệm.
3. [`docs/MTM_Getting_Started.md`](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/docs/MTM_Getting_Started.md): Player, pack, progress log, preparation, practice match.
4. [`docs/team-orchestrator-technical-guide.md`](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/docs/team-orchestrator-technical-guide.md): contract Player/Team Orchestrator, MCP, path, answer/evidence, progress schema, install/check.
5. [`docs/digital-cuder-api.md`](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/docs/digital-cuder-api.md): **wire contract chính thức** của Start Work và Submit, UUID, headers, response, error codes, idempotency.
6. [`docs/team-pack/.../contracts/progress-event.schema.json`](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/docs/team-pack/team-orchestrator-pack-example/team-orchestrator-pack/contracts/progress-event.schema.json): schema NDJSON có `additionalProperties:false`.
7. [`docs/team-pack/.../team-runtime.json`](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/docs/team-pack/team-orchestrator-pack-example/team-orchestrator-pack/team-runtime.json): manifest `protocolVersion:4`, `provider:codex`, rootAgentType `team_orchestrator`.
8. [`docs/team-pack/.../app/README.md`](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/docs/team-pack/mtm-player-0.9.32-macos-arm64/app/README.md): mặc định Player quản lý một Codex root/resume session và dừng cây process khi PAUSED.

**Không áp dụng cho PROD:** file `docs/team-pack/mtm-player-0.9.32-macos-arm64/app/docs/protocol.md` ghi rõ protocol v1 signed-event/stdio là bản **experimental legacy đã bị supersede**; không dùng nó để dựng đường API/receipt của pipeline hiện tại.

**Thứ tự ưu tiên khi nguồn khác nhau:** contract thực tế của đúng phiên bản MTM Player và Game Master practice/staging đã test > schema/manifest tương ứng phiên bản > `digital-cuder-api.md` về HTTP wire > technical guide về lifecycle/progress/workspace > rules > slide > mô phỏng nội bộ. Mỗi bất đồng phải có ticket và smoke test; không tự đoán.

### 1.2 Nguồn `mtm-ban` được tìm thấy

Không phải repository GitHub độc lập: **đây là snapshot practice challenge tại** [`mtm-ban/KWO5GPMP/`](https://github.com/QangQang-GIF/Master-the-Machine/tree/main/mtm-ban/KWO5GPMP) **bên trong repo Master-the-Machine**.

- [`README.md`](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/mtm-ban/KWO5GPMP/README.md): Solova practice 40 phút; wave ở phút 0/14/29 **chỉ của practice này**; giữ `exam-info.json` bất biến; answers ban đầu rỗng; template phát sau khi Start Work; **source và `answers/<ID>/submit.json` + evidence phải chung Git revision**.
- [`answers/README.md`](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/mtm-ban/KWO5GPMP/answers/README.md): đúng `answers/<ID>/submit.json`.
- [`env-require/README.md`](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/mtm-ban/KWO5GPMP/env-require/README.md): Windows setup, Node 20+, Git, pnpm, local PostgreSQL 15+ (khuyến nghị 16) **port 5433**, **không Docker** cho môi trường thi; `setup.ps1`, `prepare-source.ps1`, migrations/seed, `doctor.ps1` trả `Environment status: READY`. Database tests yêu cầu role `CREATEDB`, `SET ON PARAMETER session_replication_role`; nếu nhiều worktree test đồng thời thì mỗi worktree dùng một TEST_DATABASE_URL riêng.
- [`src/package.json`](https://github.com/QangQang-GIF/Master-the-Machine/blob/main/mtm-ban/KWO5GPMP/src/package.json): Next.js 15, React 19, TypeScript 5.9, tRPC 11, Prisma 6, Vitest 3, `pnpm@11.9.0`; scripts `build`, `typecheck`, `test`, `db:deploy`, `db:seed`.
- Source modules: `src/src/app`, `src/src/server/api/routers`, `src/src/server/services`, `src/prisma/schema`, `src/tests`. Đây là **đường dẫn từ folder `mtm-ban/KWO5GPMP/`**; khi runtime `matchRoot` là folder assignment chứa `src/`, dùng `src/src/...`.
- `src/BUILD_REPORT.md` ghi các kết quả lịch sử 12/12 tests và Docker deploy của tác giả source. **Đây không phải test result mới** và **không phải chỉ dẫn sử dụng Docker cho trận thi**; phải chạy lại trên local setup do `env-require` quy định.
- `exam-info.json` đánh dấu immutable; không chỉnh sửa, không xóa, không đưa vào answer patch.

## 2. Các phát hiện P0: code hiện tại chưa khớp contract BTC

| Hạng mục | Hiện trạng trong dự án | Contract/điều cần sửa |
|---|---|---|
| Codex process ownership | `CompetitionOrchestrator` + `WorkerLauncher` tự chạy `codex exec` theo task | MTM Player chạy **một** root `team_orchestrator`; đội có thể dùng native subagent trong cùng session, **không spawn CLI độc lập** cho backlog. Bỏ nested CLI path ở PROD. |
| HTTP Start Work | `OfficialGameMasterAdapter` gọi `POST /tasks/{id}/start` | Phải gọi `POST /api/v1/matches/{MATCH_UUID}/backlogs/start`, JSON chính xác `{"backlogItemKey":"TASK-01"}`. MVS/template nằm trong response. |
| HTTP Submit | Adapter gọi `POST /tasks/{id}/submit` với `task_id`, `git_revision`, `evidence`; idempotency nằm trong JSON body | Phải gọi `POST /api/v1/matches/{MATCH_UUID}/submissions` với `clientSubmissionId`, `backlogItemKey`, `gitRevision` **root**, `answer` và `Idempotency-Key` **header**. Dùng camelCase, strict schema, không `teamId` thừa. |
| Read match/backlog | `GET /match/state`, `/backlog`, `/tasks/{id}`, `/tasks/{id}/mvs` tự giả định | Docs chính thức chỉ mô tả Start/Submit machine API. Backlog/state đọc từ **authenticated `/match/<8-char match code>`**/công cụ được BTC cho phép; không gọi private admin/grader APIs hoặc tự chế REST endpoints. |
| Match ID | Adapter hiện thiếu phân biệt định danh | Path API dùng **Match UUID** lấy từ authenticated match context; match code 8 ký tự dùng trang spectator/Git folder. Không hoán đổi. |
| Authentication | Adapter tự thêm `X-Team-ID` và mang key cố định | Bearer key chỉ nhận khi START_GAME/RESUME_GAME, scope/origin từ descriptor; không truyền key lúc PREPARE. Server suy ra team từ key; không thêm `teamId` vào body. Browser session/cookie dùng đọc `/match`. |
| Idempotency/retry | `SubmissionGuard` đánh dấu in-flight, tránh POST lại; `OfficialGameMasterAdapter` retry logic không phù hợp official format | Persist **UUID, Idempotency-Key, nguyên body bytes + SHA** trước POST. Nếu timeout/429/restart, retry **chính cùng key và body** khi hợp lệ; same replay 201 với `Idempotent-Replay:true`; new key sẽ là submission mới và có thể 409. |
| Submission receipt | Dữ liệu mock/guard kỳ vọng status/points/submission_id ở top level | Official tạo `201` + `submission.id`, `submission.status=ACCEPTED`, `scoreStatus=PENDING` (không đồng nghĩa PASSED). Mapping sang `receiptId` của NDJSON dùng ID do server cấp. Grader result chỉ theo kênh chính thức. |
| Answer/evidence | Guard kiểm `evidence_paths` và SHA nhưng chưa ràng buộc đúng answer file/path | Tạo `matchRoot/answers/<ID>/submit.json`, `answers/<ID>/evidence/*`; answer JSON bằng object request `answer`; evidence paths **tương đối từ answers/<ID>/**; source+answer+evidence nằm **trong chính pushed commit**. |
| Workspace path | Worker scratch/workspace abstraction | `checkoutRoot=<TeamWorkspace>/matches/<matchCode>` chứa `.git`; `matchRoot=checkoutRoot/repository.folder`. Git tại checkoutRoot, source/answers tại matchRoot; không lấy `challengeWorkspace` làm root source. |
| Progress/prepare | Python store/trace không thay thế Player log | Append đúng file `<matchId>.progress.ndjson`, schema strict; ORCHESTRATOR_STARTED, PREPARATION_STATUS, ENVIRONMENT_READY, BACKLOG_SYNC, BACKLOG_STATUS, BACKLOG_TESTS, GAME_MASTER_API, blocker/restart/end. **Player**, không phải team engine, gọi Preparation Complete. |
| Pause/recovery | Worker pause/resume logic nội bộ | Player PAUSED dừng toàn bộ orchestrator tree; resume bằng root mới được Player spawn, dùng saved checkout/receipt/progress, không tự relaunch; không ghi ORCHESTRATOR_ENDED khi pause. |
| Runtime environment | Existing Python/benchmark simulator giả lập task | Practice `mtm-ban` là Node/pnpm/Prisma/PostgreSQL trên Windows; phải test theo doctor/baseline của repo, không dựa mô phỏng giả định. |

**Lưu ý bất nhất tài liệu cần test:** `digital-cuder-api.md` có ví dụ scope `submission:create` trong khi technical guide có `submissions:create` và `backlogs:start`; endpoint vẫn cần quyền thật do descriptor/credential cấp. Phải xác minh qua installed Player và practice credentials; **không tự hardcode phép biến đổi scope**. Technical guide cũng có ví dụ receipt `RECEIVED` chung, trong khi HTTP API cụ thể trả nested `submission.status=ACCEPTED`: adapter wire dùng API doc, progress bridge map về official receipt ID.

## 3. Pipeline production mục tiêu

```text
MTM PLAYER (owns session, PREPARE/START/PAUSE/RESUME/END)
   |
   +--> ONE CODEX ROOT: team_orchestrator
           |
           +--> PREPARE (before match clock)
           |      Git checkout assigned branch → matchRoot
           |      environment doctor / install / build / login
           |      KNOWLEDGE BOOTSTRAP → PROJECT_MAP.md
           |      write ENVIRONMENT_READY to progress NDJSON
           |      MTM Player handles Preparation Complete
           |
           +--> GAME (after START_GAME descriptor)
                  refresh authenticated match page
                  choose visible OPEN backlog
                  MCP start_work(backlogItemKey) → MVS + answer template
                  selected Skills → implement + test + evidence
                  write answers/<ID>/submit.json
                  Git stage only intended source+answer+evidence
                  commit → push exact assigned branch → remote verify SHA
                  MCP submit_work(UUID, backlogItemKey, SHA, answer)
                  persist receipt/status + progress NDJSON
                  refresh changed-file knowledge and poll for new waves
                  PAUSED/FINISHED → STOP, never self-spawn
```

**Production process policy:** No new `codex exec`/`agy`/other CLI child to solve individual tasks from Python. A root Codex can work directly or use approved native subagent profiles in the same hosted session (only if allowedModels and Player contract permit). Default one coding workstream; background match monitoring by helper is bounded and serial; it must stop promptly on host control.

## 4. Engine/API canonical contracts

### 4.1 Thin MCP Helper surface

Implement exactly a small set of tools (names are suggested in BTC technical guide):

- `configure_session(descriptor)`: validate phase, allowed origins, login/cookie, repo assignment, key length/hash/scopes, paths; no secret printed/stored to public logs. PREPARE has no submit bearer; START/RESUME only with verified new bearer.
- `login_match()`: authenticate website session and confirm identity/assigned match; no private match endpoints.
- `refresh_match()`: read public-to-team match projection from authenticated page, return visible backlog, status/deadlines and match identity. Validate assigned repo/branch/folder. Do not fabricate server clock fields.
- `start_work(backlogItemKey)`: GET state/eligibility as provided by projection, HTTP POST official Start path; parse `{submitTemplate, minimumVerificationScenarios, files, replay}`; persist template; return parsed data.
- `submit_work(clientSubmissionId, backlogItemKey, gitRevision, answer)`: **guarded** official POST + journal + retry/replay.
- `report_progress(event)`: validate against exact supplied JSON schema and append one UTF-8 JSON object/line, `schemaVersion:1`, exact current `playerSessionId`.
- `wait_for_update(timeoutMs<=55000)`: bounded serial poll; no overlapping refreshes or unbounded spin; honor host stop.

Use MCP JSON-RPC over stdin/stdout (initialize, notifications/initialized, tools/list, tools/call). Stdout is protocol-only; diagnostic logs to stderr. No arbitrary browser/admin/grader shortcuts, no new service zoo.

### 4.2 Official HTTP wire examples (illustrative IDs only)

**Start Work**:

```http
POST /api/v1/matches/{MATCH_UUID}/backlogs/start
Authorization: Bearer <RUNTIME_TEAM_KEY>
Content-Type: application/json
Accept: application/json

{"backlogItemKey":"BUG-01"}
```

- Start after backlog is OPEN; first response 201 and `replay:false`, retry 200 `replay:true`.
- **MVS/submitTemplate arrive only in Start response**; never GET unannounced MVS.

**Submit** (body schema exact; `answer` is dynamic from template, not hardcoded):

```http
POST /api/v1/matches/{MATCH_UUID}/submissions
Authorization: Bearer <RUNTIME_TEAM_KEY>
Idempotency-Key: 33333333-3333-4333-8333-333333333333
Content-Type: application/json
Accept: application/json

{
  "clientSubmissionId":"33333333-3333-4333-8333-333333333333",
  "backlogItemKey":"BUG-01",
  "gitRevision":"0123456789abcdef0123456789abcdef01234567",
  "answer": {"<copy and fill official submitTemplate>":"..."}
}
```

- `gitRevision` ở **root**; full remote-pushed Git SHA; `answer` là object parsed từ `answers/BUG-01/submit.json` và theo template thực.
- Optional `artifactUrls`: tối đa 20 HTTPS URL nếu thật sự có và được yêu cầu; evidence trong Git answer vẫn là default. Không tự thêm field không tồn tại trong schema.
- `answer` tối đa khoảng 200k JSON chars, request ≤524,288 bytes; reject oversize trước HTTP.
- 201 `submission.id` + `status:ACCEPTED` + `scoreStatus:PENDING`, **không tự cộng điểm**.
- Retry timeout: same UUID/header/exact body, same SHA, replay 201 với `Idempotent-Replay:true`; same key different body = 409 `IDEMPOTENCY_CONFLICT`; new key after accepted = 409 `ALREADY_SUBMITTED`.
- 429 honor `Retry-After` nếu thời gian hợp lệ; 4xx khác phân loại theo structured `error.code` và `X-Request-Id`; không retry 401/403/422 bừa bãi.
- API docs **không cung cấp GET submission status endpoint chính thức**. Grader status đọc qua kênh match projection được phép; không giữ `GET /tasks/{id}/submission` giả lập ở PROD.

### 4.3 Submission journal tối thiểu

Mỗi `(match UUID, backlogItemKey)` lưu local durable record (không chứa secret):

```text
state: DRAFT | STARTED | READY_TO_SUBMIT | IN_FLIGHT | ACCEPTED | GRADED | CLOSED
clientSubmissionId: UUID (once generated never regenerated for same attempt)
idempotencyKey: same UUID
requestBodySha256: SHA256(canonical serialized body bytes)
requestBody: exact serialized bytes, stored securely outside Git
answerPath: repository.folder/answers/<ID>/submit.json
commitSha: full SHA
remoteRef: exact assigned branch
receiptId: server submission.id (when accepted)
lastHttpCode / lastErrorCode / lastRequestId
```

Atomic order: verify → construct body → persist body+UUID+journal `IN_FLIGHT` → POST → persist official receipt → emit `BACKLOG_STATUS SUBMITTED`. On ambiguous network result, **do not generate new UUID/body**; replay unchanged. Pause/resume reconciles journal before new task decisions. Never mark GRADED/PASSED based solely on HTTP 201.

## 5. Commit/answer/evidence protocol — hard gate

**Workspace rule:**

```text
<TeamWorkspace>/matches/<MATCH_CODE>/                 # checkoutRoot; .git here
  <repository.folder>/                                 # matchRoot; value from assignment
    exam-info.json                                     # immutable for Solova practice
    src/                                               # app, Next.js/Prisma/tests
    challenge/                                         # Start Work source templates only
    answers/
      BUG-01/
        submit.json                                    # parsed JSON = request.answer
        evidence/
          BUG-01-TC-01.json                            # evidence relative path in answer
```

**Commit flow (no auto assumption about branch or exact file names):**

1. Validate `git -C checkoutRoot rev-parse --show-toplevel` and assigned `remote URL`, `branch`, `repository.folder` match the descriptor; no sibling match affected.
2. `start_work` first. Persist original template; construct `answers/<ID>/submit.json` **by copying and filling exactly this template**. If template is null, follow official item-specific instruction; do not fabricate schema.
3. Execute required MVS (CR/BUG) plus targeted regression; write actual evidence inside `answers/<ID>/evidence/`. Paths in answer are `evidence/<name>`, not absolute and not prefixed with `answers/<ID>/`.
4. Git stage **only** intended changed source/tests + `repository.folder/answers/<ID>/submit.json` + evidence; reject `.env`, secret, `exam-info.json`, generated Knowledge Index, unrelated paths.
5. Commit to **assigned branch**, push to assigned remote/ref. Capture *full* SHA (40/64 hex as configured). Verify `git ls-remote` SHA at exact assigned ref and `git cat-file` / `git show <sha>:<path>` that answer and every evidence asset exist **in the same revision**. Verify `json.loads(git show answer)` structurally equals payload `answer`.
6. Only then enter submission journal/POST. Never use unpushed `HEAD`, short SHA or working-tree JSON that differs from committed answer.

**No Docker for Solova practice environment** even though old `BUILD_REPORT.md` includes a historical Docker deployment. Preflight DB must be local PostgreSQL on port 5433. Baseline tests use `pnpm test`, `pnpm typecheck`, `pnpm build` as appropriate; status must reflect real exit codes. Test DB must not be shared concurrently.

## 6. Knowledge Bootstrap: `PROJECT_MAP.md` (Markdown-first)

### 6.1 Placement and timing

- Hook into PREPARE after checkout + environment discovery; **before** emitting `ENVIRONMENT_READY`.
- Keep files outside assigned Git repo (proposed writable pack-owned location `<TeamWorkspace>/team-sdlc-pack/state/<matchId>/knowledge/`), validate actual Player permissions. **Never stage or submit this knowledge file.**
- Primary artifact: `PROJECT_MAP.md` (2–4k tokens proposed cap; adjust by experiment). Optional small `index-meta.json` containing base Git SHA, relative paths, file stats and changed-path lookup; JSON is implementation detail, **not** a second knowledge brain.
- **Fast path first**: `git ls-files`, package manifests, lockfile, test config, app entrypoints, schema, paths. Follow with one bounded Codex architectural summary only if preparation budget permits. Never delay readiness indefinitely; if no time, persist a minimal verified map.
- Source of truth is actual files; every assertion in Markdown carries `verified`/`inferred`/`unknown` label. No leaked credentials, hidden tests, unrevealed prompts, grader artifacts or external private endpoints.

### 6.2 Mandatory MD schema

```markdown
# PROJECT_MAP — <assigned repository>

## Identity & Snapshot
- matchCode, assigned repository.folder, base Git SHA, generated at, status (verified/partial)

## Stack & Commands
- platform; toolchain; install; build; typecheck; targeted test; full test
- commands marked verified or not yet run; duration if measured

## Architecture & Data Flow
- key application entrypoints; API surface; routers/services; DB/data model
- module boundary and high-level dependencies with evidence paths

## Modules → Files → Tests
| Module | Primary files | Related tests | Notes |
| ... | ... | ... | ... |

## Environment & Pitfalls
- OS/dependency/database requirements; baseline failures; safe test isolation

## Engineering Conventions
- minimal patch policy, patterns, guardrails, immutable/secret paths

## Task-Time Lookup
- concise pointers and search commands, not full source dumps

## Freshness
- base SHA; changed paths; stale sections; last verified build/test
```

### 6.3 Starter mapping for `mtm-ban/KWO5GPMP` (from observed paths)

| Domain | Files in practice snapshot | Verification |
|---|---|---|
| Frontend/route app | `src/src/app/`, `src/src/components/` | Check `pnpm typecheck`/`pnpm build`; targeted UI tests if present |
| API/tRPC | `src/src/server/api/root.ts`, `src/src/server/api/routers/*.ts`, `src/src/server/api/trpc.ts` | Locate relevant router/service tests |
| Auth/data | `src/src/server/auth.ts`, `src/src/server/db.ts` | Check auth data and DB access before edits |
| Business logic | `src/src/server/services/` | `src/tests/accounting.test.ts`, `cascade.test.ts`, `heatmap.test.ts`, `move-card.test.ts` as applicable |
| Data/migrations | `src/prisma/schema/`, `src/prisma/seed.ts` | Validate local DB permissions and migration safety |
| Environment | `env-require/doctor.ps1`, `src/.env.example`, `env-require/requirements.json` | `doctor.ps1` MUST return `Environment status: READY` |

These are file pointers, **not claims that a specific future task will target a given file**. Never bake Solova-specific paths into the general-purpose knowledge generator; infer them from the assigned repo.

### 6.4 Use and updates during match

- At PREPARE load a short architecture overview into Codex context. For each backlog, retrieve only relevant sections/paths (initial target 300–800 tokens), then Codex opens actual source with `rg`.
- Do **not** call `build_index(repo)` over all files on each task; replace existing `compile_task_context()` usage with `load_project_map` / `lookup_related_paths` from precomputed snapshot.
- After successful commit, update `Freshness` and relevant module summaries from `git diff <base>..<newSHA> --name-only`; do not regenerate entire map. If content/staleness uncertain, mark stale and read source directly.
- On RESUME verify base SHA and on-disk map against Git; rebuild minimal mapping only when mismatch cannot be reconciled. Missing/invalid index must never prevent start, test or valid submission.
- Measure **total** costs (preflight + all tasks) and task latency in A/B, not just retrieval cost.

## 7. Over-engineering reduction decisions

| Existing component(s) | Decision | Execution |
|---|---|---|
| `src/mtm_agents/dispatcher.py` dynamic profile injection, restore, anti-TOCTOU | **REMOVE from PROD** | Player installer registers frozen native profiles; no task-time TOML mutation. Retain ordinary workspace containment in installer; no nanosecond timestamp restore in production path. |
| `src/mtm_competition/routing/skill_router.py`, `model_router.py` | **REMOVE from PROD control path** | Skills declared once in installed pack. Root Codex chooses by task/allowed model; fallback static policy if needed. |
| `routing/prompt_builder.py` | **REPLACE** | Thin task context: public brief, Start Work MVS/template, 2–3 index pointers, deadline and official state; no duplicate process instructions. |
| `workers/launcher.py`, `workers/supervisor.py` | **DISABLE nested Codex process handling in PROD** | MTM Player owns root lifecycle. Keep optional test-only harness for local benchmarks; no second Codex CLI. |
| `scheduling/priority.py`, `preemption.py`, `scheduler.py` | **REDUCE** | Only visibility/deadline/state hard guards and event notification. Root Codex chooses priority/preemption; PI interrupt requested through provider-native control, no second runtime. |
| `integration/integration_guard.py` | **SIMPLIFY** | One assigned worktree/branch with scoped staging and exact commit verification. Remove three-way merge for PROD unless measured/required by concurrent native agents. |
| `core/runtime/codex.py` stream 10MB reciprocal buffering / 50MB quota | **REMOVE from PROD if not used; simplify DEV** | Provider/Player owns root logs; helper uses bounded stderr/file capture and simple tail. Never block pipes. |
| `mtm_knowledge/index.py`, `mtm_agents/context.py` | **REFACTOR** | Pre-game one-shot generation, Markdown first, cheap freshness tracking; no task-time full scan or agent-role injection. |
| `mtm_training`, `mtm_arena` | **KEEP DEV-ONLY** | Freeze/pin skills before packaging; A/B only before match. No runtime imports, prompts or mutable skills in PROD. |
| `submission_guard.py`, `git_guard.py`, `tasks/store.py` | **KEEP core, FIX contract** | Journal same request-body retry, true remote SHA, answer/evidence inclusion, progress schema; SQLite/WAL only if value proven and tests retained. |
| `official_game_master.py`, `adapters/base.py` | **REPLACE wire layer** | Two documented write APIs; authenticated page/projection read; correct UUID, errors, headers, response envelopes. |

**Avoid deleting first:** feature-flag old path as `legacy_dev`, add official-conforming thin pack in parallel, achieve full parity, then delete unused production paths. No massive rename/rewrite before tests.

## 8. Master implementation roadmap (dependency order)

### P0 / Gate A — Lock official contract before refactor

**Work packages**
1. Pin sources to specific commit SHA and installed Player version; capture `team-runtime.json`, `team-pack-info.json`, progress JSON schema and actual START/RESUME descriptors (redacted).
2. Confirm four required provider registrations for installed Player: `team_orchestrator`, `team_repo_scout`, `team_feature_worker`, `team_quality_checker` per technical guide current section 5, despite older 5-role example in section 2. Keep profiles static, no runtime generation.
3. Implement offline contract fixtures: Start 201/200 replay, Submit 201/replay, nested receipt, error envelope (`error.code`, `X-Request-Id`), 409/422/429/413, allowed origin, strict fields, match UUID vs code.
4. Add `docs/OFFICIAL_CONTRACT_MATRIX.md` with links to source lines/spec and version; mark all discrepancies between technical-guide illustrative samples and digital-cuder wire.

**Exit:** every production API/payload/path/state is connected to a verified official document or installed-version test; no invented API endpoint in PROD. Current Codex CLI and actual Player version confirmed.

### P0 / Gate B — Pack + preflight parity

1. Build an **installable** team pack from scaffold; `package.json`, `team-runtime.json`, `team-pack-info.json` versions aligned, root profile name `team_orchestrator`, pack name `team-sdlc-pack`, manifest strict, correct `provider:codex`.
2. Static profiles/Skills + MCP registration; installer resolves absolute paths and preserves match checkouts and logs; checker read-only verifies exact runtime payload/registrations/identity. Trên Windows kiểm tra PowerShell 7, Node và Codex provider authentication đúng yêu cầu Player.
3. Implement PREPARE source checkout, `checkoutRoot` / `matchRoot`, `env-require` setup and `doctor.ps1` READY, authenticated page login, exact `progressLogFile`.
4. **Inject Knowledge Bootstrap here:** create PROJECT_MAP.md after source/environment check; no unrevealed backlog scan. Append accurate `ENVIRONMENT_READY`; Player triggers Preparation Complete. No team-side Preparation Complete API.

**Exit:** Player loads installed pack and sees PREPARED from proper ENVIRONMENT_READY; Solova doctor READY; base revision and knowledge snapshot agree. If Player denies MCP registration or bootstrap, stop release.

### P0 / Gate C — Official Start/Submit + Git answer correctness

1. Implement thin MCP tools and authenticated page reader; validate access scope/origin and phase.
2. Start Work official endpoint; parse MVS/template and write initial answer under `answers/<ID>/submit.json`.
3. Guard evidence, tests, scoped Git staging, assigned branch push, exact SHA + Git-tree proof of answer/evidence, immutable exam-info.
4. Persist UUID/header/body + journal and POST official Submit; map nested `submission.id` to receipt; emit schema-valid events; replay safely after timeout/429/restart.

**Exit:** dry-run with stub Game Master verifies exact request bytes, schema and remote commit; actual practice Start/Submit gives official receipt without 422/409 or missing files. No 2xx falsely called PASSED.

### P1 / Gate D — Codex-native simplification

1. Switch PROD entrypoint to native `team_orchestrator` root via Player; remove `WorkerLauncher` spawning another CLI; no duplicate Scheduler/SkillRouter/PromptBuilder instructions.
2. Codex root uses `mtm-init`, BUG/CR/TASK/PI, verification/evidence procedural Skills. Register native subagent profiles only if Player requires; spawn optional same-session collaborators based on measured advantage.
3. Keep pure state guard, deadline and submission journal in helper. Timeboxed PI strategy is Codex reasoning, with hard STOP for PAUSED/FINISHED and blocked submission when CLOSE.
4. Disable dynamic profile injection, per-task full knowledge scan, 3-way merge (single active branch), adaptive buffer in PROD. DEV test/arena remain separate.

**Exit:** one provider-owned root, no nested Codex process, correct working directory, all data via bounded MCP, no lost control events or duplicate knowledge prompt.

### P0 / Gate E — E2E, practice and release

1. Run current offline tests as baseline; update false-positive mocks to simulate **official** API rather than old `/tasks` paths.
2. Windows end-to-end with actual installed Player + Codex CLI and a Solova `mtm-ban` practice checkout: all PREPARE/START/PAUSED/RESUME/END phases; 0/14/29 waves for this sample; direct source+answer+evidence integration.
3. Chaos cases: timeout after POST accepted, 429 with Retry-After, same-key replay, changed-body conflict, closed deadline, expired browser session, wrong match UUID/code, no MVS before Start, auth key revoked, reconnect, partial Git push, missing evidence, stale Knowledge Index, SQLite restart, Player stop/pause while source being edited.
4. Benchmark A/B: baseline vs simplified w/o index vs simplified w/index, same seed/tasks/model/conditions; score/pass rate, total credits incl. bootstrap, time reveal→first edit, time reveal→accepted, Git failures, event completeness.
5. Freeze skills and pack/checksums, run `check-distribution`, install in fresh Windows workspace, verify without modifying live match; only then retire `legacy_dev` production imports.

**Exit:** all mandatory contract tests pass, live practice accepted, no protocol violations, no human intervention after START, quality not below baseline. Deployment is BLOCKED until Player + Codex CLI + practice API have been tested in the real Windows setup.

## 9. Mandatory acceptance test matrix

| Test ID | Input/condition | Expected |
|---|---|---|
| API-01 | Start Work on OPEN item | `POST /api/v1/matches/{UUID}/backlogs/start`, correct Bearer, body only `backlogItemKey`, template/MVS returned |
| API-02 | Start replay | 200 `replay:true`, no new start |
| API-03 | Match code used instead of Match UUID | local validation rejection; no POST |
| API-04 | Submit after not Start | local block + official `BACKLOG_NOT_STARTED` fixture |
| API-05 | Submit valid | Header `Idempotency-Key`, root `gitRevision`, original template-derived `answer`, 201 official `submission.id` |
| API-06 | Network timeout after server acceptance | same UUID + exact bytes + same header replay; exactly one official receipt |
| API-07 | Same key/different body; new key after acceptance | 409 `IDEMPOTENCY_CONFLICT`; 409 `ALREADY_SUBMITTED`; no local silent overwrite |
| API-08 | 429, 422, 413, 401/403 | bounded backoff if allowed; structured error codes; fail-closed schema/credentials; no blind retry |
| GIT-01 | Correct code but `submit.json` missing from commit | do not POST |
| GIT-02 | Evidence path outside answer dir or omitted from Git tree | do not POST |
| GIT-03 | Local HEAD not equal remote assigned ref | do not POST |
| GIT-04 | `exam-info.json`, `.env`, unrelated file staged | reject stage/commit and preserve files |
| GIT-05 | Wrong checkoutRoot/matchRoot/branch | fail closed; never touch sibling match |
| PREP-01 | Solova doctor not READY | no ENVIRONMENT_READY |
| PREP-02 | Environment READY and PROJECT_MAP generated | log valid; Player owns Preparation Complete |
| LOG-01 | Every progress event | passes exact `progress-event.schema.json`, append-only, correct `playerSessionId`, no extra fields |
| LOG-02 | REQUESTED/success/fail API event | recorded before/after each official action, requestId redacted safe |
| LIFE-01 | PAUSED | stop new API/Git/polling, no ORCHESTRATOR_ENDED; Player stops root |
| LIFE-02 | RESUME | use same checkout and old submit UUID/body, reconcile before work |
| LIFE-03 | FINISHED/CANCELLED | no new commit/submit, proper end when possible |
| KNO-01 | First task after PREPARE | short MD lookup with verified links; no whole repo rescan |
| KNO-02 | Changed source file | refresh only touched module, untouched knowledge intact |
| KNO-03 | Missing/stale index | fallback to direct source inspection without blocking task |
| SEC-01 | Non-allowed origin/admin/grader key access | blocked before network request |
| RUNTIME-01 | Player root runs task | no nested `codex exec` spawned by helper |

## 10. Stop conditions, rollout and rollback

- **Hard stop:** `doctor.ps1` not READY; missing official write privilege; incorrect team/match/repo assignment; no remote SHA; answer/evidence not part of commit; unknown server status; schema violation; wrong Player pack registration; secret leak; prohibited admin/grader request.
- **Soft fallback:** Knowledge Index build fails → minimal static map/direct `rg`; optional subagent fails → root Codex performs task; task hard deadline short → narrow test scope only when still satisfies required MVS and safety gate.
- **Rollback mechanism:** keep original PROD path in isolated branch and fixtures during migration, but never switch to old guessed API path in official production. Rollback code only if it still satisfies current official contract.
- **Observability:** only schema-approved progress fields to Player; separate bounded local diagnostics without credentials; official server is source of truth for accepted time, grading and scores.
- **Production pack freeze:** skill/profile checksums, exact installer distribution, read-only checker; no mutation of skills or installed TOML within game.

## 11. Detailed work order / suggested issue breakdown

1. `P0-01`: Add official contract matrix + JSON fixtures and 30+ contract tests.
2. `P0-02`: Implement installed-compatible team pack, correct static profiles, minimal MCP stub, install/check scripts.
3. `P0-03`: Implement trusted descriptor/session, browser-match projection read, progress event writer.
4. `P0-04`: Implement pregame Knowledge Bootstrap, MD generation, snapshot/freshness and failed-index fallback.
5. `P0-05`: Integrate Solova env (`doctor.ps1`, Node/pnpm/PostgreSQL, test DB) and readiness.
6. `P0-06`: Replace OfficialGameMasterAdapter endpoints/body/header/errors; obtain template/MVS on Start.
7. `P0-07`: Build answer/evidence/commit same-SHA validator + strict Git assignment guard.
8. `P0-08`: Rewrite SubmissionGuard journal, identical-body replay and official receipt parser.
9. `P0-09`: Live Player/Codex/GM practice E2E; correct version-dependent discrepancies.
10. `P1-01`: Switch PROD to Player-owned root Codex; kill nested worker process path; test pause/resume/PI.
11. `P1-02`: Remove DEV-only routers/dispatcher/integration merge complexity from PROD graph.
12. `P1-03`: A/B benchmark Index and architecture, optimize prompt/cost, approve freeze.
13. `P2-01`: Prune unused files/dependencies after two successful full regression runs.

**Dependency:** P0-01 → P0-02/03 → P0-04/05 → P0-06/07/08 → P0-09 → P1-01/02 → P1-03 → P2-01. API/commit correctness and Player compatibility outrank code cleanup.

## 12. Open questions to close only via actual Player/practice verification

1. Installed MTM Player build/version and its exact four provider profile registrations, manifest validator, MCP startup requirements (guide has both older illustrative 5 roles and current four registrations).
2. Runtime key scope names (`submission:create` vs `submissions:create`) and allowed origins in real START_GAME descriptor. Validate scope as provided; never fake it.
3. Which authenticated browser/page tool is allowed to read `/match/<8-char-code>` reliably, and whether any supported projection data hook exists. Do not scrape private/internal endpoints without explicit authorization.
4. Whether any official grading result endpoint is exposed to teams beyond the authenticated match projection. No guessing from old adapter.
5. Whether Player permits package-owned `team-sdlc-pack/state/<matchId>/knowledge/` for generated Markdown across PREPARE/GAME/RESUME; choose approved writable location outside Git.
6. Canonical acceptance receipt mapping (`submission.id` vs guide narrative `receiptId`) and actual `Idempotent-Replay` header behavior.
7. Time allowed for pregame orientation before PREPARED; preflight must cap expensive exploration and prioritize environment READY.
8. How `PAUSED` affects deadlines and whether accepting submissions during pause is configured for current match: default cannot submit, honor server.

## 13. Final recommendation

**Refactor priority must change from “delete over-engineered classes” to “make the real competition contract pass with the smallest runtime”.** The highest-risk bug today is not duplicate prompting but incorrect official API routes, missing answer-in-commit workflow, wrong Player process ownership and missing mandatory progress log. Build contract parity first, then replace duplicated Python intelligence with Codex-native Skills, and put Knowledge Bootstrap entirely in PREPARE.
