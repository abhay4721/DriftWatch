"""Read-only OpenTofu refresh-only planning adapter."""

import json
import logging
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from driftwatch.drift.adapters.base import ProviderAdapter
from driftwatch.drift.models import DriftFinding
from driftwatch.drift.normalize_plan import parse_plan
from driftwatch.drift.policy import DriftPolicy

logger = logging.getLogger(__name__)


class ScanError(RuntimeError):
    """Sanitized scan failure safe to return to a local CLI caller."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class OpenTofuAdapter(ProviderAdapter):
    name = "opentofu"

    def __init__(
        self,
        policy: DriftPolicy,
        source_root: Path,
        tofu_path: str = "tofu",
        timeout_seconds: int = 300,
    ) -> None:
        self.policy = policy
        self.source_root = source_root
        self.tofu_path = tofu_path
        self.timeout_seconds = timeout_seconds
        self.resource_count = 0

    def scan(self, source: Path) -> list[DriftFinding]:
        source = validate_source_path(source, self.source_root)
        config_files = list(source.glob("*.tf")) + list(source.glob("*.tofu"))
        if not config_files:
            raise ScanError("invalid_source", "No OpenTofu configuration files were found.")
        if shutil.which(self.tofu_path) is None and not Path(self.tofu_path).is_file():
            raise ScanError("tofu_not_found", "OpenTofu executable 'tofu' was not found on PATH.")

        self._run(["init", "-input=false", "-no-color"], source)
        self._validate(source)
        state_result = self._run(["state", "list", "-no-color"], source)
        addresses = [line for line in state_result.stdout.splitlines() if line.strip()]
        self.resource_count = len(addresses)
        if not addresses:
            raise ScanError(
                "missing_state",
                "No managed resources are present in this source state. "
                "Apply the approved configuration once before scanning.",
            )

        try:
            with tempfile.TemporaryDirectory(prefix="driftwatch-scan-") as temp_dir:
                plan_path = Path(temp_dir) / "refresh.plan"
                self._run(
                    [
                        "plan",
                        "-refresh-only",
                        "-input=false",
                        "-no-color",
                        "-detailed-exitcode",
                        f"-out={plan_path}",
                    ],
                    source,
                    allowed_exit_codes={0, 2},
                )
                show = self._run(["show", "-json", str(plan_path)], source)
                try:
                    plan: Any = json.loads(show.stdout)
                except json.JSONDecodeError as exc:
                    logger.error("OpenTofu emitted invalid plan JSON")
                    raise ScanError(
                        "invalid_plan_json", "OpenTofu returned invalid plan JSON."
                    ) from exc
                if not isinstance(plan, dict):
                    raise ScanError(
                        "invalid_plan_json", "OpenTofu returned an unexpected plan structure."
                    )
                return parse_plan(
                    plan,
                    self.policy,
                    actual_resolver=lambda resource: self._read_local_file_actual(resource, source),
                )
        except ScanError:
            raise
        except OSError as exc:
            logger.exception("Could not manage temporary OpenTofu plan file")
            raise ScanError(
                "temporary_file_error", "The temporary plan file could not be handled safely."
            ) from exc

    @staticmethod
    def _read_local_file_actual(
        resource: dict[str, Any] | Any, source: Path
    ) -> dict[str, str] | None:
        """Read safe local_file evidence when its provider reports changed content as deleted."""
        if resource.get("type") != "local_file":
            return None
        change = resource.get("change") or {}
        before = change.get("before") if isinstance(change, dict) else None
        if not isinstance(before, dict):
            return None
        filename = before.get("filename")
        if not isinstance(filename, str) or not filename:
            return None
        source_root = source.resolve()
        candidate = Path(filename)
        if not candidate.is_absolute():
            candidate = source_root / candidate
        try:
            path = candidate.resolve(strict=True)
            path.relative_to(source_root)
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1_048_576:
                return None
            content = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeError, ValueError):
            return None
        if before.get("sensitive_content") is not None:
            attribute = "sensitive_content"
        elif before.get("content") is not None:
            attribute = "content"
        else:
            return None
        return {attribute: content}

    def _validate(self, source: Path) -> None:
        result = self._run(["validate", "-json", "-no-color"], source, allowed_exit_codes={0, 1})
        try:
            validation = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise ScanError("validation_error", "OpenTofu validation failed.") from None
        if result.returncode != 0 or not validation.get("valid", False):
            raise ScanError("invalid_configuration", "OpenTofu configuration validation failed.")

    def _run(
        self,
        arguments: list[str],
        cwd: Path,
        allowed_exit_codes: set[int] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if allowed_exit_codes is None:
            allowed_exit_codes = {0}
        executable = (
            str(Path(self.tofu_path).resolve())
            if Path(self.tofu_path).is_file()
            else self.tofu_path
        )
        command = [executable, *arguments]
        env = os.environ.copy()
        env.pop("TF_LOG", None)
        env["TF_IN_AUTOMATION"] = "1"
        env["TF_INPUT"] = "0"
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            logger.error("OpenTofu command timed out: %s", arguments[0])
            raise ScanError(
                "timeout", f"OpenTofu {arguments[0]} exceeded the scan timeout."
            ) from exc
        except OSError as exc:
            logger.error(
                "OpenTofu command could not start: %s (%s)", arguments[0], type(exc).__name__
            )
            raise ScanError("execution_error", "OpenTofu could not be started.") from exc

        if result.returncode not in allowed_exit_codes:
            logger.error("OpenTofu command failed: %s (exit=%d)", arguments[0], result.returncode)
            raise ScanError(
                "execution_error",
                f"OpenTofu {arguments[0]} failed (exit code {result.returncode}); "
                "command output was suppressed to protect sensitive data.",
            )
        return result


def validate_source_path(source: Path, allowed_root: Path) -> Path:
    """Resolve a source directory and require it to stay under an allowed root."""
    try:
        root = allowed_root.resolve(strict=True)
        resolved = source.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ScanError(
            "invalid_source", "The source path is missing or outside the configured source root."
        ) from exc
    if resolved == root or not resolved.is_dir():
        raise ScanError(
            "invalid_source", "The source path must be a project directory below the source root."
        )
    return resolved
