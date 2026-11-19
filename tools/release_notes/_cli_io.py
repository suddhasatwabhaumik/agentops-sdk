# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""CLI argument parsing, metadata parsing, and output file writers."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import glob
import os
import re
import sys
import tomllib
from typing import Any

from packaging import version as pkg_version

DEFAULT_REPO = "ORG/agentops-sdk"


def get_default_wheel_name(pyproject_path: str = "pyproject.toml") -> str:
    """Resolves the wheel filename from pyproject.toml or __init__.py."""
    pkg_version_str: str | None = None
    try:
        with open(pyproject_path, "rb") as handle:
            data = tomllib.load(handle)
        raw_ver = data.get("project", {}).get("version")
        pkg_version_str = str(raw_ver) if raw_ver else None
    except (OSError, tomllib.TOMLDecodeError):
        pkg_version_str = None
    if not pkg_version_str:
        init_path = os.path.join(
            os.path.dirname(os.path.abspath(pyproject_path)),
            "sdk",
            "src",
            "agentops_sdk",
            "__init__.py",
        )
        try:
            with open(init_path, encoding="utf-8") as handle:
                match = re.search(
                    r'^__version__\s*=\s*["\']([^"\']+)["\']',
                    handle.read(),
                    re.MULTILINE,
                )
            if match:
                pkg_version_str = match.group(1)
        except OSError:
            pkg_version_str = None
    resolved = pkg_version_str or "1.0.0"
    return f"agentops_sdk-{resolved}-py3-none-any.whl"


def parse_semver(filepath: str) -> pkg_version.Version:
    """Extracts Semantic Version from a release note filename."""
    match = re.search(r"v(\d+\.\d+\.\d+)", os.path.basename(filepath))
    return (
        pkg_version.Version(match.group(1))
        if match
        else pkg_version.Version("0.0.0")
    )


def release_sort_key(filepath: str) -> tuple[pkg_version.Version, str]:
    """Returns a deterministic sort key combining SemVer and filename."""
    return parse_semver(filepath), os.path.basename(filepath)


def clean_markdown_link(text: str) -> str:
    """Extracts plain text from a markdown link [Text](url)."""
    match = re.search(r"\[([^\]]+)\]", text)
    return match.group(1).strip("` ") if match else text.strip("` \t\r\n")


def parse_release_metadata(filepath: str) -> dict[str, str]:
    """Parses title, module, version, and body from a release markdown file."""
    with open(filepath, encoding="utf-8") as handle:
        content = handle.read()
    title_m = re.search(r"^#\s+Release Notes:\s*(.+)$", content, re.MULTILINE)
    raw_title = (
        title_m.group(1).strip() if title_m else os.path.basename(filepath)
    )
    clean_title = re.sub(r"\s*\(`?v\d+\.\d+\.\d+`?\)", "", raw_title).strip()
    mod_m = re.search(
        r"^##\s+(?:Module|Component|Service):\s*(.+)$", content, re.MULTILINE
    )
    module = clean_markdown_link(mod_m.group(1)) if mod_m else "agentops_sdk"
    ver_m = re.search(r"v(\d+\.\d+\.\d+)", os.path.basename(filepath))
    version = ver_m.group(0) if ver_m else "v0.0.0"
    clean_body = re.sub(r"^<!--[\s\S]*?-->\s*", "", content).strip()
    return {
        "file": filepath,
        "filename": os.path.basename(filepath),
        "version": version,
        "title": clean_title,
        "module": module,
        "body": clean_body,
    }


def sanitize_gh_output(value: str) -> str:
    """Strips CR/LF/NUL characters to prevent GITHUB_OUTPUT injection."""
    return (
        str(value).replace("\r", " ").replace("\n", " ").replace("\x00", "")
    ).strip()


def write_outputs(
    args: argparse.Namespace,
    result_md: str,
    full_uncompressed: str | None,
) -> None:
    """Writes release notes outputs and optional GitHub step summary."""
    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(result_md)
    if full_uncompressed:
        full_path = os.path.join(out_dir or ".", "RELEASE_NOTES_FULL.md")
        with open(full_path, "w", encoding="utf-8") as handle:
            handle.write(full_uncompressed)
    if args.summary_file and os.path.exists(
        os.path.dirname(args.summary_file) or "."
    ):
        try:
            with open(args.summary_file, "a", encoding="utf-8") as handle:
                handle.write("\n\n" + result_md + "\n")
        except OSError as exc:
            print(f"Warning: summary write failed: {exc}", file=sys.stderr)


def write_github_output(
    mode: str,
    new_files: Sequence[str],
    releases_dir: str,
    parse_fn: Callable[[str], dict[str, str]] = parse_release_metadata,
) -> None:
    """Writes sanitized release metadata keys to GITHUB_OUTPUT."""
    github_output = os.getenv("GITHUB_OUTPUT")
    if not github_output or not os.path.exists(
        os.path.dirname(github_output) or "."
    ):
        return
    all_files = glob.glob(os.path.join(releases_dir, "*.md"))
    target_files = (
        sorted(new_files, key=release_sort_key, reverse=True)
        if mode == "incremental" and new_files
        else sorted(all_files, key=release_sort_key, reverse=True)
    )
    top: dict[str, Any] = parse_fn(target_files[0]) if target_files else {}
    ver = str(top.get("version", "v1.0.0"))
    suffix = (
        "Incremental Release" if mode == "incremental" else "Platform Release"
    )
    title = f"AgentOps SDK {ver} — {top.get('title', suffix)}"
    publish = "true" if mode in ("all", "incremental") else "false"
    lines = [
        f"release_version={sanitize_gh_output(ver)}\n",
        f"release_title={sanitize_gh_output(title)}\n",
        f"resolved_mode={sanitize_gh_output(mode)}\n",
        f"should_publish_release={sanitize_gh_output(publish)}\n",
    ]
    try:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.writelines(lines)
    except OSError as exc:
        print(f"Warning: GITHUB_OUTPUT write failed: {exc}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    """Constructs the CLI parser for release note consolidation."""
    parser = argparse.ArgumentParser(
        description="Consolidate AgentOps SDK Release Notes"
    )
    parser.add_argument(
        "--mode", choices=["auto", "all", "incremental"], default="auto"
    )
    parser.add_argument("--base", default=os.getenv("GITHUB_EVENT_BEFORE", ""))
    parser.add_argument("--head", default=os.getenv("GITHUB_SHA", "HEAD"))
    parser.add_argument("--releases-dir", default="releases")
    parser.add_argument("--output", default="dist/RELEASE_NOTES.md")
    parser.add_argument(
        "--summary-file", default=os.getenv("GITHUB_STEP_SUMMARY", "")
    )
    parser.add_argument(
        "--repo", default=os.getenv("GITHUB_REPOSITORY") or DEFAULT_REPO
    )
    parser.add_argument("--wheel-name", default=None)
    return parser
