"""YAML-backed severity, ignore, and remediation policy."""

from fnmatch import fnmatchcase
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["critical", "high", "medium", "low", "informational"]


class AttributeRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str
    severity: Severity
    recommended_action: str = (
        "Review the change and reconcile it through the approved IaC workflow."
    )


class DriftPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    default_severity: Severity = "medium"
    resource_types: dict[str, Severity] = Field(default_factory=dict)
    attribute_rules: list[AttributeRule] = Field(default_factory=list)
    ignore_attributes: list[str] = Field(default_factory=list)
    sensitive_attributes: list[str] = Field(default_factory=list)
    sensitive_severity: Severity = "high"
    default_recommended_action: str = (
        "Review the change and reconcile it through the approved IaC workflow."
    )

    @classmethod
    def load(cls, path: Path | None = None) -> "DriftPolicy":
        policy_path = path or Path(__file__).with_name("policies") / "default.yml"
        try:
            document = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"Unable to load drift policy at {policy_path}") from exc
        if not isinstance(document, dict):
            raise ValueError("Drift policy must contain a YAML object")
        return cls.model_validate(document)

    def is_ignored(self, resource_address: str, attribute_path: str) -> bool:
        value = f"{resource_address}.{attribute_path}"
        return any(
            fnmatchcase(attribute_path, pattern) or fnmatchcase(value, pattern)
            for pattern in self.ignore_attributes
        )

    def is_sensitive(self, attribute_path: str) -> bool:
        return any(fnmatchcase(attribute_path, pattern) for pattern in self.sensitive_attributes)

    def classify(self, resource_type: str, attribute_path: str, sensitive: bool) -> tuple[str, str]:
        if sensitive:
            return self.sensitive_severity, self.default_recommended_action
        for rule in self.attribute_rules:
            if fnmatchcase(attribute_path, rule.pattern):
                return rule.severity, rule.recommended_action
        severity = self.resource_types.get(resource_type, self.default_severity)
        return severity, self.default_recommended_action
