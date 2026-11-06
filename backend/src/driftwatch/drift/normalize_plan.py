"""Parse OpenTofu plan JSON into redacted, attribute-level findings."""

import hashlib
from collections.abc import Callable, Mapping
from typing import Any

from driftwatch.drift.models import AttributeChange, DriftFinding
from driftwatch.drift.normalize import normalize
from driftwatch.drift.policy import DriftPolicy

REDACTED = "[REDACTED]"
SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "private_key",
    "access_key",
    "credential",
)


def _is_sensitive_key(name: str) -> bool:
    lowered = name.casefold()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _at_path(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for part in path:
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _sensitive_at(mask: Any, path: tuple[str, ...]) -> bool:
    current = mask
    for part in path:
        if current is True:
            return True
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return False
        else:
            return False
    return current is True


def _flatten(
    before: Any, after: Any, path: tuple[str, ...] = ()
) -> list[tuple[tuple[str, ...], Any, Any]]:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        keys = sorted(set(before) | set(after), key=str)
        changes: list[tuple[tuple[str, ...], Any, Any]] = []
        for key in keys:
            changes.extend(_flatten(before.get(key), after.get(key), (*path, str(key))))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        changes = []
        for index in range(max(len(before), len(after))):
            old = before[index] if index < len(before) else None
            new = after[index] if index < len(after) else None
            changes.extend(_flatten(old, new, (*path, str(index))))
        return changes
    if normalize(before) == normalize(after):
        return []
    return [(path, before, after)]


def _path_string(path: tuple[str, ...]) -> str:
    result = ""
    for part in path:
        if part.isdigit():
            result += f"[{part}]"
        elif result:
            result += f".{part}"
        else:
            result = part
    return result or "(root)"


def _resource_drift(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if "resource_drift" in plan:
        raw = plan.get("resource_drift")
        return [item for item in raw or [] if isinstance(item, Mapping)]

    # Compatibility fallback for plan formats that omit resource_drift:
    # compare each saved object with the refreshed values in the plan.
    old_resources = _collect_resources((plan.get("prior_state") or {}).get("values", {}))
    refreshed = _collect_resources(plan.get("planned_values") or {})
    fallback: list[Mapping[str, Any]] = []
    for address, old in old_resources.items():
        new = refreshed.get(address)
        if new is None:
            continue
        before, after = old.get("values", {}), new.get("values", {})
        if _flatten(before, after):
            fallback.append(
                {
                    "address": address,
                    "type": old.get("type", "unknown"),
                    "change": {
                        "actions": ["update"],
                        "before": before,
                        "after": after,
                        "before_sensitive": {},
                        "after_sensitive": {},
                    },
                }
            )
    return fallback


def _collect_resources(values: Any) -> dict[str, Mapping[str, Any]]:
    resources: dict[str, Mapping[str, Any]] = {}
    if not isinstance(values, Mapping):
        return resources
    for resource in values.get("root_module", {}).get("resources", []) or []:
        if isinstance(resource, Mapping) and isinstance(resource.get("address"), str):
            resources[resource["address"]] = resource
    for module in values.get("root_module", {}).get("child_modules", []) or []:
        resources.update(_collect_resources({"root_module": module}))
    return resources


def parse_plan(
    plan: Mapping[str, Any],
    policy: DriftPolicy,
    actual_resolver: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
) -> list[DriftFinding]:
    """Parse drift changes, redact secrets, and classify every changed attribute."""
    format_version = plan.get("format_version")
    if format_version is not None:
        try:
            major_version = int(str(format_version).split(".", maxsplit=1)[0])
        except ValueError as exc:
            raise ValueError("OpenTofu plan has an invalid JSON format version.") from exc
        if major_version != 1:
            raise ValueError(
                f"OpenTofu plan JSON major version {major_version} is not supported."
            )
    findings: list[DriftFinding] = []
    for resource in _resource_drift(plan):
        address = str(resource.get("address", "unknown"))
        resource_type = str(resource.get("type", address.split(".", 1)[0]))
        change = resource.get("change") or {}
        actions = change.get("actions", []) if isinstance(change, Mapping) else []
        if not isinstance(change, Mapping) or "no-op" in actions:
            continue
        before, after = change.get("before"), change.get("after")
        if before is None and after is None:
            continue
        before = before if before is not None else {}
        if after is None and actual_resolver is not None:
            observed = actual_resolver(resource)
            if observed is not None:
                after = {**before, **observed} if isinstance(before, Mapping) else observed
        after = after if after is not None else {}
        before_mask = change.get("before_sensitive", {})
        after_mask = change.get("after_sensitive", {})
        attribute_changes: list[AttributeChange] = []
        severities: list[str] = []
        for path, expected, actual in _flatten(before, after):
            path_text = _path_string(path)
            if policy.is_ignored(address, path_text):
                continue
            sensitive = (
                _sensitive_at(before_mask, path)
                or _sensitive_at(after_mask, path)
                or any(_is_sensitive_key(part) for part in path)
                or policy.is_sensitive(path_text)
            )
            severity, action = policy.classify(resource_type, path_text, sensitive)
            severities.append(severity)
            attribute_changes.append(
                AttributeChange(
                    path=path_text,
                    expected=REDACTED if sensitive else normalize(expected),
                    actual=REDACTED if sensitive else normalize(actual),
                    sensitive=sensitive,
                )
            )
        if not attribute_changes:
            continue
        severity_rank = {"informational": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        most_severe_index = max(
            range(len(severities)), key=lambda index: severity_rank[severities[index]]
        )
        severity = severities[most_severe_index]
        recommended_action = policy.classify(
            resource_type,
            attribute_changes[most_severe_index].path,
            attribute_changes[most_severe_index].sensitive,
        )[1]
        fingerprint = hashlib.sha256(
            f"{address}\0{','.join(change.path for change in attribute_changes)}".encode()
        ).hexdigest()
        findings.append(
            DriftFinding(
                id=fingerprint,
                resource_address=address,
                resource_type=resource_type,
                attribute_changes=attribute_changes,
                severity=severity,
                recommended_action=recommended_action,
            )
        )
    return findings
