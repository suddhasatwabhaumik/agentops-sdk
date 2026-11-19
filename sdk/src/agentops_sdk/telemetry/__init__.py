# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Public telemetry facade for OpenTelemetry, FinOps, and case tracking."""

from __future__ import annotations

from agentops_sdk.telemetry import case_tracker
from agentops_sdk.telemetry import decorators
from agentops_sdk.telemetry import finops
from agentops_sdk.telemetry import gcp_exporters
from agentops_sdk.telemetry import otlp_exporters
from agentops_sdk.telemetry import provider

BatchedGCSTelemetryExporter = case_tracker.BatchedGCSTelemetryExporter
BigQueryTelemetryExporter = case_tracker.BigQueryTelemetryExporter
CachedMeterTracker = provider.CachedMeterTracker
CloudLoggingTelemetryExporter = case_tracker.CloudLoggingTelemetryExporter
CompositeTelemetryExporter = case_tracker.CompositeTelemetryExporter
InMemoryTelemetryExporter = case_tracker.InMemoryTelemetryExporter
LocalFileTelemetryExporter = case_tracker.LocalFileTelemetryExporter
OtlpStdlibLogHandler = otlp_exporters.OtlpStdlibLogHandler
TelemetryHandles = provider.TelemetryHandles
UseCaseTracker = case_tracker.UseCaseTracker
configure_telemetry = provider.configure_telemetry
create_gcp_metric_reader = gcp_exporters.create_gcp_metric_reader
create_gcp_span_processor = gcp_exporters.create_gcp_span_processor
emit_finops_telemetry = finops.emit_finops_telemetry
extract_llm_usage = finops.extract_llm_usage
get_active_telemetry_mode = provider.get_active_telemetry_mode
get_default_exporter = case_tracker.get_default_exporter
get_meter_tracker = provider.get_meter_tracker
log_llm_metrics = finops.log_llm_metrics
record_fallback_metric = provider.record_fallback_metric
reset_telemetry = provider.reset_telemetry
resolve_telemetry_mode = provider.resolve_telemetry_mode
scrub_sensitive_text = finops.scrub_sensitive_text
set_default_exporter = case_tracker.set_default_exporter
trace_agent_flow = decorators.trace_agent_flow
trace_agent_span = decorators.trace_agent_span
trace_llm_call = decorators.trace_llm_call
trace_use_case = case_tracker.trace_use_case

__all__ = [
    "BatchedGCSTelemetryExporter",
    "BigQueryTelemetryExporter",
    "CachedMeterTracker",
    "CloudLoggingTelemetryExporter",
    "CompositeTelemetryExporter",
    "InMemoryTelemetryExporter",
    "LocalFileTelemetryExporter",
    "OtlpStdlibLogHandler",
    "TelemetryHandles",
    "UseCaseTracker",
    "configure_telemetry",
    "create_gcp_metric_reader",
    "create_gcp_span_processor",
    "emit_finops_telemetry",
    "extract_llm_usage",
    "get_active_telemetry_mode",
    "get_default_exporter",
    "get_meter_tracker",
    "log_llm_metrics",
    "record_fallback_metric",
    "reset_telemetry",
    "resolve_telemetry_mode",
    "scrub_sensitive_text",
    "set_default_exporter",
    "trace_agent_flow",
    "trace_agent_span",
    "trace_llm_call",
    "trace_use_case",
]
