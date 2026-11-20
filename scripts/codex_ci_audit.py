#!/usr/bin/env python3
"""Read-only, dependency-free inventory of GitHub Actions workflows.

Heuristic text inspection, NOT a YAML parser or a GitHub billing report.
Never edits workflows, starts jobs, or reads secrets from the environment.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

EVENTS = (
    "push", "pull_request", "pull_request_target", "schedule",
    "workflow_dispatch", "workflow_run", "issues", "issue_comment",
    "repository_dispatch", "release",
)
EVENT_RE = re.compile(r"^  (" + "|".join(EVENTS) + r"):", re.MULTILINE)
NAME_RE = re.compile(r"^name:\s*(.+)$", re.MULTILINE)
CONCURRENCY_RE = re.compile(r"^\s*concurrency:", re.MULTILINE)
CACHE_RE = re.compile(r"^\s*cache:\s*(pip|npm|yarn|pnpm)\b", re.MULTILINE)
PATHS_RE = re.compile(r"^\s{4}paths(?:-ignore)?:", re.MULTILINE)


def event_block(source: str, name: str) -> str:
    """Return a single top-level on.<event> section if in block syntax."""
    lines = source.splitlines()
    begin = next((i for i, line in enumerate(lines)
                  if re.match(r"^  " + re.escape(name) + r":(?:\s|$)", line)), None)
    if begin is None:
        return ""
    result = []
    for line in lines[begin + 1:]:
        if line.strip() and not line.lstrip().startswith("#"):
            indent = len(line) - len(line.lstrip())
            if indent <= 2:
                break
        result.append(line)
    return "\n".join(result)


def scan_workflows(repo_root: Path) -> dict:
    directory = repo_root / ".github" / "workflows"
    files = sorted(set(directory.glob("*.yml")) | set(directory.glob("*.yaml")))
    event_counts = Counter()
    no_pr_paths = []
    model_downloads = []
    no_concurrency = []
    uncached_python = []

    for path in files:
        source = path.read_text(encoding="utf-8")
        # Strip comment-only lines to avoid confusing documentation with code.
        active = "\n".join(line for line in source.splitlines()
                           if not line.lstrip().startswith("#"))
        events = set(EVENT_RE.findall(active))
        event_counts.update(events)
        if "pull_request" in events and not PATHS_RE.search(event_block(active, "pull_request")):
            no_pr_paths.append(path.name)
        if re.search(r"\bollama\s+pull\b", active):
            model_downloads.append(path.name)
        if not CONCURRENCY_RE.search(active):
            no_concurrency.append(path.name)
        if ("python -m pip install" in active or "pip install" in active) and not CACHE_RE.search(active):
            uncached_python.append(path.name)

    return {
        "repo_root": str(repo_root.resolve()),
        "workflow_count": len(files),
        "event_counts": dict(sorted(event_counts.items())),
        "review_candidates": {
            "pull_requests_without_paths": no_pr_paths,
            "ollama_model_downloads": model_downloads,
            "without_concurrency": no_concurrency,
            "pip_install_without_declared_cache": uncached_python,
        },
        "warning": (
            "Heuristic static inventory only; not cost, billing, proof of duplicate "
            "jobs, or a recommendation to disable required CI/runtime schedules."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Repository root (default: current directory)")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()
    root = Path(args.repo).expanduser()
    workflow_dir = root / ".github" / "workflows"
    if not workflow_dir.is_dir():
        parser.error(f"Workflow directory does not exist: {workflow_dir}")
    result = scan_workflows(root)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"GitHub workflow files: {result['workflow_count']}")
        print("Events:", ", ".join(f"{k}={v}" for k, v in result["event_counts"].items()))
        for key, values in result["review_candidates"].items():
            print(f"{key}: {len(values)} candidates")
        print(result["warning"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
