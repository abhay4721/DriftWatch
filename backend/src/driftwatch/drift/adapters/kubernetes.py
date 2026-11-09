"""Read-only Kubernetes manifest comparison through narrowly scoped kubectl get calls."""

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

from driftwatch.drift.adapters.base import ProviderAdapter
from driftwatch.drift.adapters.opentofu import ScanError, validate_source_path
from driftwatch.drift.models import DriftFinding, ScannedResource
from driftwatch.drift.normalize_plan import parse_plan
from driftwatch.drift.policy import DriftPolicy

logger = logging.getLogger(__name__)
_KUBE_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")
_KUBE_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
_IGNORED_METADATA = {
    "creationTimestamp",
    "deletionGracePeriodSeconds",
    "deletionTimestamp",
    "managedFields",
    "resourceVersion",
    "selfLink",
    "uid",
    "generation",
}


class KubernetesAdapter(ProviderAdapter):
    """Compare declared YAML with objects fetched from one explicit kube context.

    The adapter invokes only ``kubectl get``. It never applies, deletes, patches,
    or execs in cluster workloads.
    """

    name = "kubernetes"

    def __init__(
        self,
        policy: DriftPolicy,
        source_root: Path,
        context: str,
        kubectl_path: str = "kubectl",
        timeout_seconds: int = 300,
        default_namespace: str | None = None,
    ) -> None:
        context = context.strip() if isinstance(context, str) else ""
        if not context or context.startswith("-") or len(context) > 256:
            raise ScanError(
                "invalid_kubernetes_context",
                "Configure a valid explicit Kubernetes context for this source.",
            )
        if default_namespace is not None and (
            not isinstance(default_namespace, str)
            or not _KUBE_LABEL.fullmatch(default_namespace)
        ):
            raise ScanError("invalid_kubernetes_namespace", "Kubernetes namespace is invalid.")
        self.policy = policy
        self.source_root = source_root
        self.context = context
        self.kubectl_path = kubectl_path
        self.timeout_seconds = timeout_seconds
        self.default_namespace = default_namespace
        self.resource_count = 0
        self.resources: list[ScannedResource] = []

    def scan(self, source: Path) -> list[DriftFinding]:
        source = validate_source_path(source, self.source_root)
        if shutil.which(self.kubectl_path) is None and not Path(self.kubectl_path).is_file():
            raise ScanError("kubectl_not_found", "Kubernetes CLI 'kubectl' was not found on PATH.")

        manifests = sorted(
            [*source.rglob("*.yaml"), *source.rglob("*.yml"), *source.rglob("*.json")]
        )
        if not manifests:
            raise ScanError("invalid_source", "No Kubernetes YAML or JSON manifests were found.")

        resources: list[dict[str, Any]] = []
        total_bytes = 0
        for manifest_path in manifests:
            try:
                safe_path = manifest_path.resolve(strict=True)
                safe_path.relative_to(source)
                if not safe_path.is_file():
                    continue
                size = safe_path.stat().st_size
                total_bytes += size
                if size > 1_048_576 or total_bytes > 10_485_760:
                    raise ScanError(
                        "source_too_large", "Kubernetes manifests exceed the scan size limit."
                    )
                text = safe_path.read_text(encoding="utf-8")
                documents = yaml.safe_load_all(text)
                for document in documents:
                    resources.extend(_expand_document(document))
            except ScanError:
                raise
            except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
                raise ScanError(
                    "invalid_manifest", "A Kubernetes manifest could not be read safely."
                ) from exc

        if not resources:
            raise ScanError(
                "invalid_manifest", "No Kubernetes resources were declared in the manifests."
            )

        drift: list[dict[str, Any]] = []
        seen_addresses: set[str] = set()
        self.resources = []
        for desired in resources:
            address, kind, name, namespace = _identity(desired, self.default_namespace)
            if address in seen_addresses:
                raise ScanError(
                    "duplicate_manifest_resource", "A resource is declared more than once."
                )
            seen_addresses.add(address)
            clean_desired = _strip_server_fields(desired)
            self.resources.append(
                ScannedResource(address=address, resource_type=f"kubernetes_{kind.lower()}")
            )
            actual = self._get(kind, name, namespace)
            projected = _project_declared(clean_desired, actual) if actual is not None else None
            if projected != clean_desired:
                drift.append(
                    {
                        "address": address,
                        "type": f"kubernetes_{kind.lower()}",
                        "change": {
                            "actions": ["update"],
                            "before": clean_desired,
                            "after": projected,
                            "before_sensitive": _sensitive_mask(
                                clean_desired, secret_resource=kind.lower() == "secret"
                            ),
                            "after_sensitive": _sensitive_mask(
                                projected, secret_resource=kind.lower() == "secret"
                            ),
                        },
                    }
                )
        self.resource_count = len(self.resources)
        return parse_plan({"resource_drift": drift}, self.policy)

    def _get(self, kind: str, name: str, namespace: str | None) -> dict[str, Any] | None:
        arguments = [
            self.kubectl_path,
            "get",
            kind.lower(),
            name,
            "--ignore-not-found=true",
            "-o=json",
            f"--context={self.context}",
        ]
        if namespace:
            arguments.append(f"--namespace={namespace}")
        env = os.environ.copy()
        env.pop("KUBECTL_EXTERNAL_DIFF", None)
        try:
            result = subprocess.run(
                arguments,
                cwd=self.source_root,
                env=env,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ScanError("timeout", "kubectl get exceeded the scan timeout.") from exc
        except OSError as exc:
            logger.error("kubectl could not start (%s)", type(exc).__name__)
            raise ScanError("execution_error", "Kubernetes CLI could not be started.") from exc
        if result.returncode != 0:
            logger.error("kubectl get failed (exit=%d)", result.returncode)
            raise ScanError(
                "cluster_query_failed",
                "Kubernetes resource query failed; command output was suppressed.",
            )
        if not result.stdout.strip():
            return None
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ScanError("invalid_cluster_response", "kubectl returned invalid JSON.") from exc
        if not isinstance(value, dict):
            raise ScanError("invalid_cluster_response", "kubectl returned an unexpected object.")
        return value


def _expand_document(document: Any) -> list[dict[str, Any]]:
    if document is None:
        return []
    if not isinstance(document, dict):
        raise ScanError("invalid_manifest", "Kubernetes manifest documents must be YAML objects.")
    if document.get("kind") == "List":
        items = document.get("items")
        if not isinstance(items, list):
            raise ScanError("invalid_manifest", "Kubernetes List manifests need an items array.")
        return [resource for item in items for resource in _expand_document(item)]
    _identity(document, None)
    return [document]


def _identity(
    manifest: dict[str, Any], default_namespace: str | None
) -> tuple[str, str, str, str | None]:
    kind = manifest.get("kind")
    api_version = manifest.get("apiVersion")
    metadata = manifest.get("metadata")
    name = metadata.get("name") if isinstance(metadata, dict) else None
    namespace = metadata.get("namespace") if isinstance(metadata, dict) else None
    if not isinstance(kind, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", kind):
        raise ScanError("invalid_manifest", "Kubernetes resource kind is invalid.")
    if not isinstance(api_version, str) or not api_version or len(api_version) > 128:
        raise ScanError("invalid_manifest", "Kubernetes apiVersion is invalid.")
    if not isinstance(name, str) or len(name) > 253 or not _KUBE_NAME.fullmatch(name):
        raise ScanError("invalid_manifest", "Kubernetes resource metadata.name is invalid.")
    if namespace is not None and (
        not isinstance(namespace, str) or not _KUBE_LABEL.fullmatch(namespace)
    ):
        raise ScanError("invalid_manifest", "Kubernetes resource namespace is invalid.")
    namespace = namespace or default_namespace
    address = f"{api_version}/{kind}/{namespace or '_cluster'}/{name}"
    return address, kind, name, namespace


def _project_declared(expected: Any, actual: Any) -> Any:
    """Keep only user-declared fields; ignore API defaults and server metadata."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return None
        projected = {}
        for key, expected_value in expected.items():
            if key == "status":
                continue
            if key == "metadata" and isinstance(expected_value, dict):
                metadata = {
                    meta_key: meta_value
                    for meta_key, meta_value in expected_value.items()
                    if meta_key not in _IGNORED_METADATA
                }
                projected[key] = _project_declared(metadata, actual.get(key, {}))
            else:
                projected[key] = _project_declared(expected_value, actual.get(key))
        return projected
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return None
        if len(expected) != len(actual):
            return actual
        return [
            _project_declared(value, actual[index] if index < len(actual) else None)
            for index, value in enumerate(expected)
        ]
    return actual


def _strip_server_fields(expected: Any) -> Any:
    if isinstance(expected, dict):
        result = {}
        for key, value in expected.items():
            if key == "status":
                continue
            if key == "metadata" and isinstance(value, dict):
                value = {
                    name: item for name, item in value.items() if name not in _IGNORED_METADATA
                }
            result[key] = _strip_server_fields(value)
        return result
    if isinstance(expected, list):
        return [_strip_server_fields(value) for value in expected]
    return expected


def _sensitive_mask(value: Any, secret_resource: bool = False) -> Any:
    if secret_resource and isinstance(value, dict):
        return {
            key: True if key in {"data", "stringData"} else _sensitive_mask(child)
            for key, child in value.items()
        }
    if isinstance(value, dict):
        mask = {}
        env_name = value.get("name")
        for key, child in value.items():
            if _sensitive_name(str(key)):
                mask[key] = True
            elif key == "value" and isinstance(env_name, str) and _sensitive_name(env_name):
                mask[key] = True
            else:
                nested = _sensitive_mask(child)
                if nested:
                    mask[key] = nested
        return mask
    if isinstance(value, list):
        return [_sensitive_mask(child) for child in value]
    return {}


def _sensitive_name(value: str) -> bool:
    lowered = value.casefold()
    return any(
        part in lowered
        for part in (
            "password",
            "passwd",
            "secret",
            "token",
            "private_key",
            "access_key",
            "credential",
        )
    )
