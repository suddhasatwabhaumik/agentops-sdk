# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Unit tests for tools.release_notes.consolidate CLI and helpers."""

from __future__ import annotations

import pathlib
from unittest import mock

import pytest

from tools.release_notes import consolidate


def test_release_notes_semver_banner_and_builders(
    tmp_path: pathlib.Path,
) -> None:
    files = ["releases/v1.0.0_a.md", "releases/v1.10.0_b.md"]
    assert sorted(files, key=consolidate.release_sort_key, reverse=True) == [
        "releases/v1.10.0_b.md",
        "releases/v1.0.0_a.md",
    ]
    custom_toml = tmp_path / "pyproject.toml"
    custom_toml.write_text('[project]\nversion = "1.0.0"\n', encoding="utf-8")
    assert consolidate.get_default_wheel_name(str(custom_toml)) == (
        "agentops_sdk-1.0.0-py3-none-any.whl"
    )
    banner = consolidate.build_installation_banner("v1.0.0")
    assert "#subdirectory=sdk" not in banner
    rel_dir = tmp_path / "releases"
    rel_dir.mkdir()
    next_note = rel_dir / "v2.0.0_next.md"
    next_note.write_text(
        "# Release Notes: Next (`v2.0.0`)\n## Module: `agentops_sdk`\n"
        "## 1. Overview\nNext.\n## 2. Details\nMore.\n",
        encoding="utf-8",
    )
    exec_md, full_md = consolidate.build_full_consolidation(str(rel_dir), "abc")
    assert "Current Release: v2.0.0" in exec_md
    assert "Full Manifest" in full_md
    assert "#subdirectory=sdk" not in exec_md
    assert "Newly Added Capabilities" in (
        consolidate.build_incremental_consolidation([str(next_note)], "abc")
    )


def _setup_release_fixture(
    tmp_path: pathlib.Path,
) -> tuple[list[str], pathlib.Path, pathlib.Path, pathlib.Path]:
    rel_dir = tmp_path / "releases"
    rel_dir.mkdir()
    note = rel_dir / "v1.0.0_overhaul.md"
    note.write_text(
        "# Release Notes: Overhaul (`v1.0.0`)\n## Module: `agentops_sdk`\n"
        "## 1. Overview\nClean.\n",
        encoding="utf-8",
    )
    out_md = tmp_path / "dist" / "RELEASE_NOTES.md"
    argv = [
        "--mode",
        "auto",
        "--releases-dir",
        str(rel_dir),
        "--output",
        str(out_md),
    ]
    return argv, note, out_md, tmp_path / "gh.txt"


def _bad_release_metadata(note: pathlib.Path) -> dict[str, str]:
    return {
        "version": "v1.0.0\nevil=1",
        "title": "Clean\r\nINJECT=true",
        "module": "agentops_sdk",
        "file": str(note),
        "filename": note.name,
        "body": "Body",
    }


def test_release_notes_main_sanitizes_gh_output(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv, note, out_md, gh_out = _setup_release_fixture(tmp_path)
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    with (
        mock.patch.object(
            consolidate,
            "has_prior_release_tags",
            autospec=True,
            return_value=True,
        ),
        mock.patch.object(
            consolidate,
            "get_newly_added_files",
            autospec=True,
            return_value=[str(note)],
        ),
        mock.patch.object(
            consolidate,
            "parse_release_metadata",
            autospec=True,
            return_value=_bad_release_metadata(note),
        ),
    ):
        consolidate.main(argv)
    lines = gh_out.read_text(encoding="utf-8").splitlines()
    assert lines == [
        "release_version=v1.0.0 evil=1",
        "release_title=AgentOps SDK v1.0.0 evil=1 — Clean  INJECT=true",
        "resolved_mode=incremental",
        "should_publish_release=true",
    ]
    assert "#subdirectory=sdk" not in out_md.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("raw_text", "expected"),
    [
        ("[Module `agentops_sdk`](../sdk)", "Module agentops_sdk"),
        ("[`v1.0.0`](url)", "v1.0.0"),
        ("`agentops_sdk`", "agentops_sdk"),
    ],
)
def test_clean_markdown_link_strips_embedded_backticks(
    raw_text: str,
    expected: str,
) -> None:
    assert consolidate.clean_markdown_link(raw_text) == expected
