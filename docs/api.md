# Phase 3 database and API

DriftWatch persists project configuration and scan history in PostgreSQL. SQLAlchemy models are maintained in `backend/src/driftwatch/db_models.py`; Alembic migrations live in `backend/migrations`. Compose runs `alembic upgrade head` before starting the API. For a local development database, run the migration from the repository root:

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://driftwatch:change-this-local-password@localhost:5432/driftwatch'
$env:PYTHONPATH = (Resolve-Path 'backend\src').Path
python -m alembic -c backend/alembic.ini upgrade head
```

PostgreSQL is private to the Compose network by default, so the repository-root command requires a database reachable at the configured URL. Within Compose, migrations run automatically as part of backend startup.

## Versioned routes

OpenAPI is available at `/docs`; all application endpoints are under `/api/v1`.

| Method | Route | Description |
| --- | --- | --- |
| `POST`, `GET` | `/api/v1/projects` | Create projects and list them with pagination |
| `GET` | `/api/v1/projects/{project_id}` | Read one project |
| `POST`, `GET` | `/api/v1/projects/{project_id}/environments` | Create and list project environments |
| `POST`, `GET` | `/api/v1/environments/{environment_id}/sources` | Create and list infrastructure sources |
| `GET` | `/api/v1/sources/{source_id}` | Read one source |
| `POST` | `/api/v1/sources/{source_id}/scans` | Queue a manual OpenTofu scan; returns `202` and a queued scan |
| `GET` | `/api/v1/sources/{source_id}/resources` | Read inventoried resources |
| `GET` | `/api/v1/sources/{source_id}/scans` | Read source scan history; filter by `status` |
| `GET` | `/api/v1/scans/{scan_id}` | Read one scan |
| `GET` | `/api/v1/scans/{scan_id}/findings` | Read findings recorded for a scan |
| `GET` | `/api/v1/findings` | Filter findings by `source_id`, `status`, or `severity` |
| `GET` | `/api/v1/findings/{finding_id}` | Read finding details and attribute changes |
| `POST`, `GET` | `/api/v1/policies` | Create and list JSON policies |
| `GET` | `/api/v1/audit-logs` | Read audit events; filter by `entity_type` or `entity_id` |

List routes return `{ "items": [...], "total": 0, "limit": 50, "offset": 0 }`. `limit` is capped at 100. API errors use `{ "error": { "code": "...", "message": "..." } }`; validation errors also include safe field locations and types without echoing request values.

The schema includes users, projects, environments, infrastructure sources, resources, scans, drift findings and their attribute changes, policies, notifications, remediation proposals, and audit logs. Manual scan requests are queued and processed asynchronously by the worker. The web process never executes infrastructure commands. The scheduler queues recurring scans for enabled OpenTofu sources based on `DRIFTWATCH_SCHEDULE_INTERVAL_SECONDS` (default 3600 seconds). The current worker supports OpenTofu sources only.

In Compose, source paths are relative to the mounted `demo/` directory. For example, configure the source path as `tofu-local` to scan `demo/tofu-local`. OpenTofu initialization may create `.terraform` files and a provider lock file in that source directory. Set `DRIFTWATCH_SCAN_TIMEOUT_SECONDS`, `DRIFTWATCH_WORKER_POLL_SECONDS`, and `DRIFTWATCH_SCHEDULER_POLL_SECONDS` to adjust worker timing.
