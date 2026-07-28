"""Read-only GitHub pull-request ingestion for the live demo."""

from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from model import Decision
from execution_trace import TraceStep


API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"
MAX_FILE_PAGES = 30
MAX_DIFF_CHARACTERS = 16_000
PR_PATH = re.compile(
    r"^/(?P<owner>[A-Za-z0-9][A-Za-z0-9-]*)/"
    r"(?P<repo>[A-Za-z0-9_.-]+)/pull/(?P<number>[1-9][0-9]*)/?$"
)

CIStatus = Literal["passed", "failed", "pending", "unknown"]


class GitHubFetchError(RuntimeError):
    """A user-safe failure while reading GitHub evidence."""


class GitHubChangedFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    filename: str
    status: str
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    changes: int = Field(ge=0)
    patch: str | None = None


class GitHubCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    source: Literal["check_run", "commit_status"]
    state: str
    details_url: str | None = None


class GitHubPRSnapshot(BaseModel):
    """Normalized, immutable evidence from one PR head commit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: str
    pr_number: int = Field(gt=0)
    html_url: str
    title: str
    author: str
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str
    draft: bool
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    files: tuple[GitHubChangedFile, ...]
    checks: tuple[GitHubCheck, ...]
    ci_status: CIStatus
    diff_excerpt: str
    diff_complete: bool
    fetched_at: datetime
    trace: tuple[TraceStep, ...]


def _duration_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1_000, 2)


def parse_github_pr_url(url: str) -> tuple[str, str, int]:
    """Parse a canonical github.com pull-request URL."""

    parsed = urlsplit(url.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
    ):
        raise ValueError("Enter an HTTPS github.com pull-request URL.")

    match = PR_PATH.fullmatch(parsed.path)
    if match is None:
        raise ValueError("Expected a URL like https://github.com/owner/repository/pull/42.")
    return (
        match.group("owner"),
        match.group("repo"),
        int(match.group("number")),
    )


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "merge-gate-read-only-demo",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request_json(client: httpx.Client, path: str) -> Any:
    try:
        response = client.get(path)
    except httpx.HTTPError as exc:
        raise GitHubFetchError(
            "GitHub could not be reached. Check the network and try again."
        ) from exc

    if response.status_code == 404:
        raise GitHubFetchError(
            "The pull request was not found, or the configured token cannot access it."
        )
    if response.status_code == 401:
        raise GitHubFetchError("GitHub rejected the configured credentials.")
    if response.status_code == 403:
        raise GitHubFetchError(
            "GitHub denied the read request. Check token permissions or the API rate limit."
        )
    if not response.is_success:
        raise GitHubFetchError(
            f"GitHub returned HTTP {response.status_code} for a read request."
        )
    try:
        return response.json()
    except ValueError as exc:
        raise GitHubFetchError("GitHub returned a malformed JSON response.") from exc


def _tool_step(
    *,
    name: str,
    summary: str,
    started_at: float,
    status: Literal["ok", "warning", "error"] = "ok",
    details: dict[str, str | int | float | bool | None] | None = None,
) -> TraceStep:
    return TraceStep(
        kind="tool",
        phase="GitHub evidence",
        name=name,
        status=status,
        summary=summary,
        duration_ms=_duration_ms(started_at),
        details=details or {},
    )


def _optional_request(
    client: httpx.Client,
    path: str,
    *,
    name: str,
) -> tuple[Any | None, TraceStep]:
    started_at = perf_counter()
    try:
        payload = _request_json(client, path)
    except GitHubFetchError:
        return None, _tool_step(
            name=name,
            summary="This CI evidence source was unavailable; CI will not be treated as passed.",
            started_at=started_at,
            status="warning",
        )
    return payload, _tool_step(
        name=name,
        summary="Read CI evidence for the PR head commit.",
        started_at=started_at,
    )


def _as_int(value: Any, *, default: int = 0) -> int:
    return value if isinstance(value, int) and value >= 0 else default


def _normalize_files(payload: list[Any]) -> tuple[GitHubChangedFile, ...]:
    files: list[GitHubChangedFile] = []
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
            raise GitHubFetchError("GitHub returned malformed changed-file evidence.")
        files.append(
            GitHubChangedFile(
                filename=item["filename"],
                status=str(item.get("status", "unknown")),
                additions=_as_int(item.get("additions")),
                deletions=_as_int(item.get("deletions")),
                changes=_as_int(item.get("changes")),
                patch=item.get("patch") if isinstance(item.get("patch"), str) else None,
            )
        )
    if not files:
        raise GitHubFetchError("The pull request contains no changed files to evaluate.")
    return tuple(files)


def _build_diff_excerpt(
    files: tuple[GitHubChangedFile, ...],
) -> tuple[str, bool]:
    sections = []
    complete = True
    for file in files:
        if file.patch is None:
            complete = False
        patch = file.patch or "# patch unavailable from GitHub"
        sections.append(
            "\n".join(
                [
                    f"diff --git a/{file.filename} b/{file.filename}",
                    f"# status: {file.status}",
                    patch,
                ]
            )
        )
    diff = "\n\n".join(sections)
    if len(diff) > MAX_DIFF_CHARACTERS:
        diff = (
            diff[:MAX_DIFF_CHARACTERS]
            + "\n\n# diff excerpt truncated by Merge Gate"
        )
        complete = False
    return diff, complete


def _normalize_checks(
    check_runs_payload: Any | None,
    statuses_payload: Any | None,
) -> tuple[GitHubCheck, ...]:
    checks: list[GitHubCheck] = []
    if isinstance(check_runs_payload, dict):
        for item in check_runs_payload.get("check_runs", []):
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "unknown"))
            conclusion = item.get("conclusion")
            state = str(conclusion) if status == "completed" and conclusion else status
            checks.append(
                GitHubCheck(
                    name=str(item.get("name", "Unnamed check")),
                    source="check_run",
                    state=state,
                    details_url=(
                        item.get("details_url")
                        if isinstance(item.get("details_url"), str)
                        else None
                    ),
                )
            )
    if isinstance(statuses_payload, dict):
        for item in statuses_payload.get("statuses", []):
            if not isinstance(item, dict):
                continue
            checks.append(
                GitHubCheck(
                    name=str(item.get("context", "Unnamed status")),
                    source="commit_status",
                    state=str(item.get("state", "unknown")),
                    details_url=(
                        item.get("target_url")
                        if isinstance(item.get("target_url"), str)
                        else None
                    ),
                )
            )
    return tuple(checks)


def summarize_ci_state(
    check_runs_payload: Any | None,
    statuses_payload: Any | None,
) -> CIStatus:
    """Combine observed check runs and commit statuses conservatively."""

    checks = _normalize_checks(check_runs_payload, statuses_payload)
    if not checks:
        return "unknown"

    failed = {
        "failure",
        "error",
        "cancelled",
        "timed_out",
        "action_required",
        "startup_failure",
        "stale",
    }
    pending = {"queued", "in_progress", "pending", "requested", "waiting", "unknown"}
    if any(check.state.lower() in failed for check in checks):
        return "failed"
    if any(check.state.lower() in pending for check in checks):
        return "pending"
    if all(check.state.lower() in {"success", "neutral", "skipped"} for check in checks):
        return "passed"
    return "unknown"


def build_decision_from_github(snapshot: GitHubPRSnapshot) -> Decision:
    """Map a GitHub snapshot to gate evidence without inventing unknown facts."""

    return Decision(
        id=(
            f"github_{snapshot.repository.replace('/', '_')}_"
            f"{snapshot.pr_number}_{snapshot.head_sha[:8]}"
        ),
        title=snapshot.title,
        files_touched=[file.filename for file in snapshot.files],
        diff_lines=snapshot.additions + snapshot.deletions,
        path_risk="unknown",
        ci_passed=(
            True
            if snapshot.ci_status == "passed"
            else False if snapshot.ci_status == "failed" else None
        ),
        reversible=None,
        touches_incident_code=None,
        agent_confidence=None,
        diff_complete=snapshot.diff_complete,
        bucket="live_github_pr",
        rationale="Read-only evidence fetched from GitHub.",
        diff_excerpt=snapshot.diff_excerpt,
    )


def fetch_github_text_file(
    repository: str,
    *,
    ref: str,
    path: str,
    token: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> tuple[str, TraceStep]:
    """Read a small UTF-8 policy file at an immutable repository ref."""

    if repository.count("/") != 1:
        raise ValueError("Repository must use owner/name format.")
    normalized_path = path.strip().lstrip("/")
    if (
        not normalized_path
        or normalized_path.startswith("../")
        or "/../" in normalized_path
    ):
        raise ValueError("Policy path must stay inside the target repository.")

    owner, repo = repository.split("/", 1)
    encoded_path = quote(normalized_path, safe="/")
    encoded_ref = quote(ref, safe="")
    started_at = perf_counter()
    with httpx.Client(
        base_url=API_ROOT,
        headers=_headers(token),
        timeout=20.0,
        follow_redirects=False,
        transport=transport,
    ) as client:
        payload = _request_json(
            client,
            f"/repos/{owner}/{repo}/contents/{encoded_path}?ref={encoded_ref}",
        )
    if not isinstance(payload, dict):
        raise GitHubFetchError("GitHub returned malformed repository-policy evidence.")
    if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
        raise GitHubFetchError("The repository policy is not an inspectable text file.")
    try:
        encoded_content = "".join(payload["content"].split())
        decoded = base64.b64decode(encoded_content, validate=True)
        text = decoded.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise GitHubFetchError("The repository policy is not valid UTF-8 text.") from exc
    if len(decoded) > 256_000:
        raise GitHubFetchError("The repository policy exceeds the 256 KB safety limit.")

    return text, _tool_step(
        name="github.rest.get_project_policy_at_base",
        summary=f"Read {normalized_path} from the PR base commit.",
        started_at=started_at,
        details={
            "path": normalized_path,
            "base_sha": ref[:12],
            "bytes": len(decoded),
        },
    )


def fetch_github_pr(
    url: str,
    *,
    token: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> GitHubPRSnapshot:
    """Fetch one GitHub PR, its files, and CI evidence without making writes."""

    owner, repo, pr_number = parse_github_pr_url(url)
    repository = f"{owner}/{repo}"
    root = f"/repos/{owner}/{repo}"
    trace: list[TraceStep] = []

    with httpx.Client(
        base_url=API_ROOT,
        headers=_headers(token),
        timeout=20.0,
        follow_redirects=False,
        transport=transport,
    ) as client:
        started_at = perf_counter()
        pull = _request_json(client, f"{root}/pulls/{pr_number}")
        trace.append(
            _tool_step(
                name="github.rest.get_pull_request",
                summary=f"Read metadata for {repository}#{pr_number}.",
                started_at=started_at,
            )
        )
        if not isinstance(pull, dict):
            raise GitHubFetchError("GitHub returned malformed pull-request metadata.")

        expected_files = _as_int(pull.get("changed_files"))
        raw_files: list[Any] = []
        page = 1
        started_at = perf_counter()
        while page <= MAX_FILE_PAGES:
            payload = _request_json(
                client,
                f"{root}/pulls/{pr_number}/files?per_page=100&page={page}",
            )
            if not isinstance(payload, list):
                raise GitHubFetchError("GitHub returned malformed changed-file evidence.")
            raw_files.extend(payload)
            if len(payload) < 100 or (
                expected_files and len(raw_files) >= expected_files
            ):
                break
            page += 1
        files = _normalize_files(raw_files)
        trace.append(
            _tool_step(
                name="github.rest.list_pull_request_files",
                summary=f"Read {len(files)} changed files across {page} page(s).",
                started_at=started_at,
                status="warning" if expected_files > len(files) else "ok",
                details={
                    "files": len(files),
                    "pages": page,
                    "reported_files": expected_files,
                },
            )
        )

        try:
            head_sha = str(pull["head"]["sha"])
        except (KeyError, TypeError) as exc:
            raise GitHubFetchError("GitHub did not return a PR head commit.") from exc

        check_runs, check_trace = _optional_request(
            client,
            f"{root}/commits/{head_sha}/check-runs?per_page=100",
            name="github.rest.list_check_runs",
        )
        trace.append(check_trace)
        statuses, status_trace = _optional_request(
            client,
            f"{root}/commits/{head_sha}/status?per_page=100",
            name="github.rest.get_combined_commit_status",
        )
        trace.append(status_trace)

    started_at = perf_counter()
    checks = _normalize_checks(check_runs, statuses)
    ci_status = summarize_ci_state(check_runs, statuses)
    if (check_runs is None or statuses is None) and ci_status != "failed":
        ci_status = "unknown"

    diff_excerpt, patch_complete = _build_diff_excerpt(files)
    diff_complete = patch_complete and (not expected_files or len(files) >= expected_files)
    trace.append(
        TraceStep(
            kind="function",
            phase="Normalize",
            name="build_decision_from_github",
            status="ok" if diff_complete else "warning",
            summary=(
                "Normalized GitHub data with a complete inspectable diff."
                if diff_complete
                else "Normalized GitHub data, but one or more diff patches are incomplete."
            ),
            duration_ms=_duration_ms(started_at),
            details={
                "ci_status": ci_status,
                "diff_complete": diff_complete,
                "checks": len(checks),
            },
        )
    )

    try:
        title = str(pull["title"])
        author = str(pull["user"]["login"])
        base_ref = str(pull["base"]["ref"])
        base_sha = str(pull["base"]["sha"])
        head_ref = str(pull["head"]["ref"])
    except (KeyError, TypeError) as exc:
        raise GitHubFetchError("GitHub returned incomplete pull-request metadata.") from exc

    return GitHubPRSnapshot(
        repository=repository,
        pr_number=pr_number,
        html_url=str(pull.get("html_url") or url.strip()),
        title=title,
        author=author,
        base_ref=base_ref,
        base_sha=base_sha,
        head_ref=head_ref,
        head_sha=head_sha,
        draft=bool(pull.get("draft", False)),
        additions=_as_int(pull.get("additions"), default=sum(file.additions for file in files)),
        deletions=_as_int(pull.get("deletions"), default=sum(file.deletions for file in files)),
        files=files,
        checks=checks,
        ci_status=ci_status,
        diff_excerpt=diff_excerpt,
        diff_complete=diff_complete,
        fetched_at=datetime.now(timezone.utc),
        trace=tuple(trace),
    )
