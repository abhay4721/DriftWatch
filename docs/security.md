# Security and deployment hardening

DriftWatch is a self-hosted tool. Review this guide before exposing it beyond a trusted local machine.

## Authentication and roles

- The first administrator is created once through the dashboard's setup form or `POST /api/v1/auth/setup`. The endpoint locks after the first account exists; PostgreSQL advisory locking prevents concurrent initial setup across API replicas.
- Login uses `POST /api/v1/auth/token` and returns a signed HS256 JWT. Tokens expire after 30 minutes by default. Configure `DRIFTWATCH_JWT_TTL_MINUTES` from 5 to 1440 minutes.
- Passwords are stored as salted scrypt hashes. The API returns generic invalid-credential errors, limits login attempts per source IP to 10 per minute, and limits API traffic to 300 requests per source IP per minute.
- Authenticated users are checked against the database on each request, so disabling an account immediately invalidates its tokens. Roles: viewer (read), operator (write operational data), admin (operator access plus user creation).
- Login throttling is process-local. For a multi-replica deployment, configure a shared rate limiter at the reverse proxy or load balancer.

## Secrets and transport

Set a unique `DRIFTWATCH_JWT_SECRET` with at least 32 characters. Generate one with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Store it in a protected environment file or secret manager. Do not commit `.env`, kubeconfig files, GitHub tokens, database passwords, or JWT secrets. `DRIFTWATCH_ENV=production` rejects the built-in local secret and obvious placeholder values. Put the API and dashboard behind HTTPS before sharing access; bearer tokens must not be sent over plaintext HTTP. If TLS terminates at a reverse proxy, configure HSTS at that proxy.

The API adds `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and `Permissions-Policy` response headers. HTTPS requests also receive HSTS. CORS is not enabled by default; keep the dashboard and API same-origin unless a specific trusted origin is required.

## Containers and infrastructure execution

- Backend, worker, and scheduler run as the unprivileged `driftwatch` user.
- PostgreSQL is on an internal Compose network and has no host port published by default. Backend, frontend, Grafana, and Prometheus host ports bind to `127.0.0.1`.
- OpenTofu and kubectl run only in the worker through argument arrays, timeouts, source-root checks, and read-only scan commands. Do not mount sensitive host paths into the worker. Mount kubeconfig read-only at `/home/driftwatch/.kube/config` and grant the Kubernetes identity read-only RBAC.
- Detection never applies infrastructure changes. OpenTofu plan/state data and Kubernetes Secret values are not returned to the browser.
- GitHub tokens are held as secret settings and must use only the repository permissions needed to create issues.

## Production checklist

1. Set `DRIFTWATCH_ENV=production` and unique JWT, database, and Grafana credentials.
2. Terminate TLS at a trusted reverse proxy; configure forwarded headers only for that proxy.
3. Restrict host access to the dashboard, API, and monitoring ports. Keep Prometheus/Grafana private or separately authenticated.
4. Back up PostgreSQL and test restoring it.
5. Keep kubeconfig and source mounts read-only where feasible; ensure OpenTofu source workspaces have only the write access scans require.
6. Apply dependency and image updates, and review logs/metrics for failed scans and repeated authentication failures.

The built-in API and login limits are in-memory and per process, so place a shared rate limiter in front of multi-replica installations. This project does not yet include refresh tokens, password reset, SSO, or fine-grained project-level membership.
