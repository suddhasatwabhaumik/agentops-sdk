# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Hygiene and security tests for repository markdown documentation."""

from __future__ import annotations

import pathlib
import re

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_MARKDOWN_GLOBS = (
    "README.md",
    "sdk/README.md",
    "mcp_servers/README.md",
    "docs/*.md",
    "wiki/*.md",
    "releases/*.md",
    "examples/**/*.md",
)

_FORBIDDEN_IDENTIFIER_PATTERNS = (
    ("workstation_uri", re.compile("".join(("file://", "/Users/")), re.I)),
    ("gcp_prj_id", re.compile(r"prj-[a-z0-9-]+", re.I)),
    ("gcp_project_num_1", re.compile("".join(("822159", "364951")))),
    ("gcp_project_num_2", re.compile("".join(("78517", "522776")))),
    ("cost_center_tag", re.compile("".join(("CC-", "AG", "CO")), re.I)),
    ("customer_term_1", re.compile("".join(("ag", "co")), re.I)),
    ("customer_term_2", re.compile("".join(("enable", "on")), re.I)),
    (
        "customer_term_3",
        re.compile(r"\b" + "".join(("ed", "h")) + r"\b", re.I),
    ),
    (
        "customer_term_4",
        re.compile(r"\b" + "".join(("deal", "er")) + r"\b", re.I),
    ),
)

_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+<>-])"
    r"([A-Za-z0-9._%+<>-]+@[A-Za-z0-9.<>{}$_-]+\.[A-Za-z]{2,})"
)
_RFC2606_DOMAINS = frozenset({"example.com", "example.org", "example.net"})
_RFC2606_SUFFIXES = (".example.com", ".example.org", ".example.net")

_INLINE_DSN_PASSWORD_RE = re.compile(
    r"(?:postgres(?:ql)?(?:\+\w+)?|mysql(?:\+\w+)?|mssql(?:\+\w+)?)://"
    r"[^/\s:@]+:[^@\s<{]+@"
)
_CWE209_HTTP_DETAIL_RE = re.compile(
    r"HTTPException\s*\((?:[^()]|\([^()]*\))*?\bdetail\s*=\s*"
    r"(?:(?:str|repr)\s*\("
    r"|(?:f|fr|rf)(?:\"[^\"]*\{\s*(?:(?:str|repr)\s*\(\s*)?(?:exc|err|e)\b"
    r"|'[^']*\{\s*(?:(?:str|repr)\s*\(\s*)?(?:exc|err|e)\b))",
    re.DOTALL,
)
_FENCED_CODE_RE = re.compile(r"^```[\s\S]*?^```", re.MULTILINE)
_MARKDOWN_LINK_RE = re.compile(r"(?<!\[)\[(?!\[)([^\]]+)\]\(([^)]+)\)")


def _collect_files(globs: tuple[str, ...]) -> list[pathlib.Path]:
    matched: set[pathlib.Path] = set()
    for pattern in globs:
        matched.update(_REPO_ROOT.glob(pattern))
    files = sorted(path for path in matched if path.is_file())
    assert files, f"Expected markdown files for patterns: {globs}"
    return files


def _is_allowed_email(candidate: str) -> bool:
    lowered = candidate.lower()
    local_part, _, domain = lowered.partition("@")
    if domain == "github.com" and (
        local_part == "git" or ("<" in local_part and ">" in local_part)
    ):
        return True
    if domain in _RFC2606_DOMAINS or domain.endswith(_RFC2606_SUFFIXES):
        return True
    has_placeholder = ("<" in lowered and ">" in lowered) or "${" in lowered
    return (
        domain.endswith(".gserviceaccount.com")
        and "project" in lowered
        and has_placeholder
    )


def test_markdown_no_workstation_uris_or_real_cloud_identifiers() -> None:
    violations: list[str] = []
    for md_path in _collect_files(_MARKDOWN_GLOBS):
        rel = md_path.relative_to(_REPO_ROOT)
        content = md_path.read_text(encoding="utf-8")
        for rule_name, pattern in _FORBIDDEN_IDENTIFIER_PATTERNS:
            for match in pattern.finditer(content):
                violations.append(f"{rel} [{rule_name}]: {match.group(0)!r}")
        for match in _EMAIL_RE.finditer(content):
            email = match.group(1)
            if not _is_allowed_email(email):
                violations.append(f"{rel} [non_rfc2606_email]: {email!r}")
    assert not violations, "Markdown hygiene leaks:\n" + "\n".join(violations)


def test_markdown_no_inline_dsn_passwords_or_cwe209_http_detail() -> None:
    violations: list[str] = []
    for md_path in _collect_files(_MARKDOWN_GLOBS):
        rel = md_path.relative_to(_REPO_ROOT)
        content = md_path.read_text(encoding="utf-8")
        for match in _INLINE_DSN_PASSWORD_RE.finditer(content):
            violations.append(
                f"{rel} [inline_dsn_password]: {match.group(0)!r}"
            )
        for match in _CWE209_HTTP_DETAIL_RE.finditer(content):
            snippet = " ".join(match.group(0).split())
            violations.append(f"{rel} [cwe209_http_detail]: {snippet!r}")
    assert not violations, "Markdown security leaks:\n" + "\n".join(violations)


def test_markdown_relative_links_resolve() -> None:
    broken_links: list[str] = []
    for md_path in _collect_files(_MARKDOWN_GLOBS):
        rel = md_path.relative_to(_REPO_ROOT)
        prose = _FENCED_CODE_RE.sub("", md_path.read_text(encoding="utf-8"))
        for match in _MARKDOWN_LINK_RE.finditer(prose):
            raw_target = match.group(2).strip()
            if raw_target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_part = raw_target.split("#", 1)[0].strip()
            if not path_part:
                continue
            clean_target = path_part.split(None, 1)[0]
            resolved = (md_path.parent / clean_target).resolve()
            if not resolved.exists():
                broken_links.append(f"{rel} -> {raw_target!r}")
    assert not broken_links, "Broken markdown links:\n" + "\n".join(
        broken_links
    )


@pytest.mark.parametrize(
    "dsn_snippet",
    [
        "postgres://agentops:s3cr3t@127.0.0.1:5432/agentops",
        "postgresql://agentops:s3cr3t@127.0.0.1:5432/agentops",
        "postgresql+psycopg2://agentops:s3cr3t@127.0.0.1:5434/agentops",
        "mysql://admin:s3cr3t@127.0.0.1:3306/app",
        "mssql+pyodbc://sa:s3cr3t@127.0.0.1:1433/app",
    ],
)
def test_inline_dsn_password_regex_detects_credentials(
    dsn_snippet: str,
) -> None:
    assert _INLINE_DSN_PASSWORD_RE.search(dsn_snippet) is not None


@pytest.mark.parametrize(
    "cwe209_snippet",
    [
        "HTTPException(status_code=500, detail=str(exc))",
        "HTTPException(status_code=500, detail=repr(err))",
        'HTTPException(status_code=500, detail=f"DB failed: {exc}")',
        "HTTPException(status_code=500, detail=f'Error: {err!r}')",
        'HTTPException(status_code=400, detail=f"Invalid: {e}")',
    ],
)
def test_cwe209_http_detail_regex_detects_exception_leaks(
    cwe209_snippet: str,
) -> None:
    assert _CWE209_HTTP_DETAIL_RE.search(cwe209_snippet) is not None
