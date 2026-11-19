#!/usr/bin/env python3
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Consolidates AgentOps SDK release notes for automated CI/CD."""

from __future__ import annotations

from collections.abc import Sequence
import glob
import os
import shutil
import subprocess
import sys

from tools.release_notes import _cli_io
from tools.release_notes import _markdown

DEFAULT_REPO = _cli_io.DEFAULT_REPO
MAX_GITHUB_RELEASE_BODY_CHARS = _markdown.MAX_GITHUB_RELEASE_BODY_CHARS
clean_markdown_link = _cli_io.clean_markdown_link
enforce_github_limit = _markdown.enforce_github_limit
extract_overview = _markdown.extract_overview
get_default_wheel_name = _cli_io.get_default_wheel_name
parse_release_metadata = _cli_io.parse_release_metadata
parse_semver = _cli_io.parse_semver
release_sort_key = _cli_io.release_sort_key

_ZERO_SHA = "0000000000000000000000000000000000000000"


def _run_git(args: Sequence[str]) -> str:
    git_bin = shutil.which("git")
    if not git_bin:
        raise FileNotFoundError("git executable not found on PATH.")
    res = subprocess.run(
        [git_bin, *args], capture_output=True, text=True, check=True
    )
    return str(res.stdout)


def get_latest_release_tag() -> str:
    """Returns the most recent release tag in git, or empty string."""
    try:
        return _run_git(["describe", "--tags", "--abbrev=0"]).strip()
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return ""


def _resolve_diff_base(base_sha: str) -> str:
    if not base_sha or base_sha == _ZERO_SHA:
        return get_latest_release_tag() or "HEAD~1"
    return base_sha


def get_newly_added_files(
    base_sha: str, head_sha: str, releases_dir: str
) -> list[str]:
    """Finds newly added markdown files in releases_dir between commits."""
    resolved_base = _resolve_diff_base(base_sha)
    args = [
        "diff",
        "--name-only",
        "--diff-filter=A",
        resolved_base,
        head_sha or "HEAD",
        "--",
        f"{releases_dir}/*.md",
    ]
    try:
        stdout = _run_git(args)
        files = [
            f.strip()
            for f in stdout.strip().splitlines()
            if f.strip() and f.strip().endswith(".md")
        ]
        return [f for f in files if os.path.exists(f)]
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        print(f"Warning: git diff check failed: {exc}", file=sys.stderr)
        return []


def has_prior_release_tags() -> bool:
    """Checks whether any release tags exist in the git repository."""
    try:
        return bool(_run_git(["tag", "-l"]).strip())
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False


def has_release_notes_or_banner_updates(
    base_sha: str, head_sha: str, releases_dir: str
) -> bool:
    """Checks whether release notes or consolidate.py were modified."""
    resolved_base = _resolve_diff_base(base_sha)
    args = [
        "diff",
        "--name-only",
        "--diff-filter=M",
        resolved_base,
        head_sha or "HEAD",
        "--",
        f"{releases_dir}/*.md",
        "tools/release_notes/consolidate.py",
        "tools/release_notes/_markdown.py",
    ]
    try:
        return bool(_run_git(args).strip())
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False


def build_installation_banner(
    version: str,
    repo: str = DEFAULT_REPO,
    wheel_name: str | None = None,
) -> str:
    """Builds a pip installation banner for private SSO GitHub Releases."""
    return _markdown.build_installation_banner(
        version, repo, wheel_name or get_default_wheel_name()
    )


def build_full_consolidation(
    releases_dir: str,
    sha: str,
    repo: str = DEFAULT_REPO,
    wheel_name: str | None = None,
) -> tuple[str, str]:
    """Combines all release notes into executive and full manifests."""
    return _markdown.build_full_consolidation(
        releases_dir,
        sha,
        repo,
        wheel_name or get_default_wheel_name(),
        parse_fn=parse_release_metadata,
    )


def build_incremental_consolidation(
    files: Sequence[str],
    sha: str,
    repo: str = DEFAULT_REPO,
    wheel_name: str | None = None,
) -> str:
    """Formats only newly added release note files."""
    return _markdown.build_incremental_consolidation(
        files,
        sha,
        repo,
        wheel_name or get_default_wheel_name(),
        parse_fn=parse_release_metadata,
    )


def build_fallback_latest(
    releases_dir: str,
    sha: str,
    repo: str = DEFAULT_REPO,
    wheel_name: str | None = None,
) -> str:
    """Builds fallback release notes referencing the latest release."""
    return _markdown.build_fallback_latest(
        releases_dir,
        sha,
        repo,
        wheel_name or get_default_wheel_name(),
        parse_fn=parse_release_metadata,
    )


def _resolve_execution_mode(
    requested_mode: str,
    base_sha: str,
    head_sha: str,
    releases_dir: str,
) -> tuple[str, list[str]]:
    new_files = get_newly_added_files(base_sha, head_sha, releases_dir)
    if requested_mode != "auto":
        return requested_mode, new_files
    if not has_prior_release_tags():
        return "all", new_files
    if new_files:
        return "incremental", new_files
    if has_release_notes_or_banner_updates(base_sha, head_sha, releases_dir):
        return "all", new_files
    return "latest", new_files


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint for release notes consolidation."""
    args = _cli_io.build_parser().parse_args(
        list(argv) if argv is not None else None
    )
    wheel = args.wheel_name or get_default_wheel_name()
    mode, new_files = _resolve_execution_mode(
        args.mode, args.base, args.head, args.releases_dir
    )
    full_doc: str | None = None
    if mode == "all":
        result_md, full_doc = build_full_consolidation(
            args.releases_dir, args.head, args.repo, wheel
        )
    elif mode == "incremental":
        files = new_files or glob.glob(os.path.join(args.releases_dir, "*.md"))
        result_md = build_incremental_consolidation(
            files, args.head, args.repo, wheel
        )
    else:
        result_md = build_fallback_latest(
            args.releases_dir, args.head, args.repo, wheel
        )
    _cli_io.write_outputs(args, result_md, full_doc)
    _cli_io.write_github_output(
        mode, new_files, args.releases_dir, parse_fn=parse_release_metadata
    )


if __name__ == "__main__":
    main()
