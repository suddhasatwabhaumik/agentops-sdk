#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 2: Google Cloud Vertex AI Prompt Management Integration (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null || echo "")}}}"
if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "local-offline" ]]; then
    echo "[SKIP] Test 2 requires a live GCP Project ID for Vertex AI Prompt Management."
    exit 0
fi
LOCATION="${2:-${GOOGLE_CLOUD_LOCATION:-europe-west4}}"

"${PYTHON_BIN}" - << PYEOF
import time

from agentops_sdk import PromptTemplate
from agentops_sdk import VertexAIPromptAdapter

adapter = VertexAIPromptAdapter(project_id="${PROJECT_ID}", location="${LOCATION}")
prompts = adapter.list_prompts()
print(f"Found {len(prompts)} prompts in Vertex AI.")

target_id = f"test_2_ephemeral_{int(time.time())}"
adapter.register_prompt(
    prompt_id=target_id,
    template="Test template {{var}}",
    version="1.0.0",
    description="E2E test prompt",
    variables=["var"],
)
try:
    tpl = adapter.get_prompt(target_id, version="latest")
    assert isinstance(tpl, PromptTemplate)
    assert tpl.prompt_id == target_id
    versions = adapter.list_versions(target_id)
    assert "1.0.0" in versions
finally:
    adapter.delete_prompt(target_id)

print("[PASS] Test 2: Vertex AI Prompt Management live verification passed.")
PYEOF
