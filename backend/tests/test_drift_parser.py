from pathlib import Path

from driftwatch.drift.normalize_plan import REDACTED, parse_plan
from driftwatch.drift.policy import DriftPolicy

POLICY = DriftPolicy.load(
    Path(__file__).parents[1] / "src" / "driftwatch" / "drift" / "policies" / "default.yml"
)


def test_parse_plan_reports_attribute_level_drift_and_stable_identity() -> None:
    plan = {
        "resource_drift": [
            {
                "address": "local_file.application_config",
                "type": "local_file",
                "change": {
                    "actions": ["update"],
                    "before": {"content": "APP_ENV=production\n", "file_permission": "0644"},
                    "after": {"content": "APP_ENV=development\n", "file_permission": "0644"},
                    "before_sensitive": {},
                    "after_sensitive": {},
                },
            }
        ]
    }

    first = parse_plan(plan, POLICY)
    second = parse_plan(plan, POLICY)

    assert len(first) == 1
    assert first[0].id == second[0].id
    assert first[0].severity == "medium"
    assert [change.path for change in first[0].attribute_changes] == ["content"]
    assert first[0].attribute_changes[0].expected == "APP_ENV=production\n"
    assert first[0].attribute_changes[0].actual == "APP_ENV=development\n"


def test_parse_plan_redacts_sensitive_values_from_both_sides() -> None:
    finding = parse_plan(
        {
            "resource_drift": [
                {
                    "address": "example.secret",
                    "type": "example",
                    "change": {
                        "actions": ["update"],
                        "before": {"api_token": "old-token"},
                        "after": {"api_token": "new-token"},
                        "before_sensitive": {"api_token": True},
                        "after_sensitive": {"api_token": True},
                    },
                }
            ]
        },
        POLICY,
    )[0]

    assert finding.severity == "high"
    assert finding.attribute_changes[0].expected == REDACTED
    assert finding.attribute_changes[0].actual == REDACTED
    assert "old-token" not in str(finding.model_dump())
    assert "new-token" not in str(finding.model_dump())


def test_policy_can_mark_project_specific_attributes_sensitive() -> None:
    policy = POLICY.model_copy(update={"sensitive_attributes": ["content"]})
    finding = parse_plan(
        {
            "resource_drift": [
                {
                    "address": "local_file.example",
                    "type": "local_file",
                    "change": {
                        "actions": ["update"],
                        "before": {"content": "secret before"},
                        "after": {"content": "secret after"},
                    },
                }
            ]
        },
        policy,
    )[0]
    assert finding.attribute_changes[0].expected == REDACTED
    assert finding.attribute_changes[0].actual == REDACTED


def test_parser_ignores_noop_and_applies_configured_ignore_patterns() -> None:
    policy = POLICY.model_copy(update={"ignore_attributes": ["*.comment"]})
    plan = {
        "resource_drift": [
            {
                "address": "test_resource.example",
                "type": "test_resource",
                "change": {
                    "actions": ["update"],
                    "before": {"comment": "old"},
                    "after": {"comment": "new"},
                },
            },
            {
                "address": "test_resource.noop",
                "type": "test_resource",
                "change": {
                    "actions": ["no-op"],
                    "before": {"name": "same"},
                    "after": {"name": "same"},
                },
            },
        ]
    }
    assert parse_plan(plan, policy) == []


def test_parser_uses_state_comparison_when_resource_drift_is_absent() -> None:
    plan = {
        "prior_state": {
            "values": {
                "root_module": {
                    "resources": [
                        {
                            "address": "local_file.demo",
                            "type": "local_file",
                            "values": {"content": "old"},
                        }
                    ]
                }
            }
        },
        "planned_values": {
            "root_module": {
                "resources": [
                    {
                        "address": "local_file.demo",
                        "type": "local_file",
                        "values": {"content": "new"},
                    }
                ]
            }
        },
    }
    findings = parse_plan(plan, POLICY)
    assert len(findings) == 1
    assert findings[0].attribute_changes[0].actual == "new"


def test_parser_uses_provider_evidence_when_refresh_reports_resource_deleted() -> None:
    plan = {
        "resource_drift": [
            {
                "address": "local_file.application_config",
                "type": "local_file",
                "change": {
                    "actions": ["delete"],
                    "before": {
                        "content": "APP_ENV=production\n",
                        "filename": "./generated/app.conf",
                    },
                    "after": None,
                },
            }
        ]
    }

    findings = parse_plan(
        plan, POLICY, actual_resolver=lambda _: {"content": "APP_ENV=development\n"}
    )

    assert len(findings) == 1
    assert [
        (change.path, change.expected, change.actual) for change in findings[0].attribute_changes
    ] == [("content", "APP_ENV=production\n", "APP_ENV=development\n")]


def test_parser_rejects_unsupported_plan_format_major_version() -> None:
    try:
        parse_plan({"format_version": "2.0", "resource_drift": []}, POLICY)
    except ValueError as error:
        assert "major version 2 is not supported" in str(error)
    else:
        raise AssertionError("Unsupported plan format major version was accepted")
