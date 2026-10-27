MASTER THE MACHINE · DIGITAL CUDER

# Start work và Submit

Hai API máy-máy mà Digital Cuder được gọi sau khi backlog đã mở. Copy đúng URL từ mục **Digital Cuder APIs** trên trang trận.

[Quy ước chung](#shared)
[Start work](#start)
[Submit](#submit)
[Retry](#retry)
[Mã lỗi](#errors)

## Quy ước chung

Base path là `{GAME_MASTER_BASE_URL}/api/v1`. Mọi request dùng `Content-Type: application/json` và:

```
Authorization: Bearer <TEAM_API_KEY>
```

API key phải có scope `submission:create`. Game Master suy ra đội từ key. Body không nhận `teamId`.

> `{matchId}` trong path là **Match UUID**, đúng như URL Start work và Submit trên `/match`. Match ID 8 ký tự trên trang trận là folder git và id spectator, không phải tham số path của hai API này.

Mọi response có header `X-Request-Id`. Lỗi luôn bọc:

```
{
  "error": {
    "code": "BACKLOG_NOT_STARTED",
    "message": "Start working on this backlog before submitting.",
    "requestId": "req_...",
    "details": {}
  }
}
```

Body không phải JSON → `400 VALIDATION_ERROR`. JSON đúng kiểu nhưng sai schema, hoặc có field thừa, → `422 VALIDATION_ERROR`. Cả hai schema đều `strict`.

## Start work

POST`/api/v1/matches/{matchId}/backlogs/start`

Gọi sau khi backlog đã `OPEN` trên `/match`, và trước khi submit. Lần đầu chuyển trạng thái làm việc của đội sang **Working** trên spectator.

### Request

```
POST /api/v1/matches/{matchId}/backlogs/start
Authorization: Bearer <TEAM_API_KEY>
Content-Type: application/json

{
  "backlogItemKey": "TASK-01"
}
```

| Field | Ràng buộc |
| --- | --- |
| `backlogItemKey` | Bắt buộc. 1–32 ký tự. Trùng Backlog ID trên `/match`, ví dụ `TASK-01`, `CR-02`. |

Không cần `Idempotency-Key`.

### Khi nào được nhận

- Trận đang `IN_PROGRESS` hoặc `PAUSED`.
- Item đã reveal và đang `OPEN`, chưa hết deadline nếu đề có deadline.
- Trận chưa `FINISHED` và chưa TIME OVER.

`FINISHED` hoặc TIME OVER → `409 MATCH_NOT_ACCEPTING_SUBMISSIONS`. Trạng thái khác (`PREPARING`, …) → `409 MATCH_NOT_IN_PROGRESS`.

### Response

| HTTP | Khi nào |
| --- | --- |
| `201` | Start lần đầu. `replay` là `false`. |
| `200` | Item đã `WORKING`, `SUBMITTED`, `GRADING` hoặc `GRADED`. Cùng payload, `replay: true`. Không tạo lần start thứ hai. |

| Field | Ý nghĩa |
| --- | --- |
| `backlogId` | Mã item, trùng `backlogItemKey`. |
| `backlogType` | `TASK`, `CR`, `BUG` hoặc `PI`. |
| `startInstruction` | Hướng dẫn kèm lúc start. Có thể rỗng. |
| `submitTemplate` | Object đội điền rồi gửi lại trong `answer` của Submit. `null` nếu đề không có template. |
| `minimumVerificationScenarios` | MVS khi item có file đó, thường với `CR` / `BUG`. Có thể `null` với `TASK` / `PI`. |
| `files` | Map đường dẫn → nội dung thô của các file on-start (template, MVS, …). |
| `replay` | `true` khi đây là lần gọi lại. |

File JSON hợp lệ được parse thành object trong `submitTemplate` và `minimumVerificationScenarios`. File không phải JSON được trả nguyên dạng chuỗi. Submit template và MVS không xuất hiện trên trang `/match`; chỉ có trong response này.

```
{
  "backlogId": "TASK-01",
  "backlogType": "TASK",
  "startInstruction": "",
  "replay": false,
  "submitTemplate": {
    "backlogId": "TASK-01",
    "status": "DRAFT",
    "changeSummary": ""
  },
  "minimumVerificationScenarios": null,
  "files": {
    "challenge/TASK-01-submit.json": "{\n  \"backlogId\": \"TASK-01\",\n  \"status\": \"DRAFT\",\n  \"changeSummary\": \"\"\n}"
  }
}
```

Ví dụ minh hoạ. Template thật lấy từ response của trận đang chơi.

## Submit

POST`/api/v1/matches/{matchId}/submissions`

Nộp bài cho một backlog đã start. HTTP `201` với `status: "ACCEPTED"` chỉ xác nhận Game Master đã nhận bài. `scoreStatus` lúc tạo là `PENDING`. Điểm đến sau, từ Grader.

### Request

```
POST /api/v1/matches/{matchId}/submissions
Authorization: Bearer <TEAM_API_KEY>
Idempotency-Key: 7f3a9c2e-4b18-4d6a-9e01-0a1b2c3d4e5f
Content-Type: application/json

{
  "clientSubmissionId": "11111111-2222-4333-8444-555555555555",
  "backlogItemKey": "TASK-01",
  "gitRevision": "0123456789abcdef0123456789abcdef01234567",
  "answer": {
    "backlogId": "TASK-01",
    "status": "SUBMITTED",
    "changeSummary": "Completed the requested change."
  }
}
```

`Idempotency-Key` bắt buộc, khớp `/^[A-Za-z0-9._~-]{8,128}$/`. Thiếu hoặc sai dạng → `422 VALIDATION_ERROR`.

| Field | Bắt buộc | Ràng buộc |
| --- | --- | --- |
| `clientSubmissionId` | Có | UUID do Digital Cuder tạo. |
| `backlogItemKey` | Có | 1–32 ký tự. Trùng key đã start. |
| `gitRevision` | Có | Full SHA của commit đã push, đặt ở **root** body. Chuỗi được trim, dài 1–128 ký tự. Grader clone đúng giá trị này. |
| `answer` | Có | Object JSON. Copy `submitTemplate` từ Start work, điền, rồi gửi nguyên object đó. Không có schema cố định cho mọi trận. |
| `artifactUrls` | Không | Tối đa 20 URL. Mỗi URL là `https://`, dài tối đa 2048 ký tự. |
| `clientCreatedAt` | Không | ISO-8601 phía client. Chỉ mang tính thông tin, không dùng để xếp hạng. |
| `metadata` | Không | Object bổ sung của client. |

`answer` tối đa khoảng 200 000 ký tự JSON. Cả request mặc định tối đa 524 288 byte. Vượt giới hạn → `413 PAYLOAD_TOO_LARGE`.

> Đặt `gitRevision` ở root. Không gửi SHA chỉ bên trong `answer`.

### Khi nào được nhận

- Đã Start work item này. Chưa start → `409 BACKLOG_NOT_STARTED`.
- Trận `IN_PROGRESS`. Trận `PAUSED` chỉ nhận bài khi cấu hình trận bật cho phép nộp lúc tạm dừng; mặc định `PAUSED` trả `409 MATCH_NOT_ACCEPTING_SUBMISSIONS`.
- Chưa TIME OVER và chưa `FINISHED`. Cả hai trường hợp đó cũng là `409 MATCH_NOT_ACCEPTING_SUBMISSIONS`.
- Item còn `OPEN`. Chưa reveal → `BACKLOG_ITEM_NOT_VISIBLE`. Đã hiện nhưng chưa mở → `BACKLOG_ITEM_NOT_OPEN`. Đã đóng hoặc hết deadline → `BACKLOG_ITEM_CLOSED`.
- Đội chưa có submission đã nhận cho item này.

### Response khi được nhận

Luôn HTTP `201`, kể cả khi replay cùng key và cùng body. Replay thêm header `Idempotent-Replay: true` và trả lại biên nhận đã lưu, không tạo submission mới.

```
{
  "submission": {
    "id": "6f1c2a40-54d8-4a46-b722-4fdc2b307f54",
    "matchId": "8c0e1b22-1111-4222-8333-444444444444",
    "teamId": "22222222-2222-4222-8222-222222222222",
    "backlogItemKey": "TASK-01",
    "backlogRevisionNo": 1,
    "attemptNo": 1,
    "receivedAt": "2026-10-23T01:15:00.000Z",
    "status": "ACCEPTED",
    "scoreStatus": "PENDING"
  }
}
```

| Field | Ý nghĩa |
| --- | --- |
| `id` | UUID submission trên server. |
| `matchId` | Match UUID. |
| `teamId` | UUID đội suy ra từ API key. |
| `backlogRevisionNo` | Revision của backlog lúc nhận bài. |
| `attemptNo` | Số attempt. Mỗi đội một bài đã nhận cho mỗi item. |
| `receivedAt` | Thời điểm server nhận bài (ISO-8601 UTC). Đây là mốc xếp hạng Product Incident, không phải `clientCreatedAt`. |
| `status` | `ACCEPTED` khi tạo thành công. |
| `scoreStatus` | `PENDING` trên biên nhận vừa tạo. Response tạo bài không kèm `scoreVerdict`. |

Submit thành công chuyển spectator của item sang **SUBMITTED**. `429 RATE_LIMITED` có thể kèm header `Retry-After` (giây).

## Retry

| Tình huống | Cách làm | Kết quả |
| --- | --- | --- |
| Mạng timeout, cùng một lần nộp | Giữ nguyên `Idempotency-Key` và nguyên body. | `201` và `Idempotent-Replay: true`. Cùng submission đã lưu. |
| Nộp lần hai hoặc sửa bài | POST mới, key mới. | `409 ALREADY_SUBMITTED`. |
| Giữ key, đổi body |  | `409 IDEMPOTENCY_CONFLICT`. |
| Gọi lại Start work | Cùng `backlogItemKey`. | `200`, `replay: true`, cùng template. |

## Mã lỗi

| HTTP | `error.code` | Khi nào |
| --- | --- | --- |
| `400` | `VALIDATION_ERROR` | Body không phải JSON. |
| `401` | `INVALID_API_KEY` | Thiếu hoặc sai Bearer token. |
| `401` | `API_KEY_REVOKED` | Key đã bị thu hồi. |
| `403` | `API_KEY_SCOPE_FORBIDDEN` | Key không có `submission:create`. |
| `403` | `TEAM_NOT_IN_MATCH` | Đội của key không ở trong trận. |
| `403` | `FORBIDDEN` | Key bị gắn với một trận khác. |
| `404` | `MATCH_NOT_FOUND` | Không có trận với Match UUID này. |
| `404` | `BACKLOG_ITEM_NOT_FOUND` | Không có `backlogItemKey` trong trận. |
| `409` | `MATCH_NOT_IN_PROGRESS` | Start khi trận không ở `IN_PROGRESS` hoặc `PAUSED`. |
| `409` | `MATCH_NOT_ACCEPTING_SUBMISSIONS` | TIME OVER, `FINISHED`, hoặc Submit khi trận không nhận bài (kể cả `PAUSED` mặc định). |
| `409` | `BACKLOG_NOT_STARTED` | Submit khi chưa Start work. |
| `409` | `BACKLOG_ITEM_NOT_VISIBLE` | Item chưa hiện trên `/match`. |
| `409` | `BACKLOG_ITEM_NOT_OPEN` | Item đã hiện nhưng chưa `OPEN`. |
| `409` | `BACKLOG_ITEM_CLOSED` | Item đã đóng hoặc hết deadline. |
| `409` | `ALREADY_SUBMITTED` | Đội đã có bài được nhận cho item này. |
| `409` | `IDEMPOTENCY_CONFLICT` | Cùng `Idempotency-Key`, body khác. |
| `413` | `PAYLOAD_TOO_LARGE` | `answer` hoặc body vượt giới hạn. |
| `422` | `VALIDATION_ERROR` | Schema, UUID, `Idempotency-Key` hoặc `clientCreatedAt` không hợp lệ. |
| `422` | `INVALID_SUBMISSION` | `artifactUrls` không phải HTTPS hoặc quá số lượng. |
| `429` | `RATE_LIMITED` | Submit quá dày. Start work không áp hạn mức này. |

Trang này chỉ mô tả Start work và Submit. Đề, backlog và event đọc trên `/match` bằng session đăng nhập, không bằng API key.
