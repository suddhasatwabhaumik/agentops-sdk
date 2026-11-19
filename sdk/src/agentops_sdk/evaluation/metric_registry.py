# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Vertex AI Evaluation Metric Registry client wrappers and bounded cache."""

from __future__ import annotations

from collections.abc import Mapping
import datetime
from typing import Any

import vertexai
from vertexai._genai import types as vtypes

from agentops_sdk.config import settings
from agentops_sdk.domain import exceptions
from agentops_sdk.domain import lifecycle
from agentops_sdk.evaluation import cloud_models
from agentops_sdk.evaluation import code_validator

_CACHE: lifecycle.BoundedTTLCache[
    tuple[str, str, str], cloud_models.RegisteredMetricInfo
] = lifecycle.BoundedTTLCache(maxsize=256, ttl_seconds=300.0)


def clear_metric_registry_cache() -> None:
    """Clears the process-wide metric registry TTL cache."""
    _CACHE.clear()


def _resolve_client_scope(
    project_id: str | None, location: str | None
) -> tuple[str, str]:
    proj, loc = settings.resolve_gcp_project_and_location(
        project_id, location, require_project=True
    )
    return proj or "", loc


def _call_evals(
    method_name: str,
    project_id: str,
    location: str,
    kwargs: Mapping[str, Any],
    *,
    client: Any | None = None,
) -> Any:
    try:
        active = client or vertexai.Client(
            project=project_id, location=location
        )
        return getattr(active.evals, method_name)(**kwargs)
    except exceptions.EvaluationError:
        raise
    except Exception as exc:
        raise exceptions.EvaluationError(
            f"{method_name} failed: {exc}"
        ) from exc


def _register_metric(
    clean_name: str,
    metric_type: str,
    metric_obj: Any,
    *,
    display_name: str | None,
    description: str | None,
    project_id: str | None,
    location: str | None,
    client: Any | None,
) -> cloud_models.RegisteredMetricInfo:
    proj, loc = _resolve_client_scope(project_id, location)
    desc = description or f"{metric_type}: {clean_name}"
    kwargs = {
        "display_name": display_name or clean_name,
        "description": desc,
        "metric": metric_obj,
    }
    resource_name = str(
        _call_evals(
            "create_evaluation_metric", proj, loc, kwargs, client=client
        )
    )
    info = cloud_models.RegisteredMetricInfo(
        resource_name=resource_name,
        metric_name=clean_name,
        metric_type=metric_type,
        description=description,
        create_time=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    _CACHE.set((proj, loc, clean_name), info)
    _CACHE.set((proj, loc, resource_name), info)
    return info


def register_code_execution_metric(
    metric_name: str,
    python_code: str,
    *,
    display_name: str | None = None,
    description: str | None = None,
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> cloud_models.RegisteredMetricInfo:
    """Validates via AST allowlist and registers a CodeExecutionMetric."""
    clean_name = metric_name.strip()
    validated = code_validator.validate_metric_code(python_code, "evaluate")
    metric_obj = vtypes.CodeExecutionMetric(
        name=clean_name, custom_function=validated
    )
    return _register_metric(
        clean_name,
        "code_execution",
        metric_obj,
        display_name=display_name,
        description=description,
        project_id=project_id,
        location=location,
        client=client,
    )


def register_llm_metric(
    metric_name: str,
    prompt_template: str,
    *,
    result_parsing_function: str | None = None,
    display_name: str | None = None,
    description: str | None = None,
    judge_model: str | None = None,
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> cloud_models.RegisteredMetricInfo:
    """Registers an LLMMetric in the Vertex AI Metric Registry."""
    clean_name = metric_name.strip()
    if result_parsing_function is not None:
        code_validator.validate_metric_code(result_parsing_function)
    metric_obj = vtypes.LLMMetric(
        name=clean_name,
        prompt_template=prompt_template,
        result_parsing_function=result_parsing_function,
        judge_model=judge_model,
    )
    return _register_metric(
        clean_name,
        "llm",
        metric_obj,
        display_name=display_name,
        description=description,
        project_id=project_id,
        location=location,
        client=client,
    )


def list_registered_metrics(
    *,
    filter_expr: str | None = None,
    order_by: str | None = None,
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> list[cloud_models.RegisteredMetricInfo]:
    """Lists registered metrics using server-side AIP-160 filter."""
    proj, loc = _resolve_client_scope(project_id, location)
    kwargs = {"filter": filter_expr, "order_by": order_by}
    resp = _call_evals(
        "list_evaluation_metrics", proj, loc, kwargs, client=client
    )
    items = getattr(resp, "evaluation_metrics", None) or ()
    return [
        cloud_models.RegisteredMetricInfo.from_cloud(item) for item in items
    ]


def get_registered_metric(
    metric_name_or_resource: str,
    *,
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> cloud_models.RegisteredMetricInfo | None:
    """Retrieves a registered metric by URI or display name."""
    target = metric_name_or_resource.strip()
    proj, loc = _resolve_client_scope(project_id, location)
    if (cached := _CACHE.get((proj, loc, target))) is not None:
        return cached
    if target.startswith("projects/"):
        item = _call_evals(
            "get_evaluation_metric",
            proj,
            loc,
            {"metric_resource_name": target},
            client=client,
        )
        info = cloud_models.RegisteredMetricInfo.from_cloud(item, target)
        _CACHE.set((proj, loc, target), info)
        if info.metric_name:
            _CACHE.set((proj, loc, info.metric_name), info)
        return info
    escaped = target.replace('"', '\\"')
    filter_expr = f'display_name="{escaped}"'
    hits = list_registered_metrics(
        filter_expr=filter_expr,
        project_id=proj,
        location=loc,
        client=client,
    )
    match = next((m for m in hits if m.metric_name == target), None)
    if match is not None:
        _CACHE.set((proj, loc, target), match)
        _CACHE.set((proj, loc, match.resource_name), match)
    return match


def delete_registered_metric(
    metric_name_or_resource: str,
    *,
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> bool:
    """Deletes a registered metric by URI or metric name."""
    target = metric_name_or_resource.strip()
    proj, loc = _resolve_client_scope(project_id, location)
    cached_by_target = _CACHE.get((proj, loc, target))
    if not target.startswith("projects/"):
        hit = get_registered_metric(
            target, project_id=proj, location=loc, client=client
        )
        resource_name = hit.resource_name if hit else ""
        metric_name = target
    else:
        resource_name = target
        metric_name = cached_by_target.metric_name if cached_by_target else ""
    if not resource_name:
        return False
    _call_evals(
        "delete_evaluation_metric",
        proj,
        loc,
        {"metric_resource_name": resource_name},
        client=client,
    )
    _CACHE.pop((proj, loc, resource_name))
    if metric_name:
        _CACHE.pop((proj, loc, metric_name))
    return True
