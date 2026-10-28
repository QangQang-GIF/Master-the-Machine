---
name: game-preparation
description: Official Master the Machine (MTM) preparation lifecycle skill for verifying Git authentication, repository checkout, toolchain environment, build, match login, and emitting ENVIRONMENT_READY.
---

# Game Preparation Skill

Authoritative operational specification for Master the Machine (MTM) contestant preparation phase.

## 1. Operating Rules & Boundaries
- **Single-Root Codex Session**: Operates within the root supervised Codex session spawned by MTM Player. Never spawn secondary Codex daemons or nested CLI processes.
- **Progress Reporting Protocol**: Append one compact JSON object per line (UTF-8, followed by a newline `\n`) to `descriptor.progressLogFile`. Never overwrite or truncate the file.
- **Allowed Stages**: `GIT_AUTH`, `REPOSITORY`, `ENVIRONMENT`, `BUILD`, `MATCH_LOGIN`.
- **Allowed Statuses**: `IN_PROGRESS`, `COMPLETED`, `BLOCKED`, `FAILED`. (Note: `"PASS"` is NOT a valid status for `PREPARATION_STATUS`).
- **Gating**: Only report `COMPLETED` after actual verification. Do not report backlog work or start tasks until `START_GAME` is received.
- **Player Acknowledgement**: Do not call `Preparation Complete` endpoint directly; MTM Player monitors the log and sends that acknowledgement to the Game Master automatically once `ENVIRONMENT_READY` is detected.

---

## 2. Preparation Execution Workflow

When receiving a frame with `PREPARE_GAME_V2`:

### Step 1: Parse Descriptor
Extract machine parameters from the payload:
- `playerSessionId`: The exact session UUID.
- `requestId`: The preparation request ID.
- `progressLogFile`: The exact absolute path to `<matchId>.progress.ndjson`.
- `repository`: `{ url, branch, folder }`.
- `checkoutRoot`: The target clone/checkout root.
- `matchRoot`: The challenge source folder (`checkoutRoot/repository.folder`).
- `matchPageUrl`: The Game Master URL (e.g. `https://masterthemachine.tech/match/<matchId>`).
- `username`: Game Master login username.
- `password`: Game Master login password.

---

### Step 2: GIT_AUTH & REPOSITORY
1. Verify access to `repository.url` and ensure the repository is checked out at `checkoutRoot`:
   - If not cloned yet: `git clone -b <branch> <url> <checkoutRoot>`
   - If already cloned: `git fetch origin <branch>` and ensure clean working tree on `origin/<branch>`.
2. Inspect target directory `matchRoot` (`<checkoutRoot>/<repository.folder>`).
3. Capture verified Git revision:
   ```bash
   git -C "<checkoutRoot>" rev-parse HEAD
   ```
4. Append progress events to `progressLogFile`:
   ```ndjson
   {"schemaVersion":1,"playerSessionId":"<playerSessionId>","type":"PREPARATION_STATUS","stage":"GIT_AUTH","status":"COMPLETED","summary":"Git authentication verified."}
   {"schemaVersion":1,"playerSessionId":"<playerSessionId>","type":"PREPARATION_STATUS","stage":"REPOSITORY","status":"COMPLETED","summary":"Repository checked out and verified."}
   ```

---

### Step 3: ENVIRONMENT & BUILD
1. Navigate to `matchRoot`.
2. Verify runtime toolchain:
   - Node.js / pnpm / npm / Python / Database as required by project manifests.
   - If `src/.env.example` exists and `src/.env` is missing, copy `.env.example` to `.env` and set local database credentials (`postgresql://freelanceos:freelanceos@127.0.0.1:5433/freelanceos`, `TEST_DATABASE_URL="postgresql://freelanceos:freelanceos@127.0.0.1:5433/freelanceos_test"`), ensuring doctor checks pass.
3. Install dependencies and run baseline build:
   - E.g., `pnpm install` / `pnpm run build` / `npm test`.
4. Append progress events to `progressLogFile`:
   ```ndjson
   {"schemaVersion":1,"playerSessionId":"<playerSessionId>","type":"PREPARATION_STATUS","stage":"ENVIRONMENT","status":"COMPLETED","summary":"Toolchain and dependencies verified."}
   {"schemaVersion":1,"playerSessionId":"<playerSessionId>","type":"PREPARATION_STATUS","stage":"BUILD","status":"COMPLETED","summary":"Baseline build and tests verified."}
   ```

---

### Step 4: MATCH_LOGIN
1. Verify connectivity and login credentials against Game Master at `matchPageUrl` using `username` and `password`.
2. Confirm access to match projection: `https://masterthemachine.tech/match/<matchId>`.
3. Append progress event to `progressLogFile`:
   ```ndjson
   {"schemaVersion":1,"playerSessionId":"<playerSessionId>","type":"PREPARATION_STATUS","stage":"MATCH_LOGIN","status":"COMPLETED","summary":"Match login verified."}
   ```

---

### Step 5: Emit ENVIRONMENT_READY
Append the final `ENVIRONMENT_READY` milestone event to `progressLogFile`:
```ndjson
{"schemaVersion":1,"playerSessionId":"<playerSessionId>","type":"ENVIRONMENT_READY","requestId":"<requestId>","repository":{"url":"<url>","branch":"<branch>","folder":"<folder>"},"revision":"<full_40_char_git_sha>","build":"PASS","matchLogin":"PASS"}
```

---

### Step 6: Generate Architecture Map & Stand By
1. Generate or verify `PROJECT_MAP.md` outside the challenge Git repository:
   - Target path: `<TeamWorkspace>/team-sdlc-pack/state/<matchId>/knowledge/PROJECT_MAP.md`.
   - Never commit `PROJECT_MAP.md` or any state artifacts into the challenge repository.
2. Complete turn and wait for `START_GAME`. Do not attempt any backlog work or modification of the repository prior to game launch.
