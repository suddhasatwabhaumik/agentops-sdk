#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# TEST 2: GoogleDLPScrubber Chunking, RegexPrivacyScrubber & OTel Span Attributes

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

MODE="${MODE:-hermetic}"
if [[ "${1:-}" == "--offline" ]]; then
    MODE="hermetic"
    shift
fi
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-local-offline}}}"
LOCATION="${2:-europe-west4}"

export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"
export MODE

cd "${REPO_ROOT}"
echo "Running Test 2: Cloud DLP Redaction & OTel Span Attributes (MODE=${MODE})..."

"${PYTHON_BIN}" - "${MODE}" "${PROJECT_ID}" "${LOCATION}" <<'PYEOF'
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agentops_sdk.security.dlp import (
    GoogleDLPScrubber,
    OutputDLPSanitizer,
    RegexPrivacyScrubber,
)

mode = sys.argv[1]
project_id = sys.argv[2]
location = sys.argv[3]

# 1. RegexPrivacyScrubber sync & async
regex_scrubber = RegexPrivacyScrubber()
raw_text = (
    "Tenant tenant-acme contact: alice@example.com, phone 555-234-5678, "
    "part Series-700 UNIT-001."
)
scrubbed_text, modified = regex_scrubber.scrub(raw_text)
assert modified is True
assert "alice@example.com" not in scrubbed_text
assert "555-234-5678" not in scrubbed_text
assert "[EMAIL_ADDRESS]" in scrubbed_text
assert "[PHONE_NUMBER]" in scrubbed_text

async_scrubbed, async_mod = asyncio.run(regex_scrubber.ascrub(raw_text))
assert async_mod is True
assert async_scrubbed == scrubbed_text

clean_out, clean_mod = regex_scrubber.scrub("Series-700 UNIT-002 in stock")
assert clean_mod is False
assert clean_out == "Series-700 UNIT-002 in stock"

# 2. OTel span attribute agentops.privacy.pii_scrubbed
exporter = InMemorySpanExporter()
tp = TracerProvider()
tp.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(tp)
tracer = tp.get_tracer("e2e.dlp.test")

sanitizer = OutputDLPSanitizer(scrubber=regex_scrubber)
with tracer.start_as_current_span("outbound_pii"):
    res = sanitizer.scrub("Contact bob@example.com for UNIT-001")
    assert "bob@example.com" not in res

with tracer.start_as_current_span("outbound_clean"):
    res_clean = asyncio.run(sanitizer.scrub_text("UNIT-002 ready"))
    assert res_clean == "UNIT-002 ready"

spans = {s.name: s for s in exporter.get_finished_spans()}
assert spans["outbound_pii"].attributes.get("agentops.privacy.pii_scrubbed") is True
assert spans["outbound_clean"].attributes.get("agentops.privacy.pii_scrubbed") is False

# 3. GoogleDLPScrubber multi-chunk (> 400KB)
if mode == "hermetic":
    mock_dlp = MagicMock()

    def _fake_deidentify(*, request, timeout=15.0):
        val = request.item.value.replace("alice@example.com", "[EMAIL_ADDRESS]")
        return SimpleNamespace(item=SimpleNamespace(value=val))

    mock_dlp.deidentify_content.side_effect = _fake_deidentify
    dlp_scrubber = GoogleDLPScrubber(
        project_id="example-tenant-01",
        location=location,
        client=mock_dlp,
    )
    large_payload = ("Series-700 UNIT-001 " * 25_000) + " alice@example.com"
    assert len(large_payload.encode("utf-8")) > 400_000
    out, changed = dlp_scrubber.scrub(large_payload)
    assert changed is True
    assert "alice@example.com" not in out
    assert mock_dlp.deidentify_content.call_count >= 2
else:
    if not project_id or project_id == "local-offline":
        print("[SKIP] Live Cloud DLP test requires a valid GCP project_id.")
        sys.exit(0)
    dlp_scrubber = GoogleDLPScrubber(project_id=project_id, location=location)
    out, changed = dlp_scrubber.scrub("Contact alice@example.com about Series-700")
    assert changed is True
    assert "alice@example.com" not in out

print("Cloud DLP chunking, regex scrubber, and OTel span attributes verified.")
PYEOF

echo "[PASS] Test 2: Cloud DLP Redaction & OTel Span Attributes validated."
