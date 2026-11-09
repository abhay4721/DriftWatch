# Kubernetes drift scans

The optional Kubernetes adapter reads YAML, JSON, and multi-document YAML manifests below the configured source directory. It requires an explicit kube context in the source configuration and invokes only `kubectl get`. It does not apply, patch, delete, or execute workloads. It compares fields declared by the manifest and ignores API-managed defaults, status, and server-managed metadata. Sensitive data in Kubernetes `Secret` objects and token-like fields is redacted before persistence.

## Connect a cluster to the Compose worker

The worker image includes `kubectl`, but it needs a kubeconfig mounted into the container. Create a local `docker-compose.override.yml` (ignored by Git) with a path to your kubeconfig:

```yaml
services:
  worker:
    volumes:
      - C:/Users/your-user/.kube/config:/home/driftwatch/.kube/config:ro
```

Replace the host path with your kubeconfig location, then rebuild the backend and worker:

```powershell
docker compose up -d --build backend worker
```

Make sure the context's API server address and any referenced certificate files are reachable from inside the container. Check access with `docker compose exec worker kubectl --context YOUR_CONTEXT get namespaces`. Select a kubectl version within one minor release of your cluster; override the `KUBECTL_VERSION` build argument in `.env` when needed.

## Configure a source

Put manifests below the repository's `demo/` directory (or change the worker source-root mount). Create a source using `POST /api/v1/environments/{environment_id}/sources` with a relative path and explicit context:

```json
{
  "name": "staging-cluster",
  "provider": "kubernetes",
  "path": "kubernetes/staging",
  "enabled": true,
  "configuration": {
    "context": "staging-readonly",
    "namespace": "staging"
  }
}
```

The optional `namespace` applies only to manifests that omit `metadata.namespace`. Grant the kube identity read-only access to the resources being scanned. Queue a scan with `POST /api/v1/sources/{source_id}/scans`; the worker stores inventory, findings, and scan history through the shared persistence path. The scheduler also queues periodic scans for enabled Kubernetes sources.

`kubectl` is called with argument arrays and a timeout; command output is not returned in API errors. Keep kubeconfig credentials out of source manifests and Git.
