"""Minimal, explicit GitHub Issues integration using a repository-scoped token."""

import json
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from pydantic import SecretStr


class GitHubIntegrationError(RuntimeError):
    def __init__(self, code: str, message: str, http_status: int = 502) -> None:
        self.code = code
        self.http_status = http_status
        super().__init__(message)


@dataclass(frozen=True)
class GitHubIssue:
    repository: str
    number: int
    url: str


def create_issue(
    repository: str | None,
    token: SecretStr | None,
    title: str,
    body: str,
) -> GitHubIssue:
    """Create a GitHub issue. This is called only from an explicit API action."""
    if not repository or not token or not token.get_secret_value().strip():
        raise GitHubIntegrationError(
            "github_not_configured",
            "Configure DRIFTWATCH_GITHUB_REPOSITORY and DRIFTWATCH_GITHUB_TOKEN first.",
            503,
        )
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise GitHubIntegrationError(
            "invalid_github_repository",
            "GitHub repository must use the owner/repository format.",
            503,
        )

    owner, name = repository.split("/", maxsplit=1)
    url = f"https://api.github.com/repos/{quote(owner)}/{quote(name)}/issues"
    request = Request(
        url,
        data=json.dumps({"title": title[:256], "body": body}).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token.get_secret_value()}",
            "Content-Type": "application/json",
            "User-Agent": "DriftWatch",
            "X-GitHub-Api-Version": "2026-03-10",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read())
    except HTTPError as exc:
        errors = {
            401: ("github_unauthorized", "GitHub rejected the configured token.", 502),
            403: ("github_forbidden", "The token cannot create issues in this repository.", 502),
            404: (
                "github_repository_not_found",
                "GitHub repository was not found or is inaccessible.",
                502,
            ),
            410: ("github_issues_disabled", "Issues are disabled for this GitHub repository.", 409),
            422: ("github_issue_rejected", "GitHub rejected the issue request.", 422),
        }
        code, message, status = errors.get(
            exc.code,
            ("github_request_failed", "GitHub could not create the issue.", 502),
        )
        raise GitHubIntegrationError(code, message, status) from exc
    except (TimeoutError, URLError, OSError) as exc:
        raise GitHubIntegrationError(
            "github_unavailable", "GitHub could not be reached; retry the request.", 502
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GitHubIntegrationError(
            "github_invalid_response", "GitHub returned an invalid response.", 502
        ) from exc

    issue_url = result.get("html_url") if isinstance(result, dict) else None
    issue_number = result.get("number") if isinstance(result, dict) else None
    if not isinstance(issue_url, str) or not isinstance(issue_number, int):
        raise GitHubIntegrationError(
            "github_invalid_response", "GitHub response did not include an issue URL and number."
        )
    return GitHubIssue(repository=repository, number=issue_number, url=issue_url)
