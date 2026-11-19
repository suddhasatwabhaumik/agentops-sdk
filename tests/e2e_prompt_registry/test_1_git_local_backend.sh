#!/usr/bin/env bash
# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
#
# TEST 1: LocalYAML Prompt Registry & SemVer Resolution (v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -f "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
export PYTHONPATH="${REPO_ROOT}/sdk/src:${REPO_ROOT}:${PYTHONPATH:-}"

SRC_EXAMPLES="${REPO_ROOT}/tests/e2e_prompt_registry/prompt_examples" \
"${PYTHON_BIN}" - << 'PYEOF'
import os
from pathlib import Path
import shutil
import tempfile

from agentops_sdk import LocalYAMLPromptAdapter
from agentops_sdk import PromptRegistry
from agentops_sdk import PromptTemplate

src_examples = Path(os.environ["SRC_EXAMPLES"])
with tempfile.TemporaryDirectory() as tmp:
    local_dir = Path(tmp) / "prompts"
    shutil.copytree(src_examples, local_dir)

    adapter = LocalYAMLPromptAdapter(local_dir=str(local_dir))
    registry = PromptRegistry(backend="local", adapter=adapter)

    registry.register_prompt(
        prompt_id="finance_summary",
        template="v1.10.0: Summarize {{portfolio_id}} risk={{risk_level}}",
        version="1.10.0",
    )
    registry.register_prompt(
        prompt_id="finance_summary",
        template="v2.0.0: Enterprise {{portfolio_id}} summary ({{risk_level}})",
        version="2.0.0",
    )

    assert (local_dir / "finance_summary.yaml").exists()
    latest = registry.get_prompt("finance_summary", version="latest")
    assert isinstance(latest, PromptTemplate)
    assert latest.version == "2.0.0"
    assert "v2.0.0" in latest.template

    v1_10 = registry.get_prompt("finance_summary", version="1.10.0")
    assert v1_10.version == "1.10.0"
    assert "v1.10.0" in v1_10.template

    rendered = registry.render_prompt(
        "finance_summary",
        {"portfolio_id": "PF-9876", "risk_level": "MODERATE"},
        version="latest",
    )
    assert "PF-9876" in rendered and "MODERATE" in rendered

    versions = registry.list_versions("finance_summary")
    assert versions[-2:] == ["1.10.0", "2.0.0"]

    try:
        registry.register_prompt(
            prompt_id="../../traversal_test",
            template="malicious payload",
        )
        raise AssertionError("Failed to block path traversal")
    except ValueError as err:
        assert "path traversal" in str(err).lower()

print("[PASS] Test 1: LocalYAML backend, SemVer, and path traversal guard verified.")
PYEOF
