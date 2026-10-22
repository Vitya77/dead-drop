# Dead Drop: Architecture

This document details the architecture allocated in [SRS Chapter 4](srs.md#4-high-level-architecture). Diagrams are written as code in [Mermaid](https://mermaid.js.org/) and render directly on GitHub. The Graphviz sources in [diagrams/](diagrams/) remain the figures embedded into the SRS PDF.

## 1. Architectural style

Dead Drop is a **modular monolith with a single-page frontend and asynchronous workers**, deployed on one Docker Compose host:

* **SPA + BFF-less API.** Next.js serves the responsive UI; the browser calls the Django REST Framework API on the same HTTPS origin through a reverse proxy.
* **Modular backend.** One Django codebase is split into domain modules (identity, capsules, policy, participants, notifications, administration, audit). The API, Celery workers, and Celery Beat run the same image with different commands, so domain rules are shared rather than duplicated.
* **Database as the source of truth.** PostgreSQL holds all authoritative state, including pending work. Redis is only a broker: losing it delays work but never loses releases or notifications.
* **Event-driven background processing.** Release evaluation, email delivery, and cleanup run in workers with at-least-once semantics and idempotent handlers (see [concurrency.md](concurrency.md)).

Microservices and serverless were rejected for the course MVP: the domain requires multi-entity atomic transactions (owner + capsule + approvals + notifications) that a single database makes simple, and a single host matches the accepted availability boundary in SRS §7.4.

## 2. Component view

```mermaid
flowchart TB
    browser["Browser<br/>access token in memory<br/>HttpOnly refresh cookie"]
    smtp["Deployed SMTP"]
    backups[("Off-host encrypted backups")]

    subgraph host["Single Docker Compose host"]
        proxy["Reverse proxy<br/>TLS, one HTTPS origin"]
        next["Next.js<br/>Responsive UI"]
        api["Django REST Framework<br/>identity, domain rules, authorization"]
        db[("PostgreSQL<br/>authoritative state, outbox")]
        redis[("Redis<br/>Celery broker")]
        beat["Celery Beat (exactly one)<br/>periodic scans"]
        worker["Celery workers<br/>evaluation, delivery, cleanup"]
        sink["Demo email sink"]
    end

    browser -->|HTTPS| proxy
    proxy -->|UI| next
    proxy -->|/api/v1| api
    api <-->|atomic commands| db
    api -->|dispatch after commit| redis
    beat -->|scan requests| redis
    redis -->|repeatable tasks| worker
    worker <-->|evaluate, reconcile, commit| db
    worker -->|demo SMTP| sink
    worker -->|generic notices| smtp
    db <-->|backup / restore| backups
```

| Component | Responsibility | Key requirements |
| --- | --- | --- |
| Reverse proxy | TLS termination, routing `/` to Next.js and `/api/v1` to Django, request-size limits, edge rate limiting. | REQ-SEC-003 |
| Next.js | UI screens from SRS §7.1; keeps the access token only in memory; never decides release. | REQ-USE-001, REQ-PORT-001, REQ-SEC-001 |
| Django API | Registration, sessions, capsule commands, policy validation, object-level authorization, Problem Details errors. | REQ-FUNC-001…014, 019, 022, 023, 026…030, REQ-SEC-002 |
| PostgreSQL | Accounts, capsules, activations, grants, approvals, warnings, sessions, notification outbox, audit metadata. | REQ-REL-001, REQ-SEC-004, REQ-COMP-001 |
| Redis | Carries task identifiers only; no message text. | REQ-REL-002 |
| Celery Beat | Single scheduler emitting evaluation and recovery scans every 30 s. | REQ-PERF-002, REQ-REL-002 |
| Celery workers | Condition evaluation, atomic release, outbox relay to SMTP, deletion cleanup. | REQ-FUNC-015…018, 021, 024, 025 |
| SMTP / email sink | Transport for generic notices; no release authority. | REQ-FUNC-024, REQ-FUNC-025 |

## 3. Key flows

### 3.1 Login and refresh-token rotation

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant FE as Next.js (browser)
    participant API as Django API
    participant DB as PostgreSQL

    U->>FE: Submit email + password
    FE->>API: POST /api/v1/auth/login
    API->>DB: Verify credentials, account status
    API->>DB: INSERT session (family_id, expires_at = now + 7 d)
    API-->>FE: 200 {accessToken (15 min)} + Set-Cookie refresh (Secure, HttpOnly, SameSite=Strict)
    FE->>FE: Keep access token in memory only

    Note over FE,API: Access token expires or page reloads
    FE->>API: POST /api/v1/auth/refresh (cookie + X-CSRF-Token)
    API->>DB: SELECT session FOR UPDATE
    alt token current and session not revoked, within 7 days of login
        API->>DB: Rotate refresh token (mark previous as used)
        API-->>FE: 200 {accessToken} + new refresh cookie
    else previously used refresh token (replay)
        API->>DB: Revoke whole session family
        API-->>FE: 401 SESSION_REVOKED
    end
```

### 3.2 Capsule activation

```mermaid
sequenceDiagram
    autonumber
    actor O as Owner
    participant API as Django API
    participant DB as PostgreSQL
    participant R as Redis
    participant W as Worker

    O->>API: POST /api/v1/capsules/{id}/activate (If-Match, Idempotency-Key)
    API->>DB: BEGIN, lock account row, then capsule row
    API->>API: Validate content, policy, future date, recipients/approvers, active-slot limit
    alt validation fails
        API->>DB: ROLLBACK
        API-->>O: 422 / 409 Problem Details
    else valid
        API->>DB: INSERT activation (new id, server UTC timestamp)
        API->>DB: Clear approvals and warnings, set state = ACTIVE
        API->>DB: INSERT invitation + approval-request rows into outbox
        API->>DB: COMMIT
        API-->>O: 200 capsule (state ACTIVE, new ETag)
        API->>R: on_commit: enqueue relay_outbox
        R->>W: relay_outbox
        W->>DB: Read pending outbox rows, send, mark delivered
    end
```

### 3.3 Periodic evaluation and release

```mermaid
sequenceDiagram
    autonumber
    participant B as Celery Beat
    participant R as Redis
    participant W as Worker
    participant DB as PostgreSQL

    loop every 30 s
        B->>R: enqueue evaluate_due_capsules
    end
    R->>W: evaluate_due_capsules
    W->>DB: SELECT ids of ACTIVE capsules with possibly satisfied predicates
    loop each candidate id
        W->>DB: BEGIN, lock owner, then capsule
        W->>W: Recheck owner status, state, ALL/ANY policy with server UTC
        alt still eligible
            W->>DB: state = RELEASED, INSERT release_event (unique capsule_id)
            W->>DB: INSERT notifications + outbox rows
            W->>DB: COMMIT
        else not eligible / already released
            W->>DB: ROLLBACK (no side effects)
        end
    end
    W->>R: enqueue relay_outbox
```

## 4. Deployment summary

The deployment view is in [SRS §4.2](srs.md#42-components-and-deployment). The containers are `proxy`, `frontend`, `api`, `worker` (scalable), `beat` (exactly one replica), `postgres`, `redis`, and `mailpit` (demo email sink). Secrets (SMTP credentials, encryption and signing keys) come from environment variables or Docker secrets and are never baked into images.
