"""Mechanical GitHub corpus selection for the fix-coverage study."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from ..executor.base import EnvironmentSetupError, RepoState
from ..executor.docker_exec import _environment_spec
from .fix_coverage import CORPUS_SCHEMA, _atomic_json

_API = "https://api.github.com"
_FIX_TITLE = re.compile(r"^fix(?:e[ds]?|ing)?(?:\b|\s|[:([])", re.IGNORECASE)
_PRODUCTION_EXCLUDED_PARTS = {
    "benchmarks",
    "docs",
    "documentation",
    "examples",
    "tests",
    "test",
    "testing",
}


class GitHubClient:
    """Small cached, read-only client with explicit rate-limit handling."""

    def __init__(self, cache: Path, token_env: str = "GITHUB_TOKEN"):
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        self.token = os.environ.get(token_env)

    def get(self, url: str) -> dict:
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.cache / f"{key}.json"
        if path.is_file():
            payload = json.loads(path.read_text())
            if not isinstance(payload, dict):
                raise TypeError(f"cached GitHub response is not an object: {path}")
            return payload
        transport_attempt = 0
        while True:
            headers = {
                "Accept": "application/vnd.github+json",
                "User-Agent": "exhibit-a-fix-coverage-selector/1",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    payload = json.loads(response.read())
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in {403, 429}:
                    raise
                reset = int(exc.headers.get("X-RateLimit-Reset", "0") or 0)
                delay = max(1, min(90, reset - int(time.time()) + 1))
                time.sleep(delay)
            except (urllib.error.URLError, TimeoutError):
                transport_attempt += 1
                if transport_attempt >= 5:
                    raise
                time.sleep(2 ** (transport_attempt - 1))
        if not isinstance(payload, dict):
            raise TypeError(f"GitHub response is not an object: {url}")
        _atomic_json(path, payload)
        return payload


def select_fix_corpus(
    *,
    output: str | Path,
    preregistration: str | Path,
    cache: str | Path,
    date_from: str,
    date_to: str,
    repository_count: int,
    repository_scan_limit: int,
    target_instances: int,
    per_repository_cap: int,
    token_env: str = "GITHUB_TOKEN",
) -> dict:
    """Select a stable manifest without executing Exhibit A or observing outcomes."""
    if not 1 <= repository_count <= 100:
        raise ValueError("repository_count must be between 1 and 100")
    if not repository_count <= repository_scan_limit <= 1000:
        raise ValueError("repository_scan_limit must be between repository_count and 1000")
    if not 1 <= target_instances <= 1000:
        raise ValueError("target_instances must be between 1 and 1000")
    if not 1 <= per_repository_cap <= 100:
        raise ValueError("per_repository_cap must be between 1 and 100")
    start = _date(date_from)
    end = _date(date_to, end_of_day=True)
    if end < start:
        raise ValueError("date_to must not precede date_from")

    selected_at = datetime.now(timezone.utc).isoformat()
    cache_root = Path(cache).resolve()
    api = GitHubClient(cache_root / "github-api", token_env)
    repositories = _top_repositories(api, repository_scan_limit)
    repository_records = []
    queues: list[tuple[dict, deque[dict], Path]] = []
    exclusions: list[dict] = []
    environment_eligible = 0
    for rank, repository in enumerate(repositories, start=1):
        if environment_eligible >= repository_count:
            break
        record = {
            "rank": rank,
            "full_name": repository["full_name"],
            "html_url": repository["html_url"],
            "stars_at_selection": repository["stargazers_count"],
            "default_branch": repository["default_branch"],
        }
        clone = cache_root / "repositories" / str(repository["full_name"]).replace("/", "--")
        try:
            _ensure_default_clone(repository, clone)
            record["default_head_sha"] = _git_output(["git", "-C", str(clone), "rev-parse", "HEAD"])
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            record.update(
                {
                    "eligible": False,
                    "reason_code": "repository_clone_failed",
                    "detail": str(exc)[-1000:],
                }
            )
            repository_records.append(record)
            continue
        try:
            _validate_environment_tree(
                clone,
                str(record["default_head_sha"]),
                str(repository["html_url"]),
            )
        except (OSError, ValueError, EnvironmentSetupError) as exc:
            record.update(
                {
                    "eligible": False,
                    "reason_code": "repository_no_supported_pinned_lock",
                    "detail": str(exc)[-1000:],
                }
            )
            repository_records.append(record)
            continue
        environment_eligible += 1
        candidates = _candidate_prs(api, str(repository["full_name"]), date_from, date_to)
        if not candidates:
            record.update(
                {
                    "eligible": True,
                    "has_matching_candidates": False,
                    "reason_code": "repository_no_matching_bug_prs",
                    "detail": "no merged PR with exact bug label in the registered window",
                }
            )
            repository_records.append(record)
            continue
        record.update(
            {
                "eligible": True,
                "has_matching_candidates": True,
                "candidate_prs": len(candidates),
                "candidate_prs_after_cap": min(len(candidates), per_repository_cap),
            }
        )
        repository_records.append(record)
        for candidate in candidates[per_repository_cap:]:
            exclusions.append(
                {
                    "repository": repository["full_name"],
                    "pull_number": candidate["number"],
                    "source_url": candidate["html_url"],
                    "reason_code": "per_repository_cap",
                    "detail": f"outside registered cap of {per_repository_cap} PRs per repo",
                }
            )
        queues.append((repository, deque(candidates[:per_repository_cap]), clone))

    instances: list[dict] = []
    while queues and len(instances) < target_instances:
        next_round: list[tuple[dict, deque[dict], Path]] = []
        for repository, candidates, clone in queues:
            if len(instances) >= target_instances:
                next_round.append((repository, candidates, clone))
                continue
            if not candidates:
                continue
            candidate = candidates.popleft()
            try:
                instance = _resolve_candidate(api, repository, candidate, clone, start, end)
            except (OSError, ValueError, EnvironmentSetupError, subprocess.SubprocessError) as exc:
                exclusions.append(
                    {
                        "repository": repository["full_name"],
                        "pull_number": candidate["number"],
                        "source_url": candidate["html_url"],
                        "reason_code": _selection_error_code(exc),
                        "detail": str(exc)[-1000:],
                    }
                )
            else:
                instances.append(instance)
            if candidates:
                next_round.append((repository, candidates, clone))
        queues = next_round

    for repository, candidates, _clone in queues:
        for candidate in candidates:
            exclusions.append(
                {
                    "repository": repository["full_name"],
                    "pull_number": candidate["number"],
                    "source_url": candidate["html_url"],
                    "reason_code": "sample_size_reached",
                    "detail": f"outside the registered sample of {target_instances} instances",
                }
            )

    prereg_path = Path(preregistration).resolve(strict=True)
    prereg_bytes = prereg_path.read_bytes()
    payload = {
        "schema_version": CORPUS_SCHEMA,
        "preregistration": {
            "path": str(Path(preregistration)),
            "sha256": hashlib.sha256(prereg_bytes).hexdigest(),
        },
        "selection": {
            "selected_at": selected_at,
            "source": "GitHub REST API and Git smart HTTP",
            "github_transport_retry": (
                "five attempts for URL/timeout failures with 1,2,4,8 second backoff"
            ),
            "repository_query": "language:Python fork:false archived:false",
            "repository_order": "stars descending at selection time",
            "repository_count": repository_count,
            "repository_count_definition": (
                "first star-ranked repositories passing pinned-environment eligibility"
            ),
            "repository_scan_limit": repository_scan_limit,
            "repositories_scanned": len(repository_records),
            "environment_eligible_repositories": environment_eligible,
            "pull_request_rule": (
                f"first 100 GitHub results merged {date_from}..{date_to}, sorted by "
                "updated ascending; exact case-insensitive bug label or title beginning "
                "fix/fixed/fixes/fixing"
            ),
            "pull_request_search_cap_per_repository": 100,
            "candidate_order": (
                "merged_at ascending within repository, then round-robin by repository rank"
            ),
            "per_repository_cap": per_repository_cap,
            "target_instances": target_instances,
            "repositories": repository_records,
            "stopping_reason": (
                "target_reached"
                if len(instances) == target_instances
                else "registered_candidate_universe_exhausted"
            ),
        },
        "exclusions": exclusions,
        "instances": instances,
    }
    _atomic_json(Path(output).resolve(), payload)
    return payload


def _top_repositories(api: GitHubClient, count: int) -> list[dict]:
    repositories = []
    for page in range(1, (count + 99) // 100 + 1):
        requested = min(100, count - len(repositories))
        query = urllib.parse.urlencode(
            {
                "q": "language:Python fork:false archived:false",
                "sort": "stars",
                "order": "desc",
                "per_page": requested,
                "page": page,
            }
        )
        payload = api.get(f"{_API}/search/repositories?{query}")
        items = payload.get("items")
        if not isinstance(items, list):
            raise TypeError("GitHub repository search returned invalid items")
        repositories.extend(item for item in items if isinstance(item, dict))
        if len(items) < requested:
            break
    if len(repositories) < count:
        raise ValueError(f"GitHub returned only {len(repositories)} repositories")
    return repositories[:count]


def _candidate_prs(
    api: GitHubClient,
    full_name: str,
    date_from: str,
    date_to: str,
) -> list[dict]:
    candidates = []
    query = urllib.parse.urlencode(
        {
            "q": f"repo:{full_name} is:pr is:merged merged:{date_from}..{date_to}",
            "sort": "updated",
            "order": "asc",
            "per_page": 100,
            "page": 1,
        }
    )
    payload = api.get(f"{_API}/search/issues?{query}")
    items = payload.get("items")
    if not isinstance(items, list):
        raise TypeError(f"GitHub PR search for {full_name} returned invalid items")
    for item in items:
        if not isinstance(item, dict):
            continue
        labels = item.get("labels", [])
        title = str(item.get("title", ""))
        exact_bug_label = any(
            isinstance(label, dict) and str(label.get("name", "")).casefold() == "bug"
            for label in labels
        )
        fix_title = _FIX_TITLE.match(title) is not None
        if not exact_bug_label and not fix_title:
            continue
        pull = item.get("pull_request")
        merged_at = pull.get("merged_at") if isinstance(pull, dict) else None
        if merged_at:
            basis = []
            if exact_bug_label:
                basis.append("exact_bug_label")
            if fix_title:
                basis.append("fix_title_prefix")
            candidates.append(
                {
                    "number": int(item["number"]),
                    "title": title,
                    "html_url": str(item["html_url"]),
                    "merged_at": str(merged_at),
                    "selection_basis": basis,
                }
            )
    return sorted(candidates, key=lambda item: (item["merged_at"], item["number"]))


def _resolve_candidate(
    api: GitHubClient,
    repository: dict,
    candidate: dict,
    clone: Path,
    start: datetime,
    end: datetime,
) -> dict:
    full_name = str(repository["full_name"])
    pull = api.get(f"{_API}/repos/{full_name}/pulls/{candidate['number']}")
    if not pull.get("merged") or not isinstance(pull.get("merge_commit_sha"), str):
        raise ValueError("not_merged: PR detail has no merged commit")
    fix_sha = str(pull["merge_commit_sha"]).lower()
    _git(["git", "-C", str(clone), "fetch", "--depth=2", "origin", fix_sha])
    parents = _git_output(
        ["git", "-C", str(clone), "rev-list", "--parents", "-n", "1", fix_sha]
    ).split()
    if len(parents) < 2:
        raise ValueError("missing_first_parent: fixing commit has no parent")
    buggy_sha = parents[1].lower()
    commit_date = _git_output(["git", "-C", str(clone), "show", "-s", "--format=%cI", fix_sha])
    timestamp = datetime.fromisoformat(commit_date.replace("Z", "+00:00"))
    if timestamp < start or timestamp > end:
        raise ValueError("commit_date_outside_window: commit date is outside registered window")
    changed = _git_output(
        [
            "git",
            "-C",
            str(clone),
            "diff",
            "--name-only",
            buggy_sha,
            fix_sha,
            "--",
            "*.py",
        ]
    ).splitlines()
    production = [path for path in changed if _is_production_python(path)]
    if not production:
        raise ValueError("no_production_python_change: no production Python file changed")
    _validate_environment_tree(clone, buggy_sha, str(repository["html_url"]))
    _validate_environment_tree(clone, fix_sha, str(repository["html_url"]))
    slug = full_name.casefold().replace("/", "-").replace("_", "-")
    identifier = f"{slug}-pr-{candidate['number']}"
    if len(identifier) > 80:
        suffix = hashlib.sha256(identifier.encode()).hexdigest()[:10]
        identifier = f"{identifier[:69]}-{suffix}"
    return {
        "id": identifier,
        "repository": f"{repository['html_url']}.git",
        "buggy_sha": buggy_sha,
        "fix_sha": fix_sha,
        "claim": str(pull["title"]).strip(),
        "commit_date": timestamp.astimezone(timezone.utc).isoformat(),
        "source_url": str(pull["html_url"]),
        "selection_basis": candidate["selection_basis"],
    }


def _ensure_default_clone(repository: dict, clone: Path) -> None:
    if (clone / ".git").is_dir():
        return
    clone.parent.mkdir(parents=True, exist_ok=True)
    if clone.exists():
        shutil.rmtree(clone)
    _git(
        [
            "git",
            "clone",
            "--filter=blob:none",
            "--depth=1",
            "--single-branch",
            "--branch",
            str(repository["default_branch"]),
            "--no-checkout",
            "--no-tags",
            "--",
            f"{repository['html_url']}.git",
            str(clone),
        ],
        timeout=300,
    )


def _validate_environment_tree(clone: Path, sha: str, source: str) -> None:
    names = _git_output(["git", "-C", str(clone), "ls-tree", "--name-only", sha]).splitlines()
    lock_names = [
        name
        for name in names
        if name in {"poetry.lock", "Pipfile.lock"}
        or (name.startswith("requirements") and name.endswith(".txt"))
    ]
    with tempfile.TemporaryDirectory(prefix="exhibit-a-selection-") as temporary:
        checkout = Path(temporary)
        for name in lock_names:
            content = _git_output(["git", "-C", str(clone), "show", f"{sha}:{name}"])
            (checkout / name).write_text(content + "\n")
        _environment_spec(
            RepoState(str(checkout), "selection", source=source),
            base_reference="selection-only@sha256:0",
        )


def _is_production_python(path: str) -> bool:
    parts = {part.casefold() for part in Path(path).parts[:-1]}
    return path.endswith(".py") and not parts.intersection(_PRODUCTION_EXCLUDED_PARTS)


def _selection_error_code(exc: Exception) -> str:
    detail = str(exc)
    prefix = detail.split(":", 1)[0]
    known = {
        "commit_date_outside_window",
        "missing_first_parent",
        "no_production_python_change",
        "not_merged",
    }
    if prefix in known:
        return prefix
    if isinstance(exc, EnvironmentSetupError):
        return "unsupported_or_invalid_pinned_environment"
    if isinstance(exc, subprocess.SubprocessError):
        return "git_resolution_failed"
    return "candidate_resolution_failed"


def _date(value: str, *, end_of_day: bool = False) -> datetime:
    parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end_of_day:
        return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def _git(argv: list[str], timeout: int = 120) -> None:
    subprocess.run(argv, check=True, capture_output=True, text=True, timeout=timeout)


def _git_output(argv: list[str], timeout: int = 120) -> str:
    return subprocess.run(
        argv,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    ).stdout.strip()
