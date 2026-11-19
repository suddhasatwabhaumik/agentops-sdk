# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Markdown rendering and truncation helpers for consolidated release notes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import datetime
import glob
import os
import re

from tools.release_notes import _cli_io

DEFAULT_REPO = _cli_io.DEFAULT_REPO
MAX_GITHUB_RELEASE_BODY_CHARS = 115_000
_BANNER_TEMPLATE = (
    "> [!IMPORTANT]\n"
    "> ### Package Installation "
    "(`.whl` — Private / Enterprise GitHub Repository)\n"
    "> On private or enterprise SSO repositories, unauthenticated\n"
    "> `pip install https://github.com/...` URLs redirect to an SSO login\n"
    "> page and fail wheel verification.\n>\n"
    "> **Option 1 — GitHub CLI (`gh`) (Recommended for `.whl`):**\n"
    "> ```bash\n"
    '> gh release download {tag} --repo {repo} --pattern "*.whl" '
    "--dir /tmp --clobber && \\\n"
    "> pip install /tmp/{wheel}\n"
    "> ```\n"
    "> **Option 2 — Direct `pip install` via Git (`git+ssh` or PAT):**\n"
    "> ```bash\n"
    '> pip install "git+ssh://git@github.com/{repo}.git@{tag}"\n'
    "> ```\n"
    "> **Option 3 — Browser Download:**\n"
    "> Download `{wheel}` from **Assets** and run:\n"
    "> ```bash\n"
    "> pip install ~/Downloads/{wheel}\n"
    "> ```\n\n"
)


def build_installation_banner(
    version: str,
    repo: str = DEFAULT_REPO,
    wheel_name: str = "agentops_sdk-1.0.0-py3-none-any.whl",
) -> str:
    """Builds a pip installation banner for private SSO GitHub Releases."""
    return _BANNER_TEMPLATE.format(
        tag=version or "<release-tag>",
        repo=repo,
        wheel=wheel_name,
    )


def extract_overview(body: str) -> str:
    """Extracts Section 1 (Overview) from a release note document."""
    match = re.search(r"(## 1\. Overview[\s\S]*?)(?=\n## 2\.|\Z)", body)
    if match:
        return match.group(1).strip()
    return body[:1500].strip() + "\n\n*(Full details in source manifest)*"


def enforce_github_limit(
    text: str,
    max_chars: int = MAX_GITHUB_RELEASE_BODY_CHARS,
) -> str:
    """Enforces GitHub Release body limits with a clean truncation notice."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_break = truncated.rfind("\n\n")
    if last_break > max_chars - 5000:
        truncated = truncated[:last_break]
    notice = (
        "\n\n---\n\n> [!NOTE]\n"
        "> **Release Description Truncated**: Trimmed to fit GitHub's "
        "125,000-character limit. Full specifications are in `releases/` "
        "and the attached `RELEASE_NOTES_FULL.md` release asset.\n"
    )
    return truncated + notice


def _utc_now_str() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _render_full_manifest(
    metadata_list: Sequence[dict[str, str]],
    now_str: str,
    sha: str,
    repo: str,
    wheel_name: str,
) -> str:
    top_ver = metadata_list[0]["version"] if metadata_list else "v1.0.0"
    lines = [
        "# AgentOps SDK — Consolidated Platform Release Notes "
        "(Full Manifest)\n",
        f"> **Generated at**: `{now_str}` | **Commit**: `{sha[:7]}` | "
        f"**Total Releases**: `{len(metadata_list)}`\n",
        build_installation_banner(top_ver, repo, wheel_name),
    ]
    for idx, meta in enumerate(metadata_list, 1):
        lines.append(
            f"\n## [{idx}/{len(metadata_list)}] {meta['version']} "
            f"— {meta['title']}\n"
        )
        lines.append(f"*Source File: `{meta['file']}`*\n\n")
        lines.append(meta["body"])
        lines.append("\n\n---\n")
    return "\n".join(lines)


def _render_executive_body(
    metadata_list: Sequence[dict[str, str]],
    now_str: str,
    sha: str,
    repo: str,
    wheel_name: str,
) -> str:
    top_ver = metadata_list[0]["version"] if metadata_list else "v1.0.0"
    lines = [
        "# AgentOps SDK — Consolidated Platform Release Notes\n",
        f"> **Generated at**: `{now_str}` | **Commit**: `{sha[:7]}` | "
        f"**Total Releases**: `{len(metadata_list)}`\n",
        build_installation_banner(top_ver, repo, wheel_name),
        "## Executive Release Index\n",
        "| Version | Feature / Subsystem | Primary Module | Source Document |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for meta in metadata_list:
        lines.append(
            f"| **{meta['version']}** | {meta['title']} | "
            f"`{meta['module']}` | [{meta['filename']}]"
            f"(releases/{meta['filename']}) |"
        )
    lines.append("\n---\n")
    if metadata_list:
        top = metadata_list[0]
        lines.append(
            f"\n## Current Release: {top['version']} — {top['title']}\n"
        )
        lines.append(f"*Source File: `{top['file']}`*\n\n")
        lines.append(top["body"])
        lines.append("\n\n---\n## Historical Platform Releases Overview\n")
        for idx, meta in enumerate(metadata_list[1:], 2):
            lines.append(
                f"\n### [{idx}/{len(metadata_list)}] {meta['version']} "
                f"— {meta['title']}\n"
            )
            lines.append(extract_overview(meta["body"]))
            lines.append("\n\n---\n")
    return enforce_github_limit("\n".join(lines))


def build_full_consolidation(
    releases_dir: str,
    sha: str,
    repo: str,
    wheel_name: str,
    parse_fn: Callable[[str], dict[str, str]] = _cli_io.parse_release_metadata,
) -> tuple[str, str]:
    """Combines all release notes into executive and full manifests."""
    all_files = glob.glob(os.path.join(releases_dir, "*.md"))
    sorted_files = sorted(all_files, key=_cli_io.release_sort_key, reverse=True)
    metadata = [parse_fn(path) for path in sorted_files]
    now_str = _utc_now_str()
    full_doc = _render_full_manifest(metadata, now_str, sha, repo, wheel_name)
    exec_doc = _render_executive_body(metadata, now_str, sha, repo, wheel_name)
    return exec_doc, full_doc


def build_incremental_consolidation(
    files: Sequence[str],
    sha: str,
    repo: str,
    wheel_name: str,
    parse_fn: Callable[[str], dict[str, str]] = _cli_io.parse_release_metadata,
) -> str:
    """Formats only newly added release notes for an incremental release."""
    sorted_files = sorted(files, key=_cli_io.release_sort_key, reverse=True)
    metadata = [parse_fn(path) for path in sorted_files]
    now_str = _utc_now_str()
    top_ver = metadata[0]["version"] if metadata else "v1.0.0"
    lines = [
        f"# AgentOps SDK — Release Notes for `{sha[:7]}`\n",
        f"> **Release Date**: `{now_str}` | **Commit**: `{sha}`\n",
        build_installation_banner(top_ver, repo, wheel_name),
        f"### Newly Added Capabilities in this Release "
        f"(`{len(metadata)}` new module(s)):\n",
    ]
    for meta in metadata:
        ver, title, mod = meta["version"], meta["title"], meta["module"]
        lines.append(f"* **{ver}** — **{title}** (`{mod}`)")
    lines.append("\n---\n")
    for meta in metadata:
        lines.append(meta["body"])
        lines.append("\n\n---\n")
    return enforce_github_limit("\n".join(lines))


def build_fallback_latest(
    releases_dir: str,
    sha: str,
    repo: str,
    wheel_name: str,
    parse_fn: Callable[[str], dict[str, str]] = _cli_io.parse_release_metadata,
) -> str:
    """Builds release notes referencing the latest baseline release."""
    all_files = glob.glob(os.path.join(releases_dir, "*.md"))
    if not all_files:
        return (
            "# AgentOps SDK — Release Notes\n\n"
            f"No release notes found in `{releases_dir}`."
        )
    latest_file = max(all_files, key=_cli_io.release_sort_key)
    meta = parse_fn(latest_file)
    ts = _utc_now_str()
    lines = [
        f"# AgentOps SDK — Release Notes (`{meta['version']}`)\n",
        f"> **Build Timestamp**: `{ts}` | **Commit**: `{sha[:7]}`\n",
        build_installation_banner(meta["version"], repo, wheel_name),
        f"> [!NOTE]\n> No new release note was added in commit `{sha[:7]}`. "
        f"Displaying latest platform baseline: **{meta['title']}**.\n\n",
        meta["body"],
    ]
    return enforce_github_limit("\n".join(lines))
