# GG Support Dashboard API

REST API for the GG Support Bot admin dashboard. Implemented with **FastAPI**; the running server also exposes interactive docs:

- **Swagger UI:** `GET /docs`
- **ReDoc:** `GET /redoc`
- **OpenAPI JSON:** `GET /openapi.json`

Base URL is the host where Uvicorn is bound (for example `http://localhost:8000`). All JSON routes below are prefixed as shown unless you mount the app differently.

---

## Authentication

### Login (no token)

| | |
|---|---|
| **POST** | `/api/auth/login` |

**Request body**

```json
{ "password": "string" }
```

The password must match the `DASHBOARD_PASSWORD` environment variable (default in code: `changeme`).

**Response** `200` — body:

```json
{ "token": "string" }
```

JWT (`HS256`), expires after 24 hours. Implementation: [`api/auth.py`](../api/auth.py).

### Authenticated requests

Send the header on every other `/api/*` route (except login):

```http
Authorization: Bearer <token>
Content-Type: application/json
```

Missing or invalid token → **401** (`Invalid token`, `Token expired`, etc.).

---

## Clubs

Prefix: `/api/clubs` — all routes require Bearer auth.

### List clubs

| | |
|---|---|
| **GET** | `/api/clubs` |

**Response** `200` — array of [ClubRead](#clubread).

### Create club

| | |
|---|---|
| **POST** | `/api/clubs` |

**Request body** — [ClubCreate](#clubcreate)

**Responses**

- `201` — [ClubRead](#clubread)
- `409` — Telegram user ID already used as a club primary, or already a linked backup elsewhere

### Get club

| | |
|---|---|
| **GET** | `/api/clubs/{club_id}` |

**Responses**

- `200` — [ClubRead](#clubread)
- `404` — Club not found

### Update club

| | |
|---|---|
| **PUT** | `/api/clubs/{club_id}` |

**Request body** — [ClubUpdate](#clubupdate) (partial updates: only sent fields are applied)

**Responses**

- `200` — [ClubRead](#clubread)
- `404` — Club not found
- `409` — Conflicting `telegram_user_id` (another primary, or linked account on another club). Promoting a linked user to primary removes that link row when valid.

### Delete club

| | |
|---|---|
| **DELETE** | `/api/clubs/{club_id}` |

**Responses**

- `204` — No body
- `404` — Club not found

### List linked Telegram groups

| | |
|---|---|
| **GET** | `/api/clubs/{club_id}/groups` |

**Response** `200` — array of [GroupRead](#groupread)

**Errors:** `404` if club missing.

---

## Linked accounts (backup Telegram users)

| | |
|---|---|
| **GET** | `/api/clubs/{club_id}/linked-accounts` |

**Response** `200` — array of [LinkedAccountRead](#linkedaccountread)

---

| | |
|---|---|
| **POST** | `/api/clubs/{club_id}/linked-accounts` |

**Request body**

```json
{ "telegram_user_id": 123456789 }
```

**Responses**

- `201` — [LinkedAccountRead](#linkedaccountread)
- `400` — ID is already this club’s primary
- `404` — Club not found
- `409` — ID already primary or linked elsewhere

---

| | |
|---|---|
| **DELETE** | `/api/clubs/{club_id}/linked-accounts/{account_id}` |

**Responses**

- `204` — No body
- `404` — Linked row not found for this club

See also [`LINKED_ACCOUNTS.md`](LINKED_ACCOUNTS.md).

---

## Payment methods

Routes require Bearer auth.

### List methods for a club

| | |
|---|---|
| **GET** | `/api/clubs/{club_id}/methods` |

**Query parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `direction` | string | No | If set, filter to `deposit` or `cashout` |

**Response** `200` — array of [MethodRead](#methodread) (includes nested `sub_options` and `tiers` where loaded).

**Errors:** `404` — Club not found.

### Create method

| | |
|---|---|
| **POST** | `/api/clubs/{club_id}/methods` |

**Request body** — [MethodCreate](#methodcreate). `direction` must be `deposit` or `cashout`.

**Responses**

- `201` — [MethodRead](#methodread)
- `400` — Invalid `direction`
- `404` — Club not found

### Get method by ID

| | |
|---|---|
| **GET** | `/api/methods/{method_id}` |

**Response** `200` — [MethodRead](#methodread)

**Errors:** `404` — Method not found

### Update method

| | |
|---|---|
| **PUT** | `/api/methods/{method_id}` |

**Request body** — [MethodUpdate](#methodupdate)

**Response** `200` — [MethodRead](#methodread)

### Delete method

| | |
|---|---|
| **DELETE** | `/api/methods/{method_id}` |

**Response** `204` — No body

### Reorder methods within a club

| | |
|---|---|
| **PUT** | `/api/clubs/{club_id}/methods/reorder` |

**Request body**

```json
{ "order": [1, 2, 3] }
```

`order` is an array of `payment_methods.id` values; list order becomes `sort_order` (0-based index).

**Response** `200` — Empty body (implementation returns no JSON payload).

---

## Sub-options (per payment method)

| | |
|---|---|
| **GET** | `/api/methods/{method_id}/sub-options` |

**Response** `200` — array of [SubOptionRead](#suboptionread), sorted by `sort_order`.

---

| | |
|---|---|
| **POST** | `/api/methods/{method_id}/sub-options` |

**Request body** — [SubOptionCreate](#suboptioncreate)

**Response** `201` — [SubOptionRead](#suboptionread)

---

| | |
|---|---|
| **PUT** | `/api/sub-options/{sub_id}` |

**Request body** — [SubOptionUpdate](#suboptionupdate)

**Response** `200` — [SubOptionRead](#suboptionread)

---

| | |
|---|---|
| **DELETE** | `/api/sub-options/{sub_id}` |

**Response** `204` — No body

---

## Tiers (amount bands per method)

| | |
|---|---|
| **GET** | `/api/methods/{method_id}/tiers` |

**Response** `200` — array of [TierRead](#tierread)

---

| | |
|---|---|
| **POST** | `/api/methods/{method_id}/tiers` |

**Request body** — [TierCreate](#tiercreate)

**Response** `201` — [TierRead](#tierread)

---

| | |
|---|---|
| **PUT** | `/api/tiers/{tier_id}` |

**Request body** — [TierUpdate](#tierupdate)

**Response** `200` — [TierRead](#tierread)

---

| | |
|---|---|
| **DELETE** | `/api/tiers/{tier_id}` |

**Response** `204` — No body

---

## Custom commands

| | |
|---|---|
| **GET** | `/api/clubs/{club_id}/commands` |

**Response** `200` — array of [CommandRead](#commandread)

---

| | |
|---|---|
| **POST** | `/api/clubs/{club_id}/commands` |

**Request body** — [CommandCreate](#commandcreate). `command_name` is stored **lowercased**.

**Response** `201` — [CommandRead](#commandread)

---

| | |
|---|---|
| **PUT** | `/api/commands/{cmd_id}` |

**Request body** — [CommandUpdate](#commandupdate). If `command_name` is sent, it is lowercased.

**Response** `200` — [CommandRead](#commandread)

---

| | |
|---|---|
| **DELETE** | `/api/commands/{cmd_id}` |

**Response** `204` — No body

---

## Flow simulator (read-only preview)

| | |
|---|---|
| **GET** | `/api/clubs/{club_id}/simulate/{direction}` |

`direction` path segment must be `deposit` or `cashout`.

**Response** `200` — [SimulateResponse](#simulateresponse) (active methods only; sub-options filtered to `is_active`).

**Errors:** `400` — bad direction; `404` — club not found

---

## Broadcast

Broadcast sends Telegram messages to every group linked to the club. The **API process** must have `TELEGRAM_BOT_TOKEN` set. Implementation: [`api/routes/broadcast.py`](../api/routes/broadcast.py).

### Start broadcast

| | |
|---|---|
| **POST** | `/api/clubs/{club_id}/broadcast` |

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `response_type` | string | No | Default `"text"`; use `"photo"` with `response_file_id` |
| `response_text` | string \| null | No | Text; multi-part split with `\n---\n` |
| `response_file_id` | string \| null | No | Telegram file id(s); comma-separated for album |
| `response_caption` | string \| null | No | Photo caption |

At least one of `response_text` or (`response_type` === `"photo"` and `response_file_id`) is required.

**Responses**

- `202` — Broadcast job (see below)
- `400` — No linked groups, or no content
- `404` — Club not found
- `409` — A broadcast with `status: running` already exists for this club

**Response body** (job snapshot):

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Job id |
| `club_id` | int | |
| `status` | string | `running`, `done`, `cancelled` |
| `total_groups` | int | |
| `sent` | int | Chats successfully sent |
| `failed` | int | Count of failures (errors list may be shorter) |
| `errors` | string[] | Sample of error strings |
| `created_at` | string \| null | ISO 8601 |
| `finished_at` | string \| null | ISO 8601 |

### Poll job status

| | |
|---|---|
| **GET** | `/api/clubs/{club_id}/broadcast/{job_id}` |

Same shape as the `202` body.

**Errors:** `404` — Job not found for this club

### Cancel running job

| | |
|---|---|
| **POST** | `/api/clubs/{club_id}/broadcast/{job_id}/cancel` |

**Responses**

- `200` — Updated job (status `cancelled` when worker stops)
- `400` — Job not `running`
- `404` — Job not found

Messages already delivered before cancel are not recalled.

---

## Schema reference

Types are defined in [`api/schemas.py`](../api/schemas.py). Decimal fields serialize as strings in JSON.

### LoginRequest

| Field | Type |
|-------|------|
| `password` | string |

### TokenResponse

| Field | Type |
|-------|------|
| `token` | string |

### ClubCreate

| Field | Type | Notes |
|-------|------|--------|
| `name` | string | Required |
| `telegram_user_id` | int | Required; primary owner |
| `welcome_type` | string | Default `"text"` |
| `welcome_text` | string \| null | |
| `welcome_file_id` | string \| null | |
| `welcome_caption` | string \| null | |
| `list_type` | string | Default `"text"` |
| `list_text` | string \| null | |
| `list_file_id` | string \| null | |
| `list_caption` | string \| null | |
| `allow_multi_cashout` | bool | Default `true` |
| `allow_admin_commands` | bool | Default `true` |
| `deposit_simple_mode` | bool | Default `false` |
| `deposit_simple_type` | string | Default `"text"` |
| `deposit_simple_text` | string \| null | |
| `deposit_simple_file_id` | string \| null | |
| `deposit_simple_caption` | string \| null | |
| `cashout_simple_mode` | bool | Default `false` |
| `cashout_simple_type` | string | Default `"text"` |
| `cashout_simple_text` | string \| null | |
| `cashout_simple_file_id` | string \| null | |
| `cashout_simple_caption` | string \| null | |
| `cashout_cooldown_enabled` | bool | Default `false` |
| `cashout_cooldown_hours` | int | Default `24` |
| `cashout_hours_enabled` | bool | Default `false` |
| `cashout_hours_start` | string | Default `"08:00"` |
| `cashout_hours_end` | string | Default `"23:00"` |
| `is_active` | bool | Default `true` |

### ClubUpdate

Same fields as [ClubCreate](#clubcreate) except all optional (partial update).

### ClubRead

All club fields as stored, plus:

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | |
| `created_at` | string (datetime) \| null | |
| `method_count` | int | Number of payment methods |
| `group_count` | int | Linked Telegram groups |
| `linked_account_count` | int | Backup accounts (excludes primary) |

### LinkedAccountRead

| Field | Type |
|-------|------|
| `id` | int |
| `club_id` | int |
| `telegram_user_id` | int |
| `created_at` | string (datetime) \| null |

### GroupRead

| Field | Type |
|-------|------|
| `chat_id` | int |
| `club_id` | int |
| `added_at` | string (datetime) \| null |

### MethodCreate

| Field | Type | Notes |
|-------|------|--------|
| `direction` | string | `deposit` or `cashout` |
| `name` | string | |
| `slug` | string | Unique per club + direction |
| `min_amount` | decimal \| null | |
| `max_amount` | decimal \| null | |
| `has_sub_options` | bool | Default `false` |
| `response_type` | string | Default `"text"` |
| `response_text` | string \| null | |
| `response_file_id` | string \| null | |
| `response_caption` | string \| null | |
| `is_active` | bool | Default `true` |
| `sort_order` | int | Default `0` |

### MethodUpdate

Partial update; all fields optional.

### MethodRead

| Field | Type |
|-------|------|
| `id` | int |
| `club_id` | int |
| `direction` | string |
| `name`, `slug` | string |
| `min_amount`, `max_amount` | decimal \| null |
| `has_sub_options` | bool |
| `response_type`, `response_text`, `response_file_id`, `response_caption` | optional |
| `is_active` | bool |
| `sort_order` | int |
| `created_at` | string (datetime) \| null |
| `sub_options` | [SubOptionRead](#suboptionread)[] |
| `tiers` | [TierRead](#tierread)[] |

### SubOptionCreate / SubOptionUpdate

| Field | Type (Create defaults) |
|-------|-------------------------|
| `name` | string (required on create) |
| `slug` | string (required on create) |
| `response_type` | string (`"text"`) |
| `response_text` | string \| null |
| `response_file_id` | string \| null |
| `response_caption` | string \| null |
| `is_active` | bool (`true`) |
| `sort_order` | int (`0`) |

### SubOptionRead

| Field | Type |
|-------|------|
| `id`, `method_id` | int |
| `name`, `slug` | string |
| `response_type`, `response_text`, `response_file_id`, `response_caption` | optional |
| `is_active` | bool |
| `sort_order` | int |

### TierCreate / TierUpdate

| Field | Type (Create defaults) |
|-------|-------------------------|
| `label` | string (required on create) |
| `min_amount`, `max_amount` | decimal \| null |
| `response_type` | string (`"text"`) |
| `response_text`, `response_file_id`, `response_caption` | optional |
| `sort_order` | int (`0`) |

### TierRead

| Field | Type |
|-------|------|
| `id`, `method_id` | int |
| `label` | string |
| `min_amount`, `max_amount` | decimal \| null |
| `response_type`, `response_text`, `response_file_id`, `response_caption` | optional |
| `sort_order` | int |

### CommandCreate / CommandUpdate

| Field | Type (Create defaults) |
|-------|-------------------------|
| `command_name` | string (required on create) |
| `response_type` | string (`"text"`) |
| `response_text`, `response_file_id`, `response_caption` | optional |
| `customer_visible` | bool (`false`) |
| `is_active` | bool (`true`) |

### CommandRead

| Field | Type |
|-------|------|
| `id`, `club_id` | int |
| `command_name` | string |
| `response_type`, `response_text`, `response_file_id`, `response_caption` | optional |
| `customer_visible`, `is_active` | bool |

### SimulateMethodOut

| Field | Type |
|-------|------|
| `id` | int |
| `name`, `slug` | string |
| `min_amount`, `max_amount` | decimal \| null |
| `has_sub_options` | bool |
| `response_type`, `response_text`, `response_caption` | optional |
| `sub_options` | [SubOptionRead](#suboptionread)[] |

### SimulateResponse

| Field | Type |
|-------|------|
| `club_name` | string |
| `direction` | string |
| `methods` | [SimulateMethodOut](#simulatemethodout)[] |

---

## Environment

| Variable | Used by API |
|----------|-------------|
| `DATABASE_URL` | Required — SQLAlchemy URL |
| `DASHBOARD_PASSWORD` | JWT signing and login password |
| `TELEGRAM_BOT_TOKEN` | Required for **broadcast** sends |

---

## CORS

The app allows all origins for browser access (`allow_origins=["*"]` in [`api/app.py`](../api/app.py)). Tighten for production if the dashboard is hosted on a fixed origin.
