# Dead Drop: Error Handling Design

The API contract is [api/openapi.yaml](api/openapi.yaml) (OpenAPI 3.1, 19 operations). To browse it, run `python -m http.server -d docs/api 8000` and open <http://localhost:8000>. This document explains how errors are modeled, how the status is chosen, and how clients should react. [Section 9](#9-request-error-and-client-examples-for-10-api-operations) gives complete request/error examples for 10 operations.

## 1. Principles

1. **One error format.** Every non-2xx response is `application/problem+json` per [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457). Clients branch on the stable `code`, never on `title` or `detail` text.
2. **Fail closed.** If authorization, state, or the database result is uncertain, the request is rejected. An error never grants access or releases content (SRS §4.5).
3. **No content leakage.** Error bodies, logs, and audit records never contain message text, tokens, or passwords (REQ-SEC-004, REQ-OBS-001).
4. **Anti-enumeration.** A capsule the caller has no relationship to returns `404`, not `403`. Registration and password reset always return `202`. Login returns one generic `INVALID_CREDENTIALS`.
5. **Validate everything on the server.** Frontend validation is only a convenience. Limits from SRS §2.2 are enforced in the API, including during concurrent requests (REQ-FUNC-006).
6. **Traceability.** Every problem carries a `traceId`, which is also written to the structured log and the audit metadata of the request.

## 2. Problem Details schema

```json
{
  "type": "https://deaddrop.example/errors/validation-failed",
  "title": "Validation failed",
  "status": 422,
  "code": "RELEASE_DATE_IN_PAST",
  "detail": "Fix the highlighted fields and try again.",
  "instance": "/api/v1/capsules/0b9f0e1c-5d1a-4c2e-9a77-2f7f6a0a1b2c/activate",
  "traceId": "b3c4d5e6f708192a",
  "errors": [
    { "field": "/policy/releaseAt", "code": "MUST_BE_FUTURE", "message": "The release date must be in the future and within five years." }
  ]
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `type` | yes | Documentation URI, one per code family. |
| `title` | yes | Constant human summary of the code. |
| `status` | yes | Mirrors the HTTP status. |
| `code` | yes | Stable machine-readable code from the catalog below. |
| `detail` | no | Occurrence-specific, safe explanation. |
| `instance` | no | Request path. |
| `traceId` | yes | Correlation id for logs. |
| `errors[]` | 400/422 | Field-level problems: JSON Pointer `field`, sub-`code`, `message`. |
| `retryAfter` | 429/503 | Seconds to wait; duplicates the `Retry-After` header. |

## 3. Status selection rules

The checks run in a fixed order and the first failing check determines the status:

| Order | Check | Status | Typical codes |
| --- | --- | --- | --- |
| 1 | Rate limit (proxy and API, per IP and per account) | 429 | `RATE_LIMITED` |
| 2 | Parsing: JSON, types, unknown fields, required headers | 400 | `MALFORMED_REQUEST`, `VALIDATION_FAILED` |
| 3 | Authentication: token present, valid, session not revoked | 401 | `AUTHENTICATION_REQUIRED`, `SESSION_REVOKED`, `INVALID_CREDENTIALS` |
| 4 | CSRF/Origin for cookie-authenticated calls | 403 | `CSRF_FAILED` |
| 5 | Resource exists **and** the caller is related to it | 404 | `RESOURCE_NOT_FOUND` |
| 6 | Role or account state allows the action | 403 | `EMAIL_NOT_VERIFIED`, `ACCOUNT_SUSPENDED`, `NOT_DESIGNATED_APPROVER`, `CAPSULE_LOCKED`, `ACCESS_REMOVED`, `ADMIN_REQUIRED` |
| 7 | `If-Match` present / matches the current version | 428 / 412 | `PRECONDITION_REQUIRED`, `VERSION_MISMATCH` |
| 8 | Business rules on input values | 422 | `MESSAGE_TOO_LONG`, `RELEASE_DATE_IN_PAST`, `POLICY_INCOMPLETE`, `OWNER_CANNOT_APPROVE`, `WEAK_PASSWORD`, `TOKEN_EXPIRED` |
| 9 | Current resource state (checked after taking row locks) | 409 | `CAPSULE_ALREADY_RELEASED`, `CAPSULE_NOT_ACTIVE`, `ACTIVATION_STALE`, `ACTIVE_CAPSULE_LIMIT`, `IDEMPOTENCY_KEY_REUSED` |
| — | Unexpected failure / database or broker unavailable | 500 / 503 | `INTERNAL_ERROR`, `SERVICE_UNAVAILABLE` |

**422 vs 409.** A 422 means "this request would never be valid as sent" (input problem). A 409 means "this request was valid but the resource state does not allow it now" (state problem). For example, a past `releaseAt` returns 422, while activating an already released capsule returns 409.

**409 vs 412.** A 412 means the client's copy is stale (lost-update protection). A 409 means a domain rule forbids the transition regardless of the version.

## 4. Error catalog

| Code | HTTP | When | Requirement |
| --- | --- | --- | --- |
| `MALFORMED_REQUEST` | 400 | Invalid JSON, wrong type, unknown field, bad UUID. | REQ-SEC-003 |
| `VALIDATION_FAILED` | 400 | Schema-level constraint (length, format) violated. | REQ-FUNC-006 |
| `AUTHENTICATION_REQUIRED` | 401 | Missing or expired access token. | REQ-SEC-001 |
| `INVALID_CREDENTIALS` | 401 | Wrong email or password (generic). | REQ-FUNC-003, REQ-SEC-005 |
| `SESSION_REVOKED` | 401 | Logout, password reset, suspension, or refresh-token replay. | REQ-SEC-001 |
| `CSRF_FAILED` | 403 | Missing/invalid CSRF token or foreign Origin. | REQ-SEC-003 |
| `EMAIL_NOT_VERIFIED` | 403 | Unverified account tries to create, activate, approve, or read. | REQ-FUNC-002 |
| `ACCOUNT_SUSPENDED` | 403 | Suspended account tries any participation. | REQ-FUNC-027 |
| `ADMIN_REQUIRED` | 403 | Non-administrator calls `/admin/*`. | REQ-FUNC-026 |
| `NOT_DESIGNATED_APPROVER` | 403 | Approval link opened by another identity. | REQ-FUNC-016 |
| `CAPSULE_LOCKED` | 403 | Recipient opens a not-yet-released capsule (no preview). | REQ-FUNC-022 |
| `ACCESS_REMOVED` | 403 | Recipient removed their own access. | REQ-FUNC-023 |
| `RESOURCE_NOT_FOUND` | 404 | Unknown id or unrelated caller. | REQ-SEC-002 |
| `PRECONDITION_REQUIRED` | 428 | `If-Match` missing on edit/activate. | REQ-FUNC-012 |
| `VERSION_MISMATCH` | 412 | `If-Match` does not equal current ETag. | REQ-FUNC-012 |
| `WEAK_PASSWORD` | 422 | Password fails the strength/breach policy. | REQ-SEC-005 |
| `TOKEN_EXPIRED` | 422 | Verification or reset token expired/invalid. | REQ-FUNC-002, REQ-FUNC-004 |
| `MESSAGE_TOO_LONG` | 422 | Message over 20,000 code points. | REQ-FUNC-006 |
| `RELEASE_DATE_IN_PAST` | 422 | Date not in the future or beyond five years. | REQ-FUNC-015 |
| `POLICY_INCOMPLETE` | 422 | No conditions, no recipients, or missing condition parameters. | REQ-FUNC-010 |
| `OWNER_CANNOT_APPROVE` | 422 | Owner listed as approver. | REQ-FUNC-009 |
| `EMAIL_ALREADY_VERIFIED` | 409 | Verification repeated. | REQ-FUNC-002 |
| `CAPSULE_ALREADY_RELEASED` | 409 | Edit/pause/activate after release committed. | REQ-FUNC-012, REQ-FUNC-013, REQ-FUNC-021 |
| `CAPSULE_ALREADY_ACTIVE` | 409 | Activate an active capsule. | REQ-FUNC-011 |
| `CAPSULE_NOT_ACTIVE` | 409 | Pause or approve a non-active capsule. | REQ-FUNC-013, REQ-FUNC-016 |
| `ACTIVATION_STALE` | 409 | Approval for an earlier activation. | REQ-FUNC-016 |
| `ACTIVE_CAPSULE_LIMIT` | 409 | Owner already has 20 active capsules. | REQ-FUNC-006 |
| `IDEMPOTENCY_KEY_REUSED` | 409 | Same key, different payload. | REQ-REL-002 |
| `ACCOUNT_NOT_SUSPENDED` | 409 | Restore a non-suspended account. | REQ-FUNC-027 |
| `RATE_LIMITED` | 429 | Limit exceeded (see §5). | REQ-SEC-005 |
| `SERVICE_UNAVAILABLE` | 503 | Database unavailable or maintenance/restore reconciliation. | REQ-REL-001 |
| `INTERNAL_ERROR` | 500 | Unhandled exception (details only in logs). | REQ-OBS-001 |

## 5. Rate limiting (429)

| Scope | Limit | Key |
| --- | --- | --- |
| `POST /auth/login` | 5 per minute, 20 per hour | IP + canonical email |
| `POST /auth/register`, `/auth/password-reset`, `/auth/verify-email` | 3 per 15 minutes | IP + canonical email |
| `POST /auth/refresh` | 30 per minute | session |
| Other authenticated API | 120 per minute | account |

Responses carry `Retry-After`, `RateLimit-Limit`, `RateLimit-Remaining`, and `RateLimit-Reset`. The proxy applies a coarse per-IP limit; Django applies the per-account and per-email limits backed by Redis. If Redis is unavailable, the auth limits fail closed (`503`) and the general limits fail open, so that a broker outage cannot lock users out of reading released content.

## 6. Idempotency and safe retries

* `DELETE`, `PUT /admin/.../suspension`, approval, logout, and check-in are idempotent by design: repeating them returns the same success.
* `POST /capsules` and `POST /capsules/{id}/activate` accept an `Idempotency-Key`. The server stores `(account, key) → request hash, response` for 24 hours. The same key with the same body returns the stored response; a different body returns `409 IDEMPOTENCY_KEY_REUSED`.
* State-changing edits require `If-Match`, so a blind retry after a timeout cannot overwrite newer changes.

## 7. Client behavior

| Status | Frontend reaction |
| --- | --- |
| 400 / 422 | Show `errors[]` next to fields; do not retry. |
| 401 `INVALID_CREDENTIALS` | Keep the login form open and show a generic credential error; do not call refresh. |
| 401 `AUTHENTICATION_REQUIRED` | Share one `/auth/refresh` call among concurrent failed requests, then repeat each original request once; on refresh failure redirect to login. Never refresh recursively. |
| 401 `SESSION_REVOKED` | Clear in-memory token and redirect to login. |
| 403 | Explain why (unverified, suspended, locked) without hinting at content. |
| 404 | Generic "not found or not available to you". |
| 409 | Reconcile according to `code`: reload accessible state, reopen the current approval request, or resolve an idempotency-key conflict. Never blindly repeat a state transition. |
| 412 | Reload, show a diff/merge prompt, let the user reapply changes. |
| 428 | Read the resource to obtain its ETag; preserve local edits and ask the user to confirm before resubmitting with `If-Match`. |
| 429 / 503 | Honor `Retry-After`; use exponential backoff and jitter, at most 3 retries. Automatically retry only safe/idempotent operations or supported POSTs with their original `Idempotency-Key` and unchanged payload. Do not automatically replay credentials or rotating refresh tokens. |
| 500 | Show `traceId` for support; do not retry non-idempotent requests automatically. |

## 8. Server-side implementation notes

* A single DRF exception handler maps domain exceptions (`CapsuleAlreadyReleased`, `ActivationStale`, ...) to Problem Details, so views never build error bodies by hand.
* Each domain exception class owns its `code`, HTTP status, and `title`; the catalog in §4 is generated from those classes in tests to prevent drift from `openapi.yaml`.
* State checks (§3 step 9) run **after** acquiring row locks inside the transaction (see [concurrency.md](concurrency.md)), so the reported 409 reflects the committed order.
* Unhandled exceptions are logged with `traceId` and stack trace; the client receives only `INTERNAL_ERROR`.

## 9. Request, error, and client examples for 10 API operations

These are **designed contract examples**, not responses captured from a running backend. All identities, tokens, UUIDs, and trace IDs are illustrative. Requests use the HTTPS server from OpenAPI; `Host`, bearer-token placeholders, and cookie placeholders stand for values supplied by the browser. Transport-generated headers such as `Content-Length` are omitted.

Each example assumes earlier checks in §3 pass, unless that check is the demonstrated failure. For date-sensitive scenarios, the server time is **2026-11-24T12:00:00Z**. The API's English `title`, `detail`, and field messages describe the failure; the UI uses localized copy selected by `code` and field sub-codes. It must render any fallback text as plain text.

| # | Operation | Demonstrated failure | Main client action |
| --- | --- | --- | --- |
| 1 | `POST /auth/register` | 422 `WEAK_PASSWORD` | Correct the password. |
| 2 | `POST /auth/verify-email` | 422 `TOKEN_EXPIRED` | Request a replacement link. |
| 3 | `POST /auth/login` | 401 `INVALID_CREDENTIALS`; 429 `RATE_LIMITED` | Correct credentials or wait. |
| 4 | `POST /auth/refresh` | 401 `SESSION_REVOKED` | End the local session and sign in again. |
| 5 | `POST /capsules` | 403 `EMAIL_NOT_VERIFIED` | Complete email verification before retrying. |
| 6 | `GET /capsules/{capsuleId}` | 404 `RESOURCE_NOT_FOUND` | Show a generic unavailable state. |
| 7 | `PATCH /capsules/{capsuleId}` | 412 `VERSION_MISMATCH` | Reconcile edits with the latest version. |
| 8 | `POST /capsules/{capsuleId}/activate` | 422 `RELEASE_DATE_IN_PAST` | Save a corrected policy, then activate. |
| 9 | `POST /approvals/{approvalId}` | 409 `ACTIVATION_STALE` | Review the latest approval request. |
| 10 | `GET /received/{capsuleId}/content` | 403 `CAPSULE_LOCKED` | Show the locked state without a preview. |

### 9.1 Register an account: rejected password

**Precondition.** The example password passes the 12-character schema minimum but is assumed to be on the server's breached-password list. A shorter password would fail schema validation first with 400, rather than this business-rule error. Success would be 202 without disclosing whether the email already exists.

**Request**

```http
POST /api/v1/auth/register HTTP/1.1
Host: deaddrop.example
Content-Type: application/json
Accept: application/problem+json

{
  "email": "olena@example.com",
  "password": "Password123456!",
  "displayName": "Olena"
}
```

**Error response**

```http
HTTP/1.1 422 Unprocessable Content
Content-Type: application/problem+json

{
  "type": "https://deaddrop.example/errors/validation-failed",
  "title": "Validation failed",
  "status": 422,
  "code": "WEAK_PASSWORD",
  "detail": "The password does not meet the strength policy. Choose a password not found in breached-password lists.",
  "instance": "/api/v1/auth/register",
  "traceId": "1a2b3c4d5e6f7a8b",
  "errors": [
    {
      "field": "/password",
      "code": "TOO_COMMON",
      "message": "The password appears in a list of breached passwords."
    }
  ]
}
```

**Client reaction.** Keep the form open, associate `/password` with the password field, and explain that a different password is required. Retain non-secret form values, do not log or persist the password, and let the user resubmit after correction. Do not automatically retry or infer account existence. The failed validation creates no partial account.

### 9.2 Verify email: expired or invalid link

**Precondition.** The token has valid syntax and length but is expired or invalid. Success would be 204 with no body.

**Request**

```http
POST /api/v1/auth/verify-email HTTP/1.1
Host: deaddrop.example
Content-Type: application/json
Accept: application/problem+json

{
  "token": "0123456789abcdef0123456789abcdef"
}
```

**Error response**

```http
HTTP/1.1 422 Unprocessable Content
Content-Type: application/problem+json

{
  "type": "https://deaddrop.example/errors/token-expired",
  "title": "Token expired or invalid",
  "status": 422,
  "code": "TOKEN_EXPIRED",
  "detail": "This verification link is expired or invalid. Request a new verification email.",
  "instance": "/api/v1/auth/verify-email",
  "traceId": "0f1e2d3c4b5a6978",
  "errors": [
    {
      "field": "/token",
      "code": "TOKEN_EXPIRED",
      "message": "This token cannot verify the account."
    }
  ]
}
```

**Client reaction.** Show a link-level error rather than a success screen. Offer a replacement verification email, and never retry the same token or mark the account verified locally. The replacement-link flow is required by REQ-FUNC-002, but its endpoint is not yet defined in the current OpenAPI contract; the client integration needs that contract before the action can work. Do not substitute password reset for verification. Handle `409 EMAIL_ALREADY_VERIFIED` separately by inviting the user to sign in.

### 9.3 Log in: invalid credentials and throttling

**Precondition.** The request is well formed, but the credentials do not authenticate. Success would be 200 with an access token and an HttpOnly refresh cookie.

**Request**

```http
POST /api/v1/auth/login HTTP/1.1
Host: deaddrop.example
Content-Type: application/json
Accept: application/json, application/problem+json

{
  "email": "olena@example.com",
  "password": "IncorrectPassphrase!2026"
}
```

**Error response**

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/problem+json

{
  "type": "https://deaddrop.example/errors/invalid-credentials",
  "title": "Invalid email or password",
  "status": 401,
  "code": "INVALID_CREDENTIALS",
  "detail": "Sign-in failed. Check your email and password and try again.",
  "instance": "/api/v1/auth/login",
  "traceId": "7d6c5b4a39281706"
}
```

**Client reaction.** Keep the login screen open and show one form-level message: “Invalid email or password.” Do not identify which credential failed or whether the account exists. Do not run the access-token refresh interceptor for this code; only a new user submission should repeat login.

**Rate-limit variant.** If the same request exceeds the login limit, the rate-limit check runs first and the response is 429 instead of 401. For example, after consuming the five-attempt minute window:

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/problem+json
Retry-After: 30
RateLimit-Limit: 5
RateLimit-Remaining: 0
RateLimit-Reset: 30

{
  "type": "https://deaddrop.example/errors/rate-limited",
  "title": "Too many requests",
  "status": 429,
  "code": "RATE_LIMITED",
  "detail": "Too many sign-in attempts. Try again in 30 seconds.",
  "instance": "/api/v1/auth/login",
  "traceId": "d5e6f708192a3b4c",
  "retryAfter": 30
}
```

**Client reaction to 429.** Disable submission until at least `Retry-After` seconds have elapsed and display a countdown. Do not resend the password automatically when the countdown ends. The user submits again; a longer hourly limit may still apply. Keep the message generic regardless of account existence.

### 9.4 Refresh a session: revoked token family

**Precondition.** Origin and CSRF checks pass, but the refresh session was revoked, for example after logout, a password reset, or replay of a rotated token. Success would be 200 with a new access token and a rotated refresh cookie.

**Request**

```http
POST /api/v1/auth/refresh HTTP/1.1
Host: deaddrop.example
Origin: https://deaddrop.example
Cookie: refresh_token=<revoked-refresh-token>
X-CSRF-Token: <valid-csrf-token>
Accept: application/json, application/problem+json
```

There is no request body. The browser attaches the HttpOnly cookie; JavaScript does not read its value.

**Error response**

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/problem+json

{
  "type": "https://deaddrop.example/errors/session-revoked",
  "title": "Session revoked",
  "status": 401,
  "code": "SESSION_REVOKED",
  "detail": "This session can no longer issue access tokens. Sign in again.",
  "instance": "/api/v1/auth/refresh",
  "traceId": "6a5b4c3d2e1f0a9b"
}
```

**Client reaction.** Clear the in-memory access token and account-specific cached content, stop queued protected requests, and redirect to sign-in with “Your session has ended.” Do not call refresh again or replay the revoked cookie. JavaScript cannot delete an HttpOnly cookie; clearing local authentication must not depend on doing so. Preserve only a safe internal return path. If the code is `403 CSRF_FAILED`, stop the refresh flow and re-establish the page/session context without removing the CSRF or Origin protections.

### 9.5 Create a draft: email not verified

**Precondition.** The access token is valid and the account is active, but its email is not verified. The payload is otherwise valid. Success would be 201 with the draft, `Location`, and `ETag`.

**Request**

```http
POST /api/v1/capsules HTTP/1.1
Host: deaddrop.example
Authorization: Bearer <access-token>
Content-Type: application/json
Accept: application/json, application/problem+json
Idempotency-Key: 11111111-1111-4111-8111-111111111111

{
  "title": "For the future",
  "message": "A message to read later.",
  "recipients": ["taras@example.com"],
  "approvers": [],
  "policy": {
    "operator": "ALL",
    "conditions": ["DATE"],
    "releaseAt": "2027-01-01T10:00:00Z"
  }
}
```

**Error response**

```http
HTTP/1.1 403 Forbidden
Content-Type: application/problem+json

{
  "type": "https://deaddrop.example/errors/email-not-verified",
  "title": "Email not verified",
  "status": 403,
  "code": "EMAIL_NOT_VERIFIED",
  "detail": "Verify your email before creating or activating capsules. No draft was created.",
  "instance": "/api/v1/capsules",
  "traceId": "3d4e5f6a7b8c9d0e"
}
```

**Client reaction.** Keep the unsaved form in the current view, show the verification step, and do not add a draft to the dashboard. Do not persist message text in logs or analytics. After verification, let the user explicitly submit again. For an ambiguous timeout or temporary failure of a creation attempt, reuse the original key and identical payload within the 24-hour window; never generate a new key merely to retry. If the payload changes, that is a new operation with a new key.

### 9.6 Get an owned capsule: missing or inaccessible resource

**Precondition.** The UUID is syntactically valid, but no capsule is available to this caller through the owner endpoint. Success would be 200 with the capsule and its `ETag`.

**Request**

```http
GET /api/v1/capsules/0b9f0e1c-5d1a-4c2e-9a77-2f7f6a0a1b2c HTTP/1.1
Host: deaddrop.example
Authorization: Bearer <access-token>
Accept: application/json, application/problem+json
```

**Error response**

```http
HTTP/1.1 404 Not Found
Content-Type: application/problem+json

{
  "type": "https://deaddrop.example/errors/resource-not-found",
  "title": "Resource not found",
  "status": 404,
  "code": "RESOURCE_NOT_FOUND",
  "detail": "The requested capsule was not found or is not available to you.",
  "instance": "/api/v1/capsules/0b9f0e1c-5d1a-4c2e-9a77-2f7f6a0a1b2c",
  "traceId": "9d0e1f2a3b4c5d6e"
}
```

**Client reaction.** Replace the loading view with a generic unavailable screen and a link back to the dashboard. Stop displaying any previously cached content for that route. Do not say whether the ID exists, who owns it, or whether it was deleted; do not probe other endpoints to distinguish these cases. A recipient uses the received-content endpoint through their normal dashboard flow, not this owner endpoint as an access workaround.

### 9.7 Edit a capsule: concurrent modification

**Precondition.** The owner previously read version 7, but another tab has saved version 8. The submitted `If-Match` is stale. Success would be 200 with the updated capsule and a new `ETag`; editing an ACTIVE capsule would pause it.

**Request**

```http
PATCH /api/v1/capsules/0b9f0e1c-5d1a-4c2e-9a77-2f7f6a0a1b2c HTTP/1.1
Host: deaddrop.example
Authorization: Bearer <access-token>
Content-Type: application/merge-patch+json
Accept: application/json, application/problem+json
If-Match: W/"capsule-7"

{
  "title": "Updated title"
}
```

**Error response**

```http
HTTP/1.1 412 Precondition Failed
Content-Type: application/problem+json

{
  "type": "https://deaddrop.example/errors/version-mismatch",
  "title": "Capsule was modified concurrently",
  "status": 412,
  "code": "VERSION_MISMATCH",
  "detail": "The submitted If-Match does not match the current capsule version. Reload the capsule and reapply your changes.",
  "instance": "/api/v1/capsules/0b9f0e1c-5d1a-4c2e-9a77-2f7f6a0a1b2c",
  "traceId": "8091a2b3c4d5e6f7"
}
```

**Client reaction.** Keep the unsaved edit in memory, read the latest capsule with `GET /capsules/{capsuleId}`, and compare it with the user's edit. Let the user keep the server version or explicitly reapply their changes using the newly returned `ETag`. Do not overwrite automatically or simply replace the header and replay. If the latest state is RELEASED, show read-only state and explain that editing is unavailable. A missing `If-Match` produces `428 PRECONDITION_REQUIRED` instead; obtain an ETag before offering resubmission.

### 9.8 Activate a capsule: release date is in the past

**Precondition.** The owner has a valid DRAFT capsule and a current ETag. Its saved policy selects DATE with `releaseAt = 2026-11-23T10:00:00Z`, earlier than the example server time. Other required fields are complete. Success would be 200 with state ACTIVE, a new activation ID, and an `ETag`.

**Request**

```http
POST /api/v1/capsules/0b9f0e1c-5d1a-4c2e-9a77-2f7f6a0a1b2c/activate HTTP/1.1
Host: deaddrop.example
Authorization: Bearer <access-token>
Accept: application/json, application/problem+json
If-Match: W/"capsule-8"
Idempotency-Key: 22222222-2222-4222-8222-222222222222
```

Activation has **no request body**: it validates the already saved capsule, including its policy.

**Error response**

```http
HTTP/1.1 422 Unprocessable Content
Content-Type: application/problem+json

{
  "type": "https://deaddrop.example/errors/validation-failed",
  "title": "Validation failed",
  "status": 422,
  "code": "RELEASE_DATE_IN_PAST",
  "detail": "The saved release date is earlier than the server's current time. Save a future date before activating the capsule.",
  "instance": "/api/v1/capsules/0b9f0e1c-5d1a-4c2e-9a77-2f7f6a0a1b2c/activate",
  "traceId": "b3c4d5e6f708192a",
  "errors": [
    {
      "field": "/policy/releaseAt",
      "code": "MUST_BE_FUTURE",
      "message": "The release date must be in the future and within five years."
    }
  ]
}
```

**Client reaction.** Highlight the policy date in the capsule editor even though activation has no body. Show the user's time zone and require a future date within five years. Save the corrected policy with PATCH and the current ETag, then obtain confirmation and activate using the PATCH response's ETag and a new idempotency key because the capsule version changed. Do not claim activation succeeded or invitations were sent. For an ambiguous retry of the original activation, preserve its original key and ETag; do not silently create another activation attempt.

### 9.9 Approve release: an obsolete activation

**Precondition.** The caller is the verified designated approver. The capsule is ACTIVE, but its owner edited and reactivated it after this approval request was issued. The submitted activation ID therefore refers to an older cycle. Success would be 200 with the recorded approval; repeating that same approval would not add weight.

**Request**

```http
POST /api/v1/approvals/33333333-3333-4333-8333-333333333333 HTTP/1.1
Host: deaddrop.example
Authorization: Bearer <approver-access-token>
Content-Type: application/json
Accept: application/json, application/problem+json

{
  "activationId": "44444444-4444-4444-8444-444444444444"
}
```

**Error response**

```http
HTTP/1.1 409 Conflict
Content-Type: application/problem+json

{
  "type": "https://deaddrop.example/errors/activation-stale",
  "title": "Approval request is outdated",
  "status": 409,
  "code": "ACTIVATION_STALE",
  "detail": "The capsule was changed and reactivated. This approval request belongs to an earlier activation and cannot approve the current one.",
  "instance": "/api/v1/approvals/33333333-3333-4333-8333-333333333333",
  "traceId": "5d6e7f8091a2b3c4"
}
```

**Client reaction.** Mark the opened request as obsolete and do not show approval success. Ask the user to open the latest request from their dashboard or notification, review it, and confirm again. Use the new assignment's `approvalId` and `activationId` only after that confirmation; never change IDs and resubmit automatically. This failure records no approval for the new cycle and grants no content access. If no current request is available, stay on the obsolete-request screen rather than inventing a replacement API call.

### 9.10 Read received content: capsule remains locked

**Precondition.** The caller is a verified recipient with an existing access grant, but the capsule has not reached RELEASED. An unrelated caller would receive 404 instead. Success would be 200 with released content and `Cache-Control: no-store`.

**Request**

```http
GET /api/v1/received/0b9f0e1c-5d1a-4c2e-9a77-2f7f6a0a1b2c/content HTTP/1.1
Host: deaddrop.example
Authorization: Bearer <recipient-access-token>
Accept: application/json, application/problem+json
```

**Error response**

```http
HTTP/1.1 403 Forbidden
Content-Type: application/problem+json

{
  "type": "https://deaddrop.example/errors/capsule-locked",
  "title": "Capsule is still locked",
  "status": 403,
  "code": "CAPSULE_LOCKED",
  "detail": "Content becomes available when the owner's release conditions are met. No content is included in this response.",
  "instance": "/api/v1/received/0b9f0e1c-5d1a-4c2e-9a77-2f7f6a0a1b2c/content",
  "traceId": "3b4c5d6e7f8091a2"
}
```

**Client reaction.** Show a locked state without message text, a preview, or hidden content in the DOM. Wait for a release notification or explicit user refresh instead of repeatedly requesting content. Do not unlock based on the browser clock: the server decides when release is committed. For `403 ACCESS_REMOVED`, show the removed-access state and clear any content held in memory. For 404, use the generic unavailable screen from §9.6.

### Shared handling after these examples

* An error body is diagnostic, not a state update. Do not show a successful save, activation, approval, or read before a successful response or an explicit reconciliation confirms it.
* For an unknown `code`, malformed/non-JSON error body, or proxy error, fall back to the HTTP status and a generic localized message. Do not expose raw HTML or assume that `errors[]` exists. Distinguish a network timeout, which has no HTTP response, from a server rejection.
* For `503 SERVICE_UNAVAILABLE`, keep the current form/state and honor `Retry-After` if present. With no header, use bounded exponential backoff and jitter. For 500, show a generic failure and the safe `traceId` if available; stack traces and database details belong only in server logs. Apply the retry restrictions in §7, including after timeouts when a write may already have committed.
* A support report may include method, route, HTTP status, stable `code`, and `traceId`. Never attach passwords, tokens, cookies, whole request bodies, or capsule message text. Field-level detail is intentionally absent from authorization errors where it would disclose protected information.
