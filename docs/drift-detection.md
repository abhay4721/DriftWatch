# OpenTofu drift detection

DriftWatch's OpenTofu adapter runs `tofu init -input=false`, validates with `tofu validate -json`, checks that managed state exists, and creates a read-only `tofu plan -refresh-only -detailed-exitcode`. It then reads the saved plan with `tofu show -json`. Plan exit codes 0 and 2 mean a completed scan; 1 is an execution error.

The parser prefers the JSON plan's `resource_drift` entries. When the field is absent, it compares saved prior-state values with refreshed planned values. It creates stable resource/attribute fingerprints, normalizes mappings and Unicode, applies YAML policy, and returns expected and observed values at attribute granularity. OpenTofu may represent a provider resource whose remote object changed as a deletion with `after: null`. For `local_file`, the adapter may read a UTF-8 regular file of at most 1 MiB only when its resolved path remains inside the scanned project. Other absent resources remain reported as missing.

## Safety behavior

- No `tofu apply` or deprecated `tofu refresh` runs during scans.
- Commands use a fixed argument list, `shell=False`, noninteractive mode, and a configurable timeout.
- Scans require a project directory below `DRIFTWATCH_SOURCE_ROOT` (default: `demo/`).
- The binary plan is created in a private temporary directory and removed after `tofu show -json` completes.
- Raw command output and plan JSON are not returned or logged. Sensitive fields are redacted using OpenTofu sensitivity masks and sensitive attribute names before a report is produced.
- The demo's `.terraform/`, state file, and generated file are local artifacts. `.terraform.lock.hcl` is retained so provider selection is reproducible.

## Policy

The default policy is `backend/src/driftwatch/drift/policies/default.yml`. It configures default severity, resource-specific severity, attribute-pattern rules, ignored attributes, sensitive-value severity, sensitive attribute patterns, and recommended actions. Add a pattern such as `content` under `sensitive_attributes` when that field carries a secret. Use `--policy PATH` to supply a different YAML file. The policy is validated before a scan begins.

## Local demo files

- `demo/tofu-local/main.tf` defines one `local_file.application_config` resource.
- `scripts/demo.ps1` performs the initial apply, clean scan, external edit scan, restore, and final clean scan.
- `backend/src/driftwatch/scan_cli.py` runs a sanitized scan report for one project.

The demo is local-only and does not require cloud credentials. Download the provider once with `tofu init`; subsequent scans can run offline while the provider remains available in the local plugin cache.
