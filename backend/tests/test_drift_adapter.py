import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from driftwatch.drift.adapters.opentofu import OpenTofuAdapter, ScanError, validate_source_path
from driftwatch.drift.policy import DriftPolicy

POLICY = DriftPolicy.load(
    Path(__file__).parents[1] / "src" / "driftwatch" / "drift" / "policies" / "default.yml"
)


def test_source_path_cannot_escape_allowed_root(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    project = root / "demo"
    outside = tmp_path / "outside"
    project.mkdir(parents=True)
    outside.mkdir()

    assert validate_source_path(project, root) == project.resolve()
    with pytest.raises(ScanError, match="outside the configured source root"):
        validate_source_path(outside, root)


def test_local_file_observation_is_limited_to_source_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    generated = source / "generated"
    generated.mkdir()
    file_path = generated / "app.conf"
    file_path.write_bytes(b"APP_ENV=development\n")
    resource = {
        "type": "local_file",
        "change": {
            "before": {
                "filename": "./generated/app.conf",
                "content": "APP_ENV=production\n",
            }
        },
    }

    assert OpenTofuAdapter._read_local_file_actual(resource, source) == {
        "content": "APP_ENV=development\n"
    }
    resource["change"]["before"]["filename"] = str(tmp_path / "outside.txt")
    (tmp_path / "outside.txt").write_text("must not read", encoding="utf-8")
    assert OpenTofuAdapter._read_local_file_actual(resource, source) is None


def test_commands_are_argument_arrays_and_never_use_shell(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.tf").write_text("terraform {}", encoding="utf-8")
    completed = __import__("subprocess").CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""
    )
    adapter = OpenTofuAdapter(POLICY, source_root=tmp_path)

    with patch("driftwatch.drift.adapters.opentofu.subprocess.run", return_value=completed) as run:
        adapter._run(["validate", "-json"], project)

    assert run.call_args.args[0] == ["tofu", "validate", "-json"]
    assert run.call_args.kwargs["shell"] is False
    assert run.call_args.kwargs["cwd"] == project


def test_scan_stops_safely_when_state_has_no_managed_resources(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.tf").write_text("terraform {}", encoding="utf-8")
    adapter = OpenTofuAdapter(POLICY, source_root=tmp_path)
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""
    )
    valid = subprocess.CompletedProcess(
        args=[], returncode=0, stdout='{"valid":true}', stderr=""
    )

    with (
        patch("driftwatch.drift.adapters.opentofu.shutil.which", return_value="tofu"),
        patch.object(adapter, "_run", side_effect=[completed, valid, completed]) as run,
    ):
        with pytest.raises(ScanError) as error:
            adapter.scan(project)

    assert error.value.code == "missing_state"
    assert run.call_count == 3


def test_open_to_scope_scan_returns_sanitized_findings_and_cleans_temp_plan(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.tf").write_text("terraform {}", encoding="utf-8")
    plan_json = json.dumps(
        {
            "resource_drift": [
                {
                    "address": "local_file.x",
                    "type": "local_file",
                    "change": {
                        "actions": ["update"],
                        "before": {"content": "a"},
                        "after": {"content": "b"},
                    },
                }
            ]
        }
    )
    calls: list[list[str]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> __import__("subprocess").CompletedProcess[str]:
        calls.append(command)
        if command[1] == "state":
            return __import__("subprocess").CompletedProcess(command, 0, "local_file.x\n", "")
        if command[1] == "validate":
            return __import__("subprocess").CompletedProcess(command, 0, '{"valid":true}', "")
        if command[1] == "show":
            return __import__("subprocess").CompletedProcess(command, 0, plan_json, "")
        code = 0 if command[1] in {"init"} else 2
        return __import__("subprocess").CompletedProcess(command, code, "", "")

    adapter = OpenTofuAdapter(POLICY, source_root=tmp_path)
    with (
        patch("driftwatch.drift.adapters.opentofu.shutil.which", return_value="tofu"),
        patch("driftwatch.drift.adapters.opentofu.subprocess.run", side_effect=fake_run),
    ):
        findings = adapter.scan(project)

    assert len(findings) == 1
    assert findings[0].attribute_changes[0].actual == "b"
    assert adapter.resource_count == 1
    assert [call[1] for call in calls] == ["init", "validate", "state", "plan", "show"]
    plan_arg = next(arg for call in calls for arg in call if arg.startswith("-out="))
    assert not Path(plan_arg.removeprefix("-out=")).exists()


def test_scan_timeout_is_reported_without_raw_process_output(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    adapter = OpenTofuAdapter(POLICY, source_root=tmp_path)
    with patch(
        "driftwatch.drift.adapters.opentofu.subprocess.run",
        side_effect=__import__("subprocess").TimeoutExpired(
            "tofu init", 1, stderr="credential=secret"
        ),
    ):
        with pytest.raises(ScanError, match="exceeded the scan timeout") as error:
            adapter._run(["init"], project)
    assert "secret" not in str(error.value)
