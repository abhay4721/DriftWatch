"""Run a sanitized, read-only infrastructure drift scan from the command line."""

import argparse
import json
import os
import sys
from pathlib import Path

from driftwatch.drift.adapters.opentofu import OpenTofuAdapter, ScanError, validate_source_path
from driftwatch.drift.engine import DriftEngine
from driftwatch.drift.policy import DriftPolicy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a read-only DriftWatch OpenTofu scan.")
    parser.add_argument(
        "--source",
        default="tofu-local",
        help="OpenTofu project path relative to DRIFTWATCH_SOURCE_ROOT (default: tofu-local)",
    )
    parser.add_argument(
        "--policy", type=Path, help="YAML policy file (defaults to the bundled policy)"
    )
    parser.add_argument("--tofu", default=os.environ.get("DRIFTWATCH_TOFU_PATH", "tofu"))
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("DRIFTWATCH_SCAN_TIMEOUT_SECONDS", "300")),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository = Path(__file__).resolve().parents[3]
    allowed_root = Path(os.environ.get("DRIFTWATCH_SOURCE_ROOT", repository / "demo")).expanduser()
    candidate = Path(args.source).expanduser()
    if not candidate.is_absolute():
        candidate = allowed_root / candidate
    try:
        if args.timeout < 1 or args.timeout > 3600:
            raise ValueError("Scan timeout must be between 1 and 3600 seconds.")
        source = validate_source_path(candidate, allowed_root)
        policy = DriftPolicy.load(args.policy)
        adapter = OpenTofuAdapter(
            policy,
            source_root=allowed_root,
            tofu_path=args.tofu,
            timeout_seconds=args.timeout,
        )
        report = DriftEngine(adapter).scan(source)
        try:
            report.source = source.relative_to(allowed_root.resolve()).as_posix()
        except ValueError:
            report.source = source.name
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
        return 2 if report.findings else 0
    except (ScanError, ValueError) as exc:
        error = {"status": "error", "error": str(exc)}
        if isinstance(exc, ScanError):
            error["code"] = exc.code
        print(json.dumps(error, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
