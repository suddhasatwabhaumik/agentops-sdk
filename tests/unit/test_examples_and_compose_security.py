# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Security regression tests for examples, compose configs, and workflows."""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import re
import subprocess
import sys
import types
from typing import Any

import pytest
import yaml

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TUTORIAL_DIR = _REPO_ROOT / "examples/tutorial"
_COMPOSE_FILES = (
    _REPO_ROOT / "examples/full_stack/docker-compose.yaml",
    _TUTORIAL_DIR / "observability/compose.ports.yaml",
)
_FULL_STACK_COMPOSE = _COMPOSE_FILES[0]
_OTEL_COLLECTOR_CONFIG = (
    _REPO_ROOT / "examples/full_stack/config/otel-collector/config.yaml"
)
_WORKFLOW_GLOBS = (
    ".github/workflows/*.y*ml",
    "examples/cicd_templates/*.y*ml",
)
_CLOUDSQL_HELPER = _TUTORIAL_DIR / "00_setup/resources/cloudsql.py"
_COMMON_HELPER = _TUTORIAL_DIR / "00_setup/resources/common.py"
_PREFLIGHT_SCRIPT = _TUTORIAL_DIR / "00_setup/preflight.py"
_SHARED_TELEMETRY = _TUTORIAL_DIR / "_shared/telemetry.py"
_CASE_TELEMETRY_MAIN = _TUTORIAL_DIR / "02_case_telemetry/main.py"
_AUDIT_LEDGER_MAIN = _TUTORIAL_DIR / "14_audit_ledger/main.py"

_IMAGE_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
_PINNED_ACTION_RE = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$"
)


class _ComposeSafeLoader(yaml.SafeLoader):
    """Safe YAML loader supporting Docker Compose !override tags."""


def _construct_override(loader: yaml.SafeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    msg = f"Unsupported !override node: {type(node)}"
    raise yaml.constructor.ConstructorError(None, None, msg, node.start_mark)


_ComposeSafeLoader.add_constructor("!override", _construct_override)


def _load_compose_yaml(path: pathlib.Path) -> dict[str, Any]:
    loader = _ComposeSafeLoader(path.read_text(encoding="utf-8"))
    try:
        parsed = loader.get_single_data()
    finally:
        loader.dispose()
    assert isinstance(parsed, dict)
    return parsed


def _load_module_from_path(name: str, path: pathlib.Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def _collect_workflow_files() -> list[pathlib.Path]:
    matched: set[pathlib.Path] = set()
    for pattern in _WORKFLOW_GLOBS:
        matched.update(_REPO_ROOT.glob(pattern))
    files = sorted(path for path in matched if path.is_file())
    assert files, f"No workflow files matched {_WORKFLOW_GLOBS}"
    return files


def _collect_external_uses(job_cfg: dict[str, Any]) -> list[str]:
    external_refs: list[str] = []
    job_uses = job_cfg.get("uses")
    if isinstance(job_uses, str) and not job_uses.startswith("./"):
        external_refs.append(job_uses)
    for step in job_cfg.get("steps", []):
        step_uses = step.get("uses")
        if isinstance(step_uses, str) and not step_uses.startswith("./"):
            external_refs.append(step_uses)
    return external_refs


def _raise_called_process_error(
    cmd: list[str], **_kwargs: Any
) -> subprocess.CompletedProcess[str]:
    raise subprocess.CalledProcessError(1, cmd, output="out", stderr="err")


def _has_scalar_query_param_call(tree: ast.AST, param_name: str) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        is_scalar_param = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "ScalarQueryParameter"
        )
        first_arg = node.args[0]
        if (
            is_scalar_param
            and isinstance(first_arg, ast.Constant)
            and first_arg.value == param_name
        ):
            return True
    return False


@pytest.mark.parametrize(
    "compose_path",
    _COMPOSE_FILES,
    ids=[path.name for path in _COMPOSE_FILES],
)
def test_compose_ports_bound_to_loopback(compose_path: pathlib.Path) -> None:
    compose_doc = _load_compose_yaml(compose_path)
    services = compose_doc.get("services", {})
    assert isinstance(services, dict) and services
    checked_ports = 0
    for service_name, service_config in services.items():
        for port_spec in service_config.get("ports", []):
            checked_ports += 1
            assert isinstance(port_spec, str) and port_spec.startswith(
                "127.0.0.1:"
            ), f"{compose_path.name}:{service_name} non-loopback: {port_spec!r}"
    assert checked_ports > 0


def test_full_stack_compose_images_digest_pinned() -> None:
    services = _load_compose_yaml(_FULL_STACK_COMPOSE).get("services", {})
    assert isinstance(services, dict) and services
    for service_name, service_config in services.items():
        image = service_config.get("image", "")
        assert isinstance(image, str) and _IMAGE_DIGEST_RE.search(image), (
            f"{service_name} image not digest-pinned: {image!r}"
        )


def test_otel_collector_debug_exporter_not_detailed() -> None:
    config = yaml.safe_load(_OTEL_COLLECTOR_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    exporters = config.get("exporters", {})
    assert isinstance(exporters, dict) and "debug" in exporters
    debug_config = exporters["debug"]
    assert isinstance(debug_config, dict)
    assert debug_config.get("verbosity") != "detailed"


def test_github_workflows_permissions_and_pinned_actions() -> None:
    for workflow_path in _collect_workflow_files():
        workflow_doc = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        assert isinstance(workflow_doc, dict)
        permissions = workflow_doc.get("permissions")
        assert permissions != "write-all", (
            f"{workflow_path.name} uses write-all permissions"
        )
        assert isinstance(permissions, dict) and (
            permissions.get("contents") == "read"
        ), f"{workflow_path.name} missing top-level contents: read"
        jobs = workflow_doc.get("jobs", {})
        assert isinstance(jobs, dict) and jobs
        external_actions: list[str] = []
        for job_cfg in jobs.values():
            assert isinstance(job_cfg, dict)
            assert job_cfg.get("permissions") != "write-all"
            for uses_ref in _collect_external_uses(job_cfg):
                external_actions.append(uses_ref)
                assert _PINNED_ACTION_RE.match(uses_ref), (
                    f"{workflow_path.name} unpinned action: {uses_ref!r}"
                )
        assert external_actions


@pytest.mark.parametrize(
    ("raw_args", "secret_text"),
    [
        (["gcloud", "--password=pw_inline"], "pw_inline"),
        (["gcloud", "--password", "pw_split"], "pw_split"),
        (["gcloud", "--token=tok_inline"], "tok_inline"),
        (["gcloud", "--token", "tok_split"], "tok_split"),
        (["gcloud", "--secret=sec_inline"], "sec_inline"),
        (["gcloud", "--secret", "sec_split"], "sec_split"),
        (["gcloud", "--api-key=key_inline"], "key_inline"),
        (["gcloud", "--api-key", "key_split"], "key_split"),
    ],
)
def test_cloudsql_redact_cli_args_masks_sensitive_flags(
    raw_args: list[str],
    secret_text: str,
) -> None:
    cloudsql = _load_module_from_path("tutorial_cloudsql", _CLOUDSQL_HELPER)
    joined = " ".join(cloudsql._redact_cli_args(raw_args))
    assert secret_text not in joined
    assert "***REDACTED***" in joined


def test_cloudsql_gcloud_redacts_called_process_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cloudsql = _load_module_from_path("tutorial_cloudsql", _CLOUDSQL_HELPER)
    secrets_in_args = ("pw_1", "pw_2", "tok_3", "tok_4", "sec_5", "k_6", "k_7")
    monkeypatch.setattr(cloudsql.subprocess, "run", _raise_called_process_error)
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        cloudsql._gcloud(
            "sql",
            "users",
            "create",
            "--password=pw_1",
            "--password",
            "pw_2",
            "--token=tok_3",
            "--token",
            "tok_4",
            "--secret=sec_5",
            "--api-key=k_6",
            "--api-key",
            "k_7",
        )
    exc = exc_info.value
    printed = capsys.readouterr().out
    assert "gcloud" in printed
    surfaces = (printed, str(exc), repr(exc), str(exc.args), str(exc.cmd))
    for secret_text in secrets_in_args:
        for surface in surfaces:
            assert secret_text not in surface


@pytest.mark.parametrize(
    "disallowed_url", ["file:///etc/passwd", "http://example.com"]
)
def test_setup_http_helpers_reject_non_https_urls(
    disallowed_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    common = _load_module_from_path("tutorial_common", _COMMON_HELPER)
    preflight = _load_module_from_path("tutorial_preflight", _PREFLIGHT_SCRIPT)
    with pytest.raises(ValueError, match="https://"):
        common.call("GET", disallowed_url)
    with pytest.raises(ValueError, match="https://"):
        preflight._http("test-token", "GET", disallowed_url)


def test_prometheus_query_rejects_file_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = _load_module_from_path(
        "tutorial_shared_telemetry", _SHARED_TELEMETRY
    )
    monkeypatch.setenv("PROMETHEUS_URL", "file:///etc/passwd")
    with pytest.raises(ValueError, match=r"http\(s\)://"):
        telemetry.prometheus_query("up")


@pytest.mark.parametrize(
    ("script_path", "param_name"),
    [
        (_CASE_TELEMETRY_MAIN, "case_id"),
        (_AUDIT_LEDGER_MAIN, "session_id"),
    ],
    ids=["02_case_telemetry", "14_audit_ledger"],
)
def test_tutorial_bigquery_queries_use_scalar_query_parameters(
    script_path: pathlib.Path,
    param_name: str,
) -> None:
    source = script_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(script_path))
    assert f"@{param_name}" in source
    assert _has_scalar_query_param_call(tree, param_name)
