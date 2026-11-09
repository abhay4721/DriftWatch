import json
import subprocess
from pathlib import Path

from driftwatch.drift.adapters.kubernetes import KubernetesAdapter
from driftwatch.drift.models import ScannedResource
from driftwatch.drift.policy import DriftPolicy

POLICY = DriftPolicy(version=1)


def write_deployment(source: Path) -> Path:
    source.mkdir(parents=True)
    manifest = source / "deployment.yaml"
    manifest.write_text(
        """apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
  namespace: production
  labels:
    app: api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: api
  template:
    metadata:
      labels:
        app: api
    spec:
      containers:
        - name: api
          image: example/api:v1
""",
        encoding="utf-8",
    )
    return manifest


def adapter(root: Path) -> KubernetesAdapter:
    return KubernetesAdapter(
        POLICY,
        source_root=root,
        context="docker-desktop",
        kubectl_path="kubectl",
        timeout_seconds=12,
    )


def test_clean_kubernetes_scan_ignores_api_defaults_and_server_fields(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "manifests"
    write_deployment(source)
    actual = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "api",
            "namespace": "production",
            "labels": {"app": "api", "managed-by": "controller"},
            "uid": "server-uid",
            "resourceVersion": "82",
            "managedFields": [{"manager": "kube-controller-manager"}],
        },
        "spec": {
            "replicas": 3,
            "selector": {"matchLabels": {"app": "api"}},
            "template": {
                "metadata": {"labels": {"app": "api"}},
                "spec": {
                    "containers": [
                        {
                            "name": "api",
                            "image": "example/api:v1",
                            "imagePullPolicy": "IfNotPresent",
                        }
                    ]
                },
            },
        },
        "status": {"availableReplicas": 3},
    }
    calls = []

    def fake_run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, 0, json.dumps(actual), "")

    monkeypatch.setattr("driftwatch.drift.adapters.kubernetes.shutil.which", lambda _: "kubectl")
    monkeypatch.setattr("driftwatch.drift.adapters.kubernetes.subprocess.run", fake_run)

    scan = adapter(tmp_path)
    findings = scan.scan(source)

    assert findings == []
    assert scan.resource_count == 1
    assert scan.resources == [
        ScannedResource(
            address="apps/v1/Deployment/production/api",
            resource_type="kubernetes_deployment",
        )
    ]
    arguments, kwargs = calls[0]
    assert arguments[1:4] == ["get", "deployment", "api"]
    assert "--context=docker-desktop" in arguments
    assert "--namespace=production" in arguments
    assert kwargs["shell"] is False


def test_kubernetes_scan_reports_spec_drift_and_redacts_secret_values(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "manifests"
    write_deployment(source)
    actual = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "api", "namespace": "production"},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": "api"}},
            "template": {
                "metadata": {"labels": {"app": "api"}},
                "spec": {"containers": [{"name": "api", "image": "example/api:v2"}]},
            },
        },
    }
    monkeypatch.setattr("driftwatch.drift.adapters.kubernetes.shutil.which", lambda _: "kubectl")
    monkeypatch.setattr(
        "driftwatch.drift.adapters.kubernetes.subprocess.run",
        lambda arguments, **kwargs: subprocess.CompletedProcess(
            arguments, 0, json.dumps(actual), ""
        ),
    )

    findings = adapter(tmp_path).scan(source)

    assert len(findings) == 1
    finding = findings[0]
    changes = {
        change.path: (change.expected, change.actual)
        for change in finding.attribute_changes
    }
    assert changes["spec.replicas"] == (3, 1)
    assert changes["spec.template.spec.containers[0].image"] == (
        "example/api:v1",
        "example/api:v2",
    )


def test_kubernetes_secret_changes_are_redacted(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "manifests"
    source.mkdir()
    (source / "secret.yaml").write_text(
        """apiVersion: v1
kind: Secret
metadata:
  name: credentials
  namespace: production
type: Opaque
stringData:
  password: approved-value
""",
        encoding="utf-8",
    )
    actual = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "credentials", "namespace": "production"},
        "type": "Opaque",
        "data": {"password": "c2VjcmV0"},
    }
    monkeypatch.setattr("driftwatch.drift.adapters.kubernetes.shutil.which", lambda _: "kubectl")
    monkeypatch.setattr(
        "driftwatch.drift.adapters.kubernetes.subprocess.run",
        lambda arguments, **kwargs: subprocess.CompletedProcess(
            arguments, 0, json.dumps(actual), ""
        ),
    )

    findings = adapter(tmp_path).scan(source)

    assert len(findings) == 1
    assert findings[0].severity == "high"
    assert all(change.expected == "[REDACTED]" for change in findings[0].attribute_changes)
    assert all(change.actual == "[REDACTED]" for change in findings[0].attribute_changes)
