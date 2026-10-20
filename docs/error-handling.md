# Dead Drop: Error Handling Design

The API contract is [api/openapi.yaml](api/openapi.yaml) (OpenAPI 3.1, 19 operations). To browse it, run `python -m http.server -d docs/api 8000` and open <http://localhost:8000>. This document explains how errors are modeled, how the status is chosen, and how clients should react.

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
| 401 `AUTHENTICATION_REQUIRED` | Call `/auth/refresh` once, then repeat the request; on failure redirect to login. |
| 401 `SESSION_REVOKED` | Clear in-memory token and redirect to login. |
| 403 | Explain why (unverified, suspended, locked) without hinting at content. |
| 404 | Generic "not found or not available to you". |
| 409 | Reload the resource and show its current state (e.g. "already released"). |
| 412 | Reload, show a diff/merge prompt, let the user reapply changes. |
| 429 / 503 | Retry after `Retry-After`, with exponential backoff and jitter, at most 3 times. |
| 500 | Show `traceId` for support; do not retry non-idempotent requests automatically. |

## 8. Server-side implementation notes

* A single DRF exception handler maps domain exceptions (`CapsuleAlreadyReleased`, `ActivationStale`, ...) to Problem Details, so views never build error bodies by hand.
* Each domain exception class owns its `code`, HTTP status, and `title`; the catalog in §4 is generated from those classes in tests to prevent drift from `openapi.yaml`.
* State checks (§3 step 9) run **after** acquiring row locks inside the transaction (see [concurrency.md](concurrency.md)), so the reported 409 reflects the committed order.
* Unhandled exceptions are logged with `traceId` and stack trace; the client receives only `INTERNAL_ERROR`.
